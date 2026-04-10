import { expect, test } from "@playwright/test";

test("shipping history groups rows by date, nests numbers under facility cards, and keeps not-found rows inside each date group", async ({ page }) => {
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
      await route.fulfill({ status: 200, json: { role: "admin" } });
      return;
    }

    if (path.endsWith("/shipping/status/latest") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          view: "active",
          generated_at: "2026-03-31T00:00:00Z",
          summary: {
            total: 4,
            delivered: 1,
            pending: 3,
            errors: 0,
            attention: 2,
            facility_missing: 0,
          },
          items: [
            {
              tracking_key: "111111111111",
              tracking_number: "1111-1111-1111",
              facility_name: "いこいの森",
              status: "配達完了",
              delivered: true,
              source: "shipping_pdf_parse",
              looked_up_at: "2026-03-30T03:00:00Z",
              ship_date: "2026-03-30",
              events: [
                {
                  status: "配達完了",
                  occurred_at: "2026-03-30T02:50:00Z",
                  facility_name: "施設A",
                },
              ],
            },
            {
              tracking_key: "111111111112",
              tracking_number: "1111-1111-1112",
              facility_name: "いこいの森プラス",
              status: "持戻",
              delivered: false,
              source: "shipping_pdf_parse",
              looked_up_at: "2026-03-30T03:10:00Z",
              ship_date: "2026-03-30",
              events: [],
            },
            {
              tracking_key: "222222222222",
              tracking_number: "2222-2222-2222",
              facility_name: "施設B",
              status: "該当なし",
              delivered: false,
              source: "shipping_pdf_parse",
              looked_up_at: "2026-03-30T01:00:00Z",
              ship_date: "2026-03-30",
              events: [],
            },
            {
              tracking_key: "333333333333",
              tracking_number: "3333-3333-3333",
              facility_name: "施設C",
              status: "持戻",
              delivered: false,
              source: "manual_track",
              looked_up_at: "2026-03-29T02:00:00Z",
              ship_date: "2026-03-29",
              events: [],
            },
          ],
          quota: null,
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/shipping-history`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "日付ごとの出荷状況" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "2026/03/30" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "2026/03/29" })).toBeVisible();
  const firstDateGroup = page.getByTestId("shipping-date-group").first();
  const firstDateCounts = firstDateGroup.locator(".week-counts");
  await expect(firstDateCounts.getByText("3件")).toBeVisible();
  await expect(firstDateCounts.getByText("完了 1件")).toBeVisible();
  await expect(firstDateCounts.getByText("未完了 2件")).toBeVisible();
  await expect(page.getByText("1111-1111-1111")).toHaveCount(0);
  await expect(page.getByText("2222-2222-2222")).toHaveCount(0);
  await page.getByRole("button", { name: "開く" }).first().click();
  await expect(firstDateGroup.locator(".week-group-summary").first()).toBeVisible();
  await expect(firstDateGroup.locator(".facility-slot").first()).toBeVisible();
  const firstCard = firstDateGroup.getByTestId("shipping-facility-card").first();
  await expect(firstCard).toContainText("該当無し");
  await expect(firstCard.getByTestId("shipping-tracking-card").first()).toBeVisible();
  await expect(firstDateGroup.getByText("施設B")).toBeVisible();
  await expect(firstDateGroup.getByText("いこいの森 / いこいの森プラス")).toBeVisible();
  await expect(page.getByText("1111-1111-1111")).toBeVisible();
  await expect(page.getByText("1111-1111-1112")).toBeVisible();
  await expect(page.getByText("2222-2222-2222")).toBeVisible();
  const groupedFacilityCard = firstDateGroup
    .getByTestId("shipping-facility-card")
    .filter({ hasText: "いこいの森 / いこいの森プラス" })
    .first();
  const eventTrackingCard = groupedFacilityCard
    .getByTestId("shipping-tracking-card")
    .filter({ hasText: "1111-1111-1111" })
    .first();
  await expect(eventTrackingCard).toBeVisible();
  await expect(groupedFacilityCard.getByTestId("shipping-tracking-card")).toHaveCount(2);
  await expect(groupedFacilityCard).not.toContainText("2222-2222-2222");
  const layoutMetrics = await firstDateGroup.evaluate((node) => {
    const grid = node.querySelector(".facility-slot-grid");
    const facilityCard = node.querySelector('[data-testid="shipping-facility-card"]');
    const trackingCard = node.querySelector('[data-testid="shipping-tracking-card"]');
    if (!grid || !facilityCard || !trackingCard) {
      return null;
    }
    const gridStyle = window.getComputedStyle(grid);
    const facilityStyle = window.getComputedStyle(facilityCard);
    const trackingStyle = window.getComputedStyle(trackingCard);
    return {
      gridAlignItems: gridStyle.alignItems,
      facilityBackground: facilityStyle.backgroundColor,
      trackingMinHeight: trackingStyle.minHeight,
    };
  });
  expect(layoutMetrics).not.toBeNull();
  expect(layoutMetrics?.gridAlignItems).toBe("start");
  expect(layoutMetrics?.facilityBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(layoutMetrics?.trackingMinHeight).toBe("0px");
  await page.getByRole("button", { name: "閉じる" }).first().click();
  await expect(page.getByText("1111-1111-1111")).toHaveCount(0);
});
