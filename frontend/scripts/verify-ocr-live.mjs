import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, renameSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { isAllowedOutputSourceOrigin, STG_OUTPUT_SOURCE_ORIGIN } from './output-source-live-origin.mjs';
import { firstJsonRequest } from './ocr-first-request.mjs';

const allowedMethods = new Set(['GET', 'HEAD']);
const phases = new Set([
  'validate-artifact', 'launch-browser', 'configure-private-context', 'open-workflow',
  'verify-deploy-version', 'await-workflow-reads', 'verify-live-identity',
  'verify-visible-ocr-image', 'capture-visible-ocr-image', 'capture-workflow-page',
  'validate-readonly', 'finished', 'open-ocr-detail', 'verify-state-preserved', 'verify-raw-json',
  'await-network-idle', 'verify-review-state', 'open-step2', 'verify-review-controls', 'verify-evidence-text',
]);

export function diagnosticTimeline(result, now = () => Date.now()) {
  let active;
  const end = () => {
    if (!active) return;
    active.endedAt = now();
    active.elapsedMs = active.endedAt - active.startedAt;
    active = null;
  };
  return {
    begin(check) {
      assert.ok(phases.has(check));
      end();
      result.phase = check;
      active = { check, startedAt: now() };
      (result.timings ||= []).push(active);
    },
    end,
    fail(error) {
      end();
      result.failure = { check: phases.has(result.phase) ? result.phase : 'unknown-phase',
        type: error?.name === 'TimeoutError' ? 'TimeoutError'
          : error?.name === 'AssertionError' ? 'AssertionError' : 'other' };
      result.failure.assertion = safeAssertionName(error);
    },
  };
}

export function safeAssertionName(error) {
  const location = /(?:\/|\\)(verify-(?:ocr|recognition-review)-live\.mjs):(\d+):\d+/.exec(error?.stack || '');
  return ['AssertionError', 'TimeoutError'].includes(error?.name) && location ? `${location[1]}:L${location[2]}` : null;
}

export async function gateReadonlyResource(route, {origin, allowedExternalUrls, externalGate, onWrite}) {
  const request = route.request();
  if (!allowedMethods.has(request.method())) {
    onWrite();
    return route.abort('blockedbyclient');
  }
  if (new URL(request.url()).origin === origin) return route.continue();
  // CSS/font/document loads must settle before browser actionability can complete.
  if (request.resourceType() !== 'image') return route.abort('blockedbyclient');
  await externalGate;
  if (allowedExternalUrls.has(request.url())) return route.continue();
  return route.abort('blockedbyclient');
}

export async function waitForWorkflowReadiness({page, expect, inspection, order, identity, revision}) {
  assert.equal(order.id, identity.orderId);
  assert.equal(order.current_version?.document_id, identity.documentId);
  assert.equal(inspection.order_id, identity.orderId);
  assert.equal(inspection.workflow.order_id, identity.orderId);
  assert.ok(Number.isInteger(revision));
  assert.equal(inspection.workflow.revision, revision);
  const headline = page.locator('.state-panel-main > p.subtle');
  await expect(headline).toBeVisible();
  await expect(headline).toHaveText(/\S/);
  await expect(headline).not.toHaveText('workflow-v2 を読み込み中です。');
}

export async function readLiveCandidate({ inspection, workflow, identity, page, readDetail, readCandidates, requireUnselected = false }) {
  assert.equal(inspection.order_id, identity.orderId);
  assert.equal(inspection.workflow.order_id, identity.orderId);
  assert.equal(inspection.workflow.revision, workflow.revision);
  assert.ok(Number.isInteger(workflow.revision));
  const pendingCandidates = readCandidates?.();
  await page.locator('.step-nav button').filter({ hasText: 'OCR選択' }).click();
  if (pendingCandidates) {
    const displayed = await pendingCandidates;
    assert.equal(displayed.order_id, identity.orderId);
    assert.equal(displayed.workflow.revision, workflow.revision);
    inspection = {...inspection, ocr_candidates: displayed.ocr_candidates};
  }
  assert.equal(inspection.ocr_candidates.order_id, identity.orderId);
  assert.equal(inspection.ocr_candidates.selected_ocr_result_id, workflow.selected_ocr_result_id);
  const matches = inspection.ocr_candidates.results.filter(item => item.ocr_result_id === identity.evidenceId);
  assert.equal(matches.length, 1);
  const candidate = matches[0];
  const selected = workflow.selected_ocr_result_id === identity.evidenceId;
  assert.equal(candidate.selected, selected);
  if (requireUnselected) assert.equal(workflow.selected_ocr_result_id, null);
  const card = page.locator('.ocr-card').filter({ hasText: identity.evidenceId });
  let evidence;
  if (candidate.detail_state !== 'not_loaded') {
    assert.notEqual(candidate.detail_state, 'not_loaded');
    assert.ok(candidate.execution_lineage);
    assert.equal(await card.getByRole('button', { name: '詳細を表示', exact: true }).count(), 0);
    evidence = candidate;
  } else {
    assert.equal(candidate.detail_state, 'not_loaded');
    // Attach rejection handling before click can wait or fail; rethrow when consumed.
    const pending = readDetail().then(data => ({ok: true, data}), error => ({ok: false, error}));
    await card.getByRole('button', { name: '詳細を表示', exact: true }).click();
    const receipt = await pending;
    if (!receipt.ok) throw receipt.error;
    evidence = receipt.data;
    assert.equal(evidence.workflow_revision, workflow.revision);
    assert.equal(evidence.selected, selected);
  }
  assert.equal(evidence.order_id, identity.orderId);
  assert.equal(evidence.ocr_result_id, identity.evidenceId);
  assert.ok(typeof identity.artifactDigest === 'string' && identity.artifactDigest.length > 0);
  assert.equal(evidence.artifact_digest, identity.artifactDigest);
  return evidence;
}

