// Actual React screen with a synthetic transport; no Claude/auth/native-business proof.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom'),ts=require('typescript');
const base='/assistant-trial';
const tasks=[
 {id:'hospital_pending_faxes',domain:'hospital',label:'未処理FAXを処理する',description:'未処理を確認して確定します',steps:[]},
 {id:'shift_next_month',domain:'shift',label:'翌月のシフト案を作る',description:'登録条件から作成します',steps:[],month:true,default_month:'2026-11',target_kind:'month'},
 {id:'lunch_daily_bundle',domain:'school-lunch',label:'作業工程表・動線図・実施価格を一括作成する',description:'日次作業をまとめます',steps:[],date:true,target_kind:'menu'},
 {id:'lunch_import',domain:'school-lunch',label:'献立・調理手配書を取り込む',description:'月次の原本を取り込みます',steps:[],month:true,target_kind:'school'}];
for(const [id,domain,label,target_kind,month] of [
 ['hospital_review_faxes','hospital','要確認FAXを確認・訂正する','fax',false],
 ['hospital_outputs','hospital','確定済み注文の帳票を作成する','order',false],
 ['shift_wishes','shift','希望休・勤務条件を反映して作り直す','month',true],
 ['shift_absence','shift','急な欠勤時に組み直す','staff',true],
 ['shift_check','shift','人員不足・勤務制約違反を確認する','candidate',true],
 ['shift_export','shift','確定した勤務表を出力する','confirmed_candidate',true],
 ['lunch_procurement','school-lunch','月間食材必要量・発注案を作る','school',true],
 ['lunch_month_exports','school-lunch','月間帳票をまとめて出力する','school',true],
])tasks.push({id,domain,label,target_kind,month,description:'登録済み対象から提案します',steps:[]});
const config={missing:[],domains:Object.fromEntries(['hospital','shift','school-lunch'].map(k=>[k,{baseline_ready:true}])),budget:{spent_cents:0,reserved_cents:0,monthly_cents:1000,available_cents:1000,notice:'synthetic'}};
const makeRun=(status='waiting_input')=>({id:'r1',task_id:'shift_next_month',instruction:'翌月のシフト案を作る',domains:['shift'],status,spent:0,cap:100,events:[],artifacts:[],conversation:[],task_inputs:{inputs:{month:'2026-11'},targets:[{label:'11月の勤務計画',data:{}}]},pending_question:{id:'q1',role:'assistant',question_id:'question-1',text:'登録内容で作成しますか？',options:['登録内容で進める','確認したい']}});
async function mount({initial=null,targets,adminView=false,beforeGet,beforePost,openInitial=true}={}){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/assistant',pretendToBeVisual:true});const saved=new Map();
 for(const key of ['window','document','navigator','HTMLElement','Element','Node','Event','MouseEvent','HTMLInputElement','HTMLSelectElement','HTMLTextAreaElement','FormData']){saved.set(key,Object.getOwnPropertyDescriptor(global,key));Object.defineProperty(global,key,{configurable:true,writable:true,value:dom.window[key]});}
 saved.set('IS_REACT_ACT_ENVIRONMENT',Object.getOwnPropertyDescriptor(global,'IS_REACT_ACT_ENVIRONMENT'));global.IS_REACT_ACT_ENVIRONMENT=true;
 const React=require('react'),{createRoot}=require('react-dom/client');const intervals=[],timeouts=[],calls=[],cache=new Map();let run=initial;
 const api={async get(url,options){calls.push({method:'get',url,options});if(beforeGet)await beforeGet(url);if(url.includes('/artifacts/'))return {data:new Blob(['synthetic artifact'])};if(url.includes('/question-drawings/'))return {data:new Blob(['<svg/>'],{type:'image/svg+xml'})};if(url===base+'/config')return {data:config};if(url===base+'/tasks')return {data:tasks};if(url===base+'/budget')return {data:config.budget};if(url===base+'/runs')return {data:run?[run]:[]};if(url===base+'/runs/r1')return {data:structuredClone(run)};throw Error(url);},async post(url,body,options){calls.push({method:'post',url,options,body:JSON.parse(JSON.stringify(body||{}))});if(beforePost){const result=await beforePost(url,body,options);if(result)return result;}if(url.endsWith('/target-lookups')){const result=await(targets?targets(url,body):{selection_id:'s1',choices:[{key:'2026-11',label:'11月の勤務計画',data:{}}],schools:[],layouts:[]});return {data:{lookup_id:body.request_key,state:'completed',result:{inputs:{month:'2026-11',...body.inputs},month_choices:[{month:'2026-11',label:'2026年11月',reason:'登録済みの条件で作成します'}],...result}}};};if(url===base+'/runs'){run=makeRun();return {data:structuredClone(run)};}if(url.endsWith('/reply')){run={...run,status:'running',pending_question:null,task_review:{id:'review-1',state:'awaiting_approval',summary:'作成した勤務表を確認してください',evidence:[{operation:'shift_get_run',data:{assignments:[{staff_name:'合成人員',work_date:'2026-11-02',position:'担当',quantity:0}]}},{operation:'shift_get_constraints',data:{rows:[{reason:'全件表示の確認'}]}}],actions:[]}};return {data:run};}if(url.endsWith('/decision')){run={...run,status:body.approve?'completed':'running',task_review:{...run.task_review,state:body.approve?'approved':'needs_correction'},artifacts:body.approve?[{id:'a1',name:'勤務表.xlsx',size:1000}]:[]};return {data:run};}if(url.endsWith('/stop')){run={...run,status:'stopped'};return {data:run};}if(url.endsWith('/rollback')){run={...run,status:'rolled_back',task_review:null};return {data:run};}throw Error(url);}};
 function businessResponse(value){
  if(!value?.id || !value.status)return value;
  if(value.business)return value;
  let state=value.status==='completed'?'completed':['stopped','rolled_back','stop_pending','rollback_stop_pending'].includes(value.status)?'archived':['reconciliation_required','failed','budget_reached'].includes(value.status)?'checking':value.status==='waiting_input'&&value.task_id==='lunch_import'&&!value.events?.some(e=>e.kind==='source')?'source':value.status==='waiting_input'&&value.pending_question?'question':value.status==='running'&&(value.task_review?.state==='awaiting_approval'||value.fax_items?.some(f=>f.state==='awaiting_approval'))?'review':'working';
  const titles={completed:'作業が完了しました',archived:'以前の試用記録',checking:'保存した内容を確認しています',source:'原本を添付してください',question:'この質問にお答えください',review:'内容を確認してください',working:'作業を進めています'};
  if(state==='working'&&value.task_review?.state==='needs_correction')titles.working='訂正内容を反映しています';
  return {...value,business:{state,title:titles[state],message:state==='checking'?'回答・承認した内容は保存されています。システム側で確認しており、操作は必要ありません。':state==='completed'?'保存・出力が完了しました。':'質問に答え、内容を確認してください。',next_action:({question:'answer',review:'approve',source:'source',completed:'outputs'})[state]||'none'},business_messages:value.conversation||[]};
 }
 const rawGet=api.get,rawPost=api.post;
 api.get=async(...args)=>{const result=await rawGet(...args);return {...result,data:Array.isArray(result.data)?result.data.map(businessResponse):businessResponse(result.data)};};
 api.post=async(...args)=>{const result=await rawPost(...args);return {...result,data:businessResponse(result.data)};};
 function load(file){if(cache.has(file))return cache.get(file);const exports={};cache.set(file,exports);const source=fs.readFileSync(file,'utf8');const code=ts.transpileModule(source,{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;vm.runInNewContext(code,{exports,require(name){if(name==='next/link')return {__esModule:true,default:({href,children,...props})=>React.createElement('a',{href,...props},children)};if(name.endsWith('/apiClient'))return {apiClient:api};if(name.startsWith('.'))return load(path.resolve(path.dirname(file),name)+'.tsx');return require(name);},setTimeout(fn,delay){if(delay===5000){timeouts.push(fn);return 0;}const timer=setTimeout(fn,delay);if(delay>=60000)timer.unref();return timer;},clearTimeout,setInterval(fn){intervals.push(fn);return intervals.length;},clearInterval(){},crypto:require('node:crypto').webcrypto,URL,FormData:dom.window.FormData,console});return exports;}
 const screen=load(path.resolve(__dirname,'../src/pages/assistant.tsx'));const root=createRoot(document.getElementById('root'));const settle=async(fn=()=>{})=>React.act(async()=>{await fn();await new Promise(r=>setTimeout(r,0));});
 api.put=async(url,body)=>{calls.push({method:'put',url,body:JSON.parse(JSON.stringify(body))});if(url.endsWith('/provider'))config.provider_id=body.provider;return {data:config};};
 await settle(()=>root.render(React.createElement(screen.AssistantScreen,{adminView})));
 const h={document:dom.window.document,calls,retryLoad:()=>settle(()=>timeouts.shift()()),tick:()=>intervals.at(-1)(),get run(){return run},setRun(value){run=value},settle,async poll(){await settle(()=>intervals.at(-1)());},button(text){const found=[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()===text);assert.equal(found.length,1,`button ${text}: ${document.body.textContent}`);return found[0];},async click(button){await settle(()=>button.click())},async change(input,value){await settle(()=>{Object.getOwnPropertyDescriptor(input.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:input.tagName==='SELECT'?window.HTMLSelectElement.prototype:window.HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new window.Event('input',{bubbles:true}));input.dispatchEvent(new window.Event('change',{bubbles:true}));});},async wait(predicate){const end=Date.now()+3000;while(!predicate()){assert.ok(Date.now()<end,document.body.textContent);await settle(()=>new Promise(r=>setTimeout(r,20)));}},async close(){await settle(()=>root.unmount());dom.window.close();for(const[key,d]of saved){if(d)Object.defineProperty(global,key,d);else delete global[key];}}};
 // Existing run-detail scenarios explicitly enter through history, as an operator does.
 if(initial && openInitial && !['completed','stopped','rolled_back','failed'].includes(initial.status)) {
  await h.click(h.document.querySelector('.history-row'));
 }
 return h;
}
test('entry never selects a single old request, regardless of its unfinished state',async()=>{
 for(const status of ['waiting_input','running','reconciliation_required','stop_pending','rollback_stop_pending']){
  const initial={...makeRun(status),domains:['school-lunch'],instruction:'以前の給食作業'};
  const h=await mount({initial,openInitial:false});try{
   assert.ok(h.document.querySelector('.request-panel'));
   assert.equal(h.document.querySelector('.active-work'),null);
   assert.equal(h.calls.filter(c=>c.url===base+'/runs/r1').length,0);
   assert.equal(h.calls.filter(c=>c.method==='post').length,0);
   await h.click(h.document.querySelector('.history-row'));
   assert.match(h.document.querySelector('.active-work').textContent,/以前の給食作業/);
   await h.click(h.document.querySelector('.assistant-footer button'));
   assert.ok(h.document.querySelector('.request-panel'));
   assert.equal(h.document.querySelector('.active-work'),null);
   assert.equal(h.calls.filter(c=>c.method==='post').length,0);
  }finally{await h.close();}
 }
});
test('stop recheck shows progress and an unchanged pending result without inventing completion',async()=>{
 let release;const initial=makeRun('stop_pending');
 const h=await mount({adminView:true,initial,beforePost:async url=>{if(url.endsWith('/stop')){await new Promise(r=>release=r);return {data:initial};}}});try{
  await h.click(h.button('停止を再確認する'));
  assert.match(h.document.body.textContent,/接続先へ停止状況を確認しています/);
  assert.equal(h.button('操作結果を確認中…').disabled,true);
  release();await h.settle();
  assert.match(h.document.body.textContent,/停止を再確認しました/);
  assert.match(h.document.body.textContent,/停止完了はまだ確認できていません/);
  assert.doesNotMatch(h.document.querySelector('.next-action').textContent,/作業を停止しました/);
  assert.equal(h.button('停止を再確認する').disabled,false);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/stop')).length,1);
 }finally{release?.();await h.close();}
});
test('late answer response cannot reopen a request after returning to the task list',async()=>{
 let release;const h=await mount({initial:makeRun(),beforePost:async url=>{if(url.endsWith('/reply')){await new Promise(r=>release=r);return {data:makeRun('stopped')};}}});try{
  await h.click(h.button('登録内容で進める'));
  await h.click(h.button('作業一覧に戻る'));
  release();await h.settle();
  assert.equal(h.document.querySelector('.active-work'),null);
  assert.ok(h.document.querySelector('.request-panel'));
  assert.doesNotMatch(h.document.body.textContent,/停止の確認が完了しました/);
 }finally{release?.();await h.close();}
});
test('unknown stop response remains visibly uncertain and does not resubmit',async()=>{
 const h=await mount({adminView:true,initial:makeRun('stop_pending'),beforePost:async url=>{if(url.endsWith('/stop'))throw {response:{status:503}};}});try{
  await h.click(h.button('停止を再確認する'));
  assert.match(h.document.body.textContent,/操作結果を受信できませんでした/);
  assert.match(h.document.body.textContent,/停止完了とは判断せず/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/stop')).length,1);
  assert.equal(h.document.querySelector('.assistant-error'),null);
 }finally{await h.close();}
});
test('restart reconciliation resumes once and displays the same saved question',async()=>{
 let h;h=await mount({adminView:true,initial:{...makeRun('reconciliation_required'),pending_question:null},beforePost:async url=>{
  if(url.endsWith('/resume')){h.setRun(makeRun());return {data:h.run};}
 }});try{
  assert.equal(h.document.querySelector('.answer-panel'),null);
  await h.click(h.button('保存済みの結果を確認して再開'));
  const request=h.calls.filter(c=>c.url.endsWith('/resume'));
  assert.equal(request.length,1);assert.equal(request[0].options.timeout,300000);
  assert.match(h.document.querySelector('.answer-panel').textContent,/登録内容で作成しますか/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/reply')).length,0);
  assert.equal(h.calls.filter(c=>c.url===base+'/runs'&&c.method==='post').length,0);
 }finally{await h.close();}
});
test('selection auto-loads, exact question reply, approval and output stay in one screen',async()=>{
 const h=await mount();try{
  assert.equal(h.document.querySelector('.assistant-domain-tabs'),null);assert.equal(h.document.querySelectorAll('.task-domain-group').length,3);const task=[...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('翌月のシフト案'));await h.click(task);
  await h.wait(()=>!h.button('この内容で始める').disabled);assert.equal(h.calls.filter(c=>c.url.endsWith('/target-lookups')).length,1);
  assert.equal(h.document.querySelector('input[name=target]'),null);await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));
  assert.equal(h.document.querySelector('.request-panel'),null);assert.match(h.document.querySelector('.answer-panel').textContent,/登録内容で作成しますか/);assert.doesNotMatch(h.document.body.textContent,/月次予算|使用額/);
  await h.click(h.button('登録内容で進める'));const reply=h.calls.find(c=>c.url.endsWith('/reply'));assert.equal(reply.body.question_id,'question-1');assert.equal(reply.body.option,'登録内容で進める');
  assert.match(h.document.querySelector('.review-panel').textContent,/合成人員/);assert.ok(h.document.querySelector('table').textContent.includes('0'));assert.ok(h.document.querySelector('.correction-panel'));assert.equal(h.document.querySelector('.review-panel details.evidence'),null);assert.match(h.document.querySelector('.review-panel').textContent,/全件表示の確認/);assert.equal(h.document.querySelectorAll('.review-panel .evidence').length,2);assert.equal(h.document.querySelectorAll('.review-panel details:not(:has(pre))').length,0);
  await h.click(h.button('この内容で進める'));assert.match(h.document.body.textContent,/作業が完了しました/);assert.ok(h.button('ダウンロード'));assert.equal(h.document.querySelector('.work-controls'),null);
  await h.click(h.button('別の作業を始める'));assert.ok(h.document.querySelector('.request-panel'));assert.equal(h.document.querySelector('.active-work'),null);
 }finally{await h.close();}
});
test('all twelve tasks use business questions, approval and completion without runtime controls',async()=>{
 const ids=['hospital_pending_faxes','hospital_review_faxes','hospital_outputs','shift_next_month','shift_wishes','shift_absence','shift_check','shift_export','lunch_import','lunch_daily_bundle','lunch_procurement','lunch_month_exports'];
 for(const id of ids){
  const initial={...makeRun(),task_id:id,domains:[id.startsWith('hospital_')?'hospital':id.startsWith('shift_')?'shift':'school-lunch'],events:[{kind:'source',payload:{}}]};
  const h=await mount({initial});try{
   assert.ok(h.document.querySelector('.answer-panel'),id);
   assert.equal(h.document.querySelector('.work-controls'),null,id);
   await h.click(h.button('登録内容で進める'));
   assert.ok(h.document.querySelector('.review-panel'),id);
   await h.click(h.button('この内容で進める'));
   assert.match(h.document.body.textContent,/作業が完了しました/,id);
   assert.ok(h.button('ダウンロード'),id);
   assert.equal(h.calls.filter(c=>/\/(stop|rollback|resume|reconcile-business)$/.test(c.url)).length,0,id);
   assert.doesNotMatch(h.document.querySelector('.active-work').textContent,/question-1|review-1|再開する|停止を再確認/);
  }finally{await h.close();}
 }
});
test('registered work days are proposed with reasons and selecting a day needs no new request',async()=>{
 const requests=[];const h=await mount({targets:async(url,{inputs})=>{
  requests.push(inputs);
  return {selection_id:'registered-days',choices:inputs.facility_id?[{key:'menu-a',label:'7月1日の献立',data:{service_date:'2026-07-01'}},{key:'menu-b',label:'7月2日の献立',data:{service_date:'2026-07-02'}}]:[],schools:[{id:8,name:'合成登録校'}],layouts:[],work_days:inputs.facility_id?[{service_date:'2026-07-01',menu_names:['にんじんの煮物'],menu_count:1,missing:['動線図'],reason:'動線図が未作成です'},{service_date:'2026-07-02',menu_names:['じゃがいもの煮物'],menu_count:1,missing:['作業工程表'],reason:'作業工程表が未作成です'}]:[]};
 }});try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('一括作成')));
  await h.wait(()=>h.document.querySelector('input[name=school]'));
  assert.equal(h.document.querySelector('input[type=date]'),null);assert.deepEqual(Object.keys(requests[0]),[]);
  await h.click(h.document.querySelector('input[name=school]'));
  await h.wait(()=>h.document.querySelectorAll('.work-day-proposals button').length===2);
  assert.match(h.document.querySelector('.work-day-proposals').textContent,/おすすめ：7月1日/);
  assert.match(h.document.querySelector('.work-day-proposals').textContent,/にんじんの煮物.*動線図が未作成/);
  const before=requests.length;
  await h.click(h.document.querySelectorAll('.work-day-proposals button')[0]);
  await h.click(h.document.querySelectorAll('.work-day-proposals button')[1]);
  assert.equal(requests.length,before);
  assert.equal(h.document.querySelector('input[name=school]').checked,true);
  await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));const call=h.calls.find(c=>c.url===base+'/runs'&&c.method==='post');assert.deepEqual(call.body.target_keys,['menu-b']);assert.equal(call.body.selection_id,'registered-days');
 }finally{await h.close();}
});
test('an old school lookup cannot replace the newly selected school proposal',async()=>{
 let release;const h=await mount({targets:async(url,{inputs})=>{
  if(inputs.facility_id===1)return new Promise(resolve=>release=resolve);
  return {selection_id:'school-b',choices:[],schools:[{id:1,name:'学校A'},{id:2,name:'学校B'}],layouts:[],work_days:inputs.facility_id===2?[{service_date:'2026-07-02',menu_names:['学校Bの献立'],menu_count:1,missing:['動線図'],reason:'動線図が未作成です'}]:[]};
 }});try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('一括作成')));
  await h.wait(()=>h.document.querySelectorAll('input[name=school]').length===2);
  const choices=[...h.document.querySelectorAll('input[name=school]')];
  await h.click(choices[0]);await h.wait(()=>!!release);
  // Leave the first lookup pending, then choose a different school.
  await h.click(h.button('作業を選び直す'));
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('一括作成')));
  await h.wait(()=>h.document.querySelectorAll('input[name=school]').length===2);
  await h.click(h.document.querySelectorAll('input[name=school]')[1]);
  await h.wait(()=>h.document.body.textContent.includes('学校Bの献立'));
  await h.settle(()=>release({selection_id:'school-a',choices:[],schools:[],layouts:[],work_days:[{service_date:'2026-07-01',menu_names:['古い学校A'],menu_count:1,missing:[],reason:'保存あり'}]}));
  assert.match(h.document.body.textContent,/学校Bの献立/);assert.doesNotMatch(h.document.body.textContent,/古い学校A/);
 }finally{await h.close();}
});
test('calendar remains available for a day outside the suggestions and retrieves its registered menus',async()=>{
 const requests=[];const h=await mount({targets:async(url,{inputs})=>{
  requests.push(inputs);
  const manual=inputs.service_date==='2026-07-05';
  return {selection_id:manual?'manual-day':'suggestions',choices:inputs.facility_id?[{key:manual?'manual-menu':'proposed-menu',label:manual?'別の日の登録献立':'提案日の献立',data:{service_date:manual?'2026-07-05':'2026-07-01'}}]:[],schools:[{id:8,name:'合成登録校'}],layouts:[],work_days:inputs.facility_id?[{service_date:'2026-07-01',menu_names:['提案日の献立'],menu_count:1,missing:['動線図'],reason:'動線図が未作成です'}]:[]};
 }});try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('一括作成')));
  await h.wait(()=>h.document.querySelector('input[name=school]'));
  await h.click(h.document.querySelector('input[name=school]'));
  await h.wait(()=>h.document.querySelector('.work-day-proposals button'));
  const calendar=h.document.querySelector('input[type=date]');assert.ok(calendar);
  assert.match(calendar.closest('label').textContent,/別の日を指定する/);
  await h.change(calendar,'2026-07-05');await h.wait(()=>h.document.body.textContent.includes('別の日の登録献立'));
  assert.ok(requests.some(input=>input.facility_id===8&&input.service_date==='2026-07-05'));
  await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));
  const call=h.calls.find(call=>call.url===base+'/runs'&&call.method==='post');
  assert.equal(call.body.selection_id,'manual-day');assert.deepEqual(call.body.target_keys,['manual-menu']);
 }finally{await h.close();}
});

