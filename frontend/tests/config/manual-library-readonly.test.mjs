import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { readFileSync } from 'node:fs';
import { STG_OUTPUT_SOURCE_ORIGIN as origin } from '../../scripts/output-source-live-origin.mjs';
import { manualCatalog, manualPath, originalManual, manualReceipt, clickManual } from '../../scripts/manual-library-readonly.mjs';
const href = manualCatalog().links[0].href;

test('all seven source categories and fifteen linked originals are safe and complete', () => {
  const { groups, links } = manualCatalog();
  assert.equal(groups.length, 7);
  assert.equal(links.length, 15);
  assert.equal(links.filter(link => link.href.endsWith('.pdf')).length, 14);
  assert.equal(links.filter(link => link.href.endsWith('.md')).length, 1);
  for (const link of links) {
    const bytes = originalManual(origin, link.href);
    const receipt = manualReceipt(origin, link.href, 200, bytes, bytes);
    assert.equal(receipt.bytes, bytes.length);
    assert.match(receipt.sha256, /^[0-9a-f]{64}$/);
    assert.equal(receipt.path, link.href);
  }
});
test('off-origin, encoded traversal, arbitrary paths and login HTML never accepted', () => {
  for (const path of ['https://evil.invalid' + href, '//evil.invalid' + href, '/manuals/../secret.pdf',
    '/manuals/current-stg-20260519/%2e%2e/secret.pdf', href + '?token=secret', href + '#fragment',
    '/api/orders', '/manuals/current-stg-20260519/other.html']) assert.throws(() => manualPath(origin, path));
  assert.throws(() => manualPath('https://evil.invalid', href));
  const original = originalManual(origin, href);
  for (const status of [302, 401, 403, 404, 500]) assert.throws(() => manualReceipt(origin, href, status, original, original), /manual-http-failed/);
  for (const bytes of [original.subarray(0, 20), Buffer.from('<html>secret login</html>'), Buffer.alloc(0)]) {
    assert.throws(() => manualReceipt(origin, href, 200, bytes, original), /manual-body-mismatch/);
  }
});
function fixture({ attachment = false, status = 200, broken = false, inlineDownload = false, downloadUrl = origin + href } = {}) {
  const context = new EventEmitter();
  const page = new EventEmitter(); page.context = () => context;
  const popup = new EventEmitter(); let closed = 0;
  popup.close = async () => { closed++; };
  const bytes = originalManual(origin, href);
  const request = { url: () => origin + href, method: () => 'GET' };
  const response = { request: () => request, status: () => status,
    headers: () => ({ 'content-disposition': attachment ? 'attachment' : 'inline' }),
    finished: async () => broken || inlineDownload ? new Error('secret') : null, body: async () => bytes };
  const link = { click: async () => {
    context.emit('request', request);
    context.emit('page', popup);
    context.emit('response', response);
    if (inlineDownload) for (let i = 0; i < 10; i++) await Promise.resolve();
    if (attachment || inlineDownload) popup.emit('download', { url: () => downloadUrl,
      createReadStream: async () => Readable.from([bytes]), failure: async () => broken ? 'secret' : null });
  } };
  return { page, link, bytes, context, closed: () => closed };
}
test('actual click event correlates inline and download bodies and closes only owned popup', async () => {
  for (const attachment of [false, true]) {
    const f = fixture({ attachment });
    const response = await clickManual({ ...f, origin, href });
    assert.equal(response.status, 200);
    assert.deepEqual(response.body, f.bytes);
    assert.equal(f.closed(), 1);
    assert.equal(f.context.listenerCount('response'), 0);
  }
});
test('inline response-body failure cannot preempt the later actual download stream', async () => {
  const f = fixture({ inlineDownload: true });
  const result = await clickManual({ ...f, origin, href });
  assert.deepEqual(result.body, f.bytes);
  assert.equal(result.status, 200);
  assert.equal(f.closed(), 1);
});
test('HTTP failure and interrupted inline/download bodies fail without secret messages', async () => {
  for (const options of [{ status: 500 }, { broken: true }, { attachment: true, broken: true }]) {
    const f = fixture(options);
    let expire;
    const pending = clickManual({ ...f, origin, href }, { setTimer: (fn, ms) => { assert.equal(ms, 30000); expire = fn; }, clearTimer: () => {} });
    const rejected = assert.rejects(pending, error => /^manual-(http|body|download)-failed$/.test(error.message));
    for (let i = 0; i < 20; i++) await Promise.resolve();
    if (!options.attachment && options.broken) expire();
    await rejected;
    assert.equal(f.closed(), 1);
  }
});

test('AST extraction rejects computed groups and duplicate declarations without fallback', () => {
  for (const source of ['const groups = load();', 'const groups = []; const groups = [];', 'const other = [];']) {
    assert.throws(() => manualCatalog(source));
  }
});
test('live wrapper rejects local or invalid targets before any browser operation', async () => {
  let calls = 0;
  const page = { context: () => { calls++; throw new Error('unexpected browser operation'); } };
  const link = { click: () => { calls++; } };
  for (const target of [
    { origin: 'http://127.0.0.1:1234', href },
    { origin, href: '/api/orders' },
    { origin, href: href + '?secret=value' },
  ]) await assert.rejects(clickManual({ page, link, ...target }), /manual-path-invalid/);
  assert.equal(calls, 0);
});
test('a different download URL never supplies the clicked resource body', async () => {
  for (const downloadUrl of ['https://other.invalid' + href, origin + href + '?other=1']) {
    const f = fixture({ attachment: true, downloadUrl });
    await assert.rejects(clickManual({ ...f, origin, href }), /^Error: manual-download-target$/);
    assert.equal(f.closed(), 1);
    assert.equal(f.context.listenerCount('response'), 0);
  }
});
test('manual check is last independent check and dashboard navigation is source-grounded', () => {
  const source = readFileSync(new URL('../../scripts/hospital-readonly-pages.mjs', import.meta.url), 'utf8');
  assert.ok(source.indexOf("await check('manual-library'") > source.indexOf("await check('menu-masters'"));
  assert.ok(source.indexOf("await check('manual-library'") < source.indexOf("mark('readonly-check-050')"));
  assert.ok(readFileSync(new URL('../../src/pages/hospital-dashboard.tsx', import.meta.url), 'utf8').includes('href="/manual-library"'));
});
