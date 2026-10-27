const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Mounted production page, hooks and NavigationBoundary; synthetic HTTP only.
// This is not PDF/OCR, backend CAS, browser or live operator proof.
const ORDER = 'synthetic-order', ROOT = `/orders/${ORDER}/workflow-v2`, PDF = 'pdf-A', OCR = 'ocr-A';
const clone = value => JSON.parse(JSON.stringify(value));
const fields = ['date', 'daypart', 'category', 'menu_name', 'qty.regular_x', 'qty.no_meat_x'];
const sheet = value => ({ fields, header: fields,
  rows: [['2026/3/2', '昼', '主菜', 'Component menu', value, '5']],
  base_evidence_run_id: OCR, selected_ocr_result_id: OCR, template_version_id: 'template-A',
  source: 'workflow_v2_selected_ocr_projection' });
const status = { anomaly_review_confirmed: true, sheet_review_confirmed: true, ready: true };
const checks = { anomaly_review: { confirmed: true }, sheet_review: { confirmed: true } };
async function setup() {
  const t = transport();
  const state = { workflow: { order_id: ORDER, revision: 7, state: 'sheet_saved',
    selected_ocr_result_id: OCR, saved_sheet_id: 'saved-A', facility_id: 'facility-A',
    week_start: '2026-03-02', week_end: '2026-03-08', template_id: 'template-A', template_version_id: 'template-A',
    expanded_cell_copy_mode: 'auto', blockers: [], warnings: [], menu_amount_resolution: { status: 'resolved', affected_lines: [] } }, saved: sheet('2') };
  const columns = fields.map((field, index) => ({ index, source_index: index, role: field.startsWith('qty.') ? 'quantity' : field,
    name: field, header: field, ...(field.startsWith('qty.') ? { diet_type: index === 4 ? 'regular' : 'no_meat', area_id: 'X' } : {}) }));
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'master-A', facilities: [{ id: 'facility-A', name: 'Component facility' }] } }));
  t.on('get', ROOT, () => ({ data: clone(state.workflow) }));
  t.on('get', `${ROOT}/facility-config`, () => ({ data: { source: 'canonical_json', order_id: ORDER,
    facility_id: 'facility-A', week_start: '2026-03-02', week_end: '2026-03-08', template_version_id: 'template-A',
    config: { fax_template_id: 'template-A', fax_template_ids: ['template-A'], facility_template_id: 'facility-template-A', fax_template: { columns } } } }));
  t.onWorkflowInspection(`${ROOT}/display?scope=summary`, () => ({ data: {
    workflow: clone(state.workflow),
    ocr_candidates: require('./helpers/ocr-candidates.cjs')(ORDER, [{ ocr_result_id: state.workflow.selected_ocr_result_id || OCR, selected: true, status: 'completed' }], state.workflow),
    saved_sheet: state.saved ? { sheet: clone(state.saved) } : null, pre_save_status: status, pre_save_checks: checks,
    anomaly_review: { warnings: [{ row_index: 0, col_index: 4, suggested_value: '12', message: 'component suggestion' }] },
  } }));
  t.on('get', `/orders/${ORDER}`, () => ({ data: { id: ORDER, menu_amount_resolution: { status: 'resolved', affected_lines: [] },
    current_version: { document_id: PDF }, versions: [{ document_id: PDF, is_current: true, version_no: 1 }, { document_id: 'pdf-B', version_no: 2 }] } }));
  t.on('get', `/orders/${ORDER}/document`, () => ({ data: new Blob(['not a real PDF']) }));
  t.on('get', `/orders/${ORDER}/week-options`, () => ({ data: { options: [{ week_id: '2026-03@2026-03-02~2026-03-08',
    label: '2026-03-02 / 2026-03-08', date_from: '2026-03-02', date_to: '2026-03-08', selected: true }] } }));
  const source = () => ({ data: { order_id: ORDER, selected_ocr_result_id: OCR, sheet: sheet('7') } });
  t.on('get', `${ROOT}/sheet-source`, source);
  t.on('put', `${ROOT}/sheet`, call => { state.saved = clone(call.body.sheet); state.workflow.revision++;
    return { data: { workflow: clone(state.workflow) } }; });
  const probe = { component: 'OrderWorkflowV2Page', bindings: [
    'workflow', 'workflowRevisionRef', 'sheetPayload', 'sheetPayloadRef', 'sheetJson', 'sheetDirty', 'sheetDirtyRef',
    'pendingSheetCellEditsRef', 'sheetEditGenerationRef', 'workflowEditorVersionsRef', 'effectiveSelectedDocumentId',
    'contextFacilityResolution', 'visibleStep', 'busy', 'error', 'navigation', 'generateSheetFromSelectedOcr',
    'refreshAll', 'updateSheetCell', 'getSheetPayloadForAction', 'flushPendingSheetCellEdits', 'saveSheet',
    'runOcr', 'selectOcr', 'deleteOcr', 'confirmContext', 'saveFacilityTemplateColumns', 'finalConfirm', 'setVisibleStep',
    'beginWorkflowEditorEdit', 'hasUnsavedWorkflowInput', 'applyWeekValue', 'contextForm',
    'fillQuantityColumn', 'setColumnFillTarget', 'setColumnFillValue', 'swapQuantityColumns', 'setSwapLeftColumn', 'setSwapRightColumn',
    'applyVisibleOcrSuggestions', 'applySheetAutoEditPatches', 'applySingleSheetAutoEditPatch',
    'applyAnomalyCorrections', 'applySingleAnomalyCorrection', 'proposeSheetAutoEdit', 'sheetAutoEditResult',
    'updateExpandedCellCopyMode', 'loadQuadReview', 'loadAxisReview', 'saveQuadAndRerun', 'saveAxisAndRerun', 'manualQuadPoints',
  ] };
  let pop;
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: ROOT,
    routerState: { beforePopState(fn) { pop = fn; } } });
  try {
    await h.waitFor(() => probe.current?.workflow?.revision === 7
      && probe.current.effectiveSelectedDocumentId === PDF && probe.current.contextFacilityResolution.status === 'ready');
    await h.settle(() => probe.current.setVisibleStep(3));
    await h.waitFor(() => probe.current.sheetPayload);
    return { h, t, state, probe, source, pop: (...args) => pop(...args) };
  } catch (error) { await h.close(); throw error; }
}
const calls = (f, method, path) => f.t.calls.filter(call => call.method === method && call.url === path);
function snapshot(f) {
  const p = f.probe.current;
  return clone({ json: p.sheetJson, sheet: p.sheetPayload, pending: [...p.pendingSheetCellEditsRef.current],
    dirty: p.sheetDirtyRef.current, editors: p.workflowEditorVersionsRef.current,
    revision: p.workflowRevisionRef.current, generation: p.sheetEditGenerationRef.current, order: p.workflow.order_id });
}
async function pendingCell(f, value = '19') {
  const cell = f.h.document.querySelector('input[data-sheet-row="0"][data-sheet-col="4"]');
  assert.ok(cell);
  await f.h.change(cell, value);
  assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('0:4').value, value);
  assert.equal(f.probe.current.sheetDirtyRef.current, true, 'typing pins the dirty editor before blur/flush');
}
function confirmation(f, accepted) {
  let count = 0;
  window.confirm = () => { count++; return accepted; };
  return () => count;
}
test('pending actual DOM cell before blur cancels regeneration without losing exact refs/revision or requesting source', async () => {
  const f = await setup();
  try {
    await pendingCell(f, '0');
    const count = confirmation(f, false), before = snapshot(f), reads = calls(f, 'get', `${ROOT}/sheet-source`).length;
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr());
    assert.equal(count(), 1); assert.deepEqual(snapshot(f), before);
    assert.equal(calls(f, 'get', `${ROOT}/sheet-source`).length, reads);
    assert.equal(f.t.calls.filter(call => call.method !== 'get').length, 0);
  } finally { await f.h.close(); }
});

