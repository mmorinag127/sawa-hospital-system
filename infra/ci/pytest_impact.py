"""Conservative per-test selection: testmon code coverage plus file inputs.

Only successful, dependency-complete records may suppress a test. Network,
subprocess and untracked inputs remain executable. This plugin is CI opt-in.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import threading

import pytest


SCHEMA = 1
ROOT = Path(os.environ["SAWA_IMPACT_ROOT"]).resolve()
STATE = Path(os.environ["SAWA_IMPACT_STATE"]).resolve()
if os.environ.get("SAWA_IMPACT_SPLIT_RUNS") == "1":
    # Keep separate pytest commands independent, ignoring run-owned reports.
    arguments = [value for value in sys.argv[1:] if not value.startswith(
        ("--basetemp=", "--junitxml=", "--durations="))]
    STATE /= hashlib.sha256(json.dumps(arguments).encode()).hexdigest()[:20]
    os.environ["TESTMON_DATAFILE"] = str(STATE / "testmon.sqlite")
META = STATE / "inputs.json"
DB = Path(os.environ["TESTMON_DATAFILE"]).resolve()
REPORT = STATE / "selection.json"
BUSY = False
ACTIVE = None
INPUTS = {}
UNSAFE = set()
GLOBAL_INPUTS = {}
GLOBAL_UNSAFE = set()
UNSAFE_PATHS = set()
OUTCOMES = {}
SELECTED = []
OMITTED = []
REASONS = {}
FIXTURES = {}
FIXTURE_STACK = []
DISABLED_COLLECTOR = None
BOOTSTRAP_DATABASE = None
BOOTSTRAP_DATABASE_DIGEST = None
COLLECTION_COMPLETE = False
STDLIB = str(Path(os.__file__).resolve().parent) + os.sep
RUNTIME_PATHS = tuple({Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()})
MAIN_THREAD = threading.get_ident()
STAT_READERS = {name: getattr(os, name) for name in ('stat', 'lstat')}


def metadata_digest(path, method):
    try:
        info = STAT_READERS[method](path)
        value = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
    except OSError as error:
        value = {'error': type(error).__name__, 'errno': error.errno}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def tracking_digest():
    try:
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
    except sqlite3.Error:
        return None


def input_digest(name):
    for method in ('stat', 'lstat'):
        prefix = method + ':'
        if name.startswith(prefix):
            path = Path(os.path.abspath(ROOT / name.removeprefix(prefix)))
            return metadata_digest(path, method) if path.is_relative_to(ROOT) else None
    if name.startswith('absent:'):
        return absent_digest(Path(name.removeprefix('absent:')))
    for method in ('exists', 'is_file', 'is_dir'):
        prefix = 'path-' + method + ':'
        if name.startswith(prefix):
            path = Path(os.path.abspath(ROOT / name.removeprefix(prefix)))
            try:
                return hashlib.sha256(json.dumps(getattr(path, method)()).encode()).hexdigest()
            except OSError:
                return None
    if name.startswith('source-link:'):
        path = Path(os.path.abspath(ROOT / name.removeprefix('source-link:')))
        return resolution_digest(path) if path.is_relative_to(ROOT) else None
    if name.startswith('runtime-link:'):
        return runtime_link_digest(Path(name.removeprefix('runtime-link:')))
    if name.startswith('runtime:'):
        path = Path(name.removeprefix('runtime:'))
        return digest(path) if immutable_runtime_file(path) else None
    if name.startswith('environment:'):
        key = name.removeprefix('environment:')
        try:
            value = _environment_getitem(os.environ, key)
        except KeyError:
            value = None
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()
    if not name.startswith('directory:'):
        return digest(ROOT / name)
    path = (ROOT / name.removeprefix('directory:')).resolve()
    if not path.is_relative_to(ROOT):
        return None
    try:
        entries = []
        for child in path.iterdir():
            info = child.lstat()
            attributes = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            entries.append((child.name, attributes, os.readlink(child) if child.is_symlink() else None))
        return hashlib.sha256(json.dumps(sorted(entries), sort_keys=True).encode()).hexdigest()
    except OSError:
        return None


def immutable_runtime_file(path):
    roots = tuple(Path(value) for value in (
        '/usr/share', '/etc', '/private/etc', '/System/Library', '/private/var/db/timezone'))
    try:
        return (path.resolve() == path and any(path.is_relative_to(root) for root in roots)
                and all(item.stat().st_uid == 0 and not os.access(item, os.W_OK)
                        for item in (path, *path.parents)))
    except OSError:
        return False


def root_owned_path(path):
    try:
        return all(item.lstat().st_uid == 0 and not os.access(item, os.W_OK)
                   for item in (path, *path.parents))
    except OSError:
        return False


def runtime_link_digest(path):
    try:
        target = path.resolve(strict=True)
        if not path.is_absolute() or not immutable_runtime_file(target) or not root_owned_path(path):
            return None
        return resolution_digest(path)
    except OSError:
        return None


def resolution_digest(path):
    try:
        target = path.resolve(strict=False)
        links = [(str(item), os.readlink(item)) for item in (path, *path.parents) if item.is_symlink()]
        return hashlib.sha256(json.dumps([str(target), links]).encode()).hexdigest()
    except (OSError, RuntimeError):
        return None


def absent_digest(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return hashlib.sha256(b'absent').hexdigest()
    except OSError:
        pass
    return None


def valid_input_record(name, value):
    if (not isinstance(name, str) or not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        return False
    if name in TRACKED:
        return True
    kind, separator, path = name.partition(':')
    if not separator or not path or '\0' in path:
        return False
    if kind == 'environment':
        return True
    if '..' in Path(path).parts:
        return False
    if kind in ('path-exists', 'path-is_file', 'path-is_dir'):
        return True
    if kind in ('directory', 'source-link', 'stat', 'lstat'):
        return not Path(path).is_absolute()
    return kind in ('runtime', 'runtime-link', 'absent') and Path(path).is_absolute()


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return set(result.stdout.decode().split("\0")) - {""}


TRACKED = tracked_files()
VOLATILE_ENV = {
    "INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID", "MEMORY_PRESSURE_WATCH",
    "MEMORY_PRESSURE_WRITE", "NODE_TEST_WORKER_ID",
    "CODEX_TOOL_CALL_ID",
    "HOME", "PWD", "OLDPWD", "PATH", "TMPDIR", "TMP", "TEMP", "VIRTUAL_ENV",
    "SHLVL", "_", "TESTMON_DATAFILE", "SAWA_IMPACT_ROOT", "SAWA_IMPACT_STATE",
    "SAWA_IMPACT_FULL", "PYTEST_CURRENT_TEST", "PYTHONPATH",
    "PG_BIN", "REFRESH_TEST_PG_BIN",
}
RUNNER_PREFIXES = ("GITHUB_", "RUNNER_", "ACTIONS_", "INPUT_", "SAWA_LOCAL_")


def context():
    global CONTEXT_PARTS
    # CI run identifiers and temp paths are not application settings.
    env = {
        key: value for key, value in os.environ.items()
        if key not in VOLATILE_ENV
        and not key.startswith(RUNNER_PREFIXES)
    }
    configs = {
        name: digest(ROOT / name) for name in sorted(TRACKED)
        if Path(name).suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml", ".lock"}
        or name.endswith(("requirements.txt", "package.json", "package-lock.json"))
        or name.endswith("conftest.py") or name.startswith("infra/ci/")
        or name.startswith(('backend/alembic/', 'backend/migrations/', 'backend/src/models/', 'backend/src/maintenance/'))
        or name in {'backend/src/db.py', 'backend/src/monthly_menu_identity.py', 'backend/src/services/monthly_menu_identity_schema.py'}
    }
    payload = {
        "schema": SCHEMA, "python": platform.python_version(),
        "platform": platform.system(), "files": sorted(TRACKED),
        "env": env, "configs": configs,
        "packages": sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                           if d.metadata["Name"]),
    }
    CONTEXT_PARTS = {key: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                     for key, value in payload.items() if key != 'env'}
    CONTEXT_PARTS['env'] = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in env.items()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


CONTEXT = context()
BASELINE_STATUS = 'missing-or-invalid'
CONTEXT_CHANGES = []


def load_previous():
    global BASELINE_STATUS, CONTEXT_CHANGES
    try:
        previous = json.loads(META.read_text())
        if not isinstance(previous, dict):
            return {}
        if os.environ.get('SAWA_LOCAL_SOURCE_SHA'):
            source = previous.get('source_sha')
            if not isinstance(source, str) or len(source) != 40 or any(char not in '0123456789abcdef' for char in source):
                return {}
        if previous["context"] != CONTEXT or not isinstance(previous["tests"], dict):
            BASELINE_STATUS = 'context-changed'
            before = previous.get('context_parts', {})
            before = before if isinstance(before, dict) else {}
            CONTEXT_CHANGES = [key for key in CONTEXT_PARTS if key != 'env' and before.get(key) != CONTEXT_PARTS[key]]
            old_env = before.get('env', {})
            old_env = old_env if isinstance(old_env, dict) else {}
            CONTEXT_CHANGES += ['env:' + key for key in set(old_env) | set(CONTEXT_PARTS['env'])
                                if old_env.get(key) != CONTEXT_PARTS['env'].get(key)]
            return {}
        if previous.get("tracking_sha256") != tracking_digest():
            BASELINE_STATUS = 'tracking-data-changed'
            return {}
        for node, entry in previous["tests"].items():
            if not isinstance(node, str) or not isinstance(entry, dict):
                return {}
            if not isinstance(entry.get("passed"), bool) or not isinstance(entry.get("inputs"), dict):
                return {}
            if not isinstance(entry.get("unsafe"), list) or not all(isinstance(reason, str) for reason in entry['unsafe']):
                return {}
            if any(not valid_input_record(name, value) for name, value in entry["inputs"].items()):
                return {}
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                return {}
        BASELINE_STATUS = 'verified'
        return previous["tests"]
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {}


PREVIOUS = load_previous()
BASELINE = {"inputs_sha256": digest(META), "tracking_sha256": tracking_digest(),
            "source_sha": json.loads(META.read_text()).get("source_sha")} if PREVIOUS else None
FULL = os.environ.get("SAWA_IMPACT_FULL") == "1" or not PREVIOUS
if FULL:
    STATE.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)


def framework_read():
    try:
        frame = sys._getframe(2)
    except ValueError:
        return True
    while frame:
        filename = frame.f_code.co_filename
        if (filename == importlib.metadata.__file__
                and frame.f_code.co_name in {"mtime", "children"}):
            # Distribution discovery is covered by the installed package catalog.
            return True
        if filename == __file__ or (
            filename.startswith(STDLIB) and "/site-packages/" not in filename
        ):
            frame = frame.f_back
            continue
        if filename.startswith("<frozen importlib") or any(
            part in filename for part in ("/_pytest/", "/testmon/", "/coverage/", "/pluggy/")
        ):
            return True
        if filename.startswith("<frozen "):
            frame = frame.f_back
            continue
        return False
    return False


def record_unsafe(reason):
    (GLOBAL_UNSAFE if ACTIVE is None else UNSAFE).add(reason)
    for fixture in FIXTURE_STACK:
        fixture["unsafe"].add(reason)


def progress_environment_read():
    frame = sys._getframe(1)
    while frame:
        if (frame.f_code.co_name == 'envwrap' and frame.f_code.co_filename.endswith('/tqdm/utils.py')
                and frame.f_locals.get('prefix') == 'TQDM_'
                and any(Path(frame.f_code.co_filename).is_relative_to(prefix) for prefix in RUNTIME_PATHS)):
            # tqdm selects only TQDM_* settings, all included in CONTEXT.
            return True
        frame = frame.f_back
    return False


def bootstrap_origin():
    frame = sys._getframe(1)
    while frame:
        if frame.f_code.co_filename == str(ROOT / 'backend/conftest.py') and frame.f_code.co_name == '<module>':
            return True
        frame = frame.f_back
    return False


def empty_database_bootstrap(database):
    global BOOTSTRAP_DATABASE
    if ACTIVE is not None or BOOTSTRAP_DATABASE is not None or not isinstance(database, str):
        return False
    path = Path(database)
    if path != STATE.parent / 'backend-runtime.sqlite' or not bootstrap_origin():
        return False
    try:
        empty = not path.is_symlink() and path.is_file() and path.stat().st_size == 0
    except OSError:
        empty = False
    if not empty:
        return False
    BOOTSTRAP_DATABASE = path
    return True


def record_input(name, value):
    if value is None:
        record_unsafe('unreadable-input')
        return
    target = GLOBAL_INPUTS if ACTIVE is None else INPUTS
    if name in target and target[name] != value:
        record_unsafe('changing-file-input')
    target.setdefault(name, value)
    for fixture in FIXTURE_STACK:
        if name in fixture['inputs'] and fixture['inputs'][name] != value:
            fixture['unsafe'].add('changing-file-input')
        fixture['inputs'].setdefault(name, value)


def audit(event, args):
    global BUSY
    if threading.get_ident() != MAIN_THREAD and event in {
        'open', 'os.listdir', 'os.scandir', 'subprocess.Popen', 'os.system', 'socket.connect', 'socket.getaddrinfo', 'sqlite3.connect', 'sawa.test.database-checkout',
    }:
        record_unsafe('threaded-input')
        return
    # Once reuse is forbidden, further hashing cannot change the decision.
    # Still record shared fixture inputs for their other consumers.
    if BUSY or GLOBAL_UNSAFE or (UNSAFE and ACTIVE is not None and not FIXTURE_STACK) or event not in {
        "open", "os.listdir", "os.scandir", "subprocess.Popen",
        "os.system", "socket.connect", "socket.getaddrinfo", "sqlite3.connect", "sawa.test.database-checkout",
    }:
        return
    if framework_read():
        return
    if event == 'sqlite3.connect':
        BUSY = True
        try:
            if args and empty_database_bootstrap(args[0]):
                return
        finally:
            BUSY = False
        record_unsafe(event)
        return
    if event == 'sawa.test.database-checkout':
        if (ACTIVE is None and BOOTSTRAP_DATABASE is not None and args
                and args[0] == str(BOOTSTRAP_DATABASE) and not COLLECTION_COMPLETE and bootstrap_origin()):
            return
        record_unsafe(event)
        return
    if event in {"subprocess.Popen", "os.system", "socket.connect", "socket.getaddrinfo"}:
        record_unsafe(event)
        return
    if event not in {"open", "os.listdir", "os.scandir"}:
        return
    if not args or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    BUSY = True
    try:
        original_path = Path(os.path.abspath(os.fsdecode(args[0])))
        path = original_path.resolve()
        if original_path != path and original_path.is_relative_to(ROOT):
            name = 'source-link:' + str(original_path.relative_to(ROOT))
            record_input(name, input_digest(name))
        if path.is_relative_to(STATE):
            return
        if any(path.is_relative_to(prefix) for prefix in RUNTIME_PATHS):
            return  # Installed versions are in the context, including in-repo venvs.
        if event != "open":
            if path.is_relative_to(ROOT) and path.name not in {"__pycache__", ".pytest_cache"}:
                name = 'directory:' + str(path.relative_to(ROOT))
                value = input_digest(name)
                if value is None:
                    UNSAFE_PATHS.add(str(path.relative_to(ROOT)))
                    record_unsafe("directory-input")
                else:
                    record_input(name, value)
            elif not path.is_relative_to(ROOT):
                UNSAFE_PATHS.add(str(path))
                record_unsafe('external-directory-input')
            return
        mode = args[1]
        flags = args[2] if len(args) > 2 else 0
        write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
        )
        if write:
            if path.is_relative_to(ROOT) and str(path.relative_to(ROOT)) in TRACKED:
                record_unsafe("tracked-file-write")
            return
        if path.is_relative_to(ROOT):
            name = str(path.relative_to(ROOT))
            if name in TRACKED:
                target = GLOBAL_INPUTS if ACTIVE is None else INPUTS
                value = digest(path)
                if value is None or (name in target and target[name] != value):
                    record_unsafe("changing-file-input")
                target[name] = value
                for fixture in FIXTURE_STACK:
                    fixture["inputs"][name] = value
            elif "__pycache__" not in path.parts and ".pytest_cache" not in path.parts:
                record_unsafe("untracked-file-input")
        else:
            if str(path) not in {"/dev/null", "/dev/urandom"}:
                missing = absent_digest(original_path)
                if missing is not None:
                    record_input('absent:' + str(original_path), missing)
                elif not immutable_runtime_file(path):
                    UNSAFE_PATHS.add(str(path))
                    record_unsafe("external-file-input")
                else:
                    if original_path != path:
                        name = 'runtime-link:' + str(original_path)
                        record_input(name, input_digest(name))
                    name = 'runtime:' + str(path)
                    record_input(name, input_digest(name))
    finally:
        BUSY = False


def pytest_sessionstart(session):
    selector = session.config.pluginmanager.get_plugin("TestmonSelect")
    if selector is None:
        raise pytest.UsageError("test impact requires pytest-testmon 2.2.0")
    # Always collect node IDs: sidecar inputs cannot be checked if a whole file
    # is skipped by testmon before collection.
    selector.deselected_files = []
    def observe_resolution(original):
        def observed(path, *args, **kwargs):
            global BUSY
            if BUSY or GLOBAL_UNSAFE or framework_read():
                return original(path, *args, **kwargs)
            BUSY = True
            try:
                resolved = original(path, *args, **kwargs)
                original_path = Path(os.path.abspath(os.fsdecode(path)))
                if original_path.is_relative_to(ROOT):
                    name = 'source-link:' + str(original_path.relative_to(ROOT))
                    record_input(name, resolution_digest(original_path))
                    if kwargs.get('strict') or (args and args[0]):
                        name = 'path-exists:' + str(original_path.relative_to(ROOT))
                        record_input(name, input_digest(name))
                elif not any(original_path.is_relative_to(prefix) for prefix in RUNTIME_PATHS):
                    record_unsafe('external-path-resolution')
                return resolved
            except (OSError, RuntimeError):
                record_unsafe('unresolved-path-input')
                raise
            finally:
                BUSY = False
        return observed

    Path.resolve = observe_resolution(Path.resolve)
    os.path.realpath = observe_resolution(os.path.realpath)
    def observe_predicate(original, method):
        def observed(path, *args, **kwargs):
            global BUSY
            if BUSY or GLOBAL_UNSAFE or framework_read():
                return original(path, *args, **kwargs)
            BUSY = True
            try:
                result = original(path, *args, **kwargs)
                if args or kwargs:
                    record_unsafe('file-predicate-options')
                    return result
                if isinstance(path, (str, bytes, os.PathLike)):
                    original_path = Path(os.path.abspath(os.fsdecode(path)))
                    name = 'path-' + method + ':' + str(original_path.relative_to(ROOT) if original_path.is_relative_to(ROOT) else original_path)
                    record_input(name, hashlib.sha256(json.dumps(result).encode()).hexdigest())
                else:
                    record_unsafe('file-predicate-descriptor')
                return result
            except OSError:
                record_unsafe('unreadable-file-metadata')
                raise
            finally:
                BUSY = False
        return observed

    for method, os_method in (('exists', 'exists'), ('is_file', 'isfile'), ('is_dir', 'isdir')):
        setattr(Path, method, observe_predicate(getattr(Path, method), method))
        setattr(os.path, os_method, observe_predicate(getattr(os.path, os_method), method))
    # Direct metadata reads need the complete stat result, not a content hash.
    for name in ("stat", "lstat"):
        original = getattr(os, name)

        def observed_stat(*args, _original=original, _method=name, **kwargs):
            global BUSY
            if not BUSY and not GLOBAL_UNSAFE and not framework_read():
                value = args[0] if args else kwargs.get("path")
                BUSY = True
                try:
                    if isinstance(value, (str, bytes, os.PathLike)) and len(args) <= 1 and not kwargs:
                        path = Path(os.path.abspath(os.fsdecode(value)))
                        if path.is_relative_to(ROOT):
                            record_input(_method + ':' + str(path.relative_to(ROOT)), metadata_digest(path, _method))
                        elif not any(path.is_relative_to(prefix) for prefix in RUNTIME_PATHS):
                            record_unsafe('file-metadata')
                    else:
                        record_unsafe('file-metadata')
                finally:
                    BUSY = False
            return _original(*args, **kwargs)

        setattr(os, name, observed_stat)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    global BUSY, DISABLED_COLLECTOR, COLLECTION_COMPLETE, BOOTSTRAP_DATABASE_DIGEST
    BUSY = True
    if BOOTSTRAP_DATABASE is not None:
        BOOTSTRAP_DATABASE_DIGEST = digest(BOOTSTRAP_DATABASE)
        if BOOTSTRAP_DATABASE_DIGEST is None:
            record_unsafe('unreadable-bootstrap-database')
    COLLECTION_COMPLETE = True
    selector = config.pluginmanager.get_plugin("TestmonSelect")
    candidates = set(selector.deselected_tests)
    stable = []
    for item in items:
        previous = PREVIOUS.get(item.nodeid)
        reason = None
        if FULL:
            reason = "full-or-missing-baseline"
        elif GLOBAL_UNSAFE:
            reason = "untracked-collection-input"
        elif not previous or not previous.get("passed"):
            reason = "new-failed-or-missing-input-record"
        elif previous.get("unsafe"):
            reason = "untracked-input"
        elif any(previous['inputs'].get(name) != value for name, value in GLOBAL_INPUTS.items()):
            reason = 'collection-input-changed'
        elif item.nodeid not in candidates:
            reason = "python-dependency-changed"
        elif any(input_digest(name) != value for name, value in previous["inputs"].items()):
            reason = "file-input-changed"
        if reason:
            REASONS[item.nodeid] = reason
        else:
            stable.append(item.nodeid)
            OMITTED.append(item.nodeid)
    selector.deselected_tests = stable
    if GLOBAL_UNSAFE:
        # Every test must execute. Coverage cannot make this run reusable, so
        # keep the tests and selector but do not instrument their execution.
        collector = config.pluginmanager.get_plugin("TestmonCollect")
        if collector is not None:
            config.pluginmanager.unregister(collector)
            DISABLED_COLLECTOR = collector
    BUSY = False
    yield
    SELECTED.extend(item.nodeid for item in items)
    executed = set(SELECTED)
    OMITTED[:] = [node for node in OMITTED if node not in executed]


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    global ACTIVE, INPUTS, UNSAFE, BUSY
    ACTIVE = item.nodeid
    INPUTS = dict(GLOBAL_INPUTS)
    UNSAFE = set(GLOBAL_UNSAFE)
    yield
    BUSY = True
    try:
        if not UNSAFE and any(input_digest(name) != value for name, value in INPUTS.items()):
            UNSAFE.add('changing-file-input')
    finally:
        BUSY = False
    reports = OUTCOMES.get(item.nodeid, {})
    PREVIOUS[item.nodeid] = {
        "passed": all(reports.get(phase) == "passed" for phase in ("setup", "call", "teardown")),
        "inputs": dict(INPUTS), "unsafe": sorted(UNSAFE),
    }
    ACTIVE = None


def pytest_runtest_logreport(report):
    OUTCOMES.setdefault(report.nodeid, {})[report.when] = report.outcome


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    record = {"inputs": {}, "unsafe": set()}
    FIXTURE_STACK.append(record)
    try:
        yield
    finally:
        for index, active_record in enumerate(FIXTURE_STACK):
            if active_record is record:
                del FIXTURE_STACK[index]
                break
        else:
            raise RuntimeError("Fixture tracking record is missing")
        FIXTURES[id(fixturedef)] = record


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    yield
    for definitions in item._fixtureinfo.name2fixturedefs.values():
        for definition in definitions:
            record = FIXTURES.get(id(definition))
            if record is not None:
                INPUTS.update(record["inputs"])
                UNSAFE.update(record["unsafe"])


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_sessionfinish(session, exitstatus):
    global BUSY
    yield
    exitstatus = session.exitstatus
    BUSY = True
    if DISABLED_COLLECTOR is not None:
        DISABLED_COLLECTOR.pytest_sessionfinish(session)
    STATE.mkdir(parents=True, exist_ok=True)
    for entry in PREVIOUS.values():
        entry["unsafe"] = sorted(set(entry["unsafe"]) | GLOBAL_UNSAFE)
    if int(exitstatus) == 0:
        META.write_text(json.dumps({"context": CONTEXT, "tests": PREVIOUS,
            "context_parts": CONTEXT_PARTS,
            "tracking_sha256": tracking_digest(), "source_sha": os.environ.get("SAWA_LOCAL_SOURCE_SHA")}, sort_keys=True))
    else:
        META.unlink(missing_ok=True)
    REPORT.write_text(json.dumps({
        "selected": SELECTED, "reused": OMITTED, "reasons": REASONS,
        "untracked": {node: value["unsafe"] for node, value in PREVIOUS.items() if value.get("unsafe")},
        "full": FULL, "exitstatus": int(exitstatus),
        "code_tracking_disabled": DISABLED_COLLECTOR is not None,
        "unsafe_paths": sorted(UNSAFE_PATHS),
        "context_sha256": CONTEXT, "baseline": BASELINE,
        "baseline_status": BASELINE_STATUS,
        "context_changes": sorted(CONTEXT_CHANGES),
        "source_sha": os.environ.get("SAWA_LOCAL_SOURCE_SHA"),
        "bootstrap_database": {"path": str(BOOTSTRAP_DATABASE), "sha256": BOOTSTRAP_DATABASE_DIGEST} if BOOTSTRAP_DATABASE is not None else None,
        "inputs_sha256": digest(META), "tracking_sha256": tracking_digest(),
    }, indent=2))
    BUSY = False


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(
        f"Impact selection: executed={len(SELECTED)}, unchanged={len(OMITTED)}; {REPORT}"
    )


# Observe conftest/module import inputs too, before sessionstart and collection.
sys.addaudithook(audit)
_environment_getitem = os._Environ.__getitem__
_environment_iter = os._Environ.__iter__


def observed_environment(self, key):
    global BUSY
    if (not BUSY and not GLOBAL_UNSAFE and isinstance(key, str)
            and (key in VOLATILE_ENV or key.startswith(RUNNER_PREFIXES))
            and not framework_read() and not progress_environment_read()):
        BUSY = True
        try:
            name = 'environment:' + key
            value = input_digest(name)
            record_input(name, value)
        finally:
            BUSY = False
    return _environment_getitem(self, key)


os._Environ.__getitem__ = observed_environment


def observed_environment_iter(self):
    if not BUSY and not GLOBAL_UNSAFE and not framework_read() and not progress_environment_read():
        record_unsafe("volatile-environment")
    return _environment_iter(self)


os._Environ.__iter__ = observed_environment_iter

_thread_start = threading.Thread.start


def observed_thread_start(self, *args, **kwargs):
    if not framework_read():
        record_unsafe('threaded-input')
    return _thread_start(self, *args, **kwargs)


threading.Thread.start = observed_thread_start
