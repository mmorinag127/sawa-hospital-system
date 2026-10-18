const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Actual React page/handlers; authored HTTP/session only, not backend or live proof.
const ORDER = 'authored-setting-order', BASE = `/orders/${ORDER}/workflow-v2`;
const clone = value => JSON.parse(JSON.stringify(value));
const sheet = { fields: ['date', 'daypart', 'menu_name', 'qty.regular_x'],
  rows: [['2026/10/07', '昼', 'own', '26']], base_evidence_run_id: 'own-evidence',
  template_version_id: 'own-version', selected_ocr_result_id: 'own-evidence' };

async function setup() {
  const t = transport(), pending = deferred();
  const state = { workflow: { order_id: ORDER, revision: 7, state: 'sheet_saved',
    selected_ocr_result_id: 'own-evidence', saved_sheet_id: 'own-saved',
    facility_id: 'own-facility', week_start: '2026-10-05', week_end: '2026-10-11',
    template_id: 'own-template', template_version_id: 'own-version',
    expanded_cell_copy_mode: 'auto', blockers: [], warnings: [],
    menu_amount_resolution: {status: 'resolved', affected_lines: []} } };
  const config = { source: 'canonical_json', order_id: ORDER, facility_id: 'own-facility',
    week_start: '2026-10-05', week_end: '2026-10-11', template_version_id: 'own-version',
    config: { fax_template_id: 'own-template', fax_template: { columns: [
      { index: 0, source_index: 0, role: 'date', name: 'date' },
      { index: 1, source_index: 1, role: 'daypart', name: 'daypart' },
      { index: 2, source_index: 2, role: 'menu', name: 'menu_name' },
      { index: 3, source_index: 3, role: 'quantity', name: 'qty.regular_x', diet_type: 'regular', area_id: 'X' },
    ] } } };
  t.on('get', '/auth/me', () => ({data: {role: 'operator'}}));
  t.on('get', '/facilities', () => ({data: {source: 'canonical_json', revision: 'authored',
    facilities: [{id: 'own-facility', name: 'Authored facility'}]}}));
  t.on('get', BASE, () => ({data: clone(state.workflow)}));
  t.on('get', `${BASE}/facility-config`, () => ({data: clone(config)}));
  t.on('get', `${BASE}/inspection?include_ocr_results=true`, () => ({data: {
    workflow: clone(state.workflow), pre_save_checks: {}, pre_save_status: {},
    saved_sheet: {saved_sheet_id: 'own-saved', source_ocr_result_id: 'own-evidence', sheet: clone(sheet)},
    ocr_candidates: require('./helpers/ocr-candidates.cjs')(ORDER,
      [{ocr_result_id: 'own-evidence', selected: true, status: 'completed'}], state.workflow),
  }}));
  t.on('get', `/orders/${ORDER}`, () => ({data: {id: ORDER,
    current_version: {document_id: 'own-pdf'}, versions: [
      {document_id: 'own-pdf', version_no: 1, is_current: true},
      {document_id: 'other-pdf', version_no: 2, is_current: false},
    ]}}));
  t.on('get', `/orders/${ORDER}/week-options`, () => ({data: {options: []}}));
  for (const suffix of ['', '?document_id=own-pdf', '?document_id=other-pdf'])
    t.on('get', `/orders/${ORDER}/document${suffix}`, () => ({data: new Blob(['authored transport'])}));
  t.on('put', `${BASE}/expanded-cell-copy-mode`, () => pending.promise);
  const probe = { component: 'OrderWorkflowV2Page', bindings: [
    'workflow', 'sheetPayload', 'sheetDirty', 'sheetDirtyRef', 'pendingSheetCellEditsRef',
    'expandedCellCopyMode', 'expandedCellCopySaving', 'updateExpandedCellCopyMode',
    'updateSheetCell', 'flushPendingSheetCellEdits', 'refreshAll', 'error',
    'setSelectedDocumentId', 'workflowRevisionRef',
  ]};
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx',
    {api: t.api, probe, route: `/orders/${ORDER}/workflow-v2`, routerState: {query: {id: ORDER}}});
  try {
    await h.waitFor(() => probe.current?.sheetPayload?.rows?.[0]?.[3] === '26');
    await h.settle();
    return {h, t, pending, probe, state,
      async begin() {
        let promise;
        await h.settle(() => { promise = probe.current.updateExpandedCellCopyMode('enabled'); });
        assert.ok(t.calls.some(call => call.method === 'put'));
        return {promise};
      },
      async respond() {
        state.workflow = {...state.workflow, revision: 8, expanded_cell_copy_mode: 'enabled'};
        await h.settle(() => pending.resolve({data: clone(state.workflow)}));
      },
    };
  } catch (error) { await h.close(); throw error; }
}

function onlySettingWrite(f) {
  assert.deepEqual(f.t.calls.filter(c => c.method !== 'get').map(c => [c.method, c.url]),
    [['put', `${BASE}/expanded-cell-copy-mode`]]);
  assert.equal(f.t.calls.filter(c => c.url.endsWith('/sheet-source')).length, 0);
}

test('setting change retains actual saved26 editor without regeneration or save', async () => {
  const f = await setup();
  try {
    const action = await f.begin();
    await f.respond();
    await action.promise;
    assert.equal(f.probe.current.sheetPayload.rows[0][3], '26');
    assert.equal(f.probe.current.sheetDirty, false);
    assert.equal(f.probe.current.workflow.saved_sheet_id, 'own-saved');
    assert.equal(f.probe.current.expandedCellCopyMode, 'enabled');
    onlySettingWrite(f);
  } finally { await f.h.close(); }
});

test('setting change preserves preexisting unsaved zero and pending edits', async () => {
  const f = await setup();
  try {
    await f.h.settle(() => f.probe.current.updateSheetCell(0, 3, '0'));
    const action = await f.begin();
    await f.respond();
    await action.promise;
    await f.h.settle(() => f.probe.current.flushPendingSheetCellEdits());
    assert.equal(f.probe.current.sheetPayload.rows[0][3], '0');
    assert.equal(f.probe.current.sheetDirty, true);
    await f.h.settle(() => f.probe.current.refreshAll());
    assert.equal(f.probe.current.sheetPayload.rows[0][3], '0');
    onlySettingWrite(f);
  } finally { await f.h.close(); }
});

test('input edit during setting command retires stale response and retains latest value', async () => {
  const f = await setup();
  try {
    const action = await f.begin();
    await f.h.settle(() => f.probe.current.updateSheetCell(0, 3, '31'));
    await f.respond();
    await action.promise;
    await f.h.settle(() => f.probe.current.flushPendingSheetCellEdits());
    assert.equal(f.probe.current.sheetPayload.rows[0][3], '31');
    assert.equal(f.probe.current.sheetDirty, true);
    assert.equal(f.probe.current.workflow.revision, 7);
    await f.h.settle(() => f.probe.current.refreshAll());
    assert.equal(f.probe.current.sheetPayload.rows[0][3], '31');
    assert.equal(f.probe.current.sheetDirtyRef.current, true);
    onlySettingWrite(f);
  } finally { await f.h.close(); }
});

test('document switch during setting command cannot adopt the old command response', async () => {
  const f = await setup();
  try {
    const action = await f.begin();
    await f.h.settle(() => f.probe.current.setSelectedDocumentId('other-pdf'));
    await f.respond();
    await action.promise;
    assert.notEqual(f.probe.current.expandedCellCopyMode, 'enabled');
    onlySettingWrite(f);
  } finally { await f.h.close(); }
});

