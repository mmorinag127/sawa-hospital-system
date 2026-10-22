const assert = require("node:assert/strict");
const test = require("node:test");
const load = require("./helpers/staging-dataset-module.cjs");

test("selection headers are per-document and contain no connection string", () => {
  let selected = null;
  const api = load({ sessionStorage: { getItem: () => selected } });
  assert.deepEqual(JSON.parse(JSON.stringify(api.datasetHeaders())), {});
  selected = "replay-synthetic";
  assert.deepEqual(JSON.parse(JSON.stringify(api.datasetHeaders())), { "X-Sawa-Dataset": selected });
  selected = "test";
  assert.equal(api.datasetHeaders()["X-Sawa-Dataset"], "test");
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
