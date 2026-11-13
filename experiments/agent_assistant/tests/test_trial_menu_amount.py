"""Owner HTTP/control guards with synthetic native transport, not live proof."""
from types import SimpleNamespace
import asyncio
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
    store.set_budget(100)
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


@pytest.mark.parametrize('changed',[False,True])
def test_fax_completion_includes_fresh_owner_amount_read_receipt_without_weakening_write_guard(tmp_path,monkeypatch,changed):
    from experiments.agent_assistant.business_contracts import SERVER_EXECUTION
    from experiments.agent_assistant.workflow_completion import WorkflowCompletion
    app,native,run=fixture(tmp_path,monkeypatch)
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        assert web.post('/runs/'+run['id']+'/trial-menu-amount',json=BODY).status_code==200
        if changed: native.created['qty_per_serving']=2
        reads_before=native.reads
        async def check():
            token=SERVER_EXECUTION.set(True)
            try:
                return await WorkflowCompletion(app.state.tools).owner_amount_evidence(app.state.store.get(run['id']),'completion-test')
            finally:
                SERVER_EXECUTION.reset(token)
        if changed:
            with pytest.raises(Conflict,match='一人量の再取得が一致しません'): asyncio.run(check())
        else:
            evidence=asyncio.run(check())
            assert len(evidence)==1
            event=app.state.store.latest_event(run['id'],'trial_provisional_menu_amount')['payload']
            with app.state.store.read() as db:
                row=db.execute('SELECT rowid,request,result FROM commands WHERE run_id=? AND id=?',(run['id'],evidence[0])).fetchone()
                write=db.execute('SELECT rowid FROM commands WHERE run_id=? AND id=?',(run['id'],event['command_id'])).fetchone()
            import json
            assert row['rowid']>write['rowid']
            assert json.loads(row['request'])['name']=='hospital_get_menu_master'
            assert json.loads(row['result'])['data']['item']['qty_per_serving']==1
        assert native.reads==reads_before+1 and native.posts==1


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


def area_fixture(tmp_path, monkeypatch, *, lost=False):
    app, native, run = fixture(tmp_path, monkeypatch)
    tools = app.state.tools
    app.state.store.update(run['id'], status='running')
    run = app.state.store.get(run['id'])
    native.sources['A']['status'] = 'completed'
    native.saved.add('OA')
    native.wfs['OA']['saved_sheet_id'] = 'SOA'
    async def approve():
        await tools.faxes.refresh(run)
        review = (await tools.faxes.submit(run, 'A', 'Synthetic review'))['review']
        await tools.faxes.decide(run, 'A', review['id'], True, '')
    asyncio.run(approve())
    original = native.request
    native.area_writes = []
    async def request(run, domain, method, path, **kwargs):
        if path.endswith('/trial-area-names'):
            native.area_writes.append((path, kwargs['body']))
            if lost:
                raise httpx.ReadTimeout('synthetic area response loss')
            return httpx.Response(200, json={'order_id':'OA','names':kwargs['body']['names']})
        return await original(run, domain, method, path, **kwargs)
    native.request = request
    return app, native, run


def test_area_registration_preserves_approved_sheet_and_is_copy_scoped(tmp_path, monkeypatch):
    app, native, run = area_fixture(tmp_path, monkeypatch)
    with TestClient(app) as web:
        web.headers['X-Trial-Token'] = app.state.token
        path = '/runs/'+run['id']+'/trial-area-names'
        body = {'order_id':'OA','names':{'2F':'Flower','3F':'Moon'},'reason':'Explicit approval'}
        assert web.post(path, json={**body,'order_id':'OTHER'}).status_code == 409
        assert not native.area_writes
        response = web.post(path, json=body)
        assert response.status_code == 200, response.text
        assert native.area_writes == [('/orders/OA/workflow-v2/trial-area-names', {
            'saved_sheet_id':'SOA','names':body['names'],'reason':body['reason']})]
        assert native.sheets['OA']['rows'] == [['牛乳',0]]
        assert native.wfs['OA']['saved_sheet_id'] == 'SOA'
        assert app.state.store.get(run['id'])['spent'] == 0
        assert app.state.store.latest_event(run['id'],'trial_area_names')['payload']['saved_sheet_id'] == 'SOA'


def test_unknown_area_registration_is_fenced_not_replayed(tmp_path, monkeypatch):
    app, native, run = area_fixture(tmp_path, monkeypatch, lost=True)
    with TestClient(app, raise_server_exceptions=False) as web:
        web.headers['X-Trial-Token'] = app.state.token
        path = '/runs/'+run['id']+'/trial-area-names'
        body = {'order_id':'OA','names':{'2F':'Flower'},'reason':'Explicit approval'}
        assert web.post(path, json=body).status_code == 500
        assert web.post(path, json=body).status_code == 409
        assert len(native.area_writes) == 1
        assert app.state.store.get(run['id'])['status'] == 'reconciliation_required'


