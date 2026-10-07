const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), ts = require('typescript'), vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const file = 'src/pages/facilities/[id].tsx', base = 'cefd20adef1e75bba502df75e5601acb4630a39d';
const source = fs.readFileSync(file, 'utf8'), old = execFileSync('git', ['show', base + ':frontend/' + file], { encoding: 'utf8' });
function controls(s, names) { const ast = ts.createSourceFile('f.tsx', s, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX), result = []; function visit(n) { if ((ts.isJsxSelfClosingElement(n) || ts.isJsxElement(n)) && names.includes((n.openingElement || n).tagName.getText(ast)))
    result.push({ node: n, opening: n.openingElement || n, ast }); ts.forEachChild(n, visit); } visit(ast); return result; }
const before = controls(old, ['input', 'button', 'select', 'textarea']), after = controls(source, ['Input', 'Button', 'Checkbox', 'NativeSelect']);
test('all ordinary controls retain original props/options/handlers and non-rendered source', () => {
    const imports = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX).statements.filter(ts.isImportDeclaration);
    const eventImport = imports.find(n => n.importClause?.isTypeOnly);
    assert.equal(eventImport.getText(), 'import type { ChangeEvent } from "react";');
    assert.equal(imports.filter(n => n.importClause?.isTypeOnly).length, 1);
    const prefix = s => s.slice(0, s.indexOf('  return (\n    <main')).replace(/^import .*from "@mui\/material";\n/m, '').replace(/^import type \{ ChangeEvent \} from "react";\n/m, '');
    assert.equal(prefix(source), prefix(old));
    assert.equal(after.length, before.length);
    const emit = s => ts.transpileModule('const value=' + s, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText;
    before.forEach((b, i) => {
        const a = after[i];
        const props = new Map(a.opening.attributes.properties.filter(ts.isJsxAttribute).map(p => [p.name.text, p]));
        for (const p of b.opening.attributes.properties) {
            const name = p.name.text;
            if (name === 'accept' || name === 'wrap') {
                const input = props.get('inputProps').initializer.expression;
                const value = input.properties.find(q => q.name.text === name).initializer;
                assert.equal(value.text, p.initializer.text);
                continue;
            }
            if (name === 'type' && p.initializer.text === 'checkbox') {
                assert.equal(a.opening.tagName.getText(a.ast), 'Checkbox');
                continue;
            }
            const now = props.get(name);
            assert.ok(now, name);
            const text = (x, ast) => ts.isJsxExpression(x.initializer) ? emit(x.initializer.expression.getText(ast)) : x.initializer?.getText(ast);
            assert.equal(text(now, a.ast), text(p, b.ast), name);
        }
        if (b.opening.tagName.getText(b.ast) === 'button' && !b.opening.attributes.properties.some(p => p.name.text === 'type'))
            assert.equal(props.get('type').initializer.text, 'submit');
        if (b.opening.tagName.getText(b.ast) === 'select')
            assert.equal(a.node.children.map(c => c.getText(a.ast)).join(''), b.node.children.map(c => c.getText(b.ast)).join(''));
    });
});
test('real installed MUI renders production control nodes and delivers native input events', async () => {
    assert.ok(process.env.FACILITY_CONTROLS_JSDOM_MODULE, 'explicit owned DOM module is required');
    const { JSDOM } = require(process.env.FACILITY_CONTROLS_JSDOM_MODULE), dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
    const saved = {};
    for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Event', 'MouseEvent', 'requestAnimationFrame', 'cancelAnimationFrame', 'IS_REACT_ACT_ENVIRONMENT']) {
        saved[key] = Object.getOwnPropertyDescriptor(global, key);
        const value = key === 'IS_REACT_ACT_ENVIRONMENT' ? true : ['requestAnimationFrame', 'cancelAnimationFrame'].includes(key) ? dom.window[key].bind(dom.window) : dom.window[key];
        Object.defineProperty(global, key, { configurable: true, value, writable: true });
    }
    const React = require('react'), { createRoot } = require('react-dom/client'), { act } = React, mui = require('@mui/material'), root = createRoot(document.getElementById('root'));
    const calls = [], ref = { current: 0 }, context = { React, ...mui, versionBusy: false, versionExisting: false, versionId: '', versionSnapshot: { versions: [{ version_id: 'raw-0', valid_from: '2026-01-01', valid_to: null }] }, versionFrom: '2026-01-01', versionTo: '', versionOpenEnd: false, versionText: '{ "zero":0, "unknown":null }', versionBlocked: false, effectiveDate: '', areasText: '[]', configText: 'raw', sourceWorkbookUploading: false, sourceWorkbookMonthId: '000', versionGeneration: ref, inputGeneration: { current: 0 }, previewGeneration: { current: 0 }, workbookDirty: { current: false }, configDraftRef: { current: null } };
    for (const name of ['selectVersion', 'setVersionId', 'setVersionFrom', 'setVersionTo', 'setVersionText', 'setVersionDirty', 'setVersionOpenEnd', 'setEffectiveDate', 'setDatedResult', 'setAreasText', 'markJsonDirty', 'setSourceWorkbookMonthId', 'setSpecialDirty', 'setConfigText', 'setMessage', 'handleConfigUpload', 'uploadSourceWorkbook', 'loadVersions', 'saveVersion', 'resolveVersionDate'])
        context[name] = value => calls.push([name, value]);
    context.editVersion = (setter, value) => setter(value);
    async function render(index) {
        const code = ts.transpileModule('exports.node=' + after[index].node.getText(after[index].ast), {
            compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
        }).outputText;
        const exports = {}, bindings = { ...context, exports };
        vm.compileFunction(code, Object.keys(bindings))(...Object.values(bindings));
        await act(async () => root.render(React.createElement(React.Fragment, { key: index }, exports.node)));
    }
    try {
        for (let i = 0; i < after.length; i++) {
            await render(i);
            const tag = after[i].opening.tagName.getText(after[i].ast);
            if (tag === 'Button') {
                assert.ok(document.querySelector('button'));
                continue;
            }
            const element = document.querySelector('select,input,textarea');
            assert.ok(element);
            if (tag === 'NativeSelect') {
                assert.deepEqual([...element.options].map(o => o.value), ['', 'raw-0']);
                await act(async () => { element.value = 'raw-0'; element.dispatchEvent(new Event('change', { bubbles: true })); });
                assert.deepEqual(calls.at(-1), ['selectVersion', 'raw-0']);
            }
            else if (tag === 'Checkbox') {
                await act(async () => element.click());
                assert.deepEqual(calls.at(-1), ['setVersionOpenEnd', true]);
                assert.equal(ref.current, 1);
            }
            else if (element.type === 'file') {
                const f = new window.File(['raw'], 'source.xlsx');
                const files = { 0: f, length: 1, item: i => i === 0 ? f : null };
                Object.defineProperty(element, 'files', { configurable: true, value: files });
                await act(async () => element.dispatchEvent(new Event('change', { bubbles: true })));
                assert.equal(calls.at(-1)[1], f);
                assert.ok(element.accept);
            }
            else {
                const proto = element.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                const raw = element.type === 'date' ? '2026-02-03' : ' 000 raw ';
                const start = calls.length;
                await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value').set.call(element, raw); element.dispatchEvent(new Event('input', { bubbles: true })); });
                assert.ok(calls.slice(start).some(c => c[1] === raw));
            }
        }
        await render(after.length - 1);
        assert.equal(document.querySelector('button').type, 'submit');
        await act(async () => document.querySelector('button').click());
        assert.deepEqual(calls.at(-1), ['setMessage', '通常施設設定を読み込めていないため、JSON設定を保存できません。']);
    }
    finally {
        await act(async () => root.unmount());
        dom.window.close();
        for (const key of Object.keys(saved)) {
            if (saved[key])
                Object.defineProperty(global, key, saved[key]);
            else
                delete global[key];
        }
    }
});
