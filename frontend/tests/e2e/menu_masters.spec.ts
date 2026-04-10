import { expect, test } from "@playwright/test";

type MenuMasterStateItem = {
  id: string;
  name: string;
  unit_type: string | null;
  qty_per_serving: number | null;
  bag_max_qty: number | null;
  bag_max_unit: string | null;
  temp_type: string | null;
  daypart: string | null;
  category: string | null;
  condiments: unknown[];
};

const asNullableString = (value: unknown): string | null => {
  if (value == null || value === "") return null;
  return String(value);
};

const asNullableNumber = (value: unknown): number | null => {
  if (value == null || value === "") return null;
  return typeof value === "number" ? value : Number(value);
};

test("menu master page saves cut/count unit selections canonically", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

  const state = {
    items: [
      {
        id: "MNU001",
        name: "白身魚のフライ",
        unit_type: "cut",
        qty_per_serving: 1,
        bag_max_qty: 5,
        bag_max_unit: "count",
        temp_type: "hot",
        daypart: "夕食",
        category: "主菜",
        condiments: [],
      },
    ] as MenuMasterStateItem[],
  };

  let createBody: Record<string, unknown> | null = null;
  let updateBody: Record<string, unknown> | null = null;

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

    if (path.endsWith("/menu-masters") && method === "GET") {
      await route.fulfill({ status: 200, json: { items: state.items } });
      return;
    }

    if (path.endsWith("/menu-masters") && method === "POST") {
      createBody = route.request().postDataJSON() as Record<string, unknown>;
      state.items.push({
        id: "MNU002",
        name: String(createBody.name || ""),
        unit_type: asNullableString(createBody.unit_type),
        qty_per_serving: asNullableNumber(createBody.qty_per_serving),
        bag_max_qty: asNullableNumber(createBody.bag_max_qty),
        bag_max_unit: asNullableString(createBody.bag_max_unit),
        temp_type: asNullableString(createBody.temp_type),
        daypart: asNullableString(createBody.daypart),
        category: asNullableString(createBody.category),
        condiments: Array.isArray(createBody.condiments) ? createBody.condiments : [],
      });
      await route.fulfill({ status: 200, json: { item: { id: "MNU002", ...createBody } } });
      return;
    }

    if (path.endsWith("/menu-masters/MNU001") && method === "PUT") {
      updateBody = route.request().postDataJSON() as Record<string, unknown>;
      state.items = state.items.map((item) => (item.id === "MNU001" ? { ...item, ...updateBody } : item));
      await route.fulfill({ status: 200, json: { updated: true } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menu-masters`);

  await expect(page.getByRole("heading", { name: "メニューマスター" })).toBeVisible();

  await page.getByPlaceholder("メニュー名 *").fill("タラのムニエル");
  await page.getByTestId("new-menu-master-unit-type").selectOption("cut");
  await page.getByTestId("new-menu-master-bag-max-unit").selectOption("count");
  await page.getByRole("button", { name: "追加" }).click();

  expect(createBody).toMatchObject({
    name: "タラのムニエル",
    unit_type: "cut",
    bag_max_unit: "count",
  });

  await expect(page.getByTestId("menu-master-unit-type-MNU001")).toHaveValue("cut");
  await page.getByTestId("menu-master-unit-type-MNU001").selectOption("count");
  await page.getByTestId("menu-master-bag-max-unit-MNU001").selectOption("cut");
  await expect(page.getByTestId("menu-master-unit-type-MNU001")).toHaveValue("count");
  await expect(page.getByTestId("menu-master-bag-max-unit-MNU001")).toHaveValue("cut");
  await page.locator("tr").filter({ has: page.getByTestId("menu-master-unit-type-MNU001") }).getByRole("button", { name: "保存" }).click();

  expect(updateBody).toMatchObject({
    unit_type: "count",
    bag_max_unit: "cut",
  });
});
