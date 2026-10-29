"""Named workflows with synthetic business data; no real originals/OCR/model fees."""
import asyncio
import hashlib
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from experiments.agent_assistant.catalog import OPERATIONS
from experiments.agent_assistant.store import Store, Conflict
from experiments.agent_assistant.tools import Tools, Arguments
from experiments.agent_assistant.tasks import TASKS, public_tasks


MONTH='2026-11'
QUERY={'facility_id':1,'month':MONTH}


class Native:
    def __init__(self, root):
        self.root=root; self.data={}; self.writes=[]; self.reads=[]; self.after_export=None; self.configs={}
    def path(self, ident):
        folder=self.root/ident; folder.mkdir(parents=True,exist_ok=True); return folder
    def local_jobs(self, run): return []
    def prepare(self, run): self.path(run['id'])
    async def start(self, run): pass
    async def stop(self, ident, *, expected_generation=None): return True
    async def request(self, run, domain, method, path, **kwargs):
        if path=='/outputs/validate-received':
            body=kwargs['body'];wf=self.data['/orders/'+body['order_id']+'/workflow-v2']
            return httpx.Response(200,json={'valid':True,**body,'sha256':hashlib.sha256(kwargs['files']['artifact_file'][1]).hexdigest(),'revision':wf['revision'],'confirmed_snapshot_id':wf['confirmed_snapshot_id']})
        if method=='GET':
            self.reads.append(path)
            if path == '/planning/constraint-date-counts':
                source = self.data.get('/constraints', {'items': [], 'collection_revision': 0})
                counts = {}
                for item in source.get('items', []):
                    counts[item['work_date']] = counts.get(item['work_date'], 0) + 1
                after = kwargs.get('params', {}).get('after')
                dates = sorted(day for day in counts if after is None or day > after)
                return httpx.Response(200, json={'collection_revision': source.get('collection_revision', 0),
                    'items': [{'work_date': day, 'count': counts[day]} for day in dates[:200]],
                    'next_after': dates[199] if len(dates) > 200 else None})
            if path == '/solve-runs':
                source = self.data[path]
                values = source['items'] if isinstance(source, dict) else source
                query = kwargs.get('params', {})
                values = sorted(values, key=lambda item: item['id'], reverse=True)
                if query.get('before_id') is not None:
                    values = [item for item in values if item['id'] < query['before_id']]
                return httpx.Response(200, json=deepcopy(values[:query.get('limit', 50)]))
            if path.endswith('/export') or path.endswith('/export/month') or path.startswith('/outputs/'):
                if self.after_export: self.after_export()
                return httpx.Response(200,content=b'synthetic workbook bytes',headers={'content-type':'application/octet-stream'})
            if path not in self.data: raise AssertionError('unexpected native GET '+path)
            return httpx.Response(200,json=deepcopy(self.data[path]))
        self.writes.append((path,deepcopy(kwargs)))
        if path=='/planning/solve-runs/501/confirm':
            self.data['/solve-runs/501'].update(confirmed=True,confirmed_at='synthetic-confirmed-time')
            return httpx.Response(200,json={'solve_run_id':501,'confirmed':True})
        if path=='/api/school-lunch/cooking-arrangements/confirm':
            menus=[dict(id=101,facility={'id':1,'name':'合成試験校'},**{k:v for k,v in item.items() if k!='facility_name'}) for item in kwargs['body']['menus']]
            self.data['/api/school-lunch/menu-executions']=menus
            return httpx.Response(201,json=menus)
        return httpx.Response(200,json={'ok':True})


def setup(tmp_path, ident, targets, inputs=None):
    store=Store(tmp_path/'ledger'); store.set_budget(1000)
    run,_=store.create('operator','named-task-key',TASKS[ident]['label'],[TASKS[ident]['domain']],100,
        task_id=ident,task_inputs={'inputs':inputs or {'month':MONTH},'targets':targets})
    store.update(run['id'],status='running'); run=store.get(run['id'])
    native=Native(tmp_path/'native'); tools=Tools(store,native)
    return store,native,tools,run


