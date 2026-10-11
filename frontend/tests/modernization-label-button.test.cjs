const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const file = 'frontend/src/pages/facility-master.tsx';
const current = fs.readFileSync(path.join(root, file), 'utf8');
const base = execFileSync('git', ['show', '684e11d:' + file], { cwd: root, encoding: 'utf8' });

test('only explicit setting button tag changes; actual handler requires explicit invocation', () => {
  assert.equal(current, base.replace('<button\n                        type="button"', '<Button\n                        type="button"')
    .replace('区分表示なしを明示設定\n                      </button>', '区分表示なしを明示設定\n                      </Button>'));
  const source = ts.createSourceFile(file, current, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  assert.equal(source.parseDiagnostics.length, 0);
  let button;
  const visit = node => {
    if (ts.isJsxElement(node) && node.children.some(child => ts.isJsxText(child) && child.text.includes('区分表示なしを明示設定'))) button = node;
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.ok(button);
  assert.equal(button.openingElement.tagName.getText(source), 'Button');
  const attrs = Object.fromEntries(button.openingElement.attributes.properties.map(attr => [attr.name.text, attr.initializer]));
  assert.equal(attrs.type.text, 'button');
  assert.equal(attrs.disabled.expression.getText(source), '!isEditing');
  const calls = [];
  const handler = vm.runInNewContext(ts.transpileModule('(' + attrs.onClick.expression.getText(source) + ')', {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, { updateSelectedFacilityPatch: value => calls.push(value) });
  assert.equal(calls.length, 0);
  handler();
  assert.equal(JSON.stringify(calls), '[{"daily_label_comparable_diet_types":[]}]');
  assert.equal((current.match(/<(input|select|textarea|button)\b/g) || []).length, 0);
});
