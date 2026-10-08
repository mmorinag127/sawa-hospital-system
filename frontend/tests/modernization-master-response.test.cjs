const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

// Authored synthetic master snapshots, not live master mutation or provider proof.
const copy = x => JSON.parse(JSON.stringify(x));
const master = id => ({ id, revision: 7, name: `raw-${id}`, unit_type: 'g', qty_per_serving: 0,
  bag_max_qty: null, bag_max_unit: null, temp_type: 'cold', daypart: '昼食', category: '主菜',
  condiments: ['raw condiment'], extra: { zero: 0, absent: null, enabled: false } });
const rule = id => ({ ...master(id), rule_type: 'global', match_type: null, menu_pattern: null,
  facility_id: null, diet_type: 'regular', priority: 0, active: false });
const base = id => ({ ...master(id), cycle_day: 1, slot_index: 0, diet_type: 'regular' });
const family = {
  masters: { page: 'menu-masters', item: master, bindings: ['choose', 'selected', 'list', 'editor'], field: 'name' },
  rules: { page: 'menu-rules', item: rule, bindings: ['ruleType', 'choose', 'selected', 'list', 'dirty'], field: 'daypart' },
  base: { page: 'base-menus', item: base, bindings: ['choose', 'selected', 'all', 'dirty'], field: 'name' },
};
function environment(name) {
  const f = family[name], t = transport(), state = { items: [f.item('A'), f.item('B')], revision: 'r7' };
  const sections = new Map(), probe = { component: name === 'rules' ? 'RuleSection' : 'Screen', bindings: f.bindings };
  if (name === 'rules') Object.defineProperty(probe, 'current', {
    get: () => sections.get('global'), set: value => sections.set(value.ruleType, value),
  });
  const path = `/${f.page}`;
  const snapshot = () => name === 'masters' ? { items: copy(state.items), total: state.items.length, offset: 0, limit: 50 }
    : { [name === 'rules' ? 'rules' : 'items']: copy(state.items), revision: state.revision };
  t.on('get', '/auth/me', () => ({ data: { role: 'operator' } }));
  t.on('get', path, call => ({ data: name === 'rules' && call.config.params.rule_type !== 'global'
    ? { rules: [], revision: state.revision } : snapshot() }));
  t.on('get', `${path}/A`, () => ({ data: { item: copy(state.items[0]) } }));
  t.on('put', `${path}/A`, call => {
    const saved = { ...state.items[0], ...call.body, revision: 8 };
    state.items = [saved, state.items[1]]; state.revision = 'r8';
    return { data: name === 'masters' ? { item: copy(saved) } : name === 'rules'
      ? { updated: true, rule: copy(saved), revision: 'r8' } : { updated: true, ...snapshot() } };
  });
  return { f, t, state, probe, path, snapshot };
}
const rows = (p, name) => name === 'masters' ? p.list.data?.items : name === 'rules' ? p.list.data?.rules : p.all.data?.items;
const selected = (p, name) => name === 'masters' ? p.selected : p.selected?.item;
async function ready(name) {
  const e = environment(name);
  const h = await mount(`src/pages/${e.f.page}.tsx`, { api: e.t.api, probe: e.probe, route: e.path });
  const shadowRoot = Object.getOwnPropertyDescriptor(global, 'ShadowRoot');
  Object.defineProperty(global, 'ShadowRoot', { configurable: true, writable: true, value: window.ShadowRoot });
  const close = h.close;
  h.close = async () => {
    try { await close(); }
    finally { if (shadowRoot) Object.defineProperty(global, 'ShadowRoot', shadowRoot); else delete global.ShadowRoot; }
  };
  try {
    await h.waitFor(() => e.probe.current && rows(e.probe.current, name)?.length === 2, 'actual master list ready');
    await h.settle(() => e.probe.current.choose(rows(e.probe.current, name)[0]));
    await h.waitFor(() => selected(e.probe.current, name)?.id === 'A', 'actual editor ready');
    return { ...e, h };
  } catch (error) { await h.close(); throw error; }
}
const form = h => [...h.document.querySelectorAll('form')].at(-1);
const input = (h, f) => form(h).querySelector(`input[name="${f.field}"]`);
async function submit(h) { await h.click(form(h).querySelector('button[type="submit"]')); }
async function reload(h) {
  await h.click([...form(h).querySelectorAll('button')].find(x => x.textContent === '再読込'));
  const confirm = [...h.document.querySelectorAll('button')].find(x => x.textContent === '破棄して再読込');
  if (confirm) await h.click(confirm);
}
async function finished(h) { await h.waitFor(() => !form(h).querySelector('button[type="submit"]').disabled, 'form operation finished'); }