def record(tools,run,name,data,parameters=None):
    ident='seed-'+str(len(tools.flow.commands(run['id'])))
    tools.store.command(run['id'],ident,{'name':name,'arguments':{'parameters':parameters or {}}})
    tools.store.finish_command(run['id'],ident,{'ok':True,'data':data})
    return ident


async def get(tools,run,name,ids=None,query=None):
    if name == 'shift_get_constraints' and not query:
        period = await tools.flow.read(run, 'shift_get_period', {'query': {'month': run['task_inputs']['inputs']['month']}})
        query = {'date_from': period['start_date'], 'date_to': period['end_date_exclusive']}
    ident='get-'+str(len(tools.flow.commands(run['id'])))
    result=await tools.execute(run['id'],0,name,{'parameters':{'ids':ids or {},'query':query or {}}},ident)
    assert result.get('ok'),result
    return ident


def shift(tmp_path, task_id='shift_next_month'):
    target=({'key':'501:77','label':'合成勤務案','data':{'run_id':501,'candidate_id':77}} if task_id in ('shift_check','shift_export') else {'key':MONTH,'label':MONTH,'data':{'month':MONTH}})
    store,native,tools,run=setup(tmp_path,task_id,[target])
    native.data.update({
        '/solve-runs/501':{'id':501,'target_month':MONTH,'status':'succeeded','selected_candidate_id':77,'confirmed':task_id=='shift_export',
            'confirmed_at':None,'assignments':[{'staff_id':1,'staff_name':'合成人員','work_date':'2026-11-02','shift_template':'合成勤務'}],
            'candidates':[{'id':77}],'candidate_validation':{'blocking':False}},
        '/candidates/77/validation':{'blocking':False,'violations':[]},
        '/staff':{'items':[{'id':1,'name':'合成人員'}]}, '/constraints':{'items':[]}, '/optimization-settings':{'revision':1},
        '/planning/period':{'target_month':MONTH,'start_date':'2026-11-01','end_date_exclusive':'2026-12-01','period_mode':'calendar_month'},
        '/planning/demands':{'items':[{'work_date':'2026-11-02','required_count':1}]},'/planning/workdays':{'items':[{'work_date':'2026-11-02','working':True}]},
    })
    if task_id=='shift_next_month': record(tools,run,'shift_solve',{'solve_run_id':501}, {'body':{'target_month':MONTH}})
    return store,native,tools,run


async def shift_evidence(tools,run,full=True):
    ids=[await get(tools,run,'shift_get_run',{'run_id':501},{'candidate_id':77}), await get(tools,run,'shift_get_validation',{'candidate_id':77})]
    if full:
        for name in ('shift_get_staff','shift_get_constraints','shift_get_settings','shift_get_period','shift_get_demands','shift_get_workdays'):
            ids.append(await get(tools,run,name,query={'start':'2026-11-01','end':'2026-12-01'} if name in ('shift_get_demands','shift_get_workdays') else {'month':MONTH} if name=='shift_get_period' else {}))
    return ids


def lunch(tmp_path, task_id='lunch_process_routes'):
    menus=[{'id':101,'name':'合成献立','facility':{'id':1,'name':'合成試験校'},'service_date':'2026-11-02','version':1,'servings':10}]
    target=({'key':'101','label':'合成献立','data':menus[0]} if TASKS[task_id]['target_kind']=='menu' else {'key':'1','label':'合成試験校','data':{'id':1,'name':'合成試験校'}})
    store,native,tools,run=setup(tmp_path,task_id,[target],{'month':MONTH,'facility_id':1,'layout_id':9,'layout_version':1})
    native.data.update({
        '/api/school-lunch/facilities':[{'id':1,'name':'合成試験校'}],
        '/api/school-lunch/menu-executions':menus,
        '/api/school-lunch/menu-executions/101/process':{'menu':menus[0],'revision':1,'rows':[{'operation':'原本で指定した合成工程'}]},
        '/api/school-lunch/work-schedules':[{'id':601,'facility':{'id':1,'name':'合成試験校'},'service_date':'2026-11-02','version':1,'rows':[{'operation':'合成作業'}],'stale':False}],
        '/api/school-lunch/route-diagrams':[{'id':701,'facility_id':1,'service_date':'2026-11-02','version':1,'layout_id':9,'layout_version':1,'routes':[],'blockers':[]}],
        '/api/school-lunch/route-diagrams/layouts':[{'id':9,'name':'合成図面','version':1}],
        '/api/school-lunch/monthly-procurement':{'version':1,'issues':[],'inventory_ready':True,'rows':[{'name':'合成食材','quantity':0}]},
        '/api/school-lunch/cooking-arrangements/documents':[{'id':'doc-synthetic','ocr_job_id':None,'target_month':MONTH}],
        '/api/school-lunch/cooking-arrangements/documents/doc-synthetic':{'id':'doc-synthetic','result':{'menus':[{'facility_name':'合成試験校','name':'合成献立','servings':10}]}}
    })
    return store,native,tools,run


