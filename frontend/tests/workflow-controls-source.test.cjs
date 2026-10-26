const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const cp = require("node:child_process");
const test = require("node:test");
const ts = require("typescript");
const vm = require("node:vm");

const base = "7d7e41d2085376013a835f4e6f5904c4031b0c1f";
const root = path.resolve(__dirname, "../..");
const filename = "frontend/src/pages/orders/[id]/workflow-v2.tsx";
const original = cp.execFileSync("git", ["show", base + ":" + filename], { cwd: root, encoding: "utf8" });
const migration = 'd55763365c3d2159dc976b130e0cc7a6de5f162f';
const current = cp.execFileSync('git', ['show', migration + ':' + filename], { cwd: root, encoding: 'utf8' });
test('current approved output owner/history/action AI/feedback/PDF refusal and OCR diagnostics remain exact', () => {
  assert.equal(require('node:crypto').createHash('sha256').update(fs.readFileSync(path.join(root, filename))).digest('hex'),
    'e59c5a481727e316e70bbf03fd592a72bae281a2621846cf9c760aa7700ac794',
    'Full current functions, protected grid, render callbacks, receipt and owner guards remain exact');
  const lineage = [base, migration, '4c33813', 'ded7f15', 'bdbc6de', '91d293c', '3c5a9ca', 'd26febd', 'd4b7d36', '227cae9', '2a180d57', 'HEAD'];
  for (let i = 1; i < lineage.length; i++)
    cp.execFileSync('git', ['merge-base', '--is-ancestor', lineage[i - 1], lineage[i]], { cwd: root });
});
const marker = "workflow-ordinary-control";
const names = new Map([["Button", "button"], ["Input", "input"], ["NativeSelect", "select"]]);
const expected = { button: 66, select: 16, input: 7, textarea: 2 };