INVOICE_BODY={'order_id':'OA','source_sha256':'a'*64,
    'source_columns':[{'source_index':4,'header':'Regular','diet_type':'regular','area_id':'X'}],
    'excluded_source_columns':[],
    'invoice_columns':[{'name':'Regular','source':'quantity','diet_type':'regular','area_id':'X'}],
    'reason':'Synthetic original and saved sheet reviewed independently'}


def invoice_fixture(tmp_path,monkeypatch,*,lost=False,changed=False):
    app,native,run=area_fixture(tmp_path,monkeypatch)
    native.wfs['OA']['state']='confirmed'
    asyncio.run(app.state.tools.faxes.refresh(run))
    native.invoice_writes=[]
    original=native.request
    async def request(run,domain,method,path,**kwargs):
        if path.endswith('/trial-invoice-columns'):
            native.invoice_writes.append(kwargs['body'])
            assert app.state.tools.workflow_locks[run['id']].locked()
            if lost: raise httpx.ReadTimeout('Synthetic invoice reply lost')
            if changed: native.sheets['OA']['rows'][0][1]=999
            return httpx.Response(200,json={'order_id':'OA','columns':kwargs['body']['invoice_columns']})
        return await original(run,domain,method,path,**kwargs)
    native.request=request
    return app,native,run


def test_confirmed_copy_invoice_registration_retains_sheet_and_no_model_spend(tmp_path,monkeypatch):
    app,native,run=invoice_fixture(tmp_path,monkeypatch)
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        path='/runs/'+run['id']+'/trial-invoice-columns'
        assert web.post(path,json={**INVOICE_BODY,'order_id':'OTHER'}).status_code==409
        response=web.post(path,json=INVOICE_BODY)
        assert response.status_code==200,response.text
        assert len(native.invoice_writes)==1
        assert native.invoice_writes[0]=={**{k:v for k,v in INVOICE_BODY.items() if k!='order_id'},'saved_sheet_id':'SOA'}
        assert native.sheets['OA']['rows']==[['牛乳',0]]
        assert native.wfs['OA']['saved_sheet_id']=='SOA'
        assert app.state.store.get(run['id'])['spent']==0
        assert app.state.store.latest_event(run['id'],'trial_invoice_columns')['payload']['saved_sheet_id']=='SOA'


@pytest.mark.parametrize('lost,changed',[(True,False),(False,True)])
def test_invoice_unknown_write_or_changed_saved_sheet_is_not_success(tmp_path,monkeypatch,lost,changed):
    app,native,run=invoice_fixture(tmp_path,monkeypatch,lost=lost,changed=changed)
    with TestClient(app,raise_server_exceptions=False) as web:
        web.headers['X-Trial-Token']=app.state.token
        response=web.post('/runs/'+run['id']+'/trial-invoice-columns',json=INVOICE_BODY)
        assert response.status_code== (500 if lost else 409)
        assert app.state.store.get(run['id'])['status']=='reconciliation_required'
        assert app.state.store.latest_event(run['id'],'trial_invoice_columns') is None
        assert web.post('/runs/'+run['id']+'/trial-invoice-columns',json=INVOICE_BODY).status_code==409
        assert len(native.invoice_writes)==1


def test_combined_upgrade_keeps_completion_locked_until_invoice_is_registered(tmp_path,monkeypatch):
    from experiments.agent_assistant import execution_binding
    app,native,run=invoice_fixture(tmp_path,monkeypatch)
    app.state.store.update(run['id'],status='reconciliation_required')
    calls=[]
    def approve(*args,**kwargs):
        assert app.state.tools.workflow_locks[run['id']].locked()
        calls.append(('upgrade',kwargs))
    async def resume(ident):
        assert app.state.tools.workflow_locks[ident].locked()
        assert not native.invoice_writes
        calls.append(('resume',ident))
    monkeypatch.setattr(execution_binding,'approve_native_upgrade',approve)
    app.state.managed.resume=AsyncMock(side_effect=resume)
    upgrade={'generation':run['generation'],'previous_sha256':'a'*64,'current_sha256':'b'*64}
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        response=web.post('/runs/'+run['id']+'/trial-invoice-columns',json={**INVOICE_BODY,'native_upgrade':upgrade})
        assert response.status_code==200,response.text
        assert calls==[('upgrade',upgrade),('resume',run['id'])]
        assert len(native.invoice_writes)==1


