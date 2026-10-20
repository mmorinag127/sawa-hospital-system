// Actual page/RHF/MUI with deferred HTTP only. Synthetic files are not live OCR evidence.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function mount(config = {}) {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/pdf-upload', pretendToBeVisual: true });
  dom.window.confirm = () => true;
  const saved = new Map();
  for (const key of ['window', 'document', 'navigator', 'Node', 'Element', 'HTMLElement', 'HTMLInputElement',
    'HTMLSelectElement', 'DocumentFragment', 'MutationObserver', 'Event', 'MouseEvent', 'File', 'FormData',
    'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'IS_REACT_ACT_ENVIRONMENT']) {
    saved.set(key, Object.getOwnPropertyDescriptor(global, key));
    const value = key === 'IS_REACT_ACT_ENVIRONMENT' ? true
      : ['getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame'].includes(key)
        ? dom.window[key].bind(dom.window) : dom.window[key];
    Object.defineProperty(global, key, { configurable: true, writable: true, value });
  }
  const React = require('react'), { act } = React;
  const shared = await import('@sawa/ui');
  const material = await import('@mui/material');
  const form = await import('react-hook-form');
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const source = path.resolve(__dirname, '../src');
  const cache = new Map(), events = new Map();
  const reads = [], posts = [], resolutions = [], saves = [];
  const facilityReads = [], listReads = [], retries = [];
  let uploadForm;
  const router = { asPath: '/pdf-upload', events: {
    on(name, fn) { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); },
    off(name, fn) { events.get(name)?.delete(fn); },
  } };
  const api = {
    get(url, options) {
      reads.push({ url, options });
      if (url === '/facilities') {
        const reply = deferred(); facilityReads.push(reply);
        if (!config.deferFacilities) reply.resolve({ data: { facilities: [{ id: 'F', name: 'Component F' }, { id: 'G', name: 'Component G' }] } });
        return reply.promise;
      }
      if (url === '/ingest/uploads') {
        const reply = deferred(); listReads.push(reply);
        if (!config.deferLists) reply.resolve({ data: { items: [] } });
        return reply.promise;
      }
      assert.match(url, /^\/facilities\/[FG]\/resolved$/);
      const reply = { url, date: options.params.effective_date, ...deferred() };
      resolutions.push(reply);
      return reply.promise;
    },
    post(url, data, options) {
      if (/^\/ingest\/uploads\/[^/]+\/retry$/.test(url)) {
        const reply = { url, data, options, ...deferred() }; retries.push(reply); return reply.promise;
      }
      assert.equal(url, '/ingest/upload');
      posts.push({ url, data, options, entries: [...data.entries()] });
      const reply = deferred(); saves.push(reply); return reply.promise;
    },
  };
  function loadFile(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    const module = { exports: {} }; cache.set(filename, module);
    const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
      target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    function load(name) {
      if (name === '@sawa/ui') return shared;
      if (name === '@mui/material') return material;
      if (name === 'react-hook-form') return { ...form, useForm(...args) {
        const result = form.useForm(...args); uploadForm = result; return result;
      } };
      if (name === 'next/router') return { useRouter: () => router };
      if (name === 'next/link') return { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) };
      if (name.endsWith('/components/TopNav')) return { __esModule: true, default: () => null };
      if (name.startsWith('.')) {
        const target = path.resolve(path.dirname(filename), name);
        const file = ['.ts', '.tsx'].map(ext => target + ext).find(fs.existsSync);
        if (file) {
          const exported = loadFile(file);
          return file === path.join(source, 'services/apiClient.ts') ? { ...exported, apiClient: api } : exported;
        }
      }
      return require(name);
    }
    vm.compileFunction(code, ['require', 'module', 'exports'], { filename })(load, module, module.exports);
    return module.exports;
  }
  let unmounted = false;
  let Page;
  const auth = loadFile(path.join(source, 'services/apiClient.ts'));
  auth.setBearerToken('synthetic-component-session');
  try {
    Page = loadFile(path.join(source, 'pages/pdf-upload.tsx')).default;
    await act(async () => root.render(React.createElement(Page)));
  } catch (err) { await close(); throw err; }
  async function unmount() {
    if (!unmounted) { await act(async () => root.unmount()); unmounted = true; }
  }
  async function close() {
    try { await unmount(); }
    finally {
      dom.window.close();
      for (const [key, descriptor] of saved) {
        if (descriptor) Object.defineProperty(global, key, descriptor); else delete global[key];
      }
    }
  }
  async function change(input, value) {
    assert.ok(input);
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }
  async function select(name, value) {
    const trigger = document.querySelector(`input[name="${name}"]`).parentElement.querySelector('[role="combobox"]');
    await act(async () => trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    const option = [...document.querySelectorAll('[role="option"]')].find(node => node.dataset.value === value);
    assert.ok(option, value);
    await act(async () => option.click());
  }
  async function clickLabel(text) {
    const label = [...document.querySelectorAll('label')].find(node => node.textContent.includes(text));
    assert.ok(label, text);
    await act(async () => label.querySelector('input').click());
  }
  async function files(names) {
    const input = document.querySelector('input[type="file"]');
    const selected = names.map(name => new File(['component-only-not-a-real-PDF'], name, { type: 'application/pdf' }));
    Object.defineProperty(input, 'files', { configurable: true, value: selected });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
    return selected;
  }
  async function settle(action) { await act(async () => { action(); await Promise.resolve(); }); }
  async function submit(times = 1) {
    await act(async () => {
      for (let i = 0; i < times; i++) document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
  }
  async function ready(names = ['A.bin']) {
    await change(document.querySelector('input[type="month"]'), '2026-01');
    await select('facility_hint', 'F');
    await select('week_hint', '2026-01@2026-01-01~2026-01-03');
    await change(document.querySelector('input[name="effective_date"]'), '2026-01-02');
    const selected = await files(names);
    const request = resolutions.at(-1);
    await settle(() => request.resolve({ data: { source: 'canonical_json', facility_id: 'F', effective_date: request.date,
      revision: 'component-revision', version: { version_id: 'component-version' } } }));
    await clickLabel('この施設版を使用する');
    return selected;
  }
  async function confirmFreshPreview() {
    const request = resolutions.at(-1);
    await settle(() => request.resolve({ data: { source: 'canonical_json', facility_id: 'F', effective_date: request.date,
      revision: 'fresh-revision', version: { version_id: 'fresh-version' } } }));
    await clickLabel('この施設版を使用する');
  }
  async function button(text) {
    const node = [...document.querySelectorAll('button')].find(node => node.textContent.includes(text));
    assert.ok(node, text);
    await act(async () => node.click());
  }
  async function filter(status) {
    const select = document.querySelector('select');
    assert.ok(select);
    await settle(() => {
      Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set.call(select, status);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }
  return { close, unmount, change, select, clickLabel, files, ready, submit, settle, reads, posts, resolutions, saves,
    facilityReads, listReads, retries, button, confirmFreshPreview, filter,
    document: dom.window.document, text: () => dom.window.document.body.textContent,
    getForm: () => uploadForm,
    async session() { await settle(() => auth.setBearerToken('next-synthetic-component-session')); },
    async leave() { await settle(() => events.get('routeChangeStart')?.forEach(fn => fn('/orders'))); },
    async route(asPath) {
      await act(async () => {
        events.get('routeChangeStart')?.forEach(fn => fn(asPath));
        router.asPath = asPath;
        root.render(React.createElement(Page));
      });
    },
  };
}

test('actual RHF advanced values, canonical receipt and normal FormData are sent exactly', async () => {
  const ui = await mount();
  try {
    const [file] = await ui.ready();
    await ui.change(ui.document.querySelector('input[placeholder="例: 大和なでしこ"]'), ' 0 ');
    await ui.change(ui.document.querySelector('input[type="datetime-local"]'), '2026-01-02T09:30');
    await ui.clickLabel('同じPDFでも再登録する');
    await ui.clickLabel('OCRをあとで実行する');
    await ui.submit(2);
    assert.equal(ui.posts.length, 1);
    const post = ui.posts[0], fields = Object.fromEntries(post.entries);
    assert.equal(fields.pdf_file, file);
    assert.deepEqual({ ...fields, pdf_file: file.name }, { pdf_file: 'A.bin', facility_hint: 'F', effective_date: '2026-01-02',
      expected_canonical_revision: 'component-revision', selected_version_id: 'component-version',
      week_hint: '2026-01@2026-01-01~2026-01-03', facility_name: '0',
      received_at: new Date('2026-01-02T09:30').toISOString(), force: 'true', skip_ocr: 'true' });
    assert.deepEqual(post.options, { params: { intake_protocol: 'native_ingest_v2' }, headers: { 'Content-Type': 'multipart/form-data' } });
    await ui.settle(() => ui.saves[0].resolve({ data: { duplicate_blocked: true, existing_order_id: 'existing-A' } }));
    assert.ok(ui.text().includes('同じPDFは既に取り込まれています'));
    assert.ok(ui.document.querySelector('a[href="/orders/existing-A/workflow-v2"]'));
    assert.equal(ui.text().includes('選択中: A.bin'), false);
  } finally { await ui.close(); }
});

for (const failed of [false, true]) test(`same-instance query retirement releases only its busy slot and permits new route submit; failed=${failed}`, async () => {
  const ui = await mount();
  try {
    await ui.ready(); await ui.submit();
    const form = ui.getForm(), input = ui.document.querySelector('input[type="file"]');
    await ui.route('/pdf-upload?selection=next');
    assert.equal(ui.getForm(), form);
    assert.equal(ui.document.querySelector('input[type="file"]'), input);
    assert.equal(ui.document.querySelector('form button[type="submit"]').disabled, true);
    await ui.settle(() => failed ? ui.saves[0].reject({ response: { data: { detail: 'old-query-A' } } })
      : ui.saves[0].resolve({ data: { message_id: 'old-query-A', order_id: 'old-order-A' } }));
    // A route change now requires a fresh canonical receipt and explicit confirmation.
    await ui.confirmFreshPreview();
    assert.equal(ui.document.querySelector('form button[type="submit"]').disabled, false);
    assert.equal(ui.text().includes('old-query-A'), false);
    assert.ok(ui.text().includes('選択中: A.bin'));
    assert.equal(ui.reads.filter(read => read.url === '/ingest/uploads').length, 0);
    const [next] = await ui.files(['B.bin']);
    await ui.submit(2);
    assert.equal(ui.posts.length, 2);
    assert.equal(ui.posts[1].data.get('pdf_file'), next);
    await ui.settle(() => ui.saves[1].resolve({ data: { message_id: 'current-query-B' } }));
    assert.ok(ui.text().includes('current-query-B'));
    assert.equal(ui.text().includes('選択中: B.bin'), false);
  } finally { await ui.close(); }
});

for (const failed of [false, true]) for (const order of ['old-first', 'new-first']) test(`query-retired A cannot unlock or clear newer request slot; failed=${failed}, order=${order}`, async () => {
  const ui = await mount();
  try {
    await ui.ready(); await ui.submit();
    await ui.route('/pdf-upload?selection=B');
    await ui.confirmFreshPreview();
    const [next] = await ui.files(['B.bin']);
    // Real form submission exercises the new route while A's HTTP promise is pending.
    await ui.submit(2);
    assert.equal(ui.posts.length, 2);
    assert.equal(ui.posts[1].data.get('pdf_file'), next);
    if (order === 'new-first') {
      await ui.settle(() => ui.saves[1].resolve({ data: { message_id: 'current-B' } }));
      assert.ok(ui.text().includes('current-B'));
      await ui.files(['C.bin']); await ui.submit();
      assert.equal(ui.posts.length, 3);
    }
    await ui.settle(() => failed ? ui.saves[0].reject({ response: { data: { detail: 'obsolete-query-A' } } })
      : ui.saves[0].resolve({ data: { message_id: 'obsolete-query-A' } }));
    assert.equal(ui.text().includes('obsolete-query-A'), false);
    assert.ok(ui.text().includes(`選択中: ${order === 'old-first' ? 'B.bin' : 'C.bin'}`));
    assert.equal(ui.document.querySelector('form button[type="submit"]').disabled, true);
    await ui.submit(); assert.equal(ui.posts.length, order === 'old-first' ? 2 : 3);
    const index = order === 'old-first' ? 1 : 2;
    await ui.settle(() => ui.saves[index].resolve({ data: { message_id: 'latest-owned' } }));
    assert.ok(ui.text().includes('latest-owned'));
    assert.equal(ui.document.querySelector('form button[type="submit"]').disabled, false);
  } finally { await ui.close(); }
});

test('explicitly unchecked flags retain omission semantics and blank received_at remains absent', async () => {
  const ui = await mount();
  try {
    await ui.ready();
    for (const label of ['同じPDFでも再登録する', 'OCRをあとで実行する']) { await ui.clickLabel(label); await ui.clickLabel(label); }
    await ui.submit();
    const fields = Object.fromEntries(ui.posts[0].entries);
    assert.equal(fields.force, undefined); assert.equal(fields.skip_ocr, undefined); assert.equal(fields.received_at, undefined);
    assert.equal(fields.facility_name, 'Component F');
    await ui.settle(() => ui.saves[0].resolve({ data: { existing_order_preview: { order_id: 'order-A' }, order_id: 'order-A' } }));
    assert.ok(ui.text().includes('対象注文と差し替え内容を確認'));
  } finally { await ui.close(); }
});

for (const failure of [false, true]) test(`submitted multi-file snapshot/result names survive new selection; failure=${failure}`, async () => {
  const ui = await mount();
  try {
    const originals = await ui.ready(['A1.bin', 'A2.bin']);
    await ui.submit();
    const [next] = await ui.files(['B.bin']);
    await ui.submit(2);
    assert.equal(ui.posts.length, 1);
    assert.deepEqual(ui.posts[0].data.getAll('pdf_files'), originals);
    await ui.settle(() => failure ? ui.saves[0].reject({ response: { data: { detail: 'source-rejected' } } })
      : ui.saves[0].resolve({ data: { items: [{ message_id: 'A1' }, { message_id: 'A2' }] } }));
    assert.ok(ui.text().includes('選択中: B.bin'));
    assert.deepEqual([...ui.document.querySelectorAll('.result-card-head strong')].map(node => node.textContent), ['A1.bin', 'A2.bin']);
    assert.ok(ui.text().includes(failure ? '失敗' : '2件受付'));
    await ui.submit();
    assert.equal(ui.posts.length, 2);
    assert.equal(ui.posts[1].data.get('pdf_file'), next);
    await ui.settle(() => ui.saves[1].resolve({ data: { order_id: 'order-B' } }));
    assert.equal(ui.text().includes('選択中: B.bin'), false);
  } finally { await ui.close(); }
});

for (const retirement of ['input', 'route', 'unmount', 'session']) for (const failed of [false, true]) test(`retired ${retirement} cannot adopt A or launch its list refresh; failed=${failed}`, async () => {
  const ui = await mount();
  try {
    await ui.ready(); await ui.submit();
    if (retirement === 'input') await ui.change(ui.document.querySelector('input[placeholder="例: 大和なでしこ"]'), 'new input');
    if (retirement === 'route') await ui.leave();
    if (retirement === 'unmount') await ui.unmount();
    if (retirement === 'session') await ui.session();
    await ui.settle(() => failed ? ui.saves[0].reject({ response: { data: { detail: 'retired-A' } } })
      : ui.saves[0].resolve({ data: { message_id: 'retired-A', order_id: 'retired-order-A' } }));
    assert.equal(ui.reads.filter(read => read.url === '/ingest/uploads').length, 0);
    assert.equal(ui.text().includes('retired-A'), false);
    assert.equal(ui.posts.length, 1);
    if (retirement === 'input') {
      assert.ok(ui.text().includes('選択中: A.bin'));
      await ui.submit(); assert.equal(ui.posts.length, 2);
      assert.equal(ui.posts[1].data.get('facility_name'), 'new input');
      await ui.settle(() => ui.saves[1].resolve({ data: { message_id: 'current' } }));
    }
  } finally { await ui.close(); }
});

test('new session submission cannot be completed, cleared or unlocked by old session A', async () => {
  const ui = await mount();
  try {
    await ui.ready(); await ui.submit(); await ui.session();
    await ui.ready(['B.bin']); await ui.submit();
    assert.equal(ui.posts.length, 2);
    await ui.settle(() => ui.saves[0].resolve({ data: { message_id: 'retired-A' } }));
    assert.ok(ui.text().includes('選択中: B.bin'));
    assert.equal(ui.text().includes('retired-A'), false);
    await ui.submit(); assert.equal(ui.posts.length, 2);
    await ui.settle(() => ui.saves[1].resolve({ data: { message_id: 'current-B' } }));
    assert.ok(ui.text().includes('current-B'));
  } finally { await ui.close(); }
});

test('canonical changes retire confirmation; stale/missing/foreign resolution cannot authorize POST', async () => {
  const ui = await mount();
  try {
    await ui.ready();
    await ui.select('facility_hint', 'G');
    const old = ui.resolutions.at(-1);
    await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-01-03');
    const latest = ui.resolutions.at(-1);
    await ui.settle(() => old.resolve({ data: { source: 'canonical_json', facility_id: 'G', effective_date: old.date,
      revision: 'stale', version: { version_id: 'stale' } } }));
    await ui.submit(); assert.equal(ui.posts.length, 0);
    await ui.settle(() => latest.resolve({ data: { source: 'database', facility_id: 'G', effective_date: latest.date,
      revision: null, version: { version_id: '' } } }));
    await ui.submit(); assert.equal(ui.posts.length, 0);
    assert.ok(ui.text().includes('正本施設版を確認できません'));
    await ui.select('week_hint', '2026-01@2026-01-04~2026-01-10');
    await ui.submit(); assert.equal(ui.posts.length, 0);
    assert.ok(ui.text().includes('適用日は対象週の期間内'));
    await ui.change(ui.document.querySelector('input[type="month"]'), '2026-02');
    assert.equal(ui.document.querySelector('input[name="week_hint"]').value, '');
  } finally { await ui.close(); }
});

test('invalid date conversion is an error, releases single-flight/loading and never POSTs', async () => {
  const ui = await mount();
  try {
    await ui.ready();
    await ui.settle(() => ui.getForm().setValue('received_at', 'invalid-date'));
    await ui.submit();
    assert.equal(ui.posts.length, 0);
    assert.ok(ui.text().includes('アップロードに失敗'));
    assert.equal(ui.document.querySelector('form button[type="submit"]').disabled, false);
    await ui.change(ui.document.querySelector('input[type="datetime-local"]'), '2026-01-02T08:00');
    await ui.files(['B.bin']); await ui.submit(); assert.equal(ui.posts.length, 1);
    await ui.settle(() => ui.saves[0].resolve({ data: { message_id: 'valid-B' } }));
  } finally { await ui.close(); }
});

for (const invalid of ['source', 'facility', 'date', 'revision', 'version', 'http']) test(`invalid canonical preview ${invalid} remains a blocker with inputs retained`, async () => {
  const ui = await mount();
  try {
    await ui.ready();
    await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-01-03');
    const request = ui.resolutions.at(-1);
    const data = { source: 'canonical_json', facility_id: 'F', effective_date: request.date,
      revision: 'current-revision', version: { version_id: 'current-version' } };
    if (invalid === 'source') data.source = 'database';
    if (invalid === 'facility') data.facility_id = 'G';
    if (invalid === 'date') data.effective_date = '2026-01-02';
    if (invalid === 'revision') data.revision = null;
    if (invalid === 'version') data.version.version_id = '';
    await ui.settle(() => invalid === 'http' ? request.reject({ response: { status: 503 } }) : request.resolve({ data }));
    await ui.submit(); assert.equal(ui.posts.length, 0);
    assert.equal(ui.document.querySelector('input[name="effective_date"]').value, '2026-01-03');
    assert.ok(ui.text().includes('選択中: A.bin'));
    assert.equal(ui.document.querySelector('form button[type="submit"]').disabled, true);
  } finally { await ui.close(); }
});

for (const boundary of ['route', 'session']) for (const failed of [false, true]) {
  test(`facility reads recover across ${boundary}, obsolete A/B cannot unlock C; failed=${failed}`, async () => {
    const ui = await mount({ deferFacilities: true });
    try {
      assert.equal(ui.facilityReads.length, 1);
      if (boundary === 'route') await ui.route('/pdf-upload?read=B'); else await ui.session();
      assert.equal(ui.facilityReads.length, 2);
      await ui.route('/pdf-upload?read=C');
      assert.equal(ui.facilityReads.length, 3);
      for (const old of ui.facilityReads.slice(0, 2)) await ui.settle(() => failed
        ? old.reject(new Error('obsolete-facility'))
        : old.resolve({ data: { facilities: [{ id: 'OLD', name: 'obsolete-facility' }] } }));
      const field = ui.document.querySelector('input[name="facility_hint"]');
      assert.equal(field.parentElement.querySelector('[role="combobox"]').getAttribute('aria-disabled'), 'true');
      assert.equal(ui.text().includes('obsolete-facility'), false);
      await ui.settle(() => ui.facilityReads[2].resolve({ data: { facilities: [{ id: 'F', name: 'fresh-facility' }] } }));
      assert.notEqual(field.parentElement.querySelector('[role="combobox"]').getAttribute('aria-disabled'), 'true');
      await ui.select('facility_hint', 'F');
      assert.equal(ui.document.querySelector('input[name="facility_hint"]').value, 'F');
      assert.ok(ui.text().includes('fresh-facility'));
    } finally { await ui.close(); }
  });

  test(`requested list refetches across ${boundary}, A cannot unlock C; failed=${failed}`, async () => {
    const ui = await mount({ deferLists: true });
    try {
      await ui.button('取込済みPDFを表示');
      assert.equal(ui.listReads.length, 1);
      if (boundary === 'route') await ui.route('/pdf-upload?list=B'); else await ui.session();
      assert.equal(ui.listReads.length, 2);
      await ui.settle(() => ui.listReads[1].resolve({ data: { items: [{ id: 'B', original_filename: 'current-B', status: 'manual_review' }] } }));
      assert.ok(ui.text().includes('current-B'));
      await ui.button('最新に更新');
      assert.equal(ui.listReads.length, 3);
      await ui.settle(() => failed ? ui.listReads[0].reject(new Error('obsolete-list'))
        : ui.listReads[0].resolve({ data: { items: [{ id: 'A', original_filename: 'obsolete-list' }] } }));
      assert.equal(ui.text().includes('obsolete-list'), false);
      assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('更新中')).disabled, true);
      await ui.settle(() => ui.listReads[2].resolve({ data: { items: [{ id: 'C', original_filename: 'current-C', status: 'manual_review' }] } }));
      assert.ok(ui.text().includes('current-C'));
      assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('最新に更新')).disabled, false);
    } finally { await ui.close(); }
  });

  test(`canonical preview must re-resolve and re-confirm across ${boundary}; failed=${failed}`, async () => {
    const ui = await mount();
    try {
      const [file] = await ui.ready();
      await ui.change(ui.document.querySelector('input[placeholder="例: 大和なでしこ"]'), 'operator retained');
      await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-01-03');
      const old = ui.resolutions.at(-1), form = ui.getForm();
      if (boundary === 'route') await ui.route('/pdf-upload?preview=B'); else await ui.session();
      assert.equal(ui.getForm(), form);
      assert.equal(ui.getForm().getValues('facility_name'), 'operator retained');
      assert.ok(ui.text().includes('選択中: A.bin'));
      const fresh = ui.resolutions.at(-1);
      assert.notEqual(fresh, old);
      await ui.settle(() => failed ? old.reject(new Error('obsolete-preview'))
        : old.resolve({ data: { source: 'canonical_json', facility_id: 'F', effective_date: old.date,
          revision: 'obsolete-preview', version: { version_id: 'obsolete-version' } } }));
      await ui.submit(); assert.equal(ui.posts.length, 0);
      assert.equal(ui.text().includes('obsolete-preview'), false);
      await ui.settle(() => fresh.resolve({ data: { source: 'canonical_json', facility_id: 'F', effective_date: fresh.date,
        revision: 'fresh-only', version: { version_id: 'fresh-only-version' } } }));
      await ui.submit(); assert.equal(ui.posts.length, 0);
      await ui.clickLabel('この施設版を使用する');
      await ui.submit(); assert.equal(ui.posts.length, 1);
      assert.equal(ui.posts[0].data.get('pdf_file'), file);
      assert.equal(ui.posts[0].data.get('expected_canonical_revision'), 'fresh-only');
      assert.equal(ui.posts[0].data.get('selected_version_id'), 'fresh-only-version');
      assert.equal(ui.posts[0].data.get('facility_name'), 'operator retained');
      await ui.settle(() => ui.saves[0].resolve({ data: { message_id: 'fresh-upload' } }));
    } finally { await ui.close(); }
  });
}

