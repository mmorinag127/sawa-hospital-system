const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const source=fs.readFileSync(path.join(__dirname,'../src/pages/orders/[id]/workflow-v2.tsx'),'utf8');
const ast=ts.createSourceFile('workflow.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
function actual(name,context){
  let expression;
  function visit(node){if(ts.isVariableDeclaration(node)&&node.name.getText(ast)===name)expression=node.initializer?.getText(ast);ts.forEachChild(node,visit);}
  visit(ast);assert.ok(expression,name);
  const code=ts.transpileModule(`exports.fn=${expression}`,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.CommonJS}}).outputText;
  const exports={},bindings={process:undefined,fetch:undefined,require:undefined,global:undefined,module:undefined,Buffer:undefined,globalThis:undefined,...context,exports};
  vm.compileFunction(code,Object.keys(bindings))(...Object.values(bindings));return exports.fn;
}
const plain=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function harness(){
  const state={auth:'A',review:null,points:[],loading:false,error:'',quadMessage:'',busy:'',message:'',step:1.5,discard:0,calls:[],headerMessage:'',rowMessage:'',facilityMessage:'',facilitySaving:false,copySaving:false,confirmReplacement:true};
  const receipt={workflow_revision:8,selected_pdf_receipt:{order_id:'order-A',document_id:'pdf-A',generation:null,sha256:'recorded'},
    selected_version_input:null,input_digest:'actual-input',geometry_source_digest:'recorded-geometry-proof',configured_input_digest:'actual-config',order_input_identity:{facility_code:'A',week_code:'W'},suggested_quad_px:[[0,0],[10,0],[10,20],[0,20]],unknown:{zero:0}};
  const review={order_id:'order-A',source_receipt:receipt,saved_override:{quad_px:receipt.suggested_quad_px,source_receipt:plain(receipt)},image_png_base64:'own-image',calendar_days:Array.from({length:7},(_,index)=>({date:`2026-10-${25+index}`,expected_band_count:8}))};
  const context={orderId:'order-A',effectiveSelectedDocumentId:'pdf-A',router:{asPath:'/orders/order-A/workflow-v2'},quadContextRef:{current:{orderId:'order-A',documentId:'pdf-A',route:'/orders/order-A/workflow-v2'}},
    quadContextGenerationRef:{current:0},quadReadGenerationRef:{current:0},quadCommandGenerationRef:{current:0},quadAliveRef:{current:true},quadBusyOwnerRef:{current:null},busyGenerationRef:{current:0},
    workingInputGenerationRef:{current:0},pageOperationGenerationRef:{current:0},facilitySaveGenerationRef:{current:0},copySaveGenerationRef:{current:0},facilityReadGenerationRef:{current:0},facilitySaveReadOwnerRef:{current:null},
    axisReadGenerationRef:{current:{header:0,row:0}},axisSourceRef:{current:{}},
    quadSourceRef:{current:null},sheetEditGenerationRef:{current:0},activeOrderIdRef:{current:'order-A'},workflowRevisionRef:{current:{revision:8,bootstrap:false}},workflowEditorVersionsRef:{current:{}},
    selectedOcrResultRef:{current:undefined},pendingSheetCellEditsRef:{current:new Map()},sheetPayloadRef:{current:null},sheetDirtyRef:{current:false},unsavedSessionRef:{current:null},sheetPayload:null,
    window:{confirm:()=>state.confirmReplacement},AbortController,
    CANONICAL_SOURCE_READ_TIMEOUT_MS:actual('CANONICAL_SOURCE_READ_TIMEOUT_MS',{}),
    WORKFLOW_READ_PENDING_MESSAGE:actual('WORKFLOW_READ_PENDING_MESSAGE',{}),
    rawInspectionRequestRef:{current:0},setRawInspection:()=>{},
    setSheetPayload:value=>{if(value===null)state.discard++;state.sheetPayload=value;context.sheetPayload=value;context.sheetPayloadRef.current=value;},setSheetDirty:value=>{state.sheetDirty=value;},setSheetJson:value=>{state.sheetJson=value;},setSheetJsonStale:value=>{state.sheetJsonStale=value;},
    workflow:null,outputGenerationsRef:{current:{download:0,preview:0}},outputOwnersRef:{current:{}},
    setDownloadMessage:value=>{state.downloadMessage=value;},setOutputPreviewLoading:value=>{state.outputPreviewLoading=value;},setOutputPreviewMessage:value=>{state.outputPreviewMessage=value;},setOutputPreview:value=>{state.outputPreview=value;},
    workflowJobRef:{current:{orderId:'order-A',jobId:null,attemptId:null,state:'context_confirmed'}},
    quadAuth:()=>state.auth,quadReview:review,hasActiveSessionAuthHeader:()=>state.auth!==null,browserSessionKey:()=>state.auth,
    setBusyState:value=>{state.busy=value;},setSelectedDocumentIdState:()=>{},setQuadReview:value=>{state.review=value;},setManualQuadPoints:value=>{state.points=plain(value);},setManualQuadMode:value=>{state.manualQuadMode=value;},
    setQuadReviewLoading:value=>{state.loading=value;},setQuadReviewMessage:value=>{state.quadMessage=value;},setError:value=>{state.error=value;},setMessage:value=>{state.message=value;},setVisibleStep:value=>{state.step=value;},
    contextFacilityResolution:{status:'ready'},contextForm:{facility_id:'facility-A'},facilityTemplateColumnDraft:[{quantity:0,unknown:null}],expandedCellCopyMode:'disabled',
    buildFacilityTemplateColumnsPayload:rows=>plain(rows),normalizeFacilityTemplateColumns:rows=>rows,normalizeExpandedCellCopyMode:value=>value,
    setFacilityTemplateSaving:value=>{state.facilitySaving=value;},setFacilityTemplateMessage:value=>{state.facilityMessage=value;},setFacilityResolvedConfig:()=>{},setFacilityTemplateColumns:()=>{},setFacilityTemplateColumnDraft:value=>{state.columns=plain(value);},
    setExpandedCellCopySaving:value=>{state.copySaving=value;},setExpandedCellCopyMode:value=>{state.copy=value;},
    setHeaderAxisLoading:value=>{state.headerLoading=value;},setRowAxisLoading:value=>{state.rowLoading=value;},setHeaderAxisMessage:value=>{state.headerMessage=value;},setRowAxisMessage:value=>{state.rowMessage=value;},
    headerAxisReview:null,rowAxisReview:null,headerAxisXs:[0,10],rowAxisYs:[0,20],rowDayBoundaries:['0','8','16','24','32','40','48','56'],
    setHeaderAxisReview:value=>{state.headerReview=value;context.headerAxisReview=value;},setRowAxisReview:value=>{state.rowReview=value;context.rowAxisReview=value;},setRecognitionReview:value=>{state.recognitionReview=value;},setRecognitionValues:value=>{state.recognitionValues=value;},
    setHeaderAxisXs:value=>{state.xs=plain(value);},setRowAxisYs:value=>{state.ys=plain(value);},setSelectedHeaderAxisIndex:()=>{},setSelectedRowAxisIndex:()=>{},setHeaderAxisAddMode:()=>{},setRowAxisAddMode:()=>{},
    getHeaderAxisTimeoutMs:()=>120000,getHeaderAxisExpectedCount:()=>2,
    setSheetAutoEditResult:()=>{},setLocalAnomalyReview:()=>{},setSelectedAnomalyIndex:()=>{},setSelectedAutoEditIndex:()=>{},
    refreshAll:async current=>{if(current&&!current())throw Error('retired refresh');},loadFacilityTemplateStatus:async()=>{},
    defaultSheet:{},formatJson:value=>JSON.stringify(value),invalidateSheetPreSaveChecks:()=>{},
    isPositiveWorkflowRevision:value=>Number.isInteger(value)&&value>0,workflowRevisionTokenFromRead:value=>({revision:value.revision,bootstrap:false}),setWorkflow:value=>{context.workflow=value;},
    formatApiError:(error,message)=>`${message}:${error.message}`,
    apiClient:{get:async(url,config)=>{state.calls.push({method:'GET',url,config});return{data:review};},
      put:async(url,body,config)=>{state.calls.push({method:'PUT',url,body,config});return{data:{workflow:{order_id:'order-A',revision:9}}};},
      post:async(url,body,config)=>{state.calls.push({method:'POST',url,body,config});return{data:{workflow:{order_id:'order-A',revision:10}}};}}
  };
  // Ownership and adoption checks use production closures, not harness substitutes.
  for(const name of ['sameRecordedValue','sameQuadOverrideSource','setBusy','setSelectedDocumentId','editorVersion','hasUnsavedWorkflowInput','confirmWorkflowReplacement','beginWorkflowEditorEdit','finishWorkflowEditorEdit','markSheetDirty','markSheetJsonStale','markSheetClean','discardSheetDraft','retireReplacedWorkflowInput','flushPendingSheetCellEdits','canAdoptReadEditorInput','captureWorkflowInputVersion','workflowMutationConfig','workflowFromMutationResponse','adoptReadWorkflow','adoptOwnWorkflowCommandResponse','capturePageOwner','ownsPageContext','ownsPageInput','outputEditorSignature','captureActionInput','ownsActionInput','createActionBoundary','retireOutput','captureQuadOwner','ownsQuadContext','ownsQuadInput','captureAxisOwner','ownsAxisInput','executeWorkflowMutation','loadAxisReview','saveAxisAndRerun','loadHeaderAxisReview','loadRowAxisReview','runAction'])context[name]=(...args)=>actual(name,context)(...args);
  const load=()=>actual('loadQuadReview',context)();
  const save=()=>actual('saveQuadAndRerun',context)('manual_override',receipt.suggested_quad_px);
  const seed=()=>{context.quadSourceRef.current={review,context:{...context.quadContextRef.current},generation:context.quadContextGenerationRef.current,auth:state.auth};state.review=review;};
  const retire=kind=>{
    if(kind==='pdf'){context.setSelectedDocumentId('pdf-B');context.quadContextRef.current={...context.quadContextRef.current,documentId:'pdf-B'};}
    if(kind==='order'){context.quadContextGenerationRef.current++;context.activeOrderIdRef.current='order-B';context.quadContextRef.current={...context.quadContextRef.current,orderId:'order-B'};}
    if(kind==='route'){context.quadContextGenerationRef.current++;context.quadContextRef.current={...context.quadContextRef.current,route:'/orders/order-A/workflow-v2?other'};}
    if(kind==='auth')state.auth='B';
    if(kind==='manual'){context.beginWorkflowEditorEdit('quad');state.points=[[4,5]];}
    if(kind==='sheet'){context.sheetEditGenerationRef.current++;context.beginWorkflowEditorEdit('sheet');}
    if(kind==='unmount')context.quadAliveRef.current=false;
    if(kind==='busy')context.setBusy('new-operation');
  };
  return{state,context,review,receipt,load,save,seed,retire};
}

