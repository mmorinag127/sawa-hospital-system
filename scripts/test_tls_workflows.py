from pathlib import Path
import unittest

import yaml

from tls_workflow_test_contract import STEP_HASHES
import hashlib


ROOT = Path(__file__).resolve().parents[1]


class TLSWorkflowTests(unittest.TestCase):
    def test_each_network_job_updates_ca_before_actions(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.safe_load(path.read_text())
            self.assertEqual(workflow["env"]["NODE_OPTIONS"], "--use-openssl-ca")
            self.assertEqual(workflow["env"]["CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE"], "/etc/ssl/certs/ca-certificates.crt")
            for name, job in workflow["jobs"].items():
                steps = job.get("steps", [])
                actions = [i for i, step in enumerate(steps) if "uses" in step]
                if not actions:
                    continue
                ca = [i for i, step in enumerate(steps) if step.get("name") == "Update TLS trust stores"]
                self.assertEqual(len(ca), 1, (path, name))
                self.assertLess(ca[0], min(actions), (path, name))
                self.assertEqual(hashlib.sha256(steps[ca[0]]["run"].encode()).hexdigest(), STEP_HASHES["Update TLS trust stores"])

    def test_python_and_uv_bootstrap(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            for job in yaml.safe_load(path.read_text())["jobs"].values():
                steps = job.get("steps", [])
                for i, step in enumerate(steps):
                    if step.get("uses", "").startswith("actions/setup-python@"):
                        following = steps[i + 1]
                        self.assertEqual(following["name"], "Update Python trust bundles")
                        self.assertEqual(hashlib.sha256(following["run"].encode()).hexdigest(), STEP_HASHES[following["name"]])
                    if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                        self.assertEqual(step["with"]["version"], "0.13.4")

    def test_images_are_checked_locally_and_signed_receipt_precedes_deploy(self):
        for file in ("deploy-dev.yml", "deploy-stg.yml", "deploy-prod.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / file).read_text())
            job = workflow['jobs'].get('source-gate', workflow['jobs'].get('deploy-dev'))
            gate = next(s for s in job['steps'] if 'local_hospital_test_runner.py --validate' in s.get('run', ''))
            self.assertEqual(gate['env']['SAWA_LOCAL_TEST_PUBLIC_KEY'], '${{ vars.SAWA_LOCAL_TEST_PUBLIC_KEY }}')
            self.assertNotIn('docker run ', (ROOT / '.github/workflows' / file).read_text())
        runtime = (ROOT / 'scripts/run_local_image_tls.py').read_text()
        self.assertIn('verify_tls_trust.py', runtime)
        self.assertIn('sawa.git_sha', runtime)
        self.assertIn('reject_hosted_runner()', runtime)
        frontend = (ROOT / 'scripts/run_local_frontend_image_proof.py').read_text()
        self.assertIn('verify_tls_trust.mjs', frontend)

    def test_all_agent_venvs_are_checked_after_installation(self):
        dockerfile = (ROOT / "agent_runtime/Dockerfile").read_text()
        self.assertIn("for environment in hospital gateway shift school-lunch; do", dockerfile)
        probe = "/opt/agent/$environment/bin/python /tmp/verify_tls_trust.py --online || exit 1"
        self.assertIn(probe, dockerfile)
        self.assertGreater(dockerfile.index(probe), dockerfile.rindex("/bin/pip install"))

    def test_all_hospital_grpc_runtimes_use_os_roots(self):
        expected = "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt"
        for path in ("backend/Dockerfile.base", "ocr_pipeline/Dockerfile", "agent_runtime/Dockerfile"):
            dockerfile = (ROOT / path).read_text()
            self.assertIn(expected, dockerfile)


if __name__ == "__main__":
    unittest.main()
