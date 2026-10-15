import assert from 'node:assert/strict';
import test from 'node:test';
import { expectedWorkflowRead } from '../../scripts/verify-ocr-live.mjs';

test('workflow verification matches the actual order API, not the page namespace', () => {
  for (const suffix of ['', '/workflow-v2', '/workflow-v2/ocr-results']) {
    assert.equal(expectedWorkflowRead(`/api/orders/ORD1${suffix}`, 'ORD1'), true);
    assert.equal(expectedWorkflowRead(`/api/hospital/orders/ORD1${suffix}`, 'ORD1'), false);
    assert.equal(expectedWorkflowRead(`/api/orders/ORD2${suffix}`, 'ORD1'), false);
  }
  assert.equal(expectedWorkflowRead('/api/orders/ORD1/document', 'ORD1'), false);
});