test('actual selected PDF GET retains complete receipt and saved points; PUT/POST use exact own CAS revisions',async()=>{
  const h=harness();await h.load();assert.equal(h.state.calls[0].config.params.document_id,'pdf-A');
  assert.deepEqual(h.state.points,h.receipt.suggested_quad_px);assert.equal(h.context.quadSourceRef.current.review,h.review);
  await h.save();const put=h.state.calls[1],post=h.state.calls[2];
  assert.equal(put.body.source_receipt,h.receipt);assert.equal(put.config.headers['If-Match'],'"8"');
  assert.equal(post.config.headers['If-Match'],'"9"');assert.equal(post.body.document_id,'pdf-A');
  assert.equal(h.state.discard,1);assert.equal(h.state.step,2);assert.equal(h.state.busy,'');
  assert.equal(h.receipt.unknown.zero,0);assert.equal(h.receipt.selected_version_input,null);
});

for(const kind of ['pdf','order','route','auth','manual','sheet','unmount'])for(const outcome of ['success','error']){
  test(`actual quad GET ${outcome} after ${kind} cannot resurrect review/points/message or clear newer loading`,async()=>{
    const h=harness(),old=deferred();h.context.apiClient.get=()=>old.promise;
    const waiting=h.load();h.retire(kind);
    h.state.review={order_id:'new'};h.state.quadMessage='new-message';h.state.loading=true;
    ++h.context.quadReadGenerationRef.current; // An actual successor, independent of the retired input.
    if(outcome==='success')old.resolve({data:h.review});else old.reject(Error('old-error'));
    await waiting;assert.equal(h.state.review.order_id,'new');assert.equal(h.state.quadMessage,'new-message');assert.equal(h.state.loading,true);
    assert.deepEqual(h.state.points,kind==='manual'?[[4,5]]:[]);
  });
}

test('competing quad reads adopt only the latest own response; stale finally cannot clear its loading',async()=>{
  const h=harness(),old=deferred(),own=deferred();let count=0;h.context.apiClient.get=()=>++count===1?old.promise:own.promise;
  const a=h.load(),b=h.load();old.resolve({data:h.review});await a;assert.equal(h.state.loading,true);assert.equal(h.state.review,null);
  own.resolve({data:h.review});await b;assert.equal(h.state.loading,false);assert.equal(h.state.review,h.review);
});

