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
  for (const name of ['list_templates', 'list_facilities', 'list_unclassified', 'get_unclassified']) assert.match(api, new RegExp(`def ${name}[^\\n]*\\n    _legacy_ocr_registry_disabled\\(\\)`));
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

test('ingest row errors including pending prior failure are not global pipeline errors', async () => withPage(async page => {
  const items = ['error','pending'].map((status,i)=>({id:`ingest-${i}`,status,attempts:i,generation:'a'.repeat(31)+i,generation_attempts:i,
    pdf_uri:`gs://fixture/${i}.pdf`,message_id:`mail-${i}`,last_error:`recorded failure ${i}`,failure_kind:'business',
    received_at:null,created_at:null,updated_at:null,started_at:null,finished_at:null}));
  const system={ocr_pipeline:{status:'ok'},uploaded_pdfs:{},ingest_jobs:{}};
  const banner='<div class="queue-status-banner"><p class="queue-status-value">OK</p><p class="queue-status-meta">最終成功: 未取得 / 最終失敗: 未取得</p><p class="queue-status-meta">稼働中: 未取得 / 未取得</p>'+[0,1].map(()=>'<div class="queue-status-count-card"><p class="queue-status-count-value">未取得 / 未取得 / 未取得</p><p class="queue-status-count-help">unused</p><p class="queue-status-count-help">backlog: 未取得</p></div>').join('')+'</div>';
  const cards=items.map(entry=>`<article class="queue-card"><p class="queue-title">${entry.id}</p><span class="badge">${entry.status}</span>${[
    entry.pdf_uri,`メッセージID: ${entry.message_id}`,`試行回数: ${entry.attempts} / 世代: ${entry.generation} / 世代内試行回数: ${entry.generation_attempts}`,
    '受信: 未取得 / 作成: 未取得 / 更新: 未取得','開始: 未取得 / 終了: 未取得','失敗種別: business',
  ].map(text=>`<p class="queue-meta">${text}</p>`).join('')}<p class="queue-status-error">エラー: ${entry.last_error}</p></article>`).join('');
  await page.setContent('<h1>OCRキュー</h1><h2>取込ジョブ</h2>'+banner+cards);
  const run=()=>verifyOcrAdminReadonlyPages({page,expect:expect.configure({timeout:100}),origin:'https://fixture.invalid',
    check:async(name,action)=>{if(name==='ocr-queue')await action();},
    receipt:async(path,action,query)=>{await action();if(path==='/api/ingest/jobs'){assert.deepEqual(query,{status:'pending'});return {items};}assert.equal(path,'/api/system/status');return system;},
    navigate:async path=>assert.equal(path,'/hospital/ocr-queue'),mark() {}});
  await run();
  await page.locator('.queue-card .queue-status-error').first().evaluate(node=>node.textContent='hidden failure');
  await assert.rejects(run());
  await page.setContent('<h1>OCRキュー</h1><h2>取込ジョブ</h2>'+banner+cards);
  system.ocr_pipeline.last_error='real pipeline failure';
  await assert.rejects(run());
}));
