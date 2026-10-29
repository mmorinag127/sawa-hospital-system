import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { isolatedPage as fixture } from './browser-fixture.mjs';
import { verifyUploadList, verifyUploadListPage } from '../../scripts/upload-list-readonly.mjs';
const { expect: baseExpect } = createRequire(import.meta.url)('@playwright/test');
const expect = baseExpect.configure({ timeout: 150 });
test('independent list check is wired immediately after unchanged entry checks and retains screenshots', async () => {
  const events = [];
  await assert.rejects(verifyUploadListPage({
    check: async (name, action) => { events.push(name); await action(); },
    receipt: async (path, action) => { events.push(path); await action(); throw new Error('HTTP failure'); },
    navigate: async path => events.push(path),
  }));
  assert.deepEqual(events, ['pdf-upload-list', '/api/facilities', '/hospital/pdf-upload']);
  const source = readFileSync(new URL('../../scripts/hospital-readonly-pages.mjs', import.meta.url), 'utf8');
  assert.match(source, /await verifyOperatorEntryPages\([^\n]+\);\n  await verifyUploadListPage\(/);
  assert.ok(source.includes('`readonly-${name}.png`'));
  assert.ok(source.includes('safeBusinessScreenshot(page, origin, resolve(output, filename))'));
});
test('actual click follows receipt registration; loaded file/status/error match API and mismatches reject', async () => fixture(async page => {
  await page.setContent('<button id="load">取込済みPDFを表示</button><article class="history-card"><p class="history-card-id">u1</p><h3 class="history-card-title">fax.pdf</h3><span class="history-badge">要介入</span><span class="history-badge">取込処理中</span><p class="history-error">エラー: fixture failure</p></article>');
  await page.evaluate(() => document.querySelector('#load').addEventListener('click', event => { event.target.textContent = '最新に更新'; }));
  const row = { id: 'u1', original_filename: 'fax.pdf', status: 'manual_review', current_stage: 'ingest_running', last_error_message: 'fixture failure' };
  let registered = false;
  const receipt = async (path, action, query) => {
    assert.equal(path, '/api/ingest/uploads'); assert.deepEqual(query, { limit: '50' }); registered = true;
    await action(); return { items: [row] };
  };
  const run = () => verifyUploadList({ page, expect, receipt, mark() {} });
  await run(); assert.equal(registered, true);
  for (const field of ['original_filename', 'status', 'last_error_message']) {
    await page.locator('#load').evaluate(node => { node.textContent = '取込済みPDFを表示'; });
    const before = row[field]; row[field] = 'wrong'; await assert.rejects(run()); row[field] = before;
  }
}));
test('empty response matches empty UI, invalid/duplicate responses and request failure reject', async () => fixture(async page => {
  await page.setContent('<button>取込済みPDFを表示</button><p>必要なときだけ「取込済みPDFを表示」を押してください。初期表示では重い一覧取得を行いません。</p>');
  const run = data => verifyUploadList({ page, expect, mark() {}, receipt: async (_path, action) => { await action(); return data; } });
  await run({ items: [] });
  for (const data of [{}, { items: null }, { items: [{ id: 'x' }, { id: 'x' }] }]) await assert.rejects(run(data));
  await assert.rejects(verifyUploadList({ page, expect, mark() {}, receipt: async () => { throw new Error('HTTP401'); } }));
  await page.evaluate(() => { const node = document.createElement('p'); node.className = 'message-error'; node.textContent = '取得失敗'; document.body.append(node); });
  await assert.rejects(run({ items: [] }));
}));
