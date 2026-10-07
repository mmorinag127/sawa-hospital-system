const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { webkit } = require('@playwright/test');

const output = path.resolve('../tmp/controls');
// Synthetic API/identity boundary: actual Next/MUI layout, not live order proof.
const snapshot = {
  facility: { id: 'COMPONENT', name: 'Component facility', areas: null },
  config: { raw_zero: 0, raw_null: null },
  resolved_config: {}, order_form_patterns: [],
  revision: 'component-r1', source: 'canonical_json',
};

(async () => {
  const browser = await webkit.launch({ headless: true });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const requests = [], errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        sessionStorage.setItem('auth_header', 'Bearer component-only');
        sessionStorage.setItem('sawa_auth_cache_generation', 'component');
      });
      await page.route('**/api/**', route => {
        const request = route.request();
        requests.push({ url: request.url(), method: request.method(), body: request.postData() });
        const pathname = new URL(request.url()).pathname;
        if (pathname === '/api/auth/me') {
          return route.fulfill({ json: { role: 'operator' } });
        }
        if (request.method() === 'POST') {
          return route.fulfill({ status: 409, json: { detail: 'component-only refusal' } });
        }
        if (pathname === '/api/facilities/COMPONENT/versions') {
          return route.fulfill({ json: {
            facility_id: 'COMPONENT', versions: [],
            reference: { facility_id: 'COMPONENT' },
            reference_status: 'unversioned_reference_only',
            revision: 'component-r1', source: 'canonical_json',
          } });
        }
        if (pathname === '/api/facilities/COMPONENT') {
          return route.fulfill({ json: snapshot });
        }
        errors.push('Unexpected component API request: ' + request.url());
        return route.fulfill({ status: 500, json: { detail: 'unexpected test boundary' } });
      });
      await page.goto('http://localhost:3197/hospital/facilities/COMPONENT');
      await page.getByRole('button', { name: '施設版を保存', exact: true }).waitFor();
      await page.getByRole('button', { name: '版一覧を再読込', exact: true }).click();
      await page.locator('select').first().waitFor();
      const dates = page.locator('input[type="date"]');
      await dates.first().fill('2026-02-03');
      assert.equal(await dates.first().inputValue(), '2026-02-03');
      const jsonFile = page.locator('input[type="file"][accept="application/json"]');
      await jsonFile.setInputFiles({ name: 'explicit.json', mimeType: 'application/json', buffer: Buffer.from('{"raw_zero":0,"raw_null":null}') });
      assert.equal(await jsonFile.evaluate(n => n.files instanceof FileList && n.files[0].name), 'explicit.json');
      await page.getByText('Config loaded from file.', { exact: true }).waitFor();
      const configText = page.locator('details').filter({ hasText: 'Config JSON (上級)' }).locator('textarea:not([aria-hidden="true"])');
      assert.deepEqual(JSON.parse(await configText.inputValue()), { raw_zero: 0, raw_null: null });
      const workbook = page.locator('input[type="file"]').nth(1);
      // Native FileList selection plus pre-transport dirty refusal, not XLSX validity/transport.
      await workbook.setInputFiles({ name: 'component.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('component transport bytes') });
      assert.equal(await workbook.evaluate(n => n.files instanceof FileList && n.files[0].name), 'component.xlsx');
      await page.getByText('未保存の施設情報または設定があります。保存または取消後にExcelを登録してください。', { exact: true }).waitFor();
      assert.equal(requests.filter(request => request.method === 'POST').length, 0);
      const clipping = await page.locator('input,select,textarea,button').evaluateAll(nodes => nodes.filter(n => {
        const r = n.getBoundingClientRect();
        return r.width > 0 && n.getAttribute('aria-hidden') !== 'true' && (r.left < -1 || r.right > innerWidth + 1);
      }).map(n => ({ html: n.outerHTML, rect: n.getBoundingClientRect().toJSON() })));
      fs.writeFileSync(path.join(output, `next-${width}.json`), JSON.stringify({ errors, clipping, requests }, null, 2));
      await page.screenshot({ path: path.join(output, `next-${width}.png`), fullPage: true });
      assert.deepEqual(errors, []);
      assert.deepEqual(clipping, []);
      console.log(JSON.stringify({ width, nativeFiles: true, parsedConfig: { raw_zero: 0, raw_null: null }, workbookDirtyRefusal: true, posts: 0, date: '2026-02-03', requests: requests.length }));
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
