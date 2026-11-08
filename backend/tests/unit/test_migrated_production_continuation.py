import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import resume_migrated_production as subject


@pytest.mark.parametrize('failure', [None, 'actor', 'runtime', 'dependency', 'runtime-full', 'runtime-receipt-invalid', 'bootstrap', 'backup', 'uid', 'writeable', 'earlier-frontend', 'nested', 'frontend-before-candidate', 'frontend-after-candidate', 'retired-candidate', 'candidate-uid', 'candidate-digest', 'candidate-route', 'candidate-tag', 'candidate-opened', 'candidate-not-held', 'candidate-writers-active', 'candidate-hold-failed', 'candidate-checks-passed', 'candidate-reopened'])
def test_migrated_checkpoint_preserves_originals_and_blocks_drift(monkeypatch, tmp_path, failure):
    image = 'registry/backend@sha256:' + 'c' * 64
    e = {'GITHUB_RUN_ID': '222', 'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_SHA': 'b' * 40,
         'GITHUB_REF': 'refs/heads/release/prod-new'}
    for key, value in e.items(): monkeypatch.setenv(key, value)
    path = tmp_path / 'hospital-maintenance.json'
    monkeypatch.setattr(subject, 'context', lambda: path)
    original = {'revision': 'old', 'uid': 'old-uid', 'images': ['old-image']}
    record = {'run': '111', 'sha': 'a' * 40, 'services': {n: copy.deepcopy(original) for n in ('web-prod', 'worker-prod', 'ocr-pipeline-prod')},
              'database_drained': True, 'portal_bootstrap_complete': failure != 'bootstrap', 'reopened': False}
    candidate_case = failure == 'retired-candidate' or str(failure).startswith('candidate-')
    if candidate_case:
        record.update(candidate_revision='candidate', candidate_uid='candidate-uid', candidate_images=[image],
                      candidate_tag='verify-111', candidate_url='https://candidate.invalid', maintenance_revision='maintenance',
                      held_after_failure=failure != 'candidate-not-held', failure_hold_unconfirmed=[])
        if failure == 'candidate-opened': record['writers_verified_open'] = True
        if failure == 'candidate-hold-failed': record['failure_hold_unconfirmed'] = ['writer']
        if failure == 'candidate-checks-passed': record['candidate_checks_passed'] = True
        if failure == 'candidate-reopened': record['reopened'] = True
    rollback = {'run': '111', 'sha': 'a' * 40, 'services': copy.deepcopy(record['services'])}
    backup = {'id': '77', 'instance': 'orders-prod', 'status': 'SUCCESSFUL', 'description': 'release-' + 'a' * 40 + '-111'}
    run = {'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'failure',
           'path': '.github/workflows/deploy-prod.yml', 'actor': {'login': 'other' if failure == 'actor' else 'owner'},
           'triggering_actor': {'login': 'owner'}, 'head_branch': 'release/prod-old', 'head_sha': 'a' * 40, 'run_attempt': 1}
    if failure == 'nested':
        record['migrated_origin'] = {**copy.deepcopy(record), 'run': '99', 'sha': 'c' * 40}
        backup['description'] = 'release-' + 'c' * 40 + '-99'
        record['migrated_recovery_point'] = copy.deepcopy(backup)
    if failure == 'frontend-after-candidate': record['candidate_revision'] = 'unverified-candidate'
    def output(command, **kwargs):
        if command[0] == 'git':
            return {'runtime': 'backend/src/changed.py', 'dependency': 'backend/uv.lock',
                    'runtime-full': 'backend/src/services/output_builder.py',
                    'runtime-receipt-invalid': 'backend/src/services/output_builder.py'}.get(failure, '')
        if '/jobs?' in command[-1]:
            front = failure in {'frontend-before-candidate', 'frontend-after-candidate'} or candidate_case
            page = {'jobs': [{'name': n, 'conclusion': c} for n, c in (
                ('prod-db-bootstrap-gate', 'success'), ('deploy-backend', 'success' if front else 'failure'), ('deploy-frontend', 'failure' if front else 'skipped'))]}
            if '--slurp' in command:
                if failure == 'earlier-frontend': page['jobs'].append({'name': 'deploy-frontend', 'conclusion': 'success'})
                return json.dumps([page])
            return json.dumps(page)
        return json.dumps(run)
    monkeypatch.setattr(subject.subprocess, 'check_output', output)
    def execute(command, **kwargs):
        if command[0] == 'gh':
            root = Path(command[command.index('--dir') + 1])
            for name, value in [('hospital-maintenance.json', record), ('hospital-rollback.json', rollback), ('orders-prod-recovery-point.json', backup)]:
                (root / name).write_text(json.dumps(value))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subject.subprocess, 'run', execute)
    def cloud(*args):
        assert args[2] == 'describe'
        if args[0] == 'sql': return {**backup, 'status': 'FAILED' if failure == 'backup' else 'SUCCESSFUL'}
        if args[1] == 'services':
            if args[3] == 'web-prod' and candidate_case:
                traffic = [{'percent': 100, 'revisionName': 'unrelated' if failure == 'candidate-route' else 'maintenance'}]
                if failure == 'candidate-tag': traffic.append({'tag': 'verify-111', 'revisionName': 'candidate'})
                return {'status': {'traffic': traffic}}
            return {'status': {'traffic': [{'percent': 100, 'revisionName': 'new'}]}}
        if args[3] == 'old': return {'metadata': {'uid': 'old-uid'}, 'spec': {'containers': [{'image': 'old-image'}]}}
        if args[3] == 'candidate': return {'metadata': {'uid': 'changed' if failure == 'candidate-uid' else 'candidate-uid'}, 'spec': {'containers': [{'image': 'other-image' if failure == 'candidate-digest' else image}]}}
        return {'metadata': {'uid': 'changed' if failure == 'uid' else 'new-uid'}, 'spec': {'containers': [{'image': image, 'env': [] if failure == 'writeable' else [{'name': 'SAWA_RELEASE_READ_ONLY', 'value': '1'}]}]}}
    monkeypatch.setattr(subject, 'cloud', cloud)
    mutations = []
    monkeypatch.setattr(subject, 'verify_display', lambda: mutations.append('display'))
    def stop(targets):
        assert targets == {'worker-prod': 'new', 'ocr-pipeline-prod': 'new'}
        mutations.append('stop')
    monkeypatch.setattr(subject, 'stop_writers', stop)
    def assert_stopped():
        if failure == 'candidate-writers-active': raise RuntimeError('writer is not stopped')
    monkeypatch.setattr(subject, 'assert_stopped', assert_stopped)
    verified = []
    def verify_receipt(sha):
        assert sha == e['GITHUB_SHA']
        if failure == 'runtime-receipt-invalid': raise ValueError('invalid signed full receipt')
        verified.append(sha)
    monkeypatch.setattr(subject, 'verify_runtime_repair_receipt', verify_receipt)
    proof = {'run': '111', 'writers': {n: {'revision': 'new', 'uid': 'new-uid', 'images': [image]} for n in ('worker-prod', 'ocr-pipeline-prod')}}
    if failure and failure not in {'nested', 'frontend-before-candidate', 'retired-candidate', 'runtime-full'}:
        with pytest.raises((RuntimeError, ValueError)): subject.resume(proof)
        assert not mutations and not path.exists()
    else:
        subject.resume(proof)
        saved = json.loads(path.read_text())
        assert saved['run'] == '222' and saved['services'] == record['services']
        assert saved['migrated_origin'] == record and saved['migrated_recovery_point'] == backup
        if candidate_case:
            assert not saved.get('candidate_revision')
            assert saved['migrated_origin']['candidate_uid'] == 'candidate-uid'
        assert mutations == ['display', 'stop']
    assert verified == ([e['GITHUB_SHA']] if failure == 'runtime-full' else [])


@pytest.mark.parametrize('version', [1, 2, 4, 5])
def test_runtime_repair_requires_original_full_receipt(monkeypatch, version):
    import local_receipt_signing
    import local_hospital_test_runner
    monkeypatch.setenv('LOCAL_TEST_RECEIPT', '{}')
    proof = {'receipt_version': version}
    monkeypatch.setattr(local_receipt_signing, 'verify_signed', lambda envelope: proof)
    validated = []
    def validate(actual, sha, definition, *, require_full):
        assert actual == proof and sha == 'a' * 40 and len(definition) == 64 and require_full is True
        validated.append(True)
    monkeypatch.setattr(local_hospital_test_runner, 'validate_receipt', validate)
    if version == 4:
        with pytest.raises(RuntimeError, match='exhaustive source-bound verification'):
            subject.verify_runtime_repair_receipt('a' * 40)
        assert not validated
    else:
        subject.verify_runtime_repair_receipt('a' * 40)
        assert validated == [True]
