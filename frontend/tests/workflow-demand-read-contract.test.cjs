const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {mount, transport, deferred} = require('./helpers/modernization-react-dom.cjs');
const PAGE = 'src/pages/orders/[id]/workflow-v2.tsx';
const BASE = '/orders/demand/workflow-v2';
const READ = `${BASE}/display?scope=summary`;
const RAW = `${BASE}/inspection?history_details=true`;
const DETAIL = `${BASE}/inspection-detail/evidence`;
async function setup(running = false, overrides = {}) {
  const t = transport(), timers = new Map();
  const state = {workflow: {order_id:'demand',revision:7,state:running?'ocr_running':'ocr_completed',
    blockers:[],warnings:[],ocr_job:{order_id:'demand',ocr_job_id:'job',workflow_attempt_id:'attempt',status:'running'},
    common_job_status:null,...overrides}, results:[{ocr_result_id:'evidence',detail_state:'not_loaded'}]};
  const payload = () => ({order_id:'demand',workflow:structuredClone(state.workflow),
    ocr_candidates:require('./helpers/ocr-candidates.cjs')('demand',structuredClone(state.results),state.workflow),
    saved_sheet:state.workflow.saved_sheet_id?{saved_sheet_id:state.workflow.saved_sheet_id,
      source_ocr_result_id:'evidence',sheet:{fields:['quantity'],header:['数量'],rows:[['001']],unknown:{preserved:true}}}:null});
  t.on('get','/auth/me',()=>({data:{role:'operator'}}));
  t.on('get','/facilities',()=>({data:{source:'canonical_json',revision:'fixture',facilities:[]}}));
  t.onWorkflowInspection(READ,()=>({data:payload()}));
  t.on('get',RAW,()=>({data:payload()}));
  t.on('get','/orders/demand',()=>({data:{id:'demand',current_version:{document_id:'pdf'},versions:[{document_id:'pdf',is_current:true}]}}));
  t.on('get','/orders/demand/document',()=>({data:new Blob(['component'])}));
  t.on('get','/orders/demand/week-options',()=>({data:{options:[]}}));
  const probe={component:'OrderWorkflowV2Page',bindings:['workflow','ocrResults','refreshAll','loadOcrResultDetail',
    'loadRawInspection','rawInspection','workflowRevisionRef','setVisibleStep','error','adoptReadWorkflow',
    'setError','setSelectedDocumentId','applyWeekValue']};
  const h=await mount(PAGE,{api:t.api,probe,route:'/orders/demand/workflow-v2',routerState:{isReady:true,query:{id:'demand'}},component(loader){
    window.setInterval=(fn)=>{timers.set(1,fn);return 1;}; window.clearInterval=id=>timers.delete(id);
    return loader.load(PAGE,probe).default;
  }});
  await h.waitFor(()=>probe.current?.workflow?.revision===7);
  await h.settle();
  return {h,t,state,probe,payload,timers};
}
test('summary detail button restores exact warning and blocked selection',async()=>{
  const f=await setup();
  try {
    f.t.on('get',DETAIL,()=>({data:{ocr_result_id:'evidence',workflow_revision:7,selectable:false,
      recognition_review:{count:1,cells:[{sheet_cell:'F11',raw_text:'1',reason:'hakodate_blank_input_nonempty_prediction'}]}}}));
    await f.h.settle(()=>f.probe.current.setVisibleStep(2));
    const button=()=>[...f.h.document.querySelectorAll('button')].find(b=>b.textContent==='詳細を表示');
    assert.ok(button());
    assert.equal(f.t.calls.filter(c=>c.url===DETAIL).length,0);
    await f.h.click(button());
    assert.match(f.h.document.body.textContent,/要確認: 1セル/);
    assert.match(f.h.document.body.textContent,/F11: 「1」/);
    assert.equal(f.probe.current.ocrResults[0].selectable,false);
    assert.equal(button(),undefined);
  } finally {await f.h.close();}
});
for (const boundary of ['pdf','auth','input','caller','current']) test(`detail delayed failure preserves current error at ${boundary}`,async()=>{
  const f=await setup(), pending=deferred(); let operation, callerCurrent=true;
  try {
    f.t.on('get',DETAIL,()=>pending.promise);
    await f.h.settle(()=>f.probe.current.setVisibleStep(2));
    if(boundary==='caller') {
      await f.h.settle(()=>{operation=f.probe.current.loadOcrResultDetail('evidence',()=>callerCurrent)
        .catch(err=>f.probe.current.setError(err.message));});
      callerCurrent=false;
    } else {
      const button=[...f.h.document.querySelectorAll('button')].find(b=>b.textContent==='詳細を表示');
      assert.ok(button);
      await f.h.click(button);
    }
    assert.equal(f.t.calls.filter(c=>c.url===DETAIL).length,1);
    if(boundary==='pdf') await f.h.settle(()=>f.probe.current.setSelectedDocumentId('successor-pdf'));
    if(boundary==='auth') await f.h.session(false);
    if(boundary==='input') {
      await f.h.settle(()=>f.probe.current.applyWeekValue('2026-W17'));
    }
    await f.h.settle(()=>f.probe.current.setError('current-owner-error'));
    assert.match(f.h.document.body.textContent,/current-owner-error/);
    await f.h.settle(async()=>{pending.reject(Error('old-detail-failure'));if(operation) await operation;});
    if(boundary==='current') {
      assert.match(f.probe.current.error,/old-detail-failure/);
      assert.match(f.h.document.body.textContent,/old-detail-failure/);
    } else {
      assert.equal(f.probe.current.error,'current-owner-error');
      assert.match(f.h.document.body.textContent,/current-owner-error/);
      assert.doesNotMatch(f.h.document.body.textContent,/old-detail-failure/);
    }
  } finally {await f.h.close();}
});
test('closed raw does not fetch or render JSON; open refresh refetches and close clears',async()=>{
  const f=await setup();
  try {
    assert.equal(f.t.calls.filter(c=>c.url===RAW).length,0);
    assert.equal(f.h.document.querySelector('.json-details pre'),null);
    const raw=f.h.document.querySelector('.json-details');
    await f.h.settle(()=>{raw.open=true;raw.dispatchEvent(new window.Event('toggle'));});
    await f.h.waitFor(()=>f.probe.current.rawInspection!==null);
    const count=f.t.calls.filter(c=>c.url===RAW).length;
    await f.h.settle(()=>f.probe.current.refreshAll());
    assert.ok(f.t.calls.filter(c=>c.url===RAW).length>count);
    await f.h.settle(()=>{raw.open=false;raw.dispatchEvent(new window.Event('toggle'));});
    assert.equal(f.probe.current.rawInspection,null);
    assert.equal(raw.querySelector('pre'),null);
  } finally {await f.h.close();}
});
for(const kind of ['detail','raw']) for(const boundary of ['revision','refresh','unmount']) {
  test(`${kind} delayed response respects ${boundary}`,async()=>{
    const f=await setup(), pending=deferred(); let operation;
    try {
      const url=kind==='detail'?DETAIL:RAW;
      f.t.on('get',url,()=>pending.promise);
      await f.h.settle(()=>{operation=kind==='detail'?f.probe.current.loadOcrResultDetail('evidence'):f.probe.current.loadRawInspection(true);});
      const signal=f.t.calls.findLast(c=>c.url===url).config.signal;
      if(boundary==='revision') await f.h.settle(()=>f.probe.current.adoptReadWorkflow({...f.state.workflow,revision:8}));
      if(boundary==='refresh') {
        f.t.on('get',url,()=>({data:kind==='raw'?f.payload():{ocr_result_id:'evidence',workflow_revision:7}}));
        await f.h.settle(()=>f.probe.current.refreshAll());
      }
      if(boundary==='unmount') await f.h.unmount();
      if(boundary!=='revision') assert.equal(signal.aborted,true);
      const expected=kind==='detail'?{ocr_result_id:'evidence',workflow_revision:7,selectable:true}:f.payload();
      await f.h.settle(async()=>{
        pending.resolve({data:expected});
        if(boundary==='revision'&&kind==='detail') {
          assert.equal(signal.aborted,true, 'revision change retires the outstanding detail request');
          assert.equal(await operation,null);
        }
        else await operation;
      });
      if(boundary==='revision'&&kind==='raw') assert.match(f.probe.current.error,/再読込/);
      if(kind==='detail') assert.equal(f.probe.current.ocrResults[0].detail_state,'not_loaded');
    } finally {await f.h.close();}
  });
}
test('progress single flight updates common status and retries completion refresh failure',async()=>{
  const f=await setup(true), pending=deferred();
  try {
    const progress=()=>({order_id:'demand',workflow_revision:7,state:f.state.workflow.state,
      ocr_job:f.state.workflow.ocr_job,common_job_status:{domain:'hospital',jobId:'job',attemptIdentity:'attempt',state:'running',sourceLabel:'running'}});
    f.t.on('get',`${BASE}/ocr-progress`,()=>pending.promise);
    const tick=f.timers.get(1);
    await f.h.settle(()=>{tick();tick();tick();});
    assert.equal(f.t.calls.filter(c=>c.url.endsWith('/ocr-progress')).length,1);
    await f.h.settle(()=>pending.resolve({data:progress()}));
    assert.deepEqual(f.probe.current.workflow.common_job_status,progress().common_job_status);
    f.state.workflow.state='ocr_completed';
    f.t.on('get',`${BASE}/ocr-progress`,()=>({data:progress()}));
    f.t.onWorkflowInspection(READ,()=>{throw Error('completion retry');});
    await f.h.settle(()=>tick());
    assert.match(f.probe.current.error,/completion retry/);
    assert.equal(f.probe.current.workflow.state,'ocr_running');
    f.t.onWorkflowInspection(READ,()=>({data:f.payload()}));
    await f.h.settle(()=>tick());
    assert.equal(f.probe.current.workflow.state,'ocr_completed');
  } finally {await f.h.close();}
});

