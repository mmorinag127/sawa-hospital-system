// Synthetic jsdom/transport tests are not browser, authentication, OCR or provider proof.
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { modules, navigation } = require('./modernization-module-resolver.cjs');
const { sourceLoader } = require('./modernization-source-loader.cjs');
const frontend = path.resolve(__dirname, '../..');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function transport() {
  const calls = [], handlers = new Map();
  const api = {};
  for (const method of ['get', 'put', 'post', 'delete']) api[method] = (url, body, config) => {
    const call = { method, url, body: method === 'get' ? undefined : body, config: method === 'get' ? body : config };
    calls.push(call);
    const handler = handlers.get(`${method} ${url}`);
    if (!handler) return Promise.reject(new Error(`Unhandled mocked transport ${method} ${url}`));
    try { return Promise.resolve(handler(call)); } catch (reason) { return Promise.reject(reason); }
  };
  return { api, calls, on(method, url, handler) { handlers.set(`${method} ${url}`, handler); },
    onWorkflowInspection(url, handler) {
      assert.ok(url.includes('include_order_header=true'));
      const orderUrl = url.slice(0, url.indexOf('/workflow-v2/inspection'));
      handlers.set(`get ${url}`, async call => {
        const response = await handler(call);
        // Compose the new single-response fixture from the same explicitly
        // authored header input; this is not another request by the page.
        const headerFixture = handlers.get(`get ${orderUrl}`);
        assert.equal(typeof headerFixture, 'function', `header fixture ${orderUrl}`);
        const header = await headerFixture({method: 'get', url: orderUrl, config: call.config});
        return {...response, data: {...response.data, order_header: header.data}};
      });
    },
  };
}

