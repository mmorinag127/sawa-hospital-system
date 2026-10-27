// Mounted transport contracts; not a claim of live production synchronization.
const assert = require('node:assert/strict');
const test = require('node:test');
const harness = require('./helpers/modernization-react-dom.cjs');
const choices = { enabled: true, selected: 'test', options: [{id:'test',label:'テスト用',available:true}] };
const mount = () => harness.mount('src/components/StagingDatasetSelect.tsx', { component(loader) {
  loader.load('src/services/stagingDataset.ts').initializeDocumentDataset({dataset:'test'});
  return loader.load('src/components/StagingDatasetSelect.tsx').default;
}});
const button = (h, text) => [...h.document.querySelectorAll('button')].find(b => b.textContent === text);

test('missing production source is explicit and does not trigger refresh or change Test selection', async () => {
  const previous = global.fetch;
  const calls = [];
  global.fetch = async (url, init={}) => {calls.push({url,init}); return Response.json({...choices, refresh:{configured:false,blocked_reason:'production_sources_not_configured',job:null}});};
  let h;
  try {
    h = await mount();
    await h.waitFor(() => h.document.body.textContent.includes('本番の同期元が未設定'));
    assert.equal(button(h,'本番データからコピーを更新').disabled,true);
    assert.equal(h.document.querySelector('select').value,'test');
    assert.equal(calls.filter(c=>c.init.method==='POST').length,0);
  } finally {if(h) await h.close(); global.fetch=previous;}
});

test('double click submits one durable request and leaves Test selected while processing', async () => {
  const previous=global.fetch;
  let job=null, posts=[];
  global.fetch=async(url,init={})=>{
    if(url.endsWith('/refresh')) {
      if(init.method==='POST'){posts.push(JSON.parse(init.body));job={id:posts[0].request_id,phase:'copying_database',component:'order'};return Response.json(job,{status:202});}
      return Response.json({configured:true,job});
    }
    return Response.json({...choices,refresh:{configured:true,job}});
  };
  let h;
  try {
    h=await mount();
    await h.waitFor(()=>button(h,'本番データからコピーを更新')?.disabled===false);
    await h.settle(()=>{const b=button(h,'本番データからコピーを更新');b.click();b.click();});
    await h.waitFor(()=>h.document.body.textContent.includes('DBをコピー中'));
    assert.equal(posts.length,1);
    assert.match(posts[0].request_id,/^[a-f0-9]{32}$/);
    assert.equal(h.document.querySelector('select').value,'test');
    assert.equal(button(h,'本番データからコピーを更新').disabled,true);
  } finally {if(h) await h.close();global.fetch=previous;}
});

test('queued dispatch retry resends the same request id', async () => {
  const previous=global.fetch;
  const job={id:'a'.repeat(32),phase:'queued'};let posted;
  global.fetch=async(url,init={})=>{
    if(url.endsWith('/refresh')){
      if(init.method==='POST'){posted=JSON.parse(init.body);return Response.json(job,{status:202});}
      return Response.json({configured:true,job});
    }
    return Response.json({...choices,refresh:{configured:true,job}});
  };
  let h;
  try{
    h=await mount();await h.waitFor(()=>button(h,'更新を再送'));
    await h.settle(()=>button(h,'更新を再送').click());
    assert.deepEqual(posted,{request_id:job.id});
    assert.equal(h.document.querySelector('select').value,'test');
  }finally{if(h)await h.close();global.fetch=previous;}
});

test('successful refresh exposes new copy but does not silently retarget existing Test form', async()=>{
  const previous=global.fetch;let done=false;
  global.fetch=async(url,init={})=>{
    if(url.endsWith('/refresh')){
      if(init.method==='POST'){done=true;return Response.json({id:'a'.repeat(32),phase:'succeeded'},{status:202});}
      return Response.json({configured:true,job:done?{id:'a'.repeat(32),phase:'succeeded'}:null});
    }
    return Response.json({...choices,refresh:{configured:true,job:done?{id:'a'.repeat(32),phase:'succeeded'}:null},options:done?[...choices.options,{id:'replay-new',label:'本番再現用（最新）',available:true}]:choices.options});
  };
  let h;
  try{
    h=await mount();await h.waitFor(()=>button(h,'本番データからコピーを更新')?.disabled===false);
    await h.settle(()=>button(h,'本番データからコピーを更新').click());
    await h.waitFor(()=>h.document.querySelector('option[value="replay-new"]'));
    assert.equal(h.document.querySelector('select').value,'test');
    assert.equal(h.loader.load('src/services/stagingDataset.ts').datasetHeaders()['X-Sawa-Dataset'],'test');
    assert.match(h.document.body.textContent,/更新完了/);
  }finally{if(h)await h.close();global.fetch=previous;}
});

test('failed refresh reports preservation and keeps selection', async()=>{
  const previous=global.fetch;
  global.fetch=async url=>Response.json({...choices,refresh:{configured:true,job:{id:'a'.repeat(32),phase:'failed'}}});
  let h;
  try{
    h=await mount();await h.waitFor(()=>h.document.body.textContent.includes('更新失敗。現在のコピーを維持'));
    assert.equal(h.document.querySelector('select').value,'test');
    assert.equal(button(h,'本番データからコピーを更新').disabled,false);
  }finally{if(h)await h.close();global.fetch=previous;}
});
