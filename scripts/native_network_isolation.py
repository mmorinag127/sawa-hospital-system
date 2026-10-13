"""Linux systemd network boundary for trusted native verification, not a sandbox.

The service manager creates the namespace and syscall filter. The child must
verify their real effects before any Python audit exception is available.
"""
import ctypes
import errno
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import sys
import time

DENIED_SYSCALLS = (
    'connect', 'listen', 'accept', 'accept4', 'sendmsg', 'sendmmsg',
    'recvmsg', 'recvmmsg',
    'io_uring_setup', 'io_uring_enter', 'io_uring_register',
)
CAPABILITIES = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
# Linux x86_64/aarch64 UAPI values, not the portable-check host's socket flags.
IPC_FLAGS = (0x800, 0x80000)  # SOCK_NONBLOCK, SOCK_CLOEXEC
SCMP_NE, SCMP_EQ, SCMP_MASKED_EQ = 1, 4, 7
SCMP_ALLOW, SCMP_ERRNO = 0x7fff0000, 0x00050000


class ArgumentComparison(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]


class SeccompVersion(ctypes.Structure):
    _fields_ = [('major', ctypes.c_uint), ('minor', ctypes.c_uint), ('micro', ctypes.c_uint)]


def ipc_rules():
    rules = [('sendto', 4, SCMP_NE, 0, 0),
             ('socketpair', 0, SCMP_NE, 1, 0),  # AF_UNIX
             ('socketpair', 2, SCMP_NE, 0, 0),
             ('socketpair', 1, SCMP_MASKED_EQ, 1, 0)]  # STREAM bit is required
    allowed = 1 | IPC_FLAGS[0] | IPC_FLAGS[1]
    # libseccomp has masked equality, not masked inequality. Reject each forbidden
    # bit separately; comparison arrays are AND, not OR. The syscall takes int.
    for bit in range(32):
        mask = 1 << bit
        if not mask & allowed:
            rules.append(('socketpair', 1, SCMP_MASKED_EQ, mask, mask))
    return rules


def install_ipc_filter():
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise RuntimeError('Linux native libseccomp IPC filter required')
    if len(os.listdir('/proc/self/task')) != 1:
        raise RuntimeError('IPC filter must precede thread creation')
    library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    # Public libseccomp 2.x C API; array form avoids a variadic ctypes ABI.
    for name, result, arguments in (
        ('seccomp_version', ctypes.POINTER(SeccompVersion), []),
        ('seccomp_init', ctypes.c_void_p, [ctypes.c_uint32]),
        ('seccomp_syscall_resolve_name', ctypes.c_int, [ctypes.c_char_p]),
        ('seccomp_rule_add_array', ctypes.c_int, [ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ArgumentComparison)]),
        ('seccomp_load', ctypes.c_int, [ctypes.c_void_p]),
        ('seccomp_release', None, [ctypes.c_void_p]),
    ):
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
    version = library.seccomp_version()
    if not version:
        raise RuntimeError('libseccomp version unavailable')
    context = library.seccomp_init(SCMP_ALLOW)
    if not context:
        raise RuntimeError('seccomp_init failed')
    rules = ipc_rules()
    try:
        for name, arg, op, a, b in rules:
            number = library.seccomp_syscall_resolve_name(name.encode('ascii'))
            if number < 0:
                raise RuntimeError('libseccomp syscall unavailable: ' + name)
            comparison = ArgumentComparison(arg, op, a, b)
            result = library.seccomp_rule_add_array(context, SCMP_ERRNO | errno.EPERM,
                number, 1, ctypes.byref(comparison))
            if result != 0:
                raise RuntimeError(f'seccomp_rule_add_array failed: {name}: {result}')
        result = library.seccomp_load(context)
        if result != 0:
            raise RuntimeError(f'seccomp_load failed: {result}')
    finally:
        library.seccomp_release(context)  # Frees the context, not the installed filter.
    return {'library': 'libseccomp.so.2', 'version': [version.contents.major,
            version.contents.minor, version.contents.micro], 'rules': rules}


