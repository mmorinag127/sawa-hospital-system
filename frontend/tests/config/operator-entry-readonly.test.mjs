import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { verifyOperatorEntryPages, orderRows, initialWeekDates, historyChips } from '../../scripts/operator-entry-readonly-pages.mjs';
import { businessPath, readonlyPageStatus, assertNoVisibleReadMessages } from '../../scripts/hospital-readonly-pages.mjs';
const require = createRequire(import.meta.url);
const { webkit, expect: baseExpect } = require('@playwright/test');
const expect = baseExpect.configure({ timeout: 150 });
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');

async function withPage(run) {
  const browser = await webkit.launch();
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => route.abort());
    await run(page);
  } finally { await browser.close(); }
}
async function one(page, name, options = {}) {
  return verifyOperatorEntryPages({ page, expect, mark() {},
    check: async (key, action) => { if (key === name) await action(); },
    navigate: async () => {}, receipt: async () => { throw new Error('unexpected read'); }, ...options });
}

test('identity validation rejects missing/duplicate rows, never inserts expected output', () => {
  const rows = [{ id: 'a', line_count: 0 }];
  assert.equal(orderRows({ orders: rows }), rows);
  for (const data of [{}, { orders: null }, { orders: [{}] }, { orders: [{ id: 'a' }, { id: 'a' }] }]) assert.throws(() => orderRows(data));
});

test('shipping initial controls are enabled without inputs; no action or external tracking runs', async () => withPage(async page => {
  await page.setContent('<h1>佐川追跡・伝票管理</h1><h2>PDF解析</h2><h2>伝票管理Excelの到着日時更新</h2><h2>伝票番号の追跡状況確認</h2><input type="file" accept="application/pdf"><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"><textarea></textarea><button>解析してExcelをダウンロード</button><button>到着日時を更新してExcelをダウンロード</button><button>追跡状況を取得</button><a href="/shipping-history">これまでの追跡履歴を確認する</a>');
  const paths = [];
  await one(page, 'shipping-entry', { navigate: async path => paths.push(path) });
  assert.deepEqual(paths, ['/hospital/shipping']);
  await page.locator('button').first().evaluate(node => { node.disabled = true; });
  await assert.rejects(one(page, 'shipping-entry'));
}));

test('PDF initial API options match exactly; registration stays disabled and no upload is attempted', async () => withPage(async page => {
  await page.setContent('<h1>注文書アップロード</h1><button role="combobox" aria-label="施設">施設を選択</button><div role="option">施設を選択</div><div role="option">施設名 (F1)</div><input type="file" accept="application/pdf" multiple><div class="context-banner"><span>施設: 未選択</span><span>対象週: 未選択</span><span>PDF: 未選択</span></div><button disabled>PDFを登録</button><button>取込済みPDFを表示</button>');
  const data = { source: 'canonical_json', revision: 'r1', facilities: [{ id: 'F1', name: '施設名' }] };
  const receipt = async (path, action) => { assert.equal(path, '/api/facilities'); await action(); return data; };
  await one(page, 'pdf-upload-entry', { receipt });
  data.facilities[0].name = 'different';
  await assert.rejects(one(page, 'pdf-upload-entry', { receipt }));
  data.facilities[0].name = '施設名';
  await page.getByRole('button', { name: 'PDFを登録', exact: true }).evaluate(node => { node.disabled = false; });
  await assert.rejects(one(page, 'pdf-upload-entry', { receipt }));
}));

test('weekly initial waits register all eight exact targets before navigation and compare zero-valued cells', async () => withPage(async page => {
  const initial = await page.evaluate(initialWeekDates);
  await page.setContent(`<h1>週別重量表</h1><input type="week" value="${initial.week}"><button>表示・集計</button><button>Excel出力</button><span class="badge">対象 1 件</span><span class="badge">0 週</span><div class="week-history">履歴なし</div><table><tbody><tr><td>F1</td><td>week</td><td>確定</td><td>-</td><td>0</td><td><a href="/orders/O1">詳細</a></td></tr></tbody></table><a href="/daily-delivery-notes">日別出力へ</a>`);
  const targets = [], row = { id: 'O1', facility: 'F1', week: 'week', status: '確定', line_count: 0 };
  const receipt = async (path, action, query) => {
    targets.push({ path, query });
    await action();
    return { orders: path === '/api/orders' ? [] : [row] };
  };
  const navigate = async path => {
    assert.equal(path, '/hospital/weekly-weight-output');
    assert.equal(targets.length, 8);
    assert.deepEqual(targets[0], { path: '/api/orders', query: { include_ocr: 'false', limit: '500' } });
    assert.deepEqual(targets.slice(1).map(item => item.query.date), initial.dates);
    assert.ok(targets.slice(1).every(item => item.path === '/api/orders/by-line-date'));
  };
  await one(page, 'weekly-weight-entry', { receipt, navigate });
  targets.length = 0;
  row.line_count = 1;
  await assert.rejects(one(page, 'weekly-weight-entry', { receipt, navigate }));
}));

