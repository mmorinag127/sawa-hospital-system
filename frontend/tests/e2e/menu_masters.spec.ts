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

const entryPoints = [
  { name: "root", path: "/menu-masters" },
  { name: "hospital", path: "/hospital/menu-masters" },
];

for (const entryPoint of entryPoints) {
  test(`menu master page saves cut/count unit selections canonically (${entryPoint.name})`, async ({ page }, testInfo) => {

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
  const unexpectedRequests: string[] = [];

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

    unexpectedRequests.push(`${method} ${path}`);
    await route.fulfill({ status: 500, json: { detail: `Unexpected mocked API request: ${method} ${path}` } });
  });

  await page.goto(entryPoint.path);

  await expect(page.getByRole("heading", { name: "メニューマスター" })).toBeVisible();

  await page.getByPlaceholder("メニュー名 *").fill("タラのムニエル");
  await page.getByTestId("new-menu-master-unit-type").selectOption("cut");
  await page.getByTestId("new-menu-master-bag-max-unit").selectOption("count");
  const createRequest = page.waitForRequest((request) =>
    request.method() === "POST" && new URL(request.url()).pathname === "/api/menu-masters",
  );
  const createResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/menu-masters",
  );
  const createReload = page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === "/api/menu-masters",
  );
  await page.getByRole("button", { name: "追加" }).click();
  expect((await createRequest).postDataJSON()).toMatchObject({
    name: "タラのムニエル",
    unit_type: "cut",
    bag_max_unit: "count",
  });
  expect((await createResponse).status()).toBe(200);
  await createReload;
  await expect(page.getByTestId("menu-master-unit-type-MNU002")).toHaveValue("cut");
  expect(createBody).toMatchObject({ name: "タラのムニエル" });

  await expect(page.getByTestId("menu-master-unit-type-MNU001")).toHaveValue("cut");
  await page.getByTestId("menu-master-unit-type-MNU001").selectOption("count");
  await page.getByTestId("menu-master-bag-max-unit-MNU001").selectOption("cut");
  await expect(page.getByTestId("menu-master-unit-type-MNU001")).toHaveValue("count");
  await expect(page.getByTestId("menu-master-bag-max-unit-MNU001")).toHaveValue("cut");
  const updateRequest = page.waitForRequest((request) =>
    request.method() === "PUT" && new URL(request.url()).pathname === "/api/menu-masters/MNU001",
  );
  const updateResponse = page.waitForResponse((response) =>
    response.request().method() === "PUT" && new URL(response.url()).pathname === "/api/menu-masters/MNU001",
  );
  const updateReload = page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === "/api/menu-masters",
  );
  await page.locator("tr").filter({ has: page.getByTestId("menu-master-unit-type-MNU001") }).getByRole("button", { name: "保存" }).click();
  expect((await updateRequest).postDataJSON()).toMatchObject({
    unit_type: "count",
    bag_max_unit: "cut",
  });
  expect((await updateResponse).status()).toBe(200);
  const savedListResponse = await updateReload;
  expect(savedListResponse.status()).toBe(200);
  expect(await savedListResponse.finished()).toBeNull();
  const savedList = await savedListResponse.json();
  const expectedRecord = { id: "MNU001", unit_type: "count", bag_max_unit: "cut" };
  expect(savedList.items.find((item: MenuMasterStateItem) => item.id === "MNU001")).toMatchObject(expectedRecord);
  expect(updateBody).toMatchObject({ unit_type: "count", bag_max_unit: "cut" });

  await test.step("Reload a new document and render the saved MNU001 from GET", async () => {
    const previousTimeOrigin = await page.evaluate(() => performance.timeOrigin);
    const reloadGet = page.waitForResponse((response) =>
      response.request().method() === "GET" && new URL(response.url()).pathname === "/api/menu-masters",
    );
    const documentResponse = await page.reload();
    expect(documentResponse?.status()).toBe(200);
    const reloadedListResponse = await reloadGet;
    expect(reloadedListResponse.status()).toBe(200);
    expect(await reloadedListResponse.finished()).toBeNull();
    const reloadedList = await reloadedListResponse.json();
    const reloadedRecord = reloadedList.items.find((item: MenuMasterStateItem) => item.id === "MNU001");
    expect(reloadedRecord).toMatchObject(expectedRecord);

    const navigation = await page.evaluate(() => ({
      timeOrigin: performance.timeOrigin,
      type: (performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming).type,
    }));
    expect(navigation.type).toBe("reload");
    expect(navigation.timeOrigin).toBeGreaterThan(previousTimeOrigin);
    const unitType = page.getByTestId("menu-master-unit-type-MNU001");
    const bagMaxUnit = page.getByTestId("menu-master-bag-max-unit-MNU001");
    await expect(unitType).toHaveValue("count");
    await expect(bagMaxUnit).toHaveValue("cut");

    await testInfo.attach("saved-record-after-document-reload", {
      body: JSON.stringify({
        entryPath: entryPoint.path,
        previousTimeOrigin,
        navigation,
        documentStatus: documentResponse?.status(),
        getUrl: reloadedListResponse.url(),
        getStatus: reloadedListResponse.status(),
        record: reloadedRecord,
        rendered: { id: "MNU001", unit_type: await unitType.inputValue(), bag_max_unit: await bagMaxUnit.inputValue() },
      }, null, 2),
      contentType: "application/json",
    });
  });
  expect(unexpectedRequests).toEqual([]);
  });
}
