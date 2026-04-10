import { test, expect } from "@playwright/test";

test("orders list highlights facility-unregistered and week-unresolved orders in the global summary", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const missingOrderId = "ORD-LIST-UNRESOLVED-001";
  const normalOrderId = "ORD-LIST-NORMAL-001";

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

    if (path.endsWith("/orders") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            {
              id: missingOrderId,
              status: "要確認",
              document: "missing-facility.pdf",
              ocr_status: "done",
              facility: null,
              week: null,
              week_value: null,
              week_label: null,
              workflow_state: {
                state: "review_required",
                headline: "施設と週次の確認が必要です",
                primary_action: "詳細で確認",
                blockers_json: ["facility_unresolved", "week_unresolved"],
                warnings_json: [],
              },
            },
            {
              id: normalOrderId,
              status: "確定",
              document: "normal.pdf",
              ocr_status: "done",
              facility: "FAC-001",
              week: "2026-03",
              week_value: "2026-03@2026-03-22~2026-03-28",
              week_label: "03/22 - 03/28",
              workflow_state: {
                state: "confirmed",
                headline: "確定済み",
                primary_action: "完了を確認",
                blockers_json: [],
                warnings_json: [],
              },
            },
          ],
        },
      });
      return;
    }

    if (path.endsWith(`/orders/${missingOrderId}/ocr-output`) && method === "GET") {
      await route.fulfill({ status: 200, json: { facility_candidates: [] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders`);

  await expect(page.getByRole("heading", { name: "注文一覧" })).toBeVisible();
  const unresolvedGroup = page.locator(".week-group").first();
  const resolvedGroup = page.locator(".week-group").nth(1);
  const firstWeekHeading = unresolvedGroup.locator("h3").first();
  await expect(firstWeekHeading).toHaveText("暫定週次未確定");
  await expect(unresolvedGroup).toHaveClass(/week-group-unresolved/);
  await expect(unresolvedGroup.getByText("この週は施設未登録の注文だけです。")).toBeVisible();
  await unresolvedGroup.getByRole("button", { name: "開く" }).click();
  await expect(page.getByText(missingOrderId)).toBeVisible();
  await resolvedGroup.getByRole("button", { name: "開く" }).click();
  await expect(page.getByText(normalOrderId)).toBeVisible();

  const unresolvedSurfaceColors = await page.evaluate(() => {
    const section = document.querySelector(".week-group-unresolved");
    const card = document.querySelector(".week-group-unresolved .order-card");
    if (!(section instanceof HTMLElement) || !(card instanceof HTMLElement)) {
      return null;
    }
    const sectionStyle = getComputedStyle(section);
    const cardStyle = getComputedStyle(card);
    return {
      sectionBackgroundImage: sectionStyle.backgroundImage,
      sectionBackgroundColor: sectionStyle.backgroundColor,
      cardBackgroundColor: cardStyle.backgroundColor,
      cardBorderTop: cardStyle.borderTop,
      cardBoxShadow: cardStyle.boxShadow,
    };
  });
  expect(unresolvedSurfaceColors).not.toBeNull();
  expect(unresolvedSurfaceColors?.cardBackgroundColor).not.toEqual("rgba(0, 0, 0, 0)");
  expect(unresolvedSurfaceColors?.cardBorderTop).not.toContain("0px");
  expect(unresolvedSurfaceColors?.cardBoxShadow).not.toEqual("none");
  expect(unresolvedSurfaceColors?.sectionBackgroundColor).not.toEqual(unresolvedSurfaceColors?.cardBackgroundColor);
});

test("orders list renders order cards for a resolved week and keeps submitted/missing counts in summary", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const confirmedOrderId = "ORD-LIST-FAC-001";
  const inferredOrderId = "ORD-LIST-FAC-002";

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
          facilities: [
            { id: "FAC-001", name: "施設A" },
            { id: "FAC-002", name: "施設B" },
            { id: "FAC-003", name: "施設C" },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            {
              id: confirmedOrderId,
              status: "確定",
              document: "confirmed.pdf",
              ocr_status: "done",
              facility: "FAC-001",
              week: "2026-03",
              week_value: "2026-03@2026-03-22~2026-03-28",
              week_label: "03/22 - 03/28",
              workflow_state: {
                state: "confirmed",
                headline: "確定済み",
                primary_action: "完了を確認",
                blockers_json: [],
                warnings_json: [],
              },
            },
            {
              id: inferredOrderId,
              status: "要確認",
              document: "inferred.pdf",
              ocr_status: "done",
              facility: null,
              week: "2026-03",
              week_value: "2026-03@2026-03-22~2026-03-28",
              week_label: "03/22 - 03/28",
              candidate_resolution: {
                resolutions: {
                  facility: {
                    resolved_value: "FAC-002",
                    resolved_label: "施設B",
                  },
                },
              },
              workflow_state: {
                state: "review_required",
                headline: "要確認",
                primary_action: "詳細で確認",
                blockers_json: [],
                warnings_json: ["facility_candidate_used"],
              },
            },
          ],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders`);

  const resolvedGroup = page.locator(".week-group").first();
  await expect(resolvedGroup.getByRole("heading", { name: "03/22 - 03/28" })).toBeVisible();
  await resolvedGroup.getByRole("button", { name: "開く" }).click();
  await expect(page.getByText("施設A (FAC-001)")).toBeVisible();
  await expect(page.getByText("推定: 施設B (FAC-002)")).toBeVisible();
  await expect(page.getByText(confirmedOrderId)).toBeVisible();
  await expect(page.getByText(inferredOrderId)).toBeVisible();
});

test("orders list can collapse and reopen a week group", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-LIST-TOGGLE-001";

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

    if (path.endsWith("/orders") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            {
              id: orderId,
              status: "要確認",
              document: "toggle.pdf",
              ocr_status: "done",
              facility: "FAC-001",
              week: "2026-03",
              week_value: "2026-03@2026-03-22~2026-03-28",
              week_label: "03/22 - 03/28",
              workflow_state: {
                state: "review_required",
                headline: "要確認",
                primary_action: "詳細で確認",
                blockers_json: [],
                warnings_json: [],
              },
            },
          ],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders`);

  const weekGroup = page.locator(".week-group").first();
  await expect(weekGroup.locator(".week-group-summary")).toHaveCount(0);
  await expect(weekGroup.getByText(orderId)).toHaveCount(0);
  await weekGroup.getByRole("button", { name: "開く" }).click();
  await expect(weekGroup.getByText(orderId)).toBeVisible();
  await expect(weekGroup.locator(".week-group-summary")).toHaveCount(0);
  await weekGroup.getByRole("button", { name: "閉じる" }).click();
  await expect(weekGroup.locator(".week-group-summary")).toHaveCount(0);
  await expect(weekGroup.getByText(orderId)).toHaveCount(0);
  await weekGroup.getByRole("button", { name: "開く" }).click();
  await expect(weekGroup.getByText(orderId)).toBeVisible();
});

test("orders list merges relocation facility pairs into one display slot", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const ikoiOrderId = "ORD-LIST-IKOI-PLUS";
  const shimantoOrderId = "ORD-LIST-SHIMANTO-PIA";

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
          facilities: [
            { id: "FAC00011", name: "ケアハウス四万十" },
            { id: "FAC00013", name: "いこいの森" },
            { id: "FAC00015", name: "ケアハウス四万十ピア" },
            { id: "FAC00016", name: "いこいの森プラス" },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          orders: [
            {
              id: ikoiOrderId,
              status: "確定",
              document: "ikoi-plus.pdf",
              ocr_status: "done",
              facility: "FAC00016",
              week: "2026-03",
              week_value: "2026-03@2026-03-22~2026-03-28",
              week_label: "03/22 - 03/28",
              workflow_state: {
                state: "confirmed",
                headline: "確定済み",
                primary_action: "完了を確認",
                blockers_json: [],
                warnings_json: [],
              },
            },
            {
              id: shimantoOrderId,
              status: "確定",
              document: "shimanto-pia.pdf",
              ocr_status: "done",
              facility: "FAC00015",
              week: "2026-03",
              week_value: "2026-03@2026-03-22~2026-03-28",
              week_label: "03/22 - 03/28",
              workflow_state: {
                state: "confirmed",
                headline: "確定済み",
                primary_action: "完了を確認",
                blockers_json: [],
                warnings_json: [],
              },
            },
          ],
        },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders`);

  const resolvedGroup = page.locator(".week-group").first();
  await resolvedGroup.getByRole("button", { name: "開く" }).click();
  await expect(page.getByText("いこいの森プラス (FAC00016)")).toBeVisible();
  await expect(page.getByText("ケアハウス四万十ピア (FAC00015)")).toBeVisible();
  await expect(page.getByText(ikoiOrderId)).toBeVisible();
  await expect(page.getByText(shimantoOrderId)).toBeVisible();
});

