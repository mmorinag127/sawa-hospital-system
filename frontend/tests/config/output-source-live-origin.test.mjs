import assert from 'node:assert/strict';
import test from 'node:test';
import { STG_OUTPUT_SOURCE_ORIGIN, isAllowedOutputSourceOrigin } from '../../scripts/output-source-live-origin.mjs';
import { OPERATOR_DAILY_DELIVERY_PATH, recordAllowedResponse, sanitizedFailureCode, sameOriginPathname } from '../../scripts/verify-output-source-live.mjs';

test('output-source browser verifier only accepts the exact staging origin', () => {
  assert.equal(OPERATOR_DAILY_DELIVERY_PATH, '/hospital/daily-delivery-notes');
  assert.equal(isAllowedOutputSourceOrigin(STG_OUTPUT_SOURCE_ORIGIN), true);
  for (const origin of ['', 'https://web-stg-avlnzjjrca-dt.a.run.app/', 'https://web.example.invalid', 'https://web-prod-avlnzjjrca-dt.a.run.app', 'http://localhost:3000']) {
    assert.equal(isAllowedOutputSourceOrigin(origin), false);
  }
});

test('browser evidence fixture records successful same-origin GET status without query or fragment', () => {
  const result = { phase: 'await-daily-output-context', http: [] };
  assert.equal(recordAllowedResponse(result, {
    origin: STG_OUTPUT_SOURCE_ORIGIN,
    url: `${STG_OUTPUT_SOURCE_ORIGIN}/api/orders/daily-output-context?date=2026-09-13&token=must-not-persist#fragment`,
    method: 'GET', status: 200,
  }), true);
  assert.deepEqual(result.http, [{
    phase: 'await-daily-output-context', method: 'GET', pathname: '/api/orders/daily-output-context', status: 200,
  }]);
  assert.equal(sameOriginPathname(STG_OUTPUT_SOURCE_ORIGIN, `${STG_OUTPUT_SOURCE_ORIGIN}${OPERATOR_DAILY_DELIVERY_PATH}?secret=no#no`), OPERATOR_DAILY_DELIVERY_PATH);
});

test('browser failure fixture retains only the phase-derived code and rejects off-origin or write responses', () => {
  const result = { phase: 'verify-controls', http: [] };
  assert.equal(sanitizedFailureCode(result.phase), 'browser-readonly-check-failed-at-verify-controls');
  assert.equal(sanitizedFailureCode('exception-with-token'), 'browser-readonly-check-failed-at-unknown-phase');
  assert.equal(recordAllowedResponse(result, {
    origin: STG_OUTPUT_SOURCE_ORIGIN, url: 'https://evil.example/api?token=no', method: 'GET', status: 200,
  }), false);
  assert.equal(recordAllowedResponse(result, {
    origin: STG_OUTPUT_SOURCE_ORIGIN, url: `${STG_OUTPUT_SOURCE_ORIGIN}/api/orders`, method: 'POST', status: 200,
  }), false);
  assert.deepEqual(result.http, []);
});