for (const name of Object.keys(family)) test(`${name} actual form valid update retains raw nine fields/extra/zero/null/false and neighbor`, async () => {
  const { h, f, probe, t, path } = await ready(name);
  try {
    const neighbor = copy(rows(probe.current, name)[1]);
    await h.change(input(h, f), 'operator-value'); await submit(h); await finished(h);
    const saved = selected(probe.current, name), call = t.calls.find(x => x.method === 'put');
    assert.equal(call.url, `${path}/A`); assert.equal(saved.id, 'A'); assert.equal(saved[f.field], 'operator-value');
    assert.equal(saved.qty_per_serving, 0); assert.equal(saved.bag_max_qty, null);
    assert.deepEqual(copy(saved.extra), { zero: 0, absent: null, enabled: false });
    assert.deepEqual(copy(saved.condiments), ['raw condiment']);
    assert.equal(saved.unit_type, 'g'); assert.equal(saved.temp_type, 'cold'); assert.equal(saved.category, '主菜');
    if (name === 'rules') { assert.equal(saved.active, false); assert.equal(saved.priority, 0); }
    if (name === 'masters') assert.equal(call.body.revision, 7);
    else assert.equal(call.config.headers['If-Match'], 'r7');
    assert.deepEqual(copy(rows(probe.current, name)[1]), neighbor);
    assert.match(form(h).textContent, /保存しました/);
  } finally { await h.close(); }
});

for (const name of Object.keys(family)) for (const invalid of ['foreign', 'missing', 'blank', 'duplicate'])
test(`${name} actual invalid persisted update ${invalid} cannot reset editor/cache or mutate neighbors`, async () => {
  const { h, f, probe, t, path, snapshot } = await ready(name);
  try {
    const before = copy(rows(probe.current, name)), original = selected(probe.current, name);
    const bad = { ...f.item('A'), [f.field]: 'foreign-server-value' };
    if (invalid === 'foreign') bad.id = 'FOREIGN'; if (invalid === 'missing') delete bad.id; if (invalid === 'blank') bad.id = '  ';
    if (invalid === 'duplicate' && name === 'masters') {
      // Single-record response has no duplicate collection; use a list reload below.
      const list = snapshot(); list.items = [copy(before[0]), copy(before[0])];
      t.on('get', path, () => ({ data: list }));
      await h.settle(() => probe.current.list.refetch());
      assert.deepEqual(copy(rows(probe.current, name)), before); assert.equal(selected(probe.current, name), original);
      assert.equal(t.calls.filter(x => x.method === 'put').length, 0);
    } else {
      const response = name === 'masters' ? { item: bad } : name === 'rules'
        ? { updated: true, rule: bad, revision: 'r8' } : { updated: true, items: [bad, copy(before[1])], revision: 'r8' };
      if (invalid === 'duplicate' && name === 'rules') { response.rule.id = 'B'; }
      if (invalid === 'duplicate' && name === 'base') response.items = [bad, copy(bad)];
      t.on('put', `${path}/A`, () => ({ data: response }));
      await h.change(input(h, f), 'unsaved-input'); await submit(h); await finished(h);
      assert.equal(input(h, f).value, 'unsaved-input'); assert.equal(selected(probe.current, name), original);
      assert.deepEqual(copy(rows(probe.current, name)), before);
      assert.equal(t.calls.filter(x => x.method === 'put').length, 1);
      assert.doesNotMatch(form(h).textContent, /保存しました/);
    }
    assert.ok(h.document.querySelector('[role="alert"]'));
    assert.equal(t.calls.filter(x => ['post', 'delete'].includes(x.method)).length, 0);
  } finally { await h.close(); }
});

