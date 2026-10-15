import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { diagnosticTimeline, captureFailureScreen, sanitizedFailureCode } from '../../scripts/verify-ocr-live.mjs';

const checks = ['verify-live-identity', 'await-network-idle', 'verify-review-state', 'open-step2', 'verify-review-controls', 'verify-evidence-text'];
test('all six checks retain start/end and duration, with bounded fixed failure classification', () => {
  for (const name of ['TimeoutError', 'AssertionError', 'secret-token']) {
    const result = {}; let time = 100;
    const timeline = diagnosticTimeline(result, () => time++);
    for (const check of checks) timeline.begin(check);
    timeline.fail({ name, message: 'credential', stack: 'response body' });
    assert.equal(result.timings.length, 6);
    assert.deepEqual(result.timings.map(item => item.check), checks);
    for (const item of result.timings) {
      assert.equal(item.endedAt - item.startedAt, item.elapsedMs);
      assert.ok(item.elapsedMs > 0);
      assert.notEqual(sanitizedFailureCode(item.check), 'browser-readonly-check-failed-at-unknown-phase');
    }
    assert.deepEqual(result.failure, {check: 'verify-evidence-text', type: name === 'secret-token' ? 'other' : name});
    assert.doesNotMatch(JSON.stringify(result), /credential|response body|secret-token/);
    assert.throws(() => timeline.begin('raw-secret'));
  }
});

test('successful check closes once without failure or raw data', () => {
  const result = {}; const timeline = diagnosticTimeline(result, () => 20);
  timeline.begin(checks[0]); timeline.end(); timeline.end();
  assert.deepEqual(result.timings, [{check: checks[0], startedAt: 20, endedAt: 20, elapsedMs: 0}]);
  assert.equal(result.failure, undefined);
});

for (const state of ['workflow', 'login-url', 'password', 'login-button', 'no-workflow', 'screenshot-failed']) {
  test(`failure capture safely handles ${state}`, async () => {
    let captures = 0;
    const page = {
      url: () => `https://example.test/${state === 'login-url' ? 'login' : 'hospital/orders/O/workflow-v2'}`,
      locator: selector => ({count: async () => selector === 'main .step-nav' ? (state === 'no-workflow' ? 0 : 1) : (state === 'password' ? 1 : 0)}),
      getByRole: () => ({count: async () => state === 'login-button' ? 1 : 0}),
      screenshot: async options => { captures++; assert.equal(options.timeout, 5000); if(state === 'screenshot-failed') throw Error('secret'); },
    };
    assert.equal(await captureFailureScreen(page, 'https://example.test', 'O', '/tmp'), state === 'workflow');
    assert.equal(captures, ['workflow','screenshot-failed'].includes(state) ? 1 : 0);
  });
}

test('original verification asserts, wait and readonly/external gates remain in production source', () => {
  const file = 'frontend/scripts/verify-ocr-live.mjs';
  const original = execFileSync('git', ['show', `725fd66139927ad60c058145647b79c61d705b44:${file}`], {encoding:'utf8'});
  const current = readFileSync(new URL('../../scripts/verify-ocr-live.mjs', import.meta.url), 'utf8');
  for (const line of original.split('\n').filter(line => /assert\.|await expect\(|waitForLoadState\('networkidle'\)|allowedExternalUrls\.has|allowedMethods\.has|route\.abort|\.click\(\)/.test(line))) {
    const expected = line.includes("item.pathname.endsWith('/ocr-results')")
      ? line.replace("item.pathname.endsWith('/ocr-results')", "item.pathname.endsWith('/inspection')") : line;
    assert.ok(current.includes(expected), expected);
  }
  assert.ok(current.includes("watchRead('ocr-candidates', '/workflow-v2/inspection?include_ocr_results=true')"));
  const firstRequest = readFileSync(new URL('../../scripts/ocr-first-request.mjs', import.meta.url), 'utf8');
  assert.ok(firstRequest.includes('url.search !== expected.search'));
  assert.ok(firstRequest.includes("request.method() !== 'GET'"));
  assert.ok(firstRequest.includes('url.origin !== expected.origin || url.pathname !== expected.pathname'));
  assert.ok(current.includes('assert.equal(liveOcrResults?.order_id, identity.orderId)'));
  assert.ok(current.includes('assert.ok(Array.isArray(liveOcrResults?.results))'));
  let offset = 0;
  for (const check of checks) {
    const found = current.indexOf(`diagnostics.begin('${check}')`, offset);
    assert.ok(found >= offset, check); offset = found + 1;
  }
});
