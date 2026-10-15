import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { isAllowedOutputSourceOrigin, STG_OUTPUT_SOURCE_ORIGIN } from './output-source-live-origin.mjs';

const allowedMethods = new Set(['GET', 'HEAD']);
const phases = new Set([
  'validate-artifact', 'launch-browser', 'configure-private-context', 'open-workflow',
  'verify-deploy-version', 'await-workflow-reads', 'verify-live-identity',
  'verify-visible-ocr-image', 'capture-visible-ocr-image', 'capture-workflow-page',
  'validate-readonly', 'finished',
]);

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

function verificationIdentityForCase(folder, sourceCase) {
  const caseFolder = resolve(folder, sourceCase.source_order_id);
  const order = readJson(resolve(caseFolder, 'order.json'));
  const workflow = readJson(resolve(caseFolder, 'workflow.json'));
  const evidence = readJson(resolve(caseFolder, 'evidence.json'));
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
  const expectedOutcome = sourceCase.expected_outcome ?? 'done';
  assert.ok(['done', 'recognition_review_required'].includes(expectedOutcome));
  if (expectedOutcome === 'recognition_review_required') {
    assert.equal(workflow.ocr_job?.status, 'failed');
    assert.equal(workflow.ocr_job?.result_state, 'review_required');
    assert.equal(evidence.status, 'diagnostic_only');
    assert.equal(evidence.selectable, false);
    const review = evidence.payload_json?.hakodate_recognition_review;
    assert.ok(Array.isArray(review?.cells) && review.cells.length > 0);
    assert.equal(review.count, review.cells.length);
    return { orderId, documentId, evidenceId, sourcePDFhash, expectedOutcome, review };
  }
  assert.equal(workflow.ocr_job?.status, 'done');
  return { orderId, documentId, evidenceId, sourcePDFhash };
}

export function verificationIdentities(folder) {
  const manifest = readJson(resolve(folder, 'manifest.json'));
  const sourceCases = Array.isArray(manifest.cases) ? manifest.cases : [];
  const caseDirectories = readdirSync(folder, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => entry.name)
    .sort();
  assert.ok(sourceCases.length > 0);
  assert.equal(new Set(sourceCases.map(item => item?.source_order_id)).size, sourceCases.length);
  assert.deepEqual(caseDirectories, sourceCases.map(item => item?.source_order_id).sort());
  return sourceCases.map(sourceCase => verificationIdentityForCase(folder, sourceCase));
}

function expectedWorkflowRead(pathname, orderId) {
  return pathname === `/api/hospital/orders/${orderId}/workflow-v2`
    || pathname === `/api/hospital/orders/${orderId}/workflow-v2/ocr-results`
    || pathname === `/api/hospital/orders/${orderId}`;
}

