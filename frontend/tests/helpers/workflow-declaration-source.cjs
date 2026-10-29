const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function importedExpression(pageAst, name) {
  for (const node of pageAst.statements) {
    if (!ts.isImportDeclaration(node) || !ts.isStringLiteral(node.moduleSpecifier)) continue;
    const bindings = node.importClause?.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) continue;
    const binding = bindings.elements.find(item => item.name.text === name);
    if (!binding) continue;
    const target = path.resolve(__dirname, '../../src/pages/orders/[id]', node.moduleSpecifier.text) + '.ts';
    const ast = ts.createSourceFile(target, fs.readFileSync(target, 'utf8'), ts.ScriptTarget.Latest, true);
    const exportedName = binding.propertyName?.text ?? name;
    for (const statement of ast.statements) {
      if (!ts.isVariableStatement(statement) || !statement.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) continue;
      const declaration = statement.declarationList.declarations.find(item => ts.isIdentifier(item.name) && item.name.text === exportedName);
      if (declaration?.initializer) return declaration.initializer.getText(ast);
    }
    throw new Error(`Imported workflow contract is not exported: ${name}`);
  }
  return undefined;
}

module.exports = { importedExpression };
