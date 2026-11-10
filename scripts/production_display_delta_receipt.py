"""Production acceptance of the exact staged runtime, not a claimed full rerun."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
STAGED = 'a1a435f9183fbebb8b8098b447aff4d8c1526de5'
STAGING_RUN = 42739934865
GATE_ONLY = {'scripts/local_hospital_test_runner.py', 'scripts/production_display_delta_receipt.py',
             'backend/tests/unit/test_production_display_delta_receipt.py'}
COMMAND = ['-m','pytest','backend/tests/unit/test_production_display_delta_receipt.py',
           'backend/tests/unit/test_rotation_stg_receipt.py',
           'backend/tests/unit/test_shipping_staging_receipt.py','-q']


def git(*args):
    return subprocess.check_output(['git',*args], cwd=ROOT, text=True).strip()


def require_gate_only_delta(paths):
    if not paths or set(paths)-GATE_ONLY:
        raise ValueError('Production runtime must exactly equal the successful staging source')


def validate(proof, source, definition):
    from local_hospital_test_runner import validate_receipt
    if (set(proof) != {'receipt_version','source_sha','definition_sha256','staging_receipt',
                      'changed_files','regression','clean_before','clean_after','full_rerun'}
        or proof['receipt_version'] != 9 or proof['source_sha'] != source
        or proof['definition_sha256'] != definition or proof['clean_before'] is not True
        or proof['clean_after'] is not True or proof['full_rerun'] is not False):
        raise ValueError('Exact staged-runtime production delta proof required')
    staged = verify_signed(proof['staging_receipt'])
    if staged.get('receipt_version') != 8 or staged.get('source_sha') != STAGED:
        raise ValueError('Exact signed orientation and shipping staging proof required')
    # This verifies the prior signed production baseline, shipping checks/build,
    # and orientation SQLite/native-PG checks without claiming they ran again.
    validate_receipt(staged, STAGED, definition)
    run = json.loads(subprocess.check_output(['gh','api',
        f'repos/mmorinag127/sawa-hospital-system/actions/runs/{STAGING_RUN}'], text=True))
    if (run.get('head_sha') != STAGED or run.get('conclusion') != 'success'
        or run.get('status') != 'completed' or run.get('head_branch') != 'develop'
        or run.get('event') != 'workflow_dispatch'
        or run.get('actor',{}).get('login') != 'mmorinag127'
        or run.get('path') != '.github/workflows/deploy-stg.yml'):
        raise ValueError('Successful exact owner staging deployment required')
    git('merge-base','--is-ancestor',STAGED,source)
    changed = git('diff','--name-only',STAGED,source).splitlines()
    require_gate_only_delta(changed)
    if changed != proof['changed_files']:
        raise ValueError('Staged-runtime delta changed')
    record = proof['regression']
    if (set(record) != {'command_sha256','log_sha256','exit_code'}
        or record['command_sha256'] != hashlib.sha256(json.dumps(COMMAND).encode()).hexdigest()
        or type(record['exit_code']) is not int or record['exit_code'] != 0
        or not isinstance(record['log_sha256'],str) or len(record['log_sha256']) != 64
        or any(c not in '0123456789abcdef' for c in record['log_sha256'])):
        raise ValueError('Successful production proof regression required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--staging-receipt',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or git('status','--porcelain'):
        raise ValueError('Clean fixed local source required')
    source = git('rev-parse','HEAD')
    args.output.mkdir(parents=True,exist_ok=False)
    log = args.output/'production-proof-regression.log'
    with log.open('wb') as stream:
        result = subprocess.run([sys.executable,*COMMAND],cwd=ROOT,
            env=dict(os.environ,DB_URI='sqlite:///:memory:',PYTHONPATH=str(ROOT/'backend')),
            stdout=stream,stderr=subprocess.STDOUT)
    if result.returncode or git('status','--porcelain') or git('rev-parse','HEAD') != source:
        raise ValueError('Production proof regression failed or source changed')
    proof = dict(receipt_version=9,source_sha=source,
        definition_sha256=hashlib.sha256((ROOT/'scripts/local_hospital_test_manifest.json').read_bytes()).hexdigest(),
        staging_receipt=json.loads(args.staging_receipt.read_text()),
        changed_files=git('diff','--name-only',STAGED,source).splitlines(),
        regression=dict(command_sha256=hashlib.sha256(json.dumps(COMMAND).encode()).hexdigest(),
            log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),exit_code=result.returncode),
        clean_before=True,clean_after=True,full_rerun=False)
    validate(proof,source,proof['definition_sha256'])
    (args.output/'receipt.json').write_text(json.dumps(sign(proof),indent=2)+'\n')
    print('Signed exact-staged-runtime production delta issued; no full rerun claimed')


if __name__ == '__main__':
    main()
