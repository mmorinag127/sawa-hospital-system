// Execute the production save handler; synthetic HTTP is not live OCR proof.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/pages/orders/[id]/workflow-v2.tsx'), 'utf8');
const ast = ts.createSourceFile('workflow.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function handler(context) {
  let expression;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'saveAxisAndRerun') expression = node.initializer.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(expression);
  const code = ts.transpileModule(`exports.fn=${expression}`, {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText;
  const sandbox = {...context, exports: {}};
  vm.runInNewContext(code, sandbox);
  return sandbox.exports.fn;
}
function fixture() {
  const calls = [], messages = [], retired = [];
  const receipt = {workflow_revision: 7, geometry_source_digest: 'source-bound'};
  const review = {source_receipt: receipt, coordinate_space: {mode: 'template_canvas', width: 100, height: 600},
    calendar_days: Array.from({length: 7}, (_, i) => ({date: `2026-10-${25 + i}`}))};
  const own = {context: {orderId: 'synthetic', documentId: 'original-pdf'}};
  const noop = () => {};
  const context = {
    setHeaderAxisMessage: noop, setRowAxisMessage: value => messages.push(value),
    headerAxisReview: null, rowAxisReview: review, headerAxisXs: [], rowAxisYs: Array.from({length: 57}, (_, index) => index * 10),
    rowDayBoundaries: ['0', '8', '16', '24', '32', '40', '48', '56'],
    axisSourceRef: {current: {row: {review, own}}}, ownsPageContext: () => true,
    beginWorkflowEditorEdit: noop, pageOperationGenerationRef: {current: 0},
    confirmWorkflowReplacement: () => true,
    retireReplacedWorkflowInput: editor => retired.push(editor),
    axisReadGenerationRef: {current: {row: 0}}, setRowAxisLoading: noop, setBusy: noop,
    busyGenerationRef: {current: 0}, captureWorkflowInputVersion: () => ({revision: 7}),
    captureAxisOwner: () => own, ownsAxisInput: () => true, setError: value => messages.push(value),
    apiClient: {put: async (url, body) => { calls.push({url, body}); return {data: {revision: 8}}; },
      post: async (url, body) => {calls.push({url, body}); return {data: {revision: 9}};}},
    workflowMutationConfig: () => ({}), getHeaderAxisTimeoutMs: () => 1000,
    adoptOwnWorkflowCommandResponse: () => true, workflowFromMutationResponse: value => value,
    isPositiveWorkflowRevision: value => value > 0, finishWorkflowEditorEdit: noop,
    discardSheetDraft: () => assert.fail('saved/operator sheet must not be discarded by rerun'),
    setSheetAutoEditResult: noop, setLocalAnomalyReview: noop, setSelectedAnomalyIndex: noop,
    setSelectedAutoEditIndex: noop, setMessage: noop, setVisibleStep: noop,
    formatApiError: error => error.message, quadAliveRef: {current: true},
  };
  return {context, calls, messages, retired};
}
test('row save sends explicit day labels and boundaries and retires consented local input after rerun', async () => {
  const f = fixture();
  await handler(f.context)('row');
  assert.equal(f.calls.length, 2);
  assert.deepEqual(Array.from(f.calls[0].body.body_day_boundary_indexes), [0, 8, 16, 24, 32, 40, 48, 56]);
  assert.equal(f.calls[0].body.day_labels[3], '2026-10-28');
  assert.deepEqual(Array.from(f.calls[0].body.corrected_ys), Array.from({length: 57}, (_, index) => index * 10));
  assert.equal(f.calls[1].body.document_id, 'original-pdf');
  assert.deepEqual(f.retired, ['row']);
});
test('declined replacement preserves input and never saves or reruns', async () => {
  const f = fixture();
  f.context.confirmWorkflowReplacement = () => false;
  await handler(f.context)('row');
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.retired, []);
  assert.equal(f.context.rowAxisYs.length, 57);
});
test('missing day boundary keeps editor input and never sends save or rerun', async () => {
  const f = fixture();
  f.context.rowDayBoundaries[3] = '';
  await handler(f.context)('row');
  assert.equal(f.calls.length, 0);
  assert.match(f.messages[0], /7日分/);
  assert.equal(f.context.rowDayBoundaries[3], '');
});

for (const [stage, action, label] of [
  ['row_day_boundaries', 'review_row_day_boundaries', '行・日境界の画像を開いて訂正'],
  ['quad_estimation', 'review_or_edit_quad', '表外枠4点を訂正'],
  ['template_resolution', 'review_facility_template', '施設テンプレートを確認'],
  ['hakodate_live_pipeline', 'review_ocr_error', null],
]) test(`mounted failure ${stage} exposes actual cause and only its correction action`, async () => {
  const {mount, transport} = require('./helpers/modernization-react-dom.cjs');
  const t = transport(), id = 'synthetic-recovery', root = `/orders/${id}`;
  t.on('get', '/auth/me', () => ({data: {role: 'operator'}}));
  t.on('get', '/facilities', () => ({data: {source: 'canonical_json', revision: 'synthetic', facilities: []}}));
  t.on('get', `${root}/workflow-v2`, () => ({data: {order_id: id, revision: 7, state: 'ocr_failed',
    headline: 'generic failure', blockers: ['generic-code'], ocr_job: {order_id: id, ocr_job_id: 'job',
      processing_stage: stage, recovery_action: action, error_detail: 'exact-original-cause'}}}));
  t.on('get', `${root}/workflow-v2/ocr-results`, () => ({data: {results: []}}));
  t.on('get', `${root}/workflow-v2/inspection?include_ocr_results=true`, () => ({data: { ocr_candidates: require('./helpers/ocr-candidates.cjs')(id),saved_sheet: null, pre_save_checks: {}}}));
  t.on('get', root, () => ({data: {id, current_version: {document_id: 'pdf'}, versions: [{document_id: 'pdf', is_current: true}]}}));
  t.on('get', `${root}/week-options`, () => ({data: {options: []}}));
  t.on('get', `${root}/document`, () => ({data: new Blob(['synthetic bytes'])}));
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', {api: t.api, route: `${root}/workflow-v2`,
    routerState: {isReady: true, query: {id}}});
  try {
    await h.waitFor(() => h.document.body.textContent.includes('exact-original-cause'));
    const alert = [...h.document.querySelectorAll('[role="alert"]')].find(node => node.textContent.includes('exact-original-cause'));
    assert.ok(alert);
    assert.deepEqual([...alert.querySelectorAll('button')].map(node => node.textContent.trim()), label ? [label] : []);
  } finally {await h.close();}
});
