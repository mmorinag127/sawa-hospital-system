from pathlib import Path
import tempfile
import unittest

from infra.ci.impact_collection import CollectionInputs


class CollectionDependencies(unittest.TestCase):
    def test_fixed_file_loads_track_both_canonical_codecs_without_unrelated_modules(self):
        text = '''from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
_root = Path(__file__).resolve().parents[2]
if _root.name == "backend":
    _root = _root.parent
_spec = spec_from_file_location("codec", _root / "ocr_pipeline/app/codec.py")
if _spec is None or _spec.loader is None:
    raise ImportError("required")
_codec = module_from_spec(_spec)
_spec.loader.exec_module(_codec)
_other_spec = spec_from_file_location("markup", _root / "ocr_pipeline/app/markup.py")
_other = module_from_spec(_other_spec)
_other_spec.loader.exec_module(_other)
'''
        files = {'backend/src/services/wrapper.py': text,
                 'ocr_pipeline/app/codec.py': 'VALUE = 1\n',
                 'ocr_pipeline/app/markup.py': 'VALUE = 2\n',
                 'backend/src/unrelated.py': 'VALUE = 3\n'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            expected = {str(root / name) for name in files if 'unrelated' not in name}
            graph = CollectionInputs(root, files, include_production=True)
            self.assertEqual(graph.dependencies(root / 'backend/src/services/wrapper.py'), expected)
            for changed in (
                text.replace('_root / "ocr_pipeline/app/codec.py"', 'runtime_path'),
                text + '\n_spec.loader = foreign_loader\n',
                text.replace('_spec.loader.exec_module(_codec)', 'if runtime_flag:\n    _spec.loader = foreign_loader\n_spec.loader.exec_module(_codec)'),
                text.replace('_codec = module_from_spec(_spec)', '_spec = foreign_spec\n_codec = module_from_spec(_spec)'),
                text.replace('_root = Path(__file__)', '__file__ = runtime_path\n_root = Path(__file__)'),
                text.replace('_codec = module_from_spec(_spec)', 'for item in runtime_items:\n    _spec.loader = item\n_codec = module_from_spec(_spec)'),
                text.replace('if _spec is None or _spec.loader is None:', 'if external_condition():'),
                text.replace('from pathlib import Path', 'from arbitrary_module import ImportError\nfrom pathlib import Path'),
                text.replace('spec_from_file_location, module_from_spec', 'spec_from_file_location as loader, module_from_spec').replace('_spec = spec_from_file_location(', '_spec = loader('),
                text.replace('_spec.loader.exec_module(_codec)', '_execute = _spec.loader.exec_module\n_execute(_codec)'),
                text.replace('spec_from_file_location("codec",', 'spec_from_file_location(runtime_module_name,'),
                text.replace('spec_from_file_location("codec",', 'spec_from_file_location("foreign.codec",'),
            ):
                with self.subTest(source=changed):
                    (root / 'backend/src/services/wrapper.py').write_text(changed)
                    graph = CollectionInputs(root, files, include_production=True)
                    self.assertEqual(graph.dependencies(root / 'backend/src/services/wrapper.py'),
                                     {str(root / name) for name in files})

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
