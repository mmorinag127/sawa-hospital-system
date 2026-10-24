import importlib.util
import hashlib
import json
from pathlib import Path
import unittest
import os
import subprocess
import tempfile
from unittest.mock import patch
from local_receipt_signing import sign, verify_signed
import yaml
import run_local_agent_tests as AGENT_RUNNER
import run_local_backend_tests as BACKEND_RUNNER
import run_local_frontend_e2e as E2E_RUNNER
import local_hospital_legacy_step as LEGACY_RUNNER
import run_local_pg_output_boundary as PG_OUTPUT_RUNNER


MODULE = Path(__file__).with_name("local_hospital_test_runner.py")
SPEC = importlib.util.spec_from_file_location("local_hospital_test_runner", MODULE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ReceiptContractTest(unittest.TestCase):
    def test_missing_frontend_dependency_stops_before_any_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = ('.package-lock.json', '.bin/eslint', '.bin/tsc', '.bin/next',
                        '@playwright/test/package.json')
            for name in required:
                path = root / 'frontend' / 'node_modules' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            for name in required:
                path = root / 'frontend' / 'node_modules' / name
                path.unlink()
                with patch.object(RUNNER, 'ROOT', root), \
                     patch.object(RUNNER, 'clean_tree', return_value=True), \
                     patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                     patch.object(RUNNER.subprocess, 'run') as execute:
                    with self.assertRaisesRegex(SystemExit, 'prepare frontend dependencies'):
                        RUNNER.run(root / 'evidence')
                    execute.assert_not_called()
                    self.assertFalse((root / 'evidence').exists())
                path.touch()
            with patch.object(RUNNER, 'ROOT', root):
                RUNNER.require_frontend_dependencies()

    def test_agent_preflight_requires_each_owned_environment_without_starting_tests(self):
        commands = {item['name']: item['command'] for item in json.loads(RUNNER.MANIFEST.read_text())['commands']}
        self.assertEqual(commands['agent-assistant-tests'], 'python3 scripts/run_local_agent_tests.py')
        self.assertNotIn('packaged-baseline', commands)
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(AGENT_RUNNER, 'clean_tree', return_value=True), \
             patch.object(AGENT_RUNNER, 'git', return_value='a' * 40), \
             patch.object(AGENT_RUNNER, 'docker') as execute:
            with self.assertRaises(KeyError):
                AGENT_RUNNER.main()
            execute.assert_not_called()
        for source, platform in (('b' * 40, 'amd64'), ('a' * 40, 'arm64')):
            with patch.dict(os.environ, {'SAWA_LOCAL_TEST_IMAGE_AGENT_RUNTIME': 'owned', 'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(AGENT_RUNNER, 'clean_tree', return_value=True), \
                 patch.object(AGENT_RUNNER, 'git', return_value='a' * 40), \
                 patch.object(AGENT_RUNNER, 'inspect', return_value={
                     'Os': 'linux', 'Architecture': platform, 'Config': {'Labels': {'sawa.git_sha': source}}}), \
                 patch.object(AGENT_RUNNER, 'docker') as execute:
                with self.assertRaisesRegex(ValueError, 'source/platform mismatch'):
                    AGENT_RUNNER.main()
                execute.assert_not_called()

    def test_general_backend_partition_maps_every_exclusion_to_a_dedicated_full_command(self):
        commands = {item['name']: item['command'] for item in json.loads(RUNNER.MANIFEST.read_text())['commands']}
        self.assertEqual(commands['backend-tests'], 'python3 scripts/run_local_backend_tests.py')
        seen = set()
        for name, modules in BACKEND_RUNNER.DEDICATED_MODULES.items():
            self.assertIn(name, commands)
            self.assertTrue(modules)
            for module in modules:
                self.assertTrue((RUNNER.ROOT / module).is_file(), module)
                self.assertNotIn(module, seen)
                seen.add(module)
        for suite, command in {'readonly': 'hospital-readonly-pg', 'c1': 'hospital-c1-pg',
                               'c1-guards': 'hospital-c1-guard-pg', 'menu-identity': 'hospital-menu-identity-pg'}.items():
            self.assertEqual(BACKEND_RUNNER.DEDICATED_MODULES[command], BACKEND_RUNNER.MODULES[suite])
            self.assertIn('--suite ' + suite, commands[command])
        self.assertIn('test_live_schema_adoption.py', ' '.join(BACKEND_RUNNER.DEDICATED_MODULES['live-schema-adoption']))
        self.assertEqual(BACKEND_RUNNER.DEDICATED_MODULES['modernization-pg-output'],
                         BACKEND_RUNNER.MODULES['output'] + BACKEND_RUNNER.MODULES['formal'])
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'SAWA_LOCAL_EVIDENCE_DIR': directory, 'DB_HOST': 'foreign',
                                    'DB_URI': 'foreign', 'DATABASE_URL': 'foreign', 'ALEMBIC_DB_URI': 'foreign',
                                    'PYTEST_ADDOPTS': '-k foreign', 'PYTEST_PLUGINS': 'foreign_selector',
                                    'CI': '', 'GITHUB_ACTIONS': ''}), \
             patch.object(BACKEND_RUNNER, 'clean_tree', return_value=True), \
             patch.object(BACKEND_RUNNER, 'git', return_value='a' * 40), \
             patch.object(BACKEND_RUNNER.sys, 'argv', ['runner']), \
             patch.object(BACKEND_RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as execute:
            self.assertEqual(BACKEND_RUNNER.main(), 0)
            argv = execute.call_args.args[0]
            self.assertIn('backend/tests', argv)
            self.assertEqual({item.removeprefix('--ignore=') for item in argv if item.startswith('--ignore=')}, seen)
            self.assertNotIn('DB_HOST', execute.call_args.kwargs['env'])
            self.assertNotIn('DATABASE_URL', execute.call_args.kwargs['env'])
            self.assertNotIn('ALEMBIC_DB_URI', execute.call_args.kwargs['env'])
            self.assertNotIn('PYTEST_ADDOPTS', execute.call_args.kwargs['env'])
            self.assertNotIn('PYTEST_PLUGINS', execute.call_args.kwargs['env'])
            self.assertEqual(execute.call_args.kwargs['env']['SAWA_IMPACT_FULL'], '1')
            self.assertEqual(execute.call_args.kwargs['env']['DB_URI'], 'sqlite:///' + directory + '/backend-tests/runtime.sqlite')

    def test_agent_cleanup_attempts_database_even_when_test_container_cannot_be_stopped(self):
        for failure in ('stop', 'owner'):
            removed, calls = set(), []
            identities = {'test': '1' * 64, 'pg': '2' * 64}

            def execute(*args, **kwargs):
                calls.append(args)
                if args[0] == 'container':
                    name = args[4].removeprefix('name=^/').removesuffix('$')
                    return subprocess.CompletedProcess(args, 0, stdout='' if name in removed else identities[name])
                if args[0] == 'stop' and args[-1] == identities['test'] and failure == 'stop':
                    raise RuntimeError('controlled stop failure')
                if args[0] == 'rm':
                    removed.add(next(name for name, identity in identities.items() if identity == args[-1]))
                return subprocess.CompletedProcess(args, 0)

            def inspect(identity):
                name = identity if identity in identities else next(name for name, value in identities.items() if value == identity)
                return {'Id': identities[name], 'State': {'Running': False},
                        'Config': {'Labels': {'sawa.local_run': 'foreign' if failure == 'owner' and name == 'test' else 'owned',
                                              'sawa.git_sha': 'a' * 40}}}

            with patch.object(AGENT_RUNNER, 'docker', side_effect=execute), \
                 patch.object(AGENT_RUNNER, 'inspect', side_effect=inspect):
                errors = AGENT_RUNNER.cleanup_owned_containers(('test', 'pg'), 'owned', 'a' * 40)
            self.assertEqual([name for name, _ in errors], ['test'])
            self.assertIn(('stop', '--time', '30', identities['pg']), calls)
            self.assertEqual(removed, {'pg'})
            if failure == 'owner':
                self.assertFalse(any(call[0] == 'stop' and call[-1] == identities['test'] for call in calls))

    def test_local_legacy_translation_resolves_only_known_context_without_hosted_identity(self):
        context = {'SAWA_LOCAL_SOURCE_DIR': '/owned/source', 'SAWA_LOCAL_LEGACY_TEMP_DIR': '/owned/tmp',
                   'SAWA_LOCAL_SOURCE_SHA': 'a' * 40, 'SAWA_LOCAL_RUN_ID': '123', 'SAWA_LOCAL_RUN_ATTEMPT': '1'}
        translated = LEGACY_RUNNER.local_text(
            '${{ runner.temp }}/proof $GITHUB_SHA $GITHUB_WORKSPACE $RUNNER_TEMP', context)
        self.assertEqual(translated, '/owned/tmp/proof $SAWA_LOCAL_SOURCE_SHA $SAWA_LOCAL_SOURCE_DIR $SAWA_LOCAL_LEGACY_TEMP_DIR')
        for expression in ('${{ secrets.VALUE }}', '${{ github.actor }}', '${{ matrix.suite }}'):
            with self.assertRaisesRegex(ValueError, 'unresolved workflow expression'):
                LEGACY_RUNNER.local_text(expression, context)
        for workflow, job in (('../ci.yml', 'backend'), ('ci.yml', 'backend'), ('deploy-stg.yml', 'deploy'),
                              ('modernization-pg-output.yml', 'formal-pg-chain'),
                              ('modernization-pg-output.yml', 'pg-output-boundary')):
            with self.assertRaises(ValueError):
                LEGACY_RUNNER.step(workflow, job, 'not-run')

    def test_output_and_formal_groups_both_run_after_an_independent_failure(self):
        for results in ((0, 0), (1, 0), (RuntimeError('controlled failure'), 0), (0, 1)):
            def execute(_root, _run, _attempt, suite):
                result = results[0 if suite == 'output' else 1]
                if isinstance(result, Exception):
                    raise result
                return result
            with patch.dict(os.environ, {'SAWA_LOCAL_RUN_ID': '123', 'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(PG_OUTPUT_RUNNER, 'run', side_effect=execute) as run, \
                 patch.object(PG_OUTPUT_RUNNER.traceback, 'print_exc'):
                self.assertEqual(PG_OUTPUT_RUNNER.main(), int(results != (0, 0)))
            self.assertEqual([call.args[3] for call in run.call_args_list], ['output', 'formal'])

    def test_local_legacy_gh_uses_existing_login_not_a_synthetic_actions_token(self):
        for token in (None, 'synthetic-existing-local-token'):
            with tempfile.TemporaryDirectory() as directory, \
                 patch.dict(os.environ, {} if token is None else {'GH_TOKEN': token}, clear=True), \
                 patch.object(LEGACY_RUNNER.tempfile, 'mkdtemp', return_value=directory), \
                 patch.object(LEGACY_RUNNER, 'step', return_value=(
                     {'env': {}}, {'env': {'GH_TOKEN': '${{ github.token }}'}, 'run': 'true'})), \
                 patch.object(LEGACY_RUNNER.subprocess, 'check_output', return_value='a' * 40), \
                 patch.object(LEGACY_RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as execute:
                LEGACY_RUNNER.execute('verify-stg-ocr-ui.yml', 'verify', ['owned-step'])
                self.assertEqual(execute.call_args.kwargs['env'].get('GH_TOKEN'), token)
                self.assertFalse(any(name.startswith(('GITHUB_', 'RUNNER_'))
                                     for name in execute.call_args.kwargs['env']))

    def test_legacy_image_proof_uses_fresh_evidence_and_full_child_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = [str(Path(directory) / name) for name in ('first', 'second')]
            for root in roots:
                Path(root).mkdir()
            parent = {'PYTEST_ADDOPTS': '-k foreign', 'PYTEST_PLUGINS': 'foreign_plugin',
                      'SAWA_IMPACT_STATE': '/foreign/state', 'SAWA_IMPACT_UNKNOWN': 'foreign',
                      'SAWA_NODE_IMPACT_STATE': '/foreign/node', 'SAWA_IMPACT_FULL': '0',
                      'SSL_CERT_FILE': '/owned/trust.pem', 'NODE_OPTIONS': '--use-openssl-ca'}
            with patch.dict(os.environ, parent, clear=True), \
                 patch.object(LEGACY_RUNNER.tempfile, 'mkdtemp', side_effect=roots), \
                 patch.object(LEGACY_RUNNER, 'step', return_value=({'env': {}}, {
                     'run': 'evidence="$GITHUB_WORKSPACE/tmp/image-ci"',
                     'env': {'PYTEST_PLUGINS': 'another_selector'}})), \
                 patch.object(LEGACY_RUNNER.subprocess, 'check_output', return_value='a' * 40), \
                 patch.object(LEGACY_RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as execute:
                for _ in roots:
                    LEGACY_RUNNER.execute('modernization-image-ci.yml', 'frontend-image', ['proof'])
                for call, root in zip(execute.call_args_list, roots):
                    self.assertEqual(call.args[0][-1], 'evidence="$SAWA_LOCAL_LEGACY_TEMP_DIR/image-ci"')
                    child = call.kwargs['env']
                    self.assertEqual(child['SAWA_LOCAL_LEGACY_TEMP_DIR'], root)
                    self.assertEqual(child['SAWA_IMPACT_FULL'], '1')
                    self.assertNotIn('PYTEST_ADDOPTS', child)
                    self.assertNotIn('PYTEST_PLUGINS', child)
                    self.assertNotIn('SAWA_IMPACT_UNKNOWN', child)
                    self.assertNotIn('SAWA_NODE_IMPACT_STATE', child)
                    self.assertEqual(child['SSL_CERT_FILE'], parent['SSL_CERT_FILE'])
                    self.assertEqual(child['NODE_OPTIONS'], parent['NODE_OPTIONS'])
                self.assertEqual(dict(os.environ), parent)

    def test_e2e_copies_only_tracked_source_and_keeps_next_generated_changes_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'checkout'
            frontend = root / 'frontend'
            frontend.mkdir(parents=True)
            original = frontend / 'next-env.d.ts'
            original.write_text('original source')
            (frontend / '.env.local').write_text('PRIVATE_VALUE=secret')
            dependencies = frontend / 'node_modules'
            dependencies.mkdir()
            (dependencies / 'owned.js').write_text('installed dependency')
            evidence = Path(directory) / 'evidence'
            evidence.mkdir()

            def next_run(argv, *, cwd, env):
                self.assertNotEqual(cwd, frontend)
                self.assertEqual(argv[-1], '--workers=1')
                self.assertFalse((cwd / '.env.local').exists())
                self.assertNotIn('PRIVATE_VALUE', env)
                self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS', env)
                self.assertRegex(env['E2E_BASE_URL'], r'^http://127\.0\.0\.1:[1-9][0-9]*$')
                self.assertEqual(env['NEXT_PUBLIC_GIT_SHA'], 'a' * 40)
                self.assertEqual(env['API_PROXY_TARGET'], 'http://127.0.0.1:39091')
                (cwd / 'next-env.d.ts').write_text('generated by Next')
                self.assertEqual((cwd / 'node_modules/owned.js').read_text(), 'installed dependency')
                return subprocess.CompletedProcess(argv, 0)

            with patch.dict(os.environ, {'SAWA_LOCAL_EVIDENCE_DIR': str(evidence), 'PRIVATE_VALUE': 'secret',
                                        'GOOGLE_APPLICATION_CREDENTIALS': '/foreign/key', 'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(E2E_RUNNER, 'ROOT', root), \
                 patch.object(E2E_RUNNER, 'clean_tree', return_value=True), \
                 patch.object(E2E_RUNNER, 'git', return_value='a' * 40), \
                 patch.object(E2E_RUNNER.sys, 'argv', ['runner']), \
                 patch.object(E2E_RUNNER.subprocess, 'check_output', return_value=b'frontend/next-env.d.ts\0'), \
                 patch.object(E2E_RUNNER.subprocess, 'run', side_effect=next_run):
                self.assertEqual(E2E_RUNNER.main(), 0)
            self.assertEqual(original.read_text(), 'original source')
            record = json.loads((evidence / 'frontend-e2e/source.json').read_text())
            self.assertEqual(record['files'], {'frontend/next-env.d.ts': hashlib.sha256(b'original source').hexdigest()})

    @classmethod
    def setUpClass(cls):
        cls.key_directory = tempfile.TemporaryDirectory(prefix='hospital-test-key-')
        cls.key = Path(cls.key_directory.name) / 'key.pem'
        subprocess.run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout',
                        '-out', str(cls.key)], check=True, capture_output=True)
        cls.key.chmod(0o600)
        cls.public_key = subprocess.check_output(['openssl', 'pkey', '-in', str(cls.key), '-pubout'], text=True)

    @classmethod
    def tearDownClass(cls):
        cls.key_directory.cleanup()

    def test_real_signature_tampering_unsigned_and_missing_public_key(self):
        with patch.dict(os.environ, {'SAWA_LOCAL_TEST_SIGNING_KEY': str(self.key),
                                    'SAWA_LOCAL_TEST_PUBLIC_KEY': self.public_key}):
            envelope = sign(self.receipt())
            self.assertEqual(verify_signed(envelope), self.receipt())
            envelope['payload']['source_sha_end'] = 'c' * 40
            with self.assertRaises(ValueError):
                verify_signed(envelope)
            with self.assertRaises(ValueError):
                verify_signed(self.receipt())
        with patch.dict(os.environ, {'SAWA_LOCAL_TEST_PUBLIC_KEY': ''}):
            with self.assertRaises(ValueError):
                verify_signed(envelope)

    def test_rejects_boolean_exit_version_and_dirty_flags(self):
        for field, value in [('receipt_version', True), ('clean_before', 1), ('clean_after', 'true')]:
            receipt = self.receipt()
            receipt[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                RUNNER.validate_receipt(receipt, 'a' * 40, 'b' * 64)
        for value in (False, True, 0.0, '0', None):
            receipt = self.receipt(exit_code=value)
            with self.subTest(exit_code=value), self.assertRaises(ValueError):
                RUNNER.validate_receipt(receipt, 'a' * 40, 'b' * 64)

    def test_head_change_or_command_failure_never_creates_receipt(self):
        for changed, code in ((True, 0), (False, 2)):
            with tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'commands': [{'name': 'test', 'command': 'true'}]}))
                output = Path(directory) / 'evidence'
                with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                     patch.object(RUNNER, 'MANIFEST', manifest), patch.object(RUNNER, 'clean_tree', return_value=True), \
                     patch.object(RUNNER, 'require_frontend_dependencies'), \
                     patch.object(RUNNER, 'git', side_effect=['a' * 40, ('c' if changed else 'a') * 40, ('c' if changed else 'a') * 40]), \
                     patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], code)):
                    self.assertEqual(RUNNER.run(output), 1)
                self.assertFalse((output / 'receipt.json').exists())
                self.assertTrue((output / 'failed-run.json').exists())

    def test_independent_failures_are_collected_in_one_serial_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            commands = [{'name': f'group-{index}', 'command': f'echo group-{index}'} for index in range(3)]
            manifest.write_text(json.dumps({'version': 1, 'commands': commands}))
            output = Path(directory) / 'evidence'
            with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(RUNNER, 'MANIFEST', manifest), \
                 patch.object(RUNNER, 'require_frontend_dependencies'), \
                 patch.object(RUNNER, 'clean_tree', return_value=True), \
                 patch.object(RUNNER, 'git', return_value='a' * 40), \
                 patch.object(RUNNER.subprocess, 'run', side_effect=[
                     subprocess.CompletedProcess([], code) for code in (2, 0, 3)]) as execute:
                self.assertEqual(RUNNER.run(output), 1)
            self.assertEqual(execute.call_count, 3)
            self.assertEqual([call.args[0][-1] for call in execute.call_args_list],
                             [item['command'] for item in commands])
            failed = json.loads((output / 'failed-run.json').read_text())
            self.assertEqual([item['exit_code'] for item in failed['commands']], [2, 0, 3])
            self.assertFalse((output / 'receipt.json').exists())

    def test_full_receipt_does_not_inherit_external_test_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'version': 1, 'commands': [{'name': 'full', 'command': 'true'}]}))
            with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': '', 'SAWA_IMPACT_FULL': '0',
                                        'PYTEST_ADDOPTS': '-k hidden --last-failed', 'PYTEST_PLUGINS': 'foreign_selector'}), \
                 patch.object(RUNNER, 'MANIFEST', manifest), \
                 patch.object(RUNNER, 'require_frontend_dependencies'), \
                 patch.object(RUNNER, 'clean_tree', return_value=True), \
                 patch.object(RUNNER, 'git', return_value='a' * 40), \
                 patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as execute:
                self.assertEqual(RUNNER.run(Path(directory) / 'evidence'), 1)
                environment = execute.call_args.kwargs['env']
                self.assertNotIn('PYTEST_ADDOPTS', environment)
                self.assertNotIn('PYTEST_PLUGINS', environment)
                self.assertEqual(environment['SAWA_IMPACT_FULL'], '1')
                self.assertEqual(os.environ['PYTEST_ADDOPTS'], '-k hidden --last-failed')
                self.assertEqual(os.environ['SAWA_IMPACT_FULL'], '0')

    def test_source_change_stops_remaining_groups_and_never_signs(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'version': 1, 'commands': [
                {'name': 'first', 'command': 'true'}, {'name': 'second', 'command': 'true'}]}))
            output = Path(directory) / 'evidence'
            with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(RUNNER, 'MANIFEST', manifest), \
                 patch.object(RUNNER, 'require_frontend_dependencies'), \
                 patch.object(RUNNER, 'clean_tree', side_effect=[True, False, False]), \
                 patch.object(RUNNER, 'git', return_value='a' * 40), \
                 patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as execute:
                self.assertEqual(RUNNER.run(output), 1)
            self.assertEqual(execute.call_count, 1)
            failed = json.loads((output / 'failed-run.json').read_text())
            self.assertFalse(failed['clean_after'])
            self.assertFalse((output / 'receipt.json').exists())

    def test_transient_head_change_is_not_erased_by_returning_to_original_head(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'version': 1, 'commands': [{'name': 'only', 'command': 'true'}]}))
            output = Path(directory) / 'evidence'
            with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(RUNNER, 'MANIFEST', manifest), \
                 patch.object(RUNNER, 'require_frontend_dependencies'), \
                 patch.object(RUNNER, 'clean_tree', return_value=True), \
                 patch.object(RUNNER, 'git', side_effect=['a' * 40, 'c' * 40, 'a' * 40]), \
                 patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                self.assertEqual(RUNNER.run(output), 1)
            self.assertTrue((output / 'failed-run.json').exists())
            self.assertFalse((output / 'receipt.json').exists())

    def test_workflow_receipt_gate_executes_owner_sha_and_signature_rejections(self):
        shim = '''git() {
          case "$1 $2" in
            "status --porcelain") return 0 ;;
            "fetch origin") return "${FETCH_EXIT:-0}" ;;
            "check-ref-format "*) return 0 ;;
            "rev-parse HEAD") printf '%s\\n' "$EXPECTED_SOURCE_SHA" ;;
            "rev-parse "*) printf '%s\\n' "$REMOTE_SHA" ;;
            *) return 99 ;;
          esac
        }
        sha256sum() {
          python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest(), sys.argv[1])' "$@"
        }
        '''
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'SAWA_LOCAL_TEST_SIGNING_KEY': str(self.key)}):
            payload = self.receipt()
            payload['definition_sha256'] = RUNNER.digest(RUNNER.MANIFEST)
            envelope = sign(payload)
            for filename in ('deploy-dev.yml', 'deploy-stg.yml', 'deploy-prod.yml'):
                workflow = yaml.safe_load((RUNNER.ROOT / '.github/workflows' / filename).read_text())
                job = workflow['jobs'].get('source-gate', workflow['jobs'].get('deploy-dev'))
                gate = next(s['run'] for s in job['steps'] if 'local_hospital_test_runner.py --validate' in s.get('run', ''))
                env = {**os.environ, 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_ACTOR': 'owner',
                       'GITHUB_REPOSITORY_OWNER': 'owner', 'GITHUB_TRIGGERING_ACTOR': 'owner',
                       'GITHUB_SHA': 'a' * 40, 'EXPECTED_SOURCE_SHA': 'a' * 40, 'REMOTE_SHA': 'a' * 40,
                       'GITHUB_REF_NAME': 'develop', 'RUNNER_TEMP': directory,
                       'LOCAL_TEST_RECEIPT': json.dumps(envelope), 'SAWA_LOCAL_TEST_PUBLIC_KEY': self.public_key,
                       'GOOGLE_OAUTH_CLIENT_ID': '123-staging.apps.googleusercontent.com',
                       'PROD_GOOGLE_OAUTH_CLIENT_ID': '123-production.apps.googleusercontent.com'}
                def run(overrides):
                    return subprocess.run(['bash', '-c', shim + gate], cwd=RUNNER.ROOT,
                                          env={**env, **overrides}, capture_output=True).returncode
                with self.subTest(workflow=filename):
                    self.assertEqual(run({}), 0)
                    for overrides in ({'GITHUB_EVENT_NAME': 'push'}, {'GITHUB_ACTOR': 'foreign'},
                                      {'GITHUB_TRIGGERING_ACTOR': 'foreign'}, {'GITHUB_SHA': 'c' * 40},
                                      {'SAWA_LOCAL_TEST_PUBLIC_KEY': ''}, {'LOCAL_TEST_RECEIPT': json.dumps(payload)}):
                        self.assertNotEqual(run(overrides), 0, overrides.keys())
                    if filename == 'deploy-dev.yml':
                        self.assertNotEqual(run({'REMOTE_SHA': 'c' * 40}), 0)
                        self.assertNotEqual(run({'FETCH_EXIT': '1'}), 0)
    def receipt(self, exit_code=0):
        commands = [{"name": item["name"], "command_sha256": hashlib.sha256(item["command"].encode()).hexdigest(),
                     "log_sha256": "d" * 64, "exit_code": exit_code}
                    for item in json.loads(RUNNER.MANIFEST.read_text())["commands"]]
        return {"receipt_version": 1, "owner_attestation": "manual", "source_sha_start": "a" * 40,
                "source_sha_end": "a" * 40,
                "clean_before": True, "clean_after": True, "definition_sha256": "b" * 64, "commands": commands}

    def test_rejects_nonzero_command(self):
        receipt = self.receipt(exit_code=1)
        with self.assertRaises(ValueError):
            RUNNER.validate_receipt(receipt, "a" * 40, "b" * 64)

    def test_accepts_manual_local_receipt(self):
        receipt = self.receipt()
        RUNNER.validate_receipt(json.loads(json.dumps(receipt)), "a" * 40, "b" * 64)

    def test_rejects_false_clean_flag_invalid_hash_and_changed_head(self):
        receipt = self.receipt()
        receipt["clean_after"] = False
        with self.assertRaises(ValueError):
            RUNNER.validate_receipt(receipt, "a" * 40, "b" * 64)
        receipt = self.receipt()
        receipt["commands"][0]["log_sha256"] = "not-a-hash"
        with self.assertRaises(ValueError):
            RUNNER.validate_receipt(receipt, "a" * 40, "b" * 64)
        receipt = self.receipt()
        receipt["source_sha_end"] = "c" * 40
        with self.assertRaises(ValueError):
            RUNNER.validate_receipt(receipt, "a" * 40, "b" * 64)

    def test_hosted_test_workflows_are_retired(self):
        workflows = RUNNER.ROOT / ".github/workflows"
        retired = ("ci.yml", "live-schema-adoption-test.yml", "modernization-image-ci.yml",
                   "modernization-pg-output.yml", "modernization-readonly-pg.yml",
                   "modernization-native-ci.yml", "verify-staging-data.yml", "verify-stg-ocr-ui.yml")
        self.assertTrue(all(not (workflows / name).exists() for name in retired))
        legacy = RUNNER.ROOT / "local-checks/legacy"
        self.assertTrue(all((legacy / name).is_file() for name in retired))
        for name in ("deploy-dev.yml", "deploy-stg.yml", "deploy-prod.yml"):
            text = (workflows / name).read_text()
            self.assertNotIn("\n  push:\n", text)
            self.assertIn("expected_source_sha:", text)
            self.assertIn("local_test_receipt:", text)
            self.assertIn('vars.SAWA_LOCAL_TEST_PUBLIC_KEY', text)
            self.assertIn("$GITHUB_ACTOR\" = \"$GITHUB_REPOSITORY_OWNER", text)

    def test_unmerged_native_inventory_is_not_a_release_prerequisite(self):
        commands = json.loads(RUNNER.MANIFEST.read_text())["commands"]
        self.assertTrue(commands)
        self.assertTrue(all("native_dependency_ci.py" not in item["command"] for item in commands))
        self.assertTrue(all(step["workflow"] != "modernization-native-ci.yml"
                            for item in commands for step in item.get("legacy_steps", [])))

    def test_recovery_also_requires_signed_local_tests_before_cloud_auth(self):
        workflow = yaml.safe_load((RUNNER.ROOT / '.github/workflows/deploy-stg.yml').read_text())
        recovery = next(job for job in workflow['jobs'].values()
                        if any(step.get('name') == 'Require explicit staging source and recovery approval'
                               for step in job.get('steps', [])))
        steps = recovery['steps']
        gate_index = next(i for i, step in enumerate(steps)
                          if 'local_hospital_test_runner.py --validate' in step.get('run', ''))
        auth_index = next(i for i, step in enumerate(steps)
                          if step.get('uses', '').startswith('google-github-actions/auth@'))
        self.assertLess(gate_index, auth_index)
        self.assertIn('vars.SAWA_LOCAL_TEST_PUBLIC_KEY', steps[gate_index]['env']['SAWA_LOCAL_TEST_PUBLIC_KEY'])
        self.assertIn('test "$EXPECTED_SOURCE_SHA" = "$GITHUB_SHA"', steps[gate_index]['run'])

    def test_live_deployed_source_check_is_post_deploy_not_a_predeploy_cycle(self):
        manifest = json.loads(RUNNER.MANIFEST.read_text())
        self.assertFalse(any(item['name'] == 'stg-ocr-ui-readonly' for item in manifest['commands']))
        self.assertEqual([item['name'] for item in manifest['post_deploy_commands']], ['stg-ocr-ui-readonly'])
        self.assertEqual(manifest['post_deploy_commands'][0]['command'],
                         'python3 scripts/run_local_stg_ocr_ui_check.py')


if __name__ == "__main__":
    unittest.main()
