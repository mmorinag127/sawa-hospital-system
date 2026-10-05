const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const test = require("node:test");

function load(overrides = {}) {
  const env = { ...process.env };
  delete env.E2E_PORT;
  delete env.E2E_BASE_URL;
  return spawnSync(process.execPath, ["-e", 'console.log(JSON.stringify(require("./playwright.menu-masters.config.js")))'], {
    cwd: path.resolve(__dirname, "../.."), env: { ...env, ...overrides }, encoding: "utf8",
  });
}

test("menu-master WebKit entry defaults to a dedicated localhost server without reuse", () => {
  const result = load();
  assert.equal(result.status, 0, result.stderr);
  const config = JSON.parse(result.stdout);
  assert.equal(config.use.baseURL, "http://localhost:31318/");
  assert.equal(config.use.browserName, "webkit");
  assert.equal(config.webServer.url, config.use.baseURL);
  assert.equal(config.webServer.reuseExistingServer, false);
  assert.equal(config.webServer.command, "node node_modules/next/dist/bin/next start --hostname localhost --port 31318");
});

test("menu-master entry retains URL and port safety restrictions", () => {
  for (const E2E_PORT of ["0", "80", "65536", "31318;exit", "-31318", "3.1318"]) {
    const result = load({ E2E_PORT });
    assert.notEqual(result.status, 0, E2E_PORT);
    assert.match(result.stderr, /E2E_PORT must be an integer/);
  }
  for (const E2E_BASE_URL of [
    "https://localhost:31318", "http://example.com:31318", "http://localhost:31319",
    "http://localhost:31318/menu-masters", "http://localhost:31318/?q=1",
    "http://localhost:31318/#hash", "http://user:pass@localhost:31318", "invalid",
  ]) {
    const result = load({ E2E_BASE_URL });
    assert.notEqual(result.status, 0, E2E_BASE_URL);
    assert.match(result.stderr, /E2E_BASE_URL must be/);
  }
  for (const host of ["localhost", "127.0.0.1"]) {
    assert.equal(load({ E2E_PORT: "31319", E2E_BASE_URL: `http://${host}:31319` }).status, 0);
  }
});