test('explicit regeneration accepts only selected source and retires precisely the old pending cells', async () => {
  const f = await setup();
  try {
    await pendingCell(f); const count = confirmation(f, true);
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr());
    assert.equal(count(), 1); assert.equal(f.probe.current.sheetPayload.rows[0][4], '7');
    assert.equal(f.probe.current.pendingSheetCellEditsRef.current.size, 0);
    assert.equal(f.probe.current.sheetDirtyRef.current, true);
    assert.equal(f.probe.current.workflowRevisionRef.current.revision, 7);
  } finally { await f.h.close(); }
});

for (const outcome of ['success', 'error']) test(`edit while regeneration awaits fences old ${outcome} and retains exact newer input`, async () => {
  const f = await setup(), wait = deferred(); let operation;
  try {
    f.t.on('get', `${ROOT}/sheet-source`, () => wait.promise);
    // No dirty input at click; the later edit must still block adoption.
    await f.h.settle(() => { operation = f.probe.current.generateSheetFromSelectedOcr(); });
    await pendingCell(f, '23'); const before = snapshot(f);
    await f.h.settle(() => outcome === 'success' ? wait.resolve(f.source()) : wait.reject(new Error('component network')));
    await operation; assert.deepEqual(snapshot(f), before); assert.equal(f.probe.current.busy, '');
  } finally { wait.resolve(f.source()); if (operation) await operation; await f.h.close(); }
});

