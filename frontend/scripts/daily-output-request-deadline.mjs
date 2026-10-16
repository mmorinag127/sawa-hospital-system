import { performance } from 'node:perf_hooks';

export function waitForDailyOutputReceipt(page, { origin, targetDate, section, diagnostic }, {
  now = () => performance.now(), setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  const limit = 30000;
  let observedAt;
  Object.assign(diagnostic, { requestObserved: false, completed: false, elapsedMs: null, failureCode: null });
  // Install before click. The first matching Request is the sole owner of this receipt.
  const requestReady = page.waitForRequest(request => {
    const url = new URL(request.url());
    const matches = request.method() === 'GET' && url.origin === origin
      && url.pathname === '/api/orders/daily-output-context'
      && url.searchParams.get('date') === targetDate && url.searchParams.get('section') === section;
    if (matches && observedAt === undefined) {
      observedAt = now();
      diagnostic.requestObserved = true;
    }
    return matches;
  }, { timeout: limit });
  return requestReady.then(request => new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    function finish(code, value) {
      if (settled) return;
      settled = true;
      clearTimer(timer);
      diagnostic.elapsedMs = Math.max(0, now() - observedAt);
      diagnostic.failureCode = code;
      diagnostic.completed = code === null;
      if (code) reject(new Error(code));
      else resolve(value);
    }
    const remaining = limit - (now() - observedAt);
    if (!(remaining > 0)) { finish('request-deadline'); return; }
    timer = setTimer(() => finish('request-deadline'), remaining);
    void (async () => {
      let stage = 'request-failed';
      try {
        const response = await request.response();
        if (!response || request.failure()) { finish('request-failed'); return; }
        if (response.status() !== 200) { finish('http-status'); return; }
        stage = 'body-failed';
        const error = await response.finished();
        if (error || request.failure()) { finish('body-failed'); return; }
        stage = 'invalid-json';
        const receipt = await response.json();
        if (now() - observedAt >= limit) { finish('request-deadline'); return; }
        finish(null, { section, response, receipt });
      } catch { finish(stage); }
    })();
  }), () => {
    diagnostic.failureCode = 'request-not-observed';
    throw new Error('request-not-observed');
  });
}