async def lunch_evidence(tools,run):
    values=[await get(tools,run,name,query=QUERY) for name in ('lunch_get_menus','lunch_get_schedules','lunch_get_routes')]
    values.append(await get(tools,run,'lunch_get_process',{'menu_id':101},QUERY))
    return values


def test_all_thirteen_tasks_have_public_selection_and_internal_policies():
    assert len(TASKS)==14
    assert {x['domain'] for x in TASKS.values()}=={'hospital','shift','school-lunch'}
    assert all('instruction' not in x and 'terminal' not in x for x in public_tasks())
    assert all(x['steps'] and x['label'] for x in public_tasks())


def test_shift_cannot_confirm_without_exact_human_approval_then_completes(tmp_path):
    async def scenario():
        store,native,tools,run=shift(tmp_path)
        body={'candidate_id':77,'candidate_revision':'a'*64,'input_revision':'b'*64}
        params={'ids':{'run_id':501},'query':{'candidate_id':77},'body':body,'reason':'合成試験の承認済み候補'}
        with pytest.raises(Conflict,match='利用者承認'):
            await tools.native(run,'shift_confirm',Arguments.model_validate(params))
        ids=await shift_evidence(tools,run)
        await tools.flow.submit(run,'合成勤務案と制約を確認してください',ids,[{'operation':'shift_confirm','parameters':params}])
        review=tools.flow.review(run['id'])
        waiter=asyncio.create_task(tools.execute(run['id'],0,'wait_for_task_decision',{},'wait-human'))
        await asyncio.sleep(.05)
        async with tools.lock(run['id']): await tools.flow.decide(run,review['id'],True,'')
        assert (await asyncio.wait_for(waiter,2))['state']=='approved'
        altered=deepcopy(params); altered['body']['candidate_revision']='c'*64
        with pytest.raises(Conflict,match='一致'):
            await tools.native(run,'shift_confirm',Arguments.model_validate(altered))
        result=await tools.execute(run['id'],0,'shift_confirm',{'parameters':params},'confirm-exact')
        assert result.get('ok'),result
        assert len(native.writes)==1
        output=await tools.execute(run['id'],0,'shift_export',{'parameters':{'ids':{'run_id':501},'query':{'candidate_id':77}}},'export-confirmed')
        assert output.get('artifact'),output
        ids.append(await get(tools,run,'shift_get_run',{'run_id':501},{'candidate_id':77}))
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'合成勤務案の確定と出力を確認','evidence_commands':ids,'artifact_ids':[output['artifact']['id']]},'finish-shift')
        assert result.get('ok'),result
        assert store.get(run['id'])['status']=='completed'
    asyncio.run(scenario())


