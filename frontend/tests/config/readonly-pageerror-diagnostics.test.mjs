import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { attachPageErrorDiagnostics, diagnosticPath, sanitizePageError, PAGEERROR_DIAGNOSTIC_LIMIT } from '../../scripts/readonly-pageerror-diagnostics.mjs';
const origin = 'https://fixture.invalid';
const businessPath = path => path === '/hospital/manual-library';
const secret = 'Bearer fakeBearerToken user@example.invalid';

test('message/stack/input/query/foreign URLs are never retained', () => {
  const message = `Minified React error #418; ${secret} https://foreign.invalid/?token=fakeBearer`;
  const diagnostic = sanitizePageError({ name: 'Error', message,
    stack: `${message}\n at run (${origin}/_next/static/chunks/app.js?token=fakeBearer#secret:12:34)\n at x (https://foreign.invalid/secret.js:1:2)\n at y (${origin}/api/secret.js:1:2)` }, origin);
  assert.equal(diagnostic.classification, 'react-minified');
  assert.equal(diagnostic.reactCode, '418');
  assert.deepEqual(diagnostic.stack, [{ file: 'app.js', line: '12', column: '34' }]);
  assert.equal(diagnostic.messageHash, createHash('sha256').update(message).digest('hex'));
  assert.doesNotMatch(JSON.stringify(diagnostic), /Bearer|example|foreign|token|secret|https/);
  assert.equal(diagnosticPath(`${origin}/hospital/manual-library?token=fakeBearer#email`, origin, businessPath), '/hospital/manual-library');
  for (const url of ['https://foreign.invalid/hospital/manual-library', `${origin}/login`, `${origin}/api/secret`, `${origin}/manuals/other/file.pdf`, 'about:blank']) {
    assert.equal(diagnosticPath(url, origin, businessPath), null);
  }
});

test('only actual standard classifiers are emitted; unknown stays hash-only with metadata', () => {
  assert.equal(sanitizePageError({ name: 'TypeError', message: 'Load failed' }, origin).classification, 'load-failed');
  assert.equal(sanitizePageError({ name: 'SecurityError', message: secret }, origin).classification, 'security-error');
  const unknown = sanitizePageError({ name: secret, message: `prefix Minified React error #99; ${secret}`, stack: secret }, origin);
  assert.equal(unknown.name, 'unknown');
  assert.equal(unknown.classification, 'unknown');
  assert.equal(Object.hasOwn(unknown, 'reactCode'), false);
  assert.deepEqual(unknown.stack, []);
  assert.match(unknown.messageHash, /^[a-f0-9]{64}$/);
  assert.doesNotMatch(JSON.stringify({ ...unknown, messageHash: undefined }), /Bearer|example|99/);
});

test('all errors count across pages, bounded diagnostics report dropped count and fixed metadata', () => {
  const context = new EventEmitter(), result = { phase: 'verify-readonly-pages', pageErrorCount: 0 };
  attachPageErrorDiagnostics(context, result, { origin, businessPath, phases: new Set(['verify-readonly-pages']), now: () => '2026-10-16T00:00:00.000Z' });
  const first = Object.assign(new EventEmitter(), { url: () => `${origin}/hospital/manual-library?token=fakeBearer` });
  const second = Object.assign(new EventEmitter(), { url: () => 'https://foreign.invalid/private' });
  context.emit('page', first); context.emit('page', second);
  first.emit('pageerror', new Error(secret));
  result.phase = secret;
  second.emit('pageerror', new Error(secret));
  for (let i = 0; i < PAGEERROR_DIAGNOSTIC_LIMIT; i++) first.emit('pageerror', new Error(secret));
  assert.equal(result.pageErrorCount, PAGEERROR_DIAGNOSTIC_LIMIT + 2);
  assert.equal(result.pageErrorDiagnostics.length, PAGEERROR_DIAGNOSTIC_LIMIT);
  assert.equal(result.pageErrorDiagnosticsDropped, 2);
  assert.equal(result.pageErrorDiagnostics[0].page, 1);
  assert.equal(result.pageErrorDiagnostics[1].page, 2);
  assert.equal(result.pageErrorDiagnostics[1].pathname, null);
  assert.equal(result.pageErrorDiagnostics[1].phase, 'unknown');
  assert.equal(result.pageErrorDiagnostics[0].at, '2026-10-16T00:00:00.000Z');
  assert.doesNotMatch(JSON.stringify(result.pageErrorDiagnostics), /Bearer|example|foreign/);
});

test('observer remains wired before first page; existing zero-error failure assertion remains', () => {
  const source = readFileSync(new URL('../../scripts/verify-output-source-live.mjs', import.meta.url), 'utf8');
  assert.ok(source.indexOf('attachPageErrorDiagnostics(context') < source.indexOf('page = await context.newPage()'));
  assert.match(source, /assert.equal\(result.pageErrorCount, 0\)/);
  assert.ok(source.includes("sessionStorage.setItem('auth_header', 'Bearer ' + sessionToken)"));
});
