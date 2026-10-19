// Actual React/MUI page and production command code; synthetic HTTP, not OCR/stg proof.
const assert = require('node:assert/strict');
const test = require('node:test');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

function state(orderId='A', status='running', patch={}) {
  return { order_id:orderId, revision:status==='done'?9:8, state:status==='done'?'ocr_completed':'ocr_running',
    source:'workflow_v2', blockers:[], headline:'OCR state',
    ocr_job:{ocr_job_id:'OCR-'+orderId,order_id:orderId,status,workflow_attempt_id:'attempt-'+orderId,
      workflow_completion_revision:status==='done'?9:8,error:null,error_message:null,error_detail:null},
    common_job_status:{domain:'hospital',jobId:'OCR-'+orderId,attemptIdentity:'attempt-'+orderId,
      state:status==='done'?'succeeded':status==='failed'?'failed':'running',sourceState:status,failure:null}, ...patch };
}
function accepted(id='A') { return {accepted:true,ocr_job_id:'OCR-'+id,workflow:state(id)}; }
async function setup(ids=['A']) {
  const http=transport(), probe={component:'OrdersPage',bindings:['rerunOcrWeekGroup','weekGroups','bulkOcrBusyWeek','archiveNotice','archiveError']};
  const rows=ids.map((id,index)=>({id,facility:'FAC1',week_value:'2026-06@2026-06-28~2026-07-04',
    document:'fixture:'+id,status:'要確認',received_at:`2026-10-19T00:00:0${9-index}`}));
  http.on('get','/auth/me',()=>({data:{role:'operator'}}));
  http.on('get','/facilities',()=>({data:{source:'canonical_json',revision:'fixture-revision',facilities:[{id:'FAC1',name:'Synthetic Facility'}]}}));
  http.on('get','/orders',()=>({data:{orders:rows,has_more:false,next_cursor:null}}));
  for(const id of ids){
    let reads=0;
    http.on('get',`/orders/${id}/workflow-v2`,()=>({data:++reads===1?{order_id:id,revision:7}:state(id,'done')}));
    http.on('post',`/orders/${id}/workflow-v2/ocr-runs`,()=>({data:accepted(id)}));
  }
  const ui=await mount('src/pages/orders/index.tsx',{api:http.api,route:'/orders',probe});
  await ui.waitFor(()=>probe.current.weekGroups.length===1,'actual week group');
  return {ui,http,probe};
}
const posts=http=>http.calls.filter(x=>x.method==='post');
async function start(ctx){await ctx.ui.click(ctx.ui.button('OCR一括再実行'));}
async function finished(ctx){await ctx.ui.waitFor(()=>ctx.probe.current.bulkOcrBusyWeek==='','bulk command finish');}

test('actual button sends exact revision header and counts only matching success sequentially',async()=>{
  const c=await setup(['A','B']);
  const pending=deferred();let reads=0;
  c.http.on('get','/orders/A/workflow-v2',()=>++reads===1?{data:{order_id:'A',revision:7}}:pending.promise);
  try {
    await start(c);assert.equal(posts(c.http).length,1);
    assert.equal(posts(c.http)[0].config.headers['If-Match'],'7');
    assert.deepEqual(posts(c.http)[0].body,{mode:'hakodate',stale_action:'retry'});
    assert.equal(c.http.calls.some(x=>x.url==='/orders/B/workflow-v2'),false);
    await c.ui.settle(()=>pending.resolve({data:state('A','done')}));await finished(c);
    assert.equal(posts(c.http).length,2);assert.match(c.probe.current.archiveNotice,/2 件完了/);
    assert.equal(c.probe.current.archiveError,'');
  } finally {await c.ui.close();}
});