export async function verifyOcrBrowser({ origin, token, identity, source, output }) {
  const result = {
    status: 'failed', scope: 'GET-only workflow-v2 Step2 OCR evidence; service-principal auth, not human login',
    phase: 'validate-artifact', sourceSHA: source, orderId: identity.orderId, documentId: identity.documentId,
    evidenceId: identity.evidenceId, sourcePDFhash: identity.sourcePDFhash, unexpectedWriteCount: 0,
    pageErrorCount: 0, http: [], ui: { imageVisible: false, naturalWidth: 0, naturalHeight: 0 }, finalPathname: null,
  };
  let browser;
  let context;
  let releaseExternalRequests = () => {};
  try {
    assert.equal(isAllowedOutputSourceOrigin(origin), true);
    assert.match(source, /^[0-9a-f]{40}$/);
    nonEmpty(token, 'live-id-token');
    result.phase = 'launch-browser';
    const { expect, webkit } = await import('@playwright/test');
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
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (!allowedMethods.has(request.method())) {
        result.unexpectedWriteCount++;
        return route.abort('blockedbyclient');
      }
      if (url.origin !== origin) {
        await externalGate;
        if (allowedExternalUrls.has(request.url())) return route.continue();
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    const page = await context.newPage();
    const workflowRead = settleResponse(page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === 'GET' && url.origin === origin
        && url.pathname === `/api/hospital/orders/${identity.orderId}/workflow-v2`;
    }));
    const ocrResultsRead = settleResponse(page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === 'GET' && url.origin === origin
        && url.pathname === `/api/hospital/orders/${identity.orderId}/workflow-v2/ocr-results`;
    }));
    const orderRead = settleResponse(page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === 'GET' && url.origin === origin
        && url.pathname === `/api/hospital/orders/${identity.orderId}`;
    }));
    result.phase = 'open-workflow';
    const workflowResponse = await page.goto(`${origin}/hospital/orders/${encodeURIComponent(identity.orderId)}/workflow-v2`, { waitUntil: 'domcontentloaded' });
    assert.equal(workflowResponse?.status(), 200);
    result.phase = 'verify-deploy-version';
    await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
    result.phase = 'await-workflow-reads';
    const [workflowReceipt, ocrResultsReceipt, orderReceipt] = await Promise.all([workflowRead, ocrResultsRead, orderRead]);
    assert.equal(workflowReceipt.ok, true);
    assert.equal(ocrResultsReceipt.ok, true);
    assert.equal(orderReceipt.ok, true);
    const [liveWorkflowResponse, liveOcrResultsResponse, liveOrderResponse] = [workflowReceipt.response, ocrResultsReceipt.response, orderReceipt.response];
    const [liveWorkflow, liveOcrResults, liveOrder] = await Promise.all([
      liveWorkflowResponse.json(), liveOcrResultsResponse.json(), liveOrderResponse.json(),
    ]);
    const expectedReads = result.http.filter(item => expectedWorkflowRead(item.pathname, identity.orderId));
    assert.ok(expectedReads.some(item => item.pathname.endsWith('/workflow-v2') && item.status === 200));
    assert.ok(expectedReads.some(item => item.pathname.endsWith('/ocr-results') && item.status === 200));
    assert.ok(expectedReads.some(item => item.pathname === `/api/hospital/orders/${identity.orderId}` && item.status === 200));
    result.phase = 'verify-live-identity';
    assert.equal(liveWorkflow.order_id, identity.orderId);
    assert.equal(liveWorkflow.ocr_job?.order_id, identity.orderId);
    assert.equal(liveWorkflow.ocr_job?.order_document_id, identity.documentId);
    assert.equal(liveOrder.id, identity.orderId);
    assert.equal(liveOrder.current_version?.document_id, identity.documentId);
    const liveEvidence = (liveOcrResults.results || []).find(item => item?.ocr_result_id === identity.evidenceId);
    assert.ok(liveEvidence);
    assert.equal(liveEvidence.order_id, identity.orderId);
    assert.equal(liveEvidence.execution_lineage?.evidence_run_id, identity.evidenceId);
    assert.equal(liveEvidence.execution_lineage?.selected_pdf_receipt?.document_id, identity.documentId);
    assert.equal(liveEvidence.execution_lineage?.selected_pdf_receipt?.sha256, identity.sourcePDFhash);
    const verifiedOverlayUrl = nonEmpty(liveEvidence.overlay_url, 'live-overlay-url');
    allowedExternalUrls.add(verifiedOverlayUrl);
    releaseExternalRequests();
    await page.waitForLoadState('networkidle');
    if (identity.expectedOutcome === 'recognition_review_required') {
      assert.equal(liveWorkflow.ocr_job?.status, 'failed');
      assert.equal(liveWorkflow.ocr_job?.result_state, 'review_required');
      assert.equal(liveEvidence.selectable, false);
      assert.deepEqual(liveEvidence.recognition_review, identity.review);
      await page.locator('.step-nav button').filter({hasText: 'OCR選択'}).click();
      const card = page.locator('.ocr-card').filter({hasText: identity.evidenceId});
      await expect(card.getByRole('alert')).toContainText(`要確認: ${identity.review.count}セル`);
      await expect(card.getByRole('button', {name: '正解にする', exact: true})).toBeDisabled();
      await expect(card.getByRole('button', {name: 'セル画像を確認・訂正', exact: true})).toBeVisible();
      result.review = {count: identity.review.count, selectionBlocked: true};
    }
    const identityText = await page.locator('main').innerText();
    assert.ok(identityText.includes(identity.evidenceId));
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
    result.phase = 'validate-readonly';
    assert.equal(result.unexpectedWriteCount, 0);
    assert.equal(result.pageErrorCount, 0);
    result.finalPathname = new URL(page.url()).pathname;
    result.status = 'passed';
    result.phase = 'finished';
  } catch {
    result.code = sanitizedFailureCode(result.phase);
  } finally {
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
      mkdirSync(caseOutput, { recursive: true });
      results.push(await verifyOcrBrowser({ origin: STG_OUTPUT_SOURCE_ORIGIN, token: process.env.LIVE_ID_TOKEN,
        identity, source: process.env.GITHUB_SHA, output: caseOutput }));
    }
    writeJson(resolve(output, 'browser-result.json'), {
      status: results.every(result => result.status === 'passed') ? 'passed' : 'failed',
      scope: 'GET-only workflow-v2 Step2 OCR evidence; service-principal auth, not human login',
      sourceSHA: process.env.GITHUB_SHA,
      cases: results.map(result => ({ status: result.status, orderId: result.orderId, documentId: result.documentId,
        evidenceId: result.evidenceId, sourcePDFhash: result.sourcePDFhash, code: result.code || null })),
    });
    process.exitCode = results.every(result => result.status === 'passed') ? 0 : 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
