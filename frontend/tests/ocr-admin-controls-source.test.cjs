const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test } = require("node:test");
const ts = require("typescript");

const base = "feae328e2aa7317c603bb0af9970e4cc183bcd87";
const root = path.resolve(__dirname, "../..");
const pages = {
  "ocr-facilities": { button: 5, input: 1, textarea: 1 },
  "ocr-templates": { button: 5, input: 1, textarea: 1 },
  "ocr-training-data": { button: 6, input: 1, textarea: 0 },
};
function parse(source) {
  return ts.createSourceFile("page.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}
function walk(node, visit) {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}
function opening(node) {
  return ts.isJsxElement(node) ? node.openingElement : node;
}
function attrs(node) {
  const result = new Map();
  for (const attr of opening(node).attributes.properties) {
    assert.ok(ts.isJsxAttribute(attr), "no introduced spread attributes");
    const key = attr.name.getText();
    assert.ok(!result.has(key), "no duplicate props");
    result.set(key, attr.initializer);
  }
  return result;
}
function value(node) {
  if (!node) return { present: false, text: null };
  return {
    present: true,
    text: ts.isJsxExpression(node) ? node.expression.getText() : node.getText(),
  };
}
function elements(tree, names) {
  const result = [];
  walk(tree, (node) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      if (names.includes(opening(node).tagName.getText())) result.push(node);
    }
  });
  return result;
}
function kind(node) {
  const tag = opening(node).tagName.getText();
  if (tag === "Button") return "button";
  if (tag === "Input") return attrs(node).has("multiline") ? "textarea" : "input";
  return tag;
}
function normalizeProps(node, old, marker) {
  const current = attrs(node);
  const original = attrs(old);
  const tag = opening(node).tagName.getText();
  if (tag === "Input") {
    assert.ok(current.has("disableUnderline"));
    assert.equal(current.get("disableUnderline"), undefined);
    current.delete("disableUnderline");
    if (kind(old) === "textarea") {
      assert.ok(current.has("multiline"));
      assert.equal(current.get("multiline"), undefined);
      current.delete("multiline");
    } else {
      assert.ok(!current.has("multiline"));
    }
  }
  if (current.has("inputProps")) {
    const init = current.get("inputProps");
    assert.ok(ts.isJsxExpression(init) && ts.isObjectLiteralExpression(init.expression));
    const native = init.expression.properties;
    const expected = [...original.keys()].filter((key) => ["min", "max"].includes(key));
    assert.deepEqual(native.map((prop) => prop.name.getText()).sort(), expected.sort());
    for (const prop of native) {
      assert.ok(ts.isPropertyAssignment(prop));
      assert.ok(!current.has(prop.name.getText()));
      current.set(prop.name.getText(), { nativeValue: prop.initializer.getText() });
    }
    current.delete("inputProps");
  }
  if (tag === "Button" && !original.has("type")) {
    assert.deepEqual(value(current.get("type")), { present: true, text: '"submit"' });
    current.delete("type");
  }
  const originalClass = original.get("className");
  const currentClass = current.get("className");
  assert.ok(originalClass && currentClass);
  if (ts.isStringLiteral(originalClass)) {
    assert.ok(ts.isStringLiteral(currentClass));
    assert.equal(currentClass.text, originalClass.text + " " + marker);
  } else {
    const expression = currentClass.expression;
    assert.ok(ts.isBinaryExpression(expression));
    assert.equal(expression.operatorToken.kind, ts.SyntaxKind.PlusToken);
    assert.ok(ts.isParenthesizedExpression(expression.left));
    assert.equal(expression.left.expression.getText(), originalClass.expression.getText());
    assert.equal(expression.right.text, " " + marker);
  }
  current.set("className", originalClass);
  assert.deepEqual([...current.keys()].sort(), [...original.keys()].sort());
  for (const [key, init] of original) {
    const actual = current.get(key);
    assert.deepEqual(actual && actual.nativeValue !== undefined
      ? { present: true, text: actual.nativeValue } : value(actual), value(init), key);
  }
}
function nonrendered(tree, source) {
  const ranges = [];
  walk(tree, (node) => {
    if ((ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node))
      && !(ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      let ancestor = node.parent;
      let contained = false;
      while (ancestor) {
        if (ts.isJsxElement(ancestor) || ts.isJsxFragment(ancestor)) contained = true;
        ancestor = ancestor.parent;
      }
      if (!contained) ranges.push([node.getStart(), node.end]);
    }
  });
  for (const [start, end] of ranges.sort((a, b) => b[0] - a[0])) {
    source = source.slice(0, start) + "RENDER" + source.slice(end);
  }
  return source.replace(/^import \{ (?:Button, Input|ThemeProvider|sawaTheme) \} from "(?:@mui\/material|@mui\/material\/styles|@sawa\/ui)";\n/gm, "");
}
function canonical(node, controls) {
  if (ts.isJsxElement(node) && opening(node).tagName.getText() === "ThemeProvider") {
    const children = node.children.filter((child) => !(ts.isJsxText(child) && !child.text.trim()));
    assert.equal(children.length, 1);
    return canonical(children[0], controls);
  }
  if (ts.isJsxElement(node) && opening(node).tagName.getText() === "style") return "STYLE";
  if (controls.has(node)) {
    return ["CONTROL", kind(node), ts.isJsxElement(node)
      ? node.children.map((child) => canonical(child, controls)) : []];
  }
  if (ts.isJsxText(node)) return ["TEXT", node.text];
  return [node.kind, ts.forEachChild(node, () => undefined) === undefined
    && node.getChildCount() === 0 ? node.getText() : node.getChildren().map((child) => canonical(child, controls))];
}
for (const [name, counts] of Object.entries(pages)) {
  test(name + " complete source/render/native prop contract", () => {
    const file = "frontend/src/pages/" + name + ".tsx";
    const before = execFileSync("git", ["show", base + ":" + file], { cwd: root, encoding: "utf8" });
    const after = fs.readFileSync(path.join(root, file), "utf8");
    const oldTree = parse(before);
    const newTree = parse(after);
    const oldControls = elements(oldTree, ["button", "input", "select", "textarea"]);
    const newControls = elements(newTree, ["Button", "Input", "NativeSelect", "Checkbox"]);
    const census = (nodes) => Object.fromEntries(Object.keys(counts).map((key) => [key, nodes.filter((node) => kind(node) === key).length]));
    assert.deepEqual(census(oldControls), counts);
    assert.deepEqual(census(newControls), counts);
    assert.equal(newControls.length, oldControls.length);
    assert.equal(elements(newTree, ["button", "input", "select", "textarea"]).length, 0);
    const marker = name + "-ordinary-control";
    oldControls.forEach((old, index) => {
      assert.equal(kind(newControls[index]), kind(old));
      normalizeProps(newControls[index], old, marker);
      if (ts.isJsxElement(old)) {
        assert.equal(newControls[index].children.map((child) => child.getText()).join(""),
          old.children.map((child) => child.getText()).join(""));
      }
    });
    assert.equal(nonrendered(newTree, after), nonrendered(oldTree, before));
    const oldMain = elements(oldTree, ["main"])[0];
    const newMain = elements(newTree, ["main"])[0];
    assert.equal(opening(newMain.parent).tagName.getText(), "ThemeProvider");
    assert.deepEqual(canonical(newMain, new Set(newControls)), canonical(oldMain, new Set(oldControls)));
    assert.equal(elements(newTree, ["ThemeProvider"]).length, 1);
    assert.match(after, /<ThemeProvider theme=\{sawaTheme\}>/);
    assert.equal(elements(newTree, ["SawaProvider", "QueryClientProvider", "QueryClientSessionProvider"]).length, 0);
    for (const surface of ["svg", "img", "canvas"]) {
      assert.deepEqual(elements(newTree, [surface]).map((node) => node.getText()),
        elements(oldTree, [surface]).map((node) => node.getText()));
    }
    const css = (source) => source.slice(source.indexOf("<style jsx>"), source.indexOf("</style>"));
    const oldCss = css(before);
    const newCss = css(after);
    const rules = /([^{}]+)\{([^{}]*)\}/g;
    for (const match of oldCss.matchAll(rules)) {
      if (match[1].includes("<style") || match[1].includes("@media")) continue;
      const selector = match[1].trim();
      const originalSelectors = selector.split(",").map((part) => part.trim());
      const candidates = [...newCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
      assert.ok(candidates.some((candidate) => candidate[2] === match[2]
        && originalSelectors.every((part) => candidate[1].split(",").map((item) => item.trim()).includes(part))),
        "original CSS declaration retained: " + selector);
    }
    for (const other of Object.keys(pages).filter((page) => page !== name)) {
      assert.ok(!newCss.includes(other + "-ordinary-control"));
    }
    for (const selector of newCss.matchAll(/:global\(([^)]+)\)/g)) {
      if (oldCss.includes(selector[0])) continue;
      assert.ok(selector[1].startsWith("." + marker), selector[0]);
    }
  });
}
