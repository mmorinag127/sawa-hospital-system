from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]


class WorkflowImpact(unittest.TestCase):
    def setUp(self):
        self.jobs = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(),
                              Loader=yaml.BaseLoader)["jobs"]

    def test_only_successful_develop_push_publishes_dependencies(self):
        for name in ("backend", "frontend"):
            saves = [s for s in self.jobs[name]["steps"] if s.get("uses") == "actions/cache/save@v4"]
            self.assertEqual(len(saves), 1)
            self.assertEqual(saves[0]["if"],
                             "success() && github.event_name == 'push' && github.ref == 'refs/heads/develop'")
            self.assertEqual(self.jobs[name]["env"]["SAWA_IMPACT_FULL"],
                             "${{ (inputs.full_tests || startsWith(github.ref, 'refs/heads/release/prod-')) && '1' || '0' }}")

    def test_dependency_state_separated_and_tooling_versioned(self):
        for name in ("backend", "frontend"):
            restore = next(s for s in self.jobs[name]["steps"] if s.get("uses") == "actions/cache/restore@v4")
            self.assertIn("infra/ci/**", restore["with"]["key"])
            self.assertIn(".github/workflows/ci.yml", restore["with"]["key"])
            if name == "backend":
                self.assertIn("${{ matrix.suite }}", restore["with"]["path"])

    def test_other_checks_are_not_impact_filtered(self):
        frontend = self.jobs["frontend"]
        self.assertNotIn("NODE_OPTIONS", frontend["env"])
        for name in ("Frontend lint", "Frontend typecheck", "Frontend dependency audit"):
            step = next(s for s in frontend["steps"] if s.get("name") == name)
            self.assertNotIn("if", step)
            self.assertNotIn("NODE_OPTIONS", step.get("env", {}))


if __name__ == "__main__":
    unittest.main()