def unit_name(root, phase):
    if not re.fullmatch(r'sawa-native-[1-9][0-9]{0,19}-[1-9][0-9]{0,19}', root.name):
        raise ValueError('owned GitHub run root required')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', phase):
        raise ValueError('explicit unique native phase required')
    return root.name + '-' + phase + '.service'


def service_command(unit, argv, cwd, env, uid, gid, timeout):
    if uid <= 0 or gid <= 0 or timeout <= 0:
        raise ValueError('nonroot runner identity and bounded runtime required')
    properties = {
        'User': str(uid), 'Group': str(gid), 'WorkingDirectory': str(cwd),
        'PrivateNetwork': 'yes', 'CapabilityBoundingSet': '', 'AmbientCapabilities': '',
        'NoNewPrivileges': 'yes', 'RestrictNamespaces': 'yes',
        'SystemCallArchitectures': 'native', 'RestrictAddressFamilies': 'AF_INET AF_INET6',
        'SystemCallFilter': '~' + ' '.join(DENIED_SYSCALLS),
        'SystemCallErrorNumber': 'EPERM', 'RuntimeMaxSec': str(timeout),
        'TimeoutStopSec': '10', 'KillMode': 'control-group', 'Slice': 'system.slice',
    }
    # systemd specifiers are distinct from its environment expansion. These are
    # controlled runner paths, not arbitrary specifier-bearing command strings.
    values = [str(x) for x in argv] + list(env.values()) + list(properties.values())
    if any('%' in x or '\n' in x or '\0' in x for x in values):
        raise ValueError('ambiguous systemd argument expansion refused')
    return ['/usr/bin/sudo', '-n', '--', '/usr/bin/systemd-run', '--unit=' + unit,
            '--service-type=exec', '--wait', '--pipe', '--quiet', '--expand-environment=no',
            *['--property=' + k + '=' + v for k, v in properties.items()],
            '--', '/usr/bin/env', '-i', *[k + '=' + v for k, v in sorted(env.items())],
            *map(str, argv)]


def run_isolated(command, argv, *, root, phase, cwd, env, evidence, timeout):
    """Only this invocation's unique transient unit can be inspected or stopped."""
    if sys.platform != 'linux' or not Path('/run/systemd/system').is_dir():
        raise RuntimeError('Linux systemd isolation unavailable; no native fallback')
    uid, gid = os.getuid(), os.getgid()
    unit = unit_name(root, phase)
    cgroup = Path('/sys/fs/cgroup/system.slice') / unit
    control = []

    def systemctl(*args):
        result = subprocess.run(['/usr/bin/sudo', '-n', '--', '/usr/bin/systemctl', *args],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=20, close_fds=True)
        control.append({'argv': list(args), 'exit': result.returncode,
                        'stdout': result.stdout, 'stderr': result.stderr})
        return result

    def state():
        result = systemctl('show', unit, '-p', 'LoadState', '-p', 'ActiveState',
                          '-p', 'ControlGroup', '-p', 'ExecMainCode', '-p', 'ExecMainStatus')
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if fields.get('LoadState') not in ('loaded', 'not-found'):
            raise RuntimeError('cannot verify owned transient unit state')
        return fields

    if state()['LoadState'] != 'not-found' or cgroup.exists():
        raise RuntimeError('native unit already exists; refusing to touch it')
    host_namespace = os.readlink('/proc/self/ns/net')
    child = [*argv, '--isolation-host-pid', str(os.getpid()),
             '--isolation-host-netns', host_namespace, '--isolation-unit', unit,
             '--isolation-uid', str(uid), '--isolation-gid', str(gid)]
    launch = service_command(unit, child, cwd, env, uid, gid, timeout)
    record = {'unit': unit, 'host_netns': host_namespace, 'uid': uid, 'gid': gid,
              'cgroup': str(cgroup), 'control': control, 'cleanup_complete': False}
    try:
        result = command(launch, cwd=cwd, env=env, evidence=evidence, timeout=timeout)
    finally:
        try:
            try:
                record['unit_result'] = state()
            finally:
                # Even a failed status read must not bypass cleanup of the unit
                # whose absence we verified before this one launch attempt.
                systemctl('stop', unit)
                systemctl('reset-failed', unit)
            until = time.monotonic() + 15
            while time.monotonic() < until:
                final = state()
                if final['LoadState'] == 'not-found' and not cgroup.exists():
                    record['cleanup_complete'] = True
                    break
                time.sleep(0.1)
            if not record['cleanup_complete']:
                raise RuntimeError('owned transient unit/cgroup was not removed')
        finally:
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / 'isolation-unit.json').write_text(json.dumps(record, indent=2) + '\n')
    result['isolation_cleanup_complete'] = record['cleanup_complete']
    return result


