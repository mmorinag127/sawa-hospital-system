"""Run staging core or explicit wide suites; PostgreSQL retains dedicated runners."""
import json
import hashlib
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


def staging_scope():
    path = ROOT / 'infra/ci/staging_backend_scope.json'
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'version', 'scope', 'groups'}
            or type(value['version']) is not int or value['version'] != 1
            or value['scope'] != 'staging-core-and-page-repair'
            or not isinstance(value['groups'], dict) or not value['groups']):
        raise ValueError('Invalid staging backend scope')
    modules = []
    dedicated = {name for names in DEDICATED_MODULES.values() for name in names}
    for group, names in value['groups'].items():
        if not isinstance(group, str) or not group or not isinstance(names, list) or not names:
            raise ValueError('Invalid staging backend scope group')
        for name in names:
            if (not isinstance(name, str) or not name.startswith('backend/tests/')
                    or not name.endswith('.py') or '..' in Path(name).parts
                    or name in modules or name in dedicated or not (ROOT / name).is_file()):
                raise ValueError('Invalid staging backend scope module')
            modules.append(name)
    return value, modules


def retained_failure_modules(state):
    path = state / 'failed-tests.json'
    if path.exists():
        values = json.loads(path.read_text())
    elif (state / 'selection.json').exists():
        report = json.loads((state / 'selection.json').read_text())
        failed, incomplete = report.get('failed'), report.get('incomplete')
        if not isinstance(failed, list) or not isinstance(incomplete, list):
            raise ValueError('Invalid retained backend execution report')
        values = failed + incomplete
    else:
        return []
    if not isinstance(values, list) or any(not isinstance(node, str) for node in values):
        raise ValueError('Invalid retained backend failure identities')
    modules = set()
    for node in values:
        module = node.split('::', 1)[0]
        if module.startswith('tests/'):
            module = 'backend/' + module
        if (not module.startswith('backend/tests/') or not module.endswith('.py')
                or '..' in Path(module).parts or not (ROOT / module).is_file()):
            raise ValueError('Retained backend failure needs an explicit current test mapping')
        modules.add(module)
    return sorted(modules)


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
        checkpoint = SimpleNamespace(accepted=False, invalidate=False, directory=root)
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
                if checkpoint.invalidate:
                    (state / 'inputs.json').unlink(missing_ok=True)


def valid_partial_history(state, selection, source):
    """Accept only a completed assertion-failure report bound to its DB and metadata."""
    metadata_path, database_path = state / 'inputs.json', state / 'testmon.sqlite'
    try:
        if (metadata_path.is_symlink() or database_path.is_symlink()
                or not metadata_path.is_file() or not database_path.is_file()
                or selection.get('normal_completion') is not True
                or type(selection.get('assertion_failures')) is not int
                or selection['assertion_failures'] < 1 or selection.get('exitstatus') != 1
                or selection.get('source_sha') != source
                or selection.get('incomplete') != [] or selection.get('not_started') != []):
            return False
        selected, executed, completed = (selection.get(key) for key in ('selected', 'executed', 'completed'))
        passed, failed, skipped, reused = (selection.get(key) for key in ('passed', 'failed', 'skipped', 'reused'))
        groups = (selected, executed, completed, passed, failed, skipped, reused)
        if (any(not isinstance(group, list) or any(not isinstance(node, str) for node in group)
                or len(group) != len(set(group)) for group in groups)
                or set(selected) != set(executed) or set(selected) != set(completed)
                or not failed or not set(failed) <= set(completed)
                or not set(passed) <= set(completed) or not set(skipped) <= set(completed)
                or set(passed) | set(failed) | set(skipped) != set(completed)
                or set(passed) & (set(failed) | set(skipped))
                or set(selected) & set(reused)):
            return False
        raw = metadata_path.read_bytes()
        metadata = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != selection.get('inputs_sha256')
                or not isinstance(metadata, dict) or metadata.get('source_sha') != source
                or metadata.get('schema') != 2 or not isinstance(metadata.get('tests'), dict)
                or not isinstance(metadata.get('context'), str) or len(metadata['context']) != 64
                or metadata['context'] != selection.get('context_sha256')):
            return False
        entries = metadata['tests']
        if (any(not isinstance(entries.get(node), dict) or entries[node].get('passed') is not True
                for node in passed + reused)
                or any(not isinstance(entries.get(node), dict) or entries[node].get('passed') is not False
                       for node in failed)):
            return False
        with closing(sqlite3.connect(database_path.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                return False
            tracking_hash = hashlib.sha256('\n'.join(connection.iterdump()).encode()).hexdigest()
        return (tracking_hash == metadata.get('tracking_sha256')
                and tracking_hash == selection.get('tracking_sha256'))
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return False


def run_pytest(argv, env):
    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGINT, signal.SIGTERM)}
    try:
        for kind in previous:
            signal.signal(kind, signal.default_int_handler)
        with subprocess.Popen(argv, cwd=ROOT, env=env, start_new_session=True) as process:
            try:
                return subprocess.CompletedProcess(argv, process.wait())
            except BaseException:
                # Repeated outer interrupts must not interrupt child cleanup.
                for kind in previous:
                    signal.signal(kind, signal.SIG_IGN)
                if process.poll() is None:
                    try:
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
                    except ProcessLookupError:
                        process.wait()
                raise
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


