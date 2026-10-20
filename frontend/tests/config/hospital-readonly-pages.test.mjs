import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { READONLY_VISIBLE_ERRORS, loadReadonlyFacilityVersions } from '../../scripts/hospital-readonly-pages.mjs';
import { businessPath, observedBusinessLink, readonlyPageStatus, safeBusinessScreenshot, assertNoVisibleReadMessages, READONLY_MESSAGE_SURFACES, openReadonlyDisclosure, readonlyFailureKind, READONLY_SUBPHASES } from '../../scripts/hospital-readonly-pages.mjs';

const origin = 'https://web-stg-avlnzjjrca-dt.a.run.app';

test('each content assertion has a distinct fixed diagnostic code', () => {
  const source = readFileSync(new URL('../../scripts/hospital-readonly-pages.mjs', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('export async function verifyHospitalReadonlyPages'));
  const lines = body.split('\n');
  const codes = [];
  for (let i = 0; i < lines.length; i++) {
    if (!/^\s*(?:assert\.(?:ok|equal|match|deepEqual)\(|await expect\(|await assertNoVisibleReadMessages\()/.test(lines[i])) continue;
    if (lines[i].includes('READONLY_SUBPHASES.includes')) continue;
    const before = lines[i - 1];
    assert.match(before, /(?:mark\(|responseCheck = )/);
    const code = before.match(/'readonly-check-\d{3}'/);
    assert.ok(code, lines[i]);
    codes.push(code[0]);
  }
  assert.equal(codes.length, 51);
  assert.equal(new Set(codes).size, codes.length);
  assert.ok(source.includes('entry.failedCheck = entry.checkCode;'));
  assert.ok(source.includes('failedCheck: responseCheck'));
  assert.ok(source.includes("responseCheck = 'response-json'"));
});

test('link failures retain only the exact fixed failing assertion code', () => {
  for (const [href, expectedCode] of [
    ['https://evil.test/hospital/orders', 'link-origin'],
    ['/login', 'link-business-path'],
    ['/hospital/menus', 'link-destination'],
    ['/hospital/orders?secret=hidden', 'link-query-empty'],
    ['/hospital/orders#hidden', 'link-fragment-empty'],
  ]) {
    let code;
    assert.throws(() => observedBusinessLink(origin, href, '/hospital/orders', value => { code = value; }));
    assert.equal(code, expectedCode);
    assert.ok(!code.includes('hidden'));
  }
});

test('collapsed menu disclosure is opened once and already open content is retained', async () => {
  for (const initial of [null, '']) {
    let open = initial;
    let clicks = 0;
    const locator = { getAttribute: async name => { assert.equal(name, 'open'); return open; },
      locator: selector => { assert.equal(selector, 'summary'); return { click: async () => { open = ''; clicks++; } }; } };
    await openReadonlyDisclosure(locator);
    await openReadonlyDisclosure(locator);
    assert.equal(clicks, initial === null ? 1 : 0);
  }
});

test('fixed diagnostic phases and error kinds never retain exception details', () => {
  assert.equal(readonlyFailureKind({ name: 'TimeoutError', message: 'secret' }), 'timeout');
  assert.equal(readonlyFailureKind({ name: 'AssertionError', message: 'secret' }), 'assertion');
  assert.equal(readonlyFailureKind({ name: 'secret', message: 'secret' }), 'other');
  for (const name of ['navigation-link', 'response', 'content', 'network-idle', 'source-sha', 'loading', 'visible-errors', 'page-errors', 'screenshot']) assert.ok(READONLY_SUBPHASES.includes(name));
});

test('disclosure and redirect corrections follow product contracts without suppressing dashboard errors', () => {
  const read = path => readFileSync(new URL('../../src/' + path, import.meta.url), 'utf8');
  const orders = read('pages/orders/index.tsx');
  assert.ok(orders.includes('useState<Record<string, boolean>>({})'));
  assert.ok(orders.includes('aria-expanded={expanded}'));
  const menu = read('pages/menus/[monthId].tsx');
  assert.ok(menu.includes('<details className="menu-list-details">'));
  assert.ok(menu.includes('<summary>メニュー一覧</summary>'));
  const proxy = read('proxy.ts');
  assert.ok(proxy.includes('url.pathname = `/hospital${pathname}`'));
  assert.ok(proxy.includes('NextResponse.redirect(url, 308)'));
  const dashboard = read('pages/hospital-dashboard.tsx');
  assert.ok(dashboard.includes('className="system-meta warn"'));
  assert.ok(dashboard.includes('<OcrOperationsSummary operations={systemStatus?.ocr_operations}'));
  assert.ok(read('pages/system-status.tsx').includes('<OcrOperationsHistory operations={status?.ocr_operations}'));
  assert.ok(!dashboard.includes('エラー: {systemStatus.ocr_pipeline.last_error}'));
  assert.ok(READONLY_MESSAGE_SURFACES.includes('.system-meta.warn'));
  assert.equal(readonlyPageStatus([{ status: 404 }], true), 'failed');
  assert.equal(businessPath('/hospital/facilities/FAC00015'), true);
});

test('visible nonempty message rejects despite existing content; hidden and empty messages do not', async () => {
  function page(rows) {
    return { locator: selector => {
      assert.equal(selector, READONLY_MESSAGE_SURFACES);
      return { count: async () => rows.length, nth: index => ({
        isVisible: async () => rows[index].visible,
        innerText: async () => rows[index].text,
        getAttribute: async () => null,
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
  assert.ok(helper.indexOf('await assertNoVisibleReadMessages(page, entry);') < helper.indexOf('entry.loadingFinished = true;'));
});

test('only the exact Next announcer is excluded; business alerts and dashboard errors still fail', async () => {
  const require = createRequire(import.meta.url);
  const nextSource = readFileSync(require.resolve('next/dist/client/route-announcer.js'), 'utf8');
  for (const contract of ['id: "__next-route-announcer__"', 'role: "alert"', '"aria-live": "assertive"']) assert.ok(nextSource.includes(contract));
  const { webkit } = require('playwright');
  const browser = await webkit.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<p id="__next-route-announcer__" role="alert" aria-live="assertive" style="width:1px;height:1px;clip:rect(0,0,0,0);position:absolute">Page title</p>');
    assert.equal(await page.locator('[role="alert"]:visible').count(), 1);
    assert.equal(await page.locator(READONLY_VISIBLE_ERRORS).count(), 0);
    await assertNoVisibleReadMessages(page);
    await page.evaluate(() => {
      const alert = document.createElement('p');
      alert.setAttribute('role', 'alert');
      alert.textContent = 'synthetic business failure';
      document.body.appendChild(alert);
    });
    assert.equal(await page.locator('[role="alert"]:visible').count(), 2);
    assert.equal(await page.locator(READONLY_VISIBLE_ERRORS).count(), 1);
    const simultaneous = {};
    await assert.rejects(assertNoVisibleReadMessages(page, simultaneous), { message: 'readonly-visible-message' });
    assert.deepEqual(simultaneous, { errorElement: { kind: 'business-alert', id: 'other-or-absent' } });
    for (const markup of [
      '<p role="alert" id="secret-token">secret-error</p>',
      '<p id="__next-route-announcer__" role="alert" aria-live="polite">secret-error</p>',
      '<p id="other" role="alert" aria-live="assertive">secret-error</p>',
      '<p class="system-meta warn">secret-error</p>',
      '<p class="message">secret-error</p>',
      '<p class="error" id="__next-route-announcer__" role="alert" aria-live="assertive">secret-error</p>',
    ]) {
      await page.setContent(markup);
      const diagnostic = {};
      await assert.rejects(assertNoVisibleReadMessages(page, diagnostic), { message: 'readonly-visible-message' });
      assert.ok(diagnostic.errorElement);
      assert.equal(JSON.stringify(diagnostic).includes('secret'), false);
      if (markup.includes('role="alert"')) assert.equal(await page.locator(READONLY_VISIBLE_ERRORS).count(), 1);
    }
  } finally { await browser.close(); }
});

test('facility initial receipt completes before explicit version reload and its receipt', async () => {
  const events = [];
  const facilityId = 'FAC00015';
  const page = { getByRole(role, options) {
    assert.equal(role, 'button');
    assert.deepEqual(options, { name: '版一覧を再読込', exact: true });
    return { click: async () => { events.push('click'); } };
  } };
  const navigate = async path => { events.push(path); };
  const receipt = async (path, action) => {
    events.push('wait:' + path);
    await action();
    events.push('received:' + path);
  };
  await loadReadonlyFacilityVersions({ page, receipt, navigate, facilityId });
  assert.deepEqual(events, ['wait:/api/facilities/FAC00015', '/facilities/FAC00015', 'received:/api/facilities/FAC00015', 'wait:/api/facilities/FAC00015/versions', 'click', 'received:/api/facilities/FAC00015/versions']);
  events.length = 0;
  await assert.rejects(loadReadonlyFacilityVersions({ page, navigate, facilityId, receipt: async () => { throw new Error('initial-read-failed'); } }), /initial-read-failed/);
  assert.deepEqual(events, []);
  const source = readFileSync(new URL('../../src/pages/facilities/[id].tsx', import.meta.url), 'utf8');
  assert.ok(source.includes('onClick={() => void loadVersions()}>版一覧を再読込'));
  assert.ok(source.includes('`/facilities/${target}/versions`'));
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
