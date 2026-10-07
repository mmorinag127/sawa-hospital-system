const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const ts=require('typescript');
const root=path.join(__dirname,'../src');
const pageSource=ts.createSourceFile('monthly.tsx',fs.readFileSync(path.join(root,'pages/menus/[monthId].tsx'),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const pending=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function evaluate(node,source,context){context.exports={};vm.runInNewContext(ts.transpileModule('exports.fn='+node.getText(source),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,context);return context.exports.fn;}
function actual(name,context){let node;const visit=n=>{if(ts.isVariableDeclaration(n)&&n.name.getText(pageSource)===name)node=n.initializer;ts.forEachChild(n,visit);};visit(pageSource);assert.ok(node,name);return evaluate(node,pageSource,context);}
function attribute(name,context){let node;const visit=n=>{if(ts.isJsxAttribute(n)&&n.name.getText(pageSource)===name)node=n.initializer.expression;ts.forEachChild(n,visit);};visit(pageSource);assert.ok(node,name);return evaluate(node,pageSource,context);}
function shared(name,context){
  const filename=path.join(__dirname,'../node_modules/@sawa/ui/dist/form.js');
  const source=ts.createSourceFile(filename,fs.readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let node;
  const visit=n=>{if(ts.isVariableDeclaration(n)&&n.name.getText(source)===name)node=n.initializer;ts.forEachChild(n,visit);};visit(source);assert.ok(node,name);return evaluate(node,source,context);
}

test('monthly public DataTable paging retains every item and resets size without editing values',()=>{
  let table;const visit=node=>{if(ts.isJsxSelfClosingElement(node)&&node.tagName.getText(pageSource)==='DataTable')table=node;ts.forEachChild(node,visit);};visit(pageSource);assert.ok(table);
  const props=new Map(table.attributes.properties.filter(ts.isJsxAttribute).map(node=>[node.name.getText(pageSource),node.initializer]));
  const items=Array.from({length:61},(_,id)=>({id,qty:id===0?0:null,raw:' 0 '}));
  const context={items,itemPage:0,itemRowsPerPage:25,setItemPage:value=>{context.itemPage=value;},setItemRowsPerPage:value=>{context.itemRowsPerPage=value;}};
  const value=name=>evaluate(props.get(name).expression,pageSource,context);
  const collected=[];for(let page=0;page<3;page++){context.itemPage=page;collected.push(...value('rows'));assert.equal(value('total'),61);}
  assert.deepEqual(collected,items);value('onPageChange')(2);assert.equal(context.itemPage,2);
  value('onRowsPerPageChange')(50);assert.equal(context.itemRowsPerPage,50);assert.equal(context.itemPage,0);
  assert.equal(value('rows').length,50);assert.equal(items[0].qty,0);assert.equal(items[1].qty,null);assert.equal(items[0].raw,' 0 ');
});
function harness(reload=false){
  const session={key:'auth-A',valid:true};const refs=[],cleanups=[];
  const hook={exports:{},require:name=>name==='react'?{useRef:value=>{const ref={current:value};refs.push(ref);return ref;},useEffect:fn=>{cleanups.push(fn());}}:
    name.includes('browserSession')?{browserSessionKey:()=>session.key}:{hasActiveSessionAuthHeader:()=>session.valid}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root,'hooks/useMenuInputOwner.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,hook);
  const owner=hook.exports.useMenuInputOwner('/menus/2026-10');const request=pending();
  const item={id:'A',name:'Authored A',qty_per_serving:0,bag_max_qty:null,unit_type:'g',facility_override:null};
  const state={dirty:true,revision:'read-7',error:undefined,saved:false,busy:false,resets:0,classified:0,message:'',selected:item};
  const context={owner,monthId:'2026-10',itemEditGeneration:0,itemFormEpoch:{current:0},itemFormContext:{current:{monthId:'2026-10',edit:0}},
    itemFormOperation:{current:null},selectedEntryRef:{current:'entry-A'},menuLoadGeneration:{current:0},itemSaveGeneration:{current:0},itemRevision:{current:'read-7'},
    exceptionRevision:{current:'read-7'},masterRevisions:{current:{}},itemFormDirty:true,exceptionDirty:false,selectedItem:item,
    mergeItemDraft:(saved,values)=>({...saved,...values}),itemDraft:saved=>({...saved}),setItemFormDirty:value=>{state.dirty=value;},
    setMenu:()=>{},setItems:()=>{},setItemPage:value=>{state.itemPage=value;},setEntries:()=>{},setRevision:value=>{state.revision=value;},
    setMasterCheckState:fn=>fn({issues:[],resolutions:{}}),setMessage:value=>{state.message=value;},window:{confirm:()=>true},
    apiClient:{put:(url,body,config)=>{state.sent={url,body,config};return request.promise;},get:()=>request.promise}};
  for(const name of ['beginItemFormOperation','takeItemFormOperation','validateSnapshot','adoptSnapshot','saveItem'])context[name]=(...args)=>actual(name,context)(...args);
  const local={locked:{current:false},conflict:{current:false},mounted:{current:true}};
  const formContext={...local,form:{handleSubmit:fn=>fn,reset:()=>{state.resets++;}},beginOperation:context.beginItemFormOperation,
    onSave:attribute('onSave',context),onReload:attribute('onReload',context),
    classifyError:error=>{state.classified++;return attribute('classifyError',context)(error);},
    setBusy:value=>{state.busy=value;},setError:value=>{state.error=value;},setSaved:value=>{state.saved=value;},setConfirmReload:()=>{}};
  return {owner,session,refs,cleanups,request,context,state,local,run:shared(reload?'reload':'submit',formContext),item};
}
function retire(h,kind){
  if(kind==='input')h.owner.changed();
  if(kind==='sibling'||kind==='route-start')h.context.itemFormEpoch.current++;
  if(kind==='selection'){h.owner.changed();h.context.selectedEntryRef.current='entry-B';}
  if(kind==='edit-generation')h.context.itemFormContext.current={monthId:'2026-10',edit:1};
  if(kind==='read-generation')h.context.menuLoadGeneration.current++;
  if(kind==='auth')h.session.key='auth-B';
  if(kind==='route')h.refs[3].current='/menus/2026-11';
  if(kind==='unmount'){h.cleanups[0]();h.local.mounted.current=false;}
}
const response=h=>({data:{updated:true,snapshot:{menu:{id:'2026-10'},revision:'own-8',items:[h.item],entries:[{id:'entry-A'}],master_checks:{issues:[]}}}});
for(const reload of [false,true])for(const kind of ['input','sibling','selection','edit-generation','read-generation','auth','route-start','route','unmount'])for(const outcome of ['409','success'])test(`actual monthly/shared ${reload?'reload':'save'} ${kind} rejects old ${outcome}`,async()=>{
  const h=harness(reload),command=h.run({name:'Authored A',qty_per_serving:0});retire(h,kind);
  if(outcome==='409')h.request.reject(Object.assign(Error('old conflict'),{response:{status:409}}));
  else h.request.resolve(reload?{data:response(h).data.snapshot}:response(h));await command;
  assert.equal(h.state.classified,0);assert.equal(h.local.conflict.current,false);assert.equal(h.state.error,undefined);
  assert.equal(h.state.resets,0);assert.equal(h.state.dirty,true);assert.equal(h.state.revision,'read-7');assert.equal(h.context.itemRevision.current,'read-7');
  assert.equal(h.local.locked.current,false);
});
for(const reload of [false,true])test(`actual monthly/shared active ${reload?'reload':'save'} 409 is conflict`,async()=>{
  const h=harness(reload);if(reload)h.local.conflict.current=true;
  const command=h.run({name:'Authored A'});h.request.reject(Object.assign(Error('active conflict'),{response:{status:409}}));await command;
  assert.equal(h.state.classified,1);assert.equal(h.state.error.kind,'conflict');
  assert.equal(h.local.conflict.current,true);assert.equal(h.state.dirty,true);assert.equal(h.state.resets,0);
});
for(const reload of [false,true])test(`actual monthly/shared own ${reload?'reload':'save'} advances only own lease and resets once`,async()=>{
  const h=harness(reload),command=h.run({name:'Authored A',qty_per_serving:0});
  if(!reload){assert.equal(h.state.sent.config.headers['If-Match'],'read-7');assert.equal(h.state.sent.body.qty_per_serving,0);assert.equal(h.state.sent.body.bag_max_qty,null);}
  h.request.resolve(reload?{data:response(h).data.snapshot}:response(h));await command;
  assert.equal(h.state.classified,0);assert.equal(h.state.resets,1);assert.equal(h.state.dirty,false);
  assert.equal(h.state.revision,'own-8');assert.equal(h.context.itemRevision.current,'own-8');assert.equal(h.state.busy,false);
});
test('actual monthly command never recaptures latest revision after form begin',async()=>{
  const h=harness();h.context.beginItemFormOperation();h.context.itemRevision.current='display-9';
  const operation=h.context.takeItemFormOperation();const command=h.context.saveItem(h.item,operation);
  assert.equal(h.state.sent.config.headers['If-Match'],'read-7');h.request.resolve(response(h));await command;
});
test('actual exception command retires item shared-form rejection without conflict pollution',async()=>{
  const h=harness(),other=pending(),command=h.run({name:'Authored A'});
  Object.assign(h.context,{selectedEntry:{id:'entry-B'},entryExceptionDraft:{facilityIds:['facility-B'],name:'Authored exception',
    qty_per_serving:'0',bag_max_qty:'',category:'',unit_type:'g'},setEntryExceptionSaving:()=>{}});
  h.context.apiClient.post=()=>other.promise;
  const exception=actual('saveEntryException',h.context)();
  h.request.reject(Object.assign(Error('old 409'),{response:{status:409}}));await command;
  assert.equal(h.state.classified,0);assert.equal(h.local.conflict.current,false);assert.equal(h.state.dirty,true);
  assert.equal(h.context.itemRevision.current,'read-7');assert.equal(h.state.resets,0);
  other.reject(Error('independent failure'));await exception;
});
