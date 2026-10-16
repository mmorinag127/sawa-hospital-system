import assert from 'node:assert/strict';
import { resolve } from 'node:path';

export function businessPath(path) {
  return /^(?:\/hospital)?(?:\/daily-delivery-notes|\/hospital-dashboard|\/orders|\/facility-orders|\/menus(?:\/\d{4}-\d{2})?|\/facility-master|\/facilities\/[A-Za-z0-9_-]+|\/menu-masters)$/.test(path)
    || path === '/hospital';
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

export const READONLY_MESSAGE_SURFACES = 'p.message, .error, .error-box, .archive-feedback-error, .pending-meta.warn, .system-meta.warn, [role="alert"]';
export const READONLY_SUBPHASES = Object.freeze(['navigation-link', 'navigation', 'response', 'content', 'expand-orders', 'expand-menu-list', 'facility-selection', 'facility-date', 'network-idle', 'source-sha', 'loading', 'visible-errors', 'page-errors', 'screenshot']);

export async function openReadonlyDisclosure(locator) {
  if (await locator.getAttribute('open') === null) await locator.locator('summary').click();
}

export function readonlyFailureKind(error) {
  if (error?.name === 'TimeoutError') return 'timeout';
  if (error?.name === 'AssertionError') return 'assertion';
  return 'other';
}

export async function assertNoVisibleReadMessages(page) {
  const messages = page.locator(READONLY_MESSAGE_SURFACES);
  for (let index = 0; index < await messages.count(); index++) {
    const message = messages.nth(index);
    if (await message.isVisible() && (await message.innerText()).trim()) {
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
  async function receipt(path, action) {
    let responseCheck = 'response-wait';
    const ready = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.origin === origin && url.pathname === path && response.request().method() === 'GET';
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
      await action();
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
      mark('readonly-check-009');
      await expect(page.locator('.error:visible, [role="alert"]:visible')).toHaveCount(0);
      mark('readonly-check-010');
      await assertNoVisibleReadMessages(page);
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
    // Selecting the already displayed month need not trigger another request.
    if (!new URL(page.url()).pathname.endsWith('/menus/' + month)) {
      const data = await receipt('/api/monthly-menus/' + month, () => button.click());
      mark('readonly-check-027');
      assert.equal(data.menu?.id, month);
      mark('readonly-check-028');
      assert.ok(data.items?.length || data.entries?.length);
    } else {
      await button.click();
    }
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
    await receipt(`/api/facilities/${facilityId}/versions`, () => navigate(`/facilities/${facilityId}`));
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
  mark('readonly-check-050');
  assert.ok(pages.every(entry => entry.status === 'passed'));
}
