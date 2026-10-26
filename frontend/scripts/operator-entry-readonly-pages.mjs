import assert from 'node:assert/strict';
import { readTogether } from './hospital-additional-readonly-pages.mjs';

export function orderRows(data) {
  assert.ok(Array.isArray(data?.orders));
  const ids = data.orders.map(row => row?.id);
  assert.ok(ids.every(id => typeof id === 'string' && id.length));
  assert.equal(new Set(ids).size, ids.length);
  return data.orders;
}

// Runs in the browser timezone, just like the page's initial conditions.
export function initialWeekDates() {
  const iso = date => date.toISOString().slice(0, 10);
  const monday = new Date(`${iso(new Date())}T00:00:00`);
  monday.setDate(monday.getDate() + (monday.getDay() === 0 ? -6 : 1 - monday.getDay()));
  const thursday = new Date(`${iso(monday)}T00:00:00`);
  thursday.setDate(thursday.getDate() + 3);
  const year = thursday.getFullYear();
  const start = new Date(year, 0, 1);
  const number = Math.ceil(((thursday - start) / 86400000 + start.getDay() + 1) / 7);
  const first = new Date(year, 0, 4);
  first.setDate(first.getDate() - (first.getDay() || 7) + 1 + (number - 1) * 7);
  const date = iso(first);
  return { week: `${year}-W${String(number).padStart(2, '0')}`, dates: Array.from({ length: 7 }, (_, i) => {
    const value = new Date(`${date}T00:00:00`); value.setDate(value.getDate() + i); return iso(value);
  }) };
}

