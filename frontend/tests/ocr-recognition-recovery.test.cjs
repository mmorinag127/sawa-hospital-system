const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/pages/orders/[id]/workflow-v2.tsx'), 'utf8');
const ast = ts.createSourceFile('workflow.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function handler(name, context) {
  let expression;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === name) expression = node.initializer.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(expression);
  const sandbox = {...context, exports: {}};
  vm.runInNewContext(ts.transpileModule(`exports.fn=${expression}`, {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText, sandbox);
  return sandbox.exports.fn;
}
function fixture() {
  const calls = [];
  const review = {order_id: 'order', mode: 'diagnostic_subset/v1', retained_cell_count: 1,
    source_receipt: {workflow_revision: 7, recognition_mode: 'diagnostic_subset/v1', selected_pdf_receipt: {document_id: 'pdf'}}, cells: [{cell: 'E11'}]};
  const context = {
    orderId: 'order', effectiveSelectedDocumentId: 'pdf', workflowRevisionRef: {current: {revision: 7}},
    recognitionReview: review, recognitionValues: {E11: '9'},
    editorVersion: () => ({dirty: false}),
    runAction: (_label, fn) => fn({assertCurrent() {}, adopt: fn => fn()}),
    apiClient: {get: async () => ({data: review}), put: async (url, body) => {calls.push({url, body}); return {data: {revision: 8}};}},
    setRecognitionReview: value => calls.push({review: value}), setRecognitionValues() {},
    beginWorkflowEditorEdit() {}, captureWorkflowInputVersion: () => ({revision: 7}),
    workflowMutationConfig: () => ({}), adoptOwnWorkflowCommandResponse: () => true,
    finishWorkflowEditorEdit() {},
  };
  return {context, calls};
}
test('recognition review compares numeric revision and selected PDF', async () => {
  const f = fixture();
  await handler('loadRecognitionReview', f.context)('diagnostic_subset/v1');
  assert.equal(f.calls[0].review.order_id, 'order');
  f.context.effectiveSelectedDocumentId = 'changed';
  await assert.rejects(handler('loadRecognitionReview', f.context)('diagnostic_subset/v1'), /PDF/);
});
test('recognition save retains authored value and refuses stale revision', async () => {
  const f = fixture();
  await handler('saveRecognitionReview', f.context)();
  assert.equal(f.calls[0].body.values.E11, '9');
  assert.equal(f.calls[0].body.source_receipt.workflow_revision, 7);
  f.context.workflowRevisionRef.current.revision = 8;
  const count = f.calls.length;
  await assert.rejects(handler('saveRecognitionReview', f.context)(), /入力版/);
  assert.equal(f.calls.length, count);
});

test('output refusal explains invalid quantities without replacing or accepting them', () => {
  const describeWorkflowBlocker = handler('describeWorkflowBlocker', {});
  const message = describeWorkflowBlocker('sheet_ocr_review_required');
  assert.match(message, /保存済みシートの数量欄/);
  assert.match(message, /文字・記号/);
  assert.match(message, /Step3/);
  assert.match(message, /手動修正して保存/);
  assert.match(message, /OCR原文と保存履歴は保持/);
  assert.equal(handler('formatApiError', {describeWorkflowBlocker})(
    {response: {data: {detail: 'sheet_ocr_review_required'}}}, 'failed'), message);
  assert.match(describeWorkflowBlocker('sheet_identity_review_required'), /日付・食事区分・献立名/);
  assert.equal(describeWorkflowBlocker('unknown_future_error'), 'unknown_future_error');
});