for(const stage of ['PUT','POST'])for(const kind of ['pdf','order','route','auth','manual','sheet','unmount','busy'])for(const outcome of ['success','error']){
  test(`actual quad ${stage} ${outcome} after ${kind} never chains/adopts/discards or clears newer busy`,async()=>{
    const h=harness(),pending=deferred();h.seed();
    const method=stage==='PUT'?'put':'post';h.context.apiClient[method]=(url,body,config)=>{h.state.calls.push({method:stage,url,body,config});return pending.promise;};
    const waiting=h.save();if(stage==='POST'){for(let i=0;i<6;i++)await Promise.resolve();assert.equal(h.state.calls.length,2);}
    h.retire(kind);h.state.error='new-error';h.state.message='new-message';
    const previousBusy=h.state.busy;
    if(outcome==='success')pending.resolve({data:{workflow:{order_id:'order-A',revision:stage==='PUT'?9:10}}});else pending.reject(Error('late-error'));
    await waiting;assert.equal(h.state.calls.length,stage==='PUT'?1:2);assert.equal(h.state.discard,0);assert.equal(h.state.step,1.5);
    assert.equal(h.state.busy,kind==='manual'||kind==='sheet'?'':previousBusy);assert.equal(h.state.error,'new-error');assert.equal(h.state.message,'new-message');
  });
}

test('missing auth/document/receipt and mismatched PDF receipt stop explicitly without command/default PDF',async()=>{
  for(const missing of ['auth','document']){const h=harness();if(missing==='auth')h.state.auth=null;else h.context.quadContextRef.current.documentId='';await h.load();assert.equal(h.state.calls.length,0);assert.match(h.state.quadMessage,/確認/);}
  for(const missing of ['receipt','wrong-pdf','wrong-order','revision']){
    const h=harness();if(missing==='receipt')h.review.source_receipt=null;
    if(missing==='wrong-pdf')h.receipt.selected_pdf_receipt.document_id='pdf-B';
    if(missing==='wrong-order')h.review.order_id='order-B';if(missing==='revision')h.receipt.workflow_revision=null;
    await h.load();assert.equal(h.context.quadSourceRef.current,null);assert.match(h.state.quadMessage,/取得に失敗/);
    await h.save();assert.equal(h.state.calls.filter(c=>c.method!=='GET').length,0);
  }
});

test('stale captured review revision never supplements from current workflow; malformed own command response never reruns',async()=>{
  const h=harness();h.seed();h.context.workflowRevisionRef.current={revision:12,bootstrap:false};await h.save();assert.equal(h.state.calls.length,0);assert.match(h.state.error,/版が変わり/);
  const bad=harness();bad.seed();bad.context.apiClient.put=async()=>({data:{workflow:{revision:null}}});await bad.save();assert.equal(bad.state.calls.length,0);assert.match(bad.state.error,/revision/);assert.equal(bad.state.discard,0);
});

