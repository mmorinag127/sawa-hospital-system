import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync, symlinkSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { verificationCaseId, verificationIdentities, verificationOutcome } from '../../scripts/verify-ocr-live.mjs';
import { STG_OUTPUT_SOURCE_ORIGIN } from '../../scripts/output-source-live-origin.mjs';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const ts = require('typescript');

const raw = { date: '2026-10-24', daypart: 'lunch', menu_name: 'authored fixture', field: 'qty.no_meat_x', expected_raw: '1' };
function fixture(outcome = 'done') {
  const sourceCase = { case_id: 'verified-source', expected_outcome: 'done_or_review_required', required_raw_cells: [raw], sha256: 'a'.repeat(64) };
  const review = outcome === 'recognition_review_required';
  const workflow = { order_id: 'actual-order', ocr_job: { order_id: 'actual-order', order_document_id: 'actual-document',
    ocr_job_id: 'job', workflow_attempt_id: 'attempt', status: review ? 'failed' : 'done',
    ...(review ? { result_state: 'review_required', recovery_action: 'review_recognition_cells' }
      : { result_state: 'evidence_ready', processing_stage: 'evidence_ready',
        error: null, error_message: null, error_detail: null, error_user_message: null }) } };
  const record = { ...raw, raw_text: '1', recognizer_raw_text: '1', resolved_value: '1', resolution_state: 'resolved' };
  delete record.expected_raw;
  const records = [{ raw_text: '1', normalized_value: '1', engine_metadata: { sheet_cell: 'synthetic', recognizer_output: record } }];
  const cells = [];
  if (review) {
    records.push({ raw_text: '-', normalized_value: null, engine_metadata: { sheet_cell: 'review', recognizer_output:
      { resolution_state: 'review_required', quantity_source_issue: 'hakodate_ocr_quantity_non_numeric' } } });
    cells.push({ sheet_cell: 'review', raw_text: '-', reason: 'hakodate_ocr_quantity_non_numeric' });
  }
  const evidence = { id: 'actual-evidence', status: review ? 'diagnostic_only' : 'done', selectable: !review,
    producer_version: 'hakodate_best_method_pipeline', payload_json: {
      _ocr_execution: { job_id: 'job', workflow_attempt_id: 'attempt', evidence_run_id: 'actual-evidence',
        selected_pdf_receipt: { order_id: 'actual-order', document_id: 'actual-document', sha256: sourceCase.sha256 } },
      hakodate_ocr_evidence_records: records, ...(review ? { hakodate_recognition_review: { cells, count: cells.length } } : {}),
    } };
  return { sourceCase, workflow, evidence };
}

test('case labels require exactly one safe explicit identity', () => {
  for (const invalid of [{}, { case_id: 'new', source_order_id: 'old' }, ...[null, '', 4, '../x', '/x', '.', '..', 'a/b', 'a\\b'].map(case_id => ({ case_id }))]) {
    assert.throws(() => verificationCaseId(invalid));
  }
  assert.equal(verificationCaseId({ source_order_id: 'ORDlegacy' }), 'ORDlegacy');
  assert.equal(verificationCaseId({ case_id: 'new-label' }), 'new-label');
});