def validate_state(state, host_namespace, uid, gid, unit):
    if uid <= 0 or gid <= 0 or state['uid'] != [uid] * 4 or state['gid'] != [gid] * 4:
        raise RuntimeError('native process retained or changed privileges')
    if state['namespace'] == host_namespace or state['host_namespace'] != host_namespace:
        raise RuntimeError('native network namespace is not isolated from the live parent')
    if state['interfaces'] != ['lo'] or state['external_routes']:
        raise RuntimeError('native namespace has a non-loopback interface or route')
    if any(state['capabilities'][name] != 0 for name in CAPABILITIES):
        raise RuntimeError('native process retained capabilities')
    if state['no_new_privs'] != 1 or state['seccomp'] != 2:
        raise RuntimeError('native privilege/syscall restrictions missing')
    if state['cgroup'] != '0::/system.slice/' + unit:
        raise RuntimeError('native process is outside its owned service cgroup')
    if state['socket_fds'] or state['uring_fds'] or not {0, 1, 2}.issubset(state['fds']):
        raise RuntimeError('socket/uring or missing inherited standard descriptor')


def external_routes():
    invalid = []
    for line in Path('/proc/net/route').read_text().splitlines()[1:]:
        row = line.split()
        destination = ipaddress.IPv4Address(int(row[1], 16).to_bytes(4, 'little'))
        if (row[0] != 'lo' or int(row[2], 16) != 0
                or not (destination.is_loopback or int(row[3], 16) & 0x200)):
            invalid.append(line)
    for line in Path('/proc/net/ipv6_route').read_text().splitlines():
        row = line.split()
        destination = ipaddress.IPv6Address(int(row[0], 16))
        if (row[-1] != 'lo' or int(row[4], 16) != 0
                or not (destination.is_loopback or int(row[8], 16) & 0x200)):
            invalid.append(line)
    return invalid


