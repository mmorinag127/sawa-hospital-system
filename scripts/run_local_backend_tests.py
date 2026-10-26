"""Run general cases once; owned PostgreSQL modules have dedicated runners."""
import json
import os
from pathlib import Path
import subprocess
import sys
import fcntl
from contextlib import closing, contextmanager
from importlib.metadata import PackageNotFoundError, version
import shutil
import signal
import sqlite3
import tempfile
from types import SimpleNamespace

from local_hospital_test_runner import ROOT, clean_tree, git, reject_hosted_runner
from run_readonly_pg_tests import MODULES


DEDICATED_MODULES = {
    **{name: MODULES[suite] for suite, name in {
        "readonly": "hospital-readonly-pg", "c1": "hospital-c1-pg",
        "c1-guards": "hospital-c1-guard-pg", "menu-identity": "hospital-menu-identity-pg",
    }.items()},
    "modernization-pg-output": MODULES["output"] + MODULES["formal"],
    "live-schema-adoption": ["backend/tests/integration/test_live_schema_adoption.py",
                             "backend/tests/unit/test_prepare_stg_saved_import.py",
                             "backend/tests/integration/test_stg_saved_import_transport.py"],
}


def require_impact_dependencies():
    try:
        matches = all(version(name) == expected for name, expected in (
            ('pytest-testmon', '2.2.0'), ('coverage', '7.17.0')))
    except PackageNotFoundError:
        matches = False
    if not matches:
        raise ValueError('Prepare local test dependencies: python -m pip install -r infra/ci/requirements-local-tests.txt')


@contextmanager
def preserve_baseline(state):
    """A failed attempt must not turn the next small fix into a cold full run."""
    names = ('inputs.json', 'testmon.sqlite')
    if any((state / name).is_symlink() for name in (*names, 'testmon.sqlite-wal', 'testmon.sqlite-shm', 'testmon.sqlite-journal')):
        raise ValueError('Local dependency state must not be a symlink')
    with tempfile.TemporaryDirectory(prefix='backend-baseline-', dir=state.parent) as directory:
        root = Path(directory)
        present = {name for name in names if (state / name).is_file()}
        if 'inputs.json' in present:
            # The tracker publishes by rename, so this retains even an old large
            # baseline without reading/copying gigabytes or sharing mutable data.
            os.link(state / 'inputs.json', root / 'inputs.json')
        if 'testmon.sqlite' in present:
            try:
                with closing(sqlite3.connect((state / 'testmon.sqlite').as_uri() + '?mode=ro', uri=True)) as source:
                    with closing(sqlite3.connect(root / 'testmon.sqlite')) as destination:
                        source.backup(destination)
            except sqlite3.DatabaseError:
                shutil.copyfile(state / 'testmon.sqlite', root / 'testmon.sqlite')
        checkpoint = SimpleNamespace(accepted=False, directory=root)
        try:
            yield checkpoint
        finally:
            if not checkpoint.accepted:
                for suffix in ('', '-wal', '-shm', '-journal'):
                    target = Path(str(state / 'testmon.sqlite') + suffix)
                    if target.is_symlink():
                        raise ValueError('Local tracking database became a symlink')
                    target.unlink(missing_ok=True)
                (state / 'inputs.json').unlink(missing_ok=True)
                for name in present:
                    if name == 'inputs.json':
                        os.replace(root / name, state / name)
                    else:
                        shutil.copyfile(root / name, state / name)


def run_pytest(argv, env):
    with subprocess.Popen(argv, cwd=ROOT, env=env, start_new_session=True) as process:
        try:
            return subprocess.CompletedProcess(argv, process.wait())
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
            raise


def retain_failures(state, scratch, failed, *, successful=False):
    previous_path = state / 'failed-tests.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else []
    if any(not isinstance(values, list) or any(not isinstance(node, str) for node in values)
           for values in (failed, previous)):
        raise ValueError('Invalid failed-test identities')
    temporary = scratch / 'failed-tests.json'
    temporary.write_text(json.dumps(sorted(set(failed) | (set() if successful else set(previous)))))
    os.replace(temporary, previous_path)


