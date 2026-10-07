const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {execFileSync}=require('node:child_process');
const ts=require('typescript');
const base='d55763365c3d2159dc976b130e0cc7a6de5f162f';
const cases=[['src/components/UnifiedShell.tsx','hospital-unified-shell-control',{button:1}],['src/pages/hospital-dashboard.tsx','hospital-dashboard-control',{button:1}],['src/pages/auth/automation.tsx','hospital-automation-control',{select:1,input:1,button:1}]];
const parse=s=>ts.createSourceFile('shell.tsx',s,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const presentation={
  'hospital-unified-shell-control':':global(.hospital-unified-shell-control.MuiButton-root){min-height:36px;padding:0 12px;display:inline-flex;align-items:center;border-radius:var(--sawa-radius-sm);font-size:13px;font-weight:800;border:1px solid var(--sawa-border);color:#40514d;background:#fff;cursor:pointer;font-family:inherit;line-height:normal;text-transform:none;min-width:0}:global(.hospital-unified-shell-control.MuiButton-root:hover){border-color:#d39b92;color:#9f3025;background:#fff4f2}',
  'hospital-automation-control':':global(.hospital-automation-control.MuiButton-root){all:revert;font:inherit;outline-color:var(--sawa-accent)}:global(.hospital-automation-control.MuiInput-root){all:revert;display:inline-block;font:inherit}:global(.hospital-automation-control.MuiInput-root input),:global(.hospital-automation-control.MuiInput-root select){all:revert;font:inherit;outline-color:var(--sawa-accent)}:global(.hospital-automation-control.MuiInput-root .MuiNativeSelect-icon){display:none}',
};
function normalized(source,marker){
  const ast=parse(source);
  function walk(n){
    if(!n)return null;
    if(ts.isImportDeclaration(n)&&['import { Button } from "@mui/material";','import { Button, Input, NativeSelect } from "@mui/material";'].includes(n.getText(ast)))return null;
    if(ts.isJsxElement(n)||ts.isJsxSelfClosingElement(n)){
      const open=n.openingElement||n,tag=open.tagName.getText(ast);
      if(tag==='style'){
        const child=n.children.find(c=>ts.isJsxExpression(c)&&ts.isNoSubstitutionTemplateLiteral(c.expression));
        if(child&&presentation[marker]===child.expression.text)return null;
      }
      const converted=['Button','Input','NativeSelect'].includes(tag),props=[];
      for(const p of open.attributes.properties){
        assert.ok(ts.isJsxAttribute(p));const key=p.name.text;
        if(converted&&key==='disableUnderline'){assert.equal(p.initializer,undefined);continue;}
        if(converted&&key==='className'){
          assert.ok(ts.isStringLiteral(p.initializer));
          if(p.initializer.text===marker)continue;
          assert.ok(p.initializer.text.endsWith(' '+marker));
          props.push([key,[ts.SyntaxKind.StringLiteral,p.initializer.text.slice(0,-marker.length-1)]]);continue;
        }
        if(converted&&key==='inputProps'){
          assert.ok(ts.isObjectLiteralExpression(p.initializer.expression));
          for(const q of p.initializer.expression.properties){assert.ok(ts.isPropertyAssignment(q));props.push([q.name.text,walk(q.initializer)]);}continue;
        }
        props.push([key,p.initializer?walk(ts.isJsxExpression(p.initializer)?p.initializer.expression:p.initializer):null]);
      }
      return [ts.isJsxElement(n)?'element':'self',converted?{Button:'button',Input:'input',NativeSelect:'select'}[tag]:tag,props.sort((a,b)=>a[0].localeCompare(b[0])),ts.isJsxElement(n)?n.children.map(walk).filter(v=>v!==null):[]];
    }
    if(ts.isNoSubstitutionTemplateLiteral(n)&&marker==='hospital-dashboard-control'&&ts.isJsxExpression(n.parent)&&ts.isJsxElement(n.parent.parent)&&n.parent.parent.openingElement.tagName.getText(ast)==='style'){
      const text=n.text.replace(':global(.hospital-dashboard-control.MuiButton-root){font-family:inherit;line-height:normal;text-transform:none;min-width:0}','').replace(/,\s*:global\(\.hospital-dashboard-control\.retry-button(?::disabled)?\)/g,'');
      return [n.kind,text.replace(/\s+/g,' ').trim()];
    }
    const children=[];ts.forEachChild(n,c=>{const v=walk(c);if(v!==null)children.push(v);});
    if(children.length)return [n.kind,children];
    if(ts.isStringLiteral(n))return [n.kind,n.text];
    return [n.kind,n.getText(ast)];
  }
  return walk(ast);
}
function controls(source){
  const ast=parse(source),items=[];
  function visit(n){if(ts.isJsxElement(n)||ts.isJsxSelfClosingElement(n)){const open=n.openingElement||n,tag=open.tagName.getText(ast);if(['button','input','select','textarea','Button','Input','NativeSelect'].includes(tag))items.push({n,open,tag,ast});}ts.forEachChild(n,visit);}
  visit(ast);return items;
}
function nonrender(source){
  const ast=parse(source),spans=[];
  function visit(n){if(ts.isJsxElement(n)||ts.isJsxSelfClosingElement(n)||ts.isJsxFragment(n)){spans.push([n.getStart(ast),n.end]);return;}ts.forEachChild(n,visit);}
  visit(ast);for(const [a,b]of spans.reverse())source=source.slice(0,a)+'RENDER'+source.slice(b);
  return source.replace(/^import \{[^\n]+\} from "@mui\/material";\n/,'');
}
for(const [file,marker,counts]of cases)test(file+' full source contract, unexecuted definition',()=>{
  const source=fs.readFileSync(file,'utf8'),original=execFileSync('git',['show',base+':frontend/'+file],{encoding:'utf8'});
  assert.deepEqual(parse(source).parseDiagnostics,[]);assert.equal(nonrender(source),nonrender(original));
  assert.deepEqual(normalized(source,marker),normalized(original,marker),'full recursive syntax including forms/conditions/callbacks');
  const before=controls(original),after=controls(source),actual={};
  for(const c of before)actual[c.tag]=(actual[c.tag]||0)+1;assert.deepEqual(actual,counts);assert.equal(before.length,after.length);
  for(let i=0;i<before.length;i++){
    const old=before[i],now=after[i],b=new Map(old.open.attributes.properties.map(p=>[p.name.text,p])),a=new Map(now.open.attributes.properties.map(p=>[p.name.text,p]));
    for(const p of [...old.open.attributes.properties,...now.open.attributes.properties])assert.ok(ts.isJsxAttribute(p));
    assert.equal(b.size,old.open.attributes.properties.length);assert.equal(a.size,now.open.attributes.properties.length);
    assert.equal(now.tag,{button:'Button',input:'Input',select:'NativeSelect'}[old.tag]);
    const keys=new Map(a);
    if(now.tag!=='Button'){assert.equal(keys.get('disableUnderline').initializer,undefined);keys.delete('disableUnderline');}
    if(keys.has('inputProps')){const p=keys.get('inputProps');keys.delete('inputProps');for(const q of p.initializer.expression.properties){assert.ok(!keys.has(q.name.text));assert.ok(b.has(q.name.text));keys.set(q.name.text,q);}}
    if(!b.has('className'))keys.delete('className');
    assert.deepEqual([...keys.keys()].sort(),[...b.keys()].sort());
    assert.equal(a.get('className').initializer.text,(b.has('className')?b.get('className').initializer.text+' ':'')+marker);
    for(const [key,p]of b){if(key==='className')continue;const q=keys.get(key);assert.equal(Boolean(q.initializer),Boolean(p.initializer));if(p.initializer){const val=n=>ts.isJsxExpression(n)?n.expression:n;assert.equal(val(q.initializer).getText(now.ast),val(p.initializer).getText(old.ast));}}
    if(old.tag==='button')assert.equal(a.get('type').initializer.text,b.get('type')?.initializer.text||'submit');
    if(ts.isJsxElement(old.n))assert.deepEqual(now.n.children.map(n=>n.getText(now.ast)),old.n.children.map(n=>n.getText(old.ast)));
  }
  assert.equal(/ThemeProvider|QueryClientProvider|SessionProvider/.test(source),/ThemeProvider|QueryClientProvider|SessionProvider/.test(original));
  const originalCSS=[...original.matchAll(/<style jsx(?: global)?>\{`([\s\S]*?)`\}<\/style>/g)].map(m=>m[1]);
  const css=[...source.matchAll(/<style jsx(?: global)?>\{`([\s\S]*?)`\}<\/style>/g)].map(m=>m[1]);
  for(const body of originalCSS.flatMap(c=>[...c.matchAll(/\{([^{}]*)\}/g)]))assert.ok(css.some(c=>c.includes('{'+body[1]+'}')));
  for(const block of css)for(const match of block.matchAll(/:global\(([^)]+)\)/g))if(!originalCSS.some(c=>c.includes(match[0])))assert.ok(match[1].startsWith('.'+marker+'.'));
});
