import { expect, test } from "@playwright/test";

test("monthly menu page renders entries as a sheet-style grid", async ({ page }) => {
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
          facilities: [{ id: "FAC00003", name: "施設A" }],
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
            display_name: "2026年3月献立",
          },
          items: [
            {
              id: "MMI1",
              month_id: "2026-03",
              name: "ホイコーロー",
              unit_type: "g",
              qty_per_serving: 100,
              daypart: "昼",
              category: "主菜",
              diet_type: "",
            },
          ],
          entries: [
            {
              id: "MME1",
              month_id: "2026-03",
              menu_date: "2026-03-24",
              daypart: "昼",
              name: "ホイコーロー",
              category: "主菜",
              diet_type: "",
              slot_index: 1,
              facility_override: null,
            },
            {
              id: "MME2",
              month_id: "2026-03",
              menu_date: "2026-03-24",
              daypart: "昼",
              name: "豆腐の煮物",
              category: "副菜",
              diet_type: "",
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
  const sheet = page.locator("[data-testid='monthly-menu-sheet']");
  await expect(sheet.getByRole("rowheader", { name: "03/24" })).toBeVisible();
  await expect(sheet.locator("thead").getByText("共通(base)")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("昼")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("主菜")).toHaveCount(1);
  await expect(sheet.locator("thead").getByText("副菜")).toHaveCount(1);
  await expect(sheet.locator("thead").getByText("枠1")).toBeVisible();
  await expect(sheet.locator("thead").getByText("枠2")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "ホイコーロー" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "豆腐の煮物" })).toBeVisible();
  await expect(page.getByTestId("selected-entry-name")).toHaveText("ホイコーロー");
  await expect(page.getByLabel("単位").first().locator("option")).toHaveText(["未選択", "グラム(g)", "切れ", "個"]);
  await sheet.getByRole("button", { name: "豆腐の煮物" }).click();
  await expect(page.getByTestId("selected-entry-name")).toHaveText("豆腐の煮物");
  await expect(page.getByTestId("selected-entry-category")).toHaveText("副菜");
});