for(const [name,mutate] of [
  ['failed',s=>{s.state='ocr_failed';s.ocr_job.status='failed';s.common_job_status.state='failed';s.common_job_status.sourceState='failed';}],
  ['OCR blocked',s=>{s.state='ocr_blocked';s.blockers=['ocr_result_required'];}],
  ['upstream template changed',s=>{s.state='template_version_mismatch';s.blockers=['template_version_mismatch'];}],
  ['another order',s=>{s.order_id='foreign';}],
  ['another job',s=>{s.ocr_job.ocr_job_id='foreign';}],
  ['old attempt',s=>{s.ocr_job.workflow_attempt_id='old';}],
  ['native instead of workflow attempt',s=>{s.ocr_job.native_ingest={mode:'hakodate',execution_id:'native'};}],
  ['missing job',s=>{s.ocr_job=null;}],
  ['missing common status',s=>{s.common_job_status=null;}],
  ['common foreign attempt',s=>{s.common_job_status.attemptIdentity='old';}],
  ['common foreign job',s=>{s.common_job_status.jobId='old';}],
  ['common not succeeded',s=>{s.common_job_status.state=null;}],
  ['future completion revision',s=>{s.ocr_job.workflow_completion_revision=100;}],
  ['old response revision',s=>{s.revision=7;}],
  ['missing completion revision',s=>{delete s.ocr_job.workflow_completion_revision;}],
  ['job error with done',s=>{s.ocr_job.error='persist_failed';}],
  ['common failure with done',s=>{s.common_job_status.failure={message:'failed'};}],
  ['unknown raw status',s=>{s.ocr_job.status='mystery';s.common_job_status.sourceState='mystery';}],
]) test(`actual handler rejects ${name}, never counts completion or retries`,async()=>{
  const c=await setup();let reads=0;const invalid=state('A','done');mutate(invalid);
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?{order_id:'A',revision:7}:invalid}));
  try {await start(c);await finished(c);assert.equal(c.probe.current.archiveNotice,'');assert.notEqual(c.probe.current.archiveError,'');assert.equal(posts(c.http).length,1);}
  finally {await c.ui.close();}
});

for(const initial of [{order_id:'A'}, {order_id:'B',revision:7}, {order_id:'A',revision:'7'},
  {order_id:'A',revision:-1}, {order_id:'A',revision:0,state:'ocr_completed',source:'workflow_v2'},
  {order_id:'A',revision:0,state:'not_initialized',source:'wrong'}]) {
  test(`bad initial revision/order blocks POST ${JSON.stringify(initial)}`,async()=>{
    const c=await setup();c.http.on('get','/orders/A/workflow-v2',()=>({data:initial}));
    try {await start(c);await finished(c);assert.equal(posts(c.http).length,0);assert.notEqual(c.probe.current.archiveError,'');}
    finally {await c.ui.close();}
  });
}

test('explicit bootstrap zero uses If-Match 0 without guessed revision',async()=>{
  const c=await setup();let reads=0;
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?{order_id:'A',revision:0,state:'not_initialized',source:'workflow_v2_projection'}:state('A','done')}));
  try {await start(c);await finished(c);assert.equal(posts(c.http)[0].config.headers['If-Match'],'0');assert.match(c.probe.current.archiveNotice,/1 件完了/);}
  finally {await c.ui.close();}
});

for(const [name,mutate] of [
  ['missing acknowledgement',a=>{a.accepted=false;}],
  ['missing accepted attempt',a=>{delete a.workflow.ocr_job.workflow_attempt_id;}],
  ['accepted foreign order',a=>{a.workflow.order_id='foreign';}],
  ['top/nested ID disagreement',a=>{a.ocr_job_id='foreign';}],
  ['missing accepted revision',a=>{delete a.workflow.revision;}],
])test(`invalid accepted response ${name} cannot poll or count`,async()=>{
  const c=await setup();const ack=accepted();mutate(ack);
  c.http.on('post','/orders/A/workflow-v2/ocr-runs',()=>({data:ack}));
  try{await start(c);await finished(c);assert.equal(c.http.calls.filter(x=>x.url==='/orders/A/workflow-v2').length,1);assert.equal(c.probe.current.archiveNotice,'');assert.notEqual(c.probe.current.archiveError,'');}
  finally{await c.ui.close();}
});

for(const status of [409,428,503])test(`HTTP ${status} is explicit failure, no blind refresh/retry`,async()=>{
  const c=await setup();c.http.on('post','/orders/A/workflow-v2/ocr-runs',()=>{throw {response:{status,data:{detail:{error:'command_rejected'}}}};});
  try{await start(c);await finished(c);assert.equal(posts(c.http).length,1);assert.equal(c.http.calls.filter(x=>x.url==='/orders/A/workflow-v2').length,1);assert.match(c.probe.current.archiveError,/command_rejected/);assert.equal(c.probe.current.archiveNotice,'');}
  finally{await c.ui.close();}
});

test('downstream manual selection/sheet blocker does not relabel successful OCR as failure',async()=>{
  const c=await setup();let reads=0;const done=state('A','done',{state:'saved_sheet_required',blockers:['saved_sheet_required']});
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?{order_id:'A',revision:7}:done}));
  try{await start(c);await finished(c);assert.match(c.probe.current.archiveNotice,/1 件完了/);assert.equal(c.probe.current.archiveError,'');}
  finally{await c.ui.close();}
});

