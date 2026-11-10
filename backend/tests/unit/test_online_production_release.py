import copy
import ast
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import production_online_release as release
import prepare_online_recovery_source as recovery
import production_deploy_supplement as supplement
import local_hospital_test_runner as runner


def service():
    return dict(apiVersion='serving.knative.dev/v1',kind='Service',
        metadata=dict(name='web-prod',resourceVersion='1',annotations={}),
        spec=dict(template=dict(metadata=dict(name='old',annotations={'retained':'yes'}),
            spec=dict(serviceAccountName='existing',containers=[
                dict(image='old',ports=[dict(containerPort=8080)],env=[dict(name='SECRET',value='kept')]),
                dict(image='sidecar',env=[dict(name='OTHER',value='retained')])])),
            traffic=[dict(revisionName='old',percent=100)]),
        status=dict(traffic=[dict(revisionName='old',percent=100)]))


def test_candidate_preserves_all_runtime_fields_and_old_traffic():
    original=service()
    result=release.replacement(original,'new-image','new-revision','candidate','a'*40)
    assert original == service()
    expected=copy.deepcopy(original['spec']['template']['spec'])
    expected['containers'][0]['image']='new-image'
    assert result['spec']['template']['spec'] == expected
    assert result['spec']['traffic'] == [dict(revisionName='old',percent=100),
        dict(revisionName='new-revision',percent=0,tag='candidate')]
    assert 'status' not in result


@pytest.mark.parametrize('bad', ['tag','split','maintenance','read-only','ingress'])
def test_invalid_live_state_rejected(bad):
    value=service()
    if bad=='tag': value['status']['traffic'][0]['tag']='unknown'
    if bad=='split': value['status']['traffic'][0]['percent']=50
    if bad=='maintenance': value['metadata']['labels']={'sawa-maintenance-run':'123'}
    if bad=='read-only':
        value['metadata']['name']='worker-prod'
        value['spec']['template']['spec']['containers'][0]['env'].append(dict(name='SAWA_RELEASE_READ_ONLY',value='1'))
    if bad=='ingress': value['spec']['template']['spec']['containers'][1]['ports']=[dict(containerPort=80)]
    with pytest.raises(RuntimeError): release.replacement(value,'image','new','tag','a'*40)


def test_web_candidate_write_protection_is_preserved():
    value=service()
    value['spec']['template']['spec']['containers'][0]['env'].append(dict(name='SAWA_RELEASE_READ_ONLY',value='1'))
    updated=release.replacement(value,'image','new','tag','a'*40)
    assert updated['spec']['template']['spec']['containers'][0]['env']==value['spec']['template']['spec']['containers'][0]['env']


def test_recovery_recipe_old_business_byte_identity(tmp_path):
    result=recovery.prepare(tmp_path/'recovery')
    assert (result.parent/'recovery-source.json').is_file()
    generated=(result/'src/maintenance/formal_migration_schema.py').read_text()
    original=(ROOT/'backend/src/maintenance/formal_migration_schema.py').read_text()
    assert generated == original.replace('head = formal_head_revision()', 'head = recovery_head_revision(connection)') + '\nfrom src.maintenance.online_recovery_schema import recovery_head_revision\n'
    ast.parse(generated, feature_version=(3,11))


def test_online_workflow_preserves_approval_and_never_calls_maintenance():
    data=yaml.safe_load((ROOT/'.github/workflows/deploy-prod.yml').read_text())
    jobs=data['jobs']
    online=jobs['online-production']
    assert online['environment']=='production'
    assert online['permissions']['id-token']=='write'
    assert online['needs']==['source-gate','build-backend','build-frontend']
    text=str(online)
    assert 'production_online_release.py' in text
    assert 'production_maintenance.py' not in text
    assert 'local-test-receipt' in str(jobs['source-gate'])
    assert '!inputs.online_release' in jobs['production-approval']['if']
    source=(ROOT/'scripts/production_online_release.py').read_text()
    assert 'online_projection_only=True' in source
    assert 'backup_id' in source and 'rollback_failures' in source
    assert 'downgrade' not in source


def test_signed_version6_base_is_fully_validated_not_supplement_chained(monkeypatch):
    old,new='a'*40,'b'*40
    base=dict(receipt_version=6,source_sha=old)
    monkeypatch.setattr(supplement,'verify_signed',lambda _:base)
    full=Mock()
    monkeypatch.setattr(runner,'validate_receipt',full)
    changed=['scripts/production_online_release.py']
    monkeypatch.setattr(supplement,'git',lambda *args:'\n'.join(changed))
    monkeypatch.setattr(supplement.subprocess,'run',Mock())
    proof=dict(receipt_version=4,source_sha=new,clean_before=True,clean_after=True,
        base_receipt={},changed_files=changed,regression=dict(command_sha256=supplement.COMMAND_HASH,
        log_sha256='c'*64,exit_code=0))
    supplement.validate(proof,new,'d'*64)
    full.assert_called_once_with(base,old,'d'*64,require_full=True)


