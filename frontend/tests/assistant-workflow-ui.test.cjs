// Actual React screen with a synthetic transport; no Claude/auth/native-business proof.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom'),ts=require('typescript');
const base='/assistant-trial';
const tasks=[
 {id:'hospital_pending_faxes',domain:'hospital',label:'未処理FAXを処理する',description:'未処理を確認して確定します',steps:[]},
 {id:'shift_next_month',domain:'shift',label:'翌月のシフト案を作る',description:'登録条件から作成します',steps:[],month:true,default_month:'2026-11',target_kind:'month'},
 {id:'lunch_daily_bundle',domain:'school-lunch',label:'作業工程表・動線図・実施価格を一括作成する',description:'日次作業をまとめます',steps:[],date:true,target_kind:'menu'},
 {id:'lunch_import',domain:'school-lunch',label:'献立・調理手配書を取り込む',description:'月次の原本を取り込みます',steps:[],month:true,target_kind:'school'}];
const config={missing:[],domains:Object.fromEntries(['hospital','shift','school-lunch'].map(k=>[k,{baseline_ready:true}])),budget:{spent_cents:0,reserved_cents:0,monthly_cents:1000,available_cents:1000,notice:'synthetic'}};
const makeRun=(status='waiting_input')=>({id:'r1',task_id:'shift_next_month',instruction:'翌月のシフト案を作る',domains:['shift'],status,spent:0,cap:100,events:[],artifacts:[],conversation:[],task_inputs:{inputs:{month:'2026-11'},targets:[{label:'11月の勤務計画',data:{}}]},pending_question:{id:'q1',role:'assistant',question_id:'question-1',text:'登録内容で作成しますか？',options:['登録内容で進める','確認したい']}});
async function mount({initial=null,targets,adminView=false,beforeGet}={}){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/assistant',pretendToBeVisual:true});const saved=new Map();
 for(const key of ['window','document','navigator','HTMLElement','Element','Node','Event','MouseEvent','HTMLInputElement','HTMLSelectElement','HTMLTextAreaElement','FormData']){saved.set(key,Object.getOwnPropertyDescriptor(global,key));Object.defineProperty(global,key,{configurable:true,writable:true,value:dom.window[key]});}
 saved.set('IS_REACT_ACT_ENVIRONMENT',Object.getOwnPropertyDescriptor(global,'IS_REACT_ACT_ENVIRONMENT'));global.IS_REACT_ACT_ENVIRONMENT=true;
 const React=require('react'),{createRoot}=require('react-dom/client');const intervals=[],calls=[],cache=new Map();let run=initial;
 const api={async get(url,options){calls.push({method:'get',url});if(beforeGet)await beforeGet(url);if(url.includes('/question-drawings/'))return {data:new Blob(['<svg/>'],{type:'image/svg+xml'})};if(url===base+'/config')return {data:config};if(url===base+'/tasks')return {data:tasks};if(url===base+'/budget')return {data:config.budget};if(url===base+'/runs')return {data:run?[run]:[]};if(url===base+'/runs/r1')return {data:structuredClone(run)};throw Error(url);},async post(url,body,options){calls.push({method:'post',url,options,body:JSON.parse(JSON.stringify(body||{}))});if(url.endsWith('/targets'))return {data:await(targets?targets(url,body):{selection_id:'s1',choices:[{key:'2026-11',label:'11月の勤務計画',data:{}}],schools:[],layouts:[]})};if(url===base+'/runs'){run=makeRun();return {data:structuredClone(run)};}if(url.endsWith('/reply')){run={...run,status:'running',pending_question:null,task_review:{id:'review-1',state:'awaiting_approval',summary:'作成した勤務表を確認してください',evidence:[{operation:'shift_get_run',data:{assignments:[{staff_name:'合成人員',work_date:'2026-11-02',position:'担当',quantity:0}]}},{operation:'shift_get_constraints',data:{rows:[{reason:'全件表示の確認'}]}}],actions:[]}};return {data:run};}if(url.endsWith('/decision')){run={...run,status:body.approve?'completed':'running',task_review:{...run.task_review,state:body.approve?'approved':'needs_correction'},artifacts:body.approve?[{id:'a1',name:'勤務表.xlsx',size:1000}]:[]};return {data:run};}if(url.endsWith('/stop')){run={...run,status:'stopped'};return {data:run};}if(url.endsWith('/rollback')){run={...run,status:'rolled_back',task_review:null};return {data:run};}throw Error(url);}};
 function load(file){if(cache.has(file))return cache.get(file);const exports={};cache.set(file,exports);const source=fs.readFileSync(file,'utf8');const code=ts.transpileModule(source,{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;vm.runInNewContext(code,{exports,require(name){if(name==='next/link')return {__esModule:true,default:({href,children,...props})=>React.createElement('a',{href,...props},children)};if(name.endsWith('/apiClient'))return {apiClient:api};if(name.startsWith('.'))return load(path.resolve(path.dirname(file),name)+'.tsx');return require(name);},setTimeout,clearTimeout,setInterval(fn){intervals.push(fn);return intervals.length;},clearInterval(){},crypto:require('node:crypto').webcrypto,URL,FormData:dom.window.FormData,console});return exports;}
 const screen=load(path.resolve(__dirname,'../src/pages/assistant.tsx'));const root=createRoot(document.getElementById('root'));const settle=async(fn=()=>{})=>React.act(async()=>{await fn();await new Promise(r=>setTimeout(r,0));});
 api.put=async(url,body)=>{calls.push({method:'put',url,body:JSON.parse(JSON.stringify(body))});if(url.endsWith('/provider'))config.provider_id=body.provider;return {data:config};};
 await settle(()=>root.render(React.createElement(screen.AssistantScreen,{adminView})));
 const h={document:dom.window.document,calls,tick:()=>intervals.at(-1)(),get run(){return run},setRun(value){run=value},settle,async poll(){await settle(()=>intervals.at(-1)());},button(text){const found=[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()===text);assert.equal(found.length,1,`button ${text}: ${document.body.textContent}`);return found[0];},async click(button){await settle(()=>button.click())},async change(input,value){await settle(()=>{Object.getOwnPropertyDescriptor(input.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:input.tagName==='SELECT'?window.HTMLSelectElement.prototype:window.HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new window.Event('input',{bubbles:true}));input.dispatchEvent(new window.Event('change',{bubbles:true}));});},async wait(predicate){const end=Date.now()+3000;while(!predicate()){assert.ok(Date.now()<end,document.body.textContent);await settle(()=>new Promise(r=>setTimeout(r,20)));}},async close(){await settle(()=>root.unmount());dom.window.close();for(const[key,d]of saved){if(d)Object.defineProperty(global,key,d);else delete global[key];}}};return h;
}
test('selection auto-loads, exact question reply, approval, output and rollback stay in one screen',async()=>{
 const h=await mount();try{
  assert.equal(h.document.querySelector('.assistant-domain-tabs'),null);assert.equal(h.document.querySelectorAll('.task-domain-group').length,3);const task=[...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('翌月のシフト案'));await h.click(task);
  await h.wait(()=>h.document.querySelector('input[type=radio]'));assert.equal(h.calls.filter(c=>c.url.endsWith('/targets')).length,1);
  await h.click(h.document.querySelector('input[type=radio]'));await h.click(h.button('この内容で始める'));
  assert.equal(h.document.querySelector('.request-panel'),null);assert.match(h.document.querySelector('.answer-panel').textContent,/登録内容で作成しますか/);assert.doesNotMatch(h.document.body.textContent,/月次予算|使用額/);
  await h.click(h.button('登録内容で進める'));const reply=h.calls.find(c=>c.url.endsWith('/reply'));assert.equal(reply.body.question_id,'question-1');assert.equal(reply.body.option,'登録内容で進める');
  assert.match(h.document.querySelector('.review-panel').textContent,/合成人員/);assert.ok(h.document.querySelector('table').textContent.includes('0'));assert.ok(h.document.querySelector('.correction-panel'));assert.equal(h.document.querySelector('.review-panel details.evidence'),null);assert.match(h.document.querySelector('.review-panel').textContent,/全件表示の確認/);assert.equal(h.document.querySelectorAll('.review-panel .evidence').length,2);assert.equal(h.document.querySelectorAll('.review-panel details:not(:has(pre))').length,0);
  await h.click(h.button('この内容で進める'));assert.match(h.document.body.textContent,/作業が完了しました/);assert.ok(h.button('ダウンロード'));assert.equal(h.button('作業を停止する').disabled,true);
  await h.click(h.button('開始前に戻す'));await h.click(h.button('開始前へ戻す'));assert.equal(h.button('ダウンロード').disabled,true);assert.match(h.document.body.textContent,/開始前の状態に戻しました/);
  await h.click(h.button('別の作業を始める'));assert.ok(h.document.querySelector('.request-panel'));assert.equal(h.document.querySelector('.active-work'),null);
 }finally{await h.close();}
});
test('schools precede the date, persist across day changes, and stale targets cannot replace the current day',async()=>{
 let resolveOld;const requests=[];const h=await mount({targets:async(url,{inputs})=>{
  requests.push(inputs);
  if(inputs.service_date==='2026-07-01')return new Promise(r=>resolveOld=r);
  return {selection_id:'new',choices:inputs.facility_id&&inputs.service_date?[{key:'new-menu',label:'新しい日の献立',data:{}}]:[],schools:[{id:8,name:'合成登録校'}],layouts:[]};
 }});try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('一括作成')));
  await h.wait(()=>h.document.querySelector('input[name=school]'));
  const day=h.document.querySelector('input[type=date]');assert.equal(day.disabled,true);assert.deepEqual(Object.keys(requests[0]),[]);
  assert.ok(h.document.querySelector('.school-choices').compareDocumentPosition(day)&4);
  await h.click(h.document.querySelector('input[name=school]'));await h.wait(()=>requests.some(x=>x.facility_id===8));assert.equal(day.disabled,false);assert.doesNotMatch(h.document.body.textContent,/この日の献立は未登録/);
  await h.change(day,'2026-07-01');await h.wait(()=>!!resolveOld);
  await h.change(day,'2026-07-02');await h.wait(()=>h.document.body.textContent.includes('新しい日の献立'));
  assert.equal(h.document.querySelector('input[name=school]').checked,true);
  assert.ok(requests.filter(x=>x.service_date).every(x=>x.facility_id===8));
  await h.settle(()=>resolveOld({selection_id:'old',choices:[{key:'old',label:'古い日の献立',data:{}}],schools:[],layouts:[]}));
  assert.doesNotMatch(h.document.body.textContent,/古い日の献立/);assert.ok(h.document.body.textContent.includes('新しい日の献立'));
  await h.click(h.button('この内容で始める'));const call=h.calls.find(c=>c.url===base+'/runs'&&c.method==='post');assert.deepEqual(call.body.target_keys,['new-menu']);assert.equal(call.body.selection_id,'new');
 }finally{await h.close();}
});
test('an unanswered task resumes and a changed question retires the typed reply',async()=>{
 const h=await mount({initial:makeRun()});try{
  assert.ok(h.document.querySelector('.answer-panel'));await h.change(h.document.querySelector('textarea'),'古い質問への回答');h.setRun({...h.run,pending_question:{...h.run.pending_question,question_id:'q2',text:'新しい確認です',options:[]}});await h.poll();assert.equal(h.document.querySelector('textarea').value,'');assert.match(h.document.querySelector('.answer-panel').textContent,/新しい確認/);
 }finally{await h.close();}
});
test('monthly source request has one attachment action; remote stop uncertainty remains retryable',async()=>{
 const initial={...makeRun(),task_id:'lunch_import',domains:['school-lunch'],task_inputs:{inputs:{facility_id:1,month:'2026-11'},targets:[{label:'合成校',data:{}}]}};const h=await mount({initial});try{
  assert.ok(h.document.querySelector('input[type=file]'));assert.equal(h.document.querySelector('textarea'),null);assert.equal(h.button('原本を添付して続ける').disabled,true);
  h.setRun({...initial,status:'stop_pending'});await h.poll();assert.equal(h.button('停止を再確認する').disabled,false);assert.doesNotMatch(h.document.querySelector('.next-action').textContent,/この画面でお待ち/);await h.click(h.button('停止を再確認する'));assert.match(h.document.querySelector('.next-action').textContent,/作業を停止しました/);assert.equal(h.calls.filter(c=>c.url.endsWith('/stop')).length,1);
 }finally{await h.close();}
});
test('correction sends the current review and failure reason stays visible',async()=>{
 const initial={...makeRun('running'),pending_question:null,task_review:{id:'review-current',state:'awaiting_approval',summary:'勤務表を確認してください',evidence:[],actions:[]}};
 const h=await mount({initial});try{
  assert.equal(h.button('訂正を依頼する').disabled,true);
  await h.change(h.document.querySelector('textarea'),'11月2日の担当を確認してください');await h.click(h.button('訂正を依頼する'));
  const call=h.calls.find(c=>c.url.endsWith('/decision'));assert.deepEqual(call.body,{review_id:'review-current',approve:false,message:'11月2日の担当を確認してください'});assert.match(h.document.body.textContent,/訂正内容を反映しています/);
  h.setRun({...h.run,status:'failed',conversation:[{id:'failure-message',role:'assistant',text:'半日勤務の設定が不足しています。'}]});await h.poll();assert.match(h.document.querySelector('.next-action').textContent,/半日勤務の設定が不足/);assert.ok(h.button('別の作業を始める'));
 }finally{await h.close();}
});

