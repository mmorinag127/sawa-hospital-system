import { test, expect } from "@playwright/test";

test("order detail editing flow uses grouped lines and PDF viewer", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-001";
  const explicitWeekId = "2026-01@2026-01-03~2026-01-09";
  const explicitWeekLabel = "01/03 - 01/09";
  const orderPayload: any = {
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

async function mountReparseOutcomeOrderPage(
  page: import("@playwright/test").Page,
  {
    orderId,
    orderPayload,
    workflowPayload,
    ocrOutputPayload,
    ocrPagesResponder,
    rerunResponder,
    draftSheetPayload: draftSheetPayloadOverride,
    ocrSheetPayload: ocrSheetPayloadOverride,
    requestTracker,
  }: {
    orderId: string;
    orderPayload: Record<string, unknown>;
    workflowPayload: Record<string, unknown>;
    ocrOutputPayload?: Record<string, unknown> | (() => Record<string, unknown>);
    ocrPagesResponder?: (() => Promise<{ status: number; json: any }> | { status: number; json: any }) | null;
    rerunResponder?: (() => Promise<{ status: number; json: any }> | { status: number; json: any }) | null;
    draftSheetPayload?: Record<string, unknown>;
    ocrSheetPayload?: Record<string, unknown>;
    requestTracker?: Record<string, number>;
  },
) {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-REPARSE-001",
    week_id: "2026-04@2026-04-19~2026-04-25",
    fields: ["date_mmdd", "daypart", "menu", "qty.regular"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["04/22", "朝", "Menu A", "5"]],
    row_ids: ["row-1"],
    source: "weekly_menu+ocr_payload",
    can_apply: true,
    can_confirm: false,
    apply_blockers: [],
    confirm_blockers: [],
    confirm_warnings: [],
    warnings: [],
    ...draftSheetPayloadOverride,
  };
  const ocrSheetPayload = {
    ...draftSheetPayload,
    reparse_health: orderPayload.ocr_reparse_health ?? null,
    processing_stage: orderPayload.ocr_processing_stage ?? null,
    result_state: orderPayload.ocr_result_state ?? null,
    ...ocrSheetPayloadOverride,
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      requestTracker && (requestTracker.order = (requestTracker.order ?? 0) + 1);
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`) && method === "GET") {
      requestTracker && (requestTracker.workflow = (requestTracker.workflow ?? 0) + 1);
      await route.fulfill({ status: 200, json: workflowPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) && method === "GET") {
      requestTracker && (requestTracker.draft = (requestTracker.draft ?? 0) + 1);
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`) && method === "GET") {
      requestTracker && (requestTracker.ocr = (requestTracker.ocr ?? 0) + 1);
      await route.fulfill({ status: 200, json: ocrSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`) && method === "GET") {
      const resolvedOcrOutputPayload =
        typeof ocrOutputPayload === "function" ? ocrOutputPayload() : ocrOutputPayload;
      await route.fulfill({
        status: 200,
        json: resolvedOcrOutputPayload ?? { status: "done", stage: "done" },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-pages`) && method === "GET") {
      if (ocrPagesResponder) {
        const response = await ocrPagesResponder();
        await route.fulfill(response);
        return;
      }
      await route.fulfill({
        status: 200,
        json: { pages: [{ page_index: 0, markdown_text: "|日付|区分|メニュー|常食|\n|04/22|朝|Menu A|5|" }], message: "" },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-rerun`) && method === "POST") {
      if (rerunResponder) {
        requestTracker && (requestTracker.rerun = (requestTracker.rerun ?? 0) + 1);
        const response = await rerunResponder();
        await route.fulfill(response);
        return;
      }
      requestTracker && (requestTracker.rerun = (requestTracker.rerun ?? 0) + 1);
      const nextWorkflow = {
        ...(workflowPayload || {}),
        candidate_prompt_visible: true,
        reparse_state: {
          ...(typeof workflowPayload?.reparse_state === "object" && workflowPayload.reparse_state
            ? workflowPayload.reparse_state
            : {}),
          status: "done",
        },
      };
      (orderPayload as any).ocr_status = "done";
      (orderPayload as any).ocr_error = null;
      (orderPayload as any).ocr_processing_stage = "draft_ready";
      (orderPayload as any).ocr_result_state = "done";
      (orderPayload as any).workflow_state = nextWorkflow;
      await route.fulfill({ status: 202, json: { accepted: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-recover`) && method === "POST") {
      requestTracker && (requestTracker.recover = (requestTracker.recover ?? 0) + 1);
      await route.fulfill({ status: 202, json: { accepted: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: { revisions: [], latest: null, reparse_state: workflowPayload.reparse_state ?? null },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/history`) && method === "GET") {
      await route.fulfill({ status: 200, json: { items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`) && method === "GET") {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`) && method === "GET") {
      await route.fulfill({ status: 200, json: { url: "https://example.com/order.pdf" } });
      return;
    }
    if (path.endsWith("/facilities/FAC-REPARSE-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-REPARSE-001", name: "Facility Reparse", config: {}, resolved_config: {} },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-REPARSE-001", name: "Facility Reparse" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
}

test("page summary keeps stale-timeout llm reparse visible even when current sheet remains usable", async ({ page }) => {
  const orderId = "ORD-E2E-REPARSE-FAILED";
  const workflowPayload = {
    state: "review_required",
    apply_gate: { can_apply: true, can_confirm: false, blockers: [], warnings: [] },
    current_sheet_revision_id: "current:reparse-failed",
    reparse_state: {
      status: "hard_failed",
      request_mode: "llm_reparse",
      processing_stage: "stale_timeout",
      result_state: "hard_failed",
      error: "reparse_stale_timeout>30m",
    },
  };
  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload: {
      id: orderId,
      status: "要確認",
      document: "",
      facility: "FAC-REPARSE-001",
      week: "2026-04@2026-04-19~2026-04-25",
      week_value: "2026-04@2026-04-19~2026-04-25",
      week_label: "04/19 - 04/25",
      lines: [],
      ocr_status: "failed",
      ocr_error: "reparse_stale_timeout>30m",
      ocr_processing_stage: "stale_timeout",
      ocr_result_state: "hard_failed",
      ocr_reparse_health: "hard_failed",
      ocr_metrics: {
        request_mode: "llm_reparse",
        processing_stage: "stale_timeout",
        result_state: "hard_failed",
        error: "reparse_stale_timeout>30m",
      },
      workflow_state: workflowPayload,
    },
    workflowPayload,
    ocrOutputPayload: {
      status: "done",
      stage: "done",
      _reparse_debug: {
        provider: "gemini",
        requested_provider: "gemini",
        processing_stage: "stale_timeout",
        result_state: "hard_failed",
        error: "reparse_stale_timeout>30m",
      },
    },
  });

  await expect(page.getByText("再解析失敗(現シート維持)")).toBeVisible();
  await expect(
    page.locator(".subtle").filter({
      hasText: "LLM補完再解析がタイムアウトしました。OCR結果は残っているため、必要なら再試行してください。 現在のシートは利用可能です。",
    }),
  ).toBeVisible();
});

test("page summary surfaces rejected llm reparse outcome instead of treating it as completed", async ({ page }) => {
  const orderId = "ORD-E2E-REPARSE-REJECTED";
  const workflowPayload = {
    state: "review_required",
    apply_gate: { can_apply: true, can_confirm: false, blockers: [], warnings: [] },
    current_sheet_revision_id: "current:reparse-rejected",
    reparse_state: {
      status: "done",
      request_mode: "llm_reparse",
      processing_stage: "draft_saved",
      result_state: "draft_ready_blocked",
      error: "sheet_llm_audit_failed",
    },
  };
  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload: {
      id: orderId,
      status: "要確認",
      document: "",
      facility: "FAC-REPARSE-001",
      week: "2026-04@2026-04-19~2026-04-25",
      week_value: "2026-04@2026-04-19~2026-04-25",
      week_label: "04/19 - 04/25",
      lines: [],
      ocr_status: "done",
      ocr_error: null,
      ocr_processing_stage: "draft_saved",
      ocr_result_state: "draft_ready_blocked",
      ocr_reparse_health: "done",
      ocr_metrics: {
        request_mode: "llm_reparse",
        processing_stage: "draft_saved",
        result_state: "draft_ready_blocked",
        error: "sheet_llm_audit_failed",
      },
      workflow_state: workflowPayload,
    },
    workflowPayload,
    ocrOutputPayload: {
      status: "done",
      stage: "done",
      _reparse_debug: {
        provider: "gemini",
        requested_provider: "gemini",
        processing_stage: "draft_saved",
        result_state: "draft_ready_blocked",
        error: "sheet_llm_audit_failed",
      },
    },
  });

  await expect(page.getByText("再解析却下(現シート維持)")).toBeVisible();
  await expect(
    page.locator(".subtle").filter({
      hasText: "LLM再解析結果は保存条件を満たさなかったため、現在のシートへ反映されませんでした。 現在のシートは利用可能です。",
    }),
  ).toBeVisible();
});

test("page summary prefers evidence-unavailable workflow truth and exposes OCR rerun action", async ({ page }) => {
  const orderId = "ORD-E2E-EVIDENCE-UNAVAILABLE";
  const requestTracker: Record<string, number> = {};
  const currentSheetRevisionId = "current:evidence-unavailable";
  const workflowPayload = {
    state: "uploaded",
    headline: "OCR証拠の生成待ちです",
    primary_action: "run_ocr_pipeline",
    blockers_json: ["evidence_view_unavailable", "evidence_edit_unavailable", "draft_rows_empty", "rows_empty"],
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["evidence_view_unavailable", "evidence_edit_unavailable", "draft_rows_empty", "rows_empty"],
      warnings: [],
    },
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_state: {
      status: "idle",
      request_mode: "none",
      processing_stage: "evidence_unavailable",
      result_state: "blocked",
      error: "ocr_evidence_recovery_required",
    },
  };
  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload: {
      id: orderId,
      status: "要確認",
      document: "",
      facility: "FAC-REPARSE-001",
      week: "2026-04@2026-04-19~2026-04-25",
      week_value: "2026-04@2026-04-19~2026-04-25",
      week_label: "04/19 - 04/25",
      lines: [],
      ocr_status: "failed",
      ocr_error: "ocr_recovery_exhausted",
      ocr_processing_stage: "stale_timeout",
      ocr_result_state: "hard_failed",
      ocr_reparse_health: "hard_failed",
      workflow_state: workflowPayload,
    },
    workflowPayload,
    draftSheetPayload: {
      current_sheet_revision_id: currentSheetRevisionId,
      source: "review_blocked",
      rows: [],
      row_ids: [],
      warnings: ["ocr_evidence_recovery_required"],
      apply_blockers: ["evidence_view_unavailable", "evidence_edit_unavailable", "draft_rows_empty", "rows_empty"],
    },
    ocrSheetPayload: {
      current_sheet_revision_id: currentSheetRevisionId,
      source: "review_blocked",
      rows: [],
      row_ids: [],
      warnings: ["ocr_evidence_recovery_required"],
      apply_blockers: ["evidence_view_unavailable", "evidence_edit_unavailable", "draft_rows_empty", "rows_empty"],
      reparse_health: "blocked",
      reparse_error: "ocr_evidence_recovery_required",
    },
    ocrOutputPayload: { detail: "ocr evidence recovery required" },
    requestTracker,
  });

  await expect(page.getByText("OCR証拠待ち", { exact: true })).toBeVisible();
  await expect(page.getByText("OCR結果がありません。ページは開いています。OCRパイプラインを再実行してください。")).toBeVisible();
  await expect(page.getByText("理由: ocr_recovery_exhausted")).toHaveCount(0);

  const rerunButton = page.locator(".workflow-summary-card").first().getByRole("button", { name: "OCRパイプラインを再実行" });
  await expect(rerunButton).toBeVisible();
  await rerunButton.click();
  await expect.poll(() => requestTracker.rerun ?? 0).toBe(1);
});

test("step2 auto-refreshes OCR preview after OCR rerun completes", async ({ page }) => {
  const orderId = "ORD-E2E-RERUN-PREVIEW";
  const currentSheetRevisionId = "current:rerun-preview";
  const requestTracker: Record<string, number> = {};
  const overlayUrl =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnHCq8AAAAASUVORK5CYII=";
  const workflowPayload: Record<string, any> = {
    state: "uploaded",
    headline: "OCR証拠の生成待ちです",
    primary_action: "run_ocr_pipeline",
    blockers_json: ["evidence_view_unavailable", "evidence_edit_unavailable"],
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["evidence_view_unavailable", "evidence_edit_unavailable"],
      warnings: [],
    },
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_state: {
      status: "idle",
      request_mode: "none",
      processing_stage: "evidence_unavailable",
      result_state: "blocked",
      error: "ocr_evidence_recovery_required",
    },
  };
  const orderPayload: Record<string, any> = {
    id: orderId,
    status: "要確認",
    document: "",
    facility: "FAC-REPARSE-001",
    week: "2026-04@2026-04-19~2026-04-25",
    week_value: "2026-04@2026-04-19~2026-04-25",
    week_label: "04/19 - 04/25",
    lines: [],
    ocr_status: "failed",
    ocr_error: "ocr_recovery_exhausted",
    ocr_processing_stage: "stale_timeout",
    ocr_result_state: "hard_failed",
    ocr_reparse_health: "hard_failed",
    workflow_state: workflowPayload,
  };
  let previewReady = false;
  let ocrPagesRequests = 0;

  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload,
    workflowPayload,
    ocrOutputPayload: () => ({ status: previewReady ? "done" : "running", stage: previewReady ? "done" : "ocr" }),
    ocrPagesResponder: async () => {
      ocrPagesRequests += 1;
      if (!previewReady) {
        return { status: 202, json: { pending: true } };
      }
      return {
        status: 200,
        json: {
          pages: [
            {
              page_index: 1,
              ocr_overlay_url: overlayUrl,
              figure_urls: [overlayUrl],
              tables: [
                {
                  rows: [
                    ["日付", "区分", "メニュー", "常食"],
                    ["04/22", "朝", "Menu A", "5"],
                  ],
                },
              ],
            },
          ],
          table_box: [0, 0, 1, 1],
          table_units: "normalized",
          grid_row_edges: [0, 0.5, 1],
        },
      };
    },
    rerunResponder: async () => {
      previewReady = true;
      workflowPayload.state = "review_required";
      workflowPayload.headline = "数量候補はありますが信頼度が低いため、確認してから反映してください";
      workflowPayload.primary_action = "review_ocr_sheet";
      workflowPayload.blockers_json = [];
      workflowPayload.apply_gate = {
        can_apply: false,
        can_confirm: false,
        blockers: ["quantity_review_required"],
        warnings: ["ocr_review_required"],
      };
      workflowPayload.reparse_state = {
        status: "done",
        request_mode: "ocr_rerun",
        processing_stage: "done",
        result_state: "evidence_ready",
        error: null,
      };
      orderPayload.ocr_status = "done";
      orderPayload.ocr_error = null;
      orderPayload.ocr_processing_stage = "done";
      orderPayload.ocr_result_state = "evidence_ready";
      orderPayload.workflow_state = workflowPayload;
      return { status: 202, json: { accepted: true } };
    },
    draftSheetPayload: {
      current_sheet_revision_id: currentSheetRevisionId,
      source: "review_blocked",
      rows: [],
      row_ids: [],
      warnings: ["ocr_evidence_recovery_required"],
      apply_blockers: ["evidence_view_unavailable", "evidence_edit_unavailable"],
    },
    ocrSheetPayload: {
      current_sheet_revision_id: currentSheetRevisionId,
      source: "review_blocked",
      rows: [],
      row_ids: [],
      warnings: ["ocr_evidence_recovery_required"],
      apply_blockers: ["evidence_view_unavailable", "evidence_edit_unavailable"],
    },
    requestTracker,
  });

  const rerunButton = page.locator(".workflow-summary-card").first().getByRole("button", { name: "OCRパイプラインを再実行" });
  await expect(rerunButton).toBeVisible();
  await rerunButton.click();
  await expect.poll(() => requestTracker.rerun ?? 0).toBe(1);
  await expect.poll(() => ocrPagesRequests).toBeGreaterThan(1);
  await expect(page.locator('img[alt="OCR overlay"]')).toBeVisible();
  await expect(page.getByText("OCRページを取得中...")).toHaveCount(0);
});

test("step2 auto-refreshes OCR preview when rerun completion lands on candidate-choice state", async ({ page }) => {
  const orderId = "ORD-E2E-RERUN-PREVIEW-CANDIDATE";
  const currentSheetRevisionId = "current:rerun-preview-candidate";
  const requestTracker: Record<string, number> = {};
  const overlayUrl = "https://example.com/ocr-preview-candidate.png";
  const workflowPayload: Record<string, any> = {
    state: "uploaded",
    headline: "OCR証拠の生成待ちです",
    primary_action: "run_ocr_pipeline",
    blockers_json: ["evidence_view_unavailable", "evidence_edit_unavailable"],
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["evidence_view_unavailable", "evidence_edit_unavailable"],
      warnings: [],
    },
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_state: {
      status: "idle",
      request_mode: "none",
      processing_stage: "evidence_unavailable",
      result_state: "blocked",
      error: "ocr_evidence_recovery_required",
    },
  };
  const orderPayload: Record<string, any> = {
    id: orderId,
    status: "要確認",
    document: "",
    facility: "FAC-REPARSE-001",
    week: "2026-04@2026-04-19~2026-04-25",
    week_value: "2026-04@2026-04-19~2026-04-25",
    week_label: "04/19 - 04/25",
    lines: [],
    ocr_status: "failed",
    ocr_error: "ocr_recovery_exhausted",
    ocr_processing_stage: "stale_timeout",
    ocr_result_state: "hard_failed",
    ocr_reparse_health: "hard_failed",
    workflow_state: workflowPayload,
  };
  let previewReady = false;
  let ocrPagesRequests = 0;

  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload,
    workflowPayload,
    ocrOutputPayload: () => ({ status: previewReady ? "done" : "running", stage: previewReady ? "done" : "ocr" }),
    ocrPagesResponder: async () => {
      ocrPagesRequests += 1;
      if (!previewReady) {
        return { status: 202, json: { pending: true } };
      }
      return {
        status: 200,
        json: {
          pages: [
            {
              page_index: 1,
              ocr_overlay_url: overlayUrl,
              figure_urls: [overlayUrl],
              tables: [
                {
                  rows: [
                    ["日付", "区分", "メニュー", "常食"],
                    ["04/22", "昼", "Menu B", "8"],
                  ],
                },
              ],
            },
          ],
          table_box: [0, 0, 1, 1],
          table_units: "normalized",
          grid_row_edges: [0, 0.5, 1],
        },
      };
    },
    rerunResponder: async () => {
      previewReady = true;
      workflowPayload.state = "new_evidence_available";
      workflowPayload.headline = "新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください";
      workflowPayload.primary_action = "new_evidence_available";
      workflowPayload.blockers_json = [];
      workflowPayload.candidate_prompt_visible = true;
      workflowPayload.candidate_evidence_run_id = "OEV-CANDIDATE-001";
      workflowPayload.active_evidence_run_id = "OEV-ACTIVE-001";
      workflowPayload.apply_gate = {
        can_apply: false,
        can_confirm: false,
        blockers: ["quantity_review_required"],
        warnings: ["ocr_review_required"],
      };
      workflowPayload.reparse_state = {
        status: "done",
        request_mode: "ocr_rerun",
        processing_stage: "done",
        result_state: "evidence_ready",
        error: null,
      };
      orderPayload.ocr_status = "done";
      orderPayload.ocr_error = null;
      orderPayload.ocr_processing_stage = "done";
      orderPayload.ocr_result_state = "evidence_ready";
      orderPayload.workflow_state = workflowPayload;
      return { status: 202, json: { accepted: true } };
    },
    draftSheetPayload: {
      current_sheet_revision_id: currentSheetRevisionId,
      source: "review_blocked",
      rows: [],
      row_ids: [],
      warnings: ["ocr_evidence_recovery_required"],
      apply_blockers: ["evidence_view_unavailable", "evidence_edit_unavailable"],
    },
    ocrSheetPayload: {
      current_sheet_revision_id: currentSheetRevisionId,
      source: "review_blocked",
      rows: [],
      row_ids: [],
      warnings: ["ocr_evidence_recovery_required"],
      apply_blockers: ["evidence_view_unavailable", "evidence_edit_unavailable"],
    },
    requestTracker,
  });

  const rerunButton = page.locator(".workflow-summary-card").first().getByRole("button", { name: "OCRパイプラインを再実行" });
  await expect(rerunButton).toBeVisible();
  await rerunButton.click();
  await expect.poll(() => requestTracker.rerun ?? 0).toBe(1);
  await expect.poll(() => ocrPagesRequests).toBeGreaterThan(1);
  await expect(page.locator('img[alt="OCR overlay"]')).toBeVisible();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();
});

test("step2 keeps a visible OCR refresh action when preview pages are unavailable", async ({ page }) => {
  const orderId = "ORD-E2E-RERUN-PREVIEW-SIBLING";
  const currentSheetRevisionId = "current:rerun-preview-sibling";
  const workflowPayload = {
    state: "review_required",
    headline: "数量候補はありますが信頼度が低いため、確認してから反映してください",
    primary_action: "review_ocr_sheet",
    blockers_json: [],
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["quantity_review_required"],
      warnings: ["ocr_review_required"],
    },
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_state: {
      status: "done",
      request_mode: "ocr_rerun",
      processing_stage: "done",
      result_state: "evidence_ready",
      error: null,
    },
  };
  let ocrPagesRequests = 0;

  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload: {
      id: orderId,
      status: "要確認",
      document: "",
      facility: "FAC-REPARSE-001",
      week: "2026-04@2026-04-19~2026-04-25",
      week_value: "2026-04@2026-04-19~2026-04-25",
      week_label: "04/19 - 04/25",
      lines: [],
      ocr_status: "done",
      ocr_error: null,
      ocr_processing_stage: "done",
      ocr_result_state: "evidence_ready",
      ocr_reparse_health: "done",
      workflow_state: workflowPayload,
    },
    workflowPayload,
    ocrPagesResponder: async () => {
      ocrPagesRequests += 1;
      return { status: 404, json: { detail: "ocr pages not found" } };
    },
  });

  const inlineRefreshButton = page.getByRole("button", { name: "OCR表示を再取得" });
  await expect(inlineRefreshButton).toBeVisible();
  await inlineRefreshButton.click();
  await expect.poll(() => ocrPagesRequests).toBeGreaterThan(1);
  await expect(page.getByRole("button", { name: "OCRページを更新" })).toBeVisible();
});

test("page stays in parity with order, workflow, draft-sheet, and ocr-sheet while llm reparse awaits first-pass output", async ({
  page,
}) => {
  const orderId = "ORD-E2E-REPARSE-PARITY";
  const currentSheetRevisionId =
    "current:15a3734da631c36d1a15e8a4c6a0154d34e5511d245b33714370d78775cc48f4";
  const requestTracker: Record<string, number> = {};
  const workflowPayload = {
    state: "review_required",
    apply_gate: { can_apply: false, can_confirm: false, blockers: ["column_mapping_unresolved"], warnings: [] },
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_state: {
      status: "awaiting_output",
      request_mode: "llm_reparse",
      processing_stage: "ocr_pipeline",
      result_state: "awaiting_output",
      error: "ocr_output_pending",
    },
    ocr_last_reparse_error: "ocr_output_pending",
    ocr_reparse_status: "awaiting_output",
    ocr_processing_stage: "ocr_pipeline",
    ocr_result_state: "awaiting_output",
  };
  const draftSheetPayload = {
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_status: "awaiting_output",
    reparse_health: "awaiting_output",
    reparse_error: "ocr_output_pending",
  };
  const ocrSheetPayload = {
    current_sheet_revision_id: currentSheetRevisionId,
    reparse_status: "awaiting_output",
    reparse_health: "awaiting_output",
    reparse_error: "ocr_output_pending",
  };

  await mountReparseOutcomeOrderPage(page, {
    orderId,
    orderPayload: {
      id: orderId,
      status: "要確認",
      document: "",
      facility: "FAC-REPARSE-001",
      week: "2026-04@2026-04-19~2026-04-25",
      week_value: "2026-04@2026-04-19~2026-04-25",
      week_label: "04/19 - 04/25",
      lines: [],
      ocr_status: "awaiting_output",
      ocr_error: "ocr_output_pending",
      ocr_processing_stage: "ocr_pipeline",
      ocr_result_state: "awaiting_output",
      ocr_reparse_health: "awaiting_output",
      current_sheet_revision_id: currentSheetRevisionId,
      workflow_state: workflowPayload,
    },
    workflowPayload,
    draftSheetPayload,
    ocrSheetPayload,
    requestTracker,
  });

  await expect(page.getByText("実行中", { exact: true })).toBeVisible();
  await expect(page.locator("p").filter({ hasText: "OCRを実行中です。完了まで数分かかります。" })).toBeVisible();
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.locator(".step-title")).toHaveText("OCR修正");

  expect(requestTracker.order).toBeGreaterThan(0);
  expect(requestTracker.draft).toBeGreaterThan(0);
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

test("order detail can archive and unarchive a single order", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-ARCHIVE-001";
  let archived = false;
  const baseOrderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: "2026-04@2026-04-05~2026-04-11",
    week_value: "2026-04@2026-04-05~2026-04-11",
    persisted_week_value: "2026-04@2026-04-05~2026-04-11",
    week_label: "04/05 - 04/11",
    lines: [],
  };

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

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          ...baseOrderPayload,
          is_archived: archived,
          archived_at: archived ? "2026-04-16T12:34:56" : null,
          archived_by: archived ? "operator" : null,
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/archive`) && method === "POST") {
      archived = true;
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          archived: true,
          changed: true,
          archived_at: "2026-04-16T12:34:56",
          archived_by: "operator",
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/unarchive`) && method === "POST") {
      archived = false;
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          archived: false,
          changed: true,
          archived_at: null,
          archived_by: null,
        },
      });
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
          facility_id: baseOrderPayload.facility,
          week_id: baseOrderPayload.week_value,
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
        json: {
          options: [
            {
              week_id: baseOrderPayload.week_value,
              label: baseOrderPayload.week_label,
              selected: true,
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "ready" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-history`) || path.endsWith(`/orders/${orderId}/history`)) {
      await route.fulfill({ status: 200, json: { revisions: [], latest: null, items: [] } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/shipping-statuses`)) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith(`/facilities/${baseOrderPayload.facility}`)) {
      await route.fulfill({
        status: 200,
        json: {
          id: baseOrderPayload.facility,
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
        json: { facilities: [{ id: baseOrderPayload.facility, name: "Facility E2E Test" }] },
      });
      return;
    }
    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await expect(page.getByRole("button", { name: "この注文をアーカイブ" })).toBeVisible();
  await page.getByRole("button", { name: "この注文をアーカイブ" }).click();
  await expect(page.getByText("注文をアーカイブしました。通常の注文一覧から除外されます。")).toBeVisible();
  await expect(page.getByText("この注文はアーカイブ済みです。通常の注文一覧には表示されません。")).toBeVisible();
  await expect(page.getByRole("button", { name: "アーカイブ解除" })).toBeVisible();

  await page.getByRole("button", { name: "アーカイブ解除" }).click();
  await expect(page.getByText("注文のアーカイブを解除しました。")).toBeVisible();
  await expect(page.getByRole("button", { name: "この注文をアーカイブ" })).toBeVisible();
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
  const weekSelect = page.locator(".field").filter({ hasText: "週 (Step1 必須)" }).locator("select");
  await expect(weekSelect).toBeVisible();
  await page.locator(".step-tab").nth(1).click();
  await page.getByRole("heading", { name: "OCR修正" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();

  await expect(page.getByRole("button", { name: "OCR基盤を復旧" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "修正完了 / 保存して明細に反映して次へ" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "明細に反映して次へ", exact: true })).toHaveCount(0);
  const saveDraftButtons = page.getByRole("button", { name: "シートを保存（暫定）" });
  await expect(saveDraftButtons).toHaveCount(2);
  await expect(saveDraftButtons.first()).toBeDisabled();
  await expect(saveDraftButtons.nth(1)).toBeDisabled();
  await expect(page.getByRole("button", { name: "行を追加" })).toHaveCount(0);
});

test("weekly menu blocked shows explicit stop-state instead of empty editor", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-WEEKLY-MENU-BLOCKED-001";
  const explicitWeekId = "2026-05@2026-05-01~2026-05-02";
  const explicitWeekLabel = "05/01 - 05/02";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC00010",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "review_required",
      headline: "高リスクなOCR候補を確認してください",
      apply_gate: {
        can_apply: false,
        can_confirm: false,
        blockers: ["draft_rows_empty", "monthly_menu_object_missing", "rows_empty"],
        warnings: [],
      },
    },
    apply_gate: {
      can_apply: false,
      can_confirm: false,
      blockers: ["draft_rows_empty", "monthly_menu_object_missing", "rows_empty"],
      warnings: [],
    },
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith(`/orders/${orderId}`)) {
      await route.fulfill({ status: 200, json: orderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          facility_id: orderPayload.facility,
          week_id: explicitWeekId,
          resolved_week_id: explicitWeekId,
          fields: ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
          header: ["日付", "区分", "メニュー", "常食2F", "備考"],
          rows: [],
          row_ids: [],
          source: "weekly_menu_blocked",
          warnings: ["monthly_menu_object_missing", "rows_empty"],
          blockers: ["monthly_menu_object_missing"],
          draft_state: "draft_blocked",
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-output`)) {
      await route.fulfill({ status: 200, json: { status: "done" } });
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
    if (path.endsWith(`/orders/${orderId}/ocr-pages`)) {
      await route.fulfill({ status: 200, json: { pages: [] } });
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
  const weekSelect = page.locator(".field").filter({ hasText: "週 (Step1 必須)" }).locator("select");
  await expect(weekSelect).toBeVisible();
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();

  await expect(page.getByText("編集可能な正解シートをまだ作れません")).toBeVisible();
  await expect(page.getByText("対象月の月次メニュー本体が未登録です", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "月次メニューを確認" })).toHaveAttribute("href", "/menus/2026-05");
});

test("Step2 keep-current clears unresolved candidate after reload", async ({ page }) => {
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
      candidate_prompt_visible: true,
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
  let currentOrderPayload: any = { ...orderPayload };

  const draftSheetPayload: any = {
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
    if (path.endsWith(`/orders/${orderId}/workflow-state`) && method === "GET") {
      await route.fulfill({ status: 200, json: currentOrderPayload.workflow_state || {} });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/keep-current`) && method === "POST") {
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "review_required",
          candidate_prompt_visible: false,
          candidate_evidence_run_id: null,
          acknowledged_candidate_evidence_run_id: "EVD-001",
          active_evidence_run_id: "EVD-ACTIVE-001",
          primary_action: "review_critical_cells",
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
      await route.fulfill({ status: 200, json: currentOrderPayload.workflow_state });
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
          state: "apply_ready",
          candidate_prompt_visible: false,
          candidate_evidence_run_id: null,
          acknowledged_candidate_evidence_run_id: "EVD-001",
          active_evidence_run_id: "EVD-ACTIVE-001",
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
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();
  await page.getByRole("button", { name: "現状を維持" }).click();
  await expect(page.getByText("現在のシートを維持して進みます。必要ならあとで新しいOCR候補へ切り替えられます。")).toBeVisible();
  await expect.poll(() => savedDraftPayload, { timeout: 2000 }).toBeNull();
  await expect.poll(() => applyPayload, { timeout: 2000 }).toBeNull();
  await page.reload();
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toHaveCount(0);
  await expect(page.getByText("現在のシートを維持しています。必要ならあとで新しいOCR候補へ切り替えられます。")).toHaveCount(0);
});

test("Step2 keep-current does not stay pending while the background refresh is still loading", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-KEEP-CURRENT-LIGHT-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const pdfBody = "%PDF-1.4";
  let backgroundRefreshStarted = false;
  const initialOrderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-KCL-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    persisted_week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    ocr_draft_revision_id: "OCRREV-KCL-001",
    current_sheet_revision_id: "OCRREV-KCL-001",
    workflow_state: {
      state: "apply_ready",
      candidate_prompt_visible: true,
      candidate_evidence_run_id: "EVD-CAND-KCL-001",
      acknowledged_candidate_evidence_run_id: null,
      active_evidence_run_id: "EVD-ACTIVE-KCL-001",
      current_sheet_revision_id: "OCRREV-KCL-001",
      candidate_sheet_state: {
        current_sheet_revision_id: "OCRREV-KCL-001",
        candidate_evidence_run_id: "EVD-CAND-KCL-001",
        candidate_preview_available: true,
        candidate_has_meaningful_diff: true,
        candidate_preview_error: null,
      },
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
  const acknowledgedWorkflowState = {
    state: "apply_ready",
    candidate_prompt_visible: false,
    candidate_evidence_run_id: null,
    acknowledged_candidate_evidence_run_id: "EVD-CAND-KCL-001",
    active_evidence_run_id: "EVD-ACTIVE-KCL-001",
    current_sheet_revision_id: "OCRREV-KCL-001",
    candidate_sheet_state: {
      current_sheet_revision_id: "OCRREV-KCL-001",
      candidate_evidence_run_id: null,
      candidate_preview_available: false,
      candidate_has_meaningful_diff: false,
      candidate_preview_error: null,
    },
    primary_action: "apply_draft",
    apply_gate: {
      can_apply: true,
      can_confirm: true,
      blockers: [],
      warnings: [],
    },
  };
  let currentOrderPayload: any = { ...initialOrderPayload };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-KCL-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["03/01", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
    confirm_blockers: [],
    confirm_warnings: [],
    draft_newer_than_lines: true,
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
      if (backgroundRefreshStarted) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`) && method === "GET") {
      if (backgroundRefreshStarted) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
      await route.fulfill({ status: 200, json: currentOrderPayload.workflow_state });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/keep-current`) && method === "POST") {
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: acknowledgedWorkflowState,
        apply_gate: acknowledgedWorkflowState.apply_gate,
      };
      backgroundRefreshStarted = true;
      await route.fulfill({ status: 200, json: acknowledgedWorkflowState });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      if (backgroundRefreshStarted) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
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
    if (path.endsWith("/facilities/FAC-KCL-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-KCL-001", name: "Facility Test", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-KCL-001", name: "Facility Test" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();

  const startedAt = Date.now();
  await page.getByRole("button", { name: "現状を維持" }).click();

  await expect(page.getByText("現在のシートを維持して進みます。必要ならあとで新しいOCR候補へ切り替えられます。")).toBeVisible({ timeout: 1000 });
  await expect(page.getByRole("button", { name: "現状を維持" })).toHaveCount(0, { timeout: 1000 });
  await expect(page.getByRole("button", { name: "修正完了 / 保存して明細に反映して次へ" })).toBeVisible({ timeout: 1000 });
  expect(Date.now() - startedAt).toBeLessThan(2000);
});

test("Step2 shows a genuinely new candidate even when current sheet revision stays the same", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-KEEP-CURRENT-NEW-CANDIDATE-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const staleCandidateOrderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-KC-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    persisted_week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    ocr_draft_revision_id: "OCRREV-KC-001",
    current_sheet_revision_id: "OCRREV-KC-001",
    workflow_state: {
      state: "new_evidence_available",
      candidate_prompt_visible: true,
      candidate_evidence_run_id: "EVD-CAND-KC-001",
      acknowledged_candidate_evidence_run_id: null,
      active_evidence_run_id: "EVD-ACTIVE-KC-001",
      current_sheet_revision_id: "OCRREV-KC-001",
      candidate_sheet_state: {
        current_sheet_revision_id: "OCRREV-KC-001",
        candidate_evidence_run_id: "EVD-CAND-KC-001",
        candidate_preview_available: true,
        candidate_has_meaningful_diff: true,
        candidate_preview_error: null,
      },
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
  let currentOrderPayload: any = { ...staleCandidateOrderPayload };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-KC-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["03/01", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
    confirm_blockers: [],
    confirm_warnings: [],
    draft_newer_than_lines: true,
  };
  const pdfBody = "%PDF-1.4";
  let orderGetCount = 0;
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
      orderGetCount += 1;
      if (orderGetCount === 2) {
        await new Promise((resolve) => setTimeout(resolve, 300));
        await route.fulfill({ status: 200, json: staleCandidateOrderPayload });
        return;
      }
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/keep-current`) && method === "POST") {
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "review_required",
          candidate_prompt_visible: false,
          candidate_evidence_run_id: null,
          acknowledged_candidate_evidence_run_id: "EVD-CAND-KC-001",
          active_evidence_run_id: "EVD-ACTIVE-KC-001",
          candidate_sheet_state: {
            current_sheet_revision_id: "OCRREV-KC-001",
            candidate_evidence_run_id: null,
            candidate_preview_available: false,
            candidate_has_meaningful_diff: false,
            candidate_preview_error: null,
          },
          primary_action: "review_critical_cells",
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
      await route.fulfill({ status: 200, json: currentOrderPayload.workflow_state });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) && method === "POST") {
      savedDraftPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { ok: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-apply`)) {
      applyPayload = route.request().postDataJSON();
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "apply_ready",
          candidate_prompt_visible: false,
          candidate_evidence_run_id: null,
          acknowledged_candidate_evidence_run_id: "EVD-CAND-KC-001",
          active_evidence_run_id: "EVD-ACTIVE-KC-001",
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
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
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
    if (path.endsWith("/facilities/FAC-KC-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-KC-001", name: "Facility KC", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-KC-001", name: "Facility KC" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();
  await page.getByRole("button", { name: "現状を維持" }).click();
  await expect(page.getByText("現在のシートを維持して進みます。必要ならあとで新しいOCR候補へ切り替えられます。")).toBeVisible();
  currentOrderPayload = {
    ...currentOrderPayload,
    workflow_state: {
      ...currentOrderPayload.workflow_state,
      state: "new_evidence_available",
      candidate_prompt_visible: true,
      candidate_evidence_run_id: "EVD-CAND-KC-002",
      acknowledged_candidate_evidence_run_id: "EVD-CAND-KC-001",
      active_evidence_run_id: "EVD-ACTIVE-KC-001",
      current_sheet_revision_id: "OCRREV-KC-001",
      candidate_sheet_state: {
        current_sheet_revision_id: "OCRREV-KC-001",
        candidate_evidence_run_id: "EVD-CAND-KC-002",
        candidate_preview_available: true,
        candidate_has_meaningful_diff: true,
        candidate_preview_error: null,
      },
      primary_action: "switch_to_new_evidence",
      apply_gate: {
        can_apply: true,
        can_confirm: true,
        blockers: [],
        warnings: [],
      },
    },
  };
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.reload();
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();
  await expect.poll(() => savedDraftPayload, { timeout: 2000 }).toBeNull();
  await expect.poll(() => applyPayload, { timeout: 2000 }).toBeNull();
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
      acknowledged_candidate_evidence_run_id: null,
      active_evidence_run_id: "EVD-ACTIVE-001",
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
  let currentOrderPayload = { ...orderPayload };

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
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/keep-current`) && method === "POST") {
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "review_required",
          candidate_evidence_run_id: null,
          acknowledged_candidate_evidence_run_id: "EVD-CAND-001",
          active_evidence_run_id: "EVD-ACTIVE-001",
        },
      };
      await route.fulfill({ status: 200, json: currentOrderPayload.workflow_state });
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
  await expect(page.getByRole("button", { name: "現状を維持" })).toBeVisible();
  await expect(page.getByText("OCRが失敗しました: main_ocr_failed:gemini")).toHaveCount(0);
  await expect(page.getByText("理由: main_ocr_failed:gemini")).toHaveCount(0);
  await page.getByRole("button", { name: "現状を維持" }).click();
  await expect(page.getByText("現在のシートを維持して進みます。必要ならあとで新しいOCR候補へ切り替えられます。")).toBeVisible();
  await page.reload();
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toHaveCount(0);
  await expect(page.getByText("現在のシートを維持しています。必要ならあとで新しいOCR候補へ切り替えられます。")).toHaveCount(0);
  currentOrderPayload = {
    ...currentOrderPayload,
    workflow_state: {
      ...currentOrderPayload.workflow_state,
      state: "new_evidence_available",
      candidate_evidence_run_id: "EVD-CAND-002",
      acknowledged_candidate_evidence_run_id: "EVD-CAND-001",
      active_evidence_run_id: "EVD-ACTIVE-001",
    },
  };
  await page.reload();
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。")).toBeVisible();
  await expect(page.getByRole("button", { name: "現状を維持" })).toBeVisible();

  await page.getByRole("button", { name: "いいえ / 迷う" }).click();
  await page.getByRole("button", { name: "AIに任せる" }).click();

  await expect(page.locator("select.llm-provider-select").last()).toHaveValue("gemini");
  await expect(page.locator("select.llm-model-select").last()).toHaveValue("pro");
  await expect(page.getByRole("button", { name: "シート再読込" }).last()).toBeVisible();
});

