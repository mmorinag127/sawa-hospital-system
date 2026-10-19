const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), ts = require('typescript');
const { execFileSync } = require('node:child_process');
const base = '1edb3d8628ea416dd5f5f69edae9b640c105e4b1';
const modernBase = '3a88826b62eae5427889bfc600628c7129d1476e';
const pages = ['pdf-upload', 'ocr-queue', 'ocr-results'];
const emit = s => ts.transpileModule(s, { compilerOptions: { target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS } }).outputText;
function parse(s) { return ts.createSourceFile('page.tsx', s, 99, true, ts.ScriptKind.TSX); }
function controls(s) {
  const ast = parse(s), result = [];
  function visit(n) {
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n)) {
      const open = n.openingElement || n;
      if (['input','button','select','textarea','Input','Button','Checkbox','NativeSelect','FormTextField'].includes(open.tagName.getText(ast))) result.push({ node:n, open, ast });
    }
    ts.forEachChild(n, visit);
  }
  visit(ast); return result;
}
function nonrender(s, name) {
  const ast = parse(s), edits = [];
  function visit(n) {
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) { edits.push([n.getStart(ast),n.end]); return; }
    ts.forEachChild(n,visit);
  }
  visit(ast); for (const [start,end] of edits.reverse()) s = s.slice(0,start)+'RENDER'+s.slice(end);
  s = s.replace(/^import .*from "@mui\/material";\n/m,'').replace(/^import type \{ ChangeEvent \} from "react";\n/m,'');
  if (name !== 'pdf-upload') s = s.replace(/^import \{ ThemeProvider \} from "@mui\/material\/styles";\n/m,'').replace(/^import \{ sawaTheme \} from "@sawa\/ui";\n/m,'');
  return s;
}
const current = {};
for (const name of pages) {
  const file = 'src/pages/'+name+'.tsx', source = fs.readFileSync(file,'utf8');
  const historical = execFileSync('git',['show',base+':frontend/'+file],{encoding:'utf8'});
  const original = name !== 'ocr-results' ? execFileSync('git',['show',modernBase+':frontend/'+file],{encoding:'utf8'}) : historical;
  current[name] = controls(source);
  test(name+' all rendered control props/options/handler parity and non-rendered source',()=>{
    if (name === 'pdf-upload') {
      const raw = entry => ['input','Input','Checkbox'].includes(entry.open.tagName.getText(entry.ast));
      assert.equal(controls(historical).filter(raw).length,6);
      assert.equal(controls(original).filter(raw).length,7);
      assert.deepEqual(controls(original).filter(entry=>entry.open.tagName.getText(entry.ast)==='FormTextField').map(entry=>entry.open.attributes.properties.find(prop=>prop.name.text==='name').initializer.text),['facility_hint','week_hint','effective_date']);
      assert.match(source,/useForm<UploadConditions>/);
      assert.match(source,/uploadForm\.handleSubmit\(\(values\) => void submit\(values\)\)/);
    }
    if (name === 'ocr-queue') {
      // The fixed legacy control is retained; only the retired resolve controls and source are replaced.
      const before=controls(original), after=current[name];
      assert.equal(before.length,5); assert.equal(after.length,3);
      assert.equal(after[0].open.tagName.getText(after[0].ast),'NativeSelect');
      assert.deepEqual(after[0].node.children.filter(ts.isJsxElement).map(n=>n.openingElement.attributes.properties[0].initializer.text),['','pending','processing','done','error']);
      for (const i of [1,2]) assert.equal(after[i].open.getText(after[i].ast),before[i].open.getText(before[i].ast));
      assert.match(source,/apiClient\.get\("\/ingest\/jobs"/);
      assert.doesNotMatch(source,/\/ocr\/unclassified|resolveJob|templateInputs/);
      for (const fn of ['formatDate','formatStatus']) {
        const declaration=s=>parse(s).statements.find(n=>ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText()==fn)).getText();
        assert.equal(declaration(source),declaration(original));
      }
      assert.match(source,/ThemeProvider theme=\{sawaTheme\}/);
      assert.match(source,/disabled=\{loading \|\| bulkRecoverPending\}/);
      return;
    }
    assert.equal(nonrender(source,name),nonrender(original,name));
    assert.equal(/<(input|button|select|textarea)(\s|>)/.test(source),false);
    const before = controls(original), after = current[name]; assert.equal(after.length,before.length);
    for (let i=0;i<before.length;i++) {
      const b=before[i],a=after[i], props=new Map(a.open.attributes.properties.map(p=>[p.name.text,p]));
      for (const p of b.open.attributes.properties) {
        if (p.name.text==='type' && p.initializer?.text==='checkbox') { assert.equal(a.open.tagName.getText(a.ast),'Checkbox'); continue; }
        const key=p.name.text==='ref'?'inputRef':p.name.text;
        let value=props.get(key)?.initializer;
        const nested=props.get('inputProps')?.initializer.expression.properties.find(n=>n.name.text===key);
        const text=(v,ast)=>!v?'true':ts.isJsxExpression(v)?v.expression.getText(ast):v.getText(ast);
        assert.ok(props.has(key)||nested,key);
        assert.equal(emit('const v='+ (nested?nested.initializer.getText(a.ast):text(value,a.ast))),emit('const v='+text(p.initializer,b.ast)),key);
      }
      if (b.open.tagName.getText(b.ast)==='button' && !b.open.attributes.properties.some(p=>p.name.text==='type')) assert.equal(props.get('type').initializer.text,'submit');
      if (b.open.tagName.getText(b.ast)==='select') assert.equal(a.node.children.map(n=>n.getText(a.ast)).join(''),b.node.children.map(n=>n.getText(b.ast)).join(''));
    }
    if (name!=='pdf-upload') {
      assert.equal((source.match(/<ThemeProvider theme=\{sawaTheme\}>/g)||[]).length,1);
      assert.equal(/SawaProvider|sessionKey|QueryClient/.test(source),false);
      assert.ok(source.includes('import { ThemeProvider } from "@mui/material/styles";'));
      assert.ok(source.includes('import { sawaTheme } from "@sawa/ui";'));
    }
  });
}
test('actual installed MUI raw inputs/FileList callback/ref/checkbox and disabled dispatch',async()=>{
  assert.ok(process.env.OCR_ENTRY_JSDOM_MODULE);
  const {JSDOM}=require(process.env.OCR_ENTRY_JSDOM_MODULE),dom=new JSDOM('<div id="root"></div>',{pretendToBeVisual:true,url:'http://localhost/'}),saved={};
  for (const key of ['window','document','navigator','HTMLElement','Event','MouseEvent','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT']) {
    saved[key]=Object.getOwnPropertyDescriptor(global,key);
    const value=key==='IS_REACT_ACT_ENVIRONMENT'?true:key.endsWith('AnimationFrame')?dom.window[key].bind(dom.window):dom.window[key];
    Object.defineProperty(global,key,{configurable:true,writable:true,value});
  }
  const React=require('react'),{act}=React,mui=require('@mui/material'),root=require('react-dom/client').createRoot(document.getElementById('root'));
  const calls=[],fileInputRef={current:null};
  const context={React,...mui,targetMonth:'',facilityName:'',receivedAt:'',force:false,skipOcr:false,fileInputRef,templateInputs:{component:''},entry:{id:'component'},loading:false,bulkRecoverPending:false,selectionConfirmed:false,selectionCurrent:false,pdfFiles:[]};
  for(const name of ['setTargetMonth','setFacilityName','setReceivedAt','setForce','setSkipOcr','setPdfFiles','setTemplateInputs','recoverReadyQueue','setSelectionConfirmed']) context[name]=value=>calls.push([name,value]);
  async function render(control,key,extra={},form=false) {
    const exports={},bindings={...context,...extra,exports};
    vm.compileFunction(emit('exports.node='+control.node.getText(control.ast)),Object.keys(bindings))(...Object.values(bindings));
    const parent=form?'form':React.Fragment;
    const props=form?{key,onSubmit:event=>{event.preventDefault();calls.push(['submit']);}}:{key};
    await act(async()=>root.render(React.createElement(parent,props,exports.node)));
  }
  try {
    const selected=current['pdf-upload'].filter(a=>['Input','Checkbox'].includes(a.open.tagName.getText(a.ast)));
    assert.equal(selected.length,7);
    for (const [i,a] of selected.entries()) {
      await render(a,i); const element=document.querySelector('input'),start=calls.length;
      if(element.type==='file') {
        assert.equal(fileInputRef.current,element); assert.equal(element.accept,'application/pdf');assert.equal(element.multiple,true);
        const files=[new window.File(['selection only'],'one.bin'),new window.File(['selection only'],'two.bin')];
        Object.defineProperty(element,'files',{configurable:true,value:files});
        await act(async()=>element.dispatchEvent(new Event('change',{bubbles:true})));
        assert.deepEqual(calls.slice(start),[['setPdfFiles',files]]);
      } else if(element.type==='checkbox') {
        await act(async()=>element.click());assert.equal(calls.slice(start).length,1);assert.equal(calls.at(-1)[1],true);
      } else {
        const raw=element.type==='month'?'2026-10':element.type==='datetime-local'?'2026-10-08T12:34':' 000 raw ';
        await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(element,raw);element.dispatchEvent(new Event('input',{bubbles:true}));});
        assert.equal(calls.slice(start).length,1);assert.equal(calls.at(-1)[1],raw);
      }
    }
    const button=current['ocr-queue'].find(a=>a.open.attributes.properties.some(p=>p.name.text==='onClick'&&p.initializer.expression.getText(a.ast)==='recoverReadyQueue'));
    for(const loading of [true,false]) {
      await render(button,'queue',{loading});const start=calls.length,b=document.querySelector('button');assert.equal(b.disabled,loading);assert.equal(b.type,'submit');
      await act(async()=>b.click());assert.equal(calls.slice(start).length,loading?0:1);
    }
    const submit=current['pdf-upload'].find(a=>a.open.attributes.properties.some(p=>p.name.text==='variant'&&p.initializer.text==='contained'));
    for(const [loading,selectionCurrent,selectionConfirmed] of [[false,false,false],[false,true,false],[false,false,true],[true,true,true],[false,true,true]]) {
      await render(submit,'submit',{loading,selectionCurrent,selectionConfirmed},true);
      const start=calls.length,element=document.querySelector('button'),disabled=loading||!selectionCurrent||!selectionConfirmed;
      assert.equal(element.disabled,disabled);await act(async()=>element.click());assert.deepEqual(calls.slice(start),disabled?[]:[['submit']]);
    }
    const {FormProvider,useForm}=await import('react-hook-form'),{FormTextField}=await import('@sawa/ui');
    const effective=current['pdf-upload'].find(a=>a.open.tagName.getText(a.ast)==='FormTextField'&&a.open.attributes.properties.some(p=>p.name.text==='name'&&p.initializer.text==='effective_date'));
    let form;
    function DateForm() {
      form=useForm({defaultValues:{effective_date:''}});const exports={};
      vm.compileFunction(emit('exports.node='+effective.node.getText(effective.ast)),['React','FormTextField','exports'])(React,FormTextField,exports);
      return React.createElement(FormProvider,form,exports.node);
    }
    await act(async()=>root.render(React.createElement(DateForm)));
    const date=document.querySelector('input[name="effective_date"]');assert.equal(date.type,'date');
    await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(date,'2026-10-19');date.dispatchEvent(new Event('input',{bubbles:true}));});
    assert.equal(form.getValues('effective_date'),'2026-10-19');
    await act(async()=>assert.equal(await form.trigger('effective_date'),true));
    await act(async()=>{form.setValue('effective_date','');assert.equal(await form.trigger('effective_date'),false);});
    assert.equal(form.getFieldState('effective_date').error.message,'適用日を入力してください。');
  } finally {
    await act(async()=>root.unmount());dom.window.close();for(const key of Object.keys(saved)){if(saved[key])Object.defineProperty(global,key,saved[key]);else delete global[key];}
  }
});
