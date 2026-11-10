"""Exact-source connected-ruling proof on the signed, deployed display release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a3be3637474f01fd095d26b51460666a43c07cce'
BASE_RUN = 42756647294
REPOSITORY = 'mmorinag127/sawa-hospital-system'
ALLOWED = {
    'backend/src/services/hakodate_step_review_pipeline_service.py',
    'backend/tests/unit/test_connected_ruling_arms.py',
    'backend/tests/unit/test_hakodate_observed_fax_rows.py',
    'backend/tests/unit/test_connected_ruling_release_receipt.py',
    'docs/ocr-connected-rulings-local-20261110.md',
    'scripts/connected_ruling_release_receipt.py',
    'scripts/local_hospital_test_runner.py',
    'scripts/run_prod_schema_adoption.py',
    'backend/tests/unit/test_production_online_existing_head.py',
}
MODULES = ['backend/tests/unit/' + name for name in (
    'test_connected_ruling_arms.py', 'test_observed_ruling_junctions.py',
    'test_header_topology_alignment.py', 'test_ocr_row_recovery.py',
    'test_modernization_printed_header_boundaries.py', 'test_h2_operator_repair_revision.py',
    'test_geometry_review_contract.py', 'test_ocr_header_recovery.py',
    'test_ruling_slant_before_detection.py', 'test_ocr_row_recovery_persistence.py',
    'test_row_ruling_continuity.py', 'test_canonical_saved_projection_regression.py',
    'test_hakodate_observed_fax_rows.py', 'test_connected_ruling_release_receipt.py',
    'test_production_online_existing_head.py', 'test_online_production_release.py',
)]
COMMAND = ['-m', 'pytest', '-q', '--disable-warnings', *MODULES, '--junitxml=results.xml']
FIXTURES = {
    'SAWA_STEPPED_JUNCTION_FIXTURE': '.', 'SAWA_PIA_GEOMETRY_FIXTURE': '.',
    'SAWA_KASUGA_GEOMETRY_FIXTURE': '.', 'SAWA_SHONAN_GEOMETRY_FIXTURE': '.',
    'SAWA_REPORTED_RULING_FIXTURE': 'ORDf021b65f',
}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def successful_run(run_id, source, workflow, branch):
    value = json.loads(subprocess.check_output(['gh', 'api',
        f'repos/{REPOSITORY}/actions/runs/{run_id}'], text=True))
    if not (value.get('id') == run_id and value.get('head_sha') == source
            and value.get('status') == 'completed' and value.get('conclusion') == 'success'
            and value.get('event') == 'workflow_dispatch' and value.get('path') == workflow
            and value.get('actor', {}).get('login') == 'mmorinag127'
            and value.get('repository', {}).get('full_name') == REPOSITORY
            and value.get('head_branch', '').startswith(branch)):
        raise ValueError('Exact successful owner deployment required')


def validate(proof, source, definition, *, require_staging=False):
    from local_hospital_test_runner import validate_receipt
    fields = {'receipt_version', 'source_sha', 'definition_sha256', 'baseline',
              'changed_files', 'regression', 'clean_before', 'clean_after', 'staging_run'}
    if (not isinstance(proof, dict) or set(proof) != fields
            or type(proof['receipt_version']) is not int or proof['receipt_version'] != 10
            or proof['source_sha'] != source or proof['definition_sha256'] != definition
            or proof['clean_before'] is not True or proof['clean_after'] is not True):
        raise ValueError('Exact clean connected-ruling proof required')
    baseline = verify_signed(proof['baseline'])
    if baseline.get('receipt_version') != 9 or baseline.get('source_sha') != BASE:
        raise ValueError('Exact signed deployed baseline required')
    validate_receipt(baseline, BASE, definition, require_full=True)
    successful_run(BASE_RUN, BASE, '.github/workflows/deploy-prod.yml', 'release/prod-')
    git('merge-base', '--is-ancestor', BASE, source)
    changed = git('diff', '--name-only', BASE, source).splitlines()
    if not changed or changed != proof['changed_files'] or set(changed) - ALLOWED:
        raise ValueError('Unreviewed source change')
    record = proof['regression']
    if (not isinstance(record, dict) or set(record) != {
            'command_sha256', 'log_sha256', 'junit_sha256', 'tests', 'failures', 'errors', 'skipped', 'exit_code'}
            or record['command_sha256'] != hashlib.sha256(json.dumps(COMMAND).encode()).hexdigest()
            or type(record['tests']) is not int or record['tests'] < len(MODULES)
            or any(type(record[k]) is not int or record[k] != 0 for k in ('failures', 'errors', 'skipped', 'exit_code'))
            or any(not isinstance(record[k], str) or len(record[k]) != 64
                   or any(c not in '0123456789abcdef' for c in record[k])
                   for k in ('log_sha256', 'junit_sha256'))):
        raise ValueError('Successful complete related batch without skips required')
    staging = proof['staging_run']
    if staging is not None:
        if type(staging) is not int or staging <= 0:
            raise ValueError('Invalid staging run')
        successful_run(staging, source, '.github/workflows/deploy-stg.yml', 'develop')
    elif require_staging:
        raise ValueError('Production requires the exact successful staging deployment')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--fixtures', type=Path)
    parser.add_argument('--promote', type=Path)
    parser.add_argument('--staging-run', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI') or git('status', '--porcelain'):
        raise ValueError('Clean fixed local source required')
    source = git('rev-parse', 'HEAD')
    definition = hashlib.sha256((ROOT/'scripts/local_hospital_test_manifest.json').read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.promote:
        proof = verify_signed(json.loads(args.promote.read_text()))
        validate(proof, source, definition)
        proof = {**proof, 'staging_run': args.staging_run}
        validate(proof, source, definition, require_staging=True)
    else:
        if not args.baseline or not args.fixtures:
            raise ValueError('Signed baseline and preserved original fixtures required')
        environment = dict(os.environ, DB_URI='sqlite:///:memory:', PYTHONPATH=str(ROOT/'backend'))
        environment.update({name: str((args.fixtures/path).resolve()) for name, path in FIXTURES.items()})
        # JUnit output is scoped to this execution; its destination is not a test input.
        argv = [*COMMAND[:-1], '--junitxml='+str((args.output/'results.xml').resolve())]
        log = args.output/'regression.log'
        with log.open('wb') as stream:
            run = subprocess.run([sys.executable, *argv], cwd=ROOT, env=environment,
                                 stdout=stream, stderr=subprocess.STDOUT)
        if run.returncode:
            raise ValueError('Related batch failed; no success receipt issued')
        suites = list(ET.parse(args.output/'results.xml').getroot().iter('testsuite'))
        record = {key: sum(int(s.get(key, '0')) for s in suites)
                  for key in ('tests', 'failures', 'errors', 'skipped')}
        record.update(command_sha256=hashlib.sha256(json.dumps(COMMAND).encode()).hexdigest(),
            log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
            junit_sha256=hashlib.sha256((args.output/'results.xml').read_bytes()).hexdigest(), exit_code=run.returncode)
        proof = dict(receipt_version=10, source_sha=source, definition_sha256=definition,
            baseline=json.loads(args.baseline.read_text()), changed_files=git('diff', '--name-only', BASE, source).splitlines(),
            regression=record, clean_before=True, clean_after=True, staging_run=None)
        validate(proof, source, definition)
    if git('status', '--porcelain') or git('rev-parse', 'HEAD') != source:
        raise ValueError('Source changed during proof')
    (args.output/'receipt.json').write_text(json.dumps(sign(proof), indent=2)+'\n')
    print('Signed connected-ruling receipt issued; no full rerun claimed')


if __name__ == '__main__':
    main()
