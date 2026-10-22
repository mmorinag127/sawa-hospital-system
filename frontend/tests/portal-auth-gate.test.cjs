const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { JSDOM } = require("jsdom");

function load(file, mocks, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../src/pages", file), "utf8");
  const context = { exports: {}, process: {env: {}}, AbortController, URL, URLSearchParams,
    window: global.window, document: global.document, ...globals,
    require(name) {
      if (name in mocks) return {__esModule: true, ...mocks[name]};
      if (name.startsWith("react")) return require(name);
      if (name === "next/app") return require(name);
      if (name === "../services/stagingDataset") return require("./helpers/staging-dataset-module.cjs")(global.window);
      if (name.endsWith(".css")) return {};
      throw new Error("Unexpected import " + name);
    } };
  vm.runInNewContext(ts.transpileModule(source, {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  }}).outputText, context);
  return context.exports.default;
}

async function dom(run) {
  const page = new JSDOM("<div id='root'></div>", {url: "https://portal.example.invalid/login"});
  const oldWindow = global.window, oldDocument = global.document;
  global.window = page.window; global.document = page.window.document; global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.getElementById("root"); const root = createRoot(container);
  try { await run(root, container); } finally {
    await act(async () => root.unmount()); page.window.close();
    global.window = oldWindow; global.document = oldDocument; delete global.IS_REACT_ACT_ENVIRONMENT;
  }
}

for (const outcome of ["success", "forbidden", "unavailable"]) test(`protected page waits for server me: ${outcome}`, async () => dom(async (root, container) => {
  let resolve, reject, mounts = 0;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const Shell = ({children}) => children;
  const App = load("_app.tsx", {
    "next/router": {useRouter: () => ({isReady: true, pathname: "/orders", asPath: "/orders"})},
    "../services/apiClient": {getStoredAuthHeader: () => "", establishServerSession: () => pending},
    "../components/PageTemplate": {default: Shell}, "../components/UnifiedShell": {default: Shell},
    "../components/NavigationBoundary": {NavigationBoundary: Shell},
    "../services/browserSession": {watchBrowserLogout: () => () => {}},
    "../services/loginDestination": {loginUrlFor: () => "/login"},
  });
  function Business() { React.useEffect(() => { mounts++; }, []); return React.createElement("p", null, "business"); }
  await act(async () => root.render(React.createElement(App, {Component: Business, pageProps: {}})));
  assert.equal(mounts, 0); assert.match(container.textContent, /ログインを確認/);
  await act(async () => outcome === "success" ? resolve({}) : reject({status: outcome === "forbidden" ? 403 : 503}));
  assert.equal(mounts, outcome === "success" ? 1 : 0);
  assert.match(container.textContent, outcome === "success" ? /business/ : outcome === "forbidden" ? /利用権限/ : /認証サービス/);
}));

test("login distinguishes config loading from server failure without obsolete client ID advice", async () => dom(async (root, container) => {
  let resolve;
  const Login = load("login.tsx", {
    "next/link": {default: ({children}) => children}, "../components/PublicNav": {default: () => null},
    "../services/loginDestination": {resolveLoginDestination: () => "/"},
  }, {fetch: () => new Promise(done => { resolve = done; })});
  await act(async () => root.render(React.createElement(Login)));
  assert.match(container.textContent, /ログイン設定を読み込み中/);
  await act(async () => resolve({ok: false}));
  assert.match(container.textContent, /ログイン設定の取得に失敗/);
  assert.doesNotMatch(container.textContent, /NEXT_PUBLIC_GOOGLE_CLIENT_ID|Google Client ID/);
}));
