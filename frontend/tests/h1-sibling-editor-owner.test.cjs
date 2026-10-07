const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const root = path.join(__dirname, '../src');
test('actual menu-rule classifier preserves any strict 409 and original error input', () => {
  const source = ts.createSourceFile('menu-rules.tsx', fs.readFileSync(path.join(root, 'pages/menu-rules.tsx'), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  const visit = node => {
    if (ts.isJsxAttribute(node) && node.name.getText(source) === 'classifyError') callback = node.initializer.expression;
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.ok(callback);
  let received;
  const context = { exports: {}, errorText: error => { received = error; return 'original raw message'; } };
  vm.runInNewContext(ts.transpileModule('exports.fn=' + callback.getText(source), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
  const { AxiosError } = require('axios');
  const axios409 = new AxiosError('conflict');
  axios409.response = { status: 409 };
  for (const [error, kind] of [[axios409, 'conflict'], [{ response: { status: 409 } }, 'conflict'], [null, 'error'], [{ response: { status: 500 } }, 'error']]) {
    const result = context.exports.fn(error);
    assert.equal(result.kind, kind);
    assert.equal(result.message, 'original raw message');
    assert.equal(received, error);
  }
});
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
function ownerHarness() {
  const state = {auth:'session-A', route:'/base-menus', active:true};
  const events = new Map(); const cleanups=[]; const refs=[];
  const router={asPath:state.route,events:{on:(key,fn)=>events.set(key,fn),off:key=>events.delete(key)}};
  const context={exports:{},require:name=>name==='react'?{useRef:value=>{const ref={current:value};refs.push(ref);return ref;},useEffect:fn=>{const cleanup=fn();if(cleanup)cleanups.push(cleanup);}}:
    name==='next/router'?{useRouter:()=>router}:name.includes('browserSession')?{browserSessionKey:()=>state.auth}:{hasActiveSessionAuthHeader:()=>state.active}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root,'hooks/useH1EditorOwner.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,context);
  return {owner:context.exports.useH1EditorOwner(0),state,refs,events,cleanups};
}
function actual(page, name, context, mutation=false) {
  const source=ts.createSourceFile(page,fs.readFileSync(path.join(root,'pages',page),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let node;
  const visit=n=>{if(ts.isVariableDeclaration(n)&&n.name.getText(source)===name)node=n.initializer;ts.forEachChild(n,visit);};visit(source);
  assert.ok(node, name);
  if(mutation)node=node.arguments[0].properties.find(p=>p.name.getText(source)==='mutationFn').initializer;
  const code=ts.transpileModule('exports.fn='+node.getText(source),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  context.exports={};vm.runInNewContext(code,context);return context.exports.fn;
}
function actualSave(context, page='base-menus.tsx') {
  const source=ts.createSourceFile(page,fs.readFileSync(path.join(root,'pages',page),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let node;
  const visit=n=>{if(ts.isJsxAttribute(n)&&n.name.getText(source)==='onSave')node=n.initializer.expression;ts.forEachChild(n,visit);};visit(source);
  assert.ok(node);context.exports={};vm.runInNewContext(ts.transpileModule('exports.fn='+node.getText(source),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,context);
  return context.exports.fn;
}
function baseHarness() {
  const h=ownerHarness();const A={id:'A',clientKey:'A',name:'A',cycle_day:1,slot_index:0},B={id:'B',clientKey:'B',name:'B',cycle_day:1,slot_index:0};
  const baseline={revision:'read-7',items:[A,B]},state={selected:{item:A,baseline},dirty:true,notice:'',snapshot:null};const pending=deferred();
  const context={ownership:h.owner,selected:state.selected,dirty:true,all:{data:baseline},mounted:{current:true},generation:{current:0},
    client:{setQueryData:(_key,value)=>{state.snapshot=value;}},setSelected:value=>{state.selected=value;},setDirty:value=>{state.dirty=value;},setNotice:value=>{state.notice=value;},
    window:{confirm:()=>true},messageFor:error=>String(error.message),identity:item=>JSON.stringify(item),verifyReplacement:(requested,received)=>assert.deepEqual(JSON.parse(JSON.stringify(received)),JSON.parse(JSON.stringify(requested)))};
  context.replace={mutateAsync:args=>{args.current();state.request=args;return pending.promise;}};
  context.bulkSave=(...args)=>actual('base-menus.tsx','bulkSave',context)(...args);
  return {...h,session:h.state,A,B,baseline,state,context,pending};
}
test('actual base delete B preserves dirty A and its captured CAS base after success',async()=>{
  const h=baseHarness();actual('base-menus.tsx','deleteItem',h.context)(h.B);
  assert.equal(h.state.request.revision,'read-7');assert.equal(h.state.request.items.length,1);
  h.pending.resolve({created:1,snapshot:{revision:'own-8',items:[h.A]}});await h.pending.promise;await Promise.resolve();await Promise.resolve();
  assert.equal(h.state.selected.item.id,'A');assert.equal(h.state.selected.baseline.revision,'read-7');assert.equal(h.state.dirty,true);
});
test('actual bulk update B leaves A editor and baseline mounted',async()=>{
  const h=baseHarness(),B={...h.B,name:'Updated B'};const command=h.context.bulkSave(h.baseline,[h.A,B]);
  h.pending.resolve({created:2,snapshot:{revision:'own-8',items:[h.A,B]}});await command;
  assert.equal(h.state.snapshot.items[1].name,'Updated B');assert.equal(h.state.selected.item.id,'A');
  assert.equal(h.state.selected.baseline,h.baseline);assert.equal(h.state.dirty,true);
});
test('actual choose refuses discard without consent and retires older command after explicit consent',()=>{
  const h=baseHarness(),own=h.owner.start();h.context.window.confirm=()=>false;
  actual('base-menus.tsx','choose',h.context)(h.B);assert.equal(h.state.selected.item.id,'A');assert.equal(own.current(),true);
  h.context.window.confirm=()=>true;actual('base-menus.tsx','choose',h.context)(h.B);
  assert.equal(h.state.selected.item.id,'B');assert.equal(h.state.dirty,false);assert.equal(own.current(),false);
});
for(const kind of ['input','auth','route','operation','unmount'])for(const result of ['success','failure'])test(`actual bulk ${kind} retirement preserves editor on ${result}`,async()=>{
  const h=baseHarness();const command=h.context.bulkSave(h.baseline,[h.A]);
  if(kind==='input')h.owner.edit();if(kind==='auth')h.session.auth='session-B';if(kind==='route')h.events.get('routeChangeStart')();
  if(kind==='operation')h.owner.start();if(kind==='unmount')h.cleanups[0]();
  if(result==='success')h.pending.resolve({created:1,snapshot:{revision:'own-8',items:[h.A]}});else h.pending.reject(Error('409 conflict'));
  await assert.rejects(command);assert.equal(h.state.selected.item.id,'A');assert.equal(h.state.dirty,true);assert.equal(h.state.snapshot,null);
});
test('actual bulk own new save adopts only its own returned item',async()=>{
  const h=baseHarness();const next={...h.A,id:undefined,clientKey:'new'};const command=h.context.bulkSave(h.baseline,[h.A,h.B,next],next);
  h.pending.resolve({created:3,snapshot:{revision:'own-8',items:[h.A,h.B,next]}});await command;
  assert.equal(h.state.selected.item.clientKey,'new');assert.equal(h.state.dirty,false);
});
test('actual selected EditForm save adopts own A response and retains zero values',async()=>{
  const h=baseHarness();h.context.draft=item=>({...item});
  h.context.update={mutateAsync:args=>{args.current();h.state.request=args;return h.pending.promise;}};
  h.owner.beginOperation();
  const command=actualSave(h.context)({name:'Saved A',slot_index:0});
  const saved={...h.A,name:'Saved A'};h.pending.resolve({revision:'own-8',items:[saved,h.B]});
  const result=await command;assert.equal(result.slot_index,0);assert.equal(h.state.selected.item.name,'Saved A');
  assert.equal(h.state.selected.baseline.revision,'own-8');assert.equal(h.state.dirty,false);assert.equal(h.state.request.revision,'read-7');
});

function sharedSubmit(context) {
  const sharedPath=path.join(__dirname,'../node_modules/@sawa/ui/dist/form.js');
  const source=ts.createSourceFile(sharedPath,fs.readFileSync(sharedPath,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let node;
  const visit=n=>{if(ts.isVariableDeclaration(n)&&n.name.getText(source)==='submit')node=n.initializer;ts.forEachChild(n,visit);};visit(source);
  assert.ok(node);context.exports={};vm.runInNewContext(ts.transpileModule('exports.fn='+node.getText(source),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,context);
  return context.exports.fn;
}
function sharedSaveHarness(page) {
  const h=baseHarness();h.context.draft=item=>({...item});let classifications=0,resets=0;
  const mutate=args=>{args.current();return h.pending.promise;};h.context.update={mutateAsync:mutate};
  if(page==='menu-rules.tsx') {
    h.context.selected={item:{id:'A',rule_type:'menu',qty_per_serving:0},baseline:{revision:'read-7',rules:[]}};
    h.context.ruleType='menu';h.context.save={mutateAsync:mutate};h.context.queryClient=h.context.client;
  }
  const formState={busy:false,error:null,saved:false,locked:{current:false},conflict:{current:false},mounted:{current:true}};
  const context={...formState,form:{handleSubmit:fn=>fn,reset:()=>{resets++;}},
    beginOperation:h.owner.beginOperation,onSave:actualSave(h.context,page),
    setBusy:value=>{formState.busy=value;},setError:value=>{formState.error=value;},setSaved:value=>{formState.saved=value;},
    classifyError:error=>{classifications++;return {kind:error.response?.status===409?'conflict':'error',message:'active failure'};}};
  return {...h,formState,submit:sharedSubmit(context),classifications:()=>classifications,resets:()=>resets};
}
for(const page of ['base-menus.tsx','menu-rules.tsx'])for(const retire of ['sibling','input','selection','auth','route','unmount'])for(const outcome of ['409','success'])test(`actual shared submit ${page} ${retire} rejects old ${outcome} without conflict/reset pollution`,async()=>{
  const h=sharedSaveHarness(page),command=h.submit({name:'own A',qty_per_serving:0}),sibling=deferred();
  if(retire==='sibling') {
    if(page==='base-menus.tsx') {
      h.context.replace={mutateAsync:args=>{args.current();return sibling.promise;}};
      actual(page,'deleteItem',h.context)(h.B);
    } else {
      Object.assign(h.context,{list:{data:{revision:'read-7'}},remove:{mutateAsync:args=>{args.current();return sibling.promise;}},errorText:e=>e.message});
      h.context.queryClient.invalidateQueries=async()=>{};
      actual(page,'deleteRule',h.context)({id:'B'});
    }
  }
  if(retire==='input')h.owner.edit({name:'new input'});
  if(retire==='selection')h.refs[0].current.selection+=1;if(retire==='auth')h.session.auth='new-session';
  if(retire==='route')h.events.get('routeChangeStart')();if(retire==='unmount'){h.cleanups[0]();h.formState.mounted.current=false;}
  if(outcome==='409')h.pending.reject(Object.assign(Error('old conflict'),{response:{status:409}}));
  else h.pending.resolve(page==='base-menus.tsx'?{revision:'own-8',items:[{...h.A,name:'own A'}]}:{saved:{id:'A'},snapshot:{revision:'own-8',rules:[]}});
  await command;
  assert.equal(h.classifications(),0);assert.equal(h.formState.conflict.current,false);assert.equal(h.formState.error,undefined);
  assert.equal(h.resets(),0);assert.equal(h.formState.saved,false);assert.equal(h.state.dirty,true);assert.equal(h.state.snapshot,null);
  assert.equal(h.formState.locked.current,false);
  if(retire==='sibling') { sibling.resolve(page==='base-menus.tsx'?{created:1,snapshot:{revision:'own-B-8',items:[h.A]}}:'B');await sibling.promise; }
});
for(const page of ['base-menus.tsx','menu-rules.tsx'])test(`actual shared submit ${page} active 409 stays real conflict`,async()=>{
  const h=sharedSaveHarness(page),command=h.submit({name:'own A'});
  h.pending.reject(Object.assign(Error('active conflict'),{response:{status:409}}));await command;
  assert.equal(h.classifications(),1);assert.equal(h.formState.conflict.current,true);assert.equal(h.formState.error.kind,'conflict');
  assert.equal(h.resets(),0);assert.equal(h.state.dirty,true);assert.equal(h.formState.busy,false);
});
for(const page of ['base-menus.tsx','menu-rules.tsx'])test(`actual shared submit ${page} own success advances and resets exactly once`,async()=>{
  const h=sharedSaveHarness(page),command=h.submit({name:'own A',qty_per_serving:0});
  h.pending.resolve(page==='base-menus.tsx'?{revision:'own-8',items:[{...h.A,name:'own A'}]}:{saved:{id:'A',qty_per_serving:0},snapshot:{revision:'own-8',rules:[]}});
  await command;assert.equal(h.resets(),1);assert.equal(h.classifications(),0);assert.equal(h.formState.saved,true);
  assert.equal(h.state.dirty,false);assert.equal(h.state.selected.baseline.revision,'own-8');assert.equal(h.formState.busy,false);
});
test('actual delete selected dirty item requires discard and delete consent',async()=>{
  const h=baseHarness();h.context.window.confirm=()=>false;actual('base-menus.tsx','deleteItem',h.context)(h.A);assert.equal(h.state.request,undefined);
  h.context.window.confirm=()=>true;actual('base-menus.tsx','deleteItem',h.context)(h.A);
  h.pending.resolve({created:1,snapshot:{revision:'own-8',items:[h.B]}});await h.pending.promise;await Promise.resolve();await Promise.resolve();
  assert.equal(h.state.selected,null);assert.equal(h.state.dirty,false);
});
for(const page of ['base-menus.tsx','menu-rules.tsx'])for(const name of page==='base-menus.tsx'?['update','replace']:['save','remove'])test(`actual ${page} ${name} retired mutation never dispatches HTTP`,async()=>{
  let calls=0;const context={validate:()=>{},serialize:x=>x,payload:x=>x,apiClient:{put:()=>{calls++;},post:()=>{calls++;},delete:()=>{calls++;}}};
  const mutation=actual(page,name,context,true);
  await assert.rejects(mutation({item:{id:'B'},items:[],values:{id:'B'},baseline:{revision:'read-7'},revision:'read-7',current:()=>{throw Error('retired');}}));
  assert.equal(calls,0);
});
test('actual menu rule delete B cannot clear selected dirty A',async()=>{
  const h=ownerHarness(),pending=deferred();let selected={item:{id:'A'},baseline:{revision:'read-7'}},dirty=true;
  const context={ownership:h.owner,selected,dirty,list:{data:{revision:'read-7'}},ruleType:'menu',window:{confirm:()=>true},generation:{current:0},
    remove:{mutateAsync:args=>{args.current();return pending.promise;}},queryClient:{invalidateQueries:async()=>{}},setSelected:v=>{selected=v;},setDirty:v=>{dirty=v;},setNotice:()=>{},errorText:e=>e.message};
  actual('menu-rules.tsx','deleteRule',context)({id:'B'});pending.resolve('B');await pending.promise;await Promise.resolve();await Promise.resolve();
  assert.equal(selected.item.id,'A');assert.equal(dirty,true);assert.equal(selected.baseline.revision,'read-7');
});

for(const protectedKind of ['item','master'])test(`actual monthly command-after load preserves dirty ${protectedKind} when target disappears`,async()=>{
  const pending=deferred();let adopted=0,message='';
  const hook={exports:{},require:name=>name==='react'?{useRef:value=>({current:value}),useEffect:fn=>fn()}:name.includes('browserSession')?{browserSessionKey:()=> 'auth-A'}:{hasActiveSessionAuthHeader:()=>true}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root,'hooks/useMenuInputOwner.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,hook);
  const context={monthId:'2026-10',owner:hook.exports.useMenuInputOwner('/menus/2026-10'),menuLoadGeneration:{current:0},protectedEditor:{current:{item:false,master:false}},
    apiClient:{get:()=>pending.promise},setMessage:value=>{message=value;},setMenu:()=>{adopted++;},setItems:()=>{adopted++;},setEntries:()=>{adopted++;}};
  const command=actual('menus/[monthId].tsx','loadMenu',context)();context.protectedEditor.current[protectedKind]=true;
  pending.resolve({data:{menu:{id:'2026-10'},items:[],entries:[],master_checks:{issues:[]}}});await command;
  assert.equal(adopted,0);assert.match(message,/未保存/);
});

test('actual editor owner does not retire identical values on unrelated re-render',()=>{
  const h=ownerHarness();h.owner.edit({quantity:0,unit:null});const own=h.owner.start();
  h.owner.edit({quantity:0,unit:null});assert.equal(own.current(),true);
  h.owner.edit({quantity:1,unit:null});assert.equal(own.current(),false);
});

for(const status of [409,500])test(`actual base delete failure ${status} preserves selected draft and old revision`,async()=>{
  const h=baseHarness();actual('base-menus.tsx','deleteItem',h.context)(h.B);
  h.pending.reject(Object.assign(Error('save failed'),{response:{status}}));
  await h.pending.promise.catch(()=>{});await Promise.resolve();await Promise.resolve();
  assert.equal(h.state.selected.item.id,'A');assert.equal(h.state.selected.baseline.revision,'read-7');assert.equal(h.state.dirty,true);
});
