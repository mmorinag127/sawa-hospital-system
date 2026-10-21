const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/services/portalProxySession.ts"), "utf8"), {
  compilerOptions: {module: ts.ModuleKind.CommonJS},
}).outputText, { exports: exportsObject, Headers, URL });
const proxy = exportsObject.portalProxyHeaders;
const origin = "https://web-stg-avlnzjjrca-dt.a.run.app";
const token = "ps_" + "a".repeat(43);
const cookies = {"__Host-sawa_session": token, "__Host-sawa_csrf": "csrf"};

test("school injects only GET shared-auth/me; business generation is retained", () => {
  const headers = new Headers({"sec-fetch-site": "same-origin"});
  assert.equal(proxy("/school-lunch/api/backend/shared-auth/me", "GET", headers, cookies, origin).get("authorization"), "Bearer " + token);
  const business = new Headers({origin, "x-sawa-session-generation": "generation"});
  const result = proxy("/school-lunch/api/backend/orders", "POST", business,
    {...cookies, sawa_school_lunch_session_generation: "generation"}, origin);
  assert.equal(result.get("authorization"), null);
  assert.equal(result.get("x-sawa-session-generation"), "generation");
  assert.throws(() => proxy("/school-lunch/api/backend/orders", "POST", business, cookies, origin));
  assert.equal(proxy("/school-lunch/api/backend/shared-auth/me", "POST", new Headers({origin, "x-sawa-csrf": "csrf"}), cookies, origin).get("authorization"), null);
});

test("opaque mutation requires same origin and csrf while machine bearer is unchanged", () => {
  for (const headers of [new Headers(), new Headers({origin}), new Headers({origin: "https://evil.invalid", "x-sawa-csrf": "csrf"})]) {
    assert.throws(() => proxy("/shift/api/backend/runs", "POST", headers, cookies, origin));
  }
  const result = proxy("/shift/api/backend/runs", "POST", new Headers({origin, "x-sawa-csrf": "csrf"}), cookies, origin);
  assert.equal(result.get("authorization"), "Bearer " + token);
  assert.equal(proxy("/api/orders", "POST", new Headers({authorization: "Bearer machine-token"}), cookies, "").get("authorization"), "Bearer machine-token");
});

test("callback retains Google double-cookie flow and no session is exposed in response", () => {
  assert.equal(proxy("/api/portal/auth/callback", "POST", new Headers({origin: "https://accounts.google.com"}), cookies, origin).get("authorization"), null);
  const source = fs.readFileSync(path.join(__dirname, "../src/proxy.ts"), "utf8");
  assert.match(source, /NextResponse\.next\(\{ request: \{ headers \} \}\)/);
  assert.doesNotMatch(source, /NextResponse\.(?:next|rewrite)\([^\n]*\{ headers \}\)/);
});
