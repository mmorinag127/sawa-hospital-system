import { expect, test, type Locator, type Page, type Request, type TestInfo } from "@playwright/test";

type RecordValue = {
  id: string; revision: number; name: string; unit_type: string | null;
  qty_per_serving: number | null; bag_max_qty: number | null; bag_max_unit: string | null;
  temp_type: string | null; daypart: string | null; category: string | null; condiments: string[];
};
const record = (id: string, name: string): RecordValue => ({ id, revision: 1, name,
  unit_type: "cut", qty_per_serving: 1, bag_max_qty: 5, bag_max_unit: "count",
  temp_type: "hot", daypart: "夕食", category: "主菜", condiments: [] });
const createForm = (page: Page) => page.getByRole("form", { name: "メニューマスターを追加", exact: true });
const editForm = (page: Page) => page.getByRole("form", { name: /を編集$/ });
const field = (form: Locator, name: string) => form.getByRole("textbox", { name, exact: true });
const gate = () => { let release!: () => void; const wait = new Promise<void>(r => { release = r; }); return { wait, release }; };

async function fixture(page: Page, info: TestInfo, options: { guest?: boolean; count?: number } = {}) {
  const state = {
    items: [record("MNU001", "白身魚のフライ"), record("MNU002", "日本語の白飯")],
    requests: [] as { method: string; path: string; query: string; body: any; bearer: string }[],
    unexpected: [] as string[], errors: [] as string[], console: [] as string[], blocked: [] as string[],
    putStatus: 200, getStatus: 200, listStatus: 200, postStatus: 200,
    putGate: undefined as ReturnType<typeof gate> | undefined,
    postGate: undefined as ReturnType<typeof gate> | undefined,
  };
  for (let i = 2; i < (options.count || 2); i++) state.items.push(record(`MNU${i + 1}`, `日本語メニュー${i + 1}`));
  page.on("pageerror", e => state.errors.push(e.message));
  page.on("console", m => { if (["error", "warning"].includes(m.type())) state.console.push(m.text()); });
  await page.addInitScript(() => {
    const replace = history.replaceState;
    (window as any).removeOwnedMenuPosition = () => {
      const { sawa_navigation, ...unchangedNextState } = history.state;
      replace.call(history, unchangedNextState, "");
      return sawa_navigation;
    };
    (window as any).menuNavigationWrites = [];
    history.replaceState = function (state, unused, url) {
      if (state?.sawa_navigation) (window as any).menuNavigationWrites.push({ ...state.sawa_navigation });
      return replace.call(this, state, unused, url);
    };
  });
  if (!options.guest) await page.addInitScript(() => {
    // Initialize only the first document. Logout/reload must not silently restore authentication.
    if (sessionStorage.getItem("menu_test_initialized")) return;
    sessionStorage.setItem("menu_test_initialized", "1");
    sessionStorage.setItem("auth_header", "Bearer menu-test-A");
    sessionStorage.setItem("sawa_auth_generation", localStorage.getItem("sawa_logout_generation") || "");
    sessionStorage.setItem("sawa_auth_cache_generation", "menu-test-A");
  });
  const origin = new URL(String(info.project.use.baseURL)).origin;
  const pending = new Set<Request>(), failures: string[] = [];
  let finished = 0;
  page.on("request", request => { if (new URL(request.url()).origin === origin) pending.add(request); });
  page.on("requestfinished", request => { if (pending.delete(request)) finished++; });
  page.on("requestfailed", request => {
    if (pending.delete(request)) failures.push(`${request.url()}: ${request.failure()?.errorText}`);
  });
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { state.blocked.push(url.origin + url.pathname); return route.abort("blockedbyclient"); }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api(?:\/hospital)?/, ""), method = req.method();
    const body = req.postData() ? req.postDataJSON() : null, bearer = req.headers().authorization || "";
    state.requests.push({ method, path, query: url.search, body, bearer });
    const respond = (status: number, json: any) => route.fulfill({ status, json });
    if (path === "/auth/config") return respond(200, { google_client_id: "" });
    if (!/^Bearer menu-test-[AB]$/.test(bearer)) return respond(401, { detail: "Synthetic Bearer required" });
    if (path === "/auth/me") return respond(200, { role: "admin", auth_disabled: false });
    if (path === "/portal/auth/me") return respond(200, { role: "admin", systems: ["hospital"], auth_disabled: false });
    if (path === "/menu-rules") return respond(200, { items: [] });
    if (path === "/menu-masters" && method === "GET") {
      if (state.listStatus !== 200) return respond(state.listStatus, { detail: "synthetic list failure" });
      const offset = Number(url.searchParams.get("offset") || 0), limit = Number(url.searchParams.get("limit") || 50);
      const matches = state.items.filter(r => r.name.includes(url.searchParams.get("q") || ""));
      return respond(200, { items: matches.slice(offset, offset + limit), total: matches.length, offset, limit });
    }
    if (path === "/menu-masters" && method === "POST") {
      await state.postGate?.wait;
      if (state.postStatus !== 200) return respond(state.postStatus, { detail: "synthetic create failure" });
      // Existing duplicate registration returns its current record, without overwriting it.
      let item = state.items.find(r => r.name === body.name);
      if (!item) { item = { ...body, id: `NEW${state.items.length}`, revision: 1 }; state.items.push(item!); }
      return respond(200, { item });
    }
    const item = state.items.find(r => path === `/menu-masters/${r.id}`);
    if (item && method === "GET") return respond(state.getStatus, state.getStatus === 200 ? { item } : { detail: "synthetic reload failure" });
    if (item && method === "PUT") {
      await state.putGate?.wait;
      const status = state.putStatus !== 200 ? state.putStatus : body.revision !== item.revision ? 409 : 200;
      if (status !== 200) return respond(status, { detail: "synthetic update failure" });
      Object.assign(item, body, { revision: item.revision + 1 });
      return respond(200, { item });
    }
    state.unexpected.push(`${method} ${path}`);
    return respond(500, { detail: "Unmocked request; no live fallback" });
  });
  return Object.assign(state, { settleDocument: async () => {
    // Finish finite same-origin traffic before the test destroys this document.
    await page.waitForLoadState("networkidle");
    expect([...pending].map(request => request.url())).toEqual([]);
    expect(failures).toEqual([]);
    return finished;
  } });
}

