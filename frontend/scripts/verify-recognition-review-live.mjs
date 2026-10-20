import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { verificationIdentities, assertNativeIngestOwner, captureFailureScreen, readLiveCandidate, liveStateSnapshot, safeAssertionName, gateReadonlyResource } from './verify-ocr-live.mjs';
import { firstJsonRequest } from './ocr-first-request.mjs';
import { STG_OUTPUT_SOURCE_ORIGIN as origin } from './output-source-live-origin.mjs';

export function requestedIdentity(identities, orderId) {
  assert.match(orderId, /^ORD[a-f0-9]{8}$/);
  const matches = identities.filter(item => item.orderId === orderId);
  assert.equal(matches.length, 1, 'exact artifact-bound order required');
  assert.equal(matches[0].expectedOutcome, 'recognition_review_required');
  assert.ok(matches[0].review?.count > 0);
  return matches[0];
}

export function expectedReviewReason(reason) {
  const descriptions = {
    hakodate_blank_input_nonempty_prediction: '認識画像は白紙ですが、文字が読み取られています',
    hakodate_ocr_quantity_non_numeric: '数量として解釈できない文字・記号です',
    hakodate_preprocess_ink_loss: '画像の前処理で筆跡が失われた可能性があります',
    hakodate_ocr_quantity_low_confidence: '認識スコアが確認基準を下回っています',
    hakodate_ocr_quantity_candidate_conflict: '認識候補と読み取り結果が一致していません',
  };
  assert.ok(Object.hasOwn(descriptions, reason), 'unverified review reason');
  return descriptions[reason];
}

