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
import tempfile
import threading

import pytest
from impact_collection import CollectionInputs
from impact_owned_inputs import OwnedInputs


SCHEMA = 2
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
UNSAFE_ORIGINS = set()
UNSAFE_PATHS = set()
OUTCOMES = {}
STARTED = []
SELECTED = []
OMITTED = []
REASONS = {}
FIXTURES = {}
FIXTURE_STACK = []
INPUT_SETS = {}
ACTIVE_INPUT_SETS = set()
ACTIVE_SHARED_INPUTS = {}
COLLECTION_INPUTS = None
COLLECTION_SETS = {}
CURRENT_COLLECTION_SETS = []
COLLECTED_DEPENDENCIES = {}
DISABLED_COLLECTOR = None
BOOTSTRAP_DATABASE = None
BOOTSTRAP_DATABASE_DIGEST = None
COLLECTION_COMPLETE = False
OWNED = None
SOURCE_GRAPH = None
SOURCE_INPUTS = {}
PYTEST_BASE = None
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


def atomic_json(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.inputs-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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


def global_configuration(name):
    # Other components' configs are ordinary tracked inputs for the cases that
    # read them, not a reason to discard every backend dependency record.
    return (
        name in {'pyproject.toml', 'pytest.ini', 'tox.ini', 'setup.cfg',
                 'conftest.py', 'backend/conftest.py', 'backend/pyproject.toml',
                 'backend/pytest.ini', 'backend/tox.ini', 'backend/setup.cfg',
                 'backend/alembic.ini', 'backend/requirements.txt',
                 'backend/requirements-dev.txt', 'backend/requirements-test.txt',
                 'requirements.txt', 'requirements-dev.txt', 'requirements-test.txt',
                 'uv.lock', 'poetry.lock', 'backend/uv.lock', 'backend/poetry.lock',
                 'scripts/run_local_backend_tests.py', 'scripts/local_hospital_test_runner.py',
                 'backend/src/db.py', 'backend/src/monthly_menu_identity.py',
                 'backend/src/services/monthly_menu_identity_schema.py'}
        or name.startswith('infra/ci/') and not name.startswith('infra/ci/tests/')
        and name.endswith(('.py', '.txt', '.json', '.toml'))
        or name.startswith(('backend/alembic/', 'backend/migrations/',
                            'backend/src/models/', 'backend/src/maintenance/'))
        or name.startswith('backend/') and name.endswith('/conftest.py')
    )


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
        if global_configuration(name)
    }
    payload = {
        "schema": SCHEMA, "python": platform.python_version(),
        "platform": platform.system(),
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
        # Legacy files duplicated collection inputs per case and can exceed 6GB.
        # Never exhaust the operator's memory loading one as a reusable baseline.
        if META.stat().st_size > 64 * 1024 * 1024:
            BASELINE_STATUS = 'oversized-legacy-inputs'
            return {}
        previous = json.loads(META.read_text())
        if not isinstance(previous, dict):
            return {}
        if previous.get('schema') != SCHEMA:
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
        shared = previous.get('global_inputs')
        sets = previous.get('input_sets')
        if not isinstance(shared, dict) or not isinstance(sets, dict):
            return {}
        if any(not valid_input_record(name, value) for name, value in shared.items()):
            return {}
        for key, values in sets.items():
            if not isinstance(values, dict) or any(not valid_input_record(name, value) for name, value in values.items()):
                return {}
            if key != hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest():
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
            for field in ('input_sets', 'collection_sets'):
                references = entry.get(field)
                if not isinstance(references, list) or any(not isinstance(key, str) or key not in sets for key in references):
                    return {}
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                return {}
        BASELINE_STATUS = 'verified'
        INPUT_SETS.update(sets)
        global PREVIOUS_GLOBAL_INPUTS
        PREVIOUS_GLOBAL_INPUTS = shared
        return previous["tests"]
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {}


PREVIOUS_GLOBAL_INPUTS = {}
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
    if ACTIVE is None and len(UNSAFE_ORIGINS) < 64:
        frame = sys._getframe(1)
        origin, line = "outside-source", 0
        while frame:
            filename = Path(frame.f_code.co_filename)
            if filename != Path(__file__) and filename.is_relative_to(ROOT):
                origin, line = str(filename.relative_to(ROOT)), frame.f_lineno
                break
            frame = frame.f_back
        UNSAFE_ORIGINS.add((reason, origin, line))
    if ACTIVE is None:
        owner = COLLECTION_INPUTS.owner() if COLLECTION_INPUTS is not None else None
        target = COLLECTION_INPUTS.unsafe.setdefault(owner, set()) if owner is not None else GLOBAL_UNSAFE
    else:
        target = UNSAFE
    target.add(reason)
    for fixture in FIXTURE_STACK:
        fixture["unsafe"].add(reason)


def inherit_generated_fixture(record):
    key = record.get('input_set')
    already_inherited = key is not None and key in ACTIVE_INPUT_SETS
    if key is not None:
        ACTIVE_INPUT_SETS.add(key)
    UNSAFE.update(record['unsafe'])
    if already_inherited and not FIXTURE_STACK:
        return
    for name, value in record['inputs'].items():
        if ((name in ACTIVE_SHARED_INPUTS and ACTIVE_SHARED_INPUTS[name] != value)
                or (name in INPUTS and INPUTS[name] != value)):
            UNSAFE.add('changing-file-input')
        ACTIVE_SHARED_INPUTS.setdefault(name, value)
        for fixture in FIXTURE_STACK:
            if name in fixture['inputs'] and fixture['inputs'][name] != value:
                fixture['unsafe'].add('changing-file-input')
            fixture['inputs'].setdefault(name, value)


def generated_input(path, *, write=False, database=False, readonly=False):
    return (OWNED is not None and ACTIVE is not None
            and OWNED.accepts(path, owner=ACTIVE, stack=FIXTURE_STACK,
                              write=write, database=database, readonly=readonly))


def absolute_input_path(value):
    path = Path(os.fsdecode(value))
    return path if path.is_absolute() else Path.cwd() / path


def record_fixture_sources(function):
    seen = set()
    while function is not None and id(function) not in seen:
        seen.add(id(function))
        code = getattr(function, '__code__', None)
        if code is None:
            record_unsafe('fixture-source-unresolved')
            return
        path = Path(code.co_filename).resolve()
        if str(path) in SOURCE_GRAPH.paths:
            key = path
            if key not in SOURCE_INPUTS:
                SOURCE_INPUTS[key] = {str(Path(name).relative_to(ROOT)): digest(Path(name))
                                     for name in SOURCE_GRAPH.dependencies(path)}
            for name, value in SOURCE_INPUTS[key].items():
                for fixture in FIXTURE_STACK:
                    if name in fixture['inputs'] and fixture['inputs'][name] != value:
                        fixture['unsafe'].add('changing-file-input')
                    fixture['inputs'].setdefault(name, value)
        elif not any(path.is_relative_to(prefix) for prefix in RUNTIME_PATHS):
            record_unsafe('fixture-source-untracked')
        function = getattr(function, '__wrapped__', None)


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
    if path != STATE.parent / (STATE.name + '-runtime.sqlite') or not bootstrap_origin():
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
    if ACTIVE is not None and name in GLOBAL_INPUTS:
        if GLOBAL_INPUTS[name] != value:
            record_unsafe('changing-file-input')
        else:
            return
    if ACTIVE is None:
        owner = COLLECTION_INPUTS.owner() if COLLECTION_INPUTS is not None else None
        target = COLLECTION_INPUTS.inputs.setdefault(owner, {}) if owner is not None else GLOBAL_INPUTS
    else:
        if name in ACTIVE_SHARED_INPUTS and ACTIVE_SHARED_INPUTS[name] != value:
            record_unsafe('changing-file-input')
        target = INPUTS
    if name in target and target[name] != value:
        record_unsafe('changing-file-input')
    target.setdefault(name, value)
    for fixture in FIXTURE_STACK:
        if name in fixture['inputs'] and fixture['inputs'][name] != value:
            fixture['unsafe'].add('changing-file-input')
        fixture['inputs'].setdefault(name, value)


def audit(event, args):
    global BUSY
    if (not BUSY and OWNED is not None and ACTIVE is not None
            and threading.get_ident() == MAIN_THREAD
            and event in {'os.remove', 'os.rmdir', 'os.rename', 'os.mkdir', 'os.symlink', 'os.link', 'os.chmod', 'os.truncate'}):
        BUSY = True
        try:
            paths = args[:2] if event in {'os.rename', 'os.symlink', 'os.link'} else args[:1]
            for value in paths:
                if isinstance(value, (str, bytes, os.PathLike)):
                    generated_input(absolute_input_path(value), write=True)
        finally:
            BUSY = False
    if threading.get_ident() == MAIN_THREAD and not BUSY and OWNED is not None:
        if event == 'sqlite3.connect':
            OWNED.pending_connection = (args[0], ACTIVE)
        elif event == 'sqlite3.connect/handle':
            if OWNED.pending_connection is not None:
                OWNED.connections[id(args[0])] = OWNED.pending_connection
                OWNED.pending_connection = None
            return
        elif event == 'sawa.test.generated-sqlite-template':
            BUSY = True
            try:
                frame = sys._getframe(1)
                trusted = False
                while frame:
                    if ((frame.f_code.co_filename == str(ROOT / 'backend/tests/integration/sqlite_fixture_template.py')
                         and frame.f_code.co_name == 'migrated_sqlite_template')
                            or (frame.f_code.co_filename == str(ROOT / 'backend/conftest.py')
                                and frame.f_code.co_name == 'delivery_sqlite_template')):
                        trusted = True
                        break
                    frame = frame.f_back
                if not trusted or not args or PYTEST_BASE is None:
                    record_unsafe('generated-resource-producer-unresolved')
                    return
                producer = FIXTURE_STACK[-1] if FIXTURE_STACK else None
                OWNED.register(args[0], owner=ACTIVE, producer=producer, base=PYTEST_BASE,
                               shared=producer is not None and producer['scope'] != 'function')
            finally:
                BUSY = False
            return
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
            if args and OWNED is not None and OWNED.database(args[0], owner=ACTIVE, stack=FIXTURE_STACK):
                return
        finally:
            BUSY = False
        record_unsafe(event)
        return
    if event == 'sawa.test.database-checkout':
        if (ACTIVE is None and BOOTSTRAP_DATABASE is not None and args
                and args[0] == str(BOOTSTRAP_DATABASE) and not COLLECTION_COMPLETE and bootstrap_origin()):
            return
        if OWNED is not None and len(args) > 1 and isinstance(args[1], sqlite3.Connection):
            BUSY = True
            try:
                if OWNED.database(None, owner=ACTIVE, stack=FIXTURE_STACK, connection=args[1]):
                    return
            finally:
                BUSY = False
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
        original_path = absolute_input_path(args[0])
        path = original_path.resolve()
        if original_path != path and original_path.is_relative_to(ROOT):
            name = 'source-link:' + str(original_path.relative_to(ROOT))
            record_input(name, input_digest(name))
        if path.is_relative_to(STATE):
            return
        if any(path.is_relative_to(prefix) for prefix in RUNTIME_PATHS):
            return  # Installed versions are in the context, including in-repo venvs.
        if event != "open":
            if generated_input(original_path):
                return
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
        if generated_input(original_path, write=bool(write)):
            return
        if write:
            if path.is_relative_to(ROOT) and str(path.relative_to(ROOT)) in TRACKED:
                record_unsafe("tracked-file-write")
            return
        if path.is_relative_to(ROOT):
            name = str(path.relative_to(ROOT))
            if name in TRACKED:
                record_input(name, digest(path))
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
    global BUSY, COLLECTION_INPUTS, SOURCE_GRAPH, OWNED, PYTEST_BASE
    BUSY = True
    try:
        COLLECTION_INPUTS = CollectionInputs(ROOT, TRACKED)
        SOURCE_GRAPH = CollectionInputs(ROOT, TRACKED, include_production=True)
        OWNED = OwnedInputs(record_unsafe, inherit_generated_fixture)
        PYTEST_BASE = session.config._tmp_path_factory.getbasetemp().resolve()
    finally:
        BUSY = False
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
                original_path = absolute_input_path(path)
                if generated_input(original_path):
                    return resolved
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
                    original_path = absolute_input_path(path)
                    if generated_input(original_path):
                        return result
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
                        path = absolute_input_path(value)
                        frame = sys._getframe(1)
                        transport_stat = (frame.f_globals.get('__name__') == 'shutil'
                                          and frame.f_code.co_name == '_stat')
                        if transport_stat and generated_input(path):
                            pass
                        elif path.is_relative_to(ROOT):
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
    forced_path = STATE / 'failed-tests.json'
    try:
        forced = json.loads(forced_path.read_text()) if forced_path.exists() else []
        if not isinstance(forced, list) or any(not isinstance(node, str) for node in forced):
            raise ValueError('invalid failed-test identities')
    except (ValueError, OSError) as error:
        raise pytest.UsageError('Invalid local failed-test record; refusing selection') from error
    forced = set(forced)
    # Selection is one snapshot before case execution. Repeating the same hash
    # for thousands of fixture consumers adds no information to that decision.
    initial_inputs = {}
    def unchanged(values):
        for name, value in values.items():
            if name not in initial_inputs:
                initial_inputs[name] = input_digest(name)
            if initial_inputs[name] != value:
                return False
        return True
    checked_sets = {}
    def unchanged_set(key):
        if key not in checked_sets:
            checked_sets[key] = unchanged(INPUT_SETS[key])
        return checked_sets[key]
    for path, values in COLLECTION_INPUTS.inputs.items():
        key = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        INPUT_SETS.setdefault(key, dict(values))
        COLLECTION_SETS[path] = key
    collection_changed = PREVIOUS_GLOBAL_INPUTS != GLOBAL_INPUTS
    for item in items:
        COLLECTED_DEPENDENCIES[item.nodeid] = COLLECTION_INPUTS.dependencies(item.path)
        previous = PREVIOUS.get(item.nodeid)
        reason = None
        if FULL:
            reason = "full-or-missing-baseline"
        elif item.nodeid in forced:
            reason = 'previous-run-failed'
        elif GLOBAL_UNSAFE or any(COLLECTION_INPUTS.unsafe.get(path) for path in COLLECTION_INPUTS.dependencies(item.path)):
            reason = "untracked-collection-input"
        elif not previous or not previous.get("passed"):
            reason = "new-failed-or-missing-input-record"
        elif previous.get("unsafe"):
            reason = "untracked-input"
        elif collection_changed:
            reason = 'collection-input-changed'
        elif previous['collection_sets'] != sorted(COLLECTION_SETS[path] for path in COLLECTION_INPUTS.dependencies(item.path) if path in COLLECTION_SETS):
            reason = 'module-collection-input-changed'
        elif item.nodeid not in candidates:
            reason = "python-dependency-changed"
        elif not all(unchanged_set(key) for key in previous['input_sets']) or not unchanged(previous['inputs']):
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
    atomic_json(STATE / 'selection-plan.json', {
        'source_sha': os.environ.get('SAWA_LOCAL_SOURCE_SHA'),
        'selected': SELECTED, 'reused': OMITTED,
    })
    with (STATE / 'started-tests.jsonl').open('w', encoding='utf-8') as stream:
        stream.write(json.dumps({'source_sha': os.environ.get('SAWA_LOCAL_SOURCE_SHA')}) + '\n')


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    global ACTIVE, INPUTS, UNSAFE, BUSY, ACTIVE_INPUT_SETS, ACTIVE_SHARED_INPUTS, CURRENT_COLLECTION_SETS
    ACTIVE = item.nodeid
    INPUTS = {}
    ACTIVE_INPUT_SETS = set()
    ACTIVE_SHARED_INPUTS = {}
    UNSAFE = set(GLOBAL_UNSAFE)
    dependencies = COLLECTION_INPUTS.dependencies(item.path)
    CURRENT_COLLECTION_SETS = sorted(COLLECTION_SETS[path] for path in dependencies if path in COLLECTION_SETS)
    for path in dependencies:
        UNSAFE.update(COLLECTION_INPUTS.unsafe.get(path, ()))
        for name, value in COLLECTION_INPUTS.inputs.get(path, {}).items():
            if name in ACTIVE_SHARED_INPUTS and ACTIVE_SHARED_INPUTS[name] != value:
                UNSAFE.add('changing-file-input')
            ACTIVE_SHARED_INPUTS.setdefault(name, value)
    yield
    record_fixture_usage(item)
    BUSY = True
    try:
        if not UNSAFE and any(input_digest(name) != value for name, value in INPUTS.items()):
            UNSAFE.add('changing-file-input')
    finally:
        BUSY = False
    reports = OUTCOMES.get(item.nodeid, {})
    inherited = {}
    for key in sorted(ACTIVE_INPUT_SETS) + CURRENT_COLLECTION_SETS:
        inherited.update(INPUT_SETS[key])
    PREVIOUS[item.nodeid] = {
        "passed": all(reports.get(phase) == "passed" for phase in ("setup", "call", "teardown")),
        "inputs": {name: value for name, value in INPUTS.items() if inherited.get(name, GLOBAL_INPUTS.get(name)) != value},
        "input_sets": sorted(ACTIVE_INPUT_SETS), "unsafe": sorted(UNSAFE),
        "collection_sets": CURRENT_COLLECTION_SETS,
    }
    ACTIVE = None
    ACTIVE_INPUT_SETS = set()
    ACTIVE_SHARED_INPUTS = {}


def pytest_runtest_logreport(report):
    OUTCOMES.setdefault(report.nodeid, {})[report.when] = report.outcome


def pytest_runtest_logstart(nodeid, location):
    STARTED.append(nodeid)
    with (STATE / 'started-tests.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'nodeid': nodeid}) + '\n')


def completed_test(nodeid):
    reports = OUTCOMES.get(nodeid, {})
    return ('setup' in reports and 'teardown' in reports
            and (reports['setup'] != 'passed' or 'call' in reports))


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    global BUSY
    record = {"inputs": {}, "unsafe": set(), "scope": fixturedef.scope}
    FIXTURE_STACK.append(record)
    try:
        before, BUSY = BUSY, True
        try:
            if not GLOBAL_UNSAFE:
                record_fixture_sources(fixturedef.func)
        finally:
            BUSY = before
        outcome = yield
        if (outcome.excinfo is None and fixturedef.argname == 'tmp_path'
                and fixturedef.func.__module__ == '_pytest.tmpdir'):
            before, BUSY = BUSY, True
            try:
                OWNED.register(outcome.get_result(), owner=ACTIVE, producer=record,
                               base=PYTEST_BASE, shared=False)
            finally:
                BUSY = before
    finally:
        before, BUSY = BUSY, True
        try:
            if OWNED is not None:
                OWNED.seal(record)
        finally:
            BUSY = before
        for index, active_record in enumerate(FIXTURE_STACK):
            if active_record is record:
                del FIXTURE_STACK[index]
                break
        else:
            raise RuntimeError("Fixture tracking record is missing")
        FIXTURES[id(fixturedef)] = record
        key = hashlib.sha256(json.dumps(record['inputs'], sort_keys=True).encode()).hexdigest()
        INPUT_SETS.setdefault(key, dict(record['inputs']))
        record['input_set'] = key


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    yield
    record_fixture_usage(item)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    yield
    record_fixture_usage(item)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    request = item._request
    yield
    record_fixture_usage(item, request=request)


def record_fixture_usage(item, *, request=None):
    definitions = getattr(item._request if request is None else request, '_fixture_defs', {}).values()
    seen = set()
    for definition in definitions:
        if id(definition) in seen:
            continue
        seen.add(id(definition))
        record = FIXTURES.get(id(definition))
        if record is not None:
            inherit_generated_fixture(record)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_sessionfinish(session, exitstatus):
    global BUSY
    yield
    exitstatus = session.exitstatus
    BUSY = True
    if DISABLED_COLLECTOR is not None:
        DISABLED_COLLECTOR.pytest_sessionfinish(session)
    STATE.mkdir(parents=True, exist_ok=True)
    current = {node: entry for node, entry in PREVIOUS.items() if node in COLLECTED_DEPENDENCIES}
    if OWNED is not None:
        OWNED.finish()
    final_inputs = {}
    def changed(values):
        for name, value in values.items():
            if name not in final_inputs:
                final_inputs[name] = input_digest(name)
            if final_inputs[name] != value:
                return True
        return False
    if changed(GLOBAL_INPUTS):
        GLOBAL_UNSAFE.add('changing-file-input')
    referenced = {key for entry in current.values()
                  for key in [*entry['input_sets'], *entry['collection_sets']]}
    changed_sets = {key for key in referenced if changed(INPUT_SETS[key])}
    for node, entry in current.items():
        unsafe = set(entry['unsafe']) | GLOBAL_UNSAFE
        if OWNED is not None:
            unsafe.update(OWNED.unsafe_for(node))
        if changed_sets.intersection([*entry['input_sets'], *entry['collection_sets']]):
            unsafe.add('changing-file-input')
        for path in COLLECTED_DEPENDENCIES[node]:
            unsafe.update(COLLECTION_INPUTS.unsafe.get(path, ()))
        entry['unsafe'] = sorted(unsafe)
    if int(exitstatus) == 0:
        atomic_json(META, {"schema": SCHEMA, "context": CONTEXT, "tests": current,
            "global_inputs": GLOBAL_INPUTS,
            "input_sets": {key: INPUT_SETS[key] for key in referenced},
            "context_parts": CONTEXT_PARTS,
            "tracking_sha256": tracking_digest(), "source_sha": os.environ.get("SAWA_LOCAL_SOURCE_SHA")})
    else:
        META.unlink(missing_ok=True)
    started = set(STARTED)
    executed = [node for node in SELECTED if node in started]
    completed = [node for node in executed if completed_test(node)]
    completed_set = set(completed)
    passed = [node for node in completed if all(
        OUTCOMES[node].get(phase) == 'passed' for phase in ('setup', 'call', 'teardown'))]
    skipped = [node for node in completed if 'skipped' in OUTCOMES[node].values()]
    REPORT.write_text(json.dumps({
        "selected": SELECTED, "reused": OMITTED, "reasons": REASONS,
        "executed": executed, "completed": completed, "passed": passed,
        "skipped": skipped,
        "not_started": [node for node in SELECTED if node not in started],
        "incomplete": [node for node in executed if node not in completed_set],
        "failed": [node for node, reports in OUTCOMES.items() if 'failed' in reports.values()],
        "untracked": {node: value["unsafe"] for node, value in PREVIOUS.items() if value.get("unsafe")},
        "full": FULL, "exitstatus": int(exitstatus),
        "code_tracking_disabled": DISABLED_COLLECTOR is not None,
        "unsafe_paths": sorted(UNSAFE_PATHS),
        "unsafe_origins": [{"reason": reason, "path": path, "line": line}
                           for reason, path, line in sorted(UNSAFE_ORIGINS)],
        "context_sha256": CONTEXT, "baseline": BASELINE,
        "baseline_status": BASELINE_STATUS,
        "context_changes": sorted(CONTEXT_CHANGES),
        "source_sha": os.environ.get("SAWA_LOCAL_SOURCE_SHA"),
        "bootstrap_database": {"path": str(BOOTSTRAP_DATABASE), "sha256": BOOTSTRAP_DATABASE_DIGEST} if BOOTSTRAP_DATABASE is not None else None,
        "inputs_sha256": digest(META), "tracking_sha256": tracking_digest(),
    }, indent=2))
    BUSY = False


def pytest_terminal_summary(terminalreporter):
    started = set(STARTED)
    completed = sum(completed_test(node) for node in started)
    terminalreporter.write_line(
        f"Impact selection: planned={len(SELECTED)}, started={len(started)}, "
        f"completed={completed}, not_started={len(set(SELECTED) - started)}, "
        f"reused={len(OMITTED)}; {REPORT}"
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