async function finish(page: Page, info: TestInfo, state: Awaited<ReturnType<typeof fixture>>) {
  await info.attach("local-api-and-browser-evidence", { body: JSON.stringify(state, (k, v) => /Gate$/.test(k) ? undefined : v, 2), contentType: "application/json" });
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
  expect(state.console.filter(s => /hydration|did not match|Minified React error|Failed prop type|out of range/i.test(s))).toEqual([]);
  expect(state.requests.filter(r => r.path !== "/auth/config").every(r => /^Bearer menu-test-[AB]$/.test(r.bearer))).toBe(true);
}
async function open(page: Page, path = "/hospital/menu-masters") {
  const response = await page.goto(path);
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "メニューマスター", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "白身魚のフライ", exact: true })).toBeVisible();
}
async function select(page: Page, name = "白身魚のフライ") {
  await page.getByRole("row").filter({ has: page.getByRole("cell", { name, exact: true }) }).getByRole("button", { name: "編集", exact: true }).click();
  await expect(editForm(page)).toBeVisible();
}
async function option(page: Page, form: Locator, name: string, value: string) {
  await form.getByRole("combobox", { name, exact: true }).click();
  await page.getByRole("option", { name: value, exact: true }).click();
}
async function save(form: Locator) { await form.getByRole("button", { name: "保存", exact: true }).click(); }
async function saved(form: Locator) { await expect(form.getByRole("status")).toHaveText("保存しました。"); }
async function search(page: Page, value: string) {
  await page.getByRole("textbox", { name: "メニュー名で検索", exact: true }).fill(value);
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("q") || "").toBe(value.trim());
  await expect(page.getByRole("button", { name: "検索", exact: true })).toBeEnabled();
}
async function position(page: Page) { return page.evaluate(() => ({ url: location.href, position: history.state.sawa_navigation, nextStatePresent: !!history.state.__N })); }
async function historyGo(page: Page, delta: number) { await page.evaluate(n => history.go(n), delta); }

