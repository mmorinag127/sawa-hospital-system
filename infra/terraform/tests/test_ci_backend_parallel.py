"""Keep the parallel CI split equivalent to its reviewed serial predecessor."""
import hashlib
from fnmatch import fnmatch
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
BASELINE = {
    "Run backend tests (US1-4)": ("us1-4", "d8c3f72cfe9f78d4f1478199c472fd67c0a72006c6cdd1b0bc93eaf41ee0d074"),
    "Verify initial saved-source preservation": ("saved-source", "58baf3221466a87d078c7f949f6c7460873d2318282f58fe904222b1799f77dd"),
    "Verify OCR review-resolution root regression": ("review-resolution", "1f34337f75700bf4f36b446679cd7139174301c81175004395b839205cd2e02b"),
}


class ParallelCI(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
        self.backend = self.workflow["jobs"]["backend"]

    def test_all_original_test_commands_and_options_preserved(self):
        steps = {s.get("name"): s for s in self.backend["steps"]}
        self.assertEqual(self.backend["strategy"]["matrix"]["suite"], [v[0] for v in BASELINE.values()])
        self.assertEqual(self.backend["strategy"]["fail-fast"], "false")
        for name, (suite, digest) in BASELINE.items():
            step = steps[name]
            self.assertEqual(step["if"], f"matrix.suite == '{suite}'")
            self.assertEqual(step["working-directory"], "backend")
            command = step["run"][step["run"].index("python -m pytest"):]
            self.assertEqual(hashlib.sha256(command.encode()).hexdigest(), digest)
            self.assertIn("mkdir -p .logs", step["run"])

    def test_each_matrix_job_retains_isolated_postgres_and_env(self):
        self.assertEqual(self.backend["services"]["postgres"]["image"], "postgres:16")
        self.assertEqual(self.backend["env"]["DB_URI"], "sqlite:///./test.db")
        self.assertIn("localhost:5432/bootstrap_test", self.backend["env"]["TEST_BOOTSTRAP_DB_URI"])
        artifacts = next(s for s in self.backend["steps"] if s.get("name") == "Upload backend logs")
        self.assertEqual(artifacts["if"], "always()")
        self.assertEqual(artifacts["with"]["name"], "backend-test-logs-${{ matrix.suite }}")
        self.assertEqual(artifacts["with"]["include-hidden-files"], "true")

    def test_required_backend_check_fails_on_failure_cancel_or_skip(self):
        gate = self.workflow["jobs"]["backend-required"]
        self.assertEqual(gate["name"], "backend")
        self.assertEqual(gate["if"], "always()")
        self.assertEqual(gate["needs"], ["backend"])
        step = gate["steps"][0]
        self.assertEqual(step["env"]["RESULT"], "${{ needs.backend.result }}")
        self.assertEqual(step["run"], 'test "$RESULT" = success')
        for result in ("success", "failure", "cancelled", "skipped", ""):
            completed = subprocess.run(["bash", "-c", step["run"]], env={"RESULT": result})
            self.assertEqual(completed.returncode == 0, result == "success")

    def test_cancellation_only_supersedes_same_pr(self):
        concurrency = self.workflow["concurrency"]
        self.assertEqual(concurrency["group"], "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}")
        self.assertEqual(concurrency["cancel-in-progress"], "${{ github.event_name == 'pull_request' }}")
        # Distinct PRs/workflows never cancel each other; push runs have unique IDs.
        key = lambda workflow, pr, run: f"{workflow}-{pr or run}"
        self.assertEqual(key("CI", 117, 1), key("CI", 117, 2))
        self.assertNotEqual(key("CI", 117, 1), key("CI", 116, 2))
        self.assertNotEqual(key("CI", None, 1), key("CI", None, 2))
        self.assertNotEqual(key("CI", 117, 1), key("Deploy Staging", 117, 1))

    def test_ci_only_changes_do_not_trigger_staging_deploy(self):
        deploy = yaml.load((ROOT / ".github/workflows/deploy-stg.yml").read_text(), Loader=yaml.BaseLoader)
        paths = deploy["on"]["push"]["paths"]
        for changed in (".github/workflows/ci.yml", "infra/terraform/tests/test_ci_backend_parallel.py",
                        "docs/ci-review-20261021.md"):
            self.assertFalse(any(fnmatch(changed, pattern) for pattern in paths), changed)


if __name__ == "__main__":
    unittest.main()
