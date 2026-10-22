const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const vm = require("node:vm");

const storage = () => { const data = new Map(); return { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k) }; };
const load = (fetch) => {
  const events = []; const sessionStorage = storage(); const localStorage = storage();
  const window = { sessionStorage, localStorage, location: { origin: "http://localhost", pathname: "/menu-masters", search: "", hash: "" }, crypto: { randomUUID: () => `id-${events.length}` }, addEventListener() {}, removeEventListener() {}, dispatchEvent: e => events.push(e.type) };
  const browser = fs.readFileSync(path.join(__dirname, "../src/services/browserSession.ts"), "utf8");
  const browserContext = { exports: {}, window, document: { cookie: "" }, Event: class { constructor(type) { this.type = type; } } };
  vm.runInNewContext(ts.transpileModule(browser, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, browserContext);
  const dataset = require("./helpers/staging-dataset-module.cjs")(window);
  dataset.initializeDocumentDataset({ dataset: "test" });
  const api = fs.readFileSync(path.join(__dirname, "../src/services/apiClient.ts"), "utf8");
  const context = { exports: {}, window, fetch, document: browserContext.document, process: { env: {} }, require: name => name === "./stagingDataset" ? dataset : name === "./browserSession" ? browserContext.exports : name === "./loginDestination" ? { loginUrlFor: () => "/login" } : require(name) };
  vm.runInNewContext(ts.transpileModule(api, { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText, context);
  return { api: context.exports, sessionStorage, localStorage, events, window, document: browserContext.document };
};

test("session auth is render-safe and clear is idempotent", () => {
  const t = load(); assert.equal(t.api.hasActiveSessionAuthHeader(), false); assert.equal(t.events.length, 0);
  t.api.setBearerToken("fixture-token"); const generation = t.sessionStorage.getItem("sawa_auth_cache_generation"); assert.equal(t.api.hasActiveSessionAuthHeader(), true);
  t.api.clearAuth(); assert.equal(t.sessionStorage.getItem("auth_header"), null); assert.notEqual(t.sessionStorage.getItem("sawa_auth_cache_generation"), generation);
  const afterFirst = t.events.length; const afterGeneration = t.sessionStorage.getItem("sawa_auth_cache_generation"); t.api.clearAuth(); assert.equal(t.events.length, afterFirst); assert.equal(t.sessionStorage.getItem("sawa_auth_cache_generation"), afterGeneration);
});

test("cookie session stores only generation and repeated me does not remount caches", async () => {
  const t = load(async () => ({ ok: true, json: async () => ({role: "admin", session_generation: "generation"}) }));
  await t.api.establishServerSession();
  assert.equal(t.sessionStorage.getItem("auth_header"), null);
  assert.equal(t.api.hasActiveSessionAuthHeader(), true);
  const events = t.events.length;
  await t.api.establishServerSession();
  assert.equal(t.events.length, events);
});

for (const race of ["logout", "new-auth", "abort"]) test(`late server me cannot undo ${race}`, async () => {
  let resolve;
  const t = load(() => new Promise(done => { resolve = done; }));
  const controller = new AbortController();
  const result = t.api.establishServerSession(controller.signal);
  if (race === "logout") t.localStorage.setItem("sawa_logout_generation", "logout");
  if (race === "new-auth") t.api.setBearerToken("machine-new");
  if (race === "abort") controller.abort();
  resolve({ ok: true, json: async () => ({role: "admin", session_generation: "stale"}) });
  await assert.rejects(result);
  assert.equal(t.sessionStorage.getItem("sawa_server_session"), null);
  if (race === "new-auth") assert.equal(t.sessionStorage.getItem("auth_header"), "Bearer machine-new");
});

const deferred401 = async (t) => {
  let reject, ready;
  const started = new Promise(r => { ready = r; });
  t.api.apiClient.defaults.adapter = config => new Promise((resolve, rejectRequest) => {
    reject = () => rejectRequest({ config, response: { status: 401 } }); ready();
  });
  const result = t.api.apiClient.get('/fixture').catch(error => error);
  await started;
  return { reject, result };
};

for (const nextToken of ['B', 'A']) test('old 401 cannot invalidate a newer session token=' + nextToken, async () => {
  const t = load(); t.api.setBearerToken('A'); const pending = await deferred401(t);
  t.api.setBearerToken(nextToken); const generation = t.sessionStorage.getItem('sawa_auth_cache_generation');
  pending.reject(); assert.equal((await pending.result).response.status, 401);
  assert.equal(t.sessionStorage.getItem('auth_header'), 'Bearer ' + nextToken);
  assert.equal(t.sessionStorage.getItem('sawa_auth_cache_generation'), generation);
  assert.equal(t.window.location.href, undefined);
});

test('current session 401 clears auth and redirects after notifying subscribers', async () => {
  const t = load(); t.api.setBearerToken('A'); const pending = await deferred401(t);
  const count = t.events.length; pending.reject(); await pending.result;
  assert.equal(t.sessionStorage.getItem('auth_header'), null);
  assert.equal(t.window.location.href, '/login'); assert.ok(t.events.length > count);
});