test('explicit accepted current attempt may be the same as initial, never inferred from time',async()=>{
  const c=await setup();let reads=0;const ack=accepted();ack.workflow.revision=8;
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?state('A'):state('A','done')}));
  c.http.on('post','/orders/A/workflow-v2/ocr-runs',()=>({data:ack}));
  try{await start(c);await finished(c);assert.match(c.probe.current.archiveNotice,/1 件完了/);assert.equal(posts(c.http)[0].config.headers['If-Match'],'8');}
  finally{await c.ui.close();}
});

test('completed native intake before a workflow acknowledgement does not block rerun',async()=>{
  const c=await setup();let reads=0;const before=state('A','done');before.revision=7;before.ocr_job.workflow_attempt_id=null;
  before.ocr_job.native_ingest={mode:'hakodate',execution_id:'original-native',intake_protocol:'native_ingest_v2'};
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?before:state('A','done')}));
  try{await start(c);await finished(c);assert.match(c.probe.current.archiveNotice,/1 件完了/);}
  finally{await c.ui.close();}
});

test('confirmation cancel and session change inside confirm issue no command',async()=>{
  const c=await setup();try{
    window.confirm=()=>false;await start(c);assert.equal(posts(c.http).length,0);
    window.confirm=()=>{c.ui.loader.load('src/services/browserSession.ts').markAuthCacheChanged();return true;};
    await start(c);assert.equal(posts(c.http).length,0);assert.equal(c.http.calls.filter(x=>x.url==='/orders/A/workflow-v2').length,0);
  }finally{await c.ui.close();}
});

for(const lane of ['read','post','poll'])for(const retirement of ['session','unmount'])test(`${retirement} during ${lane} prevents adoption and later queued orders`,async()=>{
  const c=await setup(['A','B']);const pending=deferred();let reads=0;
  c.http.on('get','/orders/A/workflow-v2',()=>++reads===1?(lane==='read'?pending.promise:{data:{order_id:'A',revision:7}}):(lane==='poll'?pending.promise:{data:state('A','done')}));
  if(lane==='post')c.http.on('post','/orders/A/workflow-v2/ocr-runs',()=>pending.promise);
  try{
    await start(c);if(retirement==='session')await c.ui.session();else await c.ui.unmount();
    await c.ui.settle(()=>pending.resolve({data:lane==='read'?{order_id:'A',revision:7}:lane==='post'?accepted():state('A','done')}));
    assert.equal(c.http.calls.some(x=>x.url.startsWith('/orders/B/')),false);
    assert.equal(posts(c.http).length,lane==='read'?0:1);
    assert.equal(c.probe.current.archiveNotice,'');
  }finally{await c.ui.close();}
});

test('old completion cannot clear a new session request busy state or issue old queued B',async()=>{
  const c=await setup(['A','B']);const old=deferred(),fresh=deferred();let postsCount=0;
  c.http.on('get','/orders/A/workflow-v2',()=>({data:{order_id:'A',revision:7}}));
  c.http.on('post','/orders/A/workflow-v2/ocr-runs',()=>++postsCount===1?old.promise:fresh.promise);
  try{
    await start(c);await c.ui.session();await start(c);
    await c.ui.settle(()=>old.resolve({data:accepted()}));
    assert.notEqual(c.probe.current.bulkOcrBusyWeek,'');assert.equal(c.http.calls.some(x=>x.url.startsWith('/orders/B/')),false);
    await c.ui.session(false);await c.ui.settle(()=>fresh.resolve({data:accepted()}));assert.equal(postsCount,2);
  }finally{await c.ui.close();}
});

test('synchronous duplicate handler invocation cannot start a second operation',async()=>{
  const c=await setup();const pending=deferred();c.http.on('get','/orders/A/workflow-v2',()=>pending.promise);
  try{const handler=c.probe.current.rerunOcrWeekGroup,group=c.probe.current.weekGroups[0];
    await c.ui.settle(()=>{void handler(group);void handler(group);});
    assert.equal(c.http.calls.filter(x=>x.url==='/orders/A/workflow-v2').length,1);
    await c.ui.session(false);await c.ui.settle(()=>pending.resolve({data:{order_id:'A',revision:7}}));assert.equal(posts(c.http).length,0);
  }finally{await c.ui.close();}
});

