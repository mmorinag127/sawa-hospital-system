const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Passive probes observe real component closures/state, not replacement controls,
// fake RHF state or reconstructed output algorithms. API fixtures are synthetic.
function common() {
  const t = transport();
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic-revision',
    facilities: [{ id: 'synthetic-facility', name: 'Synthetic facility' }] } }));
  return t;
}
async function start(h, action) {
  let promise; await h.settle(() => { promise = action(); promise?.catch(() => {}); });
  return { promise };
}
function blobError(detail) {
  const pending = deferred(), blob = new Blob(['synthetic']);
  blob.text = () => pending.promise;
  return { pending, error: { response: { status: 409, data: blob } }, text: JSON.stringify({ detail }) };
}
const weeklyProbe = () => ({ component: 'WeeklyWeightOutputScreen', bindings: [
  'conditionForm', 'week', 'status', 'orders', 'loading', 'downloading', 'message', 'loadWeeklyOrders', 'downloadWeeklyWeight'] });
function weeklyTransport() {
  const t = common();
  t.on('get', '/orders', () => ({ data: { orders: [] } }));
  t.on('get', '/orders/by-line-date', () => ({ data: { orders: [0, 6, 9].map(value => ({
    id: `synthetic-${value}`, facility: 'synthetic-facility', line_count: value, status: '確定' })) } }));
  return t;
}

test('weekly actual initial/list/form/output preserves 0/6/9, filename, bytes, applied vs draft', async () => {
  const t = weeklyTransport(), probe = weeklyProbe();
  const bytes = new Blob(['0,6,9\n']);
  t.on('get', '/outputs/weekly-weight', () => ({ data: bytes, headers: { 'content-disposition': 'attachment; filename="synthetic.xlsx"' } }));
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
  try {
    await h.settle(); assert.deepEqual(probe.current.orders.map(row => row.line_count), [0, 6, 9]);
    const applied = probe.current.week;
    await h.change(h.document.querySelector('input[name="week"]'), '2026-W08');
    assert.equal(probe.current.week, applied); assert.deepEqual(probe.current.orders.map(row => row.line_count), [0, 6, 9]);
    await h.click(h.button('Excel出力'));
    assert.equal(h.downloads.at(-1).name, 'synthetic.xlsx'); assert.equal(h.blobs.at(-1), bytes);
    assert.equal(await h.blobs.at(-1).text(), '0,6,9\n');
    assert.equal(t.calls.at(-1).config.timeout, 0); assert.equal(probe.current.downloading, false);
    await h.click(h.button('表示・集計')); assert.equal(probe.current.week, '2026-W08');
  } finally { await h.close(); }
});

for (const kind of ['list', 'output']) for (const boundary of ['condition', 'reset', 'session', 'unmount']) {
  for (const outcome of ['success', 'error']) test(`weekly ${kind} late ${outcome}/${boundary} rejects adoption and retires pending`, async () => {
    const t = weeklyTransport(), probe = weeklyProbe(), pending = deferred();
    const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
    try {
      await h.settle(); const previous = probe.current.orders;
      const url = kind === 'list' ? '/orders/by-line-date' : '/outputs/weekly-weight';
      const previousCalls = t.calls.filter(call => call.url === url).length;
      t.on('get', url, () => pending.promise);
      const operation = await start(h, () => kind === 'list' ? probe.current.loadWeeklyOrders(probe.current.conditionForm.getValues())
        : probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()));
      await h.waitFor(() => t.calls.filter(call => call.url === url).length > previousCalls, `weekly ${kind} request started`);
      if (boundary === 'condition') await h.change(h.document.querySelector('input[name="week"]'), '2026-W09');
      if (boundary === 'reset') await h.settle(() => probe.current.conditionForm.reset({ week: '2026-W10', status: '確定' }));
      if (boundary === 'session') await h.session(false);
      if (boundary === 'unmount') await h.unmount();
      await h.settle(() => outcome === 'success' ? pending.resolve({ data: kind === 'list' ? { orders: [{ id: 'obsolete', line_count: 99 }] } : new Blob(['obsolete']), headers: {} })
        : pending.reject({ response: { data: { detail: 'obsolete-error' } } }));
      await operation.promise.catch(() => {});
      assert.equal(h.downloads.length, 0); assert.doesNotMatch(h.document.body.textContent, /obsolete/);
      if (boundary !== 'unmount' && boundary !== 'session') {
        assert.equal(probe.current.loading, false); assert.equal(probe.current.downloading, false);
        assert.equal(probe.current.orders, previous);
      }
    } finally { await h.close(); }
  });
}