def main():
    reject_hosted_runner()
    if not clean_tree():
        raise ValueError("Backend tests require a fixed clean source")
    require_impact_dependencies()
    source = git("rev-parse", "HEAD")
    evidence = Path(os.environ["SAWA_LOCAL_EVIDENCE_DIR"]) / "backend-tests"
    evidence.mkdir(exist_ok=False)
    excluded = sorted({name for modules in DEDICATED_MODULES.values() for name in modules})
    if any(not (ROOT / name).is_file() for name in excluded):
        raise ValueError("Dedicated PostgreSQL test module is missing")
    argv = [sys.executable, "-m", "pytest", "backend/tests",
            *["--ignore=" + name for name in excluded], *sys.argv[1:]]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("DB_")
           and key not in ("DATABASE_URL", "ALEMBIC_DB_URI", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    mode = os.environ.get("SAWA_LOCAL_TEST_MODE", "full")
    state = Path(git("rev-parse", "--absolute-git-dir")) / "sawa-local-impact" / "backend"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {key: value for key, value in env.items()
           if not key.startswith("SAWA_IMPACT_") and key != "TESTMON_DATAFILE"}
    env.update(SAWA_IMPACT_FULL="1" if mode != "impact" else "0",
               SAWA_IMPACT_ROOT=str(ROOT), SAWA_IMPACT_STATE=str(state),
               TESTMON_DATAFILE=str(state / "testmon.sqlite"), SAWA_LOCAL_SOURCE_SHA=source)
    argv[3:3] = ["-p", "pytest_impact", "-p", "pytest-testmon", "--testmon"]
    runtime = state.parent / "backend-runtime.sqlite"
    env["DB_URI"] = "sqlite:///" + str(runtime)
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / path) for path in ("backend", "backend/tests/unit", "infra/ci"))
    (evidence / "partition.json").write_text(json.dumps({
        "source_sha": source, "dedicated_modules": DEDICATED_MODULES,
        "general_argv": argv, "database_uri": env["DB_URI"],
    }, indent=2))
    with (state / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (state / "selection.json").unlink(missing_ok=True)
        (state / 'selection-plan.json').unlink(missing_ok=True)
        for suffix in ("", "-wal", "-shm", "-journal"):
            owned = Path(str(runtime) + suffix)
            if owned.is_symlink():
                raise ValueError("Owned test database must not be a symlink")
            owned.unlink(missing_ok=True)
        runtime.touch(mode=0o600)
        with preserve_baseline(state) as checkpoint:
            try:
                result = run_pytest(argv, env)
            except BaseException:
                plan_path = state / 'selection-plan.json'
                if plan_path.exists():
                    plan = json.loads(plan_path.read_text())
                    if plan.get('source_sha') != source:
                        raise ValueError('Interrupted test plan has the wrong source')
                    retain_failures(state, checkpoint.directory, plan.get('selected'))
                raise
            selection = json.loads((state / "selection.json").read_text())
            selected, reused = selection["selected"], selection["reused"]
            if (not isinstance(selected, list) or not isinstance(reused, list)
                    or not all(isinstance(node, str) for node in selected + reused)
                    or len(set(selected + reused)) != len(selected + reused)
                    or not selected + reused
                    or selection["exitstatus"] != result.returncode
                    or selection["source_sha"] != source
                    or (reused and not selection["baseline"])
                    or (reused and (not isinstance(selection["baseline"].get("source_sha"), str)
                        or len(selection["baseline"]["source_sha"]) != 40))
                    or (mode != "impact" and reused)):
                raise ValueError("Invalid backend dependency selection evidence")
            (evidence / "selection.json").write_text(json.dumps(selection, indent=2))
            retain_failures(state, checkpoint.directory, selection.get('failed', []),
                            successful=result.returncode == 0)
            if not clean_tree() or git('rev-parse', 'HEAD') != source:
                raise ValueError('Backend tests changed their fixed source')
            checkpoint.accepted = result.returncode == 0
            from local_hospital_test_runner import digest
            print(json.dumps({"backend_selection": {"source_sha": source,
                "mode": mode, "executed": len(selected), "reused": len(reused),
                "report_sha256": digest(evidence / "selection.json")}}), flush=True)
    if not clean_tree() or git("rev-parse", "HEAD") != source:
        raise ValueError("Backend tests changed their fixed source")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
