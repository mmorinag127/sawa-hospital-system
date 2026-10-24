import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
if (!baseUrl) throw new Error("E2E_BASE_URL is required for isolated monthly menu source cases");

const source = { filename: "source.xlsx", file_uri: "gs://fixture/source.xlsx", content_sha256: "a".repeat(64), scope_override: null,
  diet_types: null, sheet_name: "actual-sheet", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" };
const reviewItems = Array.from({ length: 12 }, (_, index) => ({ name: index === 0 ? "Fish" : `Fish ${index}`,
  daypart: ["朝食", "昼食", "夕食"][Math.floor(index / 4)], category: "主菜", diet_type: "regular",
  unit_type: "cut", temp_type: null, qty_per_serving: 0, _inferred_master_fields: [] }));
const review = { issues: [], resolutions: [], master_diffs: [], item_count: 12,
  plans: Object.fromEntries(reviewItems.map((item, index) => [item.name, { action: "existing", menu_master_id: `master-${index}`,
    seed_fields: { unit_type: item.unit_type, temp_type: item.temp_type, qty_per_serving: item.qty_per_serving, daypart: item.daypart, category: item.category } }])),
  items: reviewItems, entries: reviewItems.map((item, index) => ({ name: item.name, menu_date: "2026-10-01", daypart: item.daypart,
    category: item.category, diet_type: item.diet_type, slot_index: index % 4 })), scope_override: null,
  input_snapshot: { revision: 1, digest: "b".repeat(64) } };
const receipt = { id: "saved-import-A", month_id: "2026-10", state: "review", source, review,
  revision: "receipt-read-1", input_revision: "monthly-read-1", current_input_revision: "monthly-read-1", current_review: review };
const snapshot = { menu: { id: "2026-10", display_name: "2026-10" }, revision: "monthly-read-1", source_month_id: "2026-10",
  items: [{ id: "item-A", month_id: "2026-10", name: "Fish", unit_type: "cut", qty_per_serving: 0, daypart: "朝食", diet_type: "regular", category: "主菜" }],
  entries: [{ id: "entry-A", month_id: "2026-10", name: "Fish", menu_date: "2026-10-01", daypart: "朝食", diet_type: "regular", category: "主菜", slot_index: 0 }],
  master_checks: { count: 0, issues: [] } };

async function setup(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem("auth_header", "Bearer source-contract-test");
    localStorage.setItem("auth_header", "Bearer source-contract-test");
    sessionStorage.setItem("sawa_auth_cache_generation", "auth-A");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { role: "admin" } });
    if (path.endsWith("/monthly-menus/scope-options")) return route.fulfill({ json: { facilities: [{ id: "F1", name: "Facility A" }], tags: [] } });
    if (path.endsWith("/monthly-menus/2026-10")) return route.fulfill({ json: snapshot });
    if (path.endsWith("/imports/saved-import-A")) return route.fulfill({ json: receipt });
    if (path.endsWith("/imports")) return route.fulfill({ json: { items: [receipt] } });
    return route.fulfill({ json: { items: [] } });
  });
}