for (const wrong of ['order', 'selected', 'missing-sheet']) test(`regeneration rejects ${wrong} receipt without clearing pending input`, async () => {
  const f = await setup();
  try {
    await pendingCell(f); confirmation(f, true); const before = snapshot(f);
    const response = f.source();
    if (wrong === 'order') response.data.order_id = 'other';
    if (wrong === 'selected') response.data.selected_ocr_result_id = 'other';
    if (wrong === 'missing-sheet') delete response.data.sheet;
    f.t.on('get', `${ROOT}/sheet-source`, () => response);
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr());
    assert.deepEqual(snapshot(f), before); assert.ok(f.probe.current.error);
  } finally { await f.h.close(); }
});

test('invalid JSON survives refresh/regeneration cancel and cannot fall back to prior saved sheet', async () => {
  const f = await setup();
  try {
    const input = f.h.document.querySelector('.json-details textarea'); assert.ok(input);
    await f.h.settle(() => {
      Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(input, '{operator unfinished');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    assert.equal(f.probe.current.sheetJson, '{operator unfinished');
    assert.equal(f.probe.current.sheetPayloadRef.current, null);
    const before = snapshot(f), count = confirmation(f, false);
    await f.h.settle(() => f.probe.current.refreshAll()); assert.deepEqual(snapshot(f), before);
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr()); assert.equal(count(), 1);
    assert.deepEqual(snapshot(f), before); assert.equal(f.probe.current.getSheetPayloadForAction(), null);
    assert.equal(f.probe.current.navigation.allow('/orders'), false);
  } finally { await f.h.close(); }
});

test('STEP switches retain pending cell/dirty editor inputs and do not ask for discard', async () => {
  const f = await setup();
  try {
    await pendingCell(f); const count = confirmation(f, false), before = snapshot(f);
    for (const step of [2, 1, 4, 3]) await f.h.settle(() => f.probe.current.setVisibleStep(step));
    assert.equal(count(), 0); assert.deepEqual(snapshot(f), before);
    assert.equal(f.h.document.querySelector('input[data-sheet-row="0"][data-sheet-col="4"]').value, '19');
  } finally { await f.h.close(); }
});

for (const action of ['runOcr', 'selectOcr', 'deleteOcr', 'confirmContext', 'saveFacilityTemplateColumns', 'finalConfirm'])
test(`source sibling ${action} cancel performs no destructive request and keeps input/CAS`, async () => {
  const f = await setup();
  try {
    await pendingCell(f); const before = snapshot(f), count = confirmation(f, false);
    await f.h.settle(() => f.probe.current[action]('ocr-B'));
    assert.equal(count(), 1); assert.deepEqual(snapshot(f), before);
    assert.equal(f.t.calls.filter(call => call.method !== 'get').length, 0);
  } finally { await f.h.close(); }
});

test('live editor callback and native beforeunload detect edits before a rendered sheetDirty update; logout is unblocked', async () => {
  const f = await setup();
  try {
    await pendingCell(f); const count = confirmation(f, false);
    assert.equal(f.probe.current.navigation.allow('/orders'), false); assert.equal(count(), 1);
    const event = new window.Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event); assert.equal(event.defaultPrevented, true);
    await f.h.session(false);
    assert.equal(f.probe.current.navigation.allow('/login'), true); assert.equal(count(), 1);
    const logout = new window.Event('beforeunload', { cancelable: true }); window.dispatchEvent(logout);
    assert.equal(logout.defaultPrevented, false);
    await f.h.unmount();
  } finally { await f.h.close(); }
});