def test_validation_report_can_complete_with_explicit_violations_without_mutation(tmp_path):
    async def scenario():
        store,native,tools,run=shift(tmp_path,'shift_check')
        native.data['/candidates/77/validation']={'blocking':True,'violations':[{'message':'合成試験の人員不足'}]}
        ids=await shift_evidence(tools,run,False)
        await tools.flow.submit(run,'人員不足があります。検査報告を確認してください',ids,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'確認しました')
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'不足の報告を確認済み','evidence_commands':ids,'artifact_ids':[]},'finish-report')
        assert result.get('ok'),result
        assert store.get(run['id'])['status']=='completed' and not native.writes
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['assignment','input','candidate','reject'])
def test_shift_stale_or_rejected_results_cannot_be_confirmed(tmp_path,change):
    async def scenario():
        _,native,tools,run=shift(tmp_path)
        ids=await shift_evidence(tools,run)
        params={'ids':{'run_id':501},'query':{'candidate_id':77},'body':{},'reason':'合成試験'}
        await tools.flow.submit(run,'合成勤務案',ids,[{'operation':'shift_confirm','parameters':params}])
        review=tools.flow.review(run['id'])
        if change=='reject': await tools.flow.decide(run,review['id'],False,'担当を再確認してください')
        else:
            await tools.flow.decide(run,review['id'],True,'')
            if change=='assignment': native.data['/solve-runs/501']['assignments'][0]['staff_id']=2
            if change=='input': native.data['/constraints']['items'].append({'staff_id':1,'work_date':'2026-11-02','status':'unavailable'})
            if change=='candidate': native.data['/solve-runs/501']['selected_candidate_id']=78
        with pytest.raises(Conflict): await tools.native(run,'shift_confirm',Arguments.model_validate(params))
        assert not native.writes
    asyncio.run(scenario())


def test_absence_only_changes_selected_staff_and_day(tmp_path):
    async def scenario():
        _,native,tools,run=setup(tmp_path,'shift_absence',[{'key':'1','label':'合成人員','data':{'id':1,'name':'合成人員'}}],{'month':MONTH,'absence_date':'2026-11-02'})
        native.data['/planning/period']={'start_date':'2026-11-01','end_date_exclusive':'2026-12-01'}
        okay={'staff_id':1,'work_date':'2026-11-02','status':'unavailable','strength':'hard'}
        for changed in ({'staff_id':2},{'work_date':'2026-11-03'},{'status':'request_work'},{'strength':'soft'}):
            with pytest.raises(Conflict): await tools.flow.scoped(run,'shift_save_constraint',Arguments(body={**okay,**changed},reason='欠勤指定'))
        await tools.flow.scoped(run,'shift_save_constraint',Arguments(body=okay,reason='欠勤指定'))
    asyncio.run(scenario())


def test_lunch_generates_reviews_and_exports_every_selected_day(tmp_path):
    async def scenario():
        store,native,tools,run=lunch(tmp_path)
        # A real endpoint receipt is required even for the synthetic fixture.
        changed=await tools.execute(run['id'],0,'lunch_infer_process',{'parameters':{'ids':{'menu_id':101},'query':QUERY,'body':{'menu_version':1},'reason':'選択献立の合成工程'}},'infer-process')
        assert changed.get('ok'),changed
        for name in ('lunch_create_schedule','lunch_generate_route'):
            result=await tools.execute(run['id'],0,name,{'parameters':{'query':QUERY,'body':{'facility_id':1,'layout_id':9,'service_date':'2026-11-02'},'reason':'合成試験の作成'}},name)
            assert result.get('ok'),result
        ids=await lunch_evidence(tools,run)
        await tools.flow.submit(run,'合成献立・工程表・動線図を確認してください',ids,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        artifacts=[]
        for name,operation,key,ident in [('lunch_export_schedule','/api/school-lunch/work-schedules','schedule_id',601),('lunch_export_route','/api/school-lunch/route-diagrams','diagram_id',701)]:
            item=native.data[operation][0]
            source={k:item[k] for k in ('id','version','service_date')};source['facility_id']=1
            if key=='diagram_id':source.update(layout_id=9,layout_version=1)
            response=await tools.execute(run['id'],0,name,{'parameters':{'ids':{key:ident},'query':{**QUERY,'expected_sources':json.dumps([source])}}},name)
            assert response.get('artifact'),response
            artifacts.append(response['artifact']['id'])
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'合成工程と二帳票の確認完了','evidence_commands':ids,'artifact_ids':artifacts},'finish-lunch')
        assert result.get('ok'),result
        assert store.get(run['id'])['status']=='completed'
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['school','month','menu','partial_day','layout','version'])
def test_lunch_scope_is_not_expanded_or_substituted(tmp_path,change):
    async def scenario():
        _,native,tools,run=lunch(tmp_path)
        ids={'menu_id':101};query=dict(QUERY)
        if change=='school':query['facility_id']=2
        if change=='month':query['month']='2026-12'
        if change=='menu':ids['menu_id']=102
        if change=='partial_day':native.data['/api/school-lunch/menu-executions'].append({**deepcopy(native.data['/api/school-lunch/menu-executions'][0]),'id':102})
        if change=='version':native.data['/api/school-lunch/menu-executions'][0]['version']=2
        name='lunch_get_process';body={}
        if change=='layout':name='lunch_generate_route';ids={};body={'facility_id':1,'layout_id':10,'service_date':'2026-11-02'}
        with pytest.raises(Conflict): await tools.flow.scoped(run,name,Arguments(ids=ids,query=query,body=body,reason='合成試験'))
    asyncio.run(scenario())


