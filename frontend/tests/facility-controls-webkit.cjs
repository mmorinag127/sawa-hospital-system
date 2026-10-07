const { webkit } = require('@playwright/test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.join(__dirname, '../../tmp/controls-proof');
// Synthetic API responses verify the actual React page, not a live operator flow.
const master = { facilities: [{ facility_id: 'COMPONENT', facility_name: 'Component facility', aliases: ['raw alias'], areas: [{ id: '2F', name: '2F' }], fax_template_override: { columns: [{ index: 0, role: 'quantity', name: 'own', header: 'own', diet_type: 'regular', area_id: '2F' }] } }] };
(async () => {
  const browser = await webkit.launch({ headless: true });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => { errors.push(error.message); console.error('pageerror:', error.message); });
      page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
      const writes = [];
      await page.route('http://127.0.0.1:3187/**', route => {
        const url = route.request().url();
        if (url.endsWith('/bundle.js')) return route.fulfill({ contentType: 'application/javascript; charset=utf-8', body: fs.readFileSync(path.join(root, 'component/bundle.js')) });
        if (url.includes('/api/')) return route.fallback();
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>' });
      });
      await page.addInitScript(() => { sessionStorage.setItem('auth_header', 'Bearer component-only'); sessionStorage.setItem('sawa_auth_cache_generation', 'component'); });
      await page.route('**/api/**', route => {
        const request = route.request();
        if (request.method() === 'PUT') {
          const body = request.postDataJSON();
          writes.push(body);
          return route.fulfill({ json: { facility_master: body.facility_master, updated: true, revision: 'component-r2', source: 'canonical_json', projection: { status: 'synced', revision: 'component-r2' }, validation: { errors: [], warnings: [] } } });
        }
        return route.fulfill({ json: { facility_master: master, revision: 'component-r1', source: 'canonical_json', validation: { errors: [], warnings: [] } } });
      });
      await page.goto('http://127.0.0.1:3187/hospital/facility-master');
      try {
        await page.getByRole('button', { name: 'この施設の編集を開始', exact: true }).waitFor();
      } finally {
        fs.writeFileSync(path.join(root, `rendered-${width}.html`), await page.content());
        fs.writeFileSync(path.join(root, `errors-${width}.json`), JSON.stringify(errors));
      }
      assert.deepEqual(errors, []);
      await page.getByRole('button', { name: 'この施設の編集を開始', exact: true }).click();
      const alias = page.locator('input').filter({ visible: true });
      assert.ok(await alias.count() > 0);
      const raw = page.locator('input').filter({ visible: true }).first();
      const editable = page.locator('input:not([disabled])').filter({ visible: true });
      assert.ok(await editable.count() > 0);
      const json = page.locator('textarea:not([aria-hidden="true"])');
      await page.locator('summary').filter({ hasText: 'JSON' }).click();
      const before = await json.inputValue();
      await json.fill(before + ' ');
      assert.equal(await json.inputValue(), before + ' ');
      assert.equal(writes.length, 0);
      await page.getByRole('button', { name: 'キャンセル', exact: true }).first().click();
      assert.equal(await json.inputValue(), before);
      assert.equal(await page.locator('input[type="checkbox"]').first().isDisabled(), true);
      await page.getByRole('button', { name: 'この施設の編集を開始', exact: true }).click();
      await page.locator('.cell-edit-button').first().click();
      const diet = page.locator('select').first();
      assert.ok(await diet.locator('option[value="soft"]').count() === 1);
      await diet.selectOption('soft');
      assert.equal(await diet.inputValue(), 'soft');
      const header = page.getByLabel('発注書の見出し', { exact: true });
      await header.fill('raw-header0');
      assert.equal(await header.inputValue(), 'raw-header0');
      const checkbox = page.locator('.toggle-item input[type="checkbox"]').first();
      assert.equal(await checkbox.isChecked(), false);
      await checkbox.check();
      assert.equal(await checkbox.isChecked(), true);
      assert.equal(JSON.parse(await json.inputValue()).facilities[0].daily_label_comparable_diet_types.length, 1);
      assert.equal(writes.length, 0);
      await page.getByRole('button', { name: 'FAX等の専用編集を保存', exact: true }).first().click();
      await page.getByRole('button', { name: 'この施設の編集を開始', exact: true }).waitFor();
      assert.equal(writes.length, 1);
      assert.equal(writes[0].expected_revision, 'component-r1');
      assert.equal(writes[0].facility_master.facilities[0].fax_template_override.columns[0].header, 'raw-header0');
      assert.equal(writes[0].facility_master.facilities[0].fax_template_override.columns[0].diet_type, 'soft');
      assert.equal(writes[0].facility_master.facilities[0].daily_label_comparable_diet_types.length, 1);
      const clipping = await page.locator('input,select,textarea,button').evaluateAll(nodes => nodes.filter(n => {
        const r = n.getBoundingClientRect();
        if (r.width === 0 || n.getAttribute('aria-hidden') === 'true') return false;
        // Only controls inside actual horizontally scrolling table containers are exempt.
        let parent = n.closest('table')?.parentElement;
        while (parent) {
          const style = getComputedStyle(parent);
          if (['auto', 'scroll'].includes(style.overflowX) && parent.scrollWidth > parent.clientWidth) return false;
          parent = parent.parentElement;
        }
        return r.left < -1 || r.right > window.innerWidth + 1;
      }).map(n => ({ html: n.outerHTML, left: n.getBoundingClientRect().left, width: n.getBoundingClientRect().width })));
      fs.writeFileSync(path.join(root, `clipping-${width}.json`), JSON.stringify(clipping));
      await page.screenshot({ path: path.join(root, `controls-${width}.png`), fullPage: true });
      assert.deepEqual(clipping, []);
      console.log(JSON.stringify({ width, draftCancel: true, rawHeader: 'raw-header0', diet: 'soft', checked: true, writes: writes.length, expected_revision: writes[0].expected_revision }));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
