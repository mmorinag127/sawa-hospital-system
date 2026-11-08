const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/pages/orders/[id]/workflow-v2.tsx'), 'utf8');
const ast = ts.createSourceFile('workflow.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function value(name, bindings) {
  let expression;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === name) expression = node.initializer.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  const exports = {};
  const code = ts.transpileModule(`exports.value = ${expression};`, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  vm.compileFunction(code, ['exports', ...Object.keys(bindings)])(exports, ...Object.values(bindings));
  return exports.value;
}
test('mismatched saved version requires explicit context confirmation, not missing-template fallback', () => {
  const workflow = {facility_id:'FAC00014', week_start:'2026-11-15', week_end:'2026-11-21'};
  for (const state of ['uploaded', 'facility_template_unresolved', 'template_version_mismatch']) {
    assert.equal(value('workflowContextConfirmed', {workflow:{...workflow,state}}), false);
  }
  assert.equal(value('workflowContextConfirmed', {workflow:{...workflow,state:'context_confirmed'}}), true);
});
test('failed template read is never classified as unregistered', () => {
  const contextForm = {facility_id:'F'};
  const facilityTemplateStatus = {facilityId:'F', loading:false, templateId:'', error:'read failed'};
  assert.equal(value('facilityTemplateMissing', {contextForm,facilityTemplateStatus}), false);
  assert.equal(value('facilityTemplateMissing', {contextForm,facilityTemplateStatus:{...facilityTemplateStatus,error:''}}), true);
});
test('upside-down option is sent through the guarded quad save command', () => {
  assert.match(source, /rotation_degrees:quadRotation/);
  assert.match(source, /FAXを上下反転（180度回転）/);
  assert.match(source, /savedRotation === 180 \? \[\.\.\.savedQuad.slice\(2\), \.\.\.savedQuad.slice\(0, 2\)\]/);
});
