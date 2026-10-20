"""Daily task UI proof using explicit synthetic native data, never real costs."""
import asyncio
import json
import httpx
import uvicorn
from experiments.agent_assistant.tests import serve_chat_fixture as chat
from experiments.agent_assistant.tests.serve_ui_fixture import app, store, tools, managed, native, ROOT
from experiments.agent_assistant.tests.test_selected_tasks import QUERY, get, lunch_evidence

from experiments.agent_assistant.tests.daily_question_fixture import install
from experiments.agent_assistant.conversation import check_answer, pending_question
from experiments.agent_assistant.store import Conflict
install(native)
PRICE_ID='a'*32
PRICE_PATH='/implementation-price-tables/source-drafts/'+PRICE_ID
request=native.request
async def synthetic_request(run,domain,method,path,**kwargs):
    if method=='POST' and path=='/implementation-price-tables/source-drafts':
        payload=kwargs['body'];native.data[PRICE_PATH]=payload
        native.data[PRICE_PATH+'/preview']={'draft_id':PRICE_ID,'payload':payload,'fingerprint':'b'*64,
            'preview':{'implementation_date':'2026-11-02','schools':[{'name':'合成試験校','headcount':10,'menu_name':'画面試験用の献立','total_price':1000,'per_person_price':100,'rows':[{'name':'画面試験用の食材','total_price':1000}]}]}}
        return httpx.Response(200,json={'draft_id':PRICE_ID})
    return await request(run,domain,method,path,**kwargs)
native.request=synthetic_request
app.state.sandbox.request=synthetic_request

async def work(run):
    try:
        for name,ids in [('lunch_create_schedule',{}),('lunch_generate_route',{})]:
            result=await tools.execute(run['id'],0,name,{'parameters':{'ids':ids,'query':QUERY,'body':{'facility_id':1,'layout_id':9,'service_date':'2026-11-02'},'reason':'合成画面試験の作成'}},name)
            if not result.get('ok'):raise ValueError(result)
        payload={'facility_id':1,'implementation_date':'2026-11-02','source_menu_execution_ids':[101],
                 'corporation_name':'合成試験法人','representative_name':'合成試験担当','tax_rate':0,
                 'schools':[{'name':'合成試験校','recipient':'合成試験校','headcount':10,'headcount_confirmed':True,'menu_name':'画面試験用の献立',
                             'quantity_items':[{'name':'画面試験用の食材','unit':'本','amount_per_person':1,'cost_key':'test'}]}],
                 'cost_items':[{'name':'画面試験用の食材','key':'test','status':'confirmed','unit_price_tax_included':100,'price_unit':'円/本','origin_or_maker':'合成','supplier':'合成'}]}
        saved=await tools.execute(run['id'],0,'lunch_save_price_source',{'parameters':{'query':QUERY,'body':payload,'reason':'合成画面試験用の価格・数量'}},'save-price')
        if not saved.get('ok'):raise ValueError(saved)
        evidence=await lunch_evidence(tools,run)
        evidence.append(await get(tools,run,'lunch_get_price_source',{'draft_id':PRICE_ID},QUERY))
        evidence.append(await get(tools,run,'lunch_get_price_result',{'draft_id':PRICE_ID},QUERY))
        await tools.flow.submit(run,'【合成画面試験】作業工程表・動線図・実施価格をまとめて確認してください。',evidence,[])
        decision=await tools.execute(run['id'],0,'wait_for_task_decision',{},'wait-review')
        if decision.get('state')!='approved':return
        artifacts=[]
        for name,path,key in [('lunch_export_schedule','/api/school-lunch/work-schedules','schedule_id'),('lunch_export_route','/api/school-lunch/route-diagrams','diagram_id')]:
            item=native.data[path][0];source={k:item[k] for k in ('id','version','service_date')};source['facility_id']=1
            if key=='diagram_id':source.update(layout_id=9,layout_version=1)
            result=await tools.execute(run['id'],0,name,{'parameters':{'ids':{key:item['id']},'query':{**QUERY,'expected_sources':json.dumps([source])}}},name)
            if not result.get('ok'):raise ValueError(result)
            artifacts.append(result['artifact']['id'])
        result=await tools.execute(run['id'],0,'lunch_export_price',{'parameters':{'ids':{'draft_id':PRICE_ID},'query':{**QUERY,'expected_fingerprint':'b'*64}}},'export-price')
        if not result.get('ok'):raise ValueError(result)
        artifacts.append(result['artifact']['id'])
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'【合成画面試験】三帳票を保存・出力しました。実業務の検証結果ではありません。','evidence_commands':evidence,'artifact_ids':artifacts},'finish')
        if not result.get('ok'):raise ValueError(result)
        store.update(run['id'],reserved=0)
    except asyncio.CancelledError:raise
    except Exception as exc:
        store.event(run['id'],'error',{'message':'合成画面試験：'+str(exc)});store.update(run['id'],status='failed',reserved=0)

async def next_place(run):
    ident='daily-place-'+str(len(store.events(run['id'])))
    result=await tools.execute(run['id'],run['generation'],'ask_lunch_process_question',{},ident)
    if result.get('waiting_input'):
        store.event(run['id'],'operator_wait',{'command_id':ident,'result':result});return
    if not result.get('complete'): raise ValueError(result)
    for i,parameters in enumerate(result['process_saves']):
        saved=await tools.execute(run['id'],run['generation'],'lunch_save_process',{'parameters':parameters},'daily-save-place-'+str(i))
        if not saved.get('ok'): raise ValueError(saved)
    # Explicit fixture projection only; actual native graph projection is tested separately.
    process=native.data['/api/school-lunch/menu-executions/101/process']['document']
    names={e['id']:e['name'] for e in native.data['/api/school-lunch/menu-executions/101/process/route-options']['templates'][0]['equipment']}
    from experiments.agent_assistant.lunch_questions import material_ids
    flows=material_ids(process);materials={m['id']:m['name'] for m in process['ingredients']}
    rows=[];routes=[]
    for step in process['steps']:
        for placement in step['daily_work']['placements']:
            rows.append({'ingredient_name':'・'.join(materials[mid] for mid in placement['ingredient_ids']),'operation':step['operation'],'location':names[placement['equipment']['id']]})
    for mid,name in materials.items():
        places=[names[p['equipment']['id']] for step in process['steps'] if mid in flows[step['id']] for p in step['daily_work']['placements'] if mid in p['ingredient_ids']]
        routes.append({'label':name,'locations':places})
    native.data['/api/school-lunch/work-schedules'][0]['rows']=rows
    native.data['/api/school-lunch/route-diagrams'][0]['routes']=routes
    managed.tasks[run['id']]=asyncio.create_task(work(store.get(run['id'])))

async def start(run):
    store.update(run['id'],status='running')
    await next_place(store.get(run['id']))

async def reply(ident,message,question_id=None,option=None):
    async with tools.lock(ident):
        run=store.get(ident)
        if run['status']!='waiting_input':raise Conflict('回答待ちではありません')
        check_answer(store.events(ident),message,question_id,option)
        selected=await tools.flow.questions.validate_reply(run,message,question_id)
        tools.flow.questions.accept_reply(run,selected)
        store.event(ident,'operator_reply',{'message':message,'question_id':question_id,'option':option})
        store.update(ident,status='running')
    await next_place(store.get(ident))
managed.start=start
managed.reply=reply
if __name__=='__main__':
    print('SYNTHETIC_DAILY_ROOT='+str(ROOT),flush=True)
    uvicorn.run(app,host='127.0.0.1',port=8148)
