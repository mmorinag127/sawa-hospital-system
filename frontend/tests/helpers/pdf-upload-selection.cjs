// Real page, React and installed shared/MUI components; only external API/navigation are doubles.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const assert = require('node:assert/strict');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function mount(api) {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
  const saved = new Map();
  for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'DocumentFragment',
    'Event', 'MouseEvent', 'File', 'FormData', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame',
    'IS_REACT_ACT_ENVIRONMENT']) {
    saved.set(key, Object.getOwnPropertyDescriptor(global, key));
    const value = key === 'IS_REACT_ACT_ENVIRONMENT' ? true : typeof dom.window[key] === 'function'
      && ['getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame'].includes(key)
      ? dom.window[key].bind(dom.window) : dom.window[key];
    Object.defineProperty(global, key, { configurable: true, writable: true, value });
  }
  const React = require('react'), { act } = React;
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const filename = path.resolve(__dirname, '../../src/pages/pdf-upload.tsx');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true,
  } }).outputText;
  const module = { exports: {} };
  function load(name) {
    if (name === 'next/link') return { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) };
    if (name === '../components/TopNav') return { __esModule: true, default: () => null };
    if (name === '../services/apiClient') return { apiClient: api };
    if (name === '../services/browserSession') return { browserSessionKey: () => 'owned-component', subscribeBrowserSession: () => () => {} };
    return require(name);
  }
  try {
    vm.compileFunction(code, ['require', 'module', 'exports'], { filename })(load, module, module.exports);
    await act(async () => root.render(React.createElement(module.exports.default)));
  } catch (error) {
    await close();
    throw error;
  }
  async function close() {
    try { await act(async () => root.unmount()); }
    finally {
      dom.window.close();
      for (const [key, descriptor] of saved) {
        if (descriptor) Object.defineProperty(global, key, descriptor); else delete global[key];
      }
    }
  }
  async function change(element, value) {
    assert.ok(element);
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(element, value);
      element.dispatchEvent(new window.Event('input', { bubbles: true }));
      element.dispatchEvent(new window.Event('change', { bubbles: true }));
    });
  }
  async function select(name, value) {
    const input = document.querySelector(`input[name="${name}"]`);
    const trigger = input.parentElement.querySelector('[role="combobox"]');
    assert.ok(trigger);
    await act(async () => trigger.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true })));
    const option = [...document.querySelectorAll('[role="option"]')].find(node => node.dataset.value === value);
    assert.ok(option, `option ${value}`);
    await act(async () => option.click());
  }
  const submit = () => document.querySelector('form button[type="submit"]');
  return { act, close, change, select, submit, document: dom.window.document,
    async file() {
      const input = document.querySelector('input[type="file"]');
      // Deliberately not an OCR fixture or a valid scenario PDF. No bytes leave the mocked API.
      const file = new window.File(['component-only'], 'component.bin', { type: 'application/pdf' });
      Object.defineProperty(input, 'files', { configurable: true, value: [file] });
      await act(async () => input.dispatchEvent(new window.Event('change', { bubbles: true })));
    },
    async click(element) { assert.ok(element); await act(async () => element.click()); },
    async settle(action) { await act(async () => { action(); await Promise.resolve(); }); },
  };
}
module.exports = { mount, deferred };