for (const path of ["/menu-masters", "/hospital/menu-masters"]) {
  test(`both entry, nine-field POST/PUT and fresh document saved values: ${path}`, async ({ page }, info) => {
    const s = await fixture(page, info); await open(page, path);
    const documentResponse = await page.request.get(path, { maxRedirects: 0 });
    const documentBody = await documentResponse.body();
    await info.attach("entry-http-response", { body: JSON.stringify({ path, status: documentResponse.status(), location: documentResponse.headers().location || null, bytes: documentBody.length }), contentType: "application/json" });
    expect(documentResponse.status()).toBe(path === "/menu-masters" ? 308 : 200);
    await expect(page.locator(".unified-current")).toHaveText("病院注文");
    const form = createForm(page);
    await field(form, "メニュー名").fill("タラのムニエル");
    await option(page, form, "単位", "切れ"); await option(page, form, "袋単位", "個");
    await option(page, form, "温冷", "冷");
    await form.getByRole("spinbutton", { name: "1人前数量", exact: true }).fill("0");
    await form.getByRole("spinbutton", { name: "袋上限数量", exact: true }).fill("10");
    await field(form, "食事帯").fill("昼"); await field(form, "分類").fill("主菜"); await field(form, "付属品").fill("塩, レモン");
    const payload = { name: "タラのムニエル", unit_type: "cut", qty_per_serving: 0, bag_max_qty: 10, bag_max_unit: "count", temp_type: "cold", daypart: "昼", category: "主菜", condiments: ["塩", "レモン"] };
    await save(form); await saved(form);
    expect(s.requests.find(r => r.method === "POST")?.body).toEqual(payload);
    await select(page); const edit = editForm(page);
    await field(edit, "メニュー名").fill("白身魚の保存値");
    await option(page, edit, "単位", "個"); await option(page, edit, "袋単位", "切れ");
    await edit.getByRole("spinbutton", { name: "1人前数量", exact: true }).fill("2");
    await edit.getByRole("spinbutton", { name: "袋上限数量", exact: true }).fill("");
    await option(page, edit, "温冷", "温");
    await field(edit, "食事帯").fill("夕"); await field(edit, "分類").fill("魚"); await field(edit, "付属品").fill("ソース");
    const update = { name: "白身魚の保存値", unit_type: "count", bag_max_unit: "cut", qty_per_serving: 2, bag_max_qty: null, temp_type: "hot", daypart: "夕", category: "魚", condiments: ["ソース"], revision: 1 };
    await save(edit); await saved(edit); expect(s.requests.find(r => r.method === "PUT")?.body).toEqual(update);
    const before = await page.evaluate(() => performance.timeOrigin);
    expect((await page.reload())?.status()).toBe(200);
    await select(page, "白身魚の保存値");
    const nav = await page.evaluate(() => ({ timeOrigin: performance.timeOrigin, type: (performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming).type }));
    expect(nav.type).toBe("reload"); expect(nav.timeOrigin).toBeGreaterThan(before);
    await expect(editForm(page).getByRole("combobox", { name: "単位", exact: true })).toHaveText("個");
    await expect(editForm(page).getByRole("combobox", { name: "袋単位", exact: true })).toHaveText("切れ");
    await expect(editForm(page).getByRole("spinbutton", { name: "袋上限数量", exact: true })).toHaveValue("");
    await expect(field(editForm(page), "メニュー名")).toHaveValue(update.name);
    await expect(editForm(page).getByRole("spinbutton", { name: "1人前数量", exact: true })).toHaveValue("2");
    await expect(editForm(page).getByRole("combobox", { name: "温冷", exact: true })).toHaveText("温");
    await expect(field(editForm(page), "食事帯")).toHaveValue("夕");
    await expect(field(editForm(page), "分類")).toHaveValue("魚");
    await expect(field(editForm(page), "付属品")).toHaveValue("ソース");
    expect(s.items[0]).toMatchObject({ ...update, revision: 2 });
    const savedRow = page.getByRole("row").filter({ has: page.getByRole("cell", { name: update.name, exact: true }) });
    await expect(savedRow.getByRole("cell")).toHaveText([update.name, "個", "2", "—", "切れ", "温", "夕", "魚", "ソース", "編集"]);
    const createdRow = page.getByRole("row").filter({ has: page.getByRole("cell", { name: payload.name, exact: true }) });
    await expect(createdRow.getByRole("cell")).toHaveText([payload.name, "切れ", "0", "10", "個", "冷", "昼", "主菜", "塩、レモン", "編集"]);
    await page.getByRole("textbox", { name: "メニュー名で検索", exact: true }).fill("日本語");
    for (const width of [360, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect(page.getByRole("heading", { name: "メニューマスター", exact: true })).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await info.attach(`actual-next-${width}`, { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
      const rects = await page.locator('form input:not([aria-hidden="true"]), form [role="combobox"]').evaluateAll(inputs => inputs.map(input => {
        const labelId = input.getAttribute('aria-labelledby')?.split(' ')[0];
        const label = labelId ? document.getElementById(labelId) : document.querySelector(`label[for="${input.id}"]`);
        const a = input.getBoundingClientRect(), b = label?.getBoundingClientRect();
        return { value: input instanceof HTMLInputElement ? input.value : input.textContent?.replace(/\u200b/g, '').trim(), label: label?.textContent, input: { x: a.x, y: a.y, height: a.height }, labelBottom: b?.bottom };
      }));
      await info.attach(`label-rectangles-${width}`, { body: JSON.stringify(rects, null, 2), contentType: "application/json" });
      for (const rect of rects.filter(r => r.value)) expect(rect.labelBottom).toBeLessThan(rect.input.y + rect.input.height / 2);
      const heading = await page.getByRole("heading", { name: "編集: 白身魚の保存値", exact: true }).boundingBox();
      const firstLabel = await editForm(page).locator("label").first().boundingBox();
      expect(firstLabel!.y).toBeGreaterThan(heading!.y + heading!.height);
      if (width === 360) {
        const region = page.getByRole("region", { name: "メニューマスター一覧" });
        const tableWidth = await page.getByRole("table", { name: "メニューマスター一覧" }).boundingBox();
        expect(tableWidth!.width).toBeGreaterThan((await region.boundingBox())!.width);
        await region.evaluate(el => { el.scrollLeft = el.scrollWidth; });
        await expect.poll(() => region.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
        await expect(region.getByRole("button", { name: "編集", exact: true }).first()).toBeInViewport();
        await region.evaluate(el => { el.scrollLeft = 0; });
      }
    }
    await finish(page, info, s);
  });
}

for (const [kind, name] of [
  ["stg-long-name", "c1-live-37266102226-1-aabd0ce687864d539b5b6f9ea984dcdd"],
  ["unbroken-ascii", "X".repeat(96)],
  ["japanese-control", "白身魚のフライ"],
]) test(`long content remains visible within the page: ${kind}`, async ({ page }, info) => {
  const s = await fixture(page, info);
  s.items = [record("MNU001", name)];
  expect((await page.goto("/hospital/menu-masters"))?.status()).toBe(200);
  await select(page, name);
  const heading = page.getByRole("heading", { name: `編集: ${name}`, exact: true });
  for (const width of [360, 1280]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(heading).toHaveText(`編集: ${name}`);
    await expect(field(editForm(page), "メニュー名")).toHaveValue(name);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const metrics = await heading.evaluate(node => {
      const box = node.getBoundingClientRect(), range = document.createRange();
      range.selectNodeContents(node);
      const text = range.getBoundingClientRect(), style = getComputedStyle(node);
      return { right: box.right, width: box.width, bottom: box.bottom, textRight: text.right,
        textWidth: text.width, overflowX: style.overflowX, textOverflow: style.textOverflow };
    });
    expect(metrics.textRight).toBeLessThanOrEqual(metrics.right + 1);
    expect(metrics.textWidth).toBeLessThanOrEqual(metrics.width + 1);
    expect(metrics.overflowX).toBe("visible");
    expect(metrics.textOverflow).not.toBe("ellipsis");
    const firstLabel = await editForm(page).locator("label").first().boundingBox();
    expect(firstLabel!.y).toBeGreaterThan(metrics.bottom);
    await info.attach(`long-content-metrics-${width}`, { body: JSON.stringify(metrics), contentType: "application/json" });
    await info.attach(`long-content-${width}`, { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  }
  await s.settleDocument();
  await finish(page, info, s);
});

test("g/null/zero and duplicate registration does not overwrite", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); const form = createForm(page);
  await field(form, "メニュー名").fill("数量ゼロ"); await option(page, form, "単位", "グラム (g)"); await option(page, form, "袋単位", "グラム (g)");
  await form.getByRole("spinbutton", { name: "袋上限数量", exact: true }).fill("0"); await save(form); await saved(form);
  expect(s.requests.find(r => r.method === "POST")?.body).toEqual({ name: "数量ゼロ", unit_type: "g", qty_per_serving: null, bag_max_qty: 0, bag_max_unit: "g", temp_type: null, daypart: null, category: null, condiments: [] });
  const original = { ...s.items[0] };
  await field(form, "メニュー名").fill(original.name); await field(form, "分類").fill("重複で上書き禁止"); await save(form); await saved(form);
  expect(s.items[0]).toEqual(original); expect(s.items).toHaveLength(3);
  await expect(field(editForm(page), "分類")).toHaveValue(original.category!);
  s.items.push({ ...record("UNKNOWN", "未知コード"), unit_type: "UNIT-X", bag_max_unit: null, temp_type: "TEMP-X" });
  await page.reload();
  const zero = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "数量ゼロ", exact: true }) });
  await expect(zero.getByRole("cell")).toHaveText(["数量ゼロ", "グラム (g)", "—", "0", "グラム (g)", "—", "—", "—", "—", "編集"]);
  const unknown = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "未知コード", exact: true }) });
  await expect(unknown.getByRole("cell")).toHaveText(["未知コード", "UNIT-X", "1", "5", "—", "TEMP-X", "夕食", "主菜", "—", "編集"]);
  await finish(page, info, s);
});

