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
const migration = '38e096557266cff1abe83902ee980ea2c2b91d26';
const guard = '826f668e24c3f5765b37d24d747753fe6db7bb0d';
const at = revision => execFileSync('git', ['show', `${revision}:${filename}`], { cwd: root, encoding: 'utf8' });

test('verified type-only migration preserves emitted JS/comments; current editor types remain raw', () => {
  for (const [ancestor, descendant] of [[base, migration], [migration, guard], [guard, 'HEAD']])
    execFileSync('git', ['merge-base', '--is-ancestor', ancestor, descendant], { cwd: root });
  const old = execFileSync('git', ['show', `${base}:${filename}`], { cwd: root, encoding: 'utf8' });
  const options = { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve, removeComments: false };
  const emit = text => ts.transpileModule(text, { fileName: 'base-menus.tsx', compilerOptions: options }).outputText;
  assert.equal(emit(at(migration)), emit(old));
  for (const name of ['BaseMenuItem', 'BaseMenuDraft', 'BaseMenuSnapshot', 'SelectedBaseMenu']) {
    const declaration = text => text.split('\n').find(line => line.startsWith(`type ${name} =`));
    assert.ok(declaration(old));
    assert.equal(declaration(source), declaration(old));
    assert.equal(declaration(at(migration)), declaration(old));
  }
});

test('current list identity, update target and reload adoption retain approved guards', () => {
  const declarations = text => {
    const ast = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    return ast.statements.filter(node =>
      (ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration =>
        ['toItems', 'toSnapshot', 'serialize'].includes(declaration.name.getText(ast)))) ||
      (ts.isFunctionDeclaration(node) && node.name?.text === 'Screen'))
      .map(node => node.getText(ast));
  };
  const approved = declarations(at(guard));
  assert.equal(approved.length, 4);
  assert.deepEqual(declarations(source), approved);
  assert.ok(source.includes('!item.id.trim() || ids.has(item.id)'));
  assert.ok(source.includes('!snapshot.items.some((saved) => saved.id === item.id)'));
  assert.ok(source.includes('targetId && !snapshot.items.some((item) => item.id === targetId)'));
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
