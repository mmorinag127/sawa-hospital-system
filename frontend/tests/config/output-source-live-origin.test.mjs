import assert from 'node:assert/strict';
import test from 'node:test';
import { STG_OUTPUT_SOURCE_ORIGIN, isAllowedOutputSourceOrigin } from '../../scripts/output-source-live-origin.mjs';
import { DAILY_OUTPUT_SECTIONS, OPERATOR_DAILY_DELIVERY_PATH, OUTPUT_SECTION_PNGS, hasCompleteDailyOutputReceipt, recordAllowedResponse, sanitizedFailureCode, sameOriginPathname, settleDailyOutputReceipts } from '../../scripts/verify-output-source-live.mjs';

test('output-source browser verifier only accepts the exact staging origin', () => {
  assert.equal(OPERATOR_DAILY_DELIVERY_PATH, '/hospital/daily-delivery-notes');
  assert.deepEqual(OUTPUT_SECTION_PNGS, [['当日袋分け一覧', 'daily-bags-section.png'], ['当日総量', 'daily-totals-section.png']]);
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

test('browser receipt fixture requires all three completed output sections before rendered completion', () => {
  const fulfilled = {
    orders: { status: 'fulfilled', data: { orders: [{ id: 'ORD1' }] } },
    meal_counts: { status: 'fulfilled', data: { groups: [{ daypart: '昼' }] } },
    daily_bags: { status: 'fulfilled', data: { groups: [{ menu_name: 'A' }] } },
    daily_bags_audit: { status: 'fulfilled', data: {} },
    totals: { status: 'fulfilled', data: { rows: [{ quantity: 1 }] } },
  };
  assert.deepEqual(Object.keys(DAILY_OUTPUT_SECTIONS), ['primary', 'bags', 'totals']);
  assert.equal(hasCompleteDailyOutputReceipt('primary', { sections: fulfilled }), true);
  assert.equal(hasCompleteDailyOutputReceipt('bags', { sections: fulfilled }), true);
  assert.equal(hasCompleteDailyOutputReceipt('totals', { sections: fulfilled }), true);
  assert.equal(hasCompleteDailyOutputReceipt('bags', { sections: { ...fulfilled, daily_bags: { status: 'fulfilled', data: { groups: [] } } } }), false);
  assert.equal(hasCompleteDailyOutputReceipt('totals', { sections: { ...fulfilled, totals: { status: 'rejected', error: {} } } }), false);
  assert.equal(hasCompleteDailyOutputReceipt('primary', { sections: { orders: fulfilled.orders, meal_counts: fulfilled.meal_counts } }), true);
  assert.equal(hasCompleteDailyOutputReceipt('primary', { sections: { orders: fulfilled.orders } }), false);
});

test('browser receipt aggregation handles all waiter rejections before click completion', async () => {
  const rejected = await settleDailyOutputReceipts([Promise.resolve('primary'), Promise.reject(new Error('secret transport detail')), Promise.resolve('totals')]);
  assert.deepEqual(rejected, { ok: false, receipts: [] });
  const fulfilled = await settleDailyOutputReceipts([Promise.resolve('primary'), Promise.resolve('bags'), Promise.resolve('totals')]);
  assert.deepEqual(fulfilled, { ok: true, receipts: ['primary', 'bags', 'totals'] });
});