def test_changed_execution_binding_rejects_invoice_before_native_write(tmp_path,monkeypatch):
    from experiments.agent_assistant import execution_binding
    app,native,run=invoice_fixture(tmp_path,monkeypatch)
    app.state.store.event(run['id'],'execution_binding',{'synthetic':'old source'})
    def reject(*args,**kwargs): raise Conflict('Source mismatch')
    monkeypatch.setattr(execution_binding,'record',reject)
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        assert web.post('/runs/'+run['id']+'/trial-invoice-columns',json=INVOICE_BODY).status_code==409
        assert not native.invoice_writes


@pytest.mark.parametrize('failure',[None,'export','changed_sheet'])
def test_completed_invoice_refresh_never_reopens_business_or_model(tmp_path,monkeypatch,failure):
    from experiments.agent_assistant import execution_binding
    from experiments.agent_assistant.business_contracts import completed_invoice_order
    app,native,run=invoice_fixture(tmp_path,monkeypatch)
    store=app.state.store
    store.update(run['id'],status='completed')
    store.event(run['id'],'execution_binding',{'synthetic':'pinned'})
    before=store.get(run['id'])
    approvals=[]
    monkeypatch.setattr(execution_binding,'record',lambda *_:{'native_sources':{'hospital':'synthetic'}})
    monkeypatch.setattr(execution_binding,'approve_native_upgrade',lambda *a,**kw:approvals.append(kw))
    app.state.managed.resume=AsyncMock()
    app.state.business.kick_many.reset_mock()
    cleanups=[]
    async def cleanup(self,ident=None,*,expected_generation=None):
        if ident==run['id']:
            assert app.state.tools.workflow_locks[ident].locked(), 'Cleanup must finish before the next owner operation'
            cleanups.append((ident,expected_generation))
        return True
    from experiments.agent_assistant.sandbox import Sandbox
    monkeypatch.setattr(Sandbox,'release_completed',cleanup)
    execute=app.state.tools.execute
    exports=[]
    async def dispatch(ident,generation,name,raw,command):
        if name=='hospital_export_delivery_notes':
            assert completed_invoice_order(store.get(ident))=='OA'
            assert raw=={'parameters':{'query':{'order_id':'OA'}}}
            exports.append(command)
            if failure=='export': return {'ok':False,'status':400}
            if failure=='changed_sheet': native.sheets['OA']['rows'][0][1]=999
            return {'ok':True,'artifact':{'id':'synthetic-output','name':'synthetic.xlsx','sha256':'c'*64,'size':12}}
        return await execute(ident,generation,name,raw,command)
    app.state.tools.execute=dispatch
    upgrade={'generation':run['generation'],'previous_sha256':'a'*64,'current_sha256':'b'*64}
    with TestClient(app) as web:
        web.headers['X-Trial-Token']=app.state.token
        response=web.post('/runs/'+run['id']+'/trial-invoice-columns',json={**INVOICE_BODY,'native_upgrade':upgrade})
        assert response.status_code==({None:200,'export':400,'changed_sheet':409}[failure]),response.text
    assert approvals==[{**upgrade,'output_only':True}]
    assert len(native.invoice_writes)==1 and len(exports)==1
    after=store.get(run['id'])
    assert {k:after[k] for k in ('status','generation','spent','cap','task_inputs')}=={k:before[k] for k in ('status','generation','spent','cap','task_inputs')}
    app.state.managed.resume.assert_not_awaited()
    app.state.business.kick_many.assert_not_awaited()
    assert bool(store.latest_event(run['id'],'trial_invoice_output'))==(failure is None)
    assert cleanups==[(run['id'],run['generation'])]


@pytest.mark.parametrize('operation,target',[('hospital_bag_order','OA'),('hospital_export_aggregate','OA'),('hospital_register_trial_invoice_columns','OTHER')])
def test_completed_invoice_scope_cannot_execute_other_operations_or_orders(tmp_path,monkeypatch,operation,target):
    from experiments.agent_assistant.business_contracts import COMPLETED_INVOICE_EXECUTION,OWNER_INPUT_EXECUTION,SERVER_EXECUTION
    app,_,run=invoice_fixture(tmp_path,monkeypatch)
    app.state.store.update(run['id'],status='completed')
    async def scenario():
        tokens=(COMPLETED_INVOICE_EXECUTION.set((run['id'],run['generation'],'OA')),OWNER_INPUT_EXECUTION.set(True),SERVER_EXECUTION.set(True))
        try:
            with pytest.raises(Conflict):
                await app.state.tools.execute(run['id'],run['generation'],operation,
                    {'parameters':{'ids':{'order_id':target},'query':{'order_id':target}}},'forbidden-completed')
        finally:
            for var,token in zip((COMPLETED_INVOICE_EXECUTION,OWNER_INPUT_EXECUTION,SERVER_EXECUTION),tokens): var.reset(token)
    asyncio.run(scenario())
