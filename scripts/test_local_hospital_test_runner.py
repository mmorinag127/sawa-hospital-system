import importlib.util
import hashlib
import json
from pathlib import Path
import unittest
import os
import subprocess
import tempfile
import sys
import time
import signal
import shutil
from unittest.mock import patch
from local_receipt_signing import sign, verify_signed
import yaml
import run_local_agent_tests as AGENT_RUNNER
import run_local_backend_tests as BACKEND_RUNNER
import run_local_frontend_e2e as E2E_RUNNER
import local_hospital_legacy_step as LEGACY_RUNNER
import run_local_pg_output_boundary as PG_OUTPUT_RUNNER
import run_local_frontend_contracts as FRONTEND_RUNNER
import local_command_evidence as COMMAND_EVIDENCE
import detect_stg_surfaces as DEPLOY_SURFACES


MODULE = Path(__file__).with_name("local_hospital_test_runner.py")
SPEC = importlib.util.spec_from_file_location("local_hospital_test_runner", MODULE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ReceiptContractTest(unittest.TestCase):
    def setUp(self):
        baseline = patch.object(RUNNER, 'CommandEvidence')
        self.command_evidence = baseline.start().return_value
        self.command_evidence.reuse.return_value = None
        self.addCleanup(baseline.stop)

    def test_deploy_surfaces_separate_runtime_from_local_verification_changes(self):
        for paths, expected in (
            (['docs/review.md', 'scripts/run_local_agent_tests.py', 'backend/tests/unit/test_a.py'], (False, False)),
            (['frontend/src/pages/orders.tsx'], (False, True)),
            (['agent_runtime/native/shift/backend/shift_scheduler_backend/main.py'], (False, True)),
            (['backend/src/api/orders.py'], (True, True)),
            (['ocr_pipeline/app/main.py'], (True, True)),
            (['.github/workflows/deploy-stg.yml'], (True, True)),
            (['infra/ci/verify_tls_trust.py'], (True, True)),
            (['scripts/prepare_hakodate_backend_deploy_source.sh'], (True, True)),
            (['unclassified-runtime-input'], (True, True)),
        ):
            with self.subTest(paths=paths):
                self.assertEqual(DEPLOY_SURFACES.surfaces(paths), expected)

    def test_deploy_baseline_requires_successful_normal_same_repository_staging_run(self):
        run = {'event': 'workflow_dispatch', 'head_branch': 'develop', 'status': 'completed',
               'conclusion': 'success', 'path': '.github/workflows/deploy-stg.yml',
               'head_sha': 'a' * 40, 'head_repository': {'full_name': DEPLOY_SURFACES.REPOSITORY}}
        jobs = {'source-gate': 'success', 'recover-web': 'skipped'}
        self.assertEqual(DEPLOY_SURFACES.previous_source([run], jobs), 'a' * 40)
        self.assertIsNone(DEPLOY_SURFACES.previous_source([], {}))
        self.assertIsNone(DEPLOY_SURFACES.previous_source([run], {**jobs, 'recover-web': 'success'}))
        for key, value in (('event', 'pull_request'), ('head_branch', 'main'),
                           ('status', 'in_progress'), ('conclusion', 'failure'),
                           ('path', '.github/workflows/deploy-prod.yml'), ('head_sha', 'invalid'),
                           ('head_repository', {'full_name': 'other/repository'})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                DEPLOY_SURFACES.previous_source([{**run, key: value}], jobs)
        for key, value in (('source-gate', 'skipped'), ('recover-web', 'failure')):
            with self.subTest(job=key), self.assertRaises(ValueError):
                DEPLOY_SURFACES.previous_source([run], {**jobs, key: value})

    def test_deploy_selection_checks_ancestor_before_writing_outputs(self):
        run = {'id': 123, 'event': 'workflow_dispatch', 'head_branch': 'develop', 'status': 'completed',
               'conclusion': 'success', 'path': '.github/workflows/deploy-stg.yml',
               'head_sha': 'a' * 40, 'head_repository': {'full_name': DEPLOY_SURFACES.REPOSITORY}}
        jobs = {'jobs': [{'name': 'source-gate', 'conclusion': 'success'},
                         {'name': 'recover-web', 'conclusion': 'skipped'}]}
        for conflict in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'outputs'
                env = {'GITHUB_REPOSITORY': DEPLOY_SURFACES.REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                       'GITHUB_REF': 'refs/heads/develop', 'GITHUB_SHA': 'b' * 40, 'GITHUB_OUTPUT': str(output)}

                def execute(argv, **kwargs):
                    if conflict and argv[:2] == ['git', 'merge-base']:
                        raise subprocess.CalledProcessError(1, argv)
                    return subprocess.CompletedProcess(argv, 0)

                with patch.dict(os.environ, env), patch.object(DEPLOY_SURFACES, 'github',
                        side_effect=[{'workflow_runs': [run]}, jobs]), \
                     patch.object(DEPLOY_SURFACES.subprocess, 'check_output',
                        side_effect=['b' * 40, 'agent_runtime/native/shift/backend/main.py\0']), \
                     patch.object(DEPLOY_SURFACES.subprocess, 'run', side_effect=execute) as commands:
                    if conflict:
                        with self.assertRaises(subprocess.CalledProcessError):
                            DEPLOY_SURFACES.main()
                        self.assertFalse(output.exists())
                    else:
                        DEPLOY_SURFACES.main()
                        self.assertEqual(output.read_text(), 'backend_changed=false\nfrontend_changed=true\n')
                    self.assertIn(['git', 'merge-base', '--is-ancestor', 'a' * 40, 'b' * 40],
                                  [call.args[0] for call in commands.call_args_list])

    def test_staging_cache_keeps_immutable_source_output_and_readonly_selection_gate(self):
        workflow = yaml.safe_load((RUNNER.ROOT / '.github/workflows/deploy-stg.yml').read_text())
        gate = workflow['jobs']['source-gate']
        self.assertEqual(gate['permissions'], {'contents': 'read', 'actions': 'read'})
        selection = next(step for step in gate['steps'] if step.get('id') == 'changes')
        self.assertIn('python3 scripts/detect_stg_surfaces.py', selection['run'])
        self.assertNotIn('echo "backend_changed=true"', selection['run'].split('before=')[0])
        build = next(step for step in workflow['jobs']['build-agent']['steps'] if step.get('id') == 'image')['run']
        self.assertIn('agent-runtime:stg-$GITHUB_SHA', build)
        self.assertIn('--build-arg "SOURCE_REVISION=$GITHUB_SHA"', build)
        self.assertIn('--cache-from "type=registry,ref=$cache_image" --cache-to type=inline', build)
        self.assertIn('--tag "$image" --tag "$cache_image"', build)
        self.assertIn('echo "image=${image%:*}@$digest"', build)
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])

    def test_native_selection_keeps_full_for_business_changes_and_unknown_base(self):
        full_paths = ('experiments/agent_assistant/runtime.py', 'agent_runtime/native/shift/backend/app.py',
                      'agent_runtime/baselines/shift.json', 'agent_runtime/Dockerfile',
                      'backend/requirements.txt', 'frontend/public/assistant-workflow.css', '')
        for path in full_paths:
            with self.subTest(path=path), patch.object(AGENT_RUNNER, 'focused_staging', return_value=True), \
                 patch.object(AGENT_RUNNER, 'staging_scope_base', return_value='a' * 40), \
                 patch.object(AGENT_RUNNER, 'git', return_value=path + '\0' if path else ''):
                self.assertEqual(AGENT_RUNNER.select_test_targets()['mode'], 'full')
        with patch.object(AGENT_RUNNER, 'focused_staging', return_value=True), \
             patch.object(AGENT_RUNNER, 'staging_scope_base', side_effect=subprocess.CalledProcessError(1, 'git')):
            self.assertEqual(AGENT_RUNNER.select_test_targets()['mode'], 'full')
        with patch.object(AGENT_RUNNER, 'focused_staging', return_value=False), patch.object(AGENT_RUNNER, 'git') as git:
            self.assertEqual(AGENT_RUNNER.select_test_targets()['mode'], 'full')
            git.assert_not_called()

    def test_native_read_selection_includes_auth_and_both_native_contracts(self):
        paths = 'backend/src/services/order_service.py\0agent_runtime/native/shift/backend/shift_scheduler_backend/schema_contract.py\0'
        with patch.object(AGENT_RUNNER, 'focused_staging', return_value=True), \
             patch.object(AGENT_RUNNER, 'staging_scope_base', return_value='a' * 40), \
             patch.object(AGENT_RUNNER, 'git', return_value=paths):
            selected = AGENT_RUNNER.select_test_targets()
        self.assertEqual(selected['mode'], 'focused-read-and-startup')
        self.assertEqual(selected['base_sha'], 'a' * 40)
        self.assertEqual(selected['changed_paths'], paths.rstrip('\0').split('\0'))
        self.assertEqual(len(selected['targets']), 8)
        self.assertIn('experiments/agent_assistant/tests/test_native_contract_comparison.py', selected['targets'])
        self.assertIn('experiments/agent_assistant/tests/test_native_workflows.py::test_packaged_api_auth_and_extensions_with_latest_contracts', selected['targets'])
        with tempfile.TemporaryDirectory() as directory, patch.object(AGENT_RUNNER, 'ROOT', Path(directory)), \
             patch.object(AGENT_RUNNER, 'focused_staging', return_value=True), \
             patch.object(AGENT_RUNNER, 'staging_scope_base', return_value='a' * 40), \
             patch.object(AGENT_RUNNER, 'git', return_value=paths):
            with self.assertRaisesRegex(ValueError, 'target is missing'):
                AGENT_RUNNER.select_test_targets()

    def test_staging_scope_base_preserves_default_and_full_mode(self):
        for path, focused in (('', True), ('/unread/in/full', False)):
            with self.subTest(path=path), patch.dict(os.environ, {'SAWA_LOCAL_TEST_BASELINE_RECEIPT': path}), \
                 patch.object(RUNNER, 'focused_staging', return_value=focused), \
                 patch.object(RUNNER, 'git', return_value='a' * 40) as git, \
                 patch.object(RUNNER, 'verify_signed') as verify:
                self.assertEqual(RUNNER.staging_scope_base(), 'a' * 40)
                git.assert_called_once_with('merge-base', 'HEAD', 'origin/develop')
                verify.assert_not_called()

    def test_staging_scope_base_verifies_signed_receipt_and_records_selection_only(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'receipt.json'
            receipt.write_text('{"signed":"fixture"}')
            payload = {'source_sha_end': 'a' * 40, 'definition_sha256': 'b' * 64}
            with patch.dict(os.environ, {'SAWA_LOCAL_TEST_BASELINE_RECEIPT': str(receipt), 'SAWA_LOCAL_EVIDENCE_DIR': directory}), \
                 patch.object(RUNNER, 'focused_staging', return_value=True), \
                 patch.object(RUNNER, 'verify_signed', return_value=payload) as signature, \
                 patch.object(RUNNER, 'validate_receipt') as validate, \
                 patch.object(RUNNER, 'digest', return_value='b' * 64), \
                 patch.object(RUNNER, 'git', side_effect=['', 'c' * 40]) as git:
                self.assertEqual(RUNNER.staging_scope_base(), 'a' * 40)
                signature.assert_called_once_with({'signed': 'fixture'})
                validate.assert_called_once_with(payload, 'a' * 40, 'b' * 64)
                self.assertEqual(git.call_args_list[0].args, ('merge-base', '--is-ancestor', 'a' * 40, 'HEAD'))
            evidence = json.loads((Path(directory) / 'scope-baseline.json').read_text())
            self.assertEqual(evidence['base_sha'], 'a' * 40)
            self.assertEqual(evidence['source_sha'], 'c' * 40)
            self.assertEqual(evidence['receipt_sha256'], hashlib.sha256(receipt.read_bytes()).hexdigest())
            self.assertIn('omitted tests are not claimed as passed', evidence['purpose'])

    def test_explicit_baseline_errors_stop_before_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'receipt.json'
            receipt.write_text('{}')
            for failure in ('signature', 'shape', 'definition', 'ancestor'):
                with self.subTest(failure=failure), \
                     patch.dict(os.environ, {'SAWA_LOCAL_TEST_BASELINE_RECEIPT': str(receipt)}), \
                     patch.object(RUNNER, 'focused_staging', return_value=True), \
                     patch.object(RUNNER, 'verify_signed', return_value={} if failure == 'shape' else {'source_sha_end': 'a' * 40},
                                  side_effect=ValueError('signature') if failure == 'signature' else None), \
                     patch.object(RUNNER, 'validate_receipt', side_effect=ValueError('definition') if failure == 'definition' else None), \
                     patch.object(RUNNER, 'git', side_effect=subprocess.CalledProcessError(1, 'git')) as git:
                    with self.assertRaises(ValueError):
                        RUNNER.staging_scope_base()
                    if failure != 'ancestor':
                        git.assert_not_called()
        with patch.object(AGENT_RUNNER, 'focused_staging', return_value=True), \
             patch.object(AGENT_RUNNER, 'staging_scope_base', side_effect=ValueError('invalid signed baseline')):
            with self.assertRaisesRegex(ValueError, 'invalid signed baseline'):
                AGENT_RUNNER.select_test_targets()

    def test_missing_or_unknown_tracker_version_stops_before_backend_execution(self):
        for value in ('unknown', None):
            with self.subTest(value=value), patch.object(BACKEND_RUNNER, 'version') as installed:
                if value is None:
                    installed.side_effect = BACKEND_RUNNER.PackageNotFoundError('pytest-testmon')
                else:
                    installed.return_value = value
                with self.assertRaisesRegex(ValueError, 'requirements-local-tests.txt'):
                    BACKEND_RUNNER.require_impact_dependencies()

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
            self.assertEqual(commands[command], 'python3 scripts/run_local_pg_batch.py')
        self.assertIn('test_live_schema_adoption.py', ' '.join(BACKEND_RUNNER.DEDICATED_MODULES['live-schema-adoption']))
        self.assertEqual(BACKEND_RUNNER.DEDICATED_MODULES['modernization-pg-output'],
                         BACKEND_RUNNER.MODULES['output'] + BACKEND_RUNNER.MODULES['formal'])
        def execute_backend(argv, env):
            state = Path(env['SAWA_IMPACT_STATE'])
            (state / 'selection.json').write_text(json.dumps({
                'selected': ['test_example.py::test_case'], 'reused': [],
                'executed': ['test_example.py::test_case'], 'completed': ['test_example.py::test_case'],
                'exitstatus': 0, 'source_sha': 'a' * 40, 'baseline': None}))
            return subprocess.CompletedProcess(argv, 0)

        for mode, full_flag in (('full', '1'), ('impact', '0')):
            self.check_backend_partition(mode, full_flag, seen, execute_backend)
        for branch, full_flag in (('release/prod-owned', '1'), ('', '1')):
            self.check_backend_partition(None, full_flag, seen, execute_backend, branch=branch)
        self.check_backend_partition('impact', '1', seen, execute_backend, branch='release/prod-owned')
        self.check_backend_partition('focused', '1', seen, execute_backend, branch='release/prod-owned')

    def check_backend_partition(self, mode, full_flag, seen, execute_backend, *, branch='codex/owned'):
        with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'SAWA_LOCAL_EVIDENCE_DIR': directory, 'DB_HOST': 'foreign',
                                    'DB_URI': 'foreign', 'DATABASE_URL': 'foreign', 'ALEMBIC_DB_URI': 'foreign',
                                    'PYTEST_ADDOPTS': '-k foreign', 'PYTEST_PLUGINS': 'foreign_selector',
                                    'CI': '', 'GITHUB_ACTIONS': '', 'SAWA_LOCAL_TEST_MODE': mode or 'impact'}), \
             patch.object(BACKEND_RUNNER, 'clean_tree', return_value=True), \
             patch.object(BACKEND_RUNNER, 'require_impact_dependencies'), \
             patch.object(BACKEND_RUNNER, 'git', side_effect=lambda *args: str(Path(directory) / 'git') if '--git-common-dir' in args else branch if args == ('branch', '--show-current') else 'a' * 40), \
             patch.object(BACKEND_RUNNER.sys, 'argv', ['runner']), \
             patch.object(BACKEND_RUNNER, 'run_pytest', side_effect=execute_backend) as execute:
            if mode is None:
                os.environ.pop('SAWA_LOCAL_TEST_MODE')
            self.assertEqual(BACKEND_RUNNER.main(), 0)
            argv = execute.call_args.args[0]
            self.assertIn('backend/tests', argv)
            partition = json.loads((Path(directory) / 'backend-tests/partition.json').read_text())
            self.assertIsNone(partition['scope'])
            self.assertIsNone(partition['outside_scope'])
            self.assertEqual({item.removeprefix('--ignore=') for item in argv if item.startswith('--ignore=')}, seen)
            env = execute.call_args.args[1]
            self.assertNotIn('DB_HOST', env)
            self.assertNotIn('DATABASE_URL', env)
            self.assertNotIn('ALEMBIC_DB_URI', env)
            self.assertNotIn('PYTEST_ADDOPTS', env)
            self.assertNotIn('PYTEST_PLUGINS', env)
            self.assertEqual(env['SAWA_IMPACT_FULL'], full_flag)
            self.assertIn('--testmon', argv)
            self.assertTrue((Path(directory) / 'backend-tests/selection.json').is_file())
            runtime = 'backend-runtime.sqlite'
            self.assertEqual(env['DB_URI'], 'sqlite:///' + str(Path(directory).resolve() / 'git/sawa-local-impact' / runtime))

    def test_focused_scope_retains_known_failures_and_interruptions_not_unstarted_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'selection.json').write_text(json.dumps({
                'failed': ['tests/unit/test_order_workflow_revision_contract.py::failed'],
                'incomplete': ['tests/contract/test_current_sheet_source_parity.py::interrupted'],
                'not_started': ['tests/unit/test_unused.py::unstarted']}))
            self.assertEqual(BACKEND_RUNNER.retained_failure_modules(state), [
                'backend/tests/contract/test_current_sheet_source_parity.py',
                'backend/tests/unit/test_order_workflow_revision_contract.py'])
            (state / 'failed-tests.json').write_text(json.dumps(['../../outside.py::test']))
            with self.assertRaisesRegex(ValueError, 'explicit current test mapping'):
                BACKEND_RUNNER.retained_failure_modules(state)

    def test_staging_scope_is_cold_safe_and_includes_changed_and_failed_modules(self):
        from backend_test_scope import select_modules
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definition = root / 'infra/ci/staging_backend_scope.json'
            definition.parent.mkdir(parents=True)
            modules = ['backend/tests/test_auth.py', 'backend/tests/test_read.py',
                       'backend/tests/test_new.py', 'backend/tests/test_failed.py']
            for module in modules:
                path = root / module
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            definition.write_text(json.dumps({'version': 2, 'always': ['auth'],
                'groups': {'auth': [modules[0]], 'reads': [modules[1]]},
                'sources': {'backend/src/read.py': ['reads']}}))
            selected = select_modules(root, ['backend/src/read.py', modules[2]], [modules[3]])
            self.assertEqual(selected['modules'], sorted(modules))
            self.assertEqual(selected['changed_tests'], [modules[2]])
            self.assertEqual(select_modules(root, [])['modules'], [modules[0]])
            shared = root / 'backend/tests/conftest.py'
            shared.touch()
            self.assertEqual(select_modules(root, ['backend/tests/conftest.py'])['modules'], sorted(modules[:2]))
            with self.assertRaisesRegex(ValueError, 'Map backend changes'):
                select_modules(root, ['backend/src/unmapped.py'])
            with self.assertRaisesRegex(ValueError, 'removed test coverage'):
                select_modules(root, ['backend/tests/test_removed.py'])

    def test_scoped_backend_does_not_load_testmon_or_claim_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / 'evidence'
            evidence.mkdir()
            module = 'backend/tests/test_owned.py'
            node = module + '::test_owned'
            source = 'a' * 40
            scope = {'kind': 'reviewed-staging-behaviors', 'modules': [module],
                     'outside_scope': 'not executed'}
            def execute(argv, env):
                self.assertIn(module, argv)
                self.assertNotIn('backend/tests', argv)
                self.assertNotIn('--testmon', argv)
                self.assertNotIn('SAWA_IMPACT_FULL', env)
                self.assertIn('scoped_backend_results', argv)
                (evidence / 'selection.json').write_text(json.dumps({
                    'source_sha': source, 'selected': [node], 'executed': [node],
                    'completed': [node], 'passed': [node], 'failed': [], 'incomplete': [],
                    'not_started': [], 'reused': [], 'normal_completion': True, 'exitstatus': 0}))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(BACKEND_RUNNER, 'clean_tree', return_value=True), \
                 patch.object(BACKEND_RUNNER, 'git', return_value=source), \
                 patch.object(BACKEND_RUNNER, 'run_pytest', side_effect=execute):
                self.assertEqual(BACKEND_RUNNER.run_scoped_backend(scope, evidence,
                    {'SAWA_IMPACT_FULL': '1'}, source, root / 'legacy/backend', [], []), 0)
            self.assertEqual(json.loads((root / 'legacy/backend-scoped/failed-tests.json').read_text()), [])

    def test_default_staging_uses_scoped_selection_before_pytest_collection(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'SAWA_LOCAL_EVIDENCE_DIR': directory, 'CI': '', 'GITHUB_ACTIONS': ''}), \
             patch.object(BACKEND_RUNNER, 'clean_tree', return_value=True), \
             patch.object(BACKEND_RUNNER, 'staging_scope_base', return_value='a' * 40), \
             patch.object(BACKEND_RUNNER, 'git', side_effect=lambda *args:
                 str(Path(directory) / 'git') if '--git-common-dir' in args else
                 'codex/owned' if args == ('branch', '--show-current') else
                 'backend/src/api/orders.py' if args[0] == 'diff' else 'a' * 40), \
             patch.object(BACKEND_RUNNER.sys, 'argv', ['runner']), \
             patch.object(BACKEND_RUNNER, 'require_impact_dependencies') as impact, \
             patch.object(BACKEND_RUNNER, 'run_scoped_backend', return_value=0) as scoped, \
             patch.object(BACKEND_RUNNER, 'run_pytest') as collect:
            os.environ.pop('SAWA_LOCAL_TEST_MODE', None)
            self.assertEqual(BACKEND_RUNNER.main(), 0)
            self.assertIn('page-read-results-and-response-ownership', scoped.call_args.args[0]['groups'])
            self.assertIn('authentication-and-session', scoped.call_args.args[0]['groups'])
            impact.assert_not_called()
            collect.assert_not_called()

    def test_scoped_results_distinguish_failure_skip_and_interrupted_cases(self):
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('owned_scoped_results',
            RUNNER.ROOT / 'infra/ci/scoped_backend_results.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        plugin.pytest_collection_finish(SimpleNamespace(items=[
            SimpleNamespace(nodeid=node) for node in ('pass', 'fail', 'skip', 'interrupted', 'unstarted')]))
        for node in ('pass', 'fail', 'skip', 'interrupted'):
            plugin.pytest_runtest_logstart(node, None)
            phases = [('setup', 'skipped')] if node == 'skip' else [('setup', 'passed')]
            if node in ('pass', 'fail'):
                phases.append(('call', 'passed' if node == 'pass' else 'failed'))
            if node != 'interrupted':
                phases.append(('teardown', 'passed'))
            for when, outcome in phases:
                plugin.pytest_runtest_logreport(SimpleNamespace(nodeid=node, when=when, outcome=outcome))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'SAWA_LOCAL_SOURCE_SHA': 'a' * 40,
                'SAWA_SCOPED_BACKEND_REPORT': str(Path(directory) / 'results.json')}):
            plugin.pytest_sessionfinish(None, 2)
            report = json.loads((Path(directory) / 'results.json').read_text())
        self.assertEqual(report['passed'], ['pass'])
        self.assertEqual(report['failed'], ['fail'])
        self.assertEqual(report['skipped'], ['skip'])
        self.assertEqual(report['incomplete'], ['interrupted'])
        self.assertEqual(report['not_started'], ['unstarted'])
        self.assertFalse(report['normal_completion'])
        self.assertEqual(report['reused'], [])

    def test_failed_attempt_restores_consistent_database_and_metadata(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir()
            metadata = state / 'inputs.json'
            metadata.write_text('original metadata')
            database = state / 'testmon.sqlite'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE original(value INTEGER)')
                connection.execute('INSERT INTO original VALUES(7)')
            with BACKEND_RUNNER.preserve_baseline(state):
                metadata.unlink()
                metadata.write_text('failed attempt')
                with sqlite3.connect(database) as connection:
                    connection.execute('DELETE FROM original')
                BACKEND_RUNNER.retain_failures(state, state, ['case::failed'])
            self.assertEqual(metadata.read_text(), 'original metadata')
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute('SELECT value FROM original').fetchall(), [(7,)])
            self.assertEqual(json.loads((state / 'failed-tests.json').read_text()), ['case::failed'])

    def test_interruption_retains_only_failed_or_incomplete_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = 'a' * 40
            selected = ['case::passed', 'case::failed', 'case::active', 'case::unstarted']
            (state / 'selection-plan.json').write_text(json.dumps({'source_sha': source, 'selected': selected}))
            (state / 'selection.json').write_text(json.dumps({
                'source_sha': source, 'selected': selected,
                'failed': ['case::failed'], 'incomplete': ['case::active']}))
            self.assertEqual(BACKEND_RUNNER.interrupted_tests(state, source), ['case::active', 'case::failed'])
            (state / 'selection.json').unlink()
            journal = state / 'started-tests.jsonl'
            journal.write_text(json.dumps({'source_sha': source}) + '\n'
                               + json.dumps({'nodeid': 'case::passed'}) + '\n'
                               + json.dumps({'nodeid': 'case::active'}) + '\n')
            # Without a completed report, started cases are conservative retries;
            # never-started cases are not fabricated as failed executions.
            self.assertEqual(BACKEND_RUNNER.interrupted_tests(state, source), ['case::active', 'case::passed'])
            journal.write_text(json.dumps({'source_sha': source}) + '\n'
                               + json.dumps({'nodeid': 'foreign::case'}) + '\n')
            with self.assertRaisesRegex(ValueError, 'identity'):
                BACKEND_RUNNER.interrupted_tests(state, source)
            journal.write_text(json.dumps({'source_sha': 'b' * 40}) + '\n')
            with self.assertRaisesRegex(ValueError, 'source'):
                BACKEND_RUNNER.interrupted_tests(state, source)

    def test_accepted_attempt_keeps_new_history_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir()
            with BACKEND_RUNNER.preserve_baseline(state) as checkpoint:
                (state / 'inputs.json').write_text('new history')
                checkpoint.accepted = True
            self.assertEqual((state / 'inputs.json').read_text(), 'new history')
            (state / 'testmon.sqlite-wal').symlink_to(state / 'inputs.json')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                with BACKEND_RUNNER.preserve_baseline(state):
                    self.fail('must reject a linked sidecar')

    def test_assert_failure_partial_history_requires_matching_report_and_database(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = 'a' * 40
            db = state / 'testmon.sqlite'
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE evidence(value INTEGER)')
                connection.execute('INSERT INTO evidence VALUES (1)')
            with sqlite3.connect(db) as connection:
                db_hash = hashlib.sha256('\n'.join(connection.iterdump()).encode()).hexdigest()
            meta = {'schema': 2, 'source_sha': source, 'tracking_sha256': db_hash, 'context': 'b' * 64,
                    'tests': {'case::passed': {'passed': True}, 'case::failed': {'passed': False}}}
            metadata = state / 'inputs.json'
            metadata.write_text(json.dumps(meta))
            selection = {'source_sha': source, 'exitstatus': 1, 'normal_completion': True,
                         'assertion_failures': 1, 'selected': ['case::passed', 'case::failed'],
                         'executed': ['case::passed', 'case::failed'],
                         'completed': ['case::passed', 'case::failed'], 'passed': ['case::passed'],
                         'failed': ['case::failed'], 'skipped': [], 'reused': [],
                         'incomplete': [], 'not_started': [], 'tracking_sha256': db_hash,
                         'inputs_sha256': hashlib.sha256(metadata.read_bytes()).hexdigest(),
                         'context_sha256': 'b' * 64}
            self.assertTrue(BACKEND_RUNNER.valid_partial_history(state, selection, source))
            for changes in ({'normal_completion': False}, {'incomplete': ['case::passed']},
                            {'assertion_failures': 0}, {'exitstatus': 2}, {'inputs_sha256': '0' * 64}):
                with self.subTest(changes=changes):
                    self.assertFalse(BACKEND_RUNNER.valid_partial_history(state, {**selection, **changes}, source))
            with sqlite3.connect(db) as connection:
                connection.execute('UPDATE evidence SET value = 2')
            self.assertFalse(BACKEND_RUNNER.valid_partial_history(state, selection, source))

    def test_invalidated_rollback_does_not_restore_reusable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            state.mkdir()
            (state / 'inputs.json').write_text('previous history')
            with BACKEND_RUNNER.preserve_baseline(state) as checkpoint:
                checkpoint.invalidate = True
                (state / 'inputs.json').write_text('interrupted history')
            self.assertFalse((state / 'inputs.json').exists())

    def test_partial_failure_resolves_only_completed_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'failed-tests.json').write_text(json.dumps(['case::passed', 'case::old']))
            BACKEND_RUNNER.retain_failures(state, state, ['case::failed'], resolved=['case::passed'])
            self.assertEqual(json.loads((state / 'failed-tests.json').read_text()), ['case::failed', 'case::old'])

    def test_interrupted_child_is_reaped_before_runner_returns(self):
        from unittest.mock import MagicMock
        process = MagicMock()
        process.pid = 43210
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        process.poll.return_value = None
        process.__enter__.return_value = process
        with patch.object(BACKEND_RUNNER.subprocess, 'Popen', return_value=process) as start, \
             patch.object(BACKEND_RUNNER.os, 'killpg') as stop:
            with self.assertRaises(KeyboardInterrupt):
                BACKEND_RUNNER.run_pytest(['python', '-m', 'pytest'], {'owned': 'environment'})
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        stop.assert_called_once_with(43210, BACKEND_RUNNER.signal.SIGINT)
        self.assertEqual(process.wait.call_count, 2)

    def test_term_reaps_real_child_even_when_parent_inherited_ignored_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / 'child.pid'
            child = f"import os,time; from pathlib import Path; Path({str(identity)!r}).write_text(str(os.getpid())); time.sleep(60)"
            parent = (
                "import os,signal,sys; signal.signal(signal.SIGINT,signal.SIG_IGN); "
                f"sys.path.insert(0,{str(Path(__file__).parent)!r}); "
                "import run_local_backend_tests as runner; "
                f"runner.run_pytest([sys.executable,'-c',{child!r}],os.environ.copy())")
            with subprocess.Popen([sys.executable, '-c', parent], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, start_new_session=True) as process:
                try:
                    deadline = time.monotonic() + 10
                    while not identity.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(identity.exists(), 'controlled child did not start')
                    process.send_signal(signal.SIGTERM)
                    process.communicate(timeout=10)
                    self.assertNotEqual(process.returncode, 0)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(identity.read_text()), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    if identity.exists():
                        try:
                            os.killpg(int(identity.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

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
                 patch.object(PG_OUTPUT_RUNNER, 'focused_staging', return_value=False), \
                 patch.object(PG_OUTPUT_RUNNER, 'run', side_effect=execute) as run, \
                 patch.object(PG_OUTPUT_RUNNER.traceback, 'print_exc'):
                self.assertEqual(PG_OUTPUT_RUNNER.main(), int(results != (0, 0)))
            self.assertEqual([call.args[3] for call in run.call_args_list], ['output', 'formal'])

    def test_focused_pg_keeps_fresh_head_and_transaction_boundary_without_history_matrix(self):
        with patch.dict(os.environ, {'SAWA_LOCAL_RUN_ID': '123', 'CI': '', 'GITHUB_ACTIONS': ''}), \
             patch.object(PG_OUTPUT_RUNNER, 'focused_staging', return_value=True), \
             patch.object(PG_OUTPUT_RUNNER, 'run', return_value=0) as run:
            self.assertEqual(PG_OUTPUT_RUNNER.main(), 0)
        self.assertEqual([call.args[3] for call in run.call_args_list], ['output'])

    def test_frontend_scope_preserves_auth_identity_edits_reads_and_real_browser_paths(self):
        with patch.dict(os.environ, {'SAWA_LOCAL_TEST_MODE': 'focused'}), \
             patch.object(RUNNER, 'git', return_value='codex/repair'):
            config = RUNNER.frontend_scope('config')
            e2e = RUNNER.frontend_scope('e2e')
        cjs = ('workflow-demand-read-contract', 'workflow-controls-source', 'workflow-route-identity',
                     'workflow-unsaved-boundary', 'modernization-workflow-action-dom', 'portal-auth-gate',
                     'portal-proxy-session', 'assistant-workflow-ui', 'assistant-drawing-proxy',
                     'pdf-upload-input-ownership', 'bulk-ocr-command-ownership', 'order-list-read-boundary',
                     'order-facility-read', 'hospital-job-status', 'login-flow', 'login-destination',
                     'current-ocr-operations')
        settings = (('google-auth-deploy', 'js'), ('ocr-first-request', 'mjs'),
                             ('ocr-queue-functional', 'mjs'), ('saved-workflow-readonly', 'mjs'),
                             ('system-status-safe-screenshot', 'mjs'), ('dashboard-monthly-read-status', 'mjs'),
                             ('ocr-admin-readonly', 'mjs'), ('hospital-readonly-pages', 'mjs'),
                             ('hospital-additional-readonly', 'mjs'),
                             ('system-status-demand-read', 'mjs'), ('operator-entry-readonly', 'mjs'),
                             ('operational-page-demand-read', 'mjs'))
        self.assertTrue({'tests/' + name + '.test.cjs' for name in cjs}
                         | {'tests/config/' + name + '.test.' + suffix for name, suffix in settings} <= set(config))
        frontend = RUNNER.ROOT / 'frontend'
        discovered = {str(path.relative_to(frontend)) for path in (frontend / 'tests').rglob('*')
                      if path.is_file() and '.test.' in path.name and path.suffix in {'.js', '.mjs', '.cjs'}}
        self.assertEqual(set(config), discovered)
        self.assertTrue({'tests/e2e/' + name + '.spec.ts' for name in (
            'api_proxy_stream', 'order_confirm', 'orders_index', 'pdf_upload', 'workflow_v2_sheet_edit')} <= set(e2e))
        self.assertEqual(set(e2e), {str(path.relative_to(frontend)) for path in (frontend / 'tests/e2e').rglob('*')
                                  if path.is_file() and '.spec.' in path.name and path.suffix in {'.ts', '.tsx', '.js', '.mjs', '.cjs'}})
        for branch in ('release/prod-repair', ''):
            with patch.dict(os.environ, {'SAWA_LOCAL_TEST_MODE': 'focused'}), \
                 patch.object(RUNNER, 'git', return_value=branch):
                self.assertIsNone(RUNNER.frontend_scope('config'))
                self.assertFalse(RUNNER.focused_staging())

    def test_invalid_frontend_scope_fails_closed_instead_of_omitting_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'infra/ci/staging_frontend_scope.json'
            target.parent.mkdir(parents=True)
            for names in ([], ['../outside.test.cjs'], ['tests/missing.test.cjs'], [[]]):
                target.write_text(json.dumps({'version': 1, 'scope': 'staging-core-and-page-repair',
                                              'config': names, 'e2e': ['tests/e2e/absent.spec.ts']}))
                with self.subTest(names=names), patch.object(RUNNER, 'ROOT', root), \
                     patch.object(RUNNER, 'focused_staging', return_value=True):
                    with self.assertRaises(ValueError):
                        RUNNER.frontend_scope('config')

    def test_frontend_context_preserves_safe_runtime_flags_without_forcing_full_selection(self):
        for options, node_path, expected in (
            ('', '', False),
            ('--use-openssl-ca', '', False),
            ('--use-openssl-ca --max-old-space-size=2048', '', False),
            ('--use-openssl-ca --max-old-space-size=2048', '/custom/modules', True),
            ('--require=./preload.cjs', '', True),
            ('--use-openssl-ca --import=./preload.mjs', '', True),
            ('--max-old-space-size=4096', '', True),
            ('"unterminated', '', True),
        ):
            environment = {'NODE_OPTIONS': options, 'NODE_PATH': node_path}
            preserved = dict(environment)
            with self.subTest(options=options, node_path=node_path):
                self.assertEqual(FRONTEND_RUNNER.requires_full_environment(environment), expected)
                self.assertEqual(environment, preserved)

    def test_frontend_contract_command_reports_scope_and_retains_the_full_entry(self):
        for modules in (None, ['tests/portal-auth-gate.test.cjs']):
            def execute(*args, **kwargs):
                if modules:
                    state = Path(kwargs['env']['SAWA_NODE_IMPACT_STATE'])
                    key = hashlib.sha256(('frontend/' + modules[0]).encode()).hexdigest()
                    (state / (key + '.json.report.json')).write_text(json.dumps({
                        'selected': ['frontend/' + modules[0] + '::case#0'], 'reused': []}))
                return subprocess.CompletedProcess([], 0)
            with tempfile.TemporaryDirectory() as directory, \
                 patch.dict(os.environ, {'SAWA_LOCAL_EVIDENCE_DIR': directory, 'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(FRONTEND_RUNNER, 'clean_tree', return_value=True), \
                 patch.object(FRONTEND_RUNNER, 'git', side_effect=lambda *args: directory if args == ('rev-parse', '--git-common-dir') else 'a' * 40), \
                 patch.object(FRONTEND_RUNNER, 'focused_staging', return_value=bool(modules)), \
                 patch.object(FRONTEND_RUNNER, 'frontend_scope', return_value=modules), \
                 patch.object(FRONTEND_RUNNER.subprocess, 'run', side_effect=execute) as run:
                self.assertEqual(FRONTEND_RUNNER.main(), 0)
                expected = ['node', '--import', str(FRONTEND_RUNNER.ROOT / 'infra/ci/node-impact/register.mjs'), '--test', '--test-concurrency=1', *modules] if modules else ['npm', 'run', 'test:config']
                self.assertEqual(run.call_args.args[0], expected)
                record = json.loads((Path(directory) / 'frontend-contracts/scope.json').read_text())
                self.assertEqual(record['modules'], modules)
                self.assertEqual(bool(record['outside_scope']), bool(modules))
                if modules:
                    selection = json.loads((Path(directory) / 'frontend-contracts/selection.json').read_text())
                    self.assertEqual(selection['source_sha'], 'a' * 40)
                    self.assertEqual(selection['selected'], ['frontend/' + modules[0] + '::case#0'])
                    self.assertEqual(selection['reused'], [])
                else:
                    self.assertEqual(run.call_args.kwargs['env']['SAWA_IMPACT_FULL'], '1')
                    self.assertNotIn('SAWA_NODE_IMPACT_STATE', run.call_args.kwargs['env'])

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
        for modules in (None, ['tests/e2e/orders_index.spec.ts']):
            self.check_e2e_isolation(modules)

    def check_e2e_isolation(self, modules):
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
                self.assertEqual(argv[3:-1], modules or [])
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
                 patch.object(E2E_RUNNER, 'frontend_scope', return_value=modules), \
                 patch.object(E2E_RUNNER, 'copy_dependencies', side_effect=shutil.copytree), \
                 patch.object(E2E_RUNNER.sys, 'argv', ['runner']), \
                 patch.object(E2E_RUNNER.subprocess, 'check_output', return_value=b'frontend/next-env.d.ts\0'), \
                 patch.object(E2E_RUNNER.subprocess, 'run', side_effect=next_run):
                self.assertEqual(E2E_RUNNER.main(), 0)
            self.assertEqual(original.read_text(), 'original source')
            record = json.loads((evidence / 'frontend-e2e/source.json').read_text())
            self.assertEqual(record['modules'], modules)
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

    def test_command_proof_rejects_changed_inputs_log_boolean_exit_and_unsigned_data(self):
        payload = {'kind': 'executed-local-command-v1', 'source_sha': 'a' * 40,
            'command_sha256': 'b' * 64, 'dependency_sha256': 'c' * 64,
            'log_sha256': 'd' * 64, 'input_guard_sha256': 'e' * 64, 'exit_code': 0}
        arguments = {'command_hash': 'b' * 64, 'dependency_hash': 'c' * 64, 'log_hash': 'd' * 64}
        with patch.dict(os.environ, {'SAWA_LOCAL_TEST_SIGNING_KEY': str(self.key),
                                    'SAWA_LOCAL_TEST_PUBLIC_KEY': self.public_key}):
            proof = sign(payload)
            self.assertEqual(COMMAND_EVIDENCE.validate_proof(proof, **arguments), payload)
            for key in arguments:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    COMMAND_EVIDENCE.validate_proof(proof, **{**arguments, key: 'f' * 64})
            with self.assertRaises(ValueError):
                COMMAND_EVIDENCE.validate_proof(sign({**payload, 'exit_code': False}), **arguments)
            with self.assertRaises(ValueError):
                COMMAND_EVIDENCE.validate_proof(payload, **arguments)

    def test_file_digest_reuse_requires_identical_stat_and_rehashes_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input'
            path.write_bytes(b'first')
            hashes = COMMAND_EVIDENCE.FileHashes()
            first = hashes.digest(path)
            with patch.object(COMMAND_EVIDENCE, 'file_digest', side_effect=AssertionError('unexpected rehash')):
                self.assertEqual(hashes.digest(path), first)
            path.write_bytes(b'other')
            self.assertNotEqual(hashes.digest(path), first)

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
            self.command_evidence.reset_mock()
            with tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'commands': [{'name': 'test', 'command': 'true'}]}))
                output = Path(directory) / 'evidence'
                with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                     patch.object(RUNNER, 'MANIFEST', manifest), patch.object(RUNNER, 'clean_tree', return_value=True), \
                     patch.object(RUNNER, 'require_frontend_dependencies'), \
                     patch.object(RUNNER, 'git', side_effect=['a' * 40, 'codex/fix', ('c' if changed else 'a') * 40, ('c' if changed else 'a') * 40]), \
                     patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], code)):
                    self.assertEqual(RUNNER.run(output), 1)
                self.assertFalse((output / 'receipt.json').exists())
                self.assertTrue((output / 'failed-run.json').exists())
                self.command_evidence.save.assert_not_called()
                if changed:
                    self.command_evidence.persist.assert_not_called()

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
            self.command_evidence.save.assert_called_once_with(commands[1]['command'], 'a' * 40,
                                                               output / '01-group-1.log')
            self.command_evidence.persist.assert_called_once()

    def test_identical_commands_execute_once_without_hiding_alias_failures(self):
        actual_run = subprocess.run
        for code in (0, 2):
            with self.subTest(exit_code=code), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'commands': [
                    {'name': name, 'command': 'true'} for name in ('first-alias', 'second-alias')]}))
                output = Path(directory) / 'evidence'
                executions = []
                def execute(command, **kwargs):
                    if command[0] == 'bash':
                        executions.append(command)
                        return subprocess.CompletedProcess(command, code)
                    return actual_run(command, **kwargs)
                with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': '',
                        'SAWA_LOCAL_TEST_SIGNING_KEY': str(self.key), 'SAWA_LOCAL_TEST_PUBLIC_KEY': self.public_key}), \
                     patch.object(RUNNER, 'MANIFEST', manifest), \
                     patch.object(RUNNER, 'require_frontend_dependencies'), \
                     patch.object(RUNNER, 'clean_tree', return_value=True), \
                     patch.object(RUNNER, 'git', return_value='a' * 40), \
                     patch.object(RUNNER.subprocess, 'run', side_effect=execute):
                    self.assertEqual(RUNNER.run(output), int(code != 0))
                    if code == 0:
                        receipt = verify_signed(json.loads((output / 'receipt.json').read_text()))
                    else:
                        self.assertFalse((output / 'receipt.json').exists())
                        receipt = json.loads((output / 'failed-run.json').read_text())
                self.assertEqual(len(executions), 1)
                self.assertEqual([item['exit_code'] for item in receipt['commands']], [code, code])
                self.assertEqual(receipt['commands'][0]['log_sha256'], receipt['commands'][1]['log_sha256'])
                timings = [json.loads(line) for line in (output / 'command-timings.jsonl').read_text().splitlines()]
                self.assertEqual([item['shared_batch_result'] for item in timings], [False, True])

    def test_full_receipt_does_not_inherit_external_test_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'version': 1, 'commands': [{'name': 'full', 'command': 'true'}]}))
            with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': '', 'SAWA_IMPACT_FULL': '0',
                                        'PYTHONPATH': 'foreign/source', 'PG_BIN': '/owned/pg16/bin',
                                        'PYTEST_ADDOPTS': '-k hidden --last-failed', 'PYTEST_PLUGINS': 'foreign_selector'}), \
                 patch.object(RUNNER, 'MANIFEST', manifest), \
                 patch.object(RUNNER, 'require_frontend_dependencies'), \
                 patch.object(RUNNER, 'clean_tree', return_value=True), \
                 patch.object(RUNNER, 'git', return_value='a' * 40), \
                 patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as execute:
                os.environ.pop('REFRESH_TEST_PG_BIN', None)
                self.assertEqual(RUNNER.run(Path(directory) / 'evidence', full=True), 1)
                environment = execute.call_args.kwargs['env']
                self.assertNotIn('PYTEST_ADDOPTS', environment)
                self.assertNotIn('PYTEST_PLUGINS', environment)
                self.assertEqual(environment['SAWA_IMPACT_FULL'], '1')
                self.assertEqual(environment['SAWA_LOCAL_TEST_MODE'], 'full')
                self.assertEqual(environment['PYTHONPATH'], os.pathsep.join(str(RUNNER.ROOT / item)
                    for item in ('backend', 'scripts', '.')))
                self.assertEqual(environment['REFRESH_TEST_PG_BIN'], '/owned/pg16/bin')
                self.assertEqual(os.environ['PYTEST_ADDOPTS'], '-k hidden --last-failed')
                self.assertEqual(os.environ['SAWA_IMPACT_FULL'], '0')

    def test_default_focused_and_release_full_are_controlled_by_runner(self):
        for branch, expected in [('codex/fix', 'focused'), ('release/prod-reviewed', 'full'), ('', 'full')]:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / 'manifest.json'
                manifest.write_text(json.dumps({'version': 1, 'commands': [{'name': 'only', 'command': 'true'}]}))
                with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': '', 'SAWA_LOCAL_TEST_MODE': 'foreign'}), \
                     patch.object(RUNNER, 'MANIFEST', manifest), \
                     patch.object(RUNNER, 'require_frontend_dependencies'), \
                     patch.object(RUNNER, 'clean_tree', return_value=True), \
                     patch.object(RUNNER, 'git', side_effect=lambda *args: branch if args[0] == 'branch' else 'a' * 40), \
                     patch.object(RUNNER.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as execute:
                    self.assertEqual(RUNNER.run(Path(directory) / 'evidence'), 1)
                    self.assertEqual(execute.call_args.kwargs['env']['SAWA_LOCAL_TEST_MODE'], expected)
                    self.assertEqual(execute.call_args.kwargs['env']['SAWA_IMPACT_FULL'], '1' if expected == 'full' else '0')

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
                 patch.object(RUNNER, 'git', side_effect=['a' * 40, 'codex/fix', 'c' * 40, 'a' * 40]), \
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
            payload['receipt_version'] = 2
            payload['backend_selection'] = {'source_sha': 'a' * 40, 'mode': 'impact',
                'executed': 2, 'reused': 10, 'report_sha256': 'e' * 64}
            envelope = sign(payload)
            for filename in ('deploy-dev.yml', 'deploy-stg.yml', 'deploy-prod.yml'):
                selected_payload = json.loads(json.dumps(payload))
                if filename == 'deploy-prod.yml':
                    selected_payload['backend_selection'].update(mode='full', executed=12, reused=0)
                envelope = sign(selected_payload)
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
                    if filename == 'deploy-prod.yml':
                        self.assertNotEqual(run({'LOCAL_TEST_RECEIPT': json.dumps(sign(payload))}), 0)
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

    def test_focused_receipt_is_explicit_and_cannot_satisfy_production_full(self):
        receipt = self.receipt()
        receipt['receipt_version'] = 2
        receipt['backend_selection'] = {'source_sha': 'a' * 40, 'mode': 'focused',
            'executed': 2, 'reused': 0, 'report_sha256': 'e' * 64}
        RUNNER.validate_receipt(receipt, 'a' * 40, 'b' * 64)
        with self.assertRaisesRegex(ValueError, 'production requires full'):
            RUNNER.validate_receipt(receipt, 'a' * 40, 'b' * 64, require_full=True)

    def test_signed_selection_rejects_wrong_source_invalid_count_and_full_reuse(self):
        receipt = self.receipt()
        receipt['receipt_version'] = 2
        receipt['backend_selection'] = {'source_sha': 'a' * 40, 'mode': 'impact',
            'executed': 2, 'reused': 10, 'report_sha256': 'e' * 64}
        RUNNER.validate_receipt(receipt, 'a' * 40, 'b' * 64)
        for key, value in [('source_sha', 'c' * 40), ('executed', True),
                           ('reused', -1), ('report_sha256', 'x' * 64), ('mode', 'full')]:
            changed = json.loads(json.dumps(receipt))
            changed['backend_selection'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                RUNNER.validate_receipt(changed, 'a' * 40, 'b' * 64)

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