test("409 retains draft; failed explicit reload retains conflict; successful reload unlocks", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); const form = editForm(page);
  await field(form, "分類").fill("未保存409"); s.items[0].revision = 2; s.items[0].category = "別担当の保存";
  await save(form); await expect(form.getByRole("alert")).toContainText("他の編集"); await expect(field(form, "分類")).toHaveValue("未保存409");
  await expect(form.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
  s.getStatus = 500; await form.getByRole("button", { name: "再読込", exact: true }).click();
  await page.getByRole("button", { name: "破棄して再読込", exact: true }).click();
  await expect(form.getByRole("alert")).toBeVisible(); await expect(field(form, "分類")).toHaveValue("未保存409");
  await expect(form.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
  s.getStatus = 200; await form.getByRole("button", { name: "再読込", exact: true }).click();
  await page.getByRole("button", { name: "戻る", exact: true }).click(); await expect(field(form, "分類")).toHaveValue("未保存409");
  await form.getByRole("button", { name: "再読込", exact: true }).click(); await page.getByRole("button", { name: "破棄して再読込", exact: true }).click();
  await expect(field(form, "分類")).toHaveValue("別担当の保存"); await expect(form.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
  await field(form, "分類").fill("再読込後"); await save(form); await saved(form);
  expect(s.requests.filter(r => r.method === "PUT").map(r => r.body.revision)).toEqual([1, 2]); await finish(page, info, s);
});

test("500 retains input and pending save ignores a duplicate submit", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); const form = editForm(page);
  s.putStatus = 500; s.putGate = gate(); await field(form, "分類").fill("保存失敗で保持"); await save(form);
  await expect(form).toHaveAttribute("aria-busy", "true");
  await form.evaluate(el => { el.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await expect(field(form, "分類")).toBeDisabled(); expect(s.requests.filter(r => r.method === "PUT")).toHaveLength(1);
  s.putGate.release(); await expect(form.getByRole("alert")).toBeVisible(); await expect(field(form, "分類")).toHaveValue("保存失敗で保持");
  s.putStatus = 200; await save(form); await saved(form); expect(s.items[0].category).toBe("保存失敗で保持"); await finish(page, info, s);
});

test("pending shared Select cannot change the submitted values", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); s.putGate = gate();
  await field(editForm(page), "分類").fill("保存中の選択値"); await save(editForm(page));
  await expect(editForm(page)).toHaveAttribute("aria-busy", "true");
  try {
    for (const name of ["単位", "袋単位", "温冷"]) {
      const select = editForm(page).getByRole("combobox", { name, exact: true });
      // Raw DOM activation probes MUI's non-native select as well as its disabled semantics.
      await select.dispatchEvent("mousedown", { button: 0 });
      await expect(page.getByRole("listbox")).toHaveCount(0);
      await expect(select).toBeDisabled();
      const box = await select.boundingBox();
      await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
      await select.press("ArrowDown"); await select.press("Enter");
      await expect(page.getByRole("listbox")).toHaveCount(0);
      for (let i = 0; i < 4; i++) {
        await page.keyboard.press("Tab"); await expect(select).not.toBeFocused();
      }
    }
  } finally { s.putGate.release(); }
  await saved(editForm(page));
  for (const name of ["単位", "袋単位", "温冷"]) await expect(editForm(page).getByRole("combobox", { name, exact: true })).toBeEnabled();
  expect(s.requests.find(r => r.method === "PUT")?.body).toMatchObject({ unit_type: "cut", bag_max_unit: "count", temp_type: "hot" });
  await finish(page, info, s);
});

