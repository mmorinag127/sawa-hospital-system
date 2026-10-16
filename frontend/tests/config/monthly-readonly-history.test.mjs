import test from 'node:test';
import assert from 'node:assert/strict';
import { readMonthlyDetailWithHistory, verifyMonthlyHistory } from '../../scripts/hospital-readonly-pages.mjs';

const origin = 'https://example.invalid';
const month = '2026-09';
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function harness(existing = false, status = 200, badBody = false) {
  const history = deferred();
  const events = [];
  const page = {
    url: () => `${origin}/hospital/menus/${existing ? month : '2026-08'}`,
    goto: async url => { assert.equal(url, `${origin}/hospital/menus/${month}`); events.push('navigate'); },
    waitForResponse: predicate => {
      const suffix = events.length === 0 ? '' : '/uploads';
      events.push(suffix || 'menu');
      const response = {
        url: () => `${origin}/api/monthly-menus/${month}${suffix}`,
        request: () => ({ method: () => 'GET' }), status: () => status,
        json: () => { if (badBody) throw new Error('secret'); return suffix ? history.promise : { menu: { id: month } }; },
      };
      assert.equal(predicate(response), true);
      assert.equal(predicate({ ...response, url: () => `${origin}/api/monthly-menus/2026-08/uploads` }), false);
      return Promise.resolve(response);
    },
  };
  return { history, events, args: { page, origin, month, button: { click: async () => events.push('click') }, mark: () => {} } };
}
test('both responses registered before selection; delayed uploads body blocks completion', async () => {
  for (const existing of [false, true]) {
    const h = harness(existing);
    let done = false;
    const pending = readMonthlyDetailWithHistory(h.args).then(v => { done = true; return v; });
    for (let i = 0; i < 10; i++) await Promise.resolve();
    assert.deepEqual(h.events, ['menu', '/uploads', existing ? 'navigate' : 'click']);
    assert.equal(done, false);
    const body = { items: [{ id: 'upload', filename: 'original.xlsx' }] };
    h.history.resolve(body);
    assert.deepEqual(await pending, [{ menu: { id: month } }, body]);
  }
});
test('HTTP and malformed body fail without exposing content', async () => {
  for (const [status, bad] of [[500, false], [200, true]]) {
    const h = harness(false, status, bad);
    await assert.rejects(readMonthlyDetailWithHistory(h.args), { message: 'monthly-response-failed' });
  }
});
function ui(count, filename, total = '1–1 / 1 件') {
  const locator = { locator: () => locator, nth: () => locator };
  return {
    page: { getByRole: () => locator },
    expect: () => ({ toBeVisible: async () => {}, toHaveCount: async expected => assert.equal(count, expected),
      toHaveText: async expected => assert.equal(expected.includes(' 件') ? total : filename, expected) }),
  };
}
test('history UI count, total and filename must match response; malformed history stops', async () => {
  const history = { items: [{ id: 'upload', month_id: month, filename: 'original.xlsx' }] };
  await verifyMonthlyHistory({ ...ui(1, 'original.xlsx'), history, month, mark: () => {} });
  for (const display of [ui(0, 'original.xlsx'), ui(1, 'wrong.xlsx'), ui(1, 'original.xlsx', '0–0 / 0 件')]) {
    await assert.rejects(verifyMonthlyHistory({ ...display, history, month, mark: () => {} }));
  }
  for (const history of [{}, { items: null }, { items: [{}] }, { items: [{ id: 'upload', month_id: '2026-08' }] }]) {
    await assert.rejects(verifyMonthlyHistory({ ...ui(1, ''), history, month, mark: () => {} }));
  }
});