test('own revision chain never recaptures latest input even if another workflow read changes the displayed revision',async()=>{
  const h=harness();h.seed();h.context.apiClient.put=async(url,body,config)=>{h.state.calls.push({method:'PUT',url,body,config});h.context.workflowRevisionRef.current={revision:99,bootstrap:false};return{data:{workflow:{order_id:'order-A',revision:9}}};};
  await h.save();assert.equal(h.state.calls[1].config.headers['If-Match'],'"9"');
  const start=source.indexOf('  const saveQuadAndRerun'),end=source.indexOf('  const loadAxisReview',start);
  assert.equal((source.slice(start,end).match(/captureWorkflowInputVersion\(/g)||[]).length,1);
});

test('real-source caller inventory retains ordinary If-Match command entries and every quad manual edit marks ownership',()=>{
  for(const endpoint of ['facility-template-columns','expanded-cell-copy-mode','ocr-runs'])assert.ok(source.includes(`/workflow-v2/${endpoint}`));
  assert.match(source,/executeWorkflowMutation\("templateColumns"/);assert.match(source,/executeWorkflowMutation\("expandedCellCopy"/);assert.match(source,/executeWorkflowMutation\("ocrRun"/);
  assert.match(source,/workflowMutationConfig\(rerunInputVersion/);
  assert.match(source,/"If-Match":/);assert.match(source,/beginWorkflowEditorEdit\("quad"\);\s*setManualQuadMode/);
  assert.match(source,/beginWorkflowEditorEdit\("quad"\);\s*setManualQuadPoints/);
  assert.match(source,/void loadQuadReview\(\);/);assert.doesNotMatch(source,/loadQuadReview\(\)\.catch/);
});

for(const outcome of ['success','error'])test(`sheet edit without successor ends only original quad loading: ${outcome}`,async()=>{
  const h=harness(),pending=deferred();h.context.apiClient.get=()=>pending.promise;
  const waiting=h.load();h.retire('sheet');assert.equal(h.state.loading,true);
  if(outcome==='success')pending.resolve({data:h.review});else pending.reject(Error('old'));
  await waiting;assert.equal(h.state.loading,false);assert.equal(h.state.review,null);assert.equal(h.state.quadMessage,'');
});

for(const mismatch of ['missing','pdf','digest','config','version','identity'])test(`saved override ${mismatch} proof never adopts foreign points`,async()=>{
  const h=harness(),saved=h.review.saved_override;
  if(mismatch==='missing')delete saved.source_receipt;
  if(mismatch==='pdf')saved.source_receipt.selected_pdf_receipt.document_id='pdf-B';
  if(mismatch==='digest')saved.source_receipt.geometry_source_digest='other';
  if(mismatch==='config')saved.source_receipt.configured_input_digest='other';
  if(mismatch==='version')delete saved.source_receipt.selected_version_input;
  if(mismatch==='identity')saved.source_receipt.order_input_identity.facility_code='other';
  await h.load();assert.deepEqual(h.state.points,[]);assert.match(h.state.quadMessage,/正本/);assert.equal(h.state.review,h.review);
});

test('foreign saved override cannot discard actual dirty manual points',async()=>{
  const h=harness();h.retire('manual');delete h.review.saved_override.source_receipt;
  await h.load();assert.deepEqual(h.state.points,[[4,5]]);
});

for(const response of [{revision:9},{order_id:'other',revision:9},{order_id:'order-A'},
  {order_id:'order-A',revision:8},{order_id:'order-A',revision:7},{order_id:'order-A',revision:10}]){
  test(`quad malformed own order/revision ${JSON.stringify(response)} cannot adopt or rerun`,async()=>{
    const h=harness();h.seed();h.context.apiClient.put=async(url,body,config)=>{h.state.calls.push({method:'PUT',url,body,config});return{data:{workflow:response}};};
    await h.save();assert.equal(h.state.calls.length,1);assert.equal(h.state.discard,0);assert.equal(h.context.workflowRevisionRef.current.revision,8);assert.ok(h.state.error);
  });
}

test('generic producer multiple-flush revision is accepted, never blanket plus-one; wrong order/equal revision refused',()=>{
  for(const response of [{order_id:'order-A',revision:11},{order_id:'other',revision:11},{revision:11},{order_id:'order-A',revision:8}]){
    const h=harness();h.context.beginWorkflowEditorEdit('templateColumns',true);const captured=h.context.captureWorkflowInputVersion('templateColumns');
    if(response.order_id==='order-A'&&response.revision===11)assert.equal(h.context.adoptOwnWorkflowCommandResponse({workflow:response},captured),true);
    else assert.throws(()=>h.context.adoptOwnWorkflowCommandResponse({workflow:response},captured),/revision/);
  }
});

for(const axis of ['header','row'])test(`actual ${axis} read/save blocks missing backend receipt without default PDF or lost input`,async()=>{
  const h=harness();h.review.source_receipt=null;
  await h.context.loadAxisReview(axis);assert.match(axis==='header'?h.state.headerMessage:h.state.rowMessage,/source_receipt/);
  await h.context.saveAxisAndRerun(axis);assert.equal(h.state.calls.filter(c=>c.method!=='GET').length,0);assert.equal(h.state.discard,0);
  h.state.auth=null;await h.context.loadAxisReview(axis);assert.equal(h.state.calls.length,1);
});

for(const name of ['saveFacilityTemplateColumns','updateExpandedCellCopyMode'])for(const kind of ['pdf','order','route','auth','manual','sheet','unmount','busy'])for(const outcome of ['success','error']){
  test(`actual ${name} ${outcome} retires on ${kind} alone without discarding draft`,async()=>{
    const h=harness(),pending=deferred();h.context.apiClient.put=(url,body,config)=>{h.state.calls.push({method:'PUT',url,body,config});return pending.promise;};
    const waiting=actual(name,h.context)('enabled');h.retire(kind);
    h.state.error='new-error';h.state.message='new-message';h.state.facilityMessage='new-facility';
    if(outcome==='success')pending.resolve({data:{workflow:{order_id:'order-A',revision:9},expanded_cell_copy_mode:'enabled'}});else pending.reject(Error('old'));
    await waiting;assert.equal(h.state.calls.length,1);assert.equal(h.state.discard,0);assert.equal(h.state.error,'new-error');assert.equal(h.state.message,'new-message');assert.equal(h.state.facilityMessage,'new-facility');
  });
}

for(const kind of ['pdf','order','route','auth','manual','sheet','unmount','busy'])for(const outcome of ['success','error'])test(`actual runAction ${outcome} after ${kind} alone never refreshes/adopts late result`,async()=>{
  const h=harness(),pending=deferred();let refreshes=0;h.context.refreshAll=async()=>{refreshes++;};
  const waiting=h.context.runAction('test',()=>pending.promise,{nextStep:2});h.retire(kind);h.state.error='new-error';h.state.message='new-message';
  if(outcome==='success')pending.resolve();else pending.reject(Error('old'));
  await waiting;assert.equal(refreshes,0);assert.equal(h.state.step,1.5);assert.equal(h.state.error,'new-error');assert.equal(h.state.message,'new-message');
  if(kind==='busy')assert.equal(h.state.busy,'new-operation');else if(kind!=='unmount')assert.equal(h.state.busy,'');
});

test('normal columns/copy retain original payload and null/zero values; actual runOcr uses explicit PDF and CAS',async()=>{
  const columns=harness();await actual('saveFacilityTemplateColumns',columns.context)();
  assert.deepEqual(columns.state.calls[0].body,{columns:[{quantity:0,unknown:null}]});assert.equal(columns.state.calls[0].config.headers['If-Match'],'"8"');assert.equal(columns.state.discard,1);
  const copy=harness(),savedSheet={base_evidence_run_id:'own-evidence',template_version_id:'own-template',rows:[['7','1',0,null,'']],unknown:{zero:0,value:null}};
  const originalSheet=plain(savedSheet);
  copy.context.sheetPayload=copy.context.sheetPayloadRef.current=savedSheet;
  copy.context.workflow={order_id:'order-A',revision:8,saved_sheet_id:'own-saved',selected_ocr_result_id:'own-evidence',template_version_id:'own-template'};
  const originalWorkflow=plain(copy.context.workflow);
  copy.context.pendingSheetCellEditsRef.current.set('0:0',{rowIndex:0,colIndex:0,value:'26'});
  copy.context.apiClient.put=async(url,body,config)=>{
    copy.state.calls.push({method:'PUT',url,body,config});
    assert.equal(copy.context.pendingSheetCellEditsRef.current.size,0);
    assert.deepEqual(plain(copy.context.sheetPayloadRef.current.rows),[['26','1',0,null,'']]);
    return{data:{workflow:{...originalWorkflow,revision:9},selected_ocr_result_id:'own-evidence',expanded_cell_copy_mode:'enabled'}};
  };
  await actual('updateExpandedCellCopyMode',copy.context)('enabled');assert.equal(copy.state.copy,'enabled');assert.equal(copy.state.discard,0);
  assert.equal(copy.state.calls.length,1);assert.deepEqual(copy.state.calls[0].body,{mode:'enabled'});assert.equal(copy.state.calls[0].config.headers['If-Match'],'"8"');
  assert.equal(copy.context.pendingSheetCellEditsRef.current.size,0);assert.equal(copy.state.sheetDirty,true);assert.equal(copy.state.sheetJsonStale,true);
  assert.equal(copy.state.sheetPayload,copy.context.sheetPayloadRef.current);
  assert.deepEqual(plain(copy.context.sheetPayloadRef.current),{...originalSheet,rows:[['26','1',0,null,'']]});
  assert.deepEqual(savedSheet,originalSheet);
  assert.equal(copy.context.workflow.saved_sheet_id,'own-saved');assert.equal(copy.context.workflow.selected_ocr_result_id,'own-evidence');assert.equal(copy.context.workflow.template_version_id,'own-template');
  assert.equal(copy.context.editorVersion('sheet').dirty,true);assert.equal(copy.context.editorVersion('sheet').baseRevision,9);assert.equal(copy.state.step,3);
  const ocr=harness();Object.assign(ocr.context,{ocrRunMode:'hakodate'});
  await actual('runOcr',ocr.context)();assert.equal(ocr.state.calls[0].body.document_id,'pdf-A');assert.equal(ocr.state.calls[0].config.headers['If-Match'],'"8"');assert.equal(ocr.state.step,2);
});

function pdfEffect(context){
  let expression;function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(ast)==='useEffect'&&node.arguments[0]?.getText(ast).includes('.get<Blob>')&&node.arguments[0].getText(ast).includes('/document'))expression=node.arguments[0].getText(ast);ts.forEachChild(node,visit);}visit(ast);assert.ok(expression);
  const exports={};vm.runInNewContext(ts.transpileModule(`exports.fn=${expression}`,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.CommonJS}}).outputText,{...context,exports});return exports.fn();
}
for(const kind of ['pdf','order','route','auth','unmount'])for(const outcome of ['success','error'])test(`actual PDF effect ${outcome} after ${kind} never restores source URL/error`,async()=>{
  const h=harness(),pending=deferred();let url='',error='',created=0,revoked=0,authRetire,routeRetire;
  Object.assign(h.context,{pdfSourceOwnerRef:{current:null},subscribeBrowserSession:fn=>{authRetire=fn;return()=>{};},setPdfUrl:value=>{url=value;},setPdfError:value=>{error=value;},URL:{createObjectURL:()=>{created++;return'own-url';},revokeObjectURL:()=>{revoked++;}}});
  h.context.router={isReady:true,asPath:h.context.router.asPath,events:{on:(_event,fn)=>{routeRetire=fn;},off:()=>{}}};h.context.apiClient.get=()=>pending.promise;
  const cleanup=pdfEffect(h.context);h.retire(kind);if(kind==='auth')authRetire();if(kind==='route')routeRetire();if(kind==='unmount')cleanup();error='new-error';
  if(outcome==='success')pending.resolve({data:{}});else pending.reject(Error('old'));
  for(let i=0;i<6;i++)await Promise.resolve();assert.equal(url,'');assert.equal(error,'new-error');assert.equal(created,0);assert.equal(revoked,0);cleanup();
});

test('actual adopted PDF URL is revoked on auth/route retirement and cleanup',async()=>{
  for(const kind of ['auth','route','unmount']){
    const h=harness();let url='',revoked=0,authRetire,routeRetire;
    Object.assign(h.context,{pdfSourceOwnerRef:{current:null},subscribeBrowserSession:fn=>{authRetire=fn;return()=>{};},setPdfUrl:value=>{url=value;},setPdfError:()=>{},URL:{createObjectURL:()=> 'own-url',revokeObjectURL:value=>{assert.equal(value,'own-url');revoked++;}}});
    h.context.router={isReady:true,asPath:h.context.router.asPath,events:{on:(_event,fn)=>{routeRetire=fn;},off:()=>{}}};
    const cleanup=pdfEffect(h.context);for(let i=0;i<6;i++)await Promise.resolve();assert.equal(url,'own-url');
    if(kind==='auth')authRetire();else if(kind==='route')routeRetire();else cleanup();assert.equal(revoked,1);if(kind!=='unmount')assert.equal(url,'');
  }
});

function axisHarness(axis){
  const h=harness();h.review.coordinate_space={mode:'template_canvas',width:10,height:20};
  h.review.x_positions=[0,10];h.review.y_positions=[0,20];
  h.review.saved_override[axis==='header'?'corrected_xs':'corrected_ys']=axis==='header'?[0,10]:[0,20];
  if(axis==='row'){
    h.review.coordinate_space.height=560;
    h.review.header_band_count=0;
    h.review.y_positions=Array.from({length:57},(_,index)=>index*10);
    h.review.saved_override.corrected_ys=[...h.review.y_positions];
    h.review.saved_override.body_day_boundary_indexes=[0,8,16,24,32,40,48,56];
    h.review.saved_override.day_labels=h.review.calendar_days.map(day=>day.date);
    h.context.rowAxisYs=[...h.review.y_positions];
    h.context.setRowDayBoundaries=value=>{h.context.rowDayBoundaries=value;};
  }
  h.loadAxis=()=>actual(axis==='header'?'loadHeaderAxisReview':'loadRowAxisReview',h.context)();
  h.saveAxis=()=>actual(axis==='header'?'saveHeaderAxisAndRerun':'saveRowAxisAndRerun',h.context)();
  return h;
}
for(const axis of ['header','row'])test(`actual ${axis} source receipt and original PDF survive manual correction and own revision chain`,async()=>{
  const h=axisHarness(axis);await h.loadAxis();assert.equal(h.state.calls[0].config.params.document_id,'pdf-A');
  h.context.beginWorkflowEditorEdit(axis);await h.saveAxis();
  assert.equal(h.state.calls[1].body.source_receipt,h.receipt);assert.equal(h.state.calls[1].config.headers['If-Match'],'"8"');
  assert.equal(h.state.calls[2].config.headers['If-Match'],'"9"');assert.equal(h.state.calls[2].body.document_id,'pdf-A');assert.equal(h.state.discard,1);
});

for(const axis of ['header','row'])for(const kind of ['pdf','order','route','auth','manual','sheet','unmount'])for(const outcome of ['success','error'])test(`actual ${axis} GET ${outcome} after ${kind} alone never adopts old review/points`,async()=>{
  const h=axisHarness(axis),pending=deferred();h.context.apiClient.get=()=>pending.promise;
  const waiting=h.loadAxis();if(kind==='manual')h.context.beginWorkflowEditorEdit(axis);else h.retire(kind);
  h.state.headerMessage='new';h.state.rowMessage='new';h.state.xs=[3];h.state.ys=[4];
  if(outcome==='success')pending.resolve({data:h.review});else pending.reject(Error('old'));
  await waiting;assert.equal(h.state.headerReview,undefined);assert.equal(h.state.rowReview,undefined);assert.deepEqual(h.state.xs,[3]);assert.deepEqual(h.state.ys,[4]);
  assert.equal(axis==='header'?h.state.headerMessage:h.state.rowMessage,'new');
  if(kind!=='unmount')assert.equal(axis==='header'?h.state.headerLoading:h.state.rowLoading,false);
});

for(const axis of ['header','row'])for(const stage of ['PUT','POST'])for(const kind of ['pdf','order','route','auth','manual','sheet','unmount','busy'])for(const outcome of ['success','error'])test(`actual ${axis} ${stage} ${outcome} after ${kind} alone never chains/discards/resets new input`,async()=>{
  const h=axisHarness(axis);await h.loadAxis();const pending=deferred();
  h.context.apiClient[stage==='PUT'?'put':'post']=(url,body,config)=>{h.state.calls.push({method:stage,url,body,config});return pending.promise;};
  const waiting=h.saveAxis();if(stage==='POST')for(let i=0;i<6;i++)await Promise.resolve();
  if(kind==='manual')h.context.beginWorkflowEditorEdit(axis);else h.retire(kind);
  h.state.error='new';h.state.message='new';
  if(outcome==='success')pending.resolve({data:{workflow:{order_id:'order-A',revision:stage==='PUT'?9:10}}});else pending.reject(Error('old'));
  await waiting;assert.equal(h.state.calls.length,stage==='PUT'?2:3);assert.equal(h.state.discard,0);assert.equal(h.state.error,'new');assert.equal(h.state.message,'new');
  if(kind==='busy')assert.equal(h.state.busy,'new-operation');else if(kind!=='unmount')assert.equal(h.state.busy,'');
});

for(const axis of ['header','row'])test(`actual ${axis} competing reads protect successor loading and dirty points`,async()=>{
  const h=axisHarness(axis),first=deferred(),second=deferred();let n=0;h.context.apiClient.get=()=>++n===1?first.promise:second.promise;
  const a=h.loadAxis(),b=h.loadAxis();first.resolve({data:h.review});await a;assert.equal(axis==='header'?h.state.headerLoading:h.state.rowLoading,true);
  second.resolve({data:h.review});await b;assert.equal(axis==='header'?h.state.headerLoading:h.state.rowLoading,false);
});

test('source PDF rendering is bound to actual owner and auth/route event cleanup, not only effect target dependencies',()=>{
  assert.match(source,/const pdfUrl = pdfSourceOwnerRef.current && ownsPageContext/);
  assert.match(source,/pdfSourceOwnerRef.current=own/);assert.match(source,/subscribeBrowserSession\(retire\)/);assert.match(source,/routeChangeStart/);
  assert.doesNotMatch(source,/loadHeaderAxisReview\(\)\.catch|loadRowAxisReview\(\)\.catch/);
  const start=source.indexOf('  const saveAxisAndRerun'),end=source.indexOf('  const loadHeaderAxisReview',start);
  assert.equal((source.slice(start,end).match(/captureWorkflowInputVersion\(/g)||[]).length,1);
});

for(const kind of ['pdf','order','route','auth','manual','sheet','unmount','busy'])for(const outcome of ['success','error'])test(`actual runOcr POST ${outcome} after ${kind} alone protects workflow and result actions`,async()=>{
  const h=harness(),pending=deferred();h.context.ocrRunMode='hakodate';
  h.context.apiClient.post=(url,body,config)=>{h.state.calls.push({method:'POST',url,body,config});return pending.promise;};
  const waiting=actual('runOcr',h.context)();h.retire(kind);h.state.error='new';h.state.message='new';
  if(outcome==='success')pending.resolve({data:{workflow:{order_id:'order-A',revision:9}}});else pending.reject(Error('old'));
  await waiting;assert.equal(h.state.calls.length,1);assert.equal(h.context.workflowRevisionRef.current.revision,8);
  assert.equal(h.state.step,1.5);assert.equal(h.state.error,'new');assert.equal(h.state.message,'new');assert.equal(h.state.discard,0);
});

test('actual competing quad command retires only old command and preserves successor busy',async()=>{
  const h=harness(),first=deferred(),second=deferred();h.seed();let n=0;h.context.apiClient.put=()=>++n===1?first.promise:second.promise;
  const a=h.save(),b=h.save();first.resolve({data:{workflow:{order_id:'order-A',revision:9}}});await a;assert.equal(h.state.busy,'quad review save');assert.equal(h.state.discard,0);
  second.resolve({data:{workflow:{order_id:'order-A',revision:9}}});await b;assert.equal(h.state.busy,'');assert.equal(h.state.discard,1);
});
for(const axis of ['header','row'])test(`actual competing ${axis} correction owns only successor`,async()=>{
  const h=axisHarness(axis);await h.loadAxis();const first=deferred(),second=deferred();let n=0;h.context.apiClient.put=()=>++n===1?first.promise:second.promise;
  const a=h.saveAxis(),b=h.saveAxis();first.resolve({data:{workflow:{order_id:'order-A',revision:9}}});await a;assert.equal(h.state.busy,`${axis} axis save`);assert.equal(h.state.discard,0);
  second.resolve({data:{workflow:{order_id:'order-A',revision:9}}});await b;assert.equal(h.state.busy,'');assert.equal(h.state.discard,1);
});

test('stable geometry proof accepts own saved points across changed full mutation digest without rewriting PUT receipt',async()=>{
  // Protocol fixture only: the backend stable-proof producer is still a prerequisite.
  const h=harness();await h.load();await h.save();
  const saved=plain(h.review.saved_override), next=plain(h.review);
  next.source_receipt.workflow_revision=10;next.source_receipt.input_digest='changed-after-own-write';next.saved_override=saved;
  h.context.apiClient.get=async()=>({data:next});h.context.quadReview=next;h.context.finishWorkflowEditorEdit('quad');
  await h.load();assert.deepEqual(h.state.points,h.receipt.suggested_quad_px);
  assert.equal(h.state.calls[1].body.source_receipt.input_digest,'actual-input');assert.equal(saved.source_receipt.input_digest,'actual-input');
});

for(const missing of ['saved','selected','both','null','empty'])test(`missing stable geometry proof ${missing} stops saved-point adoption even when full input digest matches`,async()=>{
  const h=harness();
  if(missing==='saved'||missing==='both')delete h.review.saved_override.source_receipt.geometry_source_digest;
  if(missing==='selected'||missing==='both')delete h.receipt.geometry_source_digest;
  if(missing==='null')h.review.saved_override.source_receipt.geometry_source_digest=h.receipt.geometry_source_digest=null;
  if(missing==='empty')h.review.saved_override.source_receipt.geometry_source_digest=h.receipt.geometry_source_digest='';
  await h.load();assert.deepEqual(h.state.points,[]);assert.match(h.state.quadMessage,/正本/);
  if(missing!=='saved') {
    await h.save();assert.equal(h.state.calls.filter(call=>call.method!=='GET').length,0);assert.match(h.state.quadMessage,/geometry_source_digest/);
  }
});

function refreshHarness(){
  const h=harness();
  Object.assign(h.context,{refreshGenerationRef:{current:0},sheetDirtyRef:{current:false},pendingSheetCellEditsRef:{current:new Map()},
    ocrDetailRequestRef:{current:0},ocrDetailAbortRef:{current:null},rawInspectionRequestRef:{current:0},
    rawInspectionAbortRef:{current:null},rawInspectionOpenRef:{current:false},ocrCompletionRefreshRef:{current:null},setRawInspection:()=>{},
    workflowJobRef:{current:{orderId:'order-A',jobId:'recorded-job',attemptId:'recorded-attempt',state:'ocr_running'}},
    setWorkflow:value=>{const next=typeof value==='function'?value(h.context.workflow):value;h.state.workflow=next;h.context.workflow=next;},setOrderDetail:value=>{h.state.order=value;},applyPreSaveState:()=>{},
    setOcrResults:value=>{h.state.ocr=value;},setInspection:value=>{h.state.inspection=value;},normalizeSheetPayload:value=>value,
    setSheetPayload:()=>{},setSheetJsonStale:()=>{},setContextForm:()=>{},setWeekDraft:()=>{},weekValueFromRange:()=>'',emptyContext:{facility_id:'',week_start:'',week_end:''}});
  h.context.refreshAll=(...args)=>actual('refreshAll',h.context)(...args);
  h.response=url=>{
    const workflow={order_id:'order-A',revision:8,state:'ocr_running',ocr_job:{ocr_job_id:'recorded-job',workflow_attempt_id:'recorded-attempt',status:'queued',attempt_count:0}};
    return {data:url.endsWith('/ocr-progress')?{...workflow,workflow_revision:8}:
      url.includes('/inspection?')?{workflow,saved_sheet:null,ocr_candidates:require('./helpers/ocr-candidates.cjs')('order-A')}:
      url.endsWith('/workflow-v2')?workflow:{id:'order-A'}};
  };
  return h;
}
function refreshEffect(context,marker){
  let expression;function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(ast)==='useEffect'&&node.arguments[0]?.getText(ast).includes(marker))expression=node.arguments[0].getText(ast);ts.forEachChild(node,visit);}visit(ast);assert.ok(expression,marker);
  const exports={};vm.runInNewContext(ts.transpileModule(`exports.fn=${expression}`,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.CommonJS}}).outputText,{...context,exports});return exports.fn();
}
for(const polling of [false,true])for(const kind of ['pdf','order','route','auth','manual','sheet','unmount','cleanup'])test(`actual ${polling?'OCR polling':'initial refresh'} rejection after ${kind} does not overwrite error`,async()=>{
  const h=refreshHarness(),pending=deferred();let tick;
  Object.assign(h.context,{workflow:{order_id:'order-A',revision:8,state:'ocr_running',ocr_job:{ocr_job_id:'recorded-job',workflow_attempt_id:'recorded-attempt'}},window:{setInterval:fn=>{tick=fn;return 1;},clearInterval:()=>{}}});
  h.context.apiClient.get=()=>pending.promise;
  const cleanup=refreshEffect(h.context,polling?'OCR進捗の取得に失敗しました':'workflow-v2 の取得に失敗しました');if(polling)tick();
  if(kind==='cleanup')cleanup();else h.retire(kind);h.state.error='new-error';pending.reject(Error('old'));
  for(let i=0;i<12;i++)await Promise.resolve();assert.equal(h.state.error,'new-error');assert.equal(h.state.workflow,undefined);cleanup();
});

