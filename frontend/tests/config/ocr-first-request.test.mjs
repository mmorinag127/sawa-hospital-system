import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { firstJsonRequest } from '../../scripts/ocr-first-request.mjs';
import { settleResponse } from '../../scripts/verify-ocr-live.mjs';

const url = 'https://worker.example/api/orders/O/workflow-v2/inspection?include_ocr_results=true&history_details=false';
function setup() {
  let time = 0, sequence = 0;
  const timers = new Map(), page = new EventEmitter(), timing = {};
  const watcher = firstJsonRequest(page, url, timing, {
    now: () => time,
    setTimer(fn, delay) { const id = ++sequence; timers.set(id, {fn, at: time + delay}); return id; },
    clearTimer(id) { timers.delete(id); },
  });
  const receipt = settleResponse(watcher.promise);
  return {page, timing, timers, watcher, receipt,
    tick(ms) {
      const end = time + ms;
      for (;;) {
        const next = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        time = next[1].at; timers.delete(next[0]); next[1].fn();
      }
      time = end;
    },
  };
}
function request(target = url, method = 'GET', status = 200, failure = null) {
  const req = {url: () => target, method: () => method, response: async () => response};
  const response = {request: () => req, status: () => status, finished: async () => failure,
    json: async () => ({order_id: 'O'})};
  return {req, response};
}
function clean(f) {
  assert.equal(f.timers.size, 0);
  assert.deepEqual(f.page.eventNames(), []);
}

test('startup 1.44s plus body 29s succeeds on the first request without API deadline extension', async () => {
  const f = setup(), {req, response} = request();
  f.tick(1440); f.page.emit('request', req);
  f.tick(28900); f.page.emit('response', response);
  f.tick(100); f.page.emit('requestfinished', req);
  assert.deepEqual(await f.receipt, {ok: true, response});
  assert.equal(f.timing.bodyFinishedAt - f.timing.requestStartedAt, 29000);
  assert.equal(f.timing.bodyFinishedAt - f.timing.waitStartedAt, 30440);
  clean(f);
});

test('no request stops at 30s and does not leak listeners', async () => {
  const f = setup(); f.tick(30000);
  assert.equal((await f.receipt).ok, false);
  assert.equal(f.timing.failure, 'request-not-issued'); clean(f);
});

test('response headers before 30s do not extend body deadline', async () => {
  const f = setup(), {req, response} = request();
  f.tick(1440); f.page.emit('request', req);
  f.tick(29999); f.page.emit('response', response);
  f.tick(1); f.page.emit('requestfinished', req);
  assert.equal((await f.receipt).ok, false);
  assert.equal(f.timing.failure, 'request-body-timeout'); clean(f);
});

for (const kind of ['requestfailed', 'body-error', 'status', 'non-json']) {
  test(`first ${kind} cannot be rescued by a later successful request`, async () => {
    const f = setup(), first = request(url, 'GET', kind === 'status' ? 503 : 200,
      kind === 'body-error' ? new Error('private-detail') : null);
    if (kind === 'non-json') first.response.json = async () => { throw new SyntaxError('private-body'); };
    const parsed = settleResponse(f.watcher.promise.then(response => response.json()));
    f.page.emit('request', first.req); f.page.emit('response', first.response);
    f.page.emit(kind === 'requestfailed' ? 'requestfailed' : 'requestfinished', first.req);
    const second = request();
    f.page.emit('request', second.req); f.page.emit('response', second.response); f.page.emit('requestfinished', second.req);
    assert.equal((await parsed).ok, false);
    assert.doesNotMatch(JSON.stringify(f.timing), /private-detail|private-body/); clean(f);
  });
}

test('second response cannot finish the first pending request', async () => {
  const f = setup(), first = request(), second = request();
  f.page.emit('request', first.req); f.tick(100);
  f.page.emit('request', second.req); f.page.emit('response', second.response); f.page.emit('requestfinished', second.req);
  f.tick(29900);
  assert.equal((await f.receipt).ok, false); clean(f);
});

for (const [target, method] of [
  [url.replace('worker.example', 'foreign.example'), 'GET'],
  [url.replace('/O/', '/B/'), 'GET'], [url.replace('=true', '=false'), 'GET'],
  [url + '&extra=1', 'GET'], [url.replace('?include_ocr_results=true', ''), 'GET'],
  [url + '#fragment', 'GET'], [url, 'POST'], ['not a URL', 'GET'],
]) {
  test(`unmatched URL/method cannot satisfy read: ${target} ${method}`, async () => {
    const f = setup(), r = request(target, method);
    f.page.emit('request', r.req); f.page.emit('response', r.response); f.page.emit('requestfinished', r.req);
    f.tick(30000); assert.equal((await f.receipt).ok, false);
    assert.equal(f.timing.failure, 'request-not-issued'); clean(f);
  });
}

for (const stop of ['close', 'crash', 'cancel']) test(`${stop} cleans both waiting phases`, async () => {
  for (const started of [false, true]) {
    const f = setup(); if (started) f.page.emit('request', request().req);
    if (stop === 'cancel') f.watcher.cancel(); else f.page.emit(stop);
    assert.equal((await f.receipt).ok, false); clean(f);
  }
});
