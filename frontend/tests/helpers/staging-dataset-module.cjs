const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
module.exports = window => {
  const source = fs.readFileSync(path.join(__dirname, "../../src/services/stagingDataset.ts"), "utf8");
  const context = { exports: {}, window };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
  return context.exports;
};
