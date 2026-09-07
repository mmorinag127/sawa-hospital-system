const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.resolve(__dirname, "../../src/pages/auth/automation.tsx"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function page({ system = "shift", role = "operator", systems = [system], authOk = true, sessionOk = true } = {}) {
  const state = [];
  const calls = [];
  let index = 0;
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(name) {
      if (name === "react") return { useState(initial) {
        const i = index++;
        if (!(i in state)) state[i] = initial;
        return [state[i], (value) => { state[i] = value; }];
      } };
      if (name === "react/jsx-runtime") {
        const jsx = (type, props) => ({ type, props });
        return { jsx, jsxs: jsx };
      }
      if (name.endsWith("apiClient")) return {
        clearAuth() { calls.push(["clear"]); },
        setBearerToken(token) { calls.push(["store", token]); },
      };
      throw new Error(name);
    },
    async fetch(url, options) {
      calls.push([url, options]);
      if (url.startsWith("/api/portal/")) return { ok: authOk, json: async () => ({ role, systems }) };
      assert.equal(url, "/school-lunch/api/backend/shared-auth/me");
      return { ok: sessionOk };
    },
    window: { location: { replace(url) { calls.push(["navigate", url]); } } },
  });
  function render() { index = 0; return exports.default(); }
  function find(node, type) {
    if (!node || typeof node !== "object") return null;
    if (node.type === type) return node;
    return [node.props?.children].flat().map((child) => find(child, type)).find(Boolean);
  }
  const initial = render();
  const select = find(initial, "select");
  assert.equal(select.props.value, "shift");
  assert.deepEqual(Array.from(select.props.children, (option) => option.props.value), ["shift", "school-lunch"]);
  select.props.onChange({ target: { value: system } });
  find(initial, "input").props.onChange({ target: { value: " signed-token " } });
  return {
    calls, state,
    async submit() { await find(render(), "form").props.onSubmit({ preventDefault() {} }); },
  };
}

for (const system of ["shift", "school-lunch"]) {
  test(`${system} authenticates with explicit target and canonical navigation`, async () => {
    const scenario = page({ system });
    await scenario.submit();
    const { calls } = scenario;
    assert.equal(calls[0][0], "clear");
    assert.equal(calls[1][0], `/api/portal/automation/auth?system=${system}`);
    assert.equal(calls[1][1].headers.Authorization, "Bearer signed-token");
    assert.equal(calls[1][1].method, "POST");
    if (system === "school-lunch") {
      assert.equal(calls[2][0], "/school-lunch/api/backend/shared-auth/me");
      assert.equal(calls[2][1].credentials, "same-origin");
      assert.equal(calls[2][1].headers.Authorization, "Bearer signed-token");
      assert.equal(calls[2][1].cache, "no-store");
      assert.equal(calls[2][1].redirect, "error");
    } else {
      assert.equal(calls.length, 4);
    }
    assert.deepEqual(calls.at(-2), ["store", "signed-token"]);
    assert.deepEqual(calls.at(-1), ["navigate", system === "shift" ? "/shift" : "/school-lunch/implementation-price-tables"]);
    assert.equal(scenario.state[0], "");
  });
}

for (const invalid of [
  { authOk: false }, { role: "admin" }, { systems: ["shift"] },
  { systems: ["school-lunch", "shift"] }, { systems: [] }, { sessionOk: false },
]) {
  test(`school lunch fails closed: ${JSON.stringify(invalid)}`, async () => {
    const scenario = page({ system: "school-lunch", ...invalid });
    await scenario.submit();
    assert.equal(scenario.calls.some(([name]) => name === "store" || name === "navigate"), false);
    assert.equal(scenario.state[0], "");
    assert.equal(scenario.state[2], false);
    assert.notEqual(scenario.state[3], "");
    if (invalid.sessionOk !== false) assert.equal(scenario.calls.length, 2);
  });
}
