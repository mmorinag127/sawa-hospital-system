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
  compatible=replaceOnce(compatible,
    '    selectedOcrResultRef:{current:undefined},pendingSheetCellEditsRef:{current:new Map()},sheetPayloadRef:{current:null},\n    workflow:null,outputGenerationsRef:{current:{download:0,preview:0}},outputOwnersRef:{current:{}},\n    setDownloadMessage:value=>{state.downloadMessage=value;},setOutputPreviewLoading:value=>{state.outputPreviewLoading=value;},setOutputPreviewMessage:value=>{state.outputPreviewMessage=value;},setOutputPreview:value=>{state.outputPreview=value;},\n','');
  compatible=replaceOnce(compatible,'setWorkflow:value=>{context.workflow=value;}','setWorkflow:()=>{}');
  compatible=replaceOnce(compatible,'  // Ownership and adoption checks use production closures, not harness substitutes.\n','');
  compatible=replaceOnce(compatible,"'outputEditorSignature','captureActionInput','ownsActionInput','createActionBoundary','retireOutput',",'');
  compatible=replaceOnce(compatible,'setWorkflow:value=>{h.state.workflow=value;h.context.workflow=value;}','setWorkflow:value=>{h.state.workflow=value;}');
  assert.equal(normalize(compatible),normalize(old));
  execFileSync('git',['merge-base','--is-ancestor','d1ce00c','HEAD']);
  const approvedHarness=execFileSync('git',['show','d1ce00c:frontend/tests/h2-quad-owner.test.cjs'],{encoding:'utf8'});
  assert.equal(source,approvedHarness,'Entire current fixture and original test bodies/PDF evaluator match the approved closure-binding revision');
  const production='src/pages/orders/[id]/workflow-v2.tsx';
  const approvedBefore=execFileSync('git',['show','7d7e41d2085376013a835f4e6f5904c4031b0c1f:frontend/'+production],{encoding:'utf8'});
  assert.equal(approvedBefore,execFileSync('git',['show','cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1:frontend/'+production],{encoding:'utf8'}));
  execFileSync('git',['merge-base','--is-ancestor','d557633','3c5a9ca']);
  execFileSync('git',['merge-base','--is-ancestor','3c5a9ca','HEAD']);
  let restored=fs.readFileSync(path.join(__dirname,'..',production),'utf8');
  const additions=[
    // Only the exact route identity replacement and empty-owner stop are normalized.
    ["  const orderId = router.isReady && typeof router.query.id === \"string\" && router.query.id !== \"[id]\"\n    ? router.query.id : \"\";", "  const orderId = useMemo(() => {\n    if (typeof router.query.id === \"string\" && router.query.id && router.query.id !== \"[id]\") {\n      return router.query.id;\n    }\n    if (typeof window !== \"undefined\") {\n      const fromLocation = orderIdFromWorkflowPath(window.location.pathname);\n      if (fromLocation && fromLocation !== \"[id]\") return fromLocation;\n    }\n    return orderIdFromWorkflowPath(router.asPath || \"\");\n  }, [router.asPath, router.query.id]);"],
    ["type WorkflowV2 = {", "const orderIdFromWorkflowPath = (asPath: string): string => {\n  const pathname = (asPath || \"\").split(\"?\")[0]?.split(\"#\")[0] || \"\";\n  const match = pathname.match(/^\\/orders\\/([^/]+)\\/workflow-v2\\/?$/);\n  if (!match?.[1]) return \"\";\n  try {\n    return decodeURIComponent(match[1]);\n  } catch {\n    return match[1];\n  }\n};\n\ntype WorkflowV2 = {"],
    ["  const ownsPageContext = (own:ReturnType<typeof capturePageOwner>) => quadAliveRef.current\n    && Boolean(own.context.orderId)\n", "  const ownsPageContext = (own:ReturnType<typeof capturePageOwner>) => quadAliveRef.current\n"],
    ['import { JobStatus, sawaTheme, type JobStatusRecord } from "@sawa/ui";', 'import { sawaTheme } from "@sawa/ui";'],
    ['  common_job_status?: JobStatusRecord | null;\n', ''],
    ['    order_id?: string | null;\n', ''],
    [`  const commonJobStatus = workflow?.order_id === orderId
    && workflow?.ocr_job?.order_id === orderId
    && Boolean(workflow?.ocr_job?.ocr_job_id)
    && workflow?.common_job_status?.domain === "hospital"
    && workflow.common_job_status.jobId === workflow.ocr_job.ocr_job_id
    && workflow.common_job_status.attemptIdentity === (workflow.ocr_job.workflow_attempt_id ?? null)
    ? workflow.common_job_status : null;
`, ''],
    ['          {commonJobStatus ? <JobStatus job={commonJobStatus} showDetails /> : <span>ジョブ記録未取得</span>}\n', ''],
  ];
  for(const [addition,original] of additions) restored=replaceOnce(restored,addition,original);
  assert.equal(restored,execFileSync('git',['show','3c5a9ca:frontend/'+production],{encoding:'utf8'}),'current approved ownership/action/history/feedback/PDF guards remain exact');
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
