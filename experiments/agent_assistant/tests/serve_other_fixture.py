"""Synthetic selector -> chat -> approval -> output exercise for other tasks.
No real Claude, OCR, solver, procurement calculation or business data.
"""
import asyncio
import json
import httpx
import uvicorn
from experiments.agent_assistant.tests import serve_chat_fixture as chat
from experiments.agent_assistant.tests.serve_ui_fixture import app,store,tools,managed,native,ROOT
from experiments.agent_assistant.tests.test_selected_tasks import QUERY,get,shift_evidence

native.data.update({
 '/orders':{'items':[{'id':'O1','facility_name':'合成試験施設','week_start':'2026-11-02'}],'has_more':False},
 '/orders/O1/workflow-v2':{'order_id':'O1','state':'confirmed','revision':1,'confirmed_snapshot_id':'synthetic-snapshot','blockers':[],'ocr_job':{'status':'failed'}},
 '/solve-runs':{'items':[{'id':501,'target_month':'2026-11'}]},
 '/api/school-lunch/monthly-procurement':{'facility':{'id':1,'name':'合成試験校'},'month':'2026-11','version':1,'menu_versions':{'101':'1'},'inventory_ready':True,'issues':[],
  'ingredients':[{'name':'合成食材','opening_stock':0,'unit_price':100,'supplier':'合成仕入先'}],'orders':[{'ingredient':'合成食材','quantity':10,'unit':'本'}],'month_cost':'1000.00'},
})
original=native.request
async def synthetic_request(run,domain,method,path,**kwargs):
 if method=='POST' and path=='/solve':
  native.data['/solve-runs/501'].update(confirmed=False,confirmed_at=None)
  return httpx.Response(200,json={'solve_run_id':501})
 if method=='POST' and path=='/constraints':
  row={'id':900,**kwargs['body']};native.data['/constraints']['items'].append(row)
  return httpx.Response(200,json=row)
 if path.endswith('/export/month'):
  return httpx.Response(200,content=b'synthetic workbook bytes',headers={'content-type':'application/octet-stream'})
 return await original(run,domain,method,path,**kwargs)
native.request=synthetic_request
app.state.sandbox.request=synthetic_request

async def work(run):
 try:
  task=run['task_id'];evidence=[];actions=[];outputs=[]
  if task.startswith('shift_'):
   if task=='shift_absence':
    result=await tools.execute(run['id'],0,'shift_save_constraint',{'parameters':{'body':{'staff_id':1,'work_date':run['task_inputs']['inputs']['absence_date'],'status':'unavailable','strength':'hard'},'reason':'利用者が選択した合成欠勤'}},'absence')
    if not result.get('ok'):raise ValueError(result)
   if task in ('shift_next_month','shift_wishes','shift_absence'):
    result=await tools.execute(run['id'],0,'shift_solve',{'parameters':{'body':{'target_month':'2026-11'},'reason':'合成画面試験の勤務案作成'}},'solve')
    if not result.get('ok'):raise ValueError(result)
   evidence=await shift_evidence(tools,run)
   params={'ids':{'run_id':501},'query':{'candidate_id':77},'body':{'candidate_id':77,'candidate_revision':'a'*64,'input_revision':'b'*64},'reason':'利用者が承認した合成勤務案'}
   if task in ('shift_next_month','shift_wishes','shift_absence'):actions=[{'operation':'shift_confirm','parameters':params}]
  elif task=='hospital_outputs':
   evidence=[await get(tools,run,'hospital_get_workflow',{'order_id':'O1'})]
  elif task=='lunch_procurement':
   evidence=[await get(tools,run,'lunch_get_procurement',query=QUERY)]
  elif task=='lunch_month_exports':
   evidence=[await get(tools,run,name,query=QUERY) for name in ('lunch_get_menus','lunch_get_schedules','lunch_get_routes')]
  else:raise ValueError('この合成画面試験では勤務案・帳票・月間発注案を確認できます')
  await tools.flow.submit(run,'【合成画面試験】'+run['instruction']+'の結果です。対象・内容を確認してください。',evidence,actions)
  decision=await tools.execute(run['id'],0,'wait_for_task_decision',{},'wait-review')
  if decision.get('state')!='approved':return
  for action in actions:
   result=await tools.execute(run['id'],0,action['operation'],{'parameters':action['parameters']},action['operation'])
   if not result.get('ok'):raise ValueError(result)
  if task.startswith('shift_') and task!='shift_check':
   result=await tools.execute(run['id'],0,'shift_export',{'parameters':{'ids':{'run_id':501},'query':{'candidate_id':77}}},'export')
   if not result.get('ok'):raise ValueError(result)
   outputs.append(result['artifact']['id']);evidence.append(await get(tools,run,'shift_get_run',{'run_id':501},{'candidate_id':77}))
  elif task=='hospital_outputs':
   for name in ('hospital_export_labels','hospital_export_delivery_notes','hospital_export_aggregate'):
    result=await tools.execute(run['id'],0,name,{'parameters':{'query':{'order_id':'O1'}}},name)
    if not result.get('ok'):raise ValueError(result)
    outputs.append(result['artifact']['id'])
  elif task=='lunch_procurement':
   value=await tools.flow.read(run,'lunch_get_procurement',{'query':QUERY})
   result=await tools.execute(run['id'],0,'lunch_export_procurement',{'parameters':{'query':{**QUERY,'expected_fingerprint':value['fingerprint']}}},'export')
   if not result.get('ok'):raise ValueError(result)
   outputs.append(result['artifact']['id'])
  elif task=='lunch_month_exports':
   for name,path in [('lunch_export_month_schedules','/api/school-lunch/work-schedules'),('lunch_export_month_routes','/api/school-lunch/route-diagrams')]:
    sources=[]
    for item in native.data[path]:
     source={k:item[k] for k in ('id','version','service_date')};source['facility_id']=1
     if 'routes' in name:source.update(layout_id=9,layout_version=1)
     sources.append(source)
    result=await tools.execute(run['id'],0,name,{'parameters':{'query':{**QUERY,'expected_sources':json.dumps(sources)}}},name)
    if not result.get('ok'):raise ValueError(result)
    outputs.append(result['artifact']['id'])
  result=await tools.execute(run['id'],0,'finish_work',{'summary':'【合成画面試験】保存・出力が完了しました。実業務の検証結果ではありません。','evidence_commands':evidence,'artifact_ids':outputs},'finish')
  if not result.get('ok'):raise ValueError(result)
  store.update(run['id'],reserved=0)
 except asyncio.CancelledError:raise
 except Exception as exc:
  store.event(run['id'],'error',{'message':'合成画面試験：'+str(exc)});store.update(run['id'],status='failed',reserved=0)
chat.work=work
async def start(run):
 store.update(run['id'],status='running')
 await chat.ask(store.get(run['id']),'synthetic-question-1',{'question':'【合成画面試験】選択した対象で処理を進めますか？','options':['この対象で進める','作業を停止する']})
managed.start=start
if __name__=='__main__':
 print('SYNTHETIC_OTHER_ROOT='+str(ROOT),flush=True)
 uvicorn.run(app,host='127.0.0.1',port=8148)