test("authoritative Step2 apply beats an older in-flight refresh so the same OCR candidate does not reappear", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-STALE-WORKSPACE-REFRESH-001";
  const explicitWeekId = "2026-04@2026-04-26~2026-04-30";
  const explicitWeekLabel = "04/26 - 04/30";
  const staleCandidateOrderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-WS-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    persisted_week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    lines: [],
    workflow_state: {
      state: "new_evidence_available",
      candidate_evidence_run_id: "EVD-CAND-STALE-001",
      acknowledged_candidate_evidence_run_id: null,
      active_evidence_run_id: "EVD-ACTIVE-001",
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
  let currentOrderPayload: any = {
    ...staleCandidateOrderPayload,
    workflow_state: {
      ...staleCandidateOrderPayload.workflow_state,
      state: "apply_ready",
      candidate_evidence_run_id: null,
      acknowledged_candidate_evidence_run_id: "EVD-CAND-STALE-001",
      primary_action: "apply_draft",
    },
  };

  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-WS-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["04/26", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
    confirm_blockers: [],
    confirm_warnings: [],
    draft_newer_than_lines: true,
  };

  const pdfBody = "%PDF-1.4";
  let orderGetCount = 0;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      orderGetCount += 1;
      if (orderGetCount === 2) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        await route.fulfill({ status: 200, json: staleCandidateOrderPayload });
        return;
      }
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`)) {
      if (method === "POST") {
        await route.fulfill({ status: 200, json: { ok: true } });
        return;
      }
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-apply`)) {
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "apply_ready",
          candidate_evidence_run_id: null,
          acknowledged_candidate_evidence_run_id: "EVD-CAND-STALE-001",
          primary_action: "apply_draft",
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
          review_state: "ready",
          review_stage: "confirmed",
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
    if (path.endsWith("/facilities/FAC-WS-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-WS-001", name: "Facility WS", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-WS-001", name: "Facility WS" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.getByRole("button", { name: "はい / 修正済み" }).click();
  await page.getByRole("button", { name: "修正完了 / 保存して明細に反映して次へ" }).click();
  await expect(page.locator(".step-title")).toHaveText("明細の確認と修正");
  await page.waitForTimeout(700);
  await page.getByRole("button", { name: "次へ: 袋わけ" }).first().click();
  await expect(page.locator(".step-title")).toHaveText("袋わけ結果の確認");
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await expect(
    page.getByText("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。"),
  ).toHaveCount(0);
});

test("Step2 save-and-next reaches Step3 before the post-save refresh settles", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-STEP2-SAVE-NEXT-FAST-001";
  const explicitWeekId = "2026-04@2026-04-26~2026-04-30";
  const explicitWeekLabel = "04/26 - 04/30";
  const draftSheetPayload = {
    order_id: orderId,
    facility_id: "FAC-S2-001",
    week_id: explicitWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_x"],
    header: ["日付", "区分", "メニュー", "常食"],
    rows: [["04/26", "朝", "朝食", "10"]],
    row_ids: ["r1"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
    confirm_blockers: [],
    confirm_warnings: [],
    draft_newer_than_lines: true,
  };
  let savedDraftPayload: any = null;
  let applyPayload: any = null;
  let delayedOrderRefresh = false;
  let currentOrderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-S2-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    persisted_week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    current_sheet_revision_id: "OCRREV-S2-OLD-001",
    lines: [],
    workflow_state: {
      state: "apply_ready",
      current_sheet_revision_id: "OCRREV-S2-OLD-001",
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

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();

    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      if (delayedOrderRefresh) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
      }
      await route.fulfill({ status: 200, json: currentOrderPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) && method === "POST") {
      savedDraftPayload = route.request().postDataJSON();
      delayedOrderRefresh = true;
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          revision: {
            revision_id: "OCRREV-S2-NEW-001",
            ui_mode: "sheet",
            fields: draftSheetPayload.fields,
            header: draftSheetPayload.header,
            row_ids: draftSheetPayload.row_ids,
            rows: draftSheetPayload.rows,
            review_state: "ready",
            review_blockers: [],
            review_warnings: [],
          },
          draft: {
            id: "ODR-S2-NEW-001",
            order_id: orderId,
            draft_state: "edited_sheet_exact",
          },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) && method === "GET") {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          ...draftSheetPayload,
          review_state: "ready",
          review_stage: "confirmed",
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-apply`) && method === "POST") {
      applyPayload = route.request().postDataJSON();
      currentOrderPayload = {
        ...currentOrderPayload,
        current_sheet_revision_id: "OCRREV-S2-NEW-001",
        workflow_state: {
          ...currentOrderPayload.workflow_state,
          state: "apply_ready",
          current_sheet_revision_id: "OCRREV-S2-NEW-001",
          candidate_prompt_visible: false,
        },
        lines: [
          {
            id: "line-001",
            menu_name: "朝食",
            quantity: 10,
          },
        ],
      };
      await route.fulfill({ status: 200, json: currentOrderPayload });
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
      if (delayedOrderRefresh) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
      }
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
    if (path.endsWith("/facilities/FAC-S2-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-S2-001", name: "Facility Step2", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-S2-001", name: "Facility Step2" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();
  await page.getByRole("button", { name: "はい / 修正済み" }).click();

  const startedAt = Date.now();
  await page.getByRole("button", { name: "修正完了 / 保存して明細に反映して次へ" }).click();

  await expect.poll(() => Boolean(savedDraftPayload), { timeout: 2000 }).toBe(true);
  await expect.poll(() => Boolean(applyPayload), { timeout: 2000 }).toBe(true);
  expect(applyPayload?.expected_revision_id).toBe("ODR-S2-NEW-001");
  await expect(page.locator(".step-title")).toHaveText("明細の確認と修正", { timeout: 2000 });
  expect(Date.now() - startedAt).toBeLessThan(3000);
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
  await page.getByRole("button", { name: "次へ: OCR修正" }).first().click();
  await page.getByRole("button", { name: "いいえ / 迷う" }).click();
  await page.getByRole("button", { name: "AIに任せる" }).click();
  await page.locator(".ocr-inline-details summary").click();
  const promptArea = page.locator(".ocr-llm-prompt-textarea").first();
  await expect(promptArea).toContainText("Treat the current sheet shown in the editor as the canonical row structure.");
  await expect(promptArea).toContainText("The canonical daypart blocks are 朝/昼/夕.");
  await expect(promptArea).toContainText("soft_x, regular_bag_x, no_meat_x, no_fish_x, change_1_x, change_2_x");
  await expect(promptArea).not.toContainText("Return a JSON object only.");
  await expect(promptArea).not.toContainText("regular_2f");
  await page.getByRole("button", { name: "LLM補完再解析" }).click();
  await expect
    .poll(() => reparsePayload, { timeout: 2000 })
    .not.toBeNull();
  expect(reparsePayload.ocr_prompt).toContain(
    "Treat the current sheet shown in the editor as the canonical row structure.",
  );
  expect(reparsePayload.ocr_prompt).toContain("The canonical daypart blocks are 朝/昼/夕.");
  expect(reparsePayload.ocr_prompt).toContain(
    "soft_x, regular_bag_x, no_meat_x, no_fish_x, change_1_x, change_2_x",
  );
  expect(reparsePayload.ocr_prompt).not.toContain("Return a JSON object only.");
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
  await page.getByRole("button", { name: "次へ: OCR修正" }).first().click();
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

test("last step primary action still returns to orders when post-confirm detail refresh would fail", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-CONFIRM-RETURN-REFRESH-FAIL-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CF-RTN-FAIL-001",
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
    facility_id: "FAC-CF-RTN-FAIL-001",
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
      if (confirmStarted) {
        await route.fulfill({ status: 500, json: { detail: "refresh_failed_after_confirm" } });
      } else {
        await route.fulfill({ status: 200, json: orderPayload });
      }
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: pdfBody, contentType: "application/pdf" });
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
    if (path.endsWith("/facilities/FAC-CF-RTN-FAIL-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-CF-RTN-FAIL-001",
          name: "Facility Confirm Return Refresh Fail",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CF-RTN-FAIL-001", name: "Facility Confirm Return Refresh Fail" }] },
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
  await page.locator(".step-footer").getByRole("button", { name: "確定して注文一覧へ戻る" }).click();

  await expect.poll(() => confirmStarted, { timeout: 2000 }).toBe(true);
  await page.waitForURL("**/orders");
});

test("last step confirm uses canonical current sheet revision instead of latest OCR revision", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-CONFIRM-CURRENT-REV-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const currentSheetRevisionId = "ODR-current-001";
  const staleOcrRevisionId = "OCRREV-stale-001";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CF-CURR-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    current_sheet_revision_id: currentSheetRevisionId,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    workflow_state: {
      state: "review_required",
      current_sheet_revision_id: currentSheetRevisionId,
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
    facility_id: "FAC-CF-CURR-001",
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
  let confirmPayload: any = null;

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
          current_sheet_revision_id: currentSheetRevisionId,
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
      await route.fulfill({
        status: 200,
        json: {
          latest: {
            revision_id: staleOcrRevisionId,
            edited_at: "2026-03-01T00:00:00Z",
            ui_mode: "sheet",
            rows: [["2026-03-01", "朝", "朝食", "10"]],
          },
          revisions: [
            {
              revision_id: staleOcrRevisionId,
              edited_at: "2026-03-01T00:00:00Z",
              ui_mode: "sheet",
              rows: [["2026-03-01", "朝", "朝食", "10"]],
            },
          ],
        },
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
    if (path.endsWith(`/orders/${orderId}/confirm`)) {
      confirmPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { ok: true } });
      return;
    }
    if (path.endsWith("/facilities/FAC-CF-CURR-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-CF-CURR-001",
          name: "Facility Confirm Current Revision",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CF-CURR-001", name: "Facility Confirm Current Revision" }] },
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
  await page.locator(".step-footer").getByRole("button", { name: "確定して注文一覧へ戻る" }).click();

  await expect.poll(() => confirmPayload, { timeout: 2000 }).not.toBeNull();
  expect(confirmPayload.expected_revision_id).toBe(currentSheetRevisionId);
  expect(confirmPayload.expected_revision_id).not.toBe(staleOcrRevisionId);
  await page.waitForURL("**/orders");
});

test("last step returns to orders without reconfirming when the canonical order is already confirmed", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-CONFIRMED-RETURN-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const currentSheetRevisionId = "ODR-confirmed-001";
  const orderPayload = {
    id: orderId,
    status: "確定",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CF-CONFIRMED-001",
    week: explicitWeekId,
    week_value: explicitWeekId,
    week_label: explicitWeekLabel,
    current_sheet_revision_id: currentSheetRevisionId,
    lines: [],
    lines_updated_at: "2026-01-01T00:00:00Z",
    ocr_status: "completed",
    workflow_state: {
      state: "confirmed",
      current_sheet_revision_id: currentSheetRevisionId,
      candidate_evidence_run_id: null,
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
    facility_id: "FAC-CF-CONFIRMED-001",
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
          state: "confirmed",
          current_sheet_revision_id: currentSheetRevisionId,
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
    if (path.endsWith("/facilities/FAC-CF-CONFIRMED-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-CF-CONFIRMED-001",
          name: "Facility Confirmed Return",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CF-CONFIRMED-001", name: "Facility Confirmed Return" }] },
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

  await expect(page.locator(".step-footer").getByRole("button", { name: "注文一覧へ戻る" })).toBeVisible();
  await expect(page.getByRole("button", { name: "確定済み" })).toBeDisabled();
  await page.locator(".step-footer").getByRole("button", { name: "注文一覧へ戻る" }).click();

  await expect.poll(() => confirmStarted, { timeout: 1000 }).toBe(false);
  await page.waitForURL("**/orders");
});

test("last step primary action does not return to orders when confirm is rejected", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-CONFIRM-RETURN-BLOCKED-001";
  const explicitWeekId = "2026-03@2026-03-01~2026-03-07";
  const explicitWeekLabel = "03/01 - 03/07";
  const orderPayload = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CF-RTN-BLOCK-001",
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
    facility_id: "FAC-CF-RTN-BLOCK-001",
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
      await route.fulfill({
        status: 409,
        json: { detail: { blockers: ["quantity_review_required"], message: "blocked" } },
      });
      return;
    }
    if (path.endsWith("/facilities/FAC-CF-RTN-BLOCK-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-CF-RTN-BLOCK-001",
          name: "Facility Confirm Return Blocked",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CF-RTN-BLOCK-001", name: "Facility Confirm Return Blocked" }] },
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
  await page.locator(".step-footer").getByRole("button", { name: "確定して注文一覧へ戻る" }).click();

  await expect.poll(() => confirmStarted, { timeout: 2000 }).toBe(true);
  await expect(page).toHaveURL(new RegExp(`/orders/${orderId}$`));
  await expect(
    page.getByText(/まだ確定できません。Step2で内容を整えてから再度お試しください:/),
  ).toBeVisible();
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

test("saved sheet context change requires operator choice before rebuilding the skeleton", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-SAVED-CONTEXT-CHOICE-001";
  const currentWeekId = "2026-03@2026-03-01~2026-03-07";
  const targetWeekId = "2026-03@2026-03-08~2026-03-14";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-SCC-001",
    week: currentWeekId,
    week_value: currentWeekId,
    week_label: "03/01 - 03/07",
    persisted_week_value: currentWeekId,
    ocr_has_saved_draft: true,
    ocr_review_state: "draft_saved",
    lines: [],
  };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-SCC-001",
    week_id: currentWeekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
    header: ["日付", "区分", "メニュー", "常食2F", "備考"],
    rows: [["03/01", "朝", "Menu A", "12", "saved"]],
    row_ids: ["saved-row-1"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };
  let savedWeekPayload: any = null;
  let forceWeeklyPayload: any = null;
  let facilitySaveCount = 0;

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
            { week_id: currentWeekId, label: "2026-03 (03/01-03/07)", selected: true },
            { week_id: targetWeekId, label: "2026-03 (03/08-03/14)", selected: false },
          ],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week`) && method === "POST") {
      savedWeekPayload = route.request().postDataJSON();
      orderPayload.week = savedWeekPayload.week;
      orderPayload.week_value = savedWeekPayload.week;
      orderPayload.persisted_week_value = savedWeekPayload.week;
      draftSheetPayload.week_id = savedWeekPayload.week;
      await route.fulfill({ status: 200, json: { updated: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/facility`) && method === "POST") {
      facilitySaveCount += 1;
      await route.fulfill({ status: 200, json: { updated: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet/force-weekly-menu`) && method === "POST") {
      forceWeeklyPayload = route.request().postDataJSON();
      draftSheetPayload.week_id = targetWeekId;
      draftSheetPayload.rows = [["03/08", "朝", "Menu A", "12", "saved"]];
      await route.fulfill({
        status: 200,
        json: {
          ...draftSheetPayload,
          source: "forced_weekly_menu_overwrite",
          warnings: [],
          apply_blockers: [],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({
        status: 200,
        json: {
          ...draftSheetPayload,
          source: path.endsWith("/ocr-sheet") ? "draft_sheet" : draftSheetPayload.source,
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
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
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          state: "draft_saved",
          apply_gate: { can_apply: true, can_confirm: true, blockers: [], warnings: [] },
        },
      });
      return;
    }
    if (path.includes("/shipping-statuses")) {
      await route.fulfill({ status: 200, json: { items: [], summary: null } });
      return;
    }
    if (path.endsWith("/facilities/FAC-SCC-001")) {
      await route.fulfill({
        status: 200,
        json: { id: "FAC-SCC-001", name: "Facility Saved Context", config: null, resolved_config: { fax_template: { columns: [] } } },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({ status: 200, json: { facilities: [{ id: "FAC-SCC-001", name: "Facility Saved Context" }] } });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);

  const weekSelect = page.locator(".field").filter({ hasText: "週 (Step1 必須)" }).locator("select");
  await expect(weekSelect).toHaveValue(currentWeekId);
  await weekSelect.selectOption(targetWeekId);
  await page.getByRole("button", { name: "設定を保存" }).click();

  await expect(page.getByText("保存済みシートあり")).toBeVisible();
  await expect(page.getByRole("button", { name: "数字を保持して切替" })).toBeVisible();
  await expect(page.getByRole("button", { name: "数字をクリアして切替" })).toBeVisible();
  await expect.poll(() => savedWeekPayload).toBeNull();
  await expect.poll(() => forceWeeklyPayload).toBeNull();

  await page.getByRole("button", { name: "数字を保持して切替" }).click();

  await expect.poll(() => savedWeekPayload?.week).toBe(targetWeekId);
  await expect.poll(() => forceWeeklyPayload?.blank_quantities).toBe(false);
  await expect.poll(() => facilitySaveCount).toBe(0);
  await expect(page.getByText("保存済みシートの数量を保ったまま、新しい施設/週の骨格へ切り替えました。")).toBeVisible();
  await expect(page.getByText("保存済みシートあり")).toHaveCount(0);
});

test("ocr sheet editor supports tab navigation and drag-moving a selected range", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-SHEET-EDITOR-001";
  const weekId = "2026-03@2026-03-01~2026-03-07";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-SHEET-001",
    week: weekId,
    week_value: weekId,
    week_label: "03/01 - 03/07",
    lines: [],
  };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-SHEET-001",
    week_id: weekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
    header: ["日付", "区分", "メニュー", "常食2F", "備考"],
    rows: [
      ["03/01", "朝", "Menu A", "1", "memo-a"],
      ["03/01", "昼", "Menu B", "2", "memo-b"],
      ["03/02", "朝", "Menu C", "3", "memo-c"],
    ],
    row_ids: ["row-1", "row-2", "row-3"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
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
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          state: "draft_saved",
          apply_gate: { can_apply: true, can_confirm: true, blockers: [], warnings: [] },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: weekId, label: "2026-03 (03/01-03/07)", selected: true }] },
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
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith("/facilities/FAC-SHEET-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-SHEET-001",
          name: "Facility Sheet Editor",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-SHEET-001", name: "Facility Sheet Editor" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();

  const getCell = (rowIndex: number, cellIndex: number) =>
    page.locator(".ocr-sheet-table tbody tr").nth(rowIndex).locator("input").nth(cellIndex);
  const getHeader = (cellIndex: number) =>
    page.locator(".ocr-sheet-table thead tr th").nth(cellIndex + 1);

  await expect(getCell(0, 3)).toHaveValue("1");
  await getCell(0, 3).click();
  await expect(getCell(0, 3)).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(getCell(0, 4)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(getCell(1, 4)).toBeFocused();
  await page.keyboard.press("Shift+Enter");
  await expect(getCell(0, 4)).toBeFocused();

  await getCell(0, 3).click();
  await page.keyboard.down("Shift");
  await getCell(1, 3).click();
  await page.keyboard.up("Shift");
  await expect(page.locator(".ocr-sheet-cell-selected")).toHaveCount(2);

  await getCell(0, 3).dragTo(getCell(1, 3));

  await expect(getCell(0, 3)).toHaveValue("");
  await expect(getCell(1, 3)).toHaveValue("1");
  await expect(getCell(2, 3)).toHaveValue("2");

  await getCell(1, 3).click();
  await page.keyboard.down("Shift");
  await getCell(2, 3).click();
  await page.keyboard.up("Shift");
  await page.getByRole("button", { name: "コピー", exact: true }).click();
  await getCell(0, 3).click();
  await page.getByRole("button", { name: "貼り付け", exact: true }).click();
  await expect(getCell(0, 3)).toHaveValue("1");
  await expect(getCell(1, 3)).toHaveValue("2");
  await expect(getCell(2, 3)).toHaveValue("2");

  await getCell(0, 3).click();
  await page.keyboard.down("Shift");
  await getCell(1, 3).click();
  await page.keyboard.up("Shift");
  await page.getByRole("button", { name: "クリア", exact: true }).click();
  await expect(getCell(0, 3)).toHaveValue("");
  await expect(getCell(1, 3)).toHaveValue("");

  await getCell(0, 4).click();
  await page.keyboard.press("Shift+ArrowDown");
  await page.keyboard.press("Shift+ArrowDown");
  await page.getByRole("button", { name: "下へコピー", exact: true }).click();
  await expect(getCell(0, 4)).toHaveValue("memo-a");
  await expect(getCell(1, 4)).toHaveValue("memo-a");
  await expect(getCell(2, 4)).toHaveValue("memo-a");

  await getCell(2, 3).click();
  await expect(getCell(2, 3)).toBeFocused();
  await page.keyboard.press("Shift+ArrowRight");
  await expect(page.locator(".ocr-sheet-cell-selected")).toHaveCount(2);
  await page.getByRole("button", { name: "右へコピー", exact: true }).click();
  await expect(getCell(2, 3)).toHaveValue("2");
  await expect(getCell(2, 4)).toHaveValue("2");

  await expect(getHeader(3)).toContainText("常食2F");
  await expect(getHeader(4)).toContainText("備考");
  await expect(page.getByLabel("入替元数量列")).toBeDisabled();
  await expect(page.getByLabel("入替先数量列")).toBeDisabled();
  await expect(page.getByRole("button", { name: "数量列を入替", exact: true })).toBeDisabled();

  await getCell(0, 0).click();
  await page.keyboard.press("ControlOrMeta+A");
  await expect(page.locator(".ocr-sheet-cell-selected")).toHaveCount(15);
});

test("ocr sheet quantity-column tools support bulk fill and layout toggle while preserving save payload", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-QTY-COLUMN-SWAP-001";
  const weekId = "2026-03@2026-03-01~2026-03-07";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-QTY-SHEET-001",
    week: weekId,
    week_value: weekId,
    week_label: "03/01 - 03/07",
    lines: [],
  };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-QTY-SHEET-001",
    week_id: weekId,
    fields: ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.soft_2f", "remarks"],
    header: ["日付", "区分", "メニュー", "常食2F", "軟菜2F", "備考"],
    rows: [
      ["03/01", "朝", "Menu A", "11", "21", "memo-a"],
      ["03/01", "昼", "Menu B", "12", "22", "memo-b"],
    ],
    row_ids: ["row-1", "row-2"],
    source: "edited_sheet_exact",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };
  let savedDraftPayload: any = null;

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
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) && method === "POST") {
      savedDraftPayload = route.request().postDataJSON();
      draftSheetPayload.header = [...(savedDraftPayload.header || [])];
      draftSheetPayload.fields = [...(savedDraftPayload.fields || [])];
      draftSheetPayload.rows = (savedDraftPayload.rows || []).map((row: string[]) => [...row]);
      draftSheetPayload.row_ids = [...(savedDraftPayload.row_ids || [])];
      await route.fulfill({ status: 200, json: { saved: true } });
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
          state: "draft_saved",
          apply_gate: { can_apply: true, can_confirm: true, blockers: [], warnings: [] },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: weekId, label: "2026-03 (03/01-03/07)", selected: true }] },
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
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith("/facilities/FAC-QTY-SHEET-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-QTY-SHEET-001",
          name: "Facility Quantity Swap",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-QTY-SHEET-001", name: "Facility Quantity Swap" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();

  const getCell = (rowIndex: number, cellIndex: number) =>
    page.locator(".ocr-sheet-table tbody tr").nth(rowIndex).locator("input").nth(cellIndex);
  const getHeader = (cellIndex: number) =>
    page.locator(".ocr-sheet-table thead tr th").nth(cellIndex + 1);
  const workspace = page.locator(".ocr-workspace");

  await expect(page.getByRole("tablist", { name: "ocr workspace layout" })).toBeVisible();
  await expect(workspace).not.toHaveClass(/ocr-workspace--vertical/);
  await page.getByRole("button", { name: "上下", exact: true }).click();
  await expect(workspace).toHaveClass(/ocr-workspace--vertical/);
  await page.getByRole("button", { name: "左右", exact: true }).click();
  await expect(workspace).not.toHaveClass(/ocr-workspace--vertical/);

  await expect(getHeader(3)).toContainText("常食2F");
  await expect(getHeader(4)).toContainText("軟菜2F");
  await expect(getCell(0, 2)).toHaveValue("Menu A");
  await expect(getCell(0, 3)).toHaveValue("11");
  await expect(getCell(0, 4)).toHaveValue("21");
  await expect(getCell(0, 5)).toHaveValue("memo-a");

  const quantityOptions = await page.getByLabel("入替元数量列").locator("option").allTextContents();
  expect(quantityOptions).toContain("4: 常食2F");
  expect(quantityOptions).toContain("5: 軟菜2F");
  expect(quantityOptions).not.toContain("1: 日付");
  expect(quantityOptions).not.toContain("6: 備考");
  const bulkFillOptions = await page.getByLabel("数量列一括入力の対象列").locator("option").allTextContents();
  expect(bulkFillOptions).toContain("4: 常食2F");
  expect(bulkFillOptions).toContain("5: 軟菜2F");
  expect(bulkFillOptions).not.toContain("1: 日付");
  expect(bulkFillOptions).not.toContain("6: 備考");

  await page.getByLabel("入替元数量列").selectOption("3");
  await page.getByLabel("入替先数量列").selectOption("4");
  await page.getByRole("button", { name: "数量列を入替", exact: true }).click();

  await expect(getHeader(3)).toContainText("常食2F");
  await expect(getHeader(4)).toContainText("軟菜2F");
  await expect(getCell(0, 2)).toHaveValue("Menu A");
  await expect(getCell(0, 3)).toHaveValue("21");
  await expect(getCell(0, 4)).toHaveValue("11");
  await expect(getCell(0, 5)).toHaveValue("memo-a");

  await page.getByLabel("数量列一括入力の対象列").selectOption("3");
  await page.getByLabel("数量列一括入力の値").fill("77");
  await page.getByRole("button", { name: "列全体へ入力", exact: true }).click();
  await expect(getCell(0, 3)).toHaveValue("77");
  await expect(getCell(1, 3)).toHaveValue("77");
  await expect(getCell(0, 4)).toHaveValue("11");
  await expect(getCell(1, 4)).toHaveValue("12");
  await expect(getCell(0, 5)).toHaveValue("memo-a");

  await page.getByRole("button", { name: "シートを保存（暫定）", exact: true }).click();
  await expect.poll(() => savedDraftPayload?.header?.[3]).toBe("常食2F");
  await expect.poll(() => savedDraftPayload?.header?.[4]).toBe("軟菜2F");
  await expect.poll(() => savedDraftPayload?.fields?.[3]).toBe("qty.regular_2f");
  await expect.poll(() => savedDraftPayload?.fields?.[4]).toBe("qty.soft_2f");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[3]).toBe("77");
  await expect.poll(() => savedDraftPayload?.rows?.[1]?.[3]).toBe("77");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[4]).toBe("11");
  await expect.poll(() => savedDraftPayload?.rows?.[1]?.[4]).toBe("12");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[2]).toBe("Menu A");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[5]).toBe("memo-a");
});

