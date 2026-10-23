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


MODULE = Path(__file__).with_name("local_hospital_test_runner.py")
SPEC = importlib.util.spec_from_file_location("local_hospital_test_runner", MODULE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ReceiptContractTest(unittest.TestCase):
    def test_agent_preflight_requires_each_owned_environment_without_starting_tests(self):
        commands = {item['name']: item['command'] for item in json.loads(RUNNER.MANIFEST.read_text())['commands']}
        command = commands['agent-assistant-tests']
        required = ('SAWA_AGENT_TEST_POSTGRES_DSN', 'SAWA_AGENT_GATEWAY_PYTHON',
                    'SAWA_AGENT_NATIVE_SHIFT_PYTHON', 'SAWA_AGENT_NATIVE_SCHOOL_LUNCH_PYTHON')
        prepared = {name: 'synthetic-owned-prepared-value' for name in required}
        prefix, execution = command.split(' && ', 1)
        self.assertIn('"$SAWA_AGENT_GATEWAY_PYTHON" -m pytest experiments/agent_assistant/tests', execution)
        self.assertIn('SAWA_AGENT_NATIVE_TESTS=1', execution)
        self.assertNotIn('packaged-baseline', commands)
        for missing in (*required, None):
            environment = {key: value for key, value in prepared.items() if key != missing}
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', prefix],
                                    env=environment, capture_output=True)
            with self.subTest(missing=missing):
                self.assertEqual(result.returncode == 0, missing is None)

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

    def test_source_change_stops_remaining_groups_and_never_signs(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'version': 1, 'commands': [
                {'name': 'first', 'command': 'true'}, {'name': 'second', 'command': 'true'}]}))
            output = Path(directory) / 'evidence'
            with patch.dict(os.environ, {'CI': '', 'GITHUB_ACTIONS': ''}), \
                 patch.object(RUNNER, 'MANIFEST', manifest), \
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
