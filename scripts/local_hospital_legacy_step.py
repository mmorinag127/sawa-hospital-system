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
LOCAL_TEST_JOBS = {
    ('modernization-image-ci.yml', 'frontend-image'),
    ('modernization-pg-output.yml', 'pg-output-boundary'),
    ('modernization-pg-output.yml', 'formal-pg-chain'),
    ('live-schema-adoption-test.yml', 'live-schema-adoption'),
    ('verify-stg-ocr-ui.yml', 'verify'),
}
LOCAL_NAMES = {
    'GITHUB_WORKSPACE': 'SAWA_LOCAL_SOURCE_DIR',
    'RUNNER_TEMP': 'SAWA_LOCAL_LEGACY_TEMP_DIR',
    'GITHUB_SHA': 'SAWA_LOCAL_SOURCE_SHA',
    'GITHUB_RUN_ID': 'SAWA_LOCAL_RUN_ID',
    'GITHUB_RUN_ATTEMPT': 'SAWA_LOCAL_RUN_ATTEMPT',
    'GITHUB_ENV': 'SAWA_LOCAL_LEGACY_ENV_FILE',
    'GITHUB_OUTPUT': 'SAWA_LOCAL_LEGACY_OUTPUT_FILE',
}


def local_text(value, context):
    expressions = {
        'github.workspace': context['SAWA_LOCAL_SOURCE_DIR'],
        'runner.temp': context['SAWA_LOCAL_LEGACY_TEMP_DIR'],
        'github.sha': context['SAWA_LOCAL_SOURCE_SHA'],
        'github.run_id': context['SAWA_LOCAL_RUN_ID'],
        'github.run_attempt': context['SAWA_LOCAL_RUN_ATTEMPT'],
    }
    for name, replacement in expressions.items():
        value = value.replace('${{ ' + name + ' }}', replacement)
    if '${{' in value:
        raise ValueError('unresolved workflow expression in local command')
    return re.sub(r'\b(' + '|'.join(LOCAL_NAMES) + r')\b',
                  lambda match: LOCAL_NAMES[match.group()], value)


def step(workflow, job, name):
    if Path(workflow).name != workflow or not workflow.endswith('.yml'):
        raise ValueError('local legacy workflow basename required')
    if (workflow, job) not in LOCAL_TEST_JOBS:
        raise ValueError('only explicitly mapped local test jobs are allowed')
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
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('GITHUB_', 'RUNNER_'))}
        context = {'SAWA_LOCAL_SOURCE_DIR': str(ROOT), 'SAWA_LOCAL_LEGACY_TEMP_DIR': directory,
                   'SAWA_LOCAL_SOURCE_SHA': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   'SAWA_LOCAL_RUN_ID': env.get('SAWA_LOCAL_RUN_ID', str(os.getpid())),
                   'SAWA_LOCAL_RUN_ATTEMPT': '1',
                   'SAWA_LOCAL_LEGACY_ENV_FILE': str(Path(directory) / 'environment'),
                   'SAWA_LOCAL_LEGACY_OUTPUT_FILE': str(Path(directory) / 'output')}
        env.update(context)

        def run(name):
            job, item = step(workflow, job_name, name)
            inherited = {**job.get('env', {}), **item.get('env', {})}
            # Dependency selection never omits a test in the local full run.
            for key in ('PYTEST_ADDOPTS', 'SAWA_IMPACT_ROOT', 'SAWA_IMPACT_STATE',
                        'SAWA_IMPACT_SPLIT_RUNS', 'TESTMON_DATAFILE', 'SAWA_IMPACT_FULL',
                        'SAWA_NODE_IMPACT_STATE', 'NODE_OPTIONS'):
                inherited.pop(key, None)
            local_env = dict(env)
            for key, value in inherited.items():
                key = LOCAL_NAMES.get(key, key)
                if key in local_env and key not in ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI'):
                    continue
                local_env[key] = local_text(str(value), context)
            if workflow == 'modernization-pg-output.yml' and job_name == 'pg-output-boundary':
                Path(directory, 'pg-output-boundary').mkdir(exist_ok=True)
            if 'TEST_BOOTSTRAP_DB_URI' in inherited and 'TEST_BOOTSTRAP_DB_URI' not in env:
                raise ValueError('TEST_BOOTSTRAP_DB_URI must name an explicitly owned local test database')
            command = item['run']
            if 'export GITHUB_ACTIONS=true' in command:
                command = command.replace('export GITHUB_ACTIONS=true', ': # local owned PostgreSQL execution')
            command = local_text(command, context)
            argv = [sys.executable, '-c', command] if item.get('shell') == 'python' else ['bash', '-euo', 'pipefail', '-c', command]
            subprocess.run(argv, cwd=ROOT / item.get('working-directory', '.'), env=local_env, check=True)
            updates = Path(env['SAWA_LOCAL_LEGACY_ENV_FILE'])
            if updates.exists():
                for line in updates.read_text().splitlines():
                    key, separator, value = line.partition('=')
                    if separator and re.fullmatch('[A-Z][A-Z0-9_]*', key):
                        if key.startswith(('GITHUB_', 'RUNNER_')):
                            raise ValueError('hosted identity cannot be created by a local step')
                        if key in context and value != context[key]:
                            raise ValueError('local step changed its source identity')
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
