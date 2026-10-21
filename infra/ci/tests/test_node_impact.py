import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REGISTER = Path(__file__).resolve().parents[1] / "node-impact/register.mjs"


class NodeImpact(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / ".impact"
        self.env = {**os.environ, "SAWA_IMPACT_ROOT": str(self.root),
                    "SAWA_NODE_IMPACT_STATE": str(self.state), "SAWA_IMPACT_FULL": "0"}
        self.write("first.txt", "one")
        self.write("second.txt", "two")
        self.write("example.test.mjs", """
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('first', () => assert.equal(readFileSync('first.txt', 'utf8'), 'one'));
test('second', () => assert.equal(readFileSync('second.txt', 'utf8'), 'two'));
""")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)

    def write(self, name, text):
        (self.root / name).write_text(text)

    def run_tests(self, expected=0):
        result = subprocess.run(
            ["node", "--import", str(REGISTER), "--test", "example.test.mjs"],
            cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, (result.stdout + result.stderr)[-6000:])
        reports = list(self.state.glob("*.report.json"))
        self.assertEqual(len(reports), 1, result.stdout + result.stderr)
        return json.loads(reports[0].read_text())

    def test_individual_file_dependencies(self):
        first = self.run_tests()
        self.assertEqual(len(first["selected"]), 2)
        self.assertFalse(first["previous_context_matches"])
        self.assertEqual(first["previous_count"], 0)
        same = self.run_tests()
        self.assertEqual(len(same["reused"]), 2)
        self.assertTrue(same["previous_context_matches"])
        self.assertEqual(same["previous_count"], 2)
        self.write("first.txt", "broken")
        changed = self.run_tests(1)
        self.assertEqual(changed["selected"], ["example.test.mjs::first#0"],
                         [p.read_text() for p in self.state.glob("*.json")])
        self.assertEqual(changed["reused"], ["example.test.mjs::second#0"])
        self.assertEqual(changed["reasons"]["example.test.mjs::first#0"], "changed-dependency-or-failed-test")
        self.assertGreaterEqual(changed["durations_seconds"]["example.test.mjs::first#0"], 0)

    def test_configuration_change_explains_baseline_invalidation(self):
        self.run_tests()
        self.env["IMPACT_APPLICATION_SETTING"] = "changed"
        changed = self.run_tests()
        self.assertFalse(changed["previous_context_matches"])
        self.assertEqual(changed["previous_count"], 0)
        self.assertEqual(len(changed["selected"]), 2)
        self.assertTrue(all(reason == "missing-baseline-or-full" for reason in changed["reasons"].values()))

    def test_changed_test_executes_assertions(self):
        self.run_tests()
        source = (self.root / "example.test.mjs").read_text()
        self.write("example.test.mjs", source.replace("'two'", "'wrong'"))
        self.assertEqual(len(self.run_tests(1)["selected"]), 2)

    def test_missing_metadata_executes(self):
        self.run_tests()
        for file in self.state.glob("*.json"):
            file.unlink()
        self.assertEqual(len(self.run_tests()["selected"]), 2)

    def test_ci_environment_dependency_never_reused(self):
        self.env["GITHUB_REF"] = "main"
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("\ntest('environment', () => assert.equal(process.env.GITHUB_REF, 'main'));\n")
        self.run_tests()
        self.env["GITHUB_REF"] = "changed"
        self.assertIn("example.test.mjs::environment#0", self.run_tests(1)["selected"])

    def test_environment_mutation_preserves_native_behavior(self):
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("""
test('environment write', () => {
  process.env.IMPACT_EXAMPLE = 123;
  assert.equal(process.env.IMPACT_EXAMPLE, '123');
  delete process.env.IMPACT_EXAMPLE;
  assert.equal(process.env.IMPACT_EXAMPLE, undefined);
});
""")
        self.assertEqual(len(self.run_tests()["selected"]), 3)

    def test_subprocess_never_reused(self):
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("""
import {execFileSync} from 'node:child_process';
test('process', () => execFileSync(process.execPath, ['-e', '']));
""")
        self.run_tests()
        report = self.run_tests()
        self.assertIn("example.test.mjs::process#0", report["selected"])
        self.assertIn("subprocess", report["untracked"]["example.test.mjs::process#0"])

    def test_commonjs_loader(self):
        self.write("helper.cjs", "const test = require('node:test'); test('commonjs', () => {});")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("\nimport './helper.cjs';\n")
        self.assertEqual(len(self.run_tests()["selected"]), 3)
        self.assertEqual(len(self.run_tests()["reused"]), 3)

    def test_commonjs_transitive_dependency_change(self):
        self.write("leaf.cjs", "module.exports = () => 1;")
        self.write("operation.cjs", "module.exports = require('./leaf.cjs');")
        self.write("helper.cjs", """
const test = require('node:test');
const assert = require('node:assert/strict');
const value = require('./operation.cjs');
test('commonjs dependency', () => assert.equal(value(), 1));
""")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("\nimport './helper.cjs';\n")
        self.run_tests()
        self.assertEqual(len(self.run_tests()["reused"]), 3)
        self.write("leaf.cjs", "module.exports = () => 99;")
        self.assertIn("example.test.mjs::commonjs dependency#0", self.run_tests(1)["selected"])
