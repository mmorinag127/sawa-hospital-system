import assert from 'node:assert/strict';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, webkit } from '@playwright/test';
import { isAllowedOutputSourceOrigin } from './output-source-live-origin.mjs';

const allowedMethods = new Set(['GET', 'HEAD']);

function writeJson(path, data) {
  writeFileSync(path + '.part', JSON.stringify(data));
  renameSync(path + '.part', path);
}

export async function verifyOutputSourceBrowser({ origin, token, orderId, targetDate, source, output }) {
  const result = { status: 'failed', scope: 'GET-only daily-delivery-notes evidence; service-principal auth, not human GIS login',
    unexpectedWriteCount: 0, pageErrorCount: 0, sourceSHA: source };
  let browser;
  let context;
  try {
    assert.equal(isAllowedOutputSourceOrigin(origin), true);
    assert.match(orderId, /^ORD[0-9A-Za-z_-]+$/);
    assert.match(targetDate, /^\d{4}-\d{2}-\d{2}$/);
    browser = await webkit.launch({ headless: true });
    context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    await context.addInitScript(({ token: sessionToken, origin: sessionOrigin }) => {
      if (location.origin !== sessionOrigin) return;
      sessionStorage.setItem('auth_header', 'Bearer ' + sessionToken);
      sessionStorage.setItem('sawa_auth_generation', localStorage.getItem('sawa_logout_generation') || '');
      sessionStorage.setItem('sawa_auth_cache_generation', 'output-source-live-private-context');
    }, { token, origin });
    context.on('page', page => page.on('pageerror', () => result.pageErrorCount++));
    context.on('response', response => {
      const url = new URL(response.url());
      if (url.origin === origin && url.pathname === '/api/orders/daily-output-context' && url.searchParams.get('date') === targetDate) {
        result.dailyOutputContextStatus = response.status();
      }
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
    const page = await context.newPage();
    const response = await page.goto(`${origin}/daily-delivery-notes`);
    assert.equal(response?.status(), 200);
    await page.locator('input[type="date"]').fill(targetDate);
    await page.getByRole('button', { name: '取得', exact: true }).click();
    await page.waitForLoadState('networkidle');
    const dateInput = page.locator('input[type="date"]');
    await expect(dateInput).toHaveValue(targetDate);
    assert.equal(result.dailyOutputContextStatus, 200);
    await page.screenshot({ path: resolve(output, 'daily-delivery-notes-full.png'), fullPage: true });
    assert.equal(result.unexpectedWriteCount, 0);
    assert.equal(result.pageErrorCount, 0);
    result.status = 'passed';
  } catch {
    result.code = 'browser-readonly-check-failed';
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