test("ocr sheet confidence threshold supports bulk and row proposal adoption without leaking overlay data into save/apply/confirm payloads", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-OCR-CONFIDENCE-001";
  const weekId = "2026-03@2026-03-01~2026-03-07";
  let currentOrderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CONFIDENCE-001",
    week: weekId,
    week_value: weekId,
    week_label: "03/01 - 03/07",
    lines: [],
    workflow_state: {
      state: "review_required",
      candidate_prompt_visible: false,
      candidate_evidence_run_id: null,
      acknowledged_candidate_evidence_run_id: null,
      active_evidence_run_id: "EVD-CONFIDENCE-001",
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
    ocr_can_apply_draft: true,
    ocr_can_confirm: true,
  };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-CONFIDENCE-001",
    week_id: weekId,
    fields: [
      "date_mmdd",
      "daypart",
      "menu",
      "qty.regular_x",
      "qty.change_1_x",
      "qty.regular_bag_x",
      "qty.soft_x",
      "remarks",
    ],
    header: ["日付", "区分", "メニュー", "常食1回目", "常食2回目", "常食袋分け", "軟菜", "備考欄"],
    rows: [["03/01", "朝", "Menu A", "11", "", "", "", "memo-a"]],
    row_ids: ["row-1"],
    cell_confidence_rows: [["", "", "", "high", "", "", "", ""]],
    cell_provenance_rows: [["", "", "", "ocr_payload_exact", "", "", "", ""]],
    ocr_numeric_cell_items: [
      {
        classification: "accepted",
        value: "11",
        confidence_tier: "high",
        placement_basis: "weekly_menu_exact",
        read_basis: "direct_payload_numeric_cell",
        source_row_index: 0,
        source_col_index: 3,
        target_row_index: 0,
        target_col_index: 3,
      },
      {
        classification: "deterministic_candidate",
        value: "12",
        confidence_tier: "medium",
        placement_basis: "date_menu_unique",
        read_basis: "direct_payload_numeric_cell",
        source_row_index: 0,
        source_col_index: 4,
        target_row_index: 0,
        target_col_index: 4,
      },
      {
        classification: "weak_candidate",
        value: "13",
        confidence_tier: "low",
        placement_basis: "date_group_blank_unique",
        read_basis: "direct_payload_numeric_cell",
        source_row_index: 0,
        source_col_index: 5,
        target_row_index: 0,
        target_col_index: 5,
      },
      {
        classification: "unresolved",
        value: "14",
        confidence_tier: "low",
        placement_basis: "unresolved",
        read_basis: "direct_payload_numeric_cell",
        source_row_index: 0,
        source_col_index: 6,
        reason: "target_not_proven",
      },
    ],
    ocr_numeric_cell_summary: {
      raw_ocr_numeric_count: 4,
      accepted_count: 1,
      deterministic_candidate_count: 1,
      weak_candidate_count: 1,
      unresolved_count: 1,
    },
    source: "weekly_menu+ocr_payload",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
  };
  let savedDraftPayload: any = null;
  let applyPayload: any = null;
  let confirmPayload: any = null;

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
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) && method === "POST") {
      savedDraftPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { saved: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/ocr-apply`) && method === "POST") {
      applyPayload = route.request().postDataJSON();
      currentOrderPayload = {
        ...currentOrderPayload,
        workflow_state: {
          ...currentOrderPayload.workflow_state,
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
      await route.fulfill({ status: 200, json: { ok: true } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/confirm`) && method === "POST") {
      confirmPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { ok: true } });
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
          ...currentOrderPayload.workflow_state,
          current_sheet_revision_id: "OCRREV-CONFIDENCE-001",
          apply_gate: { can_apply: true, can_confirm: true, blockers: [], warnings: [] },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: weekId, label: "2026-03 (03/01-03/07)", selected: true }] },
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
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith("/facilities/FAC-CONFIDENCE-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-CONFIDENCE-001",
          name: "Facility Confidence",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CONFIDENCE-001", name: "Facility Confidence" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();

  const row = page.locator(".ocr-sheet-table tbody tr").nth(0);
  const regularCell = row.locator("td").nth(3);
  const secondRoundCell = row.locator("td").nth(4);
  const bagCell = row.locator("td").nth(5);
  const overlaySummary = page.getByTestId("ocr-sheet-overlay-summary");
  const overlayValues = page.getByTestId("ocr-sheet-overlay-value");

  await expect(page.getByLabel("OCR信頼度表示閾値")).toHaveValue("suggestion");
  await expect(page.locator(".ocr-confidence-toolbar")).toContainText("raw 4 / accepted 1 / deterministic 1 / weak 1 / unresolved 1");
  await expect(regularCell).toHaveClass(/ocr-sheet-cell-confidence-high/);
  await expect(secondRoundCell.locator("input")).toHaveValue("");
  await expect(bagCell.locator("input")).toHaveValue("");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "3");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "2");
  await expect(overlayValues).toHaveCount(2);
  await expect(overlayValues.nth(0)).toContainText("12");
  await expect(overlayValues.nth(1)).toContainText("13");
  expect(Math.round((await row.boundingBox())?.height || 0)).toBeLessThanOrEqual(44);

  await secondRoundCell.locator("input").fill("99");
  await expect(secondRoundCell.locator("input")).toHaveValue("99");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "3");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "1");
  await expect(overlayValues).toHaveCount(1);
  await expect(overlayValues.nth(0)).toContainText("13");

  await page.getByLabel("OCR信頼度表示閾値").selectOption("assisted");
  await expect(secondRoundCell.locator("input")).toHaveValue("99");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "2");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "0");
  await expect(overlayValues).toHaveCount(0);

  await page.getByLabel("OCR信頼度表示閾値").selectOption("strict");
  await expect(secondRoundCell.locator("input")).toHaveValue("99");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "2");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "0");
  await expect(overlayValues).toHaveCount(0);

  await page.getByLabel("OCR信頼度表示閾値").selectOption("suggestion");
  await expect(secondRoundCell.locator("input")).toHaveValue("99");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "3");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "1");
  await expect(overlayValues).toHaveCount(1);
  await expect(overlayValues.nth(0)).toContainText("13");

  await secondRoundCell.locator("input").fill("");
  await expect(secondRoundCell.locator("input")).toHaveValue("");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "3");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "2");
  await expect(overlayValues).toHaveCount(2);
  await expect(overlayValues.nth(0)).toContainText("12");
  await expect(overlayValues.nth(1)).toContainText("13");

  await page.getByLabel("OCR信頼度表示閾値").selectOption("assisted");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "2");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "1");
  await expect(overlayValues).toHaveCount(1);
  await expect(overlayValues.nth(0)).toContainText("12");

  await page.getByRole("button", { name: "表示中提案を採用", exact: true }).click();
  await expect(secondRoundCell.locator("input")).toHaveValue("12");
  await expect(bagCell.locator("input")).toHaveValue("");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "2");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "0");
  await expect(overlayValues).toHaveCount(0);

  await page.getByLabel("OCR信頼度表示閾値").selectOption("suggestion");
  await expect(secondRoundCell.locator("input")).toHaveValue("12");
  await expect(bagCell.locator("input")).toHaveValue("");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "3");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "1");
  await expect(overlayValues).toHaveCount(1);
  await expect(overlayValues.nth(0)).toContainText("13");

  await row.getByRole("button", { name: "提案採用", exact: true }).click();
  await expect(secondRoundCell.locator("input")).toHaveValue("12");
  await expect(bagCell.locator("input")).toHaveValue("13");
  await expect(overlaySummary).toHaveAttribute("data-visible-count", "3");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "0");
  await expect(overlayValues).toHaveCount(0);

  await page.getByRole("button", { name: /はい .* 修正済み/ }).click();
  await page.getByRole("button", { name: "修正完了 / 保存して明細に反映して次へ" }).click();
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[3]).toBe("11");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[4]).toBe("12");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[5]).toBe("13");
  await expect.poll(() => savedDraftPayload?.rows?.[0]?.[6]).toBe("");
  expect(savedDraftPayload).not.toHaveProperty("cell_confidence_rows");
  expect(savedDraftPayload).not.toHaveProperty("cell_provenance_rows");
  expect(savedDraftPayload).not.toHaveProperty("ocr_numeric_cell_items");
  expect(savedDraftPayload).not.toHaveProperty("ocr_numeric_cell_summary");
  await expect.poll(() => applyPayload?.rows?.[0]?.[3]).toBe("11");
  await expect.poll(() => applyPayload?.rows?.[0]?.[4]).toBe("12");
  await expect.poll(() => applyPayload?.rows?.[0]?.[5]).toBe("13");
  expect(applyPayload).not.toHaveProperty("cell_confidence_rows");
  expect(applyPayload).not.toHaveProperty("cell_provenance_rows");
  expect(applyPayload).not.toHaveProperty("ocr_numeric_cell_items");
  expect(applyPayload).not.toHaveProperty("ocr_numeric_cell_summary");

  await page.locator(".step-tab").filter({ hasText: "出力" }).click();
  await page.locator(".panel").filter({ has: page.getByRole("heading", { name: "出力" }) }).getByRole("button", { name: "確定", exact: true }).click();
  await expect.poll(() => confirmPayload?.expected_revision_id).toBe(savedDraftPayload.expected_revision_id);
  expect(confirmPayload).toEqual({
    expected_revision_id: savedDraftPayload.expected_revision_id,
    expected_lines_updated_at: savedDraftPayload.expected_lines_updated_at,
  });
});