export async function inspectReview({ identity, source, token, output, playwright }) {
  const result = { status: 'failed', sourceSHA: source, orderId: identity.orderId,
    evidenceId: identity.evidenceId, documentId: identity.documentId, sourcePDFhash: identity.sourcePDFhash,
    scope: 'staging service-principal UI test; no human login, OCR selection or quantity submission',
    phase: 'initialization', unexpectedWrites: 0, pageErrors: 0, http: [], submitClicked: false };
  let browser, context, page;
  const reads = [];
  let releaseExternal = () => {};
  try {
    assert.match(source, /^[a-f0-9]{40}$/);
    assert.ok(typeof token === 'string' && token.length > 0);
    const { webkit, expect } = playwright || await import('@playwright/test');
    browser = await webkit.launch({ headless: true });
    context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    await context.addInitScript(({ token, origin }) => {
      if (location.origin !== origin) return;
      sessionStorage.setItem('auth_header', 'Bearer ' + token);
      sessionStorage.setItem('sawa_auth_generation', localStorage.getItem('sawa_logout_generation') || '');
      sessionStorage.setItem('sawa_auth_cache_generation', 'recognition-review-private-test');
    }, { token, origin });
    const externalUrls = new Set();
    const externalGate = new Promise(resolve => { releaseExternal = resolve; });
    context.on('page', item => item.on('pageerror', () => result.pageErrors++));
    context.on('response', response => {
      if (new URL(response.url()).origin === origin) result.http.push({
        pathname: new URL(response.url()).pathname, method: response.request().method(), status: response.status() });
    });
    await context.route('**/*', route => gateReadonlyResource(route, {
      origin, allowedExternalUrls: externalUrls, externalGate, onWrite: () => result.unexpectedWrites++,
    }));
    page = await context.newPage();
    const watch = path => {
      const item = firstJsonRequest(page, origin + path, {});
      reads.push(item);
      return item.promise.then(response => ({ response }), () => ({ response: null }));
    };
    const base = `/api/orders/${identity.orderId}`;
    const initialReads = [watch(base), watch(base + '/workflow-v2/inspection?include_ocr_results=true&history_details=false')];
    result.phase = 'open-exact-order';
    const navigation = await page.goto(`${origin}/hospital/orders/${identity.orderId}/workflow-v2`, { waitUntil: 'domcontentloaded' });
    assert.equal(navigation.status(), 200);
    await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
    const responses = await Promise.all(initialReads);
    assert.ok(responses.every(item => item.response));
    const [order, inspection] = await Promise.all(responses.map(item => item.response.json()));
    const workflow = inspection.workflow;
    assert.equal(order.id, identity.orderId);
    assert.equal(order.current_version?.document_id, identity.documentId);
    assert.equal(workflow.order_id, identity.orderId);
    assert.equal(inspection.ocr_candidates?.order_id, identity.orderId);
    result.phase = 'open-step2';
    const evidence = await readLiveCandidate({inspection, workflow, identity, page, requireUnselected: true,
      readDetail: async () => {
        const detailResponse = await watch(base + `/workflow-v2/inspection-detail/${identity.evidenceId}`);
        assert.ok(detailResponse.response);
        assert.equal(detailResponse.response.status(), 200);
        return detailResponse.response.json();
      }});
    const initialState = liveStateSnapshot(inspection, evidence);
    const card = page.locator('.ocr-card').filter({ hasText: identity.evidenceId });
    assert.equal(evidence.order_id, identity.orderId);
    assert.equal(evidence.workflow_revision, workflow.revision);
    assert.equal(evidence?.order_id, identity.orderId);
    assert.equal(evidence.execution_lineage?.selected_pdf_receipt?.document_id, identity.documentId);
    assert.equal(evidence.execution_lineage?.selected_pdf_receipt?.sha256, identity.sourcePDFhash);
    assert.deepEqual(evidence.recognition_review, identity.review);
    assert.ok(!evidence.recognition_acceptance, 'already reviewed: original review-dialog precondition no longer holds');
    if (identity.nativeIngest) {
      assert.deepEqual(workflow.ocr_job?.native_ingest, identity.nativeIngest);
      assertNativeIngestOwner({ expected_execution_mode: 'native_ingest', sha256: identity.sourcePDFhash }, workflow,
        { id: evidence.ocr_result_id, producer_version: evidence.producer_version,
          payload_json: { _ocr_execution: evidence.execution_lineage } });
    }
    assert.ok(evidence.overlay_url);
    externalUrls.add(evidence.overlay_url);
    releaseExternal();
    await expect(card.getByRole('alert')).toContainText(`要確認: ${identity.review.count}セル`);
    assert.ok(!(await card.innerText()).includes('認識画像と読み取り結果が一致しないため'));
    const primary = card.getByRole('button', { name: '確認して正解にする', exact: true });
    await expect(primary).toBeEnabled();
    const query = new URLSearchParams({ document_id: identity.documentId, ocr_result_id: identity.evidenceId });
    const reviewRead = watch(base + '/workflow-v2/recognition-review?' + query);
    await primary.click();
    const reviewResponse = await reviewRead;
    assert.ok(reviewResponse.response);
    const review = await reviewResponse.response.json();
    assert.equal(review.order_id, identity.orderId);
    assert.equal(review.source_receipt.workflow_revision, workflow.revision);
    assert.equal(review.source_receipt.recognition_evidence_run_id, identity.evidenceId);
    assert.equal(review.source_receipt.selected_pdf_receipt.document_id, identity.documentId);
    assert.equal(review.source_receipt.selected_pdf_receipt.sha256, identity.sourcePDFhash);
    assert.deepEqual(review.review_cells, identity.review.cells);
    result.phase = 'inspect-flagged-images';
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await page.screenshot({ path: resolve(output, 'review-dialog-top.png'), fullPage: true });
    for (const cell of identity.review.cells) {
      await expect(dialog.getByLabel(cell.sheet_cell, { exact: true })).toBeVisible();
      const image = dialog.getByRole('img', { name: `${cell.sheet_cell}のFAX原画像`, exact: true });
      await image.scrollIntoViewIfNeeded();
      await expect(image).toBeVisible();
      await expect.poll(() => image.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
      await expect(image.locator('..')).toContainText(`読み取り原文: 「${cell.raw_text}」`);
      await expect(image.locator('..')).toContainText(expectedReviewReason(cell.reason));
    }
    const unresolved = identity.review.cells.filter(cell => !/^[0-9]+$/.test(cell.raw_text)).length;
    await expect(dialog.getByText(`未確認 ${unresolved} セル`, { exact: true })).toBeVisible();
    await page.screenshot({ path: resolve(output, 'review-dialog-before-choice.png'), fullPage: true });
    result.phase = 'local-explicit-blank-choice';
    page.once('dialog', item => item.accept());
    await dialog.getByRole('button', { name: '要確認の全セルを空欄に指定', exact: true }).click();
    for (const cell of identity.review.cells) await expect(dialog.getByLabel(cell.sheet_cell, { exact: true })).toHaveValue('');
    await expect(dialog.getByRole('button', { name: '確認した値を採用して次へ', exact: true })).toBeEnabled();
    await page.screenshot({ path: resolve(output, 'review-dialog-local-choice-not-submitted.png'), fullPage: true });
    result.phase = 'verify-state-preserved';
    const finalRead = watch(base + '/workflow-v2/inspection?include_ocr_results=true&history_details=false');
    page.once('dialog', dialog => dialog.type() === 'beforeunload' ? dialog.accept() : dialog.dismiss());
    await page.reload({ waitUntil: 'domcontentloaded' });
    const finalResponse = await finalRead;
    assert.ok(finalResponse.response);
    assert.equal(finalResponse.response.status(), 200);
    const finalInspection = await finalResponse.response.json();
    assert.equal(finalInspection.workflow.revision, workflow.revision);
    const finalEvidence = await readLiveCandidate({inspection: finalInspection, workflow, identity, page, requireUnselected: true,
      readDetail: async () => {
        const receipt = await watch(base + `/workflow-v2/inspection-detail/${identity.evidenceId}`);
        assert.ok(receipt.response);
        assert.equal(receipt.response.status(), 200);
        return receipt.response.json();
      }});
    assert.deepEqual(liveStateSnapshot(finalInspection, finalEvidence), initialState);
    assert.equal(result.unexpectedWrites, 0);
    assert.equal(result.pageErrors, 0);
    result.review = identity.review;
    result.localChoiceOnly = true;
    result.status = 'passed';
    result.phase = 'finished-without-submission';
  } catch (error) {
    result.failureType = error?.name === 'AssertionError' ? 'AssertionError' : error?.name === 'TimeoutError' ? 'TimeoutError' : 'other';
    result.failureAssertion = safeAssertionName(error);
    result.failureScreenshotSaved = await captureFailureScreen(page, origin, identity.orderId, output);
  } finally {
    reads.forEach(item => item.cancel());
    releaseExternal();
    try { if (context) await context.close(); }
    catch { result.status = 'failed'; result.cleanupError = 'context-close-failed'; }
    try { if (browser) await browser.close(); }
    catch { result.status = 'failed'; result.cleanupError = 'browser-close-failed'; }
    result.ownedBrowserStopped = !browser || !browser.isConnected();
    if (!result.ownedBrowserStopped) result.status = 'failed';
    writeFileSync(resolve(output, 'review-ui-result.json'), JSON.stringify(result, null, 2));
  }
  return result;
}

async function main() {
  assert.equal(process.env.GITHUB_ACTIONS, 'true');
  assert.equal(process.env.GITHUB_EVENT_NAME, 'workflow_dispatch');
  assert.equal(process.env.GITHUB_REF, 'refs/heads/develop');
  assert.equal(process.env.WEB_URL, origin);
  const artifact = resolve(process.argv[2]);
  const output = resolve(process.argv[3]);
  mkdirSync(output, { recursive: true });
  const identity = requestedIdentity(verificationIdentities(artifact), process.env.REVIEW_ORDER_ID);
  const result = await inspectReview({ identity, source: process.env.GITHUB_SHA, token: process.env.LIVE_ID_TOKEN, output });
  process.exitCode = result.status === 'passed' ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