test('drawing selection and hover are linked, require confirmation and retire on next question',async()=>{
 const map={title:'登録済み原本',sha256:'a'.repeat(64),view_box:[0,0,900,600],hotspots:[{id:'table',name:'下処理台',option:'下処理台',bounds:[100,100,200,100]},{id:'kettle',name:'釜B',option:'釜B',bounds:[500,400,80,80]}]};
 const run=makeRun();run.pending_question.question_map=map;const h=await mount({initial:run});try{
  await h.wait(()=>h.document.querySelector('.drawing-canvas img'));
  assert.equal(h.button('この場所で回答する').disabled,true);
  await h.settle(()=>h.document.querySelector('.drawing-canvas img').dispatchEvent(new window.Event('load')));
  const area=h.document.querySelector('[aria-label="図面：釜B"]');await h.click(area);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/reply')).length,0);
  assert.equal(h.button('2　釜B').getAttribute('aria-pressed'),'true');
  await h.settle(()=>h.button('1　下処理台').dispatchEvent(new window.MouseEvent('mouseover',{bubbles:true})));
  assert.ok(h.document.querySelector('[aria-label="図面：下処理台"]').classList.contains('active'));
  h.setRun({...run,pending_question:{...run.pending_question,question_id:'next-question'}});await h.poll();
  assert.equal(h.button('この場所で回答する').disabled,true);assert.doesNotMatch(h.document.querySelector('.drawing-question').textContent,/選択中：釜B/);
  await h.wait(()=>h.document.querySelector('.drawing-canvas img'));await h.settle(()=>h.document.querySelector('.drawing-canvas img').dispatchEvent(new window.Event('load')));
  await h.click(h.button('2　釜B'));await h.click(h.button('この場所で回答する'));
  assert.deepEqual(h.calls.find(c=>c.url.endsWith('/reply')).body,{message:'釜B',question_id:'next-question',option:'釜B'});
 }finally{await h.close();}
});
test('failed original image blocks answers rather than showing generic text fallback',async()=>{
 const run=makeRun();run.pending_question.question_map={title:'原本',sha256:'b'.repeat(64),view_box:[0,0,100,100],hotspots:[{id:'a',name:'釜A',option:'釜A',bounds:[10,10,20,20]}]};const h=await mount({initial:run});try{
  await h.wait(()=>h.document.querySelector('.drawing-canvas img'));await h.settle(()=>h.document.querySelector('.drawing-canvas img').dispatchEvent(new window.Event('error')));
  assert.match(h.document.querySelector('[role=alert]').textContent,/原本図面を表示できません/);assert.equal(h.button('この場所で回答する').disabled,true);assert.equal(h.document.querySelector('.answer-panel textarea'),null);assert.equal(h.calls.filter(c=>c.url.endsWith('/reply')).length,0);
 }finally{await h.close();}
});