def kernel_refusals():
    """Real OS calls before any Python audit hook, with no reachable host socket."""
    results = []

    def denied(name, action, expected=errno.EPERM):
        try:
            result = action()
        except OSError as exc:
            results.append({'operation': name, 'errno': exc.errno, 'expected': expected})
            if exc.errno != expected:
                raise RuntimeError('kernel refusal missing: ' + name) from exc
        else:
            if hasattr(result, 'close'):
                result.close()
            elif isinstance(result, tuple):
                for item in result:
                    if hasattr(item, 'close'):
                        item.close()
            raise RuntimeError('kernel permitted forbidden operation: ' + name)

    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as stream:
        denied('connect', lambda: stream.connect(('::1', 9)))
        denied('listen', lambda: stream.listen(1))
        denied('accept', stream.accept)
        denied('recvmsg', lambda: stream.recvmsg(1, 0, socket.MSG_DONTWAIT))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
        denied('sendto', lambda: datagram.sendto(b'namespace-only', ('127.0.0.1', 9)))
        denied('sendmsg', lambda: datagram.sendmsg([b'namespace-only'], [], 0, ('127.0.0.1', 9)))
    denied('AF_UNIX', lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM), errno.EAFNOSUPPORT)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.socketpair.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.POINTER(ctypes.c_int)]
    libc.socketpair.restype = ctypes.c_int
    libc.sendto.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                           ctypes.c_void_p, ctypes.c_uint]
    libc.sendto.restype = ctypes.c_ssize_t

    def raw(name, *args):
        ctypes.set_errno(0)
        result = getattr(libc, name)(*args)
        if result == -1:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        return result

    # Invalid descriptors avoid blocking even if a required syscall filter is
    # absent. EPERM, not EBADF, proves the unconditional kernel filter fired.
    for name, args in (('accept', (-1, 0, 0)), ('accept4', (-1, 0, 0, 0)), ('sendmmsg', (-1, 0, 0, 0)),
                       ('recvmmsg', (-1, 0, 0, 0, 0))):
        denied(name, lambda name=name, args=args: raw(name, *args))
    # Linux x86_64/aarch64 UAPI assigns io_uring syscalls 425..427.
    for number, name in enumerate(('io_uring_setup', 'io_uring_enter', 'io_uring_register'), 425):
        denied(name, lambda number=number: raw('syscall', number, -1, 0, 0, 0, 0, 0))
    for name in ('connect', 'listen', 'sendmsg', 'recvmsg'):
        denied('C ' + name, lambda name=name: raw(name, -1, 0, 0))

    def pair(family, kind, protocol):
        fds = (ctypes.c_int * 2)(-1, -1)
        try:
            return raw('socketpair', family, kind, protocol, fds)
        finally:
            for fd in fds:
                if fd >= 0:
                    os.close(fd)

    invalid = [(socket.AF_UNIX, kind, 0) for kind in
               (0, socket.SOCK_DGRAM, socket.SOCK_RAW, socket.SOCK_SEQPACKET)]
    invalid += [(family, socket.SOCK_STREAM, 0) for family in (0, socket.AF_INET, socket.AF_INET6, -1)]
    invalid += [(socket.AF_UNIX, socket.SOCK_STREAM, protocol) for protocol in (1, -1)]
    invalid += [(socket.AF_UNIX, ctypes.c_int(1 | (1 << bit)).value, 0)
                for bit in range(32) if not (1 << bit) & (1 | IPC_FLAGS[0] | IPC_FLAGS[1])]
    for family, kind, protocol in invalid:
        denied(f'C socketpair({family},{kind},{protocol})',
               lambda: pair(family, kind, protocol))

    # EPERM on an invalid FD first proves the argument filter, not a missing path
    # or an unconnected socket. No pathname bind or host socket is ever created.
    pathname = os.fsencode(Path(os.environ['OWNED_EVIDENCE_ROOT']) / 'ipc-refused.sock')
    unix_address = ctypes.create_string_buffer(socket.AF_UNIX.to_bytes(2, sys.byteorder) + pathname + b'\0')
    ip_address = ctypes.create_string_buffer(socket.AF_INET.to_bytes(2, sys.byteorder)
        + (9).to_bytes(2, 'big') + socket.inet_aton('127.0.0.1') + bytes(8))
    for label, address in (('UNIX pathname', unix_address), ('IP destination', ip_address)):
        denied('C sendto invalid FD ' + label,
               lambda: raw('sendto', -1, b'x', 1, 0, address, len(address) - 1))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
        denied('C sendto IP', lambda: raw('sendto', datagram.fileno(), b'x', 1, 0,
                                        ip_address, len(ip_address) - 1))
    left, right = socket.socketpair()
    try:
        denied('C sendto UNIX pathname', lambda: raw('sendto', left.fileno(), b'x', 1, 0,
                                                   unix_address, len(unix_address) - 1))
        denied('Python sendto UNIX pathname', lambda: left.sendto(b'x', os.fsdecode(pathname)))
    finally:
        left.close()
        right.close()
    return results


