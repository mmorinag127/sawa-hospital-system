const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const { execFileSync } = require('node:child_process');
const file = 'src/pages/orders/index.tsx';
const base = '3fa27660c9b07987205a233a5f6087a73a289086';
const source = fs.readFileSync(file, 'utf8');
const original = execFileSync('git', ['show', base + ':frontend/' + file], {encoding:'utf8'});
const parse = s => ts.createSourceFile('page.tsx', s, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const emit = s => ts.transpileModule(s, {compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS}}).outputText;
function nodes(s) {
  const ast = parse(s), found = [];
  function visit(n) {
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n)) {
      const open = n.openingElement || n;
      if (['button','input','select','textarea','Button','Input','Checkbox','NativeSelect'].includes(open.tagName.getText(ast))) found.push({n,open,ast});
    }
    ts.forEachChild(n, visit);
  }
  visit(ast);
  return found;
}
function nonrender(s) {
  const ast = parse(s), edits = [];
  function visit(n) {
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) { edits.push([n.getStart(ast),n.end]); return; }
    ts.forEachChild(n,visit);
  }
  visit(ast);
  for (const [start,end] of edits.reverse()) s = s.slice(0,start)+'RENDER'+s.slice(end);
  return s.replace(/^import \{ Button, Checkbox, Input, NativeSelect \} from "@mui\/material";\n/m,'')
    .replace(/^import \{ ThemeProvider \} from "@mui\/material\/styles";\n/m,'')
    .replace(/^import \{ sawaTheme \} from "@sawa\/ui";\n/m,'');
}
test('all 13 controls preserve original props, options and non-rendered source', () => {
  assert.equal(nonrender(source),nonrender(original));
  const before = nodes(original), after = nodes(source);
  assert.equal(before.length,13);
  assert.equal(after.length,13);
  assert.equal(/<(button|input|select|textarea)\b/.test(source),false);
  assert.equal(/SawaProvider|sessionKey|QueryClient/.test(source),false);
  assert.ok(source.includes('<ThemeProvider theme={sawaTheme}>'));
  for (let i=0;i<13;i++) {
    const b=before[i], a=after[i];
    const props=new Map(a.open.attributes.properties.map(p=>[p.name.text,p]));
    for (const p of b.open.attributes.properties) {
      if (p.name.text==='type' && p.initializer?.text==='checkbox') {
        assert.equal(a.open.tagName.getText(a.ast),'Checkbox');
      } else assert.equal(props.get(p.name.text)?.getText(a.ast),p.getText(b.ast));
    }
    if(b.open.tagName.getText(b.ast)==='select') {
      assert.equal(a.n.children.map(n=>n.getText(a.ast)).join(''),b.n.children.map(n=>n.getText(b.ast)).join(''));
      assert.equal(b.n.children.filter(ts.isJsxElement).length,6);
    }
  }
});
test('real React/MUI raw input, select, checked and disabled dispatch', async () => {
  assert.ok(process.env.ORDER_LIST_JSDOM_MODULE);
  const {JSDOM}=require(process.env.ORDER_LIST_JSDOM_MODULE);
  const dom=new JSDOM('<div id="root"></div>',{pretendToBeVisual:true,url:'http://localhost/'}), saved={};
  const keys=['window','document','navigator','HTMLElement','Event','MouseEvent','requestAnimationFrame','cancelAnimationFrame','IS_REACT_ACT_ENVIRONMENT'];
  for(const key of keys) {
    saved[key]=Object.getOwnPropertyDescriptor(global,key);
    Object.defineProperty(global,key,{configurable:true,writable:true,value:key==='IS_REACT_ACT_ENVIRONMENT'?true:key.endsWith('AnimationFrame')?dom.window[key].bind(dom.window):dom.window[key]});
  }
  const React=require('react'), {act}=React, mui=require('@mui/material');
  const root=require('react-dom/client').createRoot(document.getElementById('root'));
  const calls=[], context={React,...mui,statusFilter:'',search:'',unresolvedOnly:false,showArchived:false,isLoadingMore:false,ORDER_LIST_PAGE_SIZE:50};
  for(const name of ['setStatusFilter','setSearch','setUnresolvedOnly','setShowArchived']) context[name]=v=>calls.push([name,v]);
  const clickEvents=[];
  context.loadMoreOrders=event=>{
    calls.push(['loadMoreOrders',event]);
    clickEvents.push({event,type:event.type,target:event.target,currentTarget:event.currentTarget});
  };
  try {
    for(const [i,c] of nodes(source).entries()) {
      const tag=c.open.tagName.getText(c.ast);
      if(!['Input','Checkbox','NativeSelect'].includes(tag) && !c.open.attributes.properties.some(p=>p.name.text==='onClick' && p.initializer?.expression?.getText(c.ast)==='loadMoreOrders')) continue;
      const exports={}, bindings={...context,exports};
      vm.compileFunction(emit('exports.node='+c.n.getText(c.ast)),Object.keys(bindings))(...Object.values(bindings));
      await act(async()=>root.render(React.createElement(React.Fragment,{key:i},exports.node)));
      const start=calls.length;
      if(tag==='Input') {
        const input=document.querySelector('input');
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,' 000 raw ');
        await act(async()=>input.dispatchEvent(new Event('input',{bubbles:true})));
        assert.deepEqual(calls.slice(start),[['setSearch',' 000 raw ']]);
      } else if(tag==='NativeSelect') {
        const select=document.querySelector('select'); select.value='要確認';
        await act(async()=>select.dispatchEvent(new Event('change',{bubbles:true})));
        assert.deepEqual(calls.slice(start),[['setStatusFilter','要確認']]);
      } else if(tag==='Checkbox') {
        await act(async()=>document.querySelector('input').click());
        assert.equal(calls.slice(start).length,1); assert.equal(calls.at(-1)[1],true);
      } else {
        const button=document.querySelector('button');
        await act(async()=>button.click());
        assert.equal(calls.slice(start).length,1);
        assert.equal(calls[start][0],'loadMoreOrders');
        const captured=clickEvents.at(-1);
        assert.equal(calls[start][1],captured.event);
        assert.equal(captured.type,'click');
        assert.equal(captured.target,button);
        assert.equal(captured.currentTarget,button);
        bindings.isLoadingMore=true;
        vm.compileFunction(emit('exports.node='+c.n.getText(c.ast)),Object.keys(bindings))(...Object.values(bindings));
        await act(async()=>root.render(React.createElement(React.Fragment,{key:i},exports.node)));
        const count=calls.length; await act(async()=>document.querySelector('button').click());
        assert.equal(calls.length,count); assert.equal(document.querySelector('button').disabled,true);
      }
    }
  } finally {
    await act(async()=>root.unmount()); dom.window.close();
    for(const key of keys) if(saved[key]) Object.defineProperty(global,key,saved[key]); else delete global[key];
  }
});
