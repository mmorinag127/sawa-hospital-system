import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { sanitizePageError } from '../../scripts/readonly-pageerror-diagnostics.mjs';
const origin = 'https://fixture.invalid';

for (const name of ['ChunkLoadError', 'Unhandled Promise Rejection']) {
  test(`${name}: exact chunk prefix is classified without leaking source data`, () => {
    const message = `${name === 'ChunkLoadError' ? '' : 'ChunkLoadError: '}Failed to load chunk https://foreign.invalid/private?token=fakeBearer user@example.invalid`;
    const result = sanitizePageError({ name, message, stack: `${message}\n at run (${origin}/_next/static/chunks/runtime.js?token=fakeBearer#private:12:34)\n at other (https://foreign.invalid/private.js:1:2)` }, origin);
    assert.equal(result.name, name);
    assert.equal(result.classification, 'chunk-load-failed');
    assert.deepEqual(result.stack, [{ file: 'runtime.js', line: '12', column: '34' }]);
    assert.equal(result.messageHash, createHash('sha256').update(message).digest('hex'));
    assert.doesNotMatch(JSON.stringify(result), /fakeBearer|example|foreign|private|https|token/);
  });
}

test('unrelated names and nonmatching prefixes retain unknown classification and no stack', () => {
  for (const [name, message] of [
    ['Unhandled Promise Rejection', 'Failed to load chunk secret'],
    ['Unhandled Promise Rejection', 'prefix ChunkLoadError: Failed to load chunk secret'],
    ['Other', 'ChunkLoadError: Failed to load chunk secret'],
    ['Error', 'Failed to load chunk secret'],
    ['ChunkLoadError', 'unrelated secret'],
  ]) {
    const result = sanitizePageError({ name, message, stack: `message\n at run (${origin}/_next/static/chunks/runtime.js:1:2)` }, origin);
    assert.equal(result.classification, 'unknown');
    assert.equal(result.name, ['Error', 'ChunkLoadError'].includes(name) ? name : 'unknown');
    assert.deepEqual(result.stack, []);
    assert.equal(result.messageHash, createHash('sha256').update(message).digest('hex'));
    assert.doesNotMatch(JSON.stringify(result), /secret|runtime/);
  }
});
