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

test('current production closures retain quantity, CAS and selected PDF contracts',async()=>{
  const h=harness();await h.load();await h.save();
  assert.equal(h.state.calls[1].config.headers['If-Match'],'"8"');
  assert.equal(h.state.calls[1].body.source_receipt.selected_pdf_receipt.document_id,'pdf-A');
  assert.deepEqual(h.state.calls[1].body.source_receipt,h.receipt);
  assert.equal(h.state.discard,1);
  assert.equal(h.state.calls[2].config.headers['If-Match'],'"9"');
  assert.equal(h.state.calls[2].body.document_id,'pdf-A');
  assert.equal(h.receipt.unknown.zero,0);
  assert.equal(h.receipt.selected_version_input,null);
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
