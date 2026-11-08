"""Preserve a completed failed full run and verify deployment-only corrections."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from local_receipt_signing import sign, verify_signed
from production_deploy_supplement import ROOT, ALLOWED as DEPLOY_ALLOWED, TESTS as DEPLOY_TESTS, git

FAILED_MODULES = {
    'backend/tests/unit/test_registry_image_digest.py',
    'backend/tests/unit/test_registry_platform_digest.py',
    'backend/tests/unit/test_stg_web_image_recovery.py',
}
TESTS = list(dict.fromkeys([*DEPLOY_TESTS, *sorted(FAILED_MODULES),
    'backend/tests/unit/test_hospital_delivery_activation.py',
    'backend/tests/unit/test_live_label_verification_privacy.py',
    'backend/tests/unit/test_production_corrective_receipt.py']))
ALLOWED = DEPLOY_ALLOWED | {
    '.github/workflows/deploy-stg.yml',
    'scripts/verify_live_breakfast_labels.py',
    'backend/tests/unit/test_live_label_verification_privacy.py',
    'scripts/production_corrective_receipt.py',
    'backend/tests/unit/test_production_corrective_receipt.py',
    'backend/tests/unit/test_stg_web_image_recovery.py',
}
COMMAND = ['-m', 'pytest', *TESTS, '-q']
COMMAND_HASH = hashlib.sha256(json.dumps(COMMAND).encode()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def validate_baseline(base, definition):
    require(set(base) == {'failed_run', 'failed_run_sha256', 'backend', 'full_run_success'}, 'baseline fields mismatch')
    require(base['full_run_success'] is False and valid_hash(base['failed_run_sha256']), 'failed full run must remain failed')
    run, backend = base['failed_run'], base['backend']
    require(set(run) == {'receipt_version', 'owner_attestation', 'source_sha_start', 'source_sha_end',
                        'clean_before', 'clean_after', 'definition_sha256', 'commands'}, 'original failed run required')
    source = run['source_sha_start']
    require(isinstance(source, str) and re.fullmatch('[a-f0-9]{40}', source) is not None
            and source == run['source_sha_end'] and type(run['receipt_version']) is int and run['receipt_version'] == 1
            and run['owner_attestation'] == 'manual' and run['clean_before'] is True
            and run['clean_after'] is True and run['definition_sha256'] == definition, 'unstable original full source')
    manifest = json.loads((ROOT / 'scripts/local_hospital_test_manifest.json').read_text())['commands']
    require(isinstance(run['commands'], list) and len(run['commands']) == len(manifest), 'incomplete original batch')
    for actual, expected in zip(run['commands'], manifest, strict=True):
        require(set(actual) == {'name', 'command_sha256', 'log_sha256', 'exit_code'}
                and actual['name'] == expected['name']
                and actual['command_sha256'] == sha(expected['command'].encode())
                and valid_hash(actual['log_sha256']) and type(actual['exit_code']) is int
                and actual['exit_code'] == (1 if actual['name'] == 'backend-tests' else 0),
                'only the completed backend deployment checks may fail')
    require(set(backend) == {'source_sha', 'full', 'selected', 'executed', 'completed', 'passed',
                            'skipped', 'failed', 'reused', 'report_sha256'}, 'backend proof fields mismatch')
    counts = [backend[key] for key in ('selected', 'executed', 'completed', 'passed', 'skipped', 'reused')]
    require(all(type(value) is int and value >= 0 for value in counts)
            and backend['source_sha'] == source and backend['full'] is True and backend['reused'] == 0
            and backend['selected'] > 0 and backend['selected'] == backend['executed'] == backend['completed']
            and valid_hash(backend['report_sha256']), 'exhaustive original backend execution required')
    failed = backend['failed']
    require(isinstance(failed, list) and failed and all(isinstance(node, str) for node in failed)
            and len(failed) == len(set(failed)), 'explicit failed checks required')
    require(backend['passed'] + backend['skipped'] + len(failed) == backend['selected'], 'backend coverage partition mismatch')
    for node in failed:
        module = node.split('::', 1)[0]
        module = 'backend/' + module if module.startswith('tests/') else module
        require('::' in node and module in FAILED_MODULES, 'business or unreviewed test failure cannot be supplemented')
    return source


def read_baseline(directory, definition):
    raw = (directory / 'failed-run.json').read_bytes()
    run = json.loads(raw)
    for index, record in enumerate(run['commands']):
        require(sha((directory / f"{index:02d}-{record['name']}.log").read_bytes()) == record['log_sha256'],
                'original command log changed')
    report_raw = (directory / 'backend-tests/selection.json').read_bytes()
    report = json.loads(report_raw)
    names = ('selected', 'executed', 'completed', 'passed', 'skipped', 'failed', 'reused')
    for name in names:
        nodes = report[name]
        require(isinstance(nodes, list) and all(isinstance(node, str) for node in nodes)
                and len(nodes) == len(set(nodes)), 'invalid original test identities')
    planned = set(report['selected'])
    groups = [set(report[key]) for key in ('passed', 'skipped', 'failed')]
    require(planned == set(report['executed']) == set(report['completed'])
            and set.union(*groups) == planned and not any(groups[i] & groups[j] for i in range(3) for j in range(i))
            and report['not_started'] == [] and report['incomplete'] == []
            and type(report['exitstatus']) is int and report['exitstatus'] == 1, 'original full execution did not finish')
    backend = {key: len(report[key]) for key in names if key != 'failed'}
    backend.update(failed=report['failed'], source_sha=report['source_sha'], full=report['full'], report_sha256=sha(report_raw))
    base = {'failed_run': run, 'failed_run_sha256': sha(raw), 'backend': backend, 'full_run_success': False}
    validate_baseline(base, definition)
    return base


def validate(proof, expected_sha, definition):
    require(set(proof) == {'receipt_version', 'source_sha', 'clean_before', 'clean_after',
                           'original_execution', 'changed_files', 'regression'}, 'corrective proof fields mismatch')
    require(proof['receipt_version'] == 5 and proof['source_sha'] == expected_sha
            and proof['clean_before'] is True and proof['clean_after'] is True, 'corrective source mismatch')
    source = validate_baseline(verify_signed(proof['original_execution']), definition)
    subprocess.run(['git', 'merge-base', '--is-ancestor', source, expected_sha], cwd=ROOT, check=True, capture_output=True)
    changed = git('diff', '--name-only', source, expected_sha).splitlines()
    require(changed and changed == proof['changed_files'] and not set(changed) - ALLOWED,
            'business, dependency, migration or unreviewed source changed')
    result = proof['regression']
    require(set(result) == {'command_sha256', 'log_sha256', 'exit_code'}
            and result['command_sha256'] == COMMAND_HASH and valid_hash(result['log_sha256'])
            and type(result['exit_code']) is int and result['exit_code'] == 0, 'exact corrective regressions required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--failed-batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not os.environ.get('GITHUB_ACTIONS') and not os.environ.get('CI') and not git('status', '--porcelain'), 'clean local source required')
    source = git('rev-parse', 'HEAD')
    definition = sha((ROOT / 'scripts/local_hospital_test_manifest.json').read_bytes())
    base = read_baseline(args.failed_batch, definition)
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.failed_batch / 'failed-run.json', args.output / 'original-failed-run.json')
    log = args.output / 'corrective-regression.log'
    with log.open('wb') as stream:
        result = subprocess.run([sys.executable, *COMMAND], cwd=ROOT,
            env=dict(os.environ, DB_URI='sqlite:///:memory:'), stdout=stream, stderr=subprocess.STDOUT)
    require(result.returncode == 0 and not git('status', '--porcelain') and git('rev-parse', 'HEAD') == source,
            'corrective regressions failed or source changed; no receipt issued')
    proof = {'receipt_version': 5, 'source_sha': source, 'clean_before': True, 'clean_after': True,
             'original_execution': sign(base), 'changed_files': git('diff', '--name-only', base['failed_run']['source_sha_end'], source).splitlines(),
             'regression': {'command_sha256': COMMAND_HASH, 'log_sha256': sha(log.read_bytes()), 'exit_code': 0}}
    validate(proof, source, definition)
    (args.output / 'receipt.json').write_text(json.dumps(sign(proof), indent=2) + '\n')
    print('Signed corrective proof issued; original full failure retained, business source unchanged')


if __name__ == '__main__':
    main()
