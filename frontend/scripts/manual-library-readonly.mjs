import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFileSync, realpathSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, sep, basename } from 'node:path';
import { isAllowedOutputSourceOrigin } from './output-source-live-origin.mjs';

const publicRoot = fileURLToPath(new URL('../public/', import.meta.url));
const ts = createRequire(import.meta.url)('typescript');
export function manualPath(origin, href) {
  if (!isAllowedOutputSourceOrigin(origin) || typeof href !== 'string' ||
      !/^\/manuals\/current-stg-20260519\/[A-Za-z0-9_-]+\.(pdf|md)$/.test(href)) throw new Error('manual-path-invalid');
  const url = new URL(href, origin);
  if (url.origin !== origin || url.search || url.hash) throw new Error('manual-path-invalid');
  return url.pathname;
}

export function manualCatalog(source = readFileSync(new URL('../src/pages/manual-library.tsx', import.meta.url), 'utf8')) {
  const ast = ts.createSourceFile('manual-library.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  assert.equal(ast.parseDiagnostics.length, 0);
  const declarations = ast.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations])
    .filter(node => ts.isIdentifier(node.name) && node.name.text === 'groups');
  assert.equal(declarations.length, 1);
  function literal(node) {
    assert.ok(node);
    if (ts.isAsExpression(node)) return literal(node.expression);
    if (ts.isStringLiteral(node)) return node.text;
    if (ts.isArrayLiteralExpression(node)) return node.elements.map(literal);
    assert.ok(ts.isObjectLiteralExpression(node));
    const result = {};
    for (const property of node.properties) {
      assert.ok(ts.isPropertyAssignment(property) && (ts.isStringLiteral(property.name) || ts.isIdentifier(property.name)));
      const key = property.name.text;
      assert.ok(!Object.hasOwn(result, key) && key !== '__proto__');
      result[key] = literal(property.initializer);
    }
    return result;
  }
  const groups = literal(declarations[0].initializer);
  assert.equal(groups.length, 7);
  const links = groups.flatMap(group => group.manuals.map(manual => ({ ...manual, title: group.title })));
  assert.equal(links.length, 15);
  assert.equal(links.filter(link => link.href.endsWith('.pdf')).length, 14);
  assert.equal(links.filter(link => link.href.endsWith('.md')).length, 1);
  assert.equal(new Set(links.map(link => link.href)).size, 15);
  return { groups, links };
}

export function originalManual(origin, href) {
  const path = manualPath(origin, href);
  const root = realpathSync(publicRoot);
  const file = realpathSync(resolve(root, '.' + path));
  if (!file.startsWith(root + sep)) throw new Error('manual-source-outside-public');
  return readFileSync(file);
}

export function manualReceipt(origin, href, status, body, original) {
  const path = manualPath(origin, href);
  if (status !== 200) throw new Error('manual-http-failed');
  if (!Buffer.isBuffer(body) || !body.length || !body.equals(original)) throw new Error('manual-body-mismatch');
  if (path.endsWith('.pdf') && body.subarray(0, 5).toString() !== '%PDF-') throw new Error('manual-format-invalid');
  if (/^\s*(?:<!doctype html|<html)/i.test(body.subarray(0, 256).toString())) throw new Error('manual-format-invalid');
  return { path, bytes: body.length, sha256: createHash('sha256').update(body).digest('hex') };
}

export async function clickManual({ page, link, origin, href }, timers) {
  const path = manualPath(origin, href);
  return clickResourceBody({ page, link, url: origin + path }, timers);
}

export async function clickResourceBody({ page, link, url }, { setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  const context = page.context();
  const owned = new Set();
  let firstRequest;
  let status;
  let bodyFailed = false;
  let finished = false;
  let resolveBody, rejectBody;
  const bodyReady = new Promise((resolve, reject) => { resolveBody = resolve; rejectBody = reject; });
  const fail = code => { if (!finished) { finished = true; rejectBody(new Error(code)); } };
  const complete = body => { if (!finished) { finished = true; resolveBody(body); } };
  const matches = value => value === url;
  const onRequest = request => {
    if (!firstRequest && matches(request.url()) && request.method() === 'GET') firstRequest = request;
  };
  const onResponse = async response => {
    if (response.request() !== firstRequest || !firstRequest) return;
    status = response.status();
    if (status !== 200) { fail('manual-http-failed'); return; }
    // Attachment bodies come from the actual browser download stream, not a second fetch.
    if (/attachment/i.test(response.headers()['content-disposition'] || '')) return;
    try {
      if (await response.finished()) { bodyFailed = true; return; }
      complete(await response.body());
    } catch { bodyFailed = true; }
  };
  const onDownload = async download => {
    if (!matches(download.url())) { fail('manual-download-target'); return; }
    try {
      const stream = await download.createReadStream();
      if (!stream) { fail('manual-download-failed'); return; }
      const chunks = [];
      for await (const chunk of stream) chunks.push(Buffer.from(chunk));
      if (await download.failure()) { fail('manual-download-failed'); return; }
      complete(Buffer.concat(chunks));
    } catch { fail('manual-download-failed'); }
  };
  const onPage = popup => { owned.add(popup); popup.on('download', onDownload); };
  context.on('request', onRequest);
  context.on('response', onResponse);
  context.on('page', onPage);
  page.on('download', onDownload);
  const timer = setTimer(() => fail(bodyFailed ? 'manual-body-failed' : 'manual-body-timeout'), 30000);
  const handled = bodyReady.then(body => ({ body }), error => ({ error }));
  try {
    await link.click();
    const outcome = await handled;
    if (outcome.error) throw outcome.error;
    if (!firstRequest || status !== 200) throw new Error('manual-response-unproven');
    return { status, body: outcome.body };
  } finally {
    clearTimer(timer);
    context.off('request', onRequest);
    context.off('response', onResponse);
    context.off('page', onPage);
    page.off('download', onDownload);
    for (const popup of owned) { popup.off('download', onDownload); await popup.close(); }
  }
}

export async function verifyManualLibrary({ page, expect, origin, output, result, mark }) {
  const { groups, links } = manualCatalog();
  mark('manual-heading');
  await expect(page.getByRole('heading', { name: 'マニュアル集', exact: true })).toBeVisible();
  await expect(page.locator('article.manual-row')).toHaveCount(7);
  await expect(page.locator('article.manual-row a')).toHaveCount(15);
  result.manualFiles = [];
  for (const group of groups) await expect(page.getByRole('heading', { name: group.title, exact: true })).toBeVisible();
  for (const manual of links) {
    mark('manual-link');
    const path = manualPath(origin, manual.href);
    const link = page.locator(`article.manual-row a[href="${path}"]`);
    await expect(link).toHaveCount(1);
    await expect(link).toHaveText(manual.label);
    const original = originalManual(origin, path);
    mark('manual-click-body');
    let response;
    try {
      response = await clickManual({ page, link, origin, href: path });
    } catch (error) {
      const codes = ['manual-http-failed', 'manual-body-failed', 'manual-download-target', 'manual-download-failed', 'manual-body-timeout', 'manual-response-unproven'];
      const code = codes.includes(error?.message) ? error.message : 'manual-click-failed';
      result.manualFiles.push({ path, failureCode: code });
      mark(code);
      throw new Error(code);
    }
    mark('manual-body-parity');
    const receipt = manualReceipt(origin, path, response.status, response.body, original);
    mkdirSync(resolve(output, 'manuals'), { recursive: true });
    writeFileSync(resolve(output, 'manuals', basename(path)), response.body);
    result.manualFiles.push(receipt);
  }
}
