const { defineConfig } = require("@playwright/test");

const baseUrl = process.env.E2E_BASE_URL || "http://127.0.0.1:3100";

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 30 * 1000,
  expect: {
    timeout: 5000,
  },
  use: {
    baseURL: baseUrl,
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
    url: baseUrl,
    reuseExistingServer: true,
    timeout: 120 * 1000,
  },
  reporter: [["list"]],
});