def test_export_rechecks_version_after_receiving_bytes(tmp_path):
    async def scenario():
        _,native,tools,run=lunch(tmp_path,'lunch_procurement')
        evidence=[await get(tools,run,'lunch_get_procurement',query=QUERY)]
        await tools.flow.submit(run,'合成発注案',evidence,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        native.after_export=lambda:native.data['/api/school-lunch/monthly-procurement'].update(version=2)
        with pytest.raises(Conflict,match='再確認'):
            await tools.native(run,'lunch_export_procurement',Arguments(query={**QUERY,'expected_fingerprint':tools.flow.review(run['id'])['evidence'][0]['data']['fingerprint']}))
        with tools.store.tx() as db:
            assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==0
    asyncio.run(scenario())


def test_import_only_adopts_current_attached_document_after_human_approval(tmp_path):
    async def scenario():
        store,native,tools,run=lunch(tmp_path,'lunch_import')
        record(tools,run,'lunch_import_source',{'source_document_id':'doc-synthetic'},{'query':QUERY})
        ids=[await get(tools,run,'lunch_get_source_document',{'document_id':'doc-synthetic'},QUERY)]
        menus=[{'facility_name':'合成試験校','service_date':'2026-11-02','name':'合成献立','servings':10,'version':1,'status':'draft','assignees':[],'ingredients':[]}]
        params={'query':QUERY,'body':{'source_document_id':'doc-synthetic','menus':menus},'reason':'原本照合済みの合成献立'}
        with pytest.raises(Conflict,match='利用者承認'):await tools.native(run,'lunch_confirm_import',Arguments.model_validate(params))
        await tools.flow.submit(run,'原本と抽出結果を確認してください',ids,[{'operation':'lunch_confirm_import','parameters':params}])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        response=await tools.execute(run['id'],0,'lunch_confirm_import',{'parameters':params},'confirm-import')
        assert response.get('ok'),response
        ids.append(await get(tools,run,'lunch_get_menus',query=QUERY))
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'合成献立の保存と再取得を確認','evidence_commands':ids,'artifact_ids':[]},'finish-import')
        assert result.get('ok'),result
        assert store.get(run['id'])['status']=='completed'
    asyncio.run(scenario())


@pytest.mark.parametrize('task_id',[x for x in TASKS if x!='hospital_pending_faxes'])
def test_stop_fences_all_named_task_approval_and_retains_ledger(tmp_path,task_id):
    async def scenario():
        targets=[{'key':'1','label':'合成対象','data':{'id':1,'name':'合成対象'}}]
        store,_,tools,run=setup(tmp_path,task_id,targets)
        review={'id':'review','state':'awaiting_approval','signature':'test','evidence':[],'decision':None}
        tools.flow.save_review(run['id'],review)
        waiter=asyncio.create_task(tools.execute(run['id'],0,'wait_for_task_decision',{},'wait-stop'))
        await asyncio.sleep(.02)
        store.fence(run['id']);store.update(run['id'],status='rolled_back')
        with pytest.raises(Conflict):await asyncio.wait_for(waiter,2)
        with pytest.raises(Conflict):await tools.flow.decide(store.get(run['id']),'review',True,'')
        assert tools.flow.review(run['id'])['state']=='awaiting_approval'
        assert store.get(run['id'])['task_id']==task_id
    asyncio.run(scenario())


