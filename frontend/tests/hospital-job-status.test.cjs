const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Real page and shared JobStatus with synthetic HTTP/session, not live/auth/OCR proof.
const ORDER = 'synthetic-order', BASE = `/orders/${ORDER}/workflow-v2`;
const clone = value => JSON.parse(JSON.stringify(value));
function fixtureWorkflow() {
  return { order_id: ORDER, revision: 7, state: 'ocr_failed', headline: 'Existing workflow headline',
    primary_action: 'run_ocr', selected_ocr_result_id: null, blockers: [], warnings: [],
    ocr_job: { ocr_job_id: 'selected-job', order_id: ORDER, status: 'failed',
      workflow_attempt_id: 'attempt-A', progress_step: 2, progress_total: 5, processing_stage: 'recorded-stage',
      error_message: 'Existing OCR detail' },
    common_job_status: { domain: 'hospital', jobId: 'selected-job', state: 'failed',
      sourceState: 'failed', sourceLabel: '失敗', inputVersion: ' raw version ',
      inputReference: ' raw input ', attemptIdentity: 'attempt-A', artifactReference: ' raw artifact ',
      failure: { code: null, message: 'Recorded job failure' } },
  };
}
async function setup() {
  const t = transport(), state = { value: fixtureWorkflow(), pending: null };
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', '/facilities', () => ({ data: { source: 'canonical_json', revision: 'synthetic', facilities: [] } }));
  t.on('get', BASE, () => state.pending ? state.pending.promise : { data: clone(state.value) });
  t.on('get', `${BASE}/ocr-results`, () => ({ data: { results: [] } }));
  t.on('get', `${BASE}/inspection`, () => ({ data: { saved_sheet: null, pre_save_checks: {} } }));
  t.on('get', `/orders/${ORDER}`, () => ({ data: { id: ORDER,
    current_version: { document_id: 'pdf-A' }, versions: [{ document_id: 'pdf-A', is_current: true }] } }));
  t.on('get', `/orders/${ORDER}/week-options`, () => ({ data: { options: [] } }));
  t.on('get', `/orders/${ORDER}/document`, () => ({ data: new Blob(['synthetic component bytes']) }));
  const probe = { component: 'OrderWorkflowV2Page', bindings: [
    'workflow', 'commonJobStatus', 'refreshAll', 'effectiveSelectedDocumentId',
  ] };
  const h = await mount('src/pages/orders/[id]/workflow-v2.tsx', {
    api: t.api, probe, route: `/orders/${ORDER}/workflow-v2`,
  });
  try {
    await h.waitFor(() => probe.current?.workflow?.revision === 7
      && probe.current.effectiveSelectedDocumentId === 'pdf-A');
    return { h, probe, t, state };
  } catch (error) { await h.close(); throw error; }
}
async function refresh(f, value) {
  f.state.value = clone(value);
  await f.h.settle(() => f.probe.current.refreshAll());
}
function statusText(f) {
  return Array.from(f.h.document.querySelectorAll('[role="status"]')).map(node => node.textContent).join(' ');
}