test('weekly Blob second await/old finally cannot clear a newer pending output', async () => {
  const t = weeklyTransport(), probe = weeklyProbe(), old = blobError('obsolete-second-await'), next = deferred();
  t.on('get', '/outputs/weekly-weight', () => Promise.reject(old.error));
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
  try {
    await h.settle(); const first = await start(h, () => probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()));
    await h.change(h.document.querySelector('input[name="week"]'), '2026-W09');
    t.on('get', '/outputs/weekly-weight', () => next.promise);
    const second = await start(h, () => probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()));
    await h.settle(() => old.pending.resolve(old.text)); await first.promise;
    assert.equal(probe.current.downloading, true); assert.doesNotMatch(probe.current.message, /obsolete/);
    await h.settle(() => next.resolve({ data: new Blob(['0,6,9']), headers: {} })); await second.promise;
    assert.equal(h.downloads.length, 1); assert.equal(probe.current.downloading, false);
  } finally { await h.close(); }
});

const dailyProbe = () => ({ component: 'DailyDeliveryNotesScreen', bindings: [
  'conditionForm', 'date', 'status', 'orders', 'dailyBagSummary', 'totalsRows', 'mealCountSummary', 'loading', 'message',
  'auditMessage', 'auditAiLoading', 'loadOrders', 'runAuditAi', 'openOverrideEditor', 'selectOverrideTarget', 'selectedOverrideRow',
  'overrideEditorDrafts', 'overrideEditorBulkDraft', 'overrideEditorSavingKey', 'overrideEditorBulkSaving', 'overrideEditorMessage',
  'updateOverrideDraft', 'updateOverrideBulkDraft', 'saveOverrideRow', 'saveBulkOverride', 'deleteOverrideRow',
  'openDailyDeliveryHtml', 'downloadDailyBundle', 'openHtmlOutput', 'openOutput'] });
function context(section, label, params) {
  assert.ok(params && params.date, 'explicit captured request conditions');
  const envelope = { date: params.date, status: params.status || null, facility: null };
  const fulfilled = data => ({ status: 'fulfilled', data });
  if (section === 'primary') return { data: { ...envelope, sections: { orders: fulfilled({ date: params.date, orders: [{ id: label, facility: 'synthetic-facility', line_count: 0 }] }),
    meal_counts: fulfilled({ groups: [{ daypart: '昼食', counts: [0, 6, 9].map((quantity, index) => ({ diet_type: ['regular', 'soft', 'mixer'][index], quantity })) }] }) } } };
  if (section === 'bags') return { data: { ...envelope, sections: { daily_bags: fulfilled({ groups: [], test_marker: label }),
    daily_bags_audit: fulfilled({ rule_based: { finding_count: 0 }, test_marker: label }) } } };
  return { data: { ...envelope, sections: { totals: fulfilled({ rows: [0, 6, 9].map(quantity => ({ menu_name: label, quantity })) }) } } };
}
function dailyTransport() {
  const t = common(); t.on('get', '/orders/daily-output-context', call => context(call.config.params.section, 'applied-A', call.config.params));
  return t;
}
async function applyDaily(h, probe, date = '2026-02-15') {
  await h.change(h.document.querySelector('input[name="date"]'), date);
  await h.click(h.button('表示・集計')); await h.settle();
  assert.equal(probe.current.date, date);
}

