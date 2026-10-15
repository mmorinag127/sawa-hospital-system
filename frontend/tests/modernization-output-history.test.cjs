const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Authored metadata fixtures, not records of actual download completion.
const props = () => ({ orderId: 'synthetic-order', documentId: 'synthetic-pdf', workflowRevision: 7,
  currentOutputBundleId: 'recorded-current', route: '/orders/synthetic-order/workflow-v2', inputKey: [0], captureOwner: () => () => true });
const entry = (id, current = false) => ({ output_bundle_id: id, order_id: 'synthetic-order',
  source_saved_sheet_id: 'synthetic-sheet', source_bagging_result_id: 'synthetic-bag', template_version_id: null,
  created_at: '2026-02-15T09:00:00.123456+00:00', current, validation: 'validated', blocked_reasons: [] });
const page = (number = 1) => ({ order_id: 'synthetic-order', workflow_revision: 7, current_output_bundle_id: 'recorded-current',
  page: number, limit: 25, total: 26, entries: number === 1
    ? [entry('newest-not-current'), entry('recorded-current', true)] : [entry('old-history')] });

test('actual common history table distinguishes generation/history, exact lineage, UTC and no hidden actions', async () => {
  const t = transport(); t.on('get', '/outputs/history', () => ({ data: page() }));
  const h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api, props: props() });
  try {
    await h.settle();
    const table = h.document.querySelector('table'); assert.ok(table);
    assert.deepEqual([...table.querySelectorAll('th')].map(node => node.textContent), ['生成日時', '参照', '検証']);
    assert.match(table.getAttribute('aria-label'), /ダウンロード完了記録ではありません/);
    assert.match(table.textContent, /過去（参照のみ）/); assert.match(table.textContent, /現行/);
    assert.match(table.textContent, /2026-02-15T09:00:00\.123456\+00:00（UTC）/);
    await h.click(table.querySelector('summary'));
    assert.match(table.querySelector('details').textContent, /synthetic-sheet/);
    assert.match(table.querySelector('details').textContent, /synthetic-bag/);
    assert.equal(table.querySelectorAll('a,button').length, 0);
    assert.deepEqual(h.downloads, []);
    assert.deepEqual(t.calls.map(call => call.url), ['/outputs/history']);
  } finally { await h.close(); }
});

test('real DataTable page change displays RequestState until the owned new page, never relabels old rows', async () => {
  const pending = deferred(), t = transport();
  t.on('get', '/outputs/history', call => call.config.params.page === 1 ? { data: page() } : pending.promise);
  const h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api, props: props() });
  try {
    await h.waitFor(() => h.document.querySelector('table'), 'initial history page');
    await h.click(h.document.querySelector('button[aria-label="次のページ"]'));
    assert.equal(t.calls.at(-1).config.params.page, 2);
    assert.equal(h.document.querySelector('table'), null);
    assert.ok(h.document.querySelector('[role="status"]'));
    await h.settle(() => pending.resolve({ data: page(2) }));
    await h.waitFor(() => h.document.querySelector('table'), 'owned second history page');
    assert.match(h.document.querySelector('table').textContent, /old-history/);
    assert.doesNotMatch(h.document.querySelector('table').textContent, /newest-not-current/);
  } finally { await h.close(); }
});

for (const damage of ['order', 'revision', 'current', 'blank-id', 'space-id', 'duplicate', 'owner', 'lineage', 'timestamp', 'validation']) {
  test(`history invalid ${damage} explicitly stops rather than hides/adopts data`, async () => {
    const payload = page();
    if (damage === 'order') payload.order_id = 'other';
    if (damage === 'revision') payload.workflow_revision = 8;
    if (damage === 'current') payload.current_output_bundle_id = 'newest-not-current';
    if (damage === 'blank-id') payload.entries[0].output_bundle_id = '';
    if (damage === 'space-id') payload.entries[0].output_bundle_id = ' padded ';
    if (damage === 'duplicate') payload.entries[1] = { ...payload.entries[0] };
    if (damage === 'owner') payload.entries[0].order_id = 'other';
    if (damage === 'lineage') payload.entries[0].source_saved_sheet_id = '';
    if (damage === 'timestamp') payload.entries[0].created_at = '2026-02-15';
    if (damage === 'validation') payload.entries[0].blocked_reasons = ['bad-digest'];
    const t = transport(); t.on('get', '/outputs/history', () => ({ data: payload }));
    const h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api, props: props() });
    try { await h.settle(); assert.equal(h.document.querySelector('table'), null); assert.ok(h.document.querySelector('[role="alert"]')); assert.deepEqual(h.downloads, []); }
    finally { await h.close(); }
  });
}

test('blocked digest/timestamp rows remain visible with exact blocker and aware timestamp stays verbatim', async () => {
  const payload = page(); payload.entries[0] = { ...entry('damaged'), created_at: null,
    validation: 'blocked', blocked_reasons: ['output_bundle_artifact_invalid', 'output_bundle_created_at_invalid'] };
  payload.entries[1].created_at = '2026-02-15T09:00:00.123456+09:00';
  const t = transport(); t.on('get', '/outputs/history', () => ({ data: payload }));
  const h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api, props: props() });
  try {
    await h.settle(); const table = h.document.querySelector('table'); assert.ok(table);
    assert.match(table.textContent, /日時不正（要確認）/); assert.match(table.textContent, /output_bundle_artifact_invalid/);
    assert.match(table.textContent, /2026-02-15T09:00:00\.123456\+09:00（UTC\+09:00）/);
    assert.equal(table.querySelectorAll('tbody tr').length, 2);
  } finally { await h.close(); }
});

for (const boundary of ['order', 'pdf', 'revision', 'input', 'session', 'unmount']) {
  for (const outcome of ['success', 'error']) test(`history late ${outcome} after ${boundary} never adopts`, async () => {
    const old = deferred(), current = deferred(), t = transport(); let calls = 0;
    t.on('get', '/outputs/history', () => ++calls === 1 ? old.promise : current.promise);
    const p = props(), h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api, props: p });
    try {
      if (boundary === 'unmount') await h.unmount();
      else if (boundary === 'session') await h.session();
      else {
        const next = { ...p };
        if (boundary === 'order') next.orderId = 'other-order';
        if (boundary === 'pdf') next.documentId = 'other-pdf';
        if (boundary === 'revision') next.workflowRevision = 8;
        if (boundary === 'input') next.inputKey = [1];
        await h.render(next);
      }
      await h.settle(() => outcome === 'success' ? old.resolve({ data: page() }) : old.reject(new Error('late-secret-error')));
      assert.equal(h.document.querySelector('table'), null);
      assert.doesNotMatch(h.document.body.textContent, /newest-not-current|late-secret-error/);
    } finally { await h.close(); }
  });
}

test('missing reference/revision has explicit prerequisite state and performs zero transport', async () => {
  const t = transport(), h = await mount('src/components/OutputHistoryPanel.tsx', { api: t.api,
    props: { ...props(), currentOutputBundleId: null, workflowRevision: null } });
  try { assert.equal(t.calls.length, 0); assert.ok(h.document.querySelector('[role="alert"]')); }
  finally { await h.close(); }
});
