import { createHash } from 'node:crypto';

const names = new Set(['Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'SecurityError', 'AbortError', 'NetworkError']);
const staticJs = /^\/_next\/static\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.js$/;
const manual = /^\/manuals\/current-stg-20260519\/[A-Za-z0-9_-]+\.(?:pdf|md)$/;
export const PAGEERROR_DIAGNOSTIC_LIMIT = 100;

export function diagnosticPath(value, origin, businessPath) {
  try {
    const url = new URL(value);
    if (url.origin !== origin || url.username || url.password) return null;
    return businessPath(url.pathname) || staticJs.test(url.pathname) || manual.test(url.pathname) ? url.pathname : null;
  } catch { return null; }
}

export function sanitizePageError(error, origin) {
  const message = typeof error?.message === 'string' ? error.message : '';
  const name = names.has(error?.name) ? error.name : 'unknown';
  const react = message.match(/^Minified React error #(\d+);/);
  const classification = react ? 'react-minified' : message === 'Load failed' ? 'load-failed'
    : name === 'SecurityError' ? 'security-error' : 'unknown';
  const stack = [];
  if (classification !== 'unknown' && typeof error?.stack === 'string') {
    for (const line of error.stack.split('\n').slice(1)) {
      const match = line.match(/(https?:\/\/[^\s)]+):(\d+):(\d+)\)?$/);
      if (!match) continue;
      try {
        const url = new URL(match[1]);
        if (url.origin !== origin || url.username || url.password || !staticJs.test(url.pathname)) continue;
        stack.push({ file: url.pathname.split('/').at(-1), line: match[2], column: match[3] });
      } catch { /* Unrecognized frames are not retained. */ }
      if (stack.length === 20) break;
    }
  }
  return { name, classification, ...(react ? { reactCode: react[1] } : {}), stack,
    messageHash: createHash('sha256').update(message).digest('hex') };
}

export function attachPageErrorDiagnostics(context, result, { origin, phases, businessPath, now = () => new Date().toISOString() }) {
  result.pageErrorDiagnostics = [];
  result.pageErrorDiagnosticsDropped = 0;
  let pageNumber = 0;
  context.on('page', page => {
    const number = ++pageNumber;
    page.on('pageerror', error => {
      result.pageErrorCount++;
      if (result.pageErrorDiagnostics.length >= PAGEERROR_DIAGNOSTIC_LIMIT) {
        result.pageErrorDiagnosticsDropped++;
        return;
      }
      result.pageErrorDiagnostics.push({ at: now(), phase: phases.has(result.phase) ? result.phase : 'unknown',
        page: number, pathname: diagnosticPath(page.url(), origin, businessPath), ...sanitizePageError(error, origin) });
    });
  });
}
