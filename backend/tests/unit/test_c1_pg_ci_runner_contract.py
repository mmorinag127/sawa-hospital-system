"""CI runner contracts with subprocess doubles only; never start PostgreSQL."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/run_readonly_pg_tests.py'
SPEC = importlib.util.spec_from_file_location('c1_pg_ci_runner', SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
SOURCE_SHA = '04ef4a6ce7897bb187bb4d91f77839cfa30edde7'


@pytest.mark.parametrize('suite,prefix,modules', [
    ('readonly', 'sawa-ro', ['backend/tests/integration/test_menu_readonly_boundary.py']),
    ('c1', 'sawa-c1', ['backend/tests/integration/test_menu_master_revision.py',
                      'backend/tests/integration/test_menu_editor_revision_contract.py']),
    ('c1-guards', 'sawa-c1-guards', ['backend/tests/integration/test_stg_menu_master_live.py',
                                   'backend/tests/integration/test_stg_menu_master_migration.py',
                                   'backend/tests/contract/test_automation_bootstrap_postgres.py']),
    ('menu-identity', 'sawa-menu-identity', ['backend/tests/integration/test_monthly_menu_identity_migration.py']),
])
def test_explicit_suite_selects_whole_modules_and_distinct_owned_root(suite, prefix, modules):
    root = runner.owned_root('123', '2', suite)
    assert root == Path('/tmp').resolve() / f'{prefix}-123-2'
    assert runner.pytest_command(root, suite) == [
        sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', *modules,
        '--basetemp', str(root / 'tests'), '--junitxml', str(root / 'evidence/results.xml'), '-v',
    ]
    assert runner.owned_root('123', '2') == runner.owned_root('123', '2', 'readonly')
    assert runner.pytest_command(root) == runner.pytest_command(root, 'readonly')
    assert runner.PG_BIN == Path('/usr/lib/postgresql/16/bin')


def test_fixed_profiles_match_unchanged_fixture_paths_and_databases():
    assert runner.FIXED_PG == {
        'c1-guards': (Path('/private/tmp/sawa-c2-hospital-catalog-20261006/pg16'), '55470', {
            'C1_LIVE_PG_URI': 'c2_hospital_c1_live_pg16',
            'C1_STG_PG_URI': 'c2_hospital_c1_migration_pg16',
            'TEST_BOOTSTRAP_DB_URI': 'c2_hospital_c1_bootstrap_pg16',
        }),
        'menu-identity': (Path('/private/tmp/sawa-c2-menu-migration-20261006'), '55463', {
            'C2_MENU_PG_URI': 'postgres',
        }),
    }


@pytest.mark.parametrize('suite', ['', 'C1', 'unknown'])
def test_unknown_suite_never_falls_back_or_creates_a_root(tmp_path, suite):
    with pytest.raises(ValueError, match='unknown suite'):
        runner.owned_root('123', '1', suite)
    with pytest.raises(ValueError, match='unknown suite'):
        runner.pytest_command(tmp_path, suite)
    with pytest.raises(ValueError, match='unknown suite'):
        runner.run(tmp_path, '123', '1', suite)
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_unknown_suite_before_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), '--source', str(tmp_path),
                                    '--run-id', '123', '--attempt', '1', '--suite', 'other'])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected subprocess'))
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **kw: pytest.fail('unexpected git'))
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(SCRIPT), run_name='__main__')
    assert caught.value.code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def transport(monkeypatch, tmp_path):
    source = tmp_path.resolve() / 'source'
    source.mkdir()
    root = tmp_path.resolve() / 'owned'
    state = SimpleNamespace(source=source, root=root, commands=[], fail=None, pytest_exit=0,
                            active=False, wrong_identity=False, stale_socket=False,
                            identity_override={}, identity_database=None)
    fixed_parent = tmp_path.resolve() / 'private/tmp'
    fixed_parent.mkdir(parents=True)
    monkeypatch.setattr(runner, 'FIXED_PG', {
        suite: (fixed_parent / ('catalog/pg16' if suite == 'c1-guards' else 'identity'), port, databases)
        for suite, (_, port, databases) in runner.FIXED_PG.items()
    })
    for key in tuple(os.environ):
        if key.startswith(('PG', 'C1_', 'C2_')) or key in ('TEST_BOOTSTRAP_DB_URI', 'PYTEST_ADDOPTS'):
            monkeypatch.delenv(key)
    monkeypatch.setattr(runner, 'owned_root', lambda run_id, attempt, suite='readonly': root)
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.setattr(subprocess, 'check_output',
                        lambda args, **kw: SOURCE_SHA + '\n' if args == ['git', 'rev-parse', 'HEAD']
                        and kw == {'cwd': source, 'text': True} else pytest.fail('unexpected source command'))
    real_is_file, real_access = Path.is_file, os.access
    monkeypatch.setattr(Path, 'is_file', lambda path: True if path.parent == runner.PG_BIN else real_is_file(path))
    monkeypatch.setattr(os, 'access', lambda path, mode: True if Path(path).parent == runner.PG_BIN
                        else real_access(path, mode))

    def process_probe(pid, signal):
        assert pid == 12345 and signal == 0
        if not state.active:
            raise ProcessLookupError(pid)

    monkeypatch.setattr(os, 'kill', process_probe)

    def fake_run(args, *, cwd, env, stdout, stderr):
        assert cwd == source and stderr == subprocess.STDOUT
        state.commands.append((args, dict(env)))
        data = root / 'pg/pgdata'
        c1 = 'C1_POSTGRES_URI' in env
        socket = source / 'tmp/pgs' if c1 else root / 'pg/socket'
        port = '55437' if c1 else '55471'
        for pg, profile_port, databases in runner.FIXED_PG.values():
            if set(databases) <= env.keys():
                data, socket, port = pg / 'pgdata', pg / 'socket', profile_port
                break
        tool = Path(args[0]).name
        code = 0
        if args[1:] == ['--version']:
            stdout.write('owned PG16 tool\n')
        elif tool == 'initdb':
            if state.fail == 'initdb':
                code = 1
            else:
                data.mkdir()
                (data / 'PG_VERSION').write_text('16\n')
        elif tool == 'pg_ctl':
            if args[-1] == 'start':
                state.active = True
                (data / 'postmaster.pid').write_text(f'12345\n{data}\n1\n{port}\n{socket}\n')
                (socket / f'.s.PGSQL.{port}').touch()
                code = 1 if state.fail == 'start' else 0
            elif args[-1] == 'status':
                code = 0 if state.active else 3
            elif args[-1] == 'stop':
                if state.fail == 'stop':
                    code = 1
                else:
                    state.active = False
                    (data / 'postmaster.pid').unlink()
                    if not state.stale_socket:
                        (socket / f'.s.PGSQL.{port}').unlink()
            else:
                pytest.fail(f'unexpected pg_ctl command: {args}')
        elif tool == 'createdb':
            code = 1 if state.fail == 'createdb' else 0
        elif tool == 'psql':
            code = 1 if state.fail == 'psql' else 0
            stdout.write(json.dumps({'data_directory': str(data), 'port': port,
                                    'socket': str(socket), 'listen_addresses': '',
                                    'major': 15 if state.wrong_identity else 16,
                                    'role': 'mmorinag', 'database': args[args.index('-d') + 1],
                                    **(state.identity_override if state.identity_database in (
                                        None, args[args.index('-d') + 1]) else {})}))
        elif args[:3] == [sys.executable, '-m', 'pytest']:
            code = state.pytest_exit
            (root / 'evidence/results.xml').write_text('<testsuites/>')
            stdout.write(f'controlled pytest exit {code}\n')
        else:
            pytest.fail(f'unexpected subprocess: {args}')
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return state


@pytest.mark.parametrize('platform,actions', [('darwin', ''), ('linux', 'true')])
@pytest.mark.parametrize('suite', ['c1', 'c1-guards', 'menu-identity'])
def test_c1_refuses_hosted_or_non_linux_execution_before_creating_owned_root(transport, monkeypatch, platform, actions, suite):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setenv('GITHUB_ACTIONS', actions)
    with pytest.raises(ValueError, match='local'):
        runner.run(transport.source, '123', '1', suite)
    assert not transport.root.exists() and not transport.commands


@pytest.mark.parametrize('suite', ['readonly', 'c1'])
@pytest.mark.parametrize('exit_code', [0, 1])
def test_exact_environment_commands_failure_exit_and_shutdown(transport, monkeypatch, suite, exit_code):
    state = transport
    state.pytest_exit = exit_code
    if suite == 'c1':
        for key in ('C1_POSTGRES_URI', 'C2_MENU_READONLY_PG_URI', 'PGHOST', 'PGOPTIONS', 'PYTEST_ADDOPTS'):
            monkeypatch.setenv(key, 'foreign-input')
    assert runner.run(state.source, '123', '1', suite) == exit_code
    root, source = state.root, state.source
    data = root / 'pg/pgdata'
    socket = source / 'tmp/pgs' if suite == 'c1' else root / 'pg/socket'
    port = '55437' if suite == 'c1' else '55471'
    assert socket.resolve() == socket and not socket.is_symlink()
    assert list(socket.iterdir()) == [] and not (data / 'postmaster.pid').exists()
    assert (root / 'OWNER').read_text() == (root / 'pg/OWNER').read_text() == str(source)
    pytest_calls = [(args, env) for args, env in state.commands if args[:3] == [sys.executable, '-m', 'pytest']]
    assert len(pytest_calls) == 1
    args, env = pytest_calls[0]
    assert args == runner.pytest_command(root, suite)
    assert {env[key] for key in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI')} == {
        'sqlite:///' + str(root / 'bootstrap.sqlite3')}
    assert env['PYTHONPATH'] == str(source / 'backend') and env['PYTHONDONTWRITEBYTECODE'] == '1'
    assert env['AUTH_DISABLED'] == 'true'
    assert all(env[key] == '1' for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE'))
    for key, suffix in [('ORDERS_OUTPUT_DIR', 'output'), ('XDG_CACHE_HOME', 'cache'),
                        ('HF_HOME', 'models'), ('HOME', 'home'), ('TMPDIR', '')]:
        assert env[key] == str(root / suffix)
    key = 'C1_POSTGRES_URI' if suite == 'c1' else 'C2_MENU_READONLY_PG_URI'
    uri = urlsplit(env[key])
    assert (uri.scheme, uri.netloc, uri.path) == (
        'postgresql+psycopg2', 'mmorinag@', '/c1_menu_master' if suite == 'c1' else '/postgres')
    assert parse_qs(uri.query) == {'host': [str(socket)], 'port': [port]}
    commands = [args for args, _ in state.commands]
    assert [str(runner.PG_BIN / 'initdb'), '-D', str(data), '-U', 'mmorinag', '-A', 'trust',
            '--locale=C', '--encoding=UTF8'] in commands
    assert [str(runner.PG_BIN / 'pg_ctl'), '-D', str(data), '-l', str(root / 'evidence/postgres.log'),
            '-o', f"-p {port} -k {socket} -c listen_addresses='' -c unix_socket_permissions=0700",
            '-w', 'start'] in commands
    if suite == 'c1':
        assert not any(name.startswith(('C2_', 'PG')) or name == 'PYTEST_ADDOPTS' for name in env)
        assert [str(runner.PG_BIN / 'createdb'), '-h', str(socket), '-p', port,
                '-U', 'mmorinag', 'c1_menu_master'] in commands
        sql = next(args for args in commands if Path(args[0]).name == 'psql' and args[1] == '-X')
        assert sql[:14] == [str(runner.PG_BIN / 'psql'), '-X', '-w', '-h', str(socket), '-p', port,
                           '-U', 'mmorinag', '-d', 'c1_menu_master', '-v', 'ON_ERROR_STOP=1', '-At']
        assert sql[14] == '-c' and 'current_database()' in sql[15]
        metadata = json.loads((root / 'evidence/run.json').read_text())
        assert metadata['source_sha'] == SOURCE_SHA and metadata['uri'] == env[key]
        assert metadata['modules'] == ['backend/tests/integration/test_menu_master_revision.py',
                                       'backend/tests/integration/test_menu_editor_revision_contract.py']
        assert metadata['argv'] == args
    else:
        assert env['C2_MENU_READONLY_OWNED_ROOT'] == str(root)
        assert not any(Path(args[0]).name in {'createdb', 'psql'} for args in commands)
    lifecycle = json.loads((root / 'evidence/lifecycle.json').read_text())
    assert {'pytest_exit': exit_code} in lifecycle
    assert {'shutdown_verified': True, 'socket_alias_used': False} in lifecycle
    assert [str(runner.PG_BIN / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'] in commands
    assert (root / 'evidence/git-sha.txt').read_text() == SOURCE_SHA + '\n'
    manifest = json.loads((root / 'evidence/sha256.json').read_text())
    assert {'git-sha.txt', 'pytest.log', 'results.xml', 'lifecycle.json', 'stop.log'} <= manifest.keys()
    for name, digest in manifest.items():
        assert digest == sha256((root / 'evidence' / name).read_bytes()).hexdigest()


@pytest.mark.parametrize('suite,failure', [
    (suite, failure) for suite in ('c1', 'c1-guards', 'menu-identity')
    for failure in ('initdb', 'start', 'createdb', 'psql', 'identity')
    if (suite, failure) != ('menu-identity', 'createdb')
])
def test_c1_startup_or_identity_failure_is_saved_and_never_runs_pytest(transport, failure, suite):
    transport.fail = failure
    transport.wrong_identity = failure == 'identity'
    error = ValueError if failure == 'identity' else subprocess.CalledProcessError
    with pytest.raises(error):
        runner.run(transport.source, '123', '1', suite)
    assert not any(args[:3] == [sys.executable, '-m', 'pytest'] for args, _ in transport.commands)
    evidence = transport.root / 'evidence'
    assert (evidence / 'runner-error.log').read_text()
    assert 'runner-error.log' in json.loads((evidence / 'sha256.json').read_text())
    lifecycle = json.loads((evidence / 'lifecycle.json').read_text())
    assert {'socket_empty_after_run': True, 'postmaster_pid_absent': True, 'socket_alias_used': False,
            'initialized': failure != 'initdb'} in lifecycle


@pytest.mark.parametrize('kind', ['root', 'socket', 'socket-link', 'parent-link'])
def test_c1_refuses_existing_or_aliased_ownership_without_using_tools(transport, kind):
    socket = transport.source / 'tmp/pgs'
    if kind == 'root':
        transport.root.mkdir()
    elif kind == 'parent-link':
        target = transport.source / 'foreign'
        target.mkdir()
        socket.parent.symlink_to(target, target_is_directory=True)
    else:
        socket.parent.mkdir()
        if kind == 'socket-link':
            target = transport.source / 'foreign'
            target.mkdir()
            socket.symlink_to(target, target_is_directory=True)
        else:
            socket.mkdir()
    with pytest.raises((FileExistsError, ValueError)):
        runner.run(transport.source, '123', '1', 'c1')
    assert not transport.commands
    assert (transport.root if kind == 'root' else socket.parent if kind == 'parent-link' else socket).exists()


@pytest.mark.parametrize('suite', ['c1', 'c1-guards', 'menu-identity'])
@pytest.mark.parametrize('failure', ['stop', 'stale-socket'])
def test_cleanup_failure_cannot_turn_successful_pytest_green(transport, failure, suite):
    transport.fail = failure
    transport.stale_socket = failure == 'stale-socket'
    with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
        runner.run(transport.source, '123', '1', suite)
    evidence = transport.root / 'evidence'
    assert {'pytest_exit': 0} in json.loads((evidence / 'lifecycle.json').read_text())
    assert (evidence / 'cleanup-error.log').read_text()
    assert 'cleanup-error.log' in json.loads((evidence / 'sha256.json').read_text())


@pytest.mark.parametrize('suite', ['c1-guards', 'menu-identity'])
@pytest.mark.parametrize('exit_code', [0, 1])
def test_fixed_profiles_check_every_database_before_whole_modules_and_preserve_failure(transport, monkeypatch, suite, exit_code):
    state = transport
    state.pytest_exit = exit_code
    for key in ('PGHOST', 'PGSERVICE', 'PGOPTIONS', 'C1_POSTGRES_URI', 'C1_LIVE_PG_URI',
                'C1_STG_PG_URI', 'C2_MENU_PG_URI', 'C2_MENU_READONLY_PG_URI',
                'TEST_BOOTSTRAP_DB_URI', 'PYTEST_ADDOPTS'):
        monkeypatch.setenv(key, 'foreign-input')
    assert runner.run(state.source, '123', '1', suite) == exit_code
    pg, port, databases = runner.FIXED_PG[suite]
    data, socket, evidence = pg / 'pgdata', pg / 'socket', state.root / 'evidence'
    assert (pg / 'OWNER').read_text() == (state.root / 'OWNER').read_text() == str(state.source)
    calls = [(args, env) for args, env in state.commands if args[:3] == [sys.executable, '-m', 'pytest']]
    assert len(calls) == 1
    args, env = calls[0]
    assert args == runner.pytest_command(state.root, suite)
    assert {key for key in env if key.startswith(('PG', 'C1_', 'C2_'))
            or key in ('TEST_BOOTSTRAP_DB_URI', 'PYTEST_ADDOPTS')} == set(databases)
    assert {env[key] for key in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI')} == {
        'sqlite:///' + str(state.root / 'bootstrap.sqlite3')}
    for key, database in databases.items():
        uri = urlsplit(env[key])
        assert (uri.scheme, uri.netloc, uri.path) == ('postgresql+psycopg2', 'mmorinag@', '/' + database)
        assert parse_qs(uri.query) == {'host': [str(socket)], 'port': [port]}
        assert json.loads((evidence / f'server-{database}.json').read_text()) == {
            'data_directory': str(data), 'port': port, 'socket': str(socket),
            'listen_addresses': '', 'major': 16, 'role': 'mmorinag', 'database': database}
    commands = [command for command, _ in state.commands]
    create = [command for command in commands if Path(command[0]).name == 'createdb' and command[1] != '--version']
    assert create == [[str(runner.PG_BIN / 'createdb'), '-h', str(socket), '-p', port, '-U', 'mmorinag', database]
                      for database in databases.values() if database != 'postgres']
    sql = [command for command in commands if Path(command[0]).name == 'psql' and command[1] == '-X']
    assert len(sql) == len(databases)
    for command, database in zip(sql, databases.values()):
        assert command[:15] == [str(runner.PG_BIN / 'psql'), '-X', '-w', '-h', str(socket), '-p', port,
                               '-U', 'mmorinag', '-d', database, '-v', 'ON_ERROR_STOP=1', '-At', '-c']
        assert 'current_database()' in command[15] and 'current_user' in command[15]
        assert commands.index(command) < commands.index(args)
        assert all(commands.index(created) < commands.index(command) for created in create)
    metadata = json.loads((evidence / 'run.json').read_text())
    assert metadata['source_sha'] == SOURCE_SHA and metadata['source'] == str(state.source)
    assert metadata['pg_root'] == str(pg) and metadata['argv'] == args
    assert metadata['modules'] == args[5:-5] and metadata['uris'] == {key: env[key] for key in databases}
    lifecycle = json.loads((evidence / 'lifecycle.json').read_text())
    assert {'pytest_exit': exit_code} in lifecycle
    assert {'owned_postmaster_pid': 12345} in lifecycle
    assert {'postmaster_process_absent': True, 'pid': 12345} in lifecycle
    assert {'shutdown_verified': True, 'socket_alias_used': False} in lifecycle
    assert not (data / 'postmaster.pid').exists() and list(socket.iterdir()) == []
    manifest = json.loads((evidence / 'sha256.json').read_text())
    assert {'run.json', 'git-sha.txt', 'results.xml', 'pytest.log', 'stop.log', 'lifecycle.json'} <= manifest.keys()
    assert {f'server-{database}.json' for database in databases.values()} <= manifest.keys()
    for name, digest in manifest.items():
        assert digest == sha256((evidence / name).read_bytes()).hexdigest()


@pytest.mark.parametrize('suite', ['c1-guards', 'menu-identity'])
@pytest.mark.parametrize('kind', ['root', 'profile', 'profile-link', 'parent-link'])
def test_fixed_profiles_refuse_existing_or_aliased_paths_before_tools(transport, suite, kind):
    pg, _, _ = runner.FIXED_PG[suite]
    if kind == 'root':
        transport.root.mkdir()
    elif kind == 'parent-link':
        parent = pg.parent.parent if suite == 'c1-guards' else pg.parent
        parent.rmdir()
        parent.symlink_to(transport.source, target_is_directory=True)
    else:
        pg.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'profile-link':
            pg.symlink_to(transport.source, target_is_directory=True)
        else:
            pg.mkdir()
    with pytest.raises((FileExistsError, ValueError)):
        runner.run(transport.source, '123', '1', suite)
    assert not transport.commands


@pytest.mark.parametrize('field,value', [('data_directory', '/foreign'), ('socket', '/foreign'),
                                       ('port', '5432'), ('major', 15), ('role', 'postgres'),
                                       ('database', 'foreign'), ('listen_addresses', '*')])
def test_fixed_profile_sql_identity_mismatch_stops_before_pytest(transport, field, value):
    transport.identity_override = {field: value}
    transport.identity_database = 'c2_hospital_c1_bootstrap_pg16'
    with pytest.raises(ValueError, match='physical server identity mismatch'):
        runner.run(transport.source, '123', '1', 'c1-guards')
    assert not any(args[:3] == [sys.executable, '-m', 'pytest'] for args, _ in transport.commands)
    assert not transport.active


@pytest.mark.parametrize('owner', ['root', 'pg'])
def test_fixed_profile_requires_actual_owner_source_after_start(transport, monkeypatch, owner):
    original = subprocess.run
    pg, _, _ = runner.FIXED_PG['c1-guards']

    def corrupt_owner(args, **kwargs):
        result = original(args, **kwargs)
        if Path(args[0]).name == 'pg_ctl' and args[-1] == 'start':
            ((transport.root if owner == 'root' else pg) / 'OWNER').write_text('/foreign')
        return result

    monkeypatch.setattr(subprocess, 'run', corrupt_owner)
    with pytest.raises(ValueError, match='OWNER/source mismatch'):
        runner.run(transport.source, '123', '1', 'c1-guards')
    assert not any(args[:3] == [sys.executable, '-m', 'pytest'] for args, _ in transport.commands)
    assert not transport.active


@pytest.mark.parametrize('field', [0, 1, 3, 4])
def test_postmaster_identity_rejects_wrong_pid_data_port_or_socket(tmp_path, field):
    values = ['12345', str(tmp_path), '1', '55470', str(tmp_path / 'socket')]
    values[field] = '0' if field == 0 else '/foreign'
    (tmp_path / 'postmaster.pid').write_text('\n'.join(values))
    with pytest.raises(ValueError, match='postmaster identity mismatch'):
        runner.require_postmaster(tmp_path, tmp_path / 'socket', '55470')


def test_fixed_profile_live_process_after_stop_is_cleanup_failure(transport, monkeypatch):
    monkeypatch.setattr(os, 'kill', lambda pid, signal: None)
    with pytest.raises(RuntimeError, match='process still exists'):
        runner.run(transport.source, '123', '1', 'menu-identity')
    evidence = transport.root / 'evidence'
    assert 'cleanup-error.log' in json.loads((evidence / 'sha256.json').read_text())


def test_independent_matrix_keeps_original_owner_gate_and_failure_artifacts():
    workflow = yaml.safe_load((ROOT / 'local-checks/legacy/modernization-readonly-pg.yml').read_text())
    assert workflow.get('on', workflow.get(True)) == {
        'push': {'branches': ['codex/modernization-readonly-pg-ci-20261012']}, 'workflow_dispatch': None}
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'readonly-boundary'}
    job = workflow['jobs']['readonly-boundary']
    assert job['strategy'] == {'fail-fast': False, 'matrix': {'include': [
        {'suite': 'readonly', 'root-prefix': 'sawa-ro', 'artifact-prefix': 'hospital-readonly'},
        {'suite': 'c1', 'root-prefix': 'sawa-c1', 'artifact-prefix': 'hospital-c1'},
        {'suite': 'c1-guards', 'root-prefix': 'sawa-c1-guards', 'artifact-prefix': 'hospital-c1-guards'},
        {'suite': 'menu-identity', 'root-prefix': 'sawa-menu-identity', 'artifact-prefix': 'hospital-menu-identity'},
    ]}}
    assert job['runs-on'] == 'ubuntu-24.04' and job['timeout-minutes'] == 45
    assert not {'needs', 'environment', 'services', 'continue-on-error'} & job.keys()
    steps = [s for s in job['steps'] if s.get('name') not in ('Update TLS trust stores', 'Update Python trust bundles')]
    assert not any('continue-on-error' in step for step in steps)
    assert steps[0]['with'] == {'persist-credentials': False}
    assert job['if'] == (
        "github.actor == github.repository_owner && "
        "(github.event_name == 'workflow_dispatch' ||\n "
        "(github.event_name == 'push' && github.ref == 'refs/heads/codex/modernization-readonly-pg-ci-20261012' && github.event.repository.full_name == github.repository))")
    private = steps[4]
    conditional = 'if [[ "${{ matrix.suite }}" == c1-guards || "${{ matrix.suite }}" == menu-identity ]]; then'
    assert conditional in private['run']
    assert private['run'].index(conditional) < private['run'].index('[[ -e /private || -L /private ]]')
    assert '[[ -e /private || -L /private ]]' in private['run']
    assert private['run'].index('[[ -e /private || -L /private ]]') < private['run'].index('sudo mkdir')
    assert [line.strip() for line in private['run'].splitlines() if 'sudo ' in line] == [
        'sudo mkdir -m 700 -- /private || exit $?',
        'sudo chown "$(id -u):$(id -g)" -- /private || exit $?']
    assert not any('sudo ' in step.get('run', '') for step in steps if step is not private)
    command = steps[4]['run']
    for literal in ('set -euo pipefail', '--suite "${{ matrix.suite }}"', '|| status=$?',
                    'runner-exit.txt', 'exit "$status"'):
        assert literal in command
    artifact = steps[-1]
    assert artifact['if'] == 'always()' and artifact['uses'] == 'actions/upload-artifact@v4'
    assert artifact['with']['name'] == '${{ matrix.artifact-prefix }}-${{ github.run_id }}-${{ github.run_attempt }}'
    assert artifact['with']['include-hidden-files'] is True
    paths = artifact['with']['path']
    for name in ('runner-exit.txt', 'runner.log', 'results.xml', 'git-sha.txt', '*.json', '*.log'):
        assert name in paths
    assert 'private-parent.log' in paths and 'private-parent-exit.txt' in paths
    assert '/tmp/${{ matrix.root-prefix }}-' in paths
    assert 'pgdata' not in paths and 'bootstrap' not in paths
