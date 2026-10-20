"""Explicit synthetic UI exercise. No Claude, OCR, solver or real business APIs.

Run only as a separate localhost test process; never enabled by application env.
"""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sqlite3

import uvicorn
from fastapi.responses import HTMLResponse

from experiments.agent_assistant.app import create_app,trial_cookie_name
from experiments.agent_assistant.tests.test_selected_tasks import Native, MONTH, QUERY, record, get, shift_evidence, lunch_evidence
from experiments.agent_assistant.catalog import OPERATIONS

ROOT=Path(__file__).resolve().parents[3]/'tmp'/'agent-assistant-trial'/('ui-fixture-'+datetime.now().strftime('%Y%m%dT%H%M%S'))
ROOT.mkdir(parents=True)
configs={}
for domain in ('hospital','shift','school-lunch'):
    baseline=ROOT/('baseline-'+domain);baseline.mkdir()
    (baseline/'trial-manifest.json').write_text(json.dumps({'purpose':'sawa-agent-isolated-trial','data':'synthetic_ui_fixture_not_real_work'}))
    with sqlite3.connect(baseline/'database.sqlite3') as db:
        db.execute('CREATE TABLE quantities(value INTEGER)');db.execute('INSERT INTO quantities VALUES(7)')
    configs[domain]={'baseline':str(baseline)}

class Client:
    async def close(self):pass

app=create_app({'domains':configs},ROOT/'runtime',managed_client=Client())
store=app.state.store;tools=app.state.tools;managed=app.state.managed
store.set_budget(1000)
managed.missing=lambda:[]
native=Native(ROOT/'synthetic-native')
menus=[{'id':101,'name':'画面試験用の献立','facility':{'id':1,'name':'合成試験校'},'service_date':'2026-11-02','version':1,'servings':10}]
native.data.update({
 '/staff':{'items':[{'id':1,'name':'画面試験用の職員'}]},'/constraints':{'items':[]},'/optimization-settings':{'revision':1},
 '/planning/period':{'target_month':MONTH,'start_date':'2026-11-01','end_date_exclusive':'2026-12-01','period_mode':'calendar_month'},
 '/planning/demands':{'items':[{'work_date':'2026-11-02','required_count':1}]},'/planning/workdays':{'items':[{'work_date':'2026-11-02','working':True}]},
 '/solve-runs/501':{'id':501,'target_month':MONTH,'status':'succeeded','selected_candidate_id':77,'confirmed':False,'confirmed_at':None,
   'assignments':[{'staff_id':1,'staff_name':'画面試験用の職員','work_date':'2026-11-02','shift_template':'試験勤務','position':'試験担当'}],
   'candidates':[{'id':77}],'candidate_validation':{'blocking':False}},'/candidates/77/validation':{'blocking':False,'violations':[]},
 '/api/school-lunch/facilities':[{'id':1,'name':'合成試験校'}],'/api/school-lunch/menu-executions':menus,
 '/api/school-lunch/menu-executions/101/process':{'menu':menus[0],'revision':1,'rows':[{'operation':'画面試験用の調理工程'}]},
 '/api/school-lunch/work-schedules':[{'id':601,'facility':{'id':1,'name':'合成試験校'},'service_date':'2026-11-02','version':1,'rows':[{'operation':'画面試験用の作業'}],'stale':False}],
 '/api/school-lunch/route-diagrams':[{'id':701,'facility_id':1,'service_date':'2026-11-02','version':1,'layout_id':9,'layout_version':1,'routes':[{'label':'画面試験の動線'}],'blockers':[]}],
 '/api/school-lunch/route-diagrams/layouts':[{'id':9,'name':'画面試験用の図面','version':1}],
})
async def no_start(run):pass
app.state.sandbox.start=no_start
app.state.sandbox.request=native.request

