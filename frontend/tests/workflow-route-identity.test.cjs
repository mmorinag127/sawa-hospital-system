const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Mounted production page; only router/session/HTTP are synthetic component inputs.
const PAGE = 'src/pages/orders/[id]/workflow-v2.tsx';
const ORDER = 'explicit-order', OTHER = 'second-explicit-order';
const reads = id => [`/orders/${id}/workflow-v2/display?scope=initial`, `/orders/${id}`];
const orderCalls = t => t.calls.filter(call => /^\/(orders|outputs)(\/|\?)/.test(call.url));
const value = (id, revision = 7) => ({ order_id: id, revision, state: 'ocr_failed',
  headline: `Owned ${id}`, blockers: [], warnings: [], selected_ocr_result_id: null,
  ocr_job: { order_id: id, ocr_job_id: `job-${id}`, workflow_attempt_id: `attempt-${id}`, status: 'failed' } });

async function setup(routerState, route = `/hospital/orders/${ORDER}/workflow-v2`) {
  const t = transport(), pending = new Map();
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic', facilities: [] } }));
  for (const id of [ORDER, OTHER]) {
    const [inspection, order] = reads(id);
    t.onWorkflowInspection(inspection, () => pending.get(id)?.promise || ({ data: { saved_sheet: null, pre_save_checks: {},
      workflow: value(id),
      ocr_candidates: require('./helpers/ocr-candidates.cjs')(id, [], value(id)) } }));
    t.on('get', order, () => ({ data: { id, current_version: { document_id: `pdf-${id}` },
      versions: [{ document_id: `pdf-${id}`, is_current: true }] } }));
    t.on('get', `${order}/week-options`, () => ({ data: { options: [] } }));
    t.on('get', `${order}/document`, () => ({ data: new Blob(['synthetic component bytes']) }));
  }
  const probe = { component: 'OrderWorkflowV2Page', bindings: ['orderId', 'workflow', 'refreshAll',
    'generateSheetFromSelectedOcr', 'runBagging', 'finalConfirm', 'saveSheet', 'deleteOcr',
    'effectiveSelectedDocumentId', 'capturePageOwner', 'ownsPageInput'] };
  const h = await mount(PAGE, { api: t.api, probe, route, routerState });
  return { h, probe, t, pending };
}

const invalid = [
  ['unready explicit query', { isReady: false, query: { id: ORDER } }],
  ['unready template path', { isReady: false, query: {}, asPath: '/orders/[id]/workflow-v2' }],
  ['ready template query', { isReady: true, query: { id: '[id]' }, asPath: '/orders/[id]/workflow-v2' }],
  ['ready missing query', { isReady: true, query: {} }],
  ['ready array query', { isReady: true, query: { id: [ORDER] } }],
  ['ready empty query', { isReady: true, query: { id: '' } }],
  ['ready null query', { isReady: true, query: { id: null } }],
];
for (const [name, routerState] of invalid) {
  test(`mounted ${name} cannot read or act on an order despite a concrete URL`, async () => {
    const f = await setup(routerState);
    try {
      assert.equal(f.probe.current.orderId, '');
      assert.deepEqual(orderCalls(f.t), []);
      assert.equal(f.probe.current.ownsPageInput(f.probe.current.capturePageOwner()), false);
      await f.h.settle(async () => {
        await f.probe.current.refreshAll();
        await f.probe.current.generateSheetFromSelectedOcr();
        await f.probe.current.runBagging();
        await f.probe.current.finalConfirm();
        await f.probe.current.saveSheet();
        await f.probe.current.deleteOcr('synthetic-result');
      });
      assert.equal(f.probe.current.workflow, null);
      assert.deepEqual(orderCalls(f.t), []);
    } finally { await f.h.close(); }
  });
}

test('router readiness and explicit query activate the correct order without URL fallback', async () => {
  const f = await setup({ isReady: false, query: { id: ORDER }, asPath: '/orders/[id]/workflow-v2' });
  try {
    assert.deepEqual(orderCalls(f.t), []);
    f.h.nav.router.isReady = true;
    await f.h.render();
    await f.h.waitFor(() => f.probe.current.workflow?.order_id === ORDER
      && f.probe.current.effectiveSelectedDocumentId === `pdf-${ORDER}`);
    assert.ok(f.t.calls.some(call => call.method === 'get' && call.url === reads(ORDER)[0]));
    assert.ok(!f.t.calls.some(call => call.method === 'get' && call.url === reads(ORDER)[1]));
    assert.ok(orderCalls(f.t).every(call => call.url.startsWith(`/orders/${ORDER}`)));
    assert.equal(f.probe.current.orderId, ORDER);
    assert.equal(f.probe.current.ownsPageInput(f.probe.current.capturePageOwner()), true);
    assert.equal(f.probe.current.workflow.revision, 7);
  } finally { await f.h.close(); }
});

test('ready query identity wins over a different concrete prefixed pathname', async () => {
  const f = await setup({ isReady: true, query: { id: OTHER }, asPath: `/orders/${ORDER}/workflow-v2` });
  try {
    await f.h.waitFor(() => f.probe.current.workflow?.order_id === OTHER);
    assert.equal(f.probe.current.orderId, OTHER);
    assert.ok(orderCalls(f.t).length >= 3);
    assert.ok(orderCalls(f.t).every(call => call.url.startsWith(`/orders/${OTHER}`)));
  } finally { await f.h.close(); }
});

test('readiness loss retires old reads and actions; a subsequent explicit order keeps its owner', async () => {
  const f = await setup({ isReady: true, query: { id: ORDER } });
  try {
    await f.h.waitFor(() => f.probe.current.effectiveSelectedDocumentId === `pdf-${ORDER}`);
    await f.h.settle();
    const original = f.probe.current.workflow, old = f.probe.current;
    const owner = old.capturePageOwner(), waiting = deferred();
    f.pending.set(ORDER, waiting);
    let refresh;
    await f.h.settle(() => { refresh = old.refreshAll(); });
    f.h.nav.router.isReady = false;
    await f.h.render();
    const stoppedCount = orderCalls(f.t).length;
    assert.equal(f.probe.current.orderId, '');
    assert.equal(f.probe.current.ownsPageInput(owner), false);
    await f.h.settle(async () => {
      await old.generateSheetFromSelectedOcr();
      await f.probe.current.generateSheetFromSelectedOcr();
      await f.probe.current.refreshAll();
      waiting.resolve({ data: {workflow: value(ORDER, 99)} });
      await refresh;
    });
    assert.equal(orderCalls(f.t).length, stoppedCount);
    assert.equal(f.probe.current.workflow, original);
    Object.assign(f.h.nav.router, { isReady: true, query: { id: OTHER }, asPath: `/orders/${OTHER}/workflow-v2` });
    await f.h.render();
    await f.h.waitFor(() => f.probe.current.workflow?.order_id === OTHER
      && f.probe.current.effectiveSelectedDocumentId === `pdf-${OTHER}`);
    const current = f.probe.current.workflow, finalCount = orderCalls(f.t).length;
    await f.h.settle(async () => { await old.refreshAll(); await old.generateSheetFromSelectedOcr(); });
    assert.equal(orderCalls(f.t).length, finalCount);
    assert.equal(f.probe.current.workflow, current);
    assert.equal(current.revision, 7);
    assert.equal(f.probe.current.ownsPageInput(owner), false);
  } finally { await f.h.close(); }
});