test('grouping confirmation presents both direct choices and posts the current question identity',async()=>{
 const run=makeRun();run.pending_question={...run.pending_question,question_id:'grouping-current',text:'この工程は一緒に調理しましたか？',options:['一緒に調理しました','別々に調理しました'],process_context:{answered:2,total:3,kind:'grouping'}};
 const h=await mount({initial:run});try{
  assert.equal(h.document.querySelector('.answer-panel textarea'),null);
  assert.equal(h.document.querySelector('.drawing-question'),null);
  assert.ok(h.button('一緒に調理しました'));await h.click(h.button('別々に調理しました'));
  assert.deepEqual(h.calls.find(c=>c.url.endsWith('/reply')).body,{message:'別々に調理しました',question_id:'grouping-current',option:'別々に調理しました'});
 }finally{await h.close();}
});

test('shift review puts matrix, wish result and changes before approval without internal settings',async()=>{
 const days=[{date:'2026-08-01',label:'8/1',weekday:'土',weekend:true},{date:'2026-08-02',label:'8/2',weekday:'日',weekend:true}];
 const initial={...makeRun('running'),pending_question:null,task_review:{id:'review-shift',state:'awaiting_approval',summary:'希望休を反映しました',evidence:[{operation:'shift_get_settings',data:{internal_setting:'見せない内部設定'}}],actions:[{parameters:{body:{candidate_revision:'secret-revision'}}}]},
 shift_review:{period:'2026-08-01 ～ 2026-08-02',days,matrix:[{name:'試験A',cells:[{date:'2026-08-01',text:'調理 / 日勤'},{date:'2026-08-02',text:'休み'}]}],constraints:[{'職員':'試験A','日付':'2026-08-02','指定':'希望休','適用':'希望','結果':'休み','反映':'反映済み'}],shortages:[],issues:[],constraints_known:true,validation_known:true,blocking:false,demand_known:true,comparison_available:true,comparison:'確定済み勤務表との比較',changes:[{'職員':'試験A','変更前':'調理 / 日勤','変更後':'休み'}],reflected:1,unreflected:0}};
 const h=await mount({initial});try{
  assert.match(h.document.querySelector('.shift-matrix').textContent,/試験A.*調理.*休み/);
  assert.match(h.document.querySelector('.shift-checks').textContent,/1 \/ 1件を反映/);
  assert.match(h.document.querySelector('.review-panel').textContent,/変更前.*変更後/);
  assert.doesNotMatch(h.document.querySelector('.review-panel').textContent,/見せない内部設定|candidate_revision|secret-revision/);
  assert.equal(h.document.querySelectorAll('.shift-weeks button').length,1);
  await h.click(h.button('この内容で進める'));assert.equal(h.calls.find(c=>c.url.endsWith('/decision')).body.review_id,'review-shift');
 }finally{await h.close()}
});