@app.middleware('http')
async def synthetic_banner(request,call_next):
    if request.url.path in ('/','/admin'):
        original=Path(__file__).resolve().parents[1]/'preview.html'
        html=original.read_text().replace('<main class="assistant-page">','<main class="assistant-page"><p class="notice"><strong>画面試験用の合成データです。Claude・実業務API・OCR・ソルバーは使用していません。シフト案・工程表の画面操作試験専用です。</strong></p>')
        html=html.replace('Claude接続設定があります。','合成テスト用の接続です（Claude接続なし）。')
        response=HTMLResponse(html);response.set_cookie(trial_cookie_name(request),app.state.token,httponly=True,samesite='strict');return response
    return await call_next(request)

async def work(run):
    try:
        if run['task_id']=='shift_next_month':
            record(tools,run,'shift_solve',{'solve_run_id':501},{'body':{'target_month':MONTH}})
            evidence=await shift_evidence(tools,run)
            params={'ids':{'run_id':501},'query':{'candidate_id':77},'body':{'candidate_id':77,'candidate_revision':'a'*64,'input_revision':'b'*64},'reason':'画面試験の合成案'}
            actions=[{'operation':'shift_confirm','parameters':params}]
        elif run['task_id']=='lunch_process_routes':
            for name,ids in [('lunch_infer_process',{'menu_id':101}),('lunch_create_schedule',{}),('lunch_generate_route',{})]:
                result=await tools.execute(run['id'],0,name,{'parameters':{'ids':ids,'query':QUERY,'body':{'facility_id':1,'layout_id':9,'service_date':'2026-11-02'},'reason':'画面試験用の合成作成'}},name)
                if not result.get('ok'):raise ValueError(result)
            evidence=await lunch_evidence(tools,run);actions=[]
        else:raise ValueError('この合成画面試験ではシフト案と工程表だけを実行できます')
        await tools.flow.submit(run,'画面試験用の合成結果です。対象・内容を確認して承認してください。',evidence,actions)
        result=await tools.execute(run['id'],0,'wait_for_task_decision',{},'wait-human')
        if result.get('state')!='approved':
            store.event(run['id'],'notice',{'message':'合成試験の訂正指示を受け取りました。'});return
        outputs=[]
        if run['task_id']=='shift_next_month':
            await tools.execute(run['id'],0,'shift_confirm',{'parameters':params},'confirm')
            result=await tools.execute(run['id'],0,'shift_export',{'parameters':{'ids':{'run_id':501},'query':{'candidate_id':77}}},'export')
            outputs.append(result['artifact']['id'])
            evidence.append(await get(tools,run,'shift_get_run',{'run_id':501},{'candidate_id':77}))
        else:
            for name,op,key in [('lunch_export_schedule','/api/school-lunch/work-schedules','schedule_id'),('lunch_export_route','/api/school-lunch/route-diagrams','diagram_id')]:
                item=native.data[op][0];source={k:item[k] for k in ('id','version','service_date')};source['facility_id']=1
                if key=='diagram_id':source.update(layout_id=9,layout_version=1)
                result=await tools.execute(run['id'],0,name,{'parameters':{'ids':{key:item['id']},'query':{**QUERY,'expected_sources':json.dumps([source])}}},name)
                outputs.append(result['artifact']['id'])
        result=await tools.execute(run['id'],0,'finish_work',{'summary':'合成データによる画面操作を完了しました。実業務の検証結果ではありません。','evidence_commands':evidence,'artifact_ids':outputs},'finish')
        if not result.get('ok'):raise ValueError(result)
        store.update(run['id'],reserved=0)
    except asyncio.CancelledError:raise
    except Exception as exc:
        store.event(run['id'],'error',{'message':'合成画面試験エラー：'+str(exc)});store.update(run['id'],status='failed',reserved=0)

async def start(run):
    store.update(run['id'],status='running')
    managed.tasks[run['id']]=asyncio.create_task(work(store.get(run['id'])))
managed.start=start
async def stop(ident):
    store.fence(ident)
    task=managed.tasks.get(ident)
    if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    await app.state.sandbox.stop(ident)
    store.update(ident,status='stopped',reserved=0)
managed.stop=stop

if __name__=='__main__':
    print('SYNTHETIC_UI_PROOF_ROOT='+str(ROOT),flush=True)
    uvicorn.run(app,host='127.0.0.1',port=8148)
