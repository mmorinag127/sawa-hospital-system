import { expect, type Page } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";

export type WorkflowMountOptions = {
  orderId?: string;
  documentId?: string;
  originalPdf?: Buffer;
  contextSuggestion?: Record<string, unknown>;
  facilitiesStatus?: number;
  facilities?: Array<{ id: string; name: string }>;
  workflowFacilityId?: string;
  workflowRevision?: number;
  beforeInspectionRead?: () => Promise<void>;
  workflowSource?: string;
  workflowState?: string;
  polledWorkflow?: Partial<Record<string, unknown>>;
  contextSaveStatus?: number;
  workflow?: Record<string, unknown>;
  ocrResults?: Array<Record<string, unknown>>;
  polledOcrResults?: Array<Record<string, unknown>>;
  entryPath?: "legacy";
  documentStatus?: 404 | 500;
  targetCells?: Array<{ target_row_index: number; target_col_index: number; field: string; bbox: number[] }>;
};

export async function mountWorkflowV2SheetPage(page: Page, options: WorkflowMountOptions = {}) {
  const orderId = options.orderId ?? "ORD-E2E-WORKFLOW-SHEET";
  const documentId = options.documentId ?? "DOC-E2E-001";
  const sheetPayload = {
    fields: ["date_mmdd", "daypart", "menu", "qty.regular", "qty.soft"],
    header: ["日付", "区分", "メニュー", "常食", "軟菜"],
    rows: [
      ["05/22", "朝", "Menu A", "1", "2"],
      ["05/22", "昼", "Menu B", "3", "4"],
      ["05/22", "夕", "Menu C", "5", "6"],
    ],
    row_ids: ["row-1", "row-2", "row-3"],
    target_cell_map: options.targetCells ?? [],
    ocr_numeric_cell_summary: {},
    base_evidence_run_id: "OCR-E2E-001",
    template_version_id: "TEMPLATE-E2E-001",
  };
  const facilityConfig = {
    fax_template_id: "TEMPLATE-E2E-001", fax_template_ids: ["TEMPLATE-E2E-001"],
    facility_template_id: "FACILITY-TEMPLATE-E2E-001",
    fax_template: { columns: sheetPayload.fields.map((field, index) => ({
      index, source_index: index, header: sheetPayload.header[index], name: field,
      role: field.startsWith("qty.") ? "quantity" : field === "date_mmdd" ? "date" : field === "menu" ? "menu_name" : field,
      ...(field.startsWith("qty.") ? { diet_type: field.slice(4) } : {}),
    })) },
  };
  const workflowPayload = {
    order_id: orderId,
    revision: options.workflowRevision ?? 7,
    source: options.workflowSource ?? "workflow_v2",
    state: options.workflowState ?? "ocr_selected",
    headline: "正解OCRを選択済みです。シートを編集してください。",
    selected_ocr_result_id: "OCR-E2E-001",
    saved_sheet_id: "SHEET-E2E-001",
    bagging_result_id: null as string | null,
    output_bundle_id: null as string | null,
    facility_id: options.workflowFacilityId ?? "FAC-E2E-001",
    week_start: "2026-05-17",
    week_end: "2026-05-23",
    template_version_id: "TEMPLATE-E2E-001",
    menu_amount_resolution: { status: "resolved", affected_lines: [] },
    ocr_job: options.workflowState === "ocr_running" ? {
      ocr_job_id: "OCR-E2E-RECORDED", order_id: orderId,
      workflow_attempt_id: "ATTEMPT-E2E-RECORDED", status: "running",
    } : null,
    context_suggestion: options.contextSuggestion,
    pre_save_checks: {
      anomaly_review: { confirmed: true },
      sheet_review: { confirmed: true },
    },
    ...options.workflow,
  };
  const ocrResults = options.ocrResults ?? [{ ocr_result_id: "OCR-E2E-001", selected: true,
    selectable: true, detail_state: "loaded",
    overlay_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAI0lEQVR4nGP8//8/AymAiSTVDKMaiANMRKqDg1ENxACSQwkAVW0DHeN02ZEAAAAASUVORK5CYII=",
    sheet_review_base_url: "" }];
  let savePayload: any = null;
  let nextWorkflowRevision = workflowPayload.revision;
  const requestCounts: Record<string, number> = {};
  const workflowMutationHeaders: Array<{ path: string; ifMatch: string | undefined }> = [];
  let progressRead = false;
  const currentResults = () => progressRead && options.polledOcrResults ? options.polledOcrResults : ocrResults;
  const currentWorkflow = () => progressRead && options.polledWorkflow
    ? { ...workflowPayload, ...options.polledWorkflow } : workflowPayload;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_header", "Bearer e2e-token");
    window.sessionStorage.setItem("auth_header", "Bearer e2e-token");
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method().toUpperCase();
    requestCounts[`${method} ${path}`] = (requestCounts[`${method} ${path}`] ?? 0) + 1;

    if (path.endsWith("/outputs/history") && method === "GET") {
      const current = currentWorkflow();
      expect(url.searchParams.get("order_id")).toBe(orderId);
      const expectedRevision = Number(url.searchParams.get("expected_workflow_revision"));
      expect(Number.isInteger(expectedRevision) && expectedRevision > 0).toBe(true);
      expect(url.searchParams.get("page")).toBe("1");
      expect(url.searchParams.get("limit")).toBe("25");
      if (expectedRevision !== current.revision) {
        await route.fulfill({ status: 409, json: { detail: "output_history_workflow_revision_mismatch" } });
        return;
      }
      expect(current.bagging_result_id).toBe("BAG-E2E-001");
      await route.fulfill({ status: 200, json: { order_id: orderId, workflow_revision: current.revision,
        current_output_bundle_id: current.output_bundle_id, page: 1, limit: 25, total: 1,
        entries: [{ order_id: orderId, output_bundle_id: current.output_bundle_id,
          source_saved_sheet_id: current.saved_sheet_id, source_bagging_result_id: current.bagging_result_id,
          template_version_id: current.template_version_id, created_at: "2026-05-22T00:00:00Z",
          current: true, validation: "validated", blocked_reasons: [] }] } });
      return;
    }

    if (path.endsWith(`/orders/${orderId}/workflow-v2`) && method === "GET") {
      await route.fulfill({ status: 200, json: currentWorkflow() });
      return;
    }

    if (path.endsWith(`/orders/${orderId}/document`) && method === "GET") {
      expect(url.searchParams.get("document_id")).toBe(documentId);
      if (options.originalPdf) {
        expect(options.originalPdf.subarray(0, 5).toString()).toBe("%PDF-");
        await route.fulfill({ status: 200, contentType: "application/pdf", body: options.originalPdf });
      } else await route.fulfill({ status: options.documentStatus ?? 404, json: { detail: "Synthetic original PDF unavailable" } });
      return;
    }

    if (path.endsWith(`/orders/${orderId}/workflow-v2/ocr-progress`) && method === "GET") {
      progressRead = true;
      const current = currentWorkflow();
      await route.fulfill({ status: 200, json: {
        order_id: orderId, workflow_revision: current.revision, state: current.state,
        ocr_job: current.ocr_job, common_job_status: null,
      } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/facility-config`) && method === "GET") {
      const current = currentWorkflow();
      await route.fulfill({ status: 200, json: {
        source: "canonical_json", order_id: orderId, facility_id: current.facility_id,
        week_start: current.week_start, week_end: current.week_end,
        template_version_id: current.template_version_id, config: facilityConfig,
      } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/ocr-results`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          results: currentResults(),
        },
      });
      return;
    }
    if (path.includes(`/orders/${orderId}/workflow-v2/inspection-detail/`) && method === "GET") {
      const id = path.slice(path.lastIndexOf("/") + 1);
      const detail = currentResults().find((row) => row.ocr_result_id === id);
      await route.fulfill({status: detail ? 200 : 404, json: detail ? {...detail, workflow_revision: currentWorkflow().revision} : {detail: "ocr_result_not_found"}});
      return;
    }
    if ((path.endsWith(`/orders/${orderId}/workflow-v2/inspection`) || path.endsWith(`/orders/${orderId}/workflow-v2/display`)) && method === "GET") {
      await options.beforeInspectionRead?.();
      const scope = url.searchParams.get("scope") || "inspection";
      await route.fulfill({
        status: 200,
        json: {
          display_scope: scope,
          loaded_sections: scope === "summary" ? [] : scope === "sheet" ? ["ocr", "sheet"] : [scope],
          ocr_result_count: currentResults().length,
          order_header: {
            id: orderId,
            version_count: 1,
            current_version: { document_id: documentId, is_current: true },
            versions: [{ document_id: documentId, is_current: true }],
          },
          workflow: currentWorkflow(),
          ocr_candidates: scope === "summary" || scope === "output" ? undefined : {
            order_id: orderId, workflow_state: currentWorkflow().state,
            blockers: [], selected_ocr_result_id: currentWorkflow().selected_ocr_result_id,
            candidate_template_version_id: "TEMPLATE-E2E-001", hidden_template_mismatch_result_count: 0,
            results: currentResults().map((row) => ({ocr_result_id: row.ocr_result_id, selected: row.selected,
              status: row.status, source: row.source, created_at: row.created_at, detail_state: "not_loaded"})),
          },
          saved_sheet: currentWorkflow().saved_sheet_id && currentWorkflow().selected_ocr_result_id ? {
            saved_sheet_id: currentWorkflow().saved_sheet_id,
            source_ocr_result_id: currentWorkflow().selected_ocr_result_id,
            sheet: scope === "sheet" || scope === "inspection" ? sheetPayload : undefined,
            edited_at: "2026-05-22T00:00:00Z",
          } : null,
          pre_save_status: {
            anomaly_review_confirmed: true,
            sheet_review_confirmed: true,
          },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet`) && method === "PUT") {
      savePayload = route.request().postDataJSON();
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      Object.assign(sheetPayload, savePayload.sheet);
      Object.assign(workflowPayload, { revision: nextWorkflowRevision, saved_sheet_id: "SHEET-E2E-002" });
      await route.fulfill({ status: 200, json: { workflow: workflowPayload, saved_sheet_id: "SHEET-E2E-002" } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/context`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      if (options.contextSaveStatus === 409) {
        await route.fulfill({ status: 409, json: { detail: "workflow revision conflict" } });
        return;
      }
      nextWorkflowRevision += 1;
      Object.assign(workflowPayload, route.request().postDataJSON(), { revision: nextWorkflowRevision });
      await route.fulfill({ status: 200, json: { workflow: workflowPayload } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/anomaly-review`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      workflowPayload.revision = nextWorkflowRevision;
      workflowPayload.pre_save_checks = { anomaly_review: { confirmed: true }, sheet_review: { confirmed: false } };
      await route.fulfill({ status: 200, json: { workflow: workflowPayload, anomaly_review: {},
        pre_save_checks: workflowPayload.pre_save_checks,
        pre_save_status: { anomaly_review_confirmed: true, sheet_review_confirmed: false } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/review-confirm`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      workflowPayload.revision = nextWorkflowRevision;
      workflowPayload.pre_save_checks = { anomaly_review: { confirmed: true }, sheet_review: { confirmed: true } };
      await route.fulfill({ status: 200, json: { workflow: workflowPayload, pre_save_checks: workflowPayload.pre_save_checks,
        pre_save_status: { anomaly_review_confirmed: true, sheet_review_confirmed: true } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/auto-edit`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      nextWorkflowRevision += 1;
      workflowPayload.revision = nextWorkflowRevision;
      await route.fulfill({ status: 200, json: { workflow: workflowPayload, job: { job_id: "AUTO-E2E-001", status: "running" } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/sheet/auto-edit/AUTO-E2E-001`) && method === "GET") {
      nextWorkflowRevision = Math.max(nextWorkflowRevision, 9);
      workflowPayload.revision = nextWorkflowRevision;
      await route.fulfill({ status: 200, json: { job: { job_id: "AUTO-E2E-001", status: "done", workflow_result_revision: 9 }, result: { status: "done", patches: [] } } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/quad-review`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          order_id: orderId,
          source_receipt: { workflow_revision: currentWorkflow().revision, geometry_source_digest: "a".repeat(64),
            input_digest: "b".repeat(64), configured_input_digest: "c".repeat(64), selected_version_input: null,
            order_input_identity: { facility_code: currentWorkflow().facility_id, week_code: "2026-05@2026-05-17~2026-05-23" },
            selected_pdf_receipt: { order_id: orderId, document_id: documentId, sha256: "d".repeat(64),
              storage_uri: "gs://synthetic-browser-only/fax.pdf", generation: "1" } },
          suggested_quad_px: [[1, 1], [20, 1], [20, 30], [1, 30]],
          estimate: { status: "ok", refined_quad_px: [[1, 1], [20, 1], [20, 30], [1, 30]] },
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/quad-review`) && method === "PUT") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      const receipt = route.request().postDataJSON().source_receipt;
      expect(receipt.selected_pdf_receipt.document_id).toBe(documentId);
      expect(receipt.workflow_revision).toBe(workflowPayload.revision);
      nextWorkflowRevision += 1;
      workflowPayload.revision = nextWorkflowRevision;
      await route.fulfill({ status: 200, json: { workflow: workflowPayload } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/workflow-v2/ocr-runs`) && method === "POST") {
      workflowMutationHeaders.push({ path, ifMatch: route.request().headers()["if-match"] });
      expect(route.request().postDataJSON().document_id).toBe(documentId);
      nextWorkflowRevision += 1;
      Object.assign(workflowPayload, { revision: nextWorkflowRevision, state: "ocr_running",
        selected_ocr_result_id: null, saved_sheet_id: null,
        ocr_job: { order_id: orderId, ocr_job_id: "OCR-E2E-RECORDED", workflow_attempt_id: "ATTEMPT-E2E-RECORDED", status: "running" } });
      await route.fulfill({ status: 200, json: { workflow: workflowPayload } });
      return;
    }
    if (path.endsWith(`/orders/${orderId}`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          id: orderId,
          version_count: 1,
          current_version: { document_id: documentId, is_current: true },
          versions: [{ document_id: documentId, is_current: true }],
        },
      });
      return;
    }
    if (path.endsWith(`/orders/${orderId}/week-options`) && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          options: [
            {
              week_id: "2026-05@2026-05-17~2026-05-23",
              label: "05/17 - 05/23",
              selected: true,
            },
          ],
        },
      });
      return;
    }
    if (path.startsWith("/api/facilities/") && path.endsWith("/resolved") && method === "GET") {
      await route.fulfill({
        status: 200,
        json: {
          source: "canonical_json",
          facility_id: path.split("/").at(-2),
          effective_date: url.searchParams.get("effective_date"),
          version: { version_id: "authored-e2e-version" },
          config: facilityConfig,
        },
      });
      return;
    }
    if (path.endsWith("/facilities") && method === "GET") {
      if (options.facilitiesStatus && options.facilitiesStatus !== 200) {
        await route.fulfill({ status: options.facilitiesStatus, json: { detail: "facility source unavailable" } });
        return;
      }
      await route.fulfill({
        status: 200,
        json: { source: "canonical_json", revision: "workflow-facilities-1", facilities: options.facilities ?? [{ id: "FAC-E2E-001", name: "Workflow Sheet E2E Facility" }] },
      });
      return;
    }

    await route.fulfill({ status: 200, json: {} });
  });

  await page.goto(`${baseUrl}/orders/${orderId}${options.entryPath === "legacy" ? "" : "/workflow-v2"}`);
  if (options.workflow?.output_bundle_id) await expect(page.getByRole("region", { name: "出力生成記録", exact: true })).toBeVisible();
  return {
    orderId,
    requestCounts,
    getSavePayload: () => savePayload,
    getWorkflowMutationHeaders: () => workflowMutationHeaders,
    workflow: workflowPayload,
    sheet: sheetPayload,
  };
}