def test_selection_rejects_unknown_targets_and_partial_day(tmp_path):
    _,_,tools,_=lunch(tmp_path)
    payload={'inputs':QUERY,'choices':[{'key':str(i),'label':'合成献立','data':{'id':i,'service_date':'2026-11-02'}} for i in (101,102)],'layouts':[{'id':9,'version':1}]}
    with tools.store.tx() as db:db.execute('INSERT INTO selections VALUES(?,?,?,?)',('selection','operator','lunch_process_routes',json.dumps(payload)))
    for keys in (['999'],['101'],['101','101']):
        with pytest.raises((ValueError,Conflict)):tools.flow.selection('lunch_process_routes','operator','selection',keys,9)
    chosen=tools.flow.selection('lunch_process_routes','operator','selection',['101','102'],9)
    assert len(chosen['targets'])==2 and chosen['inputs']['layout_id']==9


def test_failed_shift_without_candidate_stays_readable_as_failure_report(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path,'shift_check',[{'key':'501:failed','label':'失敗理由','data':{'run_id':501,'candidate_id':None}}])
        native.data['/solve-runs/501']={'id':501,'target_month':MONTH,'status':'failed','candidates':[],'selected_candidate_id':None,'violations':[{'severity':'hard','message':'合成試験の人員不足'}]}
        evidence=[await get(tools,run,'shift_get_run',{'run_id':501})]
        await tools.flow.submit(run,'作成失敗の理由と不足内容です',evidence,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'確認しました')
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'失敗理由の確認完了','evidence_commands':evidence,'artifact_ids':[]},'finish-failure-report')
        assert result.get('ok'),result
        assert not native.writes and store.get(run['id'])['status']=='completed'
    asyncio.run(scenario())


def test_configured_shift_period_is_not_replaced_with_calendar_month(tmp_path):
    async def scenario():
        _,native,tools,run=shift(tmp_path)
        native.data['/planning/period'].update(start_date='2026-11-21',end_date_exclusive='2026-12-21',period_mode='fixed_start_day')
        with pytest.raises(Conflict): await tools.flow.scoped(run,'shift_get_demands',Arguments(query={'start':'2026-11-01','end':'2026-12-01'}))
        await tools.flow.scoped(run,'shift_get_demands',Arguments(query={'start':'2026-11-21','end':'2026-12-21'}))
    asyncio.run(scenario())


