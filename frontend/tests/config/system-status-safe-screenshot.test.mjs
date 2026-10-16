import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, readFile, rm, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { safeBusinessScreenshot } from '../../scripts/hospital-readonly-pages.mjs';
const require = createRequire(import.meta.url);
const { webkit } = require('@playwright/test');
const origin = 'https://fixture.invalid';
const title = '<h1>システム状態と復帰手順</h1>';
const instruction = '<h3>2. Google ログインの確認</h3>';

test('WebKit captures only source-proven operational heading; authentication boundaries stay closed', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sawa-system-screenshot-'));
  let browser;
  try {
    browser = await webkit.launch();
    const page = await browser.newPage();
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<html></html>' }));
    let count = 0;
    async function check(path, html, allowed, host = origin) {
      await page.goto(`${host}${path}`);
      await page.setContent(html);
      const output = join(directory, `${count++}.png`);
      const before = (await readdir(directory)).length;
      assert.equal(await safeBusinessScreenshot(page, origin, output), allowed);
      assert.equal((await readdir(directory)).length, before + Number(allowed));
      if (allowed) assert.deepEqual((await readFile(output)).subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    }
    for (const path of ['/system-status', '/hospital/system-status']) {
      await check(path, `${title}${instruction}<p class="error">fixture pipeline failure</p>`, true);
      await check(path, `${title}${instruction}<h1>ログイン</h1>`, false);
      await check(path, `${title}${instruction}<h3>Google ログイン</h3>`, false);
      await check(path, `${title}<h2>2. Google ログインの確認</h2>`, false);
      await check(path, `${title}${instruction}<input type="password">`, false);
      await check(path, `${title}${instruction}<input type="password" hidden>`, false);
      await check(path, instruction, false);
      await check(path, `<h1 hidden>システム状態と復帰手順</h1>${instruction}`, false);
      await check(path, '<h1>Sign in</h1>', false);
      await check(path, '<h1>Login</h1>', false);
    }
    await check('/login', `${title}${instruction}`, false);
    await check('/unknown', `${title}${instruction}`, false);
    await check('/system-status', `${title}${instruction}`, false, 'https://other.invalid');
    await check('/hospital/orders', '<h1>注文一覧</h1>', true);
    await check('/hospital/daily-delivery-notes', '<h1>日別出力</h1><p class="error">fixture failure</p>', true);
    await check('/hospital/orders', `${title}${instruction}`, false);
    await check('/hospital/orders', '<h1>注文一覧</h1><h3>ログイン</h3>', false);
  } finally {
    try { await browser?.close(); } finally { await rm(directory, { recursive: true, force: true }); }
  }
});

test('exception text and h1 are grounded in existing product source', async () => {
  const source = await readFile(new URL('../../src/pages/system-status.tsx', import.meta.url), 'utf8');
  assert.ok(source.includes('<h1>システム状態と復帰手順</h1>'));
  assert.ok(source.includes('<h3>2. Google ログインの確認</h3>'));
});
