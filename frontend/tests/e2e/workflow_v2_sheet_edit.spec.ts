import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

type WorkflowMountOptions = {
  contextSuggestion?: Record<string, unknown>;
  facilitiesStatus?: number;
  facilities?: Array<{ id: string; name: string }>;
  workflowFacilityId?: string;
  workflowRevision?: number;
  workflowSource?: string;
  workflowState?: string;
  polledWorkflow?: Partial<Record<string, unknown>>;
  contextSaveStatus?: number;
};

async function mountWorkflowV2SheetPage(page: Page, options: WorkflowMountOptions = {}) {
  const orderId = "ORD-E2E-WORKFLOW-SHEET";
  const sheetPayload = {
    fields: ["date_mmdd", "daypart", "menu", "qty.regular", "qty.soft"],
    header: ["日付", "区分", "メニュー", "常食", "軟菜"],
    rows: [
      ["05/22", "朝", "Menu A", "1", "2"],
      ["05/22", "昼", "Menu B", "3", "4"],
      ["05/22", "夕", "Menu C", "5", "6"],
    ],
    row_ids: ["row-1", "row-2", "row-3"],
    target_cell_map: [],
    ocr_numeric_cell_summary: {},
  };
  const workflowPayload = {
    order_id: orderId,
    revision: options.workflowRevision ?? 7,
    source: options.workflowSource ?? "workflow_v2",
    state: options.workflowState ?? "ocr_selected",
    headline: "正解OCRを選択済みです。シートを編集してください。",
    selected_ocr_result_id: "OCR-E2E-001",
    facility_id: options.workflowFacilityId ?? "FAC-E2E-001",
    week_start: "2026-05-17",
    week_end: "2026-05-23",
    template_version_id: "TEMPLATE-E2E-001",
    context_suggestion: options.contextSuggestion,
    pre_save_checks: {
      anomaly_review: { confirmed: true },
      sheet_review: { confirmed: true },
    },
  };
  let savePayload: any = null;
  let nextWorkflowRevision = 7;
  const requestCounts: Record<string, number> = {};
  const workflowMutationHeaders: Array<{ path: string; ifMatch: string | undefined }> = [];

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();
    requestCounts[`${method} ${path}`] = (requestCounts[`${method} ${path}`] ?? 0) + 1;

    if (path.endsWith(`/orders/${orderId}/workflow-v2`) && method === "GET") {
      const pollUpdate = requestCounts[`${method} ${path}`] > 1 ? options.polledWorkflow : null;
      await route.fulfill({ status: 200, json: pollUpdate ? { ...workflowPayload, ...pollUpdate } : workflowPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/ocr-results`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          results: [
            {
              ocr_result_id: "OCR-E2E-001",
              selected: true,
              overlay_url: "",
              sheet_review_base_url: "",
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/inspection`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          workflow: workflowPayload,
          saved_sheet: {
            saved_sheet_id: "SHEET-E2E-001",
            source_ocr_result_id: "OCR-E2E-001",
            sheet: sheetPayload,
            edited_at: "2026-05-22T00:00:00Z",
          },
          pre_save_status: {
            anomaly_review_confirmed: true,
            sheet_review_confirmed: true,
          },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet`) && method === "PUT") {
      savePayload = route.request().postDataJSON();
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: nextWorkflowRevision }, saved_sheet_id: "SHEET-E2E-002" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/context`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      if (options.contextSaveStatus === 409) {
        await route.fulfill({ status: 409, json: { detail: "workflow revision conflict" } });
        return;
      }
      nextWorkflowRevision += 1;
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: nextWorkflowRevision } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/anomaly-review`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: nextWorkflowRevision }, anomaly_review: {}, pre_save_checks: {}, pre_save_status: {} } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/review-confirm`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: nextWorkflowRevision }, pre_save_checks: {}, pre_save_status: {} } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/auto-edit`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: nextWorkflowRevision }, job: { job_id: "AUTO-E2E-001", status: "running" } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/auto-edit/AUTO-E2E-001`) && method === "GET") {
      await route.fulfill({ status: 200, json: { job: { job_id: "AUTO-E2E-001", status: "done", workflow_result_revision: 9 }, result: { status: "done", patches: [] } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/quad-review`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          suggested_quad_px: [[1, 1], [20, 1], [20, 30], [1, 30]],
          estimate: { status: "ok", refined_quad_px: [[1, 1], [20, 1], [20, 30], [1, 30]] },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/quad-review`) && method === "PUT") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: 8 } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/ocr-runs`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      await route.fulfill({ status: 200, json: { workflow: { ...workflowPayload, revision: 9 } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          id: orderId,
          version_count: 1,
          current_version: { document_id: "DOC-E2E-001", is_current: true },
          versions: [{ document_id: "DOC-E2E-001", is_current: true }],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          options: [
            {
              week_id: "2026-05@2026-05-17~2026-05-23",
              label: "05/17 - 05/23",
              selected: true,
            },
          ],
        },
      });
      return;
    }
    if (path.startsWith("/api/facilities/") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          id: path.split("/").at(-1),
          name: "Workflow Sheet E2E Facility",
          config: {},
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities") && method === "GET") {
      if (options.facilitiesStatus && options.facilitiesStatus !== 200) {
        await route.fulfill({ status: options.facilitiesStatus, json: { detail: "facility source unavailable" } });
        return;
      }
      await route.fulfill({
        status: 200,
        json: { facilities: options.facilities ?? [{ id: "FAC-E2E-001", name: "Workflow Sheet E2E Facility" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}/workflow-v2`);
  return {
    orderId,
    requestCounts,
    getSavePayload: () => savePayload,
    getWorkflowMutationHeaders: () => workflowMutationHeaders,
  };
}

