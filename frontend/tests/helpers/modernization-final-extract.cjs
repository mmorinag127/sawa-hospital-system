const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const assert = require('node:assert/strict');

// Extract only an actual closure's AST; dependencies are explicit test bindings.
function extract(file, name, bindings = {}) {
  const filename = path.resolve(__dirname, '../..', file);
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found = [];
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) found.push(node.initializer.getText(source));
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found.push(node.getText(source));
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.equal(found.length, 1, `Unique actual closure ${name}`);
  const definition = found[0].startsWith('function ') ? `${found[0]};return ${name};` : `return (${found[0]});`;
  const code = ts.transpileModule(definition, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return new Function(...Object.keys(bindings), code)(...Object.values(bindings));
}
module.exports = { extract };
