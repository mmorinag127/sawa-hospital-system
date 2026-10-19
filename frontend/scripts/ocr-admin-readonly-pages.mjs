import assert from 'node:assert/strict';
import { readTogether } from './hospital-additional-readonly-pages.mjs';

export function records(data, key = 'items') {
  assert.ok(data && Array.isArray(data[key]));
  const ids = data[key].map(row => row?.id);
  assert.ok(ids.every(id => typeof id === 'string' && id.length));
  assert.equal(new Set(ids).size, ids.length);
  return data[key];
}
export function timestamp(value, timezone) {
  if (!value) return timezone ? '未取得' : '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return timezone ? '未取得' : value;
  return date.toLocaleString('ja-JP', timezone ? { timeZone: timezone } : undefined);
}
export function trainingCells(row, time) {
  return [time, row.facility_code || '-', row.order_id, row.week_code || '-', String(row.line_count ?? '-'),
    row.has_corrections ? 'あり' : 'なし', row.source || '-', row.document_uri || '-'];
}
async function exactTable(page, expect, values, empty, mark) {
  const rows = page.locator('table tbody tr');
  mark('ocr-table-count');
  await expect(rows).toHaveCount(Math.max(1, values.length));
  if (!values.length) { await expect(rows).toHaveText(empty); return; }
  for (let i = 0; i < values.length; i++) {
    mark('ocr-table-values');
    await expect(rows.nth(i).locator('td')).toHaveText(values[i]);
  }
}
export async function verifyOcrAdminReadonlyPages({ page, expect, check, receipt, navigate, mark, origin }) {
  await check('ocr-queue', async () => {
    const [queue, system] = await readTogether(receipt, [{ path: '/api/ingest/jobs', query: { status: 'pending' } },
      { path: '/api/system/status' }], () => navigate('/hospital/ocr-queue'));
    mark('ocr-queue-shape');
    const items = records(queue);
    await expect(page.getByRole('heading', { name: 'OCRキュー', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '取込ジョブ', exact: true })).toBeVisible();
    const cards = page.locator('.queue-card');
    await expect(cards).toHaveCount(items.length);
    if (!items.length) await expect(page.getByText('対象ジョブはありません。', { exact: true })).toBeVisible();
    for (let i = 0; i < items.length; i++) {
      const entry = items[i];
      const card = cards.nth(i);
      mark('ocr-queue-job');
      await expect(card.locator('.queue-title')).toHaveText(entry.id);
      await expect(card.locator('.badge')).toHaveText(entry.status);
      await expect(card.locator('.queue-meta')).toHaveText([
        entry.pdf_uri ?? '未取得', `メッセージID: ${entry.message_id ?? '未取得'}`,
        `試行回数: ${entry.attempts} / 世代: ${entry.generation ?? '未取得'} / 世代内試行回数: ${entry.generation_attempts ?? '未取得'}`,
        `受信: ${timestamp(entry.received_at, 'Asia/Tokyo')} / 作成: ${timestamp(entry.created_at, 'Asia/Tokyo')} / 更新: ${timestamp(entry.updated_at, 'Asia/Tokyo')}`,
        `開始: ${timestamp(entry.started_at, 'Asia/Tokyo')} / 終了: ${timestamp(entry.finished_at, 'Asia/Tokyo')}`,
        ...(entry.failure_kind ? [`失敗種別: ${entry.failure_kind}`] : []),
      ]);
      await expect(card.locator('.queue-status-error')).toHaveText(entry.last_error ? [`エラー: ${entry.last_error}`] : []);
    }
    mark('ocr-pipeline-shape');
    assert.ok(system.ocr_pipeline && system.uploaded_pdfs && system.ingest_jobs);
    const pipeline = system.ocr_pipeline;
    const labels = { ok: 'OK', error: 'エラー', running: '実行中', misconfigured: '未設定' };
    await expect(page.locator('.queue-status-value')).toHaveText(labels[(pipeline.status || '').toLowerCase()] || pipeline.status || '未取得');
    await expect(page.locator('.queue-status-meta').nth(0)).toHaveText(`最終成功: ${timestamp(pipeline.last_success_at, 'Asia/Tokyo')} / 最終失敗: ${timestamp(pipeline.last_error_at, 'Asia/Tokyo')}`);
    await expect(page.locator('.queue-status-meta').nth(1)).toHaveText(`稼働中: ${pipeline.inflight ?? '未取得'} / ${pipeline.max_inflight ?? '未取得'}`);
    for (const [i, key, last] of [[0, 'uploaded_pdfs', 'completed_count'], [1, 'ingest_jobs', 'stale_processing_count']]) {
      const value = system[key], card = page.locator('.queue-status-count-card').nth(i);
      await expect(card.locator('.queue-status-count-value')).toHaveText(['pending_count', 'processing_count', last].map(k => value[k] ?? '未取得').join(' / '));
      await expect(card.locator('.queue-status-count-help').nth(1)).toHaveText(`backlog: ${value.eligible_backlog_count ?? '未取得'}`);
    }
    mark('ocr-pipeline-error');
    assert.ok(!pipeline.last_error);
    await expect(page.locator('.queue-status-banner .queue-status-error')).toHaveCount(0);
  });
  await check('ocr-results', async () => {
    const candidates = new Map();
    const listener = response => {
      const url = new URL(response.url());
      const match = url.pathname.match(/^\/api\/orders\/([^/]+)\/ocr-output$/);
      if (url.origin !== origin || !match || response.request().method() !== 'GET') return;
      candidates.set(match[1], response.json().then(data => ({ data, status: response.status() }), () => ({ failed: true })));
    };
    page.on('response', listener);
    try {
      const [data, facilities] = await readTogether(receipt, [{ path: '/api/orders', query: { include_ocr: 'true' } },
        { path: '/api/facilities' }], () => navigate('/hospital/ocr-results'));
      mark('ocr-results-shape');
      const orders = records(data, 'orders');
      assert.ok(typeof facilities.revision === 'string' && facilities.revision.length);
      assert.equal(facilities.source, 'canonical_json');
      const names = new Map(records(facilities, 'facilities').map(row => [row.id, row.name]));
      for (const name of names.values()) assert.ok(typeof name === 'string' && name.trim());
      await page.waitForLoadState('networkidle');
      const hints = new Map();
      for (const order of orders.filter(row => !row.facility).slice(0, 80)) {
        mark('ocr-results-candidate-response');
        assert.ok(candidates.has(order.id));
        const value = await candidates.get(order.id);
        assert.equal(value.status, 200);
        assert.ok(Array.isArray(value.data.facility_candidates));
        hints.set(order.id, value.data.facility_candidates.filter(row => row.facility_id)[0]);
      }
      const sorted = [...orders].sort((a, b) => (b.received_at ? new Date(b.received_at).getTime() : 0) - (a.received_at ? new Date(a.received_at).getTime() : 0));
      const times = await page.evaluate(values => values.map(value => !value ? '-' : Number.isNaN(new Date(value).getTime()) ? value : new Date(value).toLocaleString('ja-JP')), sorted.map(row => row.received_at));
      await expect(page.getByRole('heading', { name: 'OCR結果監視', exact: true })).toBeVisible();
      await expect(page.locator('.badge')).toHaveText(`合計 ${sorted.length} 件`);
      await exactTable(page, expect, sorted.map((row, i) => {
        const hint = hints.get(row.id);
        const facility = row.facility ? names.has(row.facility) ? `${names.get(row.facility)} (${row.facility})` : row.facility
          : hint?.facility_name ? `推定: ${hint.facility_name} (${hint.facility_id}${hint.score != null ? ` / score=${hint.score}` : ''})` : '未確定';
        return [facility, row.week || '未確定', row.ocr_status || '-', row.status || '-', times[i], '詳細'];
      }), '該当データなし', mark);
      for (let i = 0; i < sorted.length; i++) await expect(page.locator('tbody tr').nth(i).locator('a')).toHaveAttribute('href', `/orders/${sorted[i].id}`);
    } finally { page.off('response', listener); }
  });
  await check('ocr-training-data', async () => {
    const data = await receipt('/api/ocr/training-samples', () => navigate('/hospital/ocr-training-data'), { limit: '300' });
    mark('ocr-training-shape');
    const items = records(data);
    await expect(page.getByRole('heading', { name: 'OCR学習データ', exact: true })).toBeVisible();
    const times = await page.evaluate(values => values.map(value => !value ? '-' : Number.isNaN(new Date(value).getTime()) ? value : new Date(value).toLocaleString('ja-JP')), items.map(row => row.updated_at));
    await exactTable(page, expect, items.map((row, i) => trainingCells(row, times[i])), 'データがありません。', mark);
    await expect(page.locator('.badge')).toHaveText(`合計 ${items.length} 件`);
    for (let i = 0; i < items.length; i++) await expect(page.locator('tbody tr').nth(i).locator('a')).toHaveAttribute('href', `/orders/${items[i].order_id}`);
    const message = `学習データを取得しました（${items.length}件）。`;
    await expect(page.locator('p.message')).toHaveText(message);
    return { ocrTrainingMessage: message };
  });
}