for (const name of Object.keys(family)) test(`${name} actual reload refuses foreign/missing target and preserves entered draft`, async () => {
  const { h, f, probe, t, path } = await ready(name);
  try {
    const original = selected(probe.current, name), before = copy(rows(probe.current, name));
    if (name === 'masters') t.on('get', `${path}/A`, () => ({ data: { item: f.item('FOREIGN') } }));
    else t.on('get', path, call => ({ data: name === 'rules' && call.config.params.rule_type !== 'global'
      ? { rules: [], revision: 'r8' } : { [name === 'rules' ? 'rules' : 'items']: [f.item('B')], revision: 'r8' } }));
    await h.change(input(h, f), 'reload-draft'); await reload(h); await finished(h);
    assert.equal(selected(probe.current, name), original); assert.equal(input(h, f).value, 'reload-draft');
    assert.deepEqual(copy(rows(probe.current, name)), before);
    assert.equal(t.calls.filter(x => ['put', 'post', 'delete'].includes(x.method)).length, 0);
    assert.ok(h.document.querySelector('[role="alert"]'));
  } finally { await h.close(); }
});

for (const invalid of ['wrong-type', 'duplicate', 'missing', 'blank']) test(`rules actual list/reload rejects ${invalid} before adopting snapshot`, async () => {
  const { h, f, probe, t, path } = await ready('rules');
  try {
    const before = copy(rows(probe.current, 'rules')), bad = f.item('A');
    if (invalid === 'wrong-type') bad.rule_type = 'menu'; if (invalid === 'missing') delete bad.id; if (invalid === 'blank') bad.id = '';
    t.on('get', path, call => ({ data: { revision: 'r8', rules: call.config.params.rule_type === 'global'
      ? invalid === 'duplicate' ? [bad, copy(bad)] : [bad] : [] } }));
    await h.change(input(h, f), 'still-dirty'); await reload(h); await finished(h);
    assert.deepEqual(copy(rows(probe.current, 'rules')), before); assert.equal(input(h, f).value, 'still-dirty');
    assert.ok(h.document.querySelector('[role="alert"]'));
  } finally { await h.close(); }
});

test('rules actual save rejects correct ID with wrong section type', async () => {
  const { h, f, probe, t, path } = await ready('rules');
  try {
    const before = copy(rows(probe.current, 'rules'));
    t.on('put', `${path}/A`, () => ({ data: { updated: true, rule: { ...rule('A'), rule_type: 'menu' }, revision: 'r8' } }));
    await h.change(input(h, f), 'typed-draft'); await submit(h); await finished(h);
    assert.equal(input(h, f).value, 'typed-draft'); assert.deepEqual(copy(rows(probe.current, 'rules')), before);
    assert.ok(h.document.querySelector('[role="alert"]'));
  } finally { await h.close(); }
});

for (const name of Object.keys(family)) test(`${name} isolated 409 preserves actual dirty form and exact request`, async () => {
  const { h, f, probe, t, path } = await ready(name);
  try {
    const before = copy(rows(probe.current, name));
    t.on('put', `${path}/A`, () => Promise.reject({ response: { status: 409 } }));
    await h.change(input(h, f), 'conflict-draft'); await submit(h);
    await h.waitFor(() => !!form(h).querySelector('[role="alert"]'), 'actual conflict surfaced');
    assert.equal(input(h, f).value, 'conflict-draft'); assert.deepEqual(copy(rows(probe.current, name)), before);
    assert.match(form(h).textContent, /新しい内容|他の編集/);
    assert.equal(t.calls.filter(x => x.method === 'put').length, 1);
  } finally { await h.close(); }
});