for (const boundary of ['route', 'session']) for (const failed of [false, true]) for (const order of ['old-first', 'new-first']) {
  test(`retry slot/result/list ownership across ${boundary}, ${order}; failed=${failed}`, async () => {
    const ui = await mount({ deferLists: true });
    const row = id => ({ id, original_filename: `file-${id}`, status: 'manual_review' });
    try {
      await ui.button('取込済みPDFを表示');
      await ui.settle(() => ui.listReads[0].resolve({ data: { items: [row('A')] } }));
      await ui.button('再処理に戻す');
      assert.equal(ui.retries.length, 1);
      assert.equal(ui.retries[0].url, '/ingest/uploads/A/retry');
      assert.deepEqual(ui.retries[0].options, { params: { intake_protocol: 'native_ingest_v2' } });
      if (boundary === 'route') await ui.route('/pdf-upload?retry=B'); else await ui.session();
      assert.equal(ui.listReads.length, 2);
      await ui.settle(() => ui.listReads[1].resolve({ data: { items: [row('B')] } }));
      await ui.button('再処理に戻す');
      assert.equal(ui.retries.length, 2);
      if (order === 'new-first') {
        await ui.settle(() => ui.retries[1].resolve({ data: {} }));
        assert.equal(ui.listReads.length, 3);
        await ui.settle(() => ui.listReads[2].resolve({ data: { items: [row('C')] } }));
        await ui.button('再処理に戻す');
        assert.equal(ui.retries.length, 3);
      }
      const readCount = ui.listReads.length;
      await ui.settle(() => failed ? ui.retries[0].reject(new Error('obsolete-retry')) : ui.retries[0].resolve({ data: {} }));
      assert.equal(ui.listReads.length, readCount);
      assert.equal(ui.text().includes('obsolete-retry'), false);
      assert.equal(ui.text().includes('「file-A」を再処理に戻しました'), false);
      assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('再投入中')).disabled, true);
      await ui.settle(() => ui.retries.at(-1).resolve({ data: {} }));
      await ui.settle(() => ui.listReads.at(-1).resolve({ data: { items: [row('D')] } }));
      assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('再処理に戻す')).disabled, false);
      assert.ok(ui.text().includes('file-D'));
    } finally { await ui.close(); }
  });
}

