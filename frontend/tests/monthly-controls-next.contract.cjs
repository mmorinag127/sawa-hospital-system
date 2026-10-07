const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { webkit } = require('@playwright/test');
// Component identity/API fixtures only; no business upload or backend acceptance.
const output = path.resolve('../tmp/browser');
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
        const request = route.request(), pathname = new URL(request.url()).pathname;
        requests.push({ pathname, method: request.method() });
        if (request.method() !== 'GET') { errors.push('Unexpected write'); return route.fulfill({ status: 500, json: { detail: 'writes forbidden in component proof' } }); }
        if (pathname === '/api/auth/me') return route.fulfill({ json: { role: 'operator' } });
        if (pathname === '/api/monthly-menus/scope-options') return route.fulfill({ json: { facilities: [{ id: 'COMPONENT', name: 'Component facility' }], tags: [{ value: 'raw-tag', facility_count: 0 }] } });
        if (pathname === '/api/monthly-menus/2026-10') return route.fulfill({ json: { menu: { id: '2026-10', display_name: 'Component only' }, items: [], entries: [], master_checks: { issues: [] }, revision: 'component-r1' } });
        if (['/api/monthly-menus', '/api/monthly-menus/2026-10/uploads', '/api/monthly-menus/2026-10/imports'].includes(pathname)) return route.fulfill({ json: { items: [] } });
        errors.push('Unexpected request ' + pathname);
        return route.fulfill({ status: 500, json: { detail: 'unexpected boundary' } });
      });
      await page.goto('http://localhost:3218/hospital/menus/2026-10');
      const sheet = page.getByPlaceholder('シート名 (任意)');
      await sheet.waitFor();
      await sheet.fill(' 000 raw ');
      assert.equal(await sheet.inputValue(), ' 000 raw ');
      const scope = page.locator('.scope-select select');
      await scope.selectOption('tag');
      await page.locator('.scope-input select').selectOption('raw-tag');
      assert.equal(await page.locator('.scope-input select').inputValue(), 'raw-tag');
      const checkbox = page.locator('fieldset input[type="checkbox"]').first();
      await checkbox.check(); assert.equal(await checkbox.isChecked(), true);
      await checkbox.uncheck(); assert.equal(await checkbox.isChecked(), false);
      const files = page.locator('input[type="file"]');
      assert.equal(await files.count(), 2);
      for (let i = 0; i < 2; i++) {
        await files.nth(i).setInputFiles({ name: 'component-' + i + '.bin', mimeType: 'application/octet-stream', buffer: Buffer.from('synthetic component bytes') });
        assert.equal(await files.nth(i).evaluate(n => n.files instanceof FileList && n.files[0].name), 'component-' + i + '.bin');
      }
      const upload = page.getByRole('button', { name: 'アップロード', exact: true });
      assert.equal(await upload.isEnabled(), true);
      assert.equal(await upload.getAttribute('type'), 'submit');
      assert.equal(requests.some(r => r.method !== 'GET'), false);
      const controls = await page.locator('input,select,button,textarea').evaluateAll(nodes => nodes.map(n => ({ tag: n.tagName, type: n.type, disabled: n.disabled, value: n.value })));
      assert.ok(controls.some(control => control.tag === 'BUTTON' && control.disabled), 'empty DataTable pagination controls remain disabled');
      const clipping = await page.locator('input,select,button,textarea').evaluateAll(nodes => nodes.filter(n => {
        const r = n.getBoundingClientRect();
        if (!r.width || n.getAttribute('aria-hidden') === 'true') return false;
        let parent = n.closest('table')?.parentElement;
        while (parent) { const style = getComputedStyle(parent); if (['auto', 'scroll'].includes(style.overflowX) && parent.scrollWidth > parent.clientWidth) return false; parent = parent.parentElement; }
        return r.left < -1 || r.right > innerWidth + 1;
      }).map(n => n.outerHTML));
      fs.writeFileSync(path.join(output, 'next-' + width + '.json'), JSON.stringify({ requests, errors, clipping, controls }, null, 2));
      await page.screenshot({ path: path.join(output, 'next-' + width + '.png'), fullPage: true });
      assert.deepEqual(errors, []); assert.deepEqual(clipping, []);
      console.log(JSON.stringify({ width, rawInput: true, scope: 'raw-tag', checkbox: false, nativeFiles: 2, writes: 0 }));
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
