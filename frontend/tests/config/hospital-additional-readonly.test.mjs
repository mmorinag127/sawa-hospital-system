import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readTogether, requireSnapshot, baseRows, tablePage, orderFormOptions, shippingSuccess, shippingGroups, verifyAdditionalReadonlyPages } from '../../scripts/hospital-additional-readonly-pages.mjs';
import { matchesReadonlyResponse, assertNoVisibleReadMessages, businessPath, readonlyPageStatus } from '../../scripts/hospital-readonly-pages.mjs';

const origin = 'https://example.invalid';
test('all query-specific waiters register before the single navigation; delayed replies retain their owners', async () => {
  const events = [], pending = [];
  const targets = ['global', 'menu', 'facility'].map(rule_type => ({ path: '/api/menu-rules', query: { rule_type } }));
  const result = readTogether(async (path, action, query) => {
    events.push(query.rule_type);
    const body = new Promise(resolve => pending.push(resolve));
    await action();
    return body;
  }, targets, async () => { events.push('navigation'); });
  await Promise.resolve();
  assert.deepEqual(events, ['global', 'menu', 'facility', 'navigation']);
  pending[2]('facility'); pending[0]('global'); pending[1]('menu');
  assert.deepEqual(await result, ['global', 'menu', 'facility']);
});
test('paired order-form replies also wait before navigation and propagate missing/failed response', async () => {
  const events = [];
  await assert.rejects(readTogether(async (path, action) => {
    events.push(path); await action();
    if (path.endsWith('patterns')) throw new Error('failed-response');
    return {};
  }, [{ path: '/api/facilities' }, { path: '/api/order-forms/patterns' }], () => { events.push('navigation'); }), /failed-response/);
  assert.deepEqual(events, ['/api/facilities', '/api/order-forms/patterns', 'navigation']);
});
test('receipt cannot substitute another rule_type, duplicate query, origin or method', () => {
  const response = (query, method = 'GET', host = origin) => ({ url: () => host + '/api/menu-rules' + query, request: () => ({ method: () => method }) });
  assert.equal(matchesReadonlyResponse(response('?rule_type=menu'), origin, '/api/menu-rules', { rule_type: 'menu' }), true);
  for (const value of [response('?rule_type=global'), response(''), response('?rule_type=menu&rule_type=menu'), response('?rule_type=menu', 'POST'), response('?rule_type=menu', 'GET', 'https://other.invalid')]) {
    assert.equal(matchesReadonlyResponse(value, origin, '/api/menu-rules', { rule_type: 'menu' }), false);
  }
});
test('snapshot IDs/revision/type are required; base display ordering preserves zero and input', () => {
  const data = { revision: 'r', items: [{ id: 'b', cycle_day: 2 }, { id: 'a', cycle_day: 1, qty_per_serving: 0 }] };
  assert.deepEqual(baseRows(data).map(row => row.id), ['a', 'b']);
  assert.equal(baseRows(data)[0].qty_per_serving, 0);
  assert.equal(data.items[0].id, 'b');
  for (const value of [{ items: [] }, { revision: 'r' }, { revision: 'r', items: [{ id: 'a' }, { id: 'a' }] }]) assert.throws(() => requireSnapshot(value, 'items'));
  assert.throws(() => requireSnapshot({ revision: 'r', rules: [{ id: 'a', rule_type: 'menu' }] }, 'rules', 'facility'));
});
function tableFixture(values, total = values.length, count = Math.max(1, values.length)) {
  class Locator {
    constructor(kind = '', row = 0, column = 0) { Object.assign(this, { kind, row, column }); }
    locator(kind) { return new Locator(kind, this.row, this.column); }
    nth(index) { return new Locator(this.kind, this.kind === 'tbody tr' ? index : this.row, this.kind === 'td' ? index : this.column); }
  }
  return {
    page: { getByRole: () => new Locator() }, mark: () => {},
    expect: locator => ({
      toBeVisible: async () => {},
      toHaveCount: async expected => assert.equal(count, expected),
      toHaveText: async expected => {
        const actual = locator.kind === '.MuiTablePagination-displayedRows' ? `${values.length ? 1 : 0}–${values.length} / ${total} 件`
          : locator.kind === 'tbody' ? '該当データがありません。' : String(values[locator.row][locator.column]);
        assert.equal(actual, expected);
      },
    }),
  };
}
test('table checks first 25 rows, total and exact displayed values, rejecting stale or wrong display', async () => {
  const rows = Array.from({ length: 26 }, (_, index) => [index]);
  await tablePage(tableFixture(rows.slice(0, 25), 26), '一覧', rows, row => [[0, row[0]]]);
  await tablePage(tableFixture([]), '一覧', [], row => row);
  for (const fixture of [tableFixture(rows.slice(0, 25), 25), tableFixture(rows.slice(0, 25), 26, 24), tableFixture([[999], ...rows.slice(1, 25)], 26)]) {
    await assert.rejects(tablePage(fixture, '一覧', rows, row => [[0, row[0]]]));
  }
});
test('order-form option labels/values derive from both actual responses; absent fallback rejected', () => {
  assert.deepEqual(orderFormOptions({ facilities: [{ id: 'F', name: '施設名' }] }, 'facilities'), [{ value: 'F', text: '施設名 (F)' }]);
  assert.deepEqual(orderFormOptions({ patterns: [{ pattern_id: 'P', label: '標準' }] }, 'patterns'), [{ value: 'P', text: '標準 (P)' }]);
  for (const data of [{}, { patterns: [] }, { patterns: [{ pattern_id: '' }] }, { patterns: [{ pattern_id: 'P' }, { pattern_id: 'P' }] }]) assert.throws(() => orderFormOptions(data, 'patterns'));
});
const item = { id: 'h', tracking_number: '123', tracking_key: 'k', status: '配達完了', ship_date: '2026-09-13' };
const summary = { total: 1, pending: 0, delivered: 1, errors: 0 };
test('shipping success text and selected groups come from API counts/dates, not generic success regex', () => {
  const latest = { summary, view: 'recent', base_date: '2026-09-15', window_days: 31, groups: [{ group_date: '2026-09-13', items: [item] }], items: [item] };
  assert.equal(shippingSuccess(latest), '基準日 2026-09-15 の±31日を取得しました。');
  assert.deepEqual(shippingGroups(latest, false).get('2026-09-13'), [item]);
  const history = { summary, items: [item] };
  assert.equal(shippingSuccess(history, true), '監査ログを取得しました（1件）。');
  assert.deepEqual(shippingGroups(history, true).get('2026-09-13'), [item]);
  assert.throws(() => shippingSuccess({ ...history, items: [] }, true));
  assert.throws(() => shippingSuccess({ ...latest, groups: undefined }));
  assert.throws(() => shippingSuccess({ ...latest, view: 'all' }));
});
function messagePage(path, text, plain = true) {
  const message = { isVisible: async () => true, innerText: async () => text, evaluate: async () => plain, getAttribute: async () => null };
  return { url: () => origin + path, locator: () => ({ count: async () => 1, nth: () => message }) };
}
test('shipping exception is exact/page-specific/plain-message only; business errors and other pages still fail', async () => {
  const expected = shippingSuccess({ summary, items: [item] }, true);
  await assertNoVisibleReadMessages(messagePage('/hospital/shipping-history', expected), {}, expected);
  for (const page of [messagePage('/hospital/base-menus', expected), messagePage('/hospital/shipping-history', '取得に失敗しました。'), messagePage('/hospital/shipping-history', '監査ログを取得しました（2件）。'), messagePage('/hospital/shipping-history', expected, false)]) {
    await assert.rejects(assertNoVisibleReadMessages(page, {}, expected), /readonly-visible-message/);
  }
  await assert.rejects(assertNoVisibleReadMessages(messagePage('/hospital/shipping-history', expected)), /readonly-visible-message/);
  for (const status of [401, 403, 404, 500]) assert.notEqual(readonlyPageStatus([{ status }], true), 'passed');
});
test('each new page runs independently even when every API fails', async () => {
  const names = [], errors = [];
  await verifyAdditionalReadonlyPages({ page: {}, expect: () => {}, mark: () => {}, navigate: () => {},
    receipt: async () => { throw new Error('http-failed'); },
    check: async (name, action) => { names.push(name); try { await action(); } catch (error) { errors.push(error); } },
  });
  assert.deepEqual(names, ['base-menus', 'menu-rules', 'order-forms', 'shipping-history', 'system-process-logs']);
  assert.equal(errors.length, 5);
});
test('source-backed links/controls and existing aggregate guard remain; manuals stay last', () => {
  const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
  const runner = read('../../scripts/hospital-readonly-pages.mjs');
  assert.ok(runner.indexOf('await verifyAdditionalReadonlyPages(') < runner.indexOf("await check('manual-library'"));
  assert.ok(runner.includes("assert.ok(pages.every(entry => entry.status === 'passed'))"));
  assert.ok(runner.includes('matchesReadonlyResponse(response, origin, path, query)'));
  for (const path of ['base-menus', 'menu-rules', 'order-forms', 'shipping-history', 'system-process-logs']) {
    assert.equal(businessPath('/hospital/' + path), true);
    assert.ok(read('../../src/components/TopNav.tsx').includes(`href: "/${path}"`));
  }
  assert.equal(businessPath('/hospital/weekly-orders'), false);
  for (const [page, literal] of [['base-menus', '全件再読込'], ['menu-rules', 'params: { rule_type: ruleType }'], ['order-forms', '注文書パターン'], ['shipping-history', '監査ログを取得しました（${nextItems.length}件）。'], ['system-process-logs', 'aria-label="処理ログ詳細"']]) assert.ok(read(`../../src/pages/${page}.tsx`).includes(literal));
  const implementation = read('../../scripts/hospital-additional-readonly-pages.mjs');
  assert.doesNotMatch(implementation, /\bfetch\(|apiClient|\.post\(|\.put\(|\.delete\(/);
});
test('operator process-log navigation uses the dashboard link after registering its receipt', async () => {
  const dashboard = readFileSync(new URL('../../src/pages/hospital-dashboard.tsx', import.meta.url), 'utf8');
  assert.ok(dashboard.includes('<Link href="/system-process-logs"'));
  const events = [];
  const stop = new Error('stop-after-observed-navigation');
  await verifyAdditionalReadonlyPages({
    page: {}, expect: () => {}, mark: () => {},
    navigate: async path => { events.push(['navigate', path]); },
    receipt: async (path, action, query) => {
      events.push(['receipt', path, query]);
      await action();
      throw stop;
    },
    check: async (name, action) => {
      if (name === 'system-process-logs') await assert.rejects(action, error => error === stop);
    },
  });
  assert.deepEqual(events, [
    ['navigate', '/hospital'],
    ['receipt', '/api/system/process-logs', { limit: '100' }],
    ['navigate', '/system-process-logs'],
  ]);
});
