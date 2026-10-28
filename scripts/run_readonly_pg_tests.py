#!/usr/bin/env python3
"""Run an explicit unchanged PG test suite, retaining failures and owned cleanup."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
from urllib.parse import urlencode

PG_BIN = Path(os.environ.get('PG_BIN', '/usr/lib/postgresql/16/bin'))
MODULE = 'backend/tests/integration/test_menu_readonly_boundary.py'
MODULES = {
    'readonly': [MODULE, 'backend/tests/integration/test_native_startup_reflection.py'],
    'c1': ['backend/tests/integration/test_menu_master_revision.py',
           'backend/tests/integration/test_menu_editor_revision_contract.py'],
    'c1-guards': ['backend/tests/integration/test_stg_menu_master_live.py',
                  'backend/tests/integration/test_stg_menu_master_migration.py',
                  'backend/tests/contract/test_automation_bootstrap_postgres.py'],
    'menu-identity': ['backend/tests/integration/test_monthly_menu_identity_migration.py'],
    'output': ['backend/tests/integration/pg_output_boundary_cases.py',
               'backend/tests/unit/test_ocr_history_postgresql.py'],
    'formal': ['backend/tests/integration/test_formal_alembic_fresh_chain.py'],
}
DATABASES = {
    'readonly': {'C2_MENU_READONLY_PG_URI': 'postgres'},
    'c1': {'C1_POSTGRES_URI': 'c1_menu_master'},
    'c1-guards': {
        'C1_LIVE_PG_URI': 'c2_hospital_c1_live_pg16',
        'C1_STG_PG_URI': 'c2_hospital_c1_migration_pg16',
        'TEST_BOOTSTRAP_DB_URI': 'c2_hospital_c1_bootstrap_pg16',
    },
    'menu-identity': {'C2_MENU_PG_URI': 'postgres'},
    'output': {'MODERNIZATION_PG_TEST_URI': 'sawa_modernization_output_test'},
    'formal': {'C2_FORMAL_PG_URI': 'postgres'},
}


def owned_root(run_id, attempt, suite='readonly'):
    if suite not in (*MODULES, 'batch'):
        raise ValueError('unknown suite')
    if any(not re.fullmatch(r'[1-9][0-9]{0,19}', value) for value in (run_id, attempt)):
        raise ValueError('explicit numeric local run identity required')
    prefix = {'readonly': 'sawa-ro', 'c1': 'sawa-c1',
              'c1-guards': 'sawa-c1-guards', 'menu-identity': 'sawa-menu-identity',
              'output': 'sawa-output', 'formal': 'sawa-formal', 'batch': 'sawa-pg-batch'}[suite]
    return Path('/tmp').resolve() / f'{prefix}-{run_id}-{attempt}'


def pytest_command(root, suite='readonly'):
    if suite not in MODULES:
        raise ValueError('unknown suite')
    return [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-p', 'local_pg_input_guard', *MODULES[suite],
            '--basetemp', str(root / 'tests'), '--junitxml', str(root / 'evidence' / ('results-' + suite + '.xml')), '-v']


def require_postmaster(data, socket, port):
    pid = (data / 'postmaster.pid').read_text().splitlines()
    if (len(pid) < 5 or not pid[0].isdigit() or int(pid[0]) <= 0
            or pid[1] != str(data) or pid[3] != port or pid[4] != str(socket)):
        raise ValueError('C1 owned postmaster identity mismatch')
    return int(pid[0])


def run(source, run_id, attempt, suite='readonly', *, suites=None):
    source = source.resolve(strict=True)
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI'):
        raise ValueError('readonly PG suites run only from the local hospital test entry')
    selected = list(suites) if suites is not None else [suite]
    if (not selected or len(set(selected)) != len(selected) or any(item not in MODULES for item in selected)
            or (suite != 'batch' and selected != [suite])):
        raise ValueError('explicit distinct PostgreSQL suites required')
    root = owned_root(run_id, attempt, suite)
    root.mkdir(mode=0o700, exist_ok=False)
    evidence = root / 'evidence'
    evidence.mkdir()
    home = root / 'home'
    home.mkdir(mode=0o700)
    pg = root / 'pg'
    pg.mkdir(mode=0o700)
    port = '55471'
    databases = {key: value for selected_suite in selected for key, value in DATABASES[selected_suite].items()}
    data, socket = pg / 'pgdata', pg / 'socket'
    socket.mkdir(mode=0o700)
    for owner in (root / 'OWNER', pg / 'OWNER'):
        owner.write_text(str(source))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PG', 'C1_', 'C2_', 'SAWA_OWNED_PG_'))
           and key not in ('TEST_BOOTSTRAP_DB_URI', 'MODERNIZATION_PG_TEST_URI', 'OCR_HISTORY_PROJECTION_POSTGRES_URL', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS')}
    uri = 'sqlite:///' + str(root / 'bootstrap.sqlite3')
    env.update({key: uri for key in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI')})
    env.update({
        'C2_MENU_READONLY_OWNED_ROOT': str(root),
        'SAWA_OWNED_PG_ROOT': str(pg), 'SAWA_OWNED_PG_PORT': port,
        'SAWA_OWNED_PG_SOURCE': str(source),
        'SAWA_LOCAL_PG_EVIDENCE_DIR': str(evidence),
        'PYTHONPATH': os.pathsep.join([str(source / 'scripts'), str(source / 'backend')]), 'PYTHONDONTWRITEBYTECODE': '1',
        'SAWA_PG_COMMAND_OWNED_ROOT': str(root), 'GIT_NO_REPLACE_OBJECTS': '1',
        'AUTH_DISABLED': 'true', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_DATASETS_OFFLINE': '1', 'ORDERS_OUTPUT_DIR': str(root / 'output'),
        'XDG_CACHE_HOME': str(root / 'cache'), 'HF_HOME': str(root / 'models'), 'TMPDIR': str(root),
        'HOME': str(home),
    })
    env.update({key: f'postgresql+psycopg2://mmorinag@/{database}?' + urlencode(
        {'host': str(socket), 'port': port}) for key, database in databases.items()})
    lifecycle = []
    initialized = False
    owned_pid = None
    result = 1

    def command(args, name, check=True):
        record = {'command': [str(arg) for arg in args]}
        lifecycle.append(record)
        with (evidence / name).open('a') as output:
            process = subprocess.run(record['command'], cwd=source, env=env,
                                     stdout=output, stderr=subprocess.STDOUT)
        record['exit'] = process.returncode
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, record['command'])
        return process.returncode

    try:
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True)
        (evidence / 'git-sha.txt').write_text(sha)
        (evidence / 'run.json').write_text(json.dumps({
            'source_sha': sha.strip(), 'source': str(source), 'suite': suite,
            'modules': [module for selected_suite in selected for module in MODULES[selected_suite]],
            'argv': [pytest_command(root, selected_suite) for selected_suite in selected], 'suites': selected,
            'root': str(root), 'pg_root': str(pg), 'data': str(data), 'socket': str(socket),
            'port': port, 'pg_bin': str(PG_BIN), 'uris': {key: env[key] for key in databases},
        }, indent=2) + '\n')
        for name in ('initdb', 'pg_ctl', 'createdb', 'psql'):
            tool = PG_BIN / name
            if not tool.is_file() or not os.access(tool, os.X_OK):
                raise ValueError(f'missing PG16 executable: {tool}')
            command([tool, '--version'], 'tools.log')
        command([PG_BIN / 'initdb', '-D', data, '-U', 'mmorinag', '-A', 'trust', '--locale=C', '--encoding=UTF8'], 'initdb.log')
        initialized = True
        if (data / 'PG_VERSION').read_text().strip() != '16':
            raise ValueError('PG16 required')
        command([PG_BIN / 'pg_ctl', '-D', data, '-l', evidence / 'postgres.log', '-o',
                 f"-p {port} -k {socket} -c listen_addresses='' -c unix_socket_permissions=0700", '-w', 'start'], 'start.log')
        owned_pid = require_postmaster(data, socket, port)
        env['SAWA_OWNED_PG_PID'] = str(owned_pid)
        if (pg.resolve() != pg or data.resolve() != data or socket.resolve() != socket
                or any(owner.is_symlink() or owner.read_text() != str(source)
                       for owner in (root / 'OWNER', pg / 'OWNER'))):
            raise ValueError('owned PG OWNER/source mismatch')
        lifecycle.append({'owned_postmaster_pid': owned_pid})
        if databases:
            connection = ['-h', socket, '-p', port, '-U', 'mmorinag']
            for database in sorted(set(databases.values())):
                if database != 'postgres':
                    command([PG_BIN / 'createdb', *connection, database], 'createdb.log')
            for database in sorted(set(databases.values())):
                server_log = f'server-{database}.json'
                command([PG_BIN / 'psql', '-X', '-w', *connection, '-d', database,
                         '-v', 'ON_ERROR_STOP=1', '-At', '-c',
                         "SELECT json_build_object('data_directory', current_setting('data_directory'), "
                         "'port', current_setting('port'), 'socket', current_setting('unix_socket_directories'), "
                         "'listen_addresses', current_setting('listen_addresses'), "
                         "'major', current_setting('server_version_num')::int / 10000, "
                         "'role', current_user, 'database', current_database())"], server_log)
                actual = json.loads((evidence / server_log).read_text())
                if actual != {'data_directory': str(data), 'port': port, 'socket': str(socket),
                              'listen_addresses': '', 'major': 16, 'role': 'mmorinag', 'database': database}:
                    raise ValueError('C1 physical server identity mismatch')
        result = 0
        suite_uris = {key: env[key] for key in databases}
        input_reports = {}
        for selected_suite in selected:
            env['SAWA_PG_COMMAND_INPUT_REPORT'] = str(evidence / ('inputs-' + selected_suite + '.json'))
            for key in databases:
                env.pop(key, None)
            env.update({key: suite_uris[key] for key in DATABASES[selected_suite]})
            env.update({key: uri for key in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI')})
            env.pop('OCR_HISTORY_PROJECTION_POSTGRES_URL', None)
            if selected_suite == 'output':
                env.update({key: env['MODERNIZATION_PG_TEST_URI'] for key in (
                    'DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI', 'OCR_HISTORY_PROJECTION_POSTGRES_URL')})
                migration_exit = command([sys.executable, 'backend/tests/integration/pg_output_migration_probe.py',
                    '--evidence=' + str(evidence / 'migration-probe.json')], 'migration.log', check=False)
                if migration_exit:
                    result = 1
                    lifecycle.append({'suite': selected_suite, 'not_started': 'migration_failed', 'migration_exit': migration_exit})
                    continue
            # Each fixture still verifies the physical server and its isolated DB/schema.
            code = command(pytest_command(root, selected_suite), 'pytest-' + selected_suite + '.log', check=False)
            lifecycle.append({'suite': selected_suite, 'pytest_exit': code})
            result = int(bool(code or result))
            input_reports[selected_suite] = json.loads(Path(env['SAWA_PG_COMMAND_INPUT_REPORT']).read_text())
        destination = os.environ.get('SAWA_LOCAL_EVIDENCE_DIR')
        if suite == 'batch' and destination:
            (Path(destination) / 'pg-command-inputs.json').write_text(json.dumps({
                'schema': 1, 'source_sha': sha.strip(), 'suites': selected, 'reports': input_reports}, indent=2) + '\n')
        print(json.dumps({'source_sha': sha.strip(), 'suites': {name: {
            key: report[key] for key in ('collected', 'passed', 'skipped', 'failed', 'exitstatus')}
            for name, report in input_reports.items()}, 'evidence': str(evidence), 'exit_code': result}), flush=True)
    except BaseException:
        (evidence / 'runner-error.log').write_text(traceback.format_exc())
        raise
    finally:
        try:
            if initialized:
                status = command([PG_BIN / 'pg_ctl', '-D', data, 'status'], 'stop.log', check=False)
                if status == 0:
                    current_pid = require_postmaster(data, socket, port)
                    if owned_pid is not None and current_pid != owned_pid:
                        raise RuntimeError('owned PG PID changed before cleanup')
                    owned_pid = current_pid
                    command([PG_BIN / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'], 'stop.log')
                status = command([PG_BIN / 'pg_ctl', '-D', data, 'status'], 'stop.log', check=False)
                if status != 3 or (data / 'postmaster.pid').exists() or (socket / f'.s.PGSQL.{port}').exists():
                    raise RuntimeError('owned PG shutdown not verified')
                if owned_pid is not None:
                    try:
                        os.kill(owned_pid, 0)
                    except ProcessLookupError:
                        lifecycle.append({'postmaster_process_absent': True, 'pid': owned_pid})
                    else:
                        raise RuntimeError('owned PG process still exists after shutdown')
                lifecycle.append({'shutdown_verified': True, 'socket_alias_used': False})
            if (socket.is_symlink() or socket.resolve() != socket or any(socket.iterdir())
                    or (data / 'postmaster.pid').exists()):
                raise RuntimeError('owned C1 socket shutdown not verified')
            lifecycle.append({'socket_empty_after_run': True, 'postmaster_pid_absent': True,
                              'socket_alias_used': False, 'initialized': initialized})
        except BaseException:
            (evidence / 'cleanup-error.log').write_text(traceback.format_exc())
            raise
        finally:
            try:
                (evidence / 'lifecycle.json').write_text(json.dumps(lifecycle, indent=2))
            finally:
                original_error = sys.exception()
                try:
                    manifest = evidence / 'sha256.json'
                    hashes = {path.name: sha256(path.read_bytes()).hexdigest()
                              for path in sorted(evidence.iterdir())
                              if path != manifest and path.is_file() and not path.is_symlink()}
                    manifest.write_text(json.dumps(hashes, indent=2) + '\n')
                except Exception:
                    if original_error is None and result == 0:
                        raise
                    # Evidence I/O must not replace an existing test/cleanup failure.
                    traceback.print_exc(file=sys.stderr)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--suite', choices=tuple(MODULES), default='readonly')
    args = parser.parse_args()
    sys.exit(run(args.source, args.run_id, args.attempt, args.suite))
