import { expect, test } from "@playwright/test";

for (const latestResolved of [false, true]) {
  test(`Step4 uses latest saved candidate, resolved=${latestResolved}`, async ({ page }) => {
    const orderId = "ORD-OWNED-MENU-RECOVERY";
    const affected = [{ line_id: "latest-line", date: "2099-06-07", daypart: "朝食", menu_name: "Latest Dish",
      diet_type: "regular", area_id: "explicit-area", unresolved_fields: ["qty_per_serving"] }];
    const resolution = (resolved: boolean) => ({ status: resolved ? "resolved" : "unresolved",
      blocker: resolved ? null : "menu_amount_unresolved", affected_lines: resolved ? [] : affected });
    const posts: string[] = [];
    await page.addInitScript(() => {
      localStorage.setItem("auth_header", "Bearer owned-headless-token");
      sessionStorage.setItem("auth_header", "Bearer owned-headless-token");
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET") posts.push(path);
      let json: object = {};
      if (path.endsWith(`/orders/${orderId}/workflow-v2`)) {
        json = { order_id: orderId, state: "output_review", headline: "Owned recovery loaded", blockers: [],
          saved_sheet_id: "owned-draft", output_bundle_id: "owned-bundle", menu_amount_resolution: resolution(latestResolved) };
      } else if (path.endsWith(`/orders/${orderId}`)) {
        json = { id: orderId, versions: [], menu_amount_resolution: resolution(!latestResolved),
          lines: [{ menu_name: "Old Dish", quantity_original: 11, quantity_corrected: 13 }] };
      } else if (path.endsWith("/ocr-results")) {
        json = { results: [] };
      } else if (path.endsWith("/facilities")) {
        json = { facilities: [] };
      }
      await route.fulfill({ status: 200, json });
    });
    await page.goto(`/hospital/orders/${orderId}/workflow-v2`);
    await expect(page.getByText("Owned recovery loaded")).toBeVisible();
    const bagging = page.getByRole("button", { name: "出力確認を作成", exact: true });
    const confirm = page.getByRole("button", { name: "確定して一覧にもどる", exact: true });
    if (latestResolved) {
      await expect(bagging).toBeEnabled();
      await expect(confirm).toBeEnabled();
      await expect(page.locator(".notice.error").filter({ hasText: "月次メニューの一人量" })).toHaveCount(0);
    } else {
      await expect(bagging).toBeDisabled();
      await expect(confirm).toBeDisabled();
      await expect(page.locator(".notice.error").filter({ hasText: "月次メニューの一人量" })).toBeVisible();
      await expect(page.getByText(/2099-06-07.*Latest Dish.*explicit-area/)).toBeVisible();
    }
    expect(posts).toEqual([]);
  });
}