test('live workflow renders exact received owned record and retains existing progress/error/detail', async () => {
  const f = await setup();
  try {
    const { h, probe } = f;
    assert.equal(probe.current.commonJobStatus, probe.current.workflow.common_job_status);
    assert.deepEqual(probe.current.commonJobStatus, fixtureWorkflow().common_job_status);
    assert.match(statusText(f), /処理失敗.*Recorded job failure/);
    assert.ok(h.document.body.textContent.includes('Existing workflow headline'));
    assert.ok(h.document.body.textContent.includes('Existing OCR detail'));
    const details = Array.from(h.document.querySelectorAll('details')).find(node => node.textContent.includes('処理の詳細'));
    assert.ok(details);
    for (const text of ['selected-job', ' raw version ', ' raw input ', 'attempt-A', ' raw artifact '])
      assert.ok(details.textContent.includes(text));
    const raw = fixtureWorkflow();
    raw.state = 'ocr_running';
    raw.ocr_job.workflow_attempt_id = 0;
    Object.assign(raw.common_job_status, { attemptIdentity: 0, inputVersion: 0, inputReference: null,
      artifactReference: null, state: null, sourceState: ' UNKNOWN ', sourceLabel: ' UNKNOWN ', failure: null });
    await refresh(f, raw);
    assert.equal(probe.current.commonJobStatus, probe.current.workflow.common_job_status);
    assert.deepEqual(probe.current.commonJobStatus, raw.common_job_status);
    assert.ok(h.document.body.textContent.includes('2/5 recorded-stage'));
    assert.match(statusText(f), /状態未解決.* UNKNOWN /);
    assert.ok(!statusText(f).includes('処理完了'));
    const done = fixtureWorkflow();
    done.state = 'ocr_completed'; done.ocr_job.status = 'done';
    Object.assign(done.common_job_status, { state: 'succeeded', sourceState: 'done', sourceLabel: '完了', failure: null });
    await refresh(f, done);
    assert.equal(probe.current.commonJobStatus, probe.current.workflow.common_job_status);
    assert.deepEqual(probe.current.commonJobStatus, done.common_job_status);
    assert.match(statusText(f), /処理完了 \(完了\)/);
  } finally { await f.h.close(); }
});

test('live workflow null/absent projection never borrows confirmed state or legacy order status', async () => {
  const f = await setup();
  try {
    for (const absent of [false, true]) {
      const value = fixtureWorkflow();
      value.state = 'confirmed'; value.ocr_job.status = 'done';
      value.common_job_status = null;
      if (absent) delete value.common_job_status;
      await refresh(f, value);
      assert.equal(f.probe.current.commonJobStatus, null);
      assert.ok(f.h.document.body.textContent.includes('ジョブ記録未取得'));
      assert.ok(!statusText(f).includes('処理完了'));
    }
    const missing = fixtureWorkflow();
    missing.ocr_job.workflow_attempt_id = null;
    Object.assign(missing.common_job_status, { attemptIdentity: null, inputVersion: null });
    await refresh(f, missing);
    assert.match(statusText(f), /入力版未記録・実行ID未記録/);
    assert.equal(f.probe.current.commonJobStatus.inputVersion, null);
  } finally { await f.h.close(); }
});

test('live workflow refuses mismatched order job attempt and domain without rendering their record', async () => {
  const f = await setup();
  try {
    for (const change of [
      value => { value.common_job_status.jobId = 'foreign-job'; },
      value => { value.common_job_status.attemptIdentity = 'foreign-attempt'; },
      value => { value.common_job_status.domain = 'school'; },
      value => { value.ocr_job.order_id = 'foreign-order'; },
      value => { value.order_id = 'foreign-order'; },
      value => { delete value.ocr_job.order_id; },
      value => { value.ocr_job = null; },
    ]) {
      const value = fixtureWorkflow(); change(value);
      await refresh(f, value);
      assert.equal(f.probe.current.commonJobStatus, null);
      assert.ok(f.h.document.body.textContent.includes('ジョブ記録未取得'));
      assert.ok(!statusText(f).includes('Recorded job failure'));
    }
  } finally { await f.h.close(); }
});

test('live refresh preserves selected job when old expected-job response carries a successor record', async () => {
  const f = await setup();
  try {
    const original = f.probe.current.workflow;
    const pending = deferred(); f.state.pending = pending;
    let request;
    await f.h.settle(() => { request = f.probe.current.refreshAll(() => true, {
      jobId: 'selected-job', attemptId: 'attempt-A',
    }); });
    const newer = fixtureWorkflow();
    newer.ocr_job.ocr_job_id = newer.common_job_status.jobId = 'successor-job';
    newer.ocr_job.workflow_attempt_id = newer.common_job_status.attemptIdentity = 'attempt-B';
    newer.common_job_status.state = 'succeeded';
    await f.h.settle(() => pending.resolve({ data: newer }));
    assert.equal(await request, false);
    assert.equal(f.probe.current.workflow, original);
    assert.equal(f.probe.current.commonJobStatus, original.common_job_status);
    assert.match(statusText(f), /処理失敗/);
    assert.ok(!statusText(f).includes('処理完了'));
  } finally { await f.h.close(); }
});
