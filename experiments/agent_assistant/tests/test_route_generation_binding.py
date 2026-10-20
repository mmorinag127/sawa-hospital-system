"""Provider-independent version binding, race fences and strict source resolution."""
import asyncio
from copy import deepcopy
import httpx
import pytest
from experiments.agent_assistant.tests.test_trial import prepared


@pytest.mark.parametrize('existing,claimed,expected',[(None,None,0),(None,1,0),(7,None,7),(7,999,7)])
def test_native_generation_uses_observed_version_not_model_value(tmp_path,existing,claimed,expected):
    store,sandbox,tools,run=prepared(tmp_path,('school-lunch',))
    reads=[];writes=[]
    async def native(run,domain,method,path,**kwargs):
        if method=='GET':
            reads.append(kwargs['params'])
            return httpx.Response(200,json=[] if existing is None else [{'id':9,'facility_id':1,'service_date':'2026-11-02','version':existing}])
        writes.append(deepcopy(kwargs['body']))
        return httpx.Response(200,json={'id':9,'version':expected+1})
    sandbox.request=native
    body={'facility_id':1,'service_date':'2026-11-02','layout_id':3}
    if claimed is not None:body['version']=claimed
    result=asyncio.run(tools.execute(run['id'],0,'lunch_generate_route',{'parameters':{'body':body,'reason':'試験の選択済み図面'}},'generate'))
    assert result['ok'] and writes[0]['version']==expected
    assert reads==[{'facility_id':1,'month':'2026-11'}]
    assert [e['payload']['version'] for e in store.events(run['id']) if e['kind']=='route_generation_binding']==[expected]


def test_racing_write_is_not_rebound_to_latest_after_conflict(tmp_path):
    store,sandbox,tools,run=prepared(tmp_path,('school-lunch',))
    reads=[];versions=[]
    async def native(run,domain,method,path,**kwargs):
        if method=='GET':reads.append(True);return httpx.Response(200,json=[])
        versions.append(kwargs['body']['version'])
        return httpx.Response(409,json={'detail':'Another writer created version 1'})
    sandbox.request=native
    args={'parameters':{'body':{'facility_id':1,'service_date':'2026-11-02','layout_id':3},'reason':'試験'}}
    async def scenario():
        first=await tools.execute(run['id'],0,'lunch_generate_route',args,'first')
        assert first['status']==409 and store.get(run['id'])['status']=='waiting_input'
        store.update(run['id'],status='running')  # Explicit human resumption, still cannot replace the observed version.
        second=await tools.execute(run['id'],0,'lunch_generate_route',args,'second')
        assert second['status']==409 and versions==[0,0] and len(reads)==1
    asyncio.run(scenario())


@pytest.mark.parametrize('current',[{},[{'id':1,'version':2,'facility_id':2,'service_date':'2026-11-02'}],[{'id':1,'version':None,'facility_id':1,'service_date':'2026-11-02'}],[{'id':1,'version':1,'facility_id':1,'service_date':'2026-11-99'}],[{'id':1,'version':1,'facility_id':1,'service_date':'2026-11-02'},{'id':2,'version':3,'facility_id':1,'service_date':'2026-11-02'}]])
def test_unresolved_state_never_becomes_new_diagram(tmp_path,current):
    _,sandbox,tools,run=prepared(tmp_path,('school-lunch',));writes=[]
    async def native(run,domain,method,path,**kwargs):
        if method=='GET':return httpx.Response(200,json=current)
        writes.append(True);return httpx.Response(200,json={})
    sandbox.request=native
    result=asyncio.run(tools.execute(run['id'],0,'lunch_generate_route',{'parameters':{'body':{'facility_id':1,'service_date':'2026-11-02','layout_id':3},'reason':'試験'}},'generate'))
    assert result.get('error') and not writes


def test_model_cannot_enable_confirmed_original_replacement(tmp_path):
    _,sandbox,tools,run=prepared(tmp_path,('school-lunch',));requests=[]
    async def native(*args,**kwargs):requests.append(True);return httpx.Response(200,json=[])
    sandbox.request=native
    result=asyncio.run(tools.execute(run['id'],0,'lunch_generate_route',{'parameters':{'body':{'facility_id':1,'service_date':'2026-11-02','layout_id':3,'replace_confirmed_source':True},'reason':'モデルが競合を回避'}},'replace'))
    assert '上書き' in result['error'] and not requests


def test_public_operation_contract_omits_server_managed_native_fields(tmp_path):
    _,sandbox,tools,run=prepared(tmp_path,('school-lunch',))
    async def native(*args,**kwargs):
        return httpx.Response(200,json={'paths':{'/api/school-lunch/route-diagrams/generate':{'post':{'requestBody':{'content':{'application/json':{'schema':{'$ref':'#/components/schemas/GenerateWrite'}}}}}}},'components':{'schemas':{'GenerateWrite':{'type':'object','properties':{key:{'type':'integer'} for key in ('version','facility_id','layout_id','replace_confirmed_source')},'required':['version','facility_id','layout_id']}}}})
    sandbox.request=native
    result=asyncio.run(tools.describe(run,{'operation':'lunch_generate_route'}))
    schema=result['components']['schemas']['GenerateWrite']
    assert schema['required']==['facility_id','layout_id'] and 'version' not in schema['properties']
    assert 'replace_confirmed_source' not in schema['properties']
    assert 'body.version' in result['wrapper_parameters']['managed_fields']
