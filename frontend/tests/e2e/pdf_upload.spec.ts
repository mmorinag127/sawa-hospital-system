import { expect, test } from "@playwright/test";

test("pdf upload page shows uploaded PDF rows and allows retry for incomplete rows", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  let retried = false;
  const requests: Array<{ method: string; path: string }> = [];

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  page.on("dialog", async (dialog) => {
    await dialog.accept();
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith("/auth/me") && method === "GET") {
      await route.fulfill({ status: 200, json: { role: "operator" } });
      return;
    }

    if (path.endsWith("/ingest/uploads") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          items: [
            {
              id: "UPL-pending",
              original_filename: "pending.pdf",
              message_id: "msg-pending",
              received_at: "2026-04-06T10:00:00",
              status: retried ? "pending" : "manual_review",
              current_stage: retried ? "uploaded" : "manual_review",
              attempt_count: retried ? 1 : 2,
              max_attempts: 5,
              facility_hint: "FAC00001",
              week_hint: "2026-04@2026-04-05~2026-04-11",
              current_order_id: null,
              last_error_message: retried ? null : "ocr timeout",
            },
            {
              id: "UPL-completed",
              original_filename: "completed.pdf",
              message_id: "msg-completed",
              received_at: "2026-04-06T09:30:00",
              status: "completed",
              current_stage: "completed",
              attempt_count: 1,
              max_attempts: 5,
              facility_hint: "FAC00002",
              week_hint: "2026-04@2026-04-05~2026-04-11",
              current_order_id: "ORD-COMPLETE-001",
              last_error_message: null,
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/ingest/uploads/UPL-pending/retry") && method === "POST") {
      requests.push({ method, path });
      retried = true;
      await route.fulfill({ status: 202, json: { accepted: true, item: { id: "UPL-pending", status: "pending", current_stage: "uploaded" } } });
      return;
    }

    if (path.endsWith("/orders") && method === "GET") {
      await route.fulfill({ status: 200, json: { orders: [] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/pdf-upload`);

  await expect(page.getByRole("heading", { name: "最近の取込PDF" })).toBeVisible();
  await expect(page.getByText("pending.pdf")).toBeVisible();
  await expect(page.getByText("completed.pdf")).toBeVisible();
  await expect(page.locator(".history-card").filter({ hasText: "pending.pdf" }).getByText("要介入").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "注文詳細を開く" })).toBeVisible();
  await expect(page.locator(".history-card").filter({ hasText: "completed.pdf" }).getByRole("button", { name: "再処理に戻す" })).toBeDisabled();

  await page.locator(".history-card").filter({ hasText: "pending.pdf" }).getByRole("button", { name: "再処理に戻す" }).click();

  await expect(page.getByText("「pending.pdf」を再処理に戻しました。")).toBeVisible();
  await expect(page.locator(".history-card").filter({ hasText: "pending.pdf" }).getByText("未処理")).toBeVisible();
  expect(requests).toEqual([{ method: "POST", path: "/api/ingest/uploads/UPL-pending/retry" }]);
});
