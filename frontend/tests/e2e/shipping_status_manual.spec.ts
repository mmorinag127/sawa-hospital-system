import { expect, test } from "@playwright/test";

test("shipping page minimizes not-shipped tracking numbers by default", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith("/auth/me") && method === "GET") {
      await route.fulfill({ status: 200, json: { role: "operator" } });
      return;
    }

    if (path.endsWith("/shipping/track-status") && method === "POST") {
      await route.fulfill({
        status: 200,
        json: {
          items: [
            {
              tracking_key: "111111111111",
              tracking_number: "1111-1111-1111",
              status: "配達中",
              delivered: false,
              arrival_text: null,
              error: null,
            },
            {
              tracking_key: "222222222222",
              tracking_number: "2222-2222-2222",
              status: "発送しなかった",
              delivered: false,
              arrival_text: null,
              error: null,
            },
          ],
          summary: {
            total: 2,
            delivered: 0,
            pending: 1,
            all_delivered: false,
          },
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/shipping`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("伝票番号（改行・スペース・カンマ区切り）").fill("1111-1111-1111\n2222-2222-2222");
  await page.getByRole("button", { name: "追跡状況を取得" }).click();

  const mainTable = page.locator(".track-table").first();
  await expect(mainTable.getByText("1111-1111-1111")).toBeVisible();
  await expect(mainTable.getByText("2222-2222-2222")).toHaveCount(0);
  const minimized = page.getByTestId("shipping-not-shipped-minimized");
  await expect(minimized).toContainText("発送しなかった番号 1件");
  await expect(minimized.getByText("2222-2222-2222")).not.toBeVisible();
  await minimized.getByText("発送しなかった番号 1件").click();
  await expect(minimized.getByText("2222-2222-2222")).toBeVisible();
});
