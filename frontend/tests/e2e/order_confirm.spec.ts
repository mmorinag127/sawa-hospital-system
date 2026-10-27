import { expect, test } from "@playwright/test";
import { mountWorkflowV2SheetPage } from "../helpers/workflow-v2-browser";
import { secondOrderPreviewPdf } from "../helpers/second-order-preview-pdf";

for (const status of [200, 409] as const) test(`current OCR selection uses the explicit candidate and CAS: ${status}`, async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, {
    workflowState: "ocr_completed", workflow: { selected_ocr_result_id: null, saved_sheet_id: null },
    ocrResults: ["OCR-E2E-001", "OCR-E2E-002"].map(ocr_result_id => ({
      ocr_result_id, selected: false, selectable: true, detail_state: "loaded",
      overlay_url: "", sheet_review_base_url: "", status: "done",
    })),
  });
  const calls: Array<{ path: string; revision?: string }> = [];
  const selectedSheet = { ...mounted.sheet, base_evidence_run_id: "OCR-E2E-002",
    selected_ocr_result_id: "OCR-E2E-002", rows: [
      ["05/22", "朝", "Selected Menu A", "21", "0"],
      ["05/22", "昼", "Selected Menu B", "?", ""],
      ["05/22", "夕", "Selected Menu C", "5", "6"],
    ] };
  let sheetReads = 0;
  await page.route("**/workflow-v2/sheet-source", async route => {
    expect(route.request().method()).toBe("GET");
    expect(mounted.workflow.selected_ocr_result_id).toBe("OCR-E2E-002");
    sheetReads++;
    await route.fulfill({ status: 200, json: { order_id: mounted.orderId,
      selected_ocr_result_id: "OCR-E2E-002", sheet: selectedSheet } });
  });
  await page.route("**/workflow-v2/ocr-results/*/select", async route => {
    calls.push({ path: new URL(route.request().url()).pathname, revision: route.request().headers()["if-match"] });
    expect(route.request().method()).toBe("POST");
    if (status === 409) return route.fulfill({ status, json: { detail: "selection revision conflict" } });
    Object.assign(mounted.workflow, { revision: 8, state: "ocr_selected", selected_ocr_result_id: "OCR-E2E-002", saved_sheet_id: null });
    await route.fulfill({ status, json: { workflow: mounted.workflow } });
  });
  await expect(page.getByRole("heading", { name: "正解 OCR を一つ選ぶ", exact: true })).toBeVisible();
  await page.locator(".ocr-card").filter({hasText: "OCR-E2E-002"}).getByRole("button", {name: "詳細を表示", exact: true}).click();
  await page.locator(".ocr-card").filter({ has: page.locator("strong").filter({ hasText: /^OCR-E2E-002$/ }) })
    .getByRole("button", { name: "正解にする", exact: true }).click();
  await expect.poll(() => calls).toEqual([{ path: `/api/orders/${mounted.orderId}/workflow-v2/ocr-results/OCR-E2E-002/select`, revision: '"7"' }]);
  if (status === 200) {
    await expect(page.getByRole("heading", { name: "選択 OCR からシート作成 / 編集 / 保存", exact: true })).toBeVisible();
    await expect(page.getByText("正解OCRを選択しました", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "選択OCRからシート生成", exact: true }).click();
    await expect(page.locator('[data-sheet-row="0"][data-sheet-col="2"]')).toHaveValue("Selected Menu A");
    await expect(page.locator('[data-sheet-row="0"][data-sheet-col="3"]')).toHaveValue("21");
    await expect(page.locator('[data-sheet-row="0"][data-sheet-col="4"]')).toHaveValue("0");
    await expect(page.locator('[data-sheet-row="1"][data-sheet-col="3"]')).toHaveValue("?");
    await expect(page.locator('[data-sheet-row="1"][data-sheet-col="4"]')).toHaveValue("");
    expect(sheetReads).toBe(1);
  } else {
    await expect(page.getByText(/他の更新と競合/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "正解 OCR を一つ選ぶ", exact: true })).toBeVisible();
    expect(mounted.workflow.selected_ocr_result_id).toBeNull();
    expect(sheetReads).toBe(0);
  }
});