test('header and page links call the same guard exactly once; cancel preserves current order', async () => {
  const f = await setup();
  try {
    await pendingCell(f); const count = confirmation(f, false), before = snapshot(f);
    const links = [...f.h.document.querySelectorAll('a')];
    for (const text of ['注文一覧へ戻る', '確認専用ページ', 'ダッシュボード']) {
      const link = links.find(node => node.textContent.trim() === text); assert.ok(link, text);
      const previous = count();
      await f.h.settle(() => link.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })));
      assert.equal(count(), previous + 1);
      assert.deepEqual(snapshot(f), before);
    }
  } finally { await f.h.close(); }
});

test('existing NavigationBoundary back/forward cancel restores exact owned history and preserves dirty refs', async () => {
  const f = await setup();
  try {
    await pendingCell(f); const count = confirmation(f, false), before = snapshot(f);
    const history = window.history, replace = history.replaceState;
    const initial = clone(history.state);
    history.pushState({ __N: true }, '', ROOT + '?view=3');
    const current = clone(history.state), goes = [];
    const originalGo = history.go; history.go = delta => goes.push(delta);
    try {
      // A real pop changes history.state before Next calls beforePopState.
      // Restore the test entry without invoking the wrapped replaceState.
      const nativeReplace = Object.getPrototypeOf(history).replaceState;
      nativeReplace.call(history, initial, '', ROOT);
      await f.h.settle(() => assert.equal(f.pop({ as: '/orders' }), false));
      assert.deepEqual(goes, [1]); assert.equal(count(), 1);
      nativeReplace.call(history, current, '', ROOT + '?view=3');
      await f.h.settle(() => assert.equal(f.pop({ as: ROOT + '?view=3' }), false));
      assert.deepEqual(snapshot(f), before);
      history.pushState({ __N: true }, '', ROOT + '?view=4');
      const later = clone(history.state);
      nativeReplace.call(history, current, '', ROOT + '?view=3');
      // Move within the owned entries, accepting this transition first.
      window.confirm = () => true;
      await f.h.settle(() => assert.equal(f.pop({ as: ROOT + '?view=3' }), true));
      const forwardCount = confirmation(f, false);
      nativeReplace.call(history, later, '', ROOT + '?view=4');
      await f.h.settle(() => assert.equal(f.pop({ as: '/orders' }), false));
      assert.deepEqual(goes, [1, -1]); assert.equal(forwardCount(), 1); assert.deepEqual(snapshot(f), before);
    } finally { history.go = originalGo; history.replaceState = replace; }
  } finally { await f.h.close(); }
});

for (const order of ['next-first', 'capture-first']) test(`owned pop cancellation is consumed once with ${order} listeners`, async () => {
  const f = await setup();
  try {
    await pendingCell(f);
    const count = confirmation(f, false), before = snapshot(f);
    const history = window.history, initial = clone(history.state);
    history.pushState({ __N: true, as: ROOT + '?view=3' }, '', ROOT + '?view=3');
    const current = clone(history.state), goes = [], go = history.go;
    const replace = Object.getPrototypeOf(history).replaceState;
    history.go = delta => goes.push(delta);
    const dispatch = (state, url) => f.h.settle(() => {
      replace.call(history, state, '', url);
      const event = new window.PopStateEvent('popstate', { state: history.state });
      if (order === 'next-first') assert.equal(f.pop(event.state), false);
      window.dispatchEvent(event);
      if (order === 'capture-first') assert.equal(f.pop(event.state), false);
    });
    try {
      for (let index = 0; index < 2; index++) {
        await dispatch({ ...initial, as: '/orders' }, ROOT);
        assert.equal(count(), index + 1);
        assert.deepEqual(goes, Array(index + 1).fill(1));
        await dispatch(current, ROOT + '?view=3');
        assert.deepEqual(snapshot(f), before);
      }
    } finally { history.go = go; }
  } finally { await f.h.close(); }
});

test('normal own save clears live guard only after successful revision adoption and preserves explicit zero', async () => {
  const f = await setup();
  try {
    await pendingCell(f, '0'); confirmation(f, false);
    await f.h.settle(() => f.probe.current.saveSheet());
    const requests = calls(f, 'put', `${ROOT}/sheet`);
    assert.equal(requests.length, 1); assert.equal(requests[0].config.headers['If-Match'], '"7"');
    assert.equal(requests[0].body.sheet.rows[0][4], '0');
    assert.equal(f.probe.current.workflowRevisionRef.current.revision, 8);
    assert.equal(f.probe.current.sheetDirtyRef.current, false);
    assert.equal(f.probe.current.navigation.allow('/orders'), true);
    const event = new window.Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false);
  } finally { await f.h.close(); }
});