test('current progress revision refresh failure remains visible and retries',async()=>{
  const f=await setup(true);
  try {
    f.t.on('get',`${BASE}/ocr-progress`,()=>({data:{order_id:'demand',workflow_revision:8,
      state:'ocr_running',ocr_job:f.state.workflow.ocr_job,common_job_status:null}}));
    f.t.onWorkflowInspection(READ,()=>{throw Error('revision refresh failed');});
    await f.h.settle(()=>f.timers.get(1)());
    assert.match(f.probe.current.error,/revision refresh failed/);
    assert.equal(f.probe.current.workflow.revision,7);
    f.state.workflow.revision=8;
    f.t.onWorkflowInspection(READ,()=>({data:f.payload()}));
    await f.h.settle(()=>f.timers.get(1)());
    assert.equal(f.probe.current.workflow.revision,8);
  } finally {await f.h.close();}
});

for (const boundary of ['refresh','revision','route']) test(`completion refresh late failure cannot replace ${boundary} successor feedback`,async()=>{
  const f=await setup(true), pending=deferred();
  try {
    f.t.on('get',`${BASE}/ocr-progress`,()=>({data:{order_id:'demand',workflow_revision:7,
      state:'ocr_completed',ocr_job:f.state.workflow.ocr_job,common_job_status:null}}));
    f.t.onWorkflowInspection(READ,()=>pending.promise);
    await f.h.settle(()=>f.timers.get(1)());
    if(boundary==='revision') f.state.workflow.revision=8;
    f.t.onWorkflowInspection(READ,()=>({data:f.payload()}));
    if(boundary==='route') {
      f.h.nav.router.asPath+='?successor=1';
      await f.h.render();
    } else {
      await f.h.settle(()=>f.probe.current.refreshAll());
    }
    await f.h.settle(()=>f.probe.current.setError('successor-feedback'));
    await f.h.settle(()=>pending.reject(Error('obsolete-completion-failure')));
    assert.equal(f.probe.current.error,'successor-feedback');
    assert.equal(f.probe.current.workflow.ocr_job.ocr_job_id,'job');
    assert.equal(f.probe.current.workflow.revision,boundary==='revision'?8:7);
    assert.equal(f.timers.has(1),true);
  } finally {await f.h.close();}
});

