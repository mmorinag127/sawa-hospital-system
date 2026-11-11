"""Source-bound reset/agent regression proof on the deployed signed baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from connected_ruling_release_receipt import git, successful_run
from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
BASE = 'ed03f88a8ee2d562f4acd10fb96a10043ef2f119'
BASE_RUN = 42815316580
ALLOWED = {
    '.github/workflows/deploy-prod.yml', 'backend/src/api/orders.py',
    'backend/src/services/order_workflow_v2_service.py',
    'backend/src/services/initial_saved_import_service.py',
    'backend/tests/unit/test_initial_saved_import_service.py',
    'backend/tests/unit/test_workflow_ocr_reset.py',
    'backend/tests/unit/test_online_production_release.py',
    'backend/tests/unit/test_ocr_reset_release_receipt.py',
    'experiments/agent_assistant/durable.py',
    'experiments/agent_assistant/tests/test_cloud_store_target.py',
    'frontend/src/features/orders/workflowTypes.ts',
    'frontend/next-env.d.ts',
    'frontend/src/pages/orders/[id]/workflow-v2.tsx',
    'frontend/tests/workflow-demand-read-contract.test.cjs',
    'scripts/production_online_release.py', 'scripts/local_hospital_test_runner.py',
    'scripts/ocr_reset_release_receipt.py', 'docs/ocr-reset-agent-20261111.md',
    'backend/src/services/agent_trial_transport.py',
    'backend/src/services/order_service.py', 'backend/src/services/storage_service.py',
    'backend/src/services/uploaded_pdf_service.py',
    'backend/src/services/workflow_attempt_guard_service.py',
    'backend/src/workers/ingest_mail_adapter.py',
    'backend/tests/unit/test_agent_trial_transport.py',
    'experiments/agent_assistant/app.py','experiments/agent_assistant/hospital_source.py',
    'experiments/agent_assistant/export_hospital_source.py',
    'experiments/agent_assistant/initialize_trial.py','experiments/agent_assistant/sandbox.py',
    'experiments/agent_assistant/store.py','experiments/agent_assistant/task_flow.py',
    'experiments/agent_assistant/tests/test_hospital_source.py',
    'frontend/src/pages/assistant.tsx','scripts/prepare_hospital_trial_source.py',
    'frontend/tests/helpers/modernization-react-dom.cjs',
    '.github/workflows/deploy-stg.yml','scripts/restore_stg_ocr_selection.py',
    'backend/tests/unit/test_stg_ocr_restore_command.py',
    'experiments/agent_assistant/catalog.py',
    'experiments/agent_assistant/domain_host.py',
    'experiments/agent_assistant/native_recovery.py',
    'experiments/agent_assistant/recovery.py',
    'experiments/agent_assistant/tests/test_recovery.py',
    'experiments/agent_assistant/tests/test_staging_runtime.py',
    'experiments/agent_assistant/tests/test_native_lifecycle.py',
    'docs/agent-memory-recovery-20261111.md',
}
BACKEND = ['backend/tests/unit/' + name for name in (
    'test_workflow_ocr_reset.py', 'test_order_workflow_v2_service.py',
    'test_initial_saved_import_service.py', 'test_online_production_release.py',
    'test_ocr_reset_release_receipt.py','test_agent_trial_transport.py','test_stg_ocr_restore_command.py')]
AGENT = ['experiments/agent_assistant/tests/' + name for name in (
    'test_cloud_store_target.py', 'test_connection_pool.py', 'test_durable_postgres.py',
    'test_postgres_ledger_contract.py', 'test_ledger_codec.py', 'test_generation_fence.py',
    'test_fax_workflow.py', 'test_hospital_output_completion.py',
    'test_hospital_source.py','test_selected_tasks.py','test_trial.py','test_execution_binding.py',
    'test_recovery.py','test_staging_runtime.py','test_native_lifecycle.py')]
COMMANDS = [
    ('backend', ['python', '-m', 'pytest', '-q', *BACKEND, '--junitxml=backend.xml'], '.'),
    ('agent', ['python', '-m', 'pytest', '-q', *AGENT, '--junitxml=agent.xml'], '.'),
    ('frontend', ['node', '--test', '--test-concurrency=1',
        'tests/workflow-demand-read-contract.test.cjs', 'tests/modernization-workflow-action-dom.test.cjs'], 'frontend'),
    ('types', ['node', 'node_modules/typescript/bin/tsc', '--noEmit', '--incremental', 'false'], 'frontend'),
    ('build', ['node', 'node_modules/next/dist/bin/next', 'build'], 'frontend'),
]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def require_local_postgres(dsn):
    from psycopg2.extensions import parse_dsn
    target = parse_dsn(dsn)
    if (target.get('host') not in ('127.0.0.1', 'localhost', '::1')
            or not target.get('dbname', '').startswith('agent_trial')
            or target.get('hostaddr') not in (None, '127.0.0.1', '::1')
            or 'service' in target):
        raise ValueError('Disposable local agent_trial database required')


def validate_staging_jobs(jobs):
    deployed = {job.get('name') for job in jobs if job.get('conclusion')=='success'}
    if not {'deploy-backend','deploy-frontend'}.issubset(deployed):
        raise ValueError('Successful backend and frontend deployment jobs required; restore-only runs are not deployment proof')


def require_staging_deployments(run):
    payload = json.loads(subprocess.check_output(['gh','api',
        f'repos/mmorinag127/sawa-hospital-system/actions/runs/{run}/jobs?per_page=100'],text=True))
    validate_staging_jobs(payload['jobs'])


def validate(proof, source, definition, *, require_staging=False):
    from local_hospital_test_runner import validate_receipt
    fields = {'receipt_version', 'source_sha', 'definition_sha256', 'baseline',
        'changed_files', 'commands', 'clean_before', 'clean_after', 'staging_run'}
    if (not isinstance(proof, dict) or set(proof) != fields
            or type(proof['receipt_version']) is not int or proof['receipt_version'] != 11
            or proof['source_sha'] != source or proof['definition_sha256'] != definition
            or proof['clean_before'] is not True or proof['clean_after'] is not True):
        raise ValueError('Exact clean reset/agent proof required')
    baseline = verify_signed(proof['baseline'])
    if baseline.get('receipt_version') != 10 or baseline.get('source_sha') != BASE:
        raise ValueError('Exact signed deployed baseline required')
    validate_receipt(baseline, BASE, definition, require_full=True)
    successful_run(BASE_RUN, BASE, '.github/workflows/deploy-prod.yml', 'release/prod-')
    git('merge-base', '--is-ancestor', BASE, source)
    changed = git('diff', '--name-only', BASE, source).splitlines()
    if not changed or changed != proof['changed_files'] or set(changed) - ALLOWED:
        raise ValueError('Unreviewed source change')
    if not isinstance(proof['commands'], list) or len(proof['commands']) != len(COMMANDS):
        raise ValueError('Every related command required')
    for record, (name, argv, directory) in zip(proof['commands'], COMMANDS, strict=True):
        if (not isinstance(record, dict) or set(record) != {'name', 'command_sha256', 'log_sha256', 'exit_code', 'junit'}
                or record['name'] != name
                or record['command_sha256'] != digest(json.dumps([argv, directory]).encode())
                or type(record['exit_code']) is not int or record['exit_code'] != 0
                or not isinstance(record['log_sha256'], str) or len(record['log_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in record['log_sha256'])):
            raise ValueError('Exact successful related execution required')
        junit = record['junit']
        if name in ('backend', 'agent'):
            if (not isinstance(junit, dict) or set(junit) != {'tests', 'failures', 'errors', 'skipped', 'sha256'}
                    or type(junit['tests']) is not int or junit['tests'] < len(BACKEND if name == 'backend' else AGENT)
                    or any(type(junit[k]) is not int or junit[k] != 0 for k in ('failures', 'errors', 'skipped'))
                    or not isinstance(junit['sha256'], str) or len(junit['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in junit['sha256'])):
                raise ValueError('Executed regression without skips required')
        elif junit is not None:
            raise ValueError('Unexpected JUnit record')
    staging = proof['staging_run']
    if staging is not None:
        if type(staging) is not int or staging <= 0:
            raise ValueError('Invalid staging run')
        successful_run(staging, source, '.github/workflows/deploy-stg.yml', 'develop')
        require_staging_deployments(staging)
    elif require_staging:
        raise ValueError('Production requires exact successful staging deployment')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--promote', type=Path)
    parser.add_argument('--staging-run', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI') or git('status', '--porcelain'):
        raise ValueError('Clean fixed local source required')
    source = git('rev-parse', 'HEAD')
    definition = digest((ROOT/'scripts/local_hospital_test_manifest.json').read_bytes())
    args.output.mkdir(parents=True, exist_ok=False)
    if args.promote:
        proof = verify_signed(json.loads(args.promote.read_text()))
        validate(proof, source, definition)
        proof = {**proof, 'staging_run': args.staging_run}
        validate(proof, source, definition, require_staging=True)
    else:
        if not args.baseline or not os.environ.get('SAWA_AGENT_TEST_POSTGRES_DSN'):
            raise ValueError('Signed baseline and disposable local PostgreSQL required')
        require_local_postgres(os.environ['SAWA_AGENT_TEST_POSTGRES_DSN'])
        environment = dict(os.environ, DB_URI='sqlite:///:memory:', PYTHONPATH=str(ROOT/'backend'))
        records = []
        failed = []
        for name, argv, directory in COMMANDS:
            actual = [sys.executable if argv[0] == 'python' else argv[0], *argv[1:]]
            if name in ('backend', 'agent'):
                actual[-1] = '--junitxml='+str((args.output/(name+'.xml')).resolve())
            log = args.output/(name+'.log')
            with log.open('wb') as stream:
                run = subprocess.run(actual, cwd=ROOT/directory, env=environment, stdout=stream, stderr=subprocess.STDOUT)
            if run.returncode:
                failed.append(name)
            junit = None
            if name in ('backend', 'agent'):
                path = args.output/(name+'.xml')
                if path.exists():
                    suites = list(ET.parse(path).getroot().iter('testsuite'))
                    junit = {key: sum(int(s.get(key, '0')) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
                    junit['sha256'] = digest(path.read_bytes())
            records.append(dict(name=name, command_sha256=digest(json.dumps([argv, directory]).encode()),
                log_sha256=digest(log.read_bytes()), exit_code=run.returncode, junit=junit))
        (args.output/'commands.json').write_text(json.dumps(records, indent=2)+'\n')
        if failed:
            raise ValueError(', '.join(failed)+' failed; no success receipt issued')
        proof = dict(receipt_version=11, source_sha=source, definition_sha256=definition,
            baseline=json.loads(args.baseline.read_text()), changed_files=git('diff', '--name-only', BASE, source).splitlines(),
            commands=records, clean_before=True, clean_after=True, staging_run=None)
        validate(proof, source, definition)
    if git('status', '--porcelain') or git('rev-parse', 'HEAD') != source:
        raise ValueError('Source changed during proof')
    (args.output/'receipt.json').write_text(json.dumps(sign(proof), indent=2)+'\n')
    print('Signed reset/agent related-regression receipt issued; no full rerun claimed')


if __name__ == '__main__':
    main()
