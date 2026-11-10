import copy
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
    if bad=='read-only': value['spec']['template']['spec']['containers'][0]['env'].append(dict(name='SAWA_RELEASE_READ_ONLY',value='1'))
    if bad=='ingress': value['spec']['template']['spec']['containers'][1]['ports']=[dict(containerPort=80)]
    with pytest.raises(RuntimeError): release.replacement(value,'image','new','tag','a'*40)


def test_recovery_recipe_old_business_byte_identity(tmp_path):
    result=recovery.prepare(tmp_path/'recovery')
    assert (result.parent/'recovery-source.json').is_file()
    assert 'recovery_head_revision(connection)' in (result/'src/maintenance/formal_migration_schema.py').read_text()


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
