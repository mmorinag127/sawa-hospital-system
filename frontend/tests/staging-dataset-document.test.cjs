// Synthetic document/bootstrap tests; deployed cross-tab verification is separate.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(file, window, requireSource = () => ({})) {
  const exports = {};
  const source = fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, { exports, window, process: { env: {} }, require: requireSource });
  return exports;
}
const boundary = window => load('services/stagingDataset.ts', window);

test('reload pins the cookie before choices bootstrap despite stale per-tab storage', () => {
  const browser = { sessionStorage: { getItem: () => 'test' } };
  const current = boundary(browser);
  current.initializeDocumentDataset(current.datasetFromCookie('sawa_stg_dataset=replay-copy'));
  assert.equal(current.datasetHeaders()['X-Sawa-Dataset'], 'replay-copy');
  assert.equal(current.documentDatasetMatches('replay-copy'), true);
  assert.equal(current.documentDatasetMatches('test'), false);
  current.initializeDocumentDataset({ dataset: 'test' });
  assert.equal(current.datasetHeaders()['X-Sawa-Dataset'], 'replay-copy');
  const cookies = { sawa_stg_dataset: 'test' };
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    assert.throws(() => current.forwardDatasetHeaders({ 'x-sawa-dataset': current.datasetHeaders()['X-Sawa-Dataset'] }, cookies, method), /dataset_changed/);
  }
  // Pre-switch readiness must still inspect the explicitly requested copy.
  assert.equal(current.forwardDatasetHeaders({ 'x-sawa-dataset': 'replay-copy' }, cookies, 'GET')['X-Sawa-Dataset'], 'replay-copy');
});

test('unconfirmed or malformed selection never silently sends the test dataset', () => {
  const cases = [undefined, '', 'prod', 'replay-../../prod', '%ZZ', 'postgresql%3A%2F%2Fprod%2Fdb'];
  for (const value of cases) {
    const api = boundary({});
    if (value !== undefined) api.initializeDocumentDataset(api.datasetFromCookie(`sawa_stg_dataset=${value}`));
    assert.throws(() => api.datasetHeaders(), /dataset_unconfirmed/);
  }
  const duplicate = boundary({});
  duplicate.initializeDocumentDataset(duplicate.datasetFromCookie('sawa_stg_dataset=test; sawa_stg_dataset=replay-copy'));
  assert.throws(() => duplicate.datasetHeaders(), /dataset_unconfirmed/);
});

test('server document props use each request cookie without sharing client state', async () => {
  const api = boundary(undefined);
  const app = load('pages/_app.tsx', undefined, name => name === 'next/app'
    ? { __esModule: true, default: { getInitialProps: async () => ({ pageProps: { preserved: true } }) } }
    : name === '../services/stagingDataset' ? api : {} ).default;
  for (const [cookie, expected] of [['sawa_stg_dataset=replay-one', 'replay-one'], [undefined, 'test'], ['sawa_stg_dataset=replay-two', 'replay-two']]) {
    const headers = {};
    const props = await app.getInitialProps({ ctx: { req: { headers: { cookie } }, res: { setHeader: (key, value) => { headers[key] = value; } } } });
    assert.equal(props.initialDataset.dataset, expected);
    assert.equal(props.pageProps.preserved, true);
    assert.equal(headers['Cache-Control'], 'private, no-store');
    api.initializeDocumentDataset(props.initialDataset);
    assert.equal(Object.keys(api.datasetHeaders()).length, 0);
  }
  const navigation = await app.getInitialProps({ ctx: {} });
  assert.equal(navigation.initialDataset, undefined);
  const source = fs.readFileSync(path.join(__dirname, '../src/pages/_app.tsx'), 'utf8');
  const ast = ts.createSourceFile('_app.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const component = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
  assert.equal(component.body.statements[0].expression.expression.getText(ast), 'initializeDocumentDataset');
});