test('an unanswered task resumes and a changed question retires the typed reply',async()=>{
 const h=await mount({initial:makeRun()});try{
  assert.ok(h.document.querySelector('.answer-panel'));await h.change(h.document.querySelector('textarea'),'古い質問への回答');h.setRun({...h.run,pending_question:{...h.run.pending_question,question_id:'q2',text:'新しい確認です',options:[]}});await h.poll();assert.equal(h.document.querySelector('textarea').value,'');assert.match(h.document.querySelector('.answer-panel').textContent,/新しい確認/);
 }finally{await h.close();}
});
test('monthly source request has one attachment action; remote stop uncertainty remains retryable',async()=>{
 const initial={...makeRun(),task_id:'lunch_import',domains:['school-lunch'],task_inputs:{inputs:{facility_id:1,month:'2026-11'},targets:[{label:'合成校',data:{}}]}};const h=await mount({adminView:true,initial});try{
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
  h.setRun({...h.run,status:'failed',conversation:[{id:'failure-message',role:'assistant',text:'半日勤務の設定が不足しています。'}]});await h.poll();assert.match(h.document.querySelector('.next-action').textContent,/システム側で確認/);assert.equal(h.run.conversation[0].text,'半日勤務の設定が不足しています。');assert.equal(h.document.querySelector('.work-controls'),null);assert.ok(h.button('作業一覧に戻る'));
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
 const h=await mount({adminView:true,initial});try{
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
 const h=await mount({adminView:true,initial:makeRun('running')});try{
  await h.click(h.button('作業を停止する'));
  assert.equal(h.calls.find(c=>c.url.endsWith('/stop')).options.timeout,300000);
  h.setRun({...h.run,status:'stopping'});await h.poll();
  assert.match(h.document.body.textContent,/作業の停止を確認しています/);
  assert.match(h.document.body.textContent,/追加の業務書込みを停止しました/);
  assert.doesNotMatch(h.document.body.textContent,/エージェントが作業を進めています/);
 }finally{await h.close();}
});

test('restored request can start another task while remote cost uncertainty remains visible',async()=>{
 const h=await mount({initial:{...makeRun('rollback_stop_pending'),spent:10,reserved:290}});try{
  assert.doesNotMatch(h.document.body.textContent,/接続先の停止確認中/);assert.equal(h.run.status,'rollback_stop_pending');
  assert.equal(h.document.querySelector('.work-controls'),null);
  await h.click(h.button('別の作業を始める'));
  assert.match(h.document.body.textContent,/どの作業を行いますか/);
  assert.equal(h.calls.filter(c=>c.method==='post').length,0);
 }finally{await h.close();}
});

test('uncertain approval is reconciled by polling without a second submission or false failure',async()=>{
 const initial={...makeRun('running'),pending_question:null,task_review:{id:'review-unknown',state:'awaiting_approval',summary:'確認',evidence:[],actions:[]}};
 const h=await mount({initial,beforePost:async url=>{if(url.endsWith('/decision'))throw Object.assign(new Error('timeout'),{code:'ECONNABORTED'});}});
 try{
  await h.click(h.button('この内容で進める'));
  assert.match(h.document.body.textContent,/同じ操作は再送していません/);
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.equal(h.button('この内容で進める').disabled,true);
  assert.equal(h.document.querySelector('.work-controls'),null);
  assert.equal(h.calls.find(c=>c.url.endsWith('/decision')).options.timeout,300000);
  h.setRun({...initial,status:'completed',task_review:{...initial.task_review,state:'approved'}});await h.poll();
  assert.doesNotMatch(h.document.body.textContent,/同じ操作は再送/);
  assert.match(h.document.body.textContent,/作業が完了しました/);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url.endsWith('/decision')).length,1);
 }finally{await h.close();}
});

