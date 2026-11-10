"""Staging-only recorded orientation delta on the signed deployed shipping source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
BASE = '1791015f959878dbddad8e93c1f4ded84359b92b'
RUN = 42734786979
ALLOWED = {'backend/src/services/workflow_display_read_service.py',
    'backend/tests/unit/test_ocr_history_postgresql.py',
    'backend/tests/unit/test_rotation_stg_receipt.py',
    'scripts/local_hospital_test_runner.py', 'scripts/run_local_rotation_stg_proof.py',
    'scripts/run_local_rotation_pg.py'}
COMMANDS = [
    ('display-sqlite', ['-m', 'pytest', 'backend/tests/unit/test_workflow_recorded_job_status.py',
        'backend/tests/unit/test_inspection_demand_read.py', 'backend/tests/unit/test_rotation_stg_receipt.py', '-q']),
    ('display-postgres', ['scripts/run_local_rotation_pg.py']),
]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def validate(proof, source, definition):
    from local_hospital_test_runner import validate_receipt
    if (set(proof) != {'receipt_version','source_sha','definition_sha256','baseline','changed_files',
                      'commands','clean_before','clean_after','staging_only'}
        or proof['receipt_version'] != 8 or proof['source_sha'] != source
        or proof['definition_sha256'] != definition or proof['staging_only'] is not True
        or proof['clean_before'] is not True or proof['clean_after'] is not True):
        raise ValueError('Exact staging orientation proof required')
    baseline = verify_signed(proof['baseline'])
    validate_receipt(baseline, BASE, definition)
    if baseline.get('receipt_version') != 7 or baseline.get('source_sha') != BASE:
        raise ValueError('Signed deployed staging baseline required')
    run = json.loads(subprocess.check_output(['gh','api',f'repos/mmorinag127/sawa-hospital-system/actions/runs/{RUN}'], text=True))
    if (run.get('head_sha') != BASE or run.get('conclusion') != 'success'
        or run.get('event') != 'workflow_dispatch' or run.get('actor',{}).get('login') != 'mmorinag127'
        or run.get('path') != '.github/workflows/deploy-stg.yml'):
        raise ValueError('Successful owner staging deployment required')
    git('merge-base','--is-ancestor',BASE,source)
    changed = git('diff','--name-only',BASE,source).splitlines()
    if not changed or changed != proof['changed_files'] or set(changed)-ALLOWED:
        raise ValueError('Unreviewed orientation source changes')
    if len(proof['commands']) != len(COMMANDS):
        raise ValueError('All local orientation commands required')
    for record, (name, argv) in zip(proof['commands'], COMMANDS, strict=True):
        expected = hashlib.sha256(json.dumps(argv).encode()).hexdigest()
        if (set(record) != {'name','command_sha256','log_sha256','exit_code'}
            or record['name'] != name or record['command_sha256'] != expected
            or type(record['exit_code']) is not int or record['exit_code'] != 0
            or not isinstance(record['log_sha256'], str) or len(record['log_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in record['log_sha256'])):
            raise ValueError('Exact successful orientation command required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or git('status','--porcelain'):
        raise ValueError('Clean fixed local source required')
    source = git('rev-parse','HEAD')
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    env = dict(os.environ, DB_URI='sqlite:///:memory:', PYTHONPATH=str(ROOT/'backend'))
    for name, argv in COMMANDS:
        log = args.output/(name+'.log')
        with log.open('wb') as stream:
            result = subprocess.run([sys.executable, *argv], cwd=ROOT, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        records.append(dict(name=name, command_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
            log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(), exit_code=result.returncode))
        print(name, 'passed' if result.returncode == 0 else 'FAILED', flush=True)
        if result.returncode:
            raise RuntimeError('Local orientation batch failed; no receipt issued')
    if git('status','--porcelain') or git('rev-parse','HEAD') != source:
        raise ValueError('Source changed during verification')
    proof = dict(receipt_version=8, source_sha=source,
        definition_sha256=hashlib.sha256((ROOT/'scripts/local_hospital_test_manifest.json').read_bytes()).hexdigest(),
        baseline=json.loads(args.baseline.read_text()), changed_files=git('diff','--name-only',BASE,source).splitlines(),
        commands=records, clean_before=True, clean_after=True, staging_only=True)
    validate(proof, source, proof['definition_sha256'])
    (args.output/'receipt.json').write_text(json.dumps(sign(proof), indent=2)+'\n')


if __name__ == '__main__':
    main()