test("structural Step2 row edits invalidate overlay candidates instead of keeping stale row alignment", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-OCR-CONFIDENCE-STRUCT-001";
  const weekId = "2026-03@2026-03-01~2026-03-07";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-CONFIDENCE-001",
    week: weekId,
    week_value: weekId,
    week_label: "03/01 - 03/07",
    lines: [],
  };
  const draftSheetPayload: any = {
    order_id: orderId,
    facility_id: "FAC-CONFIDENCE-001",
    week_id: weekId,
    fields: [
      "date_mmdd",
      "daypart",
      "menu",
      "qty.regular_x",
      "qty.change_1_x",
      "qty.regular_bag_x",
      "qty.soft_x",
      "remarks",
    ],
    header: ["日付", "区分", "メニュー", "常食1回目", "常食2回目", "常食袋分け", "軟菜", "備考欄"],
    rows: [["03/01", "朝", "Menu A", "11", "", "", "", "memo-a"]],
    row_ids: ["row-1"],
    cell_confidence_rows: [["", "", "", "high", "", "", "", ""]],
    cell_provenance_rows: [["", "", "", "ocr_payload_exact", "", "", "", ""]],
    ocr_numeric_cell_items: [
      {
        classification: "accepted",
        value: "11",
        confidence_tier: "high",
        placement_basis: "weekly_menu_exact",
        read_basis: "direct_payload_numeric_cell",
        source_row_index: 0,
        source_col_index: 3,
        target_row_index: 0,
        target_col_index: 3,
      },
      {
        classification: "deterministic_candidate",
        value: "12",
        confidence_tier: "medium",
        placement_basis: "date_menu_unique",
        read_basis: "direct_payload_numeric_cell",
        source_row_index: 0,
        source_col_index: 4,
        target_row_index: 0,
        target_col_index: 4,
      },
    ],
    ocr_numeric_cell_summary: {
      raw_ocr_numeric_count: 2,
      accepted_count: 1,
      deterministic_candidate_count: 1,
      weak_candidate_count: 0,
      unresolved_count: 0,
    },
    source: "weekly_menu+ocr_payload",
    can_apply: true,
    can_confirm: true,
    warnings: [],
    apply_blockers: [],
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
    if (path.endsWith(`/orders/${orderId}/draft-sheet`) || path.endsWith(`/orders/${orderId}/ocr-sheet`)) {
      await route.fulfill({ status: 200, json: draftSheetPayload });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-state`)) {
      await route.fulfill({
        status: 200,
        json: {
          state: "draft_saved",
          current_sheet_revision_id: "OCRREV-CONFIDENCE-STRUCT-001",
          apply_gate: { can_apply: true, can_confirm: true, blockers: [], warnings: [] },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`)) {
      await route.fulfill({
        status: 200,
        json: { options: [{ week_id: weekId, label: "2026-03 (03/01-03/07)", selected: true }] },
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
    if (path.endsWith(`/orders/${orderId}/document`)) {
      await route.fulfill({ status: 200, body: "%PDF-1.4", contentType: "application/pdf" });
      return;
    }
    if (path.endsWith("/facilities/FAC-CONFIDENCE-001")) {
      await route.fulfill({
        status: 200,
        json: {
          id: "FAC-CONFIDENCE-001",
          name: "Facility Confidence",
          config: null,
          resolved_config: { fax_template: { columns: [] } },
        },
      });
      return;
    }
    if (path.endsWith("/facilities")) {
      await route.fulfill({
        status: 200,
        json: { facilities: [{ id: "FAC-CONFIDENCE-001", name: "Facility Confidence" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await page.locator(".step-tab").filter({ hasText: "OCR修正" }).click();

  const overlaySummary = page.getByTestId("ocr-sheet-overlay-summary");
  const overlayValues = page.getByTestId("ocr-sheet-overlay-value");

  await expect(page.getByLabel("OCR信頼度表示閾値")).toHaveValue("suggestion");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "1");
  await expect(overlayValues).toHaveCount(1);
  expect(Math.round((await page.locator(".ocr-sheet-table tbody tr").nth(0).boundingBox())?.height || 0)).toBeLessThanOrEqual(44);

  await page.getByRole("button", { name: "複製", exact: true }).first().click();

  await expect(overlaySummary).toHaveAttribute("data-raw-count", "0");
  await expect(overlaySummary).toHaveAttribute("data-visible-overlay-count", "0");
  await expect(overlayValues).toHaveCount(0);
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

test("step1 keeps the explicit current week when week options still point at the default week", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-WEEK-EXPLICIT-CURRENT-001";
  const explicitWeekId = "2026-04@2026-04-26~2026-04-30";
  const defaultWeekId = "2026-04@2026-04-26~2026-05-02";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: "2026-04",
    week_value: explicitWeekId,
    persisted_week_value: explicitWeekId,
    lines: [],
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
    if (path.endsWith(`/orders/${orderId}/week-options`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          options: [
            { week_id: defaultWeekId, label: "2026-04 (04/26-05/02)", selected: true },
          ],
        },
      });
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

  const weekSelect = page.locator(".field").filter({ hasText: "週 (Step1 必須)" }).locator("select");

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await expect(weekSelect).toHaveValue(explicitWeekId);
  await expect(weekSelect.locator(`option[value="${explicitWeekId}"]`)).toContainText("(現在値)");

  await page.goto(`${baseUrl}/orders`);
  await page.goto(`${baseUrl}/orders/${orderId}`);
  await expect(weekSelect).toBeVisible();
  await expect(weekSelect).toHaveValue(explicitWeekId);
  await expect(weekSelect.locator(`option[value="${explicitWeekId}"]`)).toContainText("(現在値)");
});

test("step1 save keeps the explicit exception week through post-save refresh and revisit", async ({ page }) => {
  const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
  const orderId = "ORD-E2E-WEEK-SAVE-REFRESH-001";
  const explicitWeekId = "2026-04@2026-04-26~2026-04-30";
  const defaultWeekId = "2026-04@2026-04-26~2026-05-02";
  const orderPayload: any = {
    id: orderId,
    status: "要確認",
    document: "file:///tmp/order.pdf",
    facility: "FAC-001",
    week: "2026-04",
    week_value: defaultWeekId,
    persisted_week_value: defaultWeekId,
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
            { week_id: defaultWeekId, label: "2026-04 (04/26-05/02)", selected: true },
            { week_id: explicitWeekId, label: "2026-04 (04/26-04/30)", selected: false },
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
          resolved_week_id: orderPayload.week_value,
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

  const weekSelect = page.locator(".field").filter({ hasText: "週 (Step1 必須)" }).locator("select");

  await page.goto(`${baseUrl}/orders/${orderId}`);
  await expect(page.getByRole("heading", { name: "注文詳細" })).toBeVisible();
  await expect(weekSelect).toHaveValue(defaultWeekId);

  await page.locator('input[type="date"]').nth(0).fill("2026-04-26");
  await page.locator('input[type="date"]').nth(1).fill("2026-04-30");
  await page.getByRole("button", { name: "例外範囲を設定" }).click();
  await expect(weekSelect).toHaveValue(explicitWeekId);
  await page.getByRole("button", { name: "設定を保存" }).click();

  await expect.poll(() => savedWeekPayload?.week).toBe(explicitWeekId);
  await expect(page.getByText("施設と週を設定しました。")).toBeVisible();
  await expect(weekSelect).toHaveValue(explicitWeekId);

  await page.goto(`${baseUrl}/orders`);
  await page.goto(`${baseUrl}/orders/${orderId}`);
  await expect(weekSelect).toBeVisible();
  await expect(weekSelect).toHaveValue(explicitWeekId);
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
  const marker = page.getByTestId("ocr-overlay-row-marker");
  await expect(highlight).toBeVisible();
  await expect(marker).toBeVisible();
  await expect(highlight).toHaveAttribute("data-overlay-page", "2");
  await expect(highlight).toHaveAttribute("data-overlay-row", "1");
  await expect(marker).toHaveAttribute("data-overlay-page", "2");
  await expect(marker).toHaveAttribute("data-overlay-row", "1");
  await expect(marker).toHaveAttribute("data-marker-side", "ocr");
  const highlightBox = await highlight.boundingBox();
  const markerBox = await marker.boundingBox();
  expect(highlightBox).not.toBeNull();
  expect(markerBox).not.toBeNull();
  expect(markerBox!.x).toBeLessThan(highlightBox!.x + highlightBox!.width * 0.25);
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
  const marker = page.getByTestId("ocr-overlay-row-marker");
  await expect(highlight).toBeVisible();
  await expect(marker).toBeVisible();
  await expect(highlight).toHaveAttribute("data-overlay-page", "2");
  await expect(highlight).toHaveAttribute("data-overlay-row", "1");
  await expect(marker).toHaveAttribute("data-overlay-page", "2");
  await expect(marker).toHaveAttribute("data-overlay-row", "1");
  await expect(marker).toHaveAttribute("data-marker-side", "ocr");
  const highlightBox = await highlight.boundingBox();
  const markerBox = await marker.boundingBox();
  expect(highlightBox).not.toBeNull();
  expect(markerBox).not.toBeNull();
  expect(markerBox!.x).toBeLessThan(highlightBox!.x + highlightBox!.width * 0.25);
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