test('admin switches future provider explicitly and labels Gemini costs as estimates',async()=>{
 config.provider_id='claude';config.providers=[{id:'claude',label:'Claude Managed Agents',missing:[]},{id:'gemini',label:'Gemini Interactions API',missing:['GEMINI_API_KEY']}];
 const h=await mount({adminView:true});try{
  assert.equal(h.button('Claude Managed Agents（使用中）').getAttribute('aria-pressed'),'true');
  await h.click(h.button('Gemini Interactions API'));
  assert.equal(h.button('Gemini Interactions API（使用中）').getAttribute('aria-pressed'),'true');
  assert.deepEqual(h.calls.find(c=>c.url.endsWith('/provider')).body,{provider:'gemini'});
  assert.match(h.document.body.textContent,/Geminiの料金は報告トークン数/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/runs')&&c.method==='post').length,0);
 }finally{await h.close();delete config.providers;delete config.provider_id;}
});

test('lunch review shows saved food and actual equipment names before approval',async()=>{
 const initial={...makeRun('running'),provider:'gemini',task_id:'lunch_daily_bundle',domains:['school-lunch'],pending_question:null,
   lunch_review:[{'献立':'スープ','食材':'にんじん','作業':'洗浄','調理方法':'別々に調理','使用設備':'シンクA','開始':'08:00','終了':'08:15','担当':'担当A'},
                 {'献立':'スープ','食材':'じゃがいも','作業':'洗浄','調理方法':'別々に調理','使用設備':'シンクB','開始':'08:15','終了':'08:30','担当':'担当B'}],
   task_review:{id:'lunch-review',state:'awaiting_approval',summary:'当日の実績を確認',actions:[],evidence:[{operation:'lunch_get_process',data:{internal:'内部工程を重複表示しない'}}]}};
 const h=await mount({initial});try{
  const review=h.document.querySelector('.review-panel');assert.match(review.textContent,/にんじん.*シンクA.*じゃがいも.*シンクB/);
  assert.doesNotMatch(review.textContent,/内部工程を重複表示しない/);
  await h.click(h.button('この内容で進める'));assert.equal(h.calls.find(c=>c.url.endsWith('/decision')).body.review_id,'lunch-review');
 }finally{await h.close();}
});


test('slow polling never overlaps requests and resumes after completion',async()=>{
 let hold=false,release;
 const gate=new Promise(resolve=>{release=resolve});
 const h=await mount({initial:makeRun(),beforeGet:async url=>{if(hold&&url===base+'/runs/r1')await gate;}});
 try{
  hold=true;
  const count=()=>h.calls.filter(x=>x.url===base+'/runs/r1').length;
  const before=count();
  await h.settle(async()=>{
   const first=h.tick();
   await Promise.resolve();
   await h.tick();
   assert.equal(count(),before+1);
   release();await first;
  });
  await h.poll();assert.equal(count(),before+2);
 }finally{release();await h.close();}
});


test('a failed progress read preserves the task and retries without resending commands',async()=>{
 let fail=false;
 const h=await mount({initial:makeRun(),beforeGet:async url=>{if(fail&&url===base+'/runs/r1')throw Error('timeout of 30000ms exceeded');}});
 try{
  fail=true;await h.poll();
  assert.match(h.document.body.textContent,/進捗を取得できませんでした/);
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.ok(h.document.querySelector('.answer-panel'));
  assert.equal(h.calls.filter(c=>c.method==='post').length,0);
  assert.equal(h.calls.filter(c=>c.url===base+'/budget').length,0);
  fail=false;h.setRun({...h.run,status:'completed',pending_question:null});await h.poll();
  assert.match(h.document.body.textContent,/作業が完了しました/);
  assert.doesNotMatch(h.document.body.textContent,/進捗を取得できませんでした/);
  await h.click(h.button('別の作業を始める'));
  assert.doesNotMatch(h.document.body.textContent,/続きがある作業/);
  assert.match(h.document.querySelector('.history-row').textContent,/完了/);
 }finally{await h.close();}
});

test('admin usage read failure never discards the latest task status',async()=>{
 let fail=false;
 const h=await mount({initial:makeRun(),adminView:true,beforeGet:async url=>{if(fail&&url===base+'/budget')throw Error('unavailable');}});
 try{
  await h.click(h.document.querySelector('.history-row'));fail=true;
  h.setRun({...h.run,status:'completed',pending_question:null});await h.poll();
  assert.match(h.document.body.textContent,/作業が完了しました/);
  assert.match(h.document.body.textContent,/利用料金を取得できませんでした/);
  assert.equal(h.document.querySelector('.assistant-error'),null);
 }finally{await h.close();}
});


test('an accepted approval with a failed refresh is not reported as a failed command',async()=>{
 let fail=false;
 const initial={...makeRun('running'),pending_question:null,task_review:{id:'review-1',state:'awaiting_approval',summary:'確認済みの試験案',evidence:[],actions:[]}};
 const h=await mount({initial,beforeGet:async url=>{if(fail&&url===base+'/runs/r1')throw Error('timeout of 30000ms exceeded');}});
 try{
  fail=true;await h.click(h.button('この内容で進める'));
  assert.match(h.document.body.textContent,/進捗を取得できませんでした/);
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url.endsWith('/decision')).length,1);
  fail=false;await h.poll();
  assert.match(h.document.body.textContent,/作業が完了しました/);
  assert.doesNotMatch(h.document.body.textContent,/進捗を取得できませんでした/);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url.endsWith('/decision')).length,1);
 }finally{await h.close();}
});

