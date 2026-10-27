const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Authored synthetic identity/geometry only: no live PDF, OCR or provider proof.
const order = 'synthetic-order', documentId = 'pdf-A';
const copy = value => JSON.parse(JSON.stringify(value));
const points = { quad: [[0, 0], [10, 0], [10, 20], [0, 20]], header: [0, 10], row: Array.from({ length: 57 }, (_, index) => index * 10) };
const endpoint = kind => `/orders/${order}/workflow-v2/${kind === 'quad' ? 'quad' : `${kind}-axis`}-review`;
const rerun = `/orders/${order}/workflow-v2/ocr-runs`;
const probeFor = () => ({ component: 'OrderWorkflowV2Page', bindings: [
  'workflow', 'effectiveSelectedDocumentId', 'loadQuadReview', 'loadAxisReview', 'saveQuadAndRerun', 'saveAxisAndRerun',
  'quadReview', 'headerAxisReview', 'rowAxisReview', 'manualQuadPoints', 'headerAxisXs', 'rowAxisYs', 'rowDayBoundaries',
  'quadReviewLoading', 'headerAxisLoading', 'rowAxisLoading', 'quadReviewMessage', 'headerAxisMessage', 'rowAxisMessage',
  'beginWorkflowEditorEdit', 'editorVersion', 'setManualQuadPoints', 'setHeaderAxisXs', 'setRowAxisYs',
  'setSelectedDocumentId', 'setBusy', 'busy', 'setMessage', 'message', 'setError', 'error', 'visibleStep',
  'markSheetDirty', 'sheetDirty', 'sheetDirtyRef', 'pendingSheetCellEditsRef',
] });
function fixture() {
  const t = transport(), state = { revision: 7 };
  const receipt = { workflow_revision: 7,
    selected_pdf_receipt: { order_id: order, document_id: documentId, generation: null, sha256: 'synthetic-pdf-digest' },
    selected_version_input: null, input_digest: 'synthetic-input', geometry_source_digest: 'synthetic-geometry',
    configured_input_digest: 'synthetic-config', order_input_identity: { facility_code: 'synthetic-facility', week_code: '2026-W07' },
    suggested_quad_px: copy(points.quad), unknown: { quantity: 0, absent: null, counts: [0, 6, 9] } };
  const reviews = Object.fromEntries(['quad', 'header', 'row'].map(kind => [kind, {
    order_id: order, source_receipt: copy(receipt), coordinate_space: { width: 10, height: kind === 'row' ? 560 : 20 },
    image_png_base64: '', x_positions: [0, 10], y_positions: copy(points.row), header_band_count: 0,
    calendar_days: Array.from({ length: 7 }, (_, index) => ({ date: `2026-10-${25 + index}`, expected_band_count: 8 })),
    body_day_boundary_indexes: [0, 8, 16, 24, 32, 40, 48, 56],
    saved_override: { source_receipt: copy(receipt), quad_px: copy(points.quad), corrected_xs: [0, 10], corrected_ys: copy(points.row),
      body_day_boundary_indexes: [0, 8, 16, 24, 32, 40, 48, 56], day_labels: ['2026-10-25', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31'] },
  }]));
  const row = reviews.row;
  assert.equal(row.y_positions.length - 1 - row.header_band_count, row.body_day_boundary_indexes.at(-1));
  assert.equal(row.y_positions.at(-1), row.coordinate_space.height);
  assert.deepEqual(row.saved_override.corrected_ys, row.y_positions);
  assert.deepEqual(row.saved_override.day_labels, row.calendar_days.map(day => day.date));
  row.calendar_days.forEach((day, index) => {
    assert.equal(row.body_day_boundary_indexes[index + 1] - row.body_day_boundary_indexes[index], day.expected_band_count);
  });
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic-master', facilities: [] } }));
  t.on('get', `/orders/${order}/workflow-v2`, () => ({ data: { order_id: order, revision: state.revision,
    state: 'output_review', blockers: [], warnings: [], output_bundle_id: null } }));
  t.on('get', `/orders/${order}/workflow-v2/ocr-results`, () => ({ data: { results: [] } }));
  t.onWorkflowInspection(`/orders/${order}/workflow-v2/display?scope=summary`, () => ({ data: { workflow: { order_id: order, revision: state.revision, state: 'output_review', blockers: [], warnings: [], output_bundle_id: null }, ocr_candidates: require('./helpers/ocr-candidates.cjs')(order), pre_save_checks: {} } }));
  t.on('get', `/orders/${order}`, () => ({ data: { id: order, current_version: { document_id: documentId }, versions: [
    { document_id: documentId, version_no: 1, is_current: true }, { document_id: 'pdf-B', version_no: 2, is_current: false } ] } }));
  t.on('get', `/orders/${order}/week-options`, () => ({ data: { options: [] } }));
  t.on('get', `/orders/${order}/document`, () => ({ data: new Blob(['synthetic-not-a-real-pdf']) }));
  for (const kind of ['quad', 'header', 'row']) {
    t.on('get', endpoint(kind), () => ({ data: copy(reviews[kind]) }));
    t.on('put', endpoint(kind), () => ({ data: { workflow: { order_id: order, revision: ++state.revision, state: 'output_review' } } }));
  }
  t.on('post', rerun, () => ({ data: { workflow: { order_id: order, revision: ++state.revision, state: 'output_review' } } }));
  return { t, state, receipt, reviews };
}
async function setup() {
  const f = fixture(), probe = probeFor();
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: f.t.api, probe, route: `/orders/${order}/workflow-v2` });
  try {
    await h.waitFor(() => probe.current?.workflow?.revision === 7 && probe.current.effectiveSelectedDocumentId === documentId,
      'actual workflow and selected PDF read-ready');
    return { ...f, h, probe };
  } catch (error) { await h.close(); throw error; }
}
const reviewOf = (p, kind) => p[kind === 'quad' ? 'quadReview' : `${kind}AxisReview`];
const pointsOf = (p, kind) => p[kind === 'quad' ? 'manualQuadPoints' : kind === 'header' ? 'headerAxisXs' : 'rowAxisYs'];
const loadingOf = (p, kind) => p[kind === 'quad' ? 'quadReviewLoading' : `${kind}AxisLoading`];
const messageOf = (p, kind) => p[kind === 'quad' ? 'quadReviewMessage' : `${kind}AxisMessage`];
const load = (p, kind) => kind === 'quad' ? p.loadQuadReview() : p.loadAxisReview(kind);
const save = (p, kind) => kind === 'quad' ? p.saveQuadAndRerun('manual_override', p.manualQuadPoints) : p.saveAxisAndRerun(kind);
async function start(h, action) {
  let promise;
  await h.settle(() => {
    promise = action();
    // Observe rejection immediately; all assertions still await the original promise.
    promise.then(() => {}, () => {});
  });
  return { promise };
}
async function retire(h, probe, kind, boundary) {
  await h.settle(() => {
    if (boundary === 'pdf') probe.current.setSelectedDocumentId('pdf-B');
    else {
      probe.current.beginWorkflowEditorEdit(kind);
      if (kind === 'quad') probe.current.setManualQuadPoints([[6, 9]]);
      else if (kind === 'header') probe.current.setHeaderAxisXs([0, 6, 9]);
      else probe.current.setRowAxisYs([0, 6, 9]);
    }
    probe.current.setMessage('new-owned-message');
    probe.current.setError('new-owned-error');
  });
}

