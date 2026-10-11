const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
const file = 'frontend/src/pages/orders/[id].tsx';
const source = fs.readFileSync(path.join(root, file), 'utf8');
const parse = text => ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declaration = (text, name) => {
  const ast = parse(text);
  assert.equal(ast.parseDiagnostics.length, 0);
  const node = ast.statements.find(item => ts.isFunctionDeclaration(item) && item.name.text === name);
  assert.ok(node);
  return ts.createPrinter().printNode(ts.EmitHint.Unspecified, node, ast);
};

test('old order route keeps exact original server redirect and only null renderer', () => {
  for (const revision of ['7ae61ee', '4d0e6319d3e84e392389755041a89a872ff97d29']) {
    const original = execFileSync('git', ['show', `${revision}:${file}`], { cwd: root, encoding: 'utf8' });
    assert.equal(declaration(source, 'getServerSideProps'), declaration(original, 'getServerSideProps'));
  }
  const ast = parse(source);
  assert.equal(ast.statements.length, 2, 'no retired handlers, imports or component code');
  const renderer = ast.statements[1];
  assert.ok(renderer.modifiers.some(modifier => modifier.kind === ts.SyntaxKind.DefaultKeyword));
  assert.equal(renderer.body.statements.length, 1);
  assert.equal(renderer.body.statements[0].expression.kind, ts.SyntaxKind.NullKeyword);
  assert.equal(source.split('\n').length - 1, 14);
});

test('actual old URL redirect preserves ID encoding, blank/missing behavior and temporary status', async () => {
  const context = { exports: {} };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  for (const [input, destination] of [
    [{ params: { id: 'ORD8a30e085' } }, '/orders/ORD8a30e085/workflow-v2'],
    [{ params: { id: ' ORD123 ' } }, '/orders/ORD123/workflow-v2'],
    [{ params: { id: 'a/b?c' } }, '/orders/a%2Fb%3Fc/workflow-v2'],
    [{ params: { id: '' } }, '/orders'], [{ params: {} }, '/orders'], [{}, '/orders'],
  ]) {
    assert.deepEqual(JSON.parse(JSON.stringify(await context.exports.getServerSideProps(input))), {
      redirect: { destination, permanent: false },
    });
    assert.equal(context.exports.default(), null);
  }
});
