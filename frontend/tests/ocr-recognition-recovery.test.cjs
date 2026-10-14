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
  const review = {order_id: 'order', source_receipt: {workflow_revision: 7, selected_pdf_receipt: {document_id: 'pdf'}}, cells: [{cell: 'E11'}]};
  const context = {
    orderId: 'order', effectiveSelectedDocumentId: 'pdf', workflowRevisionRef: {current: {revision: 7}},
    recognitionReview: review, recognitionValues: {E11: '9'},
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
  await handler('loadRecognitionReview', f.context)();
  assert.equal(f.calls[0].review.order_id, 'order');
  f.context.effectiveSelectedDocumentId = 'changed';
  await assert.rejects(handler('loadRecognitionReview', f.context)(), /PDF/);
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
