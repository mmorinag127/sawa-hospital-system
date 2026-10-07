const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const {createRequire}=require('node:module'),{execFileSync}=require('node:child_process');
const file=path.join(__dirname,'h2-quad-owner.test.cjs'),source=fs.readFileSync(file,'utf8');
const prefix=source.slice(0,source.indexOf('\ntest('));
const {actual,harness}=vm.compileFunction(prefix+';return {actual,harness};',['require','__dirname'])(createRequire(file),__dirname);

test('current-context actual producer preserves literals and context snapshot semantics',async()=>{
  const h=harness(),captured=actual('saveFacilityTemplateColumns',h.context);
  h.context.facilityTemplateColumnDraft=[{quantity:99,unknown:'new'}];
  await captured();
  const body=h.state.calls[0].body;
  assert.deepEqual(body,{columns:[{quantity:0,unknown:null}]});
  assert.equal(Object.getPrototypeOf(body),Object.prototype);
  assert.equal(Object.getPrototypeOf(body.columns),Array.prototype);
  assert.equal(Object.getPrototypeOf(body.columns[0]),Object.prototype);
  const capture=actual('capturePageOwner',h.context),ref=h.context.workingInputGenerationRef;
  h.context.orderId='new-order';
  h.context.workingInputGenerationRef={current:99};ref.current=3;
  const owner=capture();
  assert.equal(owner.context.orderId,'order-A');
  assert.equal(owner.inputGeneration,3);
  const missing={...h.context};delete missing.quadAuth;
  assert.throws(()=>actual('capturePageOwner',missing)(),ReferenceError);
});

test('original fixtures, assertions, transpilation and PDF evaluator are byte identical',()=>{
  const old=execFileSync('git',['show','cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1:frontend/tests/h2-quad-owner.test.cjs'],{encoding:'utf8'});
  const normalize=s=>{
    const tree=ts.createSourceFile('h.cjs',s,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
    return tree.statements.filter(n=>!(ts.isFunctionDeclaration(n)&&n.name.text==='actual')).map(n=>n.getText(tree)).join('\n');
  };
  assert.equal(normalize(source),normalize(old));
  const production='src/pages/orders/[id]/workflow-v2.tsx';
  assert.equal(fs.readFileSync(path.join(__dirname,'..',production),'utf8'),execFileSync('git',['show','cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1:frontend/'+production],{encoding:'utf8'}));
  const before=old.slice(old.indexOf('function actual('),old.indexOf('\nconst plain='));
  const after=source.slice(source.indexOf('function actual('),source.indexOf('\nconst plain='));
  assert.equal(after.replace('const exports={},bindings={process:undefined,fetch:undefined,require:undefined,global:undefined,module:undefined,Buffer:undefined,globalThis:undefined,...context,exports};\n  vm.compileFunction(code,Object.keys(bindings))(...Object.values(bindings));return exports.fn;', 'const exports={};vm.runInNewContext(code,{...context,exports});return exports.fn;'),before);
});

test('Node ambient transports are undefined unless explicitly captured',()=>{
  const compile=vm.compileFunction,names=['process','fetch','require','global','module','Buffer','globalThis'];
  let observed;
  try{
    vm.compileFunction=(code,parameters,options)=>{
      const fn=compile(code+';exports.ambient=['+names.join(',')+'];',parameters,options);
      return(...values)=>{const result=fn(...values);observed=values[parameters.indexOf('exports')].ambient;return result;};
    };
    const h=harness();actual('capturePageOwner',h.context)();
    assert.deepEqual(observed,names.map(()=>undefined));
    const explicit={source:'explicit fixture only'};
    actual('capturePageOwner',{...h.context,process:explicit})();
    assert.equal(observed[0],explicit);
    assert.deepEqual(observed.slice(1),names.slice(1).map(()=>undefined));
  }finally{vm.compileFunction=compile;}
});
