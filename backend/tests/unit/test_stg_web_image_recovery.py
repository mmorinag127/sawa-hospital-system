"""Offline contracts: no registry, Cloud Run, IAM or DB operations."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
p = importlib.import_module('stg_image_protection')
r = importlib.import_module('recover_stg_web_image')
IMAGE = p.PREFIX + 'frontend@sha256:' + 'a' * 64
OLD_IMAGE = p.PREFIX + 'frontend@sha256:' + 'b' * 64
SHA = 'c' * 40
REV = 'web-stg-00365-n99'


def spec():
    return {'apiVersion': 'serving.knative.dev/v1', 'kind': 'Service',
            'metadata': {'name': 'web-stg', 'namespace': '167795504375', 'labels': {'keep': 'exact'}},
            'spec': {'traffic': [{'latestRevision': True, 'percent': 100}],
                     'template': {'metadata': {'name': REV, 'annotations': {'keep': 'exact'}},
                                  'spec': {'serviceAccountName': 'web-exec-stg@sawahospitalsystem.iam.gserviceaccount.com',
                                           'timeoutSeconds': 300, 'containerConcurrency': 80,
                                           'containers': [{'image': OLD_IMAGE, 'resources': {'limits': {'memory': '1Gi'}},
                                                           'env': [{'name': 'SECRET', 'valueFrom': {'secretKeyRef':
                                                               {'name': 'existing-secret', 'key': 'latest'}}}]}]}}}}


def test_replacement_changes_only_image_and_server_revision_name():
    before = spec()
    result = r.replacement(before, IMAGE, '167795504375')
    expected = deepcopy(before)
    del expected['spec']['template']['metadata']['name']
    expected['spec']['template']['spec']['containers'][0]['image'] = IMAGE
    assert result == expected
    assert before == spec()


@pytest.mark.parametrize('path,value', [
    (('metadata', 'name'), 'web-prod'), (('metadata', 'namespace'), 'other'),
    (('spec', 'traffic'), [{'revisionName': REV, 'percent': 100}]),
    (('spec', 'template', 'spec', 'serviceAccountName'), ''),
    (('spec', 'template', 'spec', 'containers'), []),
])
def test_unknown_target_identity_or_traffic_stops(path, value):
    before = spec()
    node = before
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(r.Blocked):
        r.replacement(before, IMAGE, '167795504375')


@pytest.mark.parametrize('image', [IMAGE.replace('/frontend@', '/backend@'),
                                   IMAGE.replace('sawahospitalsystem', 'other-project'),
                                   p.PREFIX + 'frontend:latest', ''])
def test_nonimmutable_or_other_source_stops(image):
    with pytest.raises(r.Blocked):
        r.replacement(spec(), image, '167795504375')


def repository():
    return {'name': p.REPOSITORY, 'format': 'DOCKER', 'cleanupPolicyDryRun': True,
            'cleanupPolicies': {'existing-delete': {'id': 'existing-delete', 'action': 'DELETE',
                                                  'condition': {'olderThan': '86400s'}}}}


def test_keep_merge_is_additive_and_stg_tag_only():
    before = repository()
    result = p.merge_policy(before)
    assert result['existing-delete'] == before['cleanupPolicies']['existing-delete']
    assert result[p.POLICY_ID] == p.POLICY
    assert result[p.POLICY_ID]['condition'] == {'tagState': 'TAGGED', 'tagPrefixes': ['stg-protected-']}
    assert before == repository()


def test_conflicting_keep_is_not_overwritten():
    before = repository()
    before['cleanupPolicies'][p.POLICY_ID] = {'action': 'DELETE'}
    with pytest.raises(r.Blocked, match='keep_policy_conflict'):
        p.merge_policy(before)


def test_policy_update_keeps_dry_run_and_existing_policy(monkeypatch):
    before = repository()
    after = deepcopy(before)
    after['cleanupPolicies'] = p.merge_policy(before)
    request = Mock(side_effect=[before, before, after, after])
    monkeypatch.setattr(p, 'repository_request', request)
    p.ensure_policy()
    assert request.call_args_list[2].args == ('PATCH', after['cleanupPolicies'])


def test_policy_race_stops_before_write(monkeypatch):
    before = repository()
    changed = deepcopy(before)
    changed['cleanupPolicies']['external'] = {'action': 'KEEP'}
    request = Mock(side_effect=[before, changed])
    monkeypatch.setattr(p, 'repository_request', request)
    with pytest.raises(r.Blocked, match='changed_during_capture'):
        p.ensure_policy()
    assert request.call_count == 2


def test_tag_exact_digest_no_delete_or_iam(monkeypatch):
    cloud = Mock(return_value={'image_summary': {'digest': 'sha256:' + 'a' * 64}})
    command = Mock(return_value='')
    monkeypatch.setattr(p, 'cloud', cloud)
    monkeypatch.setattr(p, 'command', command)
    p.protect_image(IMAGE, 'frontend')
    tag = p.PREFIX + 'frontend:stg-protected-' + 'a' * 64
    command.assert_called_once_with(['gcloud', 'artifacts', 'docker', 'tags', 'add', IMAGE, tag,
                                     f'--project={p.PROJECT}', '--quiet'])
    assert all('delete' not in c.args and 'iam' not in c.args for c in cloud.call_args_list)


@pytest.fixture
def recovery(monkeypatch):
    monkeypatch.setenv('GITHUB_SHA', SHA)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('RECOVERY_CONFIRMATION', r.CONFIRMATION)
    for name in ('require_context', 'verify_candidate_database', 'ensure_policy', 'protect_image',
                 'runtime_preflight', 'protect_current'):
        monkeypatch.setattr(r, name, Mock())
    monkeypatch.setattr(r, '_access_token', lambda: 'not-a-real-token')
    monkeypatch.setattr(r, 'resolve_runtime', Mock(return_value='same-verified-config'))
    monkeypatch.setattr(r, 'image_config', Mock(return_value={'Labels': {'sawa.git_sha': SHA}}))
    initial = {'metadata': {'generation': 1}, 'status': {'observedGeneration': 1,
               'url': 'https://web-stg-avlnzjjrca-dt.a.run.app'}}
    monkeypatch.setattr(r, 'service', Mock(return_value=initial))
    monkeypatch.setattr(r, 'ready_revision', Mock(side_effect=[REV, REV, 'web-stg-00366-test']))
    desired = r.replacement(spec(), IMAGE, '167795504375')
    monkeypatch.setattr(r, 'exported', Mock(side_effect=[spec(), spec(), desired]))
    monkeypatch.setattr(r, 'iam', Mock(return_value={'etag': 'known', 'bindings': []}))
    replace_seen = []

    def cloud(*args, **kwargs):
        if args[:3] == ('run', 'revisions', 'describe'):
            return {'spec': spec()['spec']['template']['spec'],
                    'status': {'imageDigest': OLD_IMAGE if args[3] == REV else IMAGE}}
        if args[:2] == ('projects', 'describe'):
            return {'projectNumber': '167795504375'}
        if args[:3] == ('run', 'services', 'replace'):
            assert r.verify_candidate_database.call_count == 1
            assert r.protect_image.call_count == 1
            assert r.runtime_preflight.call_count == 0
            path = Path(args[3])
            assert path.stat().st_mode & 0o777 == 0o600
            replace_seen.append(json.loads(path.read_text()))
            return {}
        raise AssertionError(args)
    monkeypatch.setattr(r, 'cloud', cloud)
    return replace_seen


def test_recovery_checks_db_before_and_after_and_preserves_spec(recovery):
    result = r.recover(IMAGE, REV, OLD_IMAGE)
    assert result['status'] == 'verified'
    assert recovery == [r.replacement(spec(), IMAGE, '167795504375')]
    r.runtime_preflight.assert_called_once_with()
    r.protect_current.assert_called_once_with()


@pytest.mark.parametrize('gate', ['verify_candidate_database', 'ensure_policy', 'protect_image'])
def test_pre_replace_failure_never_deploys(recovery, monkeypatch, gate):
    monkeypatch.setattr(r, gate, Mock(side_effect=r.Blocked('blocked')))
    with pytest.raises(r.Blocked):
        r.recover(IMAGE, REV, OLD_IMAGE)
    assert recovery == []
    r.runtime_preflight.assert_not_called()


def test_source_mismatch_never_deploys(recovery):
    r.image_config.return_value = {'Labels': {'sawa.git_sha': 'd' * 40}}
    with pytest.raises(r.Blocked, match='source_mismatch'):
        r.recover(IMAGE, REV, OLD_IMAGE)
    assert recovery == []


def test_post_db_failure_is_not_success(recovery):
    r.runtime_preflight.side_effect = RuntimeError('secret must not escape main')
    with pytest.raises(RuntimeError):
        r.recover(IMAGE, REV, OLD_IMAGE)
    assert len(recovery) == 1
    r.protect_current.assert_not_called()


def test_dispatch_confirmation_required(recovery, monkeypatch):
    monkeypatch.setenv('RECOVERY_CONFIRMATION', '')
    with pytest.raises(r.Blocked, match='confirmation'):
        r.recover(IMAGE, REV, OLD_IMAGE)
    assert recovery == []


def test_workflows_preserve_normal_schema_gate_and_recovery_is_web_only():
    normal = yaml.safe_load((ROOT / '.github/workflows/deploy-stg.yml').read_text())
    recovery = normal['jobs']['recover-web']
    assert normal['concurrency'] == {'group': 'deploy-stg', 'cancel-in-progress': False}
    assert 'concurrency' not in recovery
    assert recovery['needs'] == 'source-gate'
    assert recovery['if'] == ("github.ref == 'refs/heads/develop' && "
                              "github.event_name == 'workflow_dispatch' && "
                              "inputs.mode == 'recover-web-image'")
    assert 'uses' not in recovery
    assert recovery['permissions'] == {'contents': 'read', 'id-token': 'write'}
    assert 'menu-master-migration' in normal['jobs']['deploy-frontend']['needs']
    text = yaml.safe_dump(recovery)
    assert 'recover_stg_web_image.py' in text
    assert 'run_stg_schema_adoption' not in text and 'deploy_worker' not in text
    assert 'upload-artifact' not in text and 'set-iam-policy' not in text
    assert 'add-iam-policy-binding' not in text and 'remove-iam-policy-binding' not in text
    steps = normal['jobs']['deploy-frontend']['steps']
    uv = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('astral-sh/setup-uv'))
    deploy = next(i for i, step in enumerate(steps) if step.get('name') == 'Deploy web-stg')
    assert uv < deploy
    run = steps[deploy]['run']
    assert run.index('stg_image_protection.py') < run.index('deploy_web_with_checks.sh')


@pytest.mark.parametrize('variable,value', [('GITHUB_REF', 'refs/heads/main'),
    ('GITHUB_ACTIONS', 'false'), ('PROJECT_ID', 'other'), ('REGION', 'us-central1'),
    ('GITHUB_REPOSITORY', 'other/repository')])
def test_context_rejects_before_any_cloud_command(monkeypatch, variable, value):
    for key, expected in {'GITHUB_REF': 'refs/heads/develop', 'GITHUB_ACTIONS': 'true',
                          'PROJECT_ID': p.PROJECT, 'REGION': p.REGION,
                          'GITHUB_REPOSITORY': 'mmorinag127/sawa-hospital-system'}.items():
        monkeypatch.setenv(key, expected)
    monkeypatch.setenv(variable, value)
    command = Mock(side_effect=AssertionError('must not execute'))
    monkeypatch.setattr(p, 'command', command)
    with pytest.raises(r.Blocked, match='staging_actions_context'):
        p.require_context()
    command.assert_not_called()


def test_candidate_database_readonly_validation_and_rollback(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    config = SimpleNamespace(instance_connection_name='orders-stg', db_name='orders',
                             db_user='orders_app', db_password='secret-not-logged')
    monkeypatch.setattr(r, 'resolve_runtime', Mock(return_value=config))
    monkeypatch.setattr(r, 'load_service_db_config', Mock(return_value=config))
    monkeypatch.setattr(r, 'cloud_sql_proxy', lambda instance: nullcontext())
    conn = Mock()
    engine = Mock()
    engine.connect.return_value = nullcontext(conn)
    monkeypatch.setattr(r, 'create_engine', Mock(return_value=engine))
    monkeypatch.setattr(r, 'validate_catalog', Mock(side_effect=RuntimeError('catalog-invalid')))
    with pytest.raises(RuntimeError):
        r.verify_candidate_database({}, {})
    r.validate_catalog.assert_called_once_with(conn)
    conn.begin.return_value.rollback.assert_called_once_with()
    conn.commit.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_main_suppresses_unknown_secret_errors(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['recover', '--image', IMAGE, '--expected-revision', REV,
                                    '--expected-image', OLD_IMAGE])
    monkeypatch.setattr(r, 'recover', Mock(side_effect=RuntimeError('credential-should-not-leak')))
    assert r.main() == 1
    output = capsys.readouterr().out
    assert 'credential-should-not-leak' not in output
    assert json.loads(output)['reason'] == 'details_suppressed'
