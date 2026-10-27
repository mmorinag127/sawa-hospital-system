import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require = createRequire(import.meta.url);

test('status renders configuration before operations and fetches costly sections only on demand', async () => {
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
  const calls = [];
  let releaseOperations, failQuality = false;
  const configuration = { intake: { mode: 'manual_upload', manual_upload_storage: { mode: 'gcs', persisted: true, configured: true } },
    oauth_config: { configured: true, google_client_id_count: 2 } };
  const quality = { gate: { status: 'pass' }, sample: { evaluated_jobs: 3 } };
  const apiClient = { get: async (path, options) => {
    const view = options?.params?.view;
    calls.push([path, view]);
    if (path === '/system/db/quota') return { data: { resource: 'test-db', used: 7, limit: 10 } };
    assert.equal(path, '/system/status');
    if (view === 'configuration') return { data: configuration };
    if (view === 'operations-summary') return new Promise(resolve => { releaseOperations = resolve; });
    if (view === 'quality') {
      if (failQuality) throw new Error('authored-quality-failure');
      return { data: { ...configuration, ocr_reparse_quality: quality } };
    }
    assert.equal(view, 'full');
    return { data: { ...configuration, ocr_reparse_quality: quality } };
  } };
  const source = readFileSync(new URL('../../src/pages/system-status.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020,
    jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  vm.compileFunction(compiled, ['exports', 'require', 'React'])(exports, name => {
    if (name === 'react') return React;
    if (name === '@mui/material') return { Button: ({ children, ...props }) => React.createElement('button', props, children),
      Input: props => React.createElement('input', props) };
    if (name === '@mui/material/styles') return { ThemeProvider: ({ children }) => children };
    if (name === '@sawa/ui') return { sawaTheme: {} };
    if (name === 'next/link') return { default: ({ children, ...props }) => React.createElement('a', props, children) };
    if (name === '../components/TopNav') return { default: () => null };
    if (name === '../components/OcrOperations') {
      const actual = {};
      const component = readFileSync(new URL('../../src/components/OcrOperations.tsx', import.meta.url), 'utf8');
      const output = ts.transpileModule(component, { compilerOptions: {
        jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS,
      } }).outputText;
      vm.compileFunction(output, ['exports', 'require', 'React'])(actual, name => {
        if (name === 'react') return React;
        if (name === '../services/apiClient') return {apiClient};
        if (name === '../services/browserSession') return {browserSessionKey: () => 'fixture-session', subscribeBrowserSession: () => () => {}};
        assert.equal(name, 'next/link');
        return { default: ({ children, ...props }) => React.createElement('a', props, children) };
      }, React);
      return actual;
    }
    if (name === '../hooks/useCurrentUserRole') return { useCurrentUserRole: () => ({ isAdmin: false }) };
    if (name === '../services/apiClient') return { apiClient };
    throw new Error('Unexpected import ' + name);
  }, React);
  const button = label => [...document.querySelectorAll('button')].find(node => node.textContent === label);
  try {
    await act(async () => root.render(React.createElement(exports.default)));
    assert.deepEqual(calls, [['/system/status', 'configuration'], ['/system/status', 'operations-summary']]);
    assert.match(document.body.textContent, /設定数: 2/);
    assert.match(document.querySelector('[role="status"]').textContent, /OCR状態を取得中/);
    assert.equal(document.querySelector('[role="alert"]'), null);
    assert.equal(document.querySelector('.ocr-backlog-badge').textContent, '未取得');
    assert.equal(button('品質を取得').disabled, true);
    await act(async () => releaseOperations({ data: {
      ocr_operations: { source: 'recorded_sql_workflow_display/v1', validation_state: 'references_only', observed_at: '2026-10-27',
        summary: { status: 'idle', total: 0, counts: {} }, items: [], history: [] },
      uploaded_pdfs: { pending_count: 0 }, ingest_jobs: { pending_count: 0 },
    } }));
    assert.equal(document.querySelector('[role="status"]'), null);
    assert.match(document.querySelector('[aria-label="現行SQL OCR状態"]').textContent, /現在のOCRジョブなし/);
    assert.match(document.querySelector('[aria-label="現行SQL OCR状態"]').textContent, /原本・実行入力の整合性: 未検証/);
    assert.equal(document.querySelector('.ocr-backlog-badge').textContent, '正常');
    assert.match(document.querySelector('.quality-table').textContent, /未取得/);
    await act(async () => button('使用量を取得').click());
    assert.match(document.body.textContent, /test-db/);
    await act(async () => button('品質を取得').click());
    assert.match(document.body.textContent, /評価ジョブ: 3/);
    failQuality = true;
    await act(async () => button('品質を取得').click());
    assert.match(document.body.textContent, /詳細取得に失敗/);
    assert.match(document.body.textContent, /評価ジョブ: 未取得/);
    assert.match(document.body.textContent, /test-db/);
    assert.equal(calls.filter(([, view]) => view === 'full').length, 0);
    await act(async () => button('外部履歴を含む全詳細を取得').click());
    assert.equal(calls.at(-1)[1], 'full');
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(global, key, descriptor);
      else delete global[key];
    }
  }
});
