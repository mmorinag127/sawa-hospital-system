"""Owner-approved deployment-only supplement; never claims full tests reran."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from local_receipt_signing import sign, verify_signed

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    'scripts/production_online_release.py', 'scripts/online_projection_guard.py',
    'scripts/prepare_online_recovery_source.py', 'scripts/run_prod_schema_adoption.py',
    'scripts/run_stg_schema_adoption.py', 'backend/alembic/env.py',
    'backend/src/maintenance/online_recovery_schema.py',
    'backend/tests/unit/test_online_production_release.py',
    'backend/tests/integration/test_online_projection_upgrade.py',
    '.github/workflows/deploy-prod.yml', 'scripts/deploy_worker_prod_with_checks.sh',
    'scripts/deploy_web_with_checks.sh',
    'scripts/local_hospital_test_runner.py', 'scripts/production_deploy_supplement.py',
    'scripts/production_maintenance.py', 'scripts/resume_migrated_production.py',
    'scripts/registry_image_digest.py',
    'backend/tests/unit/test_production_maintenance.py',
    'backend/tests/unit/test_migrated_production_continuation.py',
    'backend/tests/unit/test_registry_platform_digest.py',
    'backend/tests/unit/test_production_deploy_supplement.py',
}
TESTS = [
    'backend/tests/unit/test_online_production_release.py',
    'backend/tests/integration/test_online_projection_upgrade.py',
    'scripts/test_local_hospital_test_runner.py',
    'backend/tests/unit/test_registry_image_digest.py',
    'backend/tests/unit/test_registry_platform_digest.py',
    'backend/tests/unit/test_production_maintenance.py',
    'backend/tests/unit/test_migrated_production_continuation.py',
    'backend/tests/unit/test_production_migration_import_target.py',
    'backend/tests/unit/test_production_agent_secret.py',
    'backend/tests/unit/test_production_deploy_supplement.py',
]
COMMAND = ['-m', 'pytest', *TESTS, '-q']
COMMAND_HASH = hashlib.sha256(json.dumps(COMMAND).encode()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def validate(proof, expected_sha, definition):
    from local_hospital_test_runner import validate_receipt
    if set(proof) != {'receipt_version', 'source_sha', 'clean_before', 'clean_after',
                      'base_receipt', 'changed_files', 'regression'}:
        raise ValueError('deployment supplement fields mismatch')
    base = verify_signed(proof['base_receipt'])
    if base.get('receipt_version') not in (1, 2, 6):
        raise ValueError('original full receipt required; chained supplements forbidden')
    old = base['source_sha'] if base.get('receipt_version') == 6 else base['source_sha_end']
    validate_receipt(base, old, definition, require_full=True)
    if proof['source_sha'] != expected_sha or proof['clean_before'] is not True or proof['clean_after'] is not True:
        raise ValueError('supplement source or clean state mismatch')
    subprocess.run(['git', 'merge-base', '--is-ancestor', old, expected_sha], cwd=ROOT, check=True, capture_output=True)
    changed = git('diff', '--name-only', old, expected_sha).splitlines()
    if not changed or set(changed) - ALLOWED or changed != proof['changed_files']:
        raise ValueError('business/dependency/migration or unreviewed source changed')
    regression = proof['regression']
    if (set(regression) != {'command_sha256', 'log_sha256', 'exit_code'}
            or regression['command_sha256'] != COMMAND_HASH
            or type(regression['exit_code']) is not int or regression['exit_code'] != 0
            or not isinstance(regression['log_sha256'], str)
            or len(regression['log_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in regression['log_sha256'])):
        raise ValueError('successful exact deployment regression required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') or git('status', '--porcelain'):
        raise ValueError('clean local source required')
    sha = git('rev-parse', 'HEAD')
    envelope = json.loads(args.base.read_text())
    base = verify_signed(envelope)
    args.output.mkdir(parents=True, exist_ok=False)
    log = args.output / 'deployment-regression.log'
    env = dict(os.environ, DB_URI='sqlite:///:memory:')
    with log.open('wb') as stream:
        result = subprocess.run([sys.executable, *COMMAND], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode or git('status', '--porcelain') or git('rev-parse', 'HEAD') != sha:
        raise ValueError('deployment regression failed or source changed; no receipt issued')
    proof = {'receipt_version': 4, 'source_sha': sha, 'clean_before': True, 'clean_after': True,
             'base_receipt': envelope, 'changed_files': git('diff', '--name-only',
                 base['source_sha'] if base.get('receipt_version') == 6 else base['source_sha_end'], sha).splitlines(),
             'regression': {'command_sha256': COMMAND_HASH, 'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(), 'exit_code': 0}}
    definition = hashlib.sha256((ROOT / 'scripts/local_hospital_test_manifest.json').read_bytes()).hexdigest()
    validate(proof, sha, definition)
    (args.output / 'receipt.json').write_text(json.dumps(sign(proof), indent=2) + '\n')
    print('Signed deployment-only supplement issued; full results retained at original source SHA')


if __name__ == '__main__':
    main()