test('missing canonical ref and out-of-bounds pending cell stop without clearing operator values', async () => {
  const f = await setup();
  try {
    await pendingCell(f); const original = f.probe.current.sheetPayloadRef.current;
    f.probe.current.sheetPayloadRef.current = null;
    assert.throws(() => f.probe.current.flushPendingSheetCellEdits(), /正本/);
    assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('0:4').value, '19');
    f.probe.current.sheetPayloadRef.current = original;
    await f.h.settle(() => f.probe.current.updateSheetCell(999, 4, 'operator'));
    assert.throws(() => f.probe.current.flushPendingSheetCellEdits(), /セル位置/);
    assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('999:4').value, 'operator');
  } finally { await f.h.close(); }
});

test('workflow editor ref remains protected even when sheetDirty is false, and refresh keeps its exact draft', async () => {
  const f = await setup();
  try {
    await f.h.settle(() => f.probe.current.applyWeekValue('2026-04@2026-04-01~2026-04-07'));
    assert.equal(f.probe.current.sheetDirtyRef.current, false);
    assert.equal(f.probe.current.workflowEditorVersionsRef.current.context.dirty, true);
    const context = clone(f.probe.current.contextForm), count = confirmation(f, false), before = snapshot(f);
    assert.equal(f.probe.current.navigation.allow('/orders'), false); assert.equal(count(), 1);
    await f.h.settle(() => f.probe.current.refreshAll());
    assert.deepEqual(clone(f.probe.current.contextForm), context); assert.deepEqual(snapshot(f), before);
    await f.h.settle(() => f.probe.current.generateSheetFromSelectedOcr());
    assert.equal(count(), 2); assert.deepEqual(snapshot(f), before);
    assert.equal(calls(f, 'get', `${ROOT}/sheet-source`).length, 0);
  } finally { await f.h.close(); }
});

