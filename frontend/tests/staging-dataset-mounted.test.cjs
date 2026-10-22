// Mounted synthetic transport tests, not deployed browser or production-copy proof.
const assert = require("node:assert/strict");
const test = require("node:test");
const harness = require("./helpers/modernization-react-dom.cjs");
const mountSelector = () => harness.mount("src/components/StagingDatasetSelect.tsx", {
  component(loader) {
    loader.load("src/services/stagingDataset.ts").initializeDocumentDataset({ dataset: "test" });
    return loader.load("src/components/StagingDatasetSelect.tsx").default;
  },
});

test("failed readiness keeps selection and credentials and does not send a switch", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    if (url === "/api/staging-datasets") return Response.json({ enabled: true, selected: "test", options: [
      { id: "test", label: "テスト用", available: true }, { id: "replay-synthetic", label: "本番再現用", available: true },
    ] });
    if (url.endsWith("shared-auth/me")) return Response.json({ session_generation: "synthetic-school-generation" });
    return Response.json({ detail: "not_ready" }, { status: 503 });
  };
  let h;
  try {
    h = await mountSelector();
    await h.waitFor(() => h.document.querySelector("select"));
    const storage = h.document.defaultView.sessionStorage;
    const credential = storage.getItem("auth_header");
    const select = h.document.querySelector("select");
    await h.settle(() => { select.value = "replay-synthetic"; select.dispatchEvent(new h.document.defaultView.Event("change", { bubbles: true })); });
    await h.waitFor(() => h.document.querySelector('[role="alert"]'));
    assert.equal(select.value, "test");
    assert.equal(storage.getItem("sawa_stg_dataset"), "test");
    assert.equal(storage.getItem("auth_header"), credential);
    assert.equal(calls.filter(call => call.init.method === "PUT").length, 0);
    assert.equal(calls.filter(call => call.url.endsWith("staging-dataset-ready")).length, 3);
    assert.match(h.document.body.textContent, /現在の選択を維持/);
  } finally { if (h) await h.close(); global.fetch = originalFetch; }
});

test("production hides selector and an unavailable replay option cannot be chosen", async () => {
  const originalFetch = global.fetch;
  let h;
  try {
    global.fetch = async () => Response.json({ enabled: false });
    h = await mountSelector();
    await h.settle();
    assert.equal(h.document.querySelector("select"), null);
    await h.close(); h = null;
    global.fetch = async () => Response.json({ enabled: true, selected: "test", options: [
      { id: "test", label: "テスト用", available: true }, { id: "unavailable", label: "本番再現用（準備未完了）", available: false },
    ] });
    h = await mountSelector();
    await h.waitFor(() => h.document.querySelector("select"));
    assert.equal(h.document.querySelector('option[value="unavailable"]').disabled, true);
  } finally { if (h) await h.close(); global.fetch = originalFetch; }
});

test("successful switch waits for all systems before publishing one selection", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  let releaseSchool;
  const schoolReady = new Promise(resolve => { releaseSchool = resolve; });
  global.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    if (url === "/api/staging-datasets" && init.method === "PUT") return Response.json({ selected: JSON.parse(init.body).dataset });
    if (url === "/api/staging-datasets") return Response.json({ enabled: true, selected: "test", options: [
      { id: "test", label: "テスト用", available: true }, { id: "replay-synthetic", label: "本番再現用", available: true },
    ] });
    if (url.endsWith("shared-auth/me")) return Response.json({ session_generation: "synthetic-school-generation" });
    if (url.startsWith("/school-lunch/")) await schoolReady;
    return Response.json({ ready: true, dataset: "replay-synthetic" });
  };
  let h;
  try {
    h = await mountSelector();
    await h.waitFor(() => h.document.querySelector("select"));
    const storage = h.document.defaultView.sessionStorage;
    const credential = storage.getItem("auth_header");
    const select = h.document.querySelector("select");
    await h.settle(() => { select.value = "replay-synthetic"; select.dispatchEvent(new h.document.defaultView.Event("change", { bubbles: true })); });
    await h.waitFor(() => calls.filter(call => call.url.endsWith("staging-dataset-ready")).length === 3);
    assert.equal(calls.some(call => call.init.method === "PUT"), false);
    assert.equal(storage.getItem("sawa_stg_dataset"), "test");
    await h.settle(() => releaseSchool());
    await h.waitFor(() => storage.getItem("sawa_stg_dataset") === "replay-synthetic");
    assert.equal(calls.filter(call => call.init.method === "PUT").length, 1);
    for (const call of calls.filter(call => call.url.endsWith("staging-dataset-ready"))) assert.equal(call.init.headers["X-Sawa-Dataset"], "replay-synthetic");
    assert.ok(h.document.defaultView.localStorage.getItem("sawa_stg_dataset_generation"));
    assert.equal(storage.getItem("auth_header"), credential);
  } finally { releaseSchool(); if (h) await h.close(); global.fetch = originalFetch; }
});
