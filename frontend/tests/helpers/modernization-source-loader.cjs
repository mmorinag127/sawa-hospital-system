const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function sourceLoader(frontend, packages, navigation, api) {
  const ts = packages.requirePackage('typescript');
  const cache = new Map(), probes = new Map();
  function filename(name, parent) {
    const base = path.resolve(path.dirname(parent), name);
    const found = [base, `${base}.ts`, `${base}.tsx`, `${base}.js`, path.join(base, 'index.ts')]
      .find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
    assert.ok(found, `Missing actual source ${base}`);
    assert.ok(found.startsWith(frontend + path.sep), 'Source must stay inside this frontend');
    return found;
  }
  function load(file, probe) {
    file = path.resolve(frontend, file);
    if (cache.has(file)) return cache.get(file).exports;
    const original = fs.readFileSync(file, 'utf8');
    const source = ts.createSourceFile(file, original, ts.ScriptTarget.Latest, true,
      file.endsWith('tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const edits = [];
    if (probe) {
      const component = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === probe.component);
      assert.ok(component?.body, `Actual component ${probe.component}`);
      const returned = component.body.statements.filter(ts.isReturnStatement).at(-1);
      assert.ok(returned, 'Observe the actual component, do not substitute its return or hooks');
      edits.push([returned.getStart(source), `__observe({${probe.bindings.join(',')}});\n`]);
      probes.set(file, probe);
    }
    let input = original;
    for (const [offset, text] of edits.sort((a, b) => b[0] - a[0])) input = input.slice(0, offset) + text + input.slice(offset);
    // Test-only introspection of real Query clients allows deterministic teardown.
    if (file.endsWith('/hooks/useCurrentUserRole.ts')) input += '\nexport { browserClients };\n';
    const code = ts.transpileModule(input, { fileName: file, compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const module = { exports: {} }; cache.set(file, module);
    function requireSource(name) {
      if (navigation.imports.has(name)) return navigation.imports.get(name);
      if (packages.shared.has(name)) return packages.shared.get(name);
      if (name.startsWith('.')) {
        const resolved = filename(name, file);
        const actual = load(resolved);
        // Transport is the only API double. Authentication guards remain production code.
        if (resolved.endsWith('/services/apiClient.ts')) return { ...actual, apiClient: api };
        return actual;
      }
      return packages.requirePackage(name);
    }
    vm.compileFunction(code, ['require', 'module', 'exports', '__observe'], { filename: file })(
      requireSource, module, module.exports, value => { if (probe) probe.current = value; });
    return module.exports;
  }
  async function close() {
    for (const [file, module] of cache) {
      if (!file.endsWith('/hooks/useCurrentUserRole.ts')) continue;
      for (const client of module.exports.browserClients.values()) { await client.cancelQueries(); client.clear(); }
      module.exports.browserClients.clear();
    }
    cache.clear(); probes.clear();
  }
  return { load, close };
}
module.exports = { sourceLoader };