test("actual table 1500/0/g remains readable and keyboard-scrollable on mobile", async ({ page }, info) => {
  const s = await fixture(page, info);
  const name = "日本語の長いメニュー名と季節野菜の盛り合わせ".repeat(3);
  s.items.push({ ...record("LARGE", name), unit_type: "g", qty_per_serving: 0, bag_max_qty: 1500, bag_max_unit: "g" });
  await open(page);
  const region = page.getByRole("region", { name: "メニューマスター一覧" });
  const row = page.getByRole("row").filter({ has: page.getByRole("cell", { name, exact: true }) });
  for (const width of [360, 1280]) {
    await page.setViewportSize({ width, height: 1000 });
    await region.scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const metrics = await row.getByRole("cell").evaluateAll(cells => cells.map(cell => {
      const range = document.createRange(); range.selectNodeContents(cell);
      const rect = cell.getBoundingClientRect();
      return { text: cell.textContent, width: rect.width, height: rect.height, lines: new Set([...range.getClientRects()].map(r => Math.round(r.y))).size, whiteSpace: getComputedStyle(cell).whiteSpace };
    }));
    for (const text of ["1500", "0", "グラム (g)", "温"]) {
      const cells = metrics.filter(c => c.text === text); expect(cells.length).toBeGreaterThan(0);
      for (const cell of cells) expect(cell.lines).toBe(1);
    }
    expect(metrics.filter(c => c.text === "グラム (g)")).toHaveLength(2);
    const headings = await region.getByRole("columnheader").evaluateAll(cells => cells.map(cell => {
      const range = document.createRange(); range.selectNodeContents(cell);
      return { text: cell.textContent, lines: new Set([...range.getClientRects()].map(r => Math.round(r.y))).size };
    }));
    for (const heading of headings.filter(c => c.text)) expect(heading.lines).toBe(1);
    expect(metrics[0].lines).toBeGreaterThan(1);
    await info.attach(`actual-quantity-table-${width}`, { body: await region.screenshot(), contentType: "image/png" });
    await info.attach(`actual-quantity-measurements-${width}`, { body: JSON.stringify({ metrics, headings }, null, 2), contentType: "application/json" });
    if (width === 360) {
      await page.bringToFront(); await region.focus(); await expect(region).toBeFocused();
      for (let i = 0; i < 12; i++) await page.keyboard.press("ArrowRight", { delay: 80 });
      await expect.poll(() => region.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
      for (let i = 0; i < 60; i++) await page.keyboard.press("ArrowRight", { delay: 60 });
      await expect.poll(() => region.evaluate(el => el.scrollWidth - el.clientWidth - el.scrollLeft)).toBeLessThanOrEqual(1);
      await expect(row.getByRole("button", { name: "編集", exact: true })).toBeInViewport();
      await info.attach("actual-quantity-mobile-keyboard-end", { body: await region.screenshot(), contentType: "image/png" });
      await region.evaluate(el => { el.scrollLeft = 0; });
    }
  }
  await row.getByRole("button", { name: "編集", exact: true }).click();
  await expect(editForm(page).getByRole("spinbutton", { name: "袋上限数量", exact: true })).toHaveValue("1500");
  await expect(editForm(page).getByRole("spinbutton", { name: "1人前数量", exact: true })).toHaveValue("0");
  await option(page, editForm(page), "袋単位", "個"); await save(editForm(page)); await saved(editForm(page));
  expect(s.items.find(r => r.id === "LARGE")).toMatchObject({ bag_max_qty: 1500, qty_per_serving: 0, bag_max_unit: "count", revision: 2 });
  await finish(page, info, s);
});

for (const current of ["unregistered", "clean"]) test(`unknown history with ${current} guard preserves ordinary navigation`, async ({ page }, info) => {
  const s = await fixture(page, info); await open(page);
  if (current === "clean") {
    await page.getByRole("link", { name: "メニュールール", exact: true }).click();
    await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible();
  }
  await expect.poll(async () => (await position(page)).position?.token).toBeTruthy();
  await page.evaluate(() => (window as any).removeOwnedMenuPosition());
  const unknownURL = page.url();
  await page.getByRole("link", { name: current === "clean" ? "メニューマスター" : "メニュールール", exact: true }).click();
  await expect(page.getByRole("heading", { name: current === "clean" ? "メニューマスター" : "メニュールール管理", exact: true })).toBeVisible();
  const currentURL = page.url();
  await historyGo(page, -1);
  await expect(page).toHaveURL(unknownURL);
  await expect(page.getByRole("heading", { name: current === "clean" ? "メニュールール管理" : "メニューマスター", exact: true })).toBeVisible();
  await expect(page.getByText("履歴の位置を確認できません。編集内容を保持して移動を停止しました。")).toHaveCount(0);
  await historyGo(page, 1); await expect(page).toHaveURL(currentURL);
  await expect(page.getByRole("heading", { name: current === "clean" ? "メニューマスター" : "メニュールール管理", exact: true })).toBeVisible();
  await page.getByRole("link", { name: current === "clean" ? "メニュールール" : "メニューマスター", exact: true }).click();
  await expect(page).toHaveURL(unknownURL);
  await finish(page, info, s);
});

test("unknown dirty history stops without guessed delta and recovers after becoming clean", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page);
  await page.getByRole("link", { name: "メニュールール", exact: true }).click();
  await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible();
  await page.evaluate(() => (window as any).removeOwnedMenuPosition()); const unknownURL = page.url();
  await page.getByRole("link", { name: "メニューマスター", exact: true }).click(); await select(page);
  await field(editForm(page), "分類").fill("未知履歴で保持");
  const historyLength = await page.evaluate(() => history.length);
  await historyGo(page, -1);
  await expect(page.getByRole("alert").filter({ hasText: "履歴の位置を確認できません" })).toBeVisible();
  await expect(field(editForm(page), "分類")).toHaveValue("未知履歴で保持");
  await expect(page).toHaveURL(unknownURL);
  expect(await page.evaluate(() => history.length)).toBe(historyLength);
  expect((await position(page)).position).toBeUndefined();
  await field(editForm(page), "分類").fill("主菜");
  await expect(page.getByText("履歴の位置を確認できません。編集内容を保持して移動を停止しました。")).toHaveCount(0);
  await page.getByRole("link", { name: "メニュールール", exact: true }).click();
  await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible();
  await expect(page.getByText("履歴の位置を確認できません。編集内容を保持して移動を停止しました。")).toHaveCount(0);
  await page.getByRole("link", { name: "メニューマスター", exact: true }).click();
  await expect(page.getByRole("heading", { name: "メニューマスター", exact: true })).toBeVisible();
  const recovered = await position(page);
  expect(recovered.position.index).toBe(0);
  // The unknown entry is untouched; create two actual pushes in the new owned segment.
  await page.getByRole("link", { name: "メニュールール", exact: true }).click();
  await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible();
  const neighbor = await position(page);
  expect(neighbor.position).toEqual({ token: recovered.position.token, index: 1 });
  await page.getByRole("link", { name: "メニューマスター", exact: true }).click();
  await expect(page.getByRole("heading", { name: "メニューマスター", exact: true })).toBeVisible();
  await select(page); await field(editForm(page), "分類").fill("新しい所有履歴で保護");
  const owned = await position(page);
  expect(owned.position).toEqual({ token: recovered.position.token, index: 2 });
  await info.attach("recovered-owned-history", { body: JSON.stringify({ recovered, neighbor, owned }), contentType: "application/json" });
  const cancel = page.waitForEvent("dialog"); await historyGo(page, -1); await (await cancel).dismiss();
  await expect.poll(async () => (await position(page)).position).toEqual(owned.position);
  await expect(page).toHaveURL(owned.url); await expect(field(editForm(page), "分類")).toHaveValue("新しい所有履歴で保護");
  const allow = page.waitForEvent("dialog"); await historyGo(page, -1); await (await allow).accept();
  await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible();
  await finish(page, info, s);
});

