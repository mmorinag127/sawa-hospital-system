const assert = require("node:assert/strict");
const test = require("node:test");
const load = require("./helpers/staging-dataset-module.cjs");

test("selection headers remain pinned to the document cookie, never mutable storage", () => {
  let selected = "test";
  const api = load({ sessionStorage: { getItem: () => selected } });
  assert.throws(() => api.datasetHeaders(), /dataset_unconfirmed/);
  api.initializeDocumentDataset(api.datasetFromCookie("sawa_stg_dataset=replay-synthetic"));
  assert.equal(api.datasetHeaders()["X-Sawa-Dataset"], "replay-synthetic");
  selected = "test";
  api.initializeDocumentDataset({ dataset: "test" });
  assert.equal(api.datasetHeaders()["X-Sawa-Dataset"], "replay-synthetic");
});

test("proxies preserve snapshot selection on GET, and reject old-page writes after switching", () => {
  const api = load();
  const cookie = { sawa_stg_dataset: "replay-synthetic" };
  assert.equal(api.forwardDatasetHeaders({}, cookie, "POST")["X-Sawa-Dataset"], "replay-synthetic");
  assert.equal(api.forwardDatasetHeaders({ "x-sawa-dataset": "test" }, cookie, "GET")["X-Sawa-Dataset"], "test");
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    assert.throws(() => api.forwardDatasetHeaders({ "x-sawa-dataset": "test" }, cookie, method), /dataset_changed/);
  }
  for (const selected of ["prod", "postgresql://prod/db", "", "replay-../../prod"]) {
    assert.throws(() => api.forwardDatasetHeaders({ "x-sawa-dataset": selected }, {}, "GET"), /dataset_invalid/);
  }
});
