const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Mounted production UI + synthetic HTTP/session only. These are NOT OCR, PDF,
// backend ownership/receipt validation, PG, operator, browser or live-flow proofs.
// Shapes: WorkflowV2/InspectionPayload/SheetAutoEditResult in workflow-v2.tsx;
// build_sheet_from_selected_ocr/get_sheet_auto_edit_job server serializers;
// qty fields match test_modernization_explicit_ocr_quantity_contract.py FIELDS.
const ORDER = 'synthetic-order', OTHER = 'synthetic-order-B', PDF = 'pdf-A';
const SELECTED = 'evidence-A', NEXT = 'evidence-B', JOB = 'auto-edit-A';
const BASE = `/orders/${ORDER}/workflow-v2`;
const clone = value => JSON.parse(JSON.stringify(value));
const fields = ['date', 'daypart', 'category', 'menu_name',
  'qty.regular_x', 'qty.no_meat_x', 'qty.no_fish_x', 'qty.staff_x'];
const RAW = ['7', '0', '?', ''];
const checks = { anomaly_review: { confirmed: true }, sheet_review: { confirmed: true } };
const status = { anomaly_review_confirmed: true, sheet_review_confirmed: true, ready: true };
const warning = { type: 'quantity', row_index: 0, col_index: 4, field: fields[4],
  value: '7', suggested_value: '8', severity: 'medium', message: 'synthetic warning' };
const sheet = (quantities = ['2', '0', '', '']) => ({ fields, header: fields,
  rows: [['2026/3/2', '昼', '主菜', 'Operator menu', ...quantities]],
  base_evidence_run_id: SELECTED, selected_ocr_result_id: SELECTED,
  template_version_id: 'template-version-A', source: 'workflow_v2_selected_ocr_projection',
  unknown: { zero: 0, nullable: null } });
const columns = fields.map((field, index) => ({ index, source_index: index,
  role: field.startsWith('qty.') ? 'quantity' : field, header: field, name: field,
  ...(field.startsWith('qty.') ? { diet_type: ['regular', 'no_meat', 'no_fish', 'staff'][index - 4], area_id: 'X' } : {}) }));
const resolvedConfig = { fax_template_id: 'template-A', fax_template_ids: ['template-A'],
  facility_template_id: 'facility-template-A', fax_template: { columns } };