test('FAX approval timeout preserves its review identity and clears only after saved decision',async()=>{
 const fax={fax_id:'fax1',state:'awaiting_approval',snapshot:{name:'synthetic.pdf',facility:'合成施設',week:'合成試験週'},review:{id:'fax-review',summary:'原本照合済み',sheet:{fields:['数量'],rows:[[20]]},original:{id:'original',name:'synthetic.pdf'}}};
 const initial={...makeRun('running'),task_id:'hospital_pending_faxes',domains:['hospital'],task_inputs:{},pending_question:null,fax_items:[fax]};
 const h=await mount({initial,beforePost:async url=>{if(url.endsWith('/decision'))throw {response:{status:503}};}});
 try{
  await h.click(h.button('この内容で確定へ進む'));
  assert.equal(h.button('この内容で確定へ進む').disabled,true);
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.equal(h.calls.find(c=>c.url.endsWith('/decision')).body.review_id,'fax-review');
  h.setRun({...initial,fax_items:[{...fax,state:'approved',decision:{approve:true}}]});await h.poll();
  assert.doesNotMatch(h.document.body.textContent,/同じ操作は再送/);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url.endsWith('/decision')).length,1);
 }finally{await h.close();}
});

test('a rejected approval remains editable and is not labelled as an unknown outcome',async()=>{
 const initial={...makeRun('running'),pending_question:null,task_review:{id:'review-rejected',state:'awaiting_approval',summary:'確認',evidence:[],actions:[]}};
 const h=await mount({initial,beforePost:async url=>{if(url.endsWith('/decision'))throw {response:{status:422,data:{detail:'食数を確認してください'}}};}});
 try{
  await h.click(h.button('この内容で進める'));assert.match(h.document.querySelector('.assistant-error').textContent,/食数を確認/);
  assert.equal(h.button('この内容で進める').disabled,false);assert.doesNotMatch(h.document.body.textContent,/同じ操作は再送/);
 }finally{await h.close();}
});

