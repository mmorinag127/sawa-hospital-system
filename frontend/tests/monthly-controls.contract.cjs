const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const vm = require('node:vm');
const ts = require('typescript');
const file = 'src/pages/menus/[monthId].tsx';
const base = '6611bade8de4bd97f4198282618cd852cdc8de35';
const old = execFileSync('git', ['show', base + ':frontend/' + file], { encoding: 'utf8' });
const source = fs.readFileSync(file, 'utf8');
function parse(s) { return ts.createSourceFile(file, s, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX); }
function controls(s, names) {
  const ast = parse(s), result = [];
  function visit(n) {
    if ((ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n)) && names.includes((n.openingElement || n).tagName.getText(ast))) result.push({ node: n, open: n.openingElement || n, ast });
    ts.forEachChild(n, visit);
  }
  visit(ast); return result;
}
const before = controls(old, ['button', 'input', 'select', 'textarea']);
const after = controls(source, ['Button', 'Input', 'NativeSelect', 'Checkbox']);
const emit = s => ts.transpileModule(s, { compilerOptions: { target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS } }).outputText;
function withoutRendering(s) {
  const ast = parse(s), edits = [];
  function visit(n) {
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) { edits.push([n.getStart(ast), n.end]); return; }
    ts.forEachChild(n, visit);
  }
  visit(ast);
  for (const [start, end] of edits.reverse()) s = s.slice(0, start) + 'RENDER' + s.slice(end);
  return s.replace(/^import .*from "@mui\/material";\n/m, '').replace(/^import type \{ ChangeEvent \} from "react";\n/m, '');
}
test('all controls and all non-rendered source preserve original contracts', () => {
  assert.equal(withoutRendering(source), withoutRendering(old));
  assert.equal(controls(source, ['button', 'input', 'select', 'textarea']).length, 0);
  assert.equal(after.length, before.length);
  for (let i = 0; i < before.length; i++) {
    const b = before[i], a = after[i];
    const props = new Map(a.open.attributes.properties.map(p => [p.name.text, p]));
    for (const p of b.open.attributes.properties) {
      if (p.name.text === 'type' && p.initializer.text === 'checkbox') { assert.equal(a.open.tagName.getText(a.ast), 'Checkbox'); continue; }
      let value = props.get(p.name.text)?.initializer;
      if (!value) {
        const nested = props.get('inputProps')?.initializer.expression.properties.find(n => n.name.text === p.name.text);
        assert.ok(nested, p.name.text);
        const expected = ts.isJsxExpression(p.initializer) ? p.initializer.expression.getText(b.ast) : p.initializer.getText(b.ast);
        assert.equal(emit('const value=' + nested.initializer.getText(a.ast)), emit('const value=' + expected)); continue;
      }
      const text = (v, ast) => ts.isJsxExpression(v) ? v.expression.getText(ast) : v.getText(ast);
      assert.equal(emit('const value=' + text(value, a.ast)), emit('const value=' + text(p.initializer, b.ast)), p.name.text);
    }
    if (b.open.tagName.getText(b.ast) === 'button' && !b.open.attributes.properties.some(p => p.name.text === 'type')) assert.equal(props.get('type').initializer.text, 'submit');
    if (b.open.tagName.getText(b.ast) === 'select') assert.equal(a.node.children.map(n => n.getText(a.ast)).join(''), b.node.children.map(n => n.getText(b.ast)).join(''));
  }
  console.log('Original ordinary controls:', before.length);
});
test('actual installed React/MUI forwards raw input, scope, file and checkbox callbacks', async () => {
  assert.ok(process.env.MONTHLY_JSDOM_MODULE);
  const { JSDOM } = require(process.env.MONTHLY_JSDOM_MODULE);
  const dom = new JSDOM('<div id="root"></div>', { pretendToBeVisual: true, url: 'http://localhost/' });
  const saved = {};
  for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Event', 'MouseEvent', 'requestAnimationFrame', 'cancelAnimationFrame', 'IS_REACT_ACT_ENVIRONMENT']) {
    saved[key] = Object.getOwnPropertyDescriptor(global, key);
    const value = key === 'IS_REACT_ACT_ENVIRONMENT' ? true : key.endsWith('AnimationFrame') ? dom.window[key].bind(dom.window) : dom.window[key];
    Object.defineProperty(global, key, { configurable: true, writable: true, value });
  }
  const React = require('react'), { act } = React, mui = require('@mui/material');
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const calls = [], context = { React, ...mui, uploadInputKey: 0, sheetName: '', scopeType: 'base', uploading: false, importDiets: [], choice: { value: 'regular' } };
  for (const name of ['setFile', 'setSheetName', 'setScopeType', 'setScopeValue', 'handleUpload', 'setImportDiets']) context[name] = value => calls.push([name, value]);
  const selected = after.filter(a => /setFile\(|setSheetName\(|setScopeType\(|setImportDiets\(/.test(a.node.getText(a.ast)));
  try {
    for (const a of selected) {
      const exports = {}, bindings = { ...context, exports };
      vm.compileFunction(emit('exports.node=' + a.node.getText(a.ast)), Object.keys(bindings))(...Object.values(bindings));
      await act(async () => root.render(React.createElement(React.Fragment, { key: after.indexOf(a) }, exports.node)));
      const element = document.querySelector('input,select'), start = calls.length;
      if (element.type === 'file') {
        const f = new window.File(['component bytes'], 'component.bin');
        Object.defineProperty(element, 'files', { value: { 0: f, length: 1 }, configurable: true });
        await act(async () => element.dispatchEvent(new Event('change', { bubbles: true })));
        assert.deepEqual(calls.slice(start), [['setFile', f]]);
      } else if (element.tagName === 'SELECT') {
        await act(async () => { element.value = 'tag'; element.dispatchEvent(new Event('change', { bubbles: true })); });
        assert.deepEqual(calls.slice(start), [['setScopeType', 'tag'], ['setScopeValue', '']]);
      } else if (element.type === 'checkbox') {
        await act(async () => element.click());
        assert.equal(calls.slice(start)[0][0], 'setImportDiets');
        assert.deepEqual(calls.slice(start)[0][1]([]), ['regular']);
      } else {
        await act(async () => { Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(element, ' 000 raw '); element.dispatchEvent(new Event('input', { bubbles: true })); });
        assert.deepEqual(calls.slice(start), [['setSheetName', ' 000 raw ']]);
      }
    }
    assert.equal(selected.length, 4);
    const upload = after.find(a => a.open.attributes.properties.some(p => p.name.text === 'onClick' && p.initializer.expression.getText(a.ast) === 'handleUpload'));
    for (const disabled of [true, false]) {
      const exports = {}, bindings = { ...context, uploading: disabled, exports };
      vm.compileFunction(emit('exports.node=' + upload.node.getText(upload.ast)), Object.keys(bindings))(...Object.values(bindings));
      await act(async () => root.render(exports.node));
      const button = document.querySelector('button'), start = calls.length;
      assert.equal(button.disabled, disabled); assert.equal(button.type, 'submit');
      await act(async () => button.click());
      assert.equal(calls.slice(start).length, disabled ? 0 : 1);
      if (!disabled) assert.equal(calls.at(-1)[0], 'handleUpload');
    }
  } finally {
    await act(async () => root.unmount()); dom.window.close();
    for (const key of Object.keys(saved)) { if (saved[key]) Object.defineProperty(global, key, saved[key]); else delete global[key]; }
  }
});
