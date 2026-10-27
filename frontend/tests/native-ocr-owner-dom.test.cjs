const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Production React page/hooks/status, authored HTTP/session/timer only. No live OCR proof.
const PAGE = 'src/pages/orders/[id]/workflow-v2.tsx';
const ORDER = 'synthetic-order', BASE = `/orders/${ORDER}/workflow-v2`;
const clone = value => JSON.parse(JSON.stringify(value));
function value(kind = 'native', terminal = false) {
  return { order_id: ORDER, revision: terminal ? 8 : 7, state: terminal ? 'ocr_completed' : 'ocr_running',
    headline: terminal ? 'Owned terminal' : 'Owned running', blockers: [], warnings: [], selected_ocr_result_id: null,
    ocr_job: { order_id: ORDER, ocr_job_id: 'owned-job', workflow_attempt_id: kind === 'workflow' ? 'same-id' : null,
      ...(kind === 'native' ? { native_ingest: { mode: 'hakodate', intake_protocol: 'native_ingest_v2', execution_id: 'same-id' } } : {}),
      status: terminal ? 'done' : 'running', result_state: terminal ? 'evidence_ready' : 'processing' },
    common_job_status: { domain: 'hospital', jobId: 'owned-job', attemptIdentity: 'same-id',
      inputVersion: 'source-hash', inputReference: 'source.pdf', artifactReference: null,
      sourceState: terminal ? 'done' : 'running', sourceLabel: terminal ? '完了' : '実行中',
      state: terminal ? 'succeeded' : 'running', failure: null } };
}

async function setup(initial = value()) {
  const t = transport(), state = { value: clone(initial), pending: null }, timers = new Map();
  let nextTimer = 0;
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'authored', facilities: [] } }));
  t.on('get', BASE, () => {
    if (state.pending) { const pending = state.pending; state.pending = null; return pending.promise; }
    return { data: clone(state.value) };
  });
  t.onWorkflowInspection(`${BASE}/display?scope=summary`, () => ({ data: { saved_sheet: null,
    workflow: clone(state.value), pre_save_checks: {}, ocr_candidates: require('./helpers/ocr-candidates.cjs')(ORDER, [], state.value) } }));
  t.on('get', `${BASE}/ocr-progress`, () => {
    if (state.pending) { const pending = state.pending; state.pending = null; return pending.promise; }
    return { data: { order_id: state.value.order_id, workflow_revision: state.value.revision,
      state: state.value.state, ocr_job: clone(state.value.ocr_job), common_job_status: clone(state.value.common_job_status) } };
  });
  t.on('get', `/orders/${ORDER}`, () => ({ data: { id: ORDER, current_version: { document_id: 'pdf-A' },
    versions: [{ document_id: 'pdf-A', is_current: true }, { document_id: 'pdf-B', is_current: false }] } }));
  t.on('get', `/orders/${ORDER}/week-options`, () => ({ data: { options: [] } }));
  for (const suffix of ['', '?document_id=pdf-A', '?document_id=pdf-B'])
    t.on('get', `/orders/${ORDER}/document${suffix}`, () => ({ data: new Blob(['authored component bytes']) }));
  const probe = { component: 'OrderWorkflowV2Page', bindings: ['workflow', 'commonJobStatus', 'refreshAll',
    'effectiveSelectedDocumentId', 'setSelectedDocumentId', 'error', 'beginWorkflowEditorEdit', 'editorVersion',
    'markSheetDirty', 'pendingSheetCellEditsRef', 'workflowRevisionRef'] };
  const h = await mount(PAGE, { api: t.api, probe, route: `/orders/${ORDER}/workflow-v2`,
    component(loader) {
      const setInterval = window.setInterval.bind(window), clearInterval = window.clearInterval.bind(window);
      window.setInterval = (fn, delay, ...args) => {
        if (delay !== 3000) return setInterval(fn, delay, ...args);
        const id = --nextTimer; timers.set(id, fn); return id;
      };
      window.clearInterval = id => { if (!timers.delete(id)) clearInterval(id); };
      return loader.load(PAGE, probe).default;
    } });
  try {
    await h.waitFor(() => probe.current?.workflow?.revision === 7
      && probe.current.effectiveSelectedDocumentId === 'pdf-A');
    await h.settle();
    return { h, probe, t, state, timers,
      async tick(fn = [...timers.values()][0]) { assert.equal(typeof fn, 'function'); await h.settle(() => fn()); } };
  } catch (error) { await h.close(); throw error; }
}
const noWrites = f => assert.ok(f.t.calls.every(call => call.method === 'get'));

