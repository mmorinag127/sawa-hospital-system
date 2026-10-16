import assert from 'node:assert/strict';

// Every waiter is installed before the single navigation starts.
export async function readTogether(receipt, targets, action) {
  let start;
  const navigation = new Promise(resolve => { start = resolve; }).then(action);
  const ready = Promise.all(targets.map(({ path, query }) => receipt(path, () => navigation, query)));
  start();
  return ready;
}

export function requireSnapshot(data, key, type) {
  assert.ok(data && typeof data.revision === 'string' && data.revision.length);
  assert.ok(Array.isArray(data[key]));
  const ids = new Set();
  for (const row of data[key]) {
    assert.ok(row && typeof row.id === 'string' && row.id.trim() && !ids.has(row.id));
    ids.add(row.id);
    if (type) assert.equal(row.rule_type, type);
  }
  return data[key];
}

export function baseRows(data) {
  return [...requireSnapshot(data, 'items')].sort((a, b) =>
    Number(a.cycle_day || 0) - Number(b.cycle_day || 0)
    || String(a.daypart || '').localeCompare(String(b.daypart || ''))
    || Number(a.slot_index || 0) - Number(b.slot_index || 0));
}

export async function tablePage({ page, expect, mark }, label, rows, cells) {
  const table = page.getByRole('table', { name: label, exact: true });
  mark('additional-table-visible');
  await expect(table).toBeVisible();
  const visible = rows.slice(0, 25);
  mark('additional-table-row-count');
  await expect(table.locator('tbody tr')).toHaveCount(Math.max(1, visible.length));
  mark('additional-table-total');
  await expect(table.locator('../..').locator('.MuiTablePagination-displayedRows'))
    .toHaveText(`${visible.length ? 1 : 0}–${visible.length} / ${rows.length} 件`);
  if (!visible.length) {
    mark('additional-table-empty');
    await expect(table.locator('tbody')).toHaveText('該当データがありません。');
  }
  for (let index = 0; index < visible.length; index++) {
    for (const [column, value] of cells(visible[index])) {
      mark(`additional-table-cell-${column}`);
      await expect(table.locator('tbody tr').nth(index).locator('td').nth(column)).toHaveText(String(value));
    }
  }
}

export function orderFormOptions(data, kind) {
  const key = kind === 'facilities' ? 'id' : 'pattern_id';
  assert.ok(data && Array.isArray(data[kind]) && data[kind].length);
  const ids = new Set();
  return data[kind].map(row => {
    assert.ok(row && typeof row[key] === 'string' && row[key].trim());
    const value = row[key].trim();
    assert.ok(!ids.has(value)); ids.add(value);
    const label = row[kind === 'facilities' ? 'name' : 'label'];
    assert.ok(label == null || typeof label === 'string');
    return { value, text: label?.trim() ? `${label.trim()} (${value})` : value };
  });
}

export function orderFormSelect(page, label) {
  return page.locator('label.field').filter({
    has: page.locator('span.field-label').filter({ hasText: new RegExp(`^${label}$`) }),
  }).locator('select');
}