for(const polling of [false,true])test(`actual ${polling?'polling':'initial'} refresh preserves queued own job and surfaces current rejection`,async()=>{
  const h=refreshHarness();let tick;
  Object.assign(h.context,{workflow:{order_id:'order-A',revision:8,state:'ocr_running',ocr_job:{ocr_job_id:'recorded-job',workflow_attempt_id:'recorded-attempt'}},window:{setInterval:fn=>{tick=fn;return 1;},clearInterval:()=>{}}});h.context.apiClient.get=async url=>h.response(url);
  const cleanup=refreshEffect(h.context,polling?'OCR進捗の取得に失敗しました':'workflow-v2 の取得に失敗しました');if(polling)tick();
  for(let i=0;i<12;i++)await Promise.resolve();assert.equal(h.state.workflow.ocr_job.status,'queued');assert.equal(h.state.workflow.ocr_job.attempt_count,0);assert.equal(h.state.workflow.state,'ocr_running');cleanup();
  h.context.apiClient.get=async()=>{throw Error('own failure');};
  const nextCleanup=refreshEffect(h.context,polling?'OCR進捗の取得に失敗しました':'workflow-v2 の取得に失敗しました');if(polling)tick();
  for(let i=0;i<12;i++)await Promise.resolve();assert.match(h.state.error,/own failure/);nextCleanup();
});