for (const kind of ['quad', 'header', 'row']) test(`actual DOM ${kind} normal receipt/points and exact own CAS/PDF retain 0/null/unknown`, async () => {
  const { h, probe, t, receipt } = await setup();
  try {
    await h.settle(() => load(probe.current, kind));
    assert.deepEqual(copy(reviewOf(probe.current, kind).source_receipt), receipt);
    assert.deepEqual(copy(pointsOf(probe.current, kind)), points[kind]);
    const get = t.calls.find(c => c.method === 'get' && c.url === endpoint(kind));
    assert.equal(get.config.params.document_id, documentId);
    await h.settle(() => save(probe.current, kind));
    const put = t.calls.find(c => c.method === 'put'), post = t.calls.find(c => c.method === 'post');
    assert.ok(put); assert.ok(post);
    assert.deepEqual(copy(put.body.source_receipt), receipt);
    assert.deepEqual(copy(put.body[kind === 'quad' ? 'quad_px' : kind === 'header' ? 'corrected_xs' : 'corrected_ys']), points[kind]);
    if (kind === 'row') {
      assert.deepEqual(copy(probe.current.rowDayBoundaries), ['0', '8', '16', '24', '32', '40', '48', '56']);
      assert.deepEqual(copy(put.body.body_day_boundary_indexes), [0, 8, 16, 24, 32, 40, 48, 56]);
      assert.deepEqual(copy(put.body.day_labels), ['2026-10-25', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31']);
    }
    assert.equal(put.config.headers['If-Match'], '"7"'); assert.equal(post.config.headers['If-Match'], '"8"');
    assert.deepEqual(copy(post.body), { stale_action: 'retry', force: true, mode: 'hakodate', document_id: documentId });
    assert.equal(probe.current.workflow.revision, 9); assert.equal(probe.current.visibleStep, 2);
    assert.equal(probe.current.busy, ''); assert.equal(loadingOf(probe.current, kind), false);
  } finally { await h.close(); }
});

for (const kind of ['quad', 'header', 'row']) for (const stage of ['get', 'put', 'post'])
for (const boundary of ['pdf', 'manual']) for (const outcome of ['success', 'error'])
test(`actual DOM ${kind} ${stage} late ${outcome} after ${boundary} alone cannot adopt/rerun/clobber feedback`, async () => {
  const { h, probe, t, reviews } = await setup(), pending = deferred();
  let operation;
  try {
    if (stage !== 'get') await h.settle(() => load(probe.current, kind));
    const url = stage === 'post' ? rerun : endpoint(kind);
    t.on(stage, url, () => pending.promise);
    const before = t.calls.filter(c => c.method === stage && c.url === url).length;
    operation = await start(h, () => stage === 'get' ? load(probe.current, kind) : save(probe.current, kind));
    await h.waitFor(() => t.calls.filter(c => c.method === stage && c.url === url).length === before + 1, 'request actually started');
    await retire(h, probe, kind, boundary);
    const retainedPoints = copy(pointsOf(probe.current, kind)), retainedReview = reviewOf(probe.current, kind);
    const retainedRevision = probe.current.workflow.revision;
    await h.settle(() => outcome === 'success'
      ? pending.resolve({ data: stage === 'get' ? copy(reviews[kind]) : { workflow: { order_id: order, revision: stage === 'put' ? 8 : 9 } } })
      : pending.reject(new Error('synthetic obsolete transport failure')));
    await operation.promise;
    assert.deepEqual(copy(pointsOf(probe.current, kind)), retainedPoints);
    assert.equal(reviewOf(probe.current, kind), retainedReview);
    assert.equal(probe.current.workflow.revision, retainedRevision);
    assert.equal(probe.current.sheetDirty, false); assert.equal(probe.current.sheetDirtyRef.current, false);
    if (boundary === 'manual') assert.equal(probe.current.editorVersion(kind).dirty, true);
    assert.equal(probe.current.message, 'new-owned-message');
    assert.equal(probe.current.error, 'new-owned-error');
    assert.equal(t.calls.filter(c => c.method === 'post').length, stage === 'post' ? 1 : 0);
  } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await h.close(); }
});

for (const kind of ['quad', 'header', 'row']) test(`actual DOM ${kind} manual retirement preserves sheet draft seeded before command`, async () => {
  const { h, probe, t } = await setup(), pending = deferred();
  let operation;
  try {
    await h.settle(() => load(probe.current, kind));
    // Real sheet edit predates the captured command, not its retirement boundary.
    await h.settle(() => probe.current.markSheetDirty());
    assert.equal(probe.current.sheetDirty, true);
    t.on('put', endpoint(kind), () => pending.promise);
    operation = await start(h, () => save(probe.current, kind));
    await h.waitFor(() => t.calls.some(c => c.method === 'put' && c.url === endpoint(kind)), 'dirty-sheet command started');
    await retire(h, probe, kind, 'manual');
    await h.settle(() => pending.resolve({ data: { workflow: { order_id: order, revision: 8 } } }));
    await operation.promise;
    assert.equal(probe.current.sheetDirty, true); assert.equal(probe.current.sheetDirtyRef.current, true);
    assert.equal(probe.current.editorVersion('sheet').dirty, true);
    assert.equal(probe.current.editorVersion(kind).dirty, true);
    assert.equal(probe.current.workflow.revision, 7);
    assert.equal(t.calls.filter(c => c.method === 'post').length, 0);
  } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await h.close(); }
});

