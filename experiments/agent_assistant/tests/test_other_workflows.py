"""Other selected workflows: full completion contracts with synthetic native data."""
import asyncio
from copy import deepcopy
import json

import pytest
from experiments.agent_assistant.tests.test_selected_tasks import setup, shift, shift_evidence, lunch, record, get, QUERY
from experiments.agent_assistant.tools import Arguments, Tools
from experiments.agent_assistant.tasks import TASKS,public_tasks
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.conversation import conversation,pending_question

async def finish(tools,store,run,evidence,artifacts):
    result=await tools.execute(run['id'],0,'finish_work',{'summary':'合成試験の保存・出力を照合しました','evidence_commands':evidence,'artifact_ids':artifacts},'finish')
    assert result.get('ok'),result
    assert store.get(run['id'])['status']=='completed'

@pytest.mark.parametrize('task_id',['shift_next_month','shift_wishes','shift_absence','shift_export'])
def test_each_shift_task_saves_and_exports_after_approval(tmp_path,task_id):
    async def scenario():
        store,native,tools,run=shift(tmp_path,task_id)
        if task_id=='shift_absence':
            context=run['task_inputs'];context['targets']=[{'key':'1','label':'合成人員','data':{'id':1,'name':'合成人員'}}];context['inputs']['absence_date']='2026-11-02'
            store.update(run['id'],task_inputs=context);run=store.get(run['id'])
            native.data['/constraints']={'items':[{'id':1,'staff_id':1,'work_date':'2026-11-02','status':'unavailable','strength':'hard'}]}
        if task_id in ('shift_wishes','shift_absence'):record(tools,run,'shift_solve',{'solve_run_id':501},{'body':{'target_month':'2026-11'}})
        evidence=await shift_evidence(tools,run)
        params={'ids':{'run_id':501},'query':{'candidate_id':77},'body':{'candidate_id':77},'reason':'合成試験の承認対象'}
        actions=[] if task_id=='shift_export' else [{'operation':'shift_confirm','parameters':params}]
        await tools.flow.submit(run,'合成試験の勤務表を確認してください',evidence,actions)
        review=tools.flow.review(run['id'])
        # A correction goes back through a fresh review before confirmation.
        await tools.flow.decide(run,review['id'],False,'担当を再確認してください')
        await tools.flow.submit(run,'合成試験で担当を再確認しました',evidence,actions)
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        if actions:
            result=await tools.execute(run['id'],0,'shift_confirm',{'parameters':params},'confirm')
            assert result.get('ok'),result
        output=await tools.execute(run['id'],0,'shift_export',{'parameters':{'ids':{'run_id':501},'query':{'candidate_id':77}}},'export')
        assert output['artifact']['name'].startswith('勤務表')
        evidence.append(await get(tools,run,'shift_get_run',{'run_id':501},{'candidate_id':77}))
        await finish(tools,store,run,evidence,[output['artifact']['id']])
        messages=conversation(store.events(run['id']))
        assert any('訂正' in x['text'] and x['role']=='user' for x in messages)
    asyncio.run(scenario())