test("pending edit completion does not steal another record or its draft", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page);
  await field(editForm(page), "分類").fill("A保存中"); s.putGate = gate(); await save(editForm(page));
  await expect(editForm(page)).toHaveAttribute("aria-busy", "true");
  page.once("dialog", d => d.accept()); await select(page, "日本語の白飯");
  await field(editForm(page), "分類").fill("B未保存"); s.putGate.release();
  await expect.poll(() => s.items[0].category).toBe("A保存中");
  await expect(field(editForm(page), "メニュー名")).toHaveValue("日本語の白飯"); await expect(field(editForm(page), "分類")).toHaveValue("B未保存");
  await finish(page, info, s);
});

for (const switchRecord of [false, true]) test(`pending create consults current editor ownership/dirty state (switch=${switchRecord})`, async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); const form = createForm(page);
  await field(form, "メニュー名").fill("追加の完了"); s.postGate = gate(); await save(form); await expect(form).toHaveAttribute("aria-busy", "true");
  if (switchRecord) await select(page, "日本語の白飯");
  await field(editForm(page), "分類").fill("作業中の編集");
  let dialogs = 0; const dismiss = async (d: any) => { dialogs++; await d.dismiss(); }; page.on("dialog", dismiss);
  s.postGate.release(); await saved(form);
  await expect(field(editForm(page), "分類")).toHaveValue("作業中の編集"); expect(dialogs).toBe(switchRecord ? 0 : 1);
  page.off("dialog", dismiss); await finish(page, info, s);
});

test("same ID selection/refetch keeps draft and original revision until explicit reload", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); await field(editForm(page), "分類").fill("保持する編集");
  s.items[0].revision = 3; s.items[0].category = "外部更新";
  await search(page, "白身魚"); await select(page); await expect(field(editForm(page), "分類")).toHaveValue("保持する編集");
  await save(editForm(page)); await expect(editForm(page).getByRole("alert")).toContainText("他の編集");
  expect(s.requests.find(r => r.method === "PUT")?.body.revision).toBe(1); await finish(page, info, s);
});

test("URL search/page/size/back/forward keeps dirty drafts, stable token and matching rows", async ({ page }, info) => {
  const s = await fixture(page, info, { count: 65 }); await open(page); await select(page);
  const setupWrites = await page.evaluate(() => (window as any).menuNavigationWrites);
  if (process.env.MENU_STRICT_PROOF === "1") expect(setupWrites.length).toBeGreaterThanOrEqual(2);
  expect(new Set(setupWrites.map((p: any) => p.token)).size).toBe(1);
  await info.attach("initial-effect-history-writes", { body: JSON.stringify(setupWrites), contentType: "application/json" });
  await field(editForm(page), "分類").fill("URL往復で保持"); const first = await position(page);
  const dialogs: string[] = []; page.on("dialog", async d => { dialogs.push(d.type()); await d.dismiss(); });
  await search(page, "日本語"); await expect(page).toHaveURL(/q=/); const searched = await position(page);
  expect(searched.position.token).toBe(first.position.token); expect(searched.position.index).toBe(first.position.index + 1);
  await page.getByRole("combobox", { name: /表示件数/ }).click(); await page.getByRole("option", { name: "25", exact: true }).click();
  await expect(page).toHaveURL(/pageSize=25/); await page.getByRole("button", { name: "次のページ", exact: true }).click(); await expect(page).toHaveURL(/page=1/);
  await expect(page.getByRole("cell", { name: "日本語メニュー27", exact: true })).toBeVisible(); const paged = await position(page);
  await historyGo(page, -1); await expect(page).not.toHaveURL(/page=1/); await expect(page.getByRole("cell", { name: "日本語の白飯", exact: true })).toBeVisible();
  await historyGo(page, 1); await expect(page).toHaveURL(/page=1/); await expect(page.getByRole("cell", { name: "日本語メニュー27", exact: true })).toBeVisible();
  expect((await position(page)).position).toEqual(paged.position);
  await historyGo(page, -3); await expect(page).toHaveURL(first.url); await expect(page.getByRole("cell", { name: "白身魚のフライ", exact: true })).toBeVisible();
  await expect(field(editForm(page), "分類")).toHaveValue("URL往復で保持"); expect(dialogs).toEqual([]);
  expect((await position(page)).position).toEqual(first.position); expect((await position(page)).nextStatePresent).toBe(true);
  await expect(page.getByText("履歴の位置を確認できません。編集内容を保持して移動を停止しました。")).toHaveCount(0);
  await finish(page, info, s);
});

