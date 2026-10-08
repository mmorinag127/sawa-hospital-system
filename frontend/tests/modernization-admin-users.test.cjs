const test = require('node:test');
const assert = require('node:assert/strict');
const { mount, transport, deferred } = require('./helpers/modernization-react-dom.cjs');

const user = (id = 'first', systems = []) => ({ id, account: `${id}@example.invalid`, role: 'operator', status: 'active', systems });
function setup(items = [user('first'), user('second')]) {
  const t = transport(); t.on('get', '/auth/me', () => ({ data: { role: 'admin' } }));
  t.on('get', '/portal/users', () => ({ data: { items } })); return t;
}
async function edit(h, id = 'first') {
  const row = [...h.document.querySelectorAll('tbody tr')].find(node => node.textContent.includes(`${id}@example.invalid`));
  assert.ok(row); await h.click([...row.querySelectorAll('button')].find(node => node.textContent === '編集'));
  assert.ok(h.document.querySelector('input[name="account"]'));
}
const writes = t => t.calls.filter(call => ['post', 'put', 'delete'].includes(call.method));

test('actual RHF required email uses native HTML validity, accepts no-dot domain and retains normalized API result', async () => {
  const t = setup([]); t.on('post', '/portal/users', call => ({ data: { user: { ...user('saved'), account: call.body.account.trim().toLowerCase() } } }));
  const h = await mount('src/pages/admin/users.tsx', { api: t.api });
  try {
    await h.settle(); await h.click(h.button('ユーザーを追加'));
    const input = h.document.querySelector('input[name="account"]');
    assert.equal(input.type, 'email'); assert.equal(input.required, true); assert.equal(input.checkValidity(), false);
    await h.click(h.button('保存')); assert.equal(writes(t).length, 0);
    await h.change(input, 'not-an-email'); assert.equal(input.checkValidity(), false);
    await h.click(h.button('保存')); assert.equal(writes(t).length, 0);
    await h.change(input, 'USER@local'); assert.equal(input.checkValidity(), true);
    await h.click(h.button('保存')); await h.settle();
    assert.deepEqual(writes(t)[0].body, { account: 'USER@local', role: 'operator', status: 'active', systems: [] });
    assert.match(h.document.body.textContent, /user@local を保存しました/);
  } finally { await h.close(); }
});

for (const unknown of ['legacy-system', '__proto__', 'constructor', 'toString']) {
  test(`actual systems control preserves unknown own-key ${unknown} and stops writes`, async () => {
    const t = setup([user('first', ['hospital', unknown])]);
    const h = await mount('src/pages/admin/users.tsx', { api: t.api });
    try {
      await h.settle(); await edit(h);
      assert.match(h.document.body.textContent, new RegExp(`現在の利用システム: ${unknown}`));
      await h.click(h.button('保存')); assert.equal(writes(t).length, 0);
      assert.match(h.document.body.textContent, /未対応の値は破棄せず保存を停止/);
      assert.ok(h.document.querySelector('input[name="account"]'));
    } finally { await h.close(); }
  });
}

for (const outcome of ['success', 'error']) {
  for (const boundary of ['editor', 'cancel', 'session', 'unmount']) {
    test(`admin old ${outcome} after ${boundary} cannot adopt or clear newer editor`, async () => {
      const pending = deferred(), t = setup(); t.on('put', '/portal/users/first', () => pending.promise);
      const h = await mount('src/pages/admin/users.tsx', { api: t.api });
      try {
        await h.settle(); await edit(h); await h.change(h.document.querySelector('input[name="account"]'), 'first-edited@local');
        await h.click(h.button('保存')); assert.equal(writes(t).length, 1);
        if (boundary === 'unmount') await h.unmount();
        else if (boundary === 'session') await h.session(false);
        else {
          await h.click(h.button('キャンセル'));
          if (boundary === 'editor') { await edit(h, 'second'); await h.change(h.document.querySelector('input[name="account"]'), 'newer@local'); }
        }
        await h.settle(() => outcome === 'success' ? pending.resolve({ data: { user: user('first') } })
          : pending.reject({ response: { data: { detail: 'obsolete-write-error' } } }));
        await h.settle();
        assert.doesNotMatch(h.document.body.textContent, /obsolete-write-error|first@example.invalid を保存しました/);
        if (boundary === 'editor') assert.equal(h.document.querySelector('input[name="account"]').value, 'newer@local');
        if (boundary === 'cancel') assert.equal(h.document.querySelector('input[name="account"]'), null);
      } finally { await h.close(); }
    });
  }
}

for (const authority of ['admin', 'operator', 'unknown']) test(`newer real form values survive retired write authority recheck -> ${authority}`, async () => {
  const write = deferred(), role = deferred(), t = setup();
  t.on('put', '/portal/users/first', () => write.promise);
  const h = await mount('src/pages/admin/users.tsx', { api: t.api });
  try {
    await h.settle(); await edit(h); await h.click(h.button('保存'));
    await h.click(h.button('キャンセル')); await edit(h, 'second');
    await h.change(h.document.querySelector('input[name="account"]'), 'new-input@local');
    const currentInput = h.document.querySelector('input[name="account"]');
    t.on('get', '/auth/me', () => role.promise);
    await h.settle(() => write.resolve({ data: { user: user('first') } }));
    assert.equal(h.document.querySelector('input[name="account"]'), currentInput);
    assert.equal(currentInput.value, 'new-input@local');
    assert.equal(currentInput.matches(':disabled'), true);
    assert.match(h.document.body.textContent, /管理者権限を確認しています/);
    await h.settle(() => role.resolve({ data: { role: authority } })); await h.settle();
    assert.equal(currentInput.value, 'new-input@local');
    assert.equal(currentInput.matches(':disabled'), authority !== 'admin');
    if (authority !== 'admin') {
      await h.click(h.button('保存')); assert.equal(writes(t).length, 1);
      assert.match(h.document.body.textContent, /管理者権限が必要/);
    }
  } finally { await h.close(); }
});

