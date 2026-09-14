const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");

const storage = () => {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key) };
};
const source = fs.readFileSync(path.join(__dirname, "../src/services/browserSession.ts"), "utf8");
function tab(localStorage) {
  const events = {};
  const window = { localStorage, sessionStorage: storage(), crypto: { randomUUID: () => "logout-" + Math.random() },
    addEventListener: (type, fn) => { events[type] = fn; }, removeEventListener: type => { delete events[type]; } };
  const context = { exports: {}, window, document: { cookie: "" } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
  return { ...context, api: context.exports, events };
}
test("logout clears current credentials and notifies a sibling tab without rebroadcasting", () => {
  const local = storage(), first = tab(local), sibling = tab(local);
  for (const t of [first, sibling]) { t.window.sessionStorage.setItem("auth_header", "Bearer test"); t.api.markSessionCurrent(); }
  local.setItem("auth_header", "Bearer legacy");
  let notifications = 0;
  sibling.api.watchBrowserLogout(() => notifications++);
  first.api.broadcastLogout();
  const generation = local.getItem("sawa_logout_generation");
  sibling.events.storage({ key: "sawa_logout_generation" });
  assert.equal(notifications, 1);
  assert.equal(first.window.sessionStorage.getItem("auth_header"), null);
  assert.equal(sibling.window.sessionStorage.getItem("auth_header"), null);
  assert.equal(local.getItem("auth_header"), null);
  assert.equal(local.getItem("sawa_logout_generation"), generation);
});
test("dormant tab rejects an old token even if it missed the storage event; fresh login succeeds", () => {
  const local = storage(), first = tab(local), dormant = tab(local);
  dormant.window.sessionStorage.setItem("auth_header", "Bearer old");
  dormant.api.markSessionCurrent();
  assert.equal(dormant.api.sessionWasLoggedOut(), false);
  first.api.broadcastLogout();
  assert.equal(dormant.api.sessionWasLoggedOut(), true);
  dormant.window.sessionStorage.setItem("auth_header", "Bearer new");
  dormant.api.markSessionCurrent();
  assert.equal(dormant.api.sessionWasLoggedOut(), false);
});
test("legacy unstamped tabs are invalid after logout, but existing sessions before first logout remain valid", () => {
  const local = storage(), t = tab(local);
  t.window.sessionStorage.setItem("auth_header", "Bearer old");
  assert.equal(t.api.sessionWasLoggedOut(), false);
  local.setItem("sawa_logout_generation", "ended");
  assert.equal(t.api.sessionWasLoggedOut(), true);
});
test("restored pages clear cached authenticated state and listener cleanup works", () => {
  const local = storage(), t = tab(local);
  let notified = 0;
  const stop = t.api.watchBrowserLogout(() => notified++);
  local.setItem("sawa_logout_generation", "ended");
  t.events.pageshow();
  assert.equal(notified, 1);
  t.events.pageshow();
  assert.equal(notified, 1);
  stop();
  assert.deepEqual(Object.keys(t.events), []);
});
