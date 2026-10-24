import { expect, test } from "@playwright/test";
import { mountWorkflowV2SheetPage } from "../helpers/workflow-v2-browser";

test("workflow-v2 reflects an OCR facility suggestion only after a canonical facility match", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    contextSuggestion: { facility_id: "FAC-E2E-001", facility_name: "PDF候補施設" },
    workflowFacilityId: "",
  });

  await page.getByRole("button", { name: /Step1\s.*施設/ }).click();
  await expect(page.getByLabel("施設 (Step1 必須)")).toHaveValue("");
  await page.getByRole("button", { name: "推定を反映" }).click();
  await expect(page.getByLabel("施設 (Step1 必須)")).toHaveValue("FAC-E2E-001");
});

test("workflow-v2 never requests disabled legacy fax template endpoints", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);

  expect(mounted.requestCounts["GET /api/facilities/fax-template-options"] ?? 0).toBe(0);
  expect(mounted.requestCounts["PUT /api/facilities/FAC-E2E-001/fax-template"] ?? 0).toBe(0);
});

test("workflow-v2 blocks an unregistered OCR facility suggestion without adding it to the selector", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    contextSuggestion: { facility_id: "FAC-NOT-REGISTERED", facility_name: "未登録候補" },
    workflowFacilityId: "",
  });

  await page.getByRole("button", { name: /Step1\s.*施設/ }).click();
  await page.getByRole("button", { name: "推定を反映" }).click();
  await expect(page.getByText("この施設IDは正本の施設一覧に未登録です。内容を確認するまで保存・OCR実行はできません。")).toBeVisible();
  await expect(page.getByLabel("施設 (Step1 必須)").locator('option[value="FAC-NOT-REGISTERED"]')).toHaveCount(0);
});

test("workflow-v2 visibly blocks context actions when canonical facility retrieval fails", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    contextSuggestion: { facility_id: "FAC-E2E-001", facility_name: "PDF候補施設" },
    facilitiesStatus: 500,
    workflowFacilityId: "",
  });

  await page.getByRole("button", { name: /Step1\s.*施設/ }).click();
  await expect(page.getByText("正本の施設一覧を取得できません。施設の反映・保存・OCR実行はできません。").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "施設と週を選択" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "OCRを実行" })).toBeDisabled();
});

test("workflow-v2 sheet edits stay local while typing and flush on save", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);

  await expect(page.getByRole("heading", { name: "選択 OCR からシート作成 / 編集 / 保存" })).toBeVisible();
  const firstQuantity = page.locator('[data-sheet-row="0"][data-sheet-col="3"]');
  const secondQuantity = page.locator('[data-sheet-row="1"][data-sheet-col="3"]');

  await firstQuantity.fill("123");
  await expect(firstQuantity).toHaveValue("123");
  expect(mounted.getSavePayload()).toBeNull();
  expect(mounted.requestCounts[`PUT /api/orders/${mounted.orderId}/workflow-v2/sheet`] ?? 0).toBe(0);

  await firstQuantity.press("Enter");
  await expect(secondQuantity).toBeFocused();
  await expect(firstQuantity).toHaveValue("123");

  await secondQuantity.fill("456");
  await expect(secondQuantity).toHaveValue("456");
  expect(mounted.getSavePayload()).toBeNull();

  await page.getByRole("button", { name: "シートを保存" }).click();
  await expect.poll(() => mounted.getSavePayload()).not.toBeNull();
  const savePayload = mounted.getSavePayload();
  expect(savePayload.sheet.rows[0][3]).toBe("123");
  expect(savePayload.sheet.rows[1][3]).toBe("456");
  expect(savePayload.sheet.rows[2][3]).toBe("5");
  expect(savePayload.edited_by).toBe("operator");
  await expect(page.getByText("シートを保存しました")).toBeVisible();
});

test("workflow-v2 keeps a dirty context draft and its old If-Match when polling a newer workflow revision", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, {
    workflowState: "ocr_running",
    facilities: [
      { id: "FAC-E2E-001", name: "Workflow Sheet E2E Facility" },
      { id: "FAC-E2E-MANUAL", name: "Manual Facility" },
    ],
    polledWorkflow: {
      revision: 8,
      facility_id: "FAC-E2E-001",
      week_start: "2026-05-24",
      week_end: "2026-05-30",
    },
    contextSaveStatus: 409,
  });

  const facility = page.getByLabel("施設 (Step1 必須)");
  await facility.selectOption("FAC-E2E-MANUAL");
  await page.waitForTimeout(3200);
  await expect(facility).toHaveValue("FAC-E2E-MANUAL");
  await expect(page.getByLabel("週 (Step1 必須)")).toHaveValue("2026-05@2026-05-17~2026-05-23");
  await page.getByRole("button", { name: "設定を保存" }).click();
  await expect(page.getByText("他の更新と競合しました。編集中の値は保持しています。最新状態を確認してから、もう一度保存してください。")).toBeVisible();
  expect(mounted.getWorkflowMutationHeaders()).toContainEqual({
    path: `/api/orders/${mounted.orderId}/workflow-v2/context`,
    ifMatch: '"7"',
  });
});

