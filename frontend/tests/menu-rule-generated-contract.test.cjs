const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
const file = 'frontend/src/pages/menu-rules.tsx';
const source = fs.readFileSync(path.join(root, file), 'utf8');
const base = '9e94528ad972862bb5f3cf4f5c409cec360bf1e1';

test('type-only page changes preserve emitted JavaScript including comments', () => {
  const old = execFileSync('git', ['show', `${base}:${file}`], { cwd: root, encoding: 'utf8' });
  const options = { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve, removeComments: false };
  const emit = text => ts.transpileModule(text, { fileName: 'menu-rules.tsx', compilerOptions: options }).outputText;
  assert.equal(emit(source), emit(old));
  const editor = text => text.match(/^type MenuRule = .*$/m)[0];
  assert.equal(editor(source), editor(old));
});

test('all four response consumption sites use generated aliases', () => {
  for (const [method, alias] of [['get', 'RuleListResponse'], ['post', 'RuleSaveResponse'], ['put', 'RuleUpdateResponse'], ['delete', 'RuleDeleteResponse']]) {
    assert.ok(source.includes(`apiClient.${method}<${alias}>`));
  }
  for (const name of ['MenuRuleListResponse', 'MenuRuleSaveResponse', 'MenuRuleUpdateResponse', 'MenuRuleDeleteResponse']) {
    assert.ok(source.includes(`components["schemas"]["${name}"]`));
  }
});

test('response inventory and raw nullable fields remain source-backed', () => {
  const contract = JSON.parse(fs.readFileSync(path.join(root, 'frontend/src/generated/menu-rule-openapi.json'), 'utf8'));
  assert.deepEqual(Object.entries(contract.paths).flatMap(([p, ops]) => Object.keys(ops).map(m => `${m} ${p}`)).sort(), ['delete /menu-rules/{rule_id}', 'get /menu-rules', 'post /menu-rules', 'put /menu-rules/{rule_id}']);
  const schema = contract.components.schemas.MenuRuleResponse;
  assert.equal(schema.additionalProperties, true);
  assert.deepEqual(schema.properties.qty_per_serving.anyOf, [{ type: 'number' }, { type: 'null' }]);
  assert.deepEqual(schema.properties.priority.anyOf, [{ type: 'integer' }, { type: 'null' }]);
  assert.ok(schema.required.includes('qty_per_serving'));
  assert.ok(schema.required.includes('priority'));
});