for (const kind of ['quad', 'header', 'row']) for (const stage of ['put', 'post'])
for (const outcome of ['success', 'error'])
test(`actual DOM ${kind} ${stage} old ${outcome} cannot clear independent newer busy/feedback`, async () => {
  const { h, probe, t } = await setup(), pending = deferred();
  let operation;
  try {
    await h.settle(() => load(probe.current, kind));
    const url = stage === 'post' ? rerun : endpoint(kind);
    t.on(stage, url, () => pending.promise);
    operation = await start(h, () => save(probe.current, kind));
    await h.waitFor(() => t.calls.some(c => c.method === stage && c.url === url), 'old command actually started');
    await h.settle(() => {
      probe.current.setBusy('independent-successor');
      probe.current.setMessage('successor-message'); probe.current.setError('successor-error');
    });
    const revision = probe.current.workflow.revision;
    await h.settle(() => outcome === 'success'
      ? pending.resolve({ data: { workflow: { order_id: order, revision: stage === 'put' ? 8 : 9 } } })
      : pending.reject(new Error('obsolete command')));
    await operation.promise;
    assert.equal(probe.current.busy, 'independent-successor');
    assert.equal(probe.current.message, 'successor-message'); assert.equal(probe.current.error, 'successor-error');
    assert.equal(probe.current.workflow.revision, revision);
    assert.equal(t.calls.filter(c => c.method === 'post').length, stage === 'post' ? 1 : 0);
  } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await h.close(); }
});