function parse(source) {
  return ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

function collect(ast, predicate) {
  const result = [];
  function visit(node) {
    if (predicate(node)) result.push(node);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return result;
}

function opening(node) {
  return ts.isJsxElement(node) ? node.openingElement : node;
}

function attributes(node) {
  return opening(node).attributes.properties;
}

function has(node, name) {
  return attributes(node).some(prop => ts.isJsxAttribute(prop) && prop.name.getText() === name);
}

function controls(ast) {
  return collect(ast, node => {
    if (!ts.isJsxElement(node) && !ts.isJsxSelfClosingElement(node)) return false;
    return ["button", "input", "select", "textarea", ...names.keys()].includes(opening(node).tagName.getText())
      && !has(node, "data-sheet-row");
  });
}

function nonrender(source) {
  const ast = parse(source);
  const ranges = [];
  function visit(node) {
    if (ts.isImportDeclaration(node) && ["@mui/material", "@mui/material/styles", "@sawa/ui"].includes(node.moduleSpecifier.text)) {
      ranges.push([node.getStart(ast), node.end + (source[node.end] === "\n" ? 1 : 0), ""]);
      return;
    }
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) {
      ranges.push([node.getStart(ast), node.end, "RENDER"]);
      return;
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  for (const [start, end, replacement] of ranges.reverse()) {
    source = source.slice(0, start) + replacement + source.slice(end);
  }
  return source;
}

function expression(node, ast) {
  return node ? node.getText(ast) : null;
}

function props(node, ast, converted) {
  const result = new Map();
  for (const prop of attributes(node)) {
    assert.ok(ts.isJsxAttribute(prop), "no hidden spread props");
    const name = prop.name.getText(ast);
    if (converted && name === "disableUnderline") {
      assert.equal(prop.initializer, undefined);
      continue;
    }
    if (converted && name === "multiline") continue;
    if (converted && name === "inputProps") {
      assert.ok(ts.isJsxExpression(prop.initializer));
      const value = prop.initializer.expression;
      assert.ok(ts.isObjectLiteralExpression(value));
      for (const item of value.properties) {
        assert.ok(ts.isPropertyAssignment(item));
        const key = ts.isStringLiteral(item.name) ? item.name.text : item.name.getText(ast);
        result.set(key, item.initializer.getText(ast));
      }
      continue;
    }
    let value = expression(prop.initializer, ast);
    if (converted && name === "className") {
      assert.ok(value.includes(marker), "converted root marker");
      if (ts.isStringLiteral(prop.initializer)) {
        const classes = prop.initializer.text.split(" ").filter(part => part !== marker).join(" ");
        if (!classes) continue;
        value = JSON.stringify(classes);
      } else {
        const binary = prop.initializer.expression;
        assert.ok(ts.isBinaryExpression(binary));
        assert.equal(binary.operatorToken.kind, ts.SyntaxKind.PlusToken);
        assert.equal(binary.right.text, " " + marker);
        assert.ok(ts.isParenthesizedExpression(binary.left));
        value = "{" + binary.left.expression.getText(ast) + "}";
      }
    }
    if (value && ts.isJsxExpression(prop.initializer)) value = value.slice(1, -1);
    result.set(name, value);
  }
  return [...result].sort(([a], [b]) => a.localeCompare(b));
}

function kind(node) {
  const tag = opening(node).tagName.getText();
  if (tag === "Input" && has(node, "multiline")) return "textarea";
  return names.get(tag) || tag;
}

test("historical UI migration: exact non-rendered business/hooks/state/types/auth/source bytes", () => {
  assert.equal(nonrender(current), nonrender(original));
});

test("all 91 ordinary openings and native props/callback expressions/options", () => {
  const oldAst = parse(original);
  const newAst = parse(current);
  const before = controls(oldAst);
  const after = controls(newAst);
  const counts = {};
  for (const node of before) counts[kind(node)] = (counts[kind(node)] || 0) + 1;
  assert.deepEqual(counts, expected);
  assert.equal(before.length, 91);
  assert.equal(after.length, before.length);
  for (let index = 0; index < before.length; index++) {
    const oldNode = before[index];
    const newNode = after[index];
    assert.equal(kind(newNode), kind(oldNode));
    assert.notEqual(opening(newNode).tagName.getText(), kind(oldNode), "ordinary native converted");
    assert.deepEqual(props(newNode, newAst, true), props(oldNode, oldAst, false), "control " + index);
    if (kind(oldNode) === "button") {
      assert.equal(new Map(props(newNode, newAst, true)).get("type"), '"button"');
    } else {
      assert.ok(has(newNode, "disableUnderline"));
    }
    if (ts.isJsxElement(oldNode)) {
      assert.equal(newNode.children.map(child => child.getText(newAst)).join(""),
        oldNode.children.map(child => child.getText(oldAst)).join(""), "children/options unchanged");
    }
  }
});

test("protected native sheet input, entire td/grid and image/SVG/canvas source", () => {
  const before = parse(original);
  const after = parse(current);
  const cell = ast => collect(ast, node => ts.isJsxSelfClosingElement(node)
    && node.tagName.getText() === "input" && has(node, "data-sheet-row"));
  assert.equal(cell(before).length, 1);
  assert.equal(cell(after).length, 1);
  assert.equal(cell(after)[0].getText(after), cell(before)[0].getText(before));
  for (const tag of ["td", "table"]) {
    function ancestor(node) {
      while (node && !(ts.isJsxElement(node) && node.openingElement.tagName.getText() === tag)) node = node.parent;
      assert.ok(node);
      return node;
    }
    assert.equal(ancestor(cell(after)[0]).getText(after), ancestor(cell(before)[0]).getText(before));
  }
  const surfaces = ast => collect(ast, node => (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node))
    && ["svg", "img", "canvas"].includes(opening(node).tagName.getText())).map(node => node.getText(ast));
  assert.deepEqual(surfaces(after), surfaces(before));
});

test("every computed class retains original expression and active/inactive tokens", () => {
  const oldAst = parse(original);
  const newAst = parse(current);
  const before = controls(oldAst);
  const after = controls(newAst);
  const contexts = [
    { visibleStep: 1, item: { step: 1 }, headerAxisAddMode: true, rowAxisAddMode: true, ocrPreviewMode: "overlay" },
    { visibleStep: 2, item: { step: 1 }, headerAxisAddMode: false, rowAxisAddMode: false, ocrPreviewMode: "original" },
    { visibleStep: 2, item: { step: 1 }, headerAxisAddMode: false, rowAxisAddMode: false, ocrPreviewMode: "sheet" },
  ];
  let count = 0;
  before.forEach((node, index) => {
    const oldClass = attributes(node).find(prop => prop.name.getText(oldAst) === "className");
    if (!oldClass || !ts.isJsxExpression(oldClass.initializer)) return;
    count++;
    const newClass = attributes(after[index]).find(prop => prop.name.getText(newAst) === "className");
    const appended = newClass.initializer.expression;
    assert.ok(ts.isBinaryExpression(appended));
    assert.equal(appended.operatorToken.kind, ts.SyntaxKind.PlusToken);
    assert.equal(appended.right.text, " " + marker);
    assert.ok(ts.isParenthesizedExpression(appended.left));
    assert.equal(appended.left.expression.getText(newAst), oldClass.initializer.expression.getText(oldAst));
    for (const context of contexts) {
      const args = Object.keys(context);
      const values = Object.values(context);
      const oldValue = vm.compileFunction("return (" + oldClass.initializer.expression.getText(oldAst) + ");", args)(...values);
      const newValue = vm.compileFunction("return (" + appended.getText(newAst) + ");", args)(...values);
      const tokens = value => value.split(/\s+/).filter(Boolean);
      assert.deepEqual(tokens(newValue), [...tokens(oldValue), marker]);
    }
  });
  assert.equal(count, 8);
});

test("theme only, original render branches and surrounding markup retained", () => {
  const oldAst = parse(original);
  const newAst = parse(current);
  const themes = collect(newAst, node => ts.isJsxElement(node) && node.openingElement.tagName.getText() === "ThemeProvider");
  assert.equal(themes.length, 1);
  assert.equal(themes[0].openingElement.getText(newAst), "<ThemeProvider theme={sawaTheme}>");
  assert.equal(collect(newAst, node => (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node))
    && ["SawaProvider", "QueryClientProvider"].includes(opening(node).tagName.getText())).length, 0);
  // Compare the complete syntax tree after accounting only for approved rendering substitutions.
  function tree(node, ast) {
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText() === "ThemeProvider") {
      const children = node.children.filter(child => !ts.isJsxText(child) || child.text.trim());
      assert.equal(children.length, 1);
      return tree(children[0], ast);
    }
    if (ts.isImportDeclaration(node) && ["@mui/material", "@mui/material/styles", "@sawa/ui"].includes(node.moduleSpecifier.text)) return null;
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText() === "style") return null;
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const converted = names.has(opening(node).tagName.getText());
      const children = ts.isJsxElement(node) ? node.children.map(child => tree(child, ast)).filter(item => item !== null) : [];
      return [kind(node), props(node, ast, converted), children];
    }
    if (ts.isJsxText(node) && !node.text.trim()) return null;
    const children = node.getChildren(ast).map(child => tree(child, ast)).filter(item => item !== null);
    return children.length ? [node.kind, children] : [node.kind, node.getText(ast)];
  }
  assert.deepEqual(tree(newAst, newAst), tree(oldAst, oldAst));
});

test("original CSS declarations and specialized sheet selectors intact; added selectors marker-only", () => {
  function rules(source) {
    const css = source.slice(source.indexOf("<style jsx>")).split("`")[1];
    const result = [];
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selectors = match[1].trim().split(",\n").map(selector => selector.trim());
      if (selectors[0].startsWith("@")) continue;
      result.push({ selectors, declarations: match[2] });
    }
    return result;
  }
  const oldRules = rules(original);
  const newRules = rules(current);
  for (const oldRule of oldRules) {
    const matching = newRules.find(rule => oldRule.selectors.every(selector => rule.selectors.includes(selector))
      && rule.declarations === oldRule.declarations);
    assert.ok(matching, oldRule.selectors.join(", "));
  }
  const originals = new Set(oldRules.flatMap(rule => rule.selectors));
  for (const rule of newRules) for (const selector of rule.selectors) {
    if (!originals.has(selector)) assert.ok(selector.includes("." + marker), selector);
  }
});