function probeFor() {
  return { component: 'OrderWorkflowV2Page', bindings: [
    'workflow', 'workflowRevisionRef', 'effectiveSelectedDocumentId', 'contextFacilityResolution',
    'sheetPayload', 'sheetDirty', 'sheetDirtyRef', 'sheetEditGenerationRef', 'pendingSheetCellEditsRef',
    'preSaveChecks', 'preSaveStatus', 'localAnomalyReview', 'sheetAutoEditResult', 'sheetAutoEditJobInputRef',
    'busy', 'message', 'error', 'visibleStep', 'facilityTemplateMessage', 'facilityTemplateSaving',
    'facilityTemplateColumnDraft', 'expandedCellCopyMode', 'expandedCellCopySaving',
    'confirmContext', 'runOcr', 'selectOcr', 'deleteOcr', 'generateSheetFromSelectedOcr',
    'proposeSheetAutoEdit', 'dismissSingleAnomalyWarning', 'saveSheet', 'runBagging',
    'runAnomalyReview', 'showSheetReview', 'finalConfirm', 'saveFacilityTemplateColumns',
    'updateExpandedCellCopyMode', 'refreshAll', 'updateSheetCell', 'setSelectedDocumentId',
    'setBusy', 'setMessage', 'setError', 'setVisibleStep', 'editorVersion',
  ] };
}
function fixture() {
  const t = transport();
  const state = { workflow: { order_id: ORDER, revision: 7, state: 'sheet_saved',
    selected_ocr_result_id: SELECTED, saved_sheet_id: 'saved-A', output_bundle_id: 'output-A',
    facility_id: 'facility-A', week_start: '2026-03-02', week_end: '2026-03-08',
    template_id: 'template-A', template_version_id: 'template-version-A',
    expanded_cell_copy_mode: 'auto', blockers: [], warnings: [],
    menu_amount_resolution: { status: 'resolved', affected_lines: [] } }, saved: sheet() };
  const workflow = (id = ORDER) => ({ ...clone(state.workflow), order_id: id });
  const command = (patch = {}, extra = {}) => {
    state.workflow = { ...state.workflow, ...patch, revision: state.workflow.revision + 1 };
    return { data: { workflow: workflow(), ...clone(extra) } };
  };
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic-master',
    facilities: [{ id: 'facility-A', name: 'Synthetic authored facility' }] } }));
  t.on('get', '/facilities/facility-A', () => ({ data: { resolved_config: clone(resolvedConfig) } }));
  for (const id of [ORDER, OTHER]) {
    const root = `/orders/${id}/workflow-v2`;
    t.on('get', root, () => ({ data: workflow(id) }));
    t.on('get', `${root}/ocr-results`, () => ({ data: { results: [SELECTED, NEXT].map(ocr_result_id => ({
      ocr_result_id, status: 'completed', selected: ocr_result_id === state.workflow.selected_ocr_result_id,
    })) } }));
    t.on('get', `${root}/inspection`, () => ({ data: { workflow: workflow(id),
      pre_save_checks: clone(checks), pre_save_status: clone(status),
      anomaly_review: { warnings: [clone(warning)] },
      saved_sheet: state.saved ? { saved_sheet_id: 'saved-A',
        source_ocr_result_id: state.workflow.selected_ocr_result_id, sheet: clone(state.saved) } : null } }));
    t.on('get', `/orders/${id}`, () => ({ data: { id,
      menu_amount_resolution: { status: 'resolved', affected_lines: [] },
      current_version: { document_id: PDF }, versions: [
        { document_id: PDF, version_no: 1, is_current: true },
        { document_id: 'pdf-B', version_no: 2, is_current: false },
      ] } }));
    t.on('get', `/orders/${id}/week-options`, () => ({ data: { options: [{
      week_id: '2026-03@2026-03-02~2026-03-08', label: '2026-03-02 / 2026-03-08',
      date_from: '2026-03-02', date_to: '2026-03-08', selected: true,
    }] } }));
    t.on('get', `/orders/${id}/document`, () => ({ data: new Blob(['synthetic transport bytes, NOT PDF proof']) }));
  }
  const source = () => ({ data: { order_id: ORDER, selected_ocr_result_id: SELECTED,
    sheet: sheet(RAW), target_cell_map: [], assignment_summary: {}, blockers: [], warnings: [],
    source: 'workflow_v2_selected_ocr_projection' } });
  t.on('get', `${BASE}/sheet-source`, source);
  t.on('post', `${BASE}/context`, () => command());
  t.on('post', `${BASE}/ocr-runs`, () => command());
  t.on('post', `${BASE}/ocr-results/${NEXT}/select`, () => {
    state.saved = null;
    return command({ selected_ocr_result_id: NEXT, saved_sheet_id: null });
  });
  t.on('delete', `${BASE}/ocr-results/${NEXT}`, () => command());
  t.on('put', `${BASE}/sheet`, call => { state.saved = clone(call.body.sheet); return command(); });
  t.on('post', `${BASE}/bagging`, () => command());
  t.on('post', `${BASE}/confirm`, () => command({ state: 'confirmed' }));
  for (const path of ['anomaly-review', 'anomaly-review/dismiss', 'review-confirm']) {
    t.on('post', `${BASE}/sheet/${path}`, () => command({}, {
      anomaly_review: { warnings: [] }, pre_save_checks: checks, pre_save_status: status,
    }));
  }
  t.on('post', `${BASE}/sheet/auto-edit`, () => command({}, { job: { job_id: JOB,
    order_id: ORDER, status: 'running', workflow_completion_revision: state.workflow.revision + 1 } }));
  t.on('put', `${BASE}/facility-template-columns`, () => {
    state.saved = null;
    return command({ selected_ocr_result_id: null, saved_sheet_id: null }, {
      resolved_config: resolvedConfig, ocr_results_cleared: 2,
    });
  });
  t.on('put', `${BASE}/expanded-cell-copy-mode`, call => {
    state.saved = null;
    const patch = { expanded_cell_copy_mode: call.body.mode, saved_sheet_id: null };
    return command(patch, { expanded_cell_copy_mode: patch.expanded_cell_copy_mode,
      selected_ocr_result_id: state.workflow.selected_ocr_result_id });
  });
  return { t, state, workflow, command, source };
}
async function setup() {
  const f = fixture(), probe = probeFor();
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', {
    api: f.t.api, probe, route: `/orders/${ORDER}/workflow-v2`,
  });
  try {
    await h.waitFor(() => probe.current?.workflow?.revision === 7
      && probe.current.effectiveSelectedDocumentId === PDF
      && probe.current.contextFacilityResolution.status === 'ready'
      && probe.current.sheetPayload && probe.current.facilityTemplateColumnDraft.length === fields.length,
    'transport-derived workflow, sheet, selected PDF and authored facility columns');
    // View navigation only; no ownership/CAS/validation state is injected.
    await h.settle(() => probe.current.setVisibleStep(3));
    return { ...f, h, probe };
  } catch (error) { await h.close(); throw error; }
}
async function start(h, action) {
  let promise;
  await h.settle(() => { promise = action(); assert.ok(promise?.then, 'actual async callback entered');
    promise.then(() => {}, () => {}); });
  return { promise };
}
const calls = (f, method, path) => f.t.calls.filter(c => c.method === method && c.url === path);
function snapshot(f) {
  const p = f.probe.current;
  return { sheet: p.sheetPayload, workflow: p.workflow, revision: clone(p.workflowRevisionRef.current),
    checks: p.preSaveChecks, status: p.preSaveStatus, ai: p.sheetAutoEditResult,
    anomaly: p.localAnomalyReview, busy: p.busy, message: p.message, error: p.error,
    step: p.visibleStep, route: f.h.nav.router.asPath };
}
function unchanged(f, before) {
  const p = f.probe.current;
  assert.equal(p.sheetPayload, before.sheet); assert.equal(p.workflow, before.workflow);
  assert.deepEqual(clone(p.workflowRevisionRef.current), before.revision);
  assert.equal(p.preSaveChecks, before.checks); assert.equal(p.preSaveStatus, before.status);
  assert.equal(p.sheetAutoEditResult, before.ai); assert.equal(p.localAnomalyReview, before.anomaly);
  assert.equal(p.message, before.message); assert.equal(p.error, before.error);
  assert.equal(p.visibleStep, before.step); assert.equal(f.h.nav.router.asPath, before.route);
  if (before.busy === 'successor-busy') assert.equal(p.busy, before.busy);
  assert.ok(f.h.document.body.textContent.includes(before.message), 'owned feedback remains rendered');
}
async function feedback(f) {
  await f.h.settle(() => { f.probe.current.setMessage('owned successor feedback');
    f.probe.current.setError('owned successor error'); });
}
async function retire(f, boundary) {
  const { h, probe } = f;
  if (boundary === 'auth') await h.session(false);
  else if (boundary === 'session') await h.session(true);
  else if (boundary === 'selected') await h.settle(() => probe.current.selectOcr(NEXT));
  else if (boundary === 'operation') await h.settle(() => probe.current.showSheetReview());
  else if (boundary === 'input') {
    const input = h.document.querySelector('[data-sheet-row="0"][data-sheet-col="4"]');
    await h.change(input, '19');
    assert.equal(probe.current.pendingSheetCellEditsRef.current.get('0:4').value, '19');
  }
  else if (boundary === 'order') {
    await h.settle(() => { h.nav.router.events.emit('routeChangeStart', `/orders/${OTHER}/workflow-v2`);
      h.nav.router.asPath = `/orders/${OTHER}/workflow-v2`; h.nav.router.query.id = OTHER; });
    await h.render();
    await h.waitFor(() => probe.current.workflow?.order_id === OTHER, 'committed successor order');
  } else await h.settle(() => {
    if (boundary === 'route') h.nav.router.events.emit('routeChangeStart', '/orders');
    else if (boundary === 'pdf') probe.current.setSelectedDocumentId('pdf-B');
    else if (boundary === 'busy') probe.current.setBusy('successor-busy');
    else throw new Error(`unknown boundary ${boundary}`);
  });
  await feedback(f);
}
async function settleOld(f, pending, operation, outcome, value) {
  await f.h.settle(() => outcome === 'success' ? pending.resolve(value)
    : pending.reject(new Error('obsolete synthetic transport failure')));
  await operation.promise;
}

