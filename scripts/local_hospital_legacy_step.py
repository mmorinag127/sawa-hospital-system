#!/usr/bin/env python3
"""Execute preserved test commands locally, with explicit per-run evidence."""
import argparse
from contextlib import nullcontext
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def step(workflow, job, name):
    if Path(workflow).name != workflow or not workflow.endswith('.yml'):
        raise ValueError('local legacy workflow basename required')
    document = yaml.safe_load((ROOT / 'local-checks/legacy' / workflow).read_text())
    matches = [item for item in document['jobs'][job]['steps'] if item.get('name') == name]
    if len(matches) != 1 or 'run' not in matches[0]:
        raise ValueError('exact preserved test step required')
    return document['jobs'][job], matches[0]


def execute(workflow, job_name, names, cleanup=None):
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI'):
        raise ValueError('legacy tests require local execution')
    with nullcontext(tempfile.mkdtemp(prefix='sawa-local-legacy-')) as directory:
        print('local_evidence_directory=' + directory, flush=True)
        env = dict(os.environ)
        env.update({'GITHUB_WORKSPACE': str(ROOT), 'RUNNER_TEMP': directory,
                    'GITHUB_SHA': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    'GITHUB_RUN_ID': env.get('SAWA_LOCAL_RUN_ID', str(os.getpid())),
                    'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_ENV': str(Path(directory) / 'environment'),
                    'GITHUB_OUTPUT': str(Path(directory) / 'output')})

        def run(name):
            job, item = step(workflow, job_name, name)
            inherited = {**job.get('env', {}), **item.get('env', {})}
            # Dependency selection never omits a test in the local full run.
            for key in ('PYTEST_ADDOPTS', 'SAWA_IMPACT_ROOT', 'SAWA_IMPACT_STATE',
                        'SAWA_IMPACT_SPLIT_RUNS', 'TESTMON_DATAFILE', 'SAWA_IMPACT_FULL',
                        'SAWA_NODE_IMPACT_STATE', 'NODE_OPTIONS'):
                inherited.pop(key, None)
            local_env = dict(env)
            if workflow == 'ci.yml':
                local_env['SAWA_IMPACT_FULL'] = '1'
                local_env['PYTEST_ADDOPTS'] = ''
                local_env['PYTHONPATH'] = str(ROOT / 'backend')
                local_env['REFRESH_TEST_PG_BIN'] = os.environ.get('REFRESH_TEST_PG_BIN', '/usr/lib/postgresql/16/bin')
            for key, value in inherited.items():
                if key in local_env and key not in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI'):
                    continue
                value = str(value).replace('${{ github.workspace }}', str(ROOT)).replace('${{ runner.temp }}', directory)
                if '${{' in value:
                    raise ValueError('explicit local environment required: ' + key)
                local_env[key] = value
            if workflow == 'modernization-pg-output.yml' and job_name == 'pg-output-boundary':
                Path(directory, 'pg-output-boundary').mkdir(exist_ok=True)
            if 'TEST_BOOTSTRAP_DB_URI' in inherited and 'TEST_BOOTSTRAP_DB_URI' not in env:
                raise ValueError('TEST_BOOTSTRAP_DB_URI must name an explicitly owned local test database')
            command = item['run']
            if 'export GITHUB_ACTIONS=true' in command:
                command = command.replace('export GITHUB_ACTIONS=true', ': # local owned PostgreSQL execution')
            if '${{' in command:
                raise ValueError('unresolved workflow expression in local command')
            argv = [sys.executable, '-c', command] if item.get('shell') == 'python' else ['bash', '-euo', 'pipefail', '-c', command]
            subprocess.run(argv, cwd=ROOT / item.get('working-directory', '.'), env=local_env, check=True)
            updates = Path(env['GITHUB_ENV'])
            if updates.exists():
                for line in updates.read_text().splitlines():
                    key, separator, value = line.partition('=')
                    if separator and re.fullmatch('[A-Z][A-Z0-9_]*', key):
                        env[key] = value

        try:
            for name in names:
                run(name)
        finally:
            if cleanup:
                run(cleanup)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--step', action='append', required=True)
    parser.add_argument('--cleanup')
    args = parser.parse_args()
    execute(args.workflow, args.job, args.step, args.cleanup)
