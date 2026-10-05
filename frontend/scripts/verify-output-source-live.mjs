import assert from 'node:assert/strict';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { isAllowedOutputSourceOrigin } from './output-source-live-origin.mjs';

const allowedMethods = new Set(['GET', 'HEAD']);
export const OPERATOR_DAILY_DELIVERY_PATH = '/hospital/daily-delivery-notes';
const phases = new Set([
  'validate-input', 'launch-browser', 'configure-private-context', 'open-daily-delivery-notes',
  'verify-deploy-version', 'verify-controls', 'set-target-date', 'await-daily-output-context',
  'verify-output-context', 'capture-full-page', 'validate-readonly', 'finished',
]);

function writeJson(path, data) {
  writeFileSync(path + '.part', JSON.stringify(data));
  renameSync(path + '.part', path);
}

export function sameOriginPathname(origin, value) {
  try {
    const url = new URL(value);
    return url.origin === origin ? url.pathname : null;
  } catch {
    return null;
  }
}

export function recordAllowedResponse(result, { origin, url, method, status }) {
  const pathname = sameOriginPathname(origin, url);
  if (!pathname || !allowedMethods.has(method) || !Number.isInteger(status)) return false;
  result.http.push({ phase: result.phase, method, pathname, status });
  return true;
}

export function sanitizedFailureCode(phase) {
  return 'browser-readonly-check-failed-at-' + (phases.has(phase) ? phase : 'unknown-phase');
}

export async function verifyOutputSourceBrowser({ origin, token, orderId, targetDate, source, output }) {
  const result = { status: 'failed', scope: 'GET-only daily-delivery-notes evidence; service-principal auth, not human GIS login',
    phase: 'validate-input', unexpectedWriteCount: 0, pageErrorCount: 0, sourceSHA: source,
    http: [], ui: { dateInputPresent: false, fetchButtonPresent: false, dateInputValue: null }, finalPathname: null };
  let browser;
  let context;
  let page;
  try {
    assert.equal(isAllowedOutputSourceOrigin(origin), true);
    assert.match(orderId, /^ORD[0-9A-Za-z_-]+$/);
    assert.match(targetDate, /^\d{4}-\d{2}-\d{2}$/);
    result.phase = 'launch-browser';
    const { expect, webkit } = await import('@playwright/test');
    browser = await webkit.launch({ headless: true });
    result.phase = 'configure-private-context';
    context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    await context.addInitScript(({ token: sessionToken, origin: sessionOrigin }) => {
      if (location.origin !== sessionOrigin) return;
      sessionStorage.setItem('auth_header', 'Bearer ' + sessionToken);
      sessionStorage.setItem('sawa_auth_generation', localStorage.getItem('sawa_logout_generation') || '');
      sessionStorage.setItem('sawa_auth_cache_generation', 'output-source-live-private-context');
    }, { token, origin });
    context.on('page', page => page.on('pageerror', () => result.pageErrorCount++));
    context.on('response', response => {
      recordAllowedResponse(result, { origin, url: response.url(), method: response.request().method(), status: response.status() });
    });
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== origin || !allowedMethods.has(request.method())) {
        if (!allowedMethods.has(request.method())) result.unexpectedWriteCount++;
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    page = await context.newPage();
    result.phase = 'open-daily-delivery-notes';
    const response = await page.goto(origin + OPERATOR_DAILY_DELIVERY_PATH);
    assert.equal(response?.status(), 200);
    result.phase = 'verify-deploy-version';
    await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
    result.phase = 'verify-controls';
    await page.waitForLoadState('networkidle');
    const dateInput = page.locator('input[type="date"]');
    const fetchButton = page.getByRole('button', { name: '取得', exact: true });
    await expect(dateInput).toBeVisible();
    await expect(fetchButton).toBeVisible();
    result.ui.dateInputPresent = true;
    result.ui.fetchButtonPresent = true;
    result.phase = 'set-target-date';
    await dateInput.fill(targetDate);
    result.phase = 'await-daily-output-context';
    const dailyOutputContext = page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === 'GET' && url.origin === origin
        && url.pathname === '/api/orders/daily-output-context' && url.searchParams.get('date') === targetDate;
    });
    await fetchButton.click();
    const contextResponse = await dailyOutputContext;
    result.phase = 'verify-output-context';
    assert.equal(contextResponse.status(), 200);
    await page.waitForLoadState('networkidle');
    await expect(dateInput).toHaveValue(targetDate);
    result.ui.dateInputValue = await dateInput.inputValue();
    result.phase = 'capture-full-page';
    await page.screenshot({ path: resolve(output, 'daily-delivery-notes-full.png'), fullPage: true });
    result.finalPathname = sameOriginPathname(origin, page.url());
    result.phase = 'validate-readonly';
    assert.equal(result.unexpectedWriteCount, 0);
    assert.equal(result.pageErrorCount, 0);
    result.status = 'passed'; result.phase = 'finished';
  } catch {
    result.code = sanitizedFailureCode(result.phase);
    result.finalPathname = sameOriginPathname(origin, page?.url());
    try {
      if (page && result.finalPathname) {
        await page.screenshot({ path: resolve(output, 'failed-own-page-full.png'), fullPage: true });
        result.failurePng = 'failed-own-page-full.png';
      }
    } catch { /* Never persist browser exception details. */ }
  } finally {
    try { if (context) await context.close(); }
    finally {
      if (browser) await browser.close();
      result.ownedBrowserStopped = true;
      writeJson(resolve(output, 'browser-result.json'), result);
    }
  }
  return result;
}

async function main() {
  const output = resolve(process.argv[2] || 'tmp/output-source-live');
  mkdirSync(output, { recursive: true });
  const allowed = process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_EVENT_NAME === 'workflow_dispatch'
    && process.env.GITHUB_REF === 'refs/heads/develop' && isAllowedOutputSourceOrigin(process.env.OUTPUT_SOURCE_WEB || '')
    && /^[0-9a-f]{40}$/.test(process.env.OUTPUT_SOURCE_SHA || '');
  if (!allowed) process.exitCode = 1;
  else {
    const result = await verifyOutputSourceBrowser({ origin: process.env.OUTPUT_SOURCE_WEB, token: process.env.LIVE_ID_TOKEN,
      orderId: process.env.OUTPUT_SOURCE_ORDER_ID, targetDate: process.env.OUTPUT_SOURCE_TARGET_DATE,
      source: process.env.OUTPUT_SOURCE_SHA, output });
    process.exitCode = result.status === 'passed' ? 0 : 1;
  }
}

if (import.meta.url === new URL(process.argv[1], 'file:').href) await main();