test("dirty link and history cancellation restores URL/render/history, then accept/revisit", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page);
  await page.getByRole("link", { name: "メニュールール", exact: true }).click(); await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible();
  const neighbor = await position(page); await page.getByRole("link", { name: "メニューマスター", exact: true }).click(); await select(page);
  await field(editForm(page), "分類").fill("取消保護"); const current = await position(page);
  page.once("dialog", d => d.dismiss()); await page.getByRole("link", { name: "メニュールール", exact: true }).click();
  await expect(page).toHaveURL(current.url); await expect(field(editForm(page), "分類")).toHaveValue("取消保護");
  for (let n = 0; n < 2; n++) {
    const dialog = page.waitForEvent("dialog"); await historyGo(page, -1); await (await dialog).dismiss();
    await expect.poll(async () => (await position(page)).position).toEqual(current.position);
    await expect(page).toHaveURL(current.url); await expect(field(editForm(page), "分類")).toHaveValue("取消保護");
  }
  const dialog = page.waitForEvent("dialog"); await historyGo(page, -1); await (await dialog).accept();
  await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible(); expect((await position(page)).position).toEqual(neighbor.position);
  await historyGo(page, 1); await expect(page.getByRole("heading", { name: "メニューマスター", exact: true })).toBeVisible();
  await expect(editForm(page)).toHaveCount(0); await select(page); await expect(field(editForm(page), "分類")).toHaveValue("主菜");
  await field(editForm(page), "分類").fill("複数entry取消"); await search(page, "白身魚"); const query = await position(page);
  const multi = page.waitForEvent("dialog"); await historyGo(page, -2); await (await multi).dismiss();
  await expect.poll(async () => (await position(page)).position).toEqual(query.position); await expect(page).toHaveURL(query.url);
  await expect(field(editForm(page), "分類")).toHaveValue("複数entry取消"); await finish(page, info, s);
});

test("record discard cancel/allow and create draft protect page exit", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); await field(editForm(page), "分類").fill("取消");
  page.once("dialog", d => d.dismiss()); await select(page, "日本語の白飯"); await expect(field(editForm(page), "分類")).toHaveValue("取消");
  page.once("dialog", d => d.accept()); await select(page, "日本語の白飯"); await expect(field(editForm(page), "分類")).toHaveValue("主菜");
  await field(createForm(page), "メニュー名").fill("新規未保存");
  page.once("dialog", d => d.dismiss()); await page.getByRole("link", { name: "メニュールール", exact: true }).click(); await expect(field(createForm(page), "メニュー名")).toHaveValue("新規未保存");
  page.once("dialog", d => d.accept()); await page.getByRole("link", { name: "メニュールール", exact: true }).click(); await expect(page.getByRole("heading", { name: "メニュールール管理", exact: true })).toBeVisible(); await finish(page, info, s);
});

test("hard navigation retains the native beforeunload cancel/allow protection", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); await field(editForm(page), "分類").fill("hard navigation未保存");
  const current = page.url(); let dialogs = 0;
  const dismiss = async (d: any) => { expect(d.type()).toBe("beforeunload"); dialogs++; await d.dismiss(); };
  page.once("dialog", dismiss); await page.getByRole("link", { name: "統合トップに戻る", exact: true }).click({ noWaitAfter: true });
  await expect.poll(() => dialogs).toBe(1); await expect(page).toHaveURL(current); await expect(field(editForm(page), "分類")).toHaveValue("hard navigation未保存");
  page.once("dialog", async d => { expect(d.type()).toBe("beforeunload"); await d.accept(); });
  await page.getByRole("link", { name: "統合トップに戻る", exact: true }).click();
  await expect(page.getByRole("heading", { name: "システム選択", exact: true })).toBeVisible(); await finish(page, info, s);
});

test("document reload adopts existing owned token and retains earlier query history", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); const initial = await position(page);
  await search(page, "日本語"); const searched = await position(page); await page.reload();
  await expect(page.getByRole("cell", { name: "日本語の白飯", exact: true })).toBeVisible();
  expect((await position(page)).position).toEqual(searched.position);
  await historyGo(page, -1); await expect(page).toHaveURL(initial.url); await expect(page.getByRole("cell", { name: "白身魚のフライ", exact: true })).toBeVisible();
  expect((await position(page)).position).toEqual(initial.position);
  await historyGo(page, 1); await expect(page).toHaveURL(searched.url); await expect(page.getByRole("textbox", { name: "メニュー名で検索", exact: true })).toHaveValue("日本語");
  expect((await position(page)).position).toEqual(searched.position); await finish(page, info, s);
});

test("invalid query stops API list without guessing a page", async ({ page }, info) => {
  const s = await fixture(page, info);
  const documents: { query: string; finished: number }[] = [];
  for (const query of ["page=-1", "page=1.5", "pageSize=0", "pageSize=7"]) {
    s.requests.length = 0; await page.goto(`/hospital/menu-masters?${query}`);
    await expect(page.getByRole("alert").filter({ hasText: "一覧の" })).toBeVisible();
    const finished = await s.settleDocument();
    expect(s.requests.filter(r => r.path === "/menu-masters")).toHaveLength(0);
    documents.push({ query, finished });
  }
  await info.attach("invalid-query-document-traffic", { body: JSON.stringify({ documents }), contentType: "application/json" });
  await finish(page, info, s);
});

test("valid integer page beyond the actual total stops without fabricated rows", async ({ page }, info) => {
  const s = await fixture(page, info); await page.goto("/hospital/menu-masters?page=99");
  await expect(page.getByRole("alert").filter({ hasText: "一覧のページが範囲外" })).toBeVisible();
  await expect(page.getByRole("table", { name: "メニューマスター一覧" })).toHaveCount(0);
  await page.getByRole("button", { name: "全件", exact: true }).click(); await expect(page.getByRole("cell", { name: "白身魚のフライ", exact: true })).toBeVisible();
  await finish(page, info, s);
});

