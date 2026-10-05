import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { webkit, expect } from '@playwright/test';

const WEB = 'https://web-stg-avlnzjjrca-dt.a.run.app';
const fields = ['name', 'unit_type', 'qty_per_serving', 'bag_max_qty', 'bag_max_unit', 'temp_type', 'daypart', 'category', 'condiments'];
const createForm = page => page.getByRole('form', { name: 'メニューマスターを追加', exact: true });
const editForm = page => page.getByRole('form', { name: /を編集$/ });
const textField = (form, name) => form.getByRole('textbox', { name, exact: true });
function json(path, data) {
  writeFileSync(path + '.part', JSON.stringify(data, null, 2));
  renameSync(path + '.part', path);
}
function snapshot(item, name) {
  assert.match(item.id, /^MNU[0-9a-f]{8}$/);
  assert.equal(item.name, name); assert.equal(item.normalized_name, name);
  assert.ok(Number.isInteger(item.revision) && item.revision > 0);
  for (const key of fields) assert.ok(Object.hasOwn(item, key));
  return Object.fromEntries(['id', 'revision', 'normalized_name', ...fields].map(key => [key, item[key]]));
}

// The exported routine is exercised against an isolated local actual app in tests.
// The only executable live entry below enforces Actions/develop/exact staging URL.
export async function verifyBrowser({ origin, token, ledgerPath, output, source }) {
  const ledger = JSON.parse(readFileSync(ledgerPath, 'utf8'));
  assert.equal(ledger.source, source);
  assert.match(ledger.name, /^c1-live-[1-9][0-9]*-[1-9][0-9]*-[0-9a-f]{32}$/);
  assert.equal(ledger.absentBefore, true); assert.deepEqual(ledger.receipts, []);
  const result = { status: 'failed', phase: 'launch', pageErrorCount: 0, hydrationErrorCount: 0,
    unexpectedWriteCount: 0, checks: [], evidence: 'real backend; service-principal injection, not human GIS login' };
  let browser, context, permittedWrite = null, injectedAbort = false;
  const persist = () => json(ledgerPath, ledger);
  const path = '/hospital/menu-masters?q=' + encodeURIComponent(ledger.name);
  const apiPath = url => new URL(url).pathname.replace(/^\/api(?:\/hospital)?/, '');
  const saveButton = form => form.getByRole('button', { name: '保存', exact: true });
  async function select(page) {
    await page.getByRole('row').filter({ has: page.getByRole('cell', { name: ledger.name, exact: true }) })
      .getByRole('button', { name: '編集', exact: true }).click();
    await expect(editForm(page)).toBeVisible();
  }
  async function option(page, form, label, value) {
    await form.getByRole('combobox', { name: label, exact: true }).click();
    await page.getByRole('option', { name: value, exact: true }).click();
  }
  async function save(page, form, method, payload, expectedStatus = 200) {
    const endpoint = method === 'POST' ? '/menu-masters' : '/menu-masters/' + ledger.receipts[0].item.id;
    ledger.writeAttempted = true; ledger.pending = { method, payload }; persist();
    permittedWrite = { method, endpoint, payload };
    try {
      const [response] = await Promise.all([
        page.waitForResponse(r => r.request().method() === method && apiPath(r.url()) === endpoint),
        saveButton(form).click(),
      ]);
      assert.equal(response.status(), expectedStatus);
      assert.deepEqual(response.request().postDataJSON(), payload);
      if (expectedStatus === 200) {
        const item = snapshot((await response.json()).item, ledger.name);
        assert.deepEqual(Object.fromEntries(fields.map(k => [k, item[k]])), Object.fromEntries(fields.map(k => [k, payload[k]])));
        const previous = ledger.receipts.at(-1)?.item;
        assert.equal(item.revision, previous ? previous.revision + 1 : 1);
        if (previous) assert.equal(item.id, previous.id);
        // Persist the fully checked canonical write before any secondary UI assertion.
        ledger.receipts.push({ method, status: 200, payload, item });
      }
      ledger.pending = null; persist();
      if (expectedStatus === 200) await expect(form.getByRole('status')).toHaveText('保存しました。');
    } finally { permittedWrite = null; }
  }
  try {
    browser = await webkit.launch({ headless: true });
    context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    context.setDefaultTimeout(15000);
    context.on('page', page => {
      page.on('pageerror', () => result.pageErrorCount++);
      page.on('console', message => {
        if (/hydration|did not match|Minified React error/i.test(message.text())) result.hydrationErrorCount++;
      });
    });
    await context.addInitScript(({ token, origin }) => {
      if (location.origin !== origin) return;
      sessionStorage.setItem('auth_header', 'Bearer ' + token);
      sessionStorage.setItem('sawa_auth_generation', localStorage.getItem('sawa_logout_generation') || '');
      sessionStorage.setItem('sawa_auth_cache_generation', 'c1-live-private-context');
    }, { token, origin });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== origin) return route.abort('blockedbyclient');
      if (['GET', 'HEAD'].includes(request.method())) return route.continue();
      let allowed = false;
      try {
        allowed = !!permittedWrite && request.method() === permittedWrite.method && apiPath(url) === permittedWrite.endpoint;
        if (allowed) assert.deepEqual(request.postDataJSON(), permittedWrite.payload);
      } catch { allowed = false; }
      if (!allowed) { result.unexpectedWriteCount++; return route.abort('blockedbyclient'); }
      const injectAbort = permittedWrite.injectAbort;
      permittedWrite = null;
      if (injectAbort) { injectedAbort = true; return route.abort('failed'); }
      return route.continue();
    });
    const page = await context.newPage();
    result.phase = 'open-owned-filter';
    assert.equal((await page.goto(origin + path)).status(), 200);
    await expect(page.getByRole('heading', { name: 'メニューマスター', exact: true })).toBeVisible();
    await expect(page.getByRole('contentinfo', { name: 'deploy version' })).toContainText(source.slice(0, 12));
    const form = createForm(page);
    await textField(form, 'メニュー名').fill(ledger.name);
    await option(page, form, '単位', '切れ'); await option(page, form, '袋単位', '個'); await option(page, form, '温冷', '冷');
    await form.getByRole('spinbutton', { name: '1人前数量', exact: true }).fill('0');
    await form.getByRole('spinbutton', { name: '袋上限数量', exact: true }).fill('1500');
    await textField(form, '食事帯').fill('昼食'); await textField(form, '分類').fill('主菜'); await textField(form, '付属品').fill('塩, レモン');
    const created = { name: ledger.name, unit_type: 'cut', qty_per_serving: 0, bag_max_qty: 1500,
      bag_max_unit: 'count', temp_type: 'cold', daypart: '昼食', category: '主菜', condiments: ['塩', 'レモン'] };
    result.phase = 'nine-field-create'; await save(page, form, 'POST', created); result.checks.push('nine-field-POST');
    result.phase = 'created-row-display';
    const region = page.getByRole('region', { name: 'メニューマスター一覧' });
    await expect(region.getByRole('row').nth(1).getByRole('cell')).toHaveText([
      ledger.name, '切れ', '0', '1500', '個', '冷', '昼食', '主菜', '塩、レモン', '編集',
    ]);
    for (const width of [360, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      await region.screenshot({ path: resolve(output, `owned-created-${width}.png`) });
      await region.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      await region.screenshot({ path: resolve(output, `owned-created-right-${width}.png`) });
      await region.evaluate(node => { node.scrollLeft = 0; });
    }
    await page.setViewportSize({ width: 1280, height: 1000 });
    await select(page); const edit = editForm(page);
    await option(page, edit, '単位', 'グラム (g)'); await option(page, edit, '袋単位', 'グラム (g)'); await option(page, edit, '温冷', '温');
    await edit.getByRole('spinbutton', { name: '1人前数量', exact: true }).fill('');
    await edit.getByRole('spinbutton', { name: '袋上限数量', exact: true }).fill('0');
    await textField(edit, '食事帯').fill('夕食'); await textField(edit, '分類').fill('試験編集'); await textField(edit, '付属品').fill('ソース');
    const updated = { ...created, unit_type: 'g', qty_per_serving: null, bag_max_qty: 0, bag_max_unit: 'g',
      temp_type: 'hot', daypart: '夕食', category: '試験編集', condiments: ['ソース'] };
    result.phase = 'nine-field-edit'; await save(page, edit, 'PUT', { ...updated, revision: 1 });
    await page.waitForLoadState('networkidle');
    const before = await page.evaluate(() => performance.timeOrigin);
    assert.equal((await page.reload()).status(), 200); await select(page);
    assert.ok(await page.evaluate(() => performance.timeOrigin) > before);
    await expect(edit.getByRole('spinbutton', { name: '1人前数量', exact: true })).toHaveValue('');
    await expect(edit.getByRole('spinbutton', { name: '袋上限数量', exact: true })).toHaveValue('0');
    await expect(edit.getByRole('combobox', { name: '単位', exact: true })).toHaveText('グラム (g)');
    await expect(edit.getByRole('combobox', { name: '袋単位', exact: true })).toHaveText('グラム (g)');
    await expect(edit.getByRole('combobox', { name: '温冷', exact: true })).toHaveText('温');
    for (const [label, value] of [['メニュー名', ledger.name], ['食事帯', '夕食'], ['分類', '試験編集'], ['付属品', 'ソース']]) await expect(textField(edit, label)).toHaveValue(value);
    result.checks.push('nine-field-PUT-fresh-document-null-zero-Japanese');
    for (const width of [360, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      const rows = region.getByRole('row'); await expect(rows).toHaveCount(2);
      await expect(rows.nth(1).getByRole('cell')).toHaveText([ledger.name, 'グラム (g)', '—', '0', 'グラム (g)', '温', '夕食', '試験編集', 'ソース', '編集']);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await region.screenshot({ path: resolve(output, `owned-row-${width}.png`) });
      await region.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      await region.screenshot({ path: resolve(output, `owned-row-right-${width}.png`) });
      await region.evaluate(node => { node.scrollLeft = 0; });
      await edit.screenshot({ path: resolve(output, `owned-edit-${width}.png`) });
    }
    await page.setViewportSize({ width: 1280, height: 1000 });
    result.phase = 'second-editor-real-conflict';
    const second = await context.newPage(); await second.goto(origin + path); await select(second);
    await textField(edit, '分類').fill('古い未保存');
    await textField(editForm(second), '分類').fill('別編集で保存');
    const concurrent = { ...updated, category: '別編集で保存' };
    await save(second, editForm(second), 'PUT', { ...concurrent, revision: 2 });
    await save(page, edit, 'PUT', { ...updated, category: '古い未保存', revision: 2 }, 409);
    await expect(edit.getByRole('alert')).toContainText('他の編集');
    await expect(textField(edit, '分類')).toHaveValue('古い未保存'); await expect(saveButton(edit)).toBeDisabled();
    await edit.getByRole('button', { name: '再読込', exact: true }).click();
    await page.getByRole('button', { name: '破棄して再読込', exact: true }).click();
    await expect(textField(edit, '分類')).toHaveValue('別編集で保存'); await expect(saveButton(edit)).toBeEnabled();
    result.checks.push('real-second-editor-PUT-stale-409-draft-retained-explicit-reload');
    result.phase = 'injected-network-abort';
    const failed = { ...concurrent, category: '通信失敗で保持', revision: 3 };
    await textField(edit, '分類').fill(failed.category);
    ledger.pending = { method: 'PUT', payload: failed, injection: 'browser-abort-before-backend' }; persist();
    permittedWrite = { method: 'PUT', endpoint: '/menu-masters/' + ledger.receipts[0].item.id, payload: failed, injectAbort: true };
    await saveButton(edit).click(); await expect(edit.getByRole('alert')).toBeVisible();
    assert.equal(injectedAbort, true); await expect(textField(edit, '分類')).toHaveValue(failed.category);
    permittedWrite = null; ledger.pending = null; persist();
    result.checks.push('injected-browser-network-abort-draft-retained-not-real-503');
    await page.waitForLoadState('networkidle'); await second.waitForLoadState('networkidle');
    assert.equal(result.pageErrorCount, 0); assert.equal(result.hydrationErrorCount, 0); assert.equal(result.unexpectedWriteCount, 0);
    result.status = 'passed'; result.phase = 'finished';
  } catch {
    // Playwright exceptions can include evaluated arguments/headers. Never persist their text.
    result.code = 'browser-check-failed-at-' + result.phase;
    try {
      const page = context?.pages()[0];
      const region = page?.getByRole('region', { name: 'メニューマスター一覧' });
      if (region && await region.getByRole('row').count() === 2
          && await region.getByRole('cell', { name: ledger.name, exact: true }).count() === 1) {
        await region.screenshot({ path: resolve(output, 'failed-owned-row.png'), timeout: 2000 });
      }
    } catch { /* No screenshot of unverified or unrelated records. */ }
  } finally {
    try { if (context) await context.close(); }
    finally {
      if (browser) await browser.close();
      result.ownedBrowserStopped = true;
      json(resolve(output, 'browser-result.json'), result);
    }
  }
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const output = resolve(process.argv[3] || 'tmp/menu-master-live');
  const allowed = process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_EVENT_NAME === 'workflow_dispatch'
    && process.env.C1_LIVE_VERIFY === 'true' && process.env.GITHUB_REF === 'refs/heads/develop'
    && process.env.WEB_URL === WEB && /^[0-9a-f]{40}$/.test(process.env.GITHUB_SHA || '');
  if (!allowed) { process.exitCode = 1; }
  else {
    const result = await verifyBrowser({ origin: WEB, token: process.env.LIVE_ID_TOKEN,
      ledgerPath: resolve(process.argv[2]), output, source: process.env.GITHUB_SHA });
    process.exitCode = result.status === 'passed' ? 0 : 1;
  }
}
