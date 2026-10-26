import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from impact_owned_inputs import OwnedInputs


class GeneratedInputs(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.unsafe = set()
        self.inherited = []
        self.inputs = OwnedInputs(self.unsafe.add, self.inherited.append)
        self.producer = {'unsafe': set(), 'inputs': {'fixture.py': 'source'}}

    def register(self, shared=False):
        root = self.base / 'fresh'
        root.mkdir()
        self.inputs.register(root, owner='producer', producer=self.producer,
                             base=self.base, shared=shared)
        return root

    def test_only_new_empty_directories_are_owned(self):
        root = self.base / 'existing'
        root.mkdir()
        (root / 'db').write_bytes(b'existing')
        self.inputs.register(root, owner='producer', producer=self.producer,
                             base=self.base, shared=False)
        self.assertFalse(self.inputs.accepts(root / 'db', owner='producer', stack=[]))
        self.assertIn('generated-resource-ownership-unresolved', self.unsafe)

    def test_case_database_is_owned_but_external_database_is_not(self):
        root = self.register()
        self.assertTrue(self.inputs.database(str(root / 'new.sqlite'), owner='producer', stack=[]))
        self.assertFalse(self.inputs.database(str(self.base / 'external.sqlite'), owner='producer', stack=[]))
        self.assertFalse(self.inputs.database(':memory:', owner=None, stack=[]))

    def test_cross_case_use_invalidates_both_sides(self):
        root = self.register()
        self.assertTrue(self.inputs.accepts(root / 'output', owner='other', stack=[]))
        self.inputs.finish()
        self.assertIn('cross-case-generated-input', self.unsafe)
        self.assertIn('cross-case-generated-input', self.inputs.unsafe_for('producer'))

    def test_shared_inputs_are_readonly_and_rechecked_for_earlier_consumers(self):
        root = self.register(shared=True)
        path = root / 'template.sqlite'
        path.write_bytes(b'original')
        self.inputs.seal(self.producer)
        uri = path.as_uri() + '?mode=ro'
        self.assertTrue(self.inputs.database(uri, owner='first', stack=[]))
        self.assertEqual(self.inherited, [self.producer])
        self.assertEqual(self.inputs.unsafe_for('first'), set())
        path.write_bytes(b'changed')
        self.inputs.finish()
        self.assertIn('shared-generated-input-changed', self.inputs.unsafe_for('first'))

    def test_shared_write_is_unsafe_even_if_bytes_are_restored(self):
        root = self.register(shared=True)
        path = root / 'template.sqlite'
        path.write_bytes(b'original')
        self.inputs.seal(self.producer)
        self.inputs.accepts(path, owner='first', stack=[])
        self.inputs.accepts(path, owner='second', stack=[], write=True)
        self.inputs.finish()
        self.assertIn('shared-generated-input-write', self.inputs.unsafe_for('first'))

    def test_symlink_cannot_escape_generated_root(self):
        root = self.register()
        external = self.base / 'external'
        external.mkdir()
        (root / 'link').symlink_to(external, target_is_directory=True)
        self.assertFalse(self.inputs.accepts(root / 'link' / 'input', owner='producer', stack=[]))


if __name__ == '__main__':
    unittest.main()
