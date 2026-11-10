"""Run the recorded display contract on an owned, short-lived PostgreSQL16."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.environ.get('GITHUB_ACTIONS'):
        raise ValueError('Local execution required')
    binary = Path(os.environ.get('SAWA_LOCAL_PG_BIN', '/opt/homebrew/opt/postgresql@16/bin'))
    with tempfile.TemporaryDirectory(prefix='sawa-rotation-pg-') as directory:
        root = Path(directory).resolve()
        data = root/'data'
        subprocess.run([str(binary/'initdb'), '-D', str(data), '-A', 'trust',
                        '--locale=C', '--encoding=UTF8'], check=True)
        try:
            subprocess.run([str(binary/'pg_ctl'), '-D', str(data), '-l', str(root/'postgres.log'),
                '-o', f"-k {root} -p 55491 -c listen_addresses='' -c unix_socket_permissions=0700", '-w', 'start'], check=True)
            uri = 'postgresql+psycopg2://' + pwd.getpwuid(os.getuid()).pw_name + '@/postgres?' + urlencode({'host': str(root), 'port': '55491'})
            env = dict(os.environ, DB_URI='sqlite:///:memory:', PYTHONPATH=str(ROOT/'backend'),
                       OCR_HISTORY_PROJECTION_POSTGRES_URL=uri)
            subprocess.run([sys.executable, '-c',
                'import os; from sqlalchemy import create_engine; '
                'from src.models.ocr_display_projection_v2 import function_ddl; '
                'e=create_engine(os.environ["OCR_HISTORY_PROJECTION_POSTGRES_URL"]); '
                'c=e.begin(); x=c.__enter__(); x.exec_driver_sql(function_ddl()); c.__exit__(None,None,None); e.dispose()'],
                cwd=ROOT, env=env, check=True)
            subprocess.run([sys.executable, '-m', 'pytest',
                'backend/tests/unit/test_ocr_history_postgresql.py::test_postgresql_job_display_extracts_small_native_and_workflow_references',
                '-q'], cwd=ROOT, env=env, check=True)
        finally:
            subprocess.run([str(binary/'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'], check=True)


if __name__ == '__main__':
    main()
