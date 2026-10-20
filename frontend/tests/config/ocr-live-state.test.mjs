import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { verifyOcrBrowser, assertRecognitionAcceptance } from '../../scripts/verify-ocr-live.mjs';
import { inspectReview } from '../../scripts/verify-recognition-review-live.mjs';
import { STG_OUTPUT_SOURCE_ORIGIN as origin } from '../../scripts/output-source-live-origin.mjs';

// Synthetic browser transport executes the complete verifier, including its real response watchers.
function fixture(selected, mutate = () => {}, failure = null) {
  const review = {count: 1, cells: [{sheet_cell: 'F11', raw_text: '1', reason: 'hakodate_blank_input_nonempty_prediction'}]};
  const pdf = {order_id: 'ORD12345678', document_id: 'DOCtest', sha256: 'a'.repeat(64)};
  const identity = {orderId: pdf.order_id, documentId: pdf.document_id, sourcePDFhash: pdf.sha256,
    evidenceId: 'OEVtest', expectedOutcome: 'recognition_review_required', review,
    originalPayloadSha256: 'b'.repeat(64), artifactDigest: 'digest', selectedPdfReceipt: pdf};
  const acceptance = {version: 1, evidence_run_id: identity.evidenceId, artifact_digest: 'digest',
    payload_sha256: identity.originalPayloadSha256, selected_pdf_receipt: pdf, workflow_revision: 9,
    source_receipt: {workflow_revision: 9, recognition_evidence_run_id: identity.evidenceId, selected_pdf_receipt: pdf},
    cells: review.cells, values: {F11: ''}, edited_by: 'synthetic-private', created_at: '2026-10-20'};
  const evidence = {ocr_result_id: identity.evidenceId, order_id: identity.orderId, selected, selectable: selected,
    recognition_review: review, recognition_acceptance: selected ? acceptance : null,
    artifact_digest: 'digest', artifact_manifest: selected ? {operator_recognition_acceptance: acceptance} : {},
    execution_lineage: {evidence_run_id: identity.evidenceId, selected_pdf_receipt: pdf},
    overlay_url: `${origin}/overlay`, overlay_uri: 'gs://synthetic/overlay'};
  const workflow = {order_id: identity.orderId, revision: 10, selected_ocr_result_id: selected ? identity.evidenceId : null,
    blockers: [], warnings: [], ocr_job: {order_id: identity.orderId, order_document_id: identity.documentId,
      status: 'failed', result_state: 'review_required'}};
  const inspection = () => ({order_id: identity.orderId, workflow: structuredClone(workflow),
    saved_sheet: {sheet: {rows: [{quantity: 0}, {quantity: null}, {quantity: 12}]}},
    artifact_lineage: {selected_ocr_result_id: workflow.selected_ocr_result_id},
    ocr_candidates: {order_id: identity.orderId, selected_ocr_result_id: workflow.selected_ocr_result_id,
      results: [selected ? structuredClone(evidence) : {ocr_result_id: identity.evidenceId, selected: false, detail_state: 'not_loaded'}]}});
  const events = new EventEmitter(), page = new EventEmitter();
  const clicks = [], requests = []; let reloaded = false, blank = false, connected = true, rawOpen = false;
  const base = `/api/orders/${identity.orderId}`;
  function respond(path, data, failed = false) {
    requests.push(path);
    const request = {url: () => origin + path, method: () => 'GET'};
    const response = {url: request.url, request: () => request, status: () => 200,
      finished: async () => null, json: async () => structuredClone(data)};
    page.emit('request', request);
    if (failed) {page.emit('requestfailed', request); return;}
    page.emit('response', response); events.emit('response', response);
    page.emit('requestfinished', request);
  }
  function initial() {
    let data = inspection();
    if (reloaded) mutate(data, evidence);
    respond(base, {id: identity.orderId, current_version: {document_id: identity.documentId}});
    respond(base + '/workflow-v2/inspection?include_ocr_results=true&history_details=false', data);
  }
  const text = () => `${evidence.recognition_acceptance ? '確認済み' : '要確認'}: 1セル ${identity.evidenceId}`;
  function locator(name = '') {
    return {
      filter: () => locator(name), locator: child => locator(name === 'details.json-details' ? `raw-${child}` : 'image-parent'),
      getByRole: (role, options = {}) => locator(options.name || role),
      getByLabel: label => locator(label), getByText: label => locator(label),
      count: async () => name === '詳細を表示' ? (selected ? 0 : 1) : name === 'input[type="password"]' ? 0 : 1,
      innerText: async () => name === 'raw-pre' ? JSON.stringify(inspection()) : text(),
      getAttribute: async () => evidence.overlay_url,
      evaluate: async fn => fn({complete: true, naturalWidth: 100, naturalHeight: 80}),
      screenshot: async () => {}, scrollIntoViewIfNeeded: async () => {},
      async click() {
        clicks.push(name);
        if (name === 'raw-summary') {
          rawOpen = !rawOpen;
          if (rawOpen) respond(base + '/workflow-v2/inspection?history_details=true', inspection());
        } else if (name === '詳細を表示') {
          assert.equal(selected, false);
          const failNow = failure && failure.reload === reloaded;
          if (failNow && failure.mode === 'click') throw Error('synthetic click failure');
          respond(base + `/workflow-v2/inspection-detail/${identity.evidenceId}`,
            {...evidence, workflow_revision: workflow.revision}, failNow && failure.mode === 'detail');
          if (failNow) await new Promise(resolve => setTimeout(resolve, 20));
        } else if (name === '確認して正解にする') {
          assert.equal(selected, false);
          respond(base + `/workflow-v2/recognition-review?document_id=${identity.documentId}&ocr_result_id=${identity.evidenceId}`,
            {order_id: identity.orderId, source_receipt: {workflow_revision: workflow.revision,
              recognition_evidence_run_id: identity.evidenceId, selected_pdf_receipt: pdf}, review_cells: review.cells});
        } else if (name === '要確認の全セルを空欄に指定') {
          let accepted = false;
          page.emit('dialog', {type: () => 'confirm', accept: () => {accepted = true;}});
          assert.equal(accepted, true);
          blank = true;
        }
        else assert.equal(name, '.step-nav button', 'unexpected click, including submission');
      },
      name,
    };
  }
  Object.assign(page, {
    locator, getByRole: (role, options = {}) => locator(options.name || role),
    goto: async () => {initial(); return {status: () => 200};},
    reload: async () => {
      if (blank) {
        let accepted = false;
        page.emit('dialog', {type: () => 'beforeunload', accept: () => {accepted = true;}, dismiss: () => {}});
        assert.equal(accepted, true, 'local-only edits must be discarded without submitting');
      }
      reloaded = true; initial();
    },
    url: () => `${origin}/hospital/orders/${identity.orderId}/workflow-v2`,
    waitForLoadState: async () => {}, screenshot: async () => {},
  });
  const context = {on: (...args) => events.on(...args), addInitScript: async () => {}, route: async () => {},
    newPage: async () => {events.emit('page', page); return page;}, close: async () => {}};
  const expect = loc => ({
    toHaveText: async value => {assert.equal(loc.name, '.state-panel-main > p.subtle'); assert.match('synthetic loaded workflow', value);},
    not: {toHaveText: async value => {assert.equal(loc.name, '.state-panel-main > p.subtle'); assert.notEqual('synthetic loaded workflow', value);}},
    toContainText: async value => {
      const actual = loc.name === 'deploy version' ? 'c'.repeat(40) : loc.name === 'image-parent'
        ? '読み取り原文: 「1」 認識画像は白紙ですが、文字が読み取られています' : text();
      assert.ok(actual.includes(value), `fixture UI text mismatch: ${loc.name}`);
    },
    toBeEnabled: async () => {
      assert.ok(['正解にする', '確認して正解にする', '確認した値を採用して次へ'].includes(loc.name));
      if (loc.name === '正解にする') assert.equal(selected, true);
      if (loc.name === '確認して正解にする') assert.equal(selected, false);
      if (loc.name === '確認した値を採用して次へ') assert.equal(blank, true);
    },
    toBeVisible: async () => {assert.ok(loc); if(loc.name === 'raw-pre') assert.equal(rawOpen, true);},
    toHaveCount: async value => {assert.equal(loc.name, 'raw-pre'); assert.equal(rawOpen ? 1 : 0, value);},
    toHaveValue: async value => {assert.equal(blank, true); assert.equal(value, '');},
    toHaveJSProperty: async (key, value) => {assert.equal(key, 'complete'); assert.equal(value, true);},
  });
  expect.poll = fn => ({toBe: async value => assert.equal(await fn(), value)});
  const playwright = {expect, webkit: {launch: async () => ({newContext: async () => context,
    close: async () => {connected = false;}, isConnected: () => connected})}};
  return {identity, workflow, evidence, playwright, clicks, requests};
}

