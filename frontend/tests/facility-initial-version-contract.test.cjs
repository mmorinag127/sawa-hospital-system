const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/services/facilityData.ts'), 'utf8');
const context = { exports: {}, require: name => {
  assert.equal(name, './apiClient');
  return { apiClient: {} };
} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, context);
const { requireFacilityVersions } = context.exports;
const digest = value => crypto.createHash('sha256').update(JSON.stringify(value, Object.keys(value).sort())).digest('hex');
const facility = { facility_id: 'FAC00015', name: 'fixture' };
const proof = { source_sha256: 'a'.repeat(64), configuration_sha256: digest(facility) };
const initial = { version_id: 'initial-' + digest(proof), valid_from: null, valid_to: null, initial_snapshot: proof, facility };
function response(version = initial) {
  return structuredClone({ source: 'canonical_json', revision: 'revision', facility_id: 'FAC00015', reference: facility,
    reference_status: 'unversioned_reference_only', versions: [version] });
}
test('initial saved snapshot has no invented start date and is not mutated', () => {
  for (const end of [null, '2026-09-30']) {
    const data = response({ ...initial, valid_to: end });
    const before = structuredClone(data);
    assert.equal(requireFacilityVersions(data, 'FAC00015'), 'revision');
    assert.deepEqual(data, before);
  }
});
test('normal version requires canonical dates, including leap-day validity', () => {
  const normal = { version_id: 'normal', valid_from: '2024-02-29', valid_to: null, facility };
  assert.equal(requireFacilityVersions(response(normal), 'FAC00015'), 'revision');
  for (const date of [null, '', '2026-02-29', '2026-09-31', '2026-9-13', '0000-01-01', '2026-09-13T00:00:00Z']) {
    assert.throws(() => requireFacilityVersions(response({ ...normal, valid_from: date }), 'FAC00015'));
  }
  for (const end of ['invalid', '2024-02-28', undefined]) {
    assert.throws(() => requireFacilityVersions(response({ ...normal, valid_to: end }), 'FAC00015'));
  }
});
test('null start requires the exact initial proof shape, never a missing or guessed proof', () => {
  const variants = [
    { ...initial, initial_snapshot: null }, { ...initial, initial_snapshot: [] },
    { ...initial, initial_snapshot: { source_sha256: proof.source_sha256 } },
    { ...initial, initial_snapshot: { ...proof, extra: true } },
    { ...initial, initial_snapshot: { ...proof, source_sha256: 'bad' } },
    { ...initial, initial_snapshot: { ...proof, configuration_sha256: null } },
    { ...initial, version_id: 'normal' }, { ...initial, valid_from: '2026-09-13' },
    { ...initial, valid_to: '2026-02-30' },
  ];
  const missingProof = { ...initial }; delete missingProof.initial_snapshot; variants.push(missingProof);
  const missingDate = { ...initial }; delete missingDate.valid_from; variants.push(missingDate);
  for (const version of variants) assert.throws(() => requireFacilityVersions(response(version), 'FAC00015'));
});
test('existing source, revision, facility, duplicate and own-save guards remain strict', () => {
  for (const mutate of [
    d => { delete d.source; }, d => { d.source = 'other'; }, d => { delete d.revision; },
    d => { d.revision = ''; }, d => { d.facility_id = 'other'; },
    d => { d.reference.facility_id = 'other'; }, d => { d.reference_status = 'other'; },
    d => { d.versions[0].facility.facility_id = 'other'; },
    d => { d.versions.push(structuredClone(d.versions[0])); },
    d => { d.version = { ...d.versions[0], valid_to: '2026-09-30' }; },
    d => { d.version = { ...d.versions[0], version_id: 'absent' }; },
  ]) {
    const data = response(); mutate(data);
    assert.throws(() => requireFacilityVersions(data, 'FAC00015'));
  }
  const data = response(); data.version = structuredClone(data.versions[0]);
  assert.equal(requireFacilityVersions(data, 'FAC00015'), 'revision');
});