test("monthly menu detail selects same-name item by daypart and uses daypart dropdown", async ({ page }) => {
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
      await route.fulfill({ status: 200, json: { facilities: [], tags: [] } });
      return;
    }
    if (path.endsWith("/monthly-menus/2026-06/uploads") && method === "GET") {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith("/monthly-menus/2026-06") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          menu: { id: "2026-06", display_name: "2026年6月献立" },
          items: [
            {
              id: "MMI_MORNING",
              month_id: "2026-06",
              name: "ジャーマンポテト",
              unit_type: "g",
              qty_per_serving: 70,
              daypart: "朝食",
              category: "主菜",
              diet_type: "",
            },
            {
              id: "MMI_LUNCH",
              month_id: "2026-06",
              name: "ジャーマンポテト",
              unit_type: "g",
              qty_per_serving: 40,
              daypart: "昼食",
              category: "副菜",
              diet_type: "",
            },
          ],
          entries: [
            {
              id: "MME_MORNING",
              month_id: "2026-06",
              menu_date: "2026-06-01",
              daypart: "朝食",
              name: "ジャーマンポテト",
              category: "主菜",
              diet_type: "",
              slot_index: 0,
              facility_override: null,
            },
            {
              id: "MME_LUNCH",
              month_id: "2026-06",
              menu_date: "2026-06-01",
              daypart: "昼食",
              name: "ジャーマンポテト",
              category: "副菜",
              diet_type: "",
              slot_index: 1,
              facility_override: null,
            },
          ],
          master_checks: { count: 0, issues: [] },
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-06`);
  const sheet = page.locator("[data-testid='monthly-menu-sheet']");
  await expect(page.getByTestId("selected-entry-name")).toHaveText("ジャーマンポテト");
  await expect(page.getByLabel("量").first()).toHaveValue("70");
  await expect(page.getByLabel("時間帯").first()).toHaveValue("朝食");
  await expect(page.getByLabel("時間帯").first().locator("option")).toHaveText(["未選択", "朝食", "昼食", "夕食"]);

  await sheet.getByRole("button", { name: "ジャーマンポテト" }).nth(1).click();

  await expect(page.getByTestId("selected-entry-category")).toHaveText("副菜");
  await expect(page.getByLabel("量").first()).toHaveValue("40");
  await expect(page.getByLabel("時間帯").first()).toHaveValue("昼食");
});

test("monthly menu page can create a facility exception from the selected entry", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  let savedRequest: Record<string, unknown> | null = null;
  let exceptionSaved = false;

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
          facilities: [
            { id: "FAC00003", name: "施設A" },
            { id: "FAC00005", name: "施設B" },
          ],
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
            display_name: "2026年3月献立",
          },
          items: exceptionSaved
            ? [
                {
                    id: "MMI1",
                    month_id: "2026-03",
                    name: "ホイコーロー",
                    unit_type: "g",
                    qty_per_serving: 100,
                    daypart: "昼",
                    category: "主菜",
                    diet_type: "",
                    facility_override: null,
                },
                {
                    id: "MMI_EXCEPTION",
                    month_id: "2026-03",
                    name: "鮭の塩焼き",
                    unit_type: "cut",
                    qty_per_serving: 1,
                    daypart: "昼",
                    category: "主菜",
                    diet_type: "",
                    facility_override: "FAC00003",
                },
              ]
            : [
                {
                  id: "MMI1",
                  month_id: "2026-03",
                  name: "ホイコーロー",
                  unit_type: "g",
                  qty_per_serving: 100,
                  daypart: "昼",
                  category: "主菜",
                  diet_type: "",
                  facility_override: null,
                },
              ],
          entries: exceptionSaved
            ? [
                {
                  id: "MME1",
                  month_id: "2026-03",
                  menu_date: "2026-03-24",
                  daypart: "昼",
                  name: "ホイコーロー",
                  category: "主菜",
                  diet_type: "",
                  slot_index: 1,
                  facility_override: null,
                },
                {
                  id: "MME_EXCEPTION",
                  month_id: "2026-03",
                  menu_date: "2026-03-24",
                  daypart: "昼",
                  name: "鮭の塩焼き",
                  category: "主菜",
                  diet_type: "",
                  slot_index: 1,
                  facility_override: "FAC00003",
                },
              ]
            : [
                {
                  id: "MME1",
                  month_id: "2026-03",
                  menu_date: "2026-03-24",
                  daypart: "昼",
                  name: "ホイコーロー",
                  category: "主菜",
                  diet_type: "",
                  slot_index: 1,
                  facility_override: null,
                },
              ],
        },
      });
      return;
    }

    if (path.endsWith("/monthly-menus/2026-03/entries/MME1/exceptions") && method === "POST") {
      savedRequest = JSON.parse(route.request().postData() || "{}");
      exceptionSaved = true;
      await route.fulfill({
        status: 200,
        json: {
          updated: true,
          entry_id: "MME1",
          facility_ids: ["FAC00003"],
          entries: [{ id: "MME_EXCEPTION", facility_override: "FAC00003", name: "鮭の塩焼き" }],
          items: [{ id: "MMI_EXCEPTION", facility_override: "FAC00003", name: "鮭の塩焼き" }],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-03`);

  await page.getByTestId("selected-entry-name").waitFor();
  await page
    .locator("[data-testid='entry-exception-form']")
    .getByLabel("例外メニュー名")
    .fill("鮭の塩焼き");
  await page.locator("[data-testid='entry-exception-form']").getByText("施設A (FAC00003)").click();
  await page
    .locator("[data-testid='entry-exception-form']")
    .getByRole("button", { name: "この位置に例外メニューを追加" })
    .click();

  expect(savedRequest).toEqual({
    facility_ids: ["FAC00003"],
    name: "鮭の塩焼き",
    unit_type: "g",
    qty_per_serving: 100,
    bag_max_qty: null,
    bag_max_unit: null,
    temp_type: null,
    category: "主菜",
    diet_type: null,
  });
  await expect(page.getByText("例外メニューを保存しました: 鮭の塩焼き")).toBeVisible();
});