def kernel_ipc():
    import asyncio
    import threading

    before = sorted(os.listdir('/proc/self/fd'))
    results = []
    for flags in (0, IPC_FLAGS[0], IPC_FLAGS[1], IPC_FLAGS[0] | IPC_FLAGS[1]):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM | flags, 0)
        try:
            left.settimeout(2)
            right.settimeout(2)
            left.sendall(b'left')
            if right.recv(4) != b'left':
                raise RuntimeError('anonymous STREAM IPC receive mismatch')
            right.sendall(b'right')
            if left.recv(5) != b'right':
                raise RuntimeError('anonymous STREAM IPC reverse receive mismatch')
        finally:
            left.close()
            right.close()
        results.append({'operation': 'anonymous STREAM bidirectional send/recv',
                        'flags': flags, 'closed': left.fileno() == right.fileno() == -1})

    async def wakeup():
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        thread = threading.Thread(target=lambda: loop.call_soon_threadsafe(ready.set_result, 'awake'))
        thread.start()
        try:
            if await asyncio.wait_for(ready, timeout=3) != 'awake':
                raise RuntimeError('asyncio thread wakeup mismatch')
        finally:
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError('asyncio wakeup thread not reaped')
    asyncio.run(wakeup())
    if sorted(os.listdir('/proc/self/fd')) != before or len(os.listdir('/proc/self/task')) != 1:
        raise RuntimeError('IPC preflight leaked descriptors or threads')
    results.append({'operation': 'asyncio thread wakeup', 'closed': True, 'thread_reaped': True})
    return results


def verify_current(host_pid, host_namespace, uid, gid, unit):
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise RuntimeError('Linux x86_64/aarch64 kernel isolation proof unavailable')
    if host_pid <= 1 or host_pid == os.getpid() or not re.fullmatch(r'net:\[[0-9]+\]', host_namespace):
        raise RuntimeError('live external parent network namespace required')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    fds, sockets, uring = [], [], []
    for entry in os.listdir('/proc/self/fd'):
        fd = int(entry)
        try:
            mode = os.fstat(fd).st_mode
            target = os.readlink('/proc/self/fd/' + entry)
        except OSError as exc:
            if exc.errno == errno.EBADF or exc.errno == errno.ENOENT:
                continue  # The descriptor used by listdir itself is already closed.
            raise
        fds.append(fd)
        if stat.S_ISSOCK(mode):
            sockets.append(fd)
        if 'io_uring' in target:
            uring.append(fd)
    state = {
        'namespace': os.readlink('/proc/self/ns/net'),
        'host_namespace': os.readlink(f'/proc/{host_pid}/ns/net'),
        'uid': list(map(int, status['Uid'].split())), 'gid': list(map(int, status['Gid'].split())),
        'capabilities': {key: int(status[key].strip(), 16) for key in CAPABILITIES},
        'no_new_privs': int(status['NoNewPrivs']), 'seccomp': int(status['Seccomp']),
        'cgroup': Path('/proc/self/cgroup').read_text().strip(),
        'interfaces': sorted(line.split(':')[0].strip() for line in
                             Path('/proc/net/dev').read_text().splitlines()[2:]),
        'external_routes': external_routes(), 'fds': fds, 'socket_fds': sockets, 'uring_fds': uring,
    }
    validate_state(state, host_namespace, uid, gid, unit)
    state['ipc_filter'] = install_ipc_filter()
    state['kernel_refusals'] = kernel_refusals()
    state['kernel_ipc'] = kernel_ipc()
    return state


def capability_bind(sock, address):
    return (type(address) is tuple and address == ('::1', 0)
            and sock.family == socket.AF_INET6 and sock.type == socket.SOCK_STREAM
            and sock.proto in (0, socket.IPPROTO_TCP) and sock.fileno() >= 0
            and sock.getsockname() == ('::', 0, 0, 0))


def add_arguments(parser):
    parser.add_argument('--isolation-host-pid', type=int)
    parser.add_argument('--isolation-host-netns')
    parser.add_argument('--isolation-unit')
    parser.add_argument('--isolation-uid', type=int)
    parser.add_argument('--isolation-gid', type=int)


def verify_arguments(args):
    values = (args.isolation_host_pid, args.isolation_host_netns, args.isolation_uid,
              args.isolation_gid, args.isolation_unit)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise RuntimeError('incomplete kernel isolation request')
    return verify_current(*values)
