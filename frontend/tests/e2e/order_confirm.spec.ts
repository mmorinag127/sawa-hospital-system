import { Buffer } from "node:buffer";
import { test, expect } from "@playwright/test";

test("order confirm flow with inline edits and PDF viewer", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-001";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    lines: [
      {
        line_id: "L1",
        date: "2026-01-03",
        daypart: "朝",
        menu_name: "Menu A",
        diet_type: "普通",
        area_id: "A",
        bag_type: "通常",
        quantity_original: 5,
        quantity_corrected: null,
      },
      {
        line_id: "L2",
        date: "2026-01-03",
        daypart: "朝",
        menu_name: "Menu B",
        diet_type: "普通",
        area_id: "A",
        bag_type: "通常",
        quantity_original: 3,
        quantity_corrected: 2,
      },
    ],
  };
  const pdfBody = "%PDF-1.4";

  let linesPayload: any = null;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route(`**/orders/${orderId}`, async (route) => {
    if (route.request().resourceType() === "document") {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, json: orderPayload });
  });
  await page.route(`**/orders/${orderId}/document`, async (route) => {
    await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
  });
  await page.route(`**/orders/${orderId}/lines`, async (route) => {
    linesPayload = route.request().postDataJSON();
    await route.fulfill({ status: 200, json: { updated: true } });
  });
  await page.route(`**/orders/${orderId}/confirm`, async (route) => {
    await route.fulfill({ status: 202, json: { accepted: true } });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await expect(page.locator(".status-pill")).toHaveText("要確認");
  await expect(page.locator("iframe[title=\"order-pdf\"]")).toBeVisible();

  const qtyInput = page.getByRole("spinbutton").first();
  await qtyInput.fill("10");

  await page.getByRole("button", { name: "保存 (要確認のまま)" }).click();
  await expect
    .poll(() => linesPayload, { timeout: 2000 })
    .not.toBeNull();
  expect(linesPayload.lines[0].quantity_corrected).toBe(10);

  const confirmResponse = page.waitForResponse(`**/orders/${orderId}/confirm`);
  await page.getByRole("button", { name: "確定" }).click();
  await confirmResponse;
});

test("overlay unavailable shows recovery-only flow and blocks edit actions", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-OVERLAY-001";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "",
    facility: "FAC-OVR",
    week: "2026-03",
    week_value: "2026-03",
    week_label: "2026-03",
    lines: [],
    ocr_status: "failed",
    ocr_error: "overlay unavailable",
    ocr_auto_apply_blocked: true,
    ocr_can_apply_draft: false,
    ocr_can_confirm: false,
    ocr_processing_stage: "ocr",
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`)) {
      if (method === "GET") {
        await route.fulfill({ status: 200, json: orderPayload });
        return;
      }
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.abort("failed");
      return;
    }
    if (path.endsWith(`/facilities/${orderPayload.facility}`)) {
      await route.fulfill({
        status: 200,
        json: { id: orderPayload.facility, name: "Facility Overlay Test", config: {} },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({ status: 200, json: { pages: [], message: "OCRページが取得できません。" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: { fields: [], header: [], rows: [], row_ids: [], source: "edited_sheet" },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "failed" } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await page.locator(".step-tab").nth(1).click();
  await page.getByRole("heading", { name: "OCR修正" }).waitFor({ state: "visible" });

  const recoveryButton = page.getByRole("button", { name: "復旧を試す" });
  const hasRecovery = await recoveryButton.isVisible().catch(() => false);
  if (hasRecovery) {
    await expect(recoveryButton).toBeVisible();
  } else {
    await expect(page.getByText(/オーバーレイが取得できないため|復旧を/)).toBeVisible({ timeout: 5000 });
  }
  await expect(page.getByRole("button", { name: "明細に反映して次へ" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "シートを保存（暫定）" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "行を追加" })).toHaveCount(0);
});
