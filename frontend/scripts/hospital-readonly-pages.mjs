import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { verifyManualLibrary } from './manual-library-readonly.mjs';
import { verifyAdditionalReadonlyPages } from './hospital-additional-readonly-pages.mjs';

export function businessPath(path) {
  return /^(?:\/hospital)?(?:\/daily-delivery-notes|\/hospital-dashboard|\/orders|\/facility-orders|\/menus(?:\/\d{4}-\d{2})?|\/facility-master|\/facilities\/[A-Za-z0-9_-]+|\/menu-masters)$/.test(path)
    || /^(?:\/hospital)?\/(?:base-menus|menu-rules|order-forms|shipping-history|system-process-logs)$/.test(path)
    || path === '/hospital' || path === '/manual-library' || path === '/hospital/manual-library';
}

export function observedBusinessLink(origin, href, expected, mark = () => {}) {
  mark('link-url-parse');
  const url = new URL(href, origin);
  mark('link-origin');
  assert.equal(url.origin, origin);
  mark('link-business-path');
  assert.equal(businessPath(url.pathname), true);
  mark('link-destination');
  assert.equal(url.pathname, expected);
  mark('link-query-empty');
  assert.equal(url.search, '');
  mark('link-fragment-empty');
  assert.equal(url.hash, '');
  return url.href;
}

export function readonlyPageStatus(http, ok) {
  if (http.some(row => row.status === 401 || row.status === 403)) return 'blocked';
  return ok && !http.some(row => row.status >= 400) ? 'passed' : 'failed';
}

export function matchesReadonlyResponse(response, origin, path, query = {}) {
  const url = new URL(response.url());
  return url.origin === origin && url.pathname === path && response.request().method() === 'GET'
    && Object.entries(query).every(([key, value]) => url.searchParams.getAll(key).length === 1 && url.searchParams.get(key) === value);
}

export const NEXT_ROUTE_ANNOUNCER = '[id="__next-route-announcer__"][role="alert"][aria-live="assertive"]';
export const BUSINESS_ALERT = `[role="alert"]:not(${NEXT_ROUTE_ANNOUNCER})`;
export const READONLY_VISIBLE_ERRORS = `.error:visible, ${BUSINESS_ALERT}:visible`;
export const READONLY_MESSAGE_SURFACES = `p.message, .error, .error-box, .archive-feedback-error, .pending-meta.warn, .system-meta.warn, ${BUSINESS_ALERT}`;
export const READONLY_SUBPHASES = Object.freeze(['navigation-link', 'navigation', 'response', 'content', 'expand-orders', 'expand-menu-list', 'facility-selection', 'facility-date', 'network-idle', 'source-sha', 'loading', 'visible-errors', 'page-errors', 'screenshot']);

export async function openReadonlyDisclosure(locator) {
  if (await locator.getAttribute('open') === null) await locator.locator('summary').click();
}

export async function readMonthlyDetailWithHistory({ page, origin, month, button, mark }) {
  const path = `/api/monthly-menus/${month}`;
  const wait = (suffix, code) => page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.origin === origin && url.pathname === path + suffix && response.request().method() === 'GET';
  }).then(async response => {
    if (response.status() !== 200) throw new Error(code + '-http');
    try { return await response.json(); } catch { throw new Error(code + '-body'); }
  });
  // Register both before navigation: uploads is requested after the menu body is consumed.
  const ready = Promise.all([wait('', 'monthly-menu'), wait('/uploads', 'monthly-history')])
    .then(values => ({ values }), error => ({ error }));
  if (new URL(page.url()).pathname === `/hospital/menus/${month}`) {
    mark('monthly-existing-navigation');
    const href = observedBusinessLink(origin, page.url(), `/hospital/menus/${month}`, mark);
    await page.goto(href);
  } else {
    mark('monthly-select');
    await button.click();
  }
  mark('monthly-responses');
  const outcome = await ready;
  if (outcome.error) {
    const allowed = ['monthly-menu-http', 'monthly-menu-body', 'monthly-history-http', 'monthly-history-body'];
    mark(allowed.includes(outcome.error.message) ? outcome.error.message : 'monthly-response-wait');
    throw new Error('monthly-response-failed');
  }
  return outcome.values;
}

