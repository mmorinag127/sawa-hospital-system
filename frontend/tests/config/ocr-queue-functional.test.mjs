import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require=createRequire(import.meta.url);

test('actual queue uses native ingest rows and preserves recovery, refresh, errors and disabled concurrency', async () => {
  const {JSDOM}=require('jsdom'),dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),saved={};
  for(const key of ['window','document','navigator','HTMLElement','Event','MouseEvent','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT']) {
    saved[key]=Object.getOwnPropertyDescriptor(global,key);
    Object.defineProperty(global,key,{configurable:true,writable:true,value:key==='IS_REACT_ACT_ENVIRONMENT'?true:key.endsWith('AnimationFrame')?dom.window[key].bind(dom.window):dom.window[key]});
  }
  const React=require('react'),{act}=React,mui=require('@mui/material'),styles=require('@mui/material/styles');
  const root=require('react-dom/client').createRoot(document.getElementById('root'));
  const ts=require('typescript'),calls=[];
  const items=['pending','processing','done','error'].map((status,i)=>({id:`ingest-${i}`,status,attempts:i,generation:i?'a'.repeat(31)+i:null,generation_attempts:i?i:null,
    message_id:`mail-${i}`,pdf_uri:`gs://fixture/${i}.pdf`,last_error:i===0?'previous failure':i===3?'native failure':null,
    failure_kind:i===3?'business':null,created_at:'2026-10-19T00:00:00Z',updated_at:'2026-10-19T01:00:00Z',
    received_at:null,started_at:null,finished_at:null}));
  const frozen=JSON.stringify(items);
  let readFailure=false,malformed=false,statusFailure=false,postFailure=false,releasePost;
  let malformedItems;
  let deferReads=false,unmounted=false,lateUpdates=0;
  const reads=[];
  let postResult=Promise.resolve({data:{accepted:true,ingest_enqueued:2,uploaded_enqueued:3,ocr_recovered:1}});
  const apiClient={get:async(path,options)=>{
    calls.push(['GET',path,options]);
    if(deferReads)return new Promise((resolve,reject)=>reads.push({path,options,resolve,reject}));
    if(path==='/system/status') {if(statusFailure)throw new Error('HTTP 403');return {data:{ocr_pipeline:{status:'ok',inflight:1,max_inflight:2},uploaded_pdfs:{pending_count:3},ingest_jobs:{processing_count:1}}};}
    assert.equal(path,'/ingest/jobs');
    if(readFailure)throw new Error('HTTP 401');
    return {data:{items:malformed?malformedItems:options?.params?.status?items.filter(row=>row.status===options.params.status):items}};
  },post:async path=>{calls.push(['POST',path]);assert.equal(path,'/ingest/recover-ready');if(postFailure)throw new Error('HTTP 403');return postResult;}};
  const source=readFileSync(new URL('../../src/pages/ocr-queue.tsx',import.meta.url),'utf8');
  assert.match(source,/import \{ apiClient \} from "\.\.\/services\/apiClient"/);
  const output=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS}}).outputText;
  const exports={};
  vm.compileFunction(output,['exports','require','React'])(exports,name=>{
    if(name==='react')return {...React,useState:(...args)=>{const [value,set]=React.useState(...args);return [value,next=>{if(unmounted)lateUpdates++;set(next);}];}};
    if(name==='@mui/material')return mui;
    if(name==='@mui/material/styles')return styles;
    if(name==='@sawa/ui')return {sawaTheme:styles.createTheme()};
    if(name==='../components/TopNav')return {default:()=>null};
    if(name==='../services/apiClient')return {apiClient};
    throw new Error('Unexpected import '+name);
  },React);
  const button=label=>Array.from(document.querySelectorAll('button')).find(node=>node.textContent===label);
  const choose=async status=>act(async()=>{const node=document.querySelector('select');node.value=status;node.dispatchEvent(new Event('change',{bubbles:true}));});
  const refresh=async()=>act(async()=>button('再取得').click());
  try {
    await act(async()=>root.render(React.createElement(exports.default)));
    assert.equal(document.querySelector('h2').textContent,'取込ジョブ');
    assert.equal(document.querySelectorAll('.queue-card').length,1);
    assert.match(document.querySelector('.queue-card').textContent,/previous failure/);
    assert.match(document.querySelector('.queue-card').textContent,/試行回数: 0 \/ 世代: 未取得 \/ 世代内試行回数: 未取得/);
    assert.deepEqual(Array.from(document.querySelector('select').options,o=>o.value),['','pending','processing','done','error']);
    for(const status of ['processing','done','error']) {
      await choose(status);assert.equal(document.querySelector('.queue-card .badge').textContent,status);
      assert.equal(calls.filter(call=>call[1]==='/ingest/jobs').at(-1)[2].params.status,status);
    }
    await choose('');
    assert.equal(calls.filter(call=>call[1]==='/ingest/jobs').at(-1)[2].params,undefined);
    assert.equal(document.querySelectorAll('.queue-card').length,4);
    const card=document.querySelectorAll('.queue-card')[3];
    assert.equal(card.querySelector('.queue-title').textContent,'ingest-3');
    assert.match(card.textContent,/gs:\/\/fixture\/3.pdf/);assert.match(card.textContent,/メッセージID: mail-3/);
    assert.ok(card.textContent.includes(`試行回数: 3 / 世代: ${items[3].generation} / 世代内試行回数: 3`));
    assert.ok(card.textContent.includes(new Date(items[3].updated_at).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})));
    assert.match(card.textContent,/失敗種別: business/);assert.match(card.textContent,/エラー: native failure/);
    assert.equal(document.querySelectorAll('input[placeholder="テンプレートID"]').length,0);
    readFailure=true;await refresh();
    assert.match(document.querySelector('.message').textContent,/取込ジョブの取得に失敗/);
    assert.equal(document.querySelectorAll('.queue-card').length,0);assert.doesNotMatch(document.body.textContent,/対象ジョブはありません/);
    readFailure=false;malformed=true;
    for(const invalid of [undefined,null,{},[null],[{}],[{...items[0],id:0}],[items[0],{...items[0]}],
      [{...items[0],status:'failed'}],[{...items[0],attempts:'2'}],[{...items[0],generation:2}],
      [{...items[0],generation_attempts:1.5}],[{...items[0],last_error:{message:'invalid'}}],[{...items[0],updated_at:42}]]) {
      malformedItems=invalid;await refresh();
      assert.match(document.querySelector('.message').textContent,/取込ジョブの取得に失敗/);
      assert.equal(document.querySelectorAll('.queue-card').length,0);
      assert.doesNotMatch(document.body.textContent,/対象ジョブはありません/);
    }
    malformedItems=[];await refresh();assert.equal(document.querySelector('.message'),null);assert.match(document.body.textContent,/対象ジョブはありません/);
    malformed=false;statusFailure=true;await refresh();assert.match(document.querySelector('.message').textContent,/OCR状態の取得に失敗/);assert.equal(document.querySelectorAll('.queue-card').length,4);
    statusFailure=false;await refresh();assert.equal(document.querySelector('.message'),null);
    postResult=new Promise(resolve=>{releasePost=resolve;});
    await act(async()=>button('滞留をまとめて再試行').click());
    assert.equal(button('まとめて再試行中...').disabled,true);assert.equal(button('再取得').disabled,true);
    const count=calls.filter(call=>call[0]==='POST').length;
    await act(async()=>button('まとめて再試行中...').click());assert.equal(calls.filter(call=>call[0]==='POST').length,count);
    readFailure=true;
    await act(async()=>releasePost({data:{accepted:true,ingest_enqueued:2,uploaded_enqueued:3,ocr_recovered:1}}));
    assert.match(document.querySelector('.message').textContent,/ingest=2件 \/ uploaded=3件 \/ ocr=1件/);
    assert.match(document.querySelector('.message').textContent,/取込ジョブの取得に失敗/);
    readFailure=false;postFailure=true;
    await act(async()=>button('滞留をまとめて再試行').click());assert.match(document.querySelector('.message').textContent,/まとめて再試行に失敗/);
    assert.equal(button('滞留をまとめて再試行').disabled,false);
    postFailure=false;
    const accepted={accepted:true,ingest_enqueued:2,uploaded_enqueued:3,ocr_recovered:1};
    for(const invalid of [null,{}, {...accepted,accepted:false},{...accepted,ocr_recovered:undefined},
      {...accepted,ingest_enqueued:-1},{...accepted,uploaded_enqueued:'3'},{...accepted,ocr_recovered:0.5}]) {
      postResult=Promise.resolve({data:invalid});const readsBefore=calls.filter(call=>call[0]==='GET').length;
      await act(async()=>button('滞留をまとめて再試行').click());
      assert.equal(document.querySelector('.message').textContent,'まとめて再試行に失敗しました。');
      assert.equal(calls.filter(call=>call[0]==='GET').length,readsBefore,'malformed recovery is not accepted or refreshed as success');
    }
    postResult=Promise.resolve({data:accepted});await act(async()=>button('滞留をまとめて再試行').click());
    assert.match(document.querySelector('.message').textContent,/ingest=2件 \/ uploaded=3件 \/ ocr=1件/);
    deferReads=true;
    const settle=async(start,status,fail=false)=>act(async()=>{
      const pair=reads.slice(start,start+2);assert.equal(pair.length,2);
      for(const read of pair) {
        if(fail)read.reject(new Error('older failed read'));
        else read.resolve({data:read.path==='/ingest/jobs'?{items:items.filter(row=>row.status===status)}:{ocr_pipeline:{status}}});
      }
    });
    let older=reads.length;await choose('pending');let latest=reads.length;await choose('error');
    assert.equal(reads[older].options.params.status,'pending');assert.equal(reads[latest].options.params.status,'error');
    await settle(older,'pending');
    assert.equal(button('再取得').disabled,true,'older finally must not clear current loading');
    await settle(latest,'error');
    assert.equal(document.querySelector('.queue-card .badge').textContent,'error');
    assert.equal(document.querySelector('.queue-status-value').textContent,'エラー');
    older=reads.length;await choose('pending');latest=reads.length;await choose('done');
    await settle(latest,'done');await settle(older,'pending',true);
    assert.equal(document.querySelector('.queue-card .badge').textContent,'done');
    assert.equal(document.querySelector('.queue-status-value').textContent,'done');
    assert.equal(document.querySelector('.message'),null,'older failure must not overwrite current success');
    older=reads.length;await choose('pending');latest=reads.length;await choose('error');
    await settle(latest,'error');await settle(older,'pending');
    assert.equal(document.querySelector('.queue-card .badge').textContent,'error','reverse success must not replace current rows');
    assert.equal(document.querySelector('.queue-status-value').textContent,'エラー');
    const detached=reads.length;await choose('pending');
    await act(async()=>root.unmount());unmounted=true;
    await settle(detached,'pending');assert.equal(lateUpdates,0,'unmounted reads must not publish');
    assert.equal(JSON.stringify(items),frozen);
    assert.equal(calls.some(call=>call[1].includes('unclassified')),false);
  } finally {
    if(!unmounted)await act(async()=>root.unmount());dom.window.close();
    for(const key of Object.keys(saved)){if(saved[key])Object.defineProperty(global,key,saved[key]);else delete global[key];}
  }
});
