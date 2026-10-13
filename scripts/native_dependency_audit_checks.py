#!/usr/bin/env python3
"""Real filesystem/FD audit regressions, not OCR or native acceptance evidence."""
import argparse
from copy import deepcopy
import ctypes
import errno
import os
from pathlib import Path
import platform
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import native_dependency_ci as ci
import native_dependency_probe as probe
import native_network_isolation as isolation


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


class NetworkChecks(unittest.TestCase):
    def denied(self, event, action):
        before = len(probe.AUDIT['violations'])
        with self.assertRaisesRegex(RuntimeError, 'native network/external process forbidden'):
            action()
        self.assertEqual(len(probe.AUDIT['violations']), before + 1)
        self.assertEqual(probe.AUDIT['violations'][-1]['event'], event)

    def test_exact_capability_requires_real_isolation(self):
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            if probe.ISOLATION is None:
                self.denied('socket.bind', lambda: sock.bind(('::1', 0)))
            else:
                sock.bind(('::1', 0))
                self.assertGreater(sock.getsockname()[1], 0)
                record = probe.AUDIT['allowed_capability_binds'][-1]
                self.assertEqual(record['os_result'], 'bound')
                self.assertEqual(record['actual_address'], list(sock.getsockname()))
        self.assertEqual(sock.fileno(), -1)

    def test_wildcard_fixedport_and_non_tcp_bind_refused(self):
        for family, kind, address in (
            (socket.AF_INET6, socket.SOCK_STREAM, ('::', 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, ('::1', 9)),
            (socket.AF_INET6, socket.SOCK_STREAM, ('::1', 0, 0, 0)),
            (socket.AF_INET6, socket.SOCK_DGRAM, ('::1', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, ('127.0.0.1', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, ('0.0.0.0', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, ('192.0.2.1', 0)),
        ):
            with self.subTest(address=address, kind=kind):
                with socket.socket(family, kind) as sock:
                    self.denied('socket.bind', lambda: sock.bind(address))

    def test_python_communications_dns_unknown_events_refused(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for event, action in (
                ('socket.connect', lambda: sock.connect(('127.0.0.1', 9))),
                ('socket.sendto', lambda: sock.sendto(b'blocked', ('127.0.0.1', 9))),
                ('socket.sendmsg', lambda: sock.sendmsg([b'blocked'], [], 0, ('127.0.0.1', 9))),
                ('socket.getaddrinfo', lambda: socket.getaddrinfo('invalid.example', 80)),
                ('socket.gethostbyname', lambda: socket.gethostbyname('invalid.example')),
                ('socket.gethostbyaddr', lambda: socket.gethostbyaddr('192.0.2.1')),
                ('socket.gethostname', socket.gethostname),
                ('socket.getnameinfo', lambda: socket.getnameinfo(('192.0.2.1', 9), 0)),
                ('socket.future_operation', lambda: sys.audit('socket.future_operation')),
            ):
                with self.subTest(event=event):
                    self.denied(event, action)

    def test_live_socket_descriptor_blocks_attestation(self):
        if probe.ISOLATION is None:
            with self.assertRaises(RuntimeError):
                isolation.verify_current(os.getpid(), 'net:[1]', os.getuid(), os.getgid(), 'fake.service')
        else:
            # Real FD, not a fabricated accepted state. Validation rejects it
            # before any raw syscall probe can run beneath the installed audit.
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM):
                with self.assertRaisesRegex(RuntimeError, 'inherited standard descriptor'):
                    isolation.verify_arguments(ISOLATION_ARGS)


class IsolationLogicChecks(unittest.TestCase):
    """Portable decision tests. Synthetic state is never Linux execution proof."""
    def test_rejects_false_namespace_privileges_routes_and_descriptors(self):
        unit = 'sawa-native-1-1-preflight.service'
        state = {'uid': [1001] * 4, 'gid': [1001] * 4, 'namespace': 'net:[2]',
                 'host_namespace': 'net:[1]', 'interfaces': ['lo'], 'external_routes': [],
                 'capabilities': dict.fromkeys(isolation.CAPABILITIES, 0),
                 'no_new_privs': 1, 'seccomp': 2, 'cgroup': '0::/system.slice/' + unit,
                 'fds': [0, 1, 2], 'socket_fds': [], 'uring_fds': []}
        for field, invalid in (
            ('namespace', 'net:[1]'), ('host_namespace', 'net:[3]'), ('uid', [0] * 4),
            ('gid', [0] * 4), ('interfaces', ['lo', 'eth0']), ('external_routes', ['default']),
            ('no_new_privs', 0), ('seccomp', 0), ('cgroup', '0::/'),
            ('fds', [1, 2]), ('socket_fds', [0]), ('uring_fds', [3]),
            *[('capabilities', {**state['capabilities'], key: 1}) for key in isolation.CAPABILITIES],
        ):
            with self.subTest(field=field, invalid=invalid):
                changed = deepcopy(state)
                changed[field] = invalid
                with self.assertRaises(RuntimeError):
                    isolation.validate_state(changed, 'net:[1]', 1001, 1001, unit)

    def test_unit_argv_is_bounded_nonroot_and_no_namespace_join(self):
        unit = isolation.unit_name(Path('/tmp/sawa-native-123-2'), 'backend-before')
        self.assertEqual(unit, 'sawa-native-123-2-backend-before.service')
        argv = isolation.service_command(unit, ['/usr/bin/python3', '-B', 'probe.py'],
                                        Path('/tmp/source'), {'HOME': '/tmp/owned'}, 1001, 1001, 120)
        for prop in ('PrivateNetwork=yes', 'CapabilityBoundingSet=', 'AmbientCapabilities=',
                     'NoNewPrivileges=yes', 'RestrictNamespaces=yes', 'SystemCallArchitectures=native',
                     'RestrictAddressFamilies=AF_INET AF_INET6', 'SystemCallErrorNumber=EPERM',
                     'RuntimeMaxSec=120', 'TimeoutStopSec=10', 'KillMode=control-group',
                     'User=1001', 'Group=1001'):
            self.assertIn('--property=' + prop, argv)
        self.assertIn('--property=SystemCallFilter=~' + ' '.join(isolation.DENIED_SYSCALLS), argv)
        self.assertIn('--expand-environment=no', argv)
        self.assertIn('-i', argv)
        self.assertFalse(any('JoinsNamespaceOf' in item or 'NetworkNamespacePath' in item for item in argv))
        with self.assertRaises(ValueError):
            isolation.service_command(unit, ['python'], Path('/tmp'), {}, 0, 0, 120)
        with self.assertRaises(ValueError):
            isolation.unit_name(Path('/tmp/other'), 'backend')
        with self.assertRaises(ValueError):
            isolation.unit_name(Path('/tmp/sawa-native-123-2'), '../other')

    def test_arguments_do_not_accept_partial_or_fake_isolation(self):
        parser = argparse.ArgumentParser()
        isolation.add_arguments(parser)
        self.assertIsNone(isolation.verify_arguments(parser.parse_args([])))
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            isolation.verify_arguments(parser.parse_args(['--isolation-host-pid', '1']))
        args = parser.parse_args(['--isolation-host-pid', str(os.getpid()), '--isolation-host-netns',
                                  'net:[1]', '--isolation-uid', '0', '--isolation-gid', '0',
                                  '--isolation-unit', 'fake.service'])
        with self.assertRaises(RuntimeError):
            isolation.verify_arguments(args)


class IPCPolicyChecks(unittest.TestCase):
    """Portable rule/ABI/failure decisions, never accepted kernel execution proof."""
    def test_rule_comparisons_cover_only_unix_stream_and_null_destination(self):
        def refused(name, args):
            for syscall, arg, op, a, b in isolation.ipc_rules():
                if syscall == name and ((op == isolation.SCMP_NE and args[arg] != a)
                    or (op == isolation.SCMP_MASKED_EQ and args[arg] & a == b)):
                    return True
            return False
        for flags in (0, *isolation.IPC_FLAGS, isolation.IPC_FLAGS[0] | isolation.IPC_FLAGS[1]):
            self.assertFalse(refused('socketpair', [1, 1 | flags, 0]))
            for kind in (0, 2, 3, 4, 5, 6, 10):
                self.assertTrue(refused('socketpair', [1, kind | flags, 0]))
        for bit in range(32):
            if (1 << bit) & (1 | isolation.IPC_FLAGS[0] | isolation.IPC_FLAGS[1]):
                continue
            self.assertTrue(refused('socketpair', [1, 1 | (1 << bit), 0]))
        for family, protocol in ((0, 0), (2, 0), (10, 0), (-1, 0), (1, 1), (1, -1)):
            self.assertTrue(refused('socketpair', [family, 1, protocol]))
        self.assertFalse(refused('sendto', [-1, 0, 0, 0, 0, 0]))
        for address in (1, 0xffffffff, 0xffffffffffffffff):
            self.assertTrue(refused('sendto', [-1, 0, 0, 0, address, 0]))
        self.assertEqual(set(isolation.DENIED_SYSCALLS), {'connect', 'listen', 'accept', 'accept4',
            'sendmsg', 'sendmmsg', 'recvmsg', 'recvmmsg',
            'io_uring_setup', 'io_uring_enter', 'io_uring_register'})

    def test_public_abi_layout_and_action_constants(self):
        self.assertEqual(ctypes.sizeof(isolation.ArgumentComparison), 24)
        self.assertEqual([getattr(isolation.ArgumentComparison, name).offset
                          for name in ('arg', 'op', 'datum_a', 'datum_b')], [0, 4, 8, 16])
        self.assertEqual(ctypes.sizeof(isolation.SeccompVersion), 12)
        self.assertEqual((isolation.SCMP_NE, isolation.SCMP_EQ, isolation.SCMP_MASKED_EQ), (1, 4, 7))
        self.assertEqual(isolation.SCMP_ALLOW, 0x7fff0000)
        self.assertEqual(isolation.SCMP_ERRNO | errno.EPERM, 0x00050001)

    def test_library_api_errors_release_context_and_never_accept_isolation(self):
        # Deliberately no successful load double: these cases prove failure handling only.
        for stage in ('version', 'init', 'resolve', 'rule', 'load'):
            library = Mock()
            library.seccomp_version.return_value = ctypes.pointer(isolation.SeccompVersion(2, 5, 5))
            library.seccomp_init.return_value = 123
            library.seccomp_syscall_resolve_name.return_value = 44
            library.seccomp_rule_add_array.return_value = 0
            library.seccomp_load.return_value = -errno.EPERM
            function, value = {'version': ('seccomp_version', None),
                'init': ('seccomp_init', None), 'resolve': ('seccomp_syscall_resolve_name', -1),
                'rule': ('seccomp_rule_add_array', -errno.EINVAL),
                'load': ('seccomp_load', -errno.EPERM)}[stage]
            getattr(library, function).return_value = value
            with self.subTest(stage=stage), patch.object(isolation.sys, 'platform', 'linux'), \
                    patch.object(isolation.platform, 'machine', return_value='x86_64'), \
                    patch.object(isolation.os, 'listdir', return_value=['1']), \
                    patch.object(isolation.ctypes, 'CDLL', return_value=library):
                with self.assertRaises(RuntimeError):
                    isolation.install_ipc_filter()
            if stage in ('version', 'init'):
                library.seccomp_release.assert_not_called()
            else:
                library.seccomp_release.assert_called_once_with(123)
            if stage == 'load':
                self.assertEqual(library.seccomp_rule_add_array.call_count, len(isolation.ipc_rules()))
                self.assertEqual(library.seccomp_rule_add_array.argtypes[-1],
                                 ctypes.POINTER(isolation.ArgumentComparison))

    def test_missing_library_and_existing_threads_fail_closed(self):
        with patch.object(isolation.sys, 'platform', 'linux'), \
                patch.object(isolation.platform, 'machine', return_value='x86_64'), \
                patch.object(isolation.os, 'listdir', return_value=['1', '2']), \
                patch.object(isolation.ctypes, 'CDLL') as loader:
            with self.assertRaisesRegex(RuntimeError, 'precede thread'):
                isolation.install_ipc_filter()
            loader.assert_not_called()
        with patch.object(isolation.sys, 'platform', 'linux'), \
                patch.object(isolation.platform, 'machine', return_value='x86_64'), \
                patch.object(isolation.os, 'listdir', return_value=['1']), \
                patch.object(isolation.ctypes, 'CDLL', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):
                isolation.install_ipc_filter()

    def test_manifest_keeps_hidden_files_and_refuses_drift(self):
        root = probe.OUT / self._testMethodName
        (root / '.cache').mkdir(parents=True)
        (root / '.cache/.ses').write_bytes(b'owned hidden evidence')
        ci.save(root / 'sha256.json', ci.evidence_manifest(root))
        ci.verify_evidence(root)
        self.assertEqual(set(ci.load(root / 'sha256.json')), {'.cache/.ses'})
        (root / '.cache/.ses').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'incomplete or changed'):
            ci.verify_evidence(root)
        (root / '.cache/.ses').unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete or changed'):
            ci.verify_evidence(root)

    def test_manifest_refuses_outside_links_and_credential_placeholder(self):
        root = probe.OUT / self._testMethodName
        root.mkdir()
        target = probe.OUT / 'manifest-outside-sentinel'
        target.write_bytes(b'not part of this evidence root')
        link = root / 'escape'
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'escapes owned root'):
            ci.evidence_manifest(root)
        link.unlink()
        (root / 'no-credentials.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'credential placeholder'):
            ci.evidence_manifest(root)
        (root / 'no-credentials.json').unlink()


def main():
    global FOREIGN, FOREIGN_DIR, FOREIGN_FILE, OWNED_DIR
    global ISOLATION_ARGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--network-checks', action='store_true')
    isolation.add_arguments(parser)
    args = parser.parse_args()
    ISOLATION_ARGS = args
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
    try:
        probe.install_audit(args)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OwnershipChecks)
        if args.network_checks:
            for tests in (NetworkChecks, IsolationLogicChecks, IPCPolicyChecks):
                suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(tests))
        ids = [test.id() for test in suite]
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        unchanged = before == (sentinel.read_bytes(), sentinel.stat().st_mode, sentinel.stat().st_ino)
        unchanged = unchanged and sorted(p.name for p in FOREIGN.iterdir()) == ['empty', 'sentinel']
        summary = {'platform': sys.platform, 'python': sys.version, 'machine': platform.machine(),
                   'cases': ids, 'count': result.testsRun, 'failures': result.failures,
                   'errors': result.errors, 'skips': result.skipped,
                   'outside_fixture_unchanged': unchanged,
                   'expected_refusals_retained': len(probe.AUDIT['violations']),
                   'kernel_isolation_verified': probe.ISOLATION is not None,
                   'label': 'real FD tooling checks only, not native OCR acceptance'}
        # unittest failures include TestCase instances; retain their exact IDs.
        for key in ('failures', 'errors', 'skips'):
            summary[key] = [(case.id(), detail) for case, detail in summary[key]]
        probe.save(owned / 'checks.json', summary)
        return 0 if result.wasSuccessful() and unchanged and not result.skipped else 1
    finally:
        # The /dev/null and foreign-link fixtures have served their refusal checks;
        # do not publish symlinks as native evidence (in particular the null alias).
        for path in owned.rglob('*'):
            if path.is_symlink():
                path.unlink()
        for fd in (FOREIGN_DIR, FOREIGN_FILE, OWNED_DIR):
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
