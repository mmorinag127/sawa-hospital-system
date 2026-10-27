import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const require = createRequire(import.meta.url);
const ts = require('typescript');
const source = readFileSync(new URL('../../src/services/dashboardMenuInfo.ts', import.meta.url), 'utf8');
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const classify = (...args) => JSON.parse(JSON.stringify(context.exports.classifyDashboardMenuRead(...args)));
const month = '2026-09';
const valid = { menu: { id: month, filename: 'source.xlsx', display_name: 'monthly' }, items: [{ id: 'a' }, { id: 'b' }], entries: [] };
test('same-month canonical 200 preserves registration count and names', () => {
  assert.deepEqual(classify(month, { status: 200, data: valid }), { status: '登録済み', itemCount: 2, filename: 'source.xlsx', displayName: 'monthly' });
  assert.equal(classify(month, { status: 200, data: { ...valid, items: [] } }).itemCount, 0);
});
test('only canonical not-found 404 means unregistered; other failures remain failures', () => {
  for (const month of ['2027-01', '2027-02']) assert.deepEqual(classify(month, { status: 404, data: { detail: 'not found' } }), { status: '未登録' });
  for (const response of [undefined, {}, { status: 404 }, { status: 404, data: { detail: 'other' } },
    ...[401, 403, 500, 204].map(status => ({ status, data: { detail: 'not found' } }))]) {
    assert.equal(classify(month, response).status, '取得失敗');
  }
});
test('malformed or wrong-month 200 cannot count as registration', () => {
  for (const data of [null, {}, { ...valid, menu: null }, { ...valid, menu: { id: '2026-08' } },
    { ...valid, items: null }, { ...valid, entries: undefined }, { ...valid, menu: { ...valid.menu, filename: 7 } }]) {
    assert.equal(classify(month, { status: 200, data }).status, '取得失敗');
  }
});
test('UI retains cancellation ownership, visible error and unknown loading state', () => {
  const page = readFileSync(new URL('../../src/pages/hospital-dashboard.tsx', import.meta.url), 'utf8');
  assert.ok(page.includes('if (cancelled) return;'));
  assert.ok(page.includes('cancelled = true;'));
  assert.ok(page.includes('/monthly-menus/summaries?'));
  assert.ok(page.includes('classifyDashboardMenuSummary('));
  assert.ok(page.includes('info?.status || "確認中"'));
  assert.ok(page.includes('<p className="message" role="alert">月次メニューの取得に失敗しました。</p>'));
  assert.ok(page.includes('登録済み (${info?.itemCount ?? 0}件)'));
});

test('small monthly summary preserves names and rejects wrong month, malformed count and unknown state', () => {
  const classifySummary = value => JSON.parse(JSON.stringify(context.exports.classifyDashboardMenuSummary(month, value)));
  const value = {month_id: month, status: 'registered', item_count: 2, menu: valid.menu};
  assert.deepEqual(classifySummary(value), {status: '登録済み', itemCount: 2, filename: 'source.xlsx', displayName: 'monthly'});
  assert.deepEqual(classifySummary({month_id: month, status: 'missing', item_count: 0, menu: null}), {status: '未登録'});
  for (const bad of [null, {}, {...value, month_id: '2026-08'}, {...value, item_count: -1},
    {...value, item_count: '2'}, {...value, status: 'unavailable'}, {...value, menu: null}])
    assert.equal(classifySummary(bad).status, '取得失敗');
});