for (const selected of [false, true]) test(`full OCR verifier executes ${selected ? 'selected approved display' : 'unselected detail'} without mutation`, async () => {
  const f = fixture(selected), output = mkdtempSync(join(tmpdir(), 'live-state-'));
  try {
    const result = await verifyOcrBrowser({origin, ...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
    assert.equal(result.status, 'passed', JSON.stringify(result.failure));
    assert.equal(f.clicks.filter(x => x === '詳細を表示').length, selected ? 0 : 2);
    assert.equal(f.clicks.includes('確認して正解にする'), false);
    assert.equal(result.review.selectionBlocked, !selected);
    assert.ok(result.timings.some(x => x.check === 'open-ocr-detail'));
    assert.ok(result.timings.some(x => x.check === 'verify-state-preserved'));
    assert.equal(result.unexpectedWriteCount, 0);
    assert.equal(f.requests.filter(x => x.endsWith('/inspection?history_details=true')).length, 1);
    assert.equal(f.clicks.filter(x => x === 'raw-summary').length, 2);
  } finally {rmSync(output, {recursive: true, force: true});}
});

for (const verifier of ['ocr','review']) for (const reload of [false,true]) for (const mode of ['detail','click']) {
  test(`${verifier} ${reload ? 'reload' : 'initial'} ${mode} rejection is handled and saves failed JSON`, async () => {
    const f = fixture(false, () => {}, {reload, mode}), output = mkdtempSync(join(tmpdir(), 'live-reject-'));
    const unhandled = [];
    const onUnhandled = error => unhandled.push(error);
    process.on('unhandledRejection', onUnhandled);
    try {
      const run = verifier === 'ocr' ? verifyOcrBrowser : inspectReview;
      const result = await run({origin, ...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
      await new Promise(resolve => setTimeout(resolve, 30));
      assert.equal(result.status, 'failed');
      assert.deepEqual(unhandled, []);
      const saved = JSON.parse(readFileSync(join(output, verifier === 'ocr' ? 'browser-result.json' : 'review-ui-result.json')));
      assert.equal(saved.status, 'failed');
      assert.equal(saved.ownedBrowserStopped, true);
      assert.equal(verifier === 'ocr' ? saved.unexpectedWriteCount : saved.unexpectedWrites, 0);
    } finally {process.off('unhandledRejection', onUnhandled); rmSync(output, {recursive: true, force: true});}
  });
}

test('full sibling review dialog verifies raw cells and local blank choice without submitting', async () => {
  const f = fixture(false), output = mkdtempSync(join(tmpdir(), 'live-review-'));
  try {
    const result = await inspectReview({...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
    assert.equal(result.status, 'passed', JSON.stringify(result));
    assert.equal(result.submitClicked, false);
    assert.equal(result.localChoiceOnly, true);
    assert.equal(f.clicks.filter(x => x === '詳細を表示').length, 2);
    assert.ok(f.clicks.includes('確認して正解にする'));
    assert.ok(!f.clicks.includes('確認した値を採用して次へ'));
  } finally {rmSync(output, {recursive: true, force: true});}
});

for (const change of ['revision','selected','quantity','approval','warning']) test(`full verifier rejects concurrent ${change} change safely`, async () => {
  const f = fixture(true, (data, evidence) => {
    if (change === 'revision') data.workflow.revision++;
    if (change === 'selected') data.workflow.selected_ocr_result_id = null;
    if (change === 'quantity') data.saved_sheet.sheet.rows[0].quantity = 99;
    if (change === 'approval') data.ocr_candidates.results[0].recognition_acceptance.values.F11 = '9';
    if (change === 'warning') data.workflow.warnings.push('synthetic-private');
  }), output = mkdtempSync(join(tmpdir(), 'live-change-'));
  try {
    const result = await verifyOcrBrowser({origin, ...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
    assert.equal(result.status, 'failed');
    assert.equal(result.failure.check, 'verify-state-preserved');
    assert.match(result.failure.assertion, /^verify-ocr-live\.mjs:L\d+$/);
    assert.doesNotMatch(JSON.stringify(result.failure), /synthetic-private|synthetic-token/);
  } finally {rmSync(output, {recursive: true, force: true});}
});

for (const field of ['evidence_run_id','payload_sha256','selected_pdf_receipt','workflow_revision','artifact_digest','cells','values','source_receipt','version','edited_by','created_at']) {
  test(`approved receipt rejects altered ${field}`, () => {
    const f = fixture(true);
    f.evidence.recognition_acceptance[field] = field === 'workflow_revision' ? 10
      : ['edited_by','created_at'].includes(field) ? '' : 'invalid';
    assert.throws(() => assertRecognitionAcceptance(f.evidence, f.workflow, f.identity));
  });
}

test('review verifier refuses selected target before opening or submitting a dialog', async () => {
  const f = fixture(true), output = mkdtempSync(join(tmpdir(), 'live-review-selected-'));
  try {
    const result = await inspectReview({...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
    assert.equal(result.status, 'failed');
    assert.equal(result.submitClicked, false);
    assert.equal(f.clicks.length, 0);
    assert.match(result.failureAssertion, /^verify-ocr-live\.mjs:L\d+$/);
  } finally {rmSync(output, {recursive: true, force: true});}
});

for (const mismatch of ['original-digest','original-review','original-pdf','approved-hash']) {
  test(`full UI verifier rejects ${mismatch} without leaking original values`, async () => {
    const f = fixture(true), output = mkdtempSync(join(tmpdir(), 'live-original-'));
    if (mismatch === 'original-digest') f.identity.artifactDigest = 'different-original';
    if (mismatch === 'original-review') f.identity.review = {count: 1, cells: [{sheet_cell: 'F11', raw_text: 'secret-original'}]};
    if (mismatch === 'original-pdf') f.identity.sourcePDFhash = 'd'.repeat(64);
    if (mismatch === 'approved-hash') f.identity.originalPayloadSha256 = 'e'.repeat(64);
    try {
      const result = await verifyOcrBrowser({origin, ...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
      assert.equal(result.status, 'failed');
      assert.match(result.failure.assertion, /^verify-ocr-live\.mjs:L\d+$/);
      assert.doesNotMatch(JSON.stringify(result.failure), /synthetic-token|secret-original|different-original/);
      assert.ok(!f.clicks.includes('確認して正解にする'));
    } finally {rmSync(output, {recursive: true, force: true});}
  });
}

test('sibling dialog run rejects revision changes after local-only choices', async () => {
  const f = fixture(false, data => {data.workflow.revision++;}), output = mkdtempSync(join(tmpdir(), 'live-review-race-'));
  try {
    const result = await inspectReview({...f, token: 'synthetic-token', source: 'c'.repeat(40), output});
    assert.equal(result.status, 'failed');
    assert.equal(result.phase, 'verify-state-preserved');
    assert.equal(result.submitClicked, false);
    assert.match(result.failureAssertion, /^verify-recognition-review-live\.mjs:L\d+$/);
    assert.ok(f.clicks.includes('要確認の全セルを空欄に指定'));
  } finally {rmSync(output, {recursive: true, force: true});}
});
