import { expect, test } from "@playwright/test";

test("daily delivery notes shows meal counts by daypart and warns about unconfirmed orders", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 200, json: { role: "admin" } });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-001", name: "施設A" }, { id: "FAC-002", name: "施設B" }] },
      });
      return;
    }
    if (path.endsWith("/orders/daily-output-context")) {
      await route.fulfill({
        status: 200,
        json: {
          sections: {
            orders: {
              status: "fulfilled",
              data: {
                orders: [
                  { id: "ORD-001", facility: "FAC-001", status: "確定", week: "2026-09" },
                  { id: "ORD-002", facility: "FAC-002", status: "要確認", week: "2026-09" },
                ],
              },
            },
            daily_bags: { status: "fulfilled", data: { groups: [] } },
            daily_bags_audit: { status: "fulfilled", data: { rule_based: { finding_count: 0 } } },
            totals: { status: "fulfilled", data: { rows: [] } },
            meal_counts: {
              status: "fulfilled",
              data: {
                date: "2026-09-01",
                groups: [
                  { daypart: "朝食", counts: [{ diet_type: "regular", quantity: 12 }, { diet_type: "diabetes", quantity: 3 }] },
                  { daypart: "昼食", counts: [{ diet_type: "soft", quantity: 7 }] },
                  { daypart: "夕食", counts: [{ diet_type: "mixer", quantity: 5 }] },
                ],
                unconfirmed_orders: [{ order_id: "ORD-002", facility_id: "FAC-002", status: "要確認" }],
              },
            },
          },
        },
      });
      return;
    }
    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/daily-delivery-notes`);
  await page.getByRole("button", { name: "取得" }).click();

  const summary = page.getByRole("heading", { name: "当日食数集計" }).locator("..").locator("..");
  await expect(summary.getByRole("heading", { name: "朝食" })).toBeVisible();
  await expect(summary.getByText("常食")).toBeVisible();
  await expect(summary.getByText("12食")).toBeVisible();
  await expect(summary.getByText("糖尿")).toBeVisible();
  await expect(summary.getByText("3食")).toBeVisible();
  await expect(summary.getByRole("heading", { name: "昼食" })).toBeVisible();
  await expect(summary.getByRole("heading", { name: "夕食" })).toBeVisible();
  await expect(summary.getByRole("alert")).toContainText("未確定の注文があるため、この集計には含まれていません。");
  await expect(summary.getByRole("alert")).toContainText("施設B (ORD-002) / 要確認");
});

test("daily delivery notes shows menu category and calculation basis in daily bag rows", async ({ page }) => {
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

    if (path.endsWith("/facilities") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          facilities: [{ id: "FAC-001", name: "施設A" }],
        },
      });
      return;
    }

    if (path.endsWith("/orders/by-line-date") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            {
              id: "ORD-DAILY-001",
              facility: "FAC-001",
              week: "2026-03",
              status: "確定",
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-bags") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date: "2026-03-24",
          order_count: 1,
          groups: [
            {
              daypart: "昼",
              daypart_key: "lunch",
              menu_category: "主菜",
              menu_name: "ホイコーロー",
              diet_groups: [
                {
                  diet_type: "regular",
                  total_quantity: 220,
                  total_amount_label: "22000g",
                  calculation_basis_label: "100g/人",
                  bag_type_groups: [
                    {
                      bag_type: "large",
                      bag_count: 2,
                      total_quantity: 220,
                      total_amount_label: "22000g",
                      breakdowns: [
                        {
                          amount_label: "22000g",
                          count: 2,
                          order_refs: [
                            {
                              order_id: "ORD-DAILY-001",
                              facility_label: "施設A (FAC-001)",
                              area_id: "2F",
                              quantity: 220,
                            },
                          ],
                        },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/totals") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date_from: "2026-03-24",
          date_to: "2026-03-24",
          rows: [
            {
              date: "2026-03-24",
              daypart: "昼",
              menu_category: "主菜",
              menu_name: "ホイコーロー",
              diet_type: "regular",
              quantity: 220,
              order_refs: [
                {
                  order_id: "ORD-DAILY-001",
                  facility_id: "FAC-001",
                  facility_name: "施設A",
                  source_diet_type: "regular",
                  aggregated_diet_type: "regular",
                  area_id: "2F",
                  quantity: 220,
                },
              ],
            },
          ],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/daily-delivery-notes`);

  await expect(page.getByRole("heading", { name: "日別出力" })).toBeVisible();
  await page.getByRole("button", { name: "取得" }).click();

  const bagTable = page.locator(".menu-bag-table").first();
  await expect(page.getByRole("heading", { name: "当日袋分け一覧" })).toBeVisible();
  await expect(page.getByText("主菜 / 1区分 / 220食")).toBeVisible();
  await expect(bagTable.getByRole("columnheader", { name: "献立区分" })).toBeVisible();
  await expect(bagTable.getByRole("columnheader", { name: "計算基準" })).toBeVisible();
  await expect(bagTable.getByRole("cell", { name: "主菜" })).toBeVisible();
  await expect(bagTable.getByRole("cell", { name: "100g/人" })).toBeVisible();
  await expect(page.locator(".bag-breakdown-ref").first()).toContainText("施設A (FAC-001) / 2F / 220食");
  await expect(page.locator(".bag-breakdown-ref .link").first()).toHaveAttribute("href", "/orders/ORD-DAILY-001");
  await expect(page.locator(".total-facility-summary")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "施設別" }).first()).toBeVisible();
});

