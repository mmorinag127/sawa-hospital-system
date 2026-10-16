import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { records, trainingCells, verifyOcrAdminReadonlyPages } from '../../scripts/ocr-admin-readonly-pages.mjs';
import { assertNoVisibleReadMessages, businessPath, readonlyPageStatus } from '../../scripts/hospital-readonly-pages.mjs';
const require = createRequire(import.meta.url);
const { webkit, expect } = require('@playwright/test');

test('response records require unique real identities and retain exact business fields', () => {
  const data = { items: [{ id: 'sample', order_id: 'order', line_count: 0, has_corrections: true }] };
  assert.equal(records(data), data.items);
  assert.deepEqual(trainingCells(data.items[0], 'time'), ['time', '-', 'order', '-', '0', 'あり', '-', '-']);
  for (const invalid of [{}, { items: null }, { items: [{}] }, { items: [{ id: 'a' }, { id: 'a' }] }]) assert.throws(() => records(invalid));
});

test('every active check runs independently after failure; auth/HTTP failures never pass', async () => {
  const checked = [];
  await verifyOcrAdminReadonlyPages({ page: { on() {}, off() {} }, origin: 'https://fixture.invalid', expect,
    check: async (name, action) => { checked.push(name); await assert.rejects(action); },
    receipt: async () => { throw new Error('HTTP failure'); }, navigate: async () => {}, mark() {} });
  assert.deepEqual(checked, ['ocr-queue', 'ocr-results', 'ocr-training-data']);
  for (const status of [401, 403, 404, 500]) assert.notEqual(readonlyPageStatus([{ status }], true), 'passed');
});

async function withPage(action) {
  const browser = await webkit.launch();
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => route.fulfill({ body: '<html></html>', contentType: 'text/html' }));
    await page.goto('https://fixture.invalid/hospital/ocr-training-data');
    await action(page);
  } finally { await browser.close(); }
}

test('real DOM training rows/options match API; success message exception is exact and page-specific', async () => withPage(async page => {
  const sample = { id: 's1', order_id: 'o1', facility_code: 'f1', week_code: '2026-09', line_count: 0,
    has_corrections: true, source: 'manual', document_uri: 'gs://fixture/file.pdf', updated_at: null };
  await page.setContent('<h1>OCR学習データ</h1><span class="badge">合計 1 件</span><p class="message">学習データを取得しました（1件）。</p><table><tbody><tr><td>-</td><td>f1</td><td><a href="/orders/o1">o1</a></td><td>2026-09</td><td>0</td><td>あり</td><td>manual</td><td>gs://fixture/file.pdf</td></tr></tbody></table>');
  async function run() {
    let checked;
    await verifyOcrAdminReadonlyPages({ page, expect: expect.configure({ timeout: 100 }), origin: 'https://fixture.invalid',
      check: async (name, action) => { if (name === 'ocr-training-data') checked = await action(); },
      receipt: async (path, action, query) => { assert.equal(path, '/api/ocr/training-samples'); assert.deepEqual(query, { limit: '300' }); await action(); return { items: [sample] }; },
      navigate: async path => assert.equal(path, '/hospital/ocr-training-data'), mark() {} });
    return checked;
  }
  const checked = await run();
  await assertNoVisibleReadMessages(page, {}, undefined, undefined, checked.ocrTrainingMessage);
  await page.locator('td').nth(4).evaluate(node => { node.textContent = '1'; });
  await assert.rejects(run());
  for (const html of ['<p class="message">取得に失敗しました。</p>', '<p class="message error">学習データを取得しました（1件）。</p>', '<div role="alert">failure</div>']) {
    await page.setContent(html);
    await assert.rejects(assertNoVisibleReadMessages(page, {}, undefined, undefined, checked.ocrTrainingMessage));
  }
  await page.setContent('<p class="message">学習データを取得しました（1件）。</p>');
  await page.evaluate(() => history.pushState({}, '', '/hospital'));
  await assert.rejects(assertNoVisibleReadMessages(page, {}, undefined, undefined, checked.ocrTrainingMessage));
}));

test('empty result monitor is compared with actual empty orders and canonical facilities', async () => withPage(async page => {
  await page.setContent('<h1>OCR結果監視</h1><span class="badge">合計 0 件</span><table><tbody><tr><td>該当データなし</td></tr></tbody></table>');
  const paths = [];
  await verifyOcrAdminReadonlyPages({ page, expect, origin: 'https://fixture.invalid',
    check: async (name, action) => { if (name === 'ocr-results') await action(); },
    receipt: async (path, action, query) => { paths.push(path); await action();
      if (path === '/api/orders') { assert.deepEqual(query, { include_ocr: 'true' }); return { orders: [] }; }
      return { source: 'canonical_json', revision: 'r1', facilities: [] }; },
    navigate: async path => { assert.equal(paths.length, 2); assert.equal(path, '/hospital/ocr-results'); }, mark() {} });
}));

test('retired facilities/templates are excluded on explicit API410 proof; active routes and GET gates remain', () => {
  const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
  const api = read('../../../backend/src/api/ocr_registry.py');
  for (const name of ['list_templates', 'list_facilities']) assert.match(api, new RegExp(`def ${name}[^\\n]*\\n    _legacy_ocr_registry_disabled\\(\\)`));
  assert.match(api, /status_code=410, detail="legacy_ocr_registry_disabled"/);
  for (const route of ['queue', 'results', 'training-data']) {
    assert.ok(read('../../src/components/TopNav.tsx').includes(`href: "/ocr-${route}"`));
    assert.equal(businessPath(`/hospital/ocr-${route}`), true);
  }
  assert.equal(businessPath('/hospital/ocr-templates'), false);
  const source = read('../../scripts/ocr-admin-readonly-pages.mjs');
  assert.doesNotMatch(source, /\.post\(|\.put\(|\.delete\(|\.request\.|export-pdfs|recover-ready|\/resolve/);
  assert.ok(source.includes("assert.ok(!pipeline.last_error)"));
  const shared = read('../../scripts/hospital-readonly-pages.mjs');
  assert.ok(shared.indexOf('await verifyOcrAdminReadonlyPages') < shared.indexOf("await check('manual-library'"));
  assert.ok(shared.includes("assert.ok(pages.every(entry => entry.status === 'passed'))"));
});
