const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
const filename = 'frontend/src/pages/base-menus.tsx';
const source = fs.readFileSync(path.join(root, filename), 'utf8');
const base = '95d832efe803a0373249a8f6a53abcd185f64e88';

test('emitted JS including comments and editor types are unchanged', () => {
  const old = execFileSync('git', ['show', `${base}:${filename}`], { cwd: root, encoding: 'utf8' });
  const options = { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve, removeComments: false };
  const emit = text => ts.transpileModule(text, { fileName: 'base-menus.tsx', compilerOptions: options }).outputText;
  assert.equal(emit(source), emit(old));
  for (const name of ['BaseMenuItem', 'BaseMenuDraft', 'BaseMenuSnapshot', 'SelectedBaseMenu']) {
    const declaration = text => text.split('\n').find(line => line.startsWith(`type ${name} =`));
    assert.ok(declaration(old));
    assert.equal(declaration(source), declaration(old));
  }
});

test('three actual response sites use generated aliases with no DELETE', () => {
  for (const [method, alias] of [['get', 'BaseMenuListResponse'], ['post', 'BaseMenuReplaceResponse'], ['put', 'BaseMenuUpdateResponse']]) {
    assert.ok(source.includes(`apiClient.${method}<${alias}>`));
    assert.ok(source.includes(`components["schemas"]["${alias}"]`));
  }
  const contract = JSON.parse(fs.readFileSync(path.join(root, 'frontend/src/generated/base-menu-openapi.json'), 'utf8'));
  assert.deepEqual(Object.entries(contract.paths).flatMap(([p, ops]) => Object.keys(ops).map(m => `${m} ${p}`)).sort(), ['get /base-menus', 'post /base-menus', 'put /base-menus/{item_id}']);
  const item = contract.components.schemas.BaseMenuItemResponse;
  assert.equal(item.additionalProperties, true);
  assert.deepEqual(item.properties.condiments.items, {});
  assert.deepEqual(item.properties.qty_per_serving.anyOf, [{ type: 'number' }, { type: 'null' }]);
  assert.deepEqual(item.properties.slot_index.anyOf, [{ type: 'integer' }, { type: 'null' }]);
  assert.ok(item.required.includes('condiments'));
  assert.ok(item.required.includes('qty_per_serving'));
});
