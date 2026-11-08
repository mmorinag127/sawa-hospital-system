const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/pages/orders/[id]/workflow-v2.tsx'), 'utf8');
const ast = ts.createSourceFile('workflow.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function value(name, bindings) {
  let expression;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === name) expression = node.initializer.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  const exports = {};
  const code = ts.transpileModule(`exports.value = ${expression};`, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  vm.compileFunction(code, ['exports', ...Object.keys(bindings)])(exports, ...Object.values(bindings));
  return exports.value;
}
test('mismatched saved version requires explicit context confirmation, not missing-template fallback', () => {
  const workflow = {facility_id:'FAC00014', week_start:'2026-11-15', week_end:'2026-11-21'};
  for (const state of ['uploaded', 'facility_template_unresolved', 'template_version_mismatch']) {
    assert.equal(value('workflowContextConfirmed', {workflow:{...workflow,state}}), false);
  }
  assert.equal(value('workflowContextConfirmed', {workflow:{...workflow,state:'context_confirmed'}}), true);
});
test('failed template read is never classified as unregistered', () => {
  const contextForm = {facility_id:'F'};
  const facilityTemplateStatus = {facilityId:'F', loading:false, templateId:'', error:'read failed'};
  assert.equal(value('facilityTemplateMissing', {contextForm,facilityTemplateStatus}), false);
  assert.equal(value('facilityTemplateMissing', {contextForm,facilityTemplateStatus:{...facilityTemplateStatus,error:''}}), true);
});
test('upside-down option is in Step1 and is sent without loading quad review', async () => {
  const sent = [];
  const run = value('runOcr', {
    runAction: (_label, action) => action({adopt: fn => fn()}),
    contextFacilityResolution: {status:'ready'}, effectiveSelectedDocumentId:'PDF-A',
    ocrRunMode:'hakodate', effectiveFaxRotation:180, orderId:'ORDER-A',
    executeWorkflowMutation: (_editor, request) => request({headers:{'If-Match':'"7"'}}),
    apiClient: {post: async (...args) => {sent.push(args); return {data:{}};}},
  });
  await run();
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], '/orders/ORDER-A/workflow-v2/ocr-runs');
  assert.equal(sent[0][1].rotation_degrees, 180);
  assert.equal(sent[0][1].document_id, 'PDF-A');
  assert.equal(sent[0][2].headers['If-Match'], '"7"');
  assert.match(source, /rotation_degrees:effectiveFaxRotation/);
  assert.match(source, /FAXを上下反転（180度回転・箱館方式）/);
  assert.match(source, /savedRotation === 180 \? \[\.\.\.savedQuad.slice\(2\), \.\.\.savedQuad.slice\(0, 2\)\]/);
});

test('domain rejection adopts only its own single revision transition, never a conflict or unrelated revision', async () => {
  for (const [code, revision, shouldAdopt] of [
    ['quantity_template_identity_duplicate',8,true], ['workflow_revision_conflict',8,false],
    ['quantity_template_identity_duplicate',9,false],
  ]) {
    const adopted = [], original = {response:{status:409,data:{detail:{
      error:code,workflow:{order_id:'A',revision},message:'列設定の重複',
    }}}};
    const mutate = value('executeWorkflowMutation', {
      sheetDirtyRef:{current:true}, workflow:{}, beginWorkflowEditorEdit:()=>{},
      captureWorkflowInputVersion:()=>({revision:7,owner:{}}), ownsPageInput:()=>true,
      workflowMutationConfig:()=>({}), adoptOwnWorkflowCommandResponse:data=>{adopted.push(data);return true;},
    });
    await assert.rejects(mutate('ocrRun', async()=>{throw original;}, fn=>fn()), error=>error===original);
    assert.equal(adopted.length, Number(shouldAdopt));
  }
});

test('domain 409 is not disguised as another operator conflict', () => {
  const format = value('formatApiError', {describeWorkflowBlocker:code=>code});
  assert.equal(format({response:{status:409,data:{detail:{error:'quantity_template_identity_duplicate',
    message:'数量列の重複'}}}}, 'fallback'), '数量列の重複');
  assert.match(format({response:{status:409,data:{detail:{error:'workflow_revision_conflict'}}}}, ''), /他の更新と競合/);
});