test('owned sheet-source exact envelope adopts raw 7/0/?/blank with real rendered cells', async () => {
  const f = await setup();
  try {
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr());
    assert.deepEqual(clone(f.probe.current.sheetPayload.rows[0].slice(4)), RAW);
    assert.deepEqual(clone(f.probe.current.sheetPayload.unknown), { zero: 0, nullable: null });
    assert.equal(f.probe.current.sheetDirty, true); assert.equal(f.probe.current.busy, '');
    const inputs = fields.slice(4).map((_, index) => f.h.document.querySelector(`[data-sheet-row="0"][data-sheet-col="${index + 4}"]`));
    assert.ok(inputs.every(Boolean)); assert.deepEqual(inputs.map(input => input.value), RAW);
    assert.match(f.h.document.body.textContent, /選択OCRからシートを生成しました/);
  } finally { await f.h.close(); }
});
for (const boundary of ['route', 'order', 'pdf', 'auth', 'session', 'selected', 'input', 'operation', 'busy'])
for (const outcome of ['success', 'error']) test(`sheet-source late ${outcome} after independent ${boundary} cannot adopt/feedback/navigate`, async () => {
  const f = await setup(), pending = deferred(); let operation;
  try {
    f.t.on('get', `${BASE}/sheet-source`, () => pending.promise);
    operation = await start(f.h, () => f.probe.current.generateSheetFromSelectedOcr());
    assert.equal(calls(f, 'get', `${BASE}/sheet-source`).length, 1);
    await retire(f, boundary); const before = snapshot(f);
    await settleOld(f, pending, operation, outcome, f.source());
    unchanged(f, before);
    if (boundary === 'input') assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('0:4').value, '19');
  } finally { pending.resolve(f.source()); if (operation) await operation.promise; await f.h.close(); }
});
for (const identity of ['foreign-order', 'missing-order', 'foreign-evidence', 'missing-evidence'])
test(`sheet-source ${identity} visible refusal keeps authored sheet`, async () => {
  const f = await setup();
  try {
    const before = f.probe.current.sheetPayload, response = f.source();
    if (identity === 'foreign-order') response.data.order_id = OTHER;
    if (identity === 'missing-order') delete response.data.order_id;
    if (identity === 'foreign-evidence') response.data.selected_ocr_result_id = NEXT;
    if (identity === 'missing-evidence') delete response.data.selected_ocr_result_id;
    f.t.on('get', `${BASE}/sheet-source`, () => response);
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr());
    assert.equal(f.probe.current.sheetPayload, before); assert.equal(f.probe.current.sheetDirty, false);
    assert.match(f.probe.current.error, /一致しないシート/);
    assert.ok(f.h.document.body.textContent.includes(f.probe.current.error));
  } finally { await f.h.close(); }
});