test('weekly history grouping retains source labels and count, including multiple orders per week', () => {
  const result = historyChips([{ week: '2026-09@2026-09-07~2026-09-13', week_label: 'first' },
    { week: '2026-09@2026-09-07~2026-09-13', week_label: 'last' }, { week: '2026-09' }]);
  assert.equal(result.length, 1);
  assert.equal(result[0].label, 'last');
  assert.equal(result[0].count, 2);
});

test('system status uses dashboard link and GET; current/historical pipeline errors remain failures', async () => withPage(async page => {
  await page.setContent('<h1>システム状態と復帰手順</h1><article class="card"><h2>取込方式</h2><p class="value">注文書アップロード</p><p class="meta">受付方式は手動アップロードに固定しています。</p><p class="meta">保存先: gcs</p><p class="meta">永続化: あり</p></article><p class="ocr-queue-summary-value">0 / 1 / 2</p><p class="ocr-queue-summary-value">3 / 4 / 5</p><button>品質を取得</button>');
  const paths = [];
  const data = { intake: { manual_upload_storage: { mode: 'gcs', persisted: true } }, oauth_config: {}, ocr_pipeline: {},
    uploaded_pdfs: { pending_count: 0, processing_count: 1, completed_count: 2 }, ingest_jobs: { pending_count: 3, processing_count: 4, stale_processing_count: 5 },
    ocr_reparse_quality: { gate: { status: 'pass' } } };
  const options = { navigate: async path => paths.push(path), receipt: async (path, action, query) => {
    assert.equal(path, '/api/system/status');
    assert.ok(['operations', 'quality'].includes(query.view));
    if (query.view === 'operations') assert.deepEqual(paths, ['/hospital']);
    else assert.deepEqual(paths, ['/hospital', '/system-status']);
    await action(); return data;
  } };
  await one(page, 'system-status-entry', options);
  assert.deepEqual(paths, ['/hospital', '/system-status']);
  paths.length = 0;
  await page.evaluate(() => {
    const note = document.createElement('p'); note.className = 'meta warn';
    note.textContent = 'quotaは許容範囲です。'; document.body.append(note);
  });
  await one(page, 'system-status-entry', options);
  paths.length = 0; data.ocr_pipeline.last_error = 'fixture error';
  await assert.rejects(one(page, 'system-status-entry', options));
  for (const html of ['<p class="message">取得失敗</p>', '<p class="error">失敗</p>', '<div role="alert">failure</div>']) {
    await page.setContent(html);
    await assert.rejects(assertNoVisibleReadMessages(page, {}));
  }
}));

test('independent checks continue after failure; HTTP/auth errors cannot become success', async () => {
  const names = [];
  await verifyOperatorEntryPages({ page: { evaluate: async () => { throw new Error('fixture'); } }, expect,
    check: async (name, action) => { names.push(name); await assert.rejects(action); },
    navigate: async () => { throw new Error('fixture'); }, receipt: async () => { throw new Error('fixture'); }, mark() {} });
  assert.deepEqual(names, ['shipping-entry', 'pdf-upload-entry', 'weekly-weight-entry', 'system-status-entry']);
  for (const status of [401, 403, 404, 500]) assert.notEqual(readonlyPageStatus([{ status }], true), 'passed');
});

test('source routes, lifecycle and no-action boundary remain grounded in current operator pages', () => {
  const nav = read('../../src/components/TopNav.tsx');
  for (const path of ['shipping', 'pdf-upload', 'weekly-weight-output']) {
    assert.ok(nav.includes(`href: "/${path}"`)); assert.equal(businessPath(`/hospital/${path}`), true);
  }
  assert.ok(read('../../src/pages/hospital-dashboard.tsx').includes('href="/system-status"'));
  assert.ok(read('../../../backend/src/api/system.py').includes('@router.get("/system/status", dependencies=[Depends(require_role("operator"))])'));
  assert.ok(read('../../src/pages/weekly-orders.tsx').includes('destination: "/orders"'));
  const shipping = read('../../src/pages/shipping.tsx');
  for (const disabled of ['pdfLoading', 'excelLoading', 'trackingLoading']) assert.ok(shipping.includes(`disabled={${disabled}}`));
  const pdf = read('../../src/pages/pdf-upload.tsx');
  assert.ok(pdf.includes('disabled={loading || !selectionCurrent || !selectionConfirmed}'));
  assert.ok(pdf.includes('初期表示では重い一覧取得を行いません。'));
  const helper = read('../../scripts/operator-entry-readonly-pages.mjs');
  assert.doesNotMatch(helper, /\.post\(|\.put\(|\.delete\(|setInputFiles|request\.get|fetch\(/);
  assert.doesNotMatch(helper, /getByRole\('button'.*\.click\(/);
  const shared = read('../../scripts/hospital-readonly-pages.mjs');
  assert.ok(shared.indexOf('await verifyOperatorEntryPages') < shared.indexOf("await check('manual-library'"));
  for (const name of ['saved-workflow', 'ocr-admin']) assert.ok(shared.includes(name));
  assert.equal(businessPath('/hospital/system-status/other'), false);
});
