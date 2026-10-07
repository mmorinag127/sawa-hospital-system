const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const test = require('node:test');
const { execFileSync } = require('node:child_process');
const ts = require('typescript');
const root = path.join(__dirname, '../..');
const file = 'frontend/src/pages/facility-master.tsx';
const base = execFileSync('git', ['show', 'cd14d8bd36fbddd63fb70cbb1c4088be544cd6b1:' + file], { cwd: root, encoding: 'utf8' });
const current = fs.readFileSync(path.join(root, file), 'utf8');
const parse = text => ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
test('non-rendered AST including handlers, effects, state and serialization is unchanged', () => {
  const collect = text => {
    const source = parse(text);
    assert.equal(source.parseDiagnostics.length, 0);
    const result = [];
    const visit = node => {
      if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isImportDeclaration(node)) return;
      if (ts.isJsxExpression(node)) { if (node.expression) visit(node.expression); return; }
      if (ts.isFunctionDeclaration(node) && node.name?.text === 'FacilityMasterPage') { ts.forEachChild(node, visit); return; }
      if (ts.isVariableStatement(node) || ts.isExpressionStatement(node) || ts.isFunctionDeclaration(node)) {
        let jsx = false;
        const scan = n => { if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n)) jsx = true; ts.forEachChild(n, scan); };
        scan(node);
        if (!jsx) { result.push(printer.printNode(ts.EmitHint.Unspecified, node, source)); return; }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    return result;
  };
  assert.deepEqual(collect(current), collect(base));
});
test('every ordinary control retains original handlers and raw value attributes', () => {
  const controls = text => {
    const source = parse(text), result = [];
    const visit = node => {
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const names = { Button: 'button', Input: 'input', NativeSelect: 'select', Checkbox: 'input' };
        const name = node.tagName.getText(source), native = names[name] || name;
        if (['input', 'select', 'textarea', 'button'].includes(native)) {
          const attributes = node.attributes.properties.map(a => a.getText(source));
          const multiline = attributes.includes('multiline');
          const attrs = attributes.filter(a => a !== 'multiline' && a !== 'type="submit"' && !a.startsWith('inputProps={{ "aria-label":')).map(a => a.startsWith('inputProps={{ wrap: "soft"') ? 'wrap="soft"' : a);
          if (name === 'Checkbox') attrs.unshift('type="checkbox"');
          result.push({ name: multiline ? 'textarea' : native, attributes: attrs });
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    return result;
  };
  assert.deepEqual(controls(current), controls(base));
  assert.equal((current.match(/<(input|select|textarea|button)\b/g) || []).length, 0);
});