test("orders list can archive a completed week and restore it from archived view", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const archivedOrderId = "ORD-LIST-ARCHIVE-001";
  let showArchived = false;
  let isArchived = false;
  const requests: Array<{ method: string; url: string; body?: any }> = [];

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  page.on("dialog", async (dialog) => {
    await dialog.accept();
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

    if (path.endsWith("/orders") && method === "GET") {
      showArchived = url.searchParams.get("include_archived") === "true";
      const orders =
        isArchived && !showArchived
          ? []
          : [
              {
                id: archivedOrderId,
                status: "確定",
                document: "archivable.pdf",
                ocr_status: "done",
                facility: "FAC-001",
                week: isArchived ? "2026-04@2026-04-05~2026-04-11" : "2026-04",
                week_value: "2026-04@2026-04-05~2026-04-11",
                week_label: "04/05 - 04/11",
                is_archived: isArchived,
                archived_at: isArchived ? "2026-04-06T12:00:00" : null,
                archived_by: isArchived ? "operator" : null,
                workflow_state: {
                  state: "confirmed",
                  headline: "確定済み",
                  primary_action: "完了を確認",
                  blockers_json: [],
                  warnings_json: [],
                },
              },
            ];
      await route.fulfill({ status: 200, json: { orders } });
      return;
    }

    if (path.endsWith("/orders/archive-week") && method === "POST") {
      const body = route.request().postDataJSON();
      requests.push({ method, url: route.request().url(), body });
      isArchived = true;
      await route.fulfill({ status: 200, json: { archived_count: 1, archived_order_ids: [archivedOrderId] } });
      return;
    }

    if (path.endsWith("/orders/unarchive-week") && method === "POST") {
      const body = route.request().postDataJSON();
      requests.push({ method, url: route.request().url(), body });
      isArchived = false;
      await route.fulfill({ status: 200, json: { restored_count: 1, restored_order_ids: [archivedOrderId] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders`);

  expect(showArchived).toBe(false);
  const weekGroup = page.locator(".week-group").filter({ has: page.getByRole("heading", { name: "04/05 - 04/11" }) });
  await expect(weekGroup.getByRole("button", { name: "アーカイブ" })).toBeVisible();
  await weekGroup.getByRole("button", { name: "アーカイブ" }).click();
  await expect(page.getByText("「04/05 - 04/11」をアーカイブしました。")).toBeVisible();
  await expect(page.getByText("注文データがありません。")).toBeVisible();
  expect(requests[0]?.body).toEqual({
    week_value: "2026-04@2026-04-05~2026-04-11",
    order_ids: [archivedOrderId],
  });

  await page.getByLabel("アーカイブ済みを表示").check();
  await expect(weekGroup.getByRole("button", { name: "戻す" })).toBeVisible();
  await weekGroup.getByRole("button", { name: "戻す" }).click();
  await expect(page.getByText("「04/05 - 04/11」を通常表示に戻しました。")).toBeVisible();
  expect(requests[1]?.body).toEqual({
    week_value: "2026-04@2026-04-05~2026-04-11",
    order_ids: [archivedOrderId],
  });
  await page.getByLabel("アーカイブ済みを表示").uncheck();
  await expect(weekGroup.getByRole("button", { name: "アーカイブ" })).toBeVisible();
});

test("orders list can bulk archive visible week groups regardless of order status", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const archiveState = new Map<string, boolean>([
    ["2026-04@2026-04-05~2026-04-11", false],
    ["2026-04@2026-04-01~2026-04-04", false],
  ]);
  const requests: Array<{ url: string; body?: any }> = [];

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  page.on("dialog", async (dialog) => {
    await dialog.accept();
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
          facilities: [
            { id: "FAC-001", name: "施設A" },
            { id: "FAC-002", name: "施設B" },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/orders") && method === "GET") {
      const showArchived = url.searchParams.get("include_archived") === "true";
      const orders = [
        {
          id: "ORD-BULK-001",
          status: "要確認",
          document: "bulk-1.pdf",
          ocr_status: "done",
          facility: "FAC-001",
          week: "2026-04",
          week_value: "2026-04@2026-04-05~2026-04-11",
          week_label: "04/05 - 04/11",
          is_archived: archiveState.get("2026-04@2026-04-05~2026-04-11") || false,
          archived_at: archiveState.get("2026-04@2026-04-05~2026-04-11") ? "2026-04-07T00:00:00" : null,
          archived_by: archiveState.get("2026-04@2026-04-05~2026-04-11") ? "operator" : null,
          workflow_state: {
            state: "review_required",
            headline: "要確認",
            primary_action: "詳細で確認",
            blockers_json: [],
            warnings_json: [],
          },
        },
        {
          id: "ORD-BULK-002",
          status: "未着",
          document: "bulk-2.pdf",
          ocr_status: "processing",
          facility: "FAC-002",
          week: "2026-04",
          week_value: "2026-04@2026-04-01~2026-04-04",
          week_label: "04/01 - 04/04",
          is_archived: archiveState.get("2026-04@2026-04-01~2026-04-04") || false,
          archived_at: archiveState.get("2026-04@2026-04-01~2026-04-04") ? "2026-04-07T00:00:00" : null,
          archived_by: archiveState.get("2026-04@2026-04-01~2026-04-04") ? "operator" : null,
          workflow_state: {
            state: "uploaded",
            headline: "OCR待ち",
            primary_action: "run_ocr_pipeline",
            blockers_json: [],
            warnings_json: [],
          },
        },
      ].filter((order) => showArchived || !order.is_archived);
      await route.fulfill({ status: 200, json: { orders } });
      return;
    }

    if (path.endsWith("/orders/archive-week") && method === "POST") {
      const body = route.request().postDataJSON();
      requests.push({ url: path, body });
      archiveState.set(String(body.week_value), true);
      await route.fulfill({ status: 200, json: { archived_count: (body.order_ids || []).length, archived_order_ids: body.order_ids || [] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders`);

  await expect(page.getByRole("button", { name: "表示中の週次を一括アーカイブ (2)" })).toBeVisible();
  await page.getByRole("button", { name: "表示中の週次を一括アーカイブ (2)" }).click();
  await expect(page.getByText("2 件の週次をアーカイブしました。")).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests.map((item) => item.body.week_value)).toEqual([
    "2026-04@2026-04-05~2026-04-11",
    "2026-04@2026-04-01~2026-04-04",
  ]);
  await expect(page.getByText("注文データがありません。")).toBeVisible();
});