test('owned save failure keeps entered values and exact-target mismatch never writes another row into list', async () => {
  const t = setup(); let mode = 'error';
  t.on('put', '/portal/users/first', () => {
    if (mode === 'error') throw { response: { data: { detail: 'synthetic-rejected' } } };
    return { data: { user: { ...user('second'), account: 'wrong-owner@local' } } };
  });
  const h = await mount('src/pages/admin/users.tsx', { api: t.api });
  try {
    await h.settle(); await edit(h); await h.change(h.document.querySelector('input[name="account"]'), 'entered@local');
    await h.click(h.button('保存')); assert.match(h.document.body.textContent, /synthetic-rejected/);
    assert.equal(h.document.querySelector('input[name="account"]').value, 'entered@local');
    mode = 'mismatch'; await h.click(h.button('保存')); await h.settle();
    assert.doesNotMatch(h.document.querySelector('tbody').textContent, /wrong-owner@local/);
    assert.equal(writes(t).every(call => call.url === '/portal/users/first'), true);
    assert.equal(h.document.querySelector('input[name="account"]').value, 'entered@local');
  } finally { await h.close(); }
});

test('real shared leave/discard guard cancels selection without resetting unsaved input', async () => {
  const t = setup(), h = await mount('src/pages/admin/users.tsx', { api: t.api });
  try {
    await h.settle(); await edit(h); await h.change(h.document.querySelector('input[name="account"]'), 'unsaved@local');
    const confirm = window.confirm; window.confirm = () => false;
    try { await h.click(h.button('キャンセル')); }
    finally { window.confirm = confirm; }
    assert.equal(h.document.querySelector('input[name="account"]').value, 'unsaved@local');
    assert.equal(writes(t).length, 0);
  } finally { await h.close(); }
});

function roleComponent(loader, React) {
  const { useCurrentUserRole } = loader.load('src/hooks/useCurrentUserRole.ts');
  function Consumer() { const role = useCurrentUserRole(); return React.createElement('output', null, JSON.stringify(role)); }
  return ({ count = 2 }) => React.createElement('div', null, Array.from({ length: count }, (_, id) => React.createElement(Consumer, { key: id })));
}

for (const role of ['admin', 'operator', 'unknown', null]) test(`real role Query without provider: ${role}, concurrent dedupe, fresh new mount`, async () => {
  const pending = deferred(), t = transport(); let first = true;
  t.on('get', '/auth/me', () => first ? pending.promise : { data: { role } });
  const h = await mount(null, { api: t.api, component: roleComponent });
  try {
    assert.equal(t.calls.length, 1); first = false;
    await h.settle(() => pending.resolve({ data: { role } })); await h.settle();
    const values = [...h.document.querySelectorAll('output')].map(node => JSON.parse(node.textContent));
    assert.equal(values.length, 2);
    assert.equal(values.every(value => value.role === (['admin', 'operator'].includes(role) ? role : '')), true);
    assert.equal(values.every(value => value.isAdmin === (role === 'admin') && value.isOperator === ['admin', 'operator'].includes(role)), true);
    const count = t.calls.length; await h.render({ count: 3 }); await h.settle();
    assert.equal(t.calls.length, count + 1, 'new mounted hook reads fresh actual authority, not Infinity cache');
  } finally { await h.close(); }
});

test('role error/logout/session response cannot promote stale admin and SSR shares no browser client', async () => {
  const old = deferred(), fresh = deferred(), t = transport(); let calls = 0;
  t.on('get', '/auth/me', () => ++calls === 1 ? old.promise : fresh.promise);
  const h = await mount(null, { api: t.api, component: roleComponent });
  try {
    await h.session(); await h.settle(() => old.resolve({ data: { role: 'admin' } }));
    assert.equal([...h.document.querySelectorAll('output')].some(node => JSON.parse(node.textContent).isAdmin), false);
    await h.settle(() => fresh.reject(new Error('synthetic-auth-failure'))); await h.settle();
    assert.equal([...h.document.querySelectorAll('output')].every(node => JSON.parse(node.textContent).role === ''), true);
    const module = h.loader.load('src/hooks/useCurrentUserRole.ts');
    const before = module.browserClients.size, apiCalls = t.calls.length;
    const descriptor = Object.getOwnPropertyDescriptor(global, 'window');
    try {
      delete global.window;
      const React = h.React, { renderToString } = h.requirePackage('react-dom/server');
      function SSR() { return React.createElement('output', null, JSON.stringify(module.useCurrentUserRole())); }
      assert.match(renderToString(React.createElement(SSR)), /&quot;role&quot;:&quot;&quot;/);
      assert.match(renderToString(React.createElement(SSR)), /&quot;loading&quot;:true/);
    } finally { Object.defineProperty(global, 'window', descriptor); }
    assert.equal(module.browserClients.size, before); assert.equal(t.calls.length, apiCalls);
    await h.session(false); assert.equal([...h.document.querySelectorAll('output')].every(node => JSON.parse(node.textContent).role === ''), true);
  } finally { await h.close(); }
});