@pytest.mark.parametrize('frontend_failure', [False, True])
def test_rollout_serves_bridge_during_ddl_and_failure_restores_compatible_runtime(monkeypatch, tmp_path, frontend_failure):
    import run_prod_schema_adoption as migration
    state={name:service() for name in release.SERVICES}
    for name,value in state.items():
        value['metadata']['name']=name
        value['spec']['template']['metadata']['annotations']['run.googleapis.com/cloudsql-instances']='sawahospitalsystem:asia-northeast2:orders-prod'
        value['status']['url']='https://'+name
    originals={name:dict(revision='old',uid='uid',images=['old']) for name in state}
    for key,value in dict(ONLINE_EXPECTED_LIVE=json.dumps(originals),ONLINE_BACKEND_IMAGE='backend',
        ONLINE_FRONTEND_IMAGE='frontend',ONLINE_RECOVERY_IMAGE='recovery',VERIFICATION_TOKEN='token',
        ORDER_ID='ORD123',RUNNER_TEMP=str(tmp_path),GITHUB_SHA='a'*40,GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1').items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(release,'context',lambda:None)
    monkeypatch.setattr(release,'identity',lambda _:dict(revision='old',uid='uid',images=['old']))
    monkeypatch.setattr(release,'resolve_image_digest',lambda value:value)
    monkeypatch.setattr(release,'require_runtime_act_as',lambda _:None)
    monkeypatch.setattr(migration,'production_database',lambda:None)
    revisions={}
    def cloud(*args):
        if args[:3]==('run','services','describe'): return copy.deepcopy(state[args[3]])
        if args[:3]==('run','services','replace'):
            data=yaml.safe_load(Path(args[3]).read_text())
            name=data['metadata']['name']
            revision=data['spec']['template']['metadata']['name']
            revisions[revision]=dict(spec=copy.deepcopy(data['spec']['template']['spec']),
                status=dict(conditions=[dict(type='Ready',status='True')]))
            data['status']=dict(url='https://'+name,traffic=[{**v,'url':'https://'+v.get('tag','public')} for v in data['spec']['traffic']])
            state[name]=data
            return
        if args[:3]==('run','revisions','describe'): return revisions[args[3]]
        if args[:3]==('run','services','update-traffic'):
            value=state[args[3]]
            for arg in args:
                if arg.startswith('--to-revisions='):
                    target=arg.split('=',1)[1].rsplit('=',1)[0]
                    value['status']['traffic']=[dict(revisionName=target,percent=100)]+[
                        {**v,'percent':0} for v in value['status']['traffic'] if v.get('tag')]
                if arg.startswith('--remove-tags='):
                    tags=arg.split('=',1)[1].split(',')
                    value['status']['traffic']=[v for v in value['status']['traffic'] if v.get('tag') not in tags]
            return
        if args[:3]==('sql','backups','create'):
            cloud.description=next(v.split('=',1)[1] for v in args if v.startswith('--description='))
            return
        if args[:3]==('sql','backups','list'): return [dict(description=cloud.description,status='SUCCESSFUL',id='backup')]
        raise AssertionError(args)
    monkeypatch.setattr(release,'cloud',cloud)
    monkeypatch.setattr(release,'verify_worker',lambda *args:None)
    def verify_web(url,*args):
        if frontend_failure and url=='https://online-candidate-123-1': raise RuntimeError('frontend rejected')
    monkeypatch.setattr(release,'verify_web',verify_web)
    def migrate(**kwargs):
        assert release.serving({**state['worker-prod'],'status':{'traffic':[v for v in state['worker-prod']['status']['traffic'] if v.get('percent')]}}).endswith('recovery38')
        assert kwargs==dict(online_projection_only=True)
        return dict(rows=1,sha256='a'*64)
    monkeypatch.setattr(migration,'run',migrate)
    if frontend_failure:
        with pytest.raises(RuntimeError,match='frontend rejected'): release.run()
        assert release.serving(state['worker-prod']).endswith('recovery39')
        assert release.serving(state['web-prod'])=='old'
    else:
        release.run()
        assert release.serving(state['worker-prod']).endswith('candidate')
        assert release.serving(state['web-prod']).endswith('candidate')
    assert not any(v.get('tag') for s in state.values() for v in s['status']['traffic'])