test('requested list filter owns its exact response and busy slot without initial eager listing', async () => {
  const ui = await mount({ deferLists: true });
  try {
    assert.equal(ui.listReads.length, 0);
    await ui.button('取込済みPDFを表示');
    const select = ui.document.querySelector('select');
    assert.ok(select);
    for (const status of ['manual_review', 'completed']) await ui.settle(() => {
      Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set.call(select, status);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    assert.equal(ui.listReads.length, 3);
    assert.deepEqual(ui.reads.filter(read => read.url === '/ingest/uploads').map(read => read.options.params), [
      { limit: 50 }, { status: 'manual_review', limit: 50 }, { status: 'completed', limit: 50 },
    ]);
    for (const old of ui.listReads.slice(0, 2)) await ui.settle(() => old.resolve({ data: { items: [{ id: 'old-filter', original_filename: 'old-filter' }] } }));
    assert.equal(ui.text().includes('old-filter'), false);
    assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('更新中')).disabled, true);
    await ui.settle(() => ui.listReads[2].resolve({ data: { items: [{ id: 'current-filter', original_filename: 'current-filter', status: 'completed' }] } }));
    assert.ok(ui.text().includes('current-filter'));
    assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('最新に更新')).disabled, false);
  } finally { await ui.close(); }
});

