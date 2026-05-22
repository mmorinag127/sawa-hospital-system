import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

async function mountWorkflowV2SheetPage(page: Page) {
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
    state: "ocr_selected",
    headline: "正解OCRを選択済みです。シートを編集してください。",
    selected_ocr_result_id: "OCR-E2E-001",
    facility_id: "FAC-E2E-001",
    week_start: "2026-05-17",
    week_end: "2026-05-23",
    pre_save_checks: {
      anomaly_review: { confirmed: true },
      sheet_review: { confirmed: true },
    },
  };
  let savePayload: any = null;
  const requestCounts: Record<string, number> = {};

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
      await route.fulfill({ status: 200, json: workflowPayload });
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
      await route.fulfill({ status: 200, json: { saved_sheet_id: "SHEET-E2E-002" } });
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
    if (path.endsWith("/facilities/FAC-E2E-001") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-E2E-001",
          name: "Workflow Sheet E2E Facility",
          config: {},
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities/fax-template-options") && method === "GET") {
      await route.fulfill({ status: 200, json: { templates: [] } });
      return;
    }
    if (path.endsWith("/facilities") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-E2E-001", name: "Workflow Sheet E2E Facility" }] },
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
  };
}

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