test('daily actual three sections adopt atomically; draft edit/primary failure preserves prior applied set', async () => {
  const t = dailyTransport(), probe = dailyProbe(), h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
  try {
    await applyDaily(h, probe);
    assert.deepEqual(probe.current.totalsRows.map(row => row.quantity), [0, 6, 9]);
    const sections = { primary: deferred(), bags: deferred(), totals: deferred() };
    t.on('get', '/orders/daily-output-context', call => sections[call.config.params.section].promise);
    await h.change(h.document.querySelector('input[name="date"]'), '2026-02-16');
    assert.equal(probe.current.date, '2026-02-15');
    const op = await start(h, () => probe.current.loadOrders(probe.current.conditionForm.getValues()));
    const captured = { ...probe.current.conditionForm.getValues() };
    await h.settle(() => sections.primary.resolve(context('primary', 'B', captured)));
    assert.equal(probe.current.date, '2026-02-15'); assert.equal(probe.current.orders[0].id, 'applied-A');
    await h.settle(() => sections.totals.resolve(context('totals', 'B', captured)));
    assert.equal(probe.current.totalsRows[0].menu_name, 'applied-A');
    await h.settle(() => sections.bags.resolve(context('bags', 'B', captured))); await op.promise;
    assert.equal(probe.current.date, '2026-02-16'); assert.equal(probe.current.orders[0].id, 'B');
    assert.equal(probe.current.dailyBagSummary.test_marker, 'B'); assert.equal(probe.current.totalsRows[0].menu_name, 'B');
    t.on('get', '/orders/daily-output-context', call => call.config.params.section === 'primary'
      ? Promise.reject(new Error('primary-stop')) : context(call.config.params.section, 'must-not-adopt', call.config.params));
    await h.change(h.document.querySelector('input[name="date"]'), '2026-02-17'); await h.click(h.button('表示・集計'));
    assert.equal(probe.current.date, '2026-02-16'); assert.equal(probe.current.dailyBagSummary.test_marker, 'B');
    assert.equal(probe.current.totalsRows[0].menu_name, 'B'); assert.match(h.document.body.textContent, /primary-stop/);
  } finally { await h.close(); }
});

for (const field of ['date', 'status', 'facility']) {
  test(`daily mismatched primary ${field} echo stops all new section adoption`, async () => {
    const t = dailyTransport(), probe = dailyProbe();
    const h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
    try {
      await applyDaily(h, probe);
      const previous = { orders: probe.current.orders, bags: probe.current.dailyBagSummary, totals: probe.current.totalsRows };
      t.on('get', '/orders/daily-output-context', call => {
        const response = context(call.config.params.section, 'wrong-echo', call.config.params);
        if (call.config.params.section === 'primary') response.data[field] = {
          date: '2026-02-14', status: 'synthetic-wrong-status', facility: 'GI8',
        }[field];
        return response;
      });
      await h.change(h.document.querySelector('input[name="date"]'), '2026-02-16');
      await h.click(h.button('表示・集計'));
      assert.equal(probe.current.date, '2026-02-15');
      assert.equal(probe.current.orders, previous.orders);
      assert.equal(probe.current.dailyBagSummary, previous.bags);
      assert.equal(probe.current.totalsRows, previous.totals);
      assert.match(h.document.body.textContent, /集計の取得対象が一致しません/);
      assert.doesNotMatch(h.document.body.textContent, /wrong-echo/);
      assert.equal(probe.current.loading, false);
    } finally { await h.close(); }
  });
}

for (const boundary of ['condition', 'session', 'unmount']) for (const outcome of ['success', 'error']) {
  test(`daily audit/load late ${outcome}/${boundary} cannot update old sections or pending`, async () => {
    const t = dailyTransport(), probe = dailyProbe(), pending = deferred();
    const h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
    try {
      await applyDaily(h, probe); const original = probe.current.orders;
      const captured = { ...probe.current.conditionForm.getValues() };
      t.on('get', '/orders/daily-output-context', call => pending.promise.then(() => context(call.config.params.section, 'obsolete', call.config.params)));
      t.on('get', '/orders/daily-bags/audit', () => pending.promise);
      const loading = await start(h, () => probe.current.loadOrders(probe.current.conditionForm.getValues()));
      const audit = await start(h, () => probe.current.runAuditAi());
      assert.ok(t.calls.some(call => call.url === '/orders/daily-bags/audit'));
      if (boundary === 'condition') await h.change(h.document.querySelector('input[name="date"]'), '2026-02-16');
      if (boundary === 'session') await h.session(false);
      if (boundary === 'unmount') await h.unmount();
      await h.settle(() => outcome === 'success' ? pending.resolve({ data: {
        ...context('primary', 'obsolete', captured).data, ai: { status: 'completed' }, obsolete: true } }) : pending.reject(new Error('obsolete-error')));
      await Promise.all([loading.promise.catch(() => {}), audit.promise]);
      assert.doesNotMatch(h.document.body.textContent, /obsolete/);
      if (boundary === 'condition') { assert.equal(probe.current.orders, original); assert.equal(probe.current.loading, false); assert.equal(probe.current.auditAiLoading, false); }
    } finally { await h.close(); }
  });
}

