const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// Run the production navigation and destination validation, as in login-destination.test.cjs.
function scenario(authorization = 'Bearer synthetic-test', status = 200) {
  const calls = [], navigation = [], alerts = [], stored = new Map();
  const globals = {
    URL, URLSearchParams, Error,
    fetch: async (url, options) => {
      calls.push({url, options});
      return {ok: status === 200, status,
        json: async () => ({role: 'operator', session_generation: 'test-generation'})};
    },
    window: {
      sessionStorage: {setItem: (key, value) => stored.set(key, value)},
      localStorage: {setItem: (key, value) => stored.set(key, value)},
      location: {assign: value => navigation.push(value)},
      alert: value => alerts.push(value),
    },
  };
  function load(name) {
    const module = {exports: {}};
    const source = fs.readFileSync(path.join(__dirname, '../src/services', name + '.ts'), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
    }}).outputText;
    vm.runInNewContext(compiled, {module, exports: module.exports, ...globals,
      require: dependency => dependency === './apiClient'
        ? {getStoredAuthHeader: () => authorization}
        : load(dependency.replace('./', '')),
    });
    return module.exports;
  }
  return {api: load('systemNavigation'), calls, navigation, alerts, stored};
}

test('ordinary school entry navigates to the single root after session verification', async () => {
  const h = scenario();
  let prevented = false;
  await h.api.enterSchoolLunch({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.deepEqual(h.navigation, ['/school-lunch']);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/school-lunch/api/backend/shared-auth/me');
  assert.equal(h.calls[0].options.headers.Authorization, 'Bearer synthetic-test');
  assert.equal(h.calls[0].options.credentials, 'same-origin');
  assert.equal(h.calls[0].options.redirect, 'error');
  assert.deepEqual(h.alerts, []);
});

for (const destination of [
  '/school-lunch/implementation-price-tables?facility_id=8&draft_id=registered',
  '/school-lunch/route-diagrams?facility_id=8&service_date=2026-07-01#saved',
]) test(`explicit deep link is preserved: ${destination}`, async () => {
  const h = scenario();
  await h.api.enterSchoolLunch({preventDefault() {}}, destination);
  assert.deepEqual(h.navigation, [destination]);
});

test('unauthenticated ordinary entry retains the root as the login return destination', async () => {
  const h = scenario('');
  await h.api.enterSchoolLunch({preventDefault() {}});
  assert.equal(h.calls.length, 0);
  assert.equal(h.stored.get('auth_next'), '/school-lunch');
  assert.equal(new URL(h.navigation[0], 'https://sawa.invalid').searchParams.get('next'), '/school-lunch');
});

test('denied ordinary entry does not navigate', async () => {
  const h = scenario('Bearer synthetic-test', 403);
  await h.api.enterSchoolLunch({preventDefault() {}});
  assert.deepEqual(h.navigation, []);
  assert.equal(h.alerts.length, 1);
});