export function liveStateSnapshot(inspection, evidence) {
  // Signed URLs may renew on GET; persisted evidence, review and quantities must not change.
  const { overlay_url, sheet_review_base_url, workflow_revision, ...persistedEvidence } = evidence;
  return structuredClone({
    state: inspection.workflow.state,
    revision: inspection.workflow.revision,
    selectedId: inspection.workflow.selected_ocr_result_id,
    blockers: inspection.workflow.blockers,
    warnings: inspection.workflow.warnings,
    savedSheet: inspection.saved_sheet?.sheet ?? null,
    lineage: inspection.artifact_lineage,
    evidence: persistedEvidence,
  });
}

export function assertRecognitionAcceptance(evidence, workflow, identity) {
  const receipt = evidence.recognition_acceptance;
  assert.equal(receipt.version, 1);
  assert.equal(receipt.evidence_run_id, identity.evidenceId);
  assert.equal(receipt.artifact_digest, identity.artifactDigest);
  assert.ok(typeof identity.artifactDigest === 'string' && identity.artifactDigest.length > 0);
  assert.equal(evidence.artifact_digest, identity.artifactDigest);
  assert.match(identity.originalPayloadSha256, /^[a-f0-9]{64}$/);
  assert.equal(receipt.payload_sha256, identity.originalPayloadSha256);
  assert.deepEqual(receipt.selected_pdf_receipt, identity.selectedPdfReceipt);
  assert.equal(receipt.selected_pdf_receipt.order_id, identity.orderId);
  assert.equal(receipt.selected_pdf_receipt.document_id, identity.documentId);
  assert.equal(receipt.selected_pdf_receipt.sha256, identity.sourcePDFhash);
  assert.ok(Number.isInteger(receipt.workflow_revision) && receipt.workflow_revision < workflow.revision);
  assert.equal(receipt.source_receipt.workflow_revision, receipt.workflow_revision);
  assert.equal(receipt.source_receipt.recognition_evidence_run_id, identity.evidenceId);
  assert.deepEqual(receipt.source_receipt.selected_pdf_receipt, receipt.selected_pdf_receipt);
  assert.deepEqual(receipt.cells, identity.review.cells);
  assert.deepEqual(Object.keys(receipt.values).sort(), identity.review.cells.map(cell => cell.sheet_cell).sort());
  for (const value of Object.values(receipt.values)) assert.match(value, /^[0-9]*$/);
  assert.ok(typeof receipt.edited_by === 'string' && receipt.edited_by.trim().length > 0);
  assert.ok(typeof receipt.created_at === 'string' && receipt.created_at.length > 0);
  assert.deepEqual(evidence.artifact_manifest.operator_recognition_acceptance, receipt);
}