test('stop stays available during an in-flight answer without clearing other outstanding work',async()=>{
 let release;
 const h=await mount({adminView:true,initial:makeRun(),beforePost:async url=>{if(url.endsWith('/reply')){await new Promise(r=>release=r);return {data:{}};}}});
 try{
  await h.click(h.button('登録内容で進める'));assert.equal(h.button('作業を停止する').disabled,false);
  await h.click(h.button('作業を停止する'));assert.match(h.document.body.textContent,/作業を停止しました/);
  assert.equal(h.button('開始前に戻す').disabled,false);
  release();await h.settle();assert.match(h.document.body.textContent,/作業を停止しました/);
  assert.equal(h.calls.filter(c=>c.url.endsWith('/reply')).length,1);assert.equal(h.calls.filter(c=>c.url.endsWith('/stop')).length,1);
 }finally{if(release)release();await h.close();}
});

test('artifact retrieval uses the business deadline',async()=>{
 const h=await mount({initial:{...makeRun('completed'),artifacts:[{id:'a1',name:'帳票.xlsx',size:100}]}});
 try{
  h.document.defaultView.HTMLAnchorElement.prototype.click=()=>{};
  await h.click(h.button('翌月のシフト案を作るシフト完了'));
  await h.click(h.button('ダウンロード'));
  assert.equal(h.calls.find(c=>c.url.endsWith('/artifacts/a1')).options.timeout,300000);
 }finally{await h.close();}
});