for (const name of Object.keys(family)) test(`${name} old successful save cannot replace newer selected editor`, async () => {
  const { h, f, probe, t, path } = await ready(name), pending = deferred();
  try {
    t.on('put', `${path}/A`, () => pending.promise);
    await h.change(input(h, f), 'old-command'); await submit(h);
    await h.waitFor(() => t.calls.some(x => x.method === 'put'), 'actual request started');
    await h.settle(() => probe.current.choose(rows(probe.current, name)[1]));
    await h.change(input(h, f), 'new-editor-draft');
    await h.settle(() => pending.resolve({ data: name === 'masters' ? { item: master('A') } : name === 'rules'
      ? { updated: true, rule: rule('A'), revision: 'r8' }
      : { updated: true, items: [base('A'), base('B')], revision: 'r8' } }));
    assert.equal(selected(probe.current, name).id, 'B'); assert.equal(input(h, f).value, 'new-editor-draft');
    assert.equal(t.calls.filter(x => x.method === 'put').length, 1);
  } finally { pending.resolve({ data: {} }); await h.settle(); await h.close(); }
});

test('master duplicate-name create accepts returned existing ID and actual list parser does not receive map index as ID', async () => {
  const { h, probe, t } = await ready('masters');
  try {
    const parser = h.loader.load('src/services/menuMasters.ts');
    const value = { items: [master('A'), master('B')], total: 2, offset: 0, limit: 50 };
    assert.deepEqual(copy(parser.parseMenuMasterList(value).items), value.items);
    const raw = { ...master(' raw-id '), name: ' raw-name ' };
    assert.deepEqual(copy(parser.parseMenuMaster(raw, ' raw-id ')), raw);
    t.on('post', '/menu-masters', () => ({ data: { item: master('A') } }));
    const createForm = h.document.querySelector('form[aria-label="メニューマスターを追加"]');
    await h.change(createForm.querySelector('input[name="name"]'), 'raw-A');
    await h.click(createForm.querySelector('button[type="submit"]'));
    await h.waitFor(() => t.calls.some(x => x.method === 'post'), 'duplicate-name create started');
    await h.waitFor(() => /保存しました/.test(createForm.textContent), 'duplicate-name create accepted');
    assert.equal(selected(probe.current, 'masters').id, 'A');
    assert.equal(t.calls.filter(x => x.method === 'post').length, 1);
  } finally { await h.close(); }
});