for (const kind of ['native', 'workflow']) test(`actual ${kind} page automatically reads owned terminal and common status`, async () => {
  const f = await setup(value(kind));
  try {
    assert.equal(f.timers.size, 1);
    assert.equal(f.probe.current.commonJobStatus, f.probe.current.workflow.common_job_status);
    assert.match(f.h.document.body.textContent, /same-id/);
    f.state.value = value(kind, true);
    await f.tick();
    await f.h.waitFor(() => f.probe.current.workflow.state === 'ocr_completed');
    assert.equal(f.probe.current.workflow.headline, 'Owned terminal');
    assert.equal(f.probe.current.commonJobStatus.state, 'succeeded');
    assert.match(f.h.document.body.textContent, /処理完了/);
    assert.equal(f.timers.size, 0);
    noWrites(f);
  } finally { await f.h.close(); }
});

const malformed = {
  missing: v => { delete v.ocr_job.native_ingest; },
  null: v => { v.ocr_job.native_ingest = null; },
  unknownMode: v => { v.ocr_job.native_ingest.mode = 'other'; },
  unknownProtocol: v => { v.ocr_job.native_ingest.intake_protocol = 'unknown'; },
  missingExecution: v => { delete v.ocr_job.native_ingest.execution_id; },
  emptyExecution: v => { v.ocr_job.native_ingest.execution_id = ' '; },
  nonStringExecution: v => { v.ocr_job.native_ingest.execution_id = 0; },
  mixed: v => { v.ocr_job.workflow_attempt_id = 'same-id'; },
  mixedZero: v => { v.ocr_job.workflow_attempt_id = 0; },
  foreignOrder: v => { v.ocr_job.order_id = 'other-order'; },
  missingJob: v => { delete v.ocr_job.ocr_job_id; },
};
for (const [name, damage] of Object.entries(malformed)) test(`actual malformed native ${name} stops polling`, async () => {
  const initial = value(); damage(initial);
  const f = await setup(initial);
  try {
    assert.equal(f.timers.size, 0);
    assert.match(f.probe.current.error, /未記録/);
    assert.equal(f.probe.current.commonJobStatus, null);
    const count = f.t.calls.length;
    await f.h.settle(); assert.equal(f.t.calls.length, count);
    noWrites(f);
  } finally { await f.h.close(); }
});

for (const name of ['job', 'jobOrder', 'workflowOrder', 'execution', 'mode', 'protocol', 'kind', 'workflowToNative'])
  test(`actual polling rejects returned ${name} replacement, including equal-string cross-kind identity`, async () => {
    const kind = name === 'workflowToNative' ? 'workflow' : 'native';
    const f = await setup(value(kind));
    try {
      const original = f.probe.current.workflow, tick = [...f.timers.values()][0];
      const changed = value(kind, true);
      if (name === 'job') changed.ocr_job.ocr_job_id = 'other-job';
      if (name === 'jobOrder') changed.ocr_job.order_id = 'other-order';
      if (name === 'workflowOrder') changed.order_id = 'other-order';
      if (name === 'execution') changed.ocr_job.native_ingest.execution_id = 'other-execution';
      if (name === 'mode') changed.ocr_job.native_ingest.mode = 'other';
      if (name === 'protocol') changed.ocr_job.native_ingest.intake_protocol = 'other';
      if (name === 'kind') { delete changed.ocr_job.native_ingest; changed.ocr_job.workflow_attempt_id = 'same-id'; }
      f.state.value = name === 'workflowToNative' ? value('native', true) : changed;
      await f.tick(tick);
      assert.equal(f.probe.current.workflow, original);
      assert.match(f.probe.current.error, /jobが変更/);
      const count = f.t.calls.length;
      await f.tick(tick); assert.equal(f.t.calls.length, count);
      assert.equal(f.timers.size, 0);
      noWrites(f);
    } finally { await f.h.close(); }
  });

