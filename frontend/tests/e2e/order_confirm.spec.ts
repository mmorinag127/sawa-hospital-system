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

  await expect(page.getByText("注文詳細")).toBeVisible();
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