test('normal sheet save flushes raw inputs, retains pre-save checks, sends exact CAS and completes own refresh', async () => {
  const f = await setup();
  try {
    const originalChecks = f.probe.current.preSaveChecks;
    for (const [index, value] of RAW.entries()) {
      await f.h.change(f.h.document.querySelector(`[data-sheet-row="0"][data-sheet-col="${index + 4}"]`), value);
    }
    assert.equal(f.probe.current.preSaveChecks, originalChecks, 'b8c8d743 edit-surviving checks');
    await f.h.settle(() => f.probe.current.saveSheet());
    const [put] = calls(f, 'put', `${BASE}/sheet`); assert.ok(put);
    assert.deepEqual(clone(put.body.sheet.rows[0].slice(4)), RAW);
    assert.equal(put.body.edited_by, 'operator'); assert.equal(put.config.headers['If-Match'], '"7"');
    assert.equal(f.probe.current.workflow.revision, 8); assert.equal(f.probe.current.sheetDirty, false);
    assert.equal(f.probe.current.visibleStep, 4); assert.equal(f.probe.current.busy, '');
    assert.match(f.h.document.body.textContent, /シートを保存しました/);
  } finally { await f.h.close(); }
});
for (const stage of ['get', 'put']) for (const boundary of ['input', 'selected'])
for (const outcome of ['success', 'error']) test(`save ${stage} late ${outcome} after ${boundary} stops adoption and successor dispatch`, async () => {
  const f = await setup(), pending = deferred(); let operation;
  try {
    const path = stage === 'get' ? BASE : `${BASE}/sheet`;
    f.t.on(stage, path, () => pending.promise);
    operation = await start(f.h, () => f.probe.current.saveSheet());
    assert.ok(calls(f, stage, path).length);
    // A selected-OCR retirement needs an independent successful refresh, not the pending first GET.
    if (stage === 'get') f.t.on('get', BASE, () => ({ data: f.workflow() }));
    await retire(f, boundary); const before = snapshot(f);
    const reads = calls(f, 'get', BASE).length;
    await settleOld(f, pending, operation, outcome, stage === 'get'
      ? { data: { ...f.workflow(), revision: 99, selected_ocr_result_id: SELECTED } }
      : { data: { workflow: { ...f.workflow(), revision: 99, selected_ocr_result_id: SELECTED } } });
    unchanged(f, before);
    assert.equal(calls(f, 'put', `${BASE}/sheet`).length, stage === 'get' ? 0 : 1);
    assert.equal(calls(f, 'get', BASE).length, reads, 'no refresh after retired reply');
  } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await f.h.close(); }
});

