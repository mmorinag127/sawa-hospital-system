"""Actions transport stays restricted to the explicitly approved staging reset."""
import importlib.util
from pathlib import Path

import pytest

SPEC=importlib.util.spec_from_file_location('stg_restore',Path(__file__).resolve().parents[3]/'scripts/restore_stg_ocr_selection.py')
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def context():
    return dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='mmorinag127/sawa-hospital-system',
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/develop',
        GITHUB_ACTOR='mmorinag127',GITHUB_TRIGGERING_ACTOR='mmorinag127',
        PROJECT_ID='sawahospitalsystem',WORKER_URL=module.BASE,WORKER_SERVICE='worker-stg',
        RESTORE_CONFIRMATION=module.CONFIRMATION,STG_ID_TOKEN='synthetic-only')


@pytest.mark.parametrize('key',list(context()))
def test_changed_context_never_runs_recovery(key):
    environment=context()
    environment[key]='wrong'
    if key=='STG_ID_TOKEN':
        environment[key]=''
    with pytest.raises(ValueError):
        module.require_context(environment)


def test_exact_context_is_accepted():
    module.require_context(context())


def test_recovery_uses_the_existing_authenticated_deploy_identity_contract():
    import yaml
    workflow=yaml.safe_load((Path(__file__).resolve().parents[3]/'.github/workflows/deploy-stg.yml').read_text())
    job=workflow['jobs']['restore-ocr-selection']
    assert job['needs']=='source-gate'
    auth=next(step['with'] for step in job['steps'] if step.get('id')=='auth')
    normal=next(step['with'] for step in workflow['jobs']['deploy-backend']['steps'] if step.get('id')=='auth-backend')
    for key in ('project_id','workload_identity_provider','service_account','token_format','id_token_audience','id_token_include_email'):
        assert auth[key]==normal[key]
    assert auth['id_token_include_email'] is True


def test_recovery_calls_normal_api_once_and_checks_readback():
    calls=[]
    before=dict(order_id=module.ORDER,revision=2,ocr_reset={'audit_id':module.AUDIT,
        'retained_sheet_id':module.EXPECTED['saved_sheet_id']})
    after=dict(order_id=module.ORDER,revision=3,**module.EXPECTED)
    def request(method,path,revision=None):
        calls.append((method,path,revision))
        return before if len(calls)==1 else after
    result=module.restore(request)
    assert result['revision']==3
    assert [item for item in calls if item[0]=='POST']==[
        ('POST','/orders/'+module.ORDER+'/workflow-v2/ocr-reset/restore',2)]
    assert calls[-2][1].endswith('/inspection') and calls[-1][1].endswith('/sheet')


@pytest.mark.parametrize('field,value',[('revision',3),('order_id','OTHER'),('ocr_reset',None),
    ('ocr_reset',{'audit_id':'OTHER'}),('saved_sheet_id','NEW')])
def test_changed_state_never_posts(field,value):
    before=dict(order_id=module.ORDER,revision=2,ocr_reset={'audit_id':module.AUDIT,
        'retained_sheet_id':module.EXPECTED['saved_sheet_id']})
    before[field]=value
    def request(method,path,revision=None):
        assert method=='GET'
        return before
    with pytest.raises(ValueError):
        module.restore(request)