export async function verifyMonthlyHistory({ page, expect, history, month, mark }) {
  mark('monthly-history-shape');
  assert.ok(history && Array.isArray(history.items));
  for (const row of history.items) {
    assert.ok(row && typeof row.id === 'string' && row.id.length);
    assert.equal(row.month_id, month);
    assert.ok(row.filename === null || row.filename === undefined || typeof row.filename === 'string');
  }
  const table = page.getByRole('table', { name: '取込履歴', exact: true });
  mark('monthly-history-table');
  await expect(table).toBeVisible();
  const visible = history.items.slice(0, 25);
  mark('monthly-history-row-count');
  await expect(table.locator('tbody tr')).toHaveCount(Math.max(1, visible.length));
  mark('monthly-history-total');
  await expect(table.locator('../..').locator('.MuiTablePagination-displayedRows'))
    .toHaveText(`${visible.length ? 1 : 0}–${visible.length} / ${history.items.length} 件`);
  if (!visible.length) {
    mark('monthly-history-empty');
    await expect(table.locator('tbody')).toHaveText('該当データがありません。');
  }
  for (let i = 0; i < visible.length; i++) {
    mark('monthly-history-filename');
    await expect(table.locator('tbody tr').nth(i).locator('td').nth(1)).toHaveText(visible[i].filename || '-');
  }
}

export function readonlyFailureKind(error) {
  if (error?.name === 'TimeoutError') return 'timeout';
  if (error?.name === 'AssertionError') return 'assertion';
  return 'other';
}

export async function recordReadonlyErrorElement(element, diagnostic) {
  const id = await element.getAttribute('id');
  const role = await element.getAttribute('role');
  diagnostic.errorElement = {
    kind: role === 'alert' ? 'business-alert' : 'business-message',
    id: id === '__next-route-announcer__' ? '__next-route-announcer__' : 'other-or-absent',
  };
}

export async function loadReadonlyFacilityVersions({ page, receipt, navigate, facilityId }) {
  await receipt(`/api/facilities/${facilityId}`, () => navigate(`/facilities/${facilityId}`));
  await receipt(`/api/facilities/${facilityId}/versions`, () =>
    page.getByRole('button', { name: '版一覧を再読込', exact: true }).click());
}

export async function assertNoVisibleReadMessages(page, diagnostic = {}, shippingMessage) {
  const messages = page.locator(READONLY_MESSAGE_SURFACES);
  for (let index = 0; index < await messages.count(); index++) {
    const message = messages.nth(index);
    if (await message.isVisible() && (await message.innerText()).trim()) {
      if (shippingMessage && new URL(page.url()).pathname === '/hospital/shipping-history'
          && await message.evaluate(element => element.matches('p.message') && !element.matches('.error, .error-box, .archive-feedback-error, .pending-meta.warn, .system-meta.warn, [role="alert"]'))
          && await message.innerText() === shippingMessage) continue;
      await recordReadonlyErrorElement(message, diagnostic);
      throw new Error('readonly-visible-message');
    }
  }
}

export async function safeBusinessScreenshot(page, origin, path) {
  const url = new URL(page.url());
  if (url.origin !== origin || !businessPath(url.pathname)) return false;
  if (await page.locator('input[type="password"]').count()) return false;
  if (await page.getByRole('heading', { name: /ログイン|Sign in|Login/i }).count()) return false;
  await page.screenshot({ path, fullPage: true });
  return true;
}