const siblings = [
  ['context', 'post', '/context', p => p.confirmContext()],
  ['ocrRun', 'post', '/ocr-runs', p => p.runOcr()],
  ['ocrSelection', 'post', `/ocr-results/${NEXT}/select`, p => p.selectOcr(NEXT)],
  ['ocrDelete', 'delete', `/ocr-results/${NEXT}`, p => p.deleteOcr(NEXT)],
  ['dismiss', 'post', '/sheet/anomaly-review/dismiss', p => p.dismissSingleAnomalyWarning(warning)],
  ['anomaly', 'post', '/sheet/anomaly-review', p => p.runAnomalyReview()],
  ['review', 'post', '/sheet/review-confirm', p => p.showSheetReview()],
  ['bagging', 'post', '/bagging', p => p.runBagging()],
  ['confirm', 'post', '/confirm', p => p.finalConfirm()],
];
for (const [name, method, suffix, invoke] of siblings) {
  test(`${name} normal real callback adopts own CAS command${name === 'confirm' ? ' and navigates' : ''}`, async () => {
    const f = await setup();
    try {
      await f.h.settle(() => invoke(f.probe.current));
      const [request] = calls(f, method, BASE + suffix); assert.ok(request, 'actual command dispatched');
      assert.equal((method === 'delete' ? request.body : request.config).headers['If-Match'], '"7"');
      assert.equal(f.probe.current.workflow.revision, 8);
      if (name === 'confirm') {
        assert.deepEqual(clone(request.body), { confirmed_by: 'operator' });
        assert.equal(f.h.nav.router.asPath, '/orders');
      } else assert.equal(f.probe.current.busy, '');
    } finally { await f.h.close(); }
  });
  for (const outcome of ['success', 'error']) test(`${name} late ${outcome} after PDF retirement cannot adopt checks/status/revision/navigation`, async () => {
    const f = await setup(), pending = deferred(); let operation;
    try {
      f.t.on(method, BASE + suffix, () => pending.promise);
      operation = await start(f.h, () => invoke(f.probe.current));
      assert.equal(calls(f, method, BASE + suffix).length, 1);
      await retire(f, 'pdf'); const before = snapshot(f), reads = calls(f, 'get', BASE).length;
      await settleOld(f, pending, operation, outcome, { data: { workflow: { ...f.workflow(), revision: 99 },
        pre_save_checks: {}, pre_save_status: {}, anomaly_review: { warnings: [] } } });
      unchanged(f, before); assert.equal(calls(f, 'get', BASE).length, reads);
    } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await f.h.close(); }
  });
}