test('unknown answer is reconciled without resending while stop remains usable',async()=>{
 const initial=makeRun();
 const h=await mount({initial,beforePost:async url=>{if(url.endsWith('/reply'))throw {response:{status:503}};}});
 try{
  await h.click(h.button('登録内容で進める'));
  assert.match(h.document.body.textContent,/同じ回答は再送していません/);
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.equal(h.button('登録内容で進める').disabled,true);
  assert.equal(h.button('回答して続ける').disabled,true);
  assert.equal(h.document.querySelector('.work-controls'),null);
  h.setRun({...initial,pending_question:{...initial.pending_question,question_id:'question-2',text:'次の確認',options:['次の回答']}});await h.poll();
  assert.doesNotMatch(h.document.body.textContent,/同じ回答は再送/);
  assert.equal(h.button('次の回答').disabled,false);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url.endsWith('/reply')).length,1);
 }finally{await h.close();}
});


test('accepted creation with a lost response opens only its exact request key without resending',async()=>{
 const h=await mount({beforePost:async(url,body)=>{if(url===base+'/runs'){h.setRun({...makeRun(),request_key:body.request_key});throw {response:{status:503}};}}});
 try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('未処理FAX')));
  await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.ok(h.document.querySelector('.active-work'));
  assert.match(h.document.body.textContent,/登録内容で作成しますか/);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url===base+'/runs').length,1);
 }finally{await h.close();}
});