for (const boundary of ['route','order','unmount']) test(`pending progress aborts at ${boundary} and cannot update its former owner`,async()=>{
  const f=await setup(true), pending=deferred();
  try {
    f.t.on('get',`${BASE}/ocr-progress`,()=>pending.promise);
    await f.h.settle(()=>f.timers.get(1)());
    const signal=f.t.calls.findLast(c=>c.url.endsWith('/ocr-progress')).config.signal;
    if(boundary==='route') {f.h.nav.router.asPath+='?new=1';await f.h.render();}
    if(boundary==='order') {f.h.nav.router.isReady=false;await f.h.render();}
    if(boundary==='unmount') await f.h.unmount();
    // A route change retires the effect through its ownership dependencies.
    assert.equal(signal.aborted,true);
    const current=f.probe.current.workflow;
    await f.h.settle(()=>pending.resolve({data:{order_id:'demand',workflow_revision:7,state:'ocr_running',
      ocr_job:{...current.ocr_job,progress_step:999},common_job_status:null}}));
    assert.equal(f.probe.current.workflow,current);
    assert.notEqual(f.probe.current.workflow.ocr_job.progress_step,999);
  } finally {await f.h.close();}
});

for (const workflow of [null,{order_id:'foreign',revision:7}]) test(`current malformed workflow ${JSON.stringify(workflow)} rejects without adoption`,async()=>{
  const f=await setup();
  try {
    const current=f.probe.current.workflow;
    f.t.onWorkflowInspection(READ,()=>({data:{...f.payload(),workflow}}));
    await f.h.settle(()=>assert.rejects(f.probe.current.refreshAll(),/読取応答/));
    assert.equal(f.probe.current.workflow,current);
  } finally {await f.h.close();}
});