test("saved OCR history refusal shows its actual reason and retains the selected sheet", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, {
    workflow: { saved_sheet_id: "SHEET-E2E-001" },
    ocrResults: [{ ocr_result_id: "OCR-E2E-001", selected: true, selectable: true,
      detail_state: "loaded", overlay_url: "", sheet_review_base_url: "" }],
  });
  await page.getByRole("button", { name: /Step2.*OCR選択/ }).click();
  await page.getByRole("button", {name: "詳細を表示", exact: true}).click();
  let deletes = 0;
  await page.route("**/workflow-v2/ocr-results/OCR-E2E-001", async route => {
    expect(route.request().method()).toBe("DELETE");
    expect(route.request().headers()["if-match"]).toBe('"7"');
    deletes++;
    await route.fulfill({ status: 409, json: { detail: "ocr_result_saved_history_conflict" } });
  });
  await page.getByRole("button", { name: "完全削除", exact: true }).click();
  await expect(page.getByText("このOCR結果に紐づく保存済みシート・修正候補・出力の履歴があるため、削除できません。現在選択中のOCRと保存内容は保持しています。", { exact: true })).toBeVisible();
  expect(deletes).toBe(1);
  expect(mounted.workflow.selected_ocr_result_id).toBe("OCR-E2E-001");
  await page.getByRole("button", { name: /Step3.*シート編集/ }).click();
  await expect(page.locator('[data-sheet-row="0"][data-sheet-col="3"]')).toHaveValue("1");
  await expect(page.locator('[data-sheet-row="0"][data-sheet-col="4"]')).toHaveValue("2");
});

