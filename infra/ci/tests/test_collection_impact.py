from pathlib import Path
import tempfile
import unittest

from infra.ci.impact_collection import CollectionInputs


class CollectionDependencies(unittest.TestCase):
    def test_relative_bare_and_ambiguous_imports_keep_all_matching_helpers(self):
        files = {
            'backend/tests/unit/test_a.py': 'from . import helper\nimport other\n',
            'backend/tests/unit/helper.py': 'from tests.shared import values\n',
            'backend/tests/shared/values.py': 'VALUE = 1\n',
            'backend/tests/unit/other.py': '',
            'backend/tests/integration/other.py': '',
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            graph = CollectionInputs(root, files)
            self.assertEqual(graph.dependencies(root / 'backend/tests/unit/test_a.py'),
                             {str(root / name) for name in files})

    def test_unknown_dynamic_import_cannot_hide_any_test_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ['backend/tests/test_a.py', 'backend/tests/helper.py']
            for name, text in zip(paths, ['__import__(name)', 'VALUE = 1']):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            graph = CollectionInputs(root, paths)
            self.assertEqual(graph.dependencies(root / paths[0]), {str(root / name) for name in paths})

    def test_conftest_reads_remain_shared_but_test_reads_are_attributed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ['backend/tests/conftest.py', 'backend/tests/test_a.py']
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
            graph = CollectionInputs(root, paths)
            for name in paths:
                namespace = {'graph': graph}
                exec(compile('owner = graph.owner()', str(root / name), 'exec'), namespace)
                self.assertEqual(namespace['owner'], None if name.endswith('conftest.py') else str(root / name))
