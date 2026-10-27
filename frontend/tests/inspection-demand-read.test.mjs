import test from "node:test";
import assert from "node:assert/strict";
import harness from "./helpers/modernization-react-dom.cjs";

const { mount, transport, deferred } = harness;
const page = "src/pages/orders/[id]/inspection-v2.tsx";
const url = id => `/orders/${id}/workflow-v2/display`;
const rawUrl = id => `/orders/${id}/workflow-v2/inspection`;
const detailUrl = id => `/orders/${id}/workflow-v2/inspection-detail/candidate-${id}`;
const value = (id, revision = 7) => ({
  workflow: { order_id: id, revision, state: "ocr_selected", selected_ocr_result_id: `selected-${id}` },
  ocr_results: [
    { ocr_result_id: `selected-${id}`, selected: true, selectable: true, overlay_url: `https://example.invalid/${id}.png` },
    { ocr_result_id: `candidate-${id}`, selected: false, detail_state: "not_loaded" },
  ],
  saved_sheet: null,
});
const detailValue = (id, revision = 7, selectable = true) => ({
  ocr_result_id: `candidate-${id}`, selected: false, selectable, workflow_revision: revision,
  overlay_url: `https://example.invalid/candidate-${id}.png`,
});

async function setup(configure = () => {}) {
  const t = transport();
  for (const id of ["A", "B"]) {
    t.on("get", url(id), () => ({ data: value(id) }));
    t.on("get", rawUrl(id), () => ({ data: value(id) }));
    t.on("get", `/orders/${id}/document`, () => ({ data: new Blob(["synthetic PDF"]) }));
    t.on("get", detailUrl(id), () => ({ data: detailValue(id) }));
  }
  configure(t);
  const h = await mount(page, {
    api: t.api, route: "/orders/A/inspection-v2", routerState: { isReady: true, query: { id: "A" } },
  });
  return { h, t };
}

async function toggle(h, open) {
  await h.settle(() => { h.document.querySelector("details").open = open; });
  await h.settle();
}

test("initial reads are parallel summary/PDF only; detail is verified on demand, not an OCR mutation", async () => {
  const pdf = deferred();
  const { h, t } = await setup(t => {
    t.on("get", "/orders/A/document", () => pdf.promise);
  });
  try {
    assert.deepEqual(t.calls.map(c => c.url), [url("A"), "/orders/A/document"]);
    assert.equal(t.calls[0].config.params.scope, "inspection");
    assert.match(h.document.body.textContent, /詳細未取得・採用可否未検証/);
    assert.equal(h.document.querySelector("details pre"), null);
    assert.equal(h.document.querySelectorAll("img").length, 1);
    await h.click(h.button("詳細を確認"));
    assert.match(h.document.body.textContent, /検証済み・採用可能/);
    assert.doesNotMatch(h.document.body.textContent, /詳細未取得・採用可否未検証/);
    assert.ok(h.document.querySelector('a[href="https://example.invalid/candidate-A.png"]'));
    assert.equal(h.document.querySelectorAll("img").length, 1);
    assert.equal(h.document.querySelector(".candidate-card.selected .candidate-id").textContent, "selected-A");
    assert.ok(t.calls.every(c => c.method === "get"));
    await h.settle(() => pdf.resolve({ data: new Blob(["synthetic"]) }));
    assert.equal(h.blobs.length, 1);
  } finally { await h.close(); }
  assert.deepEqual(h.revoked, ["blob:synthetic-1"]);
});

test("detail failure can retry; false selectability and missing overlay are not not-loaded", async () => {
  let attempt = 0;
  const { h, t } = await setup(t => {
    t.on("get", detailUrl("A"), () => {
      if (++attempt === 1) throw new Error("detail failed");
      return { data: { ...detailValue("A", 7, false), overlay_url: null, overlay_message: "artifact missing" } };
    });
  });
  try {
    await h.click(h.button("詳細を確認"));
    assert.match(h.document.body.textContent, /detail failed/);
    await h.click(h.button("再試行"));
    assert.match(h.document.body.textContent, /採用不可/);
    assert.match(h.document.body.textContent, /artifact missing/);
    assert.doesNotMatch(h.document.body.textContent, /詳細未取得・採用可否未検証/);
    assert.equal(t.calls.filter(c => c.url === detailUrl("A")).length, 2);
  } finally { await h.close(); }
});

test("revision mismatch rejects detail and offers whole-page reload", async () => {
  const { h, t } = await setup(t => {
    t.on("get", detailUrl("A"), () => ({ data: detailValue("A", 8) }));
  });
  try {
    await h.click(h.button("詳細を確認"));
    assert.match(h.document.body.textContent, /注文の状態が更新されました/);
    assert.equal(h.document.querySelector('a[href="https://example.invalid/candidate-A.png"]'), null);
    t.on("get", url("A"), () => ({ data: value("A", 8) }));
    await h.click(h.button("画面全体を再読み込み"));
    await h.click(h.button("詳細を確認"));
    assert.match(h.document.body.textContent, /検証済み・採用可能/);
    assert.equal(t.calls.filter(c => c.url === url("A")).length, 2);
  } finally { await h.close(); }
});

