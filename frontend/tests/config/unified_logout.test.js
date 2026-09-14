const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");
const root = path.join(__dirname, "../../src");
const read = file => fs.readFileSync(path.join(root, file), "utf8");

test("canonical logout expires sibling HttpOnly cookie before returning a non-cacheable page", async () => {
  const headers = {};
  let broadcast = 0;
  let redirected = "";
  const context = { exports: {}, window: { location: { replace: url => { redirected = url; } } },
    require: name => name === "react" ? { useEffect: fn => fn() } :
      name === "react/jsx-runtime" ? { jsx: () => null } : { broadcastLogout: () => broadcast++ } };
  vm.runInNewContext(ts.transpileModule(read("pages/logout.tsx"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, context);
  await context.exports.getServerSideProps({ res: { setHeader: (key, value) => { headers[key] = value; } } });
  assert.match(headers["Cache-Control"], /no-store/);
  assert.ok(headers["Set-Cookie"].includes("sawa_school_lunch_auth=; Path=/school-lunch; HttpOnly; Secure; SameSite=Strict; Max-Age=0"));
  context.exports.default();
  assert.equal(broadcast, 1);
  assert.equal(redirected, "/login");
});
test("all portal/hospital pages use central logout and reject stale tab credentials", () => {
  assert.match(read("components/UnifiedShell.tsx"), /location.replace\("\/logout"\)/);
  assert.match(read("pages/_app.tsx"), /"\/logout"/);
  assert.match(read("pages/_app.tsx"), /watchBrowserLogout/);
  assert.match(read("services/apiClient.ts"), /if \(sessionWasLoggedOut\(\)\)/);
  assert.match(read("services/apiClient.ts"), /markSessionCurrent\(\)/);
});
