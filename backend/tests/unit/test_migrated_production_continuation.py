import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import resume_migrated_production as subject


@pytest.mark.parametrize('failure', [None, 'actor', 'runtime', 'bootstrap', 'backup', 'uid', 'writeable', 'earlier-frontend', 'nested'])
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
    rollback = {'run': '111', 'sha': 'a' * 40, 'services': copy.deepcopy(record['services'])}
    backup = {'id': '77', 'instance': 'orders-prod', 'status': 'SUCCESSFUL', 'description': 'release-' + 'a' * 40 + '-111'}
    run = {'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'failure',
           'path': '.github/workflows/deploy-prod.yml', 'actor': {'login': 'other' if failure == 'actor' else 'owner'},
           'triggering_actor': {'login': 'owner'}, 'head_branch': 'release/prod-old', 'head_sha': 'a' * 40, 'run_attempt': 1}
    if failure == 'nested':
        record['migrated_origin'] = {**copy.deepcopy(record), 'run': '99', 'sha': 'c' * 40}
        backup['description'] = 'release-' + 'c' * 40 + '-99'
        record['migrated_recovery_point'] = copy.deepcopy(backup)
    def output(command, **kwargs):
        if command[0] == 'git': return 'backend/src/changed.py' if failure == 'runtime' else ''
        if '/jobs?' in command[-1]:
            page = {'jobs': [{'name': n, 'conclusion': c} for n, c in (
                ('prod-db-bootstrap-gate', 'success'), ('deploy-backend', 'failure'), ('deploy-frontend', 'skipped'))]}
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
        if args[1] == 'services': return {'status': {'traffic': [{'percent': 100, 'revisionName': 'new'}]}}
        if args[3] == 'old': return {'metadata': {'uid': 'old-uid'}, 'spec': {'containers': [{'image': 'old-image'}]}}
        return {'metadata': {'uid': 'changed' if failure == 'uid' else 'new-uid'}, 'spec': {'containers': [{'image': image, 'env': [] if failure == 'writeable' else [{'name': 'SAWA_RELEASE_READ_ONLY', 'value': '1'}]}]}}
    monkeypatch.setattr(subject, 'cloud', cloud)
    mutations = []
    monkeypatch.setattr(subject, 'verify_display', lambda: mutations.append('display'))
    def stop(targets):
        assert targets == {'worker-prod': 'new', 'ocr-pipeline-prod': 'new'}
        mutations.append('stop')
    monkeypatch.setattr(subject, 'stop_writers', stop)
    proof = {'run': '111', 'writers': {n: {'revision': 'new', 'uid': 'new-uid', 'images': [image]} for n in ('worker-prod', 'ocr-pipeline-prod')}}
    if failure and failure != 'nested':
        with pytest.raises(RuntimeError): subject.resume(proof)
        assert not mutations and not path.exists()
    else:
        subject.resume(proof)
        saved = json.loads(path.read_text())
        assert saved['run'] == '222' and saved['services'] == record['services']
        assert saved['migrated_origin'] == record and saved['migrated_recovery_point'] == backup
        assert mutations == ['display', 'stop']