test('actual refresh superseded rejection cannot overwrite a successful newer read',async()=>{
  const h=refreshHarness(),old=deferred();h.context.apiClient.get=()=>old.promise;const a=h.context.refreshAll();
  h.context.apiClient.get=async url=>h.response(url);await h.context.refreshAll();old.reject(Error('old'));await a;
  assert.equal(h.state.workflow.ocr_job.status,'queued');assert.equal(h.state.error,'');
});

function pollingHarness(){
  const h=refreshHarness();h.context.workflow={order_id:'order-A',revision:8,state:'ocr_running',ocr_job:{ocr_job_id:'recorded-job',workflow_attempt_id:'recorded-attempt',status:'queued'}};
  h.timer={tick:null,cleared:0};
  h.context.window={setInterval:fn=>{h.timer.tick=fn;return 1;},clearInterval:()=>{h.timer.cleared++;}};
  h.start=()=>refreshEffect(h.context,'OCR進捗の取得に失敗しました');
  return h;
}
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};

test('actual polling same job different returned attempt cannot adopt response or send another old-attempt tick',async()=>{
  const h=pollingHarness();let calls=0;h.state.workflow=h.context.workflow;
  h.context.apiClient.get=async url=>{calls++;const response=h.response(url);if(url.endsWith('/ocr-progress'))response.data.ocr_job.workflow_attempt_id='replacement-attempt';return response;};
  const cleanup=h.start();h.timer.tick();await settle();
  assert.equal(h.state.workflow.ocr_job.workflow_attempt_id,'recorded-attempt');assert.match(h.state.error,/attempt/);
  h.timer.tick();await settle();assert.equal(calls,1);cleanup();
});