for (const header of [undefined, {id:'foreign'}]) test(`invalid order header ${JSON.stringify(header)} cannot replace current state`,async()=>{
  const f=await setup();
  try {
    const current=f.probe.current.workflow;
    f.t.on('get',READ,()=>({data:{...f.payload(),order_header:header}}));
    await f.h.settle(()=>assert.rejects(f.probe.current.refreshAll()));
    assert.equal(f.probe.current.workflow,current);
    assert.equal(f.t.calls.filter(call=>call.url==='/orders/demand').length,0);
  } finally {await f.h.close();}
});

test('same revision detail refresh generation rejects old bytes after newer detail adoption',async()=>{
  const f=await setup(), pending=deferred(); let old;
  try {
    f.t.on('get',DETAIL,()=>pending.promise);
    await f.h.settle(()=>{old=f.probe.current.loadOcrResultDetail('evidence');});
    const signal=f.t.calls.findLast(c=>c.url===DETAIL).config.signal;
    await f.h.settle(()=>f.probe.current.refreshAll());
    assert.equal(signal.aborted,true);
    f.t.on('get',DETAIL,()=>({data:{ocr_result_id:'evidence',workflow_revision:7,selectable:false,artifact_digest:'current-detail'}}));
    await f.h.settle(()=>f.probe.current.loadOcrResultDetail('evidence'));
    const current=f.probe.current.ocrResults;
    await f.h.settle(async()=>{
      pending.resolve({data:{ocr_result_id:'evidence',workflow_revision:7,selectable:true,artifact_digest:'stale-detail'}});
      await old;
    });
    assert.equal(f.probe.current.ocrResults,current);
    assert.equal(current[0].artifact_digest,'current-detail');
    assert.equal(current[0].selectable,false);
  } finally {await f.h.close();}
});

