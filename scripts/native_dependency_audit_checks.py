#!/usr/bin/env python3
"""Real filesystem/FD audit regressions, not OCR or native acceptance evidence."""
import argparse
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest

import native_dependency_probe as probe


class OwnershipChecks(unittest.TestCase):
    def setUp(self):
        self.root = probe.OUT / self._testMethodName
        self.root.mkdir()
        self.fd = os.open(self.root, os.O_RDONLY)
        self.addCleanup(os.close, self.fd)

    def denied(self, event, action):
        before = len(probe.AUDIT['violations'])
        with self.assertRaises(RuntimeError):
            action()
        self.assertEqual(len(probe.AUDIT['violations']), before + 1)
        self.assertEqual(probe.AUDIT['violations'][-1]['event'], event)

    def test_temporary_directory_cleanup_uses_real_dir_fd(self):
        events = []
        # Observe the actual safe-rmtree events without replacing its implementation.
        sys.addaudithook(lambda event, args: events.append((event, args))
                         if event in ('os.remove', 'os.rmdir') else None)
        with tempfile.TemporaryDirectory(dir=self.root) as name:
            temp = Path(name)
            (temp / 'nested').mkdir()
            (temp / 'nested/filelock-probe-link').write_bytes(b'owned')
        self.assertFalse(temp.exists())
        self.assertTrue(any(event == 'os.remove' and args[0] == 'filelock-probe-link'
                            and args[1] >= 0 for event, args in events))

    def test_owned_dir_fd_operations(self):
        (self.root / 'source').write_bytes(b'owned')
        os.mkdir('directory', dir_fd=self.fd)
        os.chmod('source', 0o600, dir_fd=self.fd)
        os.link('source', 'hardlink', src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.rename('hardlink', 'renamed', src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.symlink('source', 'symlink', dir_fd=self.fd)
        self.assertEqual((self.root / 'symlink').read_bytes(), b'owned')
        self.assertEqual((self.root / 'renamed').stat().st_ino, (self.root / 'source').stat().st_ino)
        child_fd = os.open(self.root / 'directory', os.O_RDONLY)
        try:
            os.link('source', 'second-link', src_dir_fd=self.fd, dst_dir_fd=child_fd)
            os.rename('renamed', 'moved', src_dir_fd=self.fd, dst_dir_fd=child_fd)
            self.assertEqual((self.root / 'directory/moved').read_bytes(), b'owned')
            os.unlink('second-link', dir_fd=child_fd)
            os.unlink('moved', dir_fd=child_fd)
        finally:
            os.close(child_fd)
        for name in ('symlink', 'source'):
            os.unlink(name, dir_fd=self.fd)
        os.rmdir('directory', dir_fd=self.fd)

    def test_foreign_dir_fd_refuses_mutations(self):
        for event, action in (
            ('os.remove', lambda: os.unlink('sentinel', dir_fd=FOREIGN_DIR)),
            ('os.rmdir', lambda: os.rmdir('empty', dir_fd=FOREIGN_DIR)),
            ('os.mkdir', lambda: os.mkdir('new', dir_fd=FOREIGN_DIR)),
            ('os.chmod', lambda: os.chmod('sentinel', 0o777, dir_fd=FOREIGN_DIR)),
            ('os.symlink', lambda: os.symlink(str(self.root), 'link', dir_fd=FOREIGN_DIR)),
        ):
            with self.subTest(event=event):
                self.denied(event, action)

    def test_parent_and_ancestor_symlink_escape(self):
        escape = os.path.relpath(FOREIGN / 'sentinel', self.root)
        self.denied('os.remove', lambda: os.unlink(escape, dir_fd=self.fd))
        self.denied('os.mkdir', lambda: os.mkdir(escape + '-new', dir_fd=self.fd))
        self.denied('os.remove', lambda: os.unlink('foreign-dir/sentinel', dir_fd=OWNED_DIR))

    def test_unlink_and_rename_symlink_entries_do_not_write_targets(self):
        os.unlink('foreign-link-unlink', dir_fd=OWNED_DIR)
        self.assertFalse((probe.OUT / 'foreign-link-unlink').is_symlink())
        os.rename('foreign-link-rename', 'moved-link', src_dir_fd=OWNED_DIR, dst_dir_fd=self.fd)
        self.assertEqual(os.readlink(self.root / 'moved-link'), str(FOREIGN / 'sentinel'))
        os.unlink('moved-link', dir_fd=self.fd)
        (self.root / 'replacement').write_bytes(b'owned replacement')
        os.rename('replacement', 'foreign-link-replace', src_dir_fd=self.fd, dst_dir_fd=OWNED_DIR)
        self.assertEqual((probe.OUT / 'foreign-link-replace').read_bytes(), b'owned replacement')

    def test_symlink_target_writes_and_creation_escape_refused(self):
        link = probe.OUT / 'foreign-link-write'
        self.denied('open', lambda: link.write_bytes(b'forbidden'))
        self.denied('os.chmod', lambda: os.chmod('foreign-link-write', 0o777, dir_fd=OWNED_DIR))
        self.denied('os.truncate', lambda: os.truncate(link, 0))
        self.denied('os.symlink', lambda: os.symlink(str(FOREIGN), 'escape', dir_fd=self.fd))
        self.denied('os.symlink', lambda: os.symlink('../../outside', 'relative-escape', dir_fd=self.fd))
        self.denied('os.remove', lambda: os.unlink('foreign-dir/', dir_fd=OWNED_DIR))

    def test_rename_and_link_require_both_owned_sides(self):
        (self.root / 'source').write_bytes(b'owned')
        for operation, event in ((os.rename, 'os.rename'), (os.link, 'os.link')):
            with self.subTest(event=event):
                self.denied(event, lambda: operation('source', 'destination',
                            src_dir_fd=self.fd, dst_dir_fd=FOREIGN_DIR))
                self.denied(event, lambda: operation('sentinel', 'destination',
                            src_dir_fd=FOREIGN_DIR, dst_dir_fd=self.fd))
        # link's audit event cannot distinguish its follow_symlinks flag.
        for follow in (True, False):
            self.denied('os.link', lambda: os.link('foreign-link-write', 'linked',
                        src_dir_fd=OWNED_DIR, dst_dir_fd=self.fd, follow_symlinks=follow))
        self.assertEqual((self.root / 'source').read_bytes(), b'owned')

    def test_owned_integer_write_and_truncate(self):
        path = self.root / 'file'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            self.assertEqual(probe.descriptor_path(fd), path)
            with open(fd, 'w', closefd=False) as stream:
                stream.write('owned')
            os.ftruncate(fd, 2)
            self.assertEqual(path.read_bytes(), b'ow')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

    def test_foreign_and_reused_integer_fds_refused(self):
        self.denied('open', lambda: open(FOREIGN_FILE, 'w', closefd=False))
        self.denied('os.truncate', lambda: os.ftruncate(FOREIGN_FILE, 0))
        self.denied('os.chmod', lambda: os.fchmod(FOREIGN_FILE, 0o777))
        fd = os.open(self.root / 'reused', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            self.assertEqual(probe.descriptor_path(fd), self.root / 'reused')
            os.dup2(FOREIGN_FILE, fd)
            self.denied('open', lambda: open(fd, 'w', closefd=False))
        finally:
            os.close(fd)
        os.dup2(FOREIGN_DIR, self.fd)
        self.denied('os.remove', lambda: os.unlink('sentinel', dir_fd=self.fd))

    def test_invalid_deleted_and_non_directory_fds_refused(self):
        fd = os.open(self.root / 'removed', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            self.denied('os.remove', lambda: os.unlink('child', dir_fd=fd))
            os.unlink(self.root / 'removed')
            (self.root / 'removed').write_bytes(b'different inode')
            self.denied('open', lambda: open(fd, 'w', closefd=False))
        finally:
            os.close(fd)
        self.denied('os.mkdir', lambda: os.mkdir('invalid', dir_fd=fd))
        self.denied('open', lambda: open(fd, 'w', closefd=False))
        read_fd, write_fd = os.pipe()
        try:
            self.denied('open', lambda: open(write_fd, 'w', closefd=False))
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_directory_fd_follows_moved_physical_identity(self):
        os.rename(self.root, self.root.with_name(self.root.name + '-moved'))
        self.root = self.root.with_name(self.root.name + '-moved')
        self.assertEqual(probe.descriptor_path(self.fd, directory=True), self.root)
        os.mkdir('child', dir_fd=self.fd)
        os.rmdir('child', dir_fd=self.fd)

    def test_relative_open_with_unobservable_dir_fd_refused(self):
        original = Path.cwd()
        os.chdir(self.root)
        try:
            self.denied('open', lambda: os.open('sentinel', os.O_WRONLY, dir_fd=FOREIGN_DIR))
            self.denied('open', lambda: Path('relative-write').write_bytes(b'forbidden'))
        finally:
            os.chdir(original)

    def test_null_device_is_only_a_write_sink(self):
        with open(os.devnull, 'w') as stream:
            stream.write('discard')
        fd = os.open(os.devnull, os.O_WRONLY)
        try:
            with open(fd, 'w', closefd=False) as stream:
                stream.write('discard')
        finally:
            os.close(fd)
        self.denied('os.chmod', lambda: os.chmod(os.devnull, 0o777))
        self.denied('os.remove', lambda: os.unlink(os.devnull))
        self.denied('open', lambda: (probe.OUT / 'null-alias').write_text('forbidden alias'))


def main():
    global FOREIGN, FOREIGN_DIR, FOREIGN_FILE, OWNED_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    owned = output / 'owned'
    owned.mkdir()
    FOREIGN = output / 'outside'
    FOREIGN.mkdir()
    (FOREIGN / 'empty').mkdir()
    sentinel = FOREIGN / 'sentinel'
    sentinel.write_bytes(b'outside sentinel: must not mutate')
    before = (sentinel.read_bytes(), sentinel.stat().st_mode, sentinel.stat().st_ino)
    # All adverse fixtures are owned by this regression run, prepared before the
    # narrower production audit root is installed. No actual external data used.
    for name in ('unlink', 'rename', 'replace', 'write'):
        (owned / ('foreign-link-' + name)).symlink_to(sentinel)
    (owned / 'foreign-dir').symlink_to(FOREIGN, target_is_directory=True)
    (owned / 'null-alias').symlink_to(os.devnull)
    FOREIGN_DIR = os.open(FOREIGN, os.O_RDONLY)
    FOREIGN_FILE = os.open(sentinel, os.O_RDWR)
    OWNED_DIR = os.open(owned, os.O_RDONLY)
    os.environ['OWNED_EVIDENCE_ROOT'] = str(owned)
    probe.install_audit()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OwnershipChecks)
    ids = [test.id() for test in suite]
    try:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        unchanged = before == (sentinel.read_bytes(), sentinel.stat().st_mode, sentinel.stat().st_ino)
        unchanged = unchanged and sorted(p.name for p in FOREIGN.iterdir()) == ['empty', 'sentinel']
        summary = {'platform': sys.platform, 'python': sys.version, 'machine': platform.machine(),
                   'cases': ids, 'count': result.testsRun, 'failures': result.failures,
                   'errors': result.errors, 'skips': result.skipped,
                   'outside_fixture_unchanged': unchanged,
                   'expected_refusals_retained': len(probe.AUDIT['violations']),
                   'label': 'real FD tooling checks only, not native OCR acceptance'}
        # unittest failures include TestCase instances; retain their exact IDs.
        for key in ('failures', 'errors', 'skips'):
            summary[key] = [(case.id(), detail) for case, detail in summary[key]]
        probe.save(owned / 'checks.json', summary)
        return 0 if result.wasSuccessful() and unchanged and not result.skipped else 1
    finally:
        for fd in (FOREIGN_DIR, FOREIGN_FILE, OWNED_DIR):
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
