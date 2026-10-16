import assert from 'node:assert/strict';

export async function verifyUploadList({ page, expect, receipt, mark }) {
  const data = await receipt('/api/ingest/uploads', () => page.getByRole('button', { name: '取込済みPDFを表示', exact: true }).click(), { limit: '50' });
  mark('upload-list-response');
  assert.ok(Array.isArray(data?.items));
  const ids = data.items.map(row => row?.id);
  assert.ok(ids.every(id => typeof id === 'string' && id.length));
  assert.equal(new Set(ids).size, ids.length);
  const cards = page.locator('.history-card');
  mark('upload-list-count');
  await expect(cards).toHaveCount(data.items.length);
  const statuses = { pending: '未処理', processing: '処理中', retry_wait: '再試行待ち', completed: '完了', manual_review: '要介入', failed_permanent: '恒久失敗' };
  const stages = { uploaded: 'アップロード済み', ingest_running: '取込処理中', retry_wait: '再試行待ち', manual_review: '要介入', completed: '完了' };
  for (let i = 0; i < data.items.length; i++) {
    const row = data.items[i], card = cards.nth(i);
    mark('upload-list-identity');
    await expect(card.locator('.history-card-id')).toHaveText(row.id);
    mark('upload-list-filename');
    await expect(card.locator('.history-card-title')).toHaveText(row.original_filename || row.message_id || row.id);
    mark('upload-list-status');
    await expect(card.locator('.history-badge')).toHaveText([
      statuses[String(row.status || '').trim().toLowerCase()] || row.status || '-',
      stages[String(row.current_stage || '').trim().toLowerCase()] || row.current_stage || '-',
    ]);
    mark('upload-list-error');
    await expect(card.locator('.history-error')).toHaveCount(row.last_error_message ? 1 : 0);
    if (row.last_error_message) await expect(card.locator('.history-error')).toHaveText(`エラー: ${row.last_error_message}`);
  }
  mark('upload-list-loaded');
  await expect(page.getByRole('button', { name: data.items.length ? '最新に更新' : '取込済みPDFを表示', exact: true })).toBeEnabled();
  if (!data.items.length) await expect(page.getByText('必要なときだけ「取込済みPDFを表示」を押してください。初期表示では重い一覧取得を行いません。', { exact: true })).toBeVisible();
  await expect(page.locator('.message-error:visible')).toHaveCount(0);
}

export async function verifyUploadListPage(ctx) {
  await ctx.check('pdf-upload-list', async () => {
    await ctx.receipt('/api/facilities', () => ctx.navigate('/hospital/pdf-upload'));
    await verifyUploadList(ctx);
  });
}
