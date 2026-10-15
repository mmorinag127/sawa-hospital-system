import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('producer_deploy', ROOT / 'scripts/stg_ocr_producer_deploy.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = '1' * 40
IMAGE = mod.IMAGE_PREFIX + '@sha256:' + '2' * 64


def exported():
    return {'apiVersion': 'serving.knative.dev/v1', 'kind': 'Service',
            'metadata': {'name': mod.SERVICE, 'namespace': '167795504375',
                         'annotations': {'run.googleapis.com/ingress': 'all'}},
            'spec': {'traffic': [{'latestRevision': True, 'percent': 100}], 'template': {
                'metadata': {'name': 'ocr-pipeline-stg-00012-rlx', 'annotations': {
                    'run.googleapis.com/cpu-throttling': 'false', 'autoscaling.knative.dev/maxScale': '1',
                    'run.googleapis.com/cloudsql-instances': 'sawahospitalsystem:asia-northeast2:orders-stg'}},
                'spec': {'serviceAccountName': mod.RUNTIME, 'timeoutSeconds': 3600, 'containerConcurrency': 1,
                         'containers': [{'image': 'old', 'resources': {'limits': {'cpu': '2', 'memory': '8Gi'}},
                                         'env': [{'name': 'PRIVATE', 'value': 'SECRET'},
                                                 {'name': 'REF', 'valueFrom': {'secretKeyRef': {'name': 'ref', 'key': '2'}}}],
                                         'startupProbe': {'tcpSocket': {'port': 8080}, 'timeoutSeconds': 240}}]}}}}


def ready(name):
    return {'status': {'latestReadyRevisionName': name, 'latestCreatedRevisionName': name,
                       'conditions': [{'type': 'Ready', 'status': 'True'}],
                       'traffic': [{'revisionName': name, 'latestRevision': True, 'percent': 100}]}}


@pytest.fixture
def environment(monkeypatch, tmp_path):
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/develop',
                       'GITHUB_REPOSITORY': 'mmorinag127/sawa-hospital-system', 'PROJECT_ID': mod.PROJECT,
                       'REGION': mod.REGION, 'GITHUB_SHA': SHA, 'RUNNER_TEMP': str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT', raising=False)


def test_image_only_preserves_every_other_setting():
    before = exported()
    after = mod.replacement(before, IMAGE)
    expected = copy.deepcopy(before)
    del expected['spec']['template']['metadata']['name']
    expected['spec']['template']['spec']['containers'][0]['image'] = IMAGE
    assert after == expected
    assert before == exported()


@pytest.mark.parametrize('target', ['ocr-pipeline-prod', 'worker-stg', 'ocr-pipeline'])
def test_other_service_rejected(target):
    before = exported()
    before['metadata']['name'] = target
    with pytest.raises(mod.Blocked):
        mod.replacement(before, IMAGE)


@pytest.mark.parametrize('key,value', [('GITHUB_REF', 'refs/heads/main'), ('PROJECT_ID', 'other'),
    ('REGION', 'asia-northeast1'), ('GITHUB_ACTIONS', 'false'), ('GITHUB_SHA', '0' * 40)])
def test_wrong_context_rejected_before_command(environment, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(mod, 'command', lambda args: pytest.fail('command executed'))
    with pytest.raises(mod.Blocked):
        mod.context(IMAGE, SHA)


@pytest.mark.parametrize('image', [IMAGE.replace('ocr-pipeline@', 'ocr-pipeline-prod@'),
    IMAGE.replace('sawahospitalsystem', 'other'), mod.IMAGE_PREFIX + ':latest'])
def test_other_image_rejected(environment, monkeypatch, image):
    monkeypatch.setattr(mod, 'command', lambda args: pytest.fail('command executed'))
    with pytest.raises(mod.Blocked):
        mod.context(image, SHA)


@pytest.mark.parametrize('label_sha', [SHA, '0' * 40])
def test_actual_image_source_label(environment, monkeypatch, label_sha):
    def command(args):
        if args[:2] == ['git', 'rev-parse']:
            return SHA
        if args[:2] == ['docker', 'pull']:
            return ''
        assert args == ['docker', 'image', 'inspect', IMAGE]
        return json.dumps([{'RepoDigests': [IMAGE], 'Config': {'Labels': {
            'sawa.git_sha': label_sha, 'sawa.release_branch': 'develop'}}}])
    monkeypatch.setattr(mod, 'command', command)
    monkeypatch.setattr(mod, 'cloud', lambda *args: [{'account': mod.DEPLOY}])
    if label_sha == SHA:
        mod.context(IMAGE, SHA)
    else:
        with pytest.raises(mod.Blocked, match='source mismatch'):
            mod.context(IMAGE, SHA)


@pytest.mark.parametrize('failure', [None, 'revision', 'settings', 'iam', 'digest', 'denied', 'namespace', 'not_ready'])
def test_replace_verification_and_no_iam_repair(environment, monkeypatch, tmp_path, failure):
    monkeypatch.setattr(mod, 'context', lambda *args: None)
    before = exported()
    if failure == 'namespace':
        before['metadata']['namespace'] = 'prod-project'
    current = copy.deepcopy(before)
    calls = []
    descriptions = 0
    replaced = False
    paths = []
    def cloud(*args, output='json'):
        nonlocal descriptions, replaced, current
        calls.append(args)
        if args[:2] == ('projects', 'describe'):
            return {'projectNumber': '167795504375'}
        if args[:3] == ('run', 'services', 'describe'):
            if output == 'export':
                return copy.deepcopy(current)
            descriptions += 1
            name = 'new' if replaced or (failure == 'revision' and descriptions == 2) else 'old'
            result = ready(name)
            if failure == 'not_ready' and replaced:
                result['status']['traffic'][0]['percent'] = 50
            return result
        if args[:3] == ('run', 'services', 'get-iam-policy'):
            return {'bindings': ['changed' if failure == 'iam' and replaced else 'same']}
        if args[:3] == ('run', 'services', 'replace'):
            path = Path(args[3])
            paths.append(path)
            assert path.stat().st_mode & 0o777 == 0o600
            current = json.loads(path.read_text())
            assert current == mod.replacement(before, IMAGE)
            if failure == 'denied':
                raise mod.Blocked('command_failed_no_IAM_repair', phase='gcloud run services replace', returncode=1)
            replaced = True
            if failure == 'settings':
                current['metadata']['annotations']['server-added'] = 'unapproved'
            return {}
        if args[:3] == ('run', 'revisions', 'describe'):
            return {'status': {'imageDigest': 'wrong' if failure == 'digest' else IMAGE}}
        pytest.fail('Unexpected cloud operation')
    monkeypatch.setattr(mod, 'cloud', cloud)
    if failure:
        with pytest.raises(mod.Blocked):
            mod.deploy(IMAGE, SHA)
    else:
        assert mod.deploy(IMAGE, SHA)['ready_revision'] == 'new'
    assert not any('add-iam-policy-binding' in args for args in calls)
    assert all(not path.exists() for path in paths)
    if failure in ('revision', 'namespace'):
        assert not replaced and not paths


def test_safe_failure_output(monkeypatch, capsys):
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 7, 'SECRET', 'SECRET'))
    with pytest.raises(mod.Blocked) as caught:
        mod.command(['gcloud', 'run', 'services', 'replace', '/private/spec.json'])
    monkeypatch.setattr('sys.argv', ['script', '--image', IMAGE, '--source-sha', SHA])
    def fail(*args):
        raise caught.value
    monkeypatch.setattr(mod, 'deploy', fail)
    assert mod.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {'status': 'blocked', 'phase': 'gcloud run services replace',
                      'reason': 'command_failed_no_IAM_repair', 'returncode': 7}