const settings = [
  ['columns', '/facility-template-columns', p => p.saveFacilityTemplateColumns()],
  ['copy', '/expanded-cell-copy-mode', p => p.updateExpandedCellCopyMode('disabled')],
];
for (const boundary of ['route', 'auth', 'input', 'operation']) for (const outcome of ['success', 'error'])
test(`finalConfirm late ${outcome} after ${boundary} cannot navigate or adopt confirmation`, async () => {
  const f = await setup(), pending = deferred(); let operation;
  try {
    f.t.on('post', `${BASE}/confirm`, () => pending.promise);
    operation = await start(f.h, () => f.probe.current.finalConfirm());
    assert.equal(calls(f, 'post', `${BASE}/confirm`).length, 1);
    await retire(f, boundary); const before = snapshot(f);
    await settleOld(f, pending, operation, outcome, { data: { workflow: {
      ...f.workflow(), state: 'confirmed', revision: 99,
    } } });
    unchanged(f, before);
    assert.equal(f.h.nav.router.asPath, `/orders/${ORDER}/workflow-v2`);
  } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await f.h.close(); }
});
for (const [name, suffix, invoke] of settings) {
  test(`${name} normal own revision/discard/refresh does not retire itself`, async () => {
    const f = await setup();
    try {
      await f.h.settle(() => invoke(f.probe.current));
      const [request] = calls(f, 'put', BASE + suffix); assert.ok(request);
      assert.equal(request.config.headers['If-Match'], '"7"');
      assert.equal(f.probe.current.workflow.revision, 8); assert.equal(f.probe.current.sheetPayload, null);
      if (name === 'columns') {
        assert.equal(request.body.columns.length, fields.length);
        assert.match(f.probe.current.facilityTemplateMessage, /保存しました/);
        assert.equal(f.probe.current.facilityTemplateSaving, false);
      } else {
        assert.deepEqual(clone(request.body), { mode: 'disabled' });
        assert.equal(f.probe.current.expandedCellCopyMode, 'disabled');
        assert.match(f.probe.current.message, /設定を変更しました/);
        assert.equal(f.probe.current.expandedCellCopySaving, false);
      }
    } finally { await f.h.close(); }
  });
  for (const background of ['selected', 'revision']) for (const outcome of ['success', 'error'])
  test(`${name} late ${outcome} cannot internally adopt after background ${background} refresh`, async () => {
    const f = await setup(), pending = deferred(); let operation;
    try {
      f.t.on('put', BASE + suffix, () => pending.promise);
      operation = await start(f.h, () => invoke(f.probe.current));
      assert.equal(calls(f, 'put', BASE + suffix).length, 1);
      f.state.workflow.revision = 20;
      if (background === 'selected') f.state.workflow.selected_ocr_result_id = NEXT;
      await f.h.settle(() => f.probe.current.refreshAll());
      assert.equal(f.probe.current.workflow.revision, 20);
      await feedback(f); const before = snapshot(f);
      const columnDraft = f.probe.current.facilityTemplateColumnDraft;
      const mode = f.probe.current.expandedCellCopyMode;
      await settleOld(f, pending, operation, outcome, { data: { workflow: { ...f.workflow(), revision: 21 },
        resolved_config: clone(resolvedConfig), expanded_cell_copy_mode: 'disabled', selected_ocr_result_id: SELECTED } });
      unchanged(f, before);
      assert.equal(f.probe.current.facilityTemplateColumnDraft, columnDraft);
      assert.equal(f.probe.current.expandedCellCopyMode, mode);
    } finally { pending.resolve({ data: {} }); if (operation) await operation.promise; await f.h.close(); }
  });
}

