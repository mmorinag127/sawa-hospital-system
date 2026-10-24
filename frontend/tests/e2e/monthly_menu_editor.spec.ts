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
          revision: "grid-read-1",
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
            {
              id: "MMI2",
              month_id: "2026-03",
              name: "白身魚フライ 添)キャベツ",
              unit_type: "cut",
              qty_per_serving: 1,
              daypart: "夕",
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
            {
              id: "MME3",
              month_id: "2026-03",
              menu_date: "2026-03-24",
              daypart: "夕",
              name: "白身魚フライ 添)キャベツ",
              category: "主菜",
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

  await page.goto(`${baseUrl}/menus/2026-03`);

  await expect(page.getByRole("heading", { name: "月次メニュー編集" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "月次シート" })).toBeVisible();
  await expect(page.getByText("メニュー一覧")).toBeVisible();
  const sheet = page.locator("[data-testid='monthly-menu-sheet']");
  await expect(sheet.getByRole("rowheader", { name: "03/24" })).toBeVisible();
  await expect(sheet.locator("thead").getByText("共通(base)")).toHaveCount(3);
  await expect(sheet.locator("thead").getByText("昼")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("夕")).toHaveCount(1);
  await expect(sheet.locator("thead").getByText("主菜")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("副菜")).toHaveCount(1);
  await expect(sheet.locator("thead").getByText("枠1")).toHaveCount(2);
  await expect(sheet.locator("thead").getByText("枠2")).toBeVisible();
  await expect(sheet.getByRole("button", { name: "ホイコーロー" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "豆腐の煮物" })).toBeVisible();
  await sheet.getByRole("button", { name: "ホイコーロー" }).click();
  await expect(page.getByTestId("selected-entry-name")).toHaveText("ホイコーロー");
  await expect(page.getByRole("combobox", { name: "朝昼夕", exact: true }).first()).toHaveText("昼");
  await page.getByRole("combobox", { name: "朝昼夕", exact: true }).first().click();
  await expect(page.getByRole("listbox").getByRole("option", { name: "昼", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await page.getByRole("combobox", { name: "単位", exact: true }).first().click();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveText(["未選択", "グラム(g)", "切れ", "個"]);
  await page.keyboard.press("Escape");
  await sheet.getByRole("button", { name: "豆腐の煮物" }).click();
  await expect(page.getByTestId("selected-entry-name")).toHaveText("豆腐の煮物");
  await expect(page.getByTestId("selected-entry-category")).toHaveText("副菜");
  await expect(page.getByTestId("condiment-flag-help")).toContainText("別ファイルは必須ではありません");
  await sheet.getByRole("button", { name: "白身魚フライ 添)キャベツ" }).click();
  await expect(page.getByTestId("selected-garnish-guidance")).toContainText("主菜: 白身魚フライ");
  await expect(page.getByTestId("selected-garnish-guidance")).toContainText("添え: キャベツ");
  await expect(page.getByTestId("selected-garnish-guidance")).toContainText("メニューマスター");
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
          revision: "daypart-read-1",
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
  await sheet.getByRole("button", { name: "ジャーマンポテト" }).nth(0).click();
  await expect(page.getByTestId("selected-entry-name")).toHaveText("ジャーマンポテト");
  await expect(page.getByLabel("1人前の量").first()).toHaveValue("70");
  await expect(page.getByRole("combobox", { name: "朝昼夕", exact: true }).first()).toHaveText("朝食");
  await page.getByRole("combobox", { name: "朝昼夕", exact: true }).first().click();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveText(["未選択", "朝食", "昼食", "夕食"]);
  await page.keyboard.press("Escape");

  await sheet.getByRole("button", { name: "ジャーマンポテト" }).nth(1).click();

  await expect(page.getByTestId("selected-entry-category")).toHaveText("副菜");
  await expect(page.getByLabel("1人前の量").first()).toHaveValue("40");
  await expect(page.getByRole("combobox", { name: "朝昼夕", exact: true }).first()).toHaveText("昼食");
});

test("monthly menu editor sends the selected item's EditForm values as the item payload", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  let savedPayload: Record<string, unknown> | null = null;
  let saved = false;
  const ownSnapshot = () => ({
    revision: saved ? "item-own-2" : "item-read-1",
    menu: { id: "2026-07", display_name: "2026年7月献立" },
    items: [{ id: "MMI1", month_id: "2026-07", name: "鯖の味噌煮", unit_type: "g", qty_per_serving: saved ? 85 : 70, temp_type: "hot", daypart: "昼食", category: "主菜", diet_type: "", facility_override: null, bag_max_qty: null, bag_max_unit: null }],
    entries: [{ id: "MME1", month_id: "2026-07", menu_date: "2026-07-01", name: "鯖の味噌煮", daypart: "昼食", category: "主菜", diet_type: "", slot_index: 1, facility_override: null }],
    master_checks: { count: 0, issues: [] },
  });

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();
    if (path.endsWith("/auth/me")) return route.fulfill({ status: 200, json: { role: "admin" } });
    if (path.endsWith("/monthly-menus/scope-options")) return route.fulfill({ status: 200, json: { facilities: [], tags: [] } });
    if (path.endsWith("/monthly-menus/2026-07/uploads")) return route.fulfill({ status: 200, json: { items: [] } });
    if (path.endsWith("/monthly-menus/2026-07/items/MMI1") && method === "PUT") {
      savedPayload = JSON.parse(route.request().postData() || "{}");
      expect(route.request().headers()["if-match"]).toBe("item-read-1");
      saved = true;
      return route.fulfill({ status: 200, json: { updated: true, snapshot: ownSnapshot() } });
    }
    if (path.endsWith("/monthly-menus/2026-07") && method === "GET") {
      return route.fulfill({ status: 200, json: ownSnapshot() });
    }
    return route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-07`);
  await page.locator(".sheet-chip").filter({ hasText: "鯖の味噌煮" }).click();
  await page.getByRole("form", { name: "献立を編集" }).getByLabel("1人前の量").fill("85");
  await page.getByRole("form", { name: "献立を編集" }).getByRole("button", { name: "保存" }).click();

  expect(savedPayload).toEqual({
    name: "鯖の味噌煮", unit_type: "g", qty_per_serving: 85, temp_type: "hot", daypart: "昼食",
    category: "主菜", diet_type: "", facility_override: "", bag_max_qty: null, bag_max_unit: "",
  });
  await expect(page.getByRole("form", { name: "献立を編集" }).getByLabel("1人前の量")).toHaveValue("85");
});

test("monthly menu page can create a facility exception from the selected entry", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  let savedRequest: Record<string, unknown> | null = null;
  let exceptionSaved = false;
  let readSnapshot: Record<string, unknown> | null = null;

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
      readSnapshot = {
          revision: "exception-read-1",
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
          master_checks: { count: 0, issues: [] },
      };
      await route.fulfill({ status: 200, json: readSnapshot });
      return;
    }

    if (path.endsWith("/monthly-menus/2026-03/entries/MME1/exceptions") && method === "POST") {
      savedRequest = JSON.parse(route.request().postData() || "{}");
      expect(route.request().headers()["if-match"]).toBe("exception-read-1");
      expect(readSnapshot).not.toBeNull();
      exceptionSaved = true;
      await route.fulfill({
        status: 200,
        json: {
          updated: true,
          entry_id: "MME1",
          facility_ids: ["FAC00003"],
          entries: [{ id: "MME_EXCEPTION", facility_override: "FAC00003", name: "鮭の塩焼き" }],
          items: [{ id: "MMI_EXCEPTION", facility_override: "FAC00003", name: "鮭の塩焼き" }],
          result: { entry_id: "MME1", entries: [{ id: "MME_EXCEPTION", facility_override: "FAC00003", name: "鮭の塩焼き" }] },
          snapshot: { ...readSnapshot, revision: "exception-own-2" },
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/menus/2026-03`);

  await page.locator(".sheet-chip").filter({ hasText: "ホイコーロー" }).click();
  await page.getByTestId("selected-entry-name").waitFor();
  await page.getByRole("form", { name: "献立を編集" }).getByLabel("1人前の量").fill("85");
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
  await expect(page.getByTestId("selected-entry-name")).toHaveText("ホイコーロー");
  await expect(page.getByRole("form", { name: "献立を編集" }).getByLabel("1人前の量")).toHaveValue("85");
  await expect(page.getByRole("status").filter({ hasText: "保存済み例外:" })).toContainText("鮭の塩焼き");
  await expect(page.locator(".sheet-chip.active")).toHaveText("ホイコーロー");
});

test("monthly menu saved upload requires explicit master review and apply", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  let uploadAttemptCount = 0;
  let secondUploadPayload = "";
  let savedReceipt: Record<string, any> | null = null;

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
          master_checks: { count: 0, issues: [] },
          revision: "import-month-read-1",
        },
      });
      return;
    }

    if ((path.endsWith("/monthly-menus") || path.endsWith("/imports/review-A/review") || path.endsWith("/imports/review-A/apply")) && method === "POST") {
      if (path.endsWith("/monthly-menus")) {
        uploadAttemptCount += 1;
        const issueResponse = {
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
        };
        const issues = issueResponse.json.detail.issues.map((issue, index) => ({ ...issue,
          issue_key: ["白身魚のフライ添ブロッコリー", "タラのムニエル"][index], normalized_name: ["白身魚のフライ添ブロッコリー", "タラのムニエル"][index],
          suggested_patch: { temp_type: null, bag_max_qty: null, bag_max_unit: null, condiments: [], ...issue.suggested_patch, name: issue.source_name },
          candidates: issue.candidates.map((candidate, candidateIndex) => ({ revision: 1, temp_type: null, bag_max_qty: null, bag_max_unit: null,
            condiments: [], normalized_name: ["白身魚フライ", "白身魚のフライ"][candidateIndex], ...candidate })) }));
        const review = { issues, resolutions: [], master_diffs: [], item_count: 2,
          plans: {}, items: issues.map((issue) => ({ ...issue.suggested_patch, name: issue.source_name, diet_type: null, _inferred_master_fields: [] })),
          entries: issues.map((issue, index) => ({ name: issue.source_name, menu_date: "2026-03-01", daypart: "夕食",
            category: "主菜", diet_type: null, slot_index: index })), scope_override: null, input_snapshot: { revision: 1, digest: "b".repeat(64) } };
        savedReceipt = { id: "review-A", month_id: "2026-03", state: "review",
          source: { filename: "menu.xlsx", file_uri: "gs://fixture/menu.xlsx", content_sha256: "a".repeat(64), scope_override: null,
            diet_types: null, sheet_name: "actual-sheet", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
          review, current_review: review, revision: "import-receipt-read-1",
          input_revision: "import-month-read-1", current_input_revision: "import-month-read-1" };
        await route.fulfill({ status: 200, json: savedReceipt });
        return;
      }

      if (!savedReceipt) throw new Error("review must have a saved original receipt");
      if (path.endsWith("/review")) {
        expect(route.request().headers()["if-match"]).toBe("import-receipt-read-1");
        secondUploadPayload = route.request().postData() || "";
        const body = route.request().postDataJSON();
        expect(body.input_revision).toBe("import-month-read-1");
        const review = { ...savedReceipt.review, issues: [], resolutions: body.resolutions,
          plans: Object.fromEntries(body.resolutions.map((resolution: any) => {
            const sourceItem = savedReceipt.review.items.find((item: any) => item.name === resolution.source_name);
            if (!sourceItem) throw new Error("explicit resolution must match the saved source item");
            const seed_fields = { unit_type: sourceItem.unit_type, qty_per_serving: sourceItem.qty_per_serving,
              daypart: sourceItem.daypart, category: sourceItem.category,
              ...(resolution.action === "create" ? { unit_type: resolution.unit_type, qty_per_serving: Number(resolution.qty_per_serving) } : {}) };
            return [resolution.source_name, { action: resolution.action,
              ...(resolution.action === "create" ? { name: resolution.name || resolution.source_name } : { menu_master_id: resolution.menu_master_id }), seed_fields }];
          })) };
        savedReceipt = { ...savedReceipt, review, current_review: review, revision: "import-receipt-own-2" };
        await route.fulfill({ status: 200, json: savedReceipt });
      } else {
        expect(route.request().headers()["if-match"]).toBe("import-receipt-own-2");
        await route.fulfill({ status: 200, json: { item_count: 2, replaced: false, receipt: { ...savedReceipt, state: "applied" },
          snapshot: { source_month_id: "2026-03", menu: { id: "2026-03" },
            items: savedReceipt.review.items.map((item: any, index: number) => ({ ...item,
              ...savedReceipt.review.plans[item.name].seed_fields, id: `applied-item-${index}`, month_id: "2026-03",
              menu_master_id: savedReceipt.review.plans[item.name].action === "existing"
                ? savedReceipt.review.plans[item.name].menu_master_id : "MM102",
              master_resolution_mode: null, facility_override: null })),
            entries: savedReceipt.review.entries.map((entry: any, index: number) => ({ ...entry,
              id: `applied-entry-${index}`, month_id: "2026-03", facility_override: null })),
            master_checks: { count: 0, issues: [] }, revision: "import-month-own-2" } } });
      }
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
  await expect(page.getByTestId("menu-master-review-incomplete-summary")).toContainText("未完了 2件 / 2件");
  await expect(page.getByTestId("menu-master-review-incomplete-summary")).toContainText("白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ");
  await expect(page.getByTestId("menu-master-review-incomplete-summary")).toContainText("タラのムニエル");
  await expect(page.getByTestId("menu-master-review-card-0").getByRole("radio", { name: "既存マスターを使う" })).not.toBeChecked();
  await expect(page.getByTestId("menu-master-review-card-0").getByRole("radio", { name: "新規登録する" })).not.toBeChecked();
  await expect(page.getByTestId("menu-master-review-card-1").getByRole("radio", { name: "新規登録する" })).not.toBeChecked();
  await expect(page.getByTestId("menu-master-review-card-0")).toContainText("未完了: 対応方法を選択");
  await expect(page.getByTestId("menu-master-review-card-1")).toContainText("未完了: 対応方法を選択");

  await page.getByRole("button", { name: "未完了項目を確認する" }).click();
  expect(uploadAttemptCount).toBe(1);
  await expect(page.getByRole("alert").filter({ hasText: "未登録メニューの確認が2件残っています。最初の未完了項目: 白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ（対応方法を選択）" }))
    .toBeVisible();
  await expect(page.getByTestId("menu-master-review-card-0")).toBeFocused();

  await page.getByTestId("menu-master-review-card-0").getByRole("radio", { name: "既存マスターを使う" }).check();
  await expect(page.getByLabel("候補マスター-1")).toHaveValue("");
  await expect(page.getByTestId("menu-master-review-card-0")).toContainText("未完了: 候補マスターを選択");
  await page.getByLabel("候補マスター-1").selectOption("MM100");
  await expect(page.getByTestId("menu-master-review-incomplete-summary")).toContainText("未完了 1件 / 2件");

  await page.getByRole("button", { name: "未完了項目を確認する" }).click();
  expect(uploadAttemptCount).toBe(1);
  await expect(page.getByTestId("menu-master-review-card-1")).toBeFocused();

  await page.getByTestId("menu-master-review-card-1").getByRole("radio", { name: "新規登録する" }).check();
  await expect(page.getByLabel("新規単位-2")).toHaveValue("");
  await expect(page.getByLabel("新規量-2")).toHaveValue("");
  await expect(page.getByTestId("menu-master-review-card-1")).toContainText("未完了: 基準単位を選択、基準量を入力");
  await page.getByLabel("新規単位-2").selectOption("cut");
  await page.getByLabel("新規量-2").fill("2");

  await expect(page.getByTestId("menu-master-review-incomplete-summary")).toContainText("全2件の確認が完了しています。");

  await page.getByRole("button", { name: "差分設定を保存", exact: true }).click();
  await expect(page.getByText("差分設定を保存しました。対象と件数を確認して反映してください。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存済み差分を反映", exact: true }).click();

  await expect(page.getByText("保存済みimportを反映しました。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "未登録メニューの確認" })).toHaveCount(0);
  expect(uploadAttemptCount).toBe(1);
  expect(secondUploadPayload).toContain('"resolutions":');
  expect(secondUploadPayload).toContain('"source_name":"白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ"');
  expect(secondUploadPayload).toContain('"action":"existing"');
  expect(secondUploadPayload).toContain('"menu_master_id":"MM100"');
  expect(secondUploadPayload).toContain('"source_name":"タラのムニエル"');
  expect(secondUploadPayload).toContain('"action":"create"');
  expect(secondUploadPayload).toContain('"unit_type":"cut"');
  expect(secondUploadPayload).toContain('"qty_per_serving":"2"');
  await expect(page.locator(".sheet-chip")).toHaveCount(2);
  await page.getByRole("button", { name: "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ", exact: true }).click();
  await expect(page.getByLabel("1人前の量", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "タラのムニエル", exact: true }).click();
  await expect(page.getByLabel("1人前の量", { exact: true })).toHaveValue("2");
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
    revision: `master-read-${resolvePayloads.length}`,
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
      expect(route.request().headers()["if-match"]).toBe(`master-read-${resolvePayloads.length}`);
      resolvePayloads.push(route.request().postData() || "");
      const matched = path.match(/master-checks\/([^/]+)\/resolve$/);
      const itemId = matched?.[1] || "";
      issues = issues.filter((issue) => issue.item_id !== itemId);
      await route.fulfill({
        status: 200,
        json: {
          resolved: true,
          result: { resolved: true },
          snapshot: buildMenuPayload(),
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
  const selectIssue = async (name: string) => {
    await masterCheckSection.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "設定", exact: true }).click();
  };

  await expect(page.getByRole("heading", { name: "メニューマスター差分チェック" })).toBeVisible();
  await expect(masterCheckSection.getByText("検出件数: 4")).toBeVisible();
  await selectIssue("白身魚フライ");
  await expect(diffCard).toContainText("白身魚フライ");
  await selectIssue("アジのムニエル");
  await expect(monthOnlyCard).toContainText("アジのムニエル");
  await expect(monthOnlyCard).toContainText("適用範囲: 施設:施設A");
  await selectIssue("胡瓜の和え物");
  await expect(categoryCard).toContainText("胡瓜の和え物");
  await selectIssue("タラのムニエル");
  await expect(createCard).toContainText("タラのムニエル");

  await selectIssue("白身魚フライ");
  await diffCard.locator("select[aria-label^='差分単位-']").selectOption("count");
  await diffCard.locator("input[aria-label^='差分量-']").fill("1");
  await diffCard.locator("input[aria-label^='差分袋上限数-']").fill("25");
  await diffCard.locator("select[aria-label^='差分袋上限単位-']").selectOption("count");
  await diffCard.getByRole("button", { name: "マスターを更新" }).click();
  await expect(masterCheckSection.getByText("検出件数: 3")).toBeVisible();

  await selectIssue("アジのムニエル");
  await monthOnlyCard.getByRole("radio", { name: "当月にのみ適用" }).check();
  await monthOnlyCard.locator("select[aria-label^='当月のみ単位-']").selectOption("cut");
  await monthOnlyCard.locator("input[aria-label^='当月のみ量-']").fill("");
  await monthOnlyCard.locator("input[aria-label^='当月のみ袋上限数-']").fill("20");
  await monthOnlyCard.locator("select[aria-label^='当月のみ袋上限単位-']").selectOption("cut");
  await monthOnlyCard.getByRole("button", { name: "この月だけ反映" }).click();
  await expect(masterCheckSection.getByText("当月だけ反映しました: アジのムニエル（3件 → 2件）")).toBeVisible();
  await expect(masterCheckSection.getByText("検出件数: 2")).toBeVisible();
  await expect(monthOnlyCard).toHaveCount(0);

  await selectIssue("胡瓜の和え物");
  await categoryCard.getByRole("radio", { name: "区分だけマスターへ反映" }).check();
  await categoryCard.locator("input[aria-label^='区分だけ-']").fill("副菜");
  await categoryCard.getByRole("button", { name: "区分だけ反映" }).click();

  await expect(masterCheckSection.getByText("検出件数: 1")).toBeVisible();
  await selectIssue("タラのムニエル");
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
