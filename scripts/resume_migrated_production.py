"""Carry a migrated checkpoint forward without repeating database writes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from production_maintenance import context, cloud, require, save, serving, revision_images, verify_display, stop_writers, assert_stopped


def verify_runtime_repair_receipt(sha):
    from local_receipt_signing import verify_signed
    from local_hospital_test_runner import validate_receipt
    proof = verify_signed(json.loads(os.environ['LOCAL_TEST_RECEIPT']))
    require(proof.get('receipt_version') in (1, 2, 5), 'runtime repair requires exhaustive source-bound verification')
    manifest = Path(__file__).with_name('local_hospital_test_manifest.json')
    validate_receipt(proof, sha, hashlib.sha256(manifest.read_bytes()).hexdigest(), require_full=True)


def resume(proof):
    path = context()
    e = os.environ
    donor = str(proof['run'])
    require(re.fullmatch(r'[1-9][0-9]*', donor) and donor != e['GITHUB_RUN_ID'], 'original migrated run required')
    repo = e['GITHUB_REPOSITORY']
    def api(suffix):
        return json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/actions/runs/{donor}' + suffix], text=True))
    run = api('')
    owner = repo.split('/')[0]
    require(run['event'] == 'workflow_dispatch' and run['status'] == 'completed'
            and run['conclusion'] == 'failure' and run['path'] == '.github/workflows/deploy-prod.yml'
            and run['actor']['login'] == run['triggering_actor']['login'] == owner
            and run['head_branch'].startswith('release/prod-'), 'failed owner production run required')
    old_sha = run['head_sha']
    subprocess.run(['git', 'merge-base', '--is-ancestor', old_sha, e['GITHUB_SHA']], check=True, capture_output=True)
    protected = ('backend/src', 'backend/scripts', 'backend/alembic', 'backend/migrations',
                 'backend/alembic.ini', 'backend/Dockerfile', 'backend/Dockerfile.base',
                 'backend/requirements.txt', 'backend/pyproject.toml', 'backend/uv.lock',
                 'frontend/src', 'frontend/Dockerfile', 'frontend/package.json', 'frontend/package-lock.json',
                 'ocr_pipeline', 'agent_runtime', 'experiments')
    changed = set(subprocess.check_output(['git', 'diff', '--name-only', old_sha, e['GITHUB_SHA'], '--', *protected], text=True).splitlines())
    if changed:
        require(changed <= {'backend/src/services/output_builder.py',
                            'backend/src/services/workflow_display_read_service.py',
                            'backend/src/services/order_workflow_v2_service.py'},
                'unreviewed business runtime or migration changed')
        verify_runtime_repair_receipt(e['GITHUB_SHA'])
    jobs = {j['name']: j for j in api('/jobs?filter=latest&per_page=100')['jobs']}
    frontend_failed = jobs.get('deploy-frontend', {}).get('conclusion') == 'failure'
    require(jobs.get('prod-db-bootstrap-gate', {}).get('conclusion') == 'success'
            and ((jobs.get('deploy-backend', {}).get('conclusion') == 'failure'
                  and jobs.get('deploy-frontend', {}).get('conclusion') == 'skipped')
                 or (jobs.get('deploy-backend', {}).get('conclusion') == 'success' and frontend_failed)), 'unsupported migrated checkpoint')
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
        f'repos/{repo}/actions/runs/{donor}/jobs?filter=all&per_page=100'], text=True))
    frontend_attempts = [j for page in pages for j in page['jobs']
                         if j['name'] == 'deploy-frontend' and j.get('conclusion') != 'skipped']
    require(not frontend_attempts or (frontend_failed and len(frontend_attempts) == 1
            and frontend_attempts[0].get('conclusion') == 'failure'), 'frontend ran in an earlier attempt')
    attempt = str(run['run_attempt'])
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        for name in (f'hospital-maintenance-{donor}-{attempt}', f'hospital-rollback-{donor}-{attempt}',
                     f'hospital-db-recovery-point-{donor}-{attempt}'):
            subprocess.run(['gh', 'run', 'download', donor, '--repo', repo, '--name', name, '--dir', directory], check=True, capture_output=True)
        if frontend_failed:
            final = Path(directory) / 'final'
            final.mkdir()
            subprocess.run(['gh', 'run', 'download', donor, '--repo', repo,
                '--name', f'hospital-maintenance-final-{donor}-{attempt}', '--dir', str(final)], check=True, capture_output=True)
            (Path(directory) / 'hospital-maintenance.json').write_bytes((final / 'hospital-maintenance.json').read_bytes())
        record = json.loads((Path(directory) / 'hospital-maintenance.json').read_text())
        rollback = json.loads((Path(directory) / 'hospital-rollback.json').read_text())
        backup = json.loads((Path(directory) / 'orders-prod-recovery-point.json').read_text())
    require(record['run'] == rollback['run'] == donor and record['sha'] == rollback['sha'] == old_sha
            and record['services'] == rollback['services'] and record['database_drained'] is True
            and record['portal_bootstrap_complete'] is True and not record.get('reopened')
            and not record.get('writers_verified_open'), 'migrated artifact mismatch')
    if record.get('candidate_revision'):
        require(frontend_failed and record.get('held_after_failure') is True
                and record.get('failure_hold_unconfirmed') == []
                and not record.get('candidate_checks_passed'), 'candidate was not safely retired')
        portal = cloud('run', 'services', 'describe', 'web-prod', '--region=asia-northeast2')
        require(serving(portal) == record['maintenance_revision'], 'portal maintenance ownership changed')
        candidate = cloud('run', 'revisions', 'describe', record['candidate_revision'], '--region=asia-northeast2')
        require(candidate['metadata']['uid'] == record['candidate_uid']
                and sorted(revision_images(candidate)) == record['candidate_images'], 'retired candidate identity changed')
        assert_stopped()
    backup_origin = record
    seen = set()
    while backup_origin.get('migrated_origin'):
        require(backup_origin['run'] not in seen, 'cyclic migration origin')
        seen.add(backup_origin['run'])
        require(backup_origin.get('migrated_recovery_point') == backup, 'backup lineage changed')
        backup_origin = backup_origin['migrated_origin']
    require(backup['instance'] == 'orders-prod' and backup['status'] == 'SUCCESSFUL'
            and backup['description'] == f"release-{backup_origin['sha']}-{backup_origin['run']}", 'backup origin mismatch')
    actual = cloud('sql', 'backups', 'describe', str(backup['id']), '--instance=orders-prod')
    require(actual['status'] == 'SUCCESSFUL' and actual['description'] == backup['description'], 'backup unavailable')
    for name, original in record['services'].items():
        old = cloud('run', 'revisions', 'describe', original['revision'], '--region=asia-northeast2')
        require(old['metadata']['uid'] == original['uid'] and revision_images(old) == original['images'], 'original rollback identity changed')
    require(set(proof['writers']) == {'worker-prod', 'ocr-pipeline-prod'}, 'exact writer proof required')
    for name, expected in proof['writers'].items():
        require(expected['images'] and all(re.fullmatch(r'.+@sha256:[a-f0-9]{64}', image) for image in expected['images']), 'immutable writer digest required')
        service = cloud('run', 'services', 'describe', name, '--region=asia-northeast2')
        require(serving(service) == expected['revision'], 'writer traffic changed')
        revision = cloud('run', 'revisions', 'describe', expected['revision'], '--region=asia-northeast2')
        require(revision['metadata']['uid'] == expected['uid'] and revision_images(revision) == expected['images'], 'writer immutable identity changed')
        require(all(any(v.get('name') == 'SAWA_RELEASE_READ_ONLY' and v.get('value') == '1' for v in c.get('env', []))
                    for c in revision['spec']['containers']), 'writer is not read-only')
    origin = copy.deepcopy(record)
    for key in ('candidate_revision', 'candidate_uid', 'candidate_images', 'candidate_tag', 'candidate_url',
                'held_after_failure', 'failure_hold_unconfirmed'):
        record.pop(key, None)
    record.update(run=e['GITHUB_RUN_ID'], sha=e['GITHUB_SHA'], ref=e['GITHUB_REF'],
                  migrated_origin=origin, migrated_recovery_point=backup)
    rollback.update(run=record['run'], sha=record['sha'], ref=record['ref'])
    save(path, record)
    save(path.with_name('hospital-rollback.json'), rollback)
    verify_display()
    stop_writers({name: item['revision'] for name, item in proof['writers'].items()})


if __name__ == '__main__':
    resume(json.loads(os.environ['MIGRATED_CONTINUATION']))