for (const outcome of ['done', 'recognition_review_required']) {
  test(`new source case retains actual ${outcome} contract and owner, with policy visible`, () => {
    const { sourceCase, workflow, evidence } = fixture(outcome);
    assert.equal(verificationOutcome(sourceCase, workflow, evidence), outcome);
    const root = mkdtempSync(join(tmpdir(), 'verified-source-'));
    try {
      const folder = join(root, sourceCase.case_id); mkdirSync(folder);
      const files = { 'manifest.json': { cases: [sourceCase] } };
      writeFileSync(join(root, 'manifest.json'), JSON.stringify(files['manifest.json']));
      for (const [name, value] of Object.entries({
        'order.json': { id: 'actual-order', document_id: 'actual-document', current_version: { document_id: 'actual-document' } },
        'upload-state.json': { current_order_id: 'actual-order', current_document_id: 'actual-document', content_sha256: sourceCase.sha256 },
        'workflow.json': workflow, 'evidence.json': evidence,
        'verification-outcome.json': { manifest_policy: sourceCase.expected_outcome, actual_outcome: outcome },
        'required-raw-cells.json': { count: 1, cells: [raw] },
      })) writeFileSync(join(folder, name), JSON.stringify(value));
      const [identity] = verificationIdentities(root);
      assert.equal(identity.orderId, 'actual-order');
      assert.equal(identity.caseId, sourceCase.case_id);
      assert.equal(identity.manifestPolicy, 'done_or_review_required');
      assert.equal(identity.expectedOutcome, outcome);
      evidence.payload_json.hakodate_ocr_evidence_records[0].normalized_value = '0';
      writeFileSync(join(folder, 'evidence.json'), JSON.stringify(evidence));
      assert.throws(() => verificationIdentities(root));
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}

test('union refuses old cases, incompatible modes, hard failures and inconsistent review cells', () => {
  for (const change of ['legacy', 'native_empty_resolution', 'review_resolution', 'blank_resolution', 'missing_raw', 'hardfail', 'job', 'cells', 'done_with_review']) {
    const { sourceCase, workflow, evidence } = fixture(change === 'done_with_review' ? 'done' : 'recognition_review_required');
    if (change === 'legacy') { sourceCase.source_order_id = sourceCase.case_id; delete sourceCase.case_id; }
    else if (change.endsWith('_resolution')) sourceCase[change] = {};
    else if (change === 'missing_raw') delete sourceCase.required_raw_cells;
    else if (change === 'hardfail') workflow.ocr_job.result_state = 'error';
    else if (change === 'job') evidence.payload_json._ocr_execution.job_id = 'foreign';
    else if (change === 'cells') evidence.payload_json.hakodate_recognition_review.cells[0].raw_text = '0';
    else evidence.payload_json.hakodate_recognition_review = { cells: [{ raw_text: '-' }], count: 1 };
    assert.throws(() => verificationOutcome(sourceCase, workflow, evidence), change);
  }
});

test('union done rejects every absent, unknown and contradictory state without changing scalar policy', () => {
  for (const field of ['result_state', 'processing_stage']) {
    for (const value of [undefined, null, 'error', 'hard_failed', 'processing', 'persist_pending', 'unknown', 'draft_blocked', 'review_required', '']) {
      const { sourceCase, workflow, evidence } = fixture();
      if (value === undefined) delete workflow.ocr_job[field];
      else workflow.ocr_job[field] = value;
      assert.throws(() => verificationOutcome(sourceCase, workflow, evidence), `${field}:${value}`);
      assert.equal(verificationOutcome({ source_order_id: 'legacy', expected_outcome: 'done' }, workflow, evidence), 'done');
    }
  }
});

test('union done requires the canonical serialized error fields to be explicitly null', () => {
  for (const field of ['error', 'error_message', 'error_detail', 'error_user_message']) {
    for (const value of [undefined, 'failed', '', false, 0]) {
      const { sourceCase, workflow, evidence } = fixture();
      if (value === undefined) delete workflow.ocr_job[field];
      else workflow.ocr_job[field] = value;
      assert.throws(() => verificationOutcome(sourceCase, workflow, evidence), `${field}:${value}`);
    }
  }
});

test('case collision and symlink escape cannot choose an artifact owner', () => {
  const root = mkdtempSync(join(tmpdir(), 'verified-label-'));
  try {
    mkdirSync(join(root, 'same'));
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({ cases: [{ case_id: 'same' }, { source_order_id: 'same' }] }));
    assert.throws(() => verificationIdentities(root));
    rmSync(join(root, 'same'), { recursive: true });
    symlinkSync(root, join(root, 'escape'));
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({ cases: [{ case_id: 'escape' }] }));
    assert.throws(() => verificationIdentities(root));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('mixed legacy and case-1 labels keep distinct ordinal browser outputs and exact owner metadata', async () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'verified-browser-ordinal-')));
  const artifact = join(root, 'artifact'); mkdirSync(artifact);
  const output = join(root, 'browser');
  const legacy = fixture();
  legacy.sourceCase = { source_order_id: 'legacy-source', expected_outcome: 'done', sha256: legacy.sourceCase.sha256 };
  const current = fixture(); current.sourceCase.case_id = 'case-1';
  const inputs = [legacy, current];
  try {
    for (const [index, { sourceCase, workflow, evidence }] of inputs.entries()) {
      const label = verificationCaseId(sourceCase);
      const orderId = `actual-order-${index}`;
      const documentId = `actual-document-${index}`;
      workflow.order_id = workflow.ocr_job.order_id = orderId;
      workflow.ocr_job.order_document_id = documentId;
      evidence.payload_json._ocr_execution.selected_pdf_receipt.order_id = orderId;
      evidence.payload_json._ocr_execution.selected_pdf_receipt.document_id = documentId;
      const folder = join(artifact, label); mkdirSync(folder);
      for (const [name, value] of Object.entries({
        'order.json': { id: orderId, document_id: documentId, current_version: { document_id: documentId } },
        'workflow.json': workflow, 'evidence.json': evidence,
        'upload-state.json': { current_order_id: orderId, current_document_id: documentId, content_sha256: sourceCase.sha256 },
        ...(index === 1 ? {
          'verification-outcome.json': { manifest_policy: sourceCase.expected_outcome, actual_outcome: 'done' },
          'required-raw-cells.json': { count: 1, cells: [raw] },
        } : {}),
      })) writeFileSync(join(folder, name), JSON.stringify(value));
    }
    writeFileSync(join(artifact, 'manifest.json'), JSON.stringify({ cases: inputs.map(item => item.sourceCase) }));
    const source = readFileSync(new URL('../../scripts/verify-ocr-live.mjs', import.meta.url), 'utf8');
    const ast = ts.createSourceFile('verify-ocr-live.mjs', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    const main = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'main');
    assert.ok(main);
    const calls = [];
    const processFixture = { argv: ['node', 'verify', artifact, output], env: {
      GITHUB_ACTIONS: 'true', GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_REF: 'refs/heads/develop',
      WEB_URL: STG_OUTPUT_SOURCE_ORIGIN, GITHUB_SHA: 'a'.repeat(40), LIVE_ID_TOKEN: 'offline-fixture',
    } };
    const context = { assert, resolve, mkdirSync, existsSync, realpathSync, verificationIdentities,
      STG_OUTPUT_SOURCE_ORIGIN, process: processFixture,
      writeJson: (path, value) => writeFileSync(path, JSON.stringify(value)),
      verifyOcrBrowser: async ({ identity, output: folder }) => {
        calls.push({ identity, folder });
        const result = { status: 'passed', orderId: identity.orderId, documentId: identity.documentId,
          evidenceId: identity.evidenceId, sourcePDFhash: identity.sourcePDFhash,
          ...(Object.hasOwn(identity, 'caseId') ? { caseId: identity.caseId } : {}) };
        writeFileSync(join(folder, 'browser-result.json'), JSON.stringify(result));
        return result;
      },
    };
    const run = () => runInNewContext(`${main.getText(ast)}; main()`, context);
    await run();
    assert.equal(processFixture.exitCode, 0);
    assert.deepEqual(calls.map(call => call.folder), [join(output, 'case-1'), join(output, 'case-2')]);
    assert.equal(calls[0].identity.orderId, 'actual-order-0');
    assert.equal(Object.hasOwn(calls[0].identity, 'caseId'), false);
    assert.equal(calls[1].identity.caseId, 'case-1');
    assert.equal(calls[1].identity.orderId, 'actual-order-1');
    const results = [1, 2].map(index => JSON.parse(readFileSync(join(output, `case-${index}`, 'browser-result.json'), 'utf8')));
    assert.equal(results[0].orderId, 'actual-order-0');
    assert.equal(results[1].orderId, 'actual-order-1');
    assert.equal(results[1].caseId, 'case-1');
    const summary = JSON.parse(readFileSync(join(output, 'browser-result.json'), 'utf8'));
    assert.equal(summary.cases[0].orderId, 'actual-order-0');
    assert.equal(summary.cases[1].caseId, 'case-1');
    assert.equal(summary.cases[1].orderId, 'actual-order-1');
    rmSync(join(output, 'case-2'), { recursive: true });
    symlinkSync(artifact, join(output, 'case-2'));
    await assert.rejects(run());
    assert.equal(calls.length, 3); // The escaped second output never reaches the browser verifier.
  } finally { rmSync(root, { recursive: true, force: true }); }
});
