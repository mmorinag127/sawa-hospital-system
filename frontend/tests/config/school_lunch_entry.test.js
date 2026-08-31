const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), "utf8");

test("school lunch links establish the shared HttpOnly session before navigation", () => {
  const navigation = read("frontend/src/services/systemNavigation.ts");
  const portal = read("frontend/src/pages/index.tsx");
  const shell = read("frontend/src/components/UnifiedShell.tsx");

  assert.match(navigation, /getStoredAuthHeader\(\)/);
  assert.match(navigation, /SCHOOL_LUNCH_HOME = "\/school-lunch\/implementation-price-tables"/);
  assert.match(navigation, /\/school-lunch\/api\/backend\/shared-auth\/me/);
  assert.match(navigation, /headers: \{ Authorization: authorization \}/);
  assert.match(navigation, /credentials: "same-origin"/);
  assert.match(navigation, /if \(response\.ok\)[\s\S]*window\.location\.assign\(destination\)/);
  assert.match(portal, /href="\/school-lunch" onClick=\{enterSchoolLunch\}/);
  assert.match(shell, /href="\/school-lunch" onClick=\{enterSchoolLunch\}/);
});