def retain_failures(state, scratch, failed, *, successful=False, resolved=()):
    previous_path = state / 'failed-tests.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else []
    if any(not isinstance(values, list) or any(not isinstance(node, str) for node in values)
           for values in (failed, previous)):
        raise ValueError('Invalid failed-test identities')
    if not isinstance(resolved, (list, tuple)) or any(not isinstance(node, str) for node in resolved):
        raise ValueError('Invalid resolved-test identities')
    temporary = scratch / 'failed-tests.json'
    temporary.write_text(json.dumps(sorted(set(failed) | (set() if successful else set(previous) - set(resolved)))))
    os.replace(temporary, previous_path)


def interrupted_tests(state, source):
    plan_path = state / 'selection-plan.json'
    if not plan_path.exists():
        return []
    plan = json.loads(plan_path.read_text())
    selected = plan.get('selected')
    if (plan.get('source_sha') != source or not isinstance(selected, list)
            or any(not isinstance(node, str) for node in selected)):
        raise ValueError('Interrupted test plan has the wrong source or identities')
    planned = set(selected)
    report_path = state / 'selection.json'
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if report.get('source_sha') != source or report.get('selected') != selected:
            raise ValueError('Interrupted test report does not match its plan')
        fields = [report.get('failed'), report.get('incomplete')]
        if any(not isinstance(nodes, list) or any(
                not isinstance(node, str) or node not in planned for node in nodes)
                for nodes in fields):
            raise ValueError('Invalid interrupted test report identities')
        return sorted(set(fields[0]) | set(fields[1]))
    journal = state / 'started-tests.jsonl'
    if not journal.exists():
        raise ValueError('Interrupted test plan has no execution journal')
    started = set()
    with journal.open(encoding='utf-8') as stream:
        if json.loads(stream.readline()) != {'source_sha': source}:
            raise ValueError('Interrupted execution journal has the wrong source')
        for line in stream:
            event = json.loads(line)
            if (set(event) != {'nodeid'} or not isinstance(event['nodeid'], str)
                    or event['nodeid'] not in planned):
                raise ValueError('Invalid interrupted execution identity')
            started.add(event['nodeid'])
    return sorted(started)


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
    branch = git("branch", "--show-current")
    mode = os.environ.get("SAWA_LOCAL_TEST_MODE")
    if mode is None:
        mode = "full" if not branch or branch.startswith("release/prod-") else "focused"
    if mode not in {"full", "impact", "focused"}:
        raise ValueError("Local backend test mode must be full, impact or focused")
    if not branch or branch.startswith("release/prod-"):
        mode = "full"
    scope = None
    if mode == 'focused':
        scope, modules = staging_scope()
        argv[3:4] = modules
    common = Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = ROOT / common
    retained = []
    if mode == 'focused':
        retained = [name for name in retained_failure_modules(common.resolve() / 'sawa-local-impact/backend')
                    if name not in modules]
        if any(name in excluded for name in retained):
            raise ValueError('Retained dedicated PostgreSQL failure must use its owned runner')
        argv[3:3] = retained
    # A new worktree must not discard the same repository's verified inputs.
    # The existing exclusive lock and per-case dependency checks still apply.
    state = common.resolve() / "sawa-local-impact" / ('backend-focused' if mode == 'focused' else 'backend')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {key: value for key, value in env.items()
           if not key.startswith("SAWA_IMPACT_") and key != "TESTMON_DATAFILE"}
    env.update(SAWA_IMPACT_FULL="1" if mode == "full" else "0",
               SAWA_IMPACT_ROOT=str(ROOT), SAWA_IMPACT_STATE=str(state),
               TESTMON_DATAFILE=str(state / "testmon.sqlite"), SAWA_LOCAL_SOURCE_SHA=source)
    argv[3:3] = ["-p", "pytest_impact", "-p", "pytest-testmon", "--testmon"]
    runtime = state.parent / (state.name + '-runtime.sqlite')
    env["DB_URI"] = "sqlite:///" + str(runtime)
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / path) for path in ("backend", "backend/tests/unit", "infra/ci"))
    (evidence / "partition.json").write_text(json.dumps({
        "source_sha": source, "dedicated_modules": DEDICATED_MODULES,
        "general_argv": argv, "database_uri": env["DB_URI"],
        "dependency_state": str(state),
        "scope": scope,
        "retained_failure_modules": retained,
        "outside_scope": "Other general backend modules were not selected or claimed as passed/reused" if scope else None,
    }, indent=2))
    with (state / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (state / "selection.json").unlink(missing_ok=True)
        (state / 'selection-plan.json').unlink(missing_ok=True)
        journal = state / 'started-tests.jsonl'
        if journal.is_symlink():
            raise ValueError('Execution journal must not be a symlink')
        journal.unlink(missing_ok=True)
        for suffix in ("", "-wal", "-shm", "-journal"):
            owned = Path(str(runtime) + suffix)
            if owned.is_symlink():
                raise ValueError("Owned test database must not be a symlink")
            owned.unlink(missing_ok=True)
        runtime.touch(mode=0o600)
        with preserve_baseline(state) as checkpoint:
            checkpoint.invalidate = True
            try:
                result = run_pytest(argv, env)
            except BaseException:
                retain_failures(state, checkpoint.directory, interrupted_tests(state, source))
                raise
            selection = json.loads((state / "selection.json").read_text())
            selected, reused = selection["selected"], selection["reused"]
            executed, completed = selection["executed"], selection["completed"]
            if (not isinstance(selected, list) or not isinstance(reused, list)
                    or not isinstance(executed, list) or not isinstance(completed, list)
                    or not all(isinstance(node, str) for node in selected + reused)
                    or not all(isinstance(node, str) for node in executed + completed)
                    or len(set(selected + reused)) != len(selected + reused)
                    or len(set(executed)) != len(executed)
                    or len(set(completed)) != len(completed)
                    or not set(completed) <= set(executed) <= set(selected)
                    or (result.returncode == 0 and set(completed) != set(selected))
                    or not selected + reused
                    or selection["exitstatus"] != result.returncode
                    or selection["source_sha"] != source
                    or (reused and not selection["baseline"])
                    or (reused and (not isinstance(selection["baseline"].get("source_sha"), str)
                        or len(selection["baseline"]["source_sha"]) != 40))
                    or (mode == "full" and reused)):
                raise ValueError("Invalid backend dependency selection evidence")
            (evidence / "selection.json").write_text(json.dumps(selection, indent=2))
            if not clean_tree() or git('rev-parse', 'HEAD') != source:
                raise ValueError('Backend tests changed their fixed source')
            partial = result.returncode == 1 and valid_partial_history(state, selection, source)
            retain_failures(state, checkpoint.directory, selection.get('failed', []),
                            successful=result.returncode == 0,
                            resolved=selection.get('passed', []) if partial else ())
            checkpoint.accepted = result.returncode == 0 or partial
            from local_hospital_test_runner import digest
            print(json.dumps({"backend_selection": {"source_sha": source,
                "mode": mode, "executed": len(executed), "reused": len(reused),
                "report_sha256": digest(evidence / "selection.json")}}), flush=True)
    if not clean_tree() or git("rev-parse", "HEAD") != source:
        raise ValueError("Backend tests changed their fixed source")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
