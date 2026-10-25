import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1]


class PerTestImpact(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / ".impact"
        self.env = {
            **os.environ,
            "PYTHONPATH": str(PLUGIN) + os.pathsep + str(self.root),
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "SAWA_IMPACT_ROOT": str(self.root),
            "SAWA_IMPACT_STATE": str(self.state),
            "TESTMON_DATAFILE": str(self.state / "testmon.sqlite"),
            "SAWA_IMPACT_FULL": "0",
            "SAWA_IMPACT_SPLIT_RUNS": "0",
        }
        self.write("leaf.py", "def value():\n    return 1\n")
        self.write("operations.py", "import leaf\ndef first():\n    return leaf.value()\ndef second():\n    return 2\n")
        self.write("data.txt", "first")
        self.write("test_example.py", """
from pathlib import Path
from operations import first, second
def test_first():
    assert first() == 1
def test_second():
    assert second() == 2
def test_data():
    assert Path('data.txt').read_text() == 'first'
""")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)

    def write(self, name, text):
        (self.root / name).write_text(text)

    def run_tests(self, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "pytest_impact",
             "-p", "pytest-testmon", "--testmon", "-q", "test_example.py"],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, expected, (result.stdout + result.stderr)[-6000:])
        return json.loads((self.state / "selection.json").read_text())

    def test_unchanged_skips_individual_tests_and_indirect_change_runs_only_affected(self):
        first = self.run_tests()
        self.assertEqual(len(first["selected"]), 3)
        self.assertEqual(first["untracked"], {}, first)
        same = self.run_tests()
        self.assertEqual(same["selected"], [], same)
        self.assertEqual(len(same["reused"]), 3)
        self.write("leaf.py", "def value():\n    return 99\n")
        changed = self.run_tests(expected=1)
        self.assertIn("test_example.py::test_first", changed["selected"])
        self.assertNotIn("test_example.py::test_second", changed["selected"])
        self.assertNotIn("test_example.py::test_data", changed["selected"])

    def test_equal_nested_fixture_records_are_removed_by_identity(self):
        self.write("test_example.py", """
from types import SimpleNamespace
import pytest_impact as impact

def test_nested_tracking():
    parent = SimpleNamespace()
    child = SimpleNamespace()
    outer = impact.pytest_fixture_setup(parent, None)
    inner = impact.pytest_fixture_setup(child, None)
    next(outer)
    outer_record = impact.FIXTURE_STACK[-1]
    next(inner)
    inner_record = impact.FIXTURE_STACK[-1]
    assert outer_record == inner_record and outer_record is not inner_record
    try:
        next(inner)
    except StopIteration:
        pass
    assert impact.FIXTURE_STACK[-1] is outer_record
    outer_record['unsafe'].add('outer-only')
    try:
        next(outer)
    except StopIteration:
        pass
    assert impact.FIXTURES[id(parent)]['unsafe'] == {'outer-only'}
    assert impact.FIXTURES[id(child)]['unsafe'] == set()
""")
        report = self.run_tests()
        self.assertEqual(report["selected"], ["test_example.py::test_nested_tracking"])

    def test_file_input_change_is_not_hidden(self):
        self.run_tests()
        self.write("data.txt", "changed")
        changed = self.run_tests(expected=1)
        self.assertIn("test_example.py::test_data", changed["selected"])
        self.assertEqual(changed["reasons"]["test_example.py::test_data"], "file-input-changed")

    def test_failed_run_cannot_publish_reusable_history(self):
        self.run_tests()
        self.write('leaf.py', 'def value():\n    return 99\n')
        self.run_tests(expected=1)
        self.assertFalse((self.state / 'inputs.json').exists())
        self.write('leaf.py', 'def value():\n    return 1\n')
        report = self.run_tests()
        self.assertTrue(report['full'])
        self.assertEqual(len(report['selected']), 3)
        self.assertEqual(report['reused'], [])

    def test_tracking_database_must_match_input_history(self):
        self.run_tests()
        with __import__('sqlite3').connect(self.state / 'testmon.sqlite') as connection:
            connection.execute('CREATE TABLE foreign_tracking(value INTEGER)')
        report = self.run_tests()
        self.assertTrue(report['full'])
        self.assertEqual(report['reused'], [])

    def test_malformed_metadata_shapes_and_unsafe_values_force_full(self):
        for malformed in ([], None, {'unsafe': [{}]}, {'unsafe': [None]}):
            with self.subTest(malformed=malformed):
                self.run_tests()
                if isinstance(malformed, dict):
                    metadata = json.loads((self.state / 'inputs.json').read_text())
                    metadata['tests']['test_example.py::test_first'].update(malformed)
                else:
                    metadata = malformed
                (self.state / 'inputs.json').write_text(json.dumps(metadata))
                report = self.run_tests()
                self.assertTrue(report['full'])
                self.assertEqual(report['reused'], [])

    def test_directory_membership_change_reruns_its_consumers(self):
        (self.root / 'inputs').mkdir()
        self.write('inputs/first.txt', 'first')
        self.write('test_example.py', "from pathlib import Path\ndef test_names():\n    assert sorted(p.name for p in Path('inputs').iterdir()) == ['first.txt']\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.run_tests()
        self.assertEqual(self.run_tests()['reused'], ['test_example.py::test_names'])
        self.write('inputs/second.txt', 'second')
        self.assertEqual(self.run_tests(1)['selected'], ['test_example.py::test_names'])

    def test_directory_changes_during_a_case_cannot_authorize_reuse(self):
        (self.root / 'inputs').mkdir()
        self.write('test_example.py', "from pathlib import Path\ndef test_changes():\n    list(Path('inputs').iterdir())\n    Path('inputs/new.txt').write_text('new')\n    list(Path('inputs').iterdir())\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.run_tests()
        report = self.run_tests()
        self.assertEqual(report['selected'], ['test_example.py::test_changes'])
        self.assertIn('changing-file-input', report['untracked']['test_example.py::test_changes'])

    def test_environment_changes_during_a_case_cannot_authorize_reuse(self):
        self.env['NODE_TEST_WORKER_ID'] = 'original'
        self.write('test_example.py', "import os\ndef test_changes():\n    assert os.environ['NODE_TEST_WORKER_ID'] == 'original'\n    os.environ['NODE_TEST_WORKER_ID'] = 'changed'\n    assert os.environ['NODE_TEST_WORKER_ID'] == 'changed'\n")
        self.run_tests()
        report = self.run_tests()
        self.assertEqual(report['selected'], ['test_example.py::test_changes'])
        self.assertIn('changing-file-input', report['untracked']['test_example.py::test_changes'])

    def test_external_directory_membership_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as external:
            self.write('test_example.py', f"from pathlib import Path\ndef test_names():\n    assert list(Path({external!r}).iterdir()) == []\n")
            self.run_tests()
            report = self.run_tests()
            self.assertEqual(report['selected'], ['test_example.py::test_names'])
            self.assertIn('external-directory-input', report['untracked']['test_example.py::test_names'])

    def test_runtime_link_hash_tracks_original_path_resolution(self):
        self.write('test_example.py', """from pathlib import Path
from unittest.mock import patch
import pytest_impact as impact
def test_resolution():
    first, second, link = (Path(name).absolute() for name in ('first', 'second', 'link'))
    first.write_text('first')
    second.write_text('second')
    link.symlink_to(first)
    assert not impact.immutable_runtime_file(first)
    with patch.object(impact, 'immutable_runtime_file', return_value=True), patch.object(impact, 'root_owned_path', return_value=True):
        before = impact.runtime_link_digest(link)
        link.unlink()
        link.symlink_to(second)
        assert impact.runtime_link_digest(link) != before
""")
        report = self.run_tests()
        self.assertEqual(report['selected'], ['test_example.py::test_resolution'])

    def test_threaded_inputs_cannot_escape_tracking_busy_state(self):
        self.write('test_example.py', """from pathlib import Path
from threading import Thread
import pytest_impact as impact
def test_thread():
    impact.BUSY = True
    try:
        thread = Thread(target=lambda: Path('data.txt').read_text())
        thread.start()
        thread.join()
    finally:
        impact.BUSY = False
""")
        self.run_tests()
        report = self.run_tests()
        self.assertEqual(report['selected'], ['test_example.py::test_thread'])
        self.assertIn('threaded-input', report['untracked']['test_example.py::test_thread'])

    def test_repo_file_symlink_target_change_reruns_original_reader(self):
        self.write('first.txt', 'first')
        self.write('second.txt', 'second')
        link = self.root / 'input.link'
        link.symlink_to('first.txt')
        self.write('test_example.py', "from pathlib import Path\ndef test_link():\n    assert Path('input.link').read_text() == 'first'\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.run_tests()
        self.assertEqual(self.run_tests()['reused'], ['test_example.py::test_link'])
        link.unlink()
        link.symlink_to('second.txt')
        self.assertEqual(self.run_tests(1)['selected'], ['test_example.py::test_link'])

    def test_repo_directory_symlink_target_change_reruns_original_reader(self):
        (self.root / 'first').mkdir()
        (self.root / 'second').mkdir()
        self.write('first/first.txt', 'first')
        self.write('second/second.txt', 'second')
        link = self.root / 'input.link'
        link.symlink_to('first', target_is_directory=True)
        self.write('test_example.py', "from pathlib import Path\ndef test_link():\n    assert [p.name for p in Path('input.link').iterdir()] == ['first.txt']\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.run_tests()
        self.assertEqual(self.run_tests()['reused'], ['test_example.py::test_link'])
        link.unlink()
        link.symlink_to('second', target_is_directory=True)
        self.assertEqual(self.run_tests(1)['selected'], ['test_example.py::test_link'])

    def test_separate_invocations_reuse_independently_and_keep_each_report(self):
        self.env["SAWA_IMPACT_SPLIT_RUNS"] = "1"
        self.write("test_other.py", "from pathlib import Path\ndef test_other():\n assert Path('other.txt').read_text() == 'original'\n")
        self.write("other.txt", "original")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        def run(name, *, report_name, expected=0):
            result = subprocess.run([sys.executable, "-m", "pytest", "-p", "pytest_impact",
                "-p", "pytest-testmon", "--testmon", "-q", name,
                "--junitxml=" + str(self.root / report_name)],
                cwd=self.root, env=self.env, text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, (result.stdout + result.stderr)[-6000:])
        run("test_example.py", report_name="first.xml")
        run("test_other.py", report_name="second.xml")
        run("test_example.py", report_name="different-first.xml")
        run("test_other.py", report_name="different-second.xml")
        reports = [json.loads(path.read_text()) for path in self.state.glob("*/selection.json")]
        self.assertEqual(len(reports), 2)
        self.assertEqual(sorted(len(report["reused"]) for report in reports), [1, 3])
        self.assertTrue(all(not report["selected"] for report in reports))
        self.write("other.txt", "changed")
        run("test_other.py", report_name="changed.xml", expected=1)
        reports = [json.loads(path.read_text()) for path in self.state.glob("*/selection.json")]
        changed = next(report for report in reports if report["selected"])
        self.assertEqual(changed["selected"], ["test_other.py::test_other"])
        self.assertEqual(changed["reasons"]["test_other.py::test_other"], "file-input-changed")
        unchanged = next(report for report in reports if report["reused"])
        self.assertEqual(len(unchanged["reused"]), 3)

    def test_conftest_import_input_change_is_not_hidden(self):
        self.write("conftest.py", """from pathlib import Path
import pytest
VALUE = Path('data.txt').read_text()
@pytest.fixture
def setting():
    return VALUE
""")
        self.write("test_example.py", "def test_setting(setting):\n    assert setting == 'first'\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        self.run_tests()
        self.write("data.txt", "broken")
        self.assertIn("test_example.py::test_setting", self.run_tests(1)["selected"])

    def test_external_file_metadata_never_reused(self):
        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / 'metadata.txt'
            path.write_text('first')
            self.write('test_example.py', f"import os\ndef test_metadata():\n    assert os.stat({str(path)!r}).st_size == 5\n")
            self.run_tests()
            self.assertIn("test_example.py::test_metadata", self.run_tests()["selected"])

    def test_repository_metadata_change_executes_without_content_change(self):
        (self.root / 'data.txt').chmod(0o644)
        self.write('test_example.py', "import os\ndef test_mode():\n    assert os.stat('data.txt').st_mode & 0o777 == 0o644\n")
        self.run_tests()
        self.assertEqual(self.run_tests()['selected'], [])
        (self.root / 'data.txt').chmod(0o600)
        self.assertIn('test_example.py::test_mode', self.run_tests(1)['selected'])

    def test_collection_source_resolution_does_not_disable_reuse(self):
        self.write('conftest.py', "from pathlib import Path\nimport os\nROOT = Path(__file__).resolve().parent\nassert os.path.realpath(__file__) == str(ROOT / 'conftest.py')\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        first = self.run_tests()
        self.assertEqual(first['untracked'], {})
        self.assertEqual(len(self.run_tests()['reused']), 3)

    def test_path_predicates_track_presence_and_kind(self):
        self.write('test_example.py', """from pathlib import Path
import os
def test_predicates():
    assert Path('data.txt').exists() and Path('data.txt').is_file()
    assert not Path('data.txt').is_dir()
    assert os.path.exists('data.txt') and os.path.isfile('data.txt')
    assert not os.path.isdir('data.txt')
""")
        self.run_tests()
        self.assertEqual(self.run_tests()['selected'], [])
        (self.root / 'data.txt').unlink()
        self.assertIn('test_example.py::test_predicates', self.run_tests(1)['selected'])

    def test_strict_resolution_rechecks_existence(self):
        self.write('test_example.py', "from pathlib import Path\ndef test_strict():\n    assert Path('data.txt').resolve(strict=True).name == 'data.txt'\n")
        self.run_tests()
        self.assertEqual(self.run_tests()['selected'], [])
        (self.root / 'data.txt').unlink()
        self.assertIn('test_example.py::test_strict', self.run_tests(1)['selected'])

    def test_missing_path_resolution_tracks_a_new_symlink(self):
        self.write('test_example.py', "from pathlib import Path\ndef test_resolve():\n    assert Path('future.txt').resolve().name == 'future.txt'\n")
        self.run_tests()
        self.assertEqual(self.run_tests()['selected'], [])
        (self.root / 'future.txt').symlink_to(self.root / 'data.txt')
        self.assertIn('test_example.py::test_resolve', self.run_tests(1)['selected'])

    def test_package_catalog_discovery_does_not_disable_reuse(self):
        self.write('conftest.py', "from importlib.metadata import version\nPYTEST_VERSION = version('pytest')\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.assertEqual(self.run_tests()['untracked'], {})
        self.assertEqual(len(self.run_tests()['reused']), 3)

    def test_missing_collection_input_record_cannot_authorize_reuse(self):
        self.write('conftest.py', "from pathlib import Path\nVALUE = Path('data.txt').read_text()\n")
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.run_tests()
        path = self.state / 'inputs.json'
        history = json.loads(path.read_text())
        for entry in history['tests'].values():
            self.assertIn('data.txt', entry['inputs'])
            del entry['inputs']['data.txt']
        path.write_text(json.dumps(history))
        report = self.run_tests()
        self.assertEqual(len(report['selected']), 3)
        self.assertEqual(set(report['reasons'].values()), {'collection-input-changed'})

    def test_absent_external_read_is_rechecked_before_reuse(self):
        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / 'optional.txt'
            self.write('test_example.py', f"""def test_optional():
    try:
        with open({str(path)!r}) as stream:
            value = stream.read()
    except FileNotFoundError:
        value = 'absent'
    assert value == 'absent'
""")
            self.run_tests()
            self.assertEqual(self.run_tests()['selected'], [])
            path.write_text('now present')
            self.assertIn('test_example.py::test_optional', self.run_tests(1)['selected'])

    def test_database_reads_cannot_reuse_stale_rows(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / 'inputs.sqlite'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE inputs(value INTEGER)')
                connection.execute('INSERT INTO inputs VALUES (1)')
            self.write('test_example.py', f"import sqlite3\ndef test_database():\n    with sqlite3.connect({str(path)!r}) as connection:\n        assert connection.execute('SELECT value FROM inputs').fetchone() == (1,)\n")
            self.run_tests()
            with sqlite3.connect(path) as connection:
                connection.execute('UPDATE inputs SET value = 2')
            self.assertIn('test_example.py::test_database', self.run_tests(1)['selected'])

    def test_only_empty_owned_bootstrap_is_reusable_and_checkouts_always_execute(self):
        (self.root / 'backend').mkdir()
        self.write('backend/conftest.py', """from pathlib import Path
import sqlite3
import sys
connection = sqlite3.connect(str(Path('backend-runtime.sqlite').resolve()))
try:
    sys.audit('sawa.test.database-checkout', str(Path('backend-runtime.sqlite').resolve()))
    connection.execute('CREATE TABLE IF NOT EXISTS seeded(value INTEGER)')
finally:
    connection.close()
""")
        self.write('conftest.py', "from pathlib import Path\nimport runpy\nrunpy.run_path(str(Path('backend/conftest.py').resolve()))\n")
        self.write('test_example.py', "import sys\ndef test_plain():\n    assert True\ndef test_checkout():\n    sys.audit('sawa.test.database-checkout')\n    assert True\n")
        database = self.root / 'backend-runtime.sqlite'
        database.touch()
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        first = self.run_tests()
        self.assertFalse(first['code_tracking_disabled'])
        database.write_bytes(b'')
        same = self.run_tests()
        self.assertEqual(same['reused'], ['test_example.py::test_plain'])
        self.assertEqual(same['selected'], ['test_example.py::test_checkout'])
        retained_database = self.run_tests()
        self.assertTrue(retained_database['code_tracking_disabled'])
        self.assertEqual(retained_database['reused'], [])
        bootstrap = (self.root / 'backend/conftest.py').read_text()
        for arguments in ('', ", 'foreign-database'"):
            self.write('backend/conftest.py', bootstrap + f"\nsys.audit('sawa.test.database-checkout'{arguments})\n")
            database.write_bytes(b'')
            foreign_checkout = self.run_tests()
            self.assertTrue(foreign_checkout['code_tracking_disabled'])
            self.assertEqual(foreign_checkout['reused'], [])

    def test_tqdm_settings_remain_in_context_without_volatile_run_identity(self):
        self.write('test_example.py', "from tqdm import tqdm\ndef test_progress():\n    assert callable(tqdm)\n")
        self.env['SAWA_LOCAL_RUN_ID'] = 'first-run'
        self.run_tests()
        self.env['SAWA_LOCAL_RUN_ID'] = 'second-run'
        self.assertEqual(self.run_tests()['selected'], [])
        self.env['TQDM_DISABLE'] = '1'
        self.assertIn('test_example.py::test_progress', self.run_tests()['selected'])

    def test_ci_environment_dependency_never_reused(self):
        self.env["GITHUB_REF"] = "main"
        self.write("test_example.py", """import os
def test_env():
    assert os.environ['GITHUB_REF'] == 'main'
""")
        self.run_tests()
        self.env["GITHUB_REF"] = "changed"
        self.assertIn("test_example.py::test_env", self.run_tests(1)["selected"])

    def test_unread_runner_identities_do_not_invalidate_tests(self):
        names = ("INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID",
                 "MEMORY_PRESSURE_WATCH", "MEMORY_PRESSURE_WRITE", "NODE_TEST_WORKER_ID", "CODEX_TOOL_CALL_ID")
        self.env.update({key: "first-run" for key in names})
        self.run_tests()
        self.env.update({key: "second-run" for key in names})
        self.assertEqual(len(self.run_tests()["reused"]), 3)

    def test_environment_key_enumeration_always_executes(self):
        self.env["INVOCATION_ID"] = "first-run"
        self.write("test_example.py", "import os\ndef test_keys():\n    assert 'INVOCATION_ID' in list(os.environ)\n")
        self.run_tests()
        self.env.pop("INVOCATION_ID")
        self.assertIn("test_example.py::test_keys", self.run_tests(1)["selected"])

    def test_each_runner_identity_read_always_executes(self):
        names = ("INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID",
                 "MEMORY_PRESSURE_WATCH", "MEMORY_PRESSURE_WRITE", "NODE_TEST_WORKER_ID", "CODEX_TOOL_CALL_ID")
        self.env.update({key: "first-run" for key in names})
        self.write("test_example.py", "import os\n" + "\n".join(
            f"def test_{key}():\n    assert os.environ[{key!r}] == 'first-run'\n"
            for key in names))
        self.run_tests()
        self.env.update({key: "second-run" for key in names})
        report = self.run_tests(1)
        for key in names:
            self.assertIn(f"test_example.py::test_{key}", report["selected"])


    def test_unsafe_collection_runs_all_assertions_without_code_tracking(self):
        self.env["GITHUB_REF"] = "main"
        self.write("conftest.py", """import os
import pytest
VALUE = os.environ['GITHUB_REF']
KEYS = list(os.environ)
@pytest.fixture
def setting():
    return VALUE
""")
        self.write("test_example.py", "def test_setting(setting):\n    assert setting == 'main'\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        first = self.run_tests()
        self.assertTrue(first["code_tracking_disabled"])
        self.assertEqual(first["selected"], ["test_example.py::test_setting"])
        self.assertEqual(self.run_tests()["reused"], [])
        self.env["GITHUB_REF"] = "broken"
        self.assertEqual(self.run_tests(1)["selected"], ["test_example.py::test_setting"])

    def test_test_change_new_test_and_failed_test_execute(self):
        self.run_tests()
        with (self.root / "test_example.py").open("a") as stream:
            stream.write("\ndef test_new():\n    assert False\n")
        self.assertIn("test_example.py::test_new", self.run_tests(expected=1)["selected"])
        self.assertIn("test_example.py::test_new", self.run_tests(expected=1)["selected"])

    def test_missing_or_corrupt_sidecar_and_database_force_full_execution(self):
        self.run_tests()
        for path in (self.state / "inputs.json", self.state / "testmon.sqlite"):
            path.write_text("corrupt")
            result = self.run_tests()
            self.assertTrue(result["full"])
            self.assertEqual(len(result["selected"]), 3)
        (self.state / "inputs.json").unlink()
        self.assertEqual(len(self.run_tests()["selected"]), 3)

    def test_environment_and_config_changes_force_execution(self):
        self.run_tests()
        self.env["BUSINESS_OPTION"] = "different"
        self.assertEqual(len(self.run_tests()["selected"]), 3)
        self.write("pytest.ini", "[pytest]\n")
        subprocess.run(["git", "add", "pytest.ini"], cwd=self.root, check=True)
        self.assertEqual(len(self.run_tests()["selected"]), 3)

    def test_source_as_text_dependency_is_tracked(self):
        self.write("test_example.py", """
from pathlib import Path
def test_source():
    assert 'return 1' in Path('leaf.py').read_text()
""")
        self.run_tests()
        self.assertEqual(self.run_tests()["selected"], [])
        self.write("leaf.py", "def value():\n    return 2\n")
        self.assertEqual(self.run_tests(expected=1)["selected"], ["test_example.py::test_source"])

    def test_subprocess_input_is_never_reused(self):
        self.write("test_example.py", """
import subprocess
import sys
def test_process():
    assert subprocess.run([sys.executable, '-c', 'pass']).returncode == 0
""")
        self.run_tests()
        result = self.run_tests()
        self.assertEqual(result["selected"], ["test_example.py::test_process"])
        self.assertEqual(result["reasons"]["test_example.py::test_process"], "untracked-input")

    def test_import_time_data_dependency_is_not_lost(self):
        self.write("operations.py", "from pathlib import Path\nVALUE = Path('data.txt').read_text()\n")
        self.write("test_example.py", "from operations import VALUE\ndef test_value():\n    assert VALUE == 'first'\n")
        self.run_tests()
        self.write("data.txt", "changed")
        self.assertEqual(self.run_tests(expected=1)["selected"], ["test_example.py::test_value"])

    def test_shared_fixture_file_input_reaches_every_consumer(self):
        self.write("test_example.py", """
import pytest
from pathlib import Path
@pytest.fixture(scope='session')
def shared():
    return Path('data.txt').read_text()
def test_a(shared):
    assert shared == 'first'
def test_b(shared):
    assert shared == 'first'
""")
        self.run_tests()
        self.assertEqual(self.run_tests()["selected"], [])
        self.write("data.txt", "changed")
        self.assertEqual(set(self.run_tests(expected=1)["selected"]),
                         {"test_example.py::test_a", "test_example.py::test_b"})

    def test_parameter_cases_have_individual_dependency_sets(self):
        self.write("test_example.py", """
import pytest
from operations import first, second
@pytest.mark.parametrize('function, expected', [(first, 1), (second, 2)], ids=['first', 'second'])
def test_value(function, expected):
    assert function() == expected
""")
        self.run_tests()
        self.write("leaf.py", "def value():\n    return 123\n")
        result = self.run_tests(expected=1)
        self.assertEqual(result["selected"], ["test_example.py::test_value[first]"])
        self.assertEqual(result["reused"], ["test_example.py::test_value[second]"])


if __name__ == "__main__":
    unittest.main()
