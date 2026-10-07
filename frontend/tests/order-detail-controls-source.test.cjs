const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {execFileSync}=require('node:child_process');
const ts=require('typescript');
const base='d55763365c3d2159dc976b130e0cc7a6de5f162f';
const file='src/pages/orders/[id].tsx';
const marker='hospital-detail-control';
const parse=s=>ts.createSourceFile(file,s,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
function normalizedSyntax(source){
  const ast=parse(source);
  function css(text){
    const resets=[
      ':global(.hospital-detail-control.MuiButton-root){text-transform:none;font-family:inherit;line-height:normal;min-width:0}',
      ':global(.hospital-detail-control.MuiInput-root input),:global(.hospital-detail-control.MuiInput-root textarea),:global(.hospital-detail-control.MuiInput-root select){padding:0;border:0;background:transparent;font:inherit;color:inherit}',
      ':global(.hospital-detail-control.MuiInput-root select){appearance:auto}',
      ':global(.hospital-detail-control.MuiInput-root .MuiNativeSelect-icon){display:none}',
    ];
    for(const reset of resets)text=text.replace(reset,'');
    // Remove only added marker selector alternatives, retaining every original rule.
    text=text.replace(/,\s*[^,{}]*:global\(\.hospital-detail-control[^)]*\)(?=\s*[,\{])/g,'');
    return text.replace(/\s+/g,' ').trim();
  }
  function value(n){return ts.isJsxExpression(n)?walk(n.expression):walk(n);}
  function attributes(open,converted,tag){
    const props=[];
    for(const p of open.attributes.properties){
      if(!ts.isJsxAttribute(p)){props.push(walk(p));continue;}
      let key=p.name.text;
      if(converted&&['disableUnderline','multiline'].includes(key)){
        assert.equal(p.initializer,undefined);continue;
      }
      if(converted&&key==='inputProps'){
        assert.ok(ts.isObjectLiteralExpression(p.initializer.expression));
        for(const native of p.initializer.expression.properties){
          assert.ok(ts.isPropertyAssignment(native));
          props.push([native.name.text,walk(native.initializer)]);
        }
        continue;
      }
      if(converted&&key==='inputRef')key='ref';
      if(converted&&key==='className'){
        const init=p.initializer;
        if(ts.isStringLiteral(init)){
          const text=init.text.replace(/ hospital-detail-control(?: hospital-detail-shift-(?:input|select))?$/,'');
          if(text===marker)continue;
          props.push([key,[ts.SyntaxKind.StringLiteral,text]]);
        }else{
          const expr=init.expression;
          assert.ok(ts.isBinaryExpression(expr)&&expr.operatorToken.kind===ts.SyntaxKind.PlusToken);
          assert.ok(ts.isStringLiteral(expr.right));assert.equal(expr.right.text,' '+marker);
          assert.ok(ts.isParenthesizedExpression(expr.left));
          props.push([key,walk(expr.left.expression)]);
        }
        continue;
      }
      props.push([key,p.initializer?value(p.initializer):null]);
    }
    // Native button default submit and explicit submit have identical approved meaning.
    if(tag==='button'&&!props.some(p=>p[0]==='type'))props.push(['type',[ts.SyntaxKind.StringLiteral,'submit']]);
    return props.sort((a,b)=>String(a[0]).localeCompare(String(b[0])));
  }
  function walk(n){
    if(!n)return null;
    if(ts.isImportDeclaration(n)&&n.getText(ast)==='import { Button, Input, NativeSelect } from "@mui/material";')return null;
    if(ts.isJsxElement(n)||ts.isJsxSelfClosingElement(n)){
      const open=n.openingElement||n,tag=open.tagName.getText(ast);
      const cls=open.attributes.properties.find(p=>p.name?.text==='className')?.initializer;
      const converted=['Button','Input','NativeSelect'].includes(tag)&&cls?.getText(ast).includes(marker);
      const multiline=open.attributes.properties.some(p=>p.name?.text==='multiline');
      const nativeTag=converted?{Button:'button',NativeSelect:'select',Input:multiline?'textarea':'input'}[tag]:tag;
      if(tag==='style'){
        return ['element',tag,attributes(open,false,tag),n.children.map(child=>{
          if(ts.isJsxExpression(child)&&ts.isNoSubstitutionTemplateLiteral(child.expression))return ['css',css(child.expression.text)];
          return walk(child);
        })];
      }
      return [ts.isJsxElement(n)?'element':'selfClosing',nativeTag,attributes(open,converted,nativeTag),ts.isJsxElement(n)?n.children.map(walk):[]];
    }
    const children=[];ts.forEachChild(n,c=>{const v=walk(c);if(v!==null)children.push(v);});
    if(children.length)return [n.kind,children];
    if(ts.isStringLiteral(n))return [n.kind,n.text];
    return [n.kind,n.getText(ast)];
  }
  return walk(ast);
}
function inventory(source){
  const ast=parse(source),controls=[],protectedNodes=[],geometry=[],options=[];
  function visit(node,protectedParent=false){
    let protectedHere=protectedParent;
    if(ts.isJsxElement(node)||ts.isJsxSelfClosingElement(node)){
      const open=node.openingElement||node,tag=open.tagName.getText(ast);
      const classProp=open.attributes.properties.find(p=>p.name?.text==='className');
      if(tag==='table'&&classProp?.initializer?.text?.startsWith('ocr-sheet-table')){protectedNodes.push(node.getText(ast));protectedHere=true;}
      if(['button','input','select','textarea','Button','Input','NativeSelect'].includes(tag))controls.push({node,open,tag,ast,protected:protectedHere});
      if(['svg','img','canvas'].includes(tag))geometry.push(node.getText(ast));
      if(['option','optgroup'].includes(tag))options.push(node.getText(ast));
    }
    ts.forEachChild(node,n=>visit(n,protectedHere));
  }
  visit(ast);return {ast,controls,protectedNodes,geometry,options};
}
function nonrender(source){
  const ast=parse(source),spans=[];
  function visit(n){if(ts.isJsxElement(n)||ts.isJsxSelfClosingElement(n)||ts.isJsxFragment(n)){spans.push([n.getStart(ast),n.end]);return;}ts.forEachChild(n,visit);}
  visit(ast);for(const [a,b] of spans.reverse())source=source.slice(0,a)+'RENDER'+source.slice(b);
  return source.replace('import { Button, Input, NativeSelect } from "@mui/material";\n','');
}
test('complete ordinary control parity and byte-frozen OCR/geometry surfaces',()=>{
  const original=execFileSync('git',['show',base+':frontend/'+file],{encoding:'utf8'}),received=fs.readFileSync(file,'utf8');
  const statusEdits=[
    ['import { JobStatus, type JobStatusRecord } from "@sawa/ui";\n',''],
    ['  job_status?: JobStatusRecord | null;\n',''],
    ['  const effectiveHakodateJobStatus = hakodateJobStatus;\n','  const effectiveHakodateJobStatus = hakodateJobStatus || order?.workflow_state?.reparse_state || null;\n'],
    ['  const hakodateJobStage = firstNonEmptyText(effectiveHakodateJobStatus?.processing_stage);\n','  const hakodateJobStage = firstNonEmptyText(\n    effectiveHakodateJobStatus?.processing_stage,\n    order?.ocr_processing_stage,\n  );\n'],
    ['                                  {effectiveHakodateJobStatus?.job_status ? <JobStatus job={effectiveHakodateJobStatus.job_status} showDetails /> : <span>ジョブ記録未取得</span>}\n',''],
  ];
  let source=received;
  for(const [accepted,prior] of statusEdits){
    assert.equal(source.split(accepted).length-1,1,'exact approved recorded-status edit');
    source=source.replace(accepted,prior);
  }
  const before=inventory(original),after=inventory(source);
  assert.deepEqual(after.ast.parseDiagnostics,[]);
  assert.equal(nonrender(source),nonrender(original));
  assert.deepEqual(normalizedSyntax(source),normalizedSyntax(original),'entire recursive syntax tree including render conditions and nested callback branches');
  assert.deepEqual(after.protectedNodes,before.protectedNodes);assert.equal(after.protectedNodes.length,3);
  assert.deepEqual(after.geometry,before.geometry);assert.deepEqual(after.options,before.options);
  assert.equal(before.controls.length,140);assert.equal(after.controls.length,140);
  assert.equal(before.controls.filter(c=>c.protected).length,16);
  const b=before.controls.filter(c=>!c.protected),a=after.controls.filter(c=>!c.protected);
  const counts={};for(const c of b)counts[c.tag]=(counts[c.tag]||0)+1;
  assert.deepEqual(counts,{button:97,select:12,input:13,textarea:2});
  for(let i=0;i<b.length;i++){
    const old=b[i],now=a[i],oldProps=new Map(old.open.attributes.properties.map(p=>[p.name.text,p]));
    const props=new Map(now.open.attributes.properties.map(p=>[p.name.text,p]));
    for(const p of [...old.open.attributes.properties,...now.open.attributes.properties])assert.ok(ts.isJsxAttribute(p),'no spread props');
    assert.equal(oldProps.size,old.open.attributes.properties.length,'no duplicate original props');
    assert.equal(props.size,now.open.attributes.properties.length,'no duplicate current props');
    assert.equal(now.tag,{button:'Button',input:'Input',select:'NativeSelect',textarea:'Input'}[old.tag]);
    const normalized=new Map(props);
    if(now.tag==='Input'||now.tag==='NativeSelect'){
      assert.ok(normalized.has('disableUnderline'));
      assert.equal(normalized.get('disableUnderline').initializer,undefined,'disableUnderline remains bare');
      normalized.delete('disableUnderline');
    }
    if(old.tag==='textarea'){
      assert.ok(normalized.has('multiline'));
      assert.equal(normalized.get('multiline').initializer,undefined,'multiline remains bare');
      normalized.delete('multiline');
    }
    if(normalized.has('inputProps')){
      assert.ok(now.tag==='Input'||now.tag==='NativeSelect','native props only on input/select');
      const initializer=normalized.get('inputProps').initializer;
      assert.ok(initializer&&ts.isJsxExpression(initializer)&&ts.isObjectLiteralExpression(initializer.expression));
      normalized.delete('inputProps');
      for(const p of initializer.expression.properties){
        assert.ok(ts.isPropertyAssignment(p),'explicit native prop assignment');
        const key=p.name.text;
        assert.ok(oldProps.has(key),'native prop must exist originally');
        assert.ok(!normalized.has(key),'no duplicate top-level/native prop');
        normalized.set(key,p);
      }
    }
    if(old.tag==='select'&&oldProps.has('ref')){
      assert.ok(normalized.has('inputRef'));
      assert.ok(!normalized.has('ref'));
      normalized.set('ref',normalized.get('inputRef'));normalized.delete('inputRef');
    }
    if(!oldProps.has('className'))normalized.delete('className');
    if(old.tag==='button'&&!oldProps.has('type')){
      assert.equal(normalized.get('type')?.initializer?.text,'submit');
      normalized.delete('type');
    }
    assert.deepEqual([...normalized.keys()].sort(),[...oldProps.keys()].sort(),'complete normalized prop key set');
    for(const [key,p] of oldProps)assert.equal(Boolean(normalized.get(key).initializer),Boolean(p.initializer),key+' initializer presence');
    const cls=props.get('className').initializer;
    const oldCls=oldProps.get('className')?.initializer;
    let ancestor=old.node.parent;
    let shiftField=false;
    while(ancestor){
      if(ts.isJsxElement(ancestor)&&ancestor.openingElement.tagName.getText(old.ast)==='label'&&ancestor.openingElement.attributes.properties.some(p=>p.name?.text==='className'&&p.initializer?.text==='ocr-shift-field'))shiftField=true;
      ancestor=ancestor.parent;
    }
    const suffix=shiftField?' hospital-detail-shift-'+(old.tag==='input'?'input':'select'):'';
    if(shiftField)assert.ok(['input','select'].includes(old.tag));
    if(oldCls&&ts.isJsxExpression(oldCls))assert.equal(cls.expression.getText(now.ast),'('+oldCls.expression.getText(old.ast)+') + " '+marker+'"');
    else assert.equal(cls.text,(oldCls?oldCls.text+' ':'')+marker+suffix);
    for(const [key,p] of oldProps){
      if(key==='className')continue;
      let actualProp=props.get(key==='ref'&&old.tag==='select'?'inputRef':key);
      if(!actualProp)actualProp=props.get('inputProps')?.initializer.expression.properties.find(x=>x.name.text===key);
      assert.ok(actualProp,key);
      const value=actualProp.initializer;
      const actual=value&&ts.isJsxExpression(value)?value.expression:value;
      const expected=p.initializer&&ts.isJsxExpression(p.initializer)?p.initializer.expression:p.initializer;
      if(!expected)assert.equal(value,p.initializer);else assert.equal(actual.getText(now.ast),expected.getText(old.ast),key);
    }
    if(old.tag==='button')assert.equal(props.get('type').initializer.text,oldProps.get('type')?.initializer.text||'submit');
    if(ts.isJsxElement(old.node))assert.deepEqual(now.node.children.map(n=>n.getText(now.ast)),old.node.children.map(n=>n.getText(old.ast)));
    if(old.tag==='textarea')assert.ok(props.has('multiline'));
    if(now.tag==='Input'||now.tag==='NativeSelect')assert.ok(props.has('disableUnderline'));
    if(props.has('inputProps'))for(const p of props.get('inputProps').initializer.expression.properties)assert.ok(oldProps.has(p.name.text));
  }
  for(const ref of ['ocrSwapLeftColumnRef','ocrSwapRightColumnRef'])assert.ok(source.includes('inputRef={'+ref+'}'));
  assert.equal(source.includes('ThemeProvider'),original.includes('ThemeProvider'));
  assert.equal(source.includes('QueryClientProvider'),original.includes('QueryClientProvider'));
  const oldCSS=[...original.matchAll(/<style jsx(?: global)?>\{`([\s\S]*?)`\}<\/style>/g)].map(m=>m[1]);
  const css=[...source.matchAll(/<style jsx(?: global)?>\{`([\s\S]*?)`\}<\/style>/g)].map(m=>m[1]);
  assert.equal(css.length,oldCSS.length);assert.equal(css[1],oldCSS[1]);
  for(const body of oldCSS[0].matchAll(/\{([^{}]*)\}/g))assert.ok(css[0].includes('{'+body[1]+'}'));
  const addedGlobals=[...css[0].matchAll(/:global\(([^)]+)\)/g)].map(m=>m[1]).filter(s=>!oldCSS[0].includes(':global('+s+')'));
  for(const selector of addedGlobals)assert.ok(selector.startsWith('.'+marker+'.'));
  const inputSelector='.ocr-shift-field :global(.hospital-detail-control.hospital-detail-shift-input.MuiInput-root)';
  const selectSelector='.ocr-shift-field :global(.hospital-detail-control.hospital-detail-shift-select.MuiInput-root)';
  assert.notEqual(inputSelector,selectSelector);
  assert.ok(css[0].includes('.ocr-shift-field input,\n        '+inputSelector+' {\n          width: 72px;'));
  assert.ok(css[0].includes('.ocr-shift-field select,\n        '+selectSelector+' {\n          min-width: 128px;'));
  assert.equal(css[0].includes('.ocr-shift-field :global(.hospital-detail-control.MuiInput-root)'),false);
  for(const [tag,submarker,count] of [['Input','hospital-detail-shift-input',3],['NativeSelect','hospital-detail-shift-select',4]]){
    const marked=a.filter(c=>c.open.attributes.properties.some(p=>p.name?.text==='className'&&p.initializer?.text?.split(' ').includes(submarker)));
    assert.equal(marked.length,count);
    for(const c of marked)assert.equal(c.tag,tag);
  }
});