export async function verifyHospitalReadonlyPages({ page, expect, origin, source, output, result, targetDate, orderId, dailyOrders }) {
  let activeEntry;
  function mark(code) {
    result.readonlyCheck = code;
    if (activeEntry) activeEntry.checkCode = code;
  }
  const month = targetDate.slice(0, 7);
  const monthLabel = month.replace('-', '年') + '月';
  const selected = dailyOrders.filter(row => row.id === orderId);
  mark('readonly-check-001');
  assert.equal(selected.length, 1);
  const facilityId = selected[0].facility;
  mark('readonly-check-002');
  assert.match(facilityId, /^[A-Za-z0-9_-]+$/);
  const pages = [];
  result.readonlyPages = pages;
  result.readonlyCoverage = 'Read-only pages only; upload/edit/export/full workflow and human login not tested';
  let facilityName;
  function phase(value) {
    assert.ok(READONLY_SUBPHASES.includes(value));
    activeEntry.subphase = value;
    activeEntry.checkCode = null;
    activeEntry.steps.push({ subphase: value, at: new Date().toISOString() });
  }
  async function observed(path) {
    phase('navigation-link');
    const links = page.locator(`a[href="${path}"]`);
    mark('readonly-check-003');
    assert.ok(await links.count(), 'registered navigation link required');
    return observedBusinessLink(origin, await links.first().getAttribute('href'), path, mark);
  }
  async function navigate(path) {
    const href = await observed(path);
    phase('navigation');
    await page.goto(href);
  }
  async function receipt(path, action, query = {}) {
    let responseCheck = 'response-wait';
    const ready = page.waitForResponse(response => {
      return matchesReadonlyResponse(response, origin, path, query);
    }).then(async response => {
      responseCheck = 'readonly-check-004';
      assert.equal(response.status(), 200);
      responseCheck = 'response-json';
      return response.json();
    }).then(data => ({ data }), () => ({ failed: true, failedCheck: responseCheck }));
    await action();
    phase('response');
    const outcome = await ready;
    mark(outcome.failedCheck || 'readonly-check-005');
    assert.equal(outcome.failed, undefined);
    phase('content');
    return outcome.data;
  }
  async function check(name, action) {
    result.phase = 'verify-readonly-pages';
    const start = result.http.length;
    const errorsBefore = result.pageErrorCount;
    const entry = { name, status: 'failed', auth: 'service-principal', sourceSHA: source,
      loadingFinished: false, error: null, http: [], png: null, pathname: null, steps: [] };
    pages.push(entry);
    activeEntry = entry;
    phase('content');
    let ok = false;
    try {
      const checked = await action();
      phase('network-idle');
      await page.waitForLoadState('networkidle');
      phase('source-sha');
      mark('readonly-check-006');
      await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
      phase('loading');
      mark('readonly-check-007');
      await expect(page.locator('[role="progressbar"]')).toHaveCount(0);
      mark('readonly-check-008');
      await expect(page.getByText(/^(読み込み中\.\.\.|Loading\.\.\.|ログインを確認しています。)$/)).toHaveCount(0);
      phase('visible-errors');
      const visibleErrors = page.locator(READONLY_VISIBLE_ERRORS);
      if (await visibleErrors.count()) await recordReadonlyErrorElement(visibleErrors.first(), entry);
      mark('readonly-check-009');
      await expect(visibleErrors).toHaveCount(0);
      mark('readonly-check-010');
      await assertNoVisibleReadMessages(page, entry, name === 'shipping-history' ? checked?.shippingMessage : undefined);
      phase('page-errors');
      mark('readonly-check-011');
      assert.equal(result.pageErrorCount, errorsBefore);
      entry.loadingFinished = true;
      ok = true;
    } catch (error) {
      entry.error = 'readonly-page-check-failed';
      entry.failedSubphase = entry.subphase;
      entry.failedCheck = entry.checkCode;
      entry.failureKind = readonlyFailureKind(error);
    }
    entry.http = result.http.slice(start);
    const currentURL = new URL(page.url());
    entry.pathname = currentURL.origin === origin ? currentURL.pathname : null;
    entry.status = readonlyPageStatus(entry.http, ok);
    if (entry.status === 'blocked') entry.error = 'authorization-blocked';
    try {
      phase('screenshot');
      const filename = `readonly-${name}.png`;
      if (await safeBusinessScreenshot(page, origin, resolve(output, filename))) entry.png = filename;
    } catch { entry.status = 'failed'; entry.error = 'screenshot-failed'; }
    if (!entry.png && entry.status === 'passed') entry.status = 'failed';
  }
  await check('dashboard', async () => {
    const data = await receipt('/api/orders', () => navigate('/hospital'));
    mark('readonly-check-012');
    assert.ok(data.orders?.length);
    mark('readonly-check-013');
    await expect(page.getByRole('heading', { name: '月次の進捗と未確定を一画面で確認', exact: true })).toBeVisible();
    mark('readonly-check-014');
    await expect(page.getByRole('heading', { name: '月ごとの進捗表', exact: true })).toBeVisible();
  });
  await check('orders', async () => {
    const data = await receipt('/api/orders', () => navigate('/hospital/orders'));
    mark('readonly-check-015');
    assert.ok(data.orders?.length);
    mark('readonly-check-016');
    await expect(page.getByRole('heading', { name: '注文一覧', exact: true })).toBeVisible();
    phase('expand-orders');
    const toggle = page.locator('button.week-group-toggle').first();
    mark('readonly-check-017');
    await expect(toggle).toBeVisible();
    if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click();
    mark('readonly-check-018');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    phase('content');
    mark('readonly-check-019');
    await expect(page.locator('a[href*="/orders/"]').first()).toBeVisible();
  });
  await check('facility-orders', async () => {
    const data = await receipt('/api/orders', () => navigate('/hospital/facility-orders'));
    mark('readonly-check-020');
    assert.ok(data.orders?.length);
    mark('readonly-check-021');
    await expect(page.getByRole('table', { name: '施設別注文一覧' })).toBeVisible();
    mark('readonly-check-022');
    await expect(page.getByRole('link', { name: '詳細', exact: true }).first()).toBeVisible();
  });
  await check('monthly-list', async () => {
    const data = await receipt('/api/monthly-menus', () => navigate('/hospital/menus'));
    mark('readonly-check-023');
    assert.equal(data.items?.filter(row => row.id === month).length, 1);
    mark('readonly-check-024');
    await expect(page.locator('[aria-label="登録済み月次"]')).toBeVisible();
    mark('readonly-check-025');
    await expect(page.locator('[aria-label="登録済み月次"] button').filter({ hasText: monthLabel })).toHaveCount(1);
  });
  await check('monthly-detail', async () => {
    const button = page.locator('[aria-label="登録済み月次"] button').filter({ hasText: monthLabel });
    mark('readonly-check-026');
    await expect(button).toHaveCount(1);
    const [data, history] = await readMonthlyDetailWithHistory({ page, origin, month, button, mark });
    mark('readonly-check-027');
    assert.equal(data.menu?.id, month);
    mark('readonly-check-028');
    assert.ok(data.items?.length || data.entries?.length);
    await verifyMonthlyHistory({ page, expect, history, month, mark });
    mark('readonly-check-029');
    await expect(page.getByRole('heading', { name: '月次メニュー編集', exact: true })).toBeVisible();
    phase('expand-menu-list');
    await openReadonlyDisclosure(page.locator('details.menu-list-details'));
    phase('content');
    const table = page.getByRole('table', { name: '月次メニュー一覧' });
    mark('readonly-check-030');
    await expect(table).toBeVisible();
    mark('readonly-check-031');
    assert.ok(await table.locator('tbody tr').count());
  });
  await check('facilities', async () => {
    const data = await receipt('/api/facility-master', () => navigate('/hospital/facility-master'));
    const rows = data.facility_master?.facilities?.filter(row => row.facility_id === facilityId);
    mark('readonly-check-032');
    assert.equal(rows?.length, 1);
    facilityName = rows[0].facility_name;
    mark('readonly-check-033');
    assert.ok(typeof facilityName === 'string' && facilityName.length);
    mark('readonly-check-034');
    await expect(page.getByRole('heading', { name: '施設一覧', exact: true }).first()).toBeVisible();
    phase('facility-selection');
    const row = page.locator('button.list-item').filter({ has: page.locator('.list-title', { hasText: facilityName }) });
    mark('readonly-check-035');
    await expect(row).toHaveCount(1);
    await row.click();
    mark('readonly-check-036');
    await expect(page.locator(`a[href="/facilities/${facilityId}"]`)).toBeVisible();
  });
  await check('facility-detail', async () => {
    mark('readonly-check-037');
    assert.ok(facilityName);
    await loadReadonlyFacilityVersions({ page, receipt, navigate, facilityId });
    mark('readonly-check-038');
    await expect(page).toHaveURL(origin + `/hospital/facilities/${facilityId}`);
    mark('readonly-check-039');
    await expect(page.getByRole('heading', { name: '施設情報の有効期間', exact: true })).toBeVisible();
    phase('facility-date');
    await page.getByLabel('参照日', { exact: true }).fill(targetDate);
    const data = await receipt(`/api/facilities/${facilityId}/resolved`, () => page.getByRole('button', { name: '指定日の版を取得', exact: true }).click());
    mark('readonly-check-040');
    assert.equal(data.facility_id, facilityId);
    mark('readonly-check-041');
    assert.equal(data.effective_date, targetDate);
    mark('readonly-check-042');
    assert.ok(data.version?.version_id);
    mark('readonly-check-043');
    assert.ok(data.config && typeof data.config === 'object');
    mark('readonly-check-044');
    assert.deepEqual(data.config, data.facility);
    mark('readonly-check-045');
    assert.deepEqual(data.version.facility, data.facility);
    mark('readonly-check-046');
    await expect(page.locator('pre.json-view')).toContainText(facilityId);
  });
  await check('menu-masters', async () => {
    await receipt('/api/menu-masters', () => navigate('/hospital/menu-masters'));
    mark('readonly-check-047');
    await expect(page.getByRole('heading', { name: 'メニューマスター', exact: true })).toBeVisible();
    const table = page.getByRole('table', { name: 'メニューマスター一覧' });
    mark('readonly-check-048');
    await expect(table).toBeVisible();
    mark('readonly-check-049');
    assert.ok(await table.locator('tbody tr').count());
  });
  await verifyAdditionalReadonlyPages({ page, expect, check, receipt, navigate, mark });
  await check('manual-library', async () => {
    await navigate('/hospital');
    await navigate('/manual-library');
    await verifyManualLibrary({ page, expect, origin, output, result, mark });
  });
  mark('readonly-check-050');
  assert.ok(pages.every(entry => entry.status === 'passed'));
}