for (const field of ["scope_override", "diet_types", "sheet_name", "media_type"]) {
  test(`saved upload missing ${field} never becomes base/all-food review`, async ({ page }) => {
    await setup(page);
    const malformed: Partial<typeof source> = { ...source };
    delete malformed[field as keyof typeof source];
    await page.route("**/api/monthly-menus?**", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return route.fulfill({ json: { ...receipt, source: malformed } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await page.locator(".sheet-chip").filter({ hasText: "Fish" }).click();
    await page.locator("input[type=file]").first().setInputFiles({ name: "source.xlsx", mimeType: source.media_type, buffer: Buffer.from("fixture") });
    await page.getByRole("button", { name: "アップロード", exact: true }).click();
    await expect(page.getByText("アップロード失敗:", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存済み差分を反映", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("1人前の量", { exact: true })).toHaveValue("0");
  });
}

test("upload saves exact receipt, review saves explicit read tokens, apply uses own token without latest GET", async ({ page }) => {
  await setup(page);
  const commands: string[] = [];
  let latestReads = 0;
  await page.route("**/api/monthly-menus**", async (route) => {
    const url = new URL(route.request().url());
    const request = route.request();
    if (request.method() === "GET") {
      if (url.pathname.endsWith("/2026-10")) latestReads += 1;
      return route.fallback();
    }
    commands.push(url.pathname);
    if (url.pathname.endsWith("/monthly-menus")) {
      expect(url.searchParams.get("month_id")).toBe("2026-10");
      return route.fulfill({ json: receipt });
    }
    if (url.pathname.endsWith("/review")) {
      expect(request.headers()["if-match"]).toBe(receipt.revision);
      expect(request.postDataJSON()).toEqual({ resolutions: [], input_revision: receipt.current_input_revision });
      return route.fulfill({ json: { ...receipt, revision: "receipt-own-2" } });
    }
    expect(url.pathname).toContain("/imports/saved-import-A/apply");
    expect(request.headers()["if-match"]).toBe("receipt-own-2");
    return route.fulfill({ json: { receipt: { ...receipt, state: "applied", revision: "receipt-own-3" },
      snapshot: { ...snapshot, revision: "monthly-own-2",
        items: reviewItems.map((item, index) => ({ ...item, id: `applied-item-${index}`, month_id: "2026-10",
          menu_master_id: `master-${index}`, master_resolution_mode: null, facility_override: null,
          bag_max_qty: null, bag_max_unit: null })),
        entries: review.entries.map((entry, index) => ({ ...entry, id: `applied-entry-${index}`,
          month_id: "2026-10", facility_override: null })) }, item_count: 12, replaced: true } });
  });
  await page.goto(`${baseUrl}/menus/2026-10`);
  await expect(page.getByRole("heading", { name: "月次シート" })).toBeVisible();
  const baseline = latestReads;
  await page.locator("input[type=file]").first().setInputFiles({ name: "source.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("fixture") });
  await page.getByRole("button", { name: "アップロード", exact: true }).click();
  await page.getByRole("button", { name: "差分設定を保存", exact: true }).click();
  await expect(page.getByText("差分設定を保存しました。対象と件数を確認して反映してください。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存済み差分を反映", exact: true }).click();
  await expect(page.getByText("保存済みimportを反映しました。", { exact: true })).toBeVisible();
  expect(latestReads).toBe(baseline);
  expect(commands).toHaveLength(3);
  await expect(page.locator(".sheet-chip")).toHaveCount(12);
  for (const item of reviewItems) await expect(page.getByRole("button", { name: item.name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Fish", exact: true }).click();
  await expect(page.getByLabel("1人前の量", { exact: true })).toHaveValue("0");
});

for (const outcome of ["resolved", "retained", "failure"] as const) {
  test(`delayed other item save ${outcome} preserves dirty master input and captured base`, async ({ page }) => {
    await setup(page);
    const issue = { item_id: "item-A", source_name: "Fish", issue_type: "diff", normalized_name: "Fish",
      suggested_patch: { name: "Fish", unit_type: "cut", qty_per_serving: 0, bag_max_qty: 5, bag_max_unit: "count", daypart: "朝食", category: "主菜" },
      current_master: { id: "master-A", name: "Fish", unit_type: "g", qty_per_serving: 100, daypart: "朝食", category: "主菜" },
      candidates: [], field_diffs: [{ field: "unit_type", label: "単位", monthly_value: "cut", master_value: "g" }] };
    const read = { ...snapshot,
      items: [{ ...snapshot.items[0], facility_override: "F1" }, { ...snapshot.items[0], id: "item-B", name: "Other B" }],
      entries: [...snapshot.entries, { ...snapshot.entries[0], id: "entry-B", name: "Other B", slot_index: 1 }],
      master_checks: { count: 1, issues: [issue] } };
    let release!: () => void;
    let entered!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { entered = resolve; });
    let masterCommands = 0;
    await page.route("**/api/monthly-menus/2026-10?**", (route) => route.fulfill({ json: read }));
    await page.route("**/api/monthly-menus/2026-10/master-checks/item-A/resolve", (route) => {
      expect(route.request().headers()["if-match"]).toBe(snapshot.revision);
      expect(route.request().postDataJSON().qty_per_serving).toBe(7);
      masterCommands += 1;
      return route.fulfill({ status: 409, json: { detail: "stale captured base" } });
    });
    await page.route("**/api/monthly-menus/2026-10/items/item-B", async (route) => {
      expect(route.request().headers()["if-match"]).toBe(snapshot.revision);
      expect(route.request().postDataJSON().qty_per_serving).toBe(3);
      entered();
      await wait;
      return route.fulfill(outcome === "failure" ? { status: 409, json: { detail: "other B conflict" } } : { json: {
        updated: true, snapshot: { ...read, revision: "other-B-own-2",
          items: read.items.filter((item) => outcome !== "resolved" || item.id !== "item-A")
            .map((item) => item.id === "item-B" ? { ...item, qty_per_serving: 3 } : item),
          master_checks: { count: outcome === "resolved" ? 0 : 1, issues: outcome === "resolved" ? [] : [{ ...issue,
            suggested_patch: { ...issue.suggested_patch, qty_per_serving: 99 } }] } } } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await page.getByRole("button", { name: "Other B", exact: true }).click();
    const editor = page.getByRole("form", { name: "献立を編集" });
    await editor.getByLabel("1人前の量").fill("3");
    const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "メニューマスター差分チェック" }) });
    await panel.getByRole("button", { name: "設定", exact: true }).click();
    const card = panel.getByTestId("menu-master-check-card-0");
    await card.locator("input[aria-label^='差分量-']").fill("7");
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    await started;
    release();
    await expect(page.getByText(outcome === "failure" ? "other B conflict" : "保存しました: Other B", { exact: false }).first()).toBeVisible();
    await expect(card).toBeVisible();
    await expect(card.locator("input[aria-label^='差分量-']")).toHaveValue("7");
    await expect(card).toContainText("適用範囲: 施設:Facility A");
    await expect(editor.getByLabel("1人前の量")).toHaveValue("3");
    if (outcome !== "failure") {
      await expect(card.getByRole("alert")).toContainText("差分の編集中入力は保持しています");
      await card.getByRole("button", { name: "マスターを更新", exact: true }).click();
      await expect(card.locator("input[aria-label^='差分量-']")).toHaveValue("7");
      expect(masterCommands).toBe(0);
    } else {
      await card.getByRole("button", { name: "マスターを更新", exact: true }).click();
      await expect(page.getByText("stale captured base", { exact: false })).toBeVisible();
      expect(masterCommands).toBe(1);
    }
  });
}

for (const outcome of ["success", "failure"] as const) {
  test(`late upload ${outcome} cannot reset newer file/sheet input`, async ({ page }) => {
    await setup(page);
    let release!: () => void;
    let started!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const entered = new Promise<void>((resolve) => { started = resolve; });
    await page.route("**/api/monthly-menus?**", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      started();
      await wait;
      await route.fulfill(outcome === "success" ? { json: receipt } : { status: 503, json: { detail: "archive failed" } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await expect(page.getByRole("heading", { name: "月次シート" })).toBeVisible();
    await page.locator("input[type=file]").first().setInputFiles({ name: "source.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("fixture") });
    await page.getByRole("button", { name: "アップロード", exact: true }).click();
    await entered;
    await page.getByPlaceholder("シート名 (任意)").fill("new operator input");
    release();
    await expect(page.getByRole("button", { name: "アップロード", exact: true })).toBeEnabled();
    await expect(page.getByPlaceholder("シート名 (任意)")).toHaveValue("new operator input");
    await expect(page.getByRole("button", { name: "保存済み差分を反映", exact: true })).toHaveCount(0);
    expect(await page.locator("input[type=file]").first().evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe("source.xlsx");
  });
}

test("delayed saved diff read cannot reset subsequently typed item", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  let entered!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  const started = new Promise<void>((resolve) => { entered = resolve; });
  await page.route("**/api/monthly-menus/2026-10/imports/saved-import-A", async (route) => {
    entered();
    await wait;
    await route.fulfill({ json: receipt });
  });
  await page.goto(`${baseUrl}/menus/2026-10`);
  await page.locator(".sheet-chip").filter({ hasText: "Fish" }).click();
  await expect(page.getByLabel("献立名", { exact: true })).toHaveValue("Fish");
  await page.getByRole("button", { name: "source.xlsx / 共通(base) / 差分確認", exact: true }).click();
  await started;
  await page.getByLabel("献立名", { exact: true }).fill("new operator edit");
  release();
  await expect(page.getByLabel("献立名", { exact: true })).toHaveValue("new operator edit");
  await expect(page.getByRole("button", { name: "保存済み差分を反映", exact: true })).toHaveCount(0);
});

for (const target of ["retained", "absent"] as const) {
  test(`exception own facility receipt preserves dirty common editor when target ${target}`, async ({ page }) => {
    await setup(page);
    await page.route("**/api/monthly-menus/2026-10/entries/entry-A/exceptions", async (route) => {
      expect(route.request().headers()["if-match"]).toBe(snapshot.revision);
      const body = route.request().postDataJSON();
      expect(body.facility_ids).toEqual(["F1"]);
      expect(body.qty_per_serving).toBe(0);
      return route.fulfill({ json: { result: { entry_id: "entry-A", entries: [{ id: "facility-entry", facility_override: "F1", name: "Facility Fish" }] },
        snapshot: { ...snapshot, revision: "exception-own", entries: target === "absent" ? [] : snapshot.entries,
          items: target === "absent" ? [] : snapshot.items } } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await expect(page.getByRole("form", { name: "献立を編集" })).toHaveCount(0);
    await page.locator(".sheet-chip").filter({ hasText: "Fish" }).click();
    const editor = page.getByRole("form", { name: "献立を編集" });
    await editor.getByLabel("1人前の量").fill("7");
    const exceptions = page.getByTestId("entry-exception-form");
    await exceptions.getByLabel("例外メニュー名").fill("Facility Fish");
    await exceptions.getByText("Facility A (F1)", { exact: true }).click();
    await exceptions.getByRole("button", { name: "この位置に例外メニューを追加" }).click();
    await expect(page.getByText("例外メニューを保存しました: Facility Fish", { exact: true })).toBeVisible();
    await expect(editor.getByLabel("1人前の量")).toHaveValue("7");
    await expect(page.getByTestId("selected-entry-name")).toHaveText("Fish");
    await expect(page.getByRole("status").filter({ hasText: "保存済み例外:" })).toContainText("Facility Fish");
  });
}

for (const outcome of ["success", "failure"] as const) {
  test(`late exception ${outcome} never selects another scope or resets newer item input`, async ({ page }) => {
    await setup(page);
    let release!: () => void;
    let entered!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { entered = resolve; });
    await page.route("**/api/monthly-menus/2026-10/entries/entry-A/exceptions", async (route) => {
      entered();
      await wait;
      return route.fulfill(outcome === "failure" ? { status: 409, json: { detail: "old exception conflict" } }
        : { json: { result: { entry_id: "entry-A", entries: [{ id: "facility-entry", facility_override: "F1", name: "Facility Fish" }] },
          snapshot: { ...snapshot, revision: "late-own" } } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await page.locator(".sheet-chip").filter({ hasText: "Fish" }).click();
    const exceptions = page.getByTestId("entry-exception-form");
    await exceptions.getByLabel("例外メニュー名").fill("Facility Fish");
    await exceptions.getByText("Facility A (F1)", { exact: true }).click();
    await exceptions.getByRole("button", { name: "この位置に例外メニューを追加" }).click();
    await started;
    const editor = page.getByRole("form", { name: "献立を編集" });
    await editor.getByLabel("1人前の量").fill("18");
    release();
    await expect(editor.getByLabel("1人前の量")).toHaveValue("18");
    await expect(page.getByTestId("selected-entry-name")).toHaveText("Fish");
    await expect(page.getByRole("status").filter({ hasText: "保存済み例外:" })).toHaveCount(0);
    await expect(page.getByText("old exception conflict", { exact: false })).toHaveCount(0);
  });
}

test("old upload success cannot cross authentication generation", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  let entered!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  const started = new Promise<void>((resolve) => { entered = resolve; });
  await page.route("**/api/monthly-menus?**", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    entered();
    await wait;
    await route.fulfill({ json: receipt });
  });
  await page.goto(`${baseUrl}/menus/2026-10`);
  await page.locator("input[type=file]").first().setInputFiles({ name: "source.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("fixture") });
  await page.getByRole("button", { name: "アップロード", exact: true }).click();
  await started;
  await page.evaluate(() => {
    sessionStorage.setItem("sawa_auth_cache_generation", "auth-B");
    window.dispatchEvent(new Event("sawa:auth-generation"));
  });
  release();
  await expect(page.getByRole("button", { name: "保存済み差分を反映", exact: true })).toHaveCount(0);
});

for (const outcome of ["success", "failure"] as const) {
  test(`late review ${outcome} preserves newer working input and receipt base`, async ({ page }) => {
    await setup(page);
    let release!: () => void;
    let entered!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { entered = resolve; });
    await page.route("**/api/monthly-menus/2026-10/imports/saved-import-A/review", async (route) => {
      entered();
      await wait;
      await route.fulfill(outcome === "success" ? { json: { ...receipt, revision: "late-own" } }
        : { status: 409, json: { detail: "stale input" } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await page.getByRole("button", { name: "source.xlsx / 共通(base) / 差分確認", exact: true }).click();
    await page.getByRole("button", { name: "差分設定を保存", exact: true }).click();
    await started;
    await page.evaluate(() => {
      const input = document.querySelector<HTMLInputElement>("input[placeholder='シート名 (任意)']");
      if (!input) throw new Error("actual source sheet input is absent");
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "later working sheet");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    release();
    await expect(page.getByPlaceholder("シート名 (任意)")).toHaveValue("later working sheet");
    await expect(page.getByText("差分設定を保存しました。対象と件数を確認して反映してください。", { exact: true })).toHaveCount(0);
    await expect(page.getByText("アップロード失敗: stale input", { exact: true })).toHaveCount(0);
  });

  test(`old receipt read ${outcome} cannot cross month navigation`, async ({ page }) => {
    await setup(page);
    let release!: () => void;
    let entered!: () => void;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { entered = resolve; });
    await page.route("**/api/monthly-menus/2026-10/imports/saved-import-A", async (route) => {
      entered();
      await wait;
      await route.fulfill(outcome === "success" ? { json: receipt } : { status: 409, json: { detail: "old October error" } });
    });
    await page.goto(`${baseUrl}/menus/2026-10`);
    await page.getByRole("button", { name: "source.xlsx / 共通(base) / 差分確認", exact: true }).click();
    await started;
    await page.goto(`${baseUrl}/menus/2026-11`);
    release();
    await expect(page.getByRole("button", { name: "保存済み差分を反映", exact: true })).toHaveCount(0);
    await expect(page.getByText("old October error", { exact: false })).toHaveCount(0);
  });
}