for (const name of ['rules', 'base']) test(`${name} operator blank create and explicit neighbor delete retain stored raw values`, async () => {
  const { h, probe, t, path, state } = await ready(name);
  try {
    if (name === 'rules') {
      t.on('post', path, call => {
        const saved = { ...rule('NEW'), ...call.body, id: 'NEW' };
        state.items.push(saved); state.revision = 'r8';
        return { data: { rule: saved, revision: 'r8' } };
      });
      t.on('delete', `${path}/B`, () => {
        state.items = state.items.filter(x => x.id !== 'B'); state.revision = 'r9';
        return { data: { deleted: true } };
      });
      await h.click([...h.document.querySelectorAll('button')].find(x => x.textContent === '追加'));
      await h.change(form(h).querySelector('input[name="qty_per_serving"]'), '0');
    } else {
      t.on('post', path, call => {
        state.items = call.body.items.map((item, index) => ({ ...base(index === 2 ? 'NEW' : index === 0 ? 'A' : 'B'), ...item }));
        state.revision = 'r8';
        return { data: { created: state.items.length, items: copy(state.items), revision: 'r8' } };
      });
      await h.click(h.button('新規追加'));
      await h.change(form(h).querySelector('input[name="name"]'), 'new-operator-item');
    }
    const neighbor = copy(rows(probe.current, name)[1]);
    await submit(h); await finished(h);
    assert.equal(selected(probe.current, name).id, 'NEW');
    assert.equal(selected(probe.current, name).qty_per_serving, 0);
    assert.equal(selected(probe.current, name).bag_max_qty, null);
    assert.deepEqual(copy(selected(probe.current, name).extra), { zero: 0, absent: null, enabled: false });
    assert.deepEqual(copy(rows(probe.current, name).find(x => x.id === 'B')), neighbor);
    await h.settle(() => probe.current.choose(rows(probe.current, name).find(x => x.id === 'A')));
    await h.change(input(h, family[name]), 'neighbor-dirty');
    if (name === 'base') t.on('post', path, call => {
      assert.equal(call.config.headers['If-Match'], state.revision);
      state.items = call.body.items.map(item => ({ ...base(item.name === 'raw-A' ? 'A' : 'NEW'), ...item }));
      state.revision = 'r9';
      return { data: { created: state.items.length, revision: state.revision, items: copy(state.items) } };
    });
    const row = [...h.document.querySelectorAll('tbody tr')].find(x => name === 'base'
      ? x.textContent.includes('raw-B') : x.querySelectorAll('button').length && x === h.document.querySelectorAll('tbody tr')[1]);
    assert.ok(row);
    await h.click([...row.querySelectorAll('button')].find(x => x.textContent === '削除'));
    await h.waitFor(() => rows(probe.current, name).length === 2, 'explicit neighbor delete adopted');
    assert.equal(selected(probe.current, name).id, 'A'); assert.equal(input(h, family[name]).value, 'neighbor-dirty');
    assert.deepEqual(copy(rows(probe.current, name).find(x => x.id === 'A')), ruleOrBase(name, 'A'));
    // Explicit discard/reload acquires the post-delete baseline before another write.
    await reload(h); await finished(h);
    assert.equal(selected(probe.current, name).id, 'A');
    assert.equal(input(h, family[name]).value, name === 'base' ? 'raw-A' : '昼食');
    if (name === 'rules') t.on('delete', `${path}/A`, () => {
      state.items = state.items.filter(x => x.id !== 'A'); state.revision = 'r10';
      return { data: { deleted: true } };
    });
    const ownRow = name === 'base'
      ? [...h.document.querySelectorAll('tbody tr')].find(x => x.textContent.includes('raw-A'))
      : [...h.document.querySelectorAll('tbody tr')][0];
    assert.ok(ownRow);
    await h.click([...ownRow.querySelectorAll('button')].find(x => x.textContent === '削除'));
    await h.waitFor(() => !selected(probe.current, name) && rows(probe.current, name).length === 1, 'selected delete ends editor and refreshes list');
    assert.equal(rows(probe.current, name)[0].id, 'NEW');
    assert.equal((name === 'rules' ? probe.current.list : probe.current.all).isError, false);
  } finally { await h.close(); }
});
function ruleOrBase(name, id) {
  const value = name === 'rules' ? rule(id) : base(id);
  if (name === 'base') value.clientKey = id;
  return value;
}

for (const name of ['masters', 'base']) for (const invalid of ['missing', 'blank', 'duplicate'])
test(`${name} actual list read rejects ${invalid} identity without replacing prior snapshot`, async () => {
  const { h, probe, t, path, snapshot, f } = await ready(name);
  try {
    const before = copy(rows(probe.current, name)), bad = f.item('A'), data = snapshot();
    if (invalid === 'missing') delete bad.id; if (invalid === 'blank') bad.id = '  ';
    data.items = invalid === 'duplicate' ? [bad, copy(bad)] : [bad];
    t.on('get', path, () => ({ data }));
    await h.settle(() => (name === 'masters' ? probe.current.list : probe.current.all).refetch());
    assert.deepEqual(copy(rows(probe.current, name)), before);
    assert.equal((name === 'masters' ? probe.current.list : probe.current.all).isError, true);
    assert.equal(t.calls.filter(x => ['post', 'put', 'delete'].includes(x.method)).length, 0);
  } finally { await h.close(); }
});