test("workflow-v2 reflects an OCR facility suggestion only after a canonical facility match", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    contextSuggestion: { facility_id: "FAC-E2E-001", facility_name: "PDF候補施設" },
    workflowFacilityId: "",
  });

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

  await page.getByRole("button", { name: "推定を反映" }).click();
  await expect(page.getByText("この施設IDは正本の施設一覧に未登録です。内容を確認するまで保存・OCR実行はできません。")).toBeVisible();
  await expect(page.getByLabel("施設 (Step1 必須)").locator("option")).not.toContainText("FAC-NOT-REGISTERED");
});

test("workflow-v2 visibly blocks context actions when canonical facility retrieval fails", async ({ page }) => {
  await mountWorkflowV2SheetPage(page, {
    contextSuggestion: { facility_id: "FAC-E2E-001", facility_name: "PDF候補施設" },
    facilitiesStatus: 500,
    workflowFacilityId: "",
  });

  await expect(page.getByText("正本の施設一覧を取得できません。施設の反映・保存・OCR実行はできません。").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "設定を保存" })).toBeDisabled();
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
  await mountWorkflowV2SheetPage(page);

  await expect(page.getByRole("heading", { name: "選択 OCR からシート作成 / 編集 / 保存" })).toBeVisible();
  const firstQuantity = page.locator('[data-sheet-row="0"][data-sheet-col="3"]');
  await firstQuantity.fill("789");
  await page.getByRole("button", { name: "シート確認", exact: true }).click();
  await expect(firstQuantity).toHaveValue("789");

  await firstQuantity.fill("987");
  const sheetJsonDetails = page.locator(".json-details").filter({ hasText: "保存予定JSONを確認" });
  await sheetJsonDetails.click();
  await expect(sheetJsonDetails.locator("textarea")).toHaveValue(/"987"/);
  await expect(sheetJsonDetails.locator("textarea")).not.toHaveValue(/"789"/);
});

test("workflow-v2 quad rerun inherits the revision returned by its own save", async ({ page }) => {
  const mounted = await mountWorkflowV2SheetPage(page);

  await page.getByRole("button", { name: "4点確認/補正", exact: true }).click();
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
  await page.getByRole("button", { name: "シート確認", exact: true }).click();
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
  await page.getByRole("button", { name: "シート確認", exact: true }).click();
  await expect.poll(() => mounted.getWorkflowMutationHeaders().length).toBe(2);
  expect(mounted.getWorkflowMutationHeaders()).toEqual([
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet/auto-edit`, ifMatch: '"7"' },
    { path: `/api/orders/${mounted.orderId}/workflow-v2/sheet/review-confirm`, ifMatch: '"9"' },
  ]);
});