export async function verifiedProcessHistory({ page, expect, rows, items, mark }) {
  assert.equal(new Set(items.map(item => item.id)).size, items.length);
  for (const item of items) assert.ok(typeof item.id === 'string' && item.id.length);
  const handles = [];
  const detail = page.locator('[aria-label="処理ログ詳細"]');
  async function select(index) {
    const item = items[index];
    mark('process-history-selection');
    await rows.nth(index).click();
    await expect(rows).toHaveCount(items.length);
    await expect(page.locator('[aria-label="処理ログ一覧"] button.log-row.active')).toHaveCount(1);
    await expect(rows.nth(index)).toHaveClass(/\bactive\b/);
    mark('process-history-detail');
    await expect(detail.getByRole('heading', { name: item.title || item.id, exact: true })).toBeVisible();
    await expect(detail.locator('pre.json')).toHaveText(JSON.stringify(item.details || item, null, 2));
    for (const [offset, value] of [item.target || '-', item.actor || '-', item.source, item.summary || '-'].entries()) {
      assert.equal(typeof value, 'string');
      await expect(detail.locator('.detail-list dd').nth(offset + 1)).toHaveText(value);
    }
  }
  const failed = items.flatMap((item, index) => ['failed', 'error', 'hard_failed'].includes(item.status) ? [index] : []);
  for (const index of failed) {
    await select(index);
    mark('process-history-status');
    const badge = rows.nth(index).locator('span.status.error');
    await expect(badge).toHaveCount(1);
    await expect(badge).toHaveText('失敗');
    await expect(detail.locator('.detail-head > span.status.error')).toHaveText('失敗');
    handles.push(await badge.elementHandle());
  }
  // Restore the existing inspected row; only its own detail badge may be exempted.
  const selectedIndex = items.length > 1 ? 1 : 0;
  if (items.length) {
    await select(selectedIndex);
    if (failed.includes(selectedIndex)) handles.push(await detail.locator('.detail-head > span.status.error').elementHandle());
  }
  return async element => {
    if (!['/system-process-logs', '/hospital/system-process-logs'].includes(new URL(page.url()).pathname)) return false;
    for (const handle of handles) {
      if (await element.evaluate((node, known) => node === known && node.isConnected
        && node.matches('span.status.error') && !node.hasAttribute('role')
        && node.textContent === '失敗', handle)) return true;
    }
    return false;
  };
}

export function shippingSuccess(data, history = false) {
  assert.ok(data && data.summary);
  for (const field of ['total', 'pending', 'delivered', 'errors']) assert.ok(Number.isInteger(data.summary[field]) && data.summary[field] >= 0);
  if (history) {
    assert.ok(Array.isArray(data.items));
    assert.equal(data.summary.total, data.items.length);
    return `監査ログを取得しました（${data.items.length}件）。`;
  }
  assert.equal(data.view, 'recent');
  assert.match(data.base_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(Number.isInteger(data.window_days) && data.window_days >= 0);
  assert.ok(Array.isArray(data.groups) && Array.isArray(data.items));
  return `基準日 ${data.base_date} の±${data.window_days}日を取得しました。`;
}

function lookupDate(value) {
  if (!value || Number.isNaN(Date.parse(value))) return 'unknown';
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value));
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type).value).join('-');
}

export function shippingGroups(data, history) {
  const groups = new Map();
  const add = (date, items) => {
    assert.ok(Array.isArray(items));
    for (const item of items) {
      assert.ok(item && typeof item.tracking_number === 'string' && typeof item.tracking_key === 'string' && typeof item.status === 'string');
    }
    groups.set(date, [...(groups.get(date) || []), ...items]);
  };
  if (history) for (const item of data.items) add(item.ship_date || lookupDate(item.looked_up_at), [item]);
  else for (const group of data.groups) {
    const date = String(group.ship_date || group.group_date || group.reference_date || '').trim() || lookupDate(group.latest_looked_up_at);
    add(date, group.items);
  }
  return groups;
}

