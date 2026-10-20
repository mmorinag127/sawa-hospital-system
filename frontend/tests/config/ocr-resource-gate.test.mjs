import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {webkit} from '@playwright/test';
import {gateReadonlyResource, safeAssertionName} from '../../scripts/verify-ocr-live.mjs';

const origin = 'https://fixture.invalid';
const imageUrl = 'https://assets.invalid/owned.png?signature=fixture';
for (const resourceType of ['stylesheet', 'font', 'document', 'script', 'xhr', 'fetch', 'other']) {
  test(`external ${resourceType} aborts before unresolved gate even if URL is listed`, async () => {
    let aborted = false, continued = false;
    await gateReadonlyResource({request: () => ({method: () => 'GET', url: () => imageUrl, resourceType: () => resourceType}),
      abort: () => {aborted = true;}, continue: () => {continued = true;}},
    {origin, allowedExternalUrls: new Set([imageUrl]), externalGate: new Promise(() => {}), onWrite: () => assert.fail()});
    assert.equal(aborted, true);
    assert.equal(continued, false);
  });
}

for (const url of [imageUrl, imageUrl + '&other=1']) test(`image waits for identity and requires exact URL: ${url}`, async () => {
  let release, result;
  const allowedExternalUrls = new Set();
  const externalGate = new Promise(resolve => {release = resolve;});
  const pending = gateReadonlyResource({request: () => ({method: () => 'GET', url: () => url, resourceType: () => 'image'}),
    abort: () => {result = 'abort';}, continue: () => {result = 'continue';}},
  {origin, allowedExternalUrls, externalGate, onWrite: () => assert.fail()});
  await Promise.resolve();
  assert.equal(result, undefined);
  allowedExternalUrls.add(imageUrl);
  release();
  await pending;
  assert.equal(result, url === imageUrl ? 'continue' : 'abort');
});

test('external write is rejected and counted without waiting for image identity', async () => {
  let writes = 0, aborted = false;
  await gateReadonlyResource({request: () => ({method: () => 'POST', url: () => imageUrl, resourceType: () => 'image'}),
    abort: () => {aborted = true;}, continue: () => assert.fail()},
  {origin, allowedExternalUrls: new Set([imageUrl]), externalGate: new Promise(() => {}), onWrite: () => writes++});
  assert.equal(writes, 1);
  assert.equal(aborted, true);
});

for (const name of ['TimeoutError', 'AssertionError']) test(`${name} diagnostics expose only fixed filename and line`, () => {
  assert.equal(safeAssertionName({name, stack: 'secret-token\n at action (/private/secret/verify-ocr-live.mjs:63:4)\n private-value'}),
    'verify-ocr-live.mjs:L63');
  assert.equal(safeAssertionName({name, stack: 'secret-token /private/person.js:63:4'}), null);
});

// Real WebKit, no remote traffic: use the product's exact CSS import, not a mocked click.
for (const legacy of [true, false]) test(`WebKit pending CSS ${legacy ? 'reproduces old actionability deadlock' : 'is rejected while verified images still wait'}`, async () => {
  const source = readFileSync(new URL('../../src/pages/orders/[id]/workflow-v2.tsx', import.meta.url), 'utf8');
  const css = source.match(/@import url\("https:\/\/fonts\.googleapis[^\n]+/)[0];
  const browser = await webkit.launch({headless: true});
  const context = await browser.newContext();
  let release, cssRejected = false, imageContinued = false;
  const externalGate = new Promise(resolve => {release = resolve;});
  const allowedExternalUrls = new Set();
  try {
    await context.route('**/*', async route => {
      const request = route.request();
      const wrapped = {
        request: () => request,
        abort: async () => {
          if(request.resourceType() === 'stylesheet') cssRejected = true;
          await route.abort('blockedbyclient');
        },
        continue: async () => {
          if(request.url() === origin + '/') return route.fulfill({contentType: 'text/html', body:
            `<!doctype html><meta charset="utf-8"><style>${css}</style><main><nav class="step-nav"><button type="button" onclick="document.body.dataset.clicked='yes'">OCR選択</button></nav><img src="${imageUrl}"></main>`});
          assert.equal(request.url(), imageUrl);
          imageContinued = true;
          return route.fulfill({contentType: 'image/png', body: Buffer.from(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfZkAAAAASUVORK5CYII=', 'base64')});
        },
      };
      if(legacy && new URL(request.url()).origin !== origin) {
        await externalGate;
        return wrapped.abort();
      }
      return gateReadonlyResource(wrapped, {origin, allowedExternalUrls, externalGate, onWrite: () => assert.fail()});
    });
    const page = await context.newPage();
    await page.goto(origin + '/', {waitUntil: 'domcontentloaded'});
    const click = () => page.locator('.step-nav button').filter({hasText: 'OCR選択'}).click({timeout: 1500});
    if(legacy) {
      await assert.rejects(click(), error => error.name === 'TimeoutError'
        && error.message.includes('visible, enabled and stable'));
      assert.equal(cssRejected, false);
    } else {
      await click();
      assert.equal(await page.locator('body').getAttribute('data-clicked'), 'yes');
      assert.equal(cssRejected, true);
      assert.equal(imageContinued, false);
      allowedExternalUrls.add(imageUrl);
      release();
      await page.locator('img').evaluate(img => img.complete ? undefined : new Promise((resolve, reject) => {
        img.onload = resolve; img.onerror = reject;
      }));
      assert.equal(imageContinued, true);
      assert.ok(await page.locator('img').evaluate(img => img.naturalWidth > 0));
    }
  } finally {
    release();
    await context.close();
    await browser.close();
  }
});
