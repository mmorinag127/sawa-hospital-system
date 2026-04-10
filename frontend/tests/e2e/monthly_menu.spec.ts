import { expect, test } from "@playwright/test";

test("monthly menu index redirects to the latest registered month", async ({ page }) => {
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

    if (path.endsWith("/monthly-menus/latest") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          menu: { id: "2026-03", display_name: "2026年3月 献立" },
          items: [],
          entries: [],
        },
      });
      return;
    }

    if (path.endsWith("/monthly-menus/scope-options") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          facilities: [],
          tags: [],
        },
      });
      return;
    }

    if (path.endsWith("/monthly-menus/2026-03/uploads") && method === "GET") {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }

    if (path.endsWith("/monthly-menus/2026-03") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          menu: {
            id: "2026-03",
            filename: "menu.xlsx",
            display_name: "2026年3月 献立",
          },
          items: [],
          entries: [],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus`);

  await expect(page).toHaveURL(/\/menus\/2026-03$/);
  await expect(page.getByRole("heading", { name: "月次メニュー編集" })).toBeVisible();
});

test("monthly menu page renders entries in a sheet-style grid", async ({ page }) => {
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

    if (path.endsWith("/monthly-menus/scope-options") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          facilities: [{ id: "FAC-001", name: "施設A" }],
          tags: [],
        },
      });
      return;
    }

    if (path.endsWith("/monthly-menus/2026-03/uploads") && method === "GET") {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }

    if (path.endsWith("/monthly-menus/2026-03") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          menu: {
            id: "2026-03",
            filename: "menu.xlsx",
            display_name: "2026年3月 献立",
            uploaded_at: "2026-03-01T09:00:00",
          },
          items: [
            {
              id: "MMI001",
              month_id: "2026-03",
              name: "ホイコーロー",
              unit_type: "g",
              qty_per_serving: 100,
              temp_type: "hot",
              daypart: "昼食",
              category: "主菜",
              diet_type: "regular",
              facility_override: null,
            },
          ],
          entries: [
            {
              id: "MME001",
              month_id: "2026-03",
              menu_date: "2026-03-24",
              daypart: "昼食",
              name: "ホイコーロー",
              category: "主菜",
              diet_type: "regular",
              slot_index: 1,
              facility_override: null,
            },
            {
              id: "MME002",
              month_id: "2026-03",
              menu_date: "2026-03-24",
              daypart: "昼食",
              name: "豆腐の煮物",
              category: "副菜",
              diet_type: "regular",
              slot_index: 2,
              facility_override: null,
            },
            {
              id: "MME003",
              month_id: "2026-03",
              menu_date: "2026-03-25",
              daypart: "昼食",
              name: "筑前煮",
              category: "副菜",
              diet_type: "regular",
              slot_index: 2,
              facility_override: null,
            },
          ],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-03`);

  await expect(page.getByRole("heading", { name: "月次メニュー編集" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "月次シート" })).toBeVisible();
  await expect(page.getByText("メニュー一覧（補助）")).toBeVisible();

  const sheet = page.getByTestId("monthly-menu-sheet");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("rowheader", { name: "03/24" })).toBeVisible();
  await expect(sheet.getByRole("rowheader", { name: "03/25" })).toBeVisible();
  await expect(sheet.locator("thead").getByText("共通(base)")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("昼食")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("主菜")).toHaveCount(1);
  await expect(sheet.locator("thead").getByText("副菜")).toHaveCount(1);
  await expect(sheet.locator("thead").getByText("枠1")).toBeVisible();
  await expect(sheet.locator("thead").getByText("枠2")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "ホイコーロー" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "豆腐の煮物" })).toBeVisible();
  await expect(sheet.getByText("筑前煮")).toBeVisible();

  await expect(page.getByTestId("selected-entry-name")).toHaveText("ホイコーロー");
  await sheet.getByRole("button", { name: "豆腐の煮物" }).click();
  await expect(page.getByTestId("selected-entry-name")).toHaveText("豆腐の煮物");
  await expect(page.getByTestId("selected-entry-category")).toHaveText("副菜");
});