test("monthly menu upload asks for menu master review when backend returns 409", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  let uploadAttemptCount = 0;
  let secondUploadPayload = "";

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
          facilities: [{ id: "FAC00003", name: "施設A" }],
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
            display_name: "2026年3月献立",
          },
          items: [],
          entries: [],
        },
      });
      return;
    }

    if (path.endsWith("/monthly-menus") && method === "POST") {
      uploadAttemptCount += 1;
      if (uploadAttemptCount === 1) {
        await route.fulfill({
          status: 409,
          json: {
            detail: {
              code: "menu_master_review_required",
              issues: [
                {
                  source_name: "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
                  normalized_name: "白身魚のフライ添)ブロッコリー",
                  reason: "candidate_review_required",
                  suggested_patch: {
                    name: "白身魚のフライ",
                    unit_type: "count",
                    qty_per_serving: 1,
                    daypart: "夕食",
                    category: "主菜",
                  },
                  candidates: [
                    {
                      id: "MM100",
                      name: "白身魚フライ",
                      unit_type: "count",
                      qty_per_serving: 1,
                      daypart: "夕食",
                      category: "主菜",
                    },
                    {
                      id: "MM101",
                      name: "白身魚のフライ",
                      unit_type: "g",
                      qty_per_serving: 100,
                      daypart: "夕食",
                      category: "主菜",
                    },
                  ],
                },
                {
                  source_name: "タラのムニエル",
                  reason: "missing",
                  suggested_patch: {
                    name: "タラのムニエル",
                    unit_type: "cut",
                    qty_per_serving: 2,
                    daypart: "夕食",
                    category: "主菜",
                  },
                  candidates: [],
                },
              ],
            },
          },
        });
        return;
      }

      secondUploadPayload = route.request().postData() || "";
      await route.fulfill({
        status: 200,
        json: {
          item_count: 2,
          replaced: false,
          scope_override: null,
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-03`);

  await page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "アップロード" }) })
    .locator('input[type="file"]')
    .setInputFiles({
    name: "menu.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("dummy-menu"),
  });
  await page.getByRole("button", { name: "アップロード" }).click();

  await expect(page.getByRole("heading", { name: "未登録メニューの確認" })).toBeVisible();
  await expect(page.getByTestId("menu-master-review-card-0")).toContainText("白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ");
  await expect(page.getByTestId("menu-master-review-card-1")).toContainText("タラのムニエル");

  await page.getByTestId("menu-master-review-card-0").getByRole("radio", { name: "既存マスターを使う" }).check();
  await page.getByLabel("候補マスター-1").selectOption("MM100");

  await page.getByTestId("menu-master-review-card-1").getByRole("radio", { name: "新規登録する" }).check();
  await page.getByLabel("新規単位-2").selectOption("cut");
  await page.getByLabel("新規量-2").fill("2");

  await page.getByRole("button", { name: "この内容でアップロード" }).click();

  await expect(page.getByText("アップロードしました。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "未登録メニューの確認" })).toHaveCount(0);
  expect(secondUploadPayload).toContain("review_resolutions");
  expect(secondUploadPayload).toContain('"source_name":"白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ"');
  expect(secondUploadPayload).toContain('"action":"existing"');
  expect(secondUploadPayload).toContain('"menu_master_id":"MM100"');
  expect(secondUploadPayload).toContain('"source_name":"タラのムニエル"');
  expect(secondUploadPayload).toContain('"action":"create"');
  expect(secondUploadPayload).toContain('"unit_type":"cut"');
  expect(secondUploadPayload).toContain('"qty_per_serving":"2"');
});


test("monthly menu page can update masters, apply month-only overrides, and reflect category-only changes from diff check panel", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const resolvePayloads: string[] = [];
  const facilities = [{ id: "FAC00003", name: "施設A" }];
  const items = [
    { id: "MMI_DIFF", name: "白身魚フライ", facility_override: null, diet_type: "" },
    { id: "MMI_MONTH_ONLY", name: "アジのムニエル", facility_override: "FAC00003", diet_type: "" },
    { id: "MMI_CATEGORY", name: "胡瓜の和え物", facility_override: null, diet_type: "" },
    { id: "MMI_NEW", name: "タラのムニエル", facility_override: null, diet_type: "" },
  ];
  let issues = [
    {
      item_id: "MMI_DIFF",
      source_name: "白身魚フライ",
      issue_type: "diff",
      normalized_name: "白身魚フライ",
      suggested_patch: {
        name: "白身魚フライ",
        unit_type: "count",
        qty_per_serving: 1,
        daypart: "夕食",
        category: "主菜",
      },
      current_master: {
        id: "MNU001",
        name: "白身魚フライ",
        unit_type: "g",
        qty_per_serving: 100,
        daypart: "夕食",
        category: "主菜",
      },
      field_diffs: [
        { field: "unit_type", label: "単位", monthly_value: "count", master_value: "g" },
        { field: "qty_per_serving", label: "量", monthly_value: 1, master_value: 100 },
      ],
      candidates: [],
    },
    {
      item_id: "MMI_MONTH_ONLY",
      source_name: "アジのムニエル",
      issue_type: "diff",
      normalized_name: "アジのムニエル",
      suggested_patch: {
        name: "アジのムニエル",
        unit_type: "cut",
        qty_per_serving: 2,
        daypart: "夕食",
        category: "主菜",
      },
      current_master: {
        id: "MNU002",
        name: "アジのムニエル",
        unit_type: "g",
        qty_per_serving: 80,
        daypart: "夕食",
        category: "主菜",
      },
      field_diffs: [
        { field: "unit_type", label: "単位", monthly_value: "cut", master_value: "g" },
        { field: "qty_per_serving", label: "量", monthly_value: 2, master_value: 80 },
      ],
      candidates: [],
    },
    {
      item_id: "MMI_CATEGORY",
      source_name: "胡瓜の和え物",
      issue_type: "diff",
      normalized_name: "胡瓜の和え物",
      suggested_patch: {
        name: "胡瓜の和え物",
        unit_type: "g",
        qty_per_serving: 40,
        daypart: "夕食",
        category: "副菜",
      },
      current_master: {
        id: "MNU003",
        name: "胡瓜の和え物",
        unit_type: "g",
        qty_per_serving: 40,
        daypart: "夕食",
        category: "",
      },
      field_diffs: [{ field: "category", label: "区分", monthly_value: "副菜", master_value: "" }],
      candidates: [],
    },
    {
      item_id: "MMI_NEW",
      source_name: "タラのムニエル",
      issue_type: "missing",
      normalized_name: "タラのムニエル",
      suggested_patch: {
        name: "タラのムニエル",
        unit_type: "cut",
        qty_per_serving: 2,
        daypart: "夕食",
        category: "主菜",
      },
      current_master: null,
      field_diffs: [],
      candidates: [],
    },
  ];

  const buildMenuPayload = () => ({
    menu: {
      id: "2026-03",
      display_name: "2026年3月献立",
    },
    items,
    entries: [],
    master_checks: {
      count: issues.length,
      issues,
    },
  });

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
          facilities,
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
      await route.fulfill({ status: 200, json: buildMenuPayload() });
      return;
    }

    if (path.includes("/monthly-menus/2026-03/master-checks/") && path.endsWith("/resolve") && method === "POST") {
      resolvePayloads.push(route.request().postData() || "");
      const matched = path.match(/master-checks\/([^/]+)\/resolve$/);
      const itemId = matched?.[1] || "";
      issues = issues.filter((issue) => issue.item_id !== itemId);
      await route.fulfill({
        status: 200,
        json: {
          resolved: true,
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-03`);
  const masterCheckSection = page.locator("section").filter({ has: page.getByRole("heading", { name: "メニューマスター差分チェック" }) });
  const diffCard = masterCheckSection.locator("[data-testid^='menu-master-check-card-']").filter({ hasText: "白身魚フライ" });
  const monthOnlyCard = masterCheckSection.locator("[data-testid^='menu-master-check-card-']").filter({ hasText: "アジのムニエル" });
  const categoryCard = masterCheckSection.locator("[data-testid^='menu-master-check-card-']").filter({ hasText: "胡瓜の和え物" });
  const createCard = masterCheckSection.locator("[data-testid^='menu-master-check-card-']").filter({ hasText: "タラのムニエル" });

  await expect(page.getByRole("heading", { name: "メニューマスター差分チェック" })).toBeVisible();
  await expect(masterCheckSection.getByText("検出件数: 4")).toBeVisible();
  await expect(diffCard).toContainText("白身魚フライ");
  await expect(monthOnlyCard).toContainText("アジのムニエル");
  await expect(monthOnlyCard).toContainText("適用範囲: 施設:施設A");
  await expect(categoryCard).toContainText("胡瓜の和え物");
  await expect(createCard).toContainText("タラのムニエル");

  await diffCard.locator("select[aria-label^='差分単位-']").selectOption("count");
  await diffCard.locator("input[aria-label^='差分量-']").fill("1");
  await diffCard.locator("input[aria-label^='差分袋上限数-']").fill("25");
  await diffCard.locator("select[aria-label^='差分袋上限単位-']").selectOption("count");
  await diffCard.getByRole("button", { name: "マスターを更新" }).click();
  await expect(masterCheckSection.getByText("検出件数: 3")).toBeVisible();

  await monthOnlyCard.getByRole("radio", { name: "当月にのみ適用" }).check();
  await monthOnlyCard.locator("select[aria-label^='当月のみ単位-']").selectOption("cut");
  await monthOnlyCard.locator("input[aria-label^='当月のみ量-']").fill("");
  await monthOnlyCard.locator("input[aria-label^='当月のみ袋上限数-']").fill("20");
  await monthOnlyCard.locator("select[aria-label^='当月のみ袋上限単位-']").selectOption("cut");
  await monthOnlyCard.getByRole("button", { name: "この月だけ反映" }).click();
  await expect(masterCheckSection.getByText("当月だけ反映しました: アジのムニエル（3件 → 2件）")).toBeVisible();
  await expect(masterCheckSection.getByText("検出件数: 2")).toBeVisible();
  await expect(monthOnlyCard).toHaveCount(0);

  await categoryCard.getByRole("radio", { name: "区分だけマスターへ反映" }).check();
  await categoryCard.locator("input[aria-label^='区分だけ-']").fill("副菜");
  await categoryCard.getByRole("button", { name: "区分だけ反映" }).click();

  await createCard.locator("select[aria-label^='差分新規単位-']").selectOption("cut");
  await createCard.locator("input[aria-label^='差分新規量-']").fill("2");
  await createCard.locator("input[aria-label^='差分新規袋上限数-']").fill("20");
  await createCard.locator("select[aria-label^='差分新規袋上限単位-']").selectOption("cut");
  await createCard.getByRole("button", { name: "この内容で登録" }).click();

  expect(resolvePayloads[0]).toContain('"action":"update"');
  expect(resolvePayloads[0]).toContain('"unit_type":"count"');
  expect(resolvePayloads[1]).toContain('"action":"month_only"');
  expect(resolvePayloads[1]).toContain('"unit_type":"cut"');
  expect(resolvePayloads[1]).not.toContain('"qty_per_serving"');
  expect(resolvePayloads[2]).toContain('"action":"category_only"');
  expect(resolvePayloads[2]).toContain('"category":"副菜"');
  expect(resolvePayloads[3]).toContain('"action":"create"');
  expect(resolvePayloads[3]).toContain('"name":"タラのムニエル"');
  expect(resolvePayloads[3]).toContain('"unit_type":"cut"');
});
