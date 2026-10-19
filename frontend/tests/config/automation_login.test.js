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

async function page({ system = "shift", role = "operator", systems = [system], authOk = true, sessionOk = true } = {}) {
  const { JSDOM } = require("jsdom");
  const dom = new JSDOM("<div id='root'></div>", { url: "http://localhost/", pretendToBeVisual: true });
  const saved = new Map();
  let root, act;
  async function cleanup() {
    try { if (root) await act(async () => root.unmount()); }
    finally {
      dom.window.close();
      for (const [key, descriptor] of saved) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else delete globalThis[key];
      }
    }
  }
  try {
  for (const key of ["window", "document", "navigator", "HTMLElement", "Element", "Node", "Event"]) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key], writable: true });
  }
  saved.set("IS_REACT_ACT_ENVIRONMENT", Object.getOwnPropertyDescriptor(globalThis, "IS_REACT_ACT_ENVIRONMENT"));
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const React = require("react");
  act = React.act;
  const { createRoot } = require("react-dom/client");
  const calls = [], state = [];
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(name) {
      if (name === "react") return React;
      if (name === "react/jsx-runtime" || name === "@mui/material") return require(name);
      if (name.endsWith("apiClient")) return {
        clearAuth() { calls.push(["clear"]); },
        setBearerToken(token) { calls.push(["store", token]); },
      };
      if (name.endsWith("browserSession")) return { publishSchoolLunchSessionGeneration(value) { calls.push(["publish", value]); } };
      throw new Error(name);
    },
    async fetch(url, options) {
      calls.push([url, options]);
      if (url.startsWith("/api/portal/")) return { ok: authOk, json: async () => ({ role, systems }) };
      assert.equal(url, "/school-lunch/api/backend/shared-auth/me");
      return { ok: sessionOk, json: async () => ({ session_generation: "school-test-generation" }) };
    },
    window: { location: { replace(url) { calls.push(["navigate", url]); } } },
  });
  root = createRoot(document.getElementById("root"));
  await act(async () => root.render(React.createElement(exports.default)));
  const select = document.querySelector("select"), input = document.querySelector("input");
  assert.equal(select.value, "shift");
  assert.deepEqual(Array.from(select.options, option => option.value), ["shift", "school-lunch"]);
  assert.ok(select.closest(".MuiInput-root"));
  assert.ok(input.closest(".MuiInput-root"));
  await act(async () => { select.value = system; select.dispatchEvent(new dom.window.Event("change", { bubbles: true })); });
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value").set.call(input, " signed-token ");
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  return {
    calls, state,
    cleanup,
    async readFile(file) {
      const fileInput = document.querySelector('input[type="file"]');
      assert.ok(fileInput);
      Object.defineProperty(fileInput, "files", { configurable: true, value: [file] });
      await act(async () => fileInput.dispatchEvent(new dom.window.Event("change", { bubbles: true })));
      assert.equal(fileInput.value, "");
      state[0] = input.value;
      state[2] = document.querySelector("select").disabled;
      state[3] = document.querySelector('[role="alert"]')?.textContent || "";
    },
    async submit() {
      try {
        await act(async () => document.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true })));
        state[0] = input.value;
        state[2] = document.querySelector("select").disabled;
        state[3] = document.querySelector('[role="alert"]')?.textContent || "";
      } finally {
        await cleanup();
      }
    },
  };
  } catch (error) {
    await cleanup();
    throw error;
  }
}

test("token file uses canonical authentication without a request during local read", async () => {
  const scenario = await page({ system: "school-lunch" });
  try {
    await scenario.readFile({ size: 100, text: async () => " file-token \n" });
    assert.equal(scenario.calls.length, 0);
    assert.equal(scenario.state[0], "file-token");
  } catch (error) { await scenario.cleanup(); throw error; }
  await scenario.submit();
  assert.equal(scenario.calls[1][1].headers.Authorization, "Bearer file-token");
  assert.equal(scenario.calls[2][1].headers.Authorization, "Bearer file-token");
  assert.deepEqual(scenario.calls[3], ["publish", "school-test-generation"]);
  assert.equal(scenario.calls.at(-1)[1], "/school-lunch/implementation-price-tables");
});

test("unreadable or oversized token files clear stale credentials without requests", async () => {
  for (const file of [{ size: 16385 }, { size: 1, text: async () => { throw new Error("unreadable"); } }]) {
    const scenario = await page();
    try {
      await scenario.readFile(file);
      assert.equal(scenario.state[0], "");
      assert.equal(scenario.state[2], false);
      assert.equal(scenario.calls.length, 0);
      assert.equal(scenario.state[3], "認証ファイルを読み込めませんでした。");
    } finally { await scenario.cleanup(); }
  }
});

for (const system of ["shift", "school-lunch"]) {
  test(`${system} authenticates with explicit target and canonical navigation`, async () => {
    const scenario = await page({ system });
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
      assert.deepEqual(calls[3], ["publish", "school-test-generation"]);
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
    const scenario = await page({ system: "school-lunch", ...invalid });
    await scenario.submit();
    assert.equal(scenario.calls.some(([name]) => name === "store" || name === "navigate"), false);
    assert.equal(scenario.state[0], "");
    assert.equal(scenario.state[2], false);
    assert.notEqual(scenario.state[3], "");
    if (invalid.sessionOk !== false) assert.equal(scenario.calls.length, 2);
  });
}