for (const kind of ['quad', 'header', 'row']) for (const invalid of ['missing', 'foreign-pdf', 'foreign-order'])
test(`actual DOM ${kind} ${invalid} receipt explicitly stops all mutation`, async () => {
  const { h, probe, t, reviews } = await setup();
  try {
    const bad = copy(reviews[kind]);
    if (invalid === 'missing') bad.source_receipt = null;
    if (invalid === 'foreign-pdf') bad.source_receipt.selected_pdf_receipt.document_id = 'pdf-B';
    if (invalid === 'foreign-order') bad.source_receipt.selected_pdf_receipt.order_id = 'foreign-order';
    t.on('get', endpoint(kind), () => ({ data: bad }));
    await h.settle(() => load(probe.current, kind));
    assert.equal(messageOf(probe.current, kind), kind === 'quad'
      ? '4点確認の選択PDFまたはsource receiptを確認できません。'
      : '選択PDFのsource_receipt契約を確認できません。補正保存・OCR再実行を停止しています。');
    await h.settle(() => save(probe.current, kind));
    assert.equal(t.calls.filter(c => ['put', 'post', 'delete'].includes(c.method)).length, 0);
    assert.ok(messageOf(probe.current, kind));
  } finally { await h.close(); }
});

for (const kind of ['quad', 'header', 'row']) test(`actual DOM ${kind} obsolete read finally cannot clear successor loading/message`, async () => {
  const { h, probe, t, reviews } = await setup(), old = deferred(), next = deferred();
  let first, second, n = 0;
  try {
    t.on('get', endpoint(kind), () => ++n === 1 ? old.promise : next.promise);
    first = await start(h, () => load(probe.current, kind));
    await h.waitFor(() => n === 1, 'first read started');
    second = await start(h, () => load(probe.current, kind));
    await h.waitFor(() => n === 2, 'successor read started');
    await h.settle(() => old.reject(new Error('obsolete read'))); await first.promise;
    assert.equal(loadingOf(probe.current, kind), true); assert.equal(messageOf(probe.current, kind), '');
    await h.settle(() => next.resolve({ data: copy(reviews[kind]) })); await second.promise;
    assert.equal(loadingOf(probe.current, kind), false); assert.deepEqual(copy(pointsOf(probe.current, kind)), points[kind]);
  } finally { old.resolve({ data: {} }); next.resolve({ data: {} });
    if (first) await first.promise; if (second) await second.promise; await h.close(); }
});