test("daily delivery notes can save facility-level portion overrides and reflect them in the list", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  const overrideState: Record<string, { qty: number; unit: string; note: string; overrideId: string }> = {
    "FAC-001__regular": { qty: 100, unit: "g", note: "", overrideId: "" },
    "FAC-002__regular": { qty: 100, unit: "g", note: "", overrideId: "" },
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith("/auth/me") && method === "GET") {
      await route.fulfill({ status: 200, json: { role: "admin" } });
      return;
    }

    if (path.endsWith("/facilities") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          facilities: [{ id: "FAC-001", name: "施設A" }],
        },
      });
      return;
    }

    if (path.endsWith("/orders/by-line-date") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            {
              id: "ORD-DAILY-001",
              facility: "FAC-001",
              week: "2026-03",
              status: "確定",
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-bags") && method === "GET") {
      const totalQuantity = 220 + 18;
      const totalAmount = `${overrideState["FAC-001__regular"].qty * 220}${overrideState["FAC-001__regular"].unit === "切" ? "切" : "g"}`;
      await route.fulfill({
        status: 200,
        json: {
          date: "2026-03-24",
          order_count: 1,
          groups: [
            {
              daypart: "昼",
              daypart_key: "lunch",
              menu_category: "主菜",
              menu_name: "ホイコーロー",
              diet_groups: [
                {
                  diet_type: "regular",
                  total_quantity: totalQuantity,
                  total_amount_label: totalAmount,
                  calculation_basis_label: `${overrideState["FAC-001__regular"].qty}${overrideState["FAC-001__regular"].unit}/人`,
                  bag_type_groups: [
                    {
                      bag_type: "large",
                      bag_count: 2,
                      total_quantity: totalQuantity,
                      total_amount_label: totalAmount,
                      breakdowns: [{ amount_label: totalAmount, count: 2 }],
                    },
                  ],
                },
              ],
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/totals") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date_from: "2026-03-24",
          date_to: "2026-03-24",
          rows: [
            {
              date: "2026-03-24",
              daypart: "昼",
              menu_category: "主菜",
              menu_name: "ホイコーロー",
              diet_type: "regular",
              quantity: 220,
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-output-overrides") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date: "2026-03-24",
          daypart: "昼",
          menu_name: "ホイコーロー",
          menu_category: "主菜",
          rows: [
            {
              facility_id: "FAC-001",
              facility_label: "施設A (FAC-001)",
              diet_type: "regular",
              order_count: 1,
              total_quantity: 220,
              current_basis_label: `${overrideState["FAC-001__regular"].qty}${overrideState["FAC-001__regular"].unit}/人`,
              current_qty_per_serving: overrideState["FAC-001__regular"].qty,
              current_unit_type: overrideState["FAC-001__regular"].unit,
              requires_intervention: false,
              current_variants: [
                {
                  menu_name: "ホイコーロー",
                  daypart: "昼",
                  menu_category: "主菜",
                  unit_type: overrideState["FAC-001__regular"].unit,
                  qty_per_serving: overrideState["FAC-001__regular"].qty,
                  basis_label: `${overrideState["FAC-001__regular"].qty}${overrideState["FAC-001__regular"].unit}/人`,
                  order_ids: ["ORD-DAILY-001"],
                },
              ],
              override: overrideState["FAC-001__regular"].overrideId
                ? {
                    id: overrideState["FAC-001__regular"].overrideId,
                    output_date: "2026-03-24",
                    facility_id: "FAC-001",
                    menu_name: "ホイコーロー",
                    diet_type: "regular",
                    daypart: "昼",
                    menu_category: "主菜",
                    unit_type: overrideState["FAC-001__regular"].unit,
                    qty_per_serving: overrideState["FAC-001__regular"].qty,
                    note: overrideState["FAC-001__regular"].note,
                  }
                : null,
            },
            {
              facility_id: "FAC-002",
              facility_label: "施設B (FAC-002)",
              diet_type: "regular",
              order_count: 1,
              total_quantity: 18,
              current_basis_label: `${overrideState["FAC-002__regular"].qty}${overrideState["FAC-002__regular"].unit}/人`,
              current_qty_per_serving: overrideState["FAC-002__regular"].qty,
              current_unit_type: overrideState["FAC-002__regular"].unit,
              requires_intervention: false,
              current_variants: [
                {
                  menu_name: "ホイコーロー",
                  daypart: "昼",
                  menu_category: "主菜",
                  unit_type: overrideState["FAC-002__regular"].unit,
                  qty_per_serving: overrideState["FAC-002__regular"].qty,
                  basis_label: `${overrideState["FAC-002__regular"].qty}${overrideState["FAC-002__regular"].unit}/人`,
                  order_ids: ["ORD-DAILY-002"],
                },
              ],
              override: overrideState["FAC-002__regular"].overrideId
                ? {
                    id: overrideState["FAC-002__regular"].overrideId,
                    output_date: "2026-03-24",
                    facility_id: "FAC-002",
                    menu_name: "ホイコーロー",
                    diet_type: "regular",
                    daypart: "昼",
                    menu_category: "主菜",
                    unit_type: overrideState["FAC-002__regular"].unit,
                    qty_per_serving: overrideState["FAC-002__regular"].qty,
                    note: overrideState["FAC-002__regular"].note,
                  }
                : null,
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-output-overrides/upsert") && method === "POST") {
      const body = route.request().postDataJSON() as {
        facility_id: string;
        diet_type: string;
        qty_per_serving: number;
        unit_type: string;
        note?: string;
      };
      const key = `${body.facility_id}__${body.diet_type || "regular"}`;
      overrideState[key].qty = body.qty_per_serving;
      overrideState[key].unit = body.unit_type;
      overrideState[key].note = body.note || "";
      overrideState[key].overrideId = "DPOe2e001";
      await route.fulfill({
        status: 200,
        json: {
          override: {
            id: overrideState[key].overrideId,
            output_date: "2026-03-24",
            facility_id: body.facility_id,
            menu_name: "ホイコーロー",
            diet_type: body.diet_type || "regular",
            daypart: "昼",
            menu_category: "主菜",
            unit_type: overrideState[key].unit,
            qty_per_serving: overrideState[key].qty,
            note: overrideState[key].note,
          },
          affected_order_ids: ["ORD-DAILY-001", "ORD-DAILY-002"],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-output-overrides/upsert-bulk") && method === "POST") {
      await route.fulfill({
        status: 200,
        json: {
          overrides: [],
          affected_order_ids: ["ORD-DAILY-001", "ORD-DAILY-002"],
          updated_count: 2,
        },
      });
      return;
    }

    if (path.includes("/orders/daily-output-overrides/") && method === "DELETE") {
      overrideState["FAC-001__regular"] = { qty: 100, unit: "g", note: "", overrideId: "" };
      await route.fulfill({ status: 200, json: { override: { id: "DPOe2e001" }, affected_order_ids: ["ORD-DAILY-001"] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/daily-delivery-notes`);
  await page.getByRole("button", { name: "取得" }).click();

  await page.getByRole("button", { name: "施設別単位設定" }).click();
  await expect(page.getByRole("heading", { name: "施設別単位設定" })).toBeVisible();
  await expect(page.getByRole("dialog").getByText("全施設に一括適用")).toBeVisible();
  await page.getByLabel("施設を選ぶ").selectOption("FAC-002");
  const facilityEditor = page.locator(".override-editor-shell .override-editor-card").nth(1);
  await expect(facilityEditor.locator(".override-facility")).toHaveText("施設B (FAC-002)");
  await facilityEditor.locator("input[type='number']").fill("2");
  await facilityEditor.locator("select").last().selectOption("切");
  await facilityEditor.locator("input[type='text']").fill("この施設のみ2切");
  await facilityEditor.getByRole("button", { name: "保存", exact: true }).click();

  await expect(page.getByText("施設別単位設定を保存しました。")).toBeVisible();
  await expect(page.getByRole("dialog").getByText("2切/人")).toBeVisible();
});

test("daily delivery notes can save bulk portion overrides for all facilities", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  const bulkState = { qty: 100, unit: "g" };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith("/auth/me") && method === "GET") {
      await route.fulfill({ status: 200, json: { role: "admin" } });
      return;
    }

    if (path.endsWith("/facilities") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-001", name: "施設A" }, { id: "FAC-002", name: "施設B" }] },
      });
      return;
    }

    if (path.endsWith("/orders/by-line-date") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            { id: "ORD-DAILY-001", facility: "FAC-001", week: "2026-03", status: "確定" },
            { id: "ORD-DAILY-002", facility: "FAC-002", week: "2026-03", status: "確定" },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-bags") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date: "2026-03-24",
          order_count: 2,
          groups: [
            {
              daypart: "昼",
              daypart_key: "lunch",
              menu_category: "主菜",
              menu_name: "ホイコーロー",
              diet_groups: [
                {
                  diet_type: "regular",
                  total_quantity: 238,
                  total_amount_label: `${bulkState.qty * 238}${bulkState.unit}`,
                  calculation_basis_label: `${bulkState.qty}${bulkState.unit}/人`,
                  bag_type_groups: [
                    {
                      bag_type: "large",
                      bag_count: 3,
                      total_quantity: 238,
                      total_amount_label: `${bulkState.qty * 238}${bulkState.unit}`,
                      breakdowns: [{ amount_label: `${bulkState.qty * 238}${bulkState.unit}`, count: 3 }],
                    },
                  ],
                },
              ],
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/totals") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date_from: "2026-03-24",
          date_to: "2026-03-24",
          rows: [
            { date: "2026-03-24", daypart: "昼", menu_category: "主菜", menu_name: "ホイコーロー", diet_type: "regular", quantity: 238 },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-output-overrides") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          date: "2026-03-24",
          daypart: "昼",
          menu_name: "ホイコーロー",
          menu_category: "主菜",
          rows: [
            {
              facility_id: "FAC-001",
              facility_label: "施設A (FAC-001)",
              diet_type: "regular",
              order_count: 1,
              total_quantity: 220,
              current_basis_label: `${bulkState.qty}${bulkState.unit}/人`,
              current_qty_per_serving: bulkState.qty,
              current_unit_type: bulkState.unit,
              requires_intervention: false,
              current_variants: [],
              override: null,
            },
            {
              facility_id: "FAC-002",
              facility_label: "施設B (FAC-002)",
              diet_type: "regular",
              order_count: 1,
              total_quantity: 18,
              current_basis_label: `${bulkState.qty}${bulkState.unit}/人`,
              current_qty_per_serving: bulkState.qty,
              current_unit_type: bulkState.unit,
              requires_intervention: false,
              current_variants: [],
              override: null,
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-output-overrides/upsert-bulk") && method === "POST") {
      const body = route.request().postDataJSON() as { qty_per_serving: number; unit_type: string };
      bulkState.qty = body.qty_per_serving;
      bulkState.unit = body.unit_type;
      await route.fulfill({
        status: 200,
        json: {
          overrides: [],
          affected_order_ids: ["ORD-DAILY-001", "ORD-DAILY-002"],
          updated_count: 2,
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/daily-delivery-notes`);
  await page.getByRole("button", { name: "取得" }).click();
  await page.getByRole("button", { name: "施設別単位設定" }).click();
  const bulkEditor = page.locator(".override-bulk-card");
  await bulkEditor.locator("input[type='number']").fill("3");
  await bulkEditor.locator("select").selectOption("個");
  await bulkEditor.locator("input[type='text']").fill("全施設3個に統一");
  await page.getByRole("button", { name: "全施設に保存" }).click();

  await expect(page.getByText("全施設の単位設定を保存しました。")).toBeVisible();
  await expect(page.getByRole("dialog").getByText("3個/人")).toBeVisible();
});

test("daily delivery notes shows blocker text when bag data truly does not exist", async ({ page }) => {
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

    if (path.endsWith("/facilities") && method === "GET") {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-001", name: "施設A" }] } });
      return;
    }

    if (path.endsWith("/orders/by-line-date") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [{ id: "ORD-DAILY-EMPTY-001", facility: "FAC-001", week: "2026-03", status: "確定" }],
        },
      });
      return;
    }

    if (path.endsWith("/orders/daily-bags") && method === "GET") {
      await route.fulfill({ status: 200, json: { date: "2026-03-24", order_count: 1, groups: [] } });
      return;
    }

    if (path.endsWith("/totals") && method === "GET") {
      await route.fulfill({ status: 200, json: { date_from: "2026-03-24", date_to: "2026-03-24", rows: [] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/daily-delivery-notes`);
  await page.getByRole("button", { name: "取得" }).click();

  await expect(page.getByRole("heading", { name: "当日袋分け一覧" })).toBeVisible();
  await expect(page.getByText("袋分け結果がまだ生成されていません。")).toBeVisible();
});

test("daily delivery notes bundle download survives responses longer than the old 30s client timeout", async ({ page }) => {
  test.setTimeout(90000);
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

    if (path.endsWith("/facilities") && method === "GET") {
      await route.fulfill({ status: 200, json: { facilities: [] } });
      return;
    }

    if (path.endsWith("/orders/by-line-date") && method === "GET") {
      await route.fulfill({ status: 200, json: { orders: [] } });
      return;
    }

    if (path.endsWith("/orders/daily-bags") && method === "GET") {
      await route.fulfill({ status: 200, json: { date: "2026-03-24", order_count: 0, groups: [] } });
      return;
    }

    if (path.endsWith("/totals") && method === "GET") {
      await route.fulfill({ status: 200, json: { date_from: "2026-03-24", date_to: "2026-03-24", rows: [] } });
      return;
    }

    if (path.endsWith("/outputs/daily-bundle") && method === "GET") {
      await new Promise((resolve) => setTimeout(resolve, 31000));
      await route.fulfill({
        status: 200,
        headers: {
          "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "content-disposition": "attachment; filename=\"daily_outputs_2026-03-24_delivery.xlsx\"",
          "x-daily-bundle-success-orders": "1",
          "x-daily-bundle-empty-orders": "0",
          "x-daily-bundle-error-orders": "0",
        },
        body: "bundle",
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/daily-delivery-notes`);
  await page.getByRole("button", { name: "取得" }).click();
  await page.getByRole("button", { name: "当日納品書Excel" }).click();

  await expect(page.getByText("当日納品書Excelをダウンロードしました。成功 1件 / 失敗 0件")).toBeVisible({ timeout: 40000 });
});
