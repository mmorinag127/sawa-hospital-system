const { defineConfig } = require("@playwright/test");

const port = process.env.E2E_PORT || "31318";

if (!/^[1-9]\d{3,4}$/.test(port) || Number(port) < 1024 || Number(port) > 65535) {
  throw new Error("E2E_PORT must be an integer from 1024 through 65535");
}

let baseUrl;
try {
  baseUrl = new URL(process.env.E2E_BASE_URL || `http://127.0.0.1:${port}`);
} catch {
  throw new Error("E2E_BASE_URL must be a valid localhost URL");
}

if (
  baseUrl.protocol !== "http:" ||
  !new Set(["127.0.0.1", "localhost"]).has(baseUrl.hostname) ||
  baseUrl.port !== port ||
  baseUrl.pathname !== "/" ||
  baseUrl.search ||
  baseUrl.hash ||
  baseUrl.username ||
  baseUrl.password
) {
  throw new Error("E2E_BASE_URL must be an http localhost root URL using E2E_PORT without query, hash, or userinfo");
}

module.exports = defineConfig({
  testDir: "./tests/e2e",
  testMatch: "menu_masters.spec.ts",
  timeout: 30 * 1000,
  expect: { timeout: 5000 },
  use: {
    baseURL: baseUrl.toString(),
    browserName: "webkit",
    headless: true,
    trace: "on-first-retry",
  },
  webServer: {
    command: `PORT=${port} npm run start`,
    url: baseUrl.toString(),
    reuseExistingServer: false,
    timeout: 120 * 1000,
  },
});
