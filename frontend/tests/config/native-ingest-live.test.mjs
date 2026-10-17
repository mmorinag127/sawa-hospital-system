import assert from 'node:assert/strict';
import test from 'node:test';
import { assertNativeIngestOwner, verificationOutcome } from '../../scripts/verify-ocr-live.mjs';

function fixture(outcome = 'done') {
  const review = outcome === 'recognition_review_required';
  const source = { case_id: 'authored-source', expected_execution_mode: 'native_ingest',
    expected_outcome: 'done_or_review_required', required_raw_cells: [{ expected_raw: '1' }], sha256: 'a'.repeat(64) };
  const terminal = { job_id: 'native-job', execution_id: 'native-execution', order_id: 'order',
    evidence_run_id: 'evidence', workflow_attempt_id: null, status: review ? 'failed' : 'done', error: review ? 'review' : null };
  const job = { ocr_job_id: 'native-job', order_id: 'order', order_document_id: 'document', workflow_attempt_id: null,
    native_ingest: { mode: 'hakodate', intake_protocol: 'native_ingest_v2', execution_id: 'native-execution', terminal_receipt: terminal },
    status: terminal.status, result_state: review ? 'review_required' : 'evidence_ready',
    processing_stage: review ? 'rejected_review_saved' : 'evidence_ready', recovery_action: review ? 'review_recognition_cells' : null,
    error: terminal.error, error_message: terminal.error, error_detail: null, error_user_message: null };
  const cells = [{ sheet_cell: 'authored-review', raw_text: '-', reason: 'hakodate_ocr_quantity_non_numeric' }];
  const evidence = { id: 'evidence', producer_version: 'hakodate_best_method_pipeline', status: review ? 'diagnostic_only' : 'done',
    selectable: !review, payload_json: {
      _ocr_execution: { job_id: 'native-job', nonworkflow_execution_id: 'native-execution', workflow_attempt_id: null,
        evidence_run_id: 'evidence', selected_pdf_receipt: { order_id: 'order', document_id: 'document', sha256: source.sha256 } },
      hakodate_ocr_evidence_records: review ? [{ raw_text: '-', engine_metadata: { sheet_cell: 'authored-review',
        recognizer_output: { resolution_state: 'review_required', quantity_source_issue: 'hakodate_ocr_quantity_non_numeric' } } }] : [],
      ...(review ? { hakodate_recognition_review: { count: 1, cells } } : {}),
    } };
  return { source, workflow: { order_id: 'order', state: review ? 'ocr_failed' : 'ocr_completed', ocr_job: job }, evidence };
}

for (const outcome of ['done', 'recognition_review_required']) {
  test(`native ${outcome} uses its actual nonworkflow job and receipt without an invented attempt`, () => {
    const { source, workflow, evidence } = fixture(outcome);
    assertNativeIngestOwner(source, workflow, evidence);
    assert.equal(verificationOutcome(source, workflow, evidence), outcome);
    assert.equal(workflow.ocr_job.workflow_attempt_id, null);
    assert.equal(verificationOutcome({ ...source, expected_outcome: outcome }, workflow, evidence), outcome);
    assert.throws(() => verificationOutcome({ ...source, expected_outcome: outcome === 'done' ? 'recognition_review_required' : 'done' }, workflow, evidence));
  });
}

for (const changed of ['marker', 'mode', 'protocol', 'missing_protocol', 'execution', 'job', 'receipt', 'evidence', 'order', 'document', 'hash',
  'attempt', 'hard_failure', 'unknown_state', 'not_renderable']) {
  test(`native rejects ${changed} rather than accepting legacy or foreign evidence`, () => {
    const { source, workflow, evidence } = fixture();
    const job = workflow.ocr_job;
    if (changed === 'marker') delete job.native_ingest;
    else if (changed === 'mode') job.native_ingest.mode = 'legacy';
    else if (changed === 'protocol') job.native_ingest.intake_protocol = 'unknown';
    else if (changed === 'missing_protocol') delete job.native_ingest.intake_protocol;
    else if (changed === 'execution') evidence.payload_json._ocr_execution.nonworkflow_execution_id = 'foreign';
    else if (changed === 'job') job.ocr_job_id = 'foreign';
    else if (changed === 'receipt') job.native_ingest.terminal_receipt = null;
    else if (changed === 'evidence') evidence.id = 'foreign';
    else if (changed === 'order') workflow.order_id = 'foreign';
    else if (changed === 'document') job.order_document_id = 'foreign';
    else if (changed === 'hash') evidence.payload_json._ocr_execution.selected_pdf_receipt.sha256 = 'b'.repeat(64);
    else if (changed === 'attempt') job.workflow_attempt_id = 'fabricated';
    else if (changed === 'hard_failure') job.status = 'failed';
    else if (changed === 'unknown_state') job.result_state = 'unknown';
    else workflow.state = 'context_confirmed';
    assert.throws(() => verificationOutcome(source, workflow, evidence));
  });
}

test('native compatibility never supplies a missing workflow attempt to the old workflow union', () => {
  const { source, workflow, evidence } = fixture();
  delete source.expected_execution_mode;
  assert.throws(() => verificationOutcome(source, workflow, evidence));
});