for (const status of [409, 500] as const) test(`final confirm pending prevents duplicates and rejection retains the order: ${status}`, async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, { workflowState: "output_review",
    workflow: { saved_sheet_id: "SHEET-E2E-001", bagging_result_id: "BAG-E2E-001", output_bundle_id: "BUNDLE-E2E-001" } });
  await page.getByRole("button", { name: /Step3.*シート編集/ }).click();
  await page.locator('[data-sheet-row="0"][data-sheet-col="3"]').fill("123");
  await page.getByRole("button", { name: /Step4.*出力確認/ }).click();
  await expect(page.getByRole("heading", { name: "出力確認", exact: true })).toBeVisible();
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route("**/workflow-v2/confirm", async route => {
    calls++;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["if-match"]).toBe('"7"');
    expect(route.request().postDataJSON()).toEqual({ confirmed_by: "operator" });
    await pending;
    await route.fulfill({ status, json: { detail: "owned-confirm-rejected" } });
  });
  const confirm = page.getByRole("button", { name: "確定して一覧にもどる", exact: true });
  page.once("dialog", async dialog => {
    expect(dialog.message()).toBe("未保存の変更を破棄して処理を続けますか？");
    await dialog.accept();
  });
  await confirm.click();
  await expect.poll(() => calls).toBe(1);
  await expect(confirm).toBeDisabled();
  await page.keyboard.press("Enter");
  expect(calls).toBe(1);
  release();
  await expect(page.getByText(status === 409 ? /他の更新と競合/ : /owned-confirm-rejected/)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/orders/${mounted.orderId}/workflow-v2$`));
  await expect(confirm).toBeEnabled();
  expect(calls).toBe(1);
  await page.getByRole("button", { name: /Step3.*シート編集/ }).click();
  await expect(page.locator('[data-sheet-row="0"][data-sheet-col="3"]')).toHaveValue("123");
  await expect(page.locator('[data-sheet-row="0"][data-sheet-col="4"]')).toHaveValue("2");
  await expect(page.locator('[data-sheet-row="2"][data-sheet-col="3"]')).toHaveValue("5");
});

for (const failPostConfirmRead of [false, true]) test(`successful confirmation returns without a second detail read or training write: ${failPostConfirmRead}`, async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, { workflowState: "output_review",
    workflow: { saved_sheet_id: "SHEET-E2E-001", bagging_result_id: "BAG-E2E-001", output_bundle_id: "BUNDLE-E2E-001" } });
  let confirmations = 0, postConfirmReads = 0;
  await page.route(/\/workflow-v2\/display(?:\?|$)/, async route => {
    if (confirmations) {
      postConfirmReads++;
      return route.fulfill({ status: failPostConfirmRead ? 500 : 200, json: failPostConfirmRead
        ? { detail: "post-confirm-read-must-not-be-needed" } : { workflow: mounted.workflow } });
    }
    await route.fallback();
  });
  await page.route("**/workflow-v2/confirm", async route => {
    confirmations++;
    expect(route.request().headers()["if-match"]).toBe('"7"');
    expect(route.request().postDataJSON()).toEqual({ confirmed_by: "operator" });
    Object.assign(mounted.workflow, { revision: 8, state: "confirmed", confirmed_snapshot_id: "CONFIRMED-E2E-001" });
    await route.fulfill({ status: 200, json: { workflow: mounted.workflow } });
  });
  await page.getByRole("button", { name: "確定して一覧にもどる", exact: true }).click();
  await expect(page).toHaveURL(/\/(?:hospital\/)?orders$/);
  expect(confirmations).toBe(1);
  expect(postConfirmReads).toBe(0);
  expect(Object.keys(mounted.requestCounts).filter(key => /training/.test(key))).toEqual([]);
});

test("an already confirmed order returns without reconfirming or regenerating output", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, { workflowState: "confirmed",
    workflow: { confirmed_snapshot_id: "CONFIRMED-E2E-001", output_bundle_id: null } });
  const mutations: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname.includes(`/orders/${mounted.orderId}/workflow-v2/`)
      && request.method() !== "GET") mutations.push(request.url());
  });
  await page.getByRole("button", { name: "一覧にもどる", exact: true }).click();
  await expect(page).toHaveURL(/\/(?:hospital\/)?orders$/);
  expect(mutations).toEqual([]);
});

test("legacy order entry redirects to the current workflow and missing original PDF is not replaced by an OCR image", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, { entryPath: "legacy", workflowState: "uploaded",
    ocrResults: [{ ocr_result_id: "OCR-E2E-001", selected: true, selectable: true,
      overlay_url: "/synthetic-ocr-only.png", sheet_review_base_url: "" }] });
  await expect(page).toHaveURL(new RegExp(`/orders/${mounted.orderId}/workflow-v2$`));
  await expect(page.getByText("原本FAX PDFを現在取得できません。", { exact: true }).first()).toBeVisible();
  await expect(page.locator('iframe[title="workflow-v2-original-pdf"]')).toHaveCount(0);
  expect(mounted.requestCounts[`GET /api/orders/${mounted.orderId}/document`]).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: "注文処理 v2", exact: true })).toBeVisible();
  const second = await mountWorkflowV2SheetPage(page, {
    orderId: "ORD-E2E-WORKFLOW-SECOND", documentId: "DOC-E2E-SECOND",
    workflowState: "uploaded", originalPdf: secondOrderPreviewPdf,
  });
  await expect(page.locator('iframe[title="workflow-v2-original-pdf"]')).toHaveAttribute("src", /^blob:/);
  await expect(page.getByText("原本FAX PDFを現在取得できません。", { exact: true })).toHaveCount(0);
  expect(second.requestCounts[`GET /api/orders/${second.orderId}/document`]).toBeGreaterThan(0);
});

test("an explicitly saved exception period survives stale week options and document reload", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, { workflowState: "uploaded" });
  const writes: unknown[] = [];
  await page.route("**/workflow-v2/context", async route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["if-match"]).toBe('"7"');
    const body = route.request().postDataJSON();
    expect(body).toEqual({ facility_id: "FAC-E2E-001", week_start: "2026-05-20", week_end: "2026-05-22" });
    writes.push(body);
    Object.assign(mounted.workflow, body, { revision: 8, state: "context_confirmed" });
    await route.fulfill({ status: 200, json: { workflow: mounted.workflow } });
  });
  await page.getByText("例外範囲を指定する", { exact: true }).click();
  const range = page.locator(".exception-range-details");
  await range.locator('input[type="date"]').nth(0).fill("2026-05-20");
  await range.locator('input[type="date"]').nth(1).fill("2026-05-22");
  await range.getByRole("button", { name: "反映", exact: true }).click();
  const explicit = "2026-05@2026-05-20~2026-05-22";
  await expect(page.getByLabel("週 (Step1 必須)")).toHaveValue(explicit);
  await page.getByRole("button", { name: "設定を保存", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  await expect(page.getByLabel("週 (Step1 必須)")).toHaveValue(explicit);
  await page.reload();
  await expect(page.getByLabel("週 (Step1 必須)")).toHaveValue(explicit);
  await expect(page.getByLabel("施設 (Step1 必須)")).toHaveValue("FAC-E2E-001");
  expect(writes).toHaveLength(1);
});

for (const artifactsAvailable of [true, false]) test(`owned OCR completion refreshes candidates and artifact recovery: ${artifactsAvailable}`, async ({ page }) => {
  const orderId = "ORD-E2E-WORKFLOW-SHEET";
  const result = { ocr_result_id: "OCR-E2E-COMPLETED", selected: false,
    selectable: artifactsAvailable, detail_state: "loaded", status: "done", overlay_url: "",
    sheet_review_base_url: "", overlay_message: artifactsAvailable ? "" : "owned-artifact-unavailable" };
  const mounted = await mountWorkflowV2SheetPage(page, {
    workflowState: "ocr_running", workflow: { selected_ocr_result_id: null, saved_sheet_id: null }, ocrResults: [],
    polledWorkflow: { revision: 8, state: "ocr_completed", selected_ocr_result_id: null,
      ocr_job: { order_id: orderId, ocr_job_id: "OCR-E2E-RECORDED", workflow_attempt_id: "ATTEMPT-E2E-RECORDED", status: "done" } },
    polledOcrResults: [result],
  });
  await expect(page.getByRole("heading", { name: "正解 OCR を一つ選ぶ", exact: true })).toBeVisible();
  const card = page.locator(".ocr-card").filter({ hasText: "OCR-E2E-COMPLETED" });
  await expect(card).toBeVisible();
  await expect.poll(() => mounted.requestCounts[`GET /api/orders/${orderId}/workflow-v2/ocr-progress`] ?? 0).toBeGreaterThan(0);
  expect(mounted.requestCounts[`GET /api/orders/${orderId}/workflow-v2/inspection`]).toBeGreaterThanOrEqual(2);
  const select = card.getByRole("button", { name: "正解にする", exact: true });
  if (artifactsAvailable) await expect(select).toBeEnabled();
  else {
    await expect(select).toBeDisabled();
    await expect(card.getByText("owned-artifact-unavailable", { exact: true })).toBeVisible();
  }
  await expect(page.locator(".ocr-progress-inline")).toHaveCount(0);
});

test("real cell Tab and row focus move only their matching overlay highlights", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    ocrResults: [{ ocr_result_id: "OCR-E2E-001", selected: true, selectable: true,
      detail_state: "loaded", sheet_review_base_url: "",
      overlay_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAI0lEQVR4nGP8//8/AymAiSTVDKMaiANMRKqDg1ENxACSQwkAVW0DHeN02ZEAAAAASUVORK5CYII=" }],
    targetCells: [
      { target_row_index: 0, target_col_index: 3, field: "qty.regular", bbox: [0.2, 0.1, 0.4, 0.3] },
      { target_row_index: 0, target_col_index: 4, field: "qty.soft", bbox: [0.4, 0.1, 0.6, 0.3] },
      { target_row_index: 1, target_col_index: 3, field: "qty.regular", bbox: [0.2, 0.4, 0.4, 0.6] },
    ],
  });
  const image = page.locator(".step3-overlay-image");
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBeGreaterThan(0);
  const first = page.locator('[data-sheet-row="0"][data-sheet-col="3"]');
  const adjacent = page.locator('[data-sheet-row="0"][data-sheet-col="4"]');
  await first.click();
  const highlight = page.locator(".overlay-cell-highlight");
  await expect(highlight).toBeVisible();
  const original = await highlight.boundingBox();
  expect(original).not.toBeNull();
  await first.press("Tab");
  await expect(adjacent).toBeFocused();
  await expect(page.getByText("現在セル: R1 C5 / overlay対応あり", { exact: true })).toBeVisible();
  await expect.poll(async () => (await highlight.boundingBox())?.x).not.toBe(original!.x);
  await page.locator('[data-sheet-row="1"][data-sheet-col="3"]').click();
  await expect(page.getByText("現在セル: R2 C4 / overlay対応あり", { exact: true })).toBeVisible();
  await expect.poll(async () => (await highlight.boundingBox())?.y).not.toBe(original!.y);
  await expect(page.locator(".overlay-row-highlight")).toBeVisible();
  await expect(page.locator(".overlay-col-highlight")).toBeVisible();
  await expect(first).toHaveValue("1");
  await expect(adjacent).toHaveValue("2");
});