for (const boundary of ['route', 'session']) test(`late preview and facility responses cannot replace accepted fresh ${boundary} receipts`, async () => {
  const ui = await mount({ deferFacilities: true });
  try {
    await ui.settle(() => ui.facilityReads[0].resolve({ data: { facilities: [{ id: 'F', name: 'Component F' }] } }));
    await ui.ready();
    await ui.change(ui.document.querySelector('input[name="effective_date"]'), '2026-01-03');
    const oldPreview = ui.resolutions.at(-1);
    if (boundary === 'route') await ui.route('/pdf-upload?source=B'); else await ui.session();
    const oldFacilities = ui.facilityReads.at(-1);
    await ui.route('/pdf-upload?source=C');
    assert.equal(ui.facilityReads.length, 3);
    assert.notEqual(ui.resolutions.at(-1), oldPreview);
    await ui.settle(() => ui.facilityReads.at(-1).resolve({ data: { facilities: [{ id: 'F', name: 'fresh-C' }] } }));
    await ui.confirmFreshPreview();
    await ui.settle(() => oldFacilities.resolve({ data: { facilities: [{ id: 'X', name: 'obsolete-X' }] } }));
    await ui.settle(() => oldPreview.resolve({ data: { source: 'canonical_json', facility_id: 'F', effective_date: oldPreview.date,
      revision: 'obsolete-revision', version: { version_id: 'obsolete-version' } } }));
    assert.equal(ui.text().includes('obsolete-X'), false);
    assert.equal(ui.text().includes('obsolete-revision'), false);
    await ui.submit();
    assert.equal(ui.posts.length, 1);
    assert.equal(ui.posts[0].data.get('expected_canonical_revision'), 'fresh-revision');
    assert.equal(ui.posts[0].data.get('selected_version_id'), 'fresh-version');
    await ui.settle(() => ui.saves[0].resolve({ data: { message_id: 'fresh-C-upload' } }));
  } finally { await ui.close(); }
});

