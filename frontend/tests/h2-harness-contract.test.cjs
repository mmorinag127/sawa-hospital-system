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
  const replaceOnce=(text,from,to)=>{
    assert.equal(text.split(from).length-1,1,'Only the counted approved harness addition may be normalized');
    return text.replace(from,to);
  };
  let compatible=source;
  compatible=replaceOnce(compatible,`  if(axis==='row'){
    h.review.coordinate_space.height=560;
    h.review.header_band_count=0;
    h.review.y_positions=Array.from({length:57},(_,index)=>index*10);
    h.review.saved_override.corrected_ys=[...h.review.y_positions];
    h.review.saved_override.body_day_boundary_indexes=[0,8,16,24,32,40,48,56];
    h.review.saved_override.day_labels=h.review.calendar_days.map(day=>day.date);
    h.context.rowAxisYs=[...h.review.y_positions];
    h.context.setRowDayBoundaries=value=>{h.context.rowDayBoundaries=value;};
  }
`, '');
  compatible=replaceOnce(compatible,
    "  const review={order_id:'order-A',source_receipt:receipt,saved_override:{quad_px:receipt.suggested_quad_px,source_receipt:plain(receipt)},image_png_base64:'own-image',calendar_days:Array.from({length:7},(_,index)=>({date:`2026-10-${25+index}`,expected_band_count:8}))};",
    "  const review={order_id:'order-A',source_receipt:receipt,saved_override:{quad_px:receipt.suggested_quad_px,source_receipt:plain(receipt)},image_png_base64:'own-image'};");
  compatible=replaceOnce(compatible,
    "headerAxisReview:null,rowAxisReview:null,headerAxisXs:[0,10],rowAxisYs:[0,20],rowDayBoundaries:['0','8','16','24','32','40','48','56'],",
    "headerAxisReview:null,rowAxisReview:null,headerAxisXs:[0,10],rowAxisYs:[0,20],");
  compatible=replaceOnce(compatible,
    "assert.equal(h.state.calls[2].config.headers['If-Match'],'\"9\"');assert.equal(h.state.calls[2].body.document_id,'pdf-A');assert.equal(h.state.discard,0);",
    "assert.equal(h.state.calls[2].config.headers['If-Match'],'\"9\"');assert.equal(h.state.calls[2].body.document_id,'pdf-A');assert.equal(h.state.discard,1);");
  compatible=replaceOnce(compatible,
    "second.resolve({data:{workflow:{order_id:'order-A',revision:9}}});await b;assert.equal(h.state.busy,'');assert.equal(h.state.discard,0);",
    "second.resolve({data:{workflow:{order_id:'order-A',revision:9}}});await b;assert.equal(h.state.busy,'');assert.equal(h.state.discard,1);");
  compatible=replaceOnce(compatible,
    '    selectedOcrResultRef:{current:undefined},pendingSheetCellEditsRef:{current:new Map()},sheetPayloadRef:{current:null},\n    workflow:null,outputGenerationsRef:{current:{download:0,preview:0}},outputOwnersRef:{current:{}},\n    setDownloadMessage:value=>{state.downloadMessage=value;},setOutputPreviewLoading:value=>{state.outputPreviewLoading=value;},setOutputPreviewMessage:value=>{state.outputPreviewMessage=value;},setOutputPreview:value=>{state.outputPreview=value;},\n','');
  compatible=replaceOnce(compatible,'setWorkflow:value=>{context.workflow=value;}','setWorkflow:()=>{}');
  compatible=replaceOnce(compatible,'  // Ownership and adoption checks use production closures, not harness substitutes.\n','');
  compatible=replaceOnce(compatible,"'outputEditorSignature','captureActionInput','ownsActionInput','createActionBoundary','retireOutput',",'');
  compatible=replaceOnce(compatible,'setWorkflow:value=>{h.state.workflow=value;h.context.workflow=value;}','setWorkflow:value=>{h.state.workflow=value;}');
  assert.equal(normalize(compatible),normalize(old));
  execFileSync('git',['merge-base','--is-ancestor','d1ce00c','HEAD']);
  assert.equal(normalize(compatible),normalize(old),'Entire current fixture and original test bodies/PDF evaluator retain the approved closure-binding revision outside the required row-day inputs');
  const production='src/pages/orders/[id]/workflow-v2.tsx';
  const approvedBefore=execFileSync('git',['show','7d7e41d2085376013a835f4e6f5904c4031b0c1f:frontend/'+production],{encoding:'utf8'});
  assert.equal(approvedBefore,execFileSync('git',['show','cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1:frontend/'+production],{encoding:'utf8'}));
  execFileSync('git',['merge-base','--is-ancestor','d557633','3c5a9ca']);
  execFileSync('git',['merge-base','--is-ancestor','3c5a9ca','HEAD']);
  assert.equal(fs.readFileSync(path.join(__dirname,'..',production),'utf8'),execFileSync('git',['show','d26febd:frontend/'+production],{encoding:'utf8'}),'current approved ownership/action/history/feedback/PDF guards remain exact');
  execFileSync('git',['merge-base','--is-ancestor','d26febd','HEAD']);
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
