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
OUTCOMES = {}
SELECTED = []
OMITTED = []
REASONS = {}
FIXTURES = {}
FIXTURE_STACK = []
DISABLED_COLLECTOR = None
STDLIB = str(Path(os.__file__).resolve().parent) + os.sep
RUNTIME_PATHS = tuple({Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()})


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return set(result.stdout.decode().split("\0")) - {""}


TRACKED = tracked_files()
VOLATILE_ENV = {
    "HOME", "PWD", "OLDPWD", "PATH", "TMPDIR", "TMP", "TEMP", "VIRTUAL_ENV",
    "SHLVL", "_", "TESTMON_DATAFILE", "SAWA_IMPACT_ROOT", "SAWA_IMPACT_STATE",
    "SAWA_IMPACT_FULL", "PYTEST_CURRENT_TEST", "PYTHONPATH",
}


def context():
    # CI run identifiers and temp paths are not application settings.
    env = {
        key: value for key, value in os.environ.items()
        if key not in VOLATILE_ENV
        and not key.startswith(("GITHUB_", "RUNNER_", "ACTIONS_", "INPUT_"))
    }
    configs = {
        name: digest(ROOT / name) for name in sorted(TRACKED)
        if Path(name).suffix in {".toml", ".ini", ".cfg", ".yaml", ".yml", ".lock"}
        or name.endswith(("requirements.txt", "package.json", "package-lock.json"))
        or name.endswith("conftest.py") or name.startswith("infra/ci/")
    }
    payload = {
        "schema": SCHEMA, "python": platform.python_version(),
        "platform": platform.system(), "files": sorted(TRACKED),
        "env": env, "configs": configs,
        "packages": sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                           if d.metadata["Name"]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


CONTEXT = context()


def load_previous():
    try:
        previous = json.loads(META.read_text())
        if previous["context"] != CONTEXT or not isinstance(previous["tests"], dict):
            return {}
        for node, entry in previous["tests"].items():
            if not isinstance(node, str) or not isinstance(entry, dict):
                return {}
            if not isinstance(entry.get("passed"), bool) or not isinstance(entry.get("inputs"), dict):
                return {}
            if not isinstance(entry.get("unsafe"), list):
                return {}
            if any(name not in TRACKED or not isinstance(value, str) or len(value) != 64
                   for name, value in entry["inputs"].items()):
                return {}
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                return {}
        return previous["tests"]
    except (OSError, ValueError, KeyError, sqlite3.Error):
        return {}


PREVIOUS = load_previous()
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


def audit(event, args):
    global BUSY
    # Once reuse is forbidden, further hashing cannot change the decision.
    # Still record shared fixture inputs for their other consumers.
    if BUSY or GLOBAL_UNSAFE or (UNSAFE and ACTIVE is not None and not FIXTURE_STACK) or event not in {
        "open", "os.listdir", "os.scandir", "subprocess.Popen",
        "os.system", "socket.connect", "socket.getaddrinfo",
    }:
        return
    if framework_read():
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
        path = Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(STATE):
            return
        if any(path.is_relative_to(prefix) for prefix in RUNTIME_PATHS):
            return  # Installed versions are in the context, including in-repo venvs.
        if event != "open":
            # Directory membership may include untracked or externally generated inputs.
            if path.is_relative_to(ROOT) and path.name not in {"__pycache__", ".pytest_cache"}:
                record_unsafe("directory-input")
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
                record_unsafe("external-file-input")
    finally:
        BUSY = False


def pytest_sessionstart(session):
    selector = session.config.pluginmanager.get_plugin("TestmonSelect")
    if selector is None:
        raise pytest.UsageError("test impact requires pytest-testmon 2.2.0")
    # Always collect node IDs: sidecar inputs cannot be checked if a whole file
    # is skipped by testmon before collection.
    selector.deselected_files = []
    # stat/exists have no Python audit event. Metadata, permissions and absent
    # external files cannot be proven unchanged by content hashes.
    for name in ("stat", "lstat"):
        original = getattr(os, name)

        def observed_stat(*args, _original=original, **kwargs):
            if not BUSY and not GLOBAL_UNSAFE and not framework_read():
                value = args[0] if args else kwargs.get("path")
                runtime = isinstance(value, (str, bytes, os.PathLike)) and any(
                    Path(os.path.abspath(os.fsdecode(value))).is_relative_to(prefix) for prefix in RUNTIME_PATHS
                )
                if not runtime:
                    record_unsafe("file-metadata")
            return _original(*args, **kwargs)

        setattr(os, name, observed_stat)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    global BUSY, DISABLED_COLLECTOR
    BUSY = True
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
        elif item.nodeid not in candidates:
            reason = "python-dependency-changed"
        elif any(digest(ROOT / name) != value for name, value in previous["inputs"].items()):
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


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    global ACTIVE, INPUTS, UNSAFE
    ACTIVE = item.nodeid
    INPUTS = dict(GLOBAL_INPUTS)
    UNSAFE = set(GLOBAL_UNSAFE)
    yield
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
        FIXTURE_STACK.remove(record)
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


def pytest_sessionfinish(session, exitstatus):
    global BUSY
    BUSY = True
    if DISABLED_COLLECTOR is not None:
        DISABLED_COLLECTOR.pytest_sessionfinish(session)
    STATE.mkdir(parents=True, exist_ok=True)
    for entry in PREVIOUS.values():
        entry["unsafe"] = sorted(set(entry["unsafe"]) | GLOBAL_UNSAFE)
    META.write_text(json.dumps({"context": CONTEXT, "tests": PREVIOUS}, sort_keys=True))
    REPORT.write_text(json.dumps({
        "selected": SELECTED, "reused": OMITTED, "reasons": REASONS,
        "untracked": {node: value["unsafe"] for node, value in PREVIOUS.items() if value.get("unsafe")},
        "full": FULL, "exitstatus": int(exitstatus),
        "code_tracking_disabled": DISABLED_COLLECTOR is not None,
    }, indent=2))
    BUSY = False


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(
        f"Impact selection: executed={len(SELECTED)}, unchanged={len(OMITTED)}; {REPORT}"
    )


# Observe conftest/module import inputs too, before sessionstart and collection.
sys.addaudithook(audit)
_environment_getitem = os._Environ.__getitem__


def observed_environment(self, key):
    if (not BUSY and not GLOBAL_UNSAFE and isinstance(key, str)
            and (key in VOLATILE_ENV or key.startswith(("GITHUB_", "RUNNER_", "ACTIONS_", "INPUT_")))
            and not framework_read()):
        record_unsafe("volatile-environment")
    return _environment_getitem(self, key)


os._Environ.__getitem__ = observed_environment