test("Japanese composition Enter does not search or submit, normal Enter does", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page);
  const input = page.getByRole("textbox", { name: "メニュー名で検索", exact: true }); await input.fill("日本語");
  const before = s.requests.length; await input.dispatchEvent("compositionstart", { data: "日本語" });
  await input.press("Enter"); await input.dispatchEvent("compositionend", { data: "日本語" });
  expect(s.requests.length).toBe(before); await expect(page).not.toHaveURL(/q=/);
  await input.press("Enter"); await expect(page).toHaveURL(/q=/);
  const name = field(createForm(page), "メニュー名"); await name.fill("日本語入力");
  await name.dispatchEvent("compositionstart", { data: "日本語入力" }); await name.press("Enter"); await name.dispatchEvent("compositionend", { data: "日本語入力" });
  expect(s.requests.filter(r => r.method === "POST")).toHaveLength(0);
  await name.press("Enter"); await saved(createForm(page)); expect(s.requests.filter(r => r.method === "POST")).toHaveLength(1);
  await finish(page, info, s);
});

test("403 preserves draft and does not destroy authentication", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); await field(editForm(page), "分類").fill("権限不足"); s.putStatus = 403;
  await save(editForm(page)); await expect(editForm(page).getByRole("alert")).toContainText("権限"); await expect(field(editForm(page), "分類")).toHaveValue("権限不足");
  expect(await page.evaluate(() => sessionStorage.getItem("auth_header"))).toBe("Bearer menu-test-A"); await finish(page, info, s);
});

test("401 drops dirty draft/auth without leave confirmation", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); await field(editForm(page), "分類").fill("401未保存");
  const dialogs: string[] = []; page.on("dialog", async d => { dialogs.push(d.type()); await d.accept(); }); s.putStatus = 401;
  await save(editForm(page)); await expect(page).toHaveURL(/\/login/); expect(dialogs).toEqual([]);
  expect(await page.evaluate(() => sessionStorage.getItem("auth_header"))).toBeNull(); await expect(editForm(page)).toHaveCount(0); await finish(page, info, s);
});

test("account A to B and logout/relogin dispose cache and drafts", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); await field(editForm(page), "分類").fill("A専用draft"); await field(createForm(page), "メニュー名").fill("A新規draft");
  s.items = [record("B001", "B専用メニュー")];
  await page.evaluate(() => { sessionStorage.setItem("auth_header", "Bearer menu-test-B"); sessionStorage.setItem("sawa_auth_cache_generation", "menu-test-B"); window.dispatchEvent(new Event("sawa:auth-generation")); });
  await expect(page.getByRole("cell", { name: "B専用メニュー", exact: true })).toBeVisible(); await expect(page.getByRole("cell", { name: "白身魚のフライ", exact: true })).toHaveCount(0);
  await expect(editForm(page)).toHaveCount(0); await expect(field(createForm(page), "メニュー名")).toHaveValue("");
  await select(page, "B専用メニュー"); await field(editForm(page), "分類").fill("B未保存");
  const dialogs: string[] = []; page.on("dialog", async d => { dialogs.push(d.type()); await d.accept(); }); await page.getByRole("button", { name: "ログアウト", exact: true }).click(); await expect(page).toHaveURL(/\/login/);
  expect(dialogs).toEqual([]); expect(await page.evaluate(() => sessionStorage.getItem("auth_header"))).toBeNull();
  await page.evaluate(() => { sessionStorage.setItem("auth_header", "Bearer menu-test-B"); sessionStorage.setItem("sawa_auth_generation", localStorage.getItem("sawa_logout_generation") || ""); sessionStorage.setItem("sawa_auth_cache_generation", "menu-test-B-relogin"); });
  await page.goto("/hospital/menu-masters"); await select(page, "B専用メニュー"); await expect(field(editForm(page), "分類")).toHaveValue("主菜");
  expect(s.requests.filter(r => r.path === "/menu-masters" && r.bearer === "Bearer menu-test-B").length).toBeGreaterThanOrEqual(2); await finish(page, info, s);
});

test("late A-session 401 must not invalidate a newly authenticated B editor", async ({ page }, info) => {
  const s = await fixture(page, info); await open(page); await select(page); s.putGate = gate(); s.putStatus = 401;
  await field(editForm(page), "分類").fill("A保存中"); await save(editForm(page));
  await expect.poll(() => s.requests.filter(r => r.method === "PUT").length).toBe(1);
  s.items = [record("B001", "B専用メニュー")];
  await page.evaluate(() => { sessionStorage.setItem("auth_header", "Bearer menu-test-B"); sessionStorage.setItem("sawa_auth_cache_generation", "menu-test-B"); window.dispatchEvent(new Event("sawa:auth-generation")); });
  await select(page, "B専用メニュー"); await field(editForm(page), "分類").fill("B未保存");
  const response = page.waitForResponse(r => r.request().method() === "PUT"); s.putGate.release(); await response;
  await expect(field(editForm(page), "分類")).toHaveValue("B未保存");
  expect(await page.evaluate(() => sessionStorage.getItem("auth_header"))).toBe("Bearer menu-test-B"); await finish(page, info, s);
});

test("guest client redirect and adjacent public page have no hydration mismatch", async ({ page }, info) => {
  const s = await fixture(page, info, { guest: true }); const documents: { url: string; status: number }[] = [];
  page.on("response", r => { if (r.request().isNavigationRequest()) documents.push({ url: r.url(), status: r.status() }); });
  await page.goto("/hospital/menu-masters"); await expect(page).toHaveURL(/\/login/); expect(documents.some(d => d.url.endsWith("/hospital/menu-masters") && d.status === 200)).toBe(true);
  await s.settleDocument();
  await page.goto("/about"); await expect(page.locator(".unified-current")).toHaveText("共通ログイン");
  await s.settleDocument();
  expect(s.requests.filter(r => r.path === "/menu-masters")).toHaveLength(0);
  await info.attach("guest-document-vs-client-redirect", { body: JSON.stringify(documents), contentType: "application/json" }); await finish(page, info, s);
});