function editorRows(date) {
  return { date, daypart: '昼食', menu_name: 'synthetic-menu', menu_category: '主菜', rows: [
    { facility_id: 'GI7', facility_label: 'Synthetic GI7', diet_type: 'regular', current_qty_per_serving: 0, current_unit_type: 'g', override: { id: 'override-GI7', qty_per_serving: 0, unit_type: 'g' } },
    { facility_id: 'GI8', facility_label: 'Synthetic GI8', diet_type: 'soft', current_qty_per_serving: 6, current_unit_type: 'g' },
  ] };
}
async function openEditor(h, probe, t) {
  t.on('get', '/orders/daily-output-overrides', () => ({ data: editorRows(probe.current.date) }));
  await h.settle(() => probe.current.openOverrideEditor({ daypart: '昼食', menu_name: 'synthetic-menu', menu_category: '主菜' }));
  await h.settle(() => probe.current.selectOverrideTarget('GI7', 'regular'));
  assert.ok(probe.current.selectedOverrideRow);
}

test('daily callback-only row refresh keeps independent row/bulk drafts, exact zero body and delete target', async () => {
  // A successful callback double is not acceptance of the blocked public API.
  const t = dailyTransport(), probe = dailyProbe(); t.on('post', '/orders/daily-output-overrides/upsert', () => ({ data: {} }));
  t.on('delete', '/orders/daily-output-overrides/override-GI7', () => ({ data: {} }));
  const h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
  try {
    await applyDaily(h, probe); await openEditor(h, probe, t);
    await h.settle(() => {
      probe.current.updateOverrideDraft('GI8__soft', { qty_per_serving: '9', note: 'unsaved-neighbor' });
      probe.current.updateOverrideBulkDraft({ qty_per_serving: '6', note: 'unsaved-bulk' });
    });
    await h.settle(() => probe.current.saveOverrideRow(probe.current.selectedOverrideRow));
    const call = t.calls.find(call => call.method === 'post'); assert.ok(call);
    assert.equal(call.body.facility_id, 'GI7'); assert.equal(call.body.diet_type, 'regular'); assert.equal(call.body.qty_per_serving, 0);
    assert.equal(probe.current.overrideEditorDrafts.GI8__soft.qty_per_serving, '9');
    assert.equal(probe.current.overrideEditorDrafts.GI8__soft.note, 'unsaved-neighbor');
    assert.equal(probe.current.overrideEditorBulkDraft.qty_per_serving, '6');
    await h.settle(() => probe.current.deleteOverrideRow(probe.current.selectedOverrideRow));
    assert.equal(t.calls.filter(call => call.method === 'delete')[0].url, '/orders/daily-output-overrides/override-GI7');
    assert.equal(probe.current.overrideEditorDrafts.GI8__soft.qty_per_serving, '9');
  } finally { await h.close(); }
});