test('valid edited JSON is the canonical draft for save, without reverting to saved quantities', async () => {
  const f = await setup();
  try {
    const input = f.h.document.querySelector('.json-details textarea');
    const json = JSON.stringify(sheet('0'));
    await f.h.settle(() => {
      Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(input, json);
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    assert.equal(f.probe.current.sheetPayloadRef.current.rows[0][4], '0');
    confirmation(f, false);
    await f.h.settle(() => f.probe.current.saveSheet());
    assert.equal(calls(f, 'put', `${ROOT}/sheet`)[0].body.sheet.rows[0][4], '0');
    assert.equal(f.probe.current.sheetDirtyRef.current, false);
  } finally { await f.h.close(); }
});

test('accepted OCR source change retires consented old draft only after own response, then adopts new saved source', async () => {
  const f = await setup(), wait = deferred(); let operation;
  try {
    await pendingCell(f); confirmation(f, true); const before = snapshot(f);
    f.t.on('post', `${ROOT}/ocr-results/ocr-B/select`, () => wait.promise);
    await f.h.settle(() => { operation = f.probe.current.selectOcr('ocr-B'); });
    assert.deepEqual(snapshot(f).pending, before.pending);
    f.state.workflow = { ...f.state.workflow, revision: 8, selected_ocr_result_id: 'ocr-B', saved_sheet_id: 'saved-B' };
    f.state.saved = { ...sheet('31'), base_evidence_run_id: 'ocr-B', selected_ocr_result_id: 'ocr-B' };
    await f.h.settle(() => wait.resolve({ data: { workflow: clone(f.state.workflow) } })); await operation;
    assert.equal(f.probe.current.sheetPayload.rows[0][4], '31');
    assert.equal(f.probe.current.sheetPayload.base_evidence_run_id, 'ocr-B');
    assert.equal(f.probe.current.pendingSheetCellEditsRef.current.size, 0);
    assert.equal(f.probe.current.sheetDirtyRef.current, false);
    assert.equal(f.probe.current.navigation.allow('/orders'), true);
  } finally { wait.resolve({ data: { workflow: clone(f.state.workflow) } }); if (operation) await operation; await f.h.close(); }
});

test('ordinary fill is synchronously visible to canonical getSheetPayloadForAction before another render', async () => {
  const f = await setup();
  try {
    await f.h.settle(() => { f.probe.current.setColumnFillTarget('4'); f.probe.current.setColumnFillValue('0'); });
    await f.h.settle(() => {
      f.probe.current.fillQuantityColumn();
      assert.equal(f.probe.current.getSheetPayloadForAction().rows[0][4], '0');
    });
    await f.h.settle(() => f.probe.current.saveSheet());
    assert.equal(calls(f, 'put', `${ROOT}/sheet`)[0].body.sheet.rows[0][4], '0');
  } finally { await f.h.close(); }
});

for (const failure of ['request-error', 'new-edit']) test(`consented source replacement ${failure} preserves old/newer sheet and exact CAS base`, async () => {
  const f = await setup(), wait = deferred(); let operation;
  try {
    await pendingCell(f); confirmation(f, true);
    f.t.on('post', `${ROOT}/ocr-results/ocr-B/select`, () => wait.promise);
    await f.h.settle(() => { operation = f.probe.current.selectOcr('ocr-B'); });
    if (failure === 'new-edit') await f.h.change(f.h.document.querySelector('input[data-sheet-col="4"]'), '27');
    const before = snapshot(f);
    await f.h.settle(() => failure === 'request-error' ? wait.reject(new Error('component unavailable'))
      : wait.resolve({ data: { workflow: { ...f.state.workflow, revision: 8, selected_ocr_result_id: 'ocr-B' } } }));
    await operation;
    // Command-only editor bookkeeping is retained on failure; no sheet source or user input is retired.
    const after = snapshot(f);
    for (const key of ['sheet', 'pending', 'dirty', 'revision', 'generation', 'json', 'editors']) assert.deepEqual(after[key], before[key], key);
  } finally { wait.resolve({ data: {} }); if (operation) await operation; await f.h.close(); }
});

test('PR83 expanded copy setting advances own revision while preserving dirty canonical sheet and pending input', async () => {
  const f = await setup();
  try {
    await pendingCell(f, '0'); const count = confirmation(f, false);
    f.t.on('put', `${ROOT}/expanded-cell-copy-mode`, () => {
      f.state.workflow = { ...f.state.workflow, revision: 8, expanded_cell_copy_mode: 'always' };
      return { data: { workflow: clone(f.state.workflow), expanded_cell_copy_mode: 'always', selected_ocr_result_id: OCR } };
    });
    await f.h.settle(() => f.probe.current.updateExpandedCellCopyMode('always'));
    assert.equal(count(), 0); assert.equal(f.probe.current.sheetPayload.rows[0][4], '0');
    assert.equal(f.probe.current.sheetDirtyRef.current, true);
    assert.equal(f.probe.current.workflowEditorVersionsRef.current.sheet.baseRevision, 8);
    assert.equal(f.probe.current.sheetPayload.base_evidence_run_id, OCR);
    assert.equal(calls(f, 'put', `${ROOT}/expanded-cell-copy-mode`)[0].config.headers['If-Match'], '"7"');
  } finally { await f.h.close(); }
});

for (const kind of ['context', 'template', 'ocrRun', 'ocrDelete']) test(`successful ${kind} source command retires consented draft before fresh source adoption`, async () => {
  const f = await setup();
  try {
    await pendingCell(f); const count = confirmation(f, true);
    const method = kind === 'template' ? 'put' : kind === 'ocrDelete' ? 'delete' : 'post';
    const path = kind === 'template' ? 'facility-template-columns' : kind === 'context' ? 'context'
      : kind === 'ocrRun' ? 'ocr-runs' : 'ocr-results/ocr-A';
    f.t.on(method, `${ROOT}/${path}`, () => {
      f.state.workflow = { ...f.state.workflow, revision: 8, selected_ocr_result_id: null, saved_sheet_id: null };
      f.state.saved = null;
      return { data: { workflow: clone(f.state.workflow), resolved_config: { fax_template: { columns: [] } }, ocr_results_cleared: 1 } };
    });
    await f.h.settle(() => kind === 'context' ? f.probe.current.confirmContext() : kind === 'template'
      ? f.probe.current.saveFacilityTemplateColumns() : kind === 'ocrRun' ? f.probe.current.runOcr() : f.probe.current.deleteOcr(OCR));
    assert.equal(count(), 1); assert.equal(calls(f, method, `${ROOT}/${path}`).length, 1);
    assert.equal(f.probe.current.pendingSheetCellEditsRef.current.size, 0);
    assert.equal(f.probe.current.sheetPayloadRef.current, null);
    assert.equal(f.probe.current.sheetDirtyRef.current, false);
    assert.equal(f.probe.current.workflowRevisionRef.current.revision, 8);
  } finally { await f.h.close(); }
});

for (const kind of ['quad', 'header', 'row'])
for (const outcome of ['success', 'rerun-error', 'new-edit']) test(`owned ${kind} correction/rerun ${outcome} retires only successfully replaced matching input`, async () => {
  const f = await setup();
  try {
    await pendingCell(f); confirmation(f, true);
    const quad = [[0, 0], [10, 0], [10, 20], [0, 20]], ys = Array.from({ length: 57 }, (_, i) => i * 10);
    const receipt = { workflow_revision: 7, selected_pdf_receipt: { order_id: ORDER, document_id: PDF, generation: null, sha256: 'component-digest' },
      selected_version_input: null, input_digest: 'component-input', geometry_source_digest: 'component-geometry',
      configured_input_digest: 'component-config', order_input_identity: { facility_code: 'facility-A', week_code: '2026-W10' }, suggested_quad_px: quad };
    const review = { order_id: ORDER, source_receipt: receipt, coordinate_space: { width: 10, height: kind === 'row' ? 560 : 20 },
      image_png_base64: '', x_positions: [0, 10], y_positions: ys, header_band_count: 0,
      calendar_days: Array.from({ length: 7 }, (_, i) => ({ date: `2026-03-0${i + 2}`, expected_band_count: 8 })),
      body_day_boundary_indexes: [0, 8, 16, 24, 32, 40, 48, 56],
      saved_override: { source_receipt: receipt, quad_px: quad, corrected_xs: [0, 10], corrected_ys: ys,
        body_day_boundary_indexes: [0, 8, 16, 24, 32, 40, 48, 56], day_labels: Array.from({ length: 7 }, (_, i) => `2026-03-0${i + 2}`) } };
    const path = `${ROOT}/${kind === 'quad' ? 'quad' : `${kind}-axis`}-review`;
    f.t.on('get', path, () => ({ data: clone(review) }));
    await f.h.settle(() => kind === 'quad' ? f.probe.current.loadQuadReview() : f.probe.current.loadAxisReview(kind));
    f.t.on('put', path, () => {
      assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('0:4').value, '19');
      if (outcome === 'new-edit') f.probe.current.updateSheetCell(0, 4, '29');
      f.state.workflow.revision = 8; return { data: { workflow: clone(f.state.workflow) } };
    });
    f.t.on('post', `${ROOT}/ocr-runs`, () => {
      assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('0:4').value, '19');
      if (outcome === 'rerun-error') throw new Error('component rerun unavailable');
      f.state.workflow = { ...f.state.workflow, revision: 9, selected_ocr_result_id: null, saved_sheet_id: null };
      return { data: { workflow: clone(f.state.workflow) } };
    });
    await f.h.settle(() => kind === 'quad' ? f.probe.current.saveQuadAndRerun('manual_override', f.probe.current.manualQuadPoints)
      : f.probe.current.saveAxisAndRerun(kind));
    assert.equal(calls(f, 'put', path).length, 1);
    assert.equal(calls(f, 'post', `${ROOT}/ocr-runs`).length, outcome === 'new-edit' ? 0 : 1);
    assert.equal(f.probe.current.workflowRevisionRef.current.revision, outcome === 'success' ? 9 : outcome === 'rerun-error' ? 8 : 7);
    if (outcome === 'success') {
      assert.equal(f.probe.current.pendingSheetCellEditsRef.current.size, 0);
      assert.equal(f.probe.current.sheetPayloadRef.current, null);
    } else {
      assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('0:4').value, outcome === 'new-edit' ? '29' : '19');
      assert.equal(f.probe.current.sheetPayloadRef.current.rows[0][4], '2');
    }
    assert.equal(f.probe.current.busy, '');
  } finally { await f.h.close(); }
});

