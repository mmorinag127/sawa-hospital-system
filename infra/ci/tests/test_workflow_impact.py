from pathlib import Path
import hashlib
import re
import tempfile
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

    def test_cache_key_is_identical_before_and_after_generated_bytecode(self):
        for name in ("backend", "frontend"):
            steps = self.jobs[name]["steps"]
            restore = next(s for s in steps if s.get("uses") == "actions/cache/restore@v4")
            save = next(s for s in steps if s.get("uses") == "actions/cache/save@v4")
            self.assertEqual(restore["with"]["key"], save["with"]["key"])
            patterns = re.findall("'([^']+)'", restore["with"]["key"].split("hashFiles(")[1].split(")")[0])
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "infra/ci/tracker.py"
                source.parent.mkdir(parents=True)
                source.write_text("original")
                node = root / "infra/ci/node-impact/core.cjs"
                node.parent.mkdir()
                node.write_text("original")
                def fingerprint():
                    files = sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()})
                    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).digest()
                before = fingerprint()
                bytecode = source.parent / "__pycache__/tracker.cpython-311.pyc"
                bytecode.parent.mkdir()
                bytecode.write_bytes(b"generated runtime output")
                self.assertEqual(before, fingerprint())
                source.write_text("changed")
                self.assertNotEqual(before, fingerprint())
                source.write_text("original")
                node.write_text("changed")
                self.assertNotEqual(before, fingerprint())


if __name__ == "__main__":
    unittest.main()
