#!/usr/bin/env python3
"""Execute preserved output-boundary and full formal-chain PG16 cases locally."""
from local_hospital_legacy_step import execute
import os
from pathlib import Path
import subprocess
import tempfile


def output_boundary():
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI'):
        raise SystemExit('PG output tests require local execution')
    pg = Path(os.environ.get('PG_BIN', '/usr/lib/postgresql/16/bin'))
    root = Path(tempfile.mkdtemp(prefix='sawa-output-pg-'))
    data, socket = root / 'data', root / 'socket'
    socket.mkdir()
    print('owned_postgres_evidence=' + str(root), flush=True)
    def run(argv, name):
        with (root / name).open('ab') as log:
            subprocess.run([str(arg) for arg in argv], stdout=log, stderr=subprocess.STDOUT, check=True)
    started = False
    try:
        run([pg / 'initdb', '-D', data, '-U', 'sawa_output_test', '-A', 'trust',
             '--locale=C', '--encoding=UTF8'], 'initdb.log')
        if (data / 'PG_VERSION').read_text().strip() != '16':
            raise ValueError('owned PostgreSQL 16 required')
        # A busy port fails startup; never attach to or stop an existing server.
        run([pg / 'pg_ctl', '-D', data, '-l', root / 'postgres.log', '-o',
             f'-h 127.0.0.1 -k {socket} -p 5432', '-w', 'start'], 'start.log')
        started = True
        run([pg / 'createdb', '-h', socket, '-p', '5432', '-U', 'sawa_output_test',
             'sawa_modernization_output_test'], 'createdb.log')
        execute('modernization-pg-output.yml', 'pg-output-boundary',
                ['Record actual source SHA', 'Formal Alembic migration', 'Real PG transaction boundaries'])
    finally:
        if started:
            run([pg / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'], 'stop.log')
            if (data / 'postmaster.pid').exists():
                raise RuntimeError('owned PostgreSQL did not stop')

if __name__ == '__main__':
    output_boundary()
    execute('modernization-pg-output.yml', 'formal-pg-chain',
            ['Record source and owned paths', 'Start exclusively owned Unix socket PG16',
             'Existing formal migration module (all cases)'],
            cleanup='Stop only owned PG16')
