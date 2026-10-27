import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(import.meta.url);

async function mountPage(name, apiClient, run) {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/' });
  const saved = {};
  for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Event', 'MouseEvent', 'IS_REACT_ACT_ENVIRONMENT']) {
    saved[key] = Object.getOwnPropertyDescriptor(global, key);
    Object.defineProperty(global, key, { configurable: true, writable: true,
      value: key === 'IS_REACT_ACT_ENVIRONMENT' ? true : dom.window[key] });
  }
  const React = require('react'), { act } = React, ts = require('typescript');
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const load = file => {
    const exports = {};
    const source = readFileSync(new URL(`../../src/${file}`, import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, { compilerOptions: {
      target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS,
    } }).outputText;
    vm.compileFunction(compiled, ['exports', 'require', 'React'])(exports, name => {
      if (name === 'react') return React;
      if (name === '@mui/material') return { Button: ({ children, ...props }) => React.createElement('button', props, children) };
      if (name === '@mui/material/styles') return { ThemeProvider: ({ children }) => children };
      if (name === '@sawa/ui') return { sawaTheme: {} };
      if (name === 'next/link') return { default: ({ children, ...props }) => React.createElement('a', props, children) };
      if (name === '../components/TopNav') return { default: () => null };
      if (name === '../components/OcrOperations') return load('components/OcrOperations.tsx');
      if (name === '../services/dashboardMenuInfo') return load('services/dashboardMenuInfo.ts');
      if (name === '../services/facilityData') return {
        fetchFacilityNameMap: async () => ({}),
        fetchOrderFacilityCandidates: async () => { throw new Error('unexpected fallback read'); },
        pickBestFacilityCandidate: () => null,
      };
      if (name === '../services/apiClient') return { apiClient };
      throw new Error('Unexpected import ' + name);
    }, React);
    return exports;
  };
  try {
    await act(async () => root.render(React.createElement(load(`pages/${name}.tsx`).default)));
    await run({ act, dom, unmount: async () => { await act(async () => root.unmount()); } });
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(global, key, descriptor);
      else delete global[key];
    }
  }
}

test('process log reads summaries only; explicit raw download never parses or renders details and aborts stale reads', async () => {
  const calls = [], downloads = [], revoked = [], clicked = [];
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL;
  URL.createObjectURL = blob => { downloads.push(blob); return 'blob:original-log'; };
  URL.revokeObjectURL = url => revoked.push(url);
  const apiClient = { get: async (path, options) => {
    calls.push({ path, options });
    if (path === '/system/process-logs') {
      assert.equal(options.params.include_details, false);
      return { data: { items: ['one', 'two'].map(id => ({ id, source: 'ocr', process_type: 'ocr', title: id })) } };
    }
    return new Promise((resolve, reject) => calls.at(-1).finish = { resolve, reject });
  } };
  try {
    await mountPage('system-process-logs', apiClient, async ({ act, dom, unmount }) => {
      dom.window.HTMLAnchorElement.prototype.click = function () { clicked.push(this.download); };
      const button = label => [...document.querySelectorAll('button')].find(node => node.textContent === label);
      assert.equal(calls.length, 1);
      assert.equal(document.querySelector('pre'), null);
      await act(async () => button('元データをダウンロード').click());
      assert.equal(calls[1].options.responseType, 'blob');
      await act(async () => [...document.querySelectorAll('.log-row')][1].click());
      assert.equal(calls[1].options.signal.aborted, true);
      await act(async () => calls[1].finish.resolve({ data: new Blob(['stale']) }));
      assert.equal(downloads.length, 0);
      await act(async () => button('元データをダウンロード').click());
      const original = new Blob(['{"details":"original-unmodified"}'], { type: 'application/json' });
      await act(async () => calls[2].finish.resolve({ data: original }));
      await new Promise(resolve => dom.window.setTimeout(resolve, 5));
      assert.deepEqual(downloads, [original]);
      assert.deepEqual(clicked, ['process-log.json']);
      assert.deepEqual(revoked, ['blob:original-log']);
      assert.doesNotMatch(document.body.textContent, /original-unmodified|stale/);
      assert.equal(document.querySelector('a[download]'), null);
      await act(async () => button('元データをダウンロード').click());
      await act(async () => calls[3].finish.reject(new Error('request failed')));
      assert.match(document.querySelector('[role="alert"]').textContent, /失敗/);
      await act(async () => button('元データをダウンロード').click());
      await unmount();
      assert.equal(calls[4].options.signal.aborted, true);
    });
  } finally {
    URL.createObjectURL = oldCreate;
    URL.revokeObjectURL = oldRevoke;
  }
});

test('dashboard pending data is unknown, not zero or an OCR error; resolved empty data remains a valid zero', async () => {
  const calls = [];
  const apiClient = { get: async (path, options) => {
    const call = { path, options }; calls.push(call);
    if (path === '/shipping/status/today') return { data: { summary: { total: 0, delivered: 0, pending: 0, errors: 0, all_delivered: false }, items: [] } };
    return new Promise((resolve, reject) => call.finish = { resolve, reject });
  } };
  await mountPage('hospital-dashboard', apiClient, async ({ act, unmount }) => {
    assert.ok([...document.querySelectorAll('.summary-value')].every(node => node.textContent === '未取得'));
    assert.match(document.querySelector('[role="status"]').textContent, /OCR状態を取得中/);
    assert.equal(document.querySelector('[role="alert"]'), null);
    assert.doesNotMatch(document.body.textContent, /未確定申請はありません/);
    await act(async () => calls.find(call => call.path === '/orders/dashboard').finish.resolve({ data: {
      stats: { counts: { '未着': 0, '要確認': 0, '確定': 0, 'エラー': 0 }, unresolved: 0, total: 0, latestReceived: null },
      weeks: [], week_ids: [], pending_orders: [],
    } }));
    assert.ok([...document.querySelectorAll('.summary-value')].every(node => node.textContent === '0'));
    assert.match(document.body.textContent, /未確定申請はありません/);
    const status = calls.find(call => call.path === '/system/status');
    assert.equal(status.options.params.view, 'operations');
    await act(async () => status.finish.reject(new Error('operations unavailable')));
    assert.equal(document.querySelector('[role="status"]'), null);
    assert.match(document.querySelector('[role="alert"]').textContent, /未取得|取得でき/);
    await unmount();
    assert.equal(status.options.signal.aborted, true);
    assert.equal(calls.find(call => call.path === '/orders/dashboard').options.signal.aborted, true);
  });
});
