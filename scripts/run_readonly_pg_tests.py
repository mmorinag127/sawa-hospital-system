#!/usr/bin/env python3
"""Run the unchanged readonly module, retaining pytest failures and owned cleanup."""
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


def owned_root(run_id, attempt):
    if any(not re.fullmatch(r'[1-9][0-9]{0,19}', value) for value in (run_id, attempt)):
        raise ValueError('explicit numeric CI identity required')
    return Path('/tmp').resolve() / f'sawa-ro-{run_id}-{attempt}'


def pytest_command(root):
    return [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', MODULE,
            '--basetemp', str(root / 'tests'), '--junitxml', str(root / 'evidence/results.xml'), '-v']


def run(source, run_id, attempt):
    source = source.resolve(strict=True)
    root = owned_root(run_id, attempt)
    root.mkdir(mode=0o700, exist_ok=False)
    evidence = root / 'evidence'
    evidence.mkdir()
    home = root / 'home'
    home.mkdir(mode=0o700)
    pg = root / 'pg'
    pg.mkdir(mode=0o700)
    data, socket = pg / 'pgdata', pg / 'socket'
    socket.mkdir(mode=0o700)
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
    lifecycle = []
    initialized = False
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
        for name in ('initdb', 'pg_ctl'):
            tool = PG_BIN / name
            if not tool.is_file() or not os.access(tool, os.X_OK):
                raise ValueError(f'missing PG16 executable: {tool}')
            command([tool, '--version'], 'tools.log')
        command([PG_BIN / 'initdb', '-D', data, '-U', 'mmorinag', '-A', 'trust', '--locale=C', '--encoding=UTF8'], 'initdb.log')
        initialized = True
        if (data / 'PG_VERSION').read_text().strip() != '16':
            raise ValueError('PG16 required')
        command([PG_BIN / 'pg_ctl', '-D', data, '-l', evidence / 'postgres.log', '-o',
                 f"-p 55471 -k {socket} -c listen_addresses='' -c unix_socket_permissions=0700", '-w', 'start'], 'start.log')
        # Existing fixture validates PID, physical server, role and each case database.
        result = command(pytest_command(root), 'pytest.log', check=False)
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
                if status != 3 or (data / 'postmaster.pid').exists() or (socket / '.s.PGSQL.55471').exists():
                    raise RuntimeError('owned PG shutdown not verified')
                lifecycle.append({'shutdown_verified': True, 'socket_alias_used': False})
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
    args = parser.parse_args()
    sys.exit(run(args.source, args.run_id, args.attempt))