def test_hospital_outputs_requires_every_selected_order_and_output(tmp_path):
    async def scenario():
        _,native,tools,run=setup(tmp_path,'hospital_outputs',[{'key':'O1','label':'合成注文1','data':{'id':'O1'}},{'key':'O2','label':'合成注文2','data':{'id':'O2'}}],{})
        for ident in ('O1','O2'): native.data['/orders/'+ident+'/workflow-v2']={'order_id':ident,'state':'confirmed','revision':4,'confirmed_snapshot_id':'synthetic-'+ident}
        first=await get(tools,run,'hospital_get_workflow',{'order_id':'O1'})
        with pytest.raises(Conflict):await tools.flow.submit(run,'合成注文の帳票対象',[first],[])
        second=await get(tools,run,'hospital_get_workflow',{'order_id':'O2'})
        await tools.flow.submit(run,'二件の合成注文の帳票対象',[first,second],[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        artifacts=[]
        for ident in ('O1','O2'):
            for name in TASKS['hospital_outputs']['exports']:
                result=await tools.execute(run['id'],0,name,{'parameters':{'query':{'order_id':ident}}},name+'-'+ident)
                assert result.get('artifact'),result
                artifacts.append(result['artifact']['id'])
        await tools.flow.finish(run,artifacts)
        with pytest.raises(Conflict):await tools.flow.finish(run,artifacts[:-1])
        with pytest.raises(Conflict):await tools.flow.scoped(run,'hospital_export_labels',Arguments(query={'order_id':'other'}))
    asyncio.run(scenario())


def test_real_http_selection_source_stop_and_restore_without_starting_provider(tmp_path,monkeypatch):
    import sqlite3
    from fastapi.testclient import TestClient
    from experiments.agent_assistant.app import create_app
    from experiments.agent_assistant.tests.test_trial import prepared
    (tmp_path/'baseline').mkdir()
    _,baseline,_,_=prepared(tmp_path/'baseline',('school-lunch',))
    class Client:
        async def close(self):pass
    app=create_app({'domains':baseline.configs},tmp_path/'app',managed_client=Client())
    store=app.state.store;store.set_budget(100)
    native=Native(tmp_path/'http-native')
    native.data['/api/school-lunch/facilities']=[{'id':1,'name':'Synthetic School'}]
    async def no_start(run):pass
    monkeypatch.setattr(app.state.sandbox,'start',no_start)
    monkeypatch.setattr(app.state.sandbox,'request',native.request)
    with TestClient(app) as web:
        web.get('/')
        targets=web.post('/tasks/lunch_import/targets',json={'inputs':{'month':MONTH}})
        assert targets.status_code==200,targets.text
        selection=targets.json()
        response=web.post('/runs',json={'request_key':'http-selected-import','task_id':'lunch_import','selection_id':selection['selection_id'],'target_keys':['1'],'cap_cents':50})
        assert response.status_code==201,response.text
        run=response.json();assert run['status']=='waiting_input' and not run['session_id']
        fees=web.post('/runs/'+run['id']+'/refresh-usage')
        assert fees.status_code==200 and fees.json()['run']['spent']==0
        # Verified synthetic original; never uploaded to an OCR engine.
        import fitz
        document=fitz.open();document.new_page().insert_text((30,40),'Synthetic School / 2026-11');content=document.tobytes()
        assert 'Synthetic School' in document[0].get_text() and MONTH in document[0].get_text();document.close()
        wrong=web.post('/runs/'+run['id']+'/sources',data={'domain':'school-lunch','binding':json.dumps({'facility_id':'2','month':MONTH})},files={'file':('synthetic.pdf',content,'application/pdf')})
        assert wrong.status_code==409
        source=web.post('/runs/'+run['id']+'/sources',data={'domain':'school-lunch','binding':json.dumps({'facility_id':'1','month':MONTH})},files={'file':('synthetic.pdf',content,'application/pdf')})
        assert source.status_code==200,source.text
        folder=app.state.sandbox.path(run['id'])/'data'/'school-lunch'
        with sqlite3.connect(folder/'database.sqlite3') as db:db.execute('UPDATE quantities SET value=99')
        stopped=web.post('/runs/'+run['id']+'/stop')
        assert stopped.status_code==200 and stopped.json()['status']=='stopped'
        restored=web.post('/runs/'+run['id']+'/rollback')
        assert restored.status_code==200 and restored.json()['status']=='rolled_back',restored.text
        with sqlite3.connect(folder/'database.sqlite3') as db:assert db.execute('SELECT value FROM quantities').fetchone()[0]==7
        assert store.budget()['spent_cents']==0 and store.budget()['reserved_cents']==0
        assert web.post('/runs/'+run['id']+'/decision',json={'review_id':'old','approve':True}).status_code==409
        assert web.get('/runs/'+run['id']+'/artifacts/'+source.json()['artifact']['id']).status_code==409


def test_failed_fax_recognition_requires_separate_approval_then_quantity_review(tmp_path):
    from experiments.agent_assistant.tests.test_fax_workflow import Native as FaxNative
    async def scenario():
        source_native=FaxNative(tmp_path/'fax-files')
        source_native.sources['A']['status']='failed'
        source=source_native.sources['A']
        target={'key':'A','label':'合成の要確認FAX','data':{'id':'A','name':'synthetic.pdf','sha256':source['content_sha256'],
            'facility_id':source['facility_hint'],'week':source['week_hint'],'order_id':'OA','document_id':'DA','skip_ocr':False}}
        store,_,tools,run=setup(tmp_path,'hospital_review_faxes',[target],{})
        base=source_native.request
        async def request(run,domain,method,path,**kwargs):
            if method=='GET' and path.startswith('/outputs/'):
                return httpx.Response(200,content=b'synthetic output, not a business workbook',headers={'content-type':'application/octet-stream'})
            if path.endswith('/recognition-review'):
                return httpx.Response(200,json={'source_receipt':{'revision':source_native.wfs['OA']['revision'],'source_sha256':source['content_sha256']},
                    'cells':[{'cell':'A1','image_png_base64':'synthetic-image-not-decoded'}],'review_cells':{'A1':'0'}})
            return await base(run,domain,method,path,**kwargs)
        source_native.request=request;tools.sandbox=source_native
        evidence=[await get(tools,run,'hospital_get_recognition_review',{'order_id':'OA'})]
        receipt={'revision':1,'source_sha256':source['content_sha256']}
        params={'ids':{'order_id':'OA'},'body':{'source_receipt':receipt,'values':{'A1':'0'}},'expected_revision':1,'reason':'原本照合した合成認識値'}
        with pytest.raises(Conflict,match='利用者承認'):
            await tools.native(run,'hospital_accept_recognition',Arguments.model_validate(params))
        await tools.flow.submit(run,'合成原本セルの認識訂正',evidence,[{'operation':'hospital_accept_recognition','parameters':params}])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        adopted=await tools.execute(run['id'],0,'hospital_accept_recognition',{'parameters':params},'accept-recognition')
        assert adopted.get('ok'),adopted
        queue=await tools.faxes.refresh(run)
        assert len(queue['items'])==1 and queue['items'][0]['state']=='ready'
        review=(await tools.faxes.submit(run,'A','合成数量シートを確認してください'))['review']
        with pytest.raises(Conflict,match='利用者承認'):
            await tools.native(run,'hospital_confirm_order',Arguments(ids={'order_id':'OA'},expected_revision=2,reason='合成試験'))
        await tools.faxes.decide(run,'A',review['id'],True,'')
        for name,revision,body in [('hospital_review_sheet',2,{'sheet':review['sheet']}),('hospital_save_sheet',3,{'sheet':review['sheet']}),('hospital_bag_order',4,{}),('hospital_confirm_order',5,{})]:
            result=await tools.execute(run['id'],0,name,{'parameters':{'ids':{'order_id':'OA'},'body':body,'expected_revision':revision,'reason':'承認済みの合成数量'}},name)
            assert result.get('ok'),result
        assert (await tools.faxes.refresh(run))['items'][0]['state']=='completed'
        assert source_native.sources['A']['status']=='failed'  # Historical intake failure is never rewritten as OCR success.
        outputs=[]
        for name in ('hospital_export_labels','hospital_export_delivery_notes','hospital_export_aggregate'):
            result=await tools.execute(run['id'],0,name,{'parameters':{'query':{'order_id':'OA'}}},name)
            assert result.get('ok'),result
            outputs.append(result['artifact']['id'])
        persisted=await get(tools,run,'hospital_get_workflow',{'order_id':'OA'})
        finished=await tools.execute(run['id'],0,'finish_work',{'summary':'合成認識復旧・確定・三帳票完了','evidence_commands':[persisted],'artifact_ids':outputs},'finish-recovery')
        assert finished.get('ok'),finished
        assert store.get(run['id'])['status']=='completed'
    asyncio.run(scenario())


def test_agent_cannot_submit_a_summary_bound_to_an_old_native_read(tmp_path):
    async def scenario():
        _,native,tools,run=lunch(tmp_path,'lunch_procurement')
        evidence=[await get(tools,run,'lunch_get_procurement',query=QUERY)]
        native.data['/api/school-lunch/monthly-procurement']['rows'][0]['quantity']=8
        with pytest.raises(Conflict,match='エージェントの確認後'):
            await tools.flow.submit(run,'古い数量の合成確認結果',evidence,[])
        assert tools.flow.review(run['id']) is None
    asyncio.run(scenario())


def test_agent_does_not_receive_native_worker_lease_credentials(tmp_path):
    async def scenario():
        target={'key':'fax-synthetic','label':'合成FAX','data':{'id':'fax-synthetic','sha256':'hash','facility_id':'F1','week':'2026-11-02'}}
        _,native,tools,run=setup(tmp_path,'hospital_review_faxes',[target],{})
        native.data['/ingest/uploads/fax-synthetic']={'id':'fax-synthetic','content_sha256':'hash','status':'processing','lease_token':'native-private-value','lease_owner':'worker','storage_uri':'private-original-uri'}
        result=await tools.native(run,'hospital_get_fax',Arguments(ids={'uploaded_pdf_id':'fax-synthetic'}))
        assert result['data']=={'id':'fax-synthetic','content_sha256':'hash','status':'processing'}
        reread=await tools.flow.read(run,'hospital_get_fax',{'ids':{'uploaded_pdf_id':'fax-synthetic'}})
        assert reread==result['data']
    asyncio.run(scenario())
