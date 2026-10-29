const assert = require('node:assert/strict');
const ts = require('typescript');

module.exports = function ownedDeclarations(source, names) {
  const ast = ts.createSourceFile('owned.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  assert.equal(ast.parseDiagnostics.length, 0);
  const found = new Map(), printer = ts.createPrinter();
  function visit(node) {
    const name = (ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(ast);
    if (names.includes(name)) {
      assert.ok(!found.has(name), `ambiguous owned declaration: ${name}`);
      found.set(name, printer.printNode(ts.EmitHint.Unspecified, node, ast));
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return names.map(name => {
    assert.ok(found.has(name), `missing owned declaration: ${name}`);
    return found.get(name);
  });
};
