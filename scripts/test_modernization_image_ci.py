"""Offline workflow guards, not Docker/image execution proof."""
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ImageProofGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / ".github/workflows/modernization-image-ci.yml").read_text()
        cls.workflow = yaml.load(cls.raw, Loader=yaml.BaseLoader)
        cls.job = cls.workflow["jobs"]["frontend-image"]
        cls.script = next(step["run"] for step in cls.job["steps"]
                          if step.get("name") == "Build actual frontend image and verify vendored dependency")

    def test_trigger_identity_and_permissions(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch", "push"})
        self.assertEqual(self.workflow["on"]["push"]["branches"],
                         ["codex/modernization-image-ci-20261012"])
        self.assertIn("github.actor == github.repository_owner", self.job["if"])
        self.assertIn("refs/heads/codex/modernization-image-ci-20261012", self.job["if"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.job["runs-on"], "ubuntu-24.04")
        self.assertEqual(self.job["timeout-minutes"], "30")
        checkout = next(step for step in self.job["steps"]
                        if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["persist-credentials"], "false")

    def test_real_context_and_failure_evidence(self):
        for value in ("prepare_web_deploy_source.sh", "validate_web_deploy_source.sh",
                      'cmp frontend/Dockerfile', 'cmp frontend/package-lock.json',
                      'diff -r frontend/vendor', '--progress=plain',
                      '-f "$context/Dockerfile" "$context"', 'git rev-parse HEAD',
                      'image-inspect.json', 'exit-code.txt', 'vendor-parity.txt'):
            self.assertIn(value, self.script)
        artifact = self.job["steps"][-1]
        self.assertEqual(artifact["if"], "always()")
        self.assertEqual(artifact["with"]["if-no-files-found"], "error")

    def test_no_deploy_or_credentials(self):
        for forbidden in ("secrets.", "id-token", "google-github", "docker push",
                          "docker login", "gcloud", "environment:", "--privileged"):
            self.assertNotIn(forbidden, self.raw)
        self.assertIn("--network none --read-only --cap-drop ALL", self.script)
        self.assertIn("--entrypoint node", self.script)
        self.assertNotIn("NEXT_PUBLIC_GOOGLE_CLIENT_ID=", self.script)
        self.assertNotIn("API_PROXY_TARGET=", self.script)

    def test_bash_syntax(self):
        subprocess.run(["bash", "-n"], input=self.script, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