// Control ONLY the production poll delays. The real scheduled async callback is
// retained; React/helper timers, intervals and all handler/ownership code remain real.
function pollClock(h) {
  const win = h.document.defaultView, descriptors = new Map(), queue = new Map(); let next = 1000000;
  const timeout = win.setTimeout.bind(win), clear = win.clearTimeout.bind(win);
  for (const name of ['setTimeout', 'clearTimeout']) descriptors.set(name, Object.getOwnPropertyDescriptor(win, name));
  Object.defineProperty(win, 'setTimeout', { configurable: true, writable: true, value(fn, delay, ...args) {
    if (delay !== 1200 && delay !== 3000) return timeout(fn, delay, ...args);
    const id = next++; queue.set(id, { fn, delay, args }); return id;
  } });
  Object.defineProperty(win, 'clearTimeout', { configurable: true, writable: true, value(id) {
    if (!queue.delete(id)) clear(id);
  } });
  return { queue,
    fire() { const [id, timer] = queue.entries().next().value || []; assert.ok(timer, 'actual poll scheduled');
      queue.delete(id); return timer.fn(...timer.args); },
    restore() { queue.clear(); for (const [name, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(win, name, descriptor); else delete win[name];
    } },
  };
}
const jobReply = (revision = 9, overrides = {}) => ({ data: { job: {
  job_id: JOB, order_id: ORDER, status: 'done', workflow_completion_revision: 8,
  workflow_result_revision: revision, ...overrides,
}, result: { status: 'ok', patches: [{ row_index: 0, col_index: 4,
  current_value: '2', suggested_value: '7' }], llm: { status: 'ok' } } } });
async function startJob(f, clock, dirty = false) {
  if (dirty) await f.h.change(f.h.document.querySelector('[data-sheet-row="0"][data-sheet-col="4"]'), '7');
  await f.h.settle(() => f.probe.current.proposeSheetAutoEdit());
  assert.equal(calls(f, 'post', `${BASE}/sheet/auto-edit`).length, 1);
  assert.equal(f.probe.current.busy, '', 'intentional runAction busy clearance completed');
  assert.equal(f.probe.current.workflow.revision, 8);
  assert.equal(f.probe.current.editorVersion('sheet').baseRevision, dirty ? 8 : null);
  assert.equal(clock.queue.size, 1); assert.equal([...clock.queue.values()][0].delay, 1200);
}
for (const dirty of [false, true]) test(`AI ${dirty ? 'dirty' : 'clean'} start survives own helper completion and busy clearance`, async () => {
  const f = await setup(), clock = pollClock(f.h);
  try {
    f.t.on('get', `${BASE}/sheet/auto-edit/${JOB}`, () => jobReply());
    await startJob(f, clock, dirty);
    const beforeRows = clone(f.probe.current.sheetPayload.rows);
    await f.h.settle(() => clock.fire());
    assert.equal(calls(f, 'get', `${BASE}/sheet/auto-edit/${JOB}`).length, 1);
    assert.equal(f.probe.current.workflowRevisionRef.current.revision, 9);
    assert.equal(f.probe.current.sheetAutoEditResult.patches[0].suggested_value, '7');
    assert.deepEqual(clone(f.probe.current.sheetPayload.rows), beforeRows, 'candidate is not silently applied');
    assert.match(f.h.document.body.textContent, /AI自動編集の候補を作成しました/);
    assert.equal(clock.queue.size, 0);
  } finally { clock.restore(); await f.h.close(); }
});
for (const boundary of ['route', 'auth', 'session', 'pdf', 'input', 'selected', 'operation'])
for (const stage of ['timer', 'retry', 'success', 'error']) test(`AI ${stage} after ${boundary} retirement cannot request/adopt/retry/clobber`, async () => {
  const f = await setup(), clock = pollClock(f.h), pending = deferred(); let operation;
  try {
    f.t.on('get', `${BASE}/sheet/auto-edit/${JOB}`, () => pending.promise);
    await startJob(f, clock);
    if (stage === 'retry') {
      f.t.on('get', `${BASE}/sheet/auto-edit/${JOB}`, () => Promise.reject(new Error('retryable synthetic failure')));
      await f.h.settle(() => clock.fire());
      assert.equal(calls(f, 'get', `${BASE}/sheet/auto-edit/${JOB}`).length, 1);
      assert.equal([...clock.queue.values()][0].delay, 3000);
    } else if (stage !== 'timer') {
      operation = await start(f.h, () => clock.fire());
      assert.equal(calls(f, 'get', `${BASE}/sheet/auto-edit/${JOB}`).length, 1);
    }
    await retire(f, boundary); const before = snapshot(f);
    if (stage === 'timer' || stage === 'retry') await f.h.settle(() => clock.fire());
    else await settleOld(f, pending, operation, stage, jobReply());
    unchanged(f, before); assert.equal(clock.queue.size, 0, 'no retired retry');
    assert.equal(calls(f, 'get', `${BASE}/sheet/auto-edit/${JOB}`).length, stage === 'timer' ? 0 : 1);
  } finally { pending.resolve(jobReply()); if (operation) await operation.promise; clock.restore(); await f.h.close(); }
});
for (const invalid of ['foreign-job', 'missing-job', 'diagnostic', 'unsafe-diagnostic', 'older-revision'])
test(`AI ${invalid} stops visibly without revision regression or retry or unsafe diagnostics`, async () => {
  const f = await setup(), clock = pollClock(f.h);
  const secret = 'SECRET_INTERNAL_TRACE_CREDENTIAL';
  try {
    const response = jobReply(invalid === 'older-revision' ? 7 : 9);
    if (invalid === 'foreign-job') response.data.job.job_id = 'foreign-job';
    if (invalid === 'missing-job') delete response.data.job;
    if (invalid === 'diagnostic' || invalid === 'unsafe-diagnostic') {
      response.data.diagnostic = { error: invalid === 'diagnostic' ? 'workflow_revision_conflict' : secret };
      delete response.data.result;
    }
    f.t.on('get', `${BASE}/sheet/auto-edit/${JOB}`, () => response);
    await startJob(f, clock);
    const raw = clone(f.probe.current.sheetPayload.rows);
    await f.h.settle(() => clock.fire());
    assert.equal(f.probe.current.workflowRevisionRef.current.revision, 8);
    assert.equal(f.probe.current.sheetAutoEditJobInputRef.current, null);
    assert.deepEqual(clone(f.probe.current.sheetPayload.rows), raw);
    assert.ok(f.probe.current.error); assert.ok(f.h.document.body.textContent.includes(f.probe.current.error));
    assert.ok(!f.probe.current.error.includes(secret)); assert.ok(!f.h.document.body.textContent.includes(secret));
    assert.equal(clock.queue.size, 0); assert.equal(calls(f, 'get', `${BASE}/sheet/auto-edit/${JOB}`).length, 1);
  } finally { clock.restore(); await f.h.close(); }
});
test('AI current transport failure retains original three retry limit and readable error', async () => {
  const f = await setup(), clock = pollClock(f.h);
  try {
    f.t.on('get', `${BASE}/sheet/auto-edit/${JOB}`, () => Promise.reject(new Error('synthetic poll unavailable')));
    await startJob(f, clock);
    for (let index = 0; index < 4; index++) {
      await f.h.settle(() => clock.fire());
      assert.equal(calls(f, 'get', `${BASE}/sheet/auto-edit/${JOB}`).length, index + 1);
      assert.equal(clock.queue.size, index < 3 ? 1 : 0);
      if (index < 3) assert.equal([...clock.queue.values()][0].delay, 3000);
    }
    assert.equal(f.probe.current.sheetAutoEditResult.status, 'failed');
    assert.ok(f.probe.current.error); assert.equal(f.probe.current.workflowRevisionRef.current.revision, 8);
  } finally { clock.restore(); await f.h.close(); }
});