test('actual polling missing recorded attempt does not dispatch',()=>{
  const h=pollingHarness();h.context.workflow.ocr_job.workflow_attempt_id=null;h.start();
  assert.equal(h.timer.tick,null);assert.match(h.state.error,/未記録/);
});

test('actual polling same-job replacement attempt before first tick never dispatches old refreshAll',async()=>{
  const h=pollingHarness();let calls=0;h.context.apiClient.get=async url=>{calls++;return h.response(url);};
  const cleanup=h.start();h.context.workflowJobRef.current.attemptId='replacement-attempt';
  h.timer.tick();await settle();assert.equal(calls,0);assert.equal(h.state.workflow,undefined);cleanup();
});

for(const edit of ['manual','sheet'])for(const outcome of ['success','error'])test(`actual polling ${edit} edit retires old ${outcome} but next tick reads same job without rebasing/resetting dirty editor`,async()=>{
  const h=pollingHarness(),pending=deferred();let calls=0;
  h.context.sheetDirtyRef.current=true;h.context.pendingSheetCellEditsRef.current.set('own-cell',{quantity:0});
  h.context.apiClient.get=()=>{calls++;return pending.promise;};const cleanup=h.start();h.timer.tick();assert.equal(calls,1);
  h.retire(edit);const editor=edit==='manual'?'quad':'sheet';const base=h.context.editorVersion(editor).baseRevision;
  h.state.error='new-input-error';
  if(outcome==='success')pending.resolve(h.response('/orders/order-A/workflow-v2'));else pending.reject(Error('old-input-failure'));
  await settle();assert.equal(h.state.workflow,undefined);assert.equal(h.state.error,'new-input-error');assert.equal(h.state.discard,0);
  h.context.apiClient.get=async url=>{
    calls++;const response=h.response(url);
    if(url.endsWith('/ocr-progress')) response.data.workflow_revision=9;
    const workflow=response.data.workflow;
    if(workflow){workflow.revision=9;workflow.ocr_job.status='running';workflow.ocr_job.progress_step=2;}
    return response;
  };
  h.timer.tick();await settle();assert.equal(calls,4);assert.equal(h.state.workflow.ocr_job.ocr_job_id,'recorded-job');assert.equal(h.state.workflow.ocr_job.progress_step,2);
  assert.equal(h.state.discard,0);assert.equal(h.context.pendingSheetCellEditsRef.current.get('own-cell').quantity,0);
  assert.equal(h.context.editorVersion(editor).dirty,true);assert.equal(h.context.editorVersion(editor).baseRevision,base);
  assert.equal(h.context.captureWorkflowInputVersion(editor).revision,base);assert.equal(h.context.workflowRevisionRef.current.revision,9);
  cleanup();
});

