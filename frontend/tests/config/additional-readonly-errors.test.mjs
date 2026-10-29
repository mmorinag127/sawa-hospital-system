import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { isolatedPage } from './browser-fixture.mjs';
import { orderFormSelect, orderFormOptions, verifiedProcessHistory } from '../../scripts/hospital-additional-readonly-pages.mjs';
import { assertNoVisibleReadMessages, READONLY_VISIBLE_ERRORS, readonlyPageStatus } from '../../scripts/hospital-readonly-pages.mjs';
const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { NativeSelect } = require('@mui/material');
const { expect } = require('@playwright/test');

async function browserTest(action) {
  return isolatedPage(async page => {
    await page.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<html><body></body></html>' }));
    await page.goto('http://fixture.invalid/system-process-logs');
    await action(page);
  });
}

test('real MUI NativeSelect reproduces exact-label failure; scoped label retains complete option equality', async () => browserTest(async page => {
  const data = { facilities: [{ id: 'F1', name: '施設一' }, { id: 'F2', name: '施設二' }] };
  const options = orderFormOptions(data, 'facilities');
  const markup = renderToStaticMarkup(React.createElement('label', { className: 'field' },
    React.createElement('span', { className: 'field-label' }, '施設'),
    React.createElement(NativeSelect, { disableUnderline: true }, ...options.map(option =>
      React.createElement('option', { key: option.value, value: option.value }, option.text)))));
  await page.setContent(markup);
  assert.equal(await page.getByLabel('施設', { exact: true }).count(), 0);
  const select = orderFormSelect(page, '施設');
  await expect(select).toHaveCount(1);
  await expect(select.locator('option')).toHaveText(options.map(option => option.text));
  assert.deepEqual(await select.locator('option').evaluateAll(nodes => nodes.map(node => node.value)), options.map(option => option.value));
  await page.locator('option').first().evaluate(node => { node.textContent = '違う施設'; });
  await assert.rejects(expect(select.locator('option')).toHaveText(options.map(option => option.text), { timeout: 50 }));
  await page.setContent(markup + markup);
  assert.equal(await orderFormSelect(page, '施設').count(), 2);
}));

async function logs(page, items, label = '失敗') {
  await page.setContent('<div aria-label="処理ログ一覧"></div><aside aria-label="処理ログ詳細"><div class="detail-head"><h2></h2><span class="status error"></span></div><dl class="detail-list"></dl><pre class="json"></pre></aside>');
  await page.evaluate(({ items, label }) => {
    const list = document.querySelector('[aria-label="処理ログ一覧"]');
    for (const item of items) {
      const row = document.createElement('button'); row.className = 'log-row';
      const badge = document.createElement('span'); badge.className = 'status error'; badge.textContent = label; row.append(badge);
      row.onclick = () => {
        list.querySelectorAll('button').forEach(node => node.classList.remove('active')); row.classList.add('active');
        document.querySelector('h2').textContent = item.title;
        document.querySelector('.detail-head .status').textContent = label;
        const dl = document.querySelector('dl'); dl.replaceChildren();
        for (const value of ['date', item.target, item.actor, item.source, item.summary]) {
          const dd = document.createElement('dd'); dd.textContent = value; dl.append(dd);
        }
        document.querySelector('pre').textContent = JSON.stringify(item.details || item, null, 2);
      };
      list.append(row);
    }
  }, { items, label });
}
const items = ['failed', 'error', 'hard_failed'].map((status, i) => ({ id: `id${i}`, status, title: `title${i}`, target: `target${i}`, actor: 'actor', source: 'source', summary: 'summary', details: { id: `id${i}`, status } }));
const verify = page => verifiedProcessHistory({ page, expect, rows: page.locator('button.log-row'), items, mark() {} });

test('API-proven historical badges pass but new current errors, alerts and lookalike badges fail', async () => browserTest(async page => {
  await logs(page, items);
  const known = await verify(page);
  await assertNoVisibleReadMessages(page, {}, undefined, known);
  for (const status of [401, 403, 404, 500]) assert.notEqual(readonlyPageStatus([{ status }], true), 'passed');
  await page.evaluate(() => history.pushState({}, '', '/hospital'));
  await assert.rejects(assertNoVisibleReadMessages(page, {}, undefined, known));
  await page.evaluate(() => history.pushState({}, '', '/system-process-logs'));
  for (const kind of ['p', 'alert', 'badge']) {
    await page.evaluate(kind => {
      const node = document.createElement(kind === 'p' ? 'p' : 'span'); node.id = 'new-error';
      node.className = kind === 'badge' ? 'status error' : 'error';
      if (kind === 'alert') node.setAttribute('role', 'alert'); node.textContent = kind === 'badge' ? '失敗' : '取得失敗'; document.body.append(node);
    }, kind);
    assert.equal(await known(page.locator('#new-error')), false);
    await assert.rejects(assertNoVisibleReadMessages(page, {}, undefined, known), /readonly-visible-message/);
    assert.ok(await page.locator(READONLY_VISIBLE_ERRORS).count());
    await page.locator('#new-error').evaluate(node => node.remove());
  }
  await page.locator('.log-row .status').first().evaluate(node => { node.textContent = '別のエラー'; });
  await assert.rejects(assertNoVisibleReadMessages(page, {}, undefined, known));
}));

test('wrong status label, forged history and mismatched selected detail are not accepted', async () => browserTest(async page => {
  await logs(page, items, '完了');
  await assert.rejects(verify(page));
  const success = items.map(item => ({ ...item, status: 'success' }));
  await logs(page, success);
  const known = await verifiedProcessHistory({ page, expect, rows: page.locator('button.log-row'), items: success, mark() {} });
  await assert.rejects(assertNoVisibleReadMessages(page, {}, undefined, known));
  await logs(page, items.map(item => ({ ...item, details: { wrong: true } })));
  await assert.rejects(verify(page));
}));