test("workflow-v2 adopts a polled context revision before a clean editor starts", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    workflowState: "ocr_running",
    polledWorkflow: {
      revision: 8,
      facility_id: "FAC-E2E-001",
      week_start: "2026-05-24",
      week_end: "2026-05-30",
    },
  });

  await page.waitForTimeout(3200);
  await expect(page.getByLabel("週 (Step1 必須)")).toHaveValue("2026-05@2026-05-24~2026-05-30");
});

test("workflow-v2 stops commands when a read workflow revision is zero", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, { workflowRevision: 0 });

  await page.getByRole("button", { name: /Step1\s.*施設/ }).click();
  await expect(page.getByText("workflow revision が不正です。再読込してから操作してください。")).toBeVisible();
  await page.getByRole("button", { name: "設定を保存" }).click();
  await expect(page.getByText("workflow revision を取得できません。再読込してから操作してください。")).toBeVisible();
});

test("workflow-v2 uses revision zero only for the explicit uninitialized creation token", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, {
    workflowRevision: 0,
    workflowState: "not_initialized",
    workflowSource: "workflow_v2_projection",
  });

  await page.getByRole("button", { name: "設定を保存" }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(1);
  expect(mounted.getWorkflowMutationHeaders()).toEqual([
    { path: `/api/orders/${mounted.orderId}/workflow-v2/context`, ifMatch: '"0"' },
  ]);
});

test("workflow-v2 resets the context input base from its own successful save", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page, {
    facilities: [
      { id: "FAC-E2E-001", name: "Workflow Sheet E2E Facility" },
      { id: "FAC-E2E-MANUAL", name: "Manual Facility" },
    ],
  });

  await page.getByRole("button", { name: /Step1\s.*施設/ }).click();
  await page.getByLabel("施設 (Step1 必須)").selectOption("FAC-E2E-MANUAL");
  await page.getByRole("button", { name: "設定を保存" }).click();
  await page.getByRole("button", { name: "設定を保存" }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(2);
  expect(mounted.getWorkflowMutationHeaders()).toEqual([
    { path: `/api/orders/${mounted.orderId}/workflow-v2/context`, ifMatch: '"7"' },
    { path: `/api/orders/${mounted.orderId}/workflow-v2/context`, ifMatch: '"8"' },
  ]);
});

test("workflow-v2 sheet helper displays use the latest blurred cell edits", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);

  await expect(page.getByRole("heading", { name: "選択 OCR からシート作成 / 編集 / 保存" })).toBeVisible();
  const firstQuantity = page.locator('[data-sheet-row="0"][data-sheet-col="3"]');
  await firstQuantity.fill("789");
  await page.locator('[aria-label="Step3 preview display mode"]').getByRole("button", { name: "シート確認", exact: true }).click();
  await expect(firstQuantity).toHaveValue("789");
  expect(mounted.getWorkflowMutationHeaders()).toEqual([]);

  await firstQuantity.fill("987");
  const sheetJsonDetails = page.locator(".json-details").filter({ hasText: "保存予定JSONを確認" });
  await sheetJsonDetails.click();
  await expect(sheetJsonDetails.locator("textarea:not([aria-hidden])")).toHaveValue(/"987"/);
  await expect(sheetJsonDetails.locator("textarea:not([aria-hidden])")).not.toHaveValue(/"789"/);
});

test("workflow-v2 quad rerun inherits the revision returned by its own save", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);

  await page.getByRole("button", { name: /Step1\.5.*4点確認\/補正/ }).click();
  await expect(page.getByRole("button", { name: "推定4点をOKにしてOCR再実行" })).toBeVisible();
  await page.getByRole("button", { name: "推定4点をOKにしてOCR再実行" }).click();

  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(2);
  expect(mounted.getWorkflowMutationHeaders()).toEqual([
    { path: `/api/orders/${mounted.orderId}/workflow-v2/quad-review`, ifMatch: '"7"' },
    { path: `/api/orders/${mounted.orderId}/workflow-v2/ocr-runs`, ifMatch: '"8"' },
  ]);
});

test("workflow-v2 sheet checks advance the edited sheet revision before save", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);
  const firstQuantity = page.locator('[data-sheet-row="0"][data-sheet-col="3"]');
  await firstQuantity.fill("123");

  await page.getByRole("button", { name: "異常チェック" }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(1);
  await page.locator(".step3-top-actions-left").getByRole("button", { name: "シート確認", exact: true }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(2);
  await page.getByRole("button", { name: "シートを保存" }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(3);

  expect(mounted.getWorkflowMutationHeaders()).toEqual([
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet/anomaly-review`, ifMatch: '"7"' },
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet/review-confirm`, ifMatch: '"8"' },
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet`, ifMatch: '"9"' },
  ]);
});

test("workflow-v2 auto-edit completion advances only the matching sheet input revision", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);
  const firstQuantity = page.locator('[data-sheet-row="0"][data-sheet-col="3"]');
  await firstQuantity.fill("123");
  await page.getByRole("button", { name: "AI自動補正を提案" }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(1);
  await page.waitForTimeout(1300);
  await page.locator(".step3-top-actions-left").getByRole("button", { name: /シート確認(?:済み)?$/, exact: true }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(2);
  expect(mounted.getWorkflowMutationHeaders()).toEqual([
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet/auto-edit`, ifMatch: '"7"' },
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet/review-confirm`, ifMatch: '"9"' },
  ]);
});