for(const kind of ['pdf','order','route','auth','job','attempt','unmount','cleanup'])for(const outcome of ['success','error'])test(`actual polling ${kind} retirement rejects old ${outcome} and never dispatches another old-job tick`,async()=>{
  const h=pollingHarness(),pending=deferred();let calls=0;h.context.apiClient.get=()=>{calls++;return pending.promise;};
  const cleanup=h.start();h.timer.tick();
  if(kind==='job')h.context.workflowJobRef.current={orderId:'order-A',jobId:'new-recorded-job',attemptId:'recorded-attempt',state:'ocr_running'};
  else if(kind==='attempt')h.context.workflowJobRef.current.attemptId='new-recorded-attempt';
  else if(kind==='cleanup')cleanup();else h.retire(kind);
  h.state.error='current-error';
  if(outcome==='success')pending.resolve(h.response('/orders/order-A/workflow-v2'));else pending.reject(Error('old'));
  await settle();h.timer.tick();await settle();assert.equal(calls,1);assert.equal(h.state.workflow,undefined);assert.equal(h.state.error,'current-error');cleanup();
});

test('actual polling stops on returned different queued job rather than adopting it under old job ownership',async()=>{
  const h=pollingHarness();let calls=0;h.state.workflow=h.context.workflow;
  h.context.apiClient.get=async url=>{calls++;const response=h.response(url);if(url.endsWith('/ocr-progress'))response.data.ocr_job.ocr_job_id='different-recorded-job';return response;};
  const cleanup=h.start();h.timer.tick();await settle();assert.equal(calls,1);assert.equal(h.state.workflow.ocr_job.ocr_job_id,'recorded-job');
  assert.match(h.state.error,/jobが変更/);assert.equal(h.timer.cleared,1);h.timer.tick();await settle();assert.equal(calls,1);cleanup();
});

test('actual polling missing job stops without guessing, and current transient rejection permits next same-job read',async()=>{
  const missing=pollingHarness();missing.context.workflow.ocr_job.ocr_job_id=null;missing.start();assert.equal(missing.timer.tick,null);assert.match(missing.state.error,/未記録/);
  const h=pollingHarness();h.context.apiClient.get=async()=>{throw Error('transient own read');};const cleanup=h.start();h.timer.tick();await settle();assert.match(h.state.error,/transient own read/);
  h.context.apiClient.get=async url=>h.response(url);h.timer.tick();await settle();assert.equal(h.state.workflow.ocr_job.ocr_job_id,'recorded-job');assert.equal(h.state.workflow.ocr_job.status,'queued');cleanup();
});

for(const changed of ['generation','revision'])test(`actual polling binding rejection after newer ${changed} cannot clear current job or stop its timer`,async()=>{
  const h=pollingHarness(),pending=deferred();h.state.workflow=h.context.workflow;
  h.context.apiClient.get=()=>pending.promise;
  const cleanup=h.start();h.timer.tick();
  if(changed==='generation')h.context.refreshGenerationRef.current++;
  else h.context.workflowRevisionRef.current.revision=9;
  h.state.error='new-current-error';
  pending.reject({response:{data:{detail:'ocr_recorded_job_input_changed'}}});
  await settle();
  assert.equal(h.state.error,'new-current-error');
  assert.equal(h.state.workflow.ocr_job.ocr_job_id,'recorded-job');
  assert.equal(h.timer.cleared,0);cleanup();
});

test('actual polling current binding rejection clears only the rejected current execution',async()=>{
  const h=pollingHarness();h.state.workflow=h.context.workflow;
  h.context.apiClient.get=async()=>{throw {response:{data:{detail:'ocr_recorded_job_input_changed'}}};};
  const cleanup=h.start();h.timer.tick();await settle();
  assert.equal(h.state.workflow.ocr_job,null);
  assert.equal(h.state.workflow.common_job_status,null);
  assert.equal(h.state.workflow.ocr_job_binding_error,'ocr_recorded_job_input_changed');
  assert.equal(h.timer.cleared,1);cleanup();
});