const source = fs.readFileSync(path.join(__dirname, "../src/pages/orders/[id]/workflow-v2.tsx"), "utf8");

test("workflow demand reads retain filtered summaries and defer candidate details", () => {
  assert.match(source, /display\?scope=\$\{displayScope\}/);
  assert.match(source, /displayScope !== "summary"/);
  assert.match(source, /loaded_sections\?\.includes\("sheet"\)/);
  assert.match(source, /inspection-detail\/\$\{ocrResultId\}/);
  assert.match(source, /workflow_revision !== revision/);
  assert.match(source, /状態更新のため再読込が必要です/);
  assert.match(source, /ocrDetailAbortRef\.current\?\.abort\(\)/);
  assert.match(source, /item\.detail_state === "not_loaded"/);
  assert.match(source, /item\.detail_state === "not_loaded" \|\| \(item\.selectable !== true/);
  assert.doesNotMatch(source, /setOcrResults\(inspectionData\.ocr_results\)/);
});

for (const state of ['sheet_saved', 'confirmed']) test(`${state} initial view does not read OCR detail, saved cells or PDF`, async () => {
  const f = await setup(false, {state, selected_ocr_result_id:'evidence', saved_sheet_id:'saved',
    ...(state === 'confirmed' ? {confirmed_snapshot_id:'snapshot'} : {})});
  try {
    assert.ok(f.t.calls.some(call => call.url === `${BASE}/display?scope=output`));
    assert.equal(f.t.calls.some(call => call.url === DETAIL || call.url.endsWith('/document')
      || call.url === `${BASE}/display?scope=sheet` || call.url === `${BASE}/display?scope=ocr`), false);
    await f.h.settle(() => f.probe.current.setVisibleStep(2));
    assert.ok(f.t.calls.some(call => call.url === `${BASE}/display?scope=ocr`));
    assert.equal(f.t.calls.some(call => call.url === DETAIL), false);
    await f.h.settle(() => f.probe.current.setVisibleStep(3));
    assert.ok(f.t.calls.some(call => call.url === `${BASE}/display?scope=sheet`));
    assert.ok(f.t.calls.some(call => call.url === DETAIL));
    assert.equal(f.h.document.querySelector('input[data-sheet-row="0"][data-sheet-col="0"]').value, '001');
  } finally { await f.h.close(); }
});

test("workflow progress polling is owned, single-flight, and refreshes on completion", () => {
  assert.match(source, /workflow-v2\/ocr-progress/);
  assert.match(source, /let polling=false/);
  assert.match(source, /if \(!current\(\) \|\| polling\) return/);
  assert.match(source, /const pendingRefresh=refreshAll\(readCurrent, native \? nativeOwner! : \{jobId,attemptId:attemptId as string\}\)/);
  assert.match(source, /return await pendingRefresh/);
  assert.match(source, /polling=false/);
});

test("raw inspection has an open-only revision fence", () => {
  assert.match(source, /rawInspectionRequestRef/);
  assert.match(source, /rawInspectionAbortRef\.current\?\.abort\(\)/);
  assert.match(source, /rawInspectionOpenRef\.current/);
  assert.match(source, /response\.data\.workflow\?\.revision !== revision/);
  assert.match(source, /onToggle=\{\(event\) => void loadRawInspection\(event\.currentTarget\.open\)\}/);
  assert.match(source, /rawInspection \? <pre>\{rawInspectionJson\}<\/pre>/);
  assert.match(source, /if \(adopted && rawInspectionOpenRef\.current\) void loadRawInspection\(true\)/);
});