async function shippingDisplay(ctx, data, history) {
  const { page, expect, mark } = ctx;
  mark('shipping-response-shape');
  const message = shippingSuccess(data, history);
  const groups = shippingGroups(data, history);
  mark('shipping-success-message');
  await expect(page.locator('p.message')).toHaveText(message);
  for (const [label, field] of [['総件数', 'total'], ['有効中', 'pending'], ['配達完了', 'delivered'], ['照会失敗', 'errors']]) {
    mark(`shipping-summary-${field}`);
    const card = page.locator('.summary-card').filter({ has: page.locator('.summary-label', { hasText: label }) });
    await expect(card.locator('.summary-value')).toHaveText(String(data.summary[field]));
  }
  mark('shipping-calendar');
  await expect(page.getByTestId('shipping-calendar')).toBeVisible();
  const buttons = page.getByTestId('shipping-date-group');
  const itemCount = [...groups.values()].reduce((count, rows) => count + rows.length, 0);
  mark('shipping-response-count');
  assert.equal(itemCount, data.summary.total);
  if (!itemCount) {
    mark('shipping-empty-calendar');
    await expect(buttons).toHaveCount(0);
    return message;
  }
  mark('shipping-select-date');
  await expect(buttons.first()).toBeVisible();
  await buttons.first().click();
  const dialog = page.getByRole('dialog');
  mark('shipping-dialog');
  await expect(dialog).toBeVisible();
  const title = await dialog.locator('#shipping-detail-title').innerText();
  const date = title === '取得日未設定' ? 'unknown' : title.replaceAll('/', '-');
  mark('shipping-selected-date');
  assert.ok(groups.has(date));
  const rows = groups.get(date);
  const minimized = dialog.locator('details.not-shipped-minimized');
  if (await minimized.count() && await minimized.getAttribute('open') === null) await minimized.locator('summary').click();
  const cards = dialog.getByTestId('shipping-tracking-card');
  mark('shipping-selected-count');
  await expect(cards).toHaveCount(rows.length);
  const actual = [];
  for (let index = 0; index < rows.length; index++) {
    actual.push([await cards.nth(index).locator('.shipping-tracking-card-number').innerText(), await cards.nth(index).locator('.status-pill').innerText()]);
  }
  mark('shipping-selected-values');
  assert.deepEqual(actual.map(row => JSON.stringify(row.map(value => value.trim()))).sort(), rows.map(row => JSON.stringify([row.tracking_number || row.tracking_key || '-', row.status || '-'])).sort());
  await dialog.getByRole('button', { name: '閉じる', exact: true }).click();
  return message;
}

