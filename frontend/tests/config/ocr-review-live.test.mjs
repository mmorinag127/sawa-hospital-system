import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { verificationIdentities } from '../../scripts/verify-ocr-live.mjs';

test('live review verification requires an explicit review outcome and nonselectable evidence', () => {
  const root = mkdtempSync(join(tmpdir(), 'ocr-review-live-'));
  const save = (name, value) => writeFileSync(join(root, name), JSON.stringify(value));
  try {
    mkdirSync(join(root, 'source'));
    const sha256 = 'a'.repeat(64);
    const source = { source_order_id: 'source', sha256, expected_outcome: 'recognition_review_required' };
    const review = { count: 1, cells: [{ sheet_cell: 'F11', raw_text: '1', reason: 'hakodate_blank_input_nonempty_prediction' }] };
    const evidence = { id: 'evidence', status: 'diagnostic_only', selectable: false, payload_json: {
      hakodate_recognition_review: review,
      _ocr_execution: { evidence_run_id: 'evidence', selected_pdf_receipt: { order_id: 'order', document_id: 'doc', sha256 } },
    } };
    save('manifest.json', { cases: [source] });
    save('source/order.json', { id: 'order', document_id: 'doc', current_version: { document_id: 'doc' } });
    save('source/workflow.json', { order_id: 'order', ocr_job: { order_id: 'order', order_document_id: 'doc', status: 'failed', result_state: 'review_required' } });
    save('source/upload-state.json', { current_order_id: 'order', current_document_id: 'doc', content_sha256: sha256 });
    save('source/evidence.json', evidence);
    assert.deepEqual(verificationIdentities(root)[0].review, review);
    for (const invalid of [{ ...evidence, selectable: true }, { ...evidence, status: 'done' },
      { ...evidence, payload_json: { ...evidence.payload_json, hakodate_recognition_review: { ...review, count: 2 } } }]) {
      save('source/evidence.json', invalid);
      assert.throws(() => verificationIdentities(root));
    }
    save('source/evidence.json', evidence);
    for (const expected_outcome of ['done', 'unknown']) {
      save('manifest.json', { cases: [{ ...source, expected_outcome }] });
      assert.throws(() => verificationIdentities(root));
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
