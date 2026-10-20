"""Daily three-output workflow, synthetic native responses only."""
import asyncio
from copy import deepcopy
import json

import pytest
from experiments.agent_assistant.tests.test_selected_tasks import lunch, record, get, lunch_evidence, QUERY
from experiments.agent_assistant.tools import Arguments
from experiments.agent_assistant.tasks import public_tasks
from experiments.agent_assistant.store import Conflict
from experiments.agent_assistant.tests.daily_question_fixture import install, complete

PRICE_PATH='/implementation-price-tables/source-drafts/'+'a'*32

def setup(tmp_path):
    store,native,tools,run=lunch(tmp_path,'lunch_daily_bundle')
    context=run['task_inputs'];context['inputs'].update(service_date='2026-11-02',layout_id=9,layout_version=1)
    store.update(run['id'],task_inputs=context);run=store.get(run['id'])
    payload={'facility_id':1,'implementation_date':'2026-11-02','source_menu_execution_ids':[101]}
    native.data[PRICE_PATH]=payload
    native.data[PRICE_PATH+'/preview']={'draft_id':'a'*32,'payload':payload,'fingerprint':'b'*64,'preview':{'schools':[{'name':'合成試験校','total_price':0,'rows':[{'total_price':0}]}]}}
    for name,body,ids in [('lunch_infer_process',{}, {'menu_id':101}),('lunch_create_schedule',{'service_date':'2026-11-02'},{}),('lunch_generate_route',{'service_date':'2026-11-02'},{}),('lunch_save_price_source',payload,{})]:
        record(tools,run,name,{'draft_id':'a'*32} if 'price' in name else {'ok':True},{'ids':ids,'query':QUERY,'body':body})
    install(native)
    return store,native,tools,run


def test_daily_selector_only_includes_selected_day_and_all_dishes(tmp_path):
    store,native,tools,run=setup(tmp_path)
    native.data['/api/school-lunch/menu-executions'].append(dict(native.data['/api/school-lunch/menu-executions'][0],id=102,service_date='2026-11-03'))
    async def scenario():
        selected=await tools.flow.options('lunch_daily_bundle',{'facility_id':1,'service_date':'2026-11-02'},'operator','')
        assert [x['key'] for x in selected['choices']]==['101']
        context=tools.flow.selection('lunch_daily_bundle','operator',selected['selection_id'],['101'])
        assert context['inputs']['layout_id']==9
        assert context['inputs']['month']=='2026-11'
        assert context['inputs']['service_date']=='2026-11-02'
    asyncio.run(scenario())
    tasks=public_tasks()
    assert not any(t['id'] in ('lunch_process_routes','lunch_regenerate') for t in tasks)
    assert next(t for t in tasks if t['id']=='lunch_daily_bundle')['date']
    assert next(t for t in tasks if t['id']=='lunch_import')['month']


def test_three_outputs_required_and_stale_price_blocked(tmp_path):
    async def scenario():
        store,native,tools,run=setup(tmp_path)
        await complete(tools,run)
        evidence=await lunch_evidence(tools,run)
        with pytest.raises(Conflict,match='実施価格'):await tools.flow.submit(run,'三帳票',evidence,[])
        evidence.append(await get(tools,run,'lunch_get_price_source',{'draft_id':'a'*32},QUERY))
        evidence.append(await get(tools,run,'lunch_get_price_result',{'draft_id':'a'*32},QUERY))
        await tools.flow.submit(run,'三帳票の合成結果',evidence,[])
        review=tools.flow.review(run['id'])
        await tools.flow.decide(run,review['id'],True,'')
        artifacts=[]
        for name,path,key in [('lunch_export_schedule','/api/school-lunch/work-schedules','schedule_id'),('lunch_export_route','/api/school-lunch/route-diagrams','diagram_id')]:
            item=native.data[path][0];source={k:item[k] for k in ('id','version','service_date')};source['facility_id']=1
            if key=='diagram_id':source.update(layout_id=9,layout_version=1)
            result=await tools.execute(run['id'],0,name,{'parameters':{'ids':{key:item['id']},'query':{**QUERY,'expected_sources':json.dumps([source])}}},name)
            assert result.get('ok'),result
            artifacts.append(result['artifact']['id'])
        with pytest.raises(Conflict,match='帳票'):await tools.flow.finish(run,artifacts)
        args=Arguments(ids={'draft_id':'a'*32},query={**QUERY,'expected_fingerprint':'c'*64})
        with pytest.raises(Conflict,match='版'):await tools.flow.gate(run,'lunch_export_price',args)
        args.query['expected_fingerprint']='b'*64
        result=await tools.execute(run['id'],0,'lunch_export_price',{'parameters':args.model_dump()},'price-export')
        assert result.get('ok'),result
        artifacts.append(result['artifact']['id'])
        await tools.flow.finish(run,artifacts)
        finished=await tools.execute(run['id'],0,'finish_work',{'summary':'合成三帳票完了','evidence_commands':evidence,'artifact_ids':artifacts},'finish')
        assert finished.get('ok'),finished
        assert store.get(run['id'])['status']=='completed'
        native.data[PRICE_PATH+'/preview']['fingerprint']='d'*64
        with pytest.raises(Conflict,match='変わり'):await tools.flow.recheck(run,review)
    asyncio.run(scenario())


def test_other_school_day_or_menu_price_is_rejected(tmp_path):
    async def scenario():
        _,native,tools,run=setup(tmp_path)
        for field,value in [('facility_id',2),('implementation_date','2026-11-03'),('source_menu_execution_ids',[102])]:
            payload=deepcopy(native.data[PRICE_PATH]);native.data[PRICE_PATH][field]=value
            with pytest.raises(Conflict,match='一致'):await tools.flow.scoped(run,'lunch_get_price_result',Arguments(ids={'draft_id':'a'*32},query=QUERY))
            native.data[PRICE_PATH]=payload
    asyncio.run(scenario())


def test_multiple_layouts_need_chat_answer(tmp_path):
    store,native,tools,run=setup(tmp_path)
    context=run['task_inputs'];context['inputs']['layout_choices']=[{'id':9,'name':'図面A','version':1},{'id':10,'name':'図面B','version':2}]
    store.update(run['id'],task_inputs=context);run=store.get(run['id'])
    with pytest.raises(Conflict):tools.flow.choose_layout(run,10)
    store.event(run['id'],'operator_reply',{'message':'図面B（ID:10）'})
    tools.flow.choose_layout(run,10)
    assert store.get(run['id'])['task_inputs']['inputs']['layout_id']==10