test('20-minute limit produces failure, not late completion',async()=>{
  const c=await setup();const realNow=Date.now;let now=realNow(),reads=0;
  c.http.on('get','/orders/A/workflow-v2',()=>{if(++reads===1)return {data:{order_id:'A',revision:7}};now+=20*60*1000;return {data:state('A','done')};});
  try{Date.now=()=>now;await start(c);await finished(c);assert.match(c.probe.current.archiveError,/タイムアウト/);assert.equal(c.probe.current.archiveNotice,'');}
  finally{Date.now=realNow;await c.ui.close();}
});

test('running/awaiting output is not completion; next order starts only after accepted attempt succeeds',async()=>{
  const c=await setup(['A','B']);let reads=0,release;const timer=window.setTimeout;
  window.setTimeout=(fn,delay,...args)=>delay===5000?(release=fn,987654):timer(fn,delay,...args);
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?{order_id:'A',revision:7}:reads===2?
    state('A','awaiting_output',{common_job_status:{domain:'hospital',jobId:'OCR-A',attemptIdentity:'attempt-A',state:null,sourceState:'awaiting_output',failure:null}}):state('A','done')}));
  try{await start(c);assert.equal(typeof release,'function');assert.equal(posts(c.http).length,1);assert.equal(c.probe.current.archiveNotice,'');
    await c.ui.settle(()=>release());await finished(c);assert.equal(posts(c.http).length,2);assert.match(c.probe.current.archiveNotice,/2 件完了/);
  }finally{window.setTimeout=timer;await c.ui.close();}
});

test('session retirement during polling sleep stops the timer and queued orders',async()=>{
  const c=await setup(['A','B']);let reads=0,release;const timer=window.setTimeout;
  window.setTimeout=(fn,delay,...args)=>delay===5000?(release=fn,987654):timer(fn,delay,...args);
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?{order_id:'A',revision:7}:state('A')}));
  try{await start(c);assert.equal(typeof release,'function');await c.ui.session(false);await c.ui.settle(()=>release());
    assert.equal(reads,2);assert.equal(posts(c.http).length,1);assert.equal(c.http.calls.some(x=>x.url.startsWith('/orders/B/')),false);
  }finally{window.setTimeout=timer;await c.ui.close();}
});

test('one failed OCR is reported separately while the next successful order alone is counted',async()=>{
  const c=await setup(['A','B']);let reads=0;
  const failed=state('A','failed',{state:'ocr_failed'});failed.ocr_job.error_message='A failed';
  c.http.on('get','/orders/A/workflow-v2',()=>({data:++reads===1?{order_id:'A',revision:7}:failed}));
  try{await start(c);await finished(c);assert.match(c.probe.current.archiveNotice,/1 件完了/);assert.match(c.probe.current.archiveError,/A failed/);assert.equal(posts(c.http).length,2);}
  finally{await c.ui.close();}
});

for(const lane of ['post','poll'])test(`uncertain ${lane} failure stops later queued orders, without cancelling server job`,async()=>{
  const c=await setup(['A','B']);let reads=0;
  if(lane==='post')c.http.on('post','/orders/A/workflow-v2/ocr-runs',()=>{throw new Error('acknowledgement unknown');});
  else c.http.on('get','/orders/A/workflow-v2',()=>{if(++reads===1)return {data:{order_id:'A',revision:7}};throw new Error('completion unknown');});
  try{await start(c);await finished(c);assert.equal(posts(c.http).length,1);assert.equal(c.http.calls.some(x=>x.url.startsWith('/orders/B/')),false);
    assert.match(c.probe.current.archiveError,/残り 1 件は開始していません/);assert.equal(c.probe.current.archiveNotice,'');
    assert.equal(c.http.calls.some(x=>x.method==='delete'||x.url.includes('cancel')),false);
  }finally{await c.ui.close();}
});

test('poll HTTP error is failure, not missing-running success or automatic POST retry',async()=>{
  const c=await setup();let reads=0;
  c.http.on('get','/orders/A/workflow-v2',()=>{if(++reads===1)return {data:{order_id:'A',revision:7}};throw new Error('poll unavailable');});
  try{await start(c);await finished(c);assert.equal(posts(c.http).length,1);assert.equal(reads,2);assert.match(c.probe.current.archiveError,/poll unavailable/);assert.equal(c.probe.current.archiveNotice,'');}
  finally{await c.ui.close();}
});