test("failed initial inspection retries without blocking the independent PDF", async () => {
  let attempts = 0;
  const { h, t } = await setup(t => {
    t.on("get", url("A"), () => {
      if (++attempts === 1) throw new Error("inspection failed");
      return { data: value("A") };
    });
  });
  try {
    assert.match(h.document.body.textContent, /inspection failed/);
    assert.ok(h.document.querySelector("iframe"));
    await h.click(h.button("画面全体を再読み込み"));
    assert.match(h.document.body.textContent, /selected-A/);
    assert.equal(attempts, 2);
    assert.ok(t.calls.filter(c => c.url === url("A")).every(c => c.config.params.scope === "inspection"));
    assert.ok(h.revoked.includes("blob:synthetic-1"));
  } finally { await h.close(); }
});

test("cancelled detail and delayed order-A reads cannot appear in order B", async () => {
  const waiting = deferred(), oldPdf = deferred();
  const { h, t } = await setup(t => {
    t.on("get", detailUrl("A"), () => waiting.promise);
    t.on("get", "/orders/A/document", () => oldPdf.promise);
  });
  try {
    await h.click(h.button("詳細を確認"));
    const request = t.calls.find(c => c.url === detailUrl("A"));
    await h.click(h.button("キャンセル"));
    assert.equal(request.config.signal.aborted, true);
    await h.click(h.button("詳細を確認"));
    Object.assign(h.nav.router, { query: { id: "B" }, asPath: "/orders/B/inspection-v2" });
    await h.render();
    await h.settle(() => {
      waiting.resolve({ data: detailValue("A") });
      oldPdf.resolve({ data: new Blob(["old bytes"]) });
    });
    assert.ok(t.calls.filter(c => c.url === detailUrl("A")).every(c => c.config.signal.aborted));
    assert.doesNotMatch(h.document.body.textContent, /candidate-A|selected-A/);
    assert.match(h.document.body.textContent, /candidate-B/);
    assert.equal(h.blobs.length, 1);
  } finally { await h.close(); }
});

test("same-revision raw JSON is only rendered when open and does not replace inspection or PDF", async () => {
  const pending = deferred();
  const { h, t } = await setup(t => {
    t.on("get", detailUrl("A"), () => pending.promise);
    t.on("get", rawUrl("A"), () => ({
      data: {
        ...value("A"), source: "full-only-data",
        ocr_results: [value("A").ocr_results[0], detailValue("A")],
      },
    }));
  });
  try {
    assert.equal(t.calls.filter(c => c.url === url("A")).length, 1);
    assert.equal(h.document.querySelector("details pre"), null);
    const state = h.document.querySelector(".state-panel").textContent;
    const pdf = h.document.querySelector("iframe").src;
    await h.click(h.button("詳細を確認"));
    await toggle(h, true);
    await h.waitFor(() => h.document.querySelector("details pre"));
    assert.equal(t.calls.filter(c => c.url === rawUrl("A")).length, 1);
    assert.match(h.document.querySelector("details pre").textContent, /full-only-data/);
    assert.equal(h.document.querySelector(".state-panel").textContent, state);
    assert.equal(h.document.querySelector("iframe").src, pdf);
    assert.equal(t.calls.find(c => c.url === detailUrl("A")).config.signal.aborted, false);
    await h.settle(() => pending.resolve({ data: detailValue("A", 7) }));
    assert.match(h.document.querySelector(".candidate-grid").textContent, /検証済み・採用可能/);
    await toggle(h, false);
    assert.equal(h.document.querySelector("details pre"), null);
    await toggle(h, true);
    assert.equal(t.calls.filter(c => c.url === rawUrl("A")).length, 2);
  } finally { await h.close(); }
});

test("raw failure retry, close cancellation and late response invalidation", async () => {
  const pending = deferred();
  let fullCalls = 0;
  const { h, t } = await setup(t => {
    t.on("get", rawUrl("A"), () => {
      if (++fullCalls === 1) throw new Error("raw failed");
      return pending.promise;
    });
  });
  try {
    await toggle(h, true);
    assert.match(h.document.body.textContent, /raw failed/);
    await h.click(h.button("再試行"));
    const request = t.calls.at(-1);
    await toggle(h, false);
    assert.equal(request.config.signal.aborted, true);
    await h.settle(() => pending.resolve({ data: { ...value("A", 99), source: "late raw" } }));
    assert.equal(h.document.querySelector("details pre"), null);
    assert.doesNotMatch(h.document.body.textContent, /late raw/);
  } finally { await h.close(); }
});

