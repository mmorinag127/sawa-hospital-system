const READ_TIMEOUT_MS = 30000;

// Separate page startup from the first actual API request. Never accept a retry.
export function firstJsonRequest(page, expectedUrl, timing, {
  now = () => performance.now(), setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  let first, response, timer, settled = false;
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  const expected = new URL(expectedUrl);
  timing.waitStartedAt = now();
  const cleanup = () => {
    clearTimer(timer);
    for (const [event, handler] of listeners) page.off(event, handler);
  };
  const fail = reason => {
    if (settled) return;
    settled = true;
    timing.finishedAt = now();
    timing.failure = reason;
    cleanup();
    reject(new Error(reason));
  };
  const onRequest = request => {
    if (first || settled || request.method() !== 'GET') return;
    let url;
    try { url = new URL(request.url()); } catch { return; }
    if (url.origin !== expected.origin || url.pathname !== expected.pathname
      || url.search !== expected.search || url.username || url.password || url.hash) return;
    first = request;
    timing.requestStartedAt = now();
    clearTimer(timer);
    timer = setTimer(() => fail('request-body-timeout'), READ_TIMEOUT_MS);
  };
  const onResponse = candidate => {
    if (settled || !first || candidate.request() !== first) return;
    response = candidate;
    timing.responseAt = now();
    if (response.status() !== 200) fail('request-status-invalid');
  };
  const onFinished = async request => {
    if (settled || !first || request !== first) return;
    // requestfinished is emitted after the response body has downloaded.
    timing.bodyFinishedAt = now();
    if (timing.bodyFinishedAt - timing.requestStartedAt >= READ_TIMEOUT_MS) {
      fail('request-body-timeout');
      return;
    }
    try {
      const completed = response || await request.response();
      if (settled) return;
      if (!completed || completed.status() !== 200) { fail('request-status-invalid'); return; }
      if (await completed.finished()) { fail('request-body-failed'); return; }
      if (settled) return;
      // Parsing is still mandatory below in the verifier; only transfer is timed here.
      settled = true;
      timing.finishedAt = now();
      cleanup();
      resolve(completed);
    } catch { fail('request-body-failed'); }
  };
  const onFailed = request => { if (first && request === first) fail('request-failed'); };
  const onClosed = () => fail('page-closed');
  const listeners = [
    ['request', onRequest], ['response', onResponse], ['requestfinished', onFinished],
    ['requestfailed', onFailed], ['close', onClosed], ['crash', onClosed],
  ];
  for (const [event, handler] of listeners) page.on(event, handler);
  timer = setTimer(() => fail('request-not-issued'), READ_TIMEOUT_MS);
  return { promise, cancel: () => fail('read-cancelled') };
}
