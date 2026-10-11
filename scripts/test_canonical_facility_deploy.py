import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CanonicalDeployTest(unittest.TestCase):
    def environment(self, directory, uri):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"),
                   PYTHONDONTWRITEBYTECODE="1", FACILITY_MASTER_URI=uri,
                   FACILITY_MASTER_PATH="/not-a-runtime-source", K_SERVICE="worker")
        env.update(GITHUB_ACTIONS="true", GITHUB_RUN_ID="synthetic-test",
                   GOOGLE_CLIENT_ID="synthetic-client", GOOGLE_IDENTITY_SERVICE_ACCOUNT="synthetic",
                   WEB_URL="https://synthetic.invalid", DEPLOY_ID_TOKEN="synthetic",
                   ENSURE_GCLOUD_AUTH="/usr/bin/true", RUN_LOCAL_REGRESSION="0",
                   WORKER_EXTRA_ENV_VARS="EXISTING_SETTING=preserved", TEMPLATE_BUCKET="synthetic",
                   CALLS=str(directory / "calls.json"), PATH=f"{directory}:{env['PATH']}")
        return env

    def test_missing_and_invalid_stop_before_any_gcloud(self):
        for uri in ("", "file:///tmp/master.json", "gs://bucket", "gs://bucket/key?query", "gs://bucket/key#fragment"):
            with self.subTest(uri=uri), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                stub = directory / "gcloud"
                stub.write_text('#!/bin/sh\nprintf invoked > "$CALLS"\nexit 1\n')
                stub.chmod(0o700)
                result = subprocess.run(["bash", str(ROOT / "scripts/deploy_worker_prod_with_checks.sh"),
                                         "synthetic-image", "synthetic-order"],
                                        env=self.environment(directory, uri), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("facility_master_", result.stderr)
                self.assertFalse((directory / "calls.json").exists())

    def test_exact_uri_and_existing_env_transport(self):
        for uri in ("gs://synthetic-bucket/master.json", "gs://synthetic-bucket/a,b|c= d.json"):
            with self.subTest(uri=uri), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "curl").write_text('#!/bin/sh\nprintf 200\n')
                (directory / "curl").chmod(0o700)
                stub = directory / "gcloud"
                stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                                'if sys.argv[1:3] == ["run", "deploy"]:\n'
                                ' open(os.environ["CALLS"],"w").write(json.dumps(sys.argv[1:]))\n'
                                ' sys.exit(1)\n')
                stub.chmod(0o700)
                result = subprocess.run(["bash", str(ROOT / "scripts/deploy_worker_prod_with_checks.sh"),
                                         "synthetic-image", "synthetic-order"],
                                        env=self.environment(directory, uri), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)  # double never deploys
                args = json.loads((directory / "calls.json").read_text())
                self.assertIn("--update-env-vars=EXISTING_SETTING=preserved", args)
                self.assertIn("--update-env-vars=TEMPLATE_BUCKET=synthetic", args)
                self.assertFalse(any(arg.startswith("--set-env-vars") for arg in args))
                argument = next(arg for arg in args if "FACILITY_MASTER_URI=" in arg)
                encoded = argument.split("=", 1)[1]
                delimiter, payload = encoded[1:].split("^", 1)
                self.assertNotIn(delimiter, uri)
                self.assertEqual(payload, "FACILITY_MASTER_URI=" + uri)

    def test_workflows_use_explicit_variable_and_shared_source_gate(self):
        for target in ("stg", "prod"):
            source = (ROOT / f".github/workflows/deploy-{target}.yml").read_text()
            self.assertIn("FACILITY_MASTER_URI: ${{ vars." + target.upper() + "_FACILITY_MASTER_URI }}", source)
            gate = source.split("  source-gate:", 1)[1].split("\n  build-", 1)[0]
            self.assertIn("PYTHONPATH=backend python3 scripts/check_canonical_facility_deploy.py", gate)

    def test_terraform_requires_operator_uri_without_local_fallback(self):
        for target in ("stg", "prod"):
            directory = ROOT / "infra/terraform/envs" / target
            main = (directory / "main.tf").read_text()
            variables = (directory / "variables.tf").read_text()
            block = variables.split('variable "facility_master_uri" {', 1)[1].split('\nvariable ', 1)[0]
            self.assertIn("type        = string", block)
            self.assertIn("nullable    = false", block)
            self.assertIn('trimspace(var.facility_master_uri) != ""', block)
            self.assertNotIn("default", block)
            self.assertRegex(main, r"FACILITY_MASTER_URI\s*= var.facility_master_uri")
            self.assertNotIn("FACILITY_MASTER_PATH", main)

    def test_staging_only_backend_changes_require_uri_after_detection(self):
        source = (ROOT / ".github/workflows/deploy-stg.yml").read_text()
        gate = source.split("  source-gate:", 1)[1].split("\n  automation-bootstrap:", 1)[0]
        validation = "      - name: Validate canonical facility runtime source"
        self.assertEqual(source.count(validation), 1)
        self.assertLess(gate.index("name: Detect changed deploy surfaces"), gate.index(validation))
        self.assertEqual(gate.split(validation, 1)[1].strip(),
                         "if: steps.changes.outputs.backend_changed == 'true'\n"
                         "        run: PYTHONPATH=backend python3 scripts/check_canonical_facility_deploy.py")
        self.assertLess(source.index(validation), source.index("\n  automation-bootstrap:"))
        production = (ROOT / ".github/workflows/deploy-prod.yml").read_text()
        self.assertIn(validation + "\n        run: PYTHONPATH=backend python3 scripts/check_canonical_facility_deploy.py", production)


if __name__ == "__main__":
    unittest.main()
