const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/services/facilityData.ts'), 'utf8');
const context = { exports: {}, require: () => ({ apiClient: {} }) };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const { requireFaxTemplateOverride, requireFacilitySnapshot } = context.exports;
const snapshot = config => ({ facility: { id: 'F', name: 'own', areas: null }, config, resolved_config: {}, revision: 'own-revision', source: 'canonical_json', order_form_patterns: [] });
test('normal unknown keys and raw values retain exact JSON serialization', () => {
  const override = { units: 'unchanged', zero: 0, flag: false, raw: ' bad ', nested: { a: null }, table_box: [0, 1, 2, 3] };
  assert.equal(JSON.stringify(requireFaxTemplateOverride({ fax_template_override: override })), JSON.stringify(override));
  assert.equal(requireFacilitySnapshot(snapshot({ fax_template_override: override }), 'F'), 'own-revision');
});
test('absent and null remain distinct absence values', () => {
  assert.equal(requireFaxTemplateOverride({}), undefined);
  assert.equal(requireFaxTemplateOverride({ fax_template_override: null }), null);
  for (const config of [{}, { fax_template_override: null }]) assert.equal(requireFacilitySnapshot(snapshot(config), 'F'), 'own-revision');
});
test('invalid sibling shapes stop snapshot adoption before any PUT', () => {
  for (const value of ['raw', '', 0, 1, false, true, [], [1]]) {
    assert.throws(() => requireFaxTemplateOverride({ fax_template_override: value }), /must be an object/);
    assert.throws(() => requireFacilitySnapshot(snapshot({ fax_template_override: value }), 'F'), /must be an object/);
  }
});
test('original target and revision guards remain', () => {
  assert.throws(() => requireFacilitySnapshot(snapshot({}), 'other'));
  assert.throws(() => requireFacilitySnapshot({ ...snapshot({}), revision: '' }, 'F'));
});
test('missing or invalid outer config is never optional override absence', () => {
  for (const value of [undefined, null, '', 'raw', 0, 1, false, true, [], [1]]) {
    assert.throws(() => requireFaxTemplateOverride(value), /config must be an object/);
  }
});
function liveDeclaration(name) {
  const root = path.join(__dirname, '../..');
  const file = 'frontend/src/pages/orders/[id]/workflow-v2.tsx';
  const reference = '227cae9:' + file;
  execFileSync('git', ['merge-base', '--is-ancestor', '227cae9', 'HEAD'], { cwd: root });
  const base = execFileSync('git', ['show', reference], { cwd: root, encoding: 'utf8' });
  const declaration = text => {
    const parsed = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    assert.equal(parsed.parseDiagnostics.length, 0);
    let found;
    const visit = node => {
      if (ts.isVariableDeclaration(node) && node.name.getText(parsed) === name) found = node;
      ts.forEachChild(node, visit);
    };
    visit(parsed);
    assert.ok(found);
    return ts.createPrinter().printNode(ts.EmitHint.Unspecified, found, parsed);
  };
  assert.equal(declaration(fs.readFileSync(path.join(root, file), 'utf8')), declaration(base));
  return declaration(base);
}
test('live geometry handler retains exact PDF source receipt, ownership and revision CAS', () => {
  const save = liveDeclaration('saveQuadAndRerun');
  assert.ok(save.includes('receipt.workflow_revision'));
  assert.ok(save.includes('geometry_source_digest'));
  assert.ok(save.includes('workflowMutationConfig(captured)'));
  assert.ok(save.includes('ownsQuadInput(own)'));
});
test('live facility columns handler retains exact canonical facility and command ownership', () => {
  const save = liveDeclaration('saveFacilityTemplateColumns');
  assert.ok(save.includes('contextFacilityResolution.status !== "ready"'));
  assert.ok(save.includes('ownsPageInput(own)'));
  assert.ok(save.includes('executeWorkflowMutation("templateColumns"'));
});