export async function captureFailureScreen(page, origin, orderId, output) {
  if (!page) return false;
  try {
    const url = new URL(page.url());
    if (url.origin !== origin || url.pathname !== `/hospital/orders/${encodeURIComponent(orderId)}/workflow-v2`) return false;
    if (await page.locator('input[type="password"], input[autocomplete="username"], input[autocomplete="current-password"]').count()) return false;
    if (await page.getByRole('button', { name: /ログイン|sign in|log in/i }).count()) return false;
    if (await page.locator('main .step-nav').count() !== 1) return false;
    await page.screenshot({ path: resolve(output, 'failure-workflow.png'), fullPage: true, timeout: 5000 });
    return true;
  } catch { return false; }
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function writeJson(path, value) {
  writeFileSync(path + '.part', JSON.stringify(value));
  renameSync(path + '.part', path);
}

function nonEmpty(value, name) {
  const normalized = String(value || '').trim();
  if (!normalized) throw new Error(`${name}-required`);
  return normalized;
}

export function sameOriginPathname(origin, value) {
  try {
    const url = new URL(value);
    return url.origin === origin ? url.pathname : null;
  } catch {
    return null;
  }
}

export function recordAllowedResponse(result, { origin, url, method, status }) {
  const pathname = sameOriginPathname(origin, url);
  if (!pathname || !allowedMethods.has(method) || !Number.isInteger(status)) return false;
  result.http.push({ phase: result.phase, method, pathname, status });
  return true;
}

export function sanitizedFailureCode(phase) {
  return 'browser-readonly-check-failed-at-' + (phases.has(phase) ? phase : 'unknown-phase');
}

export function settleResponse(waiter) {
  return waiter.then(response => ({ ok: true, response }), () => ({ ok: false, response: null }));
}

export function verificationCaseId(sourceCase) {
  assert.ok(sourceCase && typeof sourceCase === 'object' && !Array.isArray(sourceCase));
  const keys = ['source_order_id', 'case_id'].filter(key => Object.hasOwn(sourceCase, key));
  assert.equal(keys.length, 1, 'Exactly one verification case identity required');
  const value = sourceCase[keys[0]];
  assert.equal(typeof value, 'string');
  assert.match(value, /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/);
  return value;
}

export function assertNativeIngestOwner(sourceCase, workflow, evidence) {
  assert.equal(sourceCase.expected_execution_mode, 'native_ingest');
  const job = workflow.ocr_job;
  const native = job?.native_ingest;
  assert.equal(native?.mode, 'hakodate');
  assert.equal(native?.intake_protocol, 'native_ingest_v2');
  const terminal = native.terminal_receipt;
  const execution = evidence.payload_json?._ocr_execution;
  const pdf = execution?.selected_pdf_receipt;
  assert.equal(nonEmpty(job.ocr_job_id, 'job-id'), execution?.job_id);
  assert.equal(nonEmpty(native.execution_id, 'execution-id'), execution?.nonworkflow_execution_id);
  assert.equal(terminal?.execution_id, native.execution_id);
  assert.equal(terminal?.job_id, job.ocr_job_id);
  assert.equal(terminal?.order_id, workflow.order_id);
  assert.equal(terminal?.status, job.status);
  assert.equal(nonEmpty(terminal?.evidence_run_id, 'terminal-evidence-id'), evidence.id);
  assert.equal(execution?.evidence_run_id, evidence.id);
  assert.equal(job.order_id, workflow.order_id);
  assert.equal(pdf?.order_id, workflow.order_id);
  assert.equal(pdf?.document_id, nonEmpty(job.order_document_id, 'document-id'));
  assert.equal(pdf?.sha256, sourceCase.sha256);
  assert.equal(job.workflow_attempt_id, null);
  assert.ok(execution.workflow_attempt_id == null);
  assert.ok(terminal.workflow_attempt_id == null);
  assert.equal(evidence.producer_version, 'hakodate_best_method_pipeline');
  assert.equal(terminal.error, job.status === 'done' ? null : job.error_message);
}

export function verificationOutcome(sourceCase, workflow, evidence) {
  const policy = sourceCase.expected_outcome ?? 'done';
  assert.ok(['done', 'recognition_review_required', 'done_or_review_required'].includes(policy));
  const nativeMode = sourceCase.expected_execution_mode === 'native_ingest';
  if (Object.hasOwn(sourceCase, 'expected_execution_mode')) assert.equal(sourceCase.expected_execution_mode, 'native_ingest');
  if (Object.hasOwn(workflow.ocr_job ?? {}, 'native_ingest')) assert.equal(nativeMode, true);
  if (policy !== 'done_or_review_required' && !nativeMode) return policy;
  if (policy === 'done_or_review_required') {
    verificationCaseId(sourceCase);
    assert.ok(Object.hasOwn(sourceCase, 'case_id') && !Object.hasOwn(sourceCase, 'source_order_id'));
    assert.ok(!['native_empty_resolution', 'review_resolution', 'blank_resolution'].some(key => Object.hasOwn(sourceCase, key)));
    assert.ok(Array.isArray(sourceCase.required_raw_cells) && sourceCase.required_raw_cells.length > 0);
  }
  const job = workflow.ocr_job;
  const payload = evidence.payload_json;
  const execution = payload?._ocr_execution;
  if (nativeMode) {
    assertNativeIngestOwner(sourceCase, workflow, evidence);
    // This is the captured execution outcome, not the later operator state.
    assert.equal(workflow.state, job.status === 'done' ? 'ocr_completed' : 'ocr_failed');
  } else {
    assert.equal(nonEmpty(job?.ocr_job_id, 'job-id'), execution?.job_id);
    assert.equal(nonEmpty(job?.workflow_attempt_id, 'attempt-id'), execution?.workflow_attempt_id);
  }
  assert.equal(evidence.producer_version, 'hakodate_best_method_pipeline');
  const records = payload.hakodate_ocr_evidence_records;
  assert.ok(Array.isArray(records));
  const expectedReview = records.filter(row => row.engine_metadata?.recognizer_output?.resolution_state === 'review_required')
    .map(row => ({ sheet_cell: row.engine_metadata.sheet_cell, raw_text: row.raw_text,
      reason: row.engine_metadata.recognizer_output.quantity_source_issue }));
  const review = payload.hakodate_recognition_review;
  if (job.status === 'done') {
    assert.equal(evidence.status, 'done');
    assert.equal(job.result_state, 'evidence_ready');
    assert.equal(job.processing_stage, 'evidence_ready');
    for (const key of ['error', 'error_message', 'error_detail', 'error_user_message']) {
      assert.ok(Object.hasOwn(job, key));
      assert.equal(job[key], null);
    }
    assert.equal(expectedReview.length, 0);
    assert.ok(!review?.cells?.length);
    assert.ok(policy === 'done_or_review_required' || policy === 'done');
    return 'done';
  }
  assert.equal(job.status, 'failed');
  assert.equal(job.result_state, 'review_required');
  assert.equal(job.recovery_action, 'review_recognition_cells');
  assert.equal(evidence.status, 'diagnostic_only');
  assert.equal(evidence.selectable, false);
  assert.ok(expectedReview.length > 0);
  assert.deepEqual(review?.cells, expectedReview);
  assert.equal(review.count, expectedReview.length);
  assert.equal(new Set(expectedReview.map(cell => cell.sheet_cell)).size, expectedReview.length);
  assert.ok(policy === 'done_or_review_required' || policy === 'recognition_review_required');
  return 'recognition_review_required';
}

function verificationIdentityForCase(folder, sourceCase) {
  const label = verificationCaseId(sourceCase);
  const caseFolder = resolve(folder, label);
  assert.equal(realpathSync(caseFolder), resolve(realpathSync(folder), label));
  const order = readJson(resolve(caseFolder, 'order.json'));
  const workflow = readJson(resolve(caseFolder, 'workflow.json'));
  const evidence = readJson(resolve(caseFolder, 'evidence.json'));
  // Use the same Python JSON number/Unicode serialization as payload_digest, not JS rounding.
  const originalPayloadSha256 = execFileSync('python3', ['-c',
    'import sys,json,hashlib; p=json.load(sys.stdin)["payload_json"]; p={k:v for k,v in p.items() if k!="operator_recognition_acceptance"}; print(hashlib.sha256(json.dumps(p,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest())'],
  { input: readFileSync(resolve(caseFolder, 'evidence.json')), encoding: 'utf8' }).trim();
  const originalIdentity = { originalPayloadSha256, artifactDigest: evidence.artifact_digest,
    selectedPdfReceipt: evidence.payload_json?.hakodate_repair_source_receipt?.selected_pdf_receipt };
  const uploadState = readJson(resolve(caseFolder, 'upload-state.json'));
  const execution = evidence.payload_json?._ocr_execution;
  const selectedPdf = execution?.selected_pdf_receipt;
  const orderId = nonEmpty(order.id, 'order-id');
  const documentId = nonEmpty(order.current_version?.document_id || order.document_id, 'document-id');
  const evidenceId = nonEmpty(evidence.id, 'evidence-id');
  const sourcePDFhash = nonEmpty(sourceCase.sha256, 'source-pdf-hash');
  assert.match(sourcePDFhash, /^[0-9a-f]{64}$/);
  assert.equal(uploadState.current_order_id, orderId);
  assert.equal(uploadState.current_document_id, documentId);
  assert.equal(uploadState.content_sha256, sourcePDFhash);
  assert.equal(order.document_id, documentId);
  assert.equal(selectedPdf?.order_id, orderId);
  assert.equal(selectedPdf?.document_id, documentId);
  assert.equal(selectedPdf?.sha256, sourcePDFhash);
  assert.equal(execution?.evidence_run_id, evidenceId);
  assert.equal(workflow.order_id, orderId);
  assert.equal(workflow.ocr_job?.order_id, orderId);
  assert.equal(workflow.ocr_job?.order_document_id, documentId);
  const expectedOutcome = verificationOutcome(sourceCase, workflow, evidence);
  assert.ok(['done', 'recognition_review_required'].includes(expectedOutcome));
  const union = sourceCase.expected_outcome === 'done_or_review_required';
  const policyIdentity = union ? { manifestPolicy: sourceCase.expected_outcome, expectedOutcome } : {};
  if (sourceCase.expected_execution_mode === 'native_ingest') {
    policyIdentity.nativeIngest = workflow.ocr_job.native_ingest;
    policyIdentity.expectedOutcome = expectedOutcome;
  }
  if (union) {
    assert.deepEqual(readJson(resolve(caseFolder, 'verification-outcome.json')),
      { manifest_policy: sourceCase.expected_outcome, actual_outcome: expectedOutcome });
  }
  if (Object.hasOwn(sourceCase, 'required_raw_cells')) {
    const identities = new Set();
    for (const cell of sourceCase.required_raw_cells) {
      assert.deepEqual(Object.keys(cell).sort(), ['date', 'daypart', 'expected_raw', 'field', 'menu_name']);
      assert.ok(Object.values(cell).every(value => typeof value === 'string' && value.length > 0));
      assert.equal(cell.expected_raw, '1');
      const keys = ['date', 'daypart', 'menu_name', 'field'];
      const key = JSON.stringify(keys.map(name => cell[name]));
      assert.ok(!identities.has(key)); identities.add(key);
      const matches = evidence.payload_json.hakodate_ocr_evidence_records.filter(row =>
        keys.every(name => row.engine_metadata?.recognizer_output?.[name] === cell[name]));
      assert.equal(matches.length, 1);
      const row = matches[0]; const raw = row.engine_metadata.recognizer_output;
      for (const name of ['recognizer_raw_text', 'raw_text', 'resolved_value']) assert.equal(raw[name], cell.expected_raw);
      assert.equal(row.raw_text, cell.expected_raw);
      assert.equal(row.normalized_value, cell.expected_raw);
    }
    assert.ok(identities.size > 0);
    assert.deepEqual(readJson(resolve(caseFolder, 'required-raw-cells.json')),
      { count: identities.size, cells: sourceCase.required_raw_cells });
  }
  if (expectedOutcome === 'recognition_review_required') {
    assert.equal(workflow.ocr_job?.status, 'failed');
    assert.equal(workflow.ocr_job?.result_state, 'review_required');
    assert.equal(evidence.status, 'diagnostic_only');
    assert.equal(evidence.selectable, false);
    const review = evidence.payload_json?.hakodate_recognition_review;
    assert.ok(Array.isArray(review?.cells) && review.cells.length > 0);
    assert.equal(review.count, review.cells.length);
    return { orderId, documentId, evidenceId, sourcePDFhash, expectedOutcome, review,
      ...originalIdentity,
      ...policyIdentity,
      ...(Object.hasOwn(sourceCase, 'case_id') ? { caseId: sourceCase.case_id } : {}) };
  }
  assert.equal(workflow.ocr_job?.status, 'done');
  return { orderId, documentId, evidenceId, sourcePDFhash,
    ...originalIdentity,
    ...policyIdentity,
    ...(Object.hasOwn(sourceCase, 'case_id') ? { caseId: sourceCase.case_id } : {}) };
}

export function verificationIdentities(folder) {
  const manifest = readJson(resolve(folder, 'manifest.json'));
  const sourceCases = Array.isArray(manifest.cases) ? manifest.cases : [];
  const caseDirectories = readdirSync(folder, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => entry.name)
    .sort();
  assert.ok(sourceCases.length > 0);
  const labels = sourceCases.map(verificationCaseId);
  if (sourceCases.every(item => Object.hasOwn(item, 'source_order_id'))) {
    assert.equal(new Set(sourceCases.map(item => item?.source_order_id)).size, sourceCases.length);
    assert.deepEqual(caseDirectories, sourceCases.map(item => item?.source_order_id).sort());
  }
  assert.equal(new Set(labels).size, sourceCases.length);
  assert.deepEqual(caseDirectories, labels.sort());
  return sourceCases.map(sourceCase => verificationIdentityForCase(folder, sourceCase));
}

export function expectedWorkflowRead(pathname, orderId) {
  return pathname === `/api/orders/${orderId}/workflow-v2/display`;
}

export async function verifyOcrBrowser({ origin, token, identity, source, output, playwright }) {
  const result = {
    status: 'failed', scope: 'GET-only workflow-v2 Step2 OCR evidence; service-principal auth, not human login',
    phase: 'validate-artifact', sourceSHA: source, orderId: identity.orderId, documentId: identity.documentId,
    evidenceId: identity.evidenceId, sourcePDFhash: identity.sourcePDFhash, unexpectedWriteCount: 0,
    pageErrorCount: 0, http: [], ui: { imageVisible: false, naturalWidth: 0, naturalHeight: 0 }, finalPathname: null,
  };
  if (Object.hasOwn(identity, 'caseId')) result.caseId = identity.caseId;
  if (Object.hasOwn(identity, 'manifestPolicy')) {
    result.manifestPolicy = identity.manifestPolicy;
    result.actualOutcome = identity.expectedOutcome;
  }
  let browser;
  let context;
  let page;
  const readWatchers = [];
  const diagnostics = diagnosticTimeline(result);
  let releaseExternalRequests = () => {};
  try {
    assert.equal(isAllowedOutputSourceOrigin(origin), true);
    assert.match(source, /^[0-9a-f]{40}$/);
    nonEmpty(token, 'live-id-token');
    result.phase = 'launch-browser';
    const { expect, webkit } = playwright || await import('@playwright/test');
    browser = await webkit.launch({ headless: true });
    result.phase = 'configure-private-context';
    context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    await context.addInitScript(({ sessionToken, sessionOrigin }) => {
      if (location.origin !== sessionOrigin) return;
      sessionStorage.setItem('auth_header', 'Bearer ' + sessionToken);
      sessionStorage.setItem('sawa_auth_generation', localStorage.getItem('sawa_logout_generation') || '');
      sessionStorage.setItem('sawa_auth_cache_generation', 'ocr-live-private-context');
    }, { sessionToken: token, sessionOrigin: origin });
    context.on('page', page => page.on('pageerror', () => result.pageErrorCount++));
    context.on('response', response => recordAllowedResponse(result, {
      origin, url: response.url(), method: response.request().method(), status: response.status(),
    }));
    const allowedExternalUrls = new Set();
    let releaseExternalGate;
    const externalGate = new Promise(resolve => { releaseExternalGate = resolve; });
    releaseExternalRequests = () => releaseExternalGate();
    await context.route('**/*', route => gateReadonlyResource(route, {
      origin, allowedExternalUrls, externalGate, onWrite: () => result.unexpectedWriteCount++,
    }));
    page = await context.newPage();
    const rawUrl = `${origin}/api/orders/${identity.orderId}/workflow-v2/inspection?history_details=true`;
    let rawRequests = 0;
    page.on('request', request => { if (request.method() === 'GET' && request.url() === rawUrl) rawRequests++; });
    result.readTimings = {};
    const watchRead = (name, suffix) => {
      const timing = {};
      result.readTimings[name] = timing;
      const watcher = firstJsonRequest(page, `${origin}/api/orders/${identity.orderId}${suffix}`, timing);
      readWatchers.push(watcher);
      return settleResponse(watcher.promise);
    };
    const inspectionRead = watchRead('inspection', '/workflow-v2/display?scope=summary');
    const candidatesRead = watchRead('ocr-candidates', '/workflow-v2/display?scope=ocr');
    result.phase = 'open-workflow';
    const workflowResponse = await page.goto(`${origin}/hospital/orders/${encodeURIComponent(identity.orderId)}/workflow-v2`, { waitUntil: 'domcontentloaded' });
    assert.equal(workflowResponse?.status(), 200);
    result.phase = 'verify-deploy-version';
    await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
    result.phase = 'await-workflow-reads';
    const inspectionReceipt = await inspectionRead;
    assert.equal(inspectionReceipt.ok, true);
    const liveInspection = await inspectionReceipt.response.json();
    const liveOrder = liveInspection.order_header;
    const liveWorkflow = liveInspection.workflow;
    await waitForWorkflowReadiness({page, expect, inspection: liveInspection, order: liveOrder, identity,
      revision: liveWorkflow.revision});
    const expectedReads = result.http.filter(item => expectedWorkflowRead(item.pathname, identity.orderId));
    assert.ok(expectedReads.some(item => item.pathname.endsWith('/display') && item.status === 200));
    diagnostics.begin('verify-live-identity');
    assert.equal(liveWorkflow.order_id, identity.orderId);
    assert.equal(liveWorkflow.ocr_job?.order_id, identity.orderId);
    assert.equal(liveWorkflow.ocr_job?.order_document_id, identity.documentId);
    assert.equal(liveOrder.id, identity.orderId);
    assert.equal(liveOrder.current_version?.document_id, identity.documentId);
    diagnostics.begin('open-ocr-detail');
    const liveEvidence = await readLiveCandidate({inspection: liveInspection, workflow: liveWorkflow, identity, page,
      readCandidates: async () => {
        const receipt = await candidatesRead;
        assert.equal(receipt.ok, true);
        return receipt.response.json();
      },
      readDetail: async () => {
        const detailReceipt = await watchRead('ocr-detail', `/workflow-v2/inspection-detail/${identity.evidenceId}`);
        assert.equal(detailReceipt.ok, true);
        return detailReceipt.response.json();
      }});
    const initialState = liveStateSnapshot(liveInspection, liveEvidence);
    diagnostics.end();
    assert.equal(liveEvidence.order_id, identity.orderId);
    assert.equal(liveEvidence.execution_lineage?.evidence_run_id, identity.evidenceId);
    assert.equal(liveEvidence.execution_lineage?.selected_pdf_receipt?.document_id, identity.documentId);
    assert.equal(liveEvidence.execution_lineage?.selected_pdf_receipt?.sha256, identity.sourcePDFhash);
    if (identity.nativeIngest) {
      assert.deepEqual(liveWorkflow.ocr_job?.native_ingest, identity.nativeIngest);
      assertNativeIngestOwner({ expected_execution_mode: 'native_ingest', sha256: identity.sourcePDFhash }, liveWorkflow,
        { id: liveEvidence.ocr_result_id, producer_version: liveEvidence.producer_version,
          payload_json: { _ocr_execution: liveEvidence.execution_lineage } });
      assert.equal(liveWorkflow.state, liveEvidence.selected ? 'ocr_selected'
        : liveWorkflow.ocr_job.status === 'done' ? 'ocr_completed' : 'ocr_failed');
    }
    const verifiedOverlayUrl = nonEmpty(liveEvidence.overlay_url, 'live-overlay-url');
    allowedExternalUrls.add(verifiedOverlayUrl);
    releaseExternalRequests();
    diagnostics.begin('await-network-idle');
    await page.waitForLoadState('networkidle');
    diagnostics.end();
    if (identity.expectedOutcome === 'recognition_review_required') {
      diagnostics.begin('verify-review-state');
      assert.equal(liveWorkflow.ocr_job?.status, 'failed');
      assert.equal(liveWorkflow.ocr_job?.result_state, 'review_required');
      assert.deepEqual(liveEvidence.recognition_review, identity.review);
      const accepted = liveEvidence.recognition_acceptance;
      if (accepted) {
        assertRecognitionAcceptance(liveEvidence, liveWorkflow, identity);
        assert.equal(liveWorkflow.selected_ocr_result_id, identity.evidenceId);
        assert.equal(liveEvidence.selectable, true);
      } else {
        assert.equal(liveEvidence.selectable, false);
        assert.equal(liveEvidence.recognition_acceptance, null);
        if (liveEvidence.selected) {
          assert.equal(liveWorkflow.selected_ocr_result_id, identity.evidenceId);
          assert.equal(liveWorkflow.state, 'ocr_selected');
          assert.ok(Array.isArray(liveWorkflow.blockers));
          assert.ok(liveWorkflow.blockers.every(code => typeof code === 'string'
            && code.length <= 128 && /^[a-z]+(?:_[a-z]+)*$/.test(code)));
          assertRecognitionAcceptance({...liveEvidence,
            recognition_acceptance: liveEvidence.artifact_manifest.operator_recognition_acceptance}, liveWorkflow, identity);
          const terminalError = nonEmpty(identity.nativeIngest?.terminal_receipt?.error, 'native-terminal-error');
          await expect(page.locator('.notice.error').filter({hasText: 'OCR失敗:'}))
            .toHaveText(`OCR失敗: ${terminalError}`);
          result.selectedInvalid = {workflowBlockers: [...liveWorkflow.blockers], manifestReceiptVerified: true};
        }
      }
      diagnostics.begin('open-step2');
      await page.locator('.step-nav button').filter({hasText: 'OCR選択'}).click();
      diagnostics.begin('verify-review-controls');
      const card = page.locator('.ocr-card').filter({hasText: identity.evidenceId});
      await expect(card.getByRole('alert')).toContainText(`${accepted ? '確認済み' : '要確認'}: ${identity.review.count}セル`);
      await expect(card.getByRole('button', {name: accepted ? '正解にする' : '確認して正解にする', exact: true})).toBeEnabled();
      await expect(card.getByRole('button', {name: 'セル画像を確認・訂正', exact: true})).toBeVisible();
      result.review = {count: identity.review.count, selectionBlocked: !accepted};
    }
    diagnostics.begin('verify-evidence-text');
    const identityText = await page.locator('main').innerText();
    assert.ok(identityText.includes(identity.evidenceId));
    diagnostics.end();
    result.phase = 'verify-visible-ocr-image';
    const image = page.locator(`img[alt="${identity.evidenceId} overlay"]`);
    await expect(image).toBeVisible();
    assert.equal(await image.getAttribute('src'), verifiedOverlayUrl);
    await expect(image).toHaveJSProperty('complete', true);
    const dimensions = await image.evaluate(node => ({ naturalWidth: node.naturalWidth, naturalHeight: node.naturalHeight }));
    assert.ok(dimensions.naturalWidth > 0 && dimensions.naturalHeight > 0);
    result.ui = { imageVisible: true, ...dimensions };
    result.phase = 'capture-visible-ocr-image';
    await image.screenshot({ path: resolve(output, 'visible-ocr-image.png') });
    result.phase = 'capture-workflow-page';
    await page.screenshot({ path: resolve(output, 'workflow-v2-step2.png'), fullPage: true });
    diagnostics.begin('verify-state-preserved');
    const finalRead = watchRead('final-inspection', '/workflow-v2/display?scope=summary');
    const finalCandidatesRead = watchRead('final-candidates', '/workflow-v2/display?scope=ocr');
    await page.reload({ waitUntil: 'domcontentloaded' });
    const finalReceipt = await finalRead;
    assert.equal(finalReceipt.ok, true);
    const finalInspection = await finalReceipt.response.json();
    const finalOrder = finalInspection.order_header;
    assert.equal(finalInspection.workflow.revision, liveWorkflow.revision);
    await waitForWorkflowReadiness({page, expect, inspection: finalInspection, order: finalOrder, identity,
      revision: liveWorkflow.revision});
    const finalEvidence = await readLiveCandidate({inspection: finalInspection, workflow: liveWorkflow, identity, page,
      readCandidates: async () => {
        const receipt = await finalCandidatesRead;
        assert.equal(receipt.ok, true);
        return receipt.response.json();
      },
      readDetail: async () => {
        const receipt = await watchRead('final-detail', `/workflow-v2/inspection-detail/${identity.evidenceId}`);
        assert.equal(receipt.ok, true);
        return receipt.response.json();
      }});
    assert.deepEqual(liveStateSnapshot(finalInspection, finalEvidence), initialState);
    diagnostics.end();
    diagnostics.begin('verify-raw-json');
    assert.equal(rawRequests, 0);
    const raw = page.locator('details.json-details').filter({hasText: 'デバッグ用 raw JSON を開く'});
    await expect(raw.locator('pre')).toHaveCount(0);
    const rawRead = watchRead('raw-inspection', '/workflow-v2/inspection?history_details=true');
    await raw.locator('summary').click();
    const rawReceipt = await rawRead;
    assert.equal(rawReceipt.ok, true);
    const rawPayload = await rawReceipt.response.json();
    assert.equal(rawPayload.order_id, identity.orderId);
    assert.equal(rawPayload.workflow.order_id, identity.orderId);
    assert.equal(rawPayload.workflow.revision, liveWorkflow.revision);
    assert.equal(rawPayload.workflow.selected_ocr_result_id, liveWorkflow.selected_ocr_result_id);
    await expect(raw.locator('pre')).toBeVisible();
    assert.deepEqual(JSON.parse(await raw.locator('pre').innerText()), rawPayload);
    assert.equal(rawRequests, 1);
    await raw.locator('summary').click();
    await expect(raw.locator('pre')).toHaveCount(0);
    assert.equal(rawRequests, 1);
    diagnostics.end();
    result.phase = 'validate-readonly';
    assert.equal(result.unexpectedWriteCount, 0);
    assert.equal(result.pageErrorCount, 0);
    result.finalPathname = new URL(page.url()).pathname;
    // Complete read-only UI observations, but do not claim parity without the original diagnosis.
    result.status = result.selectedInvalid ? 'blocked' : 'passed';
    if (result.selectedInvalid) result.code = 'selected_invalid_requires_original_diagnosis';
    result.phase = 'finished';
  } catch (error) {
    diagnostics.fail(error);
    result.code = sanitizedFailureCode(result.phase);
    result.failureScreenshotSaved = await captureFailureScreen(page, origin, identity.orderId, output);
  } finally {
    for (const watcher of readWatchers) watcher.cancel();
    releaseExternalRequests();
    try { if (context) await context.close(); }
    finally {
      if (browser) await browser.close();
      result.ownedBrowserStopped = true;
      writeJson(resolve(output, 'browser-result.json'), result);
    }
  }
  return result;
}

async function main() {
  const artifact = resolve(process.argv[2] || 'tmp/ocr-verification');
  const output = resolve(process.argv[3] || 'tmp/ocr-live');
  mkdirSync(output, { recursive: true });
  if (!existsSync(resolve(artifact, 'manifest.json'))) throw new Error('downloaded-ocr-verification-artifact-required');
  const allowed = process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_EVENT_NAME === 'workflow_dispatch'
    && process.env.GITHUB_REF === 'refs/heads/develop' && process.env.WEB_URL === STG_OUTPUT_SOURCE_ORIGIN
    && /^[0-9a-f]{40}$/.test(process.env.GITHUB_SHA || '');
  if (!allowed) process.exitCode = 1;
  else {
    let identities;
    try {
      identities = verificationIdentities(artifact);
    } catch {
      writeJson(resolve(output, 'browser-result.json'), {
        status: 'failed', scope: 'GET-only workflow-v2 Step2 OCR evidence; service-principal auth, not human login',
        sourceSHA: process.env.GITHUB_SHA, code: 'artifact-identity-invalid',
      });
      process.exitCode = 1;
      return;
    }
    const results = [];
    for (const [index, identity] of identities.entries()) {
      const caseOutput = resolve(output, `case-${index + 1}`);
      if (existsSync(caseOutput)) assert.equal(realpathSync(caseOutput), caseOutput);
      mkdirSync(caseOutput, { recursive: true });
      results.push(await verifyOcrBrowser({ origin: STG_OUTPUT_SOURCE_ORIGIN, token: process.env.LIVE_ID_TOKEN,
        identity, source: process.env.GITHUB_SHA, output: caseOutput }));
    }
    writeJson(resolve(output, 'browser-result.json'), {
      status: results.every(result => result.status === 'passed') ? 'passed' : 'failed',
      scope: 'GET-only workflow-v2 Step2 OCR evidence; service-principal auth, not human login',
      sourceSHA: process.env.GITHUB_SHA,
      cases: results.map(result => ({ status: result.status, orderId: result.orderId, documentId: result.documentId,
        evidenceId: result.evidenceId, sourcePDFhash: result.sourcePDFhash, code: result.code || null,
        ...(Object.hasOwn(result, 'manifestPolicy') ? { manifestPolicy: result.manifestPolicy, actualOutcome: result.actualOutcome } : {}),
        ...(Object.hasOwn(result, 'caseId') ? { caseId: result.caseId } : {}) })),
    });
    process.exitCode = results.every(result => result.status === 'passed') ? 0 : 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