// Match the page's grouping in its timezone; no business values are invented.
export function historyChips(orders) {
  const groups = new Map();
  for (const order of orders) {
    const match = String(order.week_value || order.week || '').trim().match(/^(\d{4}-\d{2})@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
    if (!match) continue;
    const monday = new Date(`${match[2]}T00:00:00`);
    if (Number.isNaN(monday.getTime())) continue;
    monday.setDate(monday.getDate() + (monday.getDay() === 0 ? -6 : 1 - monday.getDay()));
    const thursday = new Date(`${monday.toISOString().slice(0, 10)}T00:00:00`);
    thursday.setDate(thursday.getDate() + 3);
    const year = thursday.getFullYear(), start = new Date(year, 0, 1);
    const number = Math.ceil(((thursday - start) / 86400000 + start.getDay() + 1) / 7);
    const week = `${year}-W${String(number).padStart(2, '0')}`;
    const first = new Date(year, 0, 4);
    first.setDate(first.getDate() - (first.getDay() || 7) + 1 + (number - 1) * 7);
    groups.set(week, { date: first.toISOString().slice(0, 10),
      label: order.week_label || `${match[2].slice(5).replace('-', '/')} - ${match[3].slice(5).replace('-', '/')}`,
      count: (groups.get(week)?.count || 0) + 1 });
  }
  return [...groups.values()].sort((a, b) => b.date.localeCompare(a.date));
}

export async function verifyOperatorEntryPages({ page, expect, check, receipt, navigate, mark }) {
  await check('shipping-entry', async () => {
    await navigate('/hospital/shipping');
    mark('shipping-entry-heading');
    await expect(page.getByRole('heading', { name: '佐川追跡・伝票管理', exact: true })).toBeVisible();
    for (const name of ['PDF解析', '伝票管理Excelの到着日時更新', '伝票番号の追跡状況確認']) {
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    }
    mark('shipping-entry-inputs');
    const files = page.locator('input[type="file"]');
    await expect(files).toHaveCount(2);
    await expect(files.nth(0)).toHaveAttribute('accept', 'application/pdf');
    await expect(files.nth(1)).toHaveAttribute('accept', '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    for (let i = 0; i < 2; i++) await expect(files.nth(i)).toHaveValue('');
    await expect(page.locator('textarea').first()).toHaveValue('');
    mark('shipping-entry-controls');
    for (const name of ['解析してExcelをダウンロード', '到着日時を更新してExcelをダウンロード', '追跡状況を取得']) {
      await expect(page.getByRole('button', { name, exact: true })).toBeEnabled();
    }
    await expect(page.locator('.summary,.track-table-wrap,.file-name')).toHaveCount(0);
    mark('shipping-entry-history-link');
    await expect(page.getByRole('link', { name: 'これまでの追跡履歴を確認する', exact: true })).toHaveAttribute('href', '/shipping-history');
  });
  await check('pdf-upload-entry', async () => {
    const data = await receipt('/api/facilities', () => navigate('/hospital/pdf-upload'));
    mark('pdf-entry-facilities-shape');
    assert.equal(data.source, 'canonical_json');
    assert.ok(typeof data.revision === 'string' && data.revision.length);
    assert.ok(Array.isArray(data.facilities));
    const ids = data.facilities.map(row => row.id);
    assert.ok(ids.every(id => typeof id === 'string' && id.trim()));
    assert.equal(new Set(ids).size, ids.length);
    mark('pdf-entry-heading');
    await expect(page.getByRole('heading', { name: '注文書アップロード', exact: true })).toBeVisible();
    const facility = page.getByRole('combobox', { name: '施設', exact: true });
    mark('pdf-entry-options');
    await expect(facility).toBeEnabled();
    await facility.click();
    await expect(page.getByRole('option')).toHaveText(['施設を選択', ...data.facilities.map(row => row.name?.trim() ? `${row.name.trim()} (${row.id.trim()})` : row.id.trim())]);
    await page.keyboard.press('Escape');
    mark('pdf-entry-empty-controls');
    await expect(page.locator('input[type="file"]')).toHaveValue('');
    await expect(page.locator('input[type="file"]')).toHaveAttribute('accept', 'application/pdf');
    await expect(page.locator('input[type="file"]')).toHaveAttribute('multiple', '');
    await expect(page.locator('.context-banner span')).toHaveText(['施設: 未選択', '対象週: 未選択', 'PDF: 未選択']);
    await expect(page.getByRole('button', { name: 'PDFを登録', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: '取込済みPDFを表示', exact: true })).toBeEnabled();
    await expect(page.locator('.history-card')).toHaveCount(0);
    await expect(page.locator('details[open]')).toHaveCount(0);
    await expect(page.locator('.field-help-error:visible,.history-error:visible')).toHaveCount(0);
  });
  await check('weekly-weight-entry', async () => {
    const initial = await page.evaluate(initialWeekDates);
    const targets = [{ path: '/api/orders', query: { include_ocr: 'false', limit: '500' } },
      ...initial.dates.map(date => ({ path: '/api/orders/by-line-date', query: { date } }))];
    const [history, ...days] = await readTogether(receipt, targets, () => navigate('/hospital/weekly-weight-output'));
    mark('weekly-entry-response-shape');
    orderRows(history);
    const chips = await page.evaluate(historyChips, history.orders);
    const rows = new Map();
    for (const day of days) for (const row of orderRows(day)) rows.set(row.id, row);
    mark('weekly-entry-heading');
    await expect(page.getByRole('heading', { name: '週別重量表', exact: true })).toBeVisible();
    await expect(page.locator('input[type="week"]')).toHaveValue(initial.week);
    await expect(page.getByRole('button', { name: '表示・集計', exact: true })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Excel出力', exact: true })).toBeEnabled();
    mark('weekly-entry-count');
    await expect(page.locator('.badge').first()).toHaveText(`対象 ${rows.size} 件`);
    await expect(page.locator('.badge').nth(1)).toHaveText(`${chips.length} 週`);
    await expect(page.locator('.week-chip')).toHaveCount(chips.length);
    for (let i = 0; i < chips.length; i++) {
      mark('weekly-entry-history-values');
      await expect(page.locator('.week-chip').nth(i).locator('span').first()).toHaveText(chips[i].label);
      await expect(page.locator('.week-chip').nth(i).locator('small')).toHaveText(`${chips[i].count}件`);
    }
    if (!chips.length) await expect(page.locator('.week-history')).toHaveText('履歴なし');
    await expect(page.locator('tbody tr')).toHaveCount(Math.max(1, rows.size));
    if (!rows.size) await expect(page.locator('tbody')).toHaveText('該当データなし');
    const values = [...rows.values()];
    const times = await page.evaluate(items => items.map(value => !value ? '-' : Number.isNaN(new Date(value).getTime()) ? value : new Date(value).toLocaleString('ja-JP', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })), values.map(row => row.received_at));
    for (let i = 0; i < values.length; i++) {
      const row = values[i], displayed = page.locator('tbody tr').nth(i);
      mark('weekly-entry-row-values');
      await expect(displayed.locator('td')).toHaveText([row.facility || '-', row.week || '未確定', row.status || '-', times[i], String(row.line_count ?? '-'), '詳細']);
      await expect(displayed.locator('a')).toHaveAttribute('href', `/orders/${row.id}`);
    }
    await expect(page.getByRole('link', { name: '日別出力へ', exact: true })).toHaveAttribute('href', '/daily-delivery-notes');
  });
  await check('system-status-entry', async () => {
    await navigate('/hospital');
    const data = await receipt('/api/system/status', () => navigate('/system-status'), { view: 'operations' });
    mark('system-entry-response-shape');
    assert.ok(data?.intake && data.oauth_config && data.ocr_pipeline && data.uploaded_pdfs && data.ingest_jobs);
    await expect(page.getByRole('heading', { name: 'システム状態と復帰手順', exact: true })).toBeVisible();
    const intake = page.locator('article.card').filter({ has: page.getByRole('heading', { name: '取込方式', exact: true }) });
    mark('system-entry-intake');
    await expect(intake.locator('.value')).toHaveText('注文書アップロード');
    await expect(intake.locator('.meta').nth(1)).toHaveText(`保存先: ${data.intake.manual_upload_storage?.mode ?? '未取得'}`);
    await expect(intake.locator('.meta').nth(2)).toHaveText(`永続化: ${data.intake.manual_upload_storage?.persisted ? 'あり' : 'なし'}`);
    mark('system-entry-counts');
    await expect(page.locator('.ocr-queue-summary-value')).toHaveText([
      ['pending_count', 'processing_count', 'completed_count'].map(key => data.uploaded_pdfs[key] ?? '未取得').join(' / '),
      ['pending_count', 'processing_count', 'stale_processing_count'].map(key => data.ingest_jobs[key] ?? '未取得').join(' / '),
    ]);
    mark('system-entry-errors');
    assert.ok(!data.ocr_pipeline.last_error);
    assert.notEqual(String(data.ocr_pipeline.status || '').toLowerCase(), 'error');
    const quality = await receipt('/api/system/status', () =>
      page.getByRole('button', { name: '品質を取得', exact: true }).click(), { view: 'quality' });
    mark('system-entry-quality');
    assert.ok(quality.ocr_reparse_quality?.gate?.status);
    assert.ok(!quality.ocr_reparse_quality.error);
    assert.ok(!['fail', 'error'].includes(String(quality.ocr_reparse_quality.gate.status).toLowerCase()));
    await expect(page.locator('.error:visible,.quality-gate.fail:visible,.quality-gate.error:visible')).toHaveCount(0);
  });
}
