const assert = require("node:assert/strict");
const test = require("node:test");

const CONFIG_PATH = require.resolve("../../next.config.js");

async function loadRewrites(environment) {
  const original = {
    API_PROXY_TARGET: process.env.API_PROXY_TARGET,
    SHIFT_WEB_PROXY_TARGET: process.env.SHIFT_WEB_PROXY_TARGET,
    SCHOOL_LUNCH_WEB_PROXY_TARGET: process.env.SCHOOL_LUNCH_WEB_PROXY_TARGET,
  };
  Object.assign(process.env, environment);
  delete require.cache[CONFIG_PATH];
  try {
    return await require(CONFIG_PATH).rewrites();
  } finally {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    delete require.cache[CONFIG_PATH];
  }
}

test("external system root routes run before local placeholder pages", async () => {
  const rewrites = await loadRewrites({
    API_PROXY_TARGET: "https://hospital-api.example",
    SHIFT_WEB_PROXY_TARGET: "https://shift.example",
    SCHOOL_LUNCH_WEB_PROXY_TARGET: "https://school-lunch.example",
  });

  assert.deepEqual(rewrites.afterFiles, []);
  assert.deepEqual(rewrites.fallback, []);
  assert.ok(
    rewrites.beforeFiles.some(
      (route) => route.source === "/school-lunch"
        && route.destination === "https://school-lunch.example/school-lunch",
    ),
  );
  assert.ok(
    rewrites.beforeFiles.some(
      (route) => route.source === "/shift"
        && route.destination === "https://shift.example/shift",
    ),
  );
});

test("school lunch root and descendant routes use the same configured target", async () => {
  const rewrites = await loadRewrites({
    SCHOOL_LUNCH_WEB_PROXY_TARGET: "https://school-lunch.example",
  });
  const schoolLunchRoutes = rewrites.beforeFiles.filter((route) =>
    route.source.startsWith("/school-lunch"),
  );

  assert.deepEqual(schoolLunchRoutes, [
    { source: "/school-lunch", destination: "https://school-lunch.example/school-lunch" },
    { source: "/school-lunch-assets/:path*", destination: "https://school-lunch.example/:path*" },
    { source: "/school-lunch/api/:path*", destination: "https://school-lunch.example/api/:path*" },
    { source: "/school-lunch/:path*", destination: "https://school-lunch.example/school-lunch/:path*" },
  ]);
});

test("shift routes are disabled when no shift target is configured", async () => {
  const rewrites = await loadRewrites({
    SHIFT_WEB_PROXY_TARGET: "",
  });
  const shiftRoutes = rewrites.beforeFiles.filter((route) =>
    route.source.startsWith("/shift"),
  );

  assert.deepEqual(shiftRoutes, []);
});
