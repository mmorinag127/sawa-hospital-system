import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { recordAllowedResponse, sanitizedFailureCode, sameOriginPathname, settleResponse, verificationIdentities } from '../../scripts/verify-ocr-live.mjs';
import { STG_OUTPUT_SOURCE_ORIGIN } from '../../scripts/output-source-live-origin.mjs';

test('OCR browser verifier records only same-origin GET/HEAD response metadata', () => {
  const result = { phase: 'await-workflow-reads', http: [] };
  assert.equal(recordAllowedResponse(result, { origin: STG_OUTPUT_SOURCE_ORIGIN,
    url: `${STG_OUTPUT_SOURCE_ORIGIN}/api/hospital/orders/ORD1/workflow-v2/ocr-results?token=secret`, method: 'GET', status: 200 }), true);
  assert.deepEqual(result.http, [{ phase: 'await-workflow-reads', method: 'GET', pathname: '/api/hospital/orders/ORD1/workflow-v2/ocr-results', status: 200 }]);
  assert.equal(recordAllowedResponse(result, { origin: STG_OUTPUT_SOURCE_ORIGIN,
    url: `${STG_OUTPUT_SOURCE_ORIGIN}/api/hospital/orders/ORD1/workflow-v2`, method: 'POST', status: 200 }), false);
  assert.equal(sameOriginPathname(STG_OUTPUT_SOURCE_ORIGIN, `${STG_OUTPUT_SOURCE_ORIGIN}/x?token=secret#fragment`), '/x');
  assert.equal(sanitizedFailureCode('token-from-error'), 'browser-readonly-check-failed-at-unknown-phase');
});

test('OCR browser response waiters settle failures instead of rejecting after navigation errors', async () => {
  assert.deepEqual(await settleResponse(Promise.resolve('response')), { ok: true, response: 'response' });
  assert.deepEqual(await settleResponse(Promise.reject(new Error('transport detail'))), { ok: false, response: null });
});

function writeCase(root, sourceOrder, suffix) {
  const orderId = `ORDverified${suffix}`; const documentId = `doc-verified-${suffix}`;
  const evidenceId = `evidence-verified-${suffix}`; const sha = suffix.repeat(64); const folder = join(root, sourceOrder);
  mkdirSync(folder);
  writeFileSync(join(folder, 'order.json'), JSON.stringify({ id: orderId, document_id: documentId, current_version: { document_id: documentId } }));
  writeFileSync(join(folder, 'workflow.json'), JSON.stringify({ order_id: orderId, ocr_job: { order_id: orderId, order_document_id: documentId, status: 'done' } }));
  writeFileSync(join(folder, 'upload-state.json'), JSON.stringify({ current_order_id: orderId, current_document_id: documentId, content_sha256: sha }));
  writeFileSync(join(folder, 'evidence.json'), JSON.stringify({ id: evidenceId, payload_json: { _ocr_execution: { evidence_run_id: evidenceId, selected_pdf_receipt: { order_id: orderId, document_id: documentId, sha256: sha } } } }));
  const hashes = {a: 'b333d612c0e5a11258353ba2b92a3c4d5d1556009c94af0e28f6938094bfdee5',
    b: 'f8fae47714c45faf9204d75f6dedf4dff41d6ece7f95b77854d22b854c362668'};
  return { source_order_id: sourceOrder, sha256: sha, identity: { orderId, documentId, evidenceId, sourcePDFhash: sha,
    originalPayloadSha256: hashes[suffix], artifactDigest: undefined, selectedPdfReceipt: undefined } };
}

test('OCR verifier validates every manifest artifact case with matching order, document, evidence and source hash', () => {
  const root = mkdtempSync(join(tmpdir(), 'ocr-live-'));
  try {
    const first = writeCase(root, 'source-order-a', 'a');
    const second = writeCase(root, 'source-order-b', 'b');
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({ cases: [second, first].map(({ identity, ...source }) => source) }));
    assert.deepEqual(verificationIdentities(root), [second.identity, first.identity]);
    writeFileSync(join(root, 'source-order-a', 'upload-state.json'), JSON.stringify({ current_order_id: first.identity.orderId,
      current_document_id: first.identity.documentId, content_sha256: 'c'.repeat(64) }));
    assert.throws(() => verificationIdentities(root), /Expected values to be strictly equal/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('original payload digest preserves canonical Unicode, float and large integer bytes', () => {
  const root = mkdtempSync(join(tmpdir(), 'ocr-live-digest-'));
  try {
    const {identity, ...source} = writeCase(root, 'source-order-a', 'a');
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({cases: [source]}));
    writeFileSync(join(root, 'source-order-a', 'evidence.json'),
      `{"id":"evidence-verified-a","payload_json":{"_ocr_execution":{"evidence_run_id":"evidence-verified-a","selected_pdf_receipt":{"order_id":"ORDverifieda","document_id":"doc-verified-a","sha256":"${'a'.repeat(64)}"}},"proof":{"number":1.0,"unicode":"\\u6f22\\u5b57","large":9007199254740993}}}`);
    const [actual] = verificationIdentities(root);
    assert.equal(actual.originalPayloadSha256, '20163d8408843a359e1a82e2a510e66c14020c5a41834797cfea06efd194c5a8');
    assert.equal(actual.orderId, identity.orderId);
  } finally {rmSync(root, {recursive: true, force: true});}
});
