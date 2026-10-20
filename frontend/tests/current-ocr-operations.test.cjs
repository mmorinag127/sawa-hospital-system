const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {JSDOM} = require('jsdom');
const {createRoot} = require('react-dom/client');
const {act} = React;
const root = path.join(__dirname, '..');
const payload = () => ({source: 'recorded_sql_workflow/v1', observed_at: '2026-10-19T00:00:00Z',
  summary: {status: 'review_required', total: 1, counts: {review_required: 1}},
  items: [{order_id: 'own-order', document_id: 'own-doc', job_id: 'own-job', kind: 'native_ingest_v2',
    execution_id: 'own-execution', generation: 'own-generation', status: 'review_required', blockers: []}],
  history: [{job_id: 'old-job', order_id: 'own-order', source_status: 'failed', updated_at: '2026-10-15', error: 'old-owner-error'}],
  external_history: {current: false, source: 'legacy_external_pipeline', recorded_at: '2026-10-15', observation: {status: 'running', last_error: 'old-owner-error'}}});

function compile(relative, overrides = {}) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  const output = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020}}).outputText;
  const exports = {};
  const load = name => name in overrides ? overrides[name] : name === 'next/link'
    ? {__esModule: true, default: ({children, ...props}) => React.createElement('a', props, children)} : require(name);
  vm.runInNewContext(output, {exports, require: load, console, setTimeout, clearTimeout});
  return exports;
}
const shared = compile('src/components/OcrOperations.tsx');

test('shared current projection never adopts historical running/error and review never says success', () => {
  const html = renderToStaticMarkup(React.createElement(shared.OcrOperationsSummary, {operations: payload()}));
  assert.match(html, /認識結果の確認が必要/);
  assert.doesNotMatch(html, /old-owner-error|old-job|認識完了|実行中/);
  const empty = payload();empty.summary = {status: 'idle', total: 0, counts: {}};
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(shared.OcrOperationsSummary, {operations: empty})), /認識完了/);
  assert.match(renderToStaticMarkup(React.createElement(shared.OcrOperationsSummary, {operations: null})), /role="alert"/);
});

test('all historical records have a separate dated disclosure and current table retains owner/blocker', () => {
  const data = payload();data.items[0].status = 'blocked';data.items[0].blockers = ['ocr_native_generation_changed'];
  const html = renderToStaticMarkup(React.createElement(shared.OcrOperationsHistory, {operations: data}));
  assert.match(html, /過去のOCR記録/);assert.match(html, /2026-10-15/);assert.match(html, /old-owner-error/);
  assert.doesNotMatch(html, /<details open/);
  const table = renderToStaticMarkup(React.createElement(shared.OcrOperationsTable, {items: data.items}));
  for (const value of ['own-job', 'own-doc', 'own-execution', 'own-generation', 'ocr_native_generation_changed']) assert.ok(table.includes(value));
  assert.match(table, /\/orders\/own-order\/workflow-v2/);
});

async function queue(run, api) {
  const dom = new JSDOM('<div id="root"></div>', {url: 'https://fixture.invalid/ocr-queue'});
  global.window = dom.window;global.document = dom.window.document;global.navigator = dom.window.navigator;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const tag = name => ({children, ...props}) => React.createElement(name, props, children);
  const page = compile('src/pages/ocr-queue.tsx', {
    '@mui/material': {Button: tag('button'), NativeSelect: tag('select')},
    '@mui/material/styles': {ThemeProvider: ({children}) => children}, '@sawa/ui': {sawaTheme: {}},
    '../components/TopNav': {__esModule: true, default: () => null},
    '../components/OcrOperations': shared, '../services/apiClient': {apiClient: api},
  }).default;
  const container = document.getElementById('root'), mounted = createRoot(container);
  try {
    await act(async () => {mounted.render(React.createElement(page));});
    await run(container);
  } finally {
    await act(async () => mounted.unmount());dom.window.close();
    delete global.window;delete global.document;delete global.navigator;delete global.IS_REACT_ACT_ENVIRONMENT;
  }
}

test('actual queue requests intake and shared system status; no Firestore or retired template control', async () => {
  const calls = [];
  await queue(async container => {
    assert.deepEqual(calls, ['/ingest/jobs', '/system/status']);
    assert.match(container.textContent, /認識結果の確認が必要/);
    assert.match(container.textContent, /取込ジョブ/);
    assert.match(container.textContent, /backlog: 3/);
    assert.doesNotMatch(container.textContent, /テンプレートID|未分類ジョブ/);
    assert.ok(!container.querySelector('input'));
  }, {get: async url => {calls.push(url);return {data: url === '/ingest/jobs' ? {items: []} : {ocr_operations: payload(), uploaded_pdfs: {eligible_backlog_count: 3}, ingest_jobs: {}}};}});
});

test('actual recover-ready remains explicit and zero acceptance is not recognition success', async () => {
  const calls = [];
  await queue(async container => {
    const button = [...container.querySelectorAll('button')].find(node => node.textContent === '滞留をまとめて再試行');
    await act(async () => {button.click();button.click();});
    assert.deepEqual(calls, ['/ingest/recover-ready']);
    assert.match(container.textContent, /まとめて再試行を開始/);
    assert.match(container.textContent, /認識結果の確認が必要/);
    assert.doesNotMatch(container.textContent, /認識完了/);
    assert.match(container.textContent, /ocr=0/);
  }, {get: async url => ({data: url === '/ingest/jobs' ? {items: []} : {ocr_operations: payload()}}), post: async url => {calls.push(url);return {data: {accepted: true, ingest_enqueued: 0, uploaded_enqueued: 0, ocr_recovered: 0}};}});
});

test('queue request failure is visible, not an empty successful queue', async () => {
  await queue(async container => {
    assert.match(container.textContent, /取得に失敗/);
    assert.doesNotMatch(container.textContent, /この状態の現在ジョブはありません/);
  }, {get: async () => {throw new Error('authored failure');}});
});

test('a failed status reload after accepted recovery cannot hide the read failure', async () => {
  let reads = 0;
  await queue(async container => {
    const button = [...container.querySelectorAll('button')].find(node => node.textContent === '滞留をまとめて再試行');
    await act(async () => {button.click();});
    assert.match(container.textContent, /取得に失敗/);
    assert.doesNotMatch(container.textContent, /認識完了とは別|この状態の現在ジョブはありません/);
  }, {get: async url => {if (url === '/ingest/jobs') return {data: {items: []}};if (++reads > 1) throw new Error('authored reload failure');return {data: {ocr_operations: payload()}};},
    post: async () => ({data: {accepted: true, ingest_enqueued: 0, uploaded_enqueued: 0, ocr_recovered: 0}})});
});

test('all three source consumers render the identical current SQL component', () => {
  for (const file of ['ocr-queue.tsx', 'hospital-dashboard.tsx', 'system-status.tsx']) {
    const source = fs.readFileSync(path.join(root, 'src/pages', file), 'utf8');
    assert.match(source, /<OcrOperationsSummary operations=/);
    assert.match(source, /\.ocr_operations/);
    assert.doesNotMatch(source, /\?\.ocr_pipeline\?/);
    assert.doesNotMatch(source, /\/ocr\/unclassified/);
  }
});