for (const initialPending of [true, false]) test(`late ${initialPending ? "initial" : "raw"} response and URL ownership retire on order switch`, async () => {
  const initial = deferred(), raw = deferred();
  const { h, t } = await setup(t => {
    t.on("get", url("A"), () => initialPending ? initial.promise : { data: value("A") });
    t.on("get", rawUrl("A"), () => raw.promise);
  });
  try {
    if (initialPending) assert.equal(h.document.querySelector("details"), null);
    else await toggle(h, true);
    Object.assign(h.nav.router, { query: { id: "B" }, asPath: "/orders/B/inspection-v2" });
    await h.render();
    await h.settle(() => {
      initial.resolve({ data: value("A") });
      raw.resolve({ data: value("A", 99) });
    });
    assert.match(h.document.body.textContent, /selected-B/);
    assert.doesNotMatch(h.document.body.textContent, /selected-A/);
    assert.equal(h.document.querySelector("details pre"), null);
    assert.ok(t.calls.filter(c => [url("A"), rawUrl("A")].includes(c.url)).every(c => c.config.signal.aborted));
    assert.ok(h.revoked.includes("blob:synthetic-1"));
  } finally { await h.close(); }
});

test("raw cannot start before the initial revision is available", async () => {
  const initial = deferred();
  const { h, t } = await setup(t => t.on("get", url("A"), () => initial.promise));
  try {
    assert.equal(h.document.querySelector("details"), null);
    assert.equal(t.calls.filter(c => c.url === url("A")).length, 1);
    await h.settle(() => initial.resolve({ data: value("A") }));
    await toggle(h, true);
    await h.waitFor(() => h.document.querySelector("details pre"));
    assert.equal(t.calls.filter(c => c.url === url("A")).length, 1);
    assert.equal(t.calls.filter(c => c.url === rawUrl("A")).length, 1);
  } finally { await h.close(); }
});

test("raw revision mismatch preserves inspection/PDF until whole-page reload", async () => {
  const { h, t } = await setup(t => t.on("get", rawUrl("A"), () => ({
    data: {
      ...value("A", 8), workflow: { ...value("A", 8).workflow, headline: "updated workflow" },
    },
  })));
  try {
    const state = h.document.querySelector(".state-panel").textContent;
    const pdf = h.document.querySelector("iframe").src;
    await toggle(h, true);
    assert.match(h.document.body.textContent, /注文の状態が更新されました/);
    assert.equal(h.document.querySelector("details pre"), null);
    assert.equal(h.document.querySelector(".state-panel").textContent, state);
    assert.equal(h.document.querySelector("iframe").src, pdf);
    t.on("get", url("A"), () => ({ data: {
      ...value("A", 8), workflow: { ...value("A", 8).workflow, headline: "updated workflow" },
    } }));
    await h.click(h.button("画面全体を再読み込み"));
    assert.match(h.document.querySelector(".state-panel").textContent, /updated workflow/);
    assert.notEqual(h.document.querySelector("iframe").src, pdf);
    await toggle(h, true);
    await h.waitFor(() => h.document.querySelector("details pre"));
    assert.doesNotMatch(h.document.body.textContent, /注文の状態が更新されました/);
  } finally { await h.close(); }
});

test("whole-page reload invalidates a pending raw response even at the same revision", async () => {
  const pending = deferred();
  let pdfCalls = 0;
  const { h, t } = await setup(t => {
    t.on("get", "/orders/A/document", () => {
      if (++pdfCalls === 1) throw new Error("PDF failed");
      return { data: new Blob(["retry PDF"]) };
    });
    t.on("get", rawUrl("A"), () => pending.promise);
  });
  try {
    await toggle(h, true);
    const request = t.calls.at(-1);
    await h.click(h.button("画面全体を再読み込み"));
    assert.equal(request.config.signal.aborted, true);
    await h.settle(() => pending.resolve({ data: { ...value("A"), source: "retired response" } }));
    assert.equal(h.document.querySelector("details pre"), null);
    assert.doesNotMatch(h.document.body.textContent, /retired response/);
    assert.ok(h.document.querySelector("iframe"));
  } finally { await h.close(); }
});

test("raw success preserves the independent PDF failure and whole-page retry", async () => {
  let attempts = 0;
  const { h } = await setup(t => t.on("get", "/orders/A/document", () => {
    if (++attempts === 1) throw new Error("PDF download failed");
    return { data: new Blob(["retry PDF"]) };
  }));
  try {
    assert.match(h.document.body.textContent, /PDF download failed/);
    assert.match(h.document.querySelector(".pdf-panel").textContent, /PDFを取得できませんでした/);
    await toggle(h, true);
    await h.waitFor(() => h.document.querySelector("details pre"));
    assert.match(h.document.body.textContent, /PDF download failed/);
    assert.equal(h.document.querySelector("iframe"), null);
    await h.click(h.button("画面全体を再読み込み"));
    assert.equal(attempts, 2);
    assert.ok(h.document.querySelector("iframe"));
    assert.doesNotMatch(h.document.body.textContent, /PDF download failed/);
  } finally { await h.close(); }
});
