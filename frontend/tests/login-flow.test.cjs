const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript');
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function landing({search = '?next=%2Fschool-lunch%2Fcooking-arrangements%3Fjob_id%3Dreported', status = 200, pending = false} = {}) {
  const calls = [], effects = [], cleanups = [], messages = [], storage = new Map([['auth_next', '/hospital']]);
  let resolve, onLogout;
  const window = {location: {search, replace: value => calls.push(['navigate', value])}, sessionStorage: {removeItem: key => storage.delete(key)}};
  const context = {exports: {}, window, AbortController, URL, URLSearchParams,
    require(name) {
      if (name === 'react') return {useEffect: fn => effects.push(fn), useState: value => [value, message => messages.push(message)]};
      if (name === 'react/jsx-runtime') return {jsx: () => null};
      if (name.endsWith('/apiClient')) return {establishServerSession: signal => {
        calls.push(['me', signal]);
        return pending ? new Promise(done => {resolve = done;}) : status === 200 ? Promise.resolve({}) : Promise.reject({status});
      }};
      if (name.endsWith('/browserSession')) return {watchBrowserLogout: fn => {onLogout = fn; return () => {};}};
      if (name.endsWith('/systemNavigation')) return {prepareSystemDestination: async (destination, authorization, signal) => {
        if (signal.aborted) throw new Error('aborted'); calls.push(['handshake', destination, authorization]); return destination;
      }};
      if (name.endsWith('/loginDestination')) {
        const module = {exports: {}};
        vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/services/loginDestination.ts'), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText, {exports: module.exports, URL, URLSearchParams});
        return module.exports;
      }
      throw new Error(name);
    }};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/pages/auth/session.tsx'), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX},
  }).outputText, context);
  context.exports.default(); for (const effect of effects) cleanups.push(effect());
  return {calls, messages, storage, resolve: () => resolve({}), logout: () => onLogout(), unmount: () => cleanups.forEach(fn => fn())};
}

for (const target of ['/school-lunch/cooking-arrangements?job_id=reported', '/shift/planning?week=2026-09-21', '/']) test('server callback landing retains destination ' + target, async () => {
  const h = landing({search: '?next=' + encodeURIComponent(target)}); await flush();
  assert.deepEqual(h.calls.map(call => call[0]), ['me', 'handshake', 'navigate']);
  assert.equal(h.calls[1][2], ''); assert.equal(h.calls[2][1], target); assert.equal(h.storage.has('auth_next'), false);
});
for (const status of [401, 403, 503]) test('server me ' + status + ' does not navigate or erase return target', async () => {
  const h = landing({status}); await flush(); assert.equal(h.calls.length, 1); assert.equal(h.messages.length, 1); assert.equal(h.storage.get('auth_next'), '/hospital');
});
test('unsafe callback return is rejected before me', async () => {
  const h = landing({search: '?next=https://evil.invalid'}); await flush(); assert.equal(h.calls.length, 0); assert.equal(h.messages.length, 1);
});
for (const event of ['logout', 'unmount']) test('late server me after ' + event + ' cannot navigate', async () => {
  const h = landing({pending: true}); h[event](); assert.equal(h.calls[0][1].aborted, true); h.resolve(); await flush(); assert.equal(h.calls.length, 1);
});
test('GIS uses same-tab redirect with nonce and no human credential callback/storage', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/pages/login.tsx'), 'utf8');
  assert.match(source, /ux_mode: "redirect"/); assert.match(source, /nonce: loginConfig.nonce/);
  assert.match(source, /login_uri: loginConfig.login_uri/); assert.doesNotMatch(source, /setBearerToken|\bcredential\b|callback:/);
});