for (const command of ['upload', 'retry']) for (const order of ['old-first', 'new-first'])
  for (const oldFailed of [false, true]) for (const currentFailed of [false, true]) {
    test(`completion refresh uses current query: ${command}, ${order}, oldError=${oldFailed}, currentError=${currentFailed}`, async () => {
      const ui = await mount({ deferLists: true });
      try {
        if (command === 'upload') await ui.ready();
        await ui.button('取込済みPDFを表示');
        await ui.settle(() => ui.listReads[0].resolve({ data: { items: [{ id: 'source-A', original_filename: 'source-A', status: 'manual_review' }] } }));
        if (command === 'upload') await ui.submit(); else await ui.button('再処理に戻す');
        const pendingCommand = command === 'upload' ? ui.saves[0] : ui.retries[0];
        await ui.filter('manual_review');
        await ui.filter('completed');
        assert.equal(ui.listReads.length, 3);
        await ui.settle(() => pendingCommand.resolve({ data: { message_id: 'owned-command-result' } }));
        assert.equal(ui.listReads.length, 4);
        assert.deepEqual(ui.reads.filter(read => read.url === '/ingest/uploads').map(read => read.options.params), [
          { limit: 50 }, { status: 'manual_review', limit: 50 },
          { status: 'completed', limit: 50 }, { status: 'completed', limit: 50 },
        ]);
        assert.equal(ui.document.querySelector('select').value, 'completed');
        if (command === 'upload') {
          assert.ok(ui.text().includes('owned-command-result'));
          assert.equal(ui.text().includes('選択中: A.bin'), false);
          assert.equal(ui.posts.length, 1);
        } else {
          assert.ok(ui.text().includes('「source-A」を再処理に戻しました'));
          assert.equal(ui.retries.length, 1);
        }
        const finishCurrent = () => currentFailed
          ? ui.listReads[3].reject(new Error('current-query-error'))
          : ui.listReads[3].resolve({ data: { items: [{ id: 'current-query-D', original_filename: 'current-query-D', status: 'completed' }] } });
        const finishOld = async () => {
          for (const old of ui.listReads.slice(1, 3)) await ui.settle(() => oldFailed
            ? old.reject(new Error('stale-query-error'))
            : old.resolve({ data: { items: [{ id: 'stale-query-row', original_filename: 'stale-query-row' }] } }));
        };
        if (order === 'new-first') await ui.settle(finishCurrent);
        await finishOld();
        assert.equal(ui.text().includes('stale-query-error'), false);
        assert.equal(ui.text().includes('stale-query-row'), false);
        if (order === 'old-first') {
          assert.equal([...ui.document.querySelectorAll('button')].find(node => node.textContent.includes('更新中')).disabled, true);
          await ui.settle(finishCurrent);
        }
        assert.equal(ui.text().includes('current-query-error'), currentFailed);
        assert.equal(ui.text().includes('current-query-D'), !currentFailed);
        assert.equal([...ui.document.querySelectorAll('button')].some(node => node.textContent.includes('更新中')), false);
        const update = [...ui.document.querySelectorAll('button')].find(node => /最新に更新|取込済みPDFを表示/.test(node.textContent));
        assert.equal(update.disabled, false);
        if (command === 'upload') assert.ok(ui.text().includes('owned-command-result'));
        else assert.ok(ui.text().includes('「source-A」を再処理に戻しました'));
      } finally { await ui.close(); }
    });
  }