test('unknown creation waits for its exact request in history and never adopts another run',async()=>{
 let key;
 const h=await mount({beforePost:async(url,body)=>{if(url===base+'/runs'){key=body.request_key;throw new Error('timeout');}}});
 try{
  h.setRun({...makeRun(),id:'other',request_key:'other-request'});
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('未処理FAX')));
  await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));
  assert.match(h.document.body.textContent,/同じ依頼は再送していません/);
  assert.equal(h.document.querySelector('.active-work'),null);
  assert.equal(h.button('この内容で始める').disabled,true);
  h.setRun({...makeRun(),request_key:key});await h.poll();
  assert.ok(h.document.querySelector('.active-work'));
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url===base+'/runs').length,1);
 }finally{await h.close();}
});

test('known creation rejection is displayed and does not enter unknown-outcome polling',async()=>{
 const h=await mount({beforePost:async url=>{if(url===base+'/runs')throw {response:{status:409,data:{detail:'確認待ちの依頼があります'}}};}});
 try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('未処理FAX')));
  await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));
  assert.match(h.document.querySelector('.assistant-error').textContent,/確認待ちの依頼/);
  assert.doesNotMatch(h.document.body.textContent,/受付を確認しています/);
  assert.equal(h.button('この内容で始める').disabled,false);
 }finally{await h.close();}
});


