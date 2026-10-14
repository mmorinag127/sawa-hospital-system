const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

const historyProps = () => ({ orderId: 'synthetic-order', documentId: 'synthetic-pdf', workflowRevision: 7,
  currentOutputBundleId: 'synthetic-bundle', route: '/orders/synthetic-order/workflow-v2',
  inputKey: [0], captureOwner: () => () => true });
const history = () => ({ order_id: 'synthetic-order', workflow_revision: 7,
  current_output_bundle_id: 'synthetic-bundle', page: 1, limit: 25, total: 1,
  entries: [{ output_bundle_id: 'synthetic-bundle', order_id: 'synthetic-order',
    source_saved_sheet_id: 'synthetic-sheet', source_bagging_result_id: 'synthetic-bag',
    template_version_id: null, created_at: '2026-10-09T00:00:00+00:00', current: true,
    validation: 'validated', blocked_reasons: [] }] });

for (const field of ['source_saved_sheet_id', 'source_bagging_result_id']) {
  for (const value of [undefined, null, 0, '']) test(`history stops missing ${field}=${String(value)} without source fallback`, async () => {
    const payload = history(); payload.entries[0][field] = value;
    const t = transport(); t.on('get', '/outputs/history', () => ({ data: payload }));
    const h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api, props: historyProps() });
    try {
      await h.settle();
      assert.equal(h.document.querySelector('table'), null);
      assert.ok(h.document.querySelector('[role="alert"]'));
      assert.deepEqual(h.downloads, []);
      assert.deepEqual(t.calls.map(call => call.url), ['/outputs/history']);
    } finally { await h.close(); }
  });
}

for (const outcome of ['success', 'error']) test(`history owner retirement without rerender rejects late ${outcome}`, async () => {
  let current = true;
  const pending = deferred(), t = transport();
  t.on('get', '/outputs/history', () => pending.promise);
  const h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api,
    props: { ...historyProps(), captureOwner: () => () => current } });
  try {
    await h.waitFor(() => t.calls.length === 1, 'history request');
    current = false;
    await h.settle(() => outcome === 'success' ? pending.resolve({ data: history() }) : pending.reject(new Error('obsolete-history')));
    assert.equal(h.document.querySelector('table'), null);
    assert.doesNotMatch(h.document.body.textContent, /synthetic-bundle|obsolete-history/);
    assert.deepEqual(h.downloads, []);
  } finally { await h.close(); }
});

// Real screen/hooks/RHF; only HTTP responses are synthetic.
test('same-turn registered weekly edit fences pending output before a render', async () => {
  const t = transport(), pending = deferred();
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic',
    facilities: [{ id: 'synthetic-facility', name: 'Synthetic fixture' }] } }));
  t.on('get', '/orders', () => ({ data: { orders: [] } }));
  t.on('get', '/orders/by-line-date', () => ({ data: { orders: [] } }));
  t.on('get', '/outputs/weekly-weight', () => pending.promise);
  const probe = { component: 'WeeklyWeightOutputScreen', bindings: ['conditionForm', 'downloadWeeklyWeight', 'downloading'] };
  const h = await mount('src/pages/weekly-weight-output.tsx', { api: t.api, probe });
  try {
    await h.settle();
    let operation;
    await h.settle(() => probe.current.conditionForm.setValue('week', '2026-W41'));
    await h.settle(() => { operation = probe.current.downloadWeeklyWeight(probe.current.conditionForm.getValues()); });
    await h.waitFor(() => t.calls.some(call => call.url === '/outputs/weekly-weight'), 'actual output dispatched');
    await h.settle(() => {
      probe.current.conditionForm.setValue('week', '2026-W42', { shouldDirty: true });
      pending.resolve({ data: new Blob(['obsolete']), headers: {} });
    });
    await operation;
    assert.deepEqual(h.downloads, []);
    assert.equal(probe.current.downloading, false);
  } finally { await h.close(); }
});