for (const command of ['upload', 'retry']) test(`filter change retains current ${command} error and its existing refresh semantics`, async () => {
  const ui = await mount({ deferLists: true });
  try {
    if (command === 'upload') await ui.ready();
    await ui.button('取込済みPDFを表示');
    await ui.settle(() => ui.listReads[0].resolve({ data: { items: [{ id: 'source-A', status: 'manual_review' }] } }));
    if (command === 'upload') await ui.submit(); else await ui.button('再処理に戻す');
    await ui.filter('completed');
    const pending = command === 'upload' ? ui.saves[0] : ui.retries[0];
    await ui.settle(() => pending.reject({ response: { data: { detail: 'owned-command-error' } } }));
    // Upload intentionally refreshes after its per-file error result; failed retry does not.
    assert.equal(ui.listReads.length, command === 'upload' ? 3 : 2);
    assert.deepEqual(ui.reads.filter(read => read.url === '/ingest/uploads').at(-1).options.params, { status: 'completed', limit: 50 });
    assert.ok(ui.text().includes('owned-command-error'));
    await ui.settle(() => ui.listReads[1].resolve({ data: { items: [] } }));
    if (command === 'upload') await ui.settle(() => ui.listReads[2].resolve({ data: { items: [] } }));
    assert.ok(ui.text().includes('owned-command-error'));
    assert.equal(ui.document.querySelector('select').value, 'completed');
  } finally { await ui.close(); }
});
