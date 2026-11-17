"""Accept the owner's document-only delta over the exact signed staging runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
STAGED = 'fb24db83fef9c8cdc42b388145982abf6ebc4a69'
STAGING_RUN = 43914538923
RUNNER = 'scripts/local_hospital_test_runner.py'
ALLOWED = {
    'docs/numeric-quantity-separation-20261117.md',
    'docs/numeric-quantity-production-20261117.md',
    RUNNER, 'scripts/documented_staging_receipt.py',
    'backend/tests/unit/test_documented_staging_receipt.py',
}
ANCHOR = b"    if isinstance(receipt, dict) and receipt.get('receipt_version') == 11:\n"
INSERTION = (
    b"    if isinstance(receipt, dict) and receipt.get('receipt_version') == 12:\n"
    b"        from documented_staging_receipt import validate\n"
    b"        validate(receipt, expected_sha, expected_definition_hash)\n"
    b"        return\n"
)
COMMAND = ['-m', 'pytest', 'backend/tests/unit/test_documented_staging_receipt.py',
           'backend/tests/unit/test_ocr_reset_release_receipt.py',
           'backend/tests/unit/test_online_production_release.py', '-q', '--junitxml=gate.xml']


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def require_paths(paths):
    if not paths or len(set(paths)) != len(paths) or set(paths) - ALLOWED:
        raise ValueError('Only approved documents and isolated receipt gate files may differ')


def require_dispatcher(original, current):
    if original.count(ANCHOR) != 1 or current != original.replace(ANCHOR, INSERTION + ANCHOR, 1):
        raise ValueError('Existing receipt and production approval checks must remain unchanged')


def checked_paths(source):
    git('merge-base', '--is-ancestor', STAGED, source)
    paths = git('diff', '--name-only', STAGED, source).decode().splitlines()
    require_paths(paths)
    records = git('ls-tree', '-z', '--format=%(objectmode) %(objecttype) %(path)', source, '--', *paths).decode().split('\0')
    records = [record for record in records if record]
    if len(records) != len(paths) or any(not record.startswith('100644 blob ') for record in records):
        raise ValueError('Regular non-executable files required; deletion and symlinks forbidden')
    require_dispatcher(git('show', STAGED + ':' + RUNNER), git('show', source + ':' + RUNNER))
    return paths


def validate(proof, source, definition):
    from local_hospital_test_runner import validate_receipt
    fields = {'receipt_version', 'source_sha', 'definition_sha256', 'staging_receipt',
              'changed_files', 'regression', 'clean_before', 'clean_after', 'full_rerun'}
    if (not isinstance(proof, dict) or set(proof) != fields
            or type(proof['receipt_version']) is not int or proof['receipt_version'] != 12
            or proof['source_sha'] != source or not re.fullmatch('[a-f0-9]{40}', source)
            or proof['definition_sha256'] != definition
            or proof['clean_before'] is not True or proof['clean_after'] is not True
            or proof['full_rerun'] is not False):
        raise ValueError('Exact clean documented-runtime proof required')
    staged = verify_signed(proof['staging_receipt'])
    if (staged.get('receipt_version') != 11 or staged.get('source_sha') != STAGED
            or staged.get('staging_run') != STAGING_RUN):
        raise ValueError('Exact signed successful staging source required; recursive proofs forbidden')
    validate_receipt(staged, STAGED, definition, require_full=True)
    if checked_paths(source) != proof['changed_files']:
        raise ValueError('Runtime delta or source inventory changed')
    record = proof['regression']
    if (not isinstance(record, dict)
            or set(record) != {'command_sha256', 'log_sha256', 'exit_code', 'junit'}
            or record['command_sha256'] != digest(json.dumps(COMMAND).encode())
            or type(record['exit_code']) is not int or record['exit_code'] != 0
            or not isinstance(record['log_sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', record['log_sha256'])):
        raise ValueError('Executed receipt gate regression required')
    junit = record['junit']
    if (not isinstance(junit, dict) or set(junit) != {'tests', 'failures', 'errors', 'skipped', 'sha256'}
            or type(junit['tests']) is not int or junit['tests'] < 20
            or any(type(junit[k]) is not int or junit[k] != 0 for k in ('failures', 'errors', 'skipped'))
            or not isinstance(junit['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', junit['sha256'])):
        raise ValueError('Complete gate regression without skipped tests required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--staging-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI') or git('status', '--porcelain'):
        raise ValueError('Clean fixed local source required')
    source = git('rev-parse', 'HEAD').decode().strip()
    paths = checked_paths(source)
    args.output.mkdir(parents=True, exist_ok=False)
    log = args.output / 'gate.log'
    junit_path = (args.output / 'gate.xml').resolve()
    with log.open('wb') as stream:
        result = subprocess.run([sys.executable, *COMMAND[:-1], '--junitxml=' + str(junit_path)], cwd=ROOT,
            env=dict(os.environ, DB_URI='sqlite:///:memory:', PYTHONPATH=str(ROOT/'backend')),
            stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise ValueError('Gate regression failed; no success receipt issued')
    suites = list(ET.parse(junit_path).getroot().iter('testsuite'))
    counts = {key:sum(int(s.get(key, '0')) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    counts['sha256'] = digest(junit_path.read_bytes())
    proof = dict(receipt_version=12, source_sha=source,
        definition_sha256=digest((ROOT/'scripts/local_hospital_test_manifest.json').read_bytes()),
        staging_receipt=json.loads(args.staging_receipt.read_text()), changed_files=paths,
        regression=dict(command_sha256=digest(json.dumps(COMMAND).encode()), log_sha256=digest(log.read_bytes()),
            exit_code=result.returncode, junit=counts), clean_before=True, clean_after=True, full_rerun=False)
    validate(proof, source, proof['definition_sha256'])
    if git('status', '--porcelain') or git('rev-parse', 'HEAD').decode().strip() != source:
        raise ValueError('Source changed during verification')
    (args.output/'receipt.json').write_text(json.dumps(sign(proof), indent=2)+'\n')
    print('Signed document-only production proof issued; no runtime/full regression rerun claimed')


if __name__ == '__main__':
    main()