test('pending FAX runs without selection targets render and retain stop and rollback controls',async()=>{
 const initial={...makeRun('running'),task_id:'hospital_pending_faxes',instruction:'未処理FAXを処理する',domains:['hospital'],task_inputs:{},pending_question:undefined};
 const h=await mount({initial});try{
  assert.match(h.document.body.textContent,/エージェントが作業を進めています/);
  assert.equal(h.document.querySelector('.context-strip'),null);
  assert.equal(h.button('作業を停止する').disabled,false);
  h.setRun({...initial,status:'waiting_input',pending_question:{id:'fax-q',role:'assistant',question_id:'fax-question',text:'原本を確認してください',options:['確認しました']}});
  await h.poll();assert.match(h.document.querySelector('.answer-panel').textContent,/原本を確認/);
  h.setRun({...initial,status:'completed'});await h.poll();assert.match(h.document.body.textContent,/作業が完了しました/);
  assert.equal(h.button('開始前に戻す').disabled,false);
 }finally{await h.close();}
});

test('stop uses a control deadline and stopping never promises business progress',async()=>{
 const h=await mount({initial:makeRun('running')});try{
  await h.click(h.button('作業を停止する'));
  assert.equal(h.calls.find(c=>c.url.endsWith('/stop')).options.timeout,180000);
  h.setRun({...h.run,status:'stopping'});await h.poll();
  assert.match(h.document.body.textContent,/作業の停止を確認しています/);
  assert.match(h.document.body.textContent,/追加の業務書込みを停止しました/);
  assert.doesNotMatch(h.document.body.textContent,/エージェントが作業を進めています/);
 }finally{await h.close();}
});
