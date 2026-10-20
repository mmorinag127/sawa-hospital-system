const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const ts = require('typescript');

const root = path.resolve(__dirname, '../..');
const base = 'ab0af71e03e146afc96f43f783d35c278887c7f5';
const marker = 'delivery-ordinary-control';
const expected = {
  "daily-delivery-notes": { button: 13, input: 5, select: 4, textarea: 0 },
  "weekly-weight-output": { button: 1, input: 0, select: 0, textarea: 0 },
};
const mapped = { button: 'Button', input: 'Input', select: 'NativeSelect', textarea: 'Input' };
const emit = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;
const parse = source => ts.createSourceFile('page.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

function controls(source, convertedOnly = false) {
  const ast = parse(source);
  assert.deepEqual(ast.parseDiagnostics, []);
  const result = [];
  function visit(node) {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const open = node.openingElement || node;
      const tag = open.tagName.getText(ast);
      if ([...Object.keys(mapped), ...Object.values(mapped), 'Checkbox'].includes(tag)) {
        if (Object.hasOwn(mapped, tag) || !convertedOnly || open.attributes.getText(ast).includes(marker)) {
          result.push({ ast, node, open, tag });
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return result;
}

function nonrender(source) {
  const ast = parse(source);
  const edits = [];
  function visit(node) {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) {
      edits.push([node.getStart(ast), node.end, 'RENDER']);
      return;
    }
    if (ts.isImportDeclaration(node)) {
      const moduleName = node.moduleSpecifier.text;
      if (moduleName === '@mui/material'
          || (node.importClause?.isTypeOnly && moduleName === 'react'
              && node.importClause.namedBindings.getText(ast) === '{ ChangeEvent }')) {
        edits.push([node.getStart(ast), node.end + 1, '']);
        return;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  for (const [start, end, replacement] of edits.sort((a, b) => b[0] - a[0])) {
    source = source.slice(0, start) + replacement + source.slice(end);
  }
  return source;
}

function properties(control, flatten) {
  const result = new Map();
  for (const prop of control.open.attributes.properties) {
    assert.ok(ts.isJsxAttribute(prop), 'unexpected spread must not hide native props');
    const key = prop.name.getText(control.ast);
    if (key === 'inputProps' && flatten) {
      const expression = prop.initializer.expression;
      assert.ok(ts.isObjectLiteralExpression(expression));
      for (const entry of expression.properties) {
        assert.ok(ts.isPropertyAssignment(entry));
        const name = ts.isStringLiteral(entry.name) ? entry.name.text : entry.name.getText(control.ast);
        assert.equal(result.has(name), false, 'duplicate mapped native prop');
        result.set(name, entry.initializer.getText(control.ast));
      }
    } else {
      const value = !prop.initializer ? 'true'
        : ts.isJsxExpression(prop.initializer) ? prop.initializer.expression.getText(control.ast)
          : prop.initializer.getText(control.ast);
      assert.equal(result.has(key), false);
      result.set(key, value);
    }
  }
  return result;
}

for (const [name, counts] of Object.entries(expected)) {
  const file = 'frontend/src/pages/' + name + '.tsx';
  const original = execFileSync('git', ['show', base + ':' + file], { cwd: root, encoding: 'utf8' });
  const migration = '5634429';
  const current = execFileSync('git', ['show', migration + ':' + file], { cwd: root, encoding: 'utf8' });
  test(name + ' current approved input/output ownership and receipt functions remain exact', () => {
    const approved = name === 'daily-delivery-notes' ? '227cae9' : '1bcb756';
    for (const [ancestor, descendant] of [[base, migration], [migration, approved], [approved, 'HEAD']])
      execFileSync('git', ['merge-base', '--is-ancestor', ancestor, descendant], { cwd: root });
    if (name === 'daily-delivery-notes') execFileSync('git', ['merge-base', '--is-ancestor', 'c5dd90a', approved], { cwd: root });
    assert.equal(fs.readFileSync(path.join(root, file), 'utf8'),
      execFileSync('git', ['show', approved + ':' + file], { cwd: root, encoding: 'utf8' }),
      'Full current load/download/input/message/auth/unmount and receipt handling remain exact');
  });

  test(name + ' historical UI migration: exact non-rendered source, hooks/auth/API/constants unchanged', () => {
    assert.equal(nonrender(current), nonrender(original));
    for (const tag of ['SawaProvider', 'FormProvider', 'FormTextField', 'Button']) {
      function unchanged(source) {
        const ast = parse(source), result = [];
        function visit(node) {
          if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
            const open = node.openingElement || node;
            if (open.tagName.getText(ast) === tag && !open.attributes.getText(ast).includes(marker)) {
              result.push(node.getText(ast));
            }
          }
          ts.forEachChild(node, visit);
        }
        visit(ast);
        return result;
      }
      assert.deepEqual(unchanged(current), unchanged(original), tag);
    }
  });

  test(name + ' historical UI migration: AST counts, all native props/options/handlers/default submit retained', () => {
    const before = controls(original).filter(control => Object.hasOwn(mapped, control.tag));
    const after = controls(current, true);
    const actual = { button: 0, input: 0, select: 0, textarea: 0 };
    for (const control of before) actual[control.tag]++;
    assert.deepEqual(actual, counts);
    assert.equal(after.length, before.length);
    assert.equal(after.some(control => Object.hasOwn(mapped, control.tag)), false);

    for (let index = 0; index < before.length; index++) {
      const oldControl = before[index];
      const newControl = after[index];
      const checkbox = oldControl.open.attributes.properties.some(prop => prop.name?.text === 'type' && prop.initializer?.text === 'checkbox');
      assert.equal(newControl.tag, checkbox ? 'Checkbox' : mapped[oldControl.tag]);
      const oldProps = properties(oldControl, false);
      const newProps = properties(newControl, true);
      assert.ok(newProps.get('className').includes(marker));
      if (oldProps.has('className')) {
        newProps.set('className', newProps.get('className').replace(' ' + marker, ''));
      } else {
        assert.equal(newProps.get('className'), '"' + marker + '"');
        newProps.delete('className');
      }
      if (checkbox) oldProps.delete('type');
      if (oldControl.tag === 'button' && !oldProps.has('type')) oldProps.set('type', '"submit"');
      if (oldControl.tag !== 'button' && !checkbox) {
        assert.equal(newProps.get('disableUnderline'), 'true');
        newProps.delete('disableUnderline');
      }
      if (oldControl.tag === 'textarea') {
        assert.equal(newProps.get('multiline'), 'true');
        newProps.delete('multiline');
      }
      assert.deepEqual([...newProps.keys()].sort(), [...oldProps.keys()].sort());
      for (const [key, value] of oldProps) {
        if (key.startsWith('on')) assert.equal(newProps.get(key), value, 'exact original handler expression');
        assert.equal(emit('const value = ' + newProps.get(key)), emit('const value = ' + value),
          name + ' control ' + index + ' ' + key);
      }
      if (ts.isJsxElement(oldControl.node)) {
        assert.equal(newControl.node.children.map(node => node.getText(newControl.ast)).join(''),
          oldControl.node.children.map(node => node.getText(oldControl.ast)).join(''));
      }
    }
  });

  test(name + ' newly global control selectors restricted to converted marker', () => {
    const oldGlobals = new Set(original.match(/:global\([^\n]+?\)/g) || []);
    const added = (current.match(/:global\([^\n]+?\)/g) || []).filter(selector => !oldGlobals.has(selector));
    assert.ok(added.length > 0);
    for (const selector of added) assert.ok(selector.includes('.' + marker), selector);
    const paired = name === 'daily-delivery-notes'
      ? ['.input', '.btn', '.btn.primary', '.btn.ghost', '.btn:disabled', '.override-actions .btn']
      : ['.week-chip', '.week-chip.active', '.week-chip span', '.week-chip small'];
    for (const selector of paired) {
      const begin = original.indexOf(selector + ' {');
      assert.ok(begin >= 0, selector);
      const body = original.slice(begin + selector.length + 2, original.indexOf('}', begin));
      const marked = selector === '.override-actions .btn'
        ? '.override-actions .' + marker + '.btn' : '.' + marker + selector;
      const pair = selector + ',\n' + (selector === '.override-actions .btn' ? '          ' : '        ')
        + ':global(' + marked + ') {';
      const currentBegin = current.indexOf(pair);
      assert.ok(currentBegin >= 0, pair);
      assert.equal(current.slice(currentBegin + pair.length, current.indexOf('}', currentBegin)), body);
      assert.equal(current.split(':global(' + marked + ') {').length - 1, 1);
    }
  });
}
