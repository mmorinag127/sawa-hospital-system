import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from runner_identity_cases import RUN_IDENTITIES, runner_values


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

    def run_tests(self, expected=0, *, isolated=True):
        result = subprocess.run(
            ["node", "--import", str(getattr(self, 'register', REGISTER)), *(["--test"] if isolated else []), "example.test.mjs"],
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

    def test_native_reads_do_not_invoke_or_trust_error_stack_formatters(self):
        self.write('example.test.mjs', """
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readSync, openSync, closeSync} from 'node:fs';
function forgedFormatter() { return 'Error\\n    at tryReadSync (node:fs:416:20)'; }
test('path', () => {
  const previous = Error.prepareStackTrace;
  let called = false;
  Error.prepareStackTrace = () => { called = true; return forgedFormatter(); };
  try {
    assert.equal(readFileSync('first.txt').toString(), 'one');
    assert.equal(called, false);
  } finally { Error.prepareStackTrace = previous; }
});
test('foreign', () => {
  const previous = Error.prepareStackTrace;
  Error.prepareStackTrace = forgedFormatter;
  const fd = openSync('first.txt', 'r');
  try { assert.equal(readSync(fd, Buffer.alloc(3), 0, 3, 0), 3); }
  finally { closeSync(fd); Error.prepareStackTrace = previous; }
});
""")
        self.run_tests()
        report = self.run_tests()
        self.assertEqual(report['reused'], ['example.test.mjs::path#0'])
        self.assertEqual(report['selected'], ['example.test.mjs::foreign#0'])
        self.assertIn('file-descriptor-input', report['untracked']['example.test.mjs::foreign#0'])

    def test_direct_descriptor_reads_execute_again_and_preserve_results(self):
        self.write('example.test.mjs', """
import test from 'node:test';
import assert from 'node:assert/strict';
import {openSync, closeSync, read, readSync, readv, readvSync} from 'node:fs';
for (const method of ['read', 'readSync', 'readv', 'readvSync']) {
  test(method, async () => {
    const fd = openSync('first.txt', 'r');
    const buffer = Buffer.alloc(3);
    try {
      let count;
      if (method === 'readSync') count = readSync(fd, buffer, 0, 3, 0);
      else if (method === 'readvSync') count = readvSync(fd, [buffer], 0);
      else count = await new Promise((resolve, reject) => {
        const done = (error, value) => error ? reject(error) : resolve(value);
        if (method === 'read') read(fd, buffer, 0, 3, 0, done);
        else readv(fd, [buffer], 0, done);
      });
      assert.equal(count, 3);
      assert.equal(buffer.toString(), 'one');
    } finally { closeSync(fd); }
  });
}
""")
        self.run_tests()
        report = self.run_tests()
        for name in ('read', 'readSync', 'readv', 'readvSync'):
            node = f'example.test.mjs::{name}#0'
            self.assertIn(node, report['selected'])
            self.assertIn('file-descriptor-input', report['untracked'][node])

    def test_failed_run_reexecutes_the_entire_module(self):
        self.write('first.txt', 'broken')
        self.run_tests(1)
        report = self.run_tests(1)
        self.assertEqual(report['selected'], ['example.test.mjs::first#0', 'example.test.mjs::second#0'])
        self.assertEqual(report['reused'], [])

    def test_timed_out_callback_is_not_reused_after_it_returns(self):
        self.write('example.test.mjs', """
import test from 'node:test';
import assert from 'node:assert/strict';
test('assertion failure', () => assert.fail('failure'));
test('late callback', {timeout: 10}, async () => {
  await new Promise(resolve => setTimeout(resolve, 50));
});
""")
        self.run_tests(1)
        report = self.run_tests(1)
        self.assertEqual(report['reused'], [])
        self.assertEqual(len(report['selected']), 2)

    def test_read_file_sync_foreign_descriptor_is_not_reused(self):
        self.write('example.test.mjs', """
import test from 'node:test';
import assert from 'node:assert/strict';
import {openSync, closeSync, readFileSync} from 'node:fs';
test('foreign fd', () => {
  const fd = openSync('first.txt', 'r');
  try { assert.equal(readFileSync(fd, 'utf8'), 'one'); }
  finally { closeSync(fd); }
});
""")
        self.run_tests()
        report = self.run_tests()
        self.assertEqual(report['reused'], [])
        self.assertIn('file-descriptor-input', report['untracked']['example.test.mjs::foreign fd#0'])

    def test_explicit_process_exit_invalidates_successful_case_history(self):
        self.write('example.test.mjs', """
import test from 'node:test';
test('success', () => {});
test('exit', () => process.exit(1));
""")
        self.run_tests(1)
        report = self.run_tests(1)
        self.assertEqual(report['reused'], [])

    def test_uncaught_test_error_invalidates_successful_case_history(self):
        self.write('example.test.mjs', """
import test from 'node:test';
test('success', () => {});
test('error', () => { throw new Error('unexpected'); });
""")
        self.run_tests(1)
        report = self.run_tests(1)
        self.assertEqual(report['reused'], [])

    def test_unhandled_rejection_invalidates_successful_case_history(self):
        self.write('example.test.mjs', """
import test from 'node:test';
test('success', () => {});
test('rejection', async () => {
  Promise.reject(new Error('unexpected'));
  await new Promise(resolve => setImmediate(resolve));
});
""")
        self.run_tests(1)
        report = self.run_tests(1)
        self.assertEqual(report['reused'], [])

    def test_path_read_does_not_hide_descriptor_reads_in_options_getters(self):
        self.write('example.test.mjs', """
import test from 'node:test';
import assert from 'node:assert/strict';
import {openSync, closeSync, readFileSync, readSync} from 'node:fs';
test('nested descriptor', () => {
  const fd = openSync('second.txt', 'r');
  try {
    const options = {get encoding() {
      const buffer = Buffer.alloc(3);
      assert.equal(readSync(fd, buffer, 0, 3, 0), 3);
      assert.equal(buffer.toString(), 'two');
      return 'utf8';
    }};
    assert.equal(readFileSync('first.txt', options), 'one');
  } finally {closeSync(fd);}
});
""")
        self.run_tests()
        report = self.run_tests()
        node = 'example.test.mjs::nested descriptor#0'
        self.assertIn(node, report['selected'])
        self.assertIn('file-descriptor-input', report['untracked'][node])

    def test_unknown_tracker_directory_input_is_not_excluded(self):
        tracker = self.root / 'tracker'
        shutil.copytree(REGISTER.parent, tracker)
        self.register = tracker / 'register.mjs'
        (tracker / 'extra.txt').write_text('one')
        self.write('example.test.mjs', """
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('extra', () => assert.equal(readFileSync('tracker/extra.txt', 'utf8'), 'one'));
""")
        self.run_tests()
        report = self.run_tests()
        self.assertIn('example.test.mjs::extra#0', report['selected'])
        self.assertIn('untracked-file', report['untracked']['example.test.mjs::extra#0'])

    def test_tracker_bytes_invalidate_context(self):
        tracker = self.root / 'tracker'
        shutil.copytree(REGISTER.parent, tracker)
        self.register = tracker / 'register.mjs'
        self.run_tests()
        self.assertEqual(len(self.run_tests()['reused']), 2)
        with (tracker / 'test.cjs').open('a') as stream:
            stream.write('\n// changed tracker bytes\n')
        report = self.run_tests()
        self.assertFalse(report['previous_context_matches'])
        self.assertEqual(len(report['selected']), 2)
        self.assertIn('configs', report['changed_context_parts'])

    def test_configuration_change_explains_baseline_invalidation(self):
        self.env["IMPACT_APPLICATION_SETTING"] = "original-private-value"
        self.run_tests()
        self.env["IMPACT_APPLICATION_SETTING"] = "changed-private-value"
        changed = self.run_tests()
        self.assertFalse(changed["previous_context_matches"])
        self.assertEqual(changed["previous_count"], 0)
        self.assertEqual(len(changed["selected"]), 2)
        self.assertTrue(all(reason == "missing-baseline-or-full" for reason in changed["reasons"].values()))
        self.assertEqual(changed["changed_context_parts"], ["environment"])
        for file in self.state.glob("*.json"):
            self.assertNotIn("private-value", file.read_text())

    def test_absent_file_predicate_is_revalidated_without_global_rerun(self):
        with (self.root / 'example.test.mjs').open('a') as stream:
            stream.write("\nimport {existsSync} from 'node:fs';\n"
                         "test('absent', () => assert.equal(existsSync('later.txt'), false));\n")
        self.run_tests()
        self.assertIn('example.test.mjs::absent#0', self.run_tests()['reused'])
        self.write('later.txt', 'new input')
        changed = self.run_tests(1)
        self.assertIn('example.test.mjs::absent#0', changed['selected'])
        self.assertNotIn('example.test.mjs::absent#0', changed['reused'])

    def test_stat_returns_real_stats_and_permission_changes_execute_assertions(self):
        self.write('metadata.txt', 'metadata input')
        (self.root / 'metadata.txt').chmod(0o600)
        with (self.root / 'example.test.mjs').open('a') as stream:
            stream.write("\nimport {statSync, Stats} from 'node:fs';\n"
                         "test('metadata', () => { const value = statSync('metadata.txt'); "
                         "assert.equal(value instanceof Stats, true); "
                         "assert.equal(value.isFile, Stats.prototype.isFile); "
                         "assert.equal(value.mode & 0o777, 0o600); });\n")
        self.run_tests()
        self.assertIn('example.test.mjs::metadata#0', self.run_tests()['reused'])
        (self.root / 'metadata.txt').chmod(0o400)
        changed = self.run_tests(1)
        self.assertIn('example.test.mjs::metadata#0', changed['selected'])
        self.assertNotIn('example.test.mjs::metadata#0', changed['reused'])

    def test_byte_paths_are_not_reinterpreted_for_reuse(self):
        with (self.root / 'example.test.mjs').open('a') as stream:
            stream.write("\nimport {existsSync} from 'node:fs';\n"
                         "test('byte path', () => { "
                         "assert.equal(existsSync(Buffer.from('first.txt')), true); "
                         "assert.equal(existsSync(Buffer.from([0xff, 0xfe])), false); });\n")
        self.run_tests()
        result = self.run_tests()
        self.assertIn('example.test.mjs::byte path#0', result['selected'])
        self.assertNotIn('example.test.mjs::byte path#0', result['reused'])

    def test_buffer_content_reads_remain_untracked(self):
        with (self.root / 'example.test.mjs').open('a') as stream:
            stream.write("\ntest('byte read', () => assert.equal(readFileSync(Buffer.from('first.txt'), 'utf8'), 'one'));\n")
        self.run_tests()
        result = self.run_tests()
        self.assertIn('example.test.mjs::byte read#0', result['selected'])
        self.assertNotIn('example.test.mjs::byte read#0', result['reused'])

    def test_symlink_parent_traversal_is_not_lexically_normalized(self):
        (self.root / 'native/child').mkdir(parents=True)
        (self.root / 'link').symlink_to(self.root / 'native/child', target_is_directory=True)
        with (self.root / 'example.test.mjs').open('a') as stream:
            stream.write("\nimport {existsSync} from 'node:fs';\n"
                         "test('link parent', () => assert.equal(existsSync('link/../chosen.txt'), false));\n")
        self.run_tests()
        (self.root / 'native/chosen.txt').write_text('actual OS target')
        result = self.run_tests(1)
        self.assertIn('example.test.mjs::link parent#0', result['selected'])
        self.assertNotIn('example.test.mjs::link parent#0', result['reused'])

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

    def test_unread_runner_identities_do_not_invalidate_tests(self):
        self.env.update(runner_values())
        self.run_tests()
        self.env.update(runner_values(second=True))
        self.assertEqual(len(self.run_tests()["reused"]), 2)

    def test_each_runner_identity_read_always_executes(self):
        expected = runner_values()
        with (self.root / "example.test.mjs").open("a") as stream:
            for key in RUN_IDENTITIES:
                stream.write(f"\ntest('{key}', () => assert.equal(process.env.{key}, {json.dumps(expected[key])}));\n")
        self.env.update(expected)
        self.run_tests(isolated=False)
        self.env.update(runner_values(second=True))
        report = self.run_tests(1, isolated=False)
        for key in RUN_IDENTITIES:
            self.assertIn(f"example.test.mjs::{key}#0", report["selected"])
            self.assertIn("volatile-environment", report["untracked"][f"example.test.mjs::{key}#0"])

    def test_node_startup_options_remain_part_of_context(self):
        self.env['NODE_OPTIONS'] = '--max-old-space-size=256'
        self.run_tests()
        self.env['NODE_OPTIONS'] = '--max-old-space-size=384'
        self.assertEqual(len(self.run_tests()['selected']), 2)

    def test_native_worker_identity_read_always_executes(self):
        self.env["NODE_TEST_WORKER_ID"] = "inherited-worker"
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("\ntest('native worker', () => assert.equal(typeof process.env.NODE_TEST_WORKER_ID, 'string'));\n")
        self.run_tests()
        report = self.run_tests()
        self.assertIn("example.test.mjs::native worker#0", report["selected"])
        self.assertIn("volatile-environment", report["untracked"]["example.test.mjs::native worker#0"])

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

    def test_reflective_runner_environment_reads_always_execute(self):
        self.env["INVOCATION_ID"] = "first-run"
        with (self.root / "example.test.mjs").open("a") as stream:
            stream.write("""
test('descriptor', () => assert.equal(Object.getOwnPropertyDescriptor(process.env, 'INVOCATION_ID').value, 'first-run'));
test('presence', () => assert.ok('INVOCATION_ID' in process.env));
test('enumeration', () => assert.ok(Object.keys(process.env).includes('INVOCATION_ID')));
""")
        self.run_tests()
        self.env["INVOCATION_ID"] = "second-run"
        report = self.run_tests(1)
        for name in ("descriptor", "presence", "enumeration"):
            self.assertIn(f"example.test.mjs::{name}#0", report["selected"])
            self.assertIn("volatile-environment", report["untracked"][f"example.test.mjs::{name}#0"])

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