async function mount(file, { api, props = {}, probe, route, routerState, component } = {}) {
  const requirePackage = createRequire(path.join(frontend, 'package.json'));
  const { JSDOM } = requirePackage('jsdom');
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: `http://localhost${route || '/'}`, pretendToBeVisual: true });
  dom.window.history.replaceState({ __N: true, as: route || '/', url: route || '/' }, '');
  const descriptors = new Map(), timers = new Set(), intervals = new Set(), popups = [];
  const downloads = [], blobs = [], revoked = [];
  function assign(object, key, value) {
    let saved = descriptors.get(object); if (!saved) descriptors.set(object, saved = new Map());
    if (!saved.has(key)) saved.set(key, Object.getOwnPropertyDescriptor(object, key));
    Object.defineProperty(object, key, { configurable: true, writable: true, value });
  }
  for (const key of ['window', 'document', 'navigator', 'Node', 'Element', 'HTMLElement', 'HTMLInputElement',
    'HTMLSelectElement', 'DocumentFragment', 'MutationObserver', 'Event', 'MouseEvent', 'File', 'FormData',
    'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
    const value = ['getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame'].includes(key)
      ? dom.window[key].bind(dom.window) : dom.window[key];
    assign(global, key, value);
  }
  assign(global, 'IS_REACT_ACT_ENVIRONMENT', true);
  const nativeTimeout = global.setTimeout, nativeClear = global.clearTimeout;
  const nativeInterval = global.setInterval, nativeClearInterval = global.clearInterval;
  assign(global, 'setTimeout', (fn, delay, ...args) => {
    const timer = nativeTimeout(() => { timers.delete(timer); fn(...args); }, delay); timers.add(timer); return timer;
  });
  assign(global, 'setInterval', (fn, delay, ...args) => { const timer = nativeInterval(fn, delay, ...args); intervals.add(timer); return timer; });
  assign(URL, 'createObjectURL', blob => { blobs.push(blob); return `blob:synthetic-${blobs.length}`; });
  assign(URL, 'revokeObjectURL', url => revoked.push(url));
  assign(dom.window.HTMLAnchorElement.prototype, 'click', function () { downloads.push({ href: this.href, name: this.download }); });
  assign(dom.window, 'confirm', () => true);
  assign(dom.window, 'open', () => {
    const popup = new JSDOM('<!doctype html><p>initial</p>', { url: 'about:blank' });
    const win = popup.window, writes = [];
    const write = win.document.write.bind(win.document);
    assign(win.document, 'write', text => { writes.push(text); write(text); });
    assign(win, 'closed', false);
    let focuses = 0; assign(win, 'focus', () => { ++focuses; });
    popups.push({ win, writes, get focuses() { return focuses; },
      close() { win.closed = true; win.dispatchEvent(new win.Event('pagehide')); },
      navigate() { popup.reconfigure({ url: 'https://example.invalid/left' }); win.dispatchEvent(new win.Event('beforeunload')); },
      dispose: () => popup.window.close() });
    return win;
  });
  dom.window.sessionStorage.setItem('auth_header', 'Bearer synthetic-component-only');
  dom.window.sessionStorage.setItem('sawa_auth_cache_generation', 'synthetic-session-1');
  let React, act, nav, loader, root;
  let Component, Boundary, unmounted = false;
  const element = values => React.createElement(Boundary, null, React.createElement(Component, values));
  const settle = async (action = () => {}) => { await act(async () => { await action(); await new Promise(resolve => nativeTimeout(resolve, 0)); }); };
  async function waitFor(predicate, label = 'owned UI predicate', timeout = 10000) {
    const deadline = Date.now() + timeout;
    while (true) {
      if (predicate()) return;
      assert.ok(Date.now() < deadline, `${label} did not become ready within ${timeout}ms: ${dom.window.document.body.textContent}`);
      await settle();
    }
  }
  async function unmount() { if (!unmounted && root) { unmounted = true; await act(async () => root.unmount()); } }
  async function close() {
    try { await unmount(); await loader?.close(); }
    finally {
      for (const popup of popups) popup.dispose();
      dom.window.close(); for (const timer of timers) nativeClear(timer); for (const timer of intervals) nativeClearInterval(timer);
      for (const [object, saved] of [...descriptors].reverse()) for (const [key, descriptor] of saved) {
        if (descriptor) Object.defineProperty(object, key, descriptor); else delete object[key];
      }
    }
  }
  try {
    const packages = await modules(frontend); React = packages.shared.get('react'); act = React.act;
    nav = navigation(React, route);
    if (routerState) Object.assign(nav.router, routerState);
    loader = sourceLoader(frontend, packages, nav, api);
    root = requirePackage('react-dom/client').createRoot(document.getElementById('root'));
    Boundary = loader.load('src/components/NavigationBoundary.tsx').NavigationBoundary;
    Component = component ? component(loader, React) : loader.load(file, probe).default;
    await settle(() => root.render(element(props)));
  }
  catch (reason) { await close(); throw reason; }
  return { React, act, loader, nav, probe, requirePackage, downloads, blobs, revoked, popups, document: dom.window.document,
    settle, waitFor, unmount, close,
    async render(nextProps = props) { await settle(() => root.render(element(nextProps))); },
    async click(element) { assert.ok(element); await settle(() => element.click()); },
    button(text) { const matches = [...document.querySelectorAll('button')].filter(node => node.textContent.trim() === text); assert.equal(matches.length, 1, `button ${text}`); return matches[0]; },
    async change(element, value) {
      assert.ok(element); const prototype = element.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
      await settle(() => { Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
        element.dispatchEvent(new window.Event('input', { bubbles: true })); element.dispatchEvent(new window.Event('change', { bubbles: true })); });
    },
    async select(name, value) {
      const input = document.querySelector(`input[name="${name}"]`); assert.ok(input);
      const trigger = input.parentElement.querySelector('[role="combobox"]'); assert.ok(trigger);
      await settle(() => trigger.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true })));
      const option = [...document.querySelectorAll('[role="option"]')].find(node => node.dataset.value === value); assert.ok(option);
      await settle(() => option.click());
    },
    async session(loggedIn = true) {
      await settle(() => { const actual = loader.load('src/services/browserSession.ts');
        if (loggedIn) actual.markAuthCacheChanged(); else actual.clearBrowserSession(); });
    },
  };
}
module.exports = { mount, transport, deferred, frontend };
