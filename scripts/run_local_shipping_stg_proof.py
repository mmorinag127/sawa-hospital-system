"""Signed staging-only frontend delta; backend/dependencies must equal deployed baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a995c58d9a336cb57c258bba00817a2fad53943e'
RUN = 42729887232
ALLOWED = {
    'frontend/src/components/TopNav.tsx', 'frontend/src/pages/shipping.tsx',
    'frontend/src/pages/shipping-history.tsx', 'frontend/tests/shipping-controls-source.test.cjs',
    'frontend/tests/e2e/shipping_visibility.spec.ts',
    'scripts/run_local_shipping_stg_proof.py', 'scripts/local_hospital_test_runner.py',
    'scripts/run_local_frontend_e2e.py',
    'backend/tests/unit/test_shipping_staging_receipt.py',
}
COMMANDS = [
    ('receipt-contract', ['-m','pytest','backend/tests/unit/test_shipping_staging_receipt.py','-q'], '.'),
    ('frontend-lint', ['node','node_modules/eslint/bin/eslint.js','.'], 'frontend'),
    ('frontend-contracts', ['node','--test','--test-concurrency=1',
        'tests/shipping-controls-source.test.cjs','tests/config/hospital-additional-readonly.test.mjs',
        'tests/config/operator-entry-readonly.test.mjs','tests/portal-auth-gate.test.cjs',
        'tests/portal-proxy-session.test.cjs','tests/login-flow.test.cjs'], 'frontend'),
    ('frontend-types', ['node','node_modules/typescript/bin/tsc','--noEmit','--incremental','false'], 'frontend'),
    ('shipping-browser', ['scripts/run_local_frontend_e2e.py','tests/e2e/shipping_visibility.spec.ts','tests/e2e/shipping_history.spec.ts','tests/e2e/shipping_status_manual.spec.ts'], '.'),
    ('frontend-build', ['node','node_modules/next/dist/bin/next','build','--webpack'], 'frontend'),
]

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()

def command_hash(argv, directory):
    return hashlib.sha256(json.dumps([argv,directory]).encode()).hexdigest()

def validate(proof, expected_sha, definition):
    from local_hospital_test_runner import validate_receipt
    if set(proof) != {'receipt_version','source_sha','definition_sha256','baseline','changed_files','commands','clean_before','clean_after','staging_only'}:
        raise ValueError('shipping proof fields mismatch')
    if (proof['receipt_version'] != 7 or proof['source_sha'] != expected_sha
        or proof['definition_sha256'] != definition or proof['staging_only'] is not True
        or proof['clean_before'] is not True or proof['clean_after'] is not True):
        raise ValueError('shipping proof source mismatch')
    baseline=verify_signed(proof['baseline'])
    if baseline.get('receipt_version') != 4 or baseline.get('source_sha') != BASE:
        raise ValueError('exact signed deployed baseline required')
    validate_receipt(baseline, BASE, definition, require_full=True)
    run=json.loads(subprocess.check_output(['gh','api',f'repos/mmorinag127/sawa-hospital-system/actions/runs/{RUN}'],text=True))
    if (run.get('head_sha') != BASE or run.get('conclusion') != 'success'
        or run.get('event') != 'workflow_dispatch' or run.get('actor',{}).get('login') != 'mmorinag127'
        or run.get('path') != '.github/workflows/deploy-prod.yml'):
        raise ValueError('verified owner deployment baseline required')
    git('merge-base','--is-ancestor',BASE,expected_sha)
    changed=git('diff','--name-only',BASE,expected_sha).splitlines()
    if not changed or changed != proof['changed_files'] or set(changed)-ALLOWED:
        raise ValueError('non-shipping runtime, dependency or unreviewed source changed')
    if len(proof['commands']) != len(COMMANDS):
        raise ValueError('all frontend commands required')
    for item,(name,argv,directory) in zip(proof['commands'],COMMANDS,strict=True):
        if (set(item) != {'name','command_sha256','log_sha256','exit_code'} or item['name'] != name
            or item['command_sha256'] != command_hash(argv,directory) or type(item['exit_code']) is not int
            or item['exit_code'] != 0 or not isinstance(item['log_sha256'],str)
            or len(item['log_sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['log_sha256'])):
            raise ValueError('exact successful frontend execution required')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or git('status','--porcelain'):
        raise ValueError('clean fixed local source required')
    source=git('rev-parse','HEAD')
    args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    env=dict(os.environ,DB_URI='sqlite:///:memory:',NEXT_TELEMETRY_DISABLED='1',SAWA_LOCAL_EVIDENCE_DIR=str(args.output.resolve()))
    for name,argv,directory in COMMANDS:
        command=([sys.executable,*argv] if directory=='.' else argv)
        log=args.output/(name+'.log')
        cwd=ROOT/directory
        if name=='frontend-build':
            cwd=args.output/'frontend-e2e/source/frontend'
        with log.open('wb') as stream:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT)
        records.append(dict(name=name,command_sha256=command_hash(argv,directory),log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),exit_code=result.returncode))
        print(name, 'passed' if result.returncode==0 else 'FAILED',flush=True)
        if result.returncode:
            raise RuntimeError('local batch failed; no receipt issued')
    if git('status','--porcelain') or git('rev-parse','HEAD') != source:
        raise ValueError('source changed during verification')
    proof=dict(receipt_version=7,source_sha=source,definition_sha256=hashlib.sha256((ROOT/'scripts/local_hospital_test_manifest.json').read_bytes()).hexdigest(),
        baseline=json.loads(args.baseline.read_text()),changed_files=git('diff','--name-only',BASE,source).splitlines(),commands=records,
        clean_before=True,clean_after=True,staging_only=True)
    validate(proof,source,proof['definition_sha256'])
    (args.output/'receipt.json').write_text(json.dumps(sign(proof),indent=2)+'\n')
    print('Signed staging-only shipping proof issued; no backend/full-suite rerun claimed')

if __name__=='__main__':
    main()