def test_confirmed_order_history_is_not_a_current_blocker(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path,'hospital_outputs',[{'key':'O1','label':'合成注文','data':{'id':'O1'}}],{})
        data={'order_id':'O1','state':'confirmed','revision':4,'confirmed_snapshot_id':'confirmed-synthetic','blockers':[],
              'ocr_job':{'status':'failed'},'common_job_status':{'status':'failed'}}
        native.data['/orders/O1/workflow-v2']=data
        evidence=[await get(tools,run,'hospital_get_workflow',{'order_id':'O1'})]
        await tools.flow.submit(run,'確定済みの合成注文を確認してください',evidence,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        outputs=[]
        for name in TASKS['hospital_outputs']['exports']:
            result=await tools.execute(run['id'],0,name,{'parameters':{'query':{'order_id':'O1'}}},name)
            assert result.get('ok'),result
            outputs.append(result['artifact']['id'])
        await finish(tools,store,run,evidence,outputs)
        assert native.data['/orders/O1/workflow-v2']==data
        assert store.command(run['id'],evidence[0],{'name':'hospital_get_workflow','arguments':{'parameters':{'ids':{'order_id':'O1'},'query':{}}}})['data']['ocr_job']['status']=='failed'
    asyncio.run(scenario())


@pytest.mark.parametrize('change',[{'confirmed_snapshot_id':None},{'state':'sheet_review'},{'blockers':['current error']},{'stale':True}])
def test_current_order_problems_still_block(change):
    data={'state':'confirmed','confirmed_snapshot_id':'snapshot','blockers':[],'ocr_job':{'status':'failed'},**change}
    assert Tools.unresolved_evidence('hospital_get_workflow',data)
    assert Tools.unresolved_evidence('hospital_get_recognition_review',data)


def test_procurement_approval_binds_exact_calculation_and_finishes(tmp_path):
    async def scenario():
        store,native,tools,run=lunch(tmp_path,'lunch_procurement')
        evidence=[await get(tools,run,'lunch_get_procurement',query=QUERY)]
        await tools.flow.submit(run,'合成発注案を確認してください',evidence,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        with pytest.raises(Conflict):await tools.flow.gate(run,'lunch_export_procurement',Arguments(query=QUERY))
        result=await tools.flow.read(run,'lunch_get_procurement',{'query':QUERY})
        params={'query':{**QUERY,'expected_fingerprint':result['fingerprint']}}
        output=await tools.execute(run['id'],0,'lunch_export_procurement',{'parameters':params},'export')
        assert output.get('ok'),output
        assert output['artifact']['name'].startswith('月間食材必要量')
        await finish(tools,store,run,evidence,[output['artifact']['id']])
    asyncio.run(scenario())


def test_month_exports_include_every_day_and_finish(tmp_path):
    async def scenario():
        store,native,tools,run=lunch(tmp_path,'lunch_month_exports')
        for path in ('/api/school-lunch/menu-executions','/api/school-lunch/work-schedules','/api/school-lunch/route-diagrams'):
            item=deepcopy(native.data[path][0]);item['id']+=1;item['service_date']='2026-11-03';native.data[path].append(item)
        evidence=[await get(tools,run,name,query=QUERY) for name in ('lunch_get_menus','lunch_get_schedules','lunch_get_routes')]
        await tools.flow.submit(run,'合成二日分の月間帳票を確認してください',evidence,[])
        await tools.flow.decide(run,tools.flow.review(run['id'])['id'],True,'')
        outputs=[]
        for name,path in [('lunch_export_month_schedules','/api/school-lunch/work-schedules'),('lunch_export_month_routes','/api/school-lunch/route-diagrams')]:
            sources=[]
            for item in native.data[path]:
                source={k:item[k] for k in ('id','version','service_date')};source['facility_id']=1
                if 'routes' in name:source.update(layout_id=9,layout_version=1)
                sources.append(source)
            output=await tools.execute(run['id'],0,name,{'parameters':{'query':{**QUERY,'expected_sources':json.dumps(sources)}}},name)
            assert output.get('ok'),output
            outputs.append(output['artifact']['id'])
        with pytest.raises(Conflict):await tools.flow.finish(run,outputs[:-1])
        await finish(tools,store,run,evidence,outputs)
    asyncio.run(scenario())


def test_result_approval_does_not_answer_another_pending_question():
    events=[{'id':1,'kind':'tool_result','payload':{'result':{'question':'不足情報は？','command_id':'q','options':['A','B']}}},
            {'id':2,'kind':'fax_decision','payload':{'fax_id':'fax-1','approve':True,'message':''}}]
    assert pending_question(events)['question_id']=='q'
    events.append({'id':3,'kind':'operator_reply','payload':{'message':'A'}})
    assert pending_question(events) is None


def test_public_descriptions_avoid_internal_operations():
    for task in public_tasks():
        assert not any(x in task['description'] for x in ('OR-Tools','unavailable','target_month','get_schedule_draft'))


def test_local_preview_cookies_do_not_log_each_other_out(tmp_path):
    from fastapi.testclient import TestClient
    from experiments.agent_assistant.app import create_app
    first=create_app({},tmp_path/'first');second=create_app({},tmp_path/'second')
    with TestClient(first,base_url='http://127.0.0.1:8128') as a, TestClient(second,base_url='http://127.0.0.1:8148') as b:
        a.get('/')
        b.cookies.update(a.cookies)
        b.get('/')
        a.cookies.update(b.cookies)
        assert a.get('/config').status_code==200
        assert b.get('/config').status_code==200
        assert a.cookies.get('sawa_trial_8128') != a.cookies.get('sawa_trial_8148')
