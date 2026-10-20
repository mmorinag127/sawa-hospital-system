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
            [sys.executable, "-m", "pytest", "-p", "testmon.pytest_testmon",
             "-p", "pytest_impact", "--testmon", "-q", "test_example.py"],
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

    def test_file_input_change_is_not_hidden(self):
        self.run_tests()
        self.write("data.txt", "changed")
        changed = self.run_tests(expected=1)
        self.assertIn("test_example.py::test_data", changed["selected"])
        self.assertEqual(changed["reasons"]["test_example.py::test_data"], "file-input-changed")

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
        self.write("test_example.py", """import os
def test_metadata():
    assert os.path.exists('data.txt')
""")
        self.run_tests()
        self.assertIn("test_example.py::test_metadata", self.run_tests()["selected"])

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