def test_workflow_keeps_graph_and_producer_before_worker():
    workflow = yaml.safe_load((ROOT / '.github/workflows/deploy-stg.yml').read_text())
    jobs = workflow['jobs']
    assert jobs['deploy-backend']['needs'] == ['source-gate', 'build-backend', 'menu-master-migration']
    steps = jobs['deploy-backend']['steps']
    producer = next(i for i, step in enumerate(steps) if 'stg_ocr_producer_deploy.py' in step.get('run', ''))
    worker = next(i for i, step in enumerate(steps) if step.get('name') == 'Deploy worker-stg')
    assert producer < worker
    assert jobs['build-backend']['outputs']['producer_image'] == '${{ steps.producer.outputs.image }}'


def test_deploy_runner_configures_docker_with_existing_wif_before_producer():
    workflow = yaml.safe_load((ROOT / '.github/workflows/deploy-stg.yml').read_text())
    steps = workflow['jobs']['deploy-backend']['steps']
    auth = next(i for i, step in enumerate(steps) if step.get('id') == 'auth-backend')
    setup = next(i for i, step in enumerate(steps) if step.get('uses') == 'google-github-actions/setup-gcloud@v3')
    producer = next(i for i, step in enumerate(steps) if 'stg_ocr_producer_deploy.py' in step.get('run', ''))
    assert auth < setup < producer
    assert steps[auth]['with']['workload_identity_provider'] == '${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_STG }}'
    assert steps[auth]['with']['service_account'] == '${{ secrets.GCP_DEPLOY_SERVICE_ACCOUNT_STG }}'
    commands = steps[producer]['run'].strip().splitlines()
    assert commands == [
        'set -euo pipefail',
        'gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet',
        'uv run --project backend --extra dev --frozen python scripts/stg_ocr_producer_deploy.py --image "$PRODUCER_IMAGE" --source-sha "$GITHUB_SHA"',
    ]
