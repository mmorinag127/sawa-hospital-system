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

PG_BIN = Path('/usr/lib/postgresql/16/bin')
MODULE = 'backend/tests/integration/test_menu_readonly_boundary.py'
MODULES = {
    'readonly': [MODULE],
    'c1': ['backend/tests/integration/test_menu_master_revision.py',
           'backend/tests/integration/test_menu_editor_revision_contract.py'],
}


def owned_root(run_id, attempt, suite='readonly'):
    if suite not in MODULES:
        raise ValueError('unknown suite')
    if any(not re.fullmatch(r'[1-9][0-9]{0,19}', value) for value in (run_id, attempt)):
        raise ValueError('explicit numeric CI identity required')
    prefix = 'sawa-ro' if suite == 'readonly' else 'sawa-c1'
    return Path('/tmp').resolve() / f'{prefix}-{run_id}-{attempt}'


def pytest_command(root, suite='readonly'):
    if suite not in MODULES:
        raise ValueError('unknown suite')
    return [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', *MODULES[suite],
            '--basetemp', str(root / 'tests'), '--junitxml', str(root / 'evidence/results.xml'), '-v']


def run(source, run_id, attempt, suite='readonly'):
    source = source.resolve(strict=True)
    root = owned_root(run_id, attempt, suite)
    if suite == 'c1' and (sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true'):
        raise ValueError('C1 runner requires Linux GitHub Actions with PG16')
    root.mkdir(mode=0o700, exist_ok=False)
    evidence = root / 'evidence'
    evidence.mkdir()
    home = root / 'home'
    home.mkdir(mode=0o700)
    pg = root / 'pg'
    pg.mkdir(mode=0o700)
    data, socket = pg / 'pgdata', pg / 'socket'
    port = '55471'
    if suite == 'readonly':
        socket.mkdir(mode=0o700)
    else:
        socket, port = source / 'tmp/pgs', '55437'
    for owner in (root / 'OWNER', pg / 'OWNER'):
        owner.write_text(str(source))
    env = os.environ.copy()
    uri = 'sqlite:///' + str(root / 'bootstrap.sqlite3')
    env.update({key: uri for key in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI')})
    env.update({
        'C2_MENU_READONLY_OWNED_ROOT': str(root),
        'C2_MENU_READONLY_PG_URI': 'postgresql+psycopg2://mmorinag@/postgres?' + urlencode({'host': str(socket), 'port': '55471'}),
        'PYTHONPATH': str(source / 'backend'), 'PYTHONDONTWRITEBYTECODE': '1',
        'AUTH_DISABLED': 'true', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_DATASETS_OFFLINE': '1', 'ORDERS_OUTPUT_DIR': str(root / 'output'),
        'XDG_CACHE_HOME': str(root / 'cache'), 'HF_HOME': str(root / 'models'), 'TMPDIR': str(root),
        'HOME': str(home),
    })
    if suite == 'c1':
        for key in tuple(env):
            if key.startswith(('PG', 'C1_', 'C2_')) or key == 'PYTEST_ADDOPTS':
                env.pop(key)
        env['C1_POSTGRES_URI'] = 'postgresql+psycopg2://mmorinag@/c1_menu_master?' + urlencode(
            {'host': str(socket), 'port': port})
    lifecycle = []
    initialized = False
    socket_owned = False
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
        if suite == 'c1':
            (evidence / 'run.json').write_text(json.dumps({
                'source_sha': sha.strip(), 'source': str(source), 'suite': suite,
                'modules': MODULES[suite], 'argv': pytest_command(root, suite),
                'root': str(root), 'data': str(data), 'socket': str(socket),
                'port': port, 'pg_bin': str(PG_BIN), 'uri': env['C1_POSTGRES_URI'],
            }, indent=2) + '\n')
            if socket.parent.is_symlink() or socket.parent.resolve() != socket.parent:
                raise ValueError('C1 socket parent must be physical')
            socket.parent.mkdir(mode=0o700, exist_ok=True)
            # The unchanged fixture requires this exact checkout path, never an alias.
            socket.mkdir(mode=0o700, exist_ok=False)
            socket_owned = True
        for name in (('initdb', 'pg_ctl', 'createdb', 'psql') if suite == 'c1' else ('initdb', 'pg_ctl')):
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
        if suite == 'c1':
            pid = (data / 'postmaster.pid').read_text().splitlines()
            if (len(pid) < 5 or not pid[0].isdigit() or int(pid[0]) <= 0
                    or pid[1] != str(data) or pid[3] != port or pid[4] != str(socket)):
                raise ValueError('C1 owned postmaster identity mismatch')
            connection = ['-h', socket, '-p', port, '-U', 'mmorinag']
            command([PG_BIN / 'createdb', *connection, 'c1_menu_master'], 'createdb.log')
            command([PG_BIN / 'psql', '-X', '-w', *connection, '-d', 'c1_menu_master',
                     '-v', 'ON_ERROR_STOP=1', '-At', '-c',
                     "SELECT json_build_object('data_directory', current_setting('data_directory'), "
                     "'port', current_setting('port'), 'socket', current_setting('unix_socket_directories'), "
                     "'listen_addresses', current_setting('listen_addresses'), "
                     "'major', current_setting('server_version_num')::int / 10000, "
                     "'role', current_user, 'database', current_database())"], 'server.json')
            actual = json.loads((evidence / 'server.json').read_text())
            if actual != {'data_directory': str(data), 'port': port, 'socket': str(socket),
                          'listen_addresses': '', 'major': 16, 'role': 'mmorinag', 'database': 'c1_menu_master'}:
                raise ValueError('C1 physical server identity mismatch')
        # The readonly fixture separately validates PID, server, role and case databases.
        result = command(pytest_command(root, suite), 'pytest.log', check=False)
        lifecycle.append({'pytest_exit': result})
    except BaseException:
        (evidence / 'runner-error.log').write_text(traceback.format_exc())
        raise
    finally:
        try:
            if initialized:
                status = command([PG_BIN / 'pg_ctl', '-D', data, 'status'], 'stop.log', check=False)
                if status == 0:
                    command([PG_BIN / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'], 'stop.log')
                status = command([PG_BIN / 'pg_ctl', '-D', data, 'status'], 'stop.log', check=False)
                if status != 3 or (data / 'postmaster.pid').exists() or (socket / f'.s.PGSQL.{port}').exists():
                    raise RuntimeError('owned PG shutdown not verified')
                lifecycle.append({'shutdown_verified': True, 'socket_alias_used': False})
            if suite == 'c1' and socket_owned:
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
