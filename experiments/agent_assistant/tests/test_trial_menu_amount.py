"""Owner HTTP/control guards with synthetic native transport, not live proof."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import httpx
import pytest

from experiments.agent_assistant.app import create_app
from experiments.agent_assistant.business_contracts import require_server
from experiments.agent_assistant.catalog import OWNER_INPUT_OPERATIONS
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.test_fax_workflow import Native


def fixture(tmp_path,monkeypatch,*,lost=False):
    monkeypatch.setenv('SAWA_TRIAL_OPERATOR_ACCOUNT','operator')
    app=create_app({},tmp_path/'ledger',managed_client=SimpleNamespace(close=AsyncMock()))
    store=app.state.store
    run,_=store.create('operator','owner-provisional-menu','FAX',['hospital'],15,task_id='hospital_pending_faxes')
    store.update(run['id'],status='waiting_input',task_inputs={'business_workflow':{
        'version':1,'phase':'support','attempts':4,'resume_phase':'saving'}})
    native=Native(tmp_path/'native')
    data=native.path(run['id'])/'data/hospital';data.mkdir(parents=True)
    (data/'database.sqlite3').write_bytes(b'synthetic owned-copy marker')
    native.created=None;native.posts=0;native.reads=0
    original=native.request
    async def request(run,domain,method,path,**kwargs):
        if method=='POST' and path=='/menu-masters':
            native.posts+=1
            native.created={**kwargs['body'],'id':'synthetic-master','revision':1}
            if lost:raise httpx.ReadTimeout('synthetic response loss')
            return httpx.Response(200,json={'item':native.created})
        if path=='/menu-masters/synthetic-master':
            native.reads+=1
            return httpx.Response(200,json={'item':native.created})
        return await original(run,domain,method,path,**kwargs)
    native.request=request
    app.state.tools.sandbox=native
    app.state.business.kick_many=AsyncMock()
    return app,native,run


BODY={'name':'Synthetic sauce','quantity':1,'unit':'g','provisional':True,'reason':'Test-only placeholder, not a business serving amount'}


def test_owner_registration_replays_write_but_freshly_reads_setting(tmp_path,monkeypatch):
    app,native,run=fixture(tmp_path,monkeypatch)
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        path='/runs/'+run['id']+'/trial-menu-amount'
        for _ in range(2):
            response=web.post(path,json=BODY)
            assert response.status_code==200,response.text
        assert native.posts==1 and native.reads==2
        saved=app.state.store.get(run['id'])
        assert saved['spent']==0 and saved['cap']==15
        event=app.state.store.latest_event(run['id'],'trial_provisional_menu_amount')
        assert event['payload']['provisional'] is True
        assert event['payload']['quantity']==1
        assert event['payload']['menu_master_id']=='synthetic-master'
        assert native.sheets['OA']['rows']==[['牛乳',0]]
        detail=web.get('/runs/'+run['id']).json()
        assert detail['provisional_menu_amount']['provisional'] is True


@pytest.mark.parametrize('patch',[{'provisional':False},{'quantity':0},{'quantity':10001},
    {'unit':'invalid'},{'budget':999}])
def test_invalid_owner_input_never_reaches_native(tmp_path,monkeypatch,patch):
    app,native,run=fixture(tmp_path,monkeypatch)
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        assert web.post('/runs/'+run['id']+'/trial-menu-amount',json={**BODY,**patch}).status_code==422
        assert native.posts==0


def test_unknown_creation_is_not_replayed(tmp_path,monkeypatch):
    app,native,run=fixture(tmp_path,monkeypatch,lost=True)
    with TestClient(app,raise_server_exceptions=False) as web:
        web.headers['X-Trial-Token']=app.state.token
        path='/runs/'+run['id']+'/trial-menu-amount'
        assert web.post(path,json=BODY).status_code==500
        assert web.post(path,json=BODY).status_code==409
        assert native.posts==1
        assert app.state.store.get(run['id'])['status']=='reconciliation_required'


@pytest.mark.parametrize('status',['completed','stopped','rolled_back'])
def test_finished_copies_are_not_changed(tmp_path,monkeypatch,status):
    app,native,run=fixture(tmp_path,monkeypatch)
    app.state.store.update(run['id'],status=status)
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        assert web.post('/runs/'+run['id']+'/trial-menu-amount',json=BODY).status_code==409
        assert native.posts==0


def test_model_cannot_access_owner_operations(tmp_path,monkeypatch):
    app,_,run=fixture(tmp_path,monkeypatch)
    assert not OWNER_INPUT_OPERATIONS.intersection(item['name'] for item in app.state.tools.definitions(['hospital']))
    for operation in OWNER_INPUT_OPERATIONS:
        with pytest.raises(Conflict,match='所有者'):
            require_server(run,operation)