test('creation reconciliation never overlaps a slow initial history lookup with polling',async()=>{
 let created=false,release,reads=0;
 const h=await mount({beforeGet:async url=>{if(created&&url===base+'/runs'){reads++;await new Promise(r=>release=r);}},beforePost:async(url,body)=>{if(url===base+'/runs'){created=true;h.setRun({...makeRun(),request_key:body.request_key});throw new Error('timeout');}}});
 try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(b=>b.textContent.includes('未処理FAX')));
  await h.wait(()=>!h.button('この内容で始める').disabled);await h.click(h.button('この内容で始める'));
  assert.equal(reads,1);
  await h.poll();await h.poll();assert.equal(reads,1);
  created=false;release();await h.wait(()=>h.document.querySelector('.active-work'));
  assert.equal(h.calls.filter(c=>c.method==='post'&&c.url===base+'/runs').length,1);
 }finally{if(release)release();await h.close();}
});


test('initial transient read failure recovers without any business replay or manual reload',async()=>{
 let fail=true;
 const h=await mount({beforeGet:async url=>{if(url===base+'/config'&&fail)throw {response:{status:503}};}});
 try{
  assert.match(h.document.body.textContent,/作業一覧を再確認しています/);
  assert.equal(h.document.querySelector('.assistant-error'),null);
  assert.equal(h.document.querySelectorAll('.assistant-task').length,0);
  fail=false;await h.retryLoad();
  assert.equal(h.document.querySelectorAll('.assistant-task').length,tasks.length);
  assert.doesNotMatch(h.document.body.textContent,/作業一覧を再確認しています/);
  assert.equal(h.calls.filter(c=>c.method==='post').length,0);
 }finally{await h.close();}
});

