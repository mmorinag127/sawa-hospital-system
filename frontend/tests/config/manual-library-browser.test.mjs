import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { manualCatalog, originalManual, clickResourceBody, manualReceipt } from '../../scripts/manual-library-readonly.mjs';
import { STG_OUTPUT_SOURCE_ORIGIN as origin } from '../../scripts/output-source-live-origin.mjs';
const { webkit } = createRequire(import.meta.url)('playwright');

for (const mode of ['inline', 'attachment', 'markdown']) test(`WebKit actual HTTP ${mode} click receives complete body`, async () => {
  let server, browser;
  const sockets = new Set();
  const href = manualCatalog().links.find(link => link.href.endsWith(mode === 'markdown' ? '.md' : '.pdf')).href;
  const original = originalManual(origin, href);
  let requests = 0;
  try {
    server = createServer((request, response) => {
      assert.equal(request.method, 'GET');
      if (request.url === '/fixture') {
        response.writeHead(200, { 'content-type': 'text/html' });
        response.end(`<a href="${href}" target="_blank" rel="noreferrer">manual</a>`);
        return;
      }
      assert.equal(request.url, href);
      requests++;
      response.writeHead(200, {
        'content-type': mode === 'markdown' ? 'text/markdown' : 'application/pdf',
        'content-disposition': mode === 'attachment' ? 'attachment; filename="manual.pdf"' : 'inline',
        'content-length': original.length,
      });
      response.end(original);
    });
    server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const localOrigin = `http://127.0.0.1:${server.address().port}`;
    browser = await webkit.launch({ headless: true });
    const context = await browser.newContext({ acceptDownloads: true });
    try {
      let popups = 0;
      let downloads = 0;
      context.on('page', popup => { popups++; popup.on('download', () => { downloads++; }); });
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== localOrigin || !['/fixture', href].includes(url.pathname) || route.request().method() !== 'GET') return route.abort();
        return route.continue();
      });
      const page = await context.newPage();
      await page.goto(localOrigin + '/fixture');
      const result = await clickResourceBody({ page, link: page.getByRole('link', { name: 'manual' }), url: localOrigin + href });
      assert.equal(result.status, 200);
      assert.deepEqual(result.body, original);
      assert.equal(manualReceipt(origin, href, result.status, result.body, original).bytes, original.length);
      assert.equal(requests, 1);
      assert.ok(popups >= 1);
      if (mode === 'attachment') assert.equal(downloads, 1);
      assert.deepEqual(context.pages(), [page]);
    } finally { await context.close(); }
  } finally {
    try { await browser?.close(); }
    finally {
      for (const socket of sockets) socket.destroy();
      if (server) await new Promise(resolve => server.close(resolve));
    }
  }
  assert.equal(server.listening, false);
  assert.equal(browser.isConnected(), false);
});
