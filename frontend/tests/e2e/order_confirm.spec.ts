import { test, expect } from "@playwright/test";

test("order detail editing flow uses grouped lines and PDF viewer", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-001";
  const explicitWeekId = "2026-01@2026-01-03~2026-01-09";
  const explicitWeekLabel = "01/03 - 01/09";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [
      {
        line_id: "L1",
        date: "2026-01-03",
        daypart: "朝",
        menu_name: "Menu A",
        diet_type: "普通",
        area_id: "A",
        bag_type: "通常",
        quantity_original: 5,
        quantity_corrected: null,
      },
      {
        line_id: "L2",
        date: "2026-01-03",
        daypart: "朝",
        menu_name: "Menu B",
        diet_type: "普通",
        area_id: "A",
        bag_type: "通常",
        quantity_original: 3,
        quantity_corrected: 2,
      },
    ],
  };
  const pdfBody = "%PDF-1.4";

  let linesPayload: any = null;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/lines`) && method === "PUT") {
      linesPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { updated: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith(`/facilities/${orderPayload.facility}`)) {
      await route.fulfill({
        status: 200,
        json: {
          id: orderPayload.facility,
          name: "Facility E2E Test",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: orderPayload.facility, name: "Facility E2E Test" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await expect(page.locator(".status-pill")).toHaveText("要確認");
  await expect(page.locator("iframe[title=\"order-pdf\"]")).toBeVisible();

  await page.locator(".step-tab").filter({ hasText: "明細" }).click();
  await expect(page.locator(".step-title")).toHaveText("明細の確認と修正");
  await page.locator(".date-group-toggle").nth(1).click();

  const qtyInput = page.locator('input[type="number"]').first();
  await qtyInput.fill("10");

  await page.getByRole("button", { name: "明細を保存して作業続行" }).click();
  await expect
    .poll(() => linesPayload, { timeout: 2000 })
    .not.toBeNull();
  expect(linesPayload.lines[0].quantity_corrected).toBe(10);
  await expect(page.getByText("保存しました。")).toBeVisible();
});

test("order detail does not show OCR artifact in the fax pdf slot when original is missing", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-OCR-PDF-001";
  const explicitWeekId = "2026-01@2026-01-03~2026-01-09";
  const explicitWeekLabel = "01/03 - 01/09";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "gs://missing/original.pdf",
    facility: "FAC-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
  };
  const pdfBody = "%PDF-1.4";

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({
        status: 200,
        body: pdfBody,
        contentType: "application/pdf",
        headers: {
          "x-sawa-document-source": "ocr_artifact",
          "x-sawa-document-variant": "raw_pdf",
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: orderPayload.facility, name: "Facility E2E Test" }] },
      });
      return;
    }
    if (path.endsWith(`/facilities/${orderPayload.facility}`)) {
      await route.fulfill({
        status: 200,
        json: {
          id: orderPayload.facility,
          name: "Facility E2E Test",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (
      path.endsWith(`/orders/${orderId}/ocr-output`) ||
      path.endsWith(`/orders/${orderId}/ocr-history`) ||
      path.endsWith(`/orders/${orderId}/history`) ||
      path.endsWith(`/orders/${orderId}/shipping-statuses`)
    ) {
      await route.fulfill({ status: 200, json: path.endsWith("/shipping-statuses") ? { items: [], summary: null } : {} });
      return;
    }
    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  await expect(page.getByRole("heading", { name: "注文書 (FAX PDF)" })).toBeVisible();
  await expect(page.locator(".pdf-placeholder")).toHaveText("原本FAX PDFを現在取得できません。");
  await expect(page.getByRole("heading", { name: "OCR生成PDF" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "OCR生成PDFを開く" })).toHaveCount(0);
});

test("overlay unavailable shows recovery-only flow and blocks edit actions", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-OVERLAY-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "",
    facility: "FAC-OVR",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    ocr_status: "failed",
    ocr_error: "overlay unavailable",
    ocr_auto_apply_blocked: true,
    ocr_can_apply_draft: false,
    ocr_can_confirm: false,
    ocr_processing_stage: "ocr",
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`)) {
      if (method === "GET") {
        await route.fulfill({ status: 200, json: orderPayload });
        return;
      }
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.abort("failed");
      return;
    }
    if (path.endsWith(`/facilities/${orderPayload.facility}`)) {
      await route.fulfill({
        status: 200,
        json: { id: orderPayload.facility, name: "Facility Overlay Test", config: {} },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({ status: 200, json: { pages: [], message: "OCRページが取得できません。" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "edited_sheet",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "failed" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await page.locator(".step-tab").nth(1).click();
  await page.getByRole("heading", { name: "OCR修正" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();

  await expect(page.getByRole("button", { name: "OCR基盤を復旧" })).toBeVisible();
  await expect(page.getByRole("button", { name: "修正完了 / 保存して明細に反映" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "明細に反映して次へ" })).toHaveCount(0);
  const saveDraftButtons = page.getByRole("button", { name: "シートを保存（暫定）" });
  await expect(saveDraftButtons).toHaveCount(2);
  await expect(saveDraftButtons.first()).toBeDisabled();
  await expect(saveDraftButtons.nth(1)).toBeDisabled();
  await expect(page.getByRole("button", { name: "行を追加" })).toHaveCount(0);
});

test("Step2 new evidence path allows progress to 明細 after keeping current sheet", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-NEW-EVIDENCE-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-NE-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    workflow_state: {
      state: "new_evidence_available",
      candidate_evidence_run_id: "EVD-001",
      primary_action: "new_evidence_available",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
    },
  };
  let currentOrderPayload = { ...orderPayload };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-NE-001",
    week_id: explicitWeekId,
    fields: ["日付", "時間帯", "メニュー", "数量"],
    header: ["日付", "時間帯", "メニュー", "数量"],
    rows: [["2026-03-01", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "edited_sheet",
    can_apply: true,
    can_confirm: true,
  };

  const pdfBody = "%PDF-1.4";
  let savedDraftPayload: any = null;
  let applyPayload: any = null;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      if (method === "POST") {
        savedDraftPayload = route.request().postDataJSON();
        await route.fulfill({ status: 200, json: { ok: true } });
        return;
      }
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-apply`)) {
      applyPayload = route.request().postDataJSON();
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "review_required",
          candidate_evidence_run_id: null,
          apply_gate: {
            can_apply: true,
            can_confirm: true,
          },
        },
        apply_gate: {
          can_apply: true,
          can_confirm: true,
        },
      };
      await route.fulfill({ status: 200, json: { ok: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          ...draftSheetPayload,
          fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
          header: ["日付", "区分", "メニュー", "常食"],
          source: "draft_sheet",
          draft_newer_than_lines: true,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-NE-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-NE-001", name: "Facility Test", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-NE-001", name: "Facility Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "はい / 修正済み" }).click();
  const finishButton = page.getByRole("button", { name: "修正完了 / 保存して明細に反映" });
  await expect(finishButton).toBeVisible();
  await finishButton.click();
  await expect.poll(() => savedDraftPayload, { timeout: 2000 }).not.toBeNull();
  await expect.poll(() => applyPayload, { timeout: 2000 }).not.toBeNull();
  await expect(page.locator(".step-indicator")).toHaveText("Step 3 / 5");
  await expect(page.locator(".step-title")).toHaveText("明細の確認と修正");
  await expect(page.getByText("Step2で新しいOCR候補へ切り替えるか、現在のシートを維持してください")).toHaveCount(0);
});

test("stale failed OCR status does not override the current sheet and LLM defaults stay on Gemini Pro", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-STALE-FAILED-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-ST-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    ocr_status: "failed",
    ocr_error: "main_ocr_failed:gemini",
    workflow_state: {
      state: "new_evidence_available",
      candidate_evidence_run_id: "EVD-CAND-001",
      primary_action: "switch_to_new_evidence",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-ST-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["03/22", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "draft_sheet",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };

  const pdfBody = "%PDF-1.4";
  const overlayPng =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn6zkQAAAAASUVORK5CYII=";

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({
        status: 200,
        json: {
          pages: [
            {
              page_index: 1,
              markdown_uri: null,
              markdown_text: "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|朝食|10|",
              ocr_overlay_uri: "gs://bucket/active-overlay.png",
              ocr_overlay_url: overlayPng,
              layout_overlay_uri: null,
              layout_overlay_url: null,
              figure_uris: [],
              figure_urls: [],
            },
          ],
          combined: { raw_pdf: "file:///tmp/order.pdf" },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-ST-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-ST-001", name: "Facility Stale OCR Test", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-ST-001", name: "Facility Stale OCR Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();

  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();
  await expect(page.getByText("OCRが失敗しました: main_ocr_failed:gemini")).toHaveCount(0);
  await expect(page.getByText("理由: main_ocr_failed:gemini")).toHaveCount(0);

  await page.getByRole("button", { name: "いいえ / 迷う" }).click();
  await page.getByRole("button", { name: "AIに任せる" }).click();

  await expect(page.locator("select.llm-provider-select").last()).toHaveValue("gemini");
  await expect(page.locator("select.llm-model-select").last()).toHaveValue("pro");
  await expect(page.getByRole("button", { name: "シート再読込" }).last()).toBeVisible();
});

test("LLM extra prompt follows the active facility sheet schema dynamically", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-FACILITY-PROMPT-001";
  const explicitWeekId = "2026-04@2026-04-05~2026-04-11";
  const explicitWeekLabel = "04/05 - 04/11";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-005",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "review_required",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };
  const sheetPayload = {
    order_id: orderId,
    facility_id: "FAC-005",
    week_id: explicitWeekId,
    fields: [
      "date_mmdd",
      "daypart",
      "menu",
      "qty.soft_x",
      "qty.regular_bag_x",
      "qty.no_meat_x",
      "qty.no_fish_x",
      "qty.change_1_x",
      "qty.change_2_x",
      "remarks",
    ],
    header: ["日付", "区分", "メニュー", "軟菜", "袋分け", "肉禁", "魚禁", "変更1", "変更2", "備考欄"],
    rows: [["04/05", "朝", "いんげんのカニ和え", "0", "0", "", "", "", "", ""]],
    row_ids: ["r1"],
    source: "ocr_table+historical_daypart",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };
  let reparsePayload: any = null;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: sheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/reparse`) && method === "POST") {
      reparsePayload = route.request().postDataJSON();
      await route.fulfill({
        status: 202,
        json: { accepted: true, reparse: { status: "queued", llm_assist: true } },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({ status: 200, json: { pages: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith("/facilities/FAC-005")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-005",
          name: "Facility Prompt Test",
          config: null,
          resolved_config: {
            fax_template: {
              columns: [
                { index: 0, role: "date", header: "日付", name: "date_mmdd" },
                { index: 1, role: "daypart", header: "区分", name: "daypart" },
                { index: 2, role: "menu_name", header: "メニュー", name: "menu" },
                { index: 3, role: "quantity", header: "軟菜", name: "qty.soft_x", diet_type: "soft", area_id: "X" },
                { index: 4, role: "quantity", header: "袋分け", name: "qty.regular_bag_x", diet_type: "regular_bag", area_id: "X" },
                { index: 5, role: "quantity", header: "肉禁", name: "qty.no_meat_x", diet_type: "no_meat", area_id: "X" },
                { index: 6, role: "quantity", header: "魚禁", name: "qty.no_fish_x", diet_type: "no_fish", area_id: "X" },
                { index: 7, role: "quantity", header: "変更1", name: "qty.change_1_x", diet_type: "change_1", area_id: "X" },
                { index: 8, role: "quantity", header: "変更2", name: "qty.change_2_x", diet_type: "change_2", area_id: "X" },
                { index: 9, role: "note", header: "備考欄", name: "remarks" },
              ],
            },
          },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-005", name: "Facility Prompt Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();
  await page.getByRole("button", { name: "AIに任せる" }).click();
  await page.locator(".ocr-inline-details summary").click();
  const promptArea = page.locator(".ocr-llm-prompt-textarea").first();
  await expect(promptArea).toContainText("soft_x, regular_bag_x, no_meat_x, no_fish_x, change_1_x, change_2_x");
  await expect(promptArea).not.toContainText("regular_2f");
  await page.getByRole("button", { name: "LLM補完再解析" }).click();
  await expect
    .poll(() => reparsePayload, { timeout: 2000 })
    .not.toBeNull();
  expect(reparsePayload.ocr_prompt).toContain(
    "soft_x, regular_bag_x, no_meat_x, no_fish_x, change_1_x, change_2_x",
  );
  expect(reparsePayload.ocr_prompt).not.toContain("regular_2f");
});

test("merged-cell OCR issue exposes dedicated AI choice with Gemini Pro preset", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-MERGED-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-MG-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "review_required",
      primary_action: "review_required",
      apply_gate: {
        can_apply: false,
        can_confirm: false,
        blockers: ["semantic_shell_only", "template_unresolved"],
        warnings: ["quantity_review_required", "numeric_trust_low"],
      },
    },
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["semantic_shell_only", "template_unresolved"],
      warnings: ["quantity_review_required", "numeric_trust_low"],
    },
  };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-MG-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.diabetes_x"],
    header: ["日付", "区分", "メニュー", "常食", "糖尿"],
    rows: [["03/22", "朝", "Menu A", "", ""]],
    row_ids: ["r1"],
    source: "weekly_menu",
    can_apply: false,
    can_confirm: false,
    warnings: ["sheet_quantity_column_unmapped", "sheet_ocr_review_required"],
    apply_blockers: ["sheet_quantity_column_unmapped"],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({
        status: 200,
        json: {
          pages: [
            {
              page_index: 1,
              markdown_text: "|日付|区分|メニュー|常食|糖尿|\n|03/22|朝|Menu A|||",
              ocr_overlay_url:
                "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn6zkQAAAAASUVORK5CYII=",
              layout_overlay_url: null,
              figure_urls: [],
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({
        status: 200,
        json: {
          status: "done",
          template_id: "fax_layout_regular_diabetes_v1",
          cell_issues: [
            {
              issue_code: "merged_numeric_cell",
              row_index: 10,
              col_index: 3,
              row_span: 2,
              text: "37",
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-MG-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-MG-001",
          name: "Facility Merged Cell Test",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-MG-001", name: "Facility Merged Cell Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();

  await expect(page.getByRole("button", { name: "結合セルをAIで読む" })).toBeVisible();
  await page.getByRole("button", { name: "結合セルをAIで読む" }).click();
  await expect(page.getByText("結合セルの数量を Gemini Pro で推論する")).toBeVisible();
  await expect(page.locator("select.llm-provider-select").last()).toHaveValue("gemini");
  await expect(page.locator("select.llm-model-select").last()).toHaveValue("pro");
  await expect(page.getByText("方針: 結合セルまたがり数量")).toBeVisible();
  await expect(page.getByRole("button", { name: "シート再読込" }).last()).toBeVisible();
});

test("confirm button shows loading state while confirm is in flight", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-CONFIRM-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CF-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    workflow_state: {
      state: "review_required",
      candidate_evidence_run_id: null,
      primary_action: "apply_draft",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-CF-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["2026-03-01", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "draft_sheet",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };

  const pdfBody = "%PDF-1.4";
  let confirmStarted = false;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/confirm`)) {
      confirmStarted = true;
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.fulfill({ status: 200, json: { ok: true } });
      return;
    }
    if (path.endsWith("/facilities/FAC-CF-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-CF-001", name: "Facility Confirm Test", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-CF-001", name: "Facility Confirm Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "出力" }).click();
  const outputPanel = page.locator(".panel").filter({ has: page.getByRole("heading", { name: "出力" }) });
  const confirmButton = outputPanel.getByRole("button", { name: "確定", exact: true });
  await expect(confirmButton).toBeVisible();
  await confirmButton.click();
  await expect.poll(() => confirmStarted, { timeout: 2000 }).toBe(true);
  await expect(outputPanel.getByRole("button", { name: "確定中...", exact: true })).toBeVisible();
  await expect(page.getByText("確定しました。")).toBeVisible();
  await expect(outputPanel.getByRole("button", { name: "確定", exact: true })).toBeVisible();
});

test("last step primary action confirms and returns to orders without separate training request", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-CONFIRM-RETURN-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CF-RTN-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    workflow_state: {
      state: "review_required",
      candidate_evidence_run_id: null,
      primary_action: "apply_draft",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-CF-RTN-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["2026-03-01", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "draft_sheet",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };

  const pdfBody = "%PDF-1.4";
  let confirmStarted = false;
  let trainingStarted = false;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          state: "apply_ready",
          apply_gate: { can_apply: true, can_confirm: true, blockers: [] },
          warnings: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({ status: 200, json: { pages: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/confirm`)) {
      confirmStarted = true;
      await route.fulfill({ status: 200, json: { ok: true } });
      return;
    }
    if (path.endsWith(`/ocr/training-samples/from-order/${orderId}`)) {
      trainingStarted = true;
      await route.fulfill({ status: 200, json: { sample: { id: "sample-1", line_count: 1 } } });
      return;
    }
    if (path.endsWith("/facilities/FAC-CF-RTN-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-CF-RTN-001", name: "Facility Confirm Return", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CF-RTN-001", name: "Facility Confirm Return" }] },
      });
      return;
    }
    if (path.endsWith("/orders") && method === "GET") {
      await route.fulfill({ status: 200, json: { orders: [], total: 0 } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "出力" }).click();

  const finishButton = page.locator(".step-footer").getByRole("button", { name: "確定して注文一覧へ戻る" });
  await expect(finishButton).toBeVisible();
  await finishButton.click();

  await expect.poll(() => confirmStarted, { timeout: 2000 }).toBe(true);
  await expect.poll(() => trainingStarted, { timeout: 500 }).toBe(false);
  await page.waitForURL("**/orders");
});

test("saved sheet revision keeps current date and daypart while reusing saved quantities", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-SAVED-REVISION-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-SR-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
  };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-SR-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f", "remarks"],
    header: ["日付", "区分", "メニュー", "常食2F", "常食3F", "備考"],
    rows: [
      ["03/22", "朝", "厚揚げとさつま芋の煮物", "", "", ""],
      ["03/22", "昼", "鶏じゃが", "", "", ""],
    ],
    row_ids: ["draft-1", "draft-2"],
    source: "weekly_menu",
    can_apply: false,
    can_confirm: false,
    warnings: [],
    apply_blockers: [],
  };

  const historyPayload = {
    latest: {
      revision_id: "OCRREV-E2E-001",
      edited_at: "2026-03-27T18:49:32.948640",
      ui_mode: "sheet",
      fields: ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f", "remarks"],
      header: ["日付", "区分", "メニュー", "常食2F", "常食3F", "備考"],
      row_ids: ["draft-1", "draft-2"],
      rows: [
        ["", "", "厚揚げとさつま芋の煮物", "57", "2", ""],
        ["", "△", "鶏じゃが", "0", "0", ""],
      ],
    },
    revisions: [],
  };
  let historyRequested = false;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          ...draftSheetPayload,
          source: "draft_sheet",
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      historyRequested = true;
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await route.fulfill({ status: 200, json: historyPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-SR-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-SR-001", name: "Facility Saved Revision Test", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-SR-001", name: "Facility Saved Revision Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.waitForTimeout(1500);
  await expect.poll(() => historyRequested, { timeout: 2000 }).toBe(true);

  const firstRowInputs = page.locator(".ocr-sheet-table tbody tr").nth(0).locator("input");
  await expect(firstRowInputs.nth(0)).toHaveValue("03/22");
  await expect(firstRowInputs.nth(1)).toHaveValue("朝");
  await expect(firstRowInputs.nth(2)).toHaveValue("厚揚げとさつま芋の煮物");

  const secondRowInputs = page.locator(".ocr-sheet-table tbody tr").nth(1).locator("input");
  await expect(secondRowInputs.nth(0)).toHaveValue("03/22");
  await expect(secondRowInputs.nth(1)).toHaveValue("昼");
  await expect(secondRowInputs.nth(2)).toHaveValue("鶏じゃが");
});

test("pdf fetch failure on one order does not poison later orders", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const firstOrderId = "ORD-E2E-PDF-FAIL-001";
  const secondOrderId = "ORD-E2E-PDF-FAIL-002";

  const makeOrder = (orderId: string) => ({
    id: orderId,
    status: "要確認",
    document: `file:///tmp/${orderId}.pdf`,
    facility: "FAC-PDF-001",
    week: "2026-03",
    week_value: "2026-03",
    week_label: "2026-03",
    lines: [],
  });

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${firstOrderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: makeOrder(firstOrderId) });
      return;
    }
    if (path.endsWith(`/orders/${secondOrderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: makeOrder(secondOrderId) });
      return;
    }
    if (path.endsWith(`/orders/${firstOrderId}/document`)) {
      await route.abort("failed");
      return;
    }
    if (path.endsWith(`/orders/${secondOrderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.includes("/draft-sheet") || path.includes("/ocr-sheet")) {
      const orderId = path.includes(firstOrderId) ? firstOrderId : secondOrderId;
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: "FAC-PDF-001",
          week_id: "2026-03",
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.includes("/week-options")) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: "2026-03", label: "2026-03", selected: true }] },
      });
      return;
    }
    if (path.includes("/ocr-output")) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.includes("/ocr-history")) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.includes("/history")) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.includes("/shipping-statuses")) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-PDF-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-PDF-001", name: "Facility PDF Test", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-PDF-001", name: "Facility PDF Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${firstOrderId}`);
  await expect(page.locator(".pdf-placeholder")).toContainText("PDFの取得に失敗しました。");

  await page.goto(`${baseUrl}/orders/${secondOrderId}`);
  await expect(page.locator('iframe[title="order-pdf"]')).toBeVisible();
  await expect(page.getByText("PDFの取得に失敗しました。")).toHaveCount(0);
});

test("order detail falls back to calendar date when week options are unavailable", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-WEEK-CALENDAR";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: "",
    week_value: "",
    lines: [],
  };
  let savedWeekPayload: any = null;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`) && method === "GET") {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week`) && method === "POST") {
      savedWeekPayload = route.request().postDataJSON();
      orderPayload.week = savedWeekPayload.week;
      orderPayload.week_value = savedWeekPayload.week;
      orderPayload.persisted_week_value = savedWeekPayload.week;
      await route.fulfill({ status: 200, json: { updated: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.includes("/shipping-statuses")) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-001", name: "Facility A", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-001", name: "Facility A" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  const dateInput = page.locator('input[type="date"]').first();
  await expect(dateInput).toBeVisible();
  await dateInput.fill("2026-04-06");
  await page.getByRole("button", { name: "設定を保存" }).click();

  await expect.poll(() => savedWeekPayload?.week).toBe("2026-04@2026-04-05~2026-04-11");
  await expect(page.getByText("週を設定しました。")).toBeVisible();
});

test("order detail treats month-only week as unresolved when explicit week options exist", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-WEEK-MONTH-ONLY";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: "2026-04",
    week_value: "2026-04",
    persisted_week_value: "2026-04",
    lines: [],
  };
  let savedWeekPayload: any = null;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          options: [
            { week_id: "2026-04@2026-04-05~2026-04-11", label: "2026-04 (04/05-04/11)", selected: false },
            { week_id: "2026-04@2026-04-01~2026-04-04", label: "2026-04 (04/01-04/04)", selected: false },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week`) && method === "POST") {
      savedWeekPayload = route.request().postDataJSON();
      orderPayload.week = "2026-04";
      orderPayload.week_value = savedWeekPayload.week;
      orderPayload.persisted_week_value = savedWeekPayload.week;
      await route.fulfill({ status: 200, json: { updated: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: orderPayload.week_value,
          fields: [],
          header: [],
          rows: [],
          row_ids: [],
          source: "none",
          can_apply: false,
          can_confirm: false,
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.includes("/shipping-statuses")) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-001", name: "Facility A", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-001", name: "Facility A" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  const select = page.locator(".field").filter({ hasText: "週 (Step1 必須)" }).locator("select");
  await expect(select).toHaveValue("");
  await expect(select.locator("option", { hasText: "(現在値)" })).toHaveCount(0);
  await select.selectOption("2026-04@2026-04-05~2026-04-11");
  await page.getByRole("button", { name: "設定を保存" }).click();

  await expect.poll(() => savedWeekPayload?.week).toBe("2026-04@2026-04-05~2026-04-11");
  await expect(page.getByText("週を設定しました。")).toBeVisible();
});

test("focused Step2 sheet row highlights the matching OCR overlay row and switches pages", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-OCR-OVERLAY-FOCUS-001";
  const explicitWeekId = "2026-04@2026-04-05~2026-04-11";
  const explicitWeekLabel = "2026-04 (04/05-04/11)";
  const overlaySvg = encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1400"><rect width="900" height="1400" fill="white"/><rect x="20" y="40" width="860" height="620" fill="#f3efe5" stroke="#c7b89d"/><rect x="20" y="720" width="860" height="620" fill="#f3efe5" stroke="#c7b89d"/></svg>',
  );
  const overlayUrl = `data:image/svg+xml;charset=utf-8,${overlaySvg}`;
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "apply_ready",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };
  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [
      ["04/05", "朝", "厚揚げとさつま芋の煮物", "12"],
      ["04/05", "昼", "鶏じゃが", "9"],
    ],
    row_ids: ["r1", "r2"],
    source: "ocr_table+ocr_payload",
    can_apply: true,
    can_confirm: true,
    apply_blockers: [],
    confirm_blockers: [],
    warnings: [],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          state: "apply_ready",
          apply_gate: {
            can_apply: true,
            can_confirm: true,
            blockers: [],
            warnings: [],
          },
          warnings: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({
        status: 200,
        json: {
          table_box: [0, 0, 1, 1],
          table_units: "normalized",
          grid_row_edges: [0, 0.45, 1],
          pages: [
            {
              page_index: 1,
              markdown_text:
                "| 日付 | 区分 | メニュー | 常食 |\n| --- | --- | --- | --- |\n| 04/05 | 朝 | 厚揚げとさつま芋の煮物 | 12 |",
              ocr_overlay_url: overlayUrl,
              figure_urls: [overlayUrl],
            },
            {
              page_index: 2,
              markdown_text:
                "| 日付 | 区分 | メニュー | 常食 |\n| --- | --- | --- | --- |\n| 04/05 | 昼 | 鶏じゃが | 9 |",
              ocr_overlay_url: overlayUrl,
              figure_urls: [overlayUrl],
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-001", name: "Facility A", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-001", name: "Facility A" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();

  const secondRowMenuInput = page.locator(".ocr-sheet-table tbody tr").nth(1).locator("input").nth(2);
  await expect(secondRowMenuInput).toHaveValue("鶏じゃが");
  await secondRowMenuInput.focus();

  await expect(page.locator(".page-tab.active")).toContainText("2");
  const highlight = page.getByTestId("ocr-overlay-row-highlight");
  await expect(highlight).toBeVisible();
  await expect(highlight).toHaveAttribute("data-overlay-page", "2");
  await expect(highlight).toHaveAttribute("data-overlay-row", "1");
});

test("original preview mode also highlights the matching row when page images exist", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-ORIGINAL-OVERLAY-FOCUS-001";
  const explicitWeekId = "2026-04@2026-04-05~2026-04-11";
  const explicitWeekLabel = "2026-04 (04/05-04/11)";
  const overlaySvg = encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1400"><rect width="900" height="1400" fill="white"/><text x="80" y="120" font-size="48">原本プレビュー</text><rect x="20" y="40" width="860" height="620" fill="#f3efe5" stroke="#c7b89d"/><rect x="20" y="720" width="860" height="620" fill="#f3efe5" stroke="#c7b89d"/></svg>',
  );
  const overlayUrl = `data:image/svg+xml;charset=utf-8,${overlaySvg}`;
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "apply_ready",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };
  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [
      ["04/05", "朝", "厚揚げとさつま芋の煮物", "12"],
      ["04/05", "昼", "鶏じゃが", "9"],
    ],
    row_ids: ["r1", "r2"],
    source: "ocr_table+ocr_payload",
    can_apply: true,
    can_confirm: true,
    apply_blockers: [],
    confirm_blockers: [],
    warnings: [],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          state: "apply_ready",
          apply_gate: {
            can_apply: true,
            can_confirm: true,
            blockers: [],
            warnings: [],
          },
          warnings: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({
        status: 200,
        json: {
          table_box: [0, 0, 1, 1],
          table_units: "normalized",
          grid_row_edges: [0, 0.45, 1],
          pages: [
            {
              page_index: 1,
              markdown_text:
                "| 日付 | 区分 | メニュー | 常食 |\n| --- | --- | --- | --- |\n| 04/05 | 朝 | 厚揚げとさつま芋の煮物 | 12 |",
              ocr_overlay_url: overlayUrl,
              figure_urls: [overlayUrl],
            },
            {
              page_index: 2,
              markdown_text:
                "| 日付 | 区分 | メニュー | 常食 |\n| --- | --- | --- | --- |\n| 04/05 | 昼 | 鶏じゃが | 9 |",
              ocr_overlay_url: overlayUrl,
              figure_urls: [overlayUrl],
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-001", name: "Facility A", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-001", name: "Facility A" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "原本PDF" }).click();

  const secondRowMenuInput = page.locator(".ocr-sheet-table tbody tr").nth(1).locator("input").nth(2);
  await secondRowMenuInput.focus();

  const wrapper = page.getByTestId("ocr-preview-wrapper");
  await expect(wrapper).toHaveAttribute("data-preview-mode", "original-image");
  const highlight = page.getByTestId("ocr-overlay-row-highlight");
  await expect(highlight).toBeVisible();
  await expect(highlight).toHaveAttribute("data-overlay-page", "2");
  await expect(highlight).toHaveAttribute("data-overlay-row", "1");
});

test("step2 forced recovery actions replace the current sheet with weekly or facility truth", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-FORCED-RECOVERY-001";
  const explicitWeekId = "2026-04@2026-04-05~2026-04-11";
  const explicitWeekLabel = "2026-04 (04/05-04/11)";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-FORCE-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "review_required",
      apply_gate: {
        can_apply: false,
        can_confirm: false,
        blockers: ["sheet_structural_projection_requires_review"],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["sheet_structural_projection_requires_review"],
      warnings: [],
    },
  };

  let currentSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-FORCE-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.soft_x", "qty.regular_bag_x", "remarks"],
    header: ["日付", "区分", "メニュー", "軟菜", "袋分け", "備考欄"],
    rows: [
      ["04/08", "朝", "アジの南蛮漬 さつま芋の煮物", "5", "0", ""],
      ["04/08", "朝", "アジの南蛮漬 さつま芋の煮物", "5", "0", ""],
    ],
    row_ids: ["broken-1", "broken-2"],
    source: "ocr_table+ocr_payload",
    can_apply: false,
    can_confirm: false,
    warnings: ["sheet_structural_projection_requires_review"],
    apply_blockers: ["sheet_structural_projection_requires_review"],
  };
  let forcedWeeklyPosted = false;
  let forcedFacilityPosted = false;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: currentSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          state: currentSheetPayload.can_apply ? "apply_ready" : "review_required",
          apply_gate: {
            can_apply: Boolean(currentSheetPayload.can_apply),
            can_confirm: Boolean(currentSheetPayload.can_confirm),
            blockers: currentSheetPayload.apply_blockers || [],
            warnings: currentSheetPayload.warnings || [],
          },
          warnings: currentSheetPayload.warnings || [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/force-weekly-menu`) && method === "POST") {
      forcedWeeklyPosted = true;
      currentSheetPayload = {
        ...currentSheetPayload,
        rows: [
          ["04/08", "朝", "アジの南蛮漬 さつま芋の煮物", "5", "0", ""],
          ["04/08", "朝", "小松菜のおかか和え", "0", "0", ""],
        ],
        row_ids: ["weekly-1", "weekly-2"],
        source: "forced_weekly_menu_overwrite",
        warnings: ["forced_weekly_menu_overwrite_applied"],
        apply_blockers: [],
        can_apply: true,
        can_confirm: true,
        repair_mode: "forced_weekly_menu_overwrite",
      };
      await route.fulfill({ status: 200, json: { updated: true, draft_payload: currentSheetPayload } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/force-facility-schema`) && method === "POST") {
      forcedFacilityPosted = true;
      currentSheetPayload = {
        ...currentSheetPayload,
        fields: [
          "date_mmdd",
          "daypart",
          "menu",
          "qty.soft_x",
          "qty.regular_bag_x",
          "qty.no_meat_x",
          "qty.no_fish_x",
          "qty.change_1_x",
          "qty.change_2_x",
          "remarks",
        ],
        header: ["日付", "区分", "メニュー", "軟菜", "袋分け", "肉禁", "魚禁", "変更1", "変更2", "備考欄"],
        rows: [
          ["04/08", "朝", "アジの南蛮漬 さつま芋の煮物", "", "", "", "", "", "", ""],
          ["04/08", "朝", "小松菜のおかか和え", "", "", "", "", "", "", ""],
        ],
        row_ids: ["facility-1", "facility-2"],
        source: "forced_facility_schema_overwrite",
        warnings: [
          "forced_facility_schema_overwrite_applied",
          "forced_quantity_manual_entry_required",
        ],
        apply_blockers: [],
        can_apply: true,
        can_confirm: true,
        repair_mode: "forced_facility_schema_overwrite",
      };
      await route.fulfill({ status: 200, json: { updated: true, draft_payload: currentSheetPayload } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: explicitWeekId, label: explicitWeekLabel, selected: true }] },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({ status: 200, json: { pages: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-FORCE-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-FORCE-001",
          name: "Facility Forced Recovery",
          config: null,
          resolved_config: {
            fax_template: {
              columns: [
                { key: "date_mmdd", header: "日付", name: "日付", role: "date" },
                { key: "daypart", header: "区分", name: "区分", role: "daypart" },
                { key: "menu", header: "メニュー", name: "メニュー", role: "menu" },
                { key: "qty.soft_x", header: "軟菜", name: "軟菜", role: "quantity" },
                { key: "qty.regular_bag_x", header: "袋分け", name: "袋分け", role: "quantity" },
                { key: "qty.no_meat_x", header: "肉禁", name: "肉禁", role: "quantity" },
                { key: "qty.no_fish_x", header: "魚禁", name: "魚禁", role: "quantity" },
                { key: "qty.change_1_x", header: "変更1", name: "変更1", role: "quantity" },
                { key: "qty.change_2_x", header: "変更2", name: "変更2", role: "quantity" },
                { key: "remarks", header: "備考欄", name: "備考欄", role: "remarks" },
              ],
            },
          },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-FORCE-001", name: "Facility Forced Recovery" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();

  await expect(page.locator(".ocr-sheet-table tbody tr").nth(1).locator("input").nth(2)).toHaveValue(
    "アジの南蛮漬 さつま芋の煮物",
  );

  await page.getByRole("button", { name: "週次メニューで日付・区分・メニューを強制復元" }).click();
  await expect.poll(() => forcedWeeklyPosted, { timeout: 2000 }).toBe(true);
  await expect(page.locator(".ocr-sheet-table tbody tr").nth(1).locator("input").nth(2)).toHaveValue(
    "小松菜のおかか和え",
  );
  await expect(page.getByText("週次メニューを基準に日付・区分・メニューを上書きしました。数量は必要なら確認してください。")).toBeVisible();

  await page.getByRole("button", { name: "施設設定の列構成で強制復元（数量は空白）" }).click();
  await expect.poll(() => forcedFacilityPosted, { timeout: 2000 }).toBe(true);
  await expect(page.locator(".ocr-sheet-table thead")).toContainText("袋分け");
  await expect(page.locator(".ocr-sheet-table thead")).toContainText("変更1");
  await expect(page.locator(".ocr-sheet-table tbody tr").nth(0).locator("input").nth(3)).toHaveValue("");
});