for (const action of ['swap', 'ocr-suggestions', 'ai-all', 'ai-single', 'anomaly-all', 'anomaly-single'])
test(`ordinary ${action} writes canonical ref and state together; immediate action/save sees latest values`, async () => {
  const f = await setup();
  try {
    let expected = '12';
    if (action === 'swap') {
      await f.h.settle(() => { f.probe.current.setSwapLeftColumn('4'); f.probe.current.setSwapRightColumn('5'); }); expected = '5';
    }
    if (action === 'ocr-suggestions') {
      f.state.saved = { ...sheet(''), ocr_numeric_cell_items: [{ target_row_index: 0, target_col_index: 4, classification: 'accepted', value: '12' }] };
      await f.h.settle(() => f.probe.current.refreshAll());
    }
    if (action.startsWith('ai-')) {
      f.t.on('post', `${ROOT}/sheet/auto-edit`, () => {
        f.state.workflow.revision = 8; return { data: { workflow: clone(f.state.workflow), job: { job_id: 'component-ai', order_id: ORDER, status: 'running' } } };
      });
      f.t.on('get', `${ROOT}/sheet/auto-edit/component-ai`, () => {
        f.state.workflow.revision = 9;
        return { data: { job: { job_id: 'component-ai', order_id: ORDER, status: 'done', workflow_result_revision: 9 },
          result: { status: 'done', patches: [{ row_index: 0, col_index: 4, suggested_value: '12' }] } } };
      });
      await f.h.settle(() => f.probe.current.proposeSheetAutoEdit());
      await f.h.waitFor(() => f.probe.current.sheetAutoEditResult?.patches?.length === 1);
    }
    await f.h.settle(() => {
      const p = f.probe.current, patch = { row_index: 0, col_index: 4, suggested_value: '12' };
      if (action === 'swap') p.swapQuantityColumns();
      if (action === 'ocr-suggestions') p.applyVisibleOcrSuggestions();
      if (action === 'ai-all') p.applySheetAutoEditPatches();
      if (action === 'ai-single') p.applySingleSheetAutoEditPatch(patch);
      if (action === 'anomaly-all') p.applyAnomalyCorrections();
      if (action === 'anomaly-single') p.applySingleAnomalyCorrection(patch);
      assert.equal(p.getSheetPayloadForAction().rows[0][4], expected);
    });
    await f.h.settle(() => f.probe.current.saveSheet());
    assert.equal(calls(f, 'put', `${ROOT}/sheet`)[0].body.sheet.rows[0][4], expected);
  } finally { await f.h.close(); }
});