test('known initial access rejection is shown without retrying authorization failures',async()=>{
 const h=await mount({beforeGet:async url=>{if(url===base+'/config')throw {response:{status:403,data:{detail:'試用担当者だけが利用できます'}}};}});
 try{
  assert.match(h.document.querySelector('.assistant-error').textContent,/試用担当者だけ/);
  assert.doesNotMatch(h.document.body.textContent,/作業一覧を再確認しています/);
  assert.equal(h.calls.filter(c=>c.method==='post').length,0);
 }finally{await h.close();}
});

test('all twelve entries propose business targets while keeping alternative calendar and individual choices',async()=>{
 for(const task of tasks){
  const h=await mount({targets:async(url,body)=>{
   const school=task.domain==='school-lunch';
   const day='2026-11-02';const month=body.inputs.month||'2026-11';
   const keys=task.target_kind==='month'?[month]:task.target_kind==='school'?['8']:['one'];
   return {selection_id:'all12-'+task.id,inputs:{month,absence_date:body.inputs.absence_date||day,...body.inputs},schools:school?[{id:8,name:'合成学校'}]:[],layouts:[],choices:keys.map(key=>({key,label:school?'合成学校':'登録済みの業務対象',data:{id:8,service_date:day}})),month_choices:[{month,label:month+'の作業',reason:'登録済み献立・勤務条件を確認します'}],work_days:task.date?[{service_date:day,menu_names:['合成献立'],menu_count:1,reason:'工程表が未作成です',missing:['作業工程表']}]:[],absence_days:task.id==='shift_absence'?[{date:day,label:'今日（11/02）'},{date:'2026-11-03',label:'明日（11/03）'}]:[],batches:['fax','order','candidate','confirmed_candidate'].includes(task.target_kind)?[{label:'登録済みの対象をまとめて処理',keys,reason:'内容を確認してから進めます'}]:[]};
  }});
  try{
   await h.click([...h.document.querySelectorAll('.assistant-task')].find(button=>button.textContent.includes(task.label)));
   await h.wait(()=>!h.document.body.textContent.includes('登録済みの対象を確認しています'));
   await h.wait(()=>h.calls.some(call=>call.url.endsWith('/target-lookups')));
   await h.wait(()=>!h.document.querySelector('.retry-targets').disabled);
   if(task.domain==='school-lunch'){
    await h.click(h.document.querySelector('input[name=school]'));
    await h.wait(()=>h.calls.filter(call=>call.url.endsWith('/target-lookups')).length===2);
    await h.wait(()=>!h.document.querySelector('.retry-targets').disabled);
    if(task.date){assert.ok(h.document.querySelector('input[type=date]'));await h.click([...h.document.querySelectorAll('.work-day-proposals button')].find(button=>button.textContent.includes('合成献立')));}
    else assert.ok(h.document.querySelector('input[type=month]'));
   } else if(task.id==='shift_absence'){
    assert.equal(h.document.querySelector('input[type=month]'),null);
    assert.ok(h.document.querySelector('input[type=date]'));
    assert.ok(h.button('今日（11/02）'));assert.equal(h.document.querySelector('input[name=target]').type,'radio');await h.click(h.document.querySelector('input[name=target]'));
   } else if(['fax','order','candidate','confirmed_candidate'].includes(task.target_kind)){
    await h.click([...h.document.querySelectorAll('.work-day-proposals button')].find(button=>button.querySelector('strong')?.textContent==='登録済みの対象をまとめて処理'));assert.ok(h.document.querySelector('input[name=target]'));
   }
   if(task.target_kind==='month')assert.equal(h.document.querySelector('input[name=target]'),null);
   assert.equal(h.button('この内容で始める').disabled,false,task.id);
   assert.equal(h.document.querySelector('.assistant-error'),null,task.id);
  }finally{await h.close();}
 }
});

test('absence chooses exactly one staff member and sends the latest selection',async()=>{
 const h=await mount({targets:async(url,body)=>({selection_id:'absence-selection',inputs:{month:'2026-11',absence_date:'2026-11-02'},month_choices:[],absence_days:[{date:'2026-11-02',label:'今日（11/02）'}],schools:[],layouts:[],choices:[{key:'staff-A',label:'合成職員A',data:{}},{key:'staff-B',label:'合成職員B',data:{}}]})});
 try{
  await h.click([...h.document.querySelectorAll('.assistant-task')].find(button=>button.textContent.includes('急な欠勤時')));
  await h.wait(()=>h.document.querySelectorAll('input[name=target]').length===2);
  const inputs=[...h.document.querySelectorAll('input[name=target]')];
  await h.click(inputs[0]);await h.click(inputs[1]);
  assert.equal(inputs[0].checked,false);assert.equal(inputs[1].checked,true);
  await h.click(h.button('この内容で始める'));
  assert.deepEqual(h.calls.find(call=>call.url===base+'/runs'&&call.method==='post').body.target_keys,['staff-B']);
 }finally{await h.close();}
});
