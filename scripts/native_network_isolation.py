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
    'connect', 'listen', 'accept', 'accept4', 'sendto', 'sendmsg', 'sendmmsg',
    'recvfrom', 'recvmsg', 'recvmmsg', 'socketpair',
    'io_uring_setup', 'io_uring_enter', 'io_uring_register',
)
CAPABILITIES = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')


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
        denied('recvfrom', lambda: stream.recvfrom(1, socket.MSG_DONTWAIT))
        denied('recvmsg', lambda: stream.recvmsg(1, 0, socket.MSG_DONTWAIT))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
        denied('sendto', lambda: datagram.sendto(b'namespace-only', ('127.0.0.1', 9)))
        denied('sendmsg', lambda: datagram.sendmsg([b'namespace-only'], [], 0, ('127.0.0.1', 9)))
    denied('AF_UNIX', lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM), errno.EAFNOSUPPORT)
    denied('socketpair', socket.socketpair)
    libc = ctypes.CDLL(None, use_errno=True)

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
    state['kernel_refusals'] = kernel_refusals()
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
