const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const file = path.resolve(__dirname, '../src/pages/facility-master.tsx');
const text = fs.readFileSync(file, 'utf8');
const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ['readDailyLabelDietTypes', 'toggledDailyLabelDietTypes'];
const statements = names.map(name => {
  const statement = source.statements.find(node => ts.isVariableStatement(node)
    && node.declarationList.declarations.some(decl => decl.name.getText(source) === name));
  assert.ok(statement, name);
  return statement.getText(source);
});
const code = ts.transpileModule(statements.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const helpers = vm.runInNewContext(`${code}\n({readDailyLabelDietTypes,toggledDailyLabelDietTypes})`);
test('missing/null/invalid is distinct from explicit empty', () => {
  for (const value of [undefined, null, 0, {}, 'staff', [null], ['']]) {
    assert.equal(helpers.readDailyLabelDietTypes({ daily_label_comparable_diet_types: value }), null);
    assert.equal(helpers.toggledDailyLabelDietTypes({ daily_label_comparable_diet_types: value }, 'staff'), null);
  }
  assert.deepEqual(Array.from(helpers.readDailyLabelDietTypes({ daily_label_comparable_diet_types: [] })), []);
});
test('toggle preserves unknown authored values and does not mutate source', () => {
  const facility = { daily_label_comparable_diet_types: ['future-authored-value', 'staff'] };
  assert.deepEqual(Array.from(helpers.toggledDailyLabelDietTypes(facility, 'staff')), ['future-authored-value']);
  assert.deepEqual(Array.from(helpers.toggledDailyLabelDietTypes(facility, 'soft')), ['future-authored-value', 'staff', 'soft']);
  assert.deepEqual(facility.daily_label_comparable_diet_types, ['future-authored-value', 'staff']);
  assert.doesNotMatch(text, /未選択の場合はシステム既定値を使います/);
});
test('raw whitespace and unknown values survive unrelated explicit toggles', () => {
  const facility = { daily_label_comparable_diet_types: [' staff ', ' unknown '] };
  assert.deepEqual(Array.from(helpers.toggledDailyLabelDietTypes(facility, 'soft')), [' staff ', ' unknown ', 'soft']);
  assert.deepEqual(facility.daily_label_comparable_diet_types, [' staff ', ' unknown ']);
  assert.deepEqual(Array.from(helpers.toggledDailyLabelDietTypes(facility, 'staff')), [' unknown ']);
  assert.match(text, /value\.trim\(\) === option\.value/);
  assert.match(text, /dietLabel\(dietType\.trim\(\)\)/);
  assert.match(text, /onClick=\{\(\) => updateSelectedFacilityPatch\(\{ daily_label_comparable_diet_types: \[\] \}\)\}/);
});
