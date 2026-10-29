// One-shot, reproducible mechanical extraction; no typecheck/build/test execution.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));
const file = path.join(root, 'frontend/src/pages/orders/[id]/workflow-v2.tsx');
const directory = path.join(root, 'frontend/src/features/orders');
const source = fs.readFileSync(file, 'utf8');
const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const contracts = new Set(['resolveCanonicalFacility', 'canonicalFacilityResolutionMessage',
  'isPositiveWorkflowRevision', 'sameRecordedValue', 'sameQuadOverrideSource', 'workflowRevisionTokenFromRead']);
const bags = new Set(['normalizeDietLabel', 'formatWorkflowQuantity', 'formatWorkflowBagType',
  'extractWorkflowBagRows', 'buildWorkflowBagSummaryRows', 'groupWorkflowBagRowsByDate',
  'isBagColumnHeader', 'formatOutputPreviewCell']);
const groups = new Map(['workflowTypes', 'workflowContracts', 'workflowBagView'].map(name => [name, []]));
const symbols = new Map();
const external = new Map();
for (const node of ast.statements) {
  if (ts.isImportDeclaration(node) && node.importClause?.namedBindings
      && ts.isNamedImports(node.importClause.namedBindings)) {
    for (const entry of node.importClause.namedBindings.elements) {
      const from = node.moduleSpecifier.text;
      const relative = from.startsWith('.') ? path.relative(directory, path.resolve(path.dirname(file), from)) : null;
      external.set(entry.name.text, { original: entry.propertyName?.text || entry.name.text,
        from: relative === null ? from : (relative.startsWith('.') ? relative : './' + relative),
        type: entry.isTypeOnly || node.importClause.isTypeOnly });
    }
  }
  let name, group;
  if (ts.isTypeAliasDeclaration(node)) { name = node.name.text; group = 'workflowTypes'; }
  if (ts.isVariableStatement(node) && node.declarationList.declarations.length === 1) {
    name = node.declarationList.declarations[0].name.getText(ast);
    group = contracts.has(name) ? 'workflowContracts' : bags.has(name) ? 'workflowBagView' : null;
  }
  if (group) { groups.get(group).push({ node, name }); symbols.set(name, { group, type: ts.isTypeAliasDeclaration(node) }); }
}
if ([...contracts, ...bags].some(name => !symbols.has(name)) || groups.get('workflowTypes').length < 20) {
  throw new Error('Extraction anchors changed; no files were written');
}
for (const group of groups.keys()) {
  if (fs.existsSync(path.join(directory, group + '.ts'))) throw new Error('Destination already exists');
}
function references(nodes) {
  const result = new Set();
  function visit(node) {
    if (ts.isIdentifier(node)) {
      const parent = node.parent;
      if (!((ts.isPropertyAccessExpression(parent) && parent.name === node)
          || (ts.isPropertyAssignment(parent) && parent.name === node && !ts.isComputedPropertyName(parent.name)))) result.add(node.text);
    }
    ts.forEachChild(node, visit);
  }
  nodes.forEach(visit);return result;
}
function importsFor(nodes, own) {
  const imports = new Map();
  for (const name of references(nodes)) {
    const local = symbols.get(name), original = external.get(name);
    if (local?.group === own || (!local && !original)) continue;
    const from = local ? './' + local.group : original.from;
    const type = local ? local.type : original.type;
    const binding = original && !local && original.original !== name ? original.original + ' as ' + name : name;
    const key = (type ? 'type:' : 'value:') + from;
    if (!imports.has(key)) imports.set(key, { from, type, names: new Set() });
    imports.get(key).names.add(binding);
  }
  return [...imports.values()].map(item => 'import ' + (item.type ? 'type ' : '') + '{ '
    + [...item.names].sort().join(', ') + ' } from ' + JSON.stringify(item.from) + ';').join('\n');
}
const products = [];
const selected = new Set([...groups.values()].flat().map(item => item.node));
for (const [group, entries] of groups) {
  products.push([path.join(directory, group + '.ts'), importsFor(entries.map(item => item.node), group)
    + '\n\n' + entries.map(item => 'export ' + item.node.getText(ast)).join('\n\n') + '\n']);
}
let page = source;
for (const node of [...selected].sort((a, b) => b.getStart(ast) - a.getStart(ast))) {
  page = page.slice(0, node.getStart(ast)) + page.slice(node.getEnd());
}
const remaining = ast.statements.filter(node => !selected.has(node) && !ts.isImportDeclaration(node));
const needed = references(remaining);
const pageImports = [];
for (const [group, entries] of groups) {
  for (const type of [false, true]) {
    const names = entries.filter(item => needed.has(item.name) && symbols.get(item.name).type === type).map(item => item.name);
    if (names.length) pageImports.push('import ' + (type ? 'type ' : '') + '{ ' + names.join(', ')
      + ' } from "../../../features/orders/' + group + '";');
  }
}
page = pageImports.join('\n') + '\n' + page.replace('JobStatus, sawaTheme, type JobStatusRecord', 'JobStatus, sawaTheme');
page = page.replace(/\n{4,}/g, '\n\n\n');
for (const [destination, content] of products) fs.writeFileSync(destination, content);
fs.writeFileSync(file, page);
process.stdout.write(JSON.stringify({ moved_declarations: symbols.size,
  files: products.map(([destination]) => path.relative(root, destination)), source: path.relative(root, file) }) + '\n');