for (const kind of ['row', 'bulk', 'delete']) for (const outcome of ['success', 'error']) {
  test(`daily callback-only ${kind} late ${outcome} after newer draft/target never reloads or resets edits`, async () => {
    const pending = deferred(), t = dailyTransport(), probe = dailyProbe();
    const mutationUrl = kind === 'bulk' ? '/orders/daily-output-overrides/upsert-bulk'
      : kind === 'row' ? '/orders/daily-output-overrides/upsert' : '/orders/daily-output-overrides/override-GI7';
    t.on(kind === 'delete' ? 'delete' : 'post', mutationUrl, () => pending.promise);
    t.on('delete', '/orders/daily-output-overrides/override-GI7', () => pending.promise);
    const h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
    try {
      await applyDaily(h, probe); await openEditor(h, probe, t);
      if (kind === 'bulk') await h.settle(() => probe.current.updateOverrideBulkDraft({ qty_per_serving: '6', unit_type: 'g' }));
      const operation = await start(h, () => kind === 'bulk' ? probe.current.saveBulkOverride()
        : kind === 'row' ? probe.current.saveOverrideRow(probe.current.selectedOverrideRow) : probe.current.deleteOverrideRow(probe.current.selectedOverrideRow));
      await h.waitFor(() => t.calls.some(call => call.url === mutationUrl && call.method === (kind === 'delete' ? 'delete' : 'post')), `${kind} request started`);
      const write = t.calls.find(call => call.url === mutationUrl && call.method !== 'get');
      if (kind !== 'delete') assert.equal(write.body.qty_per_serving, kind === 'bulk' ? 6 : 0);
      await h.settle(() => { probe.current.updateOverrideDraft('GI7__regular', { qty_per_serving: '9' });
        probe.current.updateOverrideBulkDraft({ qty_per_serving: '6' }); probe.current.selectOverrideTarget('GI8', 'soft'); });
      const reads = t.calls.filter(call => call.method === 'get').length;
      await h.settle(() => outcome === 'success' ? pending.resolve({ data: {} }) : pending.reject(new Error('obsolete-save'))); await operation.promise;
      assert.equal(t.calls.filter(call => call.method === 'get').length, reads);
      assert.equal(probe.current.overrideEditorDrafts.GI7__regular.qty_per_serving, '9');
      assert.equal(probe.current.overrideEditorBulkDraft.qty_per_serving, '6');
      assert.equal(probe.current.overrideEditorSavingKey, ''); assert.equal(probe.current.overrideEditorBulkSaving, false);
      assert.doesNotMatch(probe.current.overrideEditorMessage, /保存しました|解除しました|obsolete/);
    } finally { await h.close(); }
  });
}

test('daily unresolved/duplicate explicit target stops; public blocker remains an error, not mocked normal-save proof', async () => {
  const t = dailyTransport(), probe = dailyProbe(), h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
  try {
    await applyDaily(h, probe); await openEditor(h, probe, t);
    await h.settle(() => probe.current.selectOverrideTarget('GI7', 'missing'));
    assert.equal(probe.current.selectedOverrideRow, null);
    t.on('get', '/orders/daily-output-overrides', () => ({ data: { ...editorRows(probe.current.date), rows: [editorRows(probe.current.date).rows[0], editorRows(probe.current.date).rows[0]] } }));
    await h.settle(() => probe.current.openOverrideEditor({ daypart: '昼食', menu_name: 'synthetic-menu', menu_category: '主菜' }));
    await h.settle(() => probe.current.selectOverrideTarget('GI7', 'regular')); assert.equal(probe.current.selectedOverrideRow, null);
    assert.equal(t.calls.filter(call => call.method !== 'get').length, 0);
    await openEditor(h, probe, t);
    t.on('post', '/orders/daily-output-overrides/upsert', () => Promise.reject({ response: { status: 409, data: { detail: 'daily_output_original_ownership_required' } } }));
    await h.settle(() => probe.current.saveOverrideRow(probe.current.selectedOverrideRow));
    assert.match(probe.current.overrideEditorMessage, /daily_output_original_ownership_required/);
    assert.doesNotMatch(probe.current.overrideEditorMessage, /保存しました/);
  } finally { await h.close(); }
});

