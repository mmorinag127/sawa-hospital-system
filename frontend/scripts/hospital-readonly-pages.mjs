import assert from 'node:assert/strict';
import { resolve } from 'node:path';

export function businessPath(path) {
  return /^(?:\/hospital)?(?:\/daily-delivery-notes|\/hospital-dashboard|\/orders|\/facility-orders|\/menus(?:\/\d{4}-\d{2})?|\/facility-master|\/facilities\/[A-Za-z0-9_-]+|\/menu-masters)$/.test(path)
    || path === '/hospital';
}

export function observedBusinessLink(origin, href, expected) {
  const url = new URL(href, origin);
  assert.equal(url.origin, origin);
  assert.equal(businessPath(url.pathname), true);
  assert.equal(url.pathname, expected);
  assert.equal(url.search, '');
  assert.equal(url.hash, '');
  return url.href;
}

export function readonlyPageStatus(http, ok) {
  if (http.some(row => row.status === 401 || row.status === 403)) return 'blocked';
  return ok && !http.some(row => row.status >= 400) ? 'passed' : 'failed';
}

export const READONLY_MESSAGE_SURFACES = 'p.message, .error, .error-box, .archive-feedback-error, .pending-meta.warn, [role="alert"]';

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
  const month = targetDate.slice(0, 7);
  const monthLabel = month.replace('-', '年') + '月';
  const selected = dailyOrders.filter(row => row.id === orderId);
  assert.equal(selected.length, 1);
  const facilityId = selected[0].facility;
  assert.match(facilityId, /^[A-Za-z0-9_-]+$/);
  const pages = [];
  result.readonlyPages = pages;
  result.readonlyCoverage = 'Read-only pages only; upload/edit/export/full workflow and human login not tested';
  let facilityName;
  async function observed(path) {
    const links = page.locator(`a[href="${path}"]`);
    assert.ok(await links.count(), 'registered navigation link required');
    return observedBusinessLink(origin, await links.first().getAttribute('href'), path);
  }
  async function navigate(path) {
    await page.goto(await observed(path));
  }
  async function receipt(path, action) {
    const ready = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.origin === origin && url.pathname === path && response.request().method() === 'GET';
    }).then(async response => {
      assert.equal(response.status(), 200);
      return response.json();
    }).then(data => ({ data }), () => ({ failed: true }));
    await action();
    const outcome = await ready;
    assert.equal(outcome.failed, undefined);
    return outcome.data;
  }
  async function check(name, action) {
    result.phase = 'verify-readonly-pages';
    const start = result.http.length;
    const errorsBefore = result.pageErrorCount;
    const entry = { name, status: 'failed', auth: 'service-principal', sourceSHA: source,
      loadingFinished: false, error: null, http: [], png: null, pathname: null };
    pages.push(entry);
    let ok = false;
    try {
      await action();
      await page.waitForLoadState('networkidle');
      await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
      await expect(page.locator('[role="progressbar"]')).toHaveCount(0);
      await expect(page.getByText(/^(読み込み中\.\.\.|Loading\.\.\.|ログインを確認しています。)$/)).toHaveCount(0);
      await expect(page.locator('.error:visible, [role="alert"]:visible')).toHaveCount(0);
      await assertNoVisibleReadMessages(page);
      assert.equal(result.pageErrorCount, errorsBefore);
      entry.loadingFinished = true;
      ok = true;
    } catch { entry.error = 'readonly-page-check-failed'; }
    entry.http = result.http.slice(start);
    const currentURL = new URL(page.url());
    entry.pathname = currentURL.origin === origin ? currentURL.pathname : null;
    entry.status = readonlyPageStatus(entry.http, ok);
    if (entry.status === 'blocked') entry.error = 'authorization-blocked';
    try {
      const filename = `readonly-${name}.png`;
      if (await safeBusinessScreenshot(page, origin, resolve(output, filename))) entry.png = filename;
    } catch { entry.status = 'failed'; entry.error = 'screenshot-failed'; }
    if (!entry.png && entry.status === 'passed') entry.status = 'failed';
  }
  await check('dashboard', async () => {
    const data = await receipt('/api/orders', () => navigate('/hospital'));
    assert.ok(data.orders?.length);
    await expect(page.getByRole('heading', { name: '月次の進捗と未確定を一画面で確認', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '月ごとの進捗表', exact: true })).toBeVisible();
  });
  await check('orders', async () => {
    const data = await receipt('/api/orders', () => navigate('/hospital/orders'));
    assert.ok(data.orders?.length);
    await expect(page.getByRole('heading', { name: '注文一覧', exact: true })).toBeVisible();
    await expect(page.locator('a[href*="/orders/"]').first()).toBeVisible();
  });
  await check('facility-orders', async () => {
    const data = await receipt('/api/orders', () => navigate('/hospital/facility-orders'));
    assert.ok(data.orders?.length);
    await expect(page.getByRole('table', { name: '施設別注文一覧' })).toBeVisible();
    await expect(page.getByRole('link', { name: '詳細', exact: true }).first()).toBeVisible();
  });
  await check('monthly-list', async () => {
    const data = await receipt('/api/monthly-menus', () => navigate('/hospital/menus'));
    assert.equal(data.items?.filter(row => row.id === month).length, 1);
    await expect(page.locator('[aria-label="登録済み月次"]')).toBeVisible();
    await expect(page.locator('[aria-label="登録済み月次"] button').filter({ hasText: monthLabel })).toHaveCount(1);
  });
  await check('monthly-detail', async () => {
    const button = page.locator('[aria-label="登録済み月次"] button').filter({ hasText: monthLabel });
    await expect(button).toHaveCount(1);
    // Selecting the already displayed month need not trigger another request.
    if (!new URL(page.url()).pathname.endsWith('/menus/' + month)) {
      const data = await receipt('/api/monthly-menus/' + month, () => button.click());
      assert.equal(data.menu?.id, month);
      assert.ok(data.items?.length || data.entries?.length);
    } else {
      await button.click();
    }
    await expect(page.getByRole('heading', { name: '月次メニュー編集', exact: true })).toBeVisible();
    const table = page.getByRole('table', { name: '月次メニュー一覧' });
    await expect(table).toBeVisible();
    assert.ok(await table.locator('tbody tr').count());
  });
  await check('facilities', async () => {
    const data = await receipt('/api/facility-master', () => navigate('/hospital/facility-master'));
    const rows = data.facility_master?.facilities?.filter(row => row.facility_id === facilityId);
    assert.equal(rows?.length, 1);
    facilityName = rows[0].facility_name;
    assert.ok(typeof facilityName === 'string' && facilityName.length);
    await expect(page.getByRole('heading', { name: '施設一覧', exact: true }).first()).toBeVisible();
    const row = page.locator('button.list-item').filter({ has: page.locator('.list-title', { hasText: facilityName }) });
    await expect(row).toHaveCount(1);
    await row.click();
    await expect(page.locator(`a[href="/facilities/${facilityId}"]`)).toBeVisible();
  });
  await check('facility-detail', async () => {
    assert.ok(facilityName);
    await receipt(`/api/facilities/${facilityId}/versions`, () => navigate(`/facilities/${facilityId}`));
    await expect(page.getByRole('heading', { name: '施設情報の有効期間', exact: true })).toBeVisible();
    await page.getByLabel('参照日', { exact: true }).fill(targetDate);
    const data = await receipt(`/api/facilities/${facilityId}/resolved`, () => page.getByRole('button', { name: '指定日の版を取得', exact: true }).click());
    assert.equal(data.facility_id, facilityId);
    assert.equal(data.effective_date, targetDate);
    assert.ok(data.version?.version_id);
    assert.ok(data.config && typeof data.config === 'object');
    assert.deepEqual(data.config, data.facility);
    assert.deepEqual(data.version.facility, data.facility);
    await expect(page.locator('pre.json-view')).toContainText(facilityId);
  });
  await check('menu-masters', async () => {
    await receipt('/api/menu-masters', () => navigate('/hospital/menu-masters'));
    await expect(page.getByRole('heading', { name: 'メニューマスター', exact: true })).toBeVisible();
    const table = page.getByRole('table', { name: 'メニューマスター一覧' });
    await expect(table).toBeVisible();
    assert.ok(await table.locator('tbody tr').count());
  });
  assert.ok(pages.every(entry => entry.status === 'passed'));
}
