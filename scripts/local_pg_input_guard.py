"""Observe this PG test process; unknown inputs prevent command-result reuse."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
import threading

ROOT = Path(__file__).resolve().parents[1]
OWNED = Path(os.environ['SAWA_PG_COMMAND_OWNED_ROOT']).resolve()
REPORT = Path(os.environ['SAWA_PG_COMMAND_INPUT_REPORT'])
TRACKED = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
SOURCES = [ROOT / name for name in ('backend', 'scripts', 'infra', 'ocr_pipeline', 'agent_runtime')]
RUNTIME = [Path(sysconfig.get_path(kind)).resolve() for kind in ('stdlib', 'platstdlib', 'purelib', 'platlib')]
RUNTIME.extend(Path(entry).resolve() for entry in sys.path if entry and not Path(entry).resolve().is_relative_to(ROOT))
PG_BIN = Path(os.environ.get('PG_BIN', '/usr/lib/postgresql/16/bin'))
RUNTIME.append(PG_BIN.resolve())
for option in ('--libdir', '--pkglibdir', '--sharedir'):
    RUNTIME.append(Path(subprocess.check_output([str(PG_BIN / 'pg_config'), option], text=True).strip()).resolve())
unsafe = set()
passed = skipped = failed = 0
_local = threading.local()
_stat, _lstat = os.stat, os.lstat


def _unsafe(kind, value):
    if len(unsafe) < 32:
        unsafe.add((kind, str(value)))


def _read_impl(path):
    if isinstance(path, int):
        return
    try:
        value = Path(os.fsdecode(path)).resolve()
    except (TypeError, ValueError, OSError):
        _unsafe('unknown-path', type(path).__name__)
        return
    if value.is_relative_to(OWNED) or value == ROOT:
        return
    if value.is_relative_to(ROOT / 'frontend'):
        _unsafe('frontend-input', value)
    elif (any(value.is_relative_to(root) for root in SOURCES + RUNTIME)
            or (value.is_relative_to(ROOT) and str(value.relative_to(ROOT)) in TRACKED)
            or str(value) in {'/etc/os-release', '/var/lib/dpkg/status', '/lib/apk/db/installed'}):
        return
    else:
        _unsafe('outside-captured-inputs', value)


def _read(path):
    if getattr(_local, 'busy', False):
        return
    _local.busy = True
    try:
        _read_impl(path)
    finally:
        _local.busy = False


def _metadata(original):
    def observed(path, *args, **kwargs):
        try:
            if kwargs.get('dir_fd') is not None:
                _unsafe('relative-dir-fd', type(path).__name__)
            _read(path)
        except Exception as error:
            _unsafe('observer-error', type(error).__name__)
        return original(path, *args, **kwargs)
    return observed


_observed_stat, _observed_lstat = _metadata(_stat), _metadata(_lstat)
os.stat, os.lstat = _observed_stat, _observed_lstat


def _audit_event(event, args):
    if event == 'open':
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        if not flags & os.O_WRONLY:
            _read(args[0])
    elif event in {'os.listdir', 'os.scandir'}:
        _read(args[0] if args else os.getcwd())
    elif event == 'socket.connect':
        address = args[1]
        if not isinstance(address, str) or not Path(address).is_relative_to(OWNED / 'pg/socket'):
            _unsafe('external-socket', type(address).__name__)
    elif event == 'subprocess.Popen':
        argv = args[1]
        if not (isinstance(argv, (list, tuple)) and len(argv) == 3 and list(argv[:2]) == ['git', 'show']
                and isinstance(argv[2], str) and re.fullmatch(r'[0-9a-f]{40}:.+', argv[2])):
            _unsafe('subprocess-inputs', args[0])


def _audit(event, args):
    try:
        _audit_event(event, args)
    except Exception as error:
        _unsafe('observer-error', type(error).__name__)


sys.addaudithook(_audit)


def pytest_runtest_logreport(report):
    global passed, skipped, failed
    if report.failed:
        failed += 1
    if report.skipped:
        skipped += 1
    if report.when == 'call' and report.passed:
        passed += 1


def pytest_sessionfinish(session, exitstatus):
    REPORT.write_text(json.dumps({'schema': 1, 'collected': session.testscollected,
        'passed': passed, 'skipped': skipped, 'failed': failed, 'exitstatus': int(exitstatus),
        'observer_intact': os.stat is _observed_stat and os.lstat is _observed_lstat,
        'unsafe': sorted(unsafe)}, ensure_ascii=True) + '\n')