for (const retirement of ['unmount', 'logout', 'new-session', 'new-order'])
test(`regeneration response after ${retirement} cannot overwrite old operator input or publish into successor`, async () => {
  const f = await setup(), wait = deferred(); let operation;
  try {
    await pendingCell(f); confirmation(f, true);
    f.t.on('get', `${ROOT}/sheet-source`, () => wait.promise);
    await f.h.settle(() => { operation = f.probe.current.generateSheetFromSelectedOcr(); });
    if (retirement === 'unmount') await f.h.unmount();
    if (retirement === 'logout') await f.h.session(false);
    if (retirement === 'new-session') await f.h.session(true);
    if (retirement === 'new-order') {
      await f.h.settle(() => f.h.nav.router.push('/orders/other-order/workflow-v2'));
      f.h.nav.router.query.id = 'other-order'; await f.h.render();
    }
    const before = snapshot(f);
    await f.h.settle(() => wait.resolve(f.source())); await operation;
    assert.deepEqual(snapshot(f), before);
    assert.equal(calls(f, 'put', `${ROOT}/sheet`).length, 0);
  } finally { wait.resolve(f.source()); if (operation) await operation; await f.h.close(); }
});

for (const callback of [false, true]) test(`existing useH1LeaveGuard ${callback ? 'live callback' : 'boolean'} caller keeps compatible clean/dirty behavior`, async () => {
  const t = transport(), dirty = { current: false }; let api;
  const h = await mount(null, { api: t.api, component(loader, React) {
    const { useH1LeaveGuard } = loader.load('src/hooks/useH1LeaveGuard.ts');
    return function Probe() { api = useH1LeaveGuard(callback ? () => dirty.current : dirty.current, ['/retained']); return React.createElement('p', null, 'guard'); };
  } });
  try {
    let prompts = 0; window.confirm = () => { prompts++; return false; };
    assert.equal(api.allow('/orders'), true);
    dirty.current = true; if (!callback) await h.render();
    assert.equal(api.allow('/orders'), false); assert.equal(prompts, 1);
    assert.equal(api.allow('/retained'), true); assert.equal(prompts, 1);
    dirty.current = false; if (!callback) await h.render();
    assert.equal(api.allow('/orders'), true);
  } finally { await h.close(); }
});