for (const action of ['closed', 'navigated', 'condition', 'session', 'unmount']) test(`daily HTML popup ${action} never receives late bytes`, async () => {
  const pending = deferred(), t = dailyTransport(), probe = dailyProbe();
  t.on('get', '/outputs/daily-delivery-notes/html', () => pending.promise);
  const h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
  try {
    await applyDaily(h, probe); const operation = await start(h, () => probe.current.openDailyDeliveryHtml());
    const popup = h.popups.at(-1); assert.ok(popup);
    if (action === 'closed') popup.close(); if (action === 'navigated') popup.navigate();
    if (action === 'condition') await h.change(h.document.querySelector('input[name="date"]'), '2026-02-16');
    if (action === 'session') await h.session(false); if (action === 'unmount') await h.unmount();
    await h.settle(() => pending.resolve({ data: '<p>obsolete 0/6/9</p>' })); await operation.promise;
    assert.equal(popup.writes.length, 0); assert.equal(popup.focuses, 0); assert.equal(h.downloads.length, 0);
  } finally { await h.close(); }
});

test('daily owned output preserves literal bytes/filename and second-await error retirement', async () => {
  const t = dailyTransport(), probe = dailyProbe(), bytes = new Blob(['0,6,9,null']);
  t.on('get', '/outputs/daily-bundle', () => ({ data: bytes, headers: { 'content-disposition': 'attachment; filename="synthetic.csv"' } }));
  const h = await mount('src/pages/daily-delivery-notes.tsx', { api: t.api, probe });
  try {
    await applyDaily(h, probe); await h.settle(() => probe.current.downloadDailyBundle('labels_csv'));
    assert.equal(h.blobs.at(-1), bytes); assert.equal(h.downloads.at(-1).name, 'synthetic.csv');
    const error = blobError('obsolete-blob'); t.on('get', '/outputs/daily-bundle', () => Promise.reject(error.error));
    const op = await start(h, () => probe.current.downloadDailyBundle('labels_csv'));
    await h.change(h.document.querySelector('input[name="date"]'), '2026-02-16');
    await h.settle(() => error.pending.resolve(error.text)); await op.promise;
    assert.doesNotMatch(probe.current.message, /obsolete-blob/); assert.equal(h.downloads.length, 1);
  } finally { await h.close(); }
});

const workflowProbe = () => ({ component: 'OrderWorkflowV2Page', bindings: [
  'workflow', 'effectiveSelectedDocumentId', 'refreshAll', 'openOutput', 'openHtmlOutput', 'loadOutputPreview',
  'downloadMessage', 'outputPreviewMessage', 'outputPreviewLoading', 'beginWorkflowEditorEdit', 'editorVersion'] });
function workflowTransport() {
  const t = common(), state = { revision: 7, withCurrent: false };
  for (const id of ['synthetic-order', 'other-order']) {
    t.on('get', `/orders/${id}/workflow-v2`, () => ({ data: { order_id: id, revision: state.revision, state: 'output_review',
      output_bundle_id: state.withCurrent ? 'recorded-current' : null, blockers: [], warnings: [] } }));
    t.on('get', `/orders/${id}/workflow-v2/ocr-results`, () => ({ data: { results: [] } }));
    t.on('get', `/orders/${id}/workflow-v2/inspection`, () => ({ data: { pre_save_checks: {},
      output_bundle: { output_bundle_id: 'recorded-current', status: 'ready', quantities: [0, null, 6, 9] } } }));
    t.on('get', `/orders/${id}`, () => ({ data: { id, current_version: { document_id: 'pdf-A' }, versions: [
      { document_id: 'pdf-A', version_no: 1, is_current: true }, { document_id: 'pdf-B', version_no: 2, is_current: false }] } }));
    t.on('get', `/orders/${id}/week-options`, () => ({ data: { options: [] } }));
    t.on('get', `/orders/${id}/document`, () => ({ data: new Blob(['synthetic-not-a-real-pdf']) }));
  }
  return { t, state };
}
async function workflow(h, probe) {
  await h.waitFor(() => probe.current?.workflow?.revision === 7 && probe.current.effectiveSelectedDocumentId === 'pdf-A', 'owned workflow/document read');
  assert.equal(probe.current.workflow.revision, 7);
  assert.equal(probe.current.effectiveSelectedDocumentId, 'pdf-A');
}
async function retireWorkflow(h, probe, state, boundary) {
  if (boundary === 'editor') await h.settle(() => probe.current.beginWorkflowEditorEdit('sheet'));
  if (boundary === 'revision') { state.revision = 8; await h.settle(() => probe.current.refreshAll()); }
  if (boundary === 'pdf') {
    await h.click(h.document.querySelector('nav[aria-label="workflow steps"] button'));
    await h.waitFor(() => [...h.document.querySelectorAll('select')].some(node => [...node.options].some(option => option.value === 'pdf-B')), 'Step1 actual PDF control');
    const select = [...h.document.querySelectorAll('select')].find(node => [...node.options].some(option => option.value === 'pdf-B'));
    assert.ok(select); await h.change(select, 'pdf-B');
  }
  if (boundary === 'order') {
    await h.settle(() => h.nav.router.events.emit('routeChangeStart', '/orders/other-order/workflow-v2'));
    h.nav.router.query.id = 'other-order'; h.nav.router.asPath = '/orders/other-order/workflow-v2'; await h.render();
  }
  if (boundary === 'session') await h.session(false);
  if (boundary === 'unmount') await h.unmount();
}

