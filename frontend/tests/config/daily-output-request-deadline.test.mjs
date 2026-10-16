import assert from 'node:assert/strict';
import test from 'node:test';
import { waitForDailyOutputReceipt } from '../../scripts/daily-output-request-deadline.mjs';
import { settleDailyOutputReceipts } from '../../scripts/verify-output-source-live.mjs';

const origin = 'https://stg.example.invalid';
const targetDate = '2026-09-13';
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
function harness() {
  let time = 0;
  let id = 0;
  const timers = new Map();
  const listeners = new Set();
  const clock = {
    now: () => time,
    setTimer: (fn, ms) => { timers.set(++id, { fn, at: time + ms }); return id; },
    clearTimer: key => timers.delete(key),
  };
  const page = { waitForRequest(predicate, { timeout }) {
    assert.equal(timeout, 30000);
    return new Promise((resolve, reject) => {
      const listener = request => {
        if (!predicate(request)) return;
        listeners.delete(listener);
        clock.clearTimer(timer);
        resolve(request);
      };
      const timer = clock.setTimer(() => {
        listeners.delete(listener);
        reject(new Error('secret wait error'));
      }, timeout);
      listeners.add(listener);
    });
  } };
  return {
    start(section = 'primary') {
      const diagnostic = {};
      const result = waitForDailyOutputReceipt(page, { origin, targetDate, section, diagnostic }, clock);
      return { diagnostic, result };
    },
    emit(request) { for (const listener of [...listeners]) listener(request); },
    async advance(ms) {
      time += ms;
      for (const [key, timer] of [...timers]) {
        if (timer.at <= time) { timers.delete(key); timer.fn(); }
      }
      await flush();
    },
  };
}
function request(section = 'primary', options = {}) {
  const receipt = { section, value: 17 };
  const response = {
    status: () => options.status ?? 200,
    finished: () => options.body ?? Promise.resolve(null),
    json: async () => { if (options.badJson) throw new Error('secret body'); return receipt; },
  };
  return {
    method: () => options.method ?? 'GET',
    url: () => options.url ?? `${origin}/api/orders/daily-output-context?date=${targetDate}&section=${section}`,
    failure: () => options.failed ? { errorText: 'secret abort' } : null,
    response: async () => options.failed ? null : response,
    expected: { section, response, receipt },
  };
}

test('delayed click/request start does not consume the request body budget', async () => {
  const h = harness();
  const pending = h.start();
  await h.advance(20000);
  const body = deferred();
  const req = request('primary', { body: body.promise });
  h.emit(req);
  await flush();
  await h.advance(29000);
  body.resolve(null);
  assert.deepEqual(await pending.result, req.expected);
  assert.deepEqual(pending.diagnostic, { requestObserved: true, completed: true, elapsedMs: 29000, failureCode: null });
});

test('fast responses for all three sections preserve exact correlated receipts', async () => {
  const h = harness();
  const sections = ['primary', 'bags', 'totals'];
  const pending = sections.map(section => h.start(section));
  const requests = sections.map(section => request(section));
  requests.forEach(req => h.emit(req));
  assert.deepEqual(await settleDailyOutputReceipts(pending.map(p => p.result)), { ok: true, receipts: requests.map(r => r.expected) });
  pending.forEach(p => assert.equal(p.diagnostic.completed, true));
});

test('unobserved request and incorrect URL/method do not pass', async () => {
  const h = harness();
  const p = h.start();
  const rejected = assert.rejects(p.result, { message: 'request-not-observed' });
  for (const options of [
    { method: 'POST' }, { url: `${origin}/other` },
    { url: 'https://other.invalid/api/orders/daily-output-context?date=2026-09-13&section=primary' },
    { url: `${origin}/api/orders/daily-output-context?date=2026-09-14&section=primary` },
  ]) h.emit(request('primary', options));
  h.emit(request('bags'));
  await h.advance(30000);
  await rejected;
  assert.equal(p.diagnostic.requestObserved, false);
});

for (const [name, options, code] of [
  ['abort', { failed: true }, 'request-failed'],
  ['HTTP error', { status: 500 }, 'http-status'],
  ['body failure', { body: Promise.resolve(new Error('secret transport')) }, 'body-failed'],
  ['invalid JSON', { badJson: true }, 'invalid-json'],
]) test(`${name} cannot be rescued by a later successful request`, async () => {
  const h = harness();
  const p = h.start();
  h.emit(request('primary', options));
  h.emit(request());
  await assert.rejects(p.result, { message: code });
  assert.equal(p.diagnostic.failureCode, code);
  assert.equal(p.diagnostic.completed, false);
  assert.equal(JSON.stringify(p.diagnostic).includes('secret'), false);
});

test('deadline exhausts at 30 seconds and late body cannot overwrite failure', async () => {
  const h = harness();
  const p = h.start();
  const body = deferred();
  h.emit(request('primary', { body: body.promise }));
  await flush();
  const rejected = assert.rejects(p.result, { message: 'request-deadline' });
  await h.advance(30000);
  await rejected;
  body.resolve(null);
  await flush();
  assert.deepEqual(p.diagnostic, { requestObserved: true, completed: false, elapsedMs: 30000, failureCode: 'request-deadline' });
});

test('budget starts at observation even before the response continuation runs', async () => {
  const h = harness();
  const p = h.start();
  h.emit(request());
  const rejected = assert.rejects(p.result, { message: 'request-deadline' });
  await h.advance(30000);
  await rejected;
  assert.equal(p.diagnostic.elapsedMs, 30000);
});
