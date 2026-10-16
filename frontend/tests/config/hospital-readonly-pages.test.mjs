import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { businessPath, observedBusinessLink, readonlyPageStatus, safeBusinessScreenshot, assertNoVisibleReadMessages, READONLY_MESSAGE_SURFACES } from '../../scripts/hospital-readonly-pages.mjs';

const origin = 'https://web-stg-avlnzjjrca-dt.a.run.app';

test('visible nonempty message rejects despite existing content; hidden and empty messages do not', async () => {
  function page(rows) {
    return { locator: selector => {
      assert.equal(selector, READONLY_MESSAGE_SURFACES);
      return { count: async () => rows.length, nth: index => ({
        isVisible: async () => rows[index].visible,
        innerText: async () => rows[index].text,
      }) };
    } };
  }
  for (const text of ['月次メニューを読み込めません。', 'secret server response', '未知の通知', '全件確認完了']) {
    await assert.rejects(assertNoVisibleReadMessages(page([{ visible: true, text }])),
      error => error.message === 'readonly-visible-message' && !error.message.includes(text));
  }
  await assertNoVisibleReadMessages(page([{ visible: false, text: 'error' }, { visible: true, text: ' \n ' }]));
  await assert.rejects(assertNoVisibleReadMessages(page([
    { visible: false, text: 'old error' }, { visible: true, text: 'current failure' },
  ])), /readonly-visible-message/);
});

test('product message surfaces are covered before page success is recorded', () => {
  for (const path of ['menus/[monthId].tsx', 'facility-master.tsx', 'facilities/[id].tsx']) {
    const source = readFileSync(new URL('../../src/pages/' + path, import.meta.url), 'utf8');
    assert.ok(source.includes('<p className="message">'));
  }
  assert.ok(READONLY_MESSAGE_SURFACES.split(', ').includes('p.message'));
  for (const selector of ['.error-box', '.archive-feedback-error', '.pending-meta.warn', '[role="alert"]']) assert.ok(READONLY_MESSAGE_SURFACES.includes(selector));
  const helper = readFileSync(new URL('../../scripts/hospital-readonly-pages.mjs', import.meta.url), 'utf8');
  assert.ok(helper.indexOf('await assertNoVisibleReadMessages(page);') < helper.indexOf('entry.loadingFinished = true;'));
});
test('screenshots allow only enumerated business paths, never login or arbitrary pages', () => {
  for (const path of ['/hospital', '/hospital/orders', '/hospital/facility-orders', '/hospital/menus/2026-09', '/facilities/FAC00015', '/hospital/menu-masters', '/hospital/daily-delivery-notes']) assert.equal(businessPath(path), true);
  for (const path of ['/login', '/hospital/login', '/admin/users', '/api/auth', '/hospital/orders/ORD37344b72', '/hospital/menus/2026-09/download']) assert.equal(businessPath(path), false);
});

test('observed navigation rejects external, query-bearing and wrong destinations', () => {
  assert.equal(observedBusinessLink(origin, '/hospital/orders', '/hospital/orders'), origin + '/hospital/orders');
  for (const href of ['https://evil.test/hospital/orders', '/hospital/orders?token=secret', '/hospital/orders#secret', '/hospital/menu-masters', '/login']) assert.throws(() => observedBusinessLink(origin, href, '/hospital/orders'));
});

test('403 and 401 cannot pass; other HTTP errors cannot pass with rendered headings', () => {
  assert.equal(readonlyPageStatus([{ status: 403 }], true), 'blocked');
  assert.equal(readonlyPageStatus([{ status: 401 }], false), 'blocked');
  assert.equal(readonlyPageStatus([{ status: 500 }], true), 'failed');
  assert.equal(readonlyPageStatus([{ status: 200 }], false), 'failed');
  assert.equal(readonlyPageStatus([{ status: 200 }], true), 'passed');
});

test('login screens never produce a screenshot even on a business URL', async () => {
  for (const [path, password, login, allowed] of [['/login', 0, 0, false], ['/hospital', 1, 0, false], ['/hospital', 0, 1, false], ['/hospital', 0, 0, true], ['/hospital/daily-delivery-notes', 0, 0, true]]) {
    let shots = 0;
    const page = { url: () => origin + path, locator: () => ({ count: async () => password }),
      getByRole: () => ({ count: async () => login }), screenshot: async () => { shots++; } };
    assert.equal(await safeBusinessScreenshot(page, origin, '/unused.png'), allowed);
    assert.equal(shots, allowed ? 1 : 0);
  }
});

test('coverage is wired after daily assertions and retains transport/ownership guards', () => {
  const main = readFileSync(new URL('../../scripts/verify-output-source-live.mjs', import.meta.url), 'utf8');
  assert.ok(main.indexOf('await verifyHospitalReadonlyPages(') > main.indexOf('result.sectionPngs.push(filename)'));
  assert.ok(main.indexOf('result.finalPathname = sameOriginPathname(origin, page.url());') > main.indexOf('await verifyHospitalReadonlyPages('));
  for (const guard of ["new Set(['GET', 'HEAD'])", "route.abort('blockedbyclient')", 'assert.equal(result.unexpectedWriteCount, 0)', 'assert.equal(result.pageErrorCount, 0)', 'await context.close()', 'await browser.close()', "await expect(fetchButton).toBeEnabled();", 'assert.equal(complete, true)']) assert.ok(main.includes(guard), guard);
  assert.ok(!main.includes('setDefaultTimeout'));
  const serializer = readFileSync(new URL('../../../backend/src/services/order_serialization_service.py', import.meta.url), 'utf8');
  assert.ok(serializer.slice(serializer.indexOf('def serialize_order_summary(')).includes('"facility": order.facility_code'));
});

test('page selectors are grounded in product source; no edit/upload/export actions', () => {
  const helper = readFileSync(new URL('../../scripts/hospital-readonly-pages.mjs', import.meta.url), 'utf8');
  const sources = [
    ['hospital-dashboard.tsx', '月次の進捗と未確定を一画面で確認'],
    ['orders/index.tsx', '注文一覧'], ['menus/[monthId].tsx', '登録済み月次'],
    ['menus/[monthId].tsx', '月次メニュー一覧'], ['facility-master.tsx', '施設一覧'],
    ['facilities/[id].tsx', '指定日の版を取得'], ['menu-masters.tsx', 'メニューマスター一覧'],
  ];
  for (const [file, label] of sources) {
    assert.ok(readFileSync(new URL('../../src/pages/' + file, import.meta.url), 'utf8').includes(label));
    assert.ok(helper.includes(label));
  }
  for (const name of ['dashboard', 'orders', 'facility-orders', 'monthly-list', 'monthly-detail', 'facilities', 'facility-detail', 'menu-masters']) assert.ok(helper.includes(`await check('${name}'`));
  assert.ok(helper.includes('dailyOrders.filter(row => row.id === orderId)'));
  assert.ok(helper.includes('data.items?.filter(row => row.id === month).length, 1'));
  assert.ok(helper.includes('assert.deepEqual(data.version.facility, data.facility)'));
  assert.ok(!helper.includes('request.post'));
  assert.ok(!helper.includes('request.put'));
});