test('workflow owned HTML uses only its original popup and preserves returned 0/6/9/null markup', async () => {
  const { t } = workflowTransport(), probe = workflowProbe();
  const html = '<!doctype html><p>0/6/9/null</p>';
  const url = '/outputs/delivery-notes/html?order_id=synthetic-order';
  t.on('get', url, () => ({ data: html }));
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: '/orders/synthetic-order/workflow-v2' });
  try {
    await workflow(h, probe);
    await h.settle(() => probe.current.openHtmlOutput(url, 'Synthetic HTML'));
    assert.equal(h.popups.length, 1);
    assert.equal(h.popups[0].writes.at(-1), html);
    // This actual HTML path writes/closes the original document, without focus().
    assert.equal(h.popups[0].focuses, 0);
    assert.equal(h.downloads.length, 0);
    assert.equal(t.calls.filter(call => call.url === url).length, 1);
  } finally { await h.close(); }
});

for (const kind of ['download', 'preview']) for (const boundary of ['order', 'pdf', 'revision', 'editor', 'session', 'unmount']) {
  for (const outcome of ['success', 'error']) test(`workflow ${kind} late ${outcome}/${boundary} fences real production callback`, async () => {
    const { t, state } = workflowTransport(), probe = workflowProbe(), pending = deferred();
    t.on('get', '/outputs/labels?order_id=synthetic-order', () => pending.promise);
    t.on('get', '/outputs/file-preview', () => pending.promise);
    const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: '/orders/synthetic-order/workflow-v2' });
    try {
      await workflow(h, probe);
      const op = await start(h, () => kind === 'download' ? probe.current.openOutput('/outputs/labels?order_id=synthetic-order', 'Synthetic CSV') : probe.current.loadOutputPreview('labels'));
      assert.ok(t.calls.some(call => call.url === (kind === 'download' ? '/outputs/labels?order_id=synthetic-order' : '/outputs/file-preview')));
      const writes = h.popups.at(-1)?.writes.length || 0;
      await retireWorkflow(h, probe, state, boundary);
      await h.settle(() => outcome === 'success' ? pending.resolve({ data: kind === 'download' ? new Blob(['obsolete']) : '<p>obsolete</p>', headers: {} })
        : pending.reject({ response: { status: 409, data: { detail: 'obsolete-error' } } }));
      await op.promise; assert.equal(h.downloads.length, 0);
      assert.equal(h.popups.at(-1)?.writes.length || 0, writes); assert.doesNotMatch(h.document.body.textContent, /obsolete/);
      if (boundary !== 'unmount') assert.equal(probe.current.outputPreviewLoading, false);
    } finally { await h.close(); }
  });
}

