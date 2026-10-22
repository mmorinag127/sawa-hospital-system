// Synthetic document/bootstrap tests; deployed cross-tab verification is separate.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(file, window, requireSource = () => ({}), append = '') {
  const exports = {};
  const source = fs.readFileSync(path.join(__dirname, '../src', file), 'utf8') + append;
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, { exports, window, crypto: require('node:crypto').webcrypto, process: { env: {} }, require: requireSource });
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

test('browser business drafts keep the existing Test key and separate every copy', () => {
  const stored = new Map([['business-draft', '{"raw":[0,null," 1 "]}']]);
  const original = stored.get('business-draft');
  for (const dataset of ['test', 'replay-one', 'replay-two']) {
    const api = boundary({});
    api.initializeDocumentDataset({ dataset });
    const key = api.datasetStorageKey('business-draft');
    assert.equal(key, dataset === 'test' ? 'business-draft' : `business-draft:${dataset}`);
    if (dataset === 'test') assert.equal(stored.get(key), original);
    else { assert.equal(stored.get(key), undefined); stored.set(key, dataset); }
    api.initializeDocumentDataset({ dataset: 'replay-other' });
    assert.equal(api.datasetStorageKey('business-draft'), key);
  }
  assert.equal(stored.get('business-draft'), original);
  assert.throws(() => boundary({}).datasetStorageKey('business-draft'), /dataset_unconfirmed/);
});

const shiftPage = path.join(__dirname, '../src/pages/index.tsx');
if (fs.readFileSync(shiftPage, 'utf8').includes('readCreateDraft')) test('actual Shift resume cannot adopt a Test draft in a copy document', () => {
  const original = { period: { startDate: '2026-10-01', endDateExclusive: '2026-10-08' }, createStep: 3, wishStaffId: '7', wishDate: '2026-10-02', addingWish: true };
  const values = new Map([['sawa.shift.create-draft', JSON.stringify(original)]]);
  const browser = { sessionStorage: { getItem: key => values.get(key) ?? null } };
  for (const dataset of ['test', 'replay-copy']) {
    const api = boundary(browser); api.initializeDocumentDataset({ dataset });
    const page = load('pages/index.tsx', browser, name => name === '../services/stagingDataset' ? api : {}, '\nexport { readCreateDraft };');
    assert.equal(JSON.stringify(page.readCreateDraft()), dataset === 'test' ? JSON.stringify(original) : 'null');
    if (dataset !== 'test') {
      values.set(api.datasetStorageKey('sawa.shift.create-draft'), JSON.stringify({ ...original, wishStaffId: '8' }));
      assert.equal(page.readCreateDraft().wishStaffId, '8');
    }
  }
  assert.equal(values.get('sawa.shift.create-draft'), JSON.stringify(original));
});

if (fs.existsSync(path.join(__dirname, '../src/services/catalogDraft.ts'))) test('actual School catalog retain/recover/clear never changes the other dataset draft', () => {
  const stored = new Map();
  const browser = { sessionStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) } };
  const catalog = dataset => {
    const api = boundary(browser); api.initializeDocumentDataset({ dataset });
    return load('services/catalogDraft.ts', browser, name => name === './stagingDataset' ? api : { currentCatalogOwner: () => 'same-operator' });
  };
  const testDraft = catalog('test'), copyDraft = catalog('replay-copy');
  const raw = { name: ' original ', document: { quantities: [0, null, ' 1 '] } };
  const originalId = testDraft.retainCatalogDraft(null, raw);
  const originalBytes = stored.get('sawa-catalog-recovery');
  assert.equal(copyDraft.recoverCatalogDraft(), null);
  copyDraft.clearCatalogDraft();
  assert.equal(stored.get('sawa-catalog-recovery'), originalBytes);
  const copyId = copyDraft.retainCatalogDraft(null, { ...raw, name: ' copy ' });
  assert.equal(copyDraft.recoverCatalogDraft().id, copyId);
  copyDraft.clearCatalogDraft(copyId);
  assert.equal(copyDraft.recoverCatalogDraft(), null);
  assert.equal(testDraft.recoverCatalogDraft().id, originalId);
  assert.equal(stored.get('sawa-catalog-recovery'), originalBytes);
});
