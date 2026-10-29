const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,
  '../src/services/assistantProgress.ts'), 'utf8'), {compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
}}).outputText, {exports: exportsObject});
const merge = exportsObject.mergeAssistantHistory;

test('bounded response buffer preserves unique order and accepts the exact current payload', () => {
  const previous = Array.from({length: 510}, (_, index) => ({id:index + 1, quantity:'001'}));
  const current = [{id:510, quantity:0}, {id:511, quantity:''}];
  const before = structuredClone(previous);
  const result = JSON.parse(JSON.stringify(merge(previous, current)));
  assert.equal(result.length, 500);
  assert.equal(result[0].id, 12);
  assert.deepEqual(result.slice(-2), current);
  assert.deepEqual(previous, before);
  assert.equal(new Set(result.map(item => item.id)).size, 500);
});

test('empty deltas do not invent or erase recorded content', () => {
  const before = [{id:'7', unknown:{quantity:0}}, {id:8, quantity:null}];
  assert.deepEqual(JSON.parse(JSON.stringify(merge(before, []))), before);
  assert.equal(merge(undefined, undefined).length, 0);
});