for (const retirement of ['route', 'document', 'order', 'execution', 'job', 'jobOrder', 'workflowOrder', 'mode'])
  for (const outcome of ['success', 'error']) test(`native ${retirement} retires pending ${outcome} response`, async () => {
    const f = await setup();
    try {
      const pending = deferred(), oldTick = [...f.timers.values()][0];
      f.state.pending = pending;
      await f.tick(oldTick);
      if (retirement === 'route') {
        f.h.nav.router.asPath += '?other-view=1'; await f.h.render();
      } else if (retirement === 'document') {
        await f.h.settle(() => f.probe.current.setSelectedDocumentId('pdf-B'));
      } else if (retirement === 'order') {
        f.h.nav.router.isReady = false; await f.h.render();
      } else {
        const changed = value(); changed.revision = 8;
        if (retirement === 'execution') changed.ocr_job.native_ingest.execution_id = 'new-execution';
        if (retirement === 'job') changed.ocr_job.ocr_job_id = 'new-job';
        if (retirement === 'jobOrder') changed.ocr_job.order_id = 'other-order';
        if (retirement === 'workflowOrder') changed.order_id = 'other-order';
        if (retirement === 'mode') changed.ocr_job.native_ingest.mode = 'unknown';
        f.state.value = changed;
        const before = f.probe.current.workflow;
        if (retirement === 'workflowOrder') {
          await f.h.settle(() => assert.rejects(f.probe.current.refreshAll(), /注文が一致/));
          assert.equal(f.probe.current.workflow, before);
        } else await f.h.settle(() => f.probe.current.refreshAll());
      }
      const current = f.probe.current.workflow, currentError = f.probe.current.error;
      const count = f.t.calls.length;
      await f.h.settle(() => {
        if (outcome === 'success') pending.resolve({ data: { ...value('native', true), revision: 99, headline: 'Stale result' } });
        else pending.reject(Error('stale request failure'));
      });
      assert.equal(f.probe.current.workflow, current);
      assert.equal(f.probe.current.error, currentError);
      await f.tick(oldTick);
      if (retirement === 'workflowOrder') {
        assert.equal(f.t.calls.length, count + 1);
        assert.equal(f.probe.current.workflow, current);
        assert.match(f.probe.current.error, /jobが変更/);
        assert.equal(f.timers.size, 0);
      } else assert.equal(f.t.calls.length, count);
      noWrites(f);
    } finally { await f.h.close(); }
  });

test('native polling input fence preserves dirty operator edits and allows a later owned progress read', async () => {
  const f = await setup();
  try {
    const pending = deferred(); f.state.pending = pending;
    await f.tick();
    await f.h.settle(() => {
      f.probe.current.markSheetDirty();
      f.probe.current.pendingSheetCellEditsRef.current.set('authored-cell', {rowIndex:0,colIndex:0,value:'0'});
    });
    const editor = { ...f.probe.current.editorVersion('sheet') };
    await f.h.settle(() => pending.resolve({ data: { ...value('native', true), revision: 99 } }));
    assert.equal(f.probe.current.workflow.revision, 7);
    f.state.value = { ...value(), revision: 8 };
    await f.tick();
    assert.equal(f.probe.current.workflow.revision, 8);
    assert.deepEqual(f.probe.current.editorVersion('sheet'), editor);
    assert.equal(f.probe.current.pendingSheetCellEditsRef.current.get('authored-cell').value, '0');
    noWrites(f);
  } finally { await f.h.close(); }
});
