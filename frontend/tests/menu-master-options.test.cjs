const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/components/menuMasterOptions.ts"), "utf8");
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const { menuMasterUnits, menuMasterTemperatures, menuMasterOptionLabel } = context.exports;

test("menu master select and list labels share unchanged stored codes", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(menuMasterUnits)), [
    { value: "g", label: "グラム (g)" }, { value: "cut", label: "切れ" }, { value: "count", label: "個" },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(menuMasterTemperatures)), [
    { value: "hot", label: "温" }, { value: "cold", label: "冷" },
  ]);
  for (const options of [menuMasterUnits, menuMasterTemperatures]) {
    for (const option of options) assert.equal(menuMasterOptionLabel(options, option.value), option.label);
  }
});

test("menu master labels preserve unknown codes instead of guessing known units", () => {
  for (const options of [menuMasterUnits, menuMasterTemperatures]) {
    assert.equal(menuMasterOptionLabel(options, null), "—");
    assert.equal(menuMasterOptionLabel(options, ""), "—");
    for (const value of ["UNKNOWN", "0", "G", " g "]) assert.equal(menuMasterOptionLabel(options, value), value);
  }
});