export async function verifyAdditionalReadonlyPages(ctx) {
  const { page, expect, check, receipt, navigate, mark } = ctx;
  await check('base-menus', async () => {
    const verify = async data => {
      mark('base-menu-shape');
      await tablePage(ctx, '基準メニュー一覧', baseRows(data), row => [[0, row.cycle_day ?? '-'], [1, row.daypart || '-'], [2, row.category || '-'], [3, row.name || '-'], [5, row.slot_index ?? '-'], [6, row.unit_type || '-'], [7, row.qty_per_serving ?? '-']]);
    };
    await verify(await receipt('/api/base-menus', () => navigate('/hospital/base-menus')));
    mark('base-menu-heading');
    await expect(page.getByRole('heading', { name: '基準メニュー', exact: true })).toBeVisible();
    await verify(await receipt('/api/base-menus', () => page.getByRole('button', { name: '全件再読込', exact: true }).click()));
  });
  await check('menu-rules', async () => {
    const types = ['global', 'menu', 'facility'];
    const data = await readTogether(receipt, types.map(type => ({ path: '/api/menu-rules', query: { rule_type: type } })), () => navigate('/hospital/menu-rules'));
    mark('rules-heading');
    await expect(page.getByRole('heading', { name: 'メニュールール管理', exact: true })).toBeVisible();
    for (const [index, label] of ['基本ルール', 'メニュー例外', '施設例外'].entries()) {
      mark(`rules-shape-${types[index]}`);
      const rows = requireSnapshot(data[index], 'rules', types[index]);
      await tablePage(ctx, label + '一覧', rows, row => {
        const offset = index === 0 ? 0 : index === 1 ? 2 : 3;
        return [...(index === 2 ? [[0, row.facility_id || '-']] : []), ...(index ? [[offset - 1, row.menu_pattern || '-']] : []), [offset, row.daypart || '-'], [offset + 1, row.category || '-'], [offset + 4, row.qty_per_serving ?? '-'], [offset + 5, row.priority ?? '-'], [offset + 6, row.active === false ? '無効' : '有効']];
      });
    }
  });
  await check('order-forms', async () => {
    const [facilities, patterns] = await readTogether(receipt, [{ path: '/api/facilities' }, { path: '/api/order-forms/patterns' }], () => navigate('/hospital/order-forms'));
    mark('order-forms-heading');
    await expect(page.getByRole('heading', { name: '注文書生成', exact: true })).toBeVisible();
    for (const [label, data, kind] of [['施設', facilities, 'facilities'], ['注文書パターン', patterns, 'patterns']]) {
      mark(`order-forms-${kind}-shape`);
      const options = orderFormOptions(data, kind);
      if (kind === 'patterns') options.unshift({ value: '', text: '施設既定を使う' });
      const select = orderFormSelect(page, label);
      mark(`order-forms-${kind}-options`);
      await expect(select).toHaveCount(1);
      await expect(select.locator('option')).toHaveText(options.map(option => option.text));
      assert.deepEqual(await select.locator('option').evaluateAll(nodes => nodes.map(node => node.value)), options.map(option => option.value));
    }
  });
  await check('shipping-history', async () => {
    const latest = await receipt('/api/shipping/status/latest', () => navigate('/hospital/shipping-history'), { view: 'recent' });
    mark('shipping-heading');
    await expect(page.getByRole('heading', { name: '佐川出荷状況', exact: true })).toBeVisible();
    await shippingDisplay(ctx, latest, false);
    const history = await receipt('/api/shipping/status/history', () => page.getByRole('button', { name: '監査ログ', exact: true }).click());
    return { shippingMessage: await shippingDisplay(ctx, history, true) };
  });
  await check('system-process-logs', async () => {
    await navigate('/hospital');
    const data = await receipt('/api/system/process-logs', () => navigate('/system-process-logs'), { limit: '100' });
    mark('process-logs-shape');
    assert.ok(Array.isArray(data.items));
    assert.equal(new Set(data.items.map(row => row.id)).size, data.items.length);
    for (const row of data.items) assert.ok(typeof row.id === 'string' && row.id.length);
    mark('process-logs-heading');
    await expect(page.getByRole('heading', { name: '処理ログ', exact: true })).toBeVisible();
    const rows = page.locator('[aria-label="処理ログ一覧"] button.log-row');
    mark('process-logs-count');
    await expect(rows).toHaveCount(data.items.length);
    await expect(page.locator('.toolbar-count')).toHaveText(`${data.items.length.toLocaleString('ja-JP')} 件`);
    for (let index = 0; index < data.items.length; index++) {
      mark('process-logs-title');
      await expect(rows.nth(index).locator('.title')).toHaveText(data.items[index].title || '-');
      mark('process-logs-target');
      await expect(rows.nth(index).locator('.target')).toHaveText(data.items[index].target || data.items[index].summary || '-');
    }
    if (!data.items.length) {
      mark('process-logs-empty');
      await expect(page.locator('.log-list .empty')).toHaveText('処理ログがありません。');
      return;
    }
    mark('process-logs-select');
    const selectedIndex = data.items.length > 1 ? 1 : 0;
    await rows.nth(selectedIndex).click();
    const selected = data.items[selectedIndex];
    const detail = page.locator('[aria-label="処理ログ詳細"]');
    mark('process-logs-detail-heading');
    await expect(detail.getByRole('heading', { name: selected.title || selected.id, exact: true })).toBeVisible();
    for (const [index, value] of [selected.target || '-', selected.actor || '-', selected.source, selected.summary || '-'].entries()) {
      mark(`process-logs-detail-field-${index}`);
      assert.equal(typeof value, 'string');
      await expect(detail.locator('.detail-list dd').nth(index + 1)).toHaveText(value);
    }
    mark('process-logs-detail-body');
    await expect(detail.locator('pre.json')).toHaveText(JSON.stringify(selected.details || selected, null, 2));
    return { processHistory: await verifiedProcessHistory({ page, expect, rows, items: data.items, mark }) };
  });
}
