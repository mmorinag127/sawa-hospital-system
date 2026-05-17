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
              status: "発送しなかった",
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

    if (path.endsWith("/shipping/status/history") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          summary: {
            total: 2,
            delivered: 1,
            pending: 1,
            errors: 0,
          },
          items: [
            {
              id: "log-1",
              tracking_key: "444444444444",
              tracking_number: "4444-4444-4444",
              facility_name: "施設D",
              status: "発送済み",
              delivered: true,
              source: "manual_status",
              looked_up_at: "2026-03-28T03:00:00Z",
              ship_date: "2026-03-28",
            },
            {
              id: "log-2",
              tracking_key: "555555555555",
              tracking_number: "5555-5555-5555",
              facility_name: "施設E",
              status: "配達中",
              delivered: false,
              source: "manual_track",
              looked_up_at: "2026-03-28T04:00:00Z",
              ship_date: "2026-03-28",
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
  await expect(page.getByText("2026/03/30")).toBeVisible();
  await expect(page.getByText("2026/03/29")).toBeVisible();
  const firstDateGroup = page.getByTestId("shipping-date-group").first();
  await expect(page.getByTestId("shipping-calendar")).toBeVisible();
  await expect(firstDateGroup).toContainText("3件");
  await expect(firstDateGroup).toContainText("完了 1 / 未完了 1");
  await expect(firstDateGroup).toContainText("発送なし 1");
  await expect(page.getByText("1111-1111-1111")).toHaveCount(0);
  await expect(page.getByText("2222-2222-2222")).toHaveCount(0);
  await firstDateGroup.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".week-group-summary").first()).toBeVisible();
  await expect(dialog.locator(".facility-slot").first()).toBeVisible();
  const firstCard = dialog.getByTestId("shipping-facility-card").first();
  await expect(firstCard).toContainText("該当無し");
  await expect(firstCard.getByTestId("shipping-tracking-card").first()).toBeVisible();
  await expect(dialog.getByText("施設B")).toBeVisible();
  const groupedFacilityCard = dialog
    .getByTestId("shipping-facility-card")
    .filter({ hasText: "いこいの森 / いこいの森プラス" })
    .filter({ hasText: "1111-1111-1111" })
    .first();
  await expect(groupedFacilityCard).toBeVisible();
  await expect(page.getByText("1111-1111-1111")).toBeVisible();
  await expect(page.getByText("1111-1111-1112")).not.toBeVisible();
  await expect(page.getByText("2222-2222-2222")).toBeVisible();
  const eventTrackingCard = groupedFacilityCard
    .getByTestId("shipping-tracking-card")
    .filter({ hasText: "1111-1111-1111" })
    .first();
  await expect(eventTrackingCard).toBeVisible();
  await expect(groupedFacilityCard.getByTestId("shipping-tracking-card")).toHaveCount(1);
  await expect(groupedFacilityCard).not.toContainText("2222-2222-2222");
  const notShippedSection = dialog.getByTestId("not-shipped-minimized");
  await expect(notShippedSection).toContainText("発送しなかった番号 1件");
  await expect(notShippedSection.getByText("1111-1111-1112")).not.toBeVisible();
  await notShippedSection.getByText("発送しなかった番号 1件").click();
  await expect(notShippedSection.getByText("1111-1111-1112")).toBeVisible();
  const layoutMetrics = await dialog.evaluate((node) => {
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

  await page.getByRole("button", { name: "監査ログ" }).click();
  await expect(page.getByRole("heading", { name: "監査ログカレンダー" })).toBeVisible();
  await expect(page.getByText("2026/03/28")).toBeVisible();
  await expect(page.locator("table")).toHaveCount(0);
  await expect(page.getByText("4444-4444-4444")).toHaveCount(0);
  await page.getByTestId("shipping-date-group").filter({ hasText: "2026/03/28" }).click();
  await expect(page.getByRole("dialog").getByText("4444-4444-4444")).toBeVisible();
});