test('workflow neutral editor registration is not an edit; exact owned bytes and popup no-popup alternate', async () => {
  const { t } = workflowTransport(), probe = workflowProbe(), pending = deferred(), bytes = new Blob(['0,6,9,null']);
  t.on('get', '/outputs/labels?order_id=synthetic-order', () => pending.promise);
  t.on('get', '/outputs/file-preview', () => ({ data: '<p>0/6/9</p>' }));
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: '/orders/synthetic-order/workflow-v2' });
  try {
    await workflow(h, probe); const op = await start(h, () => probe.current.openOutput('/outputs/labels?order_id=synthetic-order', 'Synthetic CSV'));
    await h.settle(() => probe.current.editorVersion('untouched-test-editor'));
    await h.settle(() => pending.resolve({ data: bytes, headers: { 'content-disposition': 'attachment; filename="synthetic.csv"' } })); await op.promise;
    assert.equal(h.downloads.at(-1).name, 'synthetic.csv'); assert.equal(h.blobs.at(-1), bytes);
    const open = window.open; window.open = () => null;
    try { await h.settle(() => probe.current.loadOutputPreview('labels')); }
    finally { window.open = open; }
    assert.equal(h.downloads.length, 2); assert.equal(await h.blobs.at(-1).text(), '<p>0/6/9</p>');
    assert.equal(probe.current.outputPreviewLoading, false);
  } finally { await h.close(); }
});

for (const kind of ['html', 'preview']) for (const state of ['closed', 'navigated']) test(`workflow ${kind} popup ${state} rejects late original-document mutation`, async () => {
  const { t } = workflowTransport(), probe = workflowProbe(), pending = deferred();
  t.on('get', '/outputs/delivery-notes/html?order_id=synthetic-order', () => pending.promise);
  t.on('get', '/outputs/file-preview', () => pending.promise);
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: '/orders/synthetic-order/workflow-v2' });
  try {
    await workflow(h, probe); const op = await start(h, () => kind === 'html'
      ? probe.current.openHtmlOutput('/outputs/delivery-notes/html?order_id=synthetic-order', 'Synthetic HTML') : probe.current.loadOutputPreview('delivery'));
    const popup = h.popups.at(-1), before = popup.writes.length;
    state === 'closed' ? popup.close() : popup.navigate();
    await h.settle(() => pending.resolve({ data: '<p>must-not-write</p>' })); await op.promise;
    assert.equal(popup.writes.length, before); assert.equal(popup.focuses, 0); assert.equal(h.downloads.length, 0);
  } finally { await h.close(); }
});

test('workflow second Blob await and old preview finally do not clear newer pending preview', async () => {
  const { t } = workflowTransport(), probe = workflowProbe(), error = blobError('obsolete-detail'), next = deferred();
  t.on('get', '/outputs/file-preview', () => Promise.reject(error.error));
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: '/orders/synthetic-order/workflow-v2' });
  try {
    await workflow(h, probe); const old = await start(h, () => probe.current.loadOutputPreview('labels'));
    t.on('get', '/outputs/file-preview', () => next.promise);
    const current = await start(h, () => probe.current.loadOutputPreview('delivery'));
    await h.settle(() => error.pending.resolve(error.text)); await old.promise;
    assert.equal(probe.current.outputPreviewLoading, true); assert.doesNotMatch(probe.current.outputPreviewMessage, /obsolete-detail/);
    await h.settle(() => next.resolve({ data: '<p>0/6/9</p>' })); await current.promise;
    assert.equal(probe.current.outputPreviewLoading, false);
    assert.match(h.popups.at(-1).writes.at(-1), /0\/6\/9/);
  } finally { await h.close(); }
});

test('workflow only renders/fetches saved history when exact current reference and revision exist', async () => {
  const { t, state } = workflowTransport(), probe = workflowProbe();
  t.on('get', '/outputs/history', () => ({ data: { order_id: 'synthetic-order', workflow_revision: 7,
    current_output_bundle_id: 'recorded-current', page: 1, limit: 25, total: 0, entries: [] } }));
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', { api: t.api, probe, route: '/orders/synthetic-order/workflow-v2' });
  try {
    await workflow(h, probe); assert.equal(t.calls.some(call => call.url === '/outputs/history'), false);
    state.withCurrent = true; await h.settle(() => probe.current.refreshAll()); await h.settle();
    assert.equal(t.calls.some(call => call.url === '/outputs/history'), true);
    assert.equal(t.calls.find(call => call.url === '/outputs/history').config.params.expected_workflow_revision, 7);
  } finally { await h.close(); }
});
