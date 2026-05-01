import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent, type MouseEvent } from "react";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";
import {
  buildMarkdownTable,
  buildPreviewBlocks,
  countMarkdownLines,
  extractFirstTable,
  extractTableFromPage,
  getOcrSheetColumnSpec,
  normalizeHeaderToken,
} from "../../features/orders/orderDetailOcrUtils";
import {
  buildBagSummaryRows,
  deriveWeekValueFromCalendarDate,
  deriveWeekValueFromCalendarRange,
  extractWeekMonthId,
  formatBagCalculationResult,
  formatBagSplitBreakdown,
  formatWeekLabel,
  groupBagSummaryRowsByDate,
  isConcreteWeekValue,
  normalizeBagGroupToken,
  normalizeConcreteWeekValue,
  normalizeWeekId,
  normalizeWeekValue,
  type BagRow,
  type BagSummaryRow,
} from "../../features/orders/orderDetailUtils";

type OrderDetail = {
  id: string;
  status: string;
  document: string;
  current_sheet_revision_id?: string | null;
  week?: string | null;
  week_value?: string | null;
  persisted_week_value?: string | null;
  week_label?: string | null;
  message_id?: string | null;
  archived_at?: string | null;
  archived_by?: string | null;
  is_archived?: boolean | null;
  lines: {
    id?: string;
    line_id?: string;
    date?: string | null;
    daypart?: string | null;
    menu_name?: string | null;
    diet_type?: string | null;
    area_id?: string | null;
    bag_type?: string | null;
    quantity_original?: number | null;
    quantity_corrected?: number | null;
    change_note?: string | null;
    menu_qty_per_serving?: number | null;
    menu_unit_type?: string | null;
    actual_amount?: number | null;
    actual_unit_type?: string | null;
  }[];
  facility?: string | null;
  ocr_status?: string | null;
  ocr_error?: string | null;
  ocr_metrics?: {
    failed_cells?: number;
    provider?: string | null;
    requested_provider?: string | null;
    request_mode?: string | null;
    processing_stage?: string | null;
    result_state?: string | null;
    error?: string | null;
    row_count?: number | null;
    line_count?: number | null;
    llm_assist?: boolean | null;
    before_count?: number | null;
    after_count?: number | null;
    changed?: boolean | null;
    warning_reasons?: string[] | null;
    warning_detail?: Record<string, unknown> | null;
  } | null;
  ocr_prompt_enabled?: boolean | null;
  ocr_updated_at?: string | null;
  lines_updated_at?: string | null;
  ocr_job_id?: string | null;
  ocr_review_state?: string | null;
  ocr_review_badges?: string[] | null;
  ocr_has_saved_draft?: boolean | null;
  ocr_draft_updated_at?: string | null;
  ocr_draft_revision_id?: string | null;
  ocr_draft_newer_than_lines?: boolean | null;
  ocr_auto_apply_blocked?: boolean | null;
  ocr_reject_reasons?: string[] | null;
  ocr_draft_row_count?: number | null;
  ocr_can_apply_draft?: boolean | null;
  ocr_apply_blockers?: string[] | null;
  ocr_can_confirm?: boolean | null;
  ocr_confirm_blockers?: string[] | null;
  ocr_confirm_warnings?: string[] | null;
  ocr_last_reparse_error?: string | null;
  ocr_processing_stage?: string | null;
  ocr_result_state?: string | null;
  ocr_reparse_health?: string | null;
  ocr_confirmed_lines_retained?: boolean | null;
  workflow_state?: WorkflowStatePayload | null;
  candidate_resolution?: CandidateResolutionPayload | null;
  critical_decisions?: CriticalDecisionPayload[] | null;
  apply_gate?: ApplyGatePayload | null;
};

type WorkflowStatePayload = {
  state?: string | null;
  headline?: string | null;
  primary_action?: string | null;
  secondary_actions_json?: string[] | null;
  blockers_json?: string[] | null;
  warnings_json?: string[] | null;
  confidence_band?: string | null;
  candidate_resolution?: CandidateResolutionPayload | null;
  critical_decisions?: CriticalDecisionPayload[] | null;
  apply_gate?: ApplyGatePayload | null;
  current_sheet_revision_id?: string | null;
  candidate_sheet_state?: CandidateSheetStatePayload | null;
  candidate_prompt_visible?: boolean | null;
  candidate_evidence_run_id?: string | null;
  acknowledged_candidate_evidence_run_id?: string | null;
  active_evidence_run_id?: string | null;
  reparse_state?: ReparseStatePayload | null;
};

type CandidateSheetStatePayload = {
  current_sheet_revision_id?: string | null;
  candidate_evidence_run_id?: string | null;
  candidate_preview_available?: boolean | null;
  candidate_has_meaningful_diff?: boolean | null;
  candidate_preview_error?: string | null;
};

type ReparseStatePayload = {
  status?: string | null;
  request_mode?: string | null;
  processing_stage?: string | null;
  result_state?: string | null;
  error?: string | null;
  progress_updated_at?: string | null;
  stale_at?: string | null;
  stale_threshold_seconds?: number | null;
  job_id?: string | null;
};

type ApplyGatePayload = {
  can_apply?: boolean | null;
  can_confirm?: boolean | null;
  blockers?: string[] | null;
  warnings?: string[] | null;
};

type ResolutionGateStatePayload = {
  decision_type?: string | null;
  resolved_value?: string | null;
  blocked_reasons?: string[] | null;
  requires_user_choice?: boolean | null;
  blocked?: boolean | null;
  status?: string | null;
  suppressed?: boolean | null;
};

type CandidateOptionPayload = {
  value?: string | null;
  label?: string | null;
  score?: number | null;
  reason?: string | null;
};

type CandidateResolutionEntryPayload = {
  decision_type?: string | null;
  resolved_value?: string | null;
  resolved_label?: string | null;
  confidence?: string | null;
  blocked?: boolean | null;
  blocked_reasons?: string[] | null;
  requires_user_choice?: boolean | null;
  candidates?: CandidateOptionPayload[] | null;
  gate_state?: ResolutionGateStatePayload | null;
};

type CandidateResolutionPayload = {
  order_id?: string | null;
  requires_user_choice?: boolean | null;
  confidence_band?: string | null;
  critical_choices?: Array<Record<string, unknown>> | null;
  resolutions?: Record<string, CandidateResolutionEntryPayload> | null;
  gate_summary?: {
    details?: ResolutionGateStatePayload[] | null;
    choice_required_types?: string[] | null;
    blocked_types?: string[] | null;
    unresolved_types?: string[] | null;
  } | null;
};

type SheetProjectionPayload = {
  status?: string | null;
  reason_code?: string | null;
  reason_message?: string | null;
};

type CriticalDecisionPayload = {
  id?: string | null;
  decision_type?: string | null;
  candidate_set_json?: {
    title?: string | null;
    candidates?: CandidateOptionPayload[] | null;
    blocked_reasons?: string[] | null;
  } | null;
  selected_value?: string | null;
  selected_by?: string | null;
  selected_at?: string | null;
};

type OcrOutput = {
  status?: string;
  stage?: string;
  template_id?: string | null;
  failed_cells?: { row?: string; col?: string; reason?: string }[];
  cell_issues?: {
    issue_code?: string | null;
    reason?: string | null;
    severity?: string | null;
    row_index?: number | null;
    col_index?: number | null;
    row_span?: number | null;
    col_span?: number | null;
    text?: string | null;
  }[];
  warnings?: string[];
  table_raw?: string;
  tables?: {
    rows?: string[][];
    row_count?: number | null;
    col_count?: number | null;
  }[];
  pages?: OcrPage[];
  facility_candidates?: FacilityCandidate[];
  ocr_source?: string;
  _reparse_debug?: ReparseDebugPayload | null;
  edited_table?: {
    header?: string[];
    rows?: string[][];
    row_ids?: string[];
    edited_at?: string;
    ui_mode?: string;
    revision_id?: string;
  } | null;
};

type ReparseDebugPayload = {
  updated_at?: string | null;
  provider?: string | null;
  requested_provider?: string | null;
  llm_assist?: boolean | null;
  row_count?: number | null;
  line_count?: number | null;
  before_count?: number | null;
  after_count?: number | null;
  changed?: boolean | null;
  error?: string | null;
  date_strings?: string[];
  sample_rows?: string[][];
  raw_text?: string | null;
  provider_debug?: Record<string, unknown> | null;
  request_prompt?: string | null;
  normalized_lines?: Record<string, unknown>[] | null;
  reject_reasons?: string[] | null;
  validation_detail?: Record<string, unknown> | null;
  warning_reasons?: string[] | null;
  warning_detail?: Record<string, unknown> | null;
  llm_quantity_only_merge?: Record<string, unknown> | null;
};

type OcrPage = {
  page_index?: number | null;
  markdown_text?: string | null;
  ocr_overlay_url?: string | null;
  layout_overlay_url?: string | null;
  hakodate_overlay_url?: string | null;
  hakodate_overlay_status?: string | null;
  hakodate_overlay_blockers?: string[] | null;
  hakodate_overlay_message?: string | null;
  figure_urls?: string[];
  tables?: {
    rows?: string[][];
    row_count?: number | null;
    col_count?: number | null;
  }[];
  synthetic?: boolean | null;
  synthetic_source?: string | null;
  pdf_variant_used?: string | null;
};

type OcrPagesMeta = {
  table_box?: number[] | null;
  table_units?: string | null;
  quantity_assignment_strategy?: string | null;
  hakodate_overlay_status?: string | null;
  hakodate_overlay_blockers?: string[] | null;
  hakodate_overlay_message?: string | null;
  hakodate_assignment?: HakodateAssignmentPayload | null;
};

type HakodateOverlayPreviewPayload = {
  status?: string | null;
  blockers?: string[] | null;
  message?: string | null;
  overlay_url?: string | null;
  overlay_uri?: string | null;
  assignment?: HakodateAssignmentPayload | null;
  job_status?: ReparseStatePayload | null;
};

type OcrSheetPayload = {
  order_id: string;
  facility_id?: string | null;
  week_id?: string | null;
  fields?: string[];
  header?: string[];
  rows?: string[][];
  row_ids?: string[];
  cell_confidence_rows?: string[][] | null;
  cell_provenance_rows?: string[][] | null;
  ocr_numeric_cell_items?: OcrNumericCellItem[] | null;
  ocr_numeric_cell_summary?: OcrNumericCellSummary | null;
  source?: string;
  quantity_assignment_strategy?: string | null;
  quantity_column_count?: number;
  warnings?: string[];
  review_state?: string | null;
  can_apply?: boolean | null;
  can_confirm?: boolean | null;
  apply_blockers?: string[] | null;
  confirm_blockers?: string[] | null;
  confirm_warnings?: string[] | null;
  draft_newer_than_lines?: boolean | null;
  auto_apply_blocked?: boolean | null;
  processing_stage?: string | null;
  result_state?: string | null;
  confirmed_lines_retained?: boolean | null;
  sheet_projection?: SheetProjectionPayload | null;
  hakodate_assignment?: HakodateAssignmentPayload | null;
  hakodate_projection_metrics?: Record<string, unknown> | null;
};

type DraftSheetJsonPayload = {
  fields?: string[] | null;
  header?: string[] | null;
  rows?: string[][] | null;
  row_ids?: string[] | null;
  rowIds?: string[] | null;
  cell_confidence_rows?: string[][] | null;
  cell_provenance_rows?: string[][] | null;
  ocr_numeric_cell_items?: OcrNumericCellItem[] | null;
  ocr_numeric_cell_summary?: OcrNumericCellSummary | null;
  source?: string | null;
  warnings?: string[] | null;
  quantity_assignment_strategy?: string | null;
};

type DraftSheetPayload = {
  id?: string | null;
  order_id?: string | null;
  base_evidence_run_id?: string | null;
  base_template_resolution_id?: string | null;
  base_menu_snapshot_id?: string | null;
  draft_sheet_json?: DraftSheetJsonPayload | null;
  draft_state?: string | null;
  blockers_json?: string[] | null;
  warnings_json?: string[] | null;
  latest_patch_candidate_id?: string | null;
  edited_by?: string | null;
  edited_at?: string | null;
  created_at?: string | null;
  fields?: string[] | null;
  header?: string[] | null;
  rows?: string[][] | null;
  row_ids?: string[] | null;
  cell_confidence_rows?: string[][] | null;
  cell_provenance_rows?: string[][] | null;
  ocr_numeric_cell_items?: OcrNumericCellItem[] | null;
  ocr_numeric_cell_summary?: OcrNumericCellSummary | null;
  source?: string | null;
  warnings?: string[] | null;
  review_state?: string | null;
  workflow_state?: WorkflowStatePayload | null;
  apply_gate?: ApplyGatePayload | null;
  candidate_resolution?: CandidateResolutionPayload | null;
  critical_decisions?: CriticalDecisionPayload[] | null;
  evidence_capabilities?: Record<string, boolean> | null;
  evidence_degraded_reasons?: string[] | null;
  sheet_projection?: SheetProjectionPayload | null;
  quantity_assignment_strategy?: string | null;
  hakodate_assignment?: HakodateAssignmentPayload | null;
  hakodate_projection_metrics?: Record<string, unknown> | null;
};

type NormalizedEditorSheetPayload = {
  fields: string[];
  header: string[];
  rows: string[][];
  rowIds: string[];
  cellConfidenceRows: string[][];
  cellProvenanceRows: string[][];
  ocrNumericCellItems: OcrNumericCellItem[];
  ocrNumericCellSummary: OcrNumericCellSummary;
  source: string;
  warnings: string[];
};

type OcrCellConfidenceTier = "high" | "medium" | "low";
type OcrConfidenceDisplayMode = "strict" | "assisted" | "suggestion";
type OcrNumericCellClassification = "accepted" | "deterministic_candidate" | "weak_candidate" | "unresolved";
type OcrNumericCellItem = {
  classification?: OcrNumericCellClassification | string | null;
  value?: string | null;
  confidence_tier?: OcrCellConfidenceTier | string | null;
  placement_basis?: string | null;
  read_basis?: string | null;
  source_row_index?: number | null;
  source_col_index?: number | null;
  target_row_index?: number | null;
  target_col_index?: number | null;
  date_key?: string | null;
  daypart_key?: string | null;
  menu_key?: string | null;
  reason?: string | null;
};
type OcrNumericCellSummary = {
  raw_ocr_numeric_count?: number | null;
  accepted_count?: number | null;
  deterministic_candidate_count?: number | null;
  weak_candidate_count?: number | null;
  unresolved_count?: number | null;
};
type OcrSheetTouchedCell = {
  rowIndex: number;
  cellIndex: number;
};

type OcrVisibleOverlayItem = OcrNumericCellItem & {
  target_row_index: number;
  target_col_index: number;
  value: string;
  classification: OcrNumericCellClassification;
};

type HakodateTargetCell = {
  [key: string]: unknown;
  target_cell_id?: string | null;
  region_id?: string | null;
  sheet_cell?: string | null;
  worksheet_row?: number | string | null;
  worksheet_col?: number | string | null;
  semantic_field?: string | null;
  field_label?: string | null;
  date?: string | null;
  daypart?: string | null;
  menu_name?: string | null;
  bbox?: number[] | null;
  center?: number[] | null;
  merged_cell?: unknown;
  logical_targets?: unknown[] | null;
  covered_sheet_cells?: string[] | null;
  metadata?: Record<string, unknown> | null;
};

type HakodateAssignmentItem = {
  target_cell_id?: string | null;
  sheet_cell?: string | null;
  worksheet_row?: number | string | null;
  worksheet_col?: number | string | null;
  semantic_field?: string | null;
  assigned_value?: string | null;
  value_text?: string | null;
  value_normalized?: string | null;
  assignment_state?: string | null;
  assignment_confidence?: number | null;
  raw_texts?: string[] | null;
  evidence_ids?: string[] | null;
  target_metadata?: Record<string, unknown> | null;
};

type HakodateSheetOutputCell = {
  target_cell_id?: string | null;
  sheet_cell?: string | null;
  worksheet_row?: number | string | null;
  worksheet_col?: number | string | null;
  semantic_field?: string | null;
  field_label?: string | null;
  date?: string | null;
  daypart?: string | null;
  menu_name?: string | null;
  value_text?: string | null;
  value_normalized?: string | null;
  assignment_state?: string | null;
  assignment_confidence?: number | null;
  raw_texts?: string[] | null;
  evidence_ids?: string[] | null;
};

type HakodateAssignmentPayload = {
  version?: string | null;
  strategy?: string | null;
  assignment_mode?: string | null;
  status?: string | null;
  blockers?: string[] | null;
  warnings?: string[] | null;
  target_cells?: HakodateTargetCell[] | null;
  evidence_records?: Record<string, unknown>[] | null;
  assignments?: HakodateAssignmentItem[] | null;
  unassigned_evidence?: Record<string, unknown>[] | null;
  sheet_output?: {
    cells?: Record<string, HakodateSheetOutputCell> | null;
    blockers?: string[] | null;
    warnings?: string[] | null;
    summary?: Record<string, unknown> | null;
  } | null;
  metrics?: Record<string, unknown> | null;
};

type HakodateOverlayCell = HakodateTargetCell & {
  targetKey: string;
  sheetCell: string;
  bbox: number[];
  center: number[] | null;
  quantityText: string;
  assignmentState: string;
  hasInk: boolean;
};

type HakodateOverlayBox = {
  left: number;
  top: number;
  width: number;
  height: number;
  centerLeft: number;
  centerTop: number;
};

type OcrEditRevision = {
  revision_id?: string;
  edited_at?: string;
  ui_mode?: string;
  fields?: string[];
  header?: string[];
  row_ids?: string[];
  rows?: string[][];
  row_count?: number;
  before_digest?: string;
  after_digest?: string;
  changed?: boolean;
  markdown?: string;
  sheet_save_only?: boolean;
  sheet_save_mode?: string;
};

type ShippingStatusItem = {
  id?: string;
  tracking_key?: string;
  tracking_number: string;
  facility_name?: string | null;
  status: string;
  delivered: boolean;
  arrival_text?: string | null;
  error?: string | null;
  looked_up_at?: string | null;
  source?: string | null;
};

type ShippingStatusesPayload = {
  facility_names?: string[];
  summary?: {
    total?: number;
    delivered?: number;
    pending?: number;
    all_delivered?: boolean;
  };
  items?: ShippingStatusItem[];
};

type OcrHistoryPayload = {
  order_id: string;
  latest?: OcrEditRevision | null;
  revisions?: OcrEditRevision[];
  raw_output?: Record<string, unknown> | null;
};

type OrderHistoryItem = {
  id?: string;
  actor?: string | null;
  action?: string | null;
  target?: string | null;
  facility?: string | null;
  week?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
};

type OrderHistoryPayload = {
  order_id: string;
  items?: OrderHistoryItem[];
};

type GridParams = {
  grid_dpi: number;
  grid_line_scale: number;
  grid_line_min_ratio: number;
  grid_line_merge_gap: number;
  grid_line_merge_tolerance: number;
  grid_expected_columns: number;
  grid_qty_gap_tolerance: number;
  grid_left_date_ratio: number;
};

const OCR_SHEET_ROW_INDEX_WIDTH = 28;

type ExpandedCellCopyMode = "disabled" | "enabled" | "persisted";
type Step2WizardChoice = "" | "yes" | "no";
type Step2RepairStage = "" | "foundation" | "candidate" | "llm" | "merged";
type SavedSheetContextChangeMode = "keep" | "clear";
type LlmPromptPreset =
  | "numeric_verification"
  | "column_missing"
  | "row_alignment"
  | "special_diet_semantics"
  | "merged_cell_quantity_spans"
  | "freeform";

type OutputPreview = {
  type: "labels" | "delivery" | "aggregate";
  headers: string[];
  rows: string[][];
};

type AppliedPortionOverride = {
  override_id: string;
  date?: string | null;
  daypart?: string | null;
  menu_name?: string | null;
  menu_category?: string | null;
  diet_type?: string | null;
  unit_type?: string | null;
  qty_per_serving?: number | null;
  note?: string | null;
};

type DetailLine = OrderDetail["lines"][number];

const EXPANDED_CELL_COPY_FACILITY_KEY = "expanded_cell_same_daypart_copy_enabled";

const canonicalizeExpandedCellDaypart = (value: string) => {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  if (normalized.includes("朝")) return "朝";
  if (normalized.includes("昼")) return "昼";
  if (normalized.includes("夕")) return "夕";
  return normalized;
};

const parseExpandedCellQuantity = (value: string) => {
  const normalized = String(value || "")
    .trim()
    .replace(/[０-９]/g, (char) => String(char.charCodeAt(0) - 0xff10))
    .replace(/[．。]/g, ".")
    .replace(/[，、]/g, ",")
    .replace(/[－ー―−]/g, "-")
    .replace(/,/g, "");
  if (!normalized || !/^-?\d+(?:\.\d+)?$/.test(normalized)) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
};

const resolveExpandedCellSheetIndexes = (fields: string[], header: string[]) => {
  const normalizedFields = fields.map((field) => String(field || "").trim());
  const normalizedHeaders = header.map((cell) => normalizeHeaderToken(String(cell || "")));
  const dateIndex = normalizedFields.findIndex((field) => field === "date_mmdd" || field === "date");
  const daypartIndex = normalizedFields.findIndex((field) => field === "daypart");
  const quantityIndexes = normalizedFields
    .map((field, idx) => (field.startsWith("qty.") ? idx : -1))
    .filter((idx) => idx >= 0);
  if (dateIndex >= 0 && daypartIndex >= 0 && quantityIndexes.length > 0) {
    return { dateIndex, daypartIndex, quantityIndexes };
  }
  const fallbackDateIndex = normalizedHeaders.findIndex((cell) => cell === "日付" || cell === "date");
  const fallbackDaypartIndex = normalizedHeaders.findIndex(
    (cell) => cell === "区分" || cell === "daypart" || cell === "meal" || cell === "time",
  );
  const fallbackQuantityIndexes = normalizedHeaders
    .map((cell, idx) => {
      if (!cell) return -1;
      if (["日付", "date", "区分", "daypart", "meal", "time", "メニュー", "menu", "menu_name", "備考", "remarks", "note"].includes(cell)) {
        return -1;
      }
      return idx;
    })
    .filter((idx) => idx >= 0);
  return {
    dateIndex: fallbackDateIndex,
    daypartIndex: fallbackDaypartIndex,
    quantityIndexes: fallbackQuantityIndexes,
  };
};

const applyExpandedCellSameDaypartCopyToRows = ({
  fields,
  header,
  rows,
}: {
  fields: string[];
  header: string[];
  rows: string[][];
}) => {
  if (!rows.length) return rows;
  const { dateIndex, daypartIndex, quantityIndexes } = resolveExpandedCellSheetIndexes(fields, header);
  if (dateIndex < 0 || daypartIndex < 0 || quantityIndexes.length === 0) {
    return rows;
  }
  const nextRows = rows.map((row) => [...row]);
  let currentStart = 0;
  let currentKey = "";
  let filled = 0;
  const flushRange = (start: number, endExclusive: number) => {
    const clusterLength = endExclusive - start;
    if (clusterLength < 2 || clusterLength > 3) {
      return;
    }
    quantityIndexes.forEach((columnIndex) => {
      const observed: Array<{ text: string; value: number }> = [];
      for (let rowIndex = start; rowIndex < endExclusive; rowIndex += 1) {
        const text = String(nextRows[rowIndex]?.[columnIndex] || "").trim();
        const value = parseExpandedCellQuantity(text);
        if (value == null) continue;
        observed.push({ text, value });
      }
      if (observed.length !== 1) {
        return;
      }
      const sourceText = observed[0].text;
      for (let rowIndex = start; rowIndex < endExclusive; rowIndex += 1) {
        const currentText = String(nextRows[rowIndex]?.[columnIndex] || "").trim();
        if (parseExpandedCellQuantity(currentText) != null) {
          continue;
        }
        while (nextRows[rowIndex].length <= columnIndex) {
          nextRows[rowIndex].push("");
        }
        nextRows[rowIndex][columnIndex] = sourceText;
        filled += 1;
      }
    });
  };

  rows.forEach((row, rowIndex) => {
    const dateKey = String(row?.[dateIndex] || "").trim();
    const daypartKey = canonicalizeExpandedCellDaypart(String(row?.[daypartIndex] || ""));
    const clusterKey = `${dateKey}__${daypartKey}`;
    if (rowIndex === 0) {
      currentKey = clusterKey;
      return;
    }
    if (clusterKey !== currentKey) {
      flushRange(currentStart, rowIndex);
      currentStart = rowIndex;
      currentKey = clusterKey;
    }
  });
  flushRange(currentStart, rows.length);
  return filled > 0 ? nextRows : rows;
};

type FacilityOption = {
  id: string;
  name: string;
};

type WeekOption = {
  week_id: string;
  label: string;
  date_from?: string | null;
  date_to?: string | null;
  selected?: boolean;
};

type FacilityTemplateColumn = {
  index: number;
  source_index?: number;
  role: string;
  header?: string;
  name?: string;
  diet_type?: string;
  area_id?: string;
};

type FacilityCandidate = {
  facility_id: string;
  facility_name?: string | null;
  score?: number | null;
  reason?: string | null;
  auto?: boolean | null;
};

type PendingSavedSheetContextChange = {
  source: "step1" | "critical_decision";
  facility: string;
  week: string;
  decisionType?: string;
  decisionValue?: string;
};

const toNumber = (value?: number | null) => (value == null || Number.isNaN(value) ? 0 : Number(value));

const normalizeDietTypeToken = (value?: string | null) => {
  const raw = (value || "").trim();
  if (!raw) return "";
  const compact = raw
    .toLowerCase()
    .replace(/[\s　]+/g, "")
    .replace(/[＿_]/g, "")
    .replace(/[／/・+＋-]/g, "")
    .replace(/[()（）\[\]【】]/g, "");
  if (!compact) return "";
  if ((compact.includes("袋") || compact.includes("bag")) && (compact.includes("regular") || compact.includes("常食") || compact.includes("通常") || compact === "常")) {
    return "regular_bag";
  }
  if (compact.includes("regular") || compact.includes("常食") || compact.includes("通常")) return "regular";
  if (compact.includes("daycare") || compact.includes("通所")) return "daycare";
  if (compact.includes("staff") || compact.includes("職員")) return "staff";
  if (compact.includes("揚げ物禁") || compact.includes("揚物禁") || compact.includes("nofried") || compact.includes("friedfree")) return "no_fried";
  if (compact.includes("tea") || compact.includes("お茶")) return "tea";
  if (compact.includes("business") || compact.includes("事業")) return "business";
  if (compact.includes("diabetes") || compact.includes("糖尿")) return "diabetes";
  if (compact.includes("pregnancy") || compact.includes("妊娠")) return "pregnancy";
  if ((compact.includes("ごま") || compact.includes("sesame")) && (raw.includes("アレル") || compact.includes("allergy"))) {
    return "sesame_allergy";
  }
  if (
    (compact.includes("肉") || compact.includes("meat")) &&
    (compact.includes("卵") || compact.includes("玉子") || compact.includes("egg")) &&
    (compact.includes("魚") || compact.includes("鯖") || compact.includes("さば") || compact.includes("fish"))
  ) {
    return "forbidden_other";
  }
  if (compact.includes("nomeat") || compact.includes("nobeef") || compact.includes("禁食肉禁") || compact.includes("肉禁")) return "no_meat";
  if (compact.includes("nofish") || compact.includes("禁食魚禁") || compact.includes("魚禁")) return "no_fish";
  if (compact === "unknown" || compact === "不明" || compact === "none") return "unknown";
  if (compact.includes("change1") || compact.includes("変更1")) return "change_1";
  if (compact.includes("change2") || compact.includes("変更2")) return "change_2";
  if (compact === "-" || compact === "placeholder") return "placeholder";
  const hasSoft = compact.includes("soft") || compact.includes("軟");
  const hasMixer = compact.includes("mixer") || compact.includes("mix") || compact.includes("ミキサ");
  if (hasSoft && hasMixer) return "soft_mixer";
  if (hasSoft) return "soft";
  if (hasMixer) return "mixer";
  return compact;
};

const normalizeFacilityAreaToken = (value?: string | null) => {
  const raw = (value || "").trim();
  if (!raw) return "X";
  const compact = raw
    .toLowerCase()
    .replace(/[\s　]+/g, "")
    .replace(/[()（）\[\]【】]/g, "");
  if (!compact) return "X";
  if (compact === "花" || compact.includes("hana")) return "2F";
  if (compact === "月" || compact.includes("tsuki")) return "3F";
  if (/^\d+$/.test(compact)) return `${compact}F`;
  const floorMatch = compact.match(/(\d)(?:f|階)/);
  if (floorMatch) return `${floorMatch[1]}F`;
  if (["x", "all", "common", "共通", "none", "null", "na", "n/a", "なし"].includes(compact)) {
    return "X";
  }
  return compact.toUpperCase();
};

const formatDietType = (value?: string | null) => {
  if (!value) return "不明";
  const token = normalizeDietTypeToken(value);
  if (token === "regular") return "常食";
  if (token === "regular_bag") return "常食(袋分け)";
  if (token === "daycare") return "通所";
  if (token === "staff") return "職員";
  if (token === "no_fried") return "禁食(揚げ物禁)";
  if (token === "tea") return "お茶";
  if (token === "business") return "事業";
  if (token === "diabetes") return "糖尿";
  if (token === "pregnancy") return "妊娠";
  if (token === "no_meat") return "禁食(肉禁)";
  if (token === "forbidden_other") return "禁食(肉卵魚禁)";
  if (token === "no_fish") return "禁食(魚禁)";
  if (token === "soft_mixer") return "軟菜/ミキサー";
  if (token === "soft") return "軟菜";
  if (token === "mixer") return "ミキサー";
  if (token === "sesame_allergy") return "ゴマアレルギー";
  if (token === "change_1") return "変更1";
  if (token === "change_2") return "変更2";
  if (token === "placeholder") return "-";
  if (token === "unknown") return "不明";
  return value;
};

const defaultHeaderForFacilityTemplateColumn = (column: {
  role?: string;
  header?: string;
  name?: string;
  diet_type?: string;
  area_id?: string;
}) => {
  const role = String(column.role || "").trim().toLowerCase();
  const header = String(column.header || "").trim();
  if (header) return header;
  if (role === "date") return "日付";
  if (role === "daypart") return "区分";
  if (role === "menu_name") return "メニュー";
  if (role === "note") return "備考";
  if (role === "quantity") {
    const diet = normalizeDietTypeToken(column.diet_type || column.header || column.name || "") || "unknown";
    const area = normalizeFacilityAreaToken(column.area_id || column.header || column.name || "");
    const base = formatDietType(diet);
    return area === "X" ? base : `${base}${area}`;
  }
  return "";
};

const defaultNameForFacilityTemplateColumn = (column: {
  role?: string;
  name?: string;
  diet_type?: string;
  area_id?: string;
}) => {
  const role = String(column.role || "").trim().toLowerCase();
  const explicitName = String(column.name || "").trim();
  if (explicitName) return explicitName;
  if (role === "date") return "date_mmdd";
  if (role === "daypart") return "daypart";
  if (role === "menu_name") return "menu";
  if (role === "note") return "remarks";
  if (role === "quantity") {
    const diet = normalizeDietTypeToken(column.diet_type || "") || "unknown";
    const area = normalizeFacilityAreaToken(column.area_id || "");
    return `qty.${diet}_${area === "X" ? "x" : area.toLowerCase()}`;
  }
  return "";
};

const isQuantityRole = (role?: string | null) => String(role || "").trim().toLowerCase() === "quantity";

const createEmptyFacilityTemplateColumn = (index: number): FacilityTemplateColumn => ({
  index,
  role: "quantity",
  header: defaultHeaderForFacilityTemplateColumn({ role: "quantity", diet_type: "unknown", area_id: "X" }),
  name: defaultNameForFacilityTemplateColumn({ role: "quantity", diet_type: "unknown", area_id: "X" }),
  diet_type: "unknown",
  area_id: "X",
});

const normalizeFacilityTemplateColumns = (columns: unknown): FacilityTemplateColumn[] => {
  if (!Array.isArray(columns)) return [];
  return columns
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item, idx) => {
      const role = String(item.role || "").trim().toLowerCase() || "quantity";
      const header = String(item.header || "").trim();
      const name = String(item.name || "").trim();
      const explicitDietType = String(item.diet_type || "").trim();
      const explicitAreaId = String(item.area_id || "").trim();
      const dietType =
        role === "quantity"
          ? (
              explicitDietType
                ? normalizeDietTypeToken(explicitDietType) || explicitDietType
                : normalizeDietTypeToken(header || name) || "unknown"
            )
          : String(item.diet_type || "");
      const areaId =
        role === "quantity"
          ? (
              explicitAreaId
                ? normalizeFacilityAreaToken(explicitAreaId)
                : normalizeFacilityAreaToken(header || name)
            )
          : String(item.area_id || "");
      return {
        index:
          typeof item.index === "number" && Number.isFinite(item.index)
            ? Number(item.index)
            : idx,
        source_index:
          typeof item.source_index === "number" && Number.isFinite(item.source_index)
            ? Number(item.source_index)
            : undefined,
        role,
        header: header || defaultHeaderForFacilityTemplateColumn({ role, header, name, diet_type: dietType, area_id: areaId }),
        name: name || defaultNameForFacilityTemplateColumn({ role, name, diet_type: dietType, area_id: areaId }),
        diet_type: dietType,
        area_id: areaId,
      };
    })
    .sort((left, right) => left.index - right.index);
};

const defaultOcrPromptQuantityTokens = [
  "regular_2f",
  "regular_3f",
  "soft_2f",
  "soft_3f",
  "mixer_2f",
  "mixer_3f",
];

const ocrPromptTokenForFacilityTemplateColumn = (column: FacilityTemplateColumn): string | null => {
  const role = String(column.role || "").trim().toLowerCase();
  if (role === "date") return "date";
  if (role === "menu_name") return "menu_name";
  if (role === "note") return "note";
  if (role !== "quantity") return null;
  const rawName =
    String(column.name || "").trim() ||
    defaultNameForFacilityTemplateColumn({
      role: column.role,
      name: column.name,
      diet_type: column.diet_type,
      area_id: column.area_id,
    });
  const normalized = rawName.replace(/^qty\./, "").trim();
  return normalized || null;
};

const ocrPromptTokenForSheetField = (field: string): string | null => {
  const normalized = String(field || "").trim();
  if (!normalized) return null;
  if (normalized === "date_mmdd" || normalized === "date") return "date";
  if (normalized === "menu" || normalized === "menu_name") return "menu_name";
  if (normalized === "remarks" || normalized === "note") return "note";
  if (normalized.startsWith("qty.")) return normalized.slice(4);
  return null;
};

const resolveOcrPromptSchemaTokens = ({
  fields,
  columns,
}: {
  fields: string[];
  columns: FacilityTemplateColumn[];
}) => {
  const quantityTokensFromFields = fields
    .map((field) => ocrPromptTokenForSheetField(field))
    .filter((token): token is string => Boolean(token && !["date", "menu_name", "note"].includes(token)));
  if (quantityTokensFromFields.length) {
    return [
      "date",
      "menu_name",
      ...Array.from(new Set(quantityTokensFromFields)),
      "note",
    ];
  }
  const quantityTokensFromColumns = columns
    .map((column) => ocrPromptTokenForFacilityTemplateColumn(column))
    .filter((token): token is string => Boolean(token && !["date", "menu_name", "note"].includes(token)));
  return [
    "date",
    "menu_name",
    ...(quantityTokensFromColumns.length
      ? Array.from(new Set(quantityTokensFromColumns))
      : defaultOcrPromptQuantityTokens),
    "note",
  ];
};

const buildOcrPromptFromCanonicalSchema = ({
  fields = [],
  columns = [],
}: {
  fields?: string[];
  columns?: FacilityTemplateColumn[];
}) => {
  const schemaTokens = resolveOcrPromptSchemaTokens({ fields, columns });
  const quantityTokens = schemaTokens.filter((token) => !["date", "menu_name", "note"].includes(token));
  const quantityOrder = quantityTokens.length
    ? quantityTokens.join(", ")
    : defaultOcrPromptQuantityTokens.join(", ");
  return [
    "Treat the current sheet shown in the editor as the canonical row structure.",
    "Return the full structural table for that current sheet, not a quantity-only sparse draft.",
    "Keep row count, row order, and blank rows exactly as they appear in the current sheet.",
    "Keep date/daypart/menu/remarks anchored to the current sheet unless the fax clearly contradicts them.",
    "The canonical daypart blocks are 朝/昼/夕.",
    "Fax-side 区分/category tokens can be aliases, sublabels, continuation marks, blanks, or OCR noise; use them only as within-block hints unless the fax clearly shows a new 朝/昼/夕 boundary.",
    `Target quantity columns, left-to-right: ${quantityOrder}.`,
    "Ignore fax-side totals, helper columns, unmatched numeric columns, and side annotations instead of shifting values into the nearest target quantity column.",
    "Do not create new remarks from side notes, allergy notes, prohibited-diet annotations, or margin text when the current sheet remarks cell is blank.",
    "If a quantity is unreadable, leave it blank rather than guessing across block boundaries.",
  ].join("\\n");
};

const reindexFacilityTemplateColumns = (columns: FacilityTemplateColumn[]) =>
  columns.map((column, idx) => ({ ...column, index: idx }));

const swapFacilityTemplateColumns = (
  columns: FacilityTemplateColumn[],
  leftIndex: number,
  rightIndex: number,
) => {
  if (
    leftIndex === rightIndex ||
    leftIndex < 0 ||
    rightIndex < 0 ||
    leftIndex >= columns.length ||
    rightIndex >= columns.length
  ) {
    return reindexFacilityTemplateColumns(columns);
  }
  const next = columns.map((column) => ({ ...column }));
  const temp = next[leftIndex];
  next[leftIndex] = next[rightIndex];
  next[rightIndex] = temp;
  return reindexFacilityTemplateColumns(next);
};

const removeFacilityTemplateColumn = (columns: FacilityTemplateColumn[], rowIndex: number) =>
  reindexFacilityTemplateColumns(columns.filter((_, idx) => idx !== rowIndex));

const buildFacilityTemplateColumnsPayload = (columns: FacilityTemplateColumn[]) =>
  columns.map((column, idx) => {
    const role = String(column.role || "").trim().toLowerCase() || "quantity";
    const header = String(column.header || "").trim();
    const name = String(column.name || "").trim();
    const payload: Record<string, unknown> = {
      index: idx,
      role,
    };
    if (typeof column.source_index === "number" && Number.isFinite(column.source_index)) {
      payload.source_index = Number(column.source_index);
    }
    if (header) payload.header = header;
    if (name) payload.name = name;
    if (role === "quantity") {
      const explicitDietType = String(column.diet_type || "").trim();
      const explicitAreaId = String(column.area_id || "").trim();
      const dietType = explicitDietType
        ? normalizeDietTypeToken(explicitDietType) || explicitDietType
        : normalizeDietTypeToken(header || name) || "unknown";
      const areaId = explicitAreaId
        ? normalizeFacilityAreaToken(explicitAreaId)
        : normalizeFacilityAreaToken(header || name);
      payload.diet_type = dietType;
      payload.area_id = areaId;
      payload.diet_type_locked = true;
      payload.area_id_locked = true;
      payload.name_locked = true;
    }
    return payload;
  });

const columnRoleOptions = [
  { value: "date", label: "日付" },
  { value: "daypart", label: "区分" },
  { value: "menu_name", label: "メニュー" },
  { value: "quantity", label: "数量" },
  { value: "note", label: "備考" },
];

const dietTypeLabels: Record<string, string> = {
  regular: "常食",
  regular_bag: "常食(袋分け)",
  daycare: "通所",
  staff: "職員",
  tea: "お茶",
  business: "事業",
  diabetes: "糖尿",
  pregnancy: "妊娠",
  soft: "軟菜",
  soft_mixer: "軟菜/ミキサー",
  mixer: "ミキサー",
  sesame_allergy: "ゴマアレルギー",
  no_fried: "禁食(揚げ物禁)",
  no_meat: "禁食(肉禁)",
  forbidden_other: "禁食(肉卵魚禁)",
  no_fish: "禁食(魚禁)",
  change_1: "変更1",
  change_2: "変更2",
  placeholder: "-",
  unknown: "不明",
};

const preferredDietOrder = [
  "regular",
  "regular_bag",
  "daycare",
  "staff",
  "tea",
  "business",
  "diabetes",
  "pregnancy",
  "soft",
  "soft_mixer",
  "mixer",
  "sesame_allergy",
  "no_fried",
  "no_meat",
  "forbidden_other",
  "no_fish",
  "change_1",
  "change_2",
  "placeholder",
  "unknown",
];

const facilityTemplateCustomDietTypeValue = "__custom_diet_type__";
const facilityTemplateCustomAreaValue = "__custom_area_id__";
const facilityTemplateCustomHeaderValue = "__custom_header__";
const facilityTemplateCustomNameValue = "__custom_name__";
const knownFacilityTemplateDietTypes = new Set(preferredDietOrder);
const facilityTemplateDietTypeOptions = preferredDietOrder.map((value) => ({
  value,
  label: dietTypeLabels[value] || value,
}));

const buildFacilityTemplateAreaOptions = (
  facilityConfig: Record<string, any> | null,
  columns: FacilityTemplateColumn[],
) => {
  const options: { value: string; label: string }[] = [];
  const seen = new Set<string>();
  const pushOption = (value: string, label?: string) => {
    const normalized = normalizeFacilityAreaToken(value);
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    options.push({
      value: normalized,
      label: label && label.trim() ? label.trim() : normalized,
    });
  };

  pushOption("X", "共通");
  const configAreas = Array.isArray(facilityConfig?.areas) ? facilityConfig.areas : [];
  configAreas.forEach((area) => {
    if (!area || typeof area !== "object") return;
    const areaId = String((area as Record<string, unknown>).area_id || "").trim();
    const areaName = String((area as Record<string, unknown>).name || "").trim();
    if (!areaId && !areaName) return;
    const normalized = normalizeFacilityAreaToken(areaId || areaName);
    if (normalized === "X") {
      pushOption("X", "共通");
      return;
    }
    const label = areaName && areaName !== normalized ? `${normalized} (${areaName})` : normalized;
    pushOption(normalized, label);
  });
  columns.forEach((column) => {
    if (!isQuantityRole(column.role)) return;
    const normalized = normalizeFacilityAreaToken(column.area_id || "");
    if (normalized && normalized !== "X") {
      pushOption(normalized, normalized);
    }
  });
  return options;
};

const resolveFacilityTemplateDietEditorValue = (column: FacilityTemplateColumn) => {
  if (!isQuantityRole(column.role)) return "";
  const normalized = normalizeDietTypeToken(column.diet_type || "");
  return knownFacilityTemplateDietTypes.has(normalized)
    ? normalized
    : facilityTemplateCustomDietTypeValue;
};

const resolveFacilityTemplateAreaEditorValue = (
  column: FacilityTemplateColumn,
  options: { value: string; label: string }[],
) => {
  if (!isQuantityRole(column.role)) return "";
  const normalized = normalizeFacilityAreaToken(column.area_id || "");
  return options.some((option) => option.value === normalized)
    ? normalized
    : facilityTemplateCustomAreaValue;
};

const buildFacilityTemplateHeaderOptions = (
  column: FacilityTemplateColumn,
  columns: FacilityTemplateColumn[],
  areaOptions: { value: string; label: string }[],
) => {
  const options: { value: string; label: string }[] = [];
  const seen = new Set<string>();
  const pushOption = (value?: string | null) => {
    const normalized = String(value || "").trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    options.push({ value: normalized, label: normalized });
  };

  columnRoleOptions.forEach((option) => {
    if (option.value === "quantity") return;
    pushOption(defaultHeaderForFacilityTemplateColumn({ role: option.value }));
  });
  if (isQuantityRole(column.role)) {
    const areas = Array.from(
      new Set(
        [
          "X",
          ...areaOptions.map((option) => normalizeFacilityAreaToken(option.value)),
          ...columns.map((candidate) => normalizeFacilityAreaToken(candidate.area_id || "")),
        ].filter((value) => Boolean(value)),
      ),
    );
    facilityTemplateDietTypeOptions.forEach((dietOption) => {
      areas.forEach((area) => {
        pushOption(
          defaultHeaderForFacilityTemplateColumn({
            role: "quantity",
            diet_type: dietOption.value,
            area_id: area,
          }),
        );
      });
    });
  }
  columns.forEach((candidate) => {
    pushOption(candidate.header);
  });
  pushOption(defaultHeaderForFacilityTemplateColumn(column));
  pushOption(column.header);
  return options;
};

const buildFacilityTemplateNameOptions = (
  column: FacilityTemplateColumn,
  columns: FacilityTemplateColumn[],
) => {
  const options: { value: string; label: string }[] = [];
  const seen = new Set<string>();
  const pushOption = (value?: string | null) => {
    const normalized = String(value || "").trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    options.push({ value: normalized, label: normalized });
  };

  pushOption(defaultNameForFacilityTemplateColumn(column));
  columns.forEach((candidate) => {
    if (candidate.role !== column.role) return;
    if (
      isQuantityRole(column.role) &&
      (normalizeDietTypeToken(candidate.diet_type || "") !== normalizeDietTypeToken(column.diet_type || "") ||
        normalizeFacilityAreaToken(candidate.area_id || "") !== normalizeFacilityAreaToken(column.area_id || ""))
    ) {
      return;
    }
    pushOption(candidate.name);
  });
  pushOption(column.name);
  return options;
};

const resolveFacilityTemplateHeaderEditorValue = (
  column: FacilityTemplateColumn,
  options: { value: string; label: string }[],
) => {
  const value = String(column.header || "").trim();
  return options.some((option) => option.value === value) ? value : facilityTemplateCustomHeaderValue;
};

const resolveFacilityTemplateNameEditorValue = (
  column: FacilityTemplateColumn,
  options: { value: string; label: string }[],
) => {
  const value = String(column.name || "").trim();
  return options.some((option) => option.value === value) ? value : facilityTemplateCustomNameValue;
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

const extractErrorDetailText = (detail: unknown): string => {
  if (typeof detail === "string") return detail.trim();
  if (detail && typeof detail === "object") {
    const payload = detail as Record<string, unknown>;
    const textCandidates = [payload.message, payload.detail, payload.error];
    for (const candidate of textCandidates) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate.trim();
      }
    }
    try {
      return JSON.stringify(detail);
    } catch {
      return "";
    }
  }
  return "";
};

const extractErrorDetailCode = (detail: unknown): string => {
  if (typeof detail === "string") return detail.trim().toLowerCase();
  if (detail && typeof detail === "object") {
    const payload = detail as Record<string, unknown>;
    const codeCandidates = [payload.error, payload.code, payload.detail];
    for (const candidate of codeCandidates) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate.trim().toLowerCase();
      }
    }
  }
  return "";
};

const resolveOcrApplyErrorMessage = (status: number | undefined, detail: unknown): string => {
  const detailText = extractErrorDetailText(detail);
  const detailCode = extractErrorDetailCode(detail);
  if (status === 403) {
    return "権限がありません。";
  }
  if (status === 404) {
    return "注文が見つかりません。画面を再読込してください。";
  }
  if (status === 400) {
    if (detailCode === "markdown or rows is required" || detailCode === "markdown_empty") {
      return "反映対象の表が空です。1行以上入力してから再実行してください。";
    }
    if (detailCode === "rows_empty") {
      return "表の行を解析できませんでした。空行のみになっていないか確認してください。";
    }
    if (detailCode === "lines_empty") {
      return "注文行を生成できませんでした。日付・区分・メニュー・数量列の入力を確認してください。";
    }
    if (detailCode === "facility missing") {
      return "施設が未設定のため反映できません。Step1（注文書）で施設を設定してください。";
    }
    if (detailCode === "facility not found") {
      return "施設設定が見つかりません。施設IDを確認して再設定してください。";
    }
    return detailText ? `OCRテーブルの反映に失敗しました: ${detailText}` : "OCRテーブルの反映に失敗しました。";
  }
  if (status) {
    return detailText
      ? `OCRテーブルの反映中にエラーが発生しました (status ${status}): ${detailText}`
      : `OCRテーブルの反映中にエラーが発生しました (status ${status})。`;
  }
  return detailText ? `OCRテーブルの反映中にエラーが発生しました: ${detailText}` : "OCRテーブルの反映中にエラーが発生しました。";
};

const describeReparseWarningReason = (code: string) => {
  const normalized = String(code || "").trim().toLowerCase();
  if (!normalized) return "";
  if (normalized === "sheet_column_anomaly") return "施設区分列の異常";
  if (normalized === "sheet_row_coverage_low") return "OCR行カバレッジ不足";
  if (normalized === "sheet_line_count_regression") return "明細行数の回帰";
  if (normalized === "sheet_llm_audit_failed") return "LLM監査NG";
  return code;
};

const describeReviewBlocker = (code: string) => {
  const normalized = String(code || "").trim().toLowerCase();
  if (!normalized) return "";
  if (normalized === "semantic_shell_only") {
    return "メニュー枠はありますが、数量はまだ信用できません";
  }
  if (normalized === "template_unresolved") {
    return "テンプレート解釈が未解決です";
  }
  if (normalized === "template_choice_required") {
    return "票面テンプレートの選択が必要です";
  }
  if (normalized === "column_mapping_choice_required") {
    return "数量列の対応候補を選択してください";
  }
  if (normalized === "quantity_choice_required") {
    return "重要な数量候補の選択が必要です";
  }
  if (normalized === "week_choice_required") {
    return "対象週の選択が必要です";
  }
  if (normalized === "sheet_quantity_column_unmapped") {
    return "数量列の対応付けが未完了です";
  }
  if (normalized === "sheet_payload_mapping_blocked_unresolved_template") {
    return "テンプレートまたは数量列対応が未解決のため、OCR数量をシートへ投影していません";
  }
  if (normalized === "ocr_evidence_recovery_required" || normalized === "evidence_recovery_required") {
    return "OCR基盤の復旧が必要です";
  }
  if (normalized === "weekly_menu_missing" || normalized === "sheet_weekly_menu_missing") {
    return "対象週の月次メニューが未登録です";
  }
  if (normalized === "monthly_menu_object_missing") {
    return "対象月の月次メニュー本体が未登録です";
  }
  if (normalized === "draft_rows_empty") {
    return "保存済みシートに行がありません";
  }
  if (normalized === "draft_newer_than_lines" || normalized === "draft_not_applied") {
    return "保存済みの下書きが明細へ未反映です";
  }
  if (normalized === "auto_apply_blocked") {
    return "自動反映を保留しました";
  }
  if (normalized === "rows_empty") {
    return "シート行が空です";
  }
  if (normalized === "sheet_contract_invalid") {
    return "正解シートの構造が不正です";
  }
  return describeReparseWarningReason(code) || code;
};

const describeReviewState = (state?: string | null) => {
  const normalized = String(state || "").trim().toLowerCase();
  if (normalized === "auto_apply_blocked") return "自動反映保留";
  if (normalized === "draft_ready") return "下書きあり";
  if (normalized === "draft_saved") return "下書き保存済み";
  if (normalized === "review_required") return "要確認";
  if (normalized === "processing_failed") return "OCR失敗";
  if (normalized === "processing") return "処理中";
  if (normalized === "ready") return "反映可能";
  return "";
};

const describeWorkflowState = (state?: string | null) => {
  const normalized = String(state || "").trim().toLowerCase();
  if (normalized === "uploaded") return "OCR待ち";
  if (normalized === "evidence_ready") return "証拠確認";
  if (normalized === "semantic_shell_only") return "数量要確認";
  if (normalized === "rerun_in_progress") return "OCR再取得中";
  if (normalized === "new_evidence_available") return "新しいOCR候補あり";
  if (normalized === "recovery_required") return "復旧待ち";
  if (normalized === "choice_required") return "選択待ち";
  if (normalized === "identity_choice_required") return "施設・週の選択待ち";
  if (normalized === "layout_choice_required") return "OCR候補の選択待ち";
  if (normalized === "draft_ready") return "下書き確認";
  if (normalized === "draft_blocked") return "反映前の確認待ち";
  if (normalized === "review_required") return "要確認";
  if (normalized === "apply_ready") return "反映可能";
  if (normalized === "confirmed") return "確定済み";
  return "";
};

const describeWorkflowPrimaryAction = (action?: string | null) => {
  const normalized = String(action || "").trim().toLowerCase();
  if (normalized === "run_ocr_pipeline" || normalized === "rerun_ocr_pipeline") return "OCR再実行";
  if (normalized === "recover_ocr_evidence") return "基盤復旧";
  if (normalized === "switch_to_new_evidence") return "候補切替";
  if (normalized === "keep_current_draft") return "現シート維持";
  if (normalized === "resolve_identity_choice") return "施設・週を選択";
  if (normalized === "resolve_layout_choice") return "OCR候補を選択";
  if (normalized === "review_critical_cells") return "高リスク箇所を確認";
  if (normalized === "apply_draft") return "明細へ反映";
  if (normalized === "edit_draft") return "下書きを確認";
  if (normalized === "wait_for_rerun" || normalized === "wait") return "完了待ち";
  return String(action || "").trim();
};

const resolveCandidateEvidenceState = (currentOrder?: OrderDetail | null) => {
  const workflowStateCode = String(currentOrder?.workflow_state?.state || "").trim().toLowerCase();
  const candidateSheetState = currentOrder?.workflow_state?.candidate_sheet_state;
  const candidateEvidenceRunId = String(
    currentOrder?.workflow_state?.candidate_evidence_run_id ||
      candidateSheetState?.candidate_evidence_run_id ||
      "",
  ).trim();
  const acknowledgedCandidateEvidenceRunId = String(
    currentOrder?.workflow_state?.acknowledged_candidate_evidence_run_id || "",
  ).trim();
  const activeEvidenceRunId = String(
    currentOrder?.workflow_state?.active_evidence_run_id || "",
  ).trim();
  const hasUnresolvedCandidateEvidenceChoice = Boolean(
    currentOrder?.workflow_state?.candidate_prompt_visible,
  );
  return {
    workflowStateCode,
    candidateEvidenceRunId,
    acknowledgedCandidateEvidenceRunId,
    activeEvidenceRunId,
    currentSheetRevisionId: String(
      currentOrder?.workflow_state?.current_sheet_revision_id ||
        candidateSheetState?.current_sheet_revision_id ||
        currentOrder?.current_sheet_revision_id ||
        currentOrder?.ocr_draft_revision_id ||
        "",
    ).trim(),
    candidatePreviewAvailable: Boolean(candidateSheetState?.candidate_preview_available),
    candidateHasMeaningfulDiff: Boolean(candidateSheetState?.candidate_has_meaningful_diff),
    candidatePreviewError: String(candidateSheetState?.candidate_preview_error || "").trim(),
    hasUnresolvedCandidateEvidenceChoice,
  };
};

const describeProcessingStage = (stage?: string | null) => {
  const normalized = String(stage || "").trim().toLowerCase();
  if (normalized === "queued") return "再解析受付済み";
  if (normalized === "first_pass_reused") return "既存OCR確認中";
  if (normalized === "ocr_pipeline") return "OCR準備中";
  if (normalized === "first_pass_missing") return "OCR土台不足";
  if (normalized === "inference") return "推論中";
  if (normalized === "validation") return "検証中";
  if (normalized === "draft_saved") return "下書き保存済み";
  if (normalized === "apply") return "明細更新中";
  if (normalized === "applied") return "明細更新済み";
  if (normalized === "stale_timeout") return "タイムアウト";
  if (normalized === "crashed") return "処理中断";
  if (normalized === "stale_context") return "古い画面状態を検知";
  return "";
};

const dedupeStrings = (items: Array<string | null | undefined>) => {
  const result: string[] = [];
  items.forEach((item) => {
    const normalized = String(item || "").trim();
    if (normalized && !result.includes(normalized)) {
      result.push(normalized);
    }
  });
  return result;
};

const isObjectRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

const asHakodateAssignmentPayload = (value: unknown): HakodateAssignmentPayload | null =>
  isObjectRecord(value) ? (value as HakodateAssignmentPayload) : null;

const asReparseStatePayload = (value: unknown): ReparseStatePayload | null =>
  isObjectRecord(value) ? (value as ReparseStatePayload) : null;

const asHakodateMetricsPayload = (value: unknown): Record<string, unknown> | null =>
  isObjectRecord(value) ? value : null;

const normalizeHakodateNumberArray = (value: unknown, expectedLength: number): number[] | null => {
  if (!Array.isArray(value) || value.length !== expectedLength) return null;
  const numbers = value.map((item) => Number(item));
  return numbers.every((item) => Number.isFinite(item)) ? numbers : null;
};

const firstNonEmptyText = (...values: unknown[]) => {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
};

const hakodateCellTruthMetadata = (value: unknown): Record<string, unknown> | null => {
  if (!isObjectRecord(value)) return null;
  const metadata = isObjectRecord(value.metadata) ? value.metadata : null;
  if (!metadata) return null;
  return isObjectRecord(metadata.truth) ? metadata.truth : null;
};

const hakodateNestedMetadata = (value: unknown): Record<string, unknown> | null => {
  if (!isObjectRecord(value)) return null;
  const nested = value.metadata;
  return isObjectRecord(nested) ? nested : value;
};

const hakodateCellHasInk = (...metadataValues: unknown[]) => {
  for (const metadataValue of metadataValues) {
    const metadata = hakodateNestedMetadata(metadataValue);
    if (!metadata) continue;
    if (metadata.recognizer_candidate === true || metadata.ocr_candidate === true) return true;
    const stats = isObjectRecord(metadata.recognizer_ink_stats) ? metadata.recognizer_ink_stats : null;
    if (!stats) continue;
    const inkArea = Number(stats.ink_area);
    const keptCount = Number(stats.kept_component_count);
    if ((Number.isFinite(inkArea) && inkArea > 0) || (Number.isFinite(keptCount) && keptCount > 0)) {
      return true;
    }
  }
  return false;
};

const readNumericMetric = (metrics: Record<string, unknown> | null | undefined, key: string): number => {
  const value = metrics?.[key];
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const describeHakodateOverlayBlocker = (code: string) => {
  const normalized = String(code || "").trim();
  if (normalized === "hakodate_target_cell_map_missing") {
    return "箱館 target_cell_map がありません。位置合わせ/読み取り対象セルの生成後に再実行してください。";
  }
  if (normalized === "hakodate_ocr_evidence_missing") {
    return "箱館 OCR evidence がありません。セルOCRを実行して数量 evidence を作成してください。";
  }
  if (normalized === "hakodate_assignment_unavailable") {
    return "箱館 assignment を生成できませんでした。";
  }
  if (normalized === "hakodate_overlay_render_unavailable") {
    return "箱館 overlay の描画に失敗しました。";
  }
  if (normalized === "template_unresolved") {
    return "箱館 template を解決できませんでした。";
  }
  if (normalized === "facility_missing") {
    return "施設設定がないため箱館 overlay を作れません。";
  }
  if (normalized === "facility_not_found") {
    return "施設設定を取得できないため箱館 overlay を作れません。";
  }
  if (normalized === "hakodate_preview_image_missing") {
    return "重ね表示に使う画像プレビューがありません。OCRページ/原本プレビューを再取得してください。";
  }
  return normalized;
};

const normalizeLlmProviderLabel = (provider?: string | null) => {
  const normalized = String(provider || "").trim().toLowerCase();
  if (normalized === "gemini") return "Gemini";
  if (normalized === "openai") return "OpenAI";
  return "LLM";
};

const isReparseStaleTimeoutError = (value?: string | null) =>
  String(value || "")
    .trim()
    .toLowerCase()
    .startsWith("reparse_stale_timeout>");

const describeOcrFailure = (value?: string | null) => {
  const raw = String(value || "").trim();
  const normalized = raw.toLowerCase();
  if (!raw) return "OCRが失敗しました。";
  if (normalized.startsWith("reparse_stale_timeout>")) {
    return "LLM補完再解析がタイムアウトしました。OCR結果は残っているため、必要なら再試行してください。";
  }
  if (normalized.startsWith("ocr_pipeline_failed:")) {
    const detail = raw.slice(raw.indexOf(":") + 1).trim();
    return detail
      ? `OCRパイプラインが失敗しました: ${detail}`
      : "OCRパイプラインが失敗しました。";
  }
  if (normalized === "ocr_pipeline_failed") {
    return "OCRパイプラインが失敗しました。";
  }
  if (normalized.startsWith("evidence_unusable")) {
    return "OCR結果を保存できるだけの成果物が揃いませんでした。OCRパイプラインを再実行してください。";
  }
  return `OCRが失敗しました: ${raw}`;
};

type ExplicitReparseOutcome = {
  kind: "failed" | "rejected";
  reasonCode: string;
  detail: string;
};

const describeReparseRejectedReason = (value?: string | null) => {
  const raw = String(value || "").trim();
  const normalized = raw.toLowerCase();
  if (!raw) {
    return "LLM再解析結果は保存条件を満たさなかったため、現在のシートへ反映されませんでした。";
  }
  if (normalized === "sheet_date_anchor_drift") {
    return "LLM再解析は日付範囲のドリフトを検知したため保存されませんでした。";
  }
  if (normalized === "sheet_canonical_mismatch") {
    return "LLM再解析は週メニュー整合チェックで不一致を検知したため保存されませんでした。";
  }
  if (normalized === "sheet_suspicious_blank_row") {
    return "LLM再解析は数量行の欠落を検知したため保存されませんでした。";
  }
  if (normalized === "sheet_row_coverage_low") {
    return "LLM再解析はOCR行カバレッジ不足を検知したため保存されませんでした。";
  }
  if (normalized === "sheet_column_anomaly") {
    return "LLM再解析は施設区分列の異常を検知したため保存されませんでした。";
  }
  if (normalized === "sheet_llm_audit_failed" || normalized === "draft_ready_blocked") {
    return "LLM再解析結果は保存条件を満たさなかったため、現在のシートへ反映されませんでした。";
  }
  return `LLM再解析結果は保存されませんでした: ${raw}`;
};

const resolveExplicitReparseOutcome = ({
  ocrStatus,
  ocrError,
  ocrProcessingStage,
  ocrResultState,
  ocrReparseHealth,
  ocrMetrics,
  workflowReparseState,
  reparseDebug,
}: {
  ocrStatus?: string | null;
  ocrError?: string | null;
  ocrProcessingStage?: string | null;
  ocrResultState?: string | null;
  ocrReparseHealth?: string | null;
  ocrMetrics?: OrderDetail["ocr_metrics"] | null;
  workflowReparseState?: ReparseStatePayload | null;
  reparseDebug?: ReparseDebugPayload | null;
}): ExplicitReparseOutcome | null => {
  const status = String(ocrStatus || "").trim().toLowerCase();
  const resultState = String(
    ocrResultState
      || ocrMetrics?.result_state
      || workflowReparseState?.result_state
      || "",
  )
    .trim()
    .toLowerCase();
  const processingStage = String(
    ocrProcessingStage
      || ocrMetrics?.processing_stage
      || workflowReparseState?.processing_stage
      || "",
  )
    .trim()
    .toLowerCase();
  const reparseHealth = String(
    ocrReparseHealth
      || workflowReparseState?.status
      || "",
  )
    .trim()
    .toLowerCase();
  const requestMode = String(
    ocrMetrics?.request_mode
      || workflowReparseState?.request_mode
      || "",
  )
    .trim()
    .toLowerCase();
  const reasonCode = String(
    ocrError
      || ocrMetrics?.error
      || workflowReparseState?.error
      || reparseDebug?.error
      || "",
  ).trim();

  const hasTerminalSignal = Boolean(
    requestMode === "llm_reparse"
      || resultState
      || reparseHealth
      || processingStage === "stale_timeout"
      || (reasonCode && isReparseStaleTimeoutError(reasonCode)),
  );
  if (!hasTerminalSignal) {
    return null;
  }

  const rejectedReason = reasonCode || resultState;
  const isRejected =
    resultState === "draft_ready_blocked"
    || [
      "sheet_date_anchor_drift",
      "sheet_canonical_mismatch",
      "sheet_suspicious_blank_row",
      "sheet_row_coverage_low",
      "sheet_column_anomaly",
      "sheet_llm_audit_failed",
    ].includes(rejectedReason.toLowerCase());
  if (isRejected) {
    return {
      kind: "rejected",
      reasonCode: rejectedReason,
      detail: describeReparseRejectedReason(rejectedReason),
    };
  }

  const isFailed =
    ["failed", "error", "stalled"].includes(status)
    || ["failed", "hard_failed"].includes(reparseHealth)
    || resultState === "hard_failed"
    || processingStage === "stale_timeout"
    || (reasonCode && isReparseStaleTimeoutError(reasonCode));
  if (isFailed) {
    return {
      kind: "failed",
      reasonCode,
      detail: describeOcrFailure(reasonCode || ocrError),
    };
  }

  return null;
};

const describeReparseProgressMessage = (
  stage?: string | null,
  options?: { llmAssist?: boolean; providerLabel?: string | null },
) => {
  const normalized = String(stage || "").trim().toLowerCase();
  const llmAssist = Boolean(options?.llmAssist);
  const providerLabel = normalizeLlmProviderLabel(options?.providerLabel);
  if (normalized === "queued") {
    return llmAssist
      ? "LLM補完再解析を受け付けました。まずOCR土台を確認します。"
      : "再解析を受け付けました。処理開始までお待ちください。";
  }
  if (normalized === "first_pass_reused") {
    return llmAssist
      ? `既存OCR結果を確認しています。続けて${providerLabel}で補完します。`
      : "既存OCR結果を確認しています。";
  }
  if (normalized === "ocr_pipeline") {
    return llmAssist
      ? `yomitoku結果を準備しています。完了後に${providerLabel}で補完します。`
      : "yomitoku結果を準備しています。";
  }
  if (normalized === "inference") {
    return llmAssist ? `${providerLabel}で補完再解析しています。` : "再解析しています。";
  }
  if (normalized === "validation") {
    return "結果を確認しています。反映可否を判定中です。";
  }
  if (normalized === "first_pass_missing") {
    return "OCRの土台が見つからないため、先に「OCRパイプラインを再実行」または「OCR基盤を復旧」を実行してください。";
  }
  if (normalized === "stale_timeout") return "再解析がタイムアウトしました。再試行してください。";
  if (normalized === "crashed") return "再解析処理が中断しました。もう一度実行してください。";
  if (normalized === "stale_context") return "画面が古いため再解析を止めました。再読み込みしてからやり直してください。";
  return "";
};

const formatQuantity = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("ja-JP");
};

const formatActualAmount = (line: {
  actual_amount?: number | null;
  actual_unit_type?: string | null;
  menu_unit_type?: string | null;
}) => {
  const value = line.actual_amount;
  if (value == null || Number.isNaN(value)) return "-";
  const numberValue = Number(value);
  const text = Number.isInteger(numberValue)
    ? numberValue.toLocaleString("ja-JP")
    : numberValue.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
  const unit = normalizeUnitType(line.actual_unit_type || line.menu_unit_type);
  return unit ? `${text}${unit}` : text;
};

const formatAmountNumber = (value: number) => {
  if (!Number.isFinite(value)) return "";
  if (Number.isInteger(value)) return `${value}`;
  return value.toFixed(2).replace(/\.00$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
};

const normalizeUnitType = (value?: string | null) => {
  const unit = (value || "").trim();
  if (!unit) return "";
  const normalized = unit.replace(/[　\s]+/g, "").toLowerCase();
  if (normalized === "g" || normalized === "ｇ" || normalized === "gram" || normalized === "grams") return "g";
  if (normalized === "個") return "個";
  if (normalized === "切") return "切";
  return unit;
};

const normalizeBagAmountDaypart = (value?: string | null) => {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.includes("朝")) return "朝";
  if (text.includes("昼")) return "昼";
  if (text.includes("夕") || text.includes("夜")) return "夕";
  return text;
};

const normalizeBagAmountMenu = (value?: string | null) => {
  const text = String(value || "").trim();
  if (!text) return "";
  return text
    .normalize("NFKC")
    .replace(/[\s　]+/g, "")
    .replace(/[・･]/g, "")
    .replace(/[／⁄]/g, "/")
    .replace(/[‐－―ーｰ-]/g, "")
    .replace(/[、,，:：]/g, "")
    .replace(/[()（）\[\]【】]/g, "")
    .toLowerCase();
};

const buildCondimentAmountKey = (date: string, daypart: string) =>
  `condiment__${date}__${normalizeBagAmountDaypart(daypart)}`;

const buildNonCondimentAmountKey = (date: string, daypart: string, menu: string, diet: string, area: string) =>
  `normal__${date}__${normalizeBagAmountDaypart(daypart)}__${normalizeBagAmountMenu(menu)}__${diet}__${normalizeFacilityAreaToken(area)}`;

const buildLooseNonCondimentAmountKey = (date: string, daypart: string, menu: string, diet: string) =>
  `normal__${date}__${normalizeBagAmountDaypart(daypart)}__${normalizeBagAmountMenu(menu)}__${diet}`;

const buildNonCondimentAmountKeyForLine = (line: OrderDetail["lines"][number]) => {
  const date = line.date || "";
  const daypart = line.daypart || "";
  const menu = line.menu_name || "";
  const diet = normalizeDietTypeToken(line.diet_type);
  const area = normalizeFacilityAreaToken(line.area_id);
  return buildNonCondimentAmountKey(date, daypart, menu, diet, area);
};

const buildNonCondimentAmountKeyForBag = (bag: BagRow) => {
  const date = bag.date || "";
  const daypart = bag.daypart || "";
  const menu = bag.menu_name || "";
  const diet = normalizeDietTypeToken(bag.diet_type);
  const area = normalizeFacilityAreaToken(bag.area_id);
  return buildNonCondimentAmountKey(date, daypart, menu, diet, area);
};

const buildLooseNonCondimentAmountKeyForLine = (line: OrderDetail["lines"][number]) => {
  const date = line.date || "";
  const daypart = line.daypart || "";
  const menu = line.menu_name || "";
  const diet = normalizeDietTypeToken(line.diet_type);
  return buildLooseNonCondimentAmountKey(date, daypart, menu, diet);
};

const buildLooseNonCondimentAmountKeyForBag = (bag: BagRow) => {
  const date = bag.date || "";
  const daypart = bag.daypart || "";
  const menu = bag.menu_name || "";
  const diet = normalizeDietTypeToken(bag.diet_type);
  return buildLooseNonCondimentAmountKey(date, daypart, menu, diet);
};

type BagAmountStats = {
  condimentTotals: Map<string, Record<string, number>>;
  perServingByGroup: Map<string, Record<string, number>>;
  perServingByLooseGroup: Map<string, { area: string; perServing: Record<string, number> }[]>;
};

const buildBagAmountStats = (lines: OrderDetail["lines"]): BagAmountStats => {
  const condimentTotals = new Map<string, Record<string, number>>();
  const nonCondimentStats = new Map<string, Record<string, { amount: number; quantity: number }>>();
  const areaByExactGroup = new Map<string, string>();
  const looseKeyByExactGroup = new Map<string, string>();
  lines.forEach((line) => {
    const quantity =
      line.quantity_corrected == null ? toNumber(line.quantity_original) : toNumber(line.quantity_corrected);
    if (!Number.isFinite(quantity) || quantity <= 0) return;
    const unit = normalizeUnitType(line.actual_unit_type || line.menu_unit_type);
    if (!unit) return;
    let amount = line.actual_amount;
    if (amount == null) {
      const perServing = line.menu_qty_per_serving;
      if (perServing == null || !Number.isFinite(Number(perServing))) return;
      amount = Number(perServing) * quantity;
    }
    if (!Number.isFinite(Number(amount))) return;
    const amountValue = Number(amount);
    const bagType = (line.bag_type || "").trim().toLowerCase();
    if (bagType === "condiment") {
      const key = buildCondimentAmountKey(line.date || "", line.daypart || "");
      const totals = condimentTotals.get(key) || {};
      totals[unit] = (totals[unit] || 0) + amountValue;
      condimentTotals.set(key, totals);
      return;
    }
    const key = buildNonCondimentAmountKeyForLine(line);
    const looseKey = buildLooseNonCondimentAmountKeyForLine(line);
    areaByExactGroup.set(key, normalizeFacilityAreaToken(line.area_id));
    looseKeyByExactGroup.set(key, looseKey);
    const unitStats = nonCondimentStats.get(key) || {};
    const current = unitStats[unit] || { amount: 0, quantity: 0 };
    current.amount += amountValue;
    current.quantity += quantity;
    unitStats[unit] = current;
    nonCondimentStats.set(key, unitStats);
  });
  const perServingByGroup = new Map<string, Record<string, number>>();
  const perServingByLooseGroup = new Map<string, { area: string; perServing: Record<string, number> }[]>();
  nonCondimentStats.forEach((unitStats, key) => {
    const perServing: Record<string, number> = {};
    Object.entries(unitStats).forEach(([unit, stat]) => {
      if (stat.quantity > 0 && Number.isFinite(stat.amount) && Number.isFinite(stat.quantity)) {
        perServing[unit] = stat.amount / stat.quantity;
      }
    });
    if (Object.keys(perServing).length) {
      perServingByGroup.set(key, perServing);
      const looseKey = looseKeyByExactGroup.get(key) || "";
      if (!looseKey) return;
      const existing = perServingByLooseGroup.get(looseKey) || [];
      existing.push({
        area: areaByExactGroup.get(key) || "X",
        perServing,
      });
      perServingByLooseGroup.set(looseKey, existing);
    }
  });
  return { condimentTotals, perServingByGroup, perServingByLooseGroup };
};

const resolveBagAmountTotals = (bag: BagRow, stats: BagAmountStats): Record<string, number> | undefined => {
  const bagType = (bag.bag_type || "").trim().toLowerCase();
  if (bagType === "condiment") {
    return stats.condimentTotals.get(buildCondimentAmountKey(bag.date || "", bag.daypart || ""));
  }
  const quantity = toNumber(bag.quantity);
  if (!Number.isFinite(quantity) || quantity < 0) return undefined;
  let perServing = stats.perServingByGroup.get(buildNonCondimentAmountKeyForBag(bag));
  if (!perServing) {
    const looseCandidates = stats.perServingByLooseGroup.get(buildLooseNonCondimentAmountKeyForBag(bag)) || [];
    const area = normalizeFacilityAreaToken(bag.area_id);
    perServing = looseCandidates.find((candidate) => candidate.area === area)?.perServing;
    if (!perServing && looseCandidates.length === 1) {
      perServing = looseCandidates[0].perServing;
    }
    if (!perServing) {
      perServing = looseCandidates.find((candidate) => candidate.area === "X")?.perServing;
    }
  }
  if (!perServing) return undefined;
  const totals: Record<string, number> = {};
  Object.entries(perServing).forEach(([unit, value]) => {
    if (!Number.isFinite(value) || value < 0) return;
    totals[unit] = value * quantity;
  });
  return totals;
};

const formatBagAmountFromTotals = (totals: Record<string, number> | undefined) => {
  if (!totals) return "-";
  const entries = Object.entries(totals).filter(([, value]) => Number.isFinite(value) && value >= 0);
  if (!entries.length) return "-";
  return entries
    .sort((a, b) => a[0].localeCompare(b[0], "ja"))
    .map(([unit, value]) => `${formatAmountNumber(value)}${unit}`)
    .join(" + ");
};

const DEFAULT_OCR_PROMPT = buildOcrPromptFromCanonicalSchema({});
const DEFAULT_LLM_REPARSE_PROVIDER = "gemini";
const DEFAULT_LLM_REPARSE_MODEL_MODE: "flash" | "pro" | "other" = "pro";

const defaultGridParams: GridParams = {
  grid_dpi: 300,
  grid_line_scale: 30,
  grid_line_min_ratio: 0.6,
  grid_line_merge_gap: 2,
  grid_line_merge_tolerance: 0.02,
  grid_expected_columns: 0,
  grid_qty_gap_tolerance: 0.02,
  grid_left_date_ratio: 0.2,
};

const makeSheetRowId = (prefix = "sheet") =>
  `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const orderSteps = [
  {
    id: "document",
    label: "注文書",
    title: "注文書 (FAX PDF) の確認",
    description: "原本のPDFを確認して内容を把握します。",
  },
  {
    id: "ocr",
    label: "OCR修正",
    title: "OCR修正",
    description: "シートUIでOCR結果を修正します。",
  },
  {
    id: "details",
    label: "明細",
    title: "明細の確認と修正",
    description: "区分別一覧と明細の数量を確認します。",
  },
  {
    id: "bags",
    label: "袋わけ",
    title: "袋わけ結果の確認",
    description: "袋分け結果を確認し、必要なら再計算します。",
  },
  {
    id: "output",
    label: "出力",
    title: "出力と確定",
    description: "出力を確認して確定します。",
  },
];

const STEP2_APPLY_NEXT_LABEL = "修正完了 / 保存して明細に反映して次へ";

const makeCategoryKey = (dietType?: string | null, areaId?: string | null) => {
  const diet = (dietType || "").toLowerCase() || "unknown";
  const area = areaId || "";
  if (!area || diet === "unknown") {
    return diet;
  }
  return `${diet}__${area}`;
};

const formatCategoryLabel = (key: string) => {
  if (!key.includes("__")) {
    const normalized = normalizeDietTypeToken(key);
    return dietTypeLabels[normalized] || formatDietType(key);
  }
  const [diet, area] = key.split("__");
  const normalized = normalizeDietTypeToken(diet);
  const dietLabel = dietTypeLabels[normalized] || formatDietType(diet);
  return `${dietLabel}${area}`;
};

const buildBagTypeLabelMap = (facilityConfig: Record<string, any> | null) => {
  const map: Record<string, string> = {
    standard: "標準",
    condiment: "付属品",
    small: "小",
    medium: "中",
    large: "大",
  };
  const bagTypes = Array.isArray(facilityConfig?.bag_types) ? facilityConfig?.bag_types : [];
  bagTypes.forEach((entry: any) => {
    const id = typeof entry?.bag_type_id === "string" ? entry.bag_type_id.trim() : "";
    const label = typeof entry?.label === "string" ? entry.label.trim() : "";
    if (!id) return;
    const normalizedId = id.toLowerCase();
    map[id] = label || id;
    map[normalizedId] = label || id;
  });
  return map;
};

const formatBagTypeLabel = (value: string | null | undefined, labelMap: Record<string, string>) => {
  const key = (value || "").trim();
  if (!key) return "-";
  return labelMap[key] || labelMap[key.toLowerCase()] || key;
};

const isBagColumnHeader = (header: string | undefined) => {
  if (!header) return false;
  const normalized = header.trim().toLowerCase();
  if (!normalized) return false;
  return normalized.includes("bag") || normalized.includes("袋");
};

const formatOutputPreviewCell = (
  cell: string,
  header: string | undefined,
  labelMap: Record<string, string>,
) => {
  if (!isBagColumnHeader(header)) return cell;
  return formatBagTypeLabel(cell, labelMap);
};

const buildCategoryColumns = (lines: OrderDetail["lines"]) => {
  const seen = new Set<string>();
  lines.forEach((line) => {
    seen.add(makeCategoryKey(line.diet_type, line.area_id));
  });
  const columns: { key: string; label: string }[] = [];
  preferredDietOrder.forEach((diet) => {
    const withArea = Array.from(seen)
      .filter((key) => key === diet || key.startsWith(`${diet}__`))
      .sort();
    withArea.forEach((key) => columns.push({ key, label: formatCategoryLabel(key) }));
  });
  const extras = Array.from(seen)
    .filter((key) => !preferredDietOrder.some((diet) => key === diet || key.startsWith(`${diet}__`)))
    .sort();
  extras.forEach((key) => columns.push({ key, label: formatCategoryLabel(key) }));
  return columns;
};

const quantityFieldDietOrder = [
  "sesame_allergy",
  "no_fried",
  "regular_bag",
  "soft_mixer",
  "no_meat",
  "forbidden_other",
  "no_fish",
  "change_1",
  "change_2",
  "pregnancy",
  "diabetes",
  "business",
  "daycare",
  "regular",
  "unknown",
  "staff",
  "mixer",
  "soft",
  "tea",
];

const parseSheetQuantity = (value?: string | null) => {
  const normalized = String(value ?? "").trim().replace(/,/g, "");
  if (!normalized) return null;
  const quantity = Number(normalized);
  if (!Number.isFinite(quantity)) return null;
  return quantity;
};

const inferAreaFromQuantityHeader = (header: string, dietType: string) => {
  const trimmed = header.trim();
  if (!trimmed) return "";
  const dietLabel = formatDietType(dietType);
  if (dietLabel && trimmed.startsWith(dietLabel)) {
    const tail = trimmed.slice(dietLabel.length).trim();
    if (tail) return normalizeFacilityAreaToken(tail);
  }
  return "";
};

const inferQuantityColumnMeta = (field?: string | null, header?: string | null) => {
  const normalizedField = String(field || "").trim().toLowerCase();
  const trimmedHeader = String(header || "").trim();
  let dietType = "";
  let areaId = "";
  if (normalizedField.startsWith("qty.")) {
    const body = normalizedField.slice(4);
    for (const token of quantityFieldDietOrder) {
      if (body === token) {
        dietType = token;
        areaId = "X";
        break;
      }
      if (body.startsWith(`${token}_`)) {
        dietType = token;
        areaId = normalizeFacilityAreaToken(body.slice(token.length + 1));
        break;
      }
    }
    if (!dietType) {
      const parts = body.split("_");
      if (parts.length > 1) {
        const candidateArea = parts[parts.length - 1];
        const normalizedArea = normalizeFacilityAreaToken(candidateArea);
        if (normalizedArea !== "X" || candidateArea === "x") {
          dietType = normalizeDietTypeToken(parts.slice(0, -1).join("_"));
          areaId = normalizedArea;
        }
      }
      if (!dietType) {
        dietType = normalizeDietTypeToken(body);
        areaId = "X";
      }
    }
  }
  if (!dietType) {
    dietType = normalizeDietTypeToken(trimmedHeader || normalizedField.replace(/^qty\./, ""));
  }
  if (!dietType) return null;
  if (!areaId) {
    areaId = inferAreaFromQuantityHeader(trimmedHeader, dietType) || "X";
  }
  return {
    diet_type: dietType,
    area_id: areaId || "X",
  };
};

const findSheetFieldIndex = (
  fields: string[],
  headers: string[],
  predicate: (field: string, header: string) => boolean,
) => {
  for (let idx = 0; idx < Math.max(fields.length, headers.length); idx += 1) {
    const field = String(fields[idx] || "").trim();
    const header = String(headers[idx] || "").trim();
    if (predicate(field, header)) return idx;
  }
  return -1;
};

type SheetIdentityIndices = {
  dateIndex: number;
  daypartIndex: number;
  menuIndex: number;
};

type RowIdentitySnapshot = {
  date: string;
  daypart: string;
  menu: string;
  key: string;
};

type OcrPageTableInfo = {
  pageArrayIndex: number;
  pageIndex: number | null;
  header: string[];
  rows: string[][];
  globalStart: number;
  globalEnd: number;
  rowIdentities: RowIdentitySnapshot[];
};

type FocusedOverlayTarget = {
  pageArrayIndex: number;
  pageIndex: number | null;
  localRowIndex: number;
  globalRowIndex: number;
  matchReason: "global_index" | "identity_match" | "global_fallback";
};

type OcrSheetCellSelection = {
  anchorRowIndex: number;
  anchorCellIndex: number;
  focusRowIndex: number;
  focusCellIndex: number;
};

type OcrSheetSelectionBounds = {
  topRowIndex: number;
  bottomRowIndex: number;
  leftCellIndex: number;
  rightCellIndex: number;
  rowCount: number;
  cellCount: number;
};

const getOcrSheetSelectionBounds = (
  selection: OcrSheetCellSelection | null,
): OcrSheetSelectionBounds | null => {
  if (!selection) return null;
  const topRowIndex = Math.min(selection.anchorRowIndex, selection.focusRowIndex);
  const bottomRowIndex = Math.max(selection.anchorRowIndex, selection.focusRowIndex);
  const leftCellIndex = Math.min(selection.anchorCellIndex, selection.focusCellIndex);
  const rightCellIndex = Math.max(selection.anchorCellIndex, selection.focusCellIndex);
  return {
    topRowIndex,
    bottomRowIndex,
    leftCellIndex,
    rightCellIndex,
    rowCount: bottomRowIndex - topRowIndex + 1,
    cellCount: rightCellIndex - leftCellIndex + 1,
  };
};

const isOcrSheetCellWithinSelection = (
  selection: OcrSheetCellSelection | null,
  rowIndex: number,
  cellIndex: number,
) => {
  const bounds = getOcrSheetSelectionBounds(selection);
  if (!bounds) return false;
  return (
    rowIndex >= bounds.topRowIndex &&
    rowIndex <= bounds.bottomRowIndex &&
    cellIndex >= bounds.leftCellIndex &&
    cellIndex <= bounds.rightCellIndex
  );
};

const normalizeSheetMatchToken = (value?: string | null) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  return raw
    .normalize("NFKC")
    .replace(/[　\s]+/g, "")
    .replace(/[‐‑‒–—―ーｰ]/g, "-")
    .toLowerCase();
};

const normalizeSheetDateToken = (value?: string | null) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const normalized = raw
    .normalize("NFKC")
    .replace(/[　\s]+/g, "")
    .replace(/年/g, "/")
    .replace(/月/g, "/")
    .replace(/日/g, "")
    .replace(/[.]/g, "/")
    .replace(/\/+/g, "/");
  const parts = normalized.split(/[\/-]/).filter(Boolean);
  if (parts.length >= 2) {
    const month = parts[parts.length - 2];
    const day = parts[parts.length - 1];
    if (/^\d{1,2}$/.test(month) && /^\d{1,2}$/.test(day)) {
      return `${month.padStart(2, "0")}/${day.padStart(2, "0")}`;
    }
  }
  return normalizeSheetMatchToken(normalized);
};

const normalizeSheetDaypartToken = (value?: string | null) => {
  const token = normalizeSheetMatchToken(value);
  if (!token) return "";
  if (token.includes("朝")) return "朝";
  if (token.includes("昼")) return "昼";
  if (token.includes("夕") || token.includes("夜")) return "夕";
  return token;
};

const normalizeSheetMenuToken = (value?: string | null) =>
  normalizeSheetMatchToken(value).replace(/[()（）[\]【】「」『』]/g, "");

const getSheetIdentityIndices = (fields: string[], headers: string[]): SheetIdentityIndices => ({
  dateIndex: findSheetFieldIndex(fields, headers, (field, header) => {
    const token = String(field || "").trim().toLowerCase();
    if (token.startsWith("date")) return true;
    const headerToken = normalizeHeaderToken(header);
    return headerToken.includes("日付") || headerToken.startsWith("date");
  }),
  daypartIndex: findSheetFieldIndex(fields, headers, (field, header) => {
    const token = String(field || "").trim().toLowerCase();
    if (token === "daypart" || token === "meal" || token === "time") return true;
    const headerToken = normalizeHeaderToken(header);
    return headerToken.includes("区分") || headerToken === "daypart" || headerToken === "meal" || headerToken === "time";
  }),
  menuIndex: findSheetFieldIndex(fields, headers, (field, header) => {
    const token = String(field || "").trim().toLowerCase();
    if (token === "menu" || token === "menu_name") return true;
    const headerToken = normalizeHeaderToken(header);
    return headerToken.includes("メニュー") || headerToken.includes("献立") || headerToken === "menu";
  }),
});

const buildRowIdentitySnapshots = (
  rows: string[][],
  indices: SheetIdentityIndices,
) => {
  let previousDate = "";
  let previousDaypart = "";
  return rows.map((row) => {
    const rawDate = indices.dateIndex >= 0 ? row[indices.dateIndex] : "";
    const rawDaypart = indices.daypartIndex >= 0 ? row[indices.daypartIndex] : "";
    const rawMenu = indices.menuIndex >= 0 ? row[indices.menuIndex] : "";
    const date = normalizeSheetDateToken(rawDate) || previousDate;
    const daypart = normalizeSheetDaypartToken(rawDaypart) || previousDaypart;
    const menu = normalizeSheetMenuToken(rawMenu);
    if (normalizeSheetDateToken(rawDate)) previousDate = date;
    if (normalizeSheetDaypartToken(rawDaypart)) previousDaypart = daypart;
    return {
      date,
      daypart,
      menu,
      key: menu ? [date, daypart, menu].join("__") : "",
    };
  });
};

const buildOcrPageTableInfos = (pages: OcrPage[]) => {
  let globalOffset = 0;
  const results: OcrPageTableInfo[] = [];
  pages.forEach((page, pageArrayIndex) => {
    const table = extractTableFromPage(page);
    if (!table || !table.rows.length) return;
    const identityIndices = getSheetIdentityIndices([], table.header);
    const rowIdentities = buildRowIdentitySnapshots(table.rows, identityIndices);
    results.push({
      pageArrayIndex,
      pageIndex: page.page_index ?? pageArrayIndex + 1,
      header: table.header,
      rows: table.rows,
      globalStart: globalOffset,
      globalEnd: globalOffset + table.rows.length,
      rowIdentities,
    });
    globalOffset += table.rows.length;
  });
  return results;
};

const resolveFocusedOverlayTarget = (
  focusedSheetRowIndex: number | null,
  pageTables: OcrPageTableInfo[],
  sheetRowIdentities: RowIdentitySnapshot[],
  sheetRowCount: number,
): FocusedOverlayTarget | null => {
  if (focusedSheetRowIndex == null || focusedSheetRowIndex < 0 || !pageTables.length) return null;
  const totalOverlayRows = pageTables.reduce((sum, table) => sum + table.rows.length, 0);
  const findByGlobalIndex = (globalRowIndex: number, matchReason: FocusedOverlayTarget["matchReason"]) => {
    const table = pageTables.find(
      (candidate) => globalRowIndex >= candidate.globalStart && globalRowIndex < candidate.globalEnd,
    );
    if (!table) return null;
    return {
      pageArrayIndex: table.pageArrayIndex,
      pageIndex: table.pageIndex,
      localRowIndex: globalRowIndex - table.globalStart,
      globalRowIndex,
      matchReason,
    } satisfies FocusedOverlayTarget;
  };

  if (sheetRowCount > 0 && sheetRowCount === totalOverlayRows) {
    return findByGlobalIndex(focusedSheetRowIndex, "global_index");
  }

  const focusedIdentity = sheetRowIdentities[focusedSheetRowIndex]?.key || "";
  if (focusedIdentity) {
    const matches: FocusedOverlayTarget[] = [];
    pageTables.forEach((table) => {
      table.rowIdentities.forEach((identity, localRowIndex) => {
        if (identity.key !== focusedIdentity) return;
        matches.push({
          pageArrayIndex: table.pageArrayIndex,
          pageIndex: table.pageIndex,
          localRowIndex,
          globalRowIndex: table.globalStart + localRowIndex,
          matchReason: "identity_match",
        });
      });
    });
    if (matches.length) {
      matches.sort((left, right) => {
        const leftDistance = Math.abs(left.globalRowIndex - focusedSheetRowIndex);
        const rightDistance = Math.abs(right.globalRowIndex - focusedSheetRowIndex);
        return leftDistance - rightDistance;
      });
      return matches[0];
    }
  }

  if (focusedSheetRowIndex < totalOverlayRows) {
    return findByGlobalIndex(focusedSheetRowIndex, "global_fallback");
  }
  return null;
};

const buildDraftPreviewLines = (
  fields: string[],
  headers: string[],
  rows: string[][],
): DetailLine[] => {
  if (!rows.length) return [];
  const dateIndex = findSheetFieldIndex(fields, headers, (field, header) => {
    const token = field.toLowerCase();
    if (token.startsWith("date")) return true;
    const headerToken = normalizeHeaderToken(header);
    return headerToken.includes("日付") || headerToken.startsWith("date");
  });
  const daypartIndex = findSheetFieldIndex(fields, headers, (field, header) => {
    const token = field.toLowerCase();
    if (token === "daypart" || token === "meal" || token === "time") return true;
    const headerToken = normalizeHeaderToken(header);
    return headerToken.includes("区分") || headerToken === "daypart" || headerToken === "meal" || headerToken === "time";
  });
  const menuIndex = findSheetFieldIndex(fields, headers, (field, header) => {
    const token = field.toLowerCase();
    if (token === "menu" || token === "menu_name") return true;
    const headerToken = normalizeHeaderToken(header);
    return headerToken.includes("メニュー") || headerToken.includes("献立") || headerToken === "menu";
  });
  const quantityColumns = headers
    .map((header, idx) => {
      const meta = inferQuantityColumnMeta(fields[idx], header);
      if (!meta) return null;
      return { index: idx, ...meta };
    })
    .filter(
      (item): item is { index: number; diet_type: string; area_id: string } => Boolean(item),
    );
  if (!quantityColumns.length) return [];
  const previewLines: DetailLine[] = [];
  rows.forEach((row, rowIdx) => {
    const date = dateIndex >= 0 ? String(row[dateIndex] || "").trim() : "";
    const daypart = daypartIndex >= 0 ? String(row[daypartIndex] || "").trim() : "";
    const menuName = menuIndex >= 0 ? String(row[menuIndex] || "").trim() : "";
    quantityColumns.forEach((column) => {
      const quantity = parseSheetQuantity(row[column.index]);
      if (quantity == null || quantity === 0) return;
      previewLines.push({
        line_id: `draft-preview-${rowIdx}-${column.index}`,
        date: date || "-",
        daypart: daypart || "-",
        menu_name: menuName || "-",
        diet_type: column.diet_type,
        area_id: column.area_id,
        bag_type: null,
        quantity_original: quantity,
        quantity_corrected: quantity,
        change_note: null,
      });
    });
  });
  return previewLines;
};

type PivotRow = {
  date: string;
  menu_name: string;
  daypart: string;
  area_id: string;
  bag_type: string;
  totals: Record<string, number>;
  notes: Set<string>;
};

type PivotCategoryRow = {
  date: string;
  categoryKey: string;
  categoryLabel: string;
  menu_name: string;
  daypart: string;
  bag_type: string;
  quantity: number;
  notes: Set<string>;
};

const buildPivotRows = (lines: OrderDetail["lines"]): PivotRow[] => {
  const map = new Map<string, PivotRow>();
  lines.forEach((line) => {
    const dietKey = makeCategoryKey(line.diet_type, line.area_id);
    const qty =
      line.quantity_corrected == null ? toNumber(line.quantity_original) : toNumber(line.quantity_corrected);
    const date = line.date || "-";
    const menu = line.menu_name || "不明";
    const daypart = line.daypart || "-";
    const area = line.area_id || "-";
    const bag = line.bag_type || "-";
    const key = `${date}__${menu}__${daypart}__${area}__${bag}`;
    const current =
      map.get(key) || {
        date,
        menu_name: menu,
        daypart,
        area_id: area,
        bag_type: bag,
        totals: {},
        notes: new Set<string>(),
      };
    current.totals[dietKey] = (current.totals[dietKey] || 0) + qty;
    if (line.change_note) {
      current.notes.add(line.change_note);
    }
    map.set(key, current);
  });
  return Array.from(map.values());
};

const buildPivotCategoryRows = (rows: PivotRow[]): PivotCategoryRow[] => {
  const result: PivotCategoryRow[] = [];
  rows.forEach((row) => {
    Object.entries(row.totals).forEach(([categoryKey, quantity]) => {
      if (!quantity) return;
      result.push({
        date: row.date,
        categoryKey,
        categoryLabel: formatCategoryLabel(categoryKey),
        menu_name: row.menu_name,
        daypart: row.daypart,
        bag_type: row.bag_type,
        quantity,
        notes: row.notes,
      });
    });
  });
  return result;
};

const groupByDateAndCategory = <
  T extends { date?: string | null; categoryKey?: string | null; categoryLabel?: string | null },
>(
  rows: T[],
  categoryOrder: string[],
) => {
  const map = new Map<string, { date: string; categoryKey: string; categoryLabel: string; rows: T[] }>();
  rows.forEach((row) => {
    const date = row.date || "-";
    const categoryKey = row.categoryKey || "unknown";
    const categoryLabel = row.categoryLabel || formatCategoryLabel(categoryKey);
    const groupKey = `${date}__${categoryKey}`;
    const group =
      map.get(groupKey) || { date, categoryKey, categoryLabel, rows: [] as T[] };
    group.rows.push(row);
    map.set(groupKey, group);
  });
  const orderIndex = new Map(categoryOrder.map((key, idx) => [key, idx]));
  return Array.from(map.values()).sort((a, b) => {
    const dateCompare = a.date.localeCompare(b.date, "ja");
    if (dateCompare !== 0) return dateCompare;
    const aRank = orderIndex.get(a.categoryKey) ?? Number.MAX_SAFE_INTEGER;
    const bRank = orderIndex.get(b.categoryKey) ?? Number.MAX_SAFE_INTEGER;
    if (aRank !== bRank) return aRank - bRank;
    return a.categoryLabel.localeCompare(b.categoryLabel, "ja");
  });
};

const getColumnCount = (header: string[], rows: string[][]) => {
  const rowMax = rows.reduce((max, row) => Math.max(max, row.length), 0);
  const base = Math.max(header.length, rowMax);
  return base > 0 ? base : 1;
};

const renderMarkdownLine = (line: string, key: string) => {
  const imageMatch = line.match(/!\[[^\]]*]\(([^)]+)\)/);
  if (imageMatch?.[1]) {
    return <img key={key} src={imageMatch[1]} alt="figure" className="markdown-image" />;
  }
  const headingMatch = line.match(/^(#{1,4})\s+(.*)$/);
  if (headingMatch) {
    const level = headingMatch[1].length;
    const Tag = (`h${Math.min(level + 2, 6)}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6") || "h4";
    return <Tag key={key}>{headingMatch[2]}</Tag>;
  }
  return (
    <p key={key} className="markdown-line">
      {line}
    </p>
  );
};

const formatFacilityLabel = (facility: FacilityOption) => {
  if (!facility.name) return facility.id;
  return `${facility.name} (${facility.id})`;
};

const formatFacilityReason = (reason?: string | null) => {
  if (!reason) return "";
  if (reason === "name_exact") return "名称一致";
  if (reason === "phone") return "電話一致";
  if (reason === "fuzzy") return "類似";
  return reason;
};

const formatOrderAction = (value?: string | null) => {
  const action = (value || "").trim();
  if (!action) return "-";
  if (action === "order_create") return "注文作成";
  if (action === "order_reparse") return "再解析";
  if (action === "order_lines_update") return "明細更新";
  if (action === "order_confirm") return "注文確定";
  if (action === "order_facility_set") return "施設設定";
  if (action === "order_week_set") return "週設定";
  if (action === "ocr_job_started") return "OCR開始";
  if (action === "ocr_job_failed") return "OCR失敗";
  return action;
};

const isGsUri = (value?: string | null) =>
  typeof value === "string" && value.trim().toLowerCase().startsWith("gs://");

const toFixedOrEmpty = (value: number | null, decimals: number) =>
  value == null || Number.isNaN(value) ? "" : value.toFixed(decimals);

export default function OrderDetailPage() {
  const router = useRouter();
  const { id } = router.query;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [facility, setFacility] = useState<string>("");
  const [weekDraft, setWeekDraft] = useState<string>("");
  const [weekOptions, setWeekOptions] = useState<WeekOption[]>([]);
  const [weekOptionsLoading, setWeekOptionsLoading] = useState<boolean>(false);
  const [weekOptionsError, setWeekOptionsError] = useState<string>("");
  const [facilityOptions, setFacilityOptions] = useState<FacilityOption[]>([]);
  const [facilityOptionsLoading, setFacilityOptionsLoading] = useState(false);
  const [facilityOptionsError, setFacilityOptionsError] = useState("");
  const [step1Saving, setStep1Saving] = useState<boolean>(false);
  const [customWeekRangeStart, setCustomWeekRangeStart] = useState<string>("");
  const [customWeekRangeEnd, setCustomWeekRangeEnd] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<string>("");
  const [archiveOrderBusy, setArchiveOrderBusy] = useState<boolean>(false);
  const [trainingSampleSaving, setTrainingSampleSaving] = useState<boolean>(false);
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [pdfError, setPdfError] = useState<string>("");
  const [pdfSourceKind, setPdfSourceKind] = useState<"original" | "ocr_artifact">("original");
  const [pdfSourceVariant, setPdfSourceVariant] = useState<string>("");
  const [ocrPrompt, setOcrPrompt] = useState<string>(DEFAULT_OCR_PROMPT);
  const lastAutoGeneratedOcrPromptRef = useRef<string>(DEFAULT_OCR_PROMPT);
  const [ocrRawText, setOcrRawText] = useState<string>("");
  const [ocrRawMessage, setOcrRawMessage] = useState<string>("");
  const [ocrRawLoading, setOcrRawLoading] = useState<boolean>(false);
  const [ocrOutput, setOcrOutput] = useState<OcrOutput | null>(null);
  const [ocrOutputMessage, setOcrOutputMessage] = useState<string>("");
  const [ocrPages, setOcrPages] = useState<OcrPage[]>([]);
  const [ocrPagesMessage, setOcrPagesMessage] = useState<string>("");
  const [ocrPagesLoading, setOcrPagesLoading] = useState<boolean>(false);
  const [hakodateOverlayLoading, setHakodateOverlayLoading] = useState<boolean>(false);
  const [hakodateOverlayUrl, setHakodateOverlayUrl] = useState<string>("");
  const [hakodateOverlayStatus, setHakodateOverlayStatus] = useState<string>("");
  const [hakodateOverlayBlockers, setHakodateOverlayBlockers] = useState<string[]>([]);
  const [hakodateOverlayMessage, setHakodateOverlayMessage] = useState<string>("");
  const [hakodateJobStatus, setHakodateJobStatus] = useState<ReparseStatePayload | null>(null);
  const [ocrTableBox, setOcrTableBox] = useState<number[] | null>(null);
  const [ocrTableUnits, setOcrTableUnits] = useState<string | null>(null);
  const [tableBoxUnitsOverride, setTableBoxUnitsOverride] = useState<string | null>(null);
  const [activeOcrPageIndex, setActiveOcrPageIndex] = useState<number>(0);
  const ocrPageSelectionModeRef = useRef<"auto" | "manual">("auto");
  const [ocrTableHeader, setOcrTableHeader] = useState<string[]>([]);
  const [ocrTableRows, setOcrTableRows] = useState<string[][]>([]);
  const [ocrTablePageIndex, setOcrTablePageIndex] = useState<number | null>(null);
  const [ocrSheetFields, setOcrSheetFields] = useState<string[]>([]);
  const [ocrSheetHeader, setOcrSheetHeader] = useState<string[]>([]);
  const [ocrSheetRows, setOcrSheetRows] = useState<string[][]>([]);
  const [ocrSheetRowIds, setOcrSheetRowIds] = useState<string[]>([]);
  const [ocrSheetCellConfidenceRows, setOcrSheetCellConfidenceRows] = useState<string[][]>([]);
  const [ocrSheetCellProvenanceRows, setOcrSheetCellProvenanceRows] = useState<string[][]>([]);
  const [ocrSheetNumericCellItems, setOcrSheetNumericCellItems] = useState<OcrNumericCellItem[]>([]);
  const [ocrSheetNumericCellSummary, setOcrSheetNumericCellSummary] = useState<OcrNumericCellSummary>({
    raw_ocr_numeric_count: 0,
    accepted_count: 0,
    deterministic_candidate_count: 0,
    weak_candidate_count: 0,
    unresolved_count: 0,
  });
  const [focusedSheetRowIndex, setFocusedSheetRowIndex] = useState<number | null>(null);
  const [focusedSheetCell, setFocusedSheetCell] = useState<{ rowIndex: number; cellIndex: number } | null>(null);
  const [ocrSheetSelection, setOcrSheetSelection] = useState<OcrSheetCellSelection | null>(null);
  const [ocrSheetDropTarget, setOcrSheetDropTarget] = useState<{ rowIndex: number; cellIndex: number } | null>(null);
  const [ocrSheetSource, setOcrSheetSource] = useState<string>("");
  const [ocrSheetWarnings, setOcrSheetWarnings] = useState<string[]>([]);
  const [ocrSheetProjection, setOcrSheetProjection] = useState<SheetProjectionPayload | null>(null);
  const [ocrSheetReviewState, setOcrSheetReviewState] = useState<string>("");
  const [ocrSheetCanApply, setOcrSheetCanApply] = useState<boolean>(false);
  const [ocrSheetCanConfirm, setOcrSheetCanConfirm] = useState<boolean>(false);
  const [ocrSheetApplyBlockers, setOcrSheetApplyBlockers] = useState<string[]>([]);
  const [ocrSheetConfirmBlockers, setOcrSheetConfirmBlockers] = useState<string[]>([]);
  const [ocrSheetConfirmWarnings, setOcrSheetConfirmWarnings] = useState<string[]>([]);
  const [ocrSheetDraftNewerThanLines, setOcrSheetDraftNewerThanLines] = useState<boolean>(false);
  const [ocrSheetAutoApplyBlockedState, setOcrSheetAutoApplyBlockedState] = useState<boolean>(false);
  const [ocrSheetProcessingStage, setOcrSheetProcessingStage] = useState<string>("");
  const [ocrSheetResultState, setOcrSheetResultState] = useState<string>("");
  const [ocrSheetConfirmedLinesRetained, setOcrSheetConfirmedLinesRetained] = useState<boolean>(false);
  const [ocrSheetLoading, setOcrSheetLoading] = useState<boolean>(false);
  const [ocrSheetLoadSettled, setOcrSheetLoadSettled] = useState<boolean>(false);
  const [ocrSheetMessage, setOcrSheetMessage] = useState<string>("");
  const [ocrSheetAutoRetryBlocked, setOcrSheetAutoRetryBlocked] = useState<boolean>(false);
  const [ocrSheetColumnFillTarget, setOcrSheetColumnFillTarget] = useState<string>("");
  const [ocrSheetColumnFillValue, setOcrSheetColumnFillValue] = useState<string>("");
  const [ocrConfidenceDisplayMode, setOcrConfidenceDisplayMode] = useState<OcrConfidenceDisplayMode>("suggestion");
  const quantityAssignmentStrategy = "hakodate" as const;
  const [hakodateAssignment, setHakodateAssignment] = useState<HakodateAssignmentPayload | null>(null);
  const [hakodateProjectionMetrics, setHakodateProjectionMetrics] = useState<Record<string, unknown> | null>(null);
  const [candidateSheetPreview, setCandidateSheetPreview] = useState<NormalizedEditorSheetPayload | null>(null);
  const [candidateSheetPreviewLoading, setCandidateSheetPreviewLoading] = useState<boolean>(false);
  const [candidateSheetPreviewMessage, setCandidateSheetPreviewMessage] = useState<string>("");
  const [ocrHistoryLatest, setOcrHistoryLatest] = useState<OcrEditRevision | null>(null);
  const [ocrHistoryRows, setOcrHistoryRows] = useState<OcrEditRevision[]>([]);
  const [ocrHistoryLoading, setOcrHistoryLoading] = useState<boolean>(false);
  const [ocrHistoryMessage, setOcrHistoryMessage] = useState<string>("");
  const latestSavedSheetRevisionRef = useRef<OcrEditRevision | null>(null);
  const ocrSheetBaselinePayloadRef = useRef<NormalizedEditorSheetPayload | null>(null);
  const ocrSheetEditedSinceLoadRef = useRef<boolean>(false);
  const ocrSheetClipboardRef = useRef<string[][] | null>(null);
  const ocrSheetCellRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const ocrSheetSelectionPointerActiveRef = useRef<boolean>(false);
  const ocrSheetDragSelectionBoundsRef = useRef<OcrSheetSelectionBounds | null>(null);
  const ocrSwapLeftColumnRef = useRef<HTMLSelectElement | null>(null);
  const ocrSwapRightColumnRef = useRef<HTMLSelectElement | null>(null);
  const [ocrSheetClipboardReady, setOcrSheetClipboardReady] = useState<boolean>(false);
  const [orderHistoryRows, setOrderHistoryRows] = useState<OrderHistoryItem[]>([]);
  const [orderHistoryLoading, setOrderHistoryLoading] = useState<boolean>(false);
  const [orderHistoryMessage, setOrderHistoryMessage] = useState<string>("");
  const [ocrTableMessage, setOcrTableMessage] = useState<string>("");
  const [ocrTableSaving, setOcrTableSaving] = useState<boolean>(false);
  const [ocrShiftStartRow, setOcrShiftStartRow] = useState<string>("");
  const [ocrShiftEndRow, setOcrShiftEndRow] = useState<string>("");
  const [ocrSwapLeftColumn, setOcrSwapLeftColumn] = useState<string>("");
  const [ocrSwapRightColumn, setOcrSwapRightColumn] = useState<string>("");
  const [showOcrEdit, setShowOcrEdit] = useState<boolean>(false);
  const [showTableBoxEditor, setShowTableBoxEditor] = useState<boolean>(false);
  const [tableBoxDraft, setTableBoxDraft] = useState<number[] | null>(null);
  const [tableBoxStep, setTableBoxStep] = useState<number>(0.005);
  const [tableBoxMessage, setTableBoxMessage] = useState<string>("");
  const [tableBoxSaving, setTableBoxSaving] = useState<boolean>(false);
  const [gridDetecting, setGridDetecting] = useState<boolean>(false);
  const [gridDetectMessage, setGridDetectMessage] = useState<string>("");
  const [facilityConfig, setFacilityConfig] = useState<Record<string, any> | null>(null);
  const [facilityResolvedConfig, setFacilityResolvedConfig] = useState<Record<string, any> | null>(null);
  const [expandedCellCopyMode, setExpandedCellCopyMode] = useState<ExpandedCellCopyMode>("disabled");
  const [expandedCellCopySaving, setExpandedCellCopySaving] = useState<boolean>(false);
  const [facilityTemplateColumns, setFacilityTemplateColumns] = useState<FacilityTemplateColumn[]>([]);
  const [facilityTemplateColumnDraft, setFacilityTemplateColumnDraft] = useState<FacilityTemplateColumn[]>([]);
  const [facilityTemplateSwapLeft, setFacilityTemplateSwapLeft] = useState<string>("");
  const [facilityTemplateSwapRight, setFacilityTemplateSwapRight] = useState<string>("");
  const [facilityTemplateMessage, setFacilityTemplateMessage] = useState<string>("");
  const [facilityTemplateSaving, setFacilityTemplateSaving] = useState<boolean>(false);
  const [forcedSheetRecoveryPending, setForcedSheetRecoveryPending] = useState<"" | "weekly" | "facility">("");
  const [showFacilityTemplateEditor, setShowFacilityTemplateEditor] = useState<boolean>(false);
  const [openPivotGroupKey, setOpenPivotGroupKey] = useState<string | null>(null);
  const [openLineGroupKey, setOpenLineGroupKey] = useState<string | null>(null);
  const [gridParams, setGridParams] = useState<GridParams>(defaultGridParams);
  const [gridParamsDraft, setGridParamsDraft] = useState<GridParams>(defaultGridParams);
  const [gridColumnEdges, setGridColumnEdges] = useState<number[] | null>(null);
  const [gridColumnEdgesDraft, setGridColumnEdgesDraft] = useState<number[] | null>(null);
  const [columnEdgesText, setColumnEdgesText] = useState<string>("");
  const [gridRowEdges, setGridRowEdges] = useState<number[] | null>(null);
  const [gridRowEdgesDraft, setGridRowEdgesDraft] = useState<number[] | null>(null);
  const [rowEdgesText, setRowEdgesText] = useState<string>("");
  const [reparsePending, setReparsePending] = useState<boolean>(false);
  const [llmReparseProvider, setLlmReparseProvider] = useState<string>(DEFAULT_LLM_REPARSE_PROVIDER);
  const [llmReparseModelMode, setLlmReparseModelMode] = useState<"flash" | "pro" | "other">(
    DEFAULT_LLM_REPARSE_MODEL_MODE,
  );
  const [llmReparseCustomModel, setLlmReparseCustomModel] = useState<string>("");
  const [llmReparsePromptPreset, setLlmReparsePromptPreset] = useState<LlmPromptPreset>("numeric_verification");
  const [criticalDecisionSaving, setCriticalDecisionSaving] = useState<string>("");
  const [ocrRecoverPending, setOcrRecoverPending] = useState<boolean>(false);
  const [switchEvidencePending, setSwitchEvidencePending] = useState<boolean>(false);
  const [keepCurrentPending, setKeepCurrentPending] = useState<boolean>(false);
  const [pendingSavedSheetContextChange, setPendingSavedSheetContextChange] =
    useState<PendingSavedSheetContextChange | null>(null);
  const [savedSheetContextChangeApplying, setSavedSheetContextChangeApplying] =
    useState<SavedSheetContextChangeMode | "">("");
  const [bagRows, setBagRows] = useState<BagRow[]>([]);
  const [bagAppliedOverrides, setBagAppliedOverrides] = useState<AppliedPortionOverride[]>([]);
  const [bagMessage, setBagMessage] = useState<string>("");
  const [bagLoading, setBagLoading] = useState<boolean>(false);
  const [outputPreview, setOutputPreview] = useState<OutputPreview | null>(null);
  const [outputPreviewMessage, setOutputPreviewMessage] = useState<string>("");
  const [outputPreviewLoading, setOutputPreviewLoading] = useState<boolean>(false);
  const [downloadMessage, setDownloadMessage] = useState<string>("");
  const [showLayoutOverlay, setShowLayoutOverlay] = useState<boolean>(false);
  const [ocrOverlayError, setOcrOverlayError] = useState<boolean>(false);
  const [layoutOverlayError, setLayoutOverlayError] = useState<boolean>(false);
  const [ocrOverlayRetry, setOcrOverlayRetry] = useState<boolean>(false);
  const [layoutOverlayRetry, setLayoutOverlayRetry] = useState<boolean>(false);
  const [overlayImageSize, setOverlayImageSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });
  const [ocrPreviewMode, setOcrPreviewMode] = useState<"overlay" | "original">("overlay");
  const [ocrWorkspaceLayoutMode, setOcrWorkspaceLayoutMode] = useState<"horizontal" | "vertical">("horizontal");
  const [step2WizardChoice, setStep2WizardChoice] = useState<Step2WizardChoice>("");
  const [step2RepairStage, setStep2RepairStage] = useState<Step2RepairStage>("");
  const [activeStep, setActiveStep] = useState<number>(0);
  const [ocrEditMode, setOcrEditMode] = useState<boolean>(false);
  const [lineEditsDirty, setLineEditsDirty] = useState<boolean>(false);
  const [confirmSaving, setConfirmSaving] = useState<boolean>(false);
  const [shippingStatuses, setShippingStatuses] = useState<ShippingStatusItem[]>([]);
  const [shippingSummary, setShippingSummary] = useState<ShippingStatusesPayload["summary"] | null>(null);
  const [shippingMessage, setShippingMessage] = useState<string>("");
  const [shippingLoading, setShippingLoading] = useState<boolean>(false);
  const overlayImageRef = useRef<HTMLImageElement | null>(null);
  const ocrPreviewWrapperRef = useRef<HTMLDivElement | null>(null);
  const reparseTimerRef = useRef<number | null>(null);
  const orderRefreshTimerRef = useRef<number | null>(null);
  const workspaceRefreshPromiseRef = useRef<Promise<OrderDetail | null> | null>(null);
  const orderDetailRequestSeqRef = useRef(0);
  const orderDetailAppliedSeqRef = useRef(0);
  const ocrPagesRequestSeqRef = useRef(0);
  const ocrSheetRequestSeqRef = useRef(0);
  const ocrSheetAppliedSeqRef = useRef(0);
  const workspaceRefreshTokenRef = useRef(0);
  const pdfRequestSeqRef = useRef(0);
  const ocrPreviewRefreshKeyRef = useRef("");
  const ocrPreviewForcedFallbackRef = useRef(false);
  const authoritativeOrderRef = useRef<OrderDetail | null>(null);

  useEffect(() => {
    authoritativeOrderRef.current = order || null;
  }, [order]);

  const loadOrderDetail = async (
    orderId: string,
    options: { preserveSelections?: boolean } = {},
  ): Promise<OrderDetail | null> => {
    const { preserveSelections = false } = options;
    const requestSeq = orderDetailRequestSeqRef.current + 1;
    orderDetailRequestSeqRef.current = requestSeq;
    const res = await apiClient.get(`/orders/${orderId}`);
    const nextOrder = (res.data || {}) as OrderDetail;
    if (requestSeq < orderDetailAppliedSeqRef.current) {
      return authoritativeOrderRef.current;
    }
    orderDetailAppliedSeqRef.current = requestSeq;
    authoritativeOrderRef.current = nextOrder;
    const currentPersistedFacility = (order?.facility || "").trim();
    const currentPersistedWeek = getCanonicalWeekSelectionSource(order);
    const selectedFacility = facility.trim();
    const selectedWeek = normalizeConcreteWeekValue(weekDraft);
    const preserveFacilitySelection =
      preserveSelections && Boolean(selectedFacility && selectedFacility !== currentPersistedFacility);
    const preserveWeekSelection =
      preserveSelections && Boolean(selectedWeek && selectedWeek !== currentPersistedWeek);
    setOrder(nextOrder);
    if (!preserveFacilitySelection) {
      setFacility(nextOrder.facility || "");
    }
    if (!preserveWeekSelection) {
      setWeekDraft(getCanonicalWeekSelectionSource(nextOrder));
    }
    return nextOrder;
  };

  const replaceAuthoritativeOrder = (nextOrder?: OrderDetail | null) => {
    if (!nextOrder) return;
    authoritativeOrderRef.current = nextOrder;
    setOrder(nextOrder);
  };

  const applyAuthoritativeWorkflowStateToOrder = (nextWorkflowState: WorkflowStatePayload | null | undefined) => {
    if (!nextWorkflowState) return;
    const mutationSeq = orderDetailRequestSeqRef.current + 1;
    orderDetailRequestSeqRef.current = mutationSeq;
    orderDetailAppliedSeqRef.current = mutationSeq;
    setOrder((current) => {
      if (!current) return current;
      const mergedWorkflowState = {
        ...(current.workflow_state || {}),
        ...nextWorkflowState,
      };
      const nextOrder = {
        ...current,
        workflow_state: mergedWorkflowState,
        apply_gate: mergedWorkflowState.apply_gate || current.apply_gate || null,
      };
      authoritativeOrderRef.current = nextOrder;
      return nextOrder;
    });
  };

  const getResolvedLlmReparseModel = () => {
    if (llmReparseProvider !== "gemini") return "";
    if (llmReparseModelMode === "other") {
      const customModel = llmReparseCustomModel.trim();
      if (customModel) return customModel;
      return "gemini-2.5-flash";
    }
    return llmReparseModelMode === "pro" ? "gemini-2.5-pro" : "gemini-2.5-flash";
  };

  useEffect(() => {
    if (!id) return;
    void loadOrderDetail(String(id));
    lastAutoGeneratedOcrPromptRef.current = DEFAULT_OCR_PROMPT;
    setOcrPrompt(DEFAULT_OCR_PROMPT);
    setLlmReparseProvider(DEFAULT_LLM_REPARSE_PROVIDER);
    setLlmReparseModelMode(DEFAULT_LLM_REPARSE_MODEL_MODE);
    setLlmReparseCustomModel("");
    setLlmReparsePromptPreset("numeric_verification");
    setFocusedSheetRowIndex(null);
    setCandidateSheetPreview(null);
    setCandidateSheetPreviewLoading(false);
    setCandidateSheetPreviewMessage("");
  }, [id]);

  const activeEditorRows = ocrSheetRows;
  const activeEditorFields = ocrSheetFields;

  useEffect(() => {
    const nextAutoPrompt = buildOcrPromptFromCanonicalSchema({
      fields: activeEditorFields,
      columns: facilityTemplateColumns,
    });
    const previousAutoPrompt = lastAutoGeneratedOcrPromptRef.current;
    const shouldReplaceCurrentPrompt =
      !ocrPrompt.trim() || ocrPrompt === previousAutoPrompt || ocrPrompt === DEFAULT_OCR_PROMPT;
    lastAutoGeneratedOcrPromptRef.current = nextAutoPrompt;
    if (shouldReplaceCurrentPrompt && ocrPrompt !== nextAutoPrompt) {
      setOcrPrompt(nextAutoPrompt);
    }
  }, [activeEditorFields, facilityTemplateColumns, ocrPrompt]);

  useEffect(() => {
    if (focusedSheetRowIndex == null) return;
    if (focusedSheetRowIndex < ocrSheetRows.length) return;
    setFocusedSheetRowIndex(null);
  }, [focusedSheetRowIndex, ocrSheetRows.length]);

  const loadShippingStatuses = async (silent: boolean = false) => {
    if (!id) return;
    if (!order?.facility) {
      setShippingStatuses([]);
      setShippingSummary(null);
      setShippingMessage("");
      return;
    }
    if (!silent) {
      setShippingLoading(true);
      setShippingMessage("追跡状況を取得中です...");
    }
    try {
      const res = await apiClient.get(`/orders/${id}/shipping-statuses`, {
        params: { limit: 8, max_age_days: 30 },
      });
      const payload: ShippingStatusesPayload = res.data || {};
      setShippingStatuses(Array.isArray(payload.items) ? payload.items : []);
      setShippingSummary(payload.summary || null);
      if (!silent) {
        setShippingMessage("追跡状況を更新しました。");
      }
    } catch (err: any) {
      setShippingStatuses([]);
      setShippingSummary(null);
      if (!silent) {
        const detail = err?.response?.data?.detail;
        setShippingMessage(detail ? `追跡状況の取得に失敗しました: ${detail}` : "追跡状況の取得に失敗しました。");
      }
    } finally {
      if (!silent) {
        setShippingLoading(false);
      }
    }
  };

  useEffect(() => {
    if (!id || !order?.facility) return;
    void loadShippingStatuses(true);
  }, [id, order?.facility]);

  useEffect(() => {
    if (!facility) {
      setFacilityConfig(null);
      setFacilityResolvedConfig(null);
      setExpandedCellCopyMode("disabled");
      setFacilityTemplateColumns([]);
      setFacilityTemplateColumnDraft([]);
      setFacilityTemplateMessage("");
      return;
    }
    let active = true;
    apiClient
      .get(`/facilities/${facility}`)
      .then((res) => {
        if (!active) return;
        setFacilityConfig(res.data?.config || null);
        setFacilityResolvedConfig(res.data?.resolved_config || null);
        setExpandedCellCopyMode(
          res.data?.resolved_config?.[EXPANDED_CELL_COPY_FACILITY_KEY] ? "persisted" : "disabled",
        );
        const resolvedColumns = normalizeFacilityTemplateColumns(
          res.data?.resolved_config?.fax_template?.columns,
        );
        setFacilityTemplateColumns(resolvedColumns);
        setFacilityTemplateColumnDraft(resolvedColumns);
        setFacilityTemplateSwapLeft("");
        setFacilityTemplateSwapRight("");
        setFacilityTemplateMessage("");
      })
      .catch(() => {
        if (!active) return;
        setFacilityConfig(null);
        setFacilityResolvedConfig(null);
        setExpandedCellCopyMode("disabled");
        setFacilityTemplateColumns([]);
        setFacilityTemplateColumnDraft([]);
        setFacilityTemplateSwapLeft("");
        setFacilityTemplateSwapRight("");
      });
    return () => {
      active = false;
    };
  }, [facility]);

  useEffect(() => {
    latestSavedSheetRevisionRef.current = null;
    ocrSheetBaselinePayloadRef.current = null;
    ocrSheetEditedSinceLoadRef.current = false;
    setOcrPages([]);
    setOcrPagesMessage("");
    setHakodateOverlayUrl("");
    setHakodateOverlayLoading(false);
    setHakodateOverlayStatus("");
    setHakodateOverlayBlockers([]);
    setHakodateOverlayMessage("");
    setHakodateJobStatus(null);
    setOcrTableBox(null);
    setOcrTableUnits(null);
    setOcrTableHeader([]);
    setOcrTableRows([]);
    setOcrTablePageIndex(null);
    setOcrSheetFields([]);
    setOcrSheetHeader([]);
    setOcrSheetRows([]);
    setOcrSheetRowIds([]);
    setOcrSheetCellConfidenceRows([]);
    setOcrSheetCellProvenanceRows([]);
    setOcrSheetNumericCellItems([]);
    setOcrSheetNumericCellSummary(blankOcrNumericCellSummary());
    setOcrSheetSource("");
    setOcrSheetWarnings([]);
    resetSheetReviewMeta();
    setOcrSheetLoading(false);
    setOcrSheetLoadSettled(false);
    setOcrSheetMessage("");
    setOcrSheetAutoRetryBlocked(false);
    setOcrHistoryLatest(null);
    setOcrHistoryRows([]);
    setOcrHistoryLoading(false);
    setOcrHistoryMessage("");
    setOrderHistoryRows([]);
    setOrderHistoryLoading(false);
    setOrderHistoryMessage("");
    setOcrTableMessage("");
    setActiveOcrPageIndex(0);
    setShowOcrEdit(false);
    setShowLayoutOverlay(false);
    setShowTableBoxEditor(false);
    setTableBoxDraft(null);
    setTableBoxMessage("");
    setTableBoxSaving(false);
    setGridDetecting(false);
    setGridDetectMessage("");
    setFacilityConfig(null);
    setGridParams(defaultGridParams);
    setGridParamsDraft(defaultGridParams);
    setGridColumnEdges(null);
    setGridColumnEdgesDraft(null);
    setColumnEdgesText("");
    setGridRowEdges(null);
    setGridRowEdgesDraft(null);
    setRowEdgesText("");
    setOverlayImageSize({ width: 0, height: 0 });
    setOcrOverlayError(false);
    setLayoutOverlayError(false);
    setOcrOverlayRetry(false);
    setLayoutOverlayRetry(false);
    setActiveStep(0);
    setOcrEditMode(false);
    setWeekDraft("");
    setWeekOptions([]);
    setWeekOptionsLoading(false);
    setWeekOptionsError("");
    setFacilityTemplateColumns([]);
    setFacilityTemplateColumnDraft([]);
    setFacilityTemplateMessage("");
    setFacilityTemplateSaving(false);
    setPdfUrl("");
    setPdfError("");
    setPdfSourceKind("original");
    setPdfSourceVariant("");
    setHakodateAssignment(null);
    setHakodateProjectionMetrics(null);
  }, [id]);

  useEffect(() => {
    setOcrOverlayError(false);
    setLayoutOverlayError(false);
    setOcrOverlayRetry(false);
    setLayoutOverlayRetry(false);
  }, [activeOcrPageIndex, ocrPages]);

  useEffect(() => {
    if (showTableBoxEditor) return;
    setTableBoxDraft(ocrTableBox ? [...ocrTableBox] : null);
  }, [ocrTableBox, showTableBoxEditor]);

  useEffect(() => {
    if (showTableBoxEditor) return;
    setGridColumnEdgesDraft(gridColumnEdges ? [...gridColumnEdges] : null);
  }, [gridColumnEdges, showTableBoxEditor]);

  useEffect(() => {
    if (!showTableBoxEditor) return;
    setColumnEdgesText(formatColumnEdgesText(gridColumnEdgesDraft));
  }, [gridColumnEdgesDraft, showTableBoxEditor]);

  useEffect(() => {
    if (showTableBoxEditor) return;
    setGridRowEdgesDraft(gridRowEdges ? [...gridRowEdges] : null);
  }, [gridRowEdges, showTableBoxEditor]);

  useEffect(() => {
    if (!showTableBoxEditor) return;
    setRowEdgesText(formatRowEdgesText(gridRowEdgesDraft));
  }, [gridRowEdgesDraft, showTableBoxEditor]);

  useEffect(() => {
    if (showTableBoxEditor) return;
    setGridParamsDraft({ ...gridParams });
  }, [gridParams, showTableBoxEditor]);

  useEffect(() => {
    let active = true;
    const loadFacilities = async () => {
      setFacilityOptionsLoading(true);
      setFacilityOptionsError("");
      try {
        const res = await apiClient.get("/facilities");
        if (!active) return;
        const raw = Array.isArray(res.data?.facilities) ? res.data.facilities : [];
        const normalized = raw
          .map((item) => ({
            id: String(item?.id || ""),
            name: String(item?.name || ""),
          }))
          .filter((item) => item.id);
        normalized.sort(
          (a, b) => a.name.localeCompare(b.name, "ja") || a.id.localeCompare(b.id, "ja"),
        );
        setFacilityOptions(normalized);
      } catch (err) {
        if (!active) return;
        setFacilityOptions([]);
        setFacilityOptionsError("施設一覧の取得に失敗しました。");
      } finally {
        if (active) {
          setFacilityOptionsLoading(false);
        }
      }
    };
    loadFacilities();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (reparseTimerRef.current !== null) {
        window.clearTimeout(reparseTimerRef.current);
      }
      if (orderRefreshTimerRef.current !== null) {
        window.clearTimeout(orderRefreshTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!order?.id) {
      pdfRequestSeqRef.current += 1;
      setPdfUrl("");
      setPdfError("");
      setPdfSourceKind("original");
      setPdfSourceVariant("");
      return;
    }
    const requestSeq = pdfRequestSeqRef.current + 1;
    pdfRequestSeqRef.current = requestSeq;
    let active = true;
    let objectUrl = "";
    setPdfError("");
    setPdfUrl("");
    setPdfSourceKind("original");
    setPdfSourceVariant("");
    apiClient
      .get(`/orders/${order.id}/document`, { responseType: "blob" })
      .then((res) => {
        if (!active || pdfRequestSeqRef.current !== requestSeq) return;
        const sourceHeader = String(res.headers?.["x-sawa-document-source"] || "original").trim().toLowerCase();
        const variantHeader = String(res.headers?.["x-sawa-document-variant"] || "").trim().toLowerCase();
        if (sourceHeader === "ocr_artifact") {
          setPdfError("原本FAX PDFを現在取得できません。");
          setPdfUrl("");
          setPdfSourceKind("original");
          setPdfSourceVariant("");
          return;
        }
        objectUrl = URL.createObjectURL(res.data);
        setPdfUrl(objectUrl);
        setPdfSourceKind("original");
        setPdfSourceVariant(variantHeader);
      })
      .catch((err: any) => {
        if (!active || pdfRequestSeqRef.current !== requestSeq) return;
        const status = err?.response?.status;
        setPdfError(status === 404 ? "原本FAX PDFを現在取得できません。" : "PDFの取得に失敗しました。");
        setPdfUrl("");
        setPdfSourceKind("original");
        setPdfSourceVariant("");
      });
    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [order?.id]);

  useEffect(() => {
    setOcrPreviewMode("overlay");
  }, [order?.id]);

  const normalizeSheetWarnings = (payload: { warnings?: unknown }) =>
    Array.isArray(payload.warnings)
      ? payload.warnings.map((item) => String(item || "").trim()).filter(Boolean)
      : [];

  const blankSheetCellMetadataRows = (rowCount: number, columnCount: number): string[][] =>
    Array.from({ length: Math.max(rowCount, 0) }, () => Array.from({ length: Math.max(columnCount, 0) }, () => ""));

  const normalizeSheetCellMetadataRows = (
    rows: unknown,
    rowCount: number,
    columnCount: number,
    normalizer: (value: unknown) => string = (value) => String(value ?? "").trim(),
  ): string[][] => {
    const sourceRows = Array.isArray(rows) ? rows : [];
    return Array.from({ length: Math.max(rowCount, 0) }, (_, rowIndex) => {
      const sourceRow = Array.isArray(sourceRows[rowIndex]) ? sourceRows[rowIndex] : [];
      return Array.from({ length: Math.max(columnCount, 0) }, (_, cellIndex) =>
        normalizer(sourceRow[cellIndex]),
      );
    });
  };

  const normalizeOcrCellConfidenceTier = (value: unknown): OcrCellConfidenceTier | "" => {
    const normalized = String(value ?? "").trim().toLowerCase();
    if (normalized === "high" || normalized === "medium" || normalized === "low") {
      return normalized;
    }
    return "";
  };

  const normalizeOcrNumericCellClassification = (
    value: unknown,
  ): OcrNumericCellClassification | "" => {
    const normalized = String(value ?? "").trim().toLowerCase();
    if (
      normalized === "accepted"
      || normalized === "deterministic_candidate"
      || normalized === "weak_candidate"
      || normalized === "unresolved"
    ) {
      return normalized;
    }
    return "";
  };

  const blankOcrNumericCellSummary = (): OcrNumericCellSummary => ({
    raw_ocr_numeric_count: 0,
    accepted_count: 0,
    deterministic_candidate_count: 0,
    weak_candidate_count: 0,
    unresolved_count: 0,
  });

  const normalizeOcrNumericCellSummary = (value: unknown): OcrNumericCellSummary => {
    const source = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
    const readCount = (key: string) => {
      const raw = source[key];
      if (typeof raw === "number" && Number.isFinite(raw)) return raw;
      if (typeof raw === "string") {
        const parsed = Number(raw);
        if (Number.isFinite(parsed)) return parsed;
      }
      return 0;
    };
    return {
      raw_ocr_numeric_count: readCount("raw_ocr_numeric_count"),
      accepted_count: readCount("accepted_count"),
      deterministic_candidate_count: readCount("deterministic_candidate_count"),
      weak_candidate_count: readCount("weak_candidate_count"),
      unresolved_count: readCount("unresolved_count"),
    };
  };

  const normalizeOcrNumericCellItems = (value: unknown): OcrNumericCellItem[] => {
    if (!Array.isArray(value)) return [];
    return value
      .map((raw): OcrNumericCellItem | null => {
        if (!raw || typeof raw !== "object") return null;
        const item = raw as Record<string, unknown>;
        const classification = normalizeOcrNumericCellClassification(item.classification);
        if (!classification) return null;
        const parseIndex = (key: string) => {
          const candidate = item[key];
          if (typeof candidate === "number" && Number.isInteger(candidate)) return candidate;
          if (typeof candidate === "string" && candidate.trim()) {
            const parsed = Number(candidate);
            if (Number.isInteger(parsed)) return parsed;
          }
          return null;
        };
        return {
          classification,
          value: String(item.value ?? "").trim(),
          confidence_tier: normalizeOcrCellConfidenceTier(item.confidence_tier),
          placement_basis: String(item.placement_basis ?? "").trim(),
          read_basis: String(item.read_basis ?? "").trim(),
          source_row_index: parseIndex("source_row_index"),
          source_col_index: parseIndex("source_col_index"),
          target_row_index: parseIndex("target_row_index"),
          target_col_index: parseIndex("target_col_index"),
          date_key: String(item.date_key ?? "").trim(),
          daypart_key: String(item.daypart_key ?? "").trim(),
          menu_key: String(item.menu_key ?? "").trim(),
          reason: String(item.reason ?? "").trim(),
        };
      })
      .filter((item): item is OcrNumericCellItem => Boolean(item));
  };

  const dedupeOcrSheetTouchedCells = (cells: OcrSheetTouchedCell[]): OcrSheetTouchedCell[] => {
    const seen = new Set<string>();
    const result: OcrSheetTouchedCell[] = [];
    for (const cell of cells) {
      const rowIndex = Number(cell?.rowIndex);
      const cellIndex = Number(cell?.cellIndex);
      if (!Number.isInteger(rowIndex) || rowIndex < 0 || !Number.isInteger(cellIndex) || cellIndex < 0) {
        continue;
      }
      const key = `${rowIndex}:${cellIndex}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({ rowIndex, cellIndex });
    }
    return result;
  };

  const rebuildOcrNumericCellSummary = (
    items: OcrNumericCellItem[],
    rawOcrNumericCount: number,
  ): OcrNumericCellSummary => {
    const next = blankOcrNumericCellSummary();
    next.raw_ocr_numeric_count = rawOcrNumericCount;
    for (const item of items) {
      const classification = normalizeOcrNumericCellClassification(item.classification);
      if (classification === "accepted") {
        next.accepted_count = Number(next.accepted_count || 0) + 1;
      } else if (classification === "deterministic_candidate") {
        next.deterministic_candidate_count = Number(next.deterministic_candidate_count || 0) + 1;
      } else if (classification === "weak_candidate") {
        next.weak_candidate_count = Number(next.weak_candidate_count || 0) + 1;
      } else if (classification === "unresolved") {
        next.unresolved_count = Number(next.unresolved_count || 0) + 1;
      }
    }
    return next;
  };

  const overlayClassificationVisibleInMode = (
    classification: OcrNumericCellClassification | "",
    mode: OcrConfidenceDisplayMode,
  ): boolean => {
    if (classification === "deterministic_candidate") {
      return mode === "assisted" || mode === "suggestion";
    }
    if (classification === "weak_candidate") {
      return mode === "suggestion";
    }
    return false;
  };

  const confidenceTierVisibleInMode = (
    tier: OcrCellConfidenceTier | "",
    mode: OcrConfidenceDisplayMode,
  ): boolean => {
    if (!tier) return false;
    if (tier === "high") return true;
    if (tier === "medium") return mode === "assisted" || mode === "suggestion";
    if (tier === "low") return mode === "suggestion";
    return false;
  };

  const normalizeLooseSheetEditorPayload = (payload: {
    fields?: unknown;
    header?: unknown;
    rows?: unknown;
    rowIds?: unknown;
    cellConfidenceRows?: unknown;
    cellProvenanceRows?: unknown;
    ocrNumericCellItems?: unknown;
    ocrNumericCellSummary?: unknown;
    source?: string;
    warnings?: unknown;
  }): NormalizedEditorSheetPayload => {
    const fields = Array.isArray(payload.fields)
      ? payload.fields.map((field) => String(field || "").trim()).filter(Boolean)
      : [];
    const headerCells = Array.isArray(payload.header)
      ? payload.header.map((cell) => String(cell ?? ""))
      : [];
    const rowValues = Array.isArray(payload.rows) ? payload.rows : [];
    const rowIds = Array.isArray(payload.rowIds)
      ? payload.rowIds.map((rowId) => String(rowId || "").trim())
      : [];
    const rowWidth = rowValues.reduce(
      (max, row) => Math.max(max, Array.isArray(row) ? row.length : 0),
      0,
    );
    const columnCount = Math.max(fields.length, headerCells.length, rowWidth, 1);
    const normalizedFields =
      fields.length
        ? [...fields]
        : Array.from({ length: columnCount }, (_, idx) => `col${idx + 1}`);
    if (normalizedFields.length < columnCount) {
      normalizedFields.push(
        ...Array.from(
          { length: columnCount - normalizedFields.length },
          (_, idx) => `col${normalizedFields.length + idx + 1}`,
        ),
      );
    }
    const fallbackHeader = Array.from(
      { length: columnCount },
      (_, idx) => normalizedFields[idx] || `列${idx + 1}`,
    );
    const normalizedHeader = Array.from(
      { length: columnCount },
      (_, idx) => headerCells[idx] || fallbackHeader[idx],
    );
    const normalizedRows = rowValues.map((row) => {
      const source = Array.isArray(row) ? row : [];
      return Array.from({ length: columnCount }, (_, idx) =>
        source[idx] == null ? "" : String(source[idx]),
      );
    });
    const normalizedRowIds = normalizedRows.map((_, idx) => rowIds[idx] || makeSheetRowId("sheet"));
    const warnings = normalizeSheetWarnings(payload);
    const cellConfidenceRows = blankSheetCellMetadataRows(normalizedRows.length, columnCount);
    const cellProvenanceRows = blankSheetCellMetadataRows(normalizedRows.length, columnCount);
    return {
      fields: normalizedFields,
      header: normalizedHeader,
      rows: normalizedRows,
      rowIds: normalizedRowIds,
      cellConfidenceRows,
      cellProvenanceRows,
      ocrNumericCellItems: [],
      ocrNumericCellSummary: blankOcrNumericCellSummary(),
      source: typeof payload.source === "string" ? payload.source : "",
      warnings,
    };
  };

  const normalizeCurrentSheetEditorPayload = (payload: {
    fields?: unknown;
    header?: unknown;
    rows?: unknown;
    rowIds?: unknown;
    cellConfidenceRows?: unknown;
    cellProvenanceRows?: unknown;
    ocrNumericCellItems?: unknown;
    ocrNumericCellSummary?: unknown;
    source?: string;
    warnings?: unknown;
  }): NormalizedEditorSheetPayload => {
    const fields = Array.isArray(payload.fields)
      ? payload.fields.map((field) => String(field || "").trim()).filter(Boolean)
      : [];
    const headerCells = Array.isArray(payload.header)
      ? payload.header.map((cell) => String(cell ?? ""))
      : [];
    const rowValues = Array.isArray(payload.rows) ? payload.rows : [];
    const rowIds = Array.isArray(payload.rowIds)
      ? payload.rowIds.map((rowId) => String(rowId || "").trim())
      : [];
    const warnings = normalizeSheetWarnings(payload);
    const columnCount = Math.max(fields.length, headerCells.length, 0);
    if (columnCount <= 0) {
      return {
        fields: [],
        header: [],
        rows: [],
        rowIds: [],
        cellConfidenceRows: [],
        cellProvenanceRows: [],
        ocrNumericCellItems: [],
        ocrNumericCellSummary: blankOcrNumericCellSummary(),
        source: typeof payload.source === "string" ? payload.source : "",
        warnings: rowValues.length ? Array.from(new Set([...warnings, "sheet_contract_invalid"])) : warnings,
      };
    }
    const normalizedHeader = Array.from({ length: columnCount }, (_, idx) => headerCells[idx] || fields[idx] || "");
    const normalizedRows = rowValues.map((row) => {
      const source = Array.isArray(row) ? row : [];
      return Array.from({ length: columnCount }, (_, idx) =>
        source[idx] == null ? "" : String(source[idx]),
      );
    });
    const normalizedRowIds = normalizedRows.map((_, idx) => rowIds[idx] || makeSheetRowId("sheet"));
    const cellConfidenceRows = normalizeSheetCellMetadataRows(
      payload.cellConfidenceRows,
      normalizedRows.length,
      columnCount,
      normalizeOcrCellConfidenceTier,
    );
    const cellProvenanceRows = normalizeSheetCellMetadataRows(
      payload.cellProvenanceRows,
      normalizedRows.length,
      columnCount,
    );
    return {
      fields,
      header: normalizedHeader,
      rows: normalizedRows,
      rowIds: normalizedRowIds,
      cellConfidenceRows,
      cellProvenanceRows,
      ocrNumericCellItems: normalizeOcrNumericCellItems((payload as Record<string, unknown>).ocrNumericCellItems),
      ocrNumericCellSummary: normalizeOcrNumericCellSummary((payload as Record<string, unknown>).ocrNumericCellSummary),
      source: typeof payload.source === "string" ? payload.source : "",
      warnings,
    };
  };

  const splitMarkdownTableCells = (line: string): string[] => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|") || !trimmed.includes("|")) return [];
    const content = trimmed.replace(/^\|/, "").replace(/\|$/, "");
    return content.split("|").map((cell) => cell.trim());
  };

  const isMarkdownSeparatorRow = (cells: string[]): boolean =>
    cells.length > 0 &&
    cells.every((cell) => {
      const compact = cell.replace(/\s+/g, "");
      return compact.length > 0 && /^:?-{3,}:?$/.test(compact);
    });

  const extractLargestMarkdownTable = (
    markdown: string,
  ): { header: string[]; rows: string[][] } | null => {
    const lines = markdown
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    let current: string[][] = [];
    let best: { header: string[]; rows: string[][] } | null = null;

    const finalizeCurrent = () => {
      if (current.length < 2) {
        current = [];
        return;
      }
      const header = current[0];
      const dataRows = (isMarkdownSeparatorRow(current[1]) ? current.slice(2) : current.slice(1)).filter((row) =>
        row.some((cell) => cell !== ""),
      );
      if (dataRows.length && (!best || dataRows.length > best.rows.length)) {
        best = { header, rows: dataRows };
      }
      current = [];
    };

    for (const line of lines) {
      const cells = splitMarkdownTableCells(line);
      if (cells.length) {
        current.push(cells);
      } else {
        finalizeCurrent();
      }
    }
    finalizeCurrent();
    return best;
  };

  const looksLikePreviewDateCell = (value: string): boolean =>
    /(?:^|[^0-9])\d{1,2}[/-]\d{1,2}(?:\s*[\(\（][^)）]+[\)）])?/.test(String(value || "").trim());

  const mergePreviewHeaderRows = (rows: string[][]): string[] => {
    const width = rows.reduce((max, row) => Math.max(max, row.length), 0);
    return Array.from({ length: width }, (_, idx) => {
      const parts = rows
        .map((row) => String(row[idx] ?? "").trim())
        .filter(Boolean);
      if (!parts.length) return `列${idx + 1}`;
      return parts.filter((part, partIdx) => parts.indexOf(part) === partIdx).join(" ");
    });
  };

  const buildReadonlySheetPreviewFromStructuredTables = (
    payload?: OcrOutput | null,
  ): NormalizedEditorSheetPayload | null => {
    const tableCandidates = [
      ...(Array.isArray(payload?.tables) ? payload!.tables! : []),
      ...((Array.isArray(payload?.pages)
        ? payload!.pages!.flatMap((page) => (Array.isArray(page?.tables) ? page.tables : []))
        : []) as Array<{ rows?: string[][]; row_count?: number | null; col_count?: number | null }>),
    ];
    const best = tableCandidates
      .filter((table) => Array.isArray(table?.rows) && table.rows.length)
      .sort(
        (left, right) =>
          Number(right?.row_count || right?.rows?.length || 0) - Number(left?.row_count || left?.rows?.length || 0),
      )[0];
    const rawRows = Array.isArray(best?.rows)
      ? best!.rows!
          .map((row) => (Array.isArray(row) ? row.map((cell) => String(cell ?? "")) : []))
          .filter((row) => row.some((cell) => cell.trim() !== ""))
      : [];
    if (!rawRows.length) {
      return null;
    }
    const firstDateRowIndex = rawRows.findIndex((row) => row.some((cell) => looksLikePreviewDateCell(cell)));
    const headerRows =
      firstDateRowIndex > 0
        ? rawRows.slice(0, Math.min(firstDateRowIndex, 2))
        : rawRows.slice(0, Math.min(rawRows.length, 2));
    const header = mergePreviewHeaderRows(headerRows.length ? headerRows : [rawRows[0]]);
    const dataRows = (
      firstDateRowIndex >= 0 ? rawRows.slice(firstDateRowIndex) : rawRows.slice(headerRows.length || 1)
    ).filter((row) => row.some((cell) => cell.trim() !== ""));
    if (!dataRows.length) {
      return null;
    }
    return normalizeLooseSheetEditorPayload({
      fields: Array.from({ length: Math.max(header.length, dataRows[0]?.length || 0, 1) }, (_, idx) => `col${idx + 1}`),
      header,
      rows: dataRows,
      rowIds: dataRows.map((_, idx) => `ocr-structured-preview-${idx + 1}`),
      source: "ocr_output_preview_structured",
      warnings: ["ocr_preview_only"],
    });
  };

  const applyExpandedCellCopyModeToPayload = (
    payload: NormalizedEditorSheetPayload,
    mode: ExpandedCellCopyMode = expandedCellCopyMode,
  ): NormalizedEditorSheetPayload => {
    const shouldCopy = mode === "enabled" || mode === "persisted";
    if (!shouldCopy) {
      return payload;
    }
    return {
      ...payload,
      rows: applyExpandedCellSameDaypartCopyToRows({
        fields: payload.fields,
        header: payload.header,
        rows: payload.rows,
      }),
      cellConfidenceRows: blankSheetCellMetadataRows(payload.rows.length, payload.header.length),
      cellProvenanceRows: blankSheetCellMetadataRows(payload.rows.length, payload.header.length),
      ocrNumericCellItems: [],
      ocrNumericCellSummary: blankOcrNumericCellSummary(),
    };
  };

  const applyNormalizedSheetEditorPayload = (payload: NormalizedEditorSheetPayload) => {
    ocrSheetBaselinePayloadRef.current = payload;
    ocrSheetEditedSinceLoadRef.current = false;
    const effectivePayload = applyExpandedCellCopyModeToPayload(payload);
    setOcrSheetFields(effectivePayload.fields);
    setOcrSheetHeader(effectivePayload.header);
    setOcrSheetRows(effectivePayload.rows);
    setOcrSheetRowIds(effectivePayload.rowIds);
    setOcrSheetCellConfidenceRows(effectivePayload.cellConfidenceRows);
    setOcrSheetCellProvenanceRows(effectivePayload.cellProvenanceRows);
    setOcrSheetNumericCellItems(effectivePayload.ocrNumericCellItems);
    setOcrSheetNumericCellSummary(effectivePayload.ocrNumericCellSummary);
    setOcrSheetSource(effectivePayload.source);
    setOcrSheetWarnings(effectivePayload.warnings);
  };

  useEffect(() => {
    const baselinePayload = ocrSheetBaselinePayloadRef.current;
    if (!baselinePayload || ocrSheetEditedSinceLoadRef.current) {
      return;
    }
    const effectivePayload = applyExpandedCellCopyModeToPayload(baselinePayload, expandedCellCopyMode);
    setOcrSheetFields(effectivePayload.fields);
    setOcrSheetHeader(effectivePayload.header);
    setOcrSheetRows(effectivePayload.rows);
    setOcrSheetRowIds(effectivePayload.rowIds);
    setOcrSheetCellConfidenceRows(effectivePayload.cellConfidenceRows);
    setOcrSheetCellProvenanceRows(effectivePayload.cellProvenanceRows);
    setOcrSheetNumericCellItems(effectivePayload.ocrNumericCellItems);
    setOcrSheetNumericCellSummary(effectivePayload.ocrNumericCellSummary);
    setOcrSheetSource(effectivePayload.source);
    setOcrSheetWarnings(effectivePayload.warnings);
  }, [expandedCellCopyMode]);

  const normalizeDraftSheetPayload = (payload?: DraftSheetPayload | null): NormalizedEditorSheetPayload => {
    const draftSheetJson =
      payload && typeof payload.draft_sheet_json === "object" && payload.draft_sheet_json
        ? payload.draft_sheet_json
        : null;
    return normalizeCurrentSheetEditorPayload({
      fields: draftSheetJson?.fields ?? payload?.fields,
      header: draftSheetJson?.header ?? payload?.header,
      rows: draftSheetJson?.rows ?? payload?.rows,
      rowIds: draftSheetJson?.row_ids ?? draftSheetJson?.rowIds ?? payload?.row_ids,
      cellConfidenceRows: draftSheetJson?.cell_confidence_rows ?? payload?.cell_confidence_rows,
      cellProvenanceRows: draftSheetJson?.cell_provenance_rows ?? payload?.cell_provenance_rows,
      ocrNumericCellItems: draftSheetJson?.ocr_numeric_cell_items ?? payload?.ocr_numeric_cell_items,
      ocrNumericCellSummary: draftSheetJson?.ocr_numeric_cell_summary ?? payload?.ocr_numeric_cell_summary,
      source: String(draftSheetJson?.source || payload?.source || payload?.draft_state || "draft").trim() || "draft",
      warnings: [
        ...(Array.isArray(draftSheetJson?.warnings) ? draftSheetJson!.warnings! : []),
        ...(Array.isArray(payload?.warnings) ? payload!.warnings! : []),
        ...(Array.isArray(payload?.warnings_json) ? payload!.warnings_json! : []),
      ],
    });
  };

  const buildReadonlySheetPreviewFromOcrOutput = (
    payload?: OcrOutput | null,
  ): NormalizedEditorSheetPayload | null => {
    const editedHeader = Array.isArray(payload?.edited_table?.header)
      ? payload!.edited_table!.header!.map((cell) => String(cell ?? ""))
      : [];
    const editedRows = Array.isArray(payload?.edited_table?.rows)
      ? payload!.edited_table!.rows!
      : [];
    if (editedRows.length) {
      return normalizeLooseSheetEditorPayload({
        fields: Array.from(
          { length: Math.max(editedHeader.length, editedRows[0]?.length || 0, 1) },
          (_, idx) => `col${idx + 1}`,
        ),
        header: editedHeader,
        rows: editedRows,
        rowIds:
          payload?.edited_table?.row_ids
          ?? editedRows.map((_, idx) => `ocr-edited-preview-${idx + 1}`),
        source: "ocr_output_preview",
        warnings: ["ocr_preview_only"],
      });
    }
    const structuredPreview = buildReadonlySheetPreviewFromStructuredTables(payload);
    if (structuredPreview) {
      return structuredPreview;
    }
    const tableRaw = typeof payload?.table_raw === "string" ? payload.table_raw.trim() : "";
    if (!tableRaw) {
      return null;
    }
    const parsedTable = extractLargestMarkdownTable(tableRaw);
    if (!parsedTable || !parsedTable.rows.length) {
      return null;
    }
    const columnCount = Math.max(
      parsedTable.header.length,
      parsedTable.rows.reduce((max, row) => Math.max(max, row.length), 0),
      1,
    );
    return normalizeLooseSheetEditorPayload({
      fields: Array.from({ length: columnCount }, (_, idx) => `col${idx + 1}`),
      header:
        parsedTable.header.length === columnCount
          ? parsedTable.header
          : Array.from({ length: columnCount }, (_, idx) => parsedTable.header[idx] ?? `列${idx + 1}`),
      rows: parsedTable.rows,
      rowIds: parsedTable.rows.map((_, idx) => `ocr-output-preview-${idx + 1}`),
      source: "ocr_output_preview",
      warnings: ["ocr_preview_only"],
    });
  };

  const renderReadonlySheetPreview = (
    preview: NormalizedEditorSheetPayload | null,
    {
      title,
      note,
      emptyMessage = "",
      className = "ocr-candidate-preview",
    }: {
      title: string;
      note: string;
      emptyMessage?: string;
      className?: string;
    },
  ) => {
    if (!preview?.rows.length) {
      return emptyMessage ? <p className="subtle ocr-remediation-empty">{emptyMessage}</p> : null;
    }
    return (
      <div className={className}>
        <p className="ocr-evidence-switch-title">{title}</p>
        <p className="subtle">{note}</p>
        <div className="ocr-sheet-wrap">
          <table className="ocr-sheet-table ocr-sheet-table--readonly">
            <thead>
              <tr>
                <th className="ocr-sheet-row-index ocr-sheet-sticky-top">#</th>
                {preview.header.map((cell, idx) => (
                  <th key={`${className}-header-${idx}`} className="ocr-sheet-sticky-top">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((row, rowIdx) => (
                <tr
                  key={`${className}-row-${rowIdx}`}
                  className={[
                    "ocr-sheet-row",
                    rowIdx % 2 === 0 ? "ocr-sheet-row-date-a" : "ocr-sheet-row-date-b",
                  ].join(" ")}
                >
                  <th className="ocr-sheet-row-index">{rowIdx + 1}</th>
                  {preview.header.map((_, cellIdx) => (
                    <td key={`${className}-cell-${rowIdx}-${cellIdx}`}>
                      <div className="ocr-sheet-preview-cell">{row[cellIdx] ?? ""}</div>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const resetSheetReviewMeta = () => {
    setOcrSheetProjection(null);
    setOcrSheetReviewState("");
    setOcrSheetCanApply(false);
    setOcrSheetCanConfirm(false);
    setOcrSheetApplyBlockers([]);
    setOcrSheetConfirmBlockers([]);
    setOcrSheetConfirmWarnings([]);
    setOcrSheetDraftNewerThanLines(false);
    setOcrSheetAutoApplyBlockedState(false);
    setOcrSheetProcessingStage("");
    setOcrSheetResultState("");
    setOcrSheetConfirmedLinesRetained(false);
  };

  const applySheetReviewMeta = (payload?: Partial<OcrSheetPayload> | null) => {
    const applyBlockers = Array.isArray(payload?.apply_blockers)
      ? payload!.apply_blockers!.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const confirmBlockers = Array.isArray(payload?.confirm_blockers)
      ? payload!.confirm_blockers!.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const confirmWarnings = Array.isArray(payload?.confirm_warnings)
      ? payload!.confirm_warnings!.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    setOcrSheetProjection(payload?.sheet_projection || null);
    setOcrSheetReviewState(String(payload?.review_state || "").trim());
    setOcrSheetCanApply(Boolean(payload?.can_apply));
    setOcrSheetCanConfirm(Boolean(payload?.can_confirm));
    setOcrSheetApplyBlockers(applyBlockers);
    setOcrSheetConfirmBlockers(confirmBlockers);
    setOcrSheetConfirmWarnings(confirmWarnings);
    setOcrSheetDraftNewerThanLines(Boolean(payload?.draft_newer_than_lines));
    setOcrSheetAutoApplyBlockedState(Boolean(payload?.auto_apply_blocked));
    setOcrSheetProcessingStage(String(payload?.processing_stage || "").trim());
    setOcrSheetResultState(String(payload?.result_state || "").trim());
    setOcrSheetConfirmedLinesRetained(Boolean(payload?.confirmed_lines_retained));
  };

  const buildSheetReviewMetaFromOrderState = (
    detail?: OrderDetail | null,
    draftPayload?: DraftSheetPayload | null,
  ): Partial<OcrSheetPayload> => {
    const workflow = detail?.workflow_state || draftPayload?.workflow_state || null;
    const applyGate = detail?.apply_gate || workflow?.apply_gate || draftPayload?.apply_gate || null;
    const hasWorkflowState = Boolean(
      workflow
      && (
        String(workflow.state || "").trim()
        || String(workflow.headline || "").trim()
        || applyGate
      ),
    );
    const reviewState =
      String(
        draftPayload?.review_state
          || draftPayload?.draft_state
          || workflow?.state
          || (!hasWorkflowState ? detail?.ocr_review_state : "")
          || "",
      ).trim() ||
      "draft_ready";
    return {
      review_state: reviewState,
      can_apply:
        applyGate?.can_apply != null
          ? Boolean(applyGate.can_apply)
          : (!hasWorkflowState && Boolean(detail?.ocr_can_apply_draft)),
      can_confirm:
        applyGate?.can_confirm != null
          ? Boolean(applyGate.can_confirm)
          : (!hasWorkflowState && Boolean(detail?.ocr_can_confirm)),
      apply_blockers: Array.isArray(applyGate?.blockers)
        ? applyGate!.blockers!
        : !hasWorkflowState && Array.isArray(detail?.ocr_apply_blockers)
          ? detail!.ocr_apply_blockers!
          : [],
      confirm_blockers: Array.isArray(applyGate?.blockers)
        ? applyGate!.blockers!
        : !hasWorkflowState && Array.isArray(detail?.ocr_confirm_blockers)
          ? detail!.ocr_confirm_blockers!
          : [],
      confirm_warnings: Array.isArray(applyGate?.warnings)
        ? applyGate!.warnings!
        : !hasWorkflowState && Array.isArray(detail?.ocr_confirm_warnings)
          ? detail!.ocr_confirm_warnings!
          : [],
      draft_newer_than_lines: !hasWorkflowState && Boolean(detail?.ocr_draft_newer_than_lines),
      auto_apply_blocked: !hasWorkflowState && Boolean(detail?.ocr_auto_apply_blocked),
      processing_stage: !hasWorkflowState ? String(detail?.ocr_processing_stage || "").trim() : "",
      result_state: !hasWorkflowState ? String(detail?.ocr_result_state || "").trim() : "",
      confirmed_lines_retained: !hasWorkflowState && Boolean(detail?.ocr_confirmed_lines_retained),
      sheet_projection: draftPayload?.sheet_projection || null,
    };
  };

  const findLatestSheetRevision = (
    latest: OcrEditRevision | null,
    revisions: OcrEditRevision[],
  ): OcrEditRevision | null => {
    const seenRevisionIds = new Set<string>();
    const candidates: OcrEditRevision[] = [];
    revisions.forEach((revision) => {
      const revisionId = String(revision.revision_id || "").trim();
      if (revisionId) {
        seenRevisionIds.add(revisionId);
      }
      candidates.push(revision);
    });
    if (latest) {
      const latestRevisionId = String(latest.revision_id || "").trim();
      if (!latestRevisionId || !seenRevisionIds.has(latestRevisionId)) {
        candidates.push(latest);
      }
    }
    for (let idx = candidates.length - 1; idx >= 0; idx -= 1) {
      const revision = candidates[idx];
      if (revision?.ui_mode !== "sheet" || !Array.isArray(revision.rows)) {
        continue;
      }
      return revision;
    }
    return null;
  };

  const refreshOcrOutput = async (orderId: string) => {
    setOcrOutputMessage("OCR結果を取得中...");
    try {
      const res = await apiClient.get(`/orders/${orderId}/ocr-output`, {
        params: { _ts: Date.now() },
      });
      if (res.status === 202 || res.data?.pending) {
        setOcrOutput(null);
        setOcrOutputMessage("OCR結果は処理中です。");
        return;
      }
      setOcrOutput(res.data);
      setOcrOutputMessage("");
    } catch (err: any) {
      if (err?.response?.status === 404) {
        setOcrOutputMessage("OCR結果はまだありません。");
      } else {
        setOcrOutputMessage("OCR結果の取得に失敗しました。");
      }
      setOcrOutput(null);
    }
  };

  const resolveWeekDraftFromCanonicalSelection = (
    currentDraft: string,
    options: WeekOption[],
    persistedWeekSource?: string | null,
  ) => {
    const normalizedCurrent = normalizeWeekValue(currentDraft);
    const concreteCurrent = normalizeConcreteWeekValue(currentDraft);
    const persistedWeekValue = normalizeWeekValue(persistedWeekSource || "");
    const selectedOption = options.find((item) => item.selected && item.week_id);
    const preserveDirtyWeekSelection = Boolean(
      normalizedCurrent && normalizedCurrent !== persistedWeekValue,
    );
    if (preserveDirtyWeekSelection) {
      return concreteCurrent || normalizedCurrent || currentDraft;
    }
    if (concreteCurrent) {
      return concreteCurrent;
    }
    if (selectedOption?.week_id) {
      return selectedOption.week_id;
    }
    return normalizedCurrent || "";
  };

  const getCanonicalWeekSelectionSource = (currentOrder?: OrderDetail | null) =>
    normalizeWeekValue(
      currentOrder?.persisted_week_value || currentOrder?.week_value || currentOrder?.week || "",
    );

  const getPendingStep1WeekSelection = (
    currentWeekDraft: string,
    rangeStart: string,
    rangeEnd: string,
  ) => {
    const concreteCurrent = normalizeConcreteWeekValue(currentWeekDraft);
    if (concreteCurrent) return concreteCurrent;
    const explicitRange = normalizeConcreteWeekValue(
      deriveWeekValueFromCalendarRange(rangeStart, rangeEnd),
    );
    if (explicitRange) return explicitRange;
    return normalizeConcreteWeekValue(
      deriveWeekValueFromCalendarDate(rangeStart || rangeEnd),
    );
  };

  const loadWeekOptions = async (
    orderId: string,
    options: { silent?: boolean; persistedWeekSource?: string | null } = {},
  ) => {
    const { silent = false, persistedWeekSource } = options;
    if (!silent) {
      setWeekOptionsLoading(true);
      setWeekOptionsError("");
    }
    try {
      const res = await apiClient.get(`/orders/${orderId}/week-options`);
      const options = Array.isArray(res.data?.options)
        ? res.data.options
            .map((item) => ({
              week_id: normalizeWeekValue(item?.week_id),
              label: String(item?.label || item?.week_id || ""),
              date_from: typeof item?.date_from === "string" ? item.date_from : null,
              date_to: typeof item?.date_to === "string" ? item.date_to : null,
              selected: Boolean(item?.selected),
            }))
            .filter((item) => item.week_id)
        : [];
      const canonicalPersistedWeekSource =
        normalizeWeekValue(persistedWeekSource ?? "") || getCanonicalWeekSelectionSource(order);
      setWeekOptions(options);
      setWeekDraft((current) => {
        return resolveWeekDraftFromCanonicalSelection(
          current,
          options,
          canonicalPersistedWeekSource,
        );
      });
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setWeekOptions([]);
      } else if (!silent) {
        setWeekOptionsError("週候補の取得に失敗しました。必要なら例外範囲を設定してください。");
      }
    } finally {
      if (!silent) {
        setWeekOptionsLoading(false);
      }
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    loadWeekOptions(order.id, { persistedWeekSource: getCanonicalWeekSelectionSource(order) });
  }, [order?.id]);

  useEffect(() => {
    const nextWeekDraft = resolveWeekDraftFromCanonicalSelection(
      weekDraft,
      weekOptions,
      getCanonicalWeekSelectionSource(order),
    );
    if (nextWeekDraft && nextWeekDraft !== weekDraft) {
      setWeekDraft(nextWeekDraft);
    }
  }, [order?.persisted_week_value, order?.week, order?.week_value, weekDraft, weekOptions]);

  useEffect(() => {
    const normalizedWeek = normalizeConcreteWeekValue(weekDraft);
    const match = normalizedWeek.match(/^\d{4}-\d{2}@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
    if (!match) {
      setCustomWeekRangeStart("");
      setCustomWeekRangeEnd("");
      return;
    }
    setCustomWeekRangeStart(match[1]);
    setCustomWeekRangeEnd(match[2]);
  }, [weekDraft]);

  const refreshOrderWorkspace = async (
    options: {
      preserveSelections?: boolean;
      reloadSheet?: boolean;
      reloadOcrPages?: boolean;
      reloadHistory?: boolean;
      reloadBags?: boolean;
      reloadWeekOptions?: boolean;
      silent?: boolean;
      force?: boolean;
    } = {},
  ) => {
    const {
      preserveSelections = true,
      reloadSheet = false,
      reloadOcrPages = false,
      reloadHistory = false,
      reloadBags = false,
      reloadWeekOptions = true,
      force = false,
    } = options;
    if (!id) return null;
    if (!force && workspaceRefreshPromiseRef.current) {
      return workspaceRefreshPromiseRef.current;
    }
    const orderId = String(id);
    const refreshToken = workspaceRefreshTokenRef.current + 1;
    workspaceRefreshTokenRef.current = refreshToken;
    const refreshPromise = (async () => {
      const nextOrder = await loadOrderDetail(orderId, { preserveSelections });
      const followupTasks: Promise<unknown>[] = [];
      if (reloadWeekOptions) {
        followupTasks.push(
          loadWeekOptions(orderId, {
            silent: true,
            persistedWeekSource: getCanonicalWeekSelectionSource(nextOrder),
          }),
        );
      }
      if (reloadHistory) {
        followupTasks.push(loadOcrHistory({ silent: true }));
        followupTasks.push(loadOrderHistory({ silent: true }));
      }
      if (reloadSheet) {
        followupTasks.push(loadOcrSheet({ silent: true }));
      }
      if (reloadOcrPages) {
        followupTasks.push(loadOcrPages({ silent: true, force: true }));
      }
      if (reloadBags) {
        followupTasks.push(loadBags());
      }
      if (followupTasks.length) {
        await Promise.all(followupTasks);
      }
      return nextOrder;
    })();
    workspaceRefreshPromiseRef.current = refreshPromise;
    try {
      return await refreshPromise;
    } finally {
      if (
        workspaceRefreshPromiseRef.current === refreshPromise &&
        workspaceRefreshTokenRef.current === refreshToken
      ) {
        workspaceRefreshPromiseRef.current = null;
      }
    }
  };

  const safeRefreshOrderWorkspace = async (
    options: {
      preserveSelections?: boolean;
      reloadSheet?: boolean;
      reloadHistory?: boolean;
      reloadBags?: boolean;
      reloadWeekOptions?: boolean;
    } = {},
    failureMessage?: string,
  ) => {
    try {
      return await refreshOrderWorkspace(options);
    } catch {
      setActionMessage(failureMessage || "最新状態の取得に失敗しました。");
      return null;
    }
  };

  useEffect(() => {
    if (!id) return;
    const hasUnsavedFacilitySelection = Boolean(
      facility.trim() && facility.trim() !== String(order?.facility || "").trim(),
    );
    const hasUnsavedWeekSelection = Boolean(
      getPendingStep1WeekSelection(weekDraft, customWeekRangeStart, customWeekRangeEnd)
      && getPendingStep1WeekSelection(weekDraft, customWeekRangeStart, customWeekRangeEnd)
        !== normalizeConcreteWeekValue(getCanonicalWeekSelectionSource(order)),
    );
    const scheduleRefresh = () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") {
        return;
      }
      if (reparsePending) {
        return;
      }
      if (lineEditsDirty) {
        return;
      }
      if (hasUnsavedFacilitySelection || hasUnsavedWeekSelection || step1Saving) {
        return;
      }
      void safeRefreshOrderWorkspace(
        { preserveSelections: true, reloadWeekOptions: false },
        "最新状態の取得に失敗しました。",
      );
    };
    const handleFocus = () => {
      scheduleRefresh();
    };
    const handleVisibilityChange = () => {
      if (typeof document === "undefined" || document.visibilityState !== "visible") {
        return;
      }
      scheduleRefresh();
    };
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (orderRefreshTimerRef.current !== null) {
        window.clearTimeout(orderRefreshTimerRef.current);
        orderRefreshTimerRef.current = null;
      }
    };
  }, [
    id,
    reparsePending,
    lineEditsDirty,
    step1Saving,
    order?.facility,
    order?.persisted_week_value,
    order?.week_value,
    order?.week,
    facility,
    weekDraft,
  ]);

  const loadOcrSheet = async (
    options: { silent?: boolean; force?: boolean } = {},
  ): Promise<{
    fields: string[];
    header: string[];
    rows: string[][];
    rowIds: string[];
    source: string;
  } | null> => {
  if (!order) return null;
  const { silent = false, force = false } = options;
  if (ocrSheetLoading && !force) return null;
    const requestSeq = ocrSheetRequestSeqRef.current + 1;
    ocrSheetRequestSeqRef.current = requestSeq;
    if (!silent) {
      setOcrSheetMessage("シートを取得中...");
    }
    setOcrSheetAutoRetryBlocked(false);
    setOcrSheetLoadSettled(false);
    setOcrSheetLoading(true);
    try {
      const res = await apiClient.get(`/orders/${order.id}/draft-sheet`, {
        params: {
          compact: 1,
          quantity_assignment_strategy: "hakodate",
        },
        timeout: 120000,
      });
      const payload = (res.data || {}) as DraftSheetPayload;
      const normalizedPayload = normalizeDraftSheetPayload(payload);
      if (requestSeq !== ocrSheetRequestSeqRef.current || requestSeq < ocrSheetAppliedSeqRef.current) {
        return ocrSheetBaselinePayloadRef.current;
      }
      if (silent && ocrSheetEditedSinceLoadRef.current) {
        return ocrSheetBaselinePayloadRef.current;
      }
      ocrSheetAppliedSeqRef.current = requestSeq;
      setHakodateAssignment(asHakodateAssignmentPayload(payload.hakodate_assignment));
      setHakodateProjectionMetrics(asHakodateMetricsPayload(payload.hakodate_projection_metrics));
      applyNormalizedSheetEditorPayload(normalizedPayload);
      applySheetReviewMeta(buildSheetReviewMetaFromOrderState(order, payload));
      setOcrSheetAutoRetryBlocked(false);
      if (!silent) {
        const reviewStateLabel = describeReviewState(
          String(payload.review_state || payload.draft_state || order?.workflow_state?.state || "").trim(),
        );
        setOcrSheetMessage(
          reviewStateLabel
            ? `箱館方式: ${reviewStateLabel}のシートを読み込みました。`
            : normalizedPayload.rows.length
              ? normalizedPayload.source.startsWith("edited_sheet")
                ? "保存済みシートを読み込みました。"
                : "箱館方式のシートを取得しました。"
              : "シートは取得しましたが、編集対象の行がありません。",
        );
      }
      return normalizedPayload;
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      setHakodateAssignment(null);
      setHakodateProjectionMetrics(null);
      setOcrSheetAutoRetryBlocked(true);
      resetSheetReviewMeta();
      if (!silent || status === 400 || status === 404) {
        if (status === 404) {
          setOcrSheetMessage("シートを取得できませんでした。施設設定またはメニューを確認してください。");
        } else if (status === 400) {
          if (detail === "facility_missing") {
            setOcrSheetMessage("シートを生成できませんでした。施設が未設定です。先に施設を設定してください。");
          } else if (detail === "menu_entries_missing" || detail === "week_unresolved") {
            setOcrSheetMessage("シートを生成できませんでした。週次メニューを参照できません。メニュー設定を確認してください。");
          } else if (detail === "sheet_week_dates_incomplete") {
            setOcrSheetMessage("シートを生成できませんでした。週次メニューの日付に欠落があります（OCR日付範囲の中間日が不足）。再解析またはメニュー設定を確認してください。");
          } else if (detail === "sheet_quantity_column_unmapped") {
            setOcrSheetMessage("シートを生成できませんでした。施設区分（数量列）にマップできないOCR行があります。施設テンプレートの列定義を確認してください。");
          } else if (detail === "sheet_fields_duplicate") {
            setOcrSheetMessage("シートを生成できませんでした。施設テンプレートの列定義に重複があります。管理画面で修正してください。");
          } else if (detail === "sheet_template_field_invalid") {
            setOcrSheetMessage("シートを生成できませんでした。施設テンプレートの列定義が不正です。管理画面で修正してください。");
          } else if (detail === "sheet_quantity_columns_missing") {
            setOcrSheetMessage("シートを生成できませんでした。施設テンプレートに数量列(qty.*)がありません。管理画面で修正してください。");
          } else if (detail === "week_menu_date_mismatch" || detail === "sheet_date_mismatch") {
            setOcrSheetMessage("シートを生成できませんでした。OCRの日付と週次メニューの日付が一致しません。週次メニュー設定を確認してください。");
          } else {
            setOcrSheetMessage("シートを生成できませんでした。施設設定を確認してください。");
          }
        } else {
          setOcrSheetMessage("シートの取得に失敗しました。");
        }
      }
      return null;
    } finally {
      if (requestSeq === ocrSheetRequestSeqRef.current) {
        setOcrSheetLoading(false);
        setOcrSheetLoadSettled(true);
      }
    }
  };

  const loadCandidateSheetPreview = async (options: { silent?: boolean } = {}): Promise<NormalizedEditorSheetPayload | null> => {
    if (!order) return null;
    if (candidateSheetPreviewLoading) return null;
    const { silent = false } = options;
    if (!silent) {
      setCandidateSheetPreviewMessage("候補シートを取得中...");
    }
    setCandidateSheetPreviewLoading(true);
    try {
      const res = await apiClient.get(`/orders/${order.id}/draft-sheet/candidate-preview`);
      const payload = normalizeDraftSheetPayload((res.data || {}) as DraftSheetPayload);
      setCandidateSheetPreview(payload);
      setCandidateSheetPreviewMessage(
        payload.rows.length ? "" : "候補シートは取得しましたが、表示できる行がありません。",
      );
      return payload;
    } catch (err: any) {
      const status = err?.response?.status;
      setCandidateSheetPreview(null);
      if (status === 404) {
        setCandidateSheetPreviewMessage(silent ? "" : "候補シートはまだありません。");
      } else if (status === 409) {
        setCandidateSheetPreviewMessage(
          silent ? "" : "候補シートはまだ表示できません。最新状態を読み直してください。",
        );
      } else {
        setCandidateSheetPreviewMessage(
          silent ? "" : "候補シートの取得に失敗しました。",
        );
      }
      return null;
    } finally {
      setCandidateSheetPreviewLoading(false);
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    if (!(order.facility || "").trim()) {
      setOcrSheetAutoRetryBlocked(true);
      setOcrSheetMessage("シートを生成できません。先に Step1（注文書）で施設設定を完了してください。");
      return;
    }
    if (!normalizeConcreteWeekValue(order.persisted_week_value || order.week_value || order.week || "")) {
      setOcrSheetAutoRetryBlocked(true);
      setOcrSheetMessage("シートを生成できません。先に Step1（注文書）で週を設定してください。");
      return;
    }
    loadOcrSheet({ silent: true });
  }, [order?.id, order?.facility, order?.week, order?.week_value, order?.persisted_week_value]);

  const loadOcrHistory = async (options: { silent?: boolean } = {}) => {
    if (!order) return;
    const { silent = false } = options;
    if (!silent) {
      setOcrHistoryMessage("履歴を取得中...");
    }
    setOcrHistoryLoading(true);
    try {
      const res = await apiClient.get(`/orders/${order.id}/ocr-history`);
      const payload = (res.data || {}) as OcrHistoryPayload;
      const latest =
        payload.latest && typeof payload.latest === "object" ? (payload.latest as OcrEditRevision) : null;
      const revisions = Array.isArray(payload.revisions)
        ? payload.revisions.filter((item): item is OcrEditRevision => Boolean(item && typeof item === "object"))
        : [];
      const latestSheetRevision = findLatestSheetRevision(latest, revisions);
      latestSavedSheetRevisionRef.current = latestSheetRevision;
      setOcrHistoryLatest(latest);
      setOcrHistoryRows(revisions);
      if (!silent) {
        setOcrHistoryMessage(
          revisions.length
            ? `履歴を取得しました (${revisions.length}件)。`
            : "履歴はまだありません。",
        );
      }
    } catch (err: any) {
      const status = err?.response?.status;
      latestSavedSheetRevisionRef.current = null;
      setOcrHistoryLatest(null);
      setOcrHistoryRows([]);
      if (!silent) {
        if (status === 404) {
          setOcrHistoryMessage("履歴はまだありません。");
        } else {
          setOcrHistoryMessage("履歴の取得に失敗しました。");
        }
      }
    } finally {
      setOcrHistoryLoading(false);
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    loadOcrHistory({ silent: true });
  }, [order?.id]);

  const loadOrderHistory = async (options: { silent?: boolean } = {}) => {
    if (!order) return;
    const { silent = false } = options;
    if (!silent) {
      setOrderHistoryMessage("注文履歴を取得中...");
    }
    setOrderHistoryLoading(true);
    try {
      const res = await apiClient.get(`/orders/${order.id}/history?limit=100`);
      const payload = (res.data || {}) as OrderHistoryPayload;
      const items = Array.isArray(payload.items)
        ? payload.items.filter((item): item is OrderHistoryItem => Boolean(item && typeof item === "object"))
        : [];
      setOrderHistoryRows(items);
      if (!silent) {
        setOrderHistoryMessage(items.length ? `注文履歴を取得しました (${items.length}件)。` : "注文履歴はまだありません。");
      }
    } catch (err: any) {
      const status = err?.response?.status;
      setOrderHistoryRows([]);
      if (!silent) {
        if (status === 404) {
          setOrderHistoryMessage("注文履歴はまだありません。");
        } else {
          setOrderHistoryMessage("注文履歴の取得に失敗しました。");
        }
      }
    } finally {
      setOrderHistoryLoading(false);
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    loadOrderHistory({ silent: true });
  }, [order?.id]);

  const currentSheetRevisionIdForMutation = () =>
    String(
      order?.workflow_state?.current_sheet_revision_id ||
        order?.current_sheet_revision_id ||
        "",
    ).trim();

  const handleRemoteUpdateConflict = async (
    message: string,
    options: { reloadSheet?: boolean; reloadHistory?: boolean; reloadBags?: boolean } = {},
  ) => {
    setLineEditsDirty(false);
    await safeRefreshOrderWorkspace(
      {
        preserveSelections: false,
        reloadSheet: options.reloadSheet,
        reloadHistory: options.reloadHistory,
        reloadBags: options.reloadBags,
      },
      "最新状態の再読込に失敗しました。",
    );
    setActionMessage(message);
    if (options.reloadSheet) {
      setOcrTableMessage(message);
    }
  };

  const loadOcrRaw = async () => {
    if (!order) return;
    setOcrRawLoading(true);
    setOcrRawMessage("生OCRを取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/ocr-raw`);
      const rawText = typeof res.data?.raw_text === "string" ? res.data.raw_text : "";
      setOcrRawText(rawText);
      setOcrRawMessage(rawText ? "生OCRを取得しました。" : "生OCRが空です。");
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setOcrRawMessage("生OCRが見つかりません。再解析後に取得してください。");
      } else {
        setOcrRawMessage("生OCRの取得に失敗しました。");
      }
    } finally {
      setOcrRawLoading(false);
    }
  };

  const setTableFromPage = (page: OcrPage | undefined, fallbackIndex?: number) => {
    if (!page) {
      setOcrTableHeader([]);
      setOcrTableRows([]);
      setOcrTablePageIndex(null);
      setOcrTableMessage("OCRページが未取得です。");
      return;
    }
    const table = extractTableFromPage(page);
    const pageIndex = page.page_index ?? (fallbackIndex != null ? fallbackIndex + 1 : null);
    setOcrTablePageIndex(pageIndex);
    if (table) {
      setOcrTableHeader(table.header);
      setOcrTableRows(table.rows.map((row) => [...row]));
      setOcrTableMessage("");
    } else {
      setOcrTableHeader([]);
      setOcrTableRows([]);
      setOcrTableMessage("編集できる表が見つかりません。");
    }
  };

  const buildGridParams = (raw: any): GridParams => {
    const next = { ...defaultGridParams };
    if (!raw || typeof raw !== "object") return next;
    Object.keys(next).forEach((key) => {
      const value = raw[key];
      if (typeof value === "number" && Number.isFinite(value)) {
        (next as Record<string, number>)[key] = value;
      } else if (typeof value === "string") {
        const parsed = Number(value);
        if (!Number.isNaN(parsed)) {
          (next as Record<string, number>)[key] = parsed;
        }
      }
    });
    return next;
  };

  const loadHakodateOverlayPreview = async (
    options: { silent?: boolean; force?: boolean } = {},
  ) => {
    const currentOrder = authoritativeOrderRef.current || order;
    const orderId = String(currentOrder?.id || "").trim();
    if (!orderId) return;
    const { silent = false, force = false } = options;
    if (hakodateOverlayLoading && !force) return;
    setHakodateOverlayLoading(true);
    if (!silent && !hakodateOverlayUrl) {
      setOcrPagesMessage("箱館オーバーレイを取得中...");
    }
    try {
      const res = await apiClient.get(`/orders/${orderId}/hakodate-overlay-preview`, {
        timeout: 30000,
      });
      const payload = (res.data || {}) as HakodateOverlayPreviewPayload;
      const nextStatus = typeof payload.status === "string" ? payload.status : "";
      const nextBlockers = Array.isArray(payload.blockers)
        ? payload.blockers.map((item: unknown) => String(item || "").trim()).filter(Boolean)
        : [];
      const nextMessage = typeof payload.message === "string" ? payload.message : "";
      const nextOverlayUrl = typeof payload.overlay_url === "string" ? payload.overlay_url.trim() : "";
      setHakodateOverlayStatus(nextStatus);
      setHakodateOverlayBlockers(nextBlockers);
      setHakodateOverlayMessage(nextMessage);
      setHakodateOverlayUrl(nextOverlayUrl);
      setHakodateJobStatus(asReparseStatePayload(payload.job_status));
      const nextAssignment = asHakodateAssignmentPayload(payload.assignment);
      if (nextAssignment) {
        setHakodateAssignment(nextAssignment);
        setHakodateProjectionMetrics(asHakodateMetricsPayload(nextAssignment.metrics));
      }
      ocrPreviewRefreshKeyRef.current = [
        orderId,
        String(currentOrder?.current_sheet_revision_id || ""),
        String(currentOrder?.ocr_updated_at || ""),
        String(currentOrder?.ocr_result_state || ""),
        String(currentOrder?.workflow_state?.current_sheet_revision_id || ""),
        String(currentOrder?.workflow_state?.active_evidence_run_id || ""),
        String(currentOrder?.workflow_state?.candidate_evidence_run_id || ""),
        String(Boolean(currentOrder?.workflow_state?.candidate_prompt_visible)),
        String(currentOrder?.workflow_state?.reparse_state?.status || ""),
      ].join("|");
      if (nextOverlayUrl && !ocrPages.length) {
        setOcrPagesMessage("");
      }
    } catch {
      if (!silent) {
        setOcrPagesMessage("箱館オーバーレイの取得に失敗しました。");
      }
      if (!hakodateOverlayUrl) {
        setHakodateOverlayStatus("blocked");
        setHakodateOverlayBlockers(["hakodate_preview_image_missing"]);
        setHakodateOverlayMessage("");
        setHakodateJobStatus(null);
      }
    } finally {
      setHakodateOverlayLoading(false);
    }
  };

  const loadOcrPages = async (
    options: { silent?: boolean; force?: boolean } = {},
  ) => {
    const currentOrder = authoritativeOrderRef.current || order;
    const orderId = String(currentOrder?.id || "").trim();
    if (!orderId) return;
    const { silent = false, force = false } = options;
    if (ocrPagesLoading && !force) return;
    const requestSeq = ocrPagesRequestSeqRef.current + 1;
    ocrPagesRequestSeqRef.current = requestSeq;
    setOcrPagesLoading(true);
    if (!silent || !ocrPages.length) {
      setOcrPagesMessage("OCRページを取得中...");
    }
    try {
      const res = await apiClient.get(`/orders/${orderId}/ocr-pages`, {
        params: {
          preview_only: 1,
          quantity_assignment_strategy: "hakodate",
        },
        timeout: 180000,
      });
      if (requestSeq < ocrPagesRequestSeqRef.current) {
        return;
      }
      if (res.status === 202 || res.data?.pending) {
        setOcrPagesMessage("OCRページは処理中です。");
        setOcrPages([]);
        setHakodateOverlayUrl("");
        setHakodateOverlayStatus("");
        setHakodateOverlayBlockers([]);
        setHakodateOverlayMessage("");
        setHakodateJobStatus(null);
        setOcrTableBox(null);
        setOcrTableUnits(null);
        setTableBoxUnitsOverride(null);
        setTableBoxDraft(null);
        setGridParams(defaultGridParams);
        setGridParamsDraft(defaultGridParams);
        setGridColumnEdges(null);
        setGridColumnEdgesDraft(null);
        setGridRowEdges(null);
        setGridRowEdgesDraft(null);
        setOcrTableHeader([]);
        setOcrTableRows([]);
        setOcrTablePageIndex(null);
        setOcrTableMessage("");
        setActiveOcrPageIndex(0);
        return;
      }
      const pages = Array.isArray(res.data?.pages) ? res.data.pages : [];
      const nextHakodateOverlayStatus =
        typeof res.data?.hakodate_overlay_status === "string" ? res.data.hakodate_overlay_status : "";
      const nextHakodateOverlayBlockers = Array.isArray(res.data?.hakodate_overlay_blockers)
        ? res.data.hakodate_overlay_blockers.map((item: unknown) => String(item || "").trim()).filter(Boolean)
        : [];
      const nextHakodateOverlayMessage =
        typeof res.data?.hakodate_overlay_message === "string" ? res.data.hakodate_overlay_message : "";
      setHakodateOverlayStatus(nextHakodateOverlayStatus);
      setHakodateOverlayBlockers(nextHakodateOverlayBlockers);
      setHakodateOverlayMessage(nextHakodateOverlayMessage);
      const nextHakodateAssignment = asHakodateAssignmentPayload(res.data?.hakodate_assignment);
      if (nextHakodateAssignment) {
        setHakodateAssignment(nextHakodateAssignment);
        setHakodateProjectionMetrics(asHakodateMetricsPayload(nextHakodateAssignment.metrics));
      }
      const metaTableBox = Array.isArray(res.data?.table_box) ? res.data.table_box : null;
      const metaTableUnits = typeof res.data?.table_units === "string" ? res.data.table_units : null;
      const metaColumnEdges = Array.isArray(res.data?.grid_column_edges)
        ? res.data.grid_column_edges
        : null;
      const metaRowEdges = Array.isArray(res.data?.grid_row_edges) ? res.data.grid_row_edges : null;
      const metaGridParams = buildGridParams(res.data?.grid_params);
      setOcrTableBox(metaTableBox);
      setOcrTableUnits(metaTableUnits);
      setTableBoxUnitsOverride(null);
      setTableBoxDraft(metaTableBox ? [...metaTableBox] : null);
      setGridParams(metaGridParams);
      setGridParamsDraft(metaGridParams);
      setGridColumnEdges(metaColumnEdges ? [...metaColumnEdges] : null);
      setGridColumnEdgesDraft(metaColumnEdges ? [...metaColumnEdges] : null);
      setGridRowEdges(metaRowEdges ? [...metaRowEdges] : null);
      setGridRowEdgesDraft(metaRowEdges ? [...metaRowEdges] : null);
      ocrPreviewRefreshKeyRef.current = [
        orderId,
        String(currentOrder?.current_sheet_revision_id || ""),
        String(currentOrder?.ocr_updated_at || ""),
        String(currentOrder?.ocr_result_state || ""),
        String(currentOrder?.workflow_state?.current_sheet_revision_id || ""),
        String(currentOrder?.workflow_state?.active_evidence_run_id || ""),
        String(currentOrder?.workflow_state?.candidate_evidence_run_id || ""),
        String(Boolean(currentOrder?.workflow_state?.candidate_prompt_visible)),
        String(currentOrder?.workflow_state?.reparse_state?.status || ""),
      ].join("|");
      if (pages.length) {
        const table = extractFirstTable(pages);
        if (table) {
          ocrPageSelectionModeRef.current = "auto";
          setActiveOcrPageIndex(table.pageArrayIndex);
          setOcrTableHeader(table.header);
          setOcrTableRows(table.rows.map((row) => [...row]));
          setOcrTablePageIndex(table.pageIndex);
          setOcrTableMessage("");
        } else {
          ocrPageSelectionModeRef.current = "auto";
          setActiveOcrPageIndex(0);
          setTableFromPage(pages[0], 0);
        }
        setOcrPages(pages);
        setOcrPagesMessage("");
      } else {
        setOcrPages([]);
        setOcrPagesMessage("OCRページがありません。");
        setHakodateOverlayUrl("");
        setHakodateOverlayStatus("");
        setHakodateOverlayBlockers([]);
        setHakodateOverlayMessage("");
        setHakodateJobStatus(null);
        setOcrTableBox(metaTableBox);
        setOcrTableUnits(metaTableUnits);
        setTableBoxUnitsOverride(null);
        setOcrTableHeader([]);
        setOcrTableRows([]);
        setOcrTablePageIndex(null);
        setOcrTableMessage("編集できる表が見つかりません。");
        ocrPageSelectionModeRef.current = "auto";
        setActiveOcrPageIndex(0);
      }
    } catch (err: any) {
      if (requestSeq < ocrPagesRequestSeqRef.current) {
        return;
      }
      const status = err?.response?.status;
      setOcrPagesMessage(status === 404 ? "OCRページが見つかりません。" : "OCRページの取得に失敗しました。");
      if (!ocrPages.length || status === 404) {
        setOcrPages([]);
        setHakodateOverlayStatus("");
        setHakodateOverlayUrl("");
        setHakodateOverlayBlockers([]);
        setHakodateOverlayMessage("");
        setHakodateJobStatus(null);
        setOcrTableBox(null);
        setOcrTableUnits(null);
        setTableBoxUnitsOverride(null);
        setTableBoxDraft(null);
        setGridColumnEdges(null);
        setGridColumnEdgesDraft(null);
        setGridRowEdges(null);
        setGridRowEdgesDraft(null);
        setOcrTableHeader([]);
        setOcrTableRows([]);
        setOcrTablePageIndex(null);
        setOcrTableMessage("");
        ocrPageSelectionModeRef.current = "auto";
        setActiveOcrPageIndex(0);
      }
    } finally {
      if (requestSeq === ocrPagesRequestSeqRef.current) {
        setOcrPagesLoading(false);
      }
    }
  };

  const loadBags = async () => {
    if (!order) return;
    setBagLoading(true);
    setBagMessage("袋分け結果を取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/bags`);
      const rows = Array.isArray(res.data?.bags) ? res.data.bags : [];
      const appliedOverrides = Array.isArray(res.data?.applied_portion_overrides)
        ? res.data.applied_portion_overrides
        : [];
      setBagRows(rows);
      setBagAppliedOverrides(appliedOverrides);
      setBagMessage(rows.length ? "" : "袋分け結果がまだ生成されていません。");
    } catch (err: any) {
      setBagRows([]);
      setBagAppliedOverrides([]);
      setBagMessage("袋分け結果の取得に失敗しました。");
    } finally {
      setBagLoading(false);
    }
  };

  const rebuildBags = async () => {
    if (!order) return false;
    setBagLoading(true);
    setBagMessage("袋分けを再計算中...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/bags/rebuild`);
      const rows = Array.isArray(res.data?.bags) ? res.data.bags : [];
      const appliedOverrides = Array.isArray(res.data?.applied_portion_overrides)
        ? res.data.applied_portion_overrides
        : [];
      setBagRows(rows);
      setBagAppliedOverrides(appliedOverrides);
      setBagMessage(rows.length ? "袋分けを更新しました。" : "袋分け結果がありません。");
      return true;
    } catch (err: any) {
      setBagRows([]);
      setBagAppliedOverrides([]);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailText = detail ? ` (${detail})` : "";
      const statusText = status ? ` [${status}]` : "";
      setBagMessage(`袋分けの再計算に失敗しました。${statusText}${detailText}`);
      return false;
    } finally {
      setBagLoading(false);
    }
  };

  const selectOcrPage = (pageIndex: number, options?: { manual?: boolean }) => {
    ocrPageSelectionModeRef.current = options?.manual === false ? "auto" : "manual";
    setActiveOcrPageIndex(pageIndex);
    setTableFromPage(ocrPages[pageIndex], pageIndex);
  };

  useEffect(() => {
    if (activeStep !== 1) return;
    const currentOrder = authoritativeOrderRef.current || order;
    if (!currentOrder?.id) return;
    const normalizedStatus = String(currentOrder.ocr_status || "").trim().toLowerCase();
    const normalizedResultState = String(currentOrder.ocr_result_state || "")
      .trim()
      .toLowerCase();
    const normalizedReparseStatus = String(currentOrder.workflow_state?.reparse_state?.status || "")
      .trim()
      .toLowerCase();
    const evidenceReady =
      normalizedStatus === "done"
      || normalizedResultState === "evidence_ready"
      || normalizedResultState === "done"
      || normalizedReparseStatus === "done";
    if (!evidenceReady) return;
    const nextRefreshKey = [
      currentOrder.id,
      String(currentOrder.current_sheet_revision_id || ""),
      String(currentOrder.ocr_updated_at || ""),
      String(currentOrder.ocr_result_state || ""),
      String(currentOrder.workflow_state?.current_sheet_revision_id || ""),
      String(currentOrder.workflow_state?.active_evidence_run_id || ""),
      String(currentOrder.workflow_state?.candidate_evidence_run_id || ""),
      String(Boolean(currentOrder.workflow_state?.candidate_prompt_visible)),
      String(currentOrder.workflow_state?.reparse_state?.status || ""),
    ].join("|");
    if (ocrPreviewRefreshKeyRef.current === nextRefreshKey) {
      return;
    }
    ocrPreviewRefreshKeyRef.current = nextRefreshKey;
    void loadHakodateOverlayPreview({ silent: true, force: true });
  }, [
    activeStep,
    order?.id,
    order?.current_sheet_revision_id,
    order?.ocr_updated_at,
    order?.ocr_status,
    order?.ocr_result_state,
    order?.workflow_state?.current_sheet_revision_id,
    order?.workflow_state?.active_evidence_run_id,
    order?.workflow_state?.candidate_evidence_run_id,
    order?.workflow_state?.candidate_prompt_visible,
    order?.workflow_state?.reparse_state?.status,
  ]);

  useEffect(() => {
    if (activeStep !== 1 && ocrEditMode) {
      setOcrEditMode(false);
      setShowOcrEdit(false);
      setShowTableBoxEditor(false);
    }
  }, [activeStep, ocrEditMode]);

  useEffect(() => {
    if (activeStep !== 1) return;
    if (!ocrEditMode) {
      setOcrEditMode(true);
    }
    if (!showOcrEdit) {
      setShowOcrEdit(true);
    }
    if (!(order?.facility || "").trim()) {
      return;
    }
    if (!ocrSheetRows.length && !ocrSheetLoading && !ocrSheetAutoRetryBlocked) {
      loadOcrSheet({ silent: true });
    }
  }, [
    activeStep,
    ocrEditMode,
    showOcrEdit,
    ocrSheetRows.length,
    ocrSheetLoading,
    ocrSheetAutoRetryBlocked,
    order?.facility,
  ]);

  useEffect(() => {
    if (!order?.id) return;
    if (activeStep !== 3) return;
    loadBags();
  }, [activeStep, order?.id]);

  useEffect(() => {
    const handlePointerRelease = () => {
      ocrSheetSelectionPointerActiveRef.current = false;
    };
    window.addEventListener("mouseup", handlePointerRelease);
    return () => window.removeEventListener("mouseup", handlePointerRelease);
  }, []);

  useEffect(() => {
    if (!ocrSheetRows.length) {
      setOcrSheetSelection(null);
      setOcrSheetDropTarget(null);
      setFocusedSheetCell(null);
      return;
    }
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    setOcrSheetSelection((prev) => {
      if (!prev) return prev;
      return {
        anchorRowIndex: Math.min(prev.anchorRowIndex, ocrSheetRows.length - 1),
        anchorCellIndex: Math.min(prev.anchorCellIndex, columnCount - 1),
        focusRowIndex: Math.min(prev.focusRowIndex, ocrSheetRows.length - 1),
        focusCellIndex: Math.min(prev.focusCellIndex, columnCount - 1),
      };
    });
    setFocusedSheetCell((prev) => {
      if (!prev) return prev;
      return {
        rowIndex: Math.min(prev.rowIndex, ocrSheetRows.length - 1),
        cellIndex: Math.min(prev.cellIndex, columnCount - 1),
      };
    });
  }, [ocrSheetHeader, ocrSheetRows]);

  const updateOcrSheetTouchedCellMetadata = (
    touchedCells: OcrSheetTouchedCell[],
    provenanceValue: string = "user_edit",
  ) => {
    const cells = dedupeOcrSheetTouchedCells(touchedCells);
    if (!cells.length) return;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    const cellKeys = new Set(cells.map((cell) => `${cell.rowIndex}:${cell.cellIndex}`));
    setOcrSheetCellConfidenceRows((prev) => {
      const next = normalizeSheetCellMetadataRows(prev, ocrSheetRows.length, columnCount);
      for (const { rowIndex, cellIndex } of cells) {
        if (rowIndex >= next.length || cellIndex >= next[rowIndex].length) continue;
        next[rowIndex][cellIndex] = "";
      }
      return next;
    });
    setOcrSheetCellProvenanceRows((prev) => {
      const next = normalizeSheetCellMetadataRows(prev, ocrSheetRows.length, columnCount);
      for (const { rowIndex, cellIndex } of cells) {
        if (rowIndex >= next.length || cellIndex >= next[rowIndex].length) continue;
        next[rowIndex][cellIndex] = provenanceValue;
      }
      return next;
    });
    const nextNumericItems = ocrSheetNumericCellItems.filter((item) => {
      if (normalizeOcrNumericCellClassification(item.classification) !== "accepted") {
        return true;
      }
      const rowIndex = typeof item.target_row_index === "number" ? item.target_row_index : null;
      const cellIndex = typeof item.target_col_index === "number" ? item.target_col_index : null;
      if (rowIndex == null || cellIndex == null) return true;
      return !cellKeys.has(`${rowIndex}:${cellIndex}`);
    });
    setOcrSheetNumericCellItems(nextNumericItems);
    setOcrSheetNumericCellSummary(rebuildOcrNumericCellSummary(nextNumericItems, ocrSheetRawNumericCount));
  };

  const clearOcrSheetOverlayState = () => {
    ocrSheetEditedSinceLoadRef.current = true;
    setOcrSheetCellConfidenceRows([]);
    setOcrSheetCellProvenanceRows([]);
    setOcrSheetNumericCellItems([]);
    setOcrSheetNumericCellSummary(blankOcrNumericCellSummary());
  };

  const adoptVisibleOcrOverlayItems = (
    items: OcrVisibleOverlayItem[],
    message: string,
  ): { adopted: number; nextRows: string[][] } => {
    const dedupedByCell = new Map<string, OcrVisibleOverlayItem>();
    for (const item of items) {
      const rowIndex = Number(item?.target_row_index);
      const cellIndex = Number(item?.target_col_index);
      const value = String(item?.value ?? "").trim();
      const classification = normalizeOcrNumericCellClassification(item?.classification);
      if (
        !Number.isInteger(rowIndex)
        || rowIndex < 0
        || !Number.isInteger(cellIndex)
        || cellIndex < 0
        || !value
        || !classification
      ) {
        continue;
      }
      if (String(ocrSheetRows[rowIndex]?.[cellIndex] ?? "").trim()) {
        continue;
      }
      dedupedByCell.set(`${rowIndex}:${cellIndex}`, {
        ...item,
        target_row_index: rowIndex,
        target_col_index: cellIndex,
        value,
        classification,
      });
    }
    const adoptableItems = Array.from(dedupedByCell.values());
    if (!adoptableItems.length) {
      return { adopted: 0, nextRows: ocrSheetRows };
    }
    ocrSheetEditedSinceLoadRef.current = true;
    const nextRows = ocrSheetRows.map((row) => [...row]);
    const touchedCells: OcrSheetTouchedCell[] = [];
    for (const item of adoptableItems) {
      while (nextRows.length <= item.target_row_index) {
        nextRows.push([]);
      }
      while (nextRows[item.target_row_index].length <= item.target_col_index) {
        nextRows[item.target_row_index].push("");
      }
      nextRows[item.target_row_index][item.target_col_index] = item.value;
      touchedCells.push({ rowIndex: item.target_row_index, cellIndex: item.target_col_index });
    }
    const dedupedTouchedCells = dedupeOcrSheetTouchedCells(touchedCells);
    const touchedCellKeys = new Set(dedupedTouchedCells.map((cell) => `${cell.rowIndex}:${cell.cellIndex}`));
    const rowCount = nextRows.length;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, nextRows), 1);
    setOcrSheetRows(nextRows);
    setOcrSheetCellConfidenceRows((prev) => {
      const next = normalizeSheetCellMetadataRows(prev, rowCount, columnCount);
      for (const item of adoptableItems) {
        if (item.target_row_index >= next.length || item.target_col_index >= next[item.target_row_index].length) {
          continue;
        }
        next[item.target_row_index][item.target_col_index] = "high";
      }
      return next;
    });
    setOcrSheetCellProvenanceRows((prev) => {
      const next = normalizeSheetCellMetadataRows(prev, rowCount, columnCount);
      for (const item of adoptableItems) {
        if (item.target_row_index >= next.length || item.target_col_index >= next[item.target_row_index].length) {
          continue;
        }
        next[item.target_row_index][item.target_col_index] = "ocr_overlay_adopted";
      }
      return next;
    });
    const nextNumericItems = [
      ...ocrSheetNumericCellItems.filter((item) => {
        const rowIndex = typeof item.target_row_index === "number" ? item.target_row_index : null;
        const cellIndex = typeof item.target_col_index === "number" ? item.target_col_index : null;
        if (rowIndex == null || cellIndex == null) return true;
        return !touchedCellKeys.has(`${rowIndex}:${cellIndex}`);
      }),
      ...adoptableItems.map((item) => ({
        ...item,
        classification: "accepted" as const,
        confidence_tier: "high" as const,
        reason: "overlay_adopted",
      })),
    ];
    setOcrSheetNumericCellItems(nextNumericItems);
    setOcrSheetNumericCellSummary(rebuildOcrNumericCellSummary(nextNumericItems, ocrSheetRawNumericCount));
    setOcrTableMessage(message);
    return { adopted: adoptableItems.length, nextRows };
  };

  const markOcrSheetEdited = (
    options: { preserveOverlay?: boolean; touchedCells?: OcrSheetTouchedCell[] } = {},
  ) => {
    ocrSheetEditedSinceLoadRef.current = true;
    if (options.preserveOverlay) {
      updateOcrSheetTouchedCellMetadata(options.touchedCells || []);
      return;
    }
    clearOcrSheetOverlayState();
  };

  const updateOcrTableCell = (
    rowIndex: number,
    cellIndex: number,
    value: string,
  ) => {
    markOcrSheetEdited({ preserveOverlay: true, touchedCells: [{ rowIndex, cellIndex }] });
    setOcrSheetRows((prev) => {
      const next = prev.map((row) => [...row]);
      while (next.length <= rowIndex) {
        next.push([]);
      }
      while (next[rowIndex].length <= cellIndex) {
        next[rowIndex].push("");
      }
      next[rowIndex][cellIndex] = value;
      return next;
    });
  };

  const applySelectedOcrSheetColumnFill = () => {
    const targetColumnIndex = Number(ocrSheetColumnFillTarget);
    const fillValue = String(ocrSheetColumnFillValue ?? "").trim();
    if (!Number.isInteger(targetColumnIndex) || targetColumnIndex < 0 || !ocrSheetRows.length || !fillValue) {
      return false;
    }
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    if (targetColumnIndex >= columnCount) {
      return false;
    }
    const touchedCells = Array.from({ length: ocrSheetRows.length }, (_, rowIndex) => ({
      rowIndex,
      cellIndex: targetColumnIndex,
    }));
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) =>
      prev.map((row) => {
        const clone = [...row];
        while (clone.length < columnCount) clone.push("");
        clone[targetColumnIndex] = fillValue;
        return clone;
      }),
    );
    const targetLabel = ocrSheetHeaders[targetColumnIndex] || `列${targetColumnIndex + 1}`;
    setOcrTableMessage(`列 ${targetLabel} に ${fillValue || "空欄"} を一括入力しました。`);
    return true;
  };

  const focusOcrSheetRow = (rowIndex: number) => {
    ocrPageSelectionModeRef.current = "auto";
    setFocusedSheetRowIndex(rowIndex);
  };

  const focusOcrSheetCell = (
    rowIndex: number,
    cellIndex: number,
    collapseSelection: boolean = true,
  ) => {
    focusOcrSheetRow(rowIndex);
    setFocusedSheetCell({ rowIndex, cellIndex });
    if (collapseSelection) {
      setOcrSheetSelection({
        anchorRowIndex: rowIndex,
        anchorCellIndex: cellIndex,
        focusRowIndex: rowIndex,
        focusCellIndex: cellIndex,
      });
    }
  };

  const handleOcrSheetCellFocus = (rowIndex: number, cellIndex: number) => {
    focusOcrSheetRow(rowIndex);
    setFocusedSheetCell({ rowIndex, cellIndex });
    if (!isOcrSheetCellWithinSelection(ocrSheetSelection, rowIndex, cellIndex)) {
      setOcrSheetSelection({
        anchorRowIndex: rowIndex,
        anchorCellIndex: cellIndex,
        focusRowIndex: rowIndex,
        focusCellIndex: cellIndex,
      });
    }
  };

  const moveFocusedOcrSheetCell = (rowDelta: number, cellDelta: number) => {
    if (!ocrSheetRows.length) return;
    const fallbackRowIndex = focusedSheetRowIndex ?? 0;
    const fallbackCellIndex = focusedSheetCell?.cellIndex ?? 0;
    const nextRowIndex = Math.min(
      Math.max((focusedSheetCell?.rowIndex ?? fallbackRowIndex) + rowDelta, 0),
      Math.max(ocrSheetRows.length - 1, 0),
    );
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    const nextCellIndex = Math.min(
      Math.max(fallbackCellIndex + cellDelta, 0),
      Math.max(columnCount - 1, 0),
    );
    focusOcrSheetCell(nextRowIndex, nextCellIndex);
    const refKey = `${nextRowIndex}:${nextCellIndex}`;
    requestAnimationFrame(() => {
      const nextInput = ocrSheetCellRefs.current[refKey];
      if (nextInput) {
        nextInput.focus();
        nextInput.select();
      }
    });
  };

  const extendOcrSheetSelection = (rowDelta: number, cellDelta: number) => {
    if (!ocrSheetRows.length) return;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    let nextRowIndex = 0;
    let nextCellIndex = 0;
    setOcrSheetSelection((prev) => {
      const base = prev || {
        anchorRowIndex: focusedSheetCell?.rowIndex ?? focusedSheetRowIndex ?? 0,
        anchorCellIndex: focusedSheetCell?.cellIndex ?? 0,
        focusRowIndex: focusedSheetCell?.rowIndex ?? focusedSheetRowIndex ?? 0,
        focusCellIndex: focusedSheetCell?.cellIndex ?? 0,
      };
      nextRowIndex = Math.min(
        Math.max(base.focusRowIndex + rowDelta, 0),
        Math.max(ocrSheetRows.length - 1, 0),
      );
      nextCellIndex = Math.min(
        Math.max(base.focusCellIndex + cellDelta, 0),
        Math.max(columnCount - 1, 0),
      );
      return {
        ...base,
        focusRowIndex: nextRowIndex,
        focusCellIndex: nextCellIndex,
      };
    });
    setFocusedSheetCell({ rowIndex: nextRowIndex, cellIndex: nextCellIndex });
    focusOcrSheetRow(nextRowIndex);
    requestAnimationFrame(() => {
      const nextInput = ocrSheetCellRefs.current[`${nextRowIndex}:${nextCellIndex}`];
      if (nextInput) {
        nextInput.focus();
        nextInput.select();
      }
    });
  };

  const selectAllOcrSheetCells = () => {
    if (!ocrSheetRows.length) return false;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    setOcrSheetSelection({
      anchorRowIndex: 0,
      anchorCellIndex: 0,
      focusRowIndex: Math.max(ocrSheetRows.length - 1, 0),
      focusCellIndex: Math.max(columnCount - 1, 0),
    });
    focusOcrSheetCell(0, 0, false);
    requestAnimationFrame(() => {
      const firstInput = ocrSheetCellRefs.current["0:0"];
      if (firstInput) {
        firstInput.focus();
        firstInput.select();
      }
    });
    return true;
  };

  const readOcrSheetSelectionMatrix = (selection: OcrSheetCellSelection | null) => {
    const selectionBounds = getOcrSheetSelectionBounds(selection);
    if (!selectionBounds) return null;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    return Array.from({ length: selectionBounds.rowCount }, (_, rowOffset) =>
      Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => {
        const sourceRow = selectionBounds.topRowIndex + rowOffset;
        const sourceCell = selectionBounds.leftCellIndex + cellOffset;
        if (sourceRow >= ocrSheetRows.length || sourceCell >= columnCount) return "";
        return ocrSheetRows[sourceRow]?.[sourceCell] ?? "";
      }),
    );
  };

  const writeTextToSystemClipboard = async (text: string) => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) return false;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return false;
    }
  };

  const readTextFromSystemClipboard = async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard?.readText) return "";
    try {
      return await navigator.clipboard.readText();
    } catch {
      return "";
    }
  };

  const serializeOcrSheetMatrix = (matrix: string[][]) =>
    matrix.map((row) => row.map((cell) => cell ?? "").join("\t")).join("\n");

  const parseOcrSheetMatrix = (text: string) => {
    const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const lines = normalized.split("\n");
    while (lines.length && lines[lines.length - 1] === "") {
      lines.pop();
    }
    return lines.map((line) => line.split("\t"));
  };

  const rememberOcrSheetClipboard = async (matrix: string[][]) => {
    ocrSheetClipboardRef.current = matrix;
    setOcrSheetClipboardReady(true);
    const copied = await writeTextToSystemClipboard(serializeOcrSheetMatrix(matrix));
    setOcrTableMessage(copied ? "選択範囲をコピーしました。" : "選択範囲をコピーしました。貼り付けはこの画面内で使えます。");
  };

  const clearOcrSheetSelectionContents = (message: string = "選択範囲をクリアしました。") => {
    const selectionBounds = getOcrSheetSelectionBounds(ocrSheetSelection);
    if (!selectionBounds) return false;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    const touchedCells = Array.from({ length: selectionBounds.rowCount }, (_, rowOffset) =>
      Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => ({
        rowIndex: selectionBounds.topRowIndex + rowOffset,
        cellIndex: selectionBounds.leftCellIndex + cellOffset,
      })),
    ).flat();
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) =>
      prev.map((row, rowIndex) => {
        const clone = [...row];
        while (clone.length < columnCount) clone.push("");
        if (rowIndex < selectionBounds.topRowIndex || rowIndex > selectionBounds.bottomRowIndex) {
          return clone;
        }
        for (let cellIndex = selectionBounds.leftCellIndex; cellIndex <= selectionBounds.rightCellIndex; cellIndex += 1) {
          clone[cellIndex] = "";
        }
        return clone;
      }),
    );
    setOcrTableMessage(message);
    return true;
  };

  const copyOcrSheetSelection = async () => {
    const matrix = readOcrSheetSelectionMatrix(ocrSheetSelection);
    if (!matrix) return false;
    await rememberOcrSheetClipboard(matrix);
    return true;
  };

  const cutOcrSheetSelection = async () => {
    const matrix = readOcrSheetSelectionMatrix(ocrSheetSelection);
    if (!matrix) return false;
    ocrSheetClipboardRef.current = matrix;
    setOcrSheetClipboardReady(true);
    await writeTextToSystemClipboard(serializeOcrSheetMatrix(matrix));
    const cleared = clearOcrSheetSelectionContents("選択範囲を切り取りました。");
    return cleared;
  };

  const applyOcrSheetMatrixAt = (
    matrix: string[][],
    targetRowIndex: number,
    targetCellIndex: number,
    successMessage: string,
  ) => {
    if (!matrix.length) return false;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    if (targetRowIndex >= ocrSheetRows.length || targetCellIndex >= columnCount) return false;
    let truncated = false;
    const touchedCells = Array.from({ length: matrix.length }, (_, rowOffset) =>
      Array.from({ length: matrix[rowOffset]?.length || 0 }, (_, cellOffset) => ({
        rowIndex: targetRowIndex + rowOffset,
        cellIndex: targetCellIndex + cellOffset,
      })),
    )
      .flat()
      .filter(
        ({ rowIndex, cellIndex }) =>
          rowIndex >= 0
          && rowIndex < ocrSheetRows.length
          && cellIndex >= 0
          && cellIndex < columnCount,
      );
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) =>
      prev.map((row, rowIndex) => {
        const clone = [...row];
        while (clone.length < columnCount) clone.push("");
        const sourceRowIndex = rowIndex - targetRowIndex;
        if (sourceRowIndex < 0 || sourceRowIndex >= matrix.length) return clone;
        const sourceRow = matrix[sourceRowIndex] || [];
        sourceRow.forEach((value, valueCellIndex) => {
          const destinationCellIndex = targetCellIndex + valueCellIndex;
          if (destinationCellIndex >= columnCount) {
            truncated = true;
            return;
          }
          clone[destinationCellIndex] = value ?? "";
        });
        if (targetCellIndex + sourceRow.length > columnCount) truncated = true;
        return clone;
      }),
    );
    if (targetRowIndex + matrix.length > ocrSheetRows.length) truncated = true;
    setOcrTableMessage(truncated ? `${successMessage} はみ出した分は貼り付けていません。` : successMessage);
    focusOcrSheetCell(targetRowIndex, targetCellIndex, false);
    requestAnimationFrame(() => {
      const targetInput = ocrSheetCellRefs.current[`${targetRowIndex}:${targetCellIndex}`];
      if (targetInput) {
        targetInput.focus();
        targetInput.select();
      }
    });
    return true;
  };

  const pasteOcrSheetSelection = async (textOverride?: string) => {
    const target = focusedSheetCell || (ocrSheetSelectionBounds
      ? { rowIndex: ocrSheetSelectionBounds.topRowIndex, cellIndex: ocrSheetSelectionBounds.leftCellIndex }
      : null);
    if (!target) return false;
    const textFromClipboard = typeof textOverride === "string" ? textOverride : await readTextFromSystemClipboard();
    let matrix = parseOcrSheetMatrix(textFromClipboard);
    if (!matrix.length) {
      matrix = ocrSheetClipboardRef.current || [];
    }
    if (!matrix.length) return false;
    return applyOcrSheetMatrixAt(matrix, target.rowIndex, target.cellIndex, "選択範囲を貼り付けました。");
  };

  const fillOcrSheetSelectionDown = () => {
    const selectionBounds = getOcrSheetSelectionBounds(ocrSheetSelection);
    if (!selectionBounds || selectionBounds.rowCount < 2) return false;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    const touchedCells = Array.from({ length: selectionBounds.rowCount - 1 }, (_, rowOffset) =>
      Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => ({
        rowIndex: selectionBounds.topRowIndex + 1 + rowOffset,
        cellIndex: selectionBounds.leftCellIndex + cellOffset,
      })),
    ).flat();
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) => {
      const next = prev.map((row) => {
        const clone = [...row];
        while (clone.length < columnCount) clone.push("");
        return clone;
      });
      const sourceRowValues = Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => {
        const sourceCellIndex = selectionBounds.leftCellIndex + cellOffset;
        return next[selectionBounds.topRowIndex]?.[sourceCellIndex] ?? "";
      });
      for (let rowIndex = selectionBounds.topRowIndex + 1; rowIndex <= selectionBounds.bottomRowIndex; rowIndex += 1) {
        sourceRowValues.forEach((value, cellOffset) => {
          next[rowIndex][selectionBounds.leftCellIndex + cellOffset] = value;
        });
      }
      return next;
    });
    setOcrTableMessage("選択範囲の先頭行を下方向へコピーしました。");
    return true;
  };

  const fillOcrSheetSelectionRight = () => {
    const selectionBounds = getOcrSheetSelectionBounds(ocrSheetSelection);
    if (!selectionBounds || selectionBounds.cellCount < 2) return false;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    const touchedCells = Array.from({ length: selectionBounds.rowCount }, (_, rowOffset) =>
      Array.from({ length: selectionBounds.cellCount - 1 }, (_, cellOffset) => ({
        rowIndex: selectionBounds.topRowIndex + rowOffset,
        cellIndex: selectionBounds.leftCellIndex + 1 + cellOffset,
      })),
    ).flat();
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) => {
      const next = prev.map((row) => {
        const clone = [...row];
        while (clone.length < columnCount) clone.push("");
        return clone;
      });
      for (let rowIndex = selectionBounds.topRowIndex; rowIndex <= selectionBounds.bottomRowIndex; rowIndex += 1) {
        const sourceValue = next[rowIndex]?.[selectionBounds.leftCellIndex] ?? "";
        for (let cellIndex = selectionBounds.leftCellIndex + 1; cellIndex <= selectionBounds.rightCellIndex; cellIndex += 1) {
          next[rowIndex][cellIndex] = sourceValue;
        }
      }
      return next;
    });
    setOcrTableMessage("選択範囲の左端セルを右方向へコピーしました。");
    return true;
  };

  const handleOcrSheetCellKeyDown = (
    event: KeyboardEvent<HTMLInputElement>,
    rowIndex: number,
    cellIndex: number,
  ) => {
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        extendOcrSheetSelection(1, 0);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        extendOcrSheetSelection(-1, 0);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        extendOcrSheetSelection(0, 1);
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        extendOcrSheetSelection(0, -1);
        return;
      }
    }
    if (event.key === "Tab") {
      event.preventDefault();
      moveFocusedOcrSheetCell(event.shiftKey ? -1 : 1, 0);
      return;
    }
    if (event.key === "Delete") {
      event.preventDefault();
      clearOcrSheetSelectionContents();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "c" || event.key === "C")) {
      event.preventDefault();
      void copyOcrSheetSelection();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "a" || event.key === "A")) {
      event.preventDefault();
      selectAllOcrSheetCells();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "x" || event.key === "X")) {
      event.preventDefault();
      void cutOcrSheetSelection();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "d" || event.key === "D")) {
      event.preventDefault();
      fillOcrSheetSelectionDown();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "r" || event.key === "R")) {
      event.preventDefault();
      fillOcrSheetSelectionRight();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "v" || event.key === "V")) {
      event.preventDefault();
      void pasteOcrSheetSelection();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (event.altKey) {
        moveFocusedOcrSheetCell(0, event.shiftKey ? -1 : 1);
        return;
      }
      moveFocusedOcrSheetCell(event.shiftKey ? -1 : 1, 0);
      return;
    }
    if (!event.ctrlKey && !event.metaKey) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveFocusedOcrSheetCell(1, 0);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveFocusedOcrSheetCell(-1, 0);
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveFocusedOcrSheetCell(0, 1);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveFocusedOcrSheetCell(0, -1);
      return;
    }
    focusOcrSheetCell(rowIndex, cellIndex);
  };

  const handleOcrSheetCellMouseDown = (
    event: MouseEvent<HTMLInputElement>,
    rowIndex: number,
    cellIndex: number,
  ) => {
    ocrSheetSelectionPointerActiveRef.current = true;
    ocrPageSelectionModeRef.current = "auto";
    if (event.shiftKey && ocrSheetSelection) {
      setOcrSheetSelection((prev) =>
        prev
          ? {
              ...prev,
              focusRowIndex: rowIndex,
              focusCellIndex: cellIndex,
            }
          : {
              anchorRowIndex: rowIndex,
              anchorCellIndex: cellIndex,
              focusRowIndex: rowIndex,
              focusCellIndex: cellIndex,
            },
      );
      setFocusedSheetCell({ rowIndex, cellIndex });
      focusOcrSheetRow(rowIndex);
      return;
    }
    if (!isOcrSheetCellWithinSelection(ocrSheetSelection, rowIndex, cellIndex)) {
      setOcrSheetSelection({
        anchorRowIndex: rowIndex,
        anchorCellIndex: cellIndex,
        focusRowIndex: rowIndex,
        focusCellIndex: cellIndex,
      });
    }
    setFocusedSheetCell({ rowIndex, cellIndex });
    focusOcrSheetRow(rowIndex);
  };

  const handleOcrSheetCellMouseEnter = (rowIndex: number, cellIndex: number) => {
    if (!ocrSheetSelectionPointerActiveRef.current) return;
    setOcrSheetSelection((prev) =>
      prev
        ? {
            ...prev,
            focusRowIndex: rowIndex,
            focusCellIndex: cellIndex,
          }
        : {
            anchorRowIndex: rowIndex,
            anchorCellIndex: cellIndex,
            focusRowIndex: rowIndex,
            focusCellIndex: cellIndex,
          },
    );
  };

  const moveOcrSheetSelectionTo = (targetRowIndex: number, targetCellIndex: number) => {
    const selectionBounds = getOcrSheetSelectionBounds(ocrSheetSelection);
    if (!selectionBounds) return false;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    if (
      targetRowIndex < 0 ||
      targetCellIndex < 0 ||
      targetRowIndex + selectionBounds.rowCount > ocrSheetRows.length ||
      targetCellIndex + selectionBounds.cellCount > columnCount
    ) {
      setOcrTableMessage("選択範囲を表の外には移動できません。");
      return false;
    }
    if (
      targetRowIndex === selectionBounds.topRowIndex &&
      targetCellIndex === selectionBounds.leftCellIndex
    ) {
      return true;
    }
    const touchedCells = [
      ...Array.from({ length: selectionBounds.rowCount }, (_, rowOffset) =>
        Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => ({
          rowIndex: selectionBounds.topRowIndex + rowOffset,
          cellIndex: selectionBounds.leftCellIndex + cellOffset,
        })),
      ).flat(),
      ...Array.from({ length: selectionBounds.rowCount }, (_, rowOffset) =>
        Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => ({
          rowIndex: targetRowIndex + rowOffset,
          cellIndex: targetCellIndex + cellOffset,
        })),
      ).flat(),
    ];
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) => {
      const next = prev.map((row) => {
        const clone = [...row];
        while (clone.length < columnCount) {
          clone.push("");
        }
        return clone;
      });
      const snapshot = Array.from({ length: selectionBounds.rowCount }, (_, rowOffset) =>
        Array.from({ length: selectionBounds.cellCount }, (_, cellOffset) => {
          const sourceRow = selectionBounds.topRowIndex + rowOffset;
          const sourceCell = selectionBounds.leftCellIndex + cellOffset;
          return next[sourceRow]?.[sourceCell] ?? "";
        }),
      );
      for (let rowOffset = 0; rowOffset < selectionBounds.rowCount; rowOffset += 1) {
        for (let cellOffset = 0; cellOffset < selectionBounds.cellCount; cellOffset += 1) {
          const sourceRow = selectionBounds.topRowIndex + rowOffset;
          const sourceCell = selectionBounds.leftCellIndex + cellOffset;
          next[sourceRow][sourceCell] = "";
        }
      }
      for (let rowOffset = 0; rowOffset < selectionBounds.rowCount; rowOffset += 1) {
        for (let cellOffset = 0; cellOffset < selectionBounds.cellCount; cellOffset += 1) {
          next[targetRowIndex + rowOffset][targetCellIndex + cellOffset] = snapshot[rowOffset][cellOffset] ?? "";
        }
      }
      return next;
    });
    setOcrSheetSelection({
      anchorRowIndex: targetRowIndex,
      anchorCellIndex: targetCellIndex,
      focusRowIndex: targetRowIndex + selectionBounds.rowCount - 1,
      focusCellIndex: targetCellIndex + selectionBounds.cellCount - 1,
    });
    setOcrTableMessage("選択範囲を移動しました。");
    focusOcrSheetCell(targetRowIndex, targetCellIndex, false);
    requestAnimationFrame(() => {
      const moved = ocrSheetCellRefs.current[`${targetRowIndex}:${targetCellIndex}`];
      if (moved) {
        moved.focus();
        moved.select();
      }
    });
    return true;
  };

  const handleOcrSheetSelectionDragStart = (
    event: DragEvent<HTMLInputElement>,
    rowIndex: number,
    cellIndex: number,
  ) => {
    const selectionBounds = getOcrSheetSelectionBounds(ocrSheetSelection);
    if (!selectionBounds || !isOcrSheetCellWithinSelection(ocrSheetSelection, rowIndex, cellIndex)) {
      event.preventDefault();
      return;
    }
    ocrSheetDragSelectionBoundsRef.current = selectionBounds;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "ocr-sheet-selection");
  };

  const handleOcrSheetSelectionDragOver = (
    event: DragEvent<HTMLTableCellElement>,
    rowIndex: number,
    cellIndex: number,
  ) => {
    const selectionBounds = ocrSheetDragSelectionBoundsRef.current;
    if (!selectionBounds) return;
    const columnCount = Math.max(getColumnCount(ocrSheetHeader, ocrSheetRows), 1);
    const canDrop =
      rowIndex >= 0 &&
      cellIndex >= 0 &&
      rowIndex + selectionBounds.rowCount <= ocrSheetRows.length &&
      cellIndex + selectionBounds.cellCount <= columnCount;
    if (!canDrop) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setOcrSheetDropTarget({ rowIndex, cellIndex });
  };

  const handleOcrSheetSelectionDrop = (
    event: DragEvent<HTMLTableCellElement>,
    rowIndex: number,
    cellIndex: number,
  ) => {
    event.preventDefault();
    const moved = moveOcrSheetSelectionTo(rowIndex, cellIndex);
    ocrSheetDragSelectionBoundsRef.current = null;
    setOcrSheetDropTarget(null);
    if (!moved) return;
  };

  const handleOcrSheetSelectionDragEnd = () => {
    ocrSheetDragSelectionBoundsRef.current = null;
    setOcrSheetDropTarget(null);
  };

  const handleOcrSheetCellPaste = async (
    event: ClipboardEvent<HTMLInputElement>,
    rowIndex: number,
    cellIndex: number,
  ) => {
    const pastedText = event.clipboardData?.getData("text/plain") || "";
    if (!pastedText) return;
    event.preventDefault();
    focusOcrSheetCell(rowIndex, cellIndex, false);
    await pasteOcrSheetSelection(pastedText);
  };

  const updateOcrTableHeaderCell = (
    cellIndex: number,
    value: string,
  ) => {
    markOcrSheetEdited();
    setOcrSheetHeader((prev) => {
      const next = [...prev];
      while (next.length <= cellIndex) {
        next.push("");
      }
      next[cellIndex] = value;
      return next;
    });
  };

  const updateOverlaySize = () => {
    const image = overlayImageRef.current;
    if (!image) return;
    const rect = image.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    setOverlayImageSize({ width: rect.width, height: rect.height });
  };

  useEffect(() => {
    const image = overlayImageRef.current;
    if (!image) return;
    updateOverlaySize();
    const handleLoad = () => updateOverlaySize();
    image.addEventListener("load", handleLoad);
    let observer: ResizeObserver | null = null;
    if ("ResizeObserver" in window) {
      observer = new ResizeObserver(() => updateOverlaySize());
      observer.observe(image);
    }
    window.addEventListener("resize", updateOverlaySize);
    return () => {
      image.removeEventListener("load", handleLoad);
      if (observer) observer.disconnect();
      window.removeEventListener("resize", updateOverlaySize);
    };
  }, [ocrPages, activeOcrPageIndex, ocrPreviewMode, quantityAssignmentStrategy, showOcrEdit, hakodateOverlayUrl]);

  const tableBoxUnitsLabel = (tableBoxUnitsOverride || ocrTableUnits || "normalized").toLowerCase();
  const tableBoxNormalized = tableBoxUnitsLabel === "normalized" || tableBoxUnitsLabel === "ratio";
  const tableBoxClampMax = tableBoxNormalized ? 1 : 10000;
  const tableBoxMinSize = tableBoxNormalized ? 0.002 : 2;
  const tableBoxDecimals = tableBoxNormalized ? 3 : 0;

  useEffect(() => {
    setTableBoxStep(tableBoxNormalized ? 0.005 : 5);
  }, [tableBoxNormalized]);

  const normalizeTableBox = (box: number[]) => {
    const clamp = (value: number, min: number, max: number) =>
      Math.min(Math.max(value, min), max);
    let [x0, y0, x1, y1] = box.map((value) => (Number.isFinite(value) ? value : 0));
    if (tableBoxNormalized) {
      x0 = clamp(x0, 0, tableBoxClampMax);
      y0 = clamp(y0, 0, tableBoxClampMax);
      x1 = clamp(x1, 0, tableBoxClampMax);
      y1 = clamp(y1, 0, tableBoxClampMax);
    }
    if (x1 <= x0) {
      x1 = clamp(x0 + tableBoxMinSize, 0, tableBoxClampMax);
    }
    if (y1 <= y0) {
      y1 = clamp(y0 + tableBoxMinSize, 0, tableBoxClampMax);
    }
    return [x0, y0, x1, y1];
  };

  const normalizeRowEdges = (edges: number[]) => {
    const clamp = (value: number) => Math.min(Math.max(value, 0), 1);
    const cleaned = edges
      .map((value) => (Number.isFinite(value) ? clamp(value) : null))
      .filter((value): value is number => value != null);
    if (!cleaned.length) {
      return null;
    }
    const unique = Array.from(new Set(cleaned)).sort((a, b) => a - b);
    return unique.length >= 2 ? unique : null;
  };

  const normalizeColumnEdges = (edges: number[]) => {
    const clamp = (value: number) => Math.min(Math.max(value, 0), 1);
    const cleaned = edges
      .map((value) => (Number.isFinite(value) ? clamp(value) : null))
      .filter((value): value is number => value != null);
    if (!cleaned.length) {
      return null;
    }
    const unique = Array.from(new Set(cleaned)).sort((a, b) => a - b);
    return unique.length >= 2 ? unique : null;
  };

  const updateGridParam = (key: keyof GridParams, rawValue: string) => {
    if (!rawValue.trim()) return;
    const parsed = Number(rawValue);
    if (Number.isNaN(parsed)) return;
    setGridParamsDraft((prev) => ({ ...prev, [key]: parsed }));
  };

  const setRowCount = (count: number) => {
    if (!Number.isFinite(count) || count <= 1) {
      setGridRowEdgesDraft(null);
      return;
    }
    const bounds = tableBoxDraft || ocrTableBox || [0, 0, 1, 1];
    const y0 = bounds[1] ?? 0;
    const y1 = bounds[3] ?? 1;
    const span = Math.max(y1 - y0, 0.001);
    const edges = Array.from({ length: count + 1 }, (_, idx) => y0 + (span * idx) / count);
    setGridRowEdgesDraft(edges);
  };

  const setColumnCount = (count: number) => {
    if (!Number.isFinite(count) || count <= 1) {
      setGridColumnEdgesDraft(null);
      return;
    }
    const bounds = tableBoxDraft || ocrTableBox || [0, 0, 1, 1];
    const x0 = bounds[0] ?? 0;
    const x1 = bounds[2] ?? 1;
    const span = Math.max(x1 - x0, 0.001);
    const edges = Array.from({ length: count + 1 }, (_, idx) => x0 + (span * idx) / count);
    setGridColumnEdgesDraft(edges);
  };

  const updateRowEdgesText = (raw: string) => {
    if (!raw.trim()) {
      setGridRowEdgesDraft(null);
      return;
    }
    const values = raw
      .split(/[,、\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => Number(item))
      .filter((num) => !Number.isNaN(num))
      .map((num) => (num > 1 ? num / 100 : num));
    const normalized = normalizeRowEdges(values);
    setGridRowEdgesDraft(normalized);
  };

  const updateColumnEdgesText = (raw: string) => {
    if (!raw.trim()) {
      setGridColumnEdgesDraft(null);
      return;
    }
    const values = raw
      .split(/[,、\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => Number(item))
      .filter((num) => !Number.isNaN(num))
      .map((num) => (num > 1 ? num / 100 : num));
    const normalized = normalizeColumnEdges(values);
    setGridColumnEdgesDraft(normalized);
  };

  const formatRowEdgesText = (edges: number[] | null) => {
    if (!edges || edges.length < 2) return "";
    return edges
      .slice(1, -1)
      .map((edge) => (edge * 100).toFixed(1).replace(/\.0$/, ""))
      .join(", ");
  };

  const formatColumnEdgesText = (edges: number[] | null) => {
    if (!edges || edges.length < 2) return "";
    return edges
      .slice(1, -1)
      .map((edge) => (edge * 100).toFixed(1).replace(/\.0$/, ""))
      .join(", ");
  };

  const updateTableBoxDraft = (next: number[]) => {
    setTableBoxDraft(normalizeTableBox(next));
  };

  const updateTableBoxIndex = (index: number, rawValue: string) => {
    if (!tableBoxDraft) return;
    if (!rawValue.trim()) return;
    const parsed = Number(rawValue);
    if (Number.isNaN(parsed)) return;
    const next = [...tableBoxDraft];
    next[index] = parsed;
    updateTableBoxDraft(next);
  };

  const nudgeTableBox = (dx: number, dy: number) => {
    if (!tableBoxDraft) return;
    const [x0, y0, x1, y1] = tableBoxDraft;
    updateTableBoxDraft([x0 + dx, y0 + dy, x1 + dx, y1 + dy]);
  };

  const expandTableBox = (delta: number) => {
    if (!tableBoxDraft) return;
    const [x0, y0, x1, y1] = tableBoxDraft;
    updateTableBoxDraft([x0 - delta, y0 - delta, x1 + delta, y1 + delta]);
  };

  const toggleTableBoxEditor = () => {
    setShowTableBoxEditor((prev) => {
      const next = !prev;
      if (next && !tableBoxDraft) {
        setTableBoxDraft(ocrTableBox ? [...ocrTableBox] : [0.05, 0.2, 0.95, 0.9]);
      }
      if (next && !ocrPages.length && !ocrPagesLoading) {
        void loadOcrPages({ silent: true, force: true });
      }
      if (!next && tableBoxDraft) {
        setOcrTableBox([...tableBoxDraft]);
        setGridParams({ ...gridParamsDraft });
        setGridColumnEdges(gridColumnEdgesDraft ? [...gridColumnEdgesDraft] : null);
        setGridRowEdges(gridRowEdgesDraft ? [...gridRowEdgesDraft] : null);
      }
      return next;
    });
    setTableBoxMessage("");
    setGridDetectMessage("");
  };

  const detectGridEdges = async () => {
    if (!order) return;
    setGridDetecting(true);
    setGridDetectMessage("自動で合わせています...");
    try {
      const payload: Record<string, any> = {};
      if (tableBoxDraft && tableBoxDraft.length >= 4) {
        payload.table_box = tableBoxDraft;
      }
      const expectedColumns =
        gridParamsDraft.grid_expected_columns > 1 ? gridParamsDraft.grid_expected_columns : null;
      const suggestedColumns =
        !expectedColumns && overlayColumnCount > 1 ? overlayColumnCount : null;
      const gridParamsPayload: Record<string, any> = {
        ...gridParamsDraft,
        ...(suggestedColumns ? { grid_expected_columns: suggestedColumns } : {}),
        grid_auto_table_box: true,
        grid_auto_use_raw_edges: true,
        grid_source: "layout_overlay",
      };
      payload.grid_params = gridParamsPayload;
      const res = await apiClient.post(`/orders/${order.id}/grid-detect`, payload);
      const detectedBox = Array.isArray(res.data?.table_box) ? res.data.table_box : null;
      const detectedColumns = Array.isArray(res.data?.grid_column_edges)
        ? res.data.grid_column_edges
        : null;
      const detectedRows = Array.isArray(res.data?.grid_row_edges) ? res.data.grid_row_edges : null;
      const detectedUnits = typeof res.data?.table_units === "string" ? res.data.table_units : null;
      const fallback = Boolean(res.data?.fallback);
      if (detectedBox && detectedBox.length >= 4) {
        setTableBoxDraft(detectedBox.slice(0, 4));
      }
      if (detectedUnits) {
        setTableBoxUnitsOverride(detectedUnits);
      }
      if (detectedColumns && detectedColumns.length >= 2) {
        setGridColumnEdgesDraft([...detectedColumns]);
      }
      if (detectedRows && detectedRows.length >= 2) {
        setGridRowEdgesDraft([...detectedRows]);
        const headerRows = ocrTableHeader.length ? 1 : 0;
        const targetRows = Math.max(detectedRows.length - 1 - headerRows, 0);
        if (targetRows > ocrTableRows.length) {
          const columnCount = getColumnCount(ocrTableHeader, ocrTableRows);
          setOcrTableRows((prev) => {
            const next = prev.map((row) => [...row]);
            while (next.length < targetRows) {
              next.push(Array.from({ length: columnCount }, () => ""));
            }
            return next;
          });
        }
      }
      const confidence =
        typeof res.data?.confidence === "number" ? res.data.confidence.toFixed(2) : null;
      setGridDetectMessage(
        `自動検出しました${fallback ? " (低信頼)" : ""}${confidence ? ` (信頼度 ${confidence})` : ""}。必要なら微調整して保存してください。`,
      );
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 404) {
        const reason =
          typeof detail?.reason === "string"
            ? detail.reason
            : typeof detail === "string"
              ? detail
              : "";
        setGridDetectMessage(reason ? `自動検出できませんでした。(${reason})` : "自動検出できませんでした。");
      } else if (status === 400) {
        setGridDetectMessage("table_boxを確認してください。");
      } else {
        setGridDetectMessage("自動検出に失敗しました。");
      }
    } finally {
      setGridDetecting(false);
    }
  };

  const saveTableBox = async () => {
    if (!facility) {
      setTableBoxMessage("施設を選択してください。");
      return;
    }
    if (!tableBoxDraft) {
      setTableBoxMessage("table_boxがありません。");
      return;
    }
    setTableBoxSaving(true);
    setTableBoxMessage("保存中...");
    try {
      let config = facilityConfig;
      if (!config) {
        const res = await apiClient.get(`/facilities/${facility}`);
        config = res.data?.config || {};
        setFacilityConfig(config);
      }
      const nextConfig = { ...(config || {}) };
      const override = { ...(nextConfig.fax_template_override || {}) };
      override.table_box = tableBoxDraft;
      override.grid_table_box = tableBoxDraft;
      if (gridColumnEdgesDraft && gridColumnEdgesDraft.length >= 2) {
        override.grid_column_edges = gridColumnEdgesDraft;
      }
      if (gridRowEdgesDraft && gridRowEdgesDraft.length >= 2) {
        override.grid_row_edges = gridRowEdgesDraft;
      }
      if (gridParamsDraft) {
        Object.entries(gridParamsDraft).forEach(([key, value]) => {
          if (typeof value === "number" && Number.isFinite(value)) {
            override[key] = value;
          }
        });
      }
      if (tableBoxUnitsOverride || ocrTableUnits) {
        override.units = tableBoxUnitsOverride || ocrTableUnits;
      }
      nextConfig.fax_template_override = override;
      await apiClient.put(`/facilities/${facility}/config`, { config: nextConfig });
      setTableBoxMessage("保存しました。再解析で反映されます。");
      setOcrTableBox([...tableBoxDraft]);
      setGridParams({ ...gridParamsDraft });
      setGridColumnEdges(gridColumnEdgesDraft ? [...gridColumnEdgesDraft] : null);
      setGridRowEdges(gridRowEdgesDraft ? [...gridRowEdgesDraft] : null);
    } catch (err: any) {
      const status = err?.response?.status;
      setTableBoxMessage(status === 403 ? "権限がありません。" : "保存に失敗しました。");
    } finally {
      setTableBoxSaving(false);
    }
  };

  const resetTableBoxDraft = () => {
    if (!ocrTableBox) {
      setTableBoxDraft(null);
      return;
    }
    setTableBoxDraft([...ocrTableBox]);
    setGridParamsDraft({ ...gridParams });
    setGridColumnEdgesDraft(gridColumnEdges ? [...gridColumnEdges] : null);
    setGridRowEdgesDraft(gridRowEdges ? [...gridRowEdges] : null);
    setTableBoxMessage("");
    setTableBoxUnitsOverride(null);
  };

  const addOcrTableRow = () => {
    if (ocrHardRecoveryMode) {
      setOcrTableMessage("現在は基盤復旧待ちのため、行の追加操作を止めています。");
      return;
    }
    const columnCount = getColumnCount(ocrSheetHeader, ocrSheetRows);
    markOcrSheetEdited();
    setOcrSheetRows((prev) => [...prev, Array.from({ length: columnCount }, () => "")]);
    setOcrSheetRowIds((prev) => [...prev, makeSheetRowId("manual")]);
  };

  const duplicateOcrTableRow = (rowIndex: number) => {
    if (ocrHardRecoveryMode) {
      setOcrTableMessage("現在は基盤復旧待ちのため、行の複製操作を止めています。");
      return;
    }
    markOcrSheetEdited();
    setOcrSheetRows((prev) => {
      const next = prev.map((row) => [...row]);
      const row = next[rowIndex];
      if (!row) return prev;
      next.splice(rowIndex + 1, 0, [...row]);
      return next;
    });
    setOcrSheetRowIds((prev) => {
      const next = [...prev];
      const sourceId = next[rowIndex] || `sheet-${rowIndex + 1}`;
      next.splice(rowIndex + 1, 0, `${sourceId}-copy-${Date.now()}`);
      return next;
    });
  };

  const removeOcrTableRow = (rowIndex: number) => {
    if (ocrHardRecoveryMode) {
      setOcrTableMessage("現在は基盤復旧待ちのため、行の削除操作を止めています。");
      return;
    }
    markOcrSheetEdited();
    setOcrSheetRows((prev) => prev.filter((_, idx) => idx !== rowIndex));
    setOcrSheetRowIds((prev) => prev.filter((_, idx) => idx !== rowIndex));
  };

  const shiftOcrTableRange = (offset: number) => {
    if (ocrHardRecoveryMode) {
      setOcrTableMessage("現在は基盤復旧待ちのため、数量シフト操作を止めています。");
      return;
    }
    if (!offset) return;
    const totalRows = ocrSheetRows.length;
    if (!totalRows) {
      setOcrTableMessage("シフトできる行がありません。");
      return;
    }
    const parsedStart = Number.parseInt(ocrShiftStartRow, 10);
    const parsedEnd = Number.parseInt(ocrShiftEndRow, 10);
    const start = Number.isFinite(parsedStart) ? parsedStart - 1 : 0;
    const end = Number.isFinite(parsedEnd) ? parsedEnd - 1 : totalRows - 1;
    const normalizedStart = Math.max(0, Math.min(start, end, totalRows - 1));
    const normalizedEnd = Math.max(normalizedStart, Math.min(Math.max(start, end), totalRows - 1));
    const quantityColumnIndexes = ocrSheetColumnSpecs
      .map((spec, idx) => (spec.className === "ocr-sheet-col-qty" ? idx : -1))
      .filter((idx) => idx >= 0);
    if (!quantityColumnIndexes.length) {
      setOcrTableMessage("シフト対象の数量列が見つかりません。");
      return;
    }
    const touchedCells = Array.from({ length: normalizedEnd - normalizedStart + 1 }, (_, rowOffset) =>
      quantityColumnIndexes.map((colIdx) => ({
        rowIndex: normalizedStart + rowOffset,
        cellIndex: colIdx,
      })),
    ).flat();
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) => {
      const next = prev.map((row) => [...row]);
      quantityColumnIndexes.forEach((colIdx) => {
        const values = [];
        for (let rowIdx = normalizedStart; rowIdx <= normalizedEnd; rowIdx += 1) {
          values.push(prev[rowIdx]?.[colIdx] ?? "");
        }
        for (let rowIdx = normalizedStart; rowIdx <= normalizedEnd; rowIdx += 1) {
          const sourceRow = rowIdx - offset;
          next[rowIdx][colIdx] =
            sourceRow >= normalizedStart && sourceRow <= normalizedEnd
              ? values[sourceRow - normalizedStart] ?? ""
              : "";
        }
      });
      return next;
    });
    if (!ocrShiftStartRow) setOcrShiftStartRow(String(normalizedStart + 1));
    if (!ocrShiftEndRow) setOcrShiftEndRow(String(normalizedEnd + 1));
    setOcrTableMessage(
      `数量列を ${normalizedStart + 1} 行目から ${normalizedEnd + 1} 行目まで ${Math.abs(offset)} 行${offset > 0 ? "下" : "上"}へずらしました。`,
    );
  };

  const swapOcrSheetColumns = (leftIndex: number, rightIndex: number) => {
    if (ocrHardRecoveryMode) {
      setOcrTableMessage("現在は基盤復旧待ちのため、列の入替操作を止めています。");
      return;
    }
    if (leftIndex === rightIndex) {
      setOcrTableMessage("別々の列を選択してください。");
      return;
    }
    if (
      ocrSheetColumnSpecs[leftIndex]?.className !== "ocr-sheet-col-qty"
      || ocrSheetColumnSpecs[rightIndex]?.className !== "ocr-sheet-col-qty"
    ) {
      setOcrTableMessage("数量列だけを入れ替えられます。");
      return;
    }
    const maxIndex = Math.max(leftIndex, rightIndex);
    const touchedCells = Array.from({ length: ocrSheetRows.length }, (_, rowIndex) => [
      { rowIndex, cellIndex: leftIndex },
      { rowIndex, cellIndex: rightIndex },
    ]).flat();
    markOcrSheetEdited({ preserveOverlay: true, touchedCells });
    setOcrSheetRows((prev) =>
      prev.map((row) => {
        const next = [...row];
        while (next.length <= maxIndex) {
          next.push("");
        }
        const leftValue = next[leftIndex] ?? "";
        next[leftIndex] = next[rightIndex] ?? "";
        next[rightIndex] = leftValue;
        return next;
      }),
    );
    setOcrTableMessage(`数量列 ${leftIndex + 1} と ${rightIndex + 1} の数字を入れ替えました。`);
  };

  const applySelectedOcrSheetColumnSwap = () => {
    const leftValue = String(ocrSwapLeftColumnRef.current?.value || ocrSwapLeftColumn || "").trim();
    const rightValue = String(ocrSwapRightColumnRef.current?.value || ocrSwapRightColumn || "").trim();
    const leftIndex = Number.parseInt(leftValue, 10);
    const rightIndex = Number.parseInt(rightValue, 10);
    if (!Number.isInteger(leftIndex) || !Number.isInteger(rightIndex)) {
      setOcrTableMessage("入れ替える2つの数量列を選択してください。");
      return;
    }
    swapOcrSheetColumns(leftIndex, rightIndex);
  };

  const toggleCurrentOrderArchive = async (nextArchived: boolean) => {
    if (!order || archiveOrderBusy) return;
    const confirmMessage = nextArchived
      ? `注文 ${order.id} をアーカイブします。通常の注文一覧から除外されます。`
      : `注文 ${order.id} のアーカイブを解除します。`;
    if (!window.confirm(confirmMessage)) {
      return;
    }
    setArchiveOrderBusy(true);
    setActionMessage(nextArchived ? "注文をアーカイブ中..." : "アーカイブを解除中...");
    try {
      await apiClient.post(`/orders/${order.id}/${nextArchived ? "archive" : "unarchive"}`);
      await refreshOrderWorkspace({ preserveSelections: true, reloadHistory: false });
      setActionMessage(
        nextArchived
          ? "注文をアーカイブしました。通常の注文一覧から除外されます。"
          : "注文のアーカイブを解除しました。",
      );
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail?.error ||
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        (nextArchived ? "注文のアーカイブに失敗しました。" : "アーカイブ解除に失敗しました。");
      setActionMessage(String(detail));
    } finally {
      setArchiveOrderBusy(false);
    }
  };

  const runOcrSheetPostMutationRefreshInBackground = (
    task: () => Promise<void>,
  ) => {
    void task().catch((error) => {
      console.error("ocr sheet background refresh failed", error);
    });
  };

  const applyOcrTable = async (
    options: { deferRefresh?: boolean; expectedRevisionId?: string | null } = {},
  ): Promise<{ ok: boolean; message: string }> => {
    const {
      deferRefresh = false,
      expectedRevisionId = null,
    } = options;
    if (!order) {
      return { ok: false, message: "注文が見つかりません。" };
    }
    if (ocrHardRecoveryMode) {
      const message = "現在は基盤復旧待ちのため、明細への反映は停止しています。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    const trimmedFacility = facility.trim();
    const persistedFacility = (order.facility || "").trim();
    const normalizedWeek = normalizeConcreteWeekValue(weekDraft);
    const persistedWeek = normalizeConcreteWeekValue(order.persisted_week_value || order.week_value || order.week || "");
    if (!trimmedFacility && !persistedFacility) {
      const message = "施設が未設定のため、OCRテーブルを反映できません。先に Step1（注文書）で施設を設定してください。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    if (!normalizedWeek && !persistedWeek) {
      const message = "週が未設定のため、OCRテーブルを反映できません。先に Step1（注文書）で週を設定してください。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    if (!effectiveCanApply) {
      const message = reviewBlockerText
        ? `まだ明細へ反映できません: ${reviewBlockerText}`
        : "まだ明細へ反映できません。Step2 の条件を解消してから再試行してください。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    if (
      (trimmedFacility && persistedFacility !== trimmedFacility)
      || (normalizedWeek && persistedWeek !== normalizedWeek)
    ) {
      const message = hasAuthoritativeSavedSheet
        ? "Step1 の施設または週に未保存の変更があります。先に保存し、保存済みシートの数量を保持するか空白へ戻すかを選択してください。"
        : "Step1 の施設または週に未保存の変更があります。先に Step1 で保存してから明細へ反映してください。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    let targetHeader = ocrSheetHeader;
    let targetRows = ocrSheetRows;
    let targetFields = ocrSheetFields;
    let targetRowIds = ocrSheetRowIds;

    if (!targetRows.length) {
      const loaded = await loadOcrSheet({ silent: true });
      if (loaded) {
        targetHeader = loaded.header;
        targetRows = loaded.rows;
        targetFields = loaded.fields;
        targetRowIds = loaded.rowIds;
      }
    }
    if (!targetRows.length) {
      const message = "編集できる表がありません。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    const markdown = buildMarkdownTable(targetHeader, targetRows);
    setOcrTableSaving(true);
    setOcrTableMessage("OCRテーブルを反映中...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/ocr-apply`, {
        markdown,
        header: targetHeader,
        rows: targetRows,
        ui_mode: "sheet",
        fields: targetFields,
        row_ids: targetRowIds,
        expected_revision_id: expectedRevisionId || currentSheetRevisionIdForMutation() || null,
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      replaceAuthoritativeOrder(res.data as OrderDetail);
      resetSheetReviewMeta();
      setLineEditsDirty(false);
      const message = "明細に反映しました。Step3で内容を確認してください。";
      setOcrTableMessage(message);
      const postApplyRefresh = async () => {
        await refreshOrderWorkspace({ preserveSelections: true, force: true });
        await refreshOcrOutput(order.id);
        await loadOcrHistory({ silent: true });
        await loadOcrSheet({ silent: true });
        await rebuildBags();
      };
      if (deferRefresh) {
        runOcrSheetPostMutationRefreshInBackground(postApplyRefresh);
      } else {
        await postApplyRefresh();
      }
      return { ok: true, message };
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error;
      if (status === 409 && (detailError === "stale_revision_conflict" || detailError === "stale_lines_conflict")) {
        const message =
          detailError === "stale_lines_conflict"
            ? "明細が他の画面で更新されました。最新状態を読み込み直しました。"
            : "別の画面で新しい下書きが保存されました。最新状態を読み込み直しました。";
        await handleRemoteUpdateConflict(message, { reloadSheet: true, reloadHistory: true, reloadBags: true });
        return { ok: false, message };
      }
      const message = resolveOcrApplyErrorMessage(status, detail);
      setOcrTableMessage(message);
      return { ok: false, message };
    } finally {
      setOcrTableSaving(false);
    }
  };

  const saveOcrSheetExact = async (
    options: { deferRefresh?: boolean; successMessage?: string } = {},
  ): Promise<{ ok: boolean; message: string; revisionId?: string | null }> => {
    const {
      deferRefresh = false,
      successMessage,
    } = options;
    if (!order) {
      return { ok: false, message: "注文が見つかりません。" };
    }
    if (ocrHardRecoveryMode) {
      const message = "現在は基盤復旧待ちのため、シートの保存は停止しています。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    let targetHeader = ocrSheetHeader;
    let targetRows = ocrSheetRows;
    let targetFields = ocrSheetFields;
    let targetRowIds = ocrSheetRowIds;

    if (!targetRows.length && !ocrSheetLoading) {
      const loaded = await loadOcrSheet({ silent: true });
      if (loaded) {
        targetHeader = loaded.header;
        targetRows = loaded.rows;
        targetFields = loaded.fields;
        targetRowIds = loaded.rowIds;
      }
    }
    if (!targetRows.length) {
      const message = "保存できるシートがありません。";
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    setOcrTableSaving(true);
    setOcrTableMessage("シートを保存中...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/draft-sheet`, {
        header: targetHeader,
        rows: targetRows,
        ui_mode: "sheet",
        fields: targetFields,
        row_ids: targetRowIds,
        expected_revision_id: currentSheetRevisionIdForMutation() || null,
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      const revisionId =
        String(res.data?.draft?.id || "").trim()
        || String(res.data?.current_sheet_revision_id || "").trim()
        || String(res.data?.draft_payload?.current_sheet_revision_id || "").trim()
        || String(res.data?.revision?.revision_id || "").trim()
        || null;
      const message = successMessage || `シートを保存しました。次に「${STEP2_APPLY_NEXT_LABEL}」を押してください。`;
      setOcrTableMessage(message);
      if (deferRefresh) {
        return { ok: true, message, revisionId };
      }
      await refreshOrderWorkspace({ preserveSelections: true, force: true });
      await refreshOcrOutput(order.id);
      await loadOcrHistory({ silent: true });
      await loadOcrSheet({ silent: true });
      return { ok: true, message, revisionId };
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error;
      let message = "シートの保存に失敗しました。";
      if (status === 404) {
        message = "注文が見つかりません。";
      } else if (status === 400 && detail === "rows_empty") {
        message = "保存できるシートがありません。";
      } else if (
        status === 409 &&
        (detailError === "stale_revision_conflict" || detailError === "stale_lines_conflict")
      ) {
        message = detailError === "stale_lines_conflict"
          ? "別の画面で明細が更新されました。最新状態を読み込み直しました。"
          : "別の画面で新しい下書きが保存されました。最新状態を読み込み直しました。";
        await handleRemoteUpdateConflict(message, { reloadSheet: true, reloadHistory: true });
      }
      setOcrTableMessage(message);
      return { ok: false, message };
    } finally {
      setOcrTableSaving(false);
    }
  };

  const handleExpandedCellCopyModeChange = async (nextMode: ExpandedCellCopyMode) => {
    if (nextMode === expandedCellCopyMode) {
      return;
    }
    if (ocrSheetEditedSinceLoadRef.current) {
      setOcrSheetMessage("手修正済みのシートがあるため、切替前に保存するか再読み込みしてください。");
      return;
    }
    if (nextMode === "persisted") {
      if (!facility.trim()) {
        setOcrSheetMessage("施設を選択してください。");
        return;
      }
      setExpandedCellCopySaving(true);
      setOcrSheetMessage("拡大セル設定を保存中...");
      try {
        let config = facilityConfig;
        if (!config) {
          const res = await apiClient.get(`/facilities/${facility}`);
          config = res.data?.config || {};
          setFacilityConfig(config);
          setFacilityResolvedConfig(res.data?.resolved_config || null);
        }
        const nextConfig = {
          ...(config || {}),
          [EXPANDED_CELL_COPY_FACILITY_KEY]: true,
        };
        const res = await apiClient.put(`/facilities/${facility}/config`, { config: nextConfig });
        setFacilityConfig(nextConfig);
        setFacilityResolvedConfig(res.data?.resolved_config || nextConfig);
        setExpandedCellCopyMode("persisted");
        await loadOcrSheet({ silent: true });
        setOcrSheetMessage("この施設で拡大セルコピーを有効化しました。");
      } catch (err: any) {
        const status = err?.response?.status;
        setOcrSheetMessage(status === 403 ? "権限がありません。" : "拡大セル設定の保存に失敗しました。");
      } finally {
        setExpandedCellCopySaving(false);
      }
      return;
    }
    setExpandedCellCopyMode(nextMode);
    setOcrSheetMessage(
      nextMode === "enabled"
        ? "この注文でのみ拡大セルコピーを有効化しました。"
        : "この画面では拡大セルコピーを無効化しました。",
    );
  };

  const enterOcrEditMode = () => {
    setOcrEditMode(true);
    setShowOcrEdit(true);
    setShowTableBoxEditor(false);
    if (!ocrSheetRows.length && !ocrSheetLoading) {
      loadOcrSheet();
    }
  };

  const exitOcrEditMode = () => {
    setOcrEditMode(false);
    setShowOcrEdit(false);
    setShowTableBoxEditor(false);
  };

  const applyOcrAndMoveToDetails = async (
    authoritativeOrder?: OrderDetail | null,
    options: { expectedRevisionId?: string | null } = {},
  ) => {
    const { expectedRevisionId = null } = options;
    const currentOrder = authoritativeOrder || order;
    if (!currentOrder) return;
    const candidateEvidenceState = resolveCandidateEvidenceState(currentOrder);
    const currentWorkflowStateCode = candidateEvidenceState.workflowStateCode;
    const currentStep2ChoiceRequired = (
      Array.isArray(currentOrder.critical_decisions)
        ? currentOrder.critical_decisions
        : Array.isArray(currentOrder.workflow_state?.critical_decisions)
          ? currentOrder.workflow_state?.critical_decisions || []
          : []
    ).some((item) => {
      const decisionType = String(item?.decision_type || "").trim().toLowerCase();
      return decisionType !== "facility" && decisionType !== "week" && !String(item?.selected_value || "").trim();
    });
    const currentRerunInProgressState =
      currentWorkflowStateCode === "rerun_in_progress" ||
      ["running", "pending"].includes(String(currentOrder.workflow_state?.reparse_state?.status || "").trim().toLowerCase());
    const currentSemanticShellOnly = currentWorkflowStateCode === "semantic_shell_only";
    if (ocrHardRecoveryMode) {
      setActionMessage("現在は基盤復旧待ちのため、明細反映処理を止めています。");
      return;
    }
    if (currentStep2ChoiceRequired) {
      setActionMessage("OCR候補の選択が未完了です。Step2 で候補を確定してから明細へ反映してください。");
      return;
    }
    if (currentRerunInProgressState) {
      setActionMessage("OCRパイプラインを再実行しています。完了後に新しい候補を確認してください。");
      return;
    }
    if (currentSemanticShellOnly) {
      setActionMessage("メニュー枠はありますが、数量はまだ信用できません。Step2 で OCR 基盤を整えてから明細へ反映してください。");
      return;
    }
    let activeRows = ocrSheetRows;
    if (!activeRows.length) {
      const loaded = await loadOcrSheet({ silent: true });
      if (loaded) {
        activeRows = loaded.rows;
      }
    }
    if (!activeRows.length) {
      setActionMessage("編集できる表がありません。");
      return;
    }
    setActionMessage("OCR結果を明細に反映中...");
    const result = await applyOcrTable({ deferRefresh: true, expectedRevisionId });
    if (result.ok) {
      setActionMessage("明細に反映しました。Step3で内容を確認してください。");
      exitOcrEditMode();
      setActiveStep(2);
    } else {
      setActionMessage(result.message);
    }
  };

  const completeStep2AndMoveToDetails = async () => {
    if (ocrTableSaving) return;
    let authoritativeOrder = order;
    let expectedRevisionId = currentSheetRevisionIdForMutation() || null;
    if (ocrHasEditableSheet && canSaveDraftSheet) {
      const saved = await saveOcrSheetExact({
        deferRefresh: true,
        successMessage: "シートを保存しました。明細へ反映しています...",
      });
      if (!saved.ok) {
        setActionMessage(saved.message);
        return;
      }
      expectedRevisionId = saved.revisionId || expectedRevisionId;
    }
    await applyOcrAndMoveToDetails(authoritativeOrder, { expectedRevisionId });
  };

  const saveLines = async () => {
    if (!order) return;
    try {
      await apiClient.put(`/orders/${order.id}/lines`, {
        lines: order.lines || [],
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      setLineEditsDirty(false);
      await refreshOrderWorkspace({ preserveSelections: true, reloadBags: true, force: true });
      setActionMessage("保存しました。");
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error;
      if (status === 409 && detailError === "stale_lines_conflict") {
        await handleRemoteUpdateConflict("明細が他の画面で更新されました。最新状態を読み込み直しました。", {
          reloadBags: true,
        });
        return;
      }
      setActionMessage("保存に失敗しました。");
    }
  };

  const confirm = async (
    options?: {
      successMessage?: string;
      refreshWorkspaceAfterSuccess?: boolean;
    },
  ) => {
    if (!order) return;
    if (confirmSaving) return false;
    if (sheetWeeklyMenuMissing) {
      setActionMessage("この週の月次メニューが未登録のため、まだ確定できません。先に月次メニューを登録してください。");
      return false;
    }
    if (!ocrSheetCanConfirm && ocrSheetConfirmBlockers.length) {
      const blockerText = ocrSheetConfirmBlockers.map((item) => describeReviewBlocker(item)).filter(Boolean).join(" / ");
      setActionMessage(
        blockerText
          ? `まだ確定できません。Step2で内容を整えてから再度お試しください: ${blockerText}`
          : "まだ確定できません。Step2で内容を整えてから再度お試しください。",
      );
      return false;
    }
    setConfirmSaving(true);
    try {
      const refreshWorkspaceAfterSuccess = options?.refreshWorkspaceAfterSuccess !== false;
      setActionMessage("確定中...");
      await apiClient.post(`/orders/${order.id}/confirm`, {
        expected_revision_id: currentSheetRevisionIdForMutation() || null,
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      setLineEditsDirty(false);
      let refreshFailed = false;
      if (refreshWorkspaceAfterSuccess) {
        try {
          await refreshOrderWorkspace({
            preserveSelections: true,
            reloadBags: true,
            reloadHistory: true,
            force: true,
          });
        } catch {
          refreshFailed = true;
        }
      }
      const successMessage = options?.successMessage ?? "確定しました。";
      setActionMessage(
        refreshFailed ? `${successMessage} 最新状態の取得に失敗しました。` : successMessage,
      );
      return true;
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error;
      if (status === 409 && (detailError === "stale_revision_conflict" || detailError === "stale_lines_conflict")) {
        const message =
          detailError === "stale_lines_conflict"
            ? "明細が他の画面で更新されました。最新状態を読み込み直しました。"
            : "別の画面で下書きが更新されました。最新状態を読み込み直しました。";
        await handleRemoteUpdateConflict(message, { reloadSheet: true, reloadHistory: true, reloadBags: true });
        return false;
      }
      const blockers = Array.isArray(detail?.blockers)
        ? detail.blockers.map((item: unknown) => describeReviewBlocker(String(item || ""))).filter(Boolean)
        : [];
      const message = typeof detail?.message === "string" ? detail.message : "";
      if (blockers.length) {
        setActionMessage(`まだ確定できません。Step2で内容を整えてから再度お試しください: ${blockers.join(" / ")}`);
      } else if (message) {
        setActionMessage(`確定に失敗しました: ${message}`);
      } else {
        setActionMessage("確定に失敗しました。");
      }
      return false;
    } finally {
      setConfirmSaving(false);
    }
  };

  const registerTrainingSample = async (
    options?: { source?: string; note?: string; successMessage?: string },
  ) => {
    if (!order) return;
    setTrainingSampleSaving(true);
    try {
      const res = await apiClient.post(`/ocr/training-samples/from-order/${order.id}`, {
        source: options?.source ?? "manual",
        note: options?.note ?? "registered from order detail",
      });
      const sampleId = typeof res.data?.sample?.id === "string" ? res.data.sample.id : "";
      const lineCount =
        typeof res.data?.sample?.line_count === "number" ? res.data.sample.line_count : null;
      const lineText = lineCount == null ? "" : ` (${lineCount}行)`;
      setActionMessage(
        options?.successMessage
          ? options.successMessage
          : sampleId
            ? `学習データに登録しました。sample_id=${sampleId}${lineText}`
            : `学習データに登録しました。${lineText}`,
      );
      return true;
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailText = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : "";
      if (status === 404) {
        setActionMessage("注文が見つからないため、学習データ登録できませんでした。");
      } else if (status === 400) {
        setActionMessage(
          detailText
            ? `学習データ登録に失敗しました: ${detailText}`
            : "学習データ登録に失敗しました。",
        );
      } else if (status === 403) {
        setActionMessage("権限がないため、学習データ登録できません。");
      } else {
        setActionMessage("学習データ登録中にエラーが発生しました。");
      }
      return false;
    } finally {
      setTrainingSampleSaving(false);
    }
  };

  const confirmAndReturnToOrders = async () => {
    if (!order) return;
    if (confirmSaving || trainingSampleSaving) return;
    if (orderAlreadyConfirmed) {
      await router.push("/orders");
      return;
    }
    const confirmed = await confirm({
      successMessage: "注文一覧へ戻ります...",
      refreshWorkspaceAfterSuccess: false,
    });
    if (!confirmed) return;
    await router.push("/orders");
  };

  const saveFacility = async (facilityId: string): Promise<boolean> => {
    return persistFacilitySelection(facilityId);
  };

  const persistFacilitySelection = async (
    facilityId: string,
    options: { refreshWorkspace?: boolean; skipSheetRefresh?: boolean } = {},
  ): Promise<boolean> => {
    if (!order) return false;
    const { refreshWorkspace = true, skipSheetRefresh = true } = options;
    const trimmed = facilityId.trim();
    if (!trimmed) {
      setActionMessage("施設IDを入力してください。");
      return false;
    }
    try {
      await apiClient.post(`/orders/${order.id}/facility`, {
        facility: trimmed,
        expected_current_facility: order.facility || null,
        refresh_current_sheet: !skipSheetRefresh,
      });
      setLineEditsDirty(false);
      if (refreshWorkspace) {
        await refreshOrderWorkspace({ preserveSelections: true, reloadWeekOptions: false });
      }
      setActionMessage("施設を設定しました。");
      return true;
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setActionMessage("施設が見つかりません。");
      } else if (status === 409) {
        await handleRemoteUpdateConflict("施設設定が他の画面で更新されました。最新状態を読み込み直しました。");
      } else {
        setActionMessage("施設の設定に失敗しました。");
      }
      return false;
    }
  };

  const saveWeek = async (weekId: string): Promise<boolean> => {
    return persistWeekSelection(weekId);
  };

  const persistWeekSelection = async (
    weekId: string,
    options: { refreshWorkspace?: boolean } = {},
  ): Promise<boolean> => {
    if (!order) return false;
    const { refreshWorkspace = true } = options;
    const normalizedWeek = normalizeConcreteWeekValue(weekId);
    if (!normalizedWeek) {
      setActionMessage("週の形式が不正です。候補から選択してください。");
      return false;
    }
    try {
      await apiClient.post(`/orders/${order.id}/week`, {
        week: normalizedWeek,
        expected_current_week: getCanonicalWeekSelectionSource(order) || null,
      });
      setLineEditsDirty(false);
      if (refreshWorkspace) {
        await refreshOrderWorkspace({ preserveSelections: true, reloadWeekOptions: false });
      }
      setWeekDraft(normalizedWeek);
      setActionMessage("週を設定しました。");
      return true;
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setActionMessage("注文が見つかりません。");
      } else if (status === 409) {
        await handleRemoteUpdateConflict("週設定が他の画面で更新されました。最新状態を読み込み直しました。");
      } else if (status === 400) {
        setActionMessage("週の設定に失敗しました。候補を確認してください。");
      } else {
        setActionMessage("週の設定に失敗しました。");
      }
      return false;
    }
  };

  const queueSavedSheetContextChange = (payload: PendingSavedSheetContextChange) => {
    if (!payload.facility.trim()) {
      setActionMessage("施設が未確定のため、保存済みシートの切替を進められません。");
      return false;
    }
    if (!normalizeConcreteWeekValue(payload.week)) {
      setActionMessage("週が未確定のため、保存済みシートの切替を進められません。");
      return false;
    }
    setPendingSavedSheetContextChange({
      ...payload,
      facility: payload.facility.trim(),
      week: normalizeConcreteWeekValue(payload.week) || payload.week,
    });
    setActionMessage("保存済みシートがあるため、数量の扱いを選択してください。");
    return true;
  };

  const applySavedSheetContextChange = async (mode: SavedSheetContextChangeMode) => {
    if (!order?.id || !pendingSavedSheetContextChange) return;
    setSavedSheetContextChangeApplying(mode);
    setStep1Saving(true);
    setActionMessage(
      mode === "keep"
        ? "保存済みシートの数量を保ったまま、新しい骨格へ切り替えています..."
        : "保存済みシートの数量を空白へ戻し、新しい骨格へ切り替えています...",
    );
    try {
      if (pendingSavedSheetContextChange.source === "critical_decision") {
        const decisionType = String(pendingSavedSheetContextChange.decisionType || "").trim();
        const decisionValue = String(pendingSavedSheetContextChange.decisionValue || "").trim();
        if (!decisionType || !decisionValue) {
          setActionMessage("候補の反映条件が不足しているため、切替を続けられません。");
          return;
        }
        try {
          await apiClient.post(`/orders/${order.id}/critical-decisions/${decisionType}`, {
            selected_value: decisionValue,
          });
        } catch (err: any) {
          const status = err?.response?.status;
          if (status === 404) {
            setActionMessage("候補情報が見つかりません。最新状態を読み直してください。");
          } else if (status === 400) {
            setActionMessage("候補の反映に失敗しました。");
          } else {
            setActionMessage("候補の反映中にエラーが発生しました。");
          }
          return;
        }
      } else {
        if (pendingSavedSheetContextChange.facility !== persistedFacility) {
          const savedFacility = await persistFacilitySelection(pendingSavedSheetContextChange.facility, {
            refreshWorkspace: false,
            skipSheetRefresh: true,
          });
          if (!savedFacility) return;
        }
        if (pendingSavedSheetContextChange.week !== persistedWeek) {
          const savedWeek = await persistWeekSelection(pendingSavedSheetContextChange.week, {
            refreshWorkspace: false,
          });
          if (!savedWeek) return;
        }
      }
      const overwritten = await forceWeeklyMenuOverwrite({
        blankQuantities: mode === "clear",
        pendingMessage:
          mode === "keep"
            ? "保存済みシートの数量を保ったまま、週次メニュー骨格へ切り替えています..."
            : "保存済みシートの数量を空白へ戻し、週次メニュー骨格へ切り替えています...",
        successMessage:
          mode === "keep"
            ? "保存済みシートの数量を保ったまま、新しい施設/週の骨格へ切り替えました。"
            : "保存済みシートの数量を空白へ戻し、新しい施設/週の骨格へ切り替えました。",
      });
      if (!overwritten) return;
      setPendingSavedSheetContextChange(null);
      if (activeStep === 1) {
        await loadOcrSheet({ silent: true });
      }
    } finally {
      setSavedSheetContextChangeApplying("");
      setStep1Saving(false);
    }
  };

  const updateStep1 = async () => {
    const normalizedWeek = getPendingStep1WeekSelection(
      weekDraft,
      customWeekRangeStart,
      customWeekRangeEnd,
    );
    if (!facility.trim()) {
      setActionMessage("施設を選択してください。");
      return;
    }
    if (!normalizedWeek) {
      setActionMessage("週を選択してください。");
      return;
    }
    const persistedFacility = (order?.facility || "").trim();
    const persistedWeek = normalizeConcreteWeekValue(
      getCanonicalWeekSelectionSource(order),
    );
    const hasContextChange =
      facility.trim() !== persistedFacility || normalizedWeek !== persistedWeek;
    if (hasAuthoritativeSavedSheet && hasContextChange) {
      queueSavedSheetContextChange({
        source: "step1",
        facility: facility.trim(),
        week: normalizedWeek,
      });
      return;
    }
    setStep1Saving(true);
    setActionMessage("設定中...");
    try {
      if (facility.trim() !== persistedFacility) {
        const saved = await persistFacilitySelection(facility, { refreshWorkspace: false });
        if (!saved) return;
      }
      if (normalizedWeek !== persistedWeek) {
        const saved = await persistWeekSelection(normalizedWeek, { refreshWorkspace: false });
        if (!saved) return;
      }
      if (order?.id) {
        await refreshOrderWorkspace({ preserveSelections: true, reloadWeekOptions: true });
      }
      setOcrSheetAutoRetryBlocked(false);
      if (activeStep === 1) {
        await loadOcrSheet();
      }
      setActionMessage("施設と週を設定しました。");
    } finally {
      setStep1Saving(false);
    }
  };

  const applyCustomWeekRange = () => {
    const weekValue = deriveWeekValueFromCalendarRange(customWeekRangeStart, customWeekRangeEnd);
    if (!weekValue) {
      setActionMessage("例外範囲の日付が不正です。");
      return;
    }
    setWeekDraft(weekValue);
    setActionMessage("例外範囲を設定しました。");
  };

  const chooseCriticalDecision = async (decisionType: string, selectedValue: string) => {
    if (!order?.id) return;
    const normalizedType = String(decisionType || "").trim();
    const normalizedValue = String(selectedValue || "").trim();
    if (!normalizedType || !normalizedValue) return;
    if (normalizedType === "facility" || normalizedType === "week") {
      const targetFacility =
        normalizedType === "facility"
          ? normalizedValue
          : (facility.trim() || persistedFacility || "");
      const targetWeek = normalizeConcreteWeekValue(
        normalizedType === "week"
          ? normalizedValue
          : (weekDraft || persistedWeek || ""),
      );
      const contextActuallyChanges =
        targetFacility.trim() !== persistedFacility
        || (targetWeek || "") !== (persistedWeek || "");
      if (hasAuthoritativeSavedSheet && contextActuallyChanges) {
        if (normalizedType === "facility") {
          setFacility(normalizedValue);
        }
        if (normalizedType === "week") {
          setWeekDraft(normalizeWeekValue(normalizedValue));
        }
        queueSavedSheetContextChange({
          source: "critical_decision",
          facility: targetFacility,
          week: targetWeek || "",
          decisionType: normalizedType,
          decisionValue: normalizedValue,
        });
        return;
      }
    }
    setCriticalDecisionSaving(normalizedType);
    setActionMessage("候補を反映中...");
    try {
      await apiClient.post(`/orders/${order.id}/critical-decisions/${normalizedType}`, {
        selected_value: normalizedValue,
      });
      await refreshOrderWorkspace({ preserveSelections: false, reloadSheet: true, reloadHistory: true });
      if (normalizedType === "facility") {
        setFacility(normalizedValue);
      }
      if (normalizedType === "week") {
        setWeekDraft(normalizeWeekValue(normalizedValue));
      }
      setActionMessage("候補を反映しました。");
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setActionMessage("候補情報が見つかりません。最新状態を読み直してください。");
      } else if (status === 400) {
        setActionMessage("候補の反映に失敗しました。");
      } else {
        setActionMessage("候補の反映中にエラーが発生しました。");
      }
    } finally {
      setCriticalDecisionSaving("");
    }
  };

  const keepCurrentDraft = async () => {
    const candidateEvidenceRunId = resolveCandidateEvidenceState(order).candidateEvidenceRunId;
    if (!candidateEvidenceRunId) {
      setActionMessage("現在は新しいOCR候補がありません。");
      return;
    }
    if (!order?.id) return;
    setKeepCurrentPending(true);
    setActionMessage("現在のシート維持を記録しています...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/draft-sheet/keep-current`);
      const nextWorkflowState = res.data && typeof res.data === "object" ? res.data : null;
      applyAuthoritativeWorkflowStateToOrder(nextWorkflowState);
      setStep2WizardChoice("yes");
      setStep2RepairStage("");
      setActionMessage("現在のシートを維持して進みます。必要ならあとで新しいOCR候補へ切り替えられます。");
      void (async () => {
        try {
          const workflowRes = await apiClient.get(`/orders/${order.id}/workflow-state`);
          const refreshedWorkflowState =
            workflowRes.data && typeof workflowRes.data === "object" ? (workflowRes.data as WorkflowStatePayload) : null;
          applyAuthoritativeWorkflowStateToOrder(refreshedWorkflowState);
          await refreshOrderWorkspace({
            preserveSelections: true,
            reloadSheet: true,
            reloadHistory: true,
            force: true,
          });
        } catch {
          // The keep-current acknowledgement is already authoritative. Background refresh is best-effort only.
        }
      })();
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setActionMessage("現在は新しいOCR候補がありません。最新状態を読み直してください。");
      } else {
        setActionMessage("現在のシート維持の記録に失敗しました。");
      }
    } finally {
      setKeepCurrentPending(false);
    }
  };

  const switchDraftToLatestEvidence = async () => {
    if (!order?.id) return;
    setSwitchEvidencePending(true);
    setActionMessage("新しいOCR候補に切り替えています...");
    try {
      await apiClient.post(`/orders/${order.id}/draft-sheet/switch-evidence`);
      await refreshOrderWorkspace({
        preserveSelections: true,
        reloadSheet: true,
        reloadHistory: true,
        force: true,
      });
      await loadOcrSheet({ silent: true });
      setActionMessage("新しいOCR候補に切り替えました。");
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 404) {
        setActionMessage("新しいOCR候補への切替APIがまだ利用できません。現在のシートはそのままです。");
      } else if (status === 409) {
        setActionMessage(detail?.message || "新しいOCR候補へ切り替えられませんでした。");
      } else {
        setActionMessage("新しいOCR候補への切替に失敗しました。");
      }
    } finally {
      setSwitchEvidencePending(false);
    }
  };

  const updateFacilityTemplateColumn = (
    rowIndex: number,
    key: keyof FacilityTemplateColumn,
    value: string,
  ) => {
    setFacilityTemplateColumnDraft((prev) =>
      prev.map((column, idx) => {
        if (idx !== rowIndex) return column;
        const next = { ...column, [key]: value };
        if (key === "role" && value !== "quantity") {
          next.diet_type = "";
          next.area_id = "";
        }
        if (key === "role") {
          if (value === "quantity") {
            next.diet_type = normalizeDietTypeToken(next.diet_type || "") || "unknown";
            next.area_id = normalizeFacilityAreaToken(next.area_id || "") || "X";
          }
          next.header = defaultHeaderForFacilityTemplateColumn(next);
          next.name = defaultNameForFacilityTemplateColumn(next);
          return next;
        }
        if (next.role === "quantity" && (key === "diet_type" || key === "area_id")) {
          next.diet_type = normalizeDietTypeToken(next.diet_type || "") || "unknown";
          if (key === "area_id" && !String(value || "").trim()) {
            next.area_id = "";
            return next;
          }
          next.area_id = normalizeFacilityAreaToken(next.area_id || "") || "X";
          next.header = defaultHeaderForFacilityTemplateColumn(next);
          next.name = defaultNameForFacilityTemplateColumn(next);
          return next;
        }
        if (next.role === "quantity" && (key === "header" || key === "name")) {
          const inferred = inferQuantityColumnMeta(key === "name" ? value : next.name, key === "header" ? value : next.header);
          if (inferred) {
            next.diet_type = inferred.diet_type;
            next.area_id = inferred.area_id || "X";
          }
        }
        return next;
      }),
    );
  };

  const applyFacilityTemplateColumnSwap = (leftIndex: number, rightIndex: number) => {
    setShowFacilityTemplateEditor(true);
    setFacilityTemplateColumnDraft((prev) => swapFacilityTemplateColumns(prev, leftIndex, rightIndex));
    setFacilityTemplateSwapLeft("");
    setFacilityTemplateSwapRight("");
    setFacilityTemplateMessage("施設区分列の並びを入れ替えました。必要なら保存してください。");
  };

  const applySelectedFacilityTemplateColumnSwap = () => {
    const leftIndex = Number(facilityTemplateSwapLeft);
    const rightIndex = Number(facilityTemplateSwapRight);
    if (!Number.isInteger(leftIndex) || !Number.isInteger(rightIndex)) {
      setFacilityTemplateMessage("入れ替える2つの列を選択してください。");
      return;
    }
    if (leftIndex === rightIndex) {
      setFacilityTemplateMessage("別々の列を選択してください。");
      return;
    }
    applyFacilityTemplateColumnSwap(leftIndex, rightIndex);
  };

  const appendFacilityTemplateColumn = () => {
    setShowFacilityTemplateEditor(true);
    setFacilityTemplateSwapLeft("");
    setFacilityTemplateSwapRight("");
    setFacilityTemplateColumnDraft((prev) =>
      reindexFacilityTemplateColumns([...prev, createEmptyFacilityTemplateColumn(prev.length)]),
    );
    setFacilityTemplateMessage("施設区分列を追加しました。必要なら編集して保存してください。");
  };

  const deleteFacilityTemplateColumn = (rowIndex: number) => {
    setShowFacilityTemplateEditor(true);
    setFacilityTemplateSwapLeft("");
    setFacilityTemplateSwapRight("");
    setFacilityTemplateColumnDraft((prev) => removeFacilityTemplateColumn(prev, rowIndex));
    setFacilityTemplateMessage("施設区分列を削除しました。必要なら保存してください。");
  };

  const saveFacilityTemplateColumns = async () => {
    setShowFacilityTemplateEditor(true);
    if (!order?.id) {
      setFacilityTemplateMessage("注文が見つかりません。");
      return;
    }
    if (!facility.trim()) {
      setFacilityTemplateMessage("施設を選択してください。");
      return;
    }
    if (facility.trim() !== (order.facility || "").trim()) {
      setFacilityTemplateMessage("先に Step1 の施設設定を保存してください。");
      return;
    }
    if (!facilityTemplateColumnDraft.length) {
      setFacilityTemplateMessage("保存できる施設区分列がありません。");
      return;
    }
    setFacilityTemplateSaving(true);
    setFacilityTemplateMessage("施設テンプレートを保存中...");
    try {
      const columns = buildFacilityTemplateColumnsPayload(facilityTemplateColumnDraft);
      const res = await apiClient.put(`/orders/${order.id}/facility-template-columns`, { columns });
      const resolvedConfig = res.data?.resolved_config || null;
      const resolvedColumns = normalizeFacilityTemplateColumns(
        resolvedConfig?.fax_template?.columns ?? columns,
      );
      const nextConfig = {
        ...(facilityConfig || {}),
        fax_template_override: {
          ...((facilityConfig || {}).fax_template_override || {}),
          columns,
        },
      };
      delete nextConfig.fax_template_override.main_ocr_row_fields;
      setFacilityConfig(nextConfig);
      setFacilityTemplateColumns(resolvedColumns);
      setFacilityTemplateColumnDraft(resolvedColumns);
      const refreshedDraftPayload = res.data?.draft_payload || res.data?.draft || null;
      if (refreshedDraftPayload) {
        const normalizedDraftPayload = normalizeDraftSheetPayload(refreshedDraftPayload);
        applyNormalizedSheetEditorPayload(normalizedDraftPayload);
        applySheetReviewMeta(buildSheetReviewMetaFromOrderState(order, refreshedDraftPayload));
      }
      setFacilityTemplateMessage(
        res.data?.draft_refreshed
          ? "施設テンプレートに保存し、現在のシートにも反映しました。"
          : "施設テンプレートに保存しました。シートを再読込して確認してください。",
      );
      if (order?.id && normalizeConcreteWeekValue(order.persisted_week_value || order.week_value || order.week || "")) {
        await refreshOrderWorkspace({ reloadSheet: true, preserveSelections: true });
      }
    } catch (err: any) {
      const status = err?.response?.status;
      setFacilityTemplateMessage(
        status === 403 ? "権限がありません。" : "施設テンプレートの保存に失敗しました。"
      );
    } finally {
      setFacilityTemplateSaving(false);
    }
  };

  const forceWeeklyMenuOverwrite = async (
    options: { blankQuantities?: boolean; successMessage?: string; pendingMessage?: string } = {},
  ): Promise<boolean> => {
    if (!order?.id) {
      setActionMessage("注文が見つかりません。");
      return false;
    }
    const { blankQuantities = false, successMessage, pendingMessage } = options;
    setForcedSheetRecoveryPending("weekly");
    setActionMessage(
      pendingMessage
        || (
          blankQuantities
            ? "週次メニューを基準に骨格を復元し、数量を空白へ戻しています..."
            : "週次メニューを基準に日付・区分・メニューを復元しています..."
        ),
    );
    try {
      const res = await apiClient.post(`/orders/${order.id}/draft-sheet/force-weekly-menu`, {
        blank_quantities: blankQuantities,
      });
      const refreshedDraftPayload = res.data?.draft_payload || null;
      if (refreshedDraftPayload) {
        const normalizedDraftPayload = normalizeDraftSheetPayload(refreshedDraftPayload);
        applyNormalizedSheetEditorPayload(normalizedDraftPayload);
        applySheetReviewMeta(buildSheetReviewMetaFromOrderState(order, refreshedDraftPayload));
      }
      setActionMessage(
        successMessage
          || (
            blankQuantities
              ? "週次メニューを基準に骨格を上書きし、数量は空白へ戻しました。"
              : "週次メニューを基準に日付・区分・メニューを上書きしました。数量は必要なら確認してください。"
          ),
      );
      await refreshOrderWorkspace({ reloadSheet: true, reloadHistory: true, preserveSelections: true });
      return true;
    } catch (err: any) {
      const detail = String(err?.response?.data?.detail || "").trim();
      if (detail === "weekly_menu_missing") {
        setActionMessage("週次メニューが見つからないため、強制上書きできません。");
      } else if (detail === "facility missing") {
        setActionMessage("先に Step1 の施設設定を保存してください。");
      } else if (detail === "week missing") {
        setActionMessage("先に Step1 の週設定を保存してください。");
      } else {
        setActionMessage("週次メニューの強制上書きに失敗しました。");
      }
      return false;
    } finally {
      setForcedSheetRecoveryPending("");
    }
  };

  const forceFacilitySchemaOverwrite = async () => {
    if (!order?.id) {
      setActionMessage("注文が見つかりません。");
      return;
    }
    setForcedSheetRecoveryPending("facility");
    setActionMessage("施設設定の列構成でシートを復元し、数量を空白に戻しています...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/draft-sheet/force-facility-schema`, {
        blank_quantities: true,
      });
      const refreshedDraftPayload = res.data?.draft_payload || null;
      if (refreshedDraftPayload) {
        const normalizedDraftPayload = normalizeDraftSheetPayload(refreshedDraftPayload);
        applyNormalizedSheetEditorPayload(normalizedDraftPayload);
        applySheetReviewMeta(buildSheetReviewMetaFromOrderState(order, refreshedDraftPayload));
      }
      setActionMessage("施設設定の列構成でシートを上書きし、数量は手入力用に空白へ戻しました。");
      await refreshOrderWorkspace({ reloadSheet: true, reloadHistory: true, preserveSelections: true });
    } catch (err: any) {
      const detail = String(err?.response?.data?.detail || "").trim();
      if (detail === "facility missing") {
        setActionMessage("先に Step1 の施設設定を保存してください。");
      } else {
        setActionMessage("施設設定の列構成による強制復元に失敗しました。");
      }
    } finally {
      setForcedSheetRecoveryPending("");
    }
  };

  const reparse = async (options?: {
    ocrProvider?: string;
    llmAssist?: boolean;
    force?: boolean;
    staleAction?: "retry" | "wait";
  }) => {
    if (!order) return;
    if (ocrHardRecoveryMode) {
      setActionMessage("現在は基盤復旧待ちのため、再解析は保留しました。");
      return;
    }
    const providerOverride = (options?.ocrProvider || "").trim().toLowerCase();
    const explicitLlmAssist = options?.llmAssist;
    const llmAssist =
      explicitLlmAssist !== undefined ? explicitLlmAssist : Boolean(options?.llmAssist && providerOverride);
    const providerLabel =
      providerOverride === "gemini" ? "Gemini" : providerOverride === "openai" ? "OpenAI" : "";
    if (reparseTimerRef.current !== null) {
      window.clearTimeout(reparseTimerRef.current);
      reparseTimerRef.current = null;
    }
    setActionMessage(
      llmAssist
        ? "LLM補完再解析を開始しました。まずOCR土台を確認します。"
        : "再解析を開始しました。"
    );
    setReparsePending(true);
    const beforeCount = order.lines?.length ?? 0;
    const orderId = order.id;
    const errorContext = (id: string) => {
      const timestamp = new Date().toLocaleString("ja-JP");
      return ` [注文ID: ${id} / 時刻: ${timestamp}]`;
    };
    const withErrorContext = (message: string, id: string) => `${message}${errorContext(id)}`;
    let accepted = false;
    try {
      const payload: Record<string, any> = {};
      if (ocrPrompt.trim()) {
        payload.ocr_prompt = ocrPrompt.trim();
      }
      if (llmAssist) {
        payload.prompt_preset = llmReparsePromptPreset;
      }
      if (providerOverride) {
        payload.ocr_provider = providerOverride;
      }
      if (providerOverride === "gemini") {
        const resolvedModel = getResolvedLlmReparseModel();
        if (resolvedModel) {
          payload.llm_model = resolvedModel;
        }
      }
      if (explicitLlmAssist !== undefined) {
        payload.llm_assist = explicitLlmAssist;
      }
      if (typeof options?.force === "boolean") {
        payload.force = options.force;
      }
      if (options?.staleAction) {
        payload.stale_action = options.staleAction;
      }
      const requestPayload = Object.keys(payload).length ? payload : null;
      const res = await apiClient.post(`/orders/${orderId}/reparse`, requestPayload, { timeout: 900000 });
      if (res.status === 202 || res.data?.accepted) {
        accepted = true;
        setActionMessage(
          llmAssist
            ? "LLM補完再解析を開始しました。まずOCR土台を確認します。"
            : "再解析を開始しました。完了まで数分かかります。"
        );
        replaceAuthoritativeOrder({
          ...order,
          ocr_status: "running",
          ocr_error: null,
          ocr_processing_stage: "queued",
          ocr_updated_at: new Date().toISOString(),
        });
        const pollReparse = async () => {
          try {
            const statusRes = await apiClient.get(`/orders/${orderId}`);
            const updated = statusRes.data as OrderDetail;
            replaceAuthoritativeOrder(updated);
            const status = updated.ocr_status || "";
            const stageLabel = describeProcessingStage(updated.ocr_processing_stage);
            if (status && status !== "running" && status !== "pending") {
              setReparsePending(false);
              const afterCount = updated.lines?.length ?? 0;
              const metricBefore = updated.ocr_metrics?.before_count;
              const metricAfter = updated.ocr_metrics?.after_count;
              const metricChanged = updated.ocr_metrics?.changed;
              const summaryBefore = typeof metricBefore === "number" ? metricBefore : beforeCount;
              const summaryAfter = typeof metricAfter === "number" ? metricAfter : afterCount;
              const changed =
                typeof metricChanged === "boolean" ? metricChanged : summaryBefore !== summaryAfter;
              const changedText = changed ? "変更あり" : "変更なし";
              const error = updated.ocr_error || "";
              const errorDetail = error ? ` (${error})` : "";
              if (status === "failed" || status === "empty") {
                if (
                  error === "first_pass_ocr_missing" ||
                  String(updated.ocr_processing_stage || "").trim().toLowerCase() === "first_pass_missing"
                ) {
                  setActionMessage(
                    withErrorContext(
                      "OCRの土台が見つからないため、LLM補完再解析を開始できませんでした。先に「OCRパイプラインを再実行」または「OCR基盤を復旧」を実行してください。",
                      orderId
                    )
                  );
                } else if (error === "lines_empty") {
                  setActionMessage(
                    withErrorContext(
                      `解析結果が空でした。OCR設定を見直してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "sheet_canonical_mismatch") {
                  setActionMessage(
                    withErrorContext(
                      `週メニュー整合チェックで不一致を検知しました。再解析デバッグを確認してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "sheet_suspicious_blank_row") {
                  setActionMessage(
                    withErrorContext(
                      `数量行の欠落を検知したため保存を中止しました。再解析デバッグを確認してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "sheet_row_coverage_low") {
                  setActionMessage(
                    withErrorContext(
                      `OCR行カバレッジ不足を検知したため保存を中止しました。再解析デバッグを確認してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "sheet_column_anomaly") {
                  setActionMessage(
                    withErrorContext(
                      `施設区分列の異常を検知したため保存を中止しました。再解析デバッグを確認してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "sheet_date_anchor_drift") {
                  setActionMessage(
                    withErrorContext(
                      `既存シートの日付範囲から大きく外れたため保存を中止しました。再解析デバッグを確認してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "document_ai_missing") {
                  setActionMessage(
                    withErrorContext(`OCRの設定が必要です。${errorDetail}`, orderId)
                  );
                } else if (error === "document_ai_failed") {
                  setActionMessage(
                    withErrorContext(`OCRの処理に失敗しました。${errorDetail}`, orderId)
                  );
                } else if (error.startsWith("main_ocr_failed")) {
                  const provider = error.includes(":") ? error.split(":")[1] : "";
                  const providerLabel = provider || "OCR";
                  setActionMessage(
                    withErrorContext(`${providerLabel}の処理に失敗しました。${errorDetail}`, orderId)
                  );
                } else {
                  setActionMessage(withErrorContext(`再解析に失敗しました。${errorDetail}`, orderId));
                }
              } else {
                const provider =
                  typeof updated.ocr_metrics?.provider === "string"
                    ? updated.ocr_metrics.provider
                    : "";
                const providerText = provider ? ` / ${provider}` : "";
                const resultState = String(updated.ocr_result_state || "").trim().toLowerCase();
                const warningReasonsRaw = Array.isArray(updated.ocr_metrics?.warning_reasons)
                  ? updated.ocr_metrics?.warning_reasons
                  : [];
                const warningReasons = warningReasonsRaw
                  .map((item) => String(item || "").trim())
                  .filter(Boolean);
                if (resultState === "draft_ready_blocked") {
                  setActionMessage(
                    `再解析候補を下書きとして保存しました。自動反映は保留し、現在の確定明細は保持しています。Step2 で確認してから明細へ反映してください。${providerText}`
                  );
                } else if (warningReasons.length) {
                  const warningText = warningReasons
                    .map((code) => describeReparseWarningReason(code))
                    .filter(Boolean)
                    .join(" / ");
                  setActionMessage(
                    `再解析しました（警告あり: ${warningText || warningReasons.join(" / ")}）。シートには反映済みです。${summaryBefore}→${summaryAfter} (${changedText})${providerText}`
                  );
                } else {
                  setActionMessage(
                    `再解析しました。${summaryBefore}→${summaryAfter} (${changedText})${providerText}`
                  );
                }
                await rebuildBags();
              }
              reparseTimerRef.current = null;
              await refreshOrderWorkspace({ preserveSelections: true, reloadHistory: true, reloadSheet: true });
              await refreshOcrOutput(orderId);
              return;
            }
            const stageMessage = describeReparseProgressMessage(updated.ocr_processing_stage, {
              llmAssist,
              providerLabel,
            });
            if (stageMessage) {
              setActionMessage(stageMessage);
            } else if (stageLabel) {
              setActionMessage(`再解析中... ${stageLabel}`);
            }
            reparseTimerRef.current = window.setTimeout(pollReparse, 5000);
          } catch {
            setReparsePending(false);
            reparseTimerRef.current = null;
            setActionMessage(withErrorContext("再解析の状態取得に失敗しました。", orderId));
          }
        };
        reparseTimerRef.current = window.setTimeout(pollReparse, 2000);
        return;
      }
      if (res.data?.order) {
        replaceAuthoritativeOrder(res.data.order as OrderDetail);
      }
      const reparse = res.data?.order?.reparse;
      if (reparse) {
        const changedText = reparse.changed ? "変更あり" : "変更なし";
        const provider = typeof reparse.provider === "string" ? reparse.provider : "";
        const providerText = provider ? ` / ${provider}` : "";
        setActionMessage(
          `再解析しました。${reparse.before_count}→${reparse.after_count} (${changedText})${providerText}`
        );
      } else {
        setActionMessage("再解析しました。");
      }
      await rebuildBags();
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error;
      const detailProvider = typeof detail === "object" ? detail?.provider : "";
      if (status === 400) {
        setActionMessage(withErrorContext("施設IDを設定してから再解析してください。", orderId));
      } else if (status === 404) {
        setActionMessage(withErrorContext("注文または施設が見つかりません。", orderId));
      } else if (status === 409) {
        if (detailError === "reparse_in_progress") {
          setActionMessage(withErrorContext("別の画面で再解析が実行中です。完了を待つか、最新状態を再読込してください。", orderId));
        } else if (detailError === "document_ai_missing") {
          setActionMessage(withErrorContext("OCRの設定が必要です。", orderId));
        } else if (detailError === "document_ai_failed") {
          setActionMessage(withErrorContext("OCRの処理に失敗しました。", orderId));
        } else if (detailError === "main_ocr_failed" || detailError?.startsWith("main_ocr_failed")) {
          const inlineProvider =
            typeof detailError === "string" && detailError.includes(":")
              ? detailError.split(":")[1]
              : "";
          const provider = detailProvider || inlineProvider;
          const providerLabel = provider || "OCR";
          setActionMessage(withErrorContext(`${providerLabel}の処理に失敗しました。`, orderId));
        } else if (detailError === "first_pass_ocr_missing") {
          setActionMessage(
            withErrorContext(
              "OCRの土台が見つからないため、LLM補完再解析を開始できませんでした。先に「OCRパイプラインを再実行」または「OCR基盤を復旧」を実行してください。",
              orderId
            )
          );
        } else if (detailError === "lines_empty") {
          setActionMessage(
            withErrorContext("解析結果が空でした。OCR設定を見直してください。", orderId)
          );
        } else {
          setActionMessage(
            withErrorContext("解析結果が空でした。OCR設定を見直してください。", orderId)
          );
        }
      } else {
        const suffix = status ? ` (status ${status})` : " (network error)";
        const detailText = detail ? ` ${detail}` : "";
        setActionMessage(
          withErrorContext(`再解析に失敗しました。${suffix}${detailText}`, orderId)
        );
      }
    } finally {
      if (!accepted) {
        setReparsePending(false);
      }
    }
  };

  const rerunOcrPipeline = async () => {
    if (!order) return;
    const workflowPrimaryActionCode = String(order?.workflow_state?.primary_action || "").trim().toLowerCase();
    if (ocrHardRecoveryMode && workflowPrimaryActionCode === "recover_ocr_evidence") {
      setActionMessage("現在は基盤復旧待ちのため、OCRパイプライン再実行は保留しました。");
      return;
    }
    const orderId = order.id;
    if (reparseTimerRef.current !== null) {
      window.clearTimeout(reparseTimerRef.current);
      reparseTimerRef.current = null;
    }
    setReparsePending(true);
    setActionMessage("OCRパイプラインを再実行しています。新しいOCR候補を作成します。");
    const pollRerun = async () => {
      try {
        const statusRes = await apiClient.get(`/orders/${orderId}`);
        const updated = statusRes.data as OrderDetail;
        replaceAuthoritativeOrder(updated);
        const nextCandidatePromptVisible = Boolean(updated?.workflow_state?.candidate_prompt_visible);
        const nextState = String(updated?.workflow_state?.state || "").trim().toLowerCase();
        const nextReparseStatus = String(updated?.workflow_state?.reparse_state?.status || "").trim().toLowerCase();
        if (nextCandidatePromptVisible) {
          setReparsePending(false);
          reparseTimerRef.current = null;
          setActionMessage("新しいOCR候補ができました。候補ブロックから切り替えるか選んでください。");
          await refreshOrderWorkspace({
            preserveSelections: true,
            reloadSheet: true,
            reloadHistory: true,
            reloadOcrPages: true,
          });
          return;
        }
        if (nextState === "rerun_failed_keep_current" || nextReparseStatus === "hard_failed" || nextReparseStatus === "failed") {
          setReparsePending(false);
          reparseTimerRef.current = null;
          setActionMessage("OCRパイプライン再実行に失敗しました。現在のシートは保持されています。");
          await refreshOrderWorkspace({
            preserveSelections: true,
            reloadSheet: true,
            reloadHistory: true,
            reloadOcrPages: true,
          });
          return;
        }
        if (nextState === "rerun_in_progress" || ["running", "pending", "queued"].includes(nextReparseStatus)) {
          reparseTimerRef.current = window.setTimeout(pollRerun, 5000);
          return;
        }
        setReparsePending(false);
        reparseTimerRef.current = null;
        await refreshOrderWorkspace({
          preserveSelections: true,
          reloadSheet: true,
          reloadHistory: true,
          reloadOcrPages: true,
        });
        setActionMessage("OCRパイプライン再実行が完了しました。最新のOCR結果を表示しました。");
      } catch {
        setReparsePending(false);
        reparseTimerRef.current = null;
        setActionMessage("OCRパイプライン再実行の状態取得に失敗しました。最新状態を再読込してください。");
      }
    };
    try {
      const res = await apiClient.post(`/orders/${orderId}/ocr-rerun`, { stale_action: "retry" }, { timeout: 900000 });
      if (res.status === 202 || res.data?.accepted) {
        replaceAuthoritativeOrder({
          ...order,
          ocr_status: "running",
          ocr_error: null,
          ocr_processing_stage: "queued",
          ocr_updated_at: new Date().toISOString(),
          workflow_state: {
            ...(order.workflow_state || {}),
            state: "rerun_in_progress",
          },
        });
        reparseTimerRef.current = window.setTimeout(pollRerun, 2000);
        return;
      }
      setReparsePending(false);
      setActionMessage("OCRパイプライン再実行を開始できませんでした。");
    } catch (err: any) {
      setReparsePending(false);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error || "";
      if (status === 409 && detailError === "reparse_in_progress") {
        setActionMessage("別の画面でOCRパイプライン再実行中です。完了を待つか、最新状態を再読込してください。");
      } else if (status === 404) {
        setActionMessage("OCRパイプライン再実行の対象が見つかりません。");
      } else {
        setActionMessage("OCRパイプライン再実行に失敗しました。");
      }
    }
  };

  const recoverOcrFoundation = async () => {
    if (!order) return;
    if (ocrRecoverPending) return;
    const orderId = order.id;
    const errorContext = (id: string) => {
      const timestamp = new Date().toLocaleString("ja-JP");
      return ` [注文ID: ${id} / 時刻: ${timestamp}]`;
    };
    setOcrRecoverPending(true);
    setActionMessage("復旧を試します。OCRの基盤を再構築します。");
    try {
      const res = await apiClient.post(`/orders/${orderId}/ocr-recover`, null, {
        timeout: 900000,
      });
      if (res.status === 202 || res.data?.accepted) {
        setActionMessage("復旧を試しています。OCRの基盤再作成が完了するまで数分かかる場合があります。");
      } else {
        setActionMessage("OCR基盤の復旧を開始しました。完了まで数分かかる場合があります。");
      }
      await Promise.all([
        refreshOrderWorkspace({ preserveSelections: true, reloadSheet: true, reloadHistory: true }),
        loadOcrPages({ force: true }),
        loadOcrSheet({ silent: true }),
      ]);
      setActionMessage(`復旧を再要求しました。OCR結果が揃うまでしばらくお待ちください。${errorContext(orderId)}`);
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error || "";
      if (status === 409) {
        if (detailError === "ocr_recover_in_progress") {
          setActionMessage(`現在復旧処理が進行中です。完了を待ってから再試行してください。${errorContext(orderId)}`);
        } else {
          setActionMessage(`復旧に失敗しました。${detailError || "再試行してください"}${errorContext(orderId)}`);
        }
      } else if (status === 404) {
        setActionMessage(`復旧対象の注文またはリソースが見つかりません。${errorContext(orderId)}`);
      } else {
        setActionMessage(`復旧に失敗しました。${errorContext(orderId)}`);
      }
    } finally {
      setOcrRecoverPending(false);
    }
  };

  const updateLineQuantity = (idx: number, qty: number) => {
    if (!order) return;
    const next = [...order.lines];
    next[idx] = { ...next[idx], quantity_corrected: qty };
    replaceAuthoritativeOrder({ ...order, lines: next });
    setLineEditsDirty(true);
  };

  const extractFilename = (value?: string | null) => {
    if (!value) return "";
    const match = value.match(/filename\\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
    const rawName = match?.[1] || match?.[2] || "";
    if (!rawName) return "";
    try {
      return decodeURIComponent(rawName);
    } catch {
      return rawName;
    }
  };

  const openOutput = async (path: string, label: string) => {
    const timestamp = new Date().toLocaleString("ja-JP");
    setActionMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    setDownloadMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    let popup: Window | null = null;
    try {
      popup = window.open("", "_blank");
      if (popup) {
        popup.document.title = `${label} ダウンロード`;
        popup.document.body.innerHTML = "<p>ダウンロードを準備中...</p>";
      }
    } catch {
      popup = null;
    }
    try {
      const res = await apiClient.get(path, { responseType: "blob" });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "output";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      if (popup) {
        popup.location.href = url;
      } else {
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setDownloadMessage(`${label}をダウンロードしました。 (${timestamp})`);
    } catch (err: any) {
      const status = err?.response?.status;
      const suffix = status ? ` (${status})` : "";
      setActionMessage(`${label}のダウンロードに失敗しました。${suffix}`);
      setDownloadMessage(`${label}のダウンロードに失敗しました。${suffix}`);
      if (popup) {
        popup.close();
      }
    }
  };

  const outputPreviewLabels: Record<OutputPreview["type"], string> = {
    labels: "ラベルCSV",
    delivery: "納品書Excel",
    aggregate: "総量CSV",
  };

  const loadOutputPreview = async (type: OutputPreview["type"]) => {
    if (!order) return;
    setOutputPreviewLoading(true);
    setOutputPreviewMessage("プレビューを取得中...");
    try {
      const res = await apiClient.get("/outputs/preview", {
        params: { order_id: order.id, type, limit: 10 },
      });
      const headers = Array.isArray(res.data?.headers) ? res.data.headers : [];
      const rows = Array.isArray(res.data?.rows) ? res.data.rows : [];
      const cappedRows = rows.slice(0, 10);
      setOutputPreview({ type, headers, rows: cappedRows });
      if (!cappedRows.length) {
        setOutputPreviewMessage("プレビューが空です。");
      } else if (rows.length > cappedRows.length) {
        setOutputPreviewMessage("先頭10件のみ表示しています。");
      } else {
        setOutputPreviewMessage("");
      }
    } catch {
      setOutputPreview(null);
      setOutputPreviewMessage("プレビューの取得に失敗しました。");
    } finally {
      setOutputPreviewLoading(false);
    }
  };

  const persistedLines = order?.lines || [];
  const bagSummaryGroups = groupBagSummaryRowsByDate(buildBagSummaryRows(bagRows));
  const bagAmountStats = buildBagAmountStats(persistedLines);
  const bagTypeLabelMap = buildBagTypeLabelMap(facilityConfig);
  const activeOcrPage = ocrPages[activeOcrPageIndex];
  const activeOcrPageLabel = activeOcrPage
    ? activeOcrPage.page_index ?? activeOcrPageIndex + 1
    : null;
  const facilityCandidates = ocrOutput?.facility_candidates || [];
  const reparseDebug = ocrOutput?._reparse_debug || null;
  const reparseProviderDebugText = (() => {
    if (!reparseDebug || !reparseDebug.provider_debug) return "";
    try {
      return JSON.stringify(reparseDebug.provider_debug, null, 2);
    } catch {
      return "";
    }
  })();
  const reparseNormalizedLinesText = (() => {
    if (!reparseDebug || !Array.isArray(reparseDebug.normalized_lines) || !reparseDebug.normalized_lines.length) {
      return "";
    }
    try {
      return JSON.stringify(reparseDebug.normalized_lines, null, 2);
    } catch {
      return "";
    }
  })();
  const reparseValidationDetailText = (() => {
    if (!reparseDebug || !reparseDebug.validation_detail) return "";
    try {
      return JSON.stringify(reparseDebug.validation_detail, null, 2);
    } catch {
      return "";
    }
  })();
  const reparseWarningReasons = (() => {
    const fromDebug = Array.isArray(reparseDebug?.warning_reasons) ? reparseDebug.warning_reasons : [];
    const fromMetrics = Array.isArray(order?.ocr_metrics?.warning_reasons)
      ? order?.ocr_metrics?.warning_reasons
      : [];
    const merged = [...fromDebug, ...fromMetrics]
      .map((item) => String(item || "").trim())
      .filter(Boolean);
    return Array.from(new Set(merged));
  })();
  const reparseWarningDetailText = (() => {
    const detail =
      (reparseDebug?.warning_detail && typeof reparseDebug.warning_detail === "object"
        ? reparseDebug.warning_detail
        : null) ||
      (order?.ocr_metrics?.warning_detail && typeof order.ocr_metrics.warning_detail === "object"
        ? order.ocr_metrics.warning_detail
        : null);
    if (!detail) return "";
    try {
      return JSON.stringify(detail, null, 2);
    } catch {
      return "";
    }
  })();
  const reparseQuantityMergeText = (() => {
    if (!reparseDebug || !reparseDebug.llm_quantity_only_merge) return "";
    try {
      return JSON.stringify(reparseDebug.llm_quantity_only_merge, null, 2);
    } catch {
      return "";
    }
  })();
  const ocrPagesPending = ocrPagesMessage.includes("処理中");
  const rawOcrStatus = (order?.ocr_status || "").toLowerCase();
  const workflowPrimaryActionCode = String(order?.workflow_state?.primary_action || "").trim().toLowerCase();
  const hasEvidenceUnavailableBlocker = [
    ...(Array.isArray(order?.workflow_state?.blockers_json)
      ? order.workflow_state.blockers_json.map((item) => String(item || "").trim())
      : []),
    ...(Array.isArray(order?.workflow_state?.apply_gate?.blockers)
      ? order.workflow_state.apply_gate.blockers.map((item) => String(item || "").trim())
      : []),
  ].some((code) => code === "evidence_view_unavailable" || code === "evidence_edit_unavailable");
  const explicitTerminalReparseOutcome = resolveExplicitReparseOutcome({
    ocrStatus: order?.ocr_status,
    ocrError: order?.ocr_error,
    ocrProcessingStage: order?.ocr_processing_stage,
    ocrResultState: order?.ocr_result_state,
    ocrReparseHealth: order?.ocr_reparse_health,
    ocrMetrics: order?.ocr_metrics,
    workflowReparseState: order?.workflow_state?.reparse_state,
    reparseDebug,
  });
  let ocrStatusLabel = "未実行";
  let ocrStatusDetail = "";
  if (
    rawOcrStatus === "running" ||
    rawOcrStatus === "pending" ||
    rawOcrStatus === "awaiting_output" ||
    rawOcrStatus === "recovering" ||
    (!rawOcrStatus && ocrPagesPending)
  ) {
    ocrStatusLabel = "実行中";
    ocrStatusDetail = "OCRを実行中です。完了まで数分かかります。";
  } else if (hasEvidenceUnavailableBlocker || rawOcrStatus === "blocked") {
    if (workflowPrimaryActionCode === "recover_ocr_evidence") {
      ocrStatusLabel = "OCR基盤待ち";
      ocrStatusDetail = "OCR結果を表示できません。ページは開いています。OCR基盤を復旧してください。";
    } else {
      ocrStatusLabel = "OCR証拠待ち";
      ocrStatusDetail = "OCR結果がありません。ページは開いています。OCRパイプラインを再実行してください。";
    }
  } else if (explicitTerminalReparseOutcome?.kind === "failed") {
    ocrStatusLabel = "再解析失敗";
    ocrStatusDetail = explicitTerminalReparseOutcome.detail;
  } else if (explicitTerminalReparseOutcome?.kind === "rejected") {
    ocrStatusLabel = "再解析却下";
    ocrStatusDetail = explicitTerminalReparseOutcome.detail;
  } else if (rawOcrStatus === "stalled") {
    ocrStatusLabel = "停止";
    ocrStatusDetail = order?.ocr_error
      ? `理由: ${order.ocr_error}`
      : "OCRが停止しました。再解析してください。";
  } else if (rawOcrStatus === "failed" || rawOcrStatus === "error") {
    ocrStatusLabel = "失敗";
    ocrStatusDetail = order?.ocr_error ? `理由: ${order.ocr_error}` : "OCRの処理に失敗しました。";
  } else if (rawOcrStatus === "empty") {
    ocrStatusLabel = "空";
    ocrStatusDetail = "解析結果が空です。";
  } else if (rawOcrStatus === "skipped") {
    ocrStatusLabel = "スキップ";
    ocrStatusDetail = "バックログのためOCRをスキップしました。";
  } else if (rawOcrStatus) {
    if (!ocrPagesLoading && !ocrPages.length) {
      ocrStatusLabel = "完了(表示なし)";
      ocrStatusDetail = ocrPagesMessage || "OCR結果は完了していますが表示データがありません。";
    } else {
      ocrStatusLabel = "完了";
    }
  } else if (ocrPagesMessage) {
    ocrStatusDetail = ocrPagesMessage;
  }
  const activeColumnEdges = showTableBoxEditor ? gridColumnEdgesDraft : gridColumnEdges;
  const activeRowEdges = showTableBoxEditor ? gridRowEdgesDraft : gridRowEdges;
  const overlayHeaderRows = ocrTableHeader.length ? 1 : 0;
  const gridRowCount =
    activeRowEdges && activeRowEdges.length >= 2 ? activeRowEdges.length - 1 : null;
  const overlayRowCount = gridRowCount ?? overlayHeaderRows + ocrTableRows.length;
  const fallbackColumnCount = getColumnCount(ocrTableHeader, ocrTableRows);
  const overlayColumnCount =
    activeColumnEdges && activeColumnEdges.length >= 2
      ? activeColumnEdges.length - 1
      : fallbackColumnCount;
  const ocrOverlayUrl = activeOcrPage?.ocr_overlay_url ?? null;
  const layoutOverlayUrl = activeOcrPage?.layout_overlay_url ?? null;
  const ocrOverlayGs = isGsUri(ocrOverlayUrl);
  const layoutOverlayGs = isGsUri(layoutOverlayUrl);
  const showOcrOverlay = Boolean(ocrOverlayUrl && !ocrOverlayGs && !ocrOverlayError);
  const usingSyntheticOverlay = Boolean(activeOcrPage?.synthetic);
  const canToggleLayoutOverlay = Boolean(layoutOverlayUrl);
  const showLayoutOverlayImage = Boolean(
    showLayoutOverlay && layoutOverlayUrl && !layoutOverlayGs && !layoutOverlayError,
  );
  const tableBoxReady = Boolean(tableBoxDraft && tableBoxDraft.length >= 4);
  const tableBoxNudge = Math.abs(tableBoxStep) || (tableBoxNormalized ? 0.005 : 5);
  let ocrOverlayPlaceholder = "OCRオーバーレイなし";
  if (ocrOverlayUrl && ocrOverlayGs) {
    ocrOverlayPlaceholder = "OCRオーバーレイの署名URL取得に失敗しました。";
  } else if (ocrOverlayError) {
    ocrOverlayPlaceholder = "OCRオーバーレイの読み込みに失敗しました。";
  }
  let layoutOverlayPlaceholder = "レイアウトオーバーレイなし";
  if (layoutOverlayUrl && layoutOverlayGs) {
    layoutOverlayPlaceholder = "レイアウトオーバーレイの署名URL取得に失敗しました。";
  } else if (layoutOverlayError) {
    layoutOverlayPlaceholder = "レイアウトオーバーレイの読み込みに失敗しました。";
  }
  const hasUsableOverlayPreview = showOcrOverlay || usingSyntheticOverlay || Boolean(hakodateOverlayUrl);
  const originalPreviewImageUrl = (() => {
    const figureUrl =
      Array.isArray(activeOcrPage?.figure_urls) && activeOcrPage?.figure_urls.length
        ? String(activeOcrPage?.figure_urls[0] || "").trim()
        : "";
    if (figureUrl) return figureUrl;
    if (activeOcrPage?.synthetic_source === "pdf_render" && ocrOverlayUrl) {
      return ocrOverlayUrl;
    }
    return "";
  })();
  const canHighlightOriginalPreview = Boolean(originalPreviewImageUrl);
  const isHakodateOverlayMode = quantityAssignmentStrategy === "hakodate";
  const hakodateMetrics = hakodateAssignment?.metrics || hakodateProjectionMetrics || null;
  const hakodateTargetCellsRaw = Array.isArray(hakodateAssignment?.target_cells)
    ? hakodateAssignment.target_cells
    : [];
  const hakodateEvidenceRecords = Array.isArray(hakodateAssignment?.evidence_records)
    ? hakodateAssignment.evidence_records
    : [];
  const hakodateAssignmentItems = Array.isArray(hakodateAssignment?.assignments)
    ? hakodateAssignment.assignments
    : [];
  const hakodateSheetOutputCells =
    hakodateAssignment?.sheet_output?.cells && isObjectRecord(hakodateAssignment.sheet_output.cells)
      ? hakodateAssignment.sheet_output.cells
      : {};
  const hakodateAssignmentByTargetId = new Map<string, HakodateAssignmentItem>();
  const hakodateAssignmentBySheetCell = new Map<string, HakodateAssignmentItem>();
  const hakodateQuantityByTargetId = new Map<string, { text: string; state: string }>();
  const hakodateQuantityBySheetCell = new Map<string, { text: string; state: string }>();
  const registerHakodateQuantity = (
    targetId: string,
    sheetCell: string,
    text: string,
    state: string,
  ) => {
    const record = { text, state };
    if (targetId) {
      const previous = hakodateQuantityByTargetId.get(targetId);
      if (!previous || (!previous.text && text)) {
        hakodateQuantityByTargetId.set(targetId, record);
      }
    }
    if (sheetCell) {
      const previous = hakodateQuantityBySheetCell.get(sheetCell);
      if (!previous || (!previous.text && text)) {
        hakodateQuantityBySheetCell.set(sheetCell, record);
      }
    }
  };
  hakodateAssignmentItems.forEach((assignment) => {
    const targetId = firstNonEmptyText(assignment.target_cell_id);
    const sheetCell = firstNonEmptyText(assignment.sheet_cell);
    const text = firstNonEmptyText(assignment.assigned_value, assignment.value_normalized, assignment.value_text);
    const state = firstNonEmptyText(assignment.assignment_state);
    if (targetId) hakodateAssignmentByTargetId.set(targetId, assignment);
    if (sheetCell) hakodateAssignmentBySheetCell.set(sheetCell, assignment);
    registerHakodateQuantity(targetId, sheetCell, text, state);
  });
  Object.entries(hakodateSheetOutputCells).forEach(([sheetCellKey, cell]) => {
    const targetId = firstNonEmptyText(cell.target_cell_id);
    const sheetCell = firstNonEmptyText(cell.sheet_cell, sheetCellKey);
    const text = firstNonEmptyText(cell.value_normalized, cell.value_text);
    const state = firstNonEmptyText(cell.assignment_state);
    registerHakodateQuantity(targetId, sheetCell, text, state);
  });
  const hakodateTargetCellMatchesActivePage = (cell: HakodateTargetCell) => {
    const metadata = isObjectRecord(cell.metadata) ? cell.metadata : null;
    const pageValue = firstNonEmptyText(
      cell.page_index,
      cell.page,
      cell.page_number,
      metadata?.page_index,
      metadata?.page,
      metadata?.page_number,
    );
    if (!pageValue) return true;
    const parsed = Number(pageValue);
    if (!Number.isFinite(parsed)) return true;
    const zeroBasedPageIndex = parsed > 0 ? parsed - 1 : parsed;
    return zeroBasedPageIndex === activeOcrPageIndex;
  };
  const hakodateOverlayCells: HakodateOverlayCell[] = hakodateTargetCellsRaw
    .filter(hakodateTargetCellMatchesActivePage)
    .map((cell) => {
      const bbox = normalizeHakodateNumberArray(cell.bbox, 4);
      if (!bbox || bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) return null;
      const center =
        normalizeHakodateNumberArray(cell.center, 2) || [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2];
      const targetKey = firstNonEmptyText(cell.target_cell_id, cell.region_id, cell.sheet_cell);
      const sheetCell = firstNonEmptyText(cell.sheet_cell, targetKey);
      const quantity =
        (targetKey ? hakodateQuantityByTargetId.get(targetKey) : undefined) ||
        (sheetCell ? hakodateQuantityBySheetCell.get(sheetCell) : undefined) ||
        { text: "", state: "" };
      const assignment =
        (targetKey ? hakodateAssignmentByTargetId.get(targetKey) : undefined) ||
        (sheetCell ? hakodateAssignmentBySheetCell.get(sheetCell) : undefined);
      return {
        ...cell,
        targetKey,
        sheetCell,
        bbox,
        center,
        quantityText: quantity.text,
        assignmentState: quantity.state,
        hasInk: hakodateCellHasInk(assignment?.target_metadata, cell.metadata),
      };
    })
    .filter((cell): cell is HakodateOverlayCell => Boolean(cell));
  const resolveHakodateOverlayBox = (bbox: number[], center: number[] | null): HakodateOverlayBox | null => {
    if (!overlayImageSize.width || !overlayImageSize.height) return null;
    const image = overlayImageRef.current;
    const naturalWidth = image?.naturalWidth || overlayImageSize.width;
    const naturalHeight = image?.naturalHeight || overlayImageSize.height;
    const values = [...bbox, ...(center || [])];
    const normalized = values.every((value) => value >= -0.02 && value <= 1.2);
    const coordinateMaxX = Math.max(
      naturalWidth,
      ...hakodateOverlayCells.map((cell) => Number(cell.bbox?.[2] ?? 0)).filter((value) => Number.isFinite(value)),
    );
    const coordinateMaxY = Math.max(
      naturalHeight,
      ...hakodateOverlayCells.map((cell) => Number(cell.bbox?.[3] ?? 0)).filter((value) => Number.isFinite(value)),
    );
    const absoluteScaleX =
      coordinateMaxX > naturalWidth * 1.05
        ? overlayImageSize.width / coordinateMaxX
        : overlayImageSize.width / (naturalWidth || 1);
    const absoluteScaleY =
      coordinateMaxY > naturalHeight * 1.05
        ? overlayImageSize.height / coordinateMaxY
        : overlayImageSize.height / (naturalHeight || 1);
    const scaleX = normalized ? overlayImageSize.width : absoluteScaleX;
    const scaleY = normalized ? overlayImageSize.height : absoluteScaleY;
    const left = bbox[0] * scaleX;
    const top = bbox[1] * scaleY;
    const right = bbox[2] * scaleX;
    const bottom = bbox[3] * scaleY;
    const width = Math.max(right - left, 1);
    const height = Math.max(bottom - top, 1);
    if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
      return null;
    }
    const centerLeft = center ? center[0] * scaleX : left + width / 2;
    const centerTop = center ? center[1] * scaleY : top + height / 2;
    return { left, top, width, height, centerLeft, centerTop };
  };
  const hakodateRenderedOverlayCells = hakodateOverlayCells
    .map((cell) => ({ cell, box: resolveHakodateOverlayBox(cell.bbox, cell.center) }))
    .filter((item): item is { cell: HakodateOverlayCell; box: HakodateOverlayBox } => Boolean(item.box));
  const hakodateFocusedOverlayHighlights = (() => {
    const focusedRowIndex = focusedSheetCell?.rowIndex ?? focusedSheetRowIndex;
    if (!hakodateRenderedOverlayCells.length || focusedRowIndex == null) {
      return { row: null as HakodateOverlayBox | null, column: null as HakodateOverlayBox | null };
    }
    const focusedField = focusedSheetCell ? String(ocrSheetFields[focusedSheetCell.cellIndex] || "").trim() : "";
    const focusedFieldAliases = new Set(
      [
        focusedField,
        focusedField === "qty.placeholder_x" ? "post_menu.F" : "",
        focusedField === "remarks" ? "note" : "",
      ].filter(Boolean),
    );
    const hakodateTruthRowIndex = (cell: HakodateOverlayCell): number | null => {
      const truth = hakodateCellTruthMetadata(cell);
      const rowIndex = Number(truth?.row_index);
      return Number.isFinite(rowIndex) ? rowIndex : null;
    };
    const hakodateTruthField = (cell: HakodateOverlayCell): string =>
      firstNonEmptyText(hakodateCellTruthMetadata(cell)?.field, cell.semantic_field, cell.field);
    const boxFromItems = (items: Array<{ cell: HakodateOverlayCell; box: HakodateOverlayBox }>) => {
      if (!items.length) return null;
      const left = Math.min(...items.map((item) => item.box.left));
      const top = Math.min(...items.map((item) => item.box.top));
      const right = Math.max(...items.map((item) => item.box.left + item.box.width));
      const bottom = Math.max(...items.map((item) => item.box.top + item.box.height));
      return {
        left,
        top,
        width: Math.max(right - left, 1),
        height: Math.max(bottom - top, 1),
        centerLeft: left + Math.max(right - left, 1) / 2,
        centerTop: top + Math.max(bottom - top, 1) / 2,
      };
    };
    const rowItemsByTruth = hakodateRenderedOverlayCells.filter(
      (item) => hakodateTruthRowIndex(item.cell) === focusedRowIndex,
    );
    const columnItems = focusedFieldAliases.size
      ? hakodateRenderedOverlayCells.filter((item) =>
          focusedFieldAliases.has(hakodateTruthField(item.cell)),
        )
      : [];
    return {
      row: boxFromItems(rowItemsByTruth),
      column: boxFromItems(columnItems),
    };
  })();
  const hakodateTargetCellCount =
    hakodateTargetCellsRaw.length || readNumericMetric(hakodateMetrics, "target_cell_count");
  const hakodateEvidenceCount =
    hakodateEvidenceRecords.length || readNumericMetric(hakodateMetrics, "evidence_count");
  const hakodateAssignedCount =
    readNumericMetric(hakodateMetrics, "assigned_target_count") ||
    readNumericMetric(hakodateMetrics, "assigned_count") ||
    hakodateOverlayCells.filter((cell) => Boolean(cell.quantityText)).length;
  const hakodateQuantityLabelCount = hakodateOverlayCells.filter((cell) => Boolean(cell.quantityText)).length;
  const activeHakodateServerOverlayUrl = firstNonEmptyText(hakodateOverlayUrl);
  const hakodatePreviewUsesServerOverlay =
    isHakodateOverlayMode &&
    Boolean(activeHakodateServerOverlayUrl);
  const hakodatePreviewImageUrl = hakodatePreviewUsesServerOverlay ? activeHakodateServerOverlayUrl : null;
  const effectiveHakodateJobStatus = hakodateJobStatus || order?.workflow_state?.reparse_state || null;
  const hakodateJobStatusCode = String(effectiveHakodateJobStatus?.status || "").trim().toLowerCase();
  const hakodateJobStage = firstNonEmptyText(
    effectiveHakodateJobStatus?.processing_stage,
    order?.ocr_processing_stage,
  );
  const hakodateJobStatusLabel = (() => {
    if (!hakodateJobStatusCode || hakodateJobStatusCode === "idle") return "未実行";
    if (["queued", "pending"].includes(hakodateJobStatusCode)) return "待機中";
    if (hakodateJobStatusCode === "running") return "実行中";
    if (hakodateJobStatusCode === "done") return "完了";
    if (hakodateJobStatusCode === "awaiting_output") return "成果物待ち";
    if (hakodateJobStatusCode === "recovering") return "復旧中";
    if (hakodateJobStatusCode === "stalled") return "停止";
    if (hakodateJobStatusCode === "hard_failed" || hakodateJobStatusCode === "failed") return "失敗";
    return hakodateJobStatusCode;
  })();
  const hakodateJobDetail = dedupeStrings([
    effectiveHakodateJobStatus?.job_id ? `job ${effectiveHakodateJobStatus.job_id}` : "",
    hakodateJobStage ? describeProcessingStage(hakodateJobStage) || hakodateJobStage : "",
    effectiveHakodateJobStatus?.result_state ? `result ${effectiveHakodateJobStatus.result_state}` : "",
    effectiveHakodateJobStatus?.error ? `error ${effectiveHakodateJobStatus.error}` : "",
    effectiveHakodateJobStatus?.progress_updated_at ? `updated ${effectiveHakodateJobStatus.progress_updated_at}` : "",
  ]).join(" / ");
  const hakodateRawBlockers = dedupeStrings([
    ...(isHakodateOverlayMode && hakodateOverlayStatus === "blocked" ? hakodateOverlayBlockers : []),
    ...(Array.isArray(hakodateAssignment?.blockers) ? hakodateAssignment.blockers : []),
    ...(Array.isArray(hakodateAssignment?.sheet_output?.blockers) ? hakodateAssignment.sheet_output.blockers : []),
    ...ocrSheetApplyBlockers.filter((item) => String(item || "").startsWith("hakodate")),
    ...ocrSheetConfirmBlockers.filter((item) => String(item || "").startsWith("hakodate")),
    hakodateTargetCellCount ? "" : "hakodate_target_cell_map_missing",
    hakodateEvidenceCount ? "" : "hakodate_ocr_evidence_missing",
    hakodatePreviewImageUrl || ocrPagesLoading || hakodateOverlayLoading || (isHakodateOverlayMode && hakodateOverlayStatus === "blocked")
      ? ""
      : "hakodate_preview_image_missing",
  ]);
  const hakodateOverlayBlockerMessages = [
    ...hakodateRawBlockers.map(describeHakodateOverlayBlocker),
    ...(hakodateOverlayMessage && !hakodateRawBlockers.length ? [hakodateOverlayMessage] : []),
  ];
  const hakodateOverlayHasBlocker = hakodateRawBlockers.length > 0;
  const step2CriticalBannerMessages = (() => {
    const messages: string[] = [];
    const candidateEvidenceState = resolveCandidateEvidenceState(order);
    const bannerWorkflowStateCode = candidateEvidenceState.workflowStateCode;
    const bannerReparseStateStatus = String(order?.workflow_state?.reparse_state?.status || "").trim().toLowerCase();
    const bannerShowNewEvidenceChoice = candidateEvidenceState.hasUnresolvedCandidateEvidenceChoice;
    const bannerRerunInProgress =
      bannerWorkflowStateCode === "rerun_in_progress"
      || bannerReparseStateStatus === "running"
      || bannerReparseStateStatus === "pending";
    const bannerSemanticShellOnly = bannerWorkflowStateCode === "semantic_shell_only";
    if (bannerShowNewEvidenceChoice) {
      messages.push("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。");
    }
    if (bannerRerunInProgress) {
      messages.push("OCRパイプラインを再実行しています。完了後に新しいOCR候補を確認してください。");
    }
    if (bannerSemanticShellOnly) {
      messages.push("メニュー枠はありますが、数量はまだ信用できません。先にOCRパイプラインを再実行してください。");
    }
    if (rawOcrStatus === "failed" || rawOcrStatus === "error") {
      messages.push(describeOcrFailure(order?.ocr_error));
    } else if (rawOcrStatus === "empty") {
      messages.push(
        hasUsableOverlayPreview
          ? "OCR結果の構造化行は空でした。左のPDFプレビューを見ながら右のシートを修正してください。"
          : "OCR結果が空でした。overlay ではなく原本PDFを見ながら修正してください。",
      );
    } else if (rawOcrStatus === "stalled") {
      messages.push(order?.ocr_error ? `OCRが停止しました: ${order.ocr_error}` : "OCRが停止しました。");
    }
    if (!ocrPagesLoading && !ocrPages.length) {
      messages.push(ocrPagesMessage || "OCRページが取得できていません。");
    }
    if (!hasUsableOverlayPreview && !ocrPagesLoading) {
      if (ocrOverlayUrl && ocrOverlayGs) {
        messages.push("OCRオーバーレイの署名URL取得に失敗しました。");
      } else if (ocrOverlayError) {
        messages.push("OCRオーバーレイの読み込みに失敗しました。");
      } else if (!ocrOverlayUrl) {
        messages.push("OCRオーバーレイがありません。");
      }
    }
    return Array.from(new Set(messages.filter(Boolean)));
  })();
  const shouldFallbackToRawPdfPreview = Boolean(
    pdfUrl &&
      !ocrPagesLoading &&
      !hasUsableOverlayPreview,
  );
  const showingArtifactPdf = pdfSourceKind === "ocr_artifact";
  const artifactPdfLabel =
    pdfSourceVariant === "raw_pdf"
      ? "OCR生成PDF"
      : pdfSourceVariant
        ? `OCR生成PDF (${pdfSourceVariant})`
        : "OCR生成PDF";
  const canShowOriginalPdfPreview = Boolean(pdfUrl);
  const shouldShowOriginalPdfPreview = shouldFallbackToRawPdfPreview || ocrPreviewMode === "original";
  const step2FallbackSummary =
    shouldFallbackToRawPdfPreview && pdfUrl
      ? showingArtifactPdf
        ? "原本FAX PDFが見つからないため、OCR生成PDFを表示しています。右のシートを直接修正してください。"
        : "原本PDFを表示しています。右のシートを直接修正してください。"
      : "";
  const canSwitchPreviewMode = Boolean(
    !isHakodateOverlayMode && canShowOriginalPdfPreview && hasUsableOverlayPreview && !shouldFallbackToRawPdfPreview,
  );
  const overlayPreviewModeLabel = isHakodateOverlayMode
    ? "箱館オーバーレイ（数量込み）"
    : shouldShowOriginalPdfPreview
    ? shouldFallbackToRawPdfPreview
      ? showingArtifactPdf
        ? `${artifactPdfLabel} (fallback)`
        : "原本PDF (fallback)"
      : showingArtifactPdf
        ? artifactPdfLabel
        : "原本PDF"
    : usingSyntheticOverlay
      ? `OCRプレビュー (${activeOcrPage?.pdf_variant_used === "corrected" ? "corrected PDF" : "raw PDF"})`
      : "OCRオーバーレイ";
  const primaryPreviewOpenUrl = isHakodateOverlayMode
    ? hakodatePreviewImageUrl || pdfUrl || null
    : shouldShowOriginalPdfPreview
      ? pdfUrl
      : showOcrOverlay
        ? ocrOverlayUrl
        : null;
  useEffect(() => {
    if (shouldFallbackToRawPdfPreview) {
      ocrPreviewForcedFallbackRef.current = true;
      setOcrPreviewMode("original");
      return;
    }
    if (ocrPreviewForcedFallbackRef.current && hasUsableOverlayPreview) {
      ocrPreviewForcedFallbackRef.current = false;
      setOcrPreviewMode("overlay");
      return;
    }
    if (!canShowOriginalPdfPreview && ocrPreviewMode === "original") {
      ocrPreviewForcedFallbackRef.current = false;
      setOcrPreviewMode("overlay");
    }
  }, [canShowOriginalPdfPreview, hasUsableOverlayPreview, ocrPreviewMode, shouldFallbackToRawPdfPreview]);
  const activeTableBox = showTableBoxEditor && tableBoxDraft ? tableBoxDraft : ocrTableBox;
  const overlayBox = (() => {
    if (!activeTableBox || overlayRowCount < 1 || overlayColumnCount < 1) return null;
    if (!overlayImageSize.width || !overlayImageSize.height) return null;
    const [x0, y0, x1, y1] = activeTableBox.map((value) => Number(value));
    if ([x0, y0, x1, y1].some((value) => Number.isNaN(value))) return null;
    if (x1 <= x0 || y1 <= y0) return null;
    const units = (tableBoxUnitsOverride || ocrTableUnits || "normalized").toLowerCase();
    const normalized = units === "normalized" || units === "ratio";
    const image = overlayImageRef.current;
    const naturalWidth = image?.naturalWidth || overlayImageSize.width;
    const naturalHeight = image?.naturalHeight || overlayImageSize.height;
    const scaleX = normalized ? overlayImageSize.width : overlayImageSize.width / (naturalWidth || 1);
    const scaleY = normalized ? overlayImageSize.height : overlayImageSize.height / (naturalHeight || 1);
    const left = x0 * scaleX;
    const top = y0 * scaleY;
    const width = (x1 - x0) * scaleX;
    const height = (y1 - y0) * scaleY;
    if (width <= 0 || height <= 0) return null;
    return { left, top, width, height };
  })();
  const normalizeEdgesToTable = (
    edges: number[] | null,
    tableBox: number[] | null,
    axis: "x" | "y",
  ) => {
    if (!edges || edges.length < 2 || !tableBox || tableBox.length < 4) return edges;
    const [x0, y0, x1, y1] = tableBox;
    const min = axis === "x" ? x0 : y0;
    const max = axis === "x" ? x1 : y1;
    const span = max - min;
    if (span <= 0) return edges;
    const epsilon = 0.02;
    const withinTable = edges.every((edge) => edge >= min - epsilon && edge <= max + epsilon);
    if (!withinTable) {
      return edges;
    }
    return edges.map((edge) => (edge - min) / span);
  };

  const overlayColumnEdges = normalizeEdgesToTable(activeColumnEdges, activeTableBox, "x");
  const overlayRowEdges = normalizeEdgesToTable(activeRowEdges, activeTableBox, "y");
  const overlayColumnTemplate = (() => {
    if (!overlayBox || !overlayColumnEdges || overlayColumnEdges.length < 2) {
      return `repeat(${overlayColumnCount}, 1fr)`;
    }
    const widths = overlayColumnEdges
      .slice(1)
      .map((edge, idx) => edge - overlayColumnEdges[idx])
      .map((width) => `${Math.max(width, 0) * 100}%`);
    return widths.join(" ");
  })();
  const overlayColumnEdgesPx = overlayBox && overlayColumnEdges && overlayColumnEdges.length >= 2
    ? overlayColumnEdges.map((edge) => overlayBox.left + overlayBox.width * edge)
    : null;
  const overlayRowTemplate = (() => {
    if (!overlayBox || !overlayRowEdges || overlayRowEdges.length !== overlayRowCount + 1) {
      return `repeat(${overlayRowCount}, 1fr)`;
    }
    const heights = overlayRowEdges
      .slice(1)
      .map((edge, idx) => edge - overlayRowEdges[idx])
      .map((height) => `${Math.max(height, 0) * 100}%`);
    return heights.join(" ");
  })();
  const overlayRowEdgesPx =
    overlayBox && overlayRowEdges && overlayRowEdges.length === overlayRowCount + 1
      ? overlayRowEdges.map((edge) => overlayBox.top + overlayBox.height * edge)
      : null;
  const overlayCellHeight = overlayBox ? overlayBox.height / overlayRowCount : 0;
  const activeEditorRowIds = ocrSheetRowIds;
  const ocrPageTableInfos = buildOcrPageTableInfos(ocrPages);
  const ocrSheetIdentityIndices = getSheetIdentityIndices(ocrSheetFields, ocrSheetHeader);
  const ocrSheetRowIdentities = buildRowIdentitySnapshots(ocrSheetRows, ocrSheetIdentityIndices);
  const focusedOverlayTarget = resolveFocusedOverlayTarget(
    focusedSheetRowIndex,
    ocrPageTableInfos,
    ocrSheetRowIdentities,
    ocrSheetRows.length,
  );
  const focusedOverlayHighlight = (() => {
    const showHighlightOnCurrentPreview = shouldShowOriginalPdfPreview
      ? canHighlightOriginalPreview
      : showOcrOverlay;
    if (
      focusedOverlayTarget == null ||
      focusedOverlayTarget.pageArrayIndex !== activeOcrPageIndex ||
      !showHighlightOnCurrentPreview ||
      !overlayBox
    ) {
      return null;
    }
    const overlayGridRowIndex = overlayHeaderRows + focusedOverlayTarget.localRowIndex;
    if (overlayGridRowIndex < overlayHeaderRows || overlayGridRowIndex >= overlayRowCount) {
      return null;
    }
    let top = overlayBox.top + overlayCellHeight * overlayGridRowIndex;
    let height = Math.max(overlayCellHeight, 1);
    if (
      overlayRowEdgesPx &&
      overlayRowEdgesPx.length === overlayRowCount + 1 &&
      overlayRowEdgesPx[overlayGridRowIndex] != null &&
      overlayRowEdgesPx[overlayGridRowIndex + 1] != null
    ) {
      top = overlayRowEdgesPx[overlayGridRowIndex];
      height = Math.max(overlayRowEdgesPx[overlayGridRowIndex + 1] - overlayRowEdgesPx[overlayGridRowIndex], 1);
    }
    return {
      top,
      height,
      left: overlayBox.left,
      width: overlayBox.width,
      pageArrayIndex: focusedOverlayTarget.pageArrayIndex,
      pageIndex: focusedOverlayTarget.pageIndex,
      localRowIndex: focusedOverlayTarget.localRowIndex,
      globalRowIndex: focusedOverlayTarget.globalRowIndex,
      matchReason: focusedOverlayTarget.matchReason,
    };
  })();
  const focusedOverlayMarker = focusedOverlayHighlight
    ? {
      top: focusedOverlayHighlight.top + focusedOverlayHighlight.height / 2,
      left: focusedOverlayHighlight.left + 10,
      pageArrayIndex: focusedOverlayHighlight.pageArrayIndex,
      pageIndex: focusedOverlayHighlight.pageIndex,
      localRowIndex: focusedOverlayHighlight.localRowIndex,
      globalRowIndex: focusedOverlayHighlight.globalRowIndex,
      matchReason: focusedOverlayHighlight.matchReason,
      side: "ocr" as const,
    }
    : null;
  const recentOcrHistory = [...ocrHistoryRows].reverse().slice(0, 5);
  const latestOcrRevisionMode =
    ocrHistoryLatest?.ui_mode === "sheet"
      ? "シートUI"
      : ocrHistoryLatest?.ui_mode === "legacy"
        ? "履歴UI"
        : ocrHistoryLatest?.ui_mode || "-";
  const latestOcrRevisionChanged =
    typeof ocrHistoryLatest?.changed === "boolean"
      ? ocrHistoryLatest.changed
        ? "あり"
        : "なし"
      : "-";
  const latestOcrRevisionActionLabel =
    ocrHistoryLatest?.sheet_save_only || ocrHistoryLatest?.sheet_save_mode === "exact"
      ? "最終保存"
      : "最終反映";
  const ocrSheetColumnCount = getColumnCount(ocrSheetHeader, ocrSheetRows);
  const ocrSheetHeaders = Array.from({ length: ocrSheetColumnCount }, (_, idx) => {
    const name = ocrSheetHeader[idx]?.trim() || "";
    return name || `列${idx + 1}`;
  });
  const ocrSheetColumnSpecs = ocrSheetHeaders.map((header, idx) =>
    getOcrSheetColumnSpec(ocrSheetFields[idx] || "", header, normalizeDietTypeToken),
  );
  const ocrSheetQuantityColumnOptions = ocrSheetColumnSpecs
    .map((spec, idx) => ({
      className: spec.className,
      value: String(idx),
      label: `${idx + 1}: ${ocrSheetHeaders[idx] || `列${idx + 1}`}`,
    }))
    .filter((option) => option.className === "ocr-sheet-col-qty")
    .map(({ value, label }) => ({ value, label }));
  const ocrSheetBulkFillColumnOptions = ocrSheetColumnSpecs
    .map((spec, idx) => ({
      className: spec.className,
      field: String(ocrSheetFields[idx] || "").trim().toLowerCase(),
      header: String(ocrSheetHeaders[idx] || "").trim(),
      value: String(idx),
      label: `${idx + 1}: ${ocrSheetHeaders[idx] || `列${idx + 1}`}`,
    }))
    .filter((option) => {
      if (option.className !== "ocr-sheet-col-qty") return false;
      if (option.field.startsWith("qty.placeholder")) return false;
      if (option.header === "-") return false;
      return true;
    })
    .map(({ value, label }) => ({ value, label }));
  const ocrSheetAcceptedVisibleCountFallback = ocrSheetRows.reduce((count, row) => {
    return count + ocrSheetQuantityColumnOptions.reduce((rowCount, option) => {
      const cellIndex = Number(option.value);
      return rowCount + (String(row[cellIndex] ?? "").trim() ? 1 : 0);
    }, 0);
  }, 0);
  const ocrSheetAcceptedCount = Math.max(
    Number(ocrSheetNumericCellSummary.accepted_count || 0),
    ocrSheetAcceptedVisibleCountFallback,
  );
  const ocrSheetDeterministicCandidateCount = Number(
    ocrSheetNumericCellSummary.deterministic_candidate_count || 0,
  );
  const ocrSheetWeakCandidateCount = Number(ocrSheetNumericCellSummary.weak_candidate_count || 0);
  const ocrSheetUnresolvedCount = Number(ocrSheetNumericCellSummary.unresolved_count || 0);
  const ocrSheetRawNumericCount = Number(ocrSheetNumericCellSummary.raw_ocr_numeric_count || 0);
  const ocrSheetVisibleOverlayCellMap = (() => {
    const map = new Map<string, OcrNumericCellItem>();
    const classificationRank = (classification: OcrNumericCellClassification | "") =>
      classification === "deterministic_candidate" ? 2 : classification === "weak_candidate" ? 1 : 0;
    for (const item of ocrSheetNumericCellItems) {
      const classification = normalizeOcrNumericCellClassification(item.classification);
      if (!overlayClassificationVisibleInMode(classification, ocrConfidenceDisplayMode)) {
        continue;
      }
      const rowIndex = typeof item.target_row_index === "number" ? item.target_row_index : null;
      const cellIndex = typeof item.target_col_index === "number" ? item.target_col_index : null;
      const overlayValue = String(item.value ?? "").trim();
      if (
        rowIndex == null
        || cellIndex == null
        || rowIndex < 0
        || cellIndex < 0
        || !overlayValue
        || String(ocrSheetRows[rowIndex]?.[cellIndex] ?? "").trim()
      ) {
        continue;
      }
      const key = `${rowIndex}:${cellIndex}`;
      const current = map.get(key);
      if (!current) {
        map.set(key, item);
        continue;
      }
      const currentClassification = normalizeOcrNumericCellClassification(current.classification);
      if (classificationRank(classification) > classificationRank(currentClassification)) {
        map.set(key, item);
      }
    }
    return map;
  })();
  const ocrSheetVisibleOverlayItems = Array.from(ocrSheetVisibleOverlayCellMap.values())
    .map((item): OcrVisibleOverlayItem | null => {
      const rowIndex = typeof item.target_row_index === "number" ? item.target_row_index : null;
      const cellIndex = typeof item.target_col_index === "number" ? item.target_col_index : null;
      const classification = normalizeOcrNumericCellClassification(item.classification);
      const value = String(item.value ?? "").trim();
      if (rowIndex == null || cellIndex == null || !classification || !value) {
        return null;
      }
      return {
        ...item,
        target_row_index: rowIndex,
        target_col_index: cellIndex,
        classification,
        value,
      };
    })
    .filter((item): item is OcrVisibleOverlayItem => Boolean(item));
  const ocrSheetVisibleOverlayItemsByRow = (() => {
    const map = new Map<number, OcrVisibleOverlayItem[]>();
    for (const item of ocrSheetVisibleOverlayItems) {
      const current = map.get(item.target_row_index) || [];
      current.push(item);
      map.set(item.target_row_index, current);
    }
    return map;
  })();
  const ocrSheetVisibleOverlayCount = ocrSheetVisibleOverlayCellMap.size;
  const ocrSheetVisibleCellCount = ocrSheetAcceptedCount + ocrSheetVisibleOverlayCount;
  const ocrSheetConfidenceLegendText = [
    `raw ${ocrSheetRawNumericCount}`,
    `accepted ${ocrSheetAcceptedCount}`,
    `deterministic ${ocrSheetDeterministicCandidateCount}`,
    `weak ${ocrSheetWeakCandidateCount}`,
    `unresolved ${ocrSheetUnresolvedCount}`,
    `visible ${ocrSheetVisibleCellCount}`,
  ].join(" / ");
  const ocrSheetConfidenceModeDescription =
    ocrConfidenceDisplayMode === "strict"
      ? "厳格: OCRスコア0.45以上だけをシート本体に表示します。"
      : ocrConfidenceDisplayMode === "assisted"
        ? "補助: 厳格に加えてOCRスコア0.15以上の候補をoverlay表示します。"
        : "提案: 厳格/補助に加えてOCRスコア0.05以上の弱い候補をoverlay表示します。";
  const applyVisibleOcrOverlaySuggestions = () => {
    adoptVisibleOcrOverlayItems(
      ocrSheetVisibleOverlayItems,
      ocrSheetVisibleOverlayItems.length
        ? `表示中の提案 ${ocrSheetVisibleOverlayItems.length} 件を採用しました。`
        : "表示中に採用できる提案がありません。",
    );
  };
  const applyVisibleOcrOverlaySuggestionsForRow = (rowIndex: number) => {
    const rowItems = ocrSheetVisibleOverlayItemsByRow.get(rowIndex) || [];
    adoptVisibleOcrOverlayItems(
      rowItems,
      rowItems.length
        ? `行 ${rowIndex + 1} の提案 ${rowItems.length} 件を採用しました。`
        : `行 ${rowIndex + 1} に採用できる提案がありません。`,
    );
  };
  useEffect(() => {
    if (!focusedOverlayTarget) return;
    if (focusedOverlayTarget.pageArrayIndex === activeOcrPageIndex) return;
    if (!ocrPages[focusedOverlayTarget.pageArrayIndex]) return;
    if (ocrPageSelectionModeRef.current === "manual") return;
    selectOcrPage(focusedOverlayTarget.pageArrayIndex, { manual: false });
  }, [focusedOverlayTarget, activeOcrPageIndex, ocrPages]);

  useEffect(() => {
    if (!focusedOverlayHighlight) return;
    const wrapper = ocrPreviewWrapperRef.current;
    if (!wrapper) return;
    const top = focusedOverlayHighlight.top;
    const bottom = focusedOverlayHighlight.top + focusedOverlayHighlight.height;
    const viewportTop = wrapper.scrollTop;
    const viewportBottom = viewportTop + wrapper.clientHeight;
    if (top >= viewportTop && bottom <= viewportBottom) return;
    const targetTop = Math.max(top - Math.max((wrapper.clientHeight - focusedOverlayHighlight.height) / 2, 24), 0);
    wrapper.scrollTo({ top: targetTop, behavior: "smooth" });
  }, [focusedOverlayHighlight]);

  const ocrSheetStickyColumnOffsets = (() => {
    const stickyRoles = new Set(["ocr-sheet-col-date", "ocr-sheet-col-daypart", "ocr-sheet-col-menu"]);
    let left = OCR_SHEET_ROW_INDEX_WIDTH;
    return ocrSheetColumnSpecs.map((spec) => {
      if (!stickyRoles.has(spec.className)) {
        return null;
      }
      const offset = left;
      left += spec.width;
      return offset;
    });
  })();
  const ocrSheetDateColumnIndex = (() => {
    const fieldIdx = ocrSheetFields.findIndex((field) =>
      String(field || "").trim().toLowerCase().startsWith("date"),
    );
    if (fieldIdx >= 0) return fieldIdx;
    return ocrSheetHeaders.findIndex((header) => {
      const token = normalizeHeaderToken(header);
      return token.includes("日付") || token.startsWith("date");
    });
  })();
  const ocrSheetDaypartColumnIndex = (() => {
    const fieldIdx = ocrSheetFields.findIndex((field) => {
      const token = String(field || "").trim().toLowerCase();
      return token === "daypart" || token === "meal" || token === "time";
    });
    if (fieldIdx >= 0) return fieldIdx;
    return ocrSheetHeaders.findIndex((header) => {
      const token = normalizeHeaderToken(header);
      return token.includes("区分") || token === "daypart" || token === "meal" || token === "time";
    });
  })();
  useEffect(() => {
    const indices = new Set(ocrSheetQuantityColumnOptions.map((option) => option.value));
    if (ocrSwapLeftColumn && !indices.has(ocrSwapLeftColumn)) {
      setOcrSwapLeftColumn("");
    }
    if (ocrSwapRightColumn && !indices.has(ocrSwapRightColumn)) {
      setOcrSwapRightColumn("");
    }
  }, [ocrSheetQuantityColumnOptions, ocrSwapLeftColumn, ocrSwapRightColumn]);
  useEffect(() => {
    const indices = new Set(ocrSheetBulkFillColumnOptions.map((option) => option.value));
    if (ocrSheetColumnFillTarget && !indices.has(ocrSheetColumnFillTarget)) {
      setOcrSheetColumnFillTarget("");
    }
  }, [ocrSheetBulkFillColumnOptions, ocrSheetColumnFillTarget]);
  const ocrSheetRowDateStripeClasses = (() => {
    const stripes: string[] = [];
    let tone = 0;
    let prevDate = "";
    ocrSheetRows.forEach((row, idx) => {
      const rawDate =
        ocrSheetDateColumnIndex >= 0 && ocrSheetDateColumnIndex < row.length
          ? row[ocrSheetDateColumnIndex]
          : "";
      const dateValue = String(rawDate || "").trim();
      const effectiveDate = dateValue || prevDate;
      if (idx === 0) {
        prevDate = effectiveDate;
      } else if (effectiveDate && effectiveDate !== prevDate) {
        tone = tone === 0 ? 1 : 0;
        prevDate = effectiveDate;
      } else if (!prevDate && dateValue) {
        prevDate = dateValue;
      }
      stripes.push(tone === 0 ? "ocr-sheet-row-date-a" : "ocr-sheet-row-date-b");
    });
    return stripes;
  })();
  const ocrSheetRowBoundaryClasses = (() => {
    const boundaries: string[] = [];
    let prevDate = "";
    let prevDaypart = "";
    ocrSheetRows.forEach((row, idx) => {
      const rawDate =
        ocrSheetDateColumnIndex >= 0 && ocrSheetDateColumnIndex < row.length
          ? row[ocrSheetDateColumnIndex]
          : "";
      const rawDaypart =
        ocrSheetDaypartColumnIndex >= 0 && ocrSheetDaypartColumnIndex < row.length
          ? row[ocrSheetDaypartColumnIndex]
          : "";
      const dateValue = String(rawDate || "").trim();
      const daypartValue = String(rawDaypart || "").trim();
      const effectiveDate = dateValue || prevDate;
      const effectiveDaypart = daypartValue || prevDaypart;

      let boundaryClass = "";
      if (idx > 0) {
        if (effectiveDate && prevDate && effectiveDate !== prevDate) {
          boundaryClass = "ocr-sheet-boundary-date";
        } else if (
          effectiveDate &&
          prevDate &&
          effectiveDate === prevDate &&
          effectiveDaypart &&
          prevDaypart &&
          effectiveDaypart !== prevDaypart
        ) {
          boundaryClass = "ocr-sheet-boundary-daypart";
        }
      }
      boundaries.push(boundaryClass);
      if (dateValue) prevDate = dateValue;
      if (daypartValue) prevDaypart = daypartValue;
    });
    return boundaries;
  })();
  const ocrSheetSelectionBounds = getOcrSheetSelectionBounds(ocrSheetSelection);
  const sheetWeeklyMenuMissing = ocrSheetWarnings.includes("sheet_weekly_menu_missing");
  const ocrTableFallbackWarning = ocrSheetSource.startsWith("ocr_table") && !sheetWeeklyMenuMissing;
  const orderReviewBadges = Array.isArray(order?.ocr_review_badges)
    ? order.ocr_review_badges.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const dedupedOrderReviewBadges = Array.from(new Set(orderReviewBadges));
  const candidateEvidenceState = resolveCandidateEvidenceState(order);
  const workflowStateCode = candidateEvidenceState.workflowStateCode;
  const workflowHeadline = String(order?.workflow_state?.headline || "").trim();
  const workflowApplyGate = order?.apply_gate || order?.workflow_state?.apply_gate || null;
  const workflowCandidateEvidenceRunId = candidateEvidenceState.candidateEvidenceRunId;
  const hasUnresolvedCandidateEvidenceChoice = candidateEvidenceState.hasUnresolvedCandidateEvidenceChoice;
  const criticalDecisions = Array.isArray(order?.critical_decisions)
    ? order.critical_decisions.filter((item) => item && !String(item.selected_value || "").trim())
    : Array.isArray(order?.workflow_state?.critical_decisions)
      ? (order?.workflow_state?.critical_decisions || []).filter((item) => item && !String(item.selected_value || "").trim())
      : [];
  useEffect(() => {
    if (!order?.id) return;
    if (
      !hasUnresolvedCandidateEvidenceChoice
      || activeEditorRows.length > 0
      || ocrSheetLoading
      || !ocrSheetLoadSettled
    ) {
      setCandidateSheetPreview(null);
      setCandidateSheetPreviewLoading(false);
      setCandidateSheetPreviewMessage("");
      return;
    }
    void loadCandidateSheetPreview({ silent: true });
  }, [order?.id, hasUnresolvedCandidateEvidenceChoice, activeEditorRows.length, ocrSheetLoading, ocrSheetLoadSettled]);
  const workflowStateLabel = describeWorkflowState(workflowStateCode);
  const step1CriticalDecisions = criticalDecisions.filter((decision) => {
    const decisionType = String(decision?.decision_type || "").trim().toLowerCase();
    return decisionType === "facility" || decisionType === "week";
  });
  const step2CriticalDecisions = criticalDecisions.filter((decision) => {
    const decisionType = String(decision?.decision_type || "").trim().toLowerCase();
    return decisionType !== "facility" && decisionType !== "week";
  });
  const ocrOutputIssueCodes = Array.isArray(ocrOutput?.cell_issues)
    ? ocrOutput.cell_issues
        .map((item) => String(item?.issue_code || "").trim().toLowerCase())
        .filter(Boolean)
    : [];
  const mergedCellTemplateHint = new Set([
    "fax_layout_regular_diabetes_v1",
    "fax_layout_regular_forbidden_v1",
  ]).has(String(ocrOutput?.template_id || "").trim());
  const mergedCellLlmRecommended =
    mergedCellTemplateHint ||
    ocrOutputIssueCodes.includes("merged_numeric_cell") ||
    Boolean(
      Array.isArray(ocrOutput?.failed_cells) &&
        ocrOutput.failed_cells.some((cell) =>
          String(cell?.reason || "")
            .trim()
            .toLowerCase()
            .includes("merged_numeric_cell"),
        ),
    );
  const hasWorkflowState = Boolean(workflowStateCode || workflowHeadline || workflowApplyGate);
  const effectiveSheetReviewState =
    ocrSheetReviewState || workflowStateCode || (!hasWorkflowState ? String(order?.ocr_review_state || "").trim() : "");
  const effectiveSheetReviewLabel = describeReviewState(effectiveSheetReviewState);
  const effectiveProcessingStage =
    ocrSheetProcessingStage || (!hasWorkflowState ? String(order?.ocr_processing_stage || "").trim() : "");
  const effectiveProcessingStageLabel = describeProcessingStage(effectiveProcessingStage);
  const effectiveConfirmedLinesRetained =
    ocrSheetConfirmedLinesRetained || (!hasWorkflowState && Boolean(order?.ocr_confirmed_lines_retained));
  const workflowBlockers = dedupeStrings([
    ...(Array.isArray(order?.workflow_state?.blockers_json)
      ? order.workflow_state.blockers_json.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
    ...(Array.isArray(workflowApplyGate?.blockers)
      ? workflowApplyGate.blockers.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
  ]);
  const workflowWarnings = dedupeStrings([
    ...(Array.isArray(order?.workflow_state?.warnings_json)
      ? order.workflow_state.warnings_json.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
    ...(Array.isArray(workflowApplyGate?.warnings)
      ? workflowApplyGate.warnings.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
  ]);
  const unresolvedCriticalDecisionCount = criticalDecisions.length;
  const semanticShellOnly = workflowStateCode === "semantic_shell_only";
  const rerunInProgressState = workflowStateCode === "rerun_in_progress";
  const showNewEvidenceChoice = hasUnresolvedCandidateEvidenceChoice;
  const workflowSupportText = (() => {
    if (unresolvedCriticalDecisionCount > 0) {
      return `未確定の候補が ${unresolvedCriticalDecisionCount} 件あります。先に候補を選んでください。`;
    }
    if (workflowBlockers.length) {
      return `要対応: ${workflowBlockers.map((item) => describeReviewBlocker(item)).filter(Boolean).join(" / ")}`;
    }
    if (workflowWarnings.length) {
      return `確認: ${workflowWarnings.map((item) => describeReviewBlocker(item)).filter(Boolean).join(" / ")}`;
    }
    return "";
  })();
  const workflowGateAvailable =
    workflowApplyGate != null &&
    (workflowApplyGate?.can_apply != null ||
      workflowApplyGate?.can_confirm != null ||
      workflowBlockers.length > 0 ||
      workflowWarnings.length > 0);
  const effectiveApplyBlockers = dedupeStrings([
    ...(workflowGateAvailable ? workflowBlockers : []),
    ...ocrSheetApplyBlockers,
    ...(!hasWorkflowState && Array.isArray(order?.ocr_apply_blockers)
      ? order.ocr_apply_blockers.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
  ]);
  const effectiveConfirmBlockers = dedupeStrings([
    ...(workflowGateAvailable ? workflowBlockers : []),
    ...ocrSheetConfirmBlockers,
    ...(!hasWorkflowState && Array.isArray(order?.ocr_confirm_blockers)
      ? order.ocr_confirm_blockers.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
    !hasWorkflowState && order?.ocr_draft_newer_than_lines ? "draft_newer_than_lines" : "",
  ]);
  const effectiveConfirmWarnings = dedupeStrings([
    ...(workflowGateAvailable ? workflowWarnings : []),
    ...ocrSheetConfirmWarnings,
    ...(!hasWorkflowState && Array.isArray(order?.ocr_confirm_warnings)
      ? order.ocr_confirm_warnings.map((item) => String(item || "").trim()).filter(Boolean)
      : []),
    !hasWorkflowState && order?.ocr_auto_apply_blocked ? "auto_apply_blocked" : "",
  ]);
  const detailPrefersDraftPreview =
    (ocrSheetDraftNewerThanLines ||
      workflowWarnings.includes("draft_newer_than_lines") ||
      effectiveConfirmBlockers.includes("draft_newer_than_lines")) &&
    ocrSheetRows.length > 0;
  const draftPreviewLines = detailPrefersDraftPreview
    ? buildDraftPreviewLines(ocrSheetFields, ocrSheetHeaders, ocrSheetRows)
    : [];
  const detailUsesDraftPreview = detailPrefersDraftPreview && draftPreviewLines.length > 0;
  const detailDisplayLines = detailUsesDraftPreview ? draftPreviewLines : persistedLines;
  const pivotRows = buildPivotRows(detailDisplayLines);
  const categoryOrder = buildCategoryColumns(detailDisplayLines).map((col) => col.key);
  const pivotGroups = groupByDateAndCategory(buildPivotCategoryRows(pivotRows), categoryOrder);
  const lineGroups = groupByDateAndCategory(
    detailDisplayLines.map((line, idx) => {
      const categoryKey = makeCategoryKey(line.diet_type, line.area_id);
      return {
        line,
        idx,
        date: line.date,
        categoryKey,
        categoryLabel: formatCategoryLabel(categoryKey),
      };
    }),
    categoryOrder,
  );
  const effectiveCanApply = workflowGateAvailable
    ? Boolean(workflowApplyGate?.can_apply)
    : ocrSheetCanApply || Boolean(order?.ocr_can_apply_draft);
  const effectiveCanConfirm = sheetWeeklyMenuMissing
    ? false
    : (workflowGateAvailable
        ? Boolean(workflowApplyGate?.can_confirm)
        : ocrSheetCanConfirm || Boolean(order?.ocr_can_confirm)) &&
      effectiveConfirmBlockers.length === 0;
  const reviewBlockerText = effectiveConfirmBlockers
    .map((item) => describeReviewBlocker(item))
    .filter(Boolean)
    .join(" / ");
  const reviewWarningText = effectiveConfirmWarnings
    .map((item) => describeReviewBlocker(item))
    .filter(Boolean)
    .join(" / ");
  const workflowCandidateResolution = order?.candidate_resolution || order?.workflow_state?.candidate_resolution || null;
  const unresolvedLayoutResolutionDetails = Array.isArray(workflowCandidateResolution?.gate_summary?.details)
    ? workflowCandidateResolution.gate_summary.details
        .filter((item) => {
          const decisionType = String(item?.decision_type || "").trim().toLowerCase();
          const status = String(item?.status || "").trim().toLowerCase();
          return (
            (decisionType === "template" || decisionType === "column_mapping" || decisionType === "quantity") &&
            (status === "choice_required" || status === "blocked") &&
            !item?.suppressed
          );
        })
        .map((item) => describeReviewBlocker(`${String(item?.decision_type || "").trim()}_${String(item?.status || "").trim() === "choice_required" ? "choice_required" : "unresolved"}`))
        .filter(Boolean)
    : [];
  const ocrSheetProjectionMessage = (() => {
    const payloadMessage = String(ocrSheetProjection?.reason_message || "").trim();
    if (payloadMessage) return payloadMessage;
    if (ocrSheetWarnings.includes("sheet_payload_mapping_blocked_unresolved_template")) {
      return describeReviewBlocker("sheet_payload_mapping_blocked_unresolved_template");
    }
    if (ocrSheetWarnings.includes("sheet_payload_mapping_low_confidence")) {
      return describeReviewBlocker("sheet_payload_mapping_low_confidence");
    }
    if (ocrSheetSource === "weekly_menu" && unresolvedLayoutResolutionDetails.length) {
      return `OCR数量を週次メニューシートへ投影していません: ${unresolvedLayoutResolutionDetails.join(" / ")}`;
    }
    return "";
  })();
  const ocrReparseBlockedHint = (() => {
    const outcome = explicitTerminalReparseOutcome;
    if (!outcome) return "";
    const error = String(outcome.reasonCode || "").trim();
    if (outcome.kind === "rejected") {
      return describeReparseRejectedReason(error || "draft_ready_blocked");
    }
    const status = String(order?.ocr_status || "").trim().toLowerCase();
    if (!error && status !== "failed" && status !== "empty" && status !== "stalled") return "";
    if (isReparseStaleTimeoutError(error)) {
      return "LLM補完再解析が30分以上進まず停止扱いになりました。OCR結果は残っているため、必要なら再解析をやり直してください。";
    }
    if (error === "sheet_date_anchor_drift") {
      return "LLM再解析は日付範囲のドリフトを検知したため保存されませんでした。シート再読込では値は変わりません。";
    }
    if (error === "sheet_canonical_mismatch") {
      return "LLM再解析は週メニュー整合チェックで不一致を検知したため保存されませんでした。シート再読込では値は変わりません。";
    }
    if (error === "sheet_suspicious_blank_row") {
      return "LLM再解析は数量行の欠落を検知したため保存されませんでした。シート再読込では値は変わりません。";
    }
    if (error === "sheet_row_coverage_low") {
      return "LLM再解析はOCR行カバレッジ不足を検知したため保存されませんでした。シート再読込では値は変わりません。";
    }
    if (error === "sheet_column_anomaly") {
      return "LLM再解析は施設区分列の異常を検知したため保存されませんでした。シート再読込では値は変わりません。";
    }
    return "";
  })();
  const blockedMenuMonthId = extractWeekMonthId(
    order?.persisted_week_value
      || order?.week_value
      || order?.week
      || "",
  );
  const editorBlockedReasons = Array.from(
    new Set(
      [
        ...effectiveApplyBlockers,
        ...effectiveConfirmBlockers,
        ...(ocrSheetWarnings.includes("sheet_contract_invalid") ? ["sheet_contract_invalid"] : []),
      ]
        .map((item) => describeReviewBlocker(item))
        .filter(Boolean),
    ),
  );
  const ocrReviewStripBadges = (() => {
    const labels: string[] = [];
    if (effectiveSheetReviewLabel) labels.push(effectiveSheetReviewLabel);
    if (!hasWorkflowState) {
      dedupedOrderReviewBadges.forEach((badge) => {
        if (badge && !labels.includes(badge)) labels.push(badge);
      });
    }
    return labels;
  })();
  const visiblePrimaryReviewLabel = ocrReviewStripBadges[0] || "";
  const technicalReviewItems = [
    ...ocrReviewStripBadges.slice(1).map((badge) => `レビュー: ${badge}`),
    effectiveProcessingStageLabel ? `処理段階: ${effectiveProcessingStageLabel}` : "",
    effectiveConfirmedLinesRetained ? "確定明細は保持中" : "",
    reviewBlockerText ? `要対応: ${reviewBlockerText}` : "",
    reviewWarningText ? `確認: ${reviewWarningText}` : "",
    ocrReparseBlockedHint ? `再解析: ${ocrReparseBlockedHint}` : "",
  ].filter(Boolean);
  const computeStep1State = (
    currentOrder: OrderDetail | null | undefined,
    currentFacilityDraft: string,
    currentWeekDraft: string,
    currentCustomWeekRangeStart: string,
    currentCustomWeekRangeEnd: string,
  ) => {
    const persistedFacilityValue = (currentOrder?.facility || "").trim();
    const selectedFacilityValue = currentFacilityDraft.trim();
    const persistedWeekValue = getCanonicalWeekSelectionSource(currentOrder);
    const selectedWeekValue = getPendingStep1WeekSelection(
      currentWeekDraft,
      currentCustomWeekRangeStart,
      currentCustomWeekRangeEnd,
    );
    const persistedConcreteWeekValue = normalizeConcreteWeekValue(persistedWeekValue);
    const selectedConcreteWeekValue = normalizeConcreteWeekValue(selectedWeekValue);
    const facilityMissingValue = !persistedFacilityValue;
    const weekMissingValue = !persistedConcreteWeekValue;
    const facilitySelectionPendingValue = Boolean(
      selectedFacilityValue && selectedFacilityValue !== persistedFacilityValue,
    );
    const weekSelectionPendingValue = Boolean(
      selectedConcreteWeekValue && selectedConcreteWeekValue !== persistedConcreteWeekValue,
    );
    const step1BlockReasonsValue = [
      facilityMissingValue ? "施設未設定" : "",
      weekMissingValue ? "週未設定" : "",
      facilitySelectionPendingValue ? "施設未保存" : "",
      weekSelectionPendingValue ? "週未保存" : "",
    ].filter(Boolean);
    return {
      persistedFacility: persistedFacilityValue,
      selectedFacility: selectedFacilityValue,
      persistedWeek: persistedConcreteWeekValue,
      selectedWeek: selectedConcreteWeekValue,
      facilityMissing: facilityMissingValue,
      weekMissing: weekMissingValue,
      facilitySelectionPending: facilitySelectionPendingValue,
      weekSelectionPending: weekSelectionPendingValue,
      canSaveStep1:
        Boolean(selectedFacilityValue && selectedFacilityValue !== persistedFacilityValue) ||
        Boolean(selectedConcreteWeekValue && selectedConcreteWeekValue !== persistedConcreteWeekValue),
      step1Incomplete:
        facilityMissingValue ||
        weekMissingValue ||
        facilitySelectionPendingValue ||
        weekSelectionPendingValue,
      step1BlockReasons: step1BlockReasonsValue,
    };
  };
  const step1State = computeStep1State(
    order,
    facility,
    weekDraft,
    customWeekRangeStart,
    customWeekRangeEnd,
  );
  const {
    persistedFacility,
    selectedFacility,
    persistedWeek,
    selectedWeek,
    facilityMissing,
    weekMissing,
    facilitySelectionPending,
    weekSelectionPending,
    canSaveStep1,
    step1Incomplete,
    step1BlockReasons,
  } = step1State;
  const hasAuthoritativeSavedSheet = Boolean(order?.ocr_has_saved_draft);
    const monthlyMenuMonthId = extractWeekMonthId(
    weekDraft || getCanonicalWeekSelectionSource(order),
  );
  const monthlyMenuHref = monthlyMenuMonthId ? `/menus/${monthlyMenuMonthId}` : "";
  const step1ChoiceRequired = step1CriticalDecisions.length > 0;
  const step2ChoiceRequired = step2CriticalDecisions.length > 0;

  useEffect(() => {
    setStep2WizardChoice("");
    setStep2RepairStage("");
  }, [order?.id]);

  useEffect(() => {
    if (activeStep === 1 && !step1Incomplete) return;
    setStep2WizardChoice("");
    setStep2RepairStage("");
  }, [activeStep, step1Incomplete]);

  useEffect(() => {
    if (step2WizardChoice === "no") return;
    if (step2RepairStage) {
      setStep2RepairStage("");
    }
  }, [step2WizardChoice, step2RepairStage]);

  const getStepBlockedReason = (
    index: number,
    state: {
      step1Incomplete: boolean;
      step1BlockReasons: string[];
    } = step1State,
  ) => {
    if (index <= 0) return "";
    if (state.step1Incomplete) {
      return state.step1BlockReasons.join(" / ") || "Step1を完了してください";
    }
    if (index > 0 && step1ChoiceRequired) {
      return "施設または週の候補選択が必要です";
    }
    if (index > 1 && step2ChoiceRequired) {
      return "OCR候補の選択が必要です";
    }
    if (index > 2 && step2OutputBlockedReason) {
      return step2OutputBlockedReason;
    }
    return "";
  };
  const canAccessStep = (index: number) => !getStepBlockedReason(index);
  const normalizedOcrStatus = String(order?.ocr_status || "").trim().toLowerCase();
  const ocrHasEditableSheet = activeEditorRows.length > 0;
  const ocrSheetInitialLoadPending = !ocrHasEditableSheet && (ocrSheetLoading || !ocrSheetLoadSettled);
  const hasCanonicalSheetBlocker =
    ocrSheetSource === "weekly_menu_blocked" || editorBlockedReasons.length > 0;
  const showEditorBlockedPanel =
    !ocrHasEditableSheet &&
    (
      hasCanonicalSheetBlocker
      || (!ocrSheetInitialLoadPending && Boolean(ocrSheetMessage.trim()))
    );
  const ocrNeedsDraftSave = ocrHasEditableSheet && !effectiveCanApply;
  const ocrNeedsDraftApply = effectiveConfirmBlockers.includes("draft_newer_than_lines");
  const ocrProcessingNow =
    normalizedOcrStatus === "running" ||
    normalizedOcrStatus === "pending" ||
    Boolean(reparsePending) ||
    rerunInProgressState;
  const ocrTerminalFailureState =
    normalizedOcrStatus === "failed" ||
    normalizedOcrStatus === "error" ||
    normalizedOcrStatus === "empty" ||
    normalizedOcrStatus === "stalled";
  const ocrPagesUnavailable =
    !ocrPagesLoading &&
    !ocrPages.length &&
    !hasUsableOverlayPreview &&
    Boolean(ocrPagesMessage);
  const ocrHasUsableRecoveryFoundation =
    ocrHasEditableSheet || hasUsableOverlayPreview || shouldFallbackToRawPdfPreview || ocrPages.length > 0;
  const showOcrRecoveryAction =
    !ocrProcessingNow &&
    !ocrPagesLoading &&
    !step1Incomplete &&
    ((ocrTerminalFailureState && !ocrHasUsableRecoveryFoundation) || ocrPagesUnavailable);
  const overlayUnavailableMode =
    !hasUsableOverlayPreview && !shouldFallbackToRawPdfPreview && !ocrPagesLoading && !ocrPages.length && Boolean(ocrPagesMessage);
  const ocrHardRecoveryMode =
    overlayUnavailableMode &&
    !ocrHasEditableSheet &&
    (showOcrRecoveryAction || ocrPagesUnavailable || ocrTerminalFailureState);
  const canApplyOcrSheet =
    ocrHasEditableSheet &&
    effectiveCanApply &&
    !ocrHardRecoveryMode &&
    !ocrRecoverPending &&
    !step2ChoiceRequired &&
    !semanticShellOnly &&
    !rerunInProgressState;
  const showOcrPipelineRerunAction = !step1Incomplete;
  const activateStep2RepairStage = (nextStage: Exclude<Step2RepairStage, "">) => {
    setStep2RepairStage(nextStage);
    if (nextStage === "merged") {
      setLlmReparseProvider("gemini");
      setLlmReparseModelMode("pro");
      setLlmReparseCustomModel("");
      setLlmReparsePromptPreset("merged_cell_quantity_spans");
      return;
    }
    if (nextStage === "llm" && llmReparsePromptPreset === "merged_cell_quantity_spans") {
      setLlmReparsePromptPreset("numeric_verification");
    }
  };
  const step2SuggestedRepairStage: Exclude<Step2RepairStage, ""> = (() => {
    if (step2ChoiceRequired) {
      return "candidate";
    }
    if (
      mergedCellLlmRecommended &&
      ocrHasEditableSheet &&
      !ocrProcessingNow &&
      !showOcrRecoveryAction &&
      !ocrHardRecoveryMode
    ) {
      return "merged";
    }
    if (
      ocrProcessingNow ||
      showOcrRecoveryAction ||
      ocrHardRecoveryMode ||
      semanticShellOnly ||
      !ocrHasEditableSheet ||
      ocrNeedsDraftSave
    ) {
      return "foundation";
    }
    return "llm";
  })();
  const activeStep2RepairStage: Exclude<Step2RepairStage, ""> =
    step2RepairStage || step2SuggestedRepairStage;
  const ocrPrimaryActionHint = (() => {
    if (workflowHeadline) return workflowHeadline;
    if (showNewEvidenceChoice) {
      return "新しいOCR候補があります。必要なら確認できますが、現在のシートのまま明細へ進めます";
    }
    if (rerunInProgressState) {
      return "OCRパイプラインを再実行しています。完了後に新しい候補を確認してください";
    }
    if (semanticShellOnly) {
      return "メニュー枠はありますが、数量はまだ信用できません。先にOCRパイプラインを再実行してください";
    }
    if (mergedCellLlmRecommended) {
      return "結合セルの数量がまたがる票です。Gemini Pro で span を推論してからシートを整えてください";
    }
    if (sheetWeeklyMenuMissing) return "先に対象月のメニューを登録してください";
    if (step1Incomplete) return "Step1で施設と週を保存してください";
    if (step2ChoiceRequired) return "まず OCR 候補を選択してから、シート修正に進んでください";
    if (ocrPagesUnavailable) {
      return "OCRページが使えません。先にOCR基盤を復旧してください。";
    }
    if (showOcrRecoveryAction) {
      return "OCR土台が不完全です。先にOCR基盤を復旧してから次の操作に進んでください。";
    }
    if (ocrSheetInitialLoadPending) {
      return "シートを取得中です。取得後に数字を確認してください";
    }
    if (showOcrPipelineRerunAction && !ocrHasEditableSheet) {
      return "先にOCRパイプラインを再実行してOCR基盤を更新してください。";
    }
    if ((normalizedOcrStatus === "running" || normalizedOcrStatus === "pending") && effectiveProcessingStageLabel) {
      return `${effectiveProcessingStageLabel}。完了後にシートを確認してください`;
    }
    if (!ocrHasEditableSheet) return "まずシートを再読込して編集対象を表示してください";
    if (!effectiveCanApply) return "まずシートを保存（暫定）して内容を整えてください";
    if (effectiveConfirmBlockers.includes("draft_newer_than_lines")) {
      return `内容を確認して「${STEP2_APPLY_NEXT_LABEL}」を押してください`;
    }
    if (reviewWarningText) return "内容を確認してから明細に反映してください";
    if (effectiveCanConfirm) return "明細確認後に確定できます";
    return `必要な数値を修正したら「${STEP2_APPLY_NEXT_LABEL}」を押してください`;
  })();
  const ocrPrimaryActionNote = (() => {
    if (step1ChoiceRequired) {
      return "Step1 に戻って、施設または週の候補を先に確定してください。";
    }
    if (showNewEvidenceChoice) {
      return "現在のシートが正解です。新しいOCR候補はプレビュー専用で保持され、必要な時だけ切り替えます。";
    }
    if (rerunInProgressState) {
      return "再実行中は現在のシートを維持したまま待機します。完了後に切り替え可否を判断してください。";
    }
    if (semanticShellOnly) {
      return "いまのシートは行と列の枠だけ確認できます。数量はまだ信頼できないため、基盤の再実行または復旧を優先してください。";
    }
    if (
      workflowStateCode === "layout_choice_required"
      || workflowStateCode === "choice_required"
      || step2ChoiceRequired
    ) {
      return "候補が複数あるため、下の候補選択を先に確定してください。";
    }
    if (workflowStateCode === "draft_blocked") {
      return reviewBlockerText || "反映前に残っている条件を先に解消してください。";
    }
    if (workflowStateCode === "recovery_required") {
      return "OCR基盤の復旧が必要です。復旧完了後にシート確認へ進みます。";
    }
    if (sheetWeeklyMenuMissing) return "登録後にシートを再読込すると、正しいメニュー土台で確認できます。";
    if (step1Incomplete) return "施設と週の設定が保存されるまで次の工程には進めません。";
    if (ocrPagesUnavailable) {
      return "いまは原本PDFフォールバックだけです。OCR基盤を復旧すると、OCRページとオーバーレイの再取得を行います。";
    }
    if (showOcrRecoveryAction) return "復旧を完了すると、シートの再読込と明細反映の復旧チェックを進めやすくなります。";
    if (!ocrHasEditableSheet) return "";
    if (!effectiveCanApply && reviewBlockerText) return reviewBlockerText;
    if (effectiveConfirmBlockers.includes("draft_newer_than_lines")) {
      return "シートは保存済みです。まだ明細には反映していません。";
    }
    if (reviewWarningText) return reviewWarningText;
    if (effectiveConfirmedLinesRetained) return "現在の確定済み明細は保持したまま確認できます。";
    return "";
  })();
  const workflowSummaryAction = (() => {
    if (workflowPrimaryActionCode === "run_ocr_pipeline" || workflowPrimaryActionCode === "rerun_ocr_pipeline") {
      return {
        label: reparsePending || rerunInProgressState ? "再実行中..." : "OCRパイプラインを再実行",
        onClick: () => void rerunOcrPipeline(),
        disabled: reparsePending || rerunInProgressState || ocrRecoverPending,
      };
    }
    if (workflowPrimaryActionCode === "recover_ocr_evidence") {
      return {
        label: ocrRecoverPending ? "復旧中..." : "OCR基盤を復旧",
        onClick: () => void recoverOcrFoundation(),
        disabled: ocrRecoverPending || ocrProcessingNow,
      };
    }
    return null;
  })();
  const step2OutputBlockedReason = (() => {
    if (step1Incomplete) return "";
    if (step2ChoiceRequired) {
      return "Step2でOCR候補を確定してください";
    }
    if (rerunInProgressState) {
      return "Step2のOCR再実行が完了するまで待ってください";
    }
    if (semanticShellOnly) {
      return "Step2でOCR基盤を更新して数量を確認してください";
    }
    if (effectiveConfirmBlockers.includes("draft_newer_than_lines")) {
      return "Step2でシートを整えて明細へ反映してください";
    }
    if (!effectiveCanApply && reviewBlockerText) {
      return `Step2で条件を解消してください: ${reviewBlockerText}`;
    }
    return "";
  })();
  const highlightApplyAction = ocrHasEditableSheet && !step1Incomplete && effectiveCanApply;
  const canSaveDraftSheet = !step1Incomplete && ocrHasEditableSheet && !ocrHardRecoveryMode && !ocrSheetAutoRetryBlocked;
  const canAttemptApplyOcrSheet = !ocrTableSaving && !step1Incomplete;
  const currentDraftReadyForWork =
    ocrHasEditableSheet &&
    !semanticShellOnly &&
    !step2ChoiceRequired &&
    !ocrHardRecoveryMode &&
    !rerunInProgressState &&
    (effectiveCanApply || effectiveConfirmBlockers.includes("draft_newer_than_lines"));
  const staleRawOcrFailureForCurrentDraft =
    currentDraftReadyForWork &&
    !explicitTerminalReparseOutcome &&
    ["failed", "error", "empty", "stalled"].includes(normalizedOcrStatus);
  const staleRawOcrStatusMessages = (() => {
    const messages = new Set<string>();
    if (rawOcrStatus === "failed" || rawOcrStatus === "error") {
      messages.add(describeOcrFailure(order?.ocr_error));
    } else if (rawOcrStatus === "empty") {
      messages.add(
        hasUsableOverlayPreview
          ? "OCR結果の構造化行は空でした。左のPDFプレビューを見ながら右のシートを修正してください。"
          : "OCR結果が空でした。overlay ではなく原本PDFを見ながら修正してください。",
      );
    } else if (rawOcrStatus === "stalled") {
      messages.add(order?.ocr_error ? `OCRが停止しました: ${order.ocr_error}` : "OCRが停止しました。");
    }
    return messages;
  })();
  const visibleStep2CriticalBannerMessages = staleRawOcrFailureForCurrentDraft
    ? step2CriticalBannerMessages.filter((message) => !staleRawOcrStatusMessages.has(message))
    : step2CriticalBannerMessages;
  if (staleRawOcrFailureForCurrentDraft) {
    ocrStatusLabel = showNewEvidenceChoice ? "完了(候補あり)" : "完了";
    if (showNewEvidenceChoice) {
      ocrStatusDetail = "現在のシートは利用可能です。新しいOCR候補は別扱いで確認できます。";
    } else {
      ocrStatusDetail = "";
    }
  }
  if (explicitTerminalReparseOutcome && currentDraftReadyForWork) {
    const usableDetail = showNewEvidenceChoice
      ? "現在のシートは利用可能です。新しいOCR候補は別扱いで確認できます。"
      : "現在のシートは利用可能です。";
    if (explicitTerminalReparseOutcome.kind === "failed") {
      ocrStatusLabel = showNewEvidenceChoice ? "再解析失敗(候補あり)" : "再解析失敗(現シート維持)";
    } else {
      ocrStatusLabel = showNewEvidenceChoice ? "再解析却下(候補あり)" : "再解析却下(現シート維持)";
    }
    ocrStatusDetail = [explicitTerminalReparseOutcome.detail, usableDetail].filter(Boolean).join(" ");
  }
  const step2FinishVisible = Boolean(step2WizardChoice);
  const step2FinishActionNote = (() => {
    if (step2WizardChoice === "yes") {
      return workflowCandidateEvidenceRunId
        ? "現在のシートを維持したまま、保存して明細に反映します。"
        : "現在のシートを保存して、そのまま明細に反映します。";
    }
    if (ocrProcessingNow) {
      return "再解析中は待機します。完了後にこのボタンで保存して明細へ進みます。";
    }
    if (showNewEvidenceChoice) {
      return "新しいOCR候補を選ぶか、今のシートを維持してからここで明細へ進みます。";
    }
    return "どの手段を使っても、最後はここでシートを保存して明細へ反映します。";
  })();
  const saveSheetButtonClassName = canSaveDraftSheet && highlightApplyAction ? "btn ghost" : "btn";
  const applySheetButtonClassName = canApplyOcrSheet && highlightApplyAction ? "btn primary" : "btn ghost";
  const ocrTechnicalDetails = (() => {
    const items = new Set<string>();
    items.add(`編集対象: シートテンプレート`);
    items.add(`ソース: ${ocrSheetSource || "-"}`);
    items.add(`項目: ${activeEditorFields.length} / 行: ${activeEditorRows.length} / 列: ${ocrSheetHeaders.length}`);
    if (visiblePrimaryReviewLabel) items.add(`状態: ${visiblePrimaryReviewLabel}`);
    technicalReviewItems.forEach((item) => {
      if (item) items.add(item);
    });
    return Array.from(items);
  })();
  const facilityTemplateAreaOptions = buildFacilityTemplateAreaOptions(
    facilityConfig,
    facilityTemplateColumnDraft,
  );
  const facilityTemplateDirty =
    JSON.stringify(facilityTemplateColumns) !== JSON.stringify(facilityTemplateColumnDraft);
  useEffect(() => {
    const indices = new Set(facilityTemplateColumnDraft.map((column, idx) => String(column.index ?? idx)));
    if (facilityTemplateSwapLeft && !indices.has(facilityTemplateSwapLeft)) {
      setFacilityTemplateSwapLeft("");
    }
    if (facilityTemplateSwapRight && !indices.has(facilityTemplateSwapRight)) {
      setFacilityTemplateSwapRight("");
    }
  }, [facilityTemplateColumnDraft, facilityTemplateSwapLeft, facilityTemplateSwapRight]);
  const stepCount = orderSteps.length;
  const activeStepIndex = Math.min(Math.max(activeStep, 0), stepCount - 1);
  const activeStepMeta = orderSteps[activeStepIndex];
  const canStepPrev = activeStepIndex > 0;
  const canStepNext = activeStepIndex < stepCount - 1 && canAccessStep(activeStepIndex + 1);
  const nextStepLabel = canStepNext ? orderSteps[activeStepIndex + 1].label : "";
  const prevStepLabel = canStepPrev ? orderSteps[activeStepIndex - 1].label : "";
  const isLastStep = activeStepIndex >= stepCount - 1;
  const orderAlreadyConfirmed = String(order?.status || "").trim() === "確定";
  const nextStepButtonLabel = isLastStep
    ? orderAlreadyConfirmed
      ? "注文一覧へ戻る"
      : confirmSaving
        ? "確定中..."
        : "確定して注文一覧へ戻る"
    : canStepNext
      ? `次へ: ${nextStepLabel}`
      : "次へ";
  const stepInteractionLocked =
    facilitySelectionPending || weekSelectionPending || Boolean(pendingSavedSheetContextChange);
  const goStep = async (index: number) => {
    const bounded = Math.min(Math.max(index, 0), stepCount - 1);
    const blockedReason = getStepBlockedReason(bounded);
    if (blockedReason) {
      if (!stepInteractionLocked && bounded > 0) {
        const refreshedOrder = await safeRefreshOrderWorkspace(
          { preserveSelections: false },
          "最新状態の取得に失敗しました。",
        );
        const refreshedStep1State = computeStep1State(
          refreshedOrder || order,
          refreshedOrder?.facility || facility,
          getCanonicalWeekSelectionSource(refreshedOrder),
          customWeekRangeStart,
          customWeekRangeEnd,
        );
        if (!getStepBlockedReason(bounded, refreshedStep1State)) {
          setActiveStep(bounded);
          return;
        }
        setActionMessage(
          `次のステップへ進めません: ${getStepBlockedReason(bounded, refreshedStep1State) || blockedReason}`,
        );
        return;
      }
      setActionMessage(`次のステップへ進めません: ${blockedReason}`);
      return;
    }
    setActiveStep(bounded);
  };
  const goNextStep = async () => {
    if (isLastStep) {
      await confirmAndReturnToOrders();
      return;
    }
    if (activeStepIndex < stepCount - 1) {
      await goStep(activeStepIndex + 1);
    }
  };
  const goPrevStep = () => {
    if (canStepPrev) {
      goStep(activeStepIndex - 1);
    }
  };

  const renderCriticalDecisionPanel = (
    decisions: CriticalDecisionPayload[],
    options: { title: string; note: string },
  ) => {
    if (!decisions.length) return null;
    return (
      <div className="warning-banner critical-choice-panel">
        <p className="field-label">{options.title}</p>
        <div className="critical-choice-list">
          {decisions.map((decision) => {
            const decisionType = String(decision.decision_type || "").trim();
            const title = String(decision.candidate_set_json?.title || decisionType || "候補").trim();
            const candidates = Array.isArray(decision.candidate_set_json?.candidates)
              ? decision.candidate_set_json?.candidates || []
              : [];
            return (
              <div key={`${decision.id || decisionType}-${title}`} className="critical-choice-card">
                <p className="facility-chip-name">{title}</p>
                <div className="facility-suggestion-list">
                  {candidates.map((candidate) => {
                    const candidateValue = String(candidate.value || "").trim();
                    const candidateLabel = String(candidate.label || candidateValue).trim() || candidateValue;
                    const scoreLabel =
                      typeof candidate.score === "number" && Number.isFinite(candidate.score)
                        ? `${Math.round(candidate.score * 100)}%`
                        : "";
                    return (
                      <button
                        key={`${decisionType}-${candidateValue}`}
                        type="button"
                        className={`facility-chip${decision.selected_value === candidateValue ? " auto" : ""}`}
                        onClick={() => void chooseCriticalDecision(decisionType, candidateValue)}
                        disabled={!candidateValue || criticalDecisionSaving === decisionType}
                      >
                        <span className="facility-chip-name">{candidateLabel}</span>
                        {scoreLabel ? <span className="facility-chip-score">{scoreLabel}</span> : null}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
        <p className="subtle">{options.note}</p>
      </div>
    );
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Order Detail</p>
          <h1>注文詳細</h1>
          <p className="subtle">修正・確定・出力までをまとめて操作できます。</p>
        </div>
        <TopNav />
      </header>

      {!order ? (
        <p className="subtle">Loading...</p>
      ) : (
        <>
          <section className="panel">
            <header className="panel-header">
              <div>
                <h2>概要</h2>
                {order.is_archived ? (
                  <p className="subtle">この注文はアーカイブ済みです。通常の注文一覧には表示されません。</p>
                ) : null}
              </div>
              <div className="panel-actions">
                <span className="status-pill">{order.status}</span>
                {order.is_archived ? (
                  <button
                    className="btn ghost"
                    type="button"
                    onClick={() => toggleCurrentOrderArchive(false)}
                    disabled={archiveOrderBusy}
                  >
                    {archiveOrderBusy ? "処理中..." : "アーカイブ解除"}
                  </button>
                ) : (
                  <button
                    className="btn ghost"
                    type="button"
                    onClick={() => toggleCurrentOrderArchive(true)}
                    disabled={archiveOrderBusy}
                  >
                    {archiveOrderBusy ? "処理中..." : "この注文をアーカイブ"}
                  </button>
                )}
              </div>
            </header>
            <div className="summary-grid summary-grid--compact">
              <div className="summary-primary-card">
                <p className="field-label">注文ID</p>
                <p className="summary-value">{order.id}</p>
              </div>
              <div className="summary-primary-card">
                <p className="field-label">解析ステータス</p>
                <p className="summary-value">
                  {ocrStatusLabel}
                  {order.ocr_updated_at ? ` / ${formatTimestamp(order.ocr_updated_at)}` : ""}
                  {order.lines_updated_at ? ` / 明細:${formatTimestamp(order.lines_updated_at)}` : ""}
                </p>
                {ocrStatusDetail ? <p className="subtle">{ocrStatusDetail}</p> : null}
              </div>
            </div>
            {workflowHeadline || workflowStateLabel || workflowSummaryAction ? (
              <div className="workflow-summary-card">
                <div>
                  <p className="field-label">作業状態</p>
                  <p className="workflow-summary-title">
                    {workflowHeadline || workflowStateLabel || "状態を確認してください"}
                  </p>
                  {workflowStateLabel && workflowHeadline && workflowStateLabel !== workflowHeadline ? (
                    <p className="subtle">状態: {workflowStateLabel}</p>
                  ) : null}
                  {workflowSupportText ? <p className="subtle">{workflowSupportText}</p> : null}
                </div>
                <div className="workflow-summary-actions">
                  {workflowSummaryAction ? (
                    <button
                      className="btn primary workflow-summary-action-btn"
                      type="button"
                      onClick={workflowSummaryAction.onClick}
                      disabled={workflowSummaryAction.disabled}
                    >
                      {workflowSummaryAction.label}
                    </button>
                  ) : null}
                  {order?.workflow_state?.primary_action ? (
                    <span className="ocr-review-pill ocr-review-pill--state">
                      {describeWorkflowPrimaryAction(order.workflow_state.primary_action)}
                    </span>
                  ) : workflowStateLabel ? (
                    <span className="ocr-review-pill ocr-review-pill--state">{workflowStateLabel}</span>
                  ) : null}
                </div>
              </div>
            ) : null}
            <details className="summary-details">
              <summary>詳細と内部情報を表示</summary>
              <div className="summary-details-grid">
                <div className="summary-detail-list">
                  <div>
                    <p className="field-label">Message ID</p>
                    <p className="summary-value">{order.message_id || "不明"}</p>
                  </div>
                  <div>
                    <p className="field-label">対象週</p>
                    <p className="summary-value">
                      {formatWeekLabel(getCanonicalWeekSelectionSource(order), order.week_label) || "未確定"}{" "}
                      {extractWeekMonthId(getCanonicalWeekSelectionSource(order))
                        ? <Link href={`/menus/${extractWeekMonthId(getCanonicalWeekSelectionSource(order))}`}>メニュー編集</Link>
                        : null}
                    </p>
                  </div>
                  <div>
                    <p className="field-label">作業状態</p>
                    <p className="summary-value">{workflowStateLabel || "未判定"}</p>
                    {workflowHeadline ? <p className="subtle">{workflowHeadline}</p> : null}
                    {workflowSupportText ? <p className="subtle">{workflowSupportText}</p> : null}
                  </div>
                  <div>
                    <p className="field-label">アーカイブ</p>
                    <p className="summary-value">{order.is_archived ? "アーカイブ済み" : "通常表示"}</p>
                    {order.archived_at ? (
                      <p className="subtle">
                        {formatTimestamp(order.archived_at)}
                        {order.archived_by ? ` / ${order.archived_by}` : ""}
                      </p>
                    ) : null}
                  </div>
                </div>
              </div>
              <div className="summary-details-stack">
                <details className="prompt-panel">
                  <summary>生OCR（最新）</summary>
                  <div className="raw-actions">
                    <button className="btn ghost" type="button" onClick={loadOcrRaw} disabled={ocrRawLoading}>
                      {ocrRawLoading ? "取得中..." : "生OCRを取得"}
                    </button>
                    {ocrRawMessage ? <span className="raw-message">{ocrRawMessage}</span> : null}
                  </div>
                  {ocrRawText ? <pre className="raw-output">{ocrRawText}</pre> : null}
                </details>
                <details className="prompt-panel">
                  <summary>OCR結果（失敗セル）</summary>
                  {ocrOutputMessage ? <p className="subtle">{ocrOutputMessage}</p> : null}
                  {ocrOutput ? (
                    <div className="raw-output">
                      <p>
                        ステータス: {ocrOutput.status || "不明"}
                        {ocrOutput.stage ? ` / ステージ: ${ocrOutput.stage}` : ""}
                        {" / "}テンプレート:{" "}
                        {ocrOutput.template_id || "未分類"}
                      </p>
                      {ocrOutput.ocr_source ? <p>反映ソース: {ocrOutput.ocr_source}</p> : null}
                      {ocrOutput.edited_table?.edited_at ? (
                        <p>
                          最終編集: {formatTimestamp(ocrOutput.edited_table.edited_at)} / UI:{" "}
                          {ocrOutput.edited_table.ui_mode || "-"}
                        </p>
                      ) : null}
                      {ocrOutput.warnings?.length ? (
                        <p>警告: {ocrOutput.warnings.join(" / ")}</p>
                      ) : null}
                      {ocrOutput.failed_cells?.length ? (
                        <ul className="failed-list">
                          {ocrOutput.failed_cells.map((cell, idx) => (
                            <li key={`${cell.row}-${cell.col}-${idx}`}>
                              {cell.row || "-"} / {cell.col || "-"} {cell.reason ? `(${cell.reason})` : ""}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p>失敗セルなし</p>
                      )}
                      <div className="reparse-debug-panel">
                        <p className="subtle">再解析デバッグ</p>
                        {reparseDebug ? (
                          <>
                          <p>
                            Provider: {reparseDebug.provider || "-"}
                            {reparseDebug.requested_provider
                              ? ` / requested=${reparseDebug.requested_provider}`
                              : ""}
                          </p>
                          <p>
                            行数: OCR {reparseDebug.row_count ?? "-"} / 明細 {reparseDebug.line_count ?? "-"} /{" "}
                            変更:{" "}
                            {typeof reparseDebug.changed === "boolean"
                              ? reparseDebug.changed
                                ? "あり"
                                : "なし"
                              : "-"}
                          </p>
                          <p>
                            件数: {reparseDebug.before_count ?? "-"}→{reparseDebug.after_count ?? "-"} /{" "}
                            LLM補完:{" "}
                            {typeof reparseDebug.llm_assist === "boolean"
                              ? reparseDebug.llm_assist
                                ? "ON"
                                : "OFF"
                              : "-"}
                          </p>
                          {reparseDebug.updated_at ? (
                            <p>更新: {formatTimestamp(reparseDebug.updated_at)}</p>
                          ) : null}
                          {reparseDebug.error ? <p>エラー: {reparseDebug.error}</p> : null}
                          {Array.isArray(reparseDebug.reject_reasons) && reparseDebug.reject_reasons.length ? (
                            <p>拒否理由: {reparseDebug.reject_reasons.join(" / ")}</p>
                          ) : null}
                          {Array.isArray(reparseDebug.warning_reasons) && reparseDebug.warning_reasons.length ? (
                            <p>
                              警告理由:{" "}
                              {reparseDebug.warning_reasons
                                .map((code) => describeReparseWarningReason(String(code || "")))
                                .filter(Boolean)
                                .join(" / ")}
                            </p>
                          ) : null}
                          {Array.isArray(reparseDebug.date_strings) && reparseDebug.date_strings.length ? (
                            <p>日付候補: {reparseDebug.date_strings.join(", ")}</p>
                          ) : null}
                          {typeof reparseDebug.request_prompt === "string" && reparseDebug.request_prompt ? (
                            <>
                              <p className="subtle">送信プロンプト</p>
                              <pre className="raw-output">{reparseDebug.request_prompt}</pre>
                            </>
                          ) : null}
                          {Array.isArray(reparseDebug.sample_rows) && reparseDebug.sample_rows.length ? (
                            <>
                              <p className="subtle">OCR行サンプル</p>
                              <pre className="raw-output">{reparseDebug.sample_rows.map((row) => row.join(" | ")).join("\n")}</pre>
                            </>
                          ) : null}
                          {reparseNormalizedLinesText ? (
                            <>
                              <p className="subtle">正規化後行（保存前）</p>
                              <pre className="raw-output">{reparseNormalizedLinesText}</pre>
                            </>
                          ) : null}
                          {typeof reparseDebug.raw_text === "string" && reparseDebug.raw_text ? (
                            <>
                              <p className="subtle">LLM生出力</p>
                              <pre className="raw-output">{reparseDebug.raw_text}</pre>
                            </>
                          ) : null}
                          {reparseValidationDetailText ? (
                            <>
                              <p className="subtle">検証詳細</p>
                              <pre className="raw-output">{reparseValidationDetailText}</pre>
                            </>
                          ) : null}
                          {reparseWarningDetailText ? (
                            <>
                              <p className="subtle">警告詳細</p>
                              <pre className="raw-output">{reparseWarningDetailText}</pre>
                            </>
                          ) : null}
                          {reparseQuantityMergeText ? (
                            <>
                              <p className="subtle">数量専用マージ統計</p>
                              <pre className="raw-output">{reparseQuantityMergeText}</pre>
                            </>
                          ) : null}
                          {reparseProviderDebugText ? (
                            <>
                              <p className="subtle">LLMメタ情報</p>
                              <pre className="raw-output">{reparseProviderDebugText}</pre>
                            </>
                          ) : null}
                          </>
                        ) : (
                          <p>まだ再解析デバッグ情報がありません。LLM補完再解析を実行すると表示されます。</p>
                        )}
                      </div>
                      {typeof ocrOutput.table_raw === "string" && ocrOutput.table_raw ? (
                        <>
                          <p className="subtle">OCR生出力</p>
                          <pre className="raw-output">{ocrOutput.table_raw}</pre>
                        </>
                      ) : null}
                    </div>
                  ) : null}
                </details>
                <details className="prompt-panel">
                  <summary>注文操作履歴</summary>
                  <div className="raw-actions">
                    <button
                      className="btn ghost"
                      type="button"
                      onClick={() => loadOrderHistory()}
                      disabled={orderHistoryLoading}
                    >
                      {orderHistoryLoading ? "取得中..." : "履歴を更新"}
                    </button>
                    {orderHistoryMessage ? <span className="raw-message">{orderHistoryMessage}</span> : null}
                  </div>
                  {orderHistoryRows.length ? (
                    <div className="order-history-list">
                      {orderHistoryRows.slice(0, 30).map((item, idx) => (
                        <div className="order-history-row" key={`order-history-${item.id || idx}`}>
                          <span>{formatTimestamp(item.created_at)}</span>
                          <span>{formatOrderAction(item.action)}</span>
                          <span>{item.actor || "-"}</span>
                          <span>{item.target || "-"}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="subtle">履歴はまだありません。</p>
                  )}
                </details>
              </div>
            </details>
            {actionMessage && <p className="message">{actionMessage}</p>}
          </section>

          {pendingSavedSheetContextChange ? (
            <div className="sheet-context-dialog-backdrop" role="presentation">
              <div
                className="sheet-context-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="sheet-context-dialog-title"
              >
                <p className="field-label">保存済みシートあり</p>
                <h3 id="sheet-context-dialog-title">施設または週を変更します</h3>
                <p className="subtle">
                  保存済みシートが正解です。新しい施設/週の骨格へ切り替える方法を選んでください。
                </p>
                <div className="sheet-context-dialog-summary">
                  <div className="sheet-context-dialog-row">
                    <span>施設</span>
                    <strong>{persistedFacility || "-"}</strong>
                    <span className="sheet-context-dialog-arrow">→</span>
                    <strong>{pendingSavedSheetContextChange.facility || "-"}</strong>
                  </div>
                  <div className="sheet-context-dialog-row">
                    <span>週</span>
                    <strong>{formatWeekLabel(persistedWeek) || persistedWeek || "-"}</strong>
                    <span className="sheet-context-dialog-arrow">→</span>
                    <strong>
                      {formatWeekLabel(pendingSavedSheetContextChange.week) || pendingSavedSheetContextChange.week || "-"}
                    </strong>
                  </div>
                </div>
                <p className="subtle">
                  骨格は新しい施設/週の週次メニューへ切り替えます。選べるのは、数量を引き継ぐか空白へ戻すかだけです。
                </p>
                <div className="sheet-context-dialog-actions">
                  <button
                    className="btn primary"
                    type="button"
                    onClick={() => void applySavedSheetContextChange("keep")}
                    disabled={Boolean(savedSheetContextChangeApplying)}
                  >
                    {savedSheetContextChangeApplying === "keep" ? "切替中..." : "数字を保持して切替"}
                  </button>
                  <button
                    className="btn ghost"
                    type="button"
                    onClick={() => void applySavedSheetContextChange("clear")}
                    disabled={Boolean(savedSheetContextChangeApplying)}
                  >
                    {savedSheetContextChangeApplying === "clear" ? "切替中..." : "数字をクリアして切替"}
                  </button>
                  <button
                    className="btn secondary"
                    type="button"
                    onClick={() => setPendingSavedSheetContextChange(null)}
                    disabled={Boolean(savedSheetContextChangeApplying)}
                  >
                    キャンセル
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          <section className="panel step-panel">
            <header className="panel-header">
              <div>
                <h2>進行ステップ</h2>
                <p className="subtle">左から順に進み、必要に応じてタブで戻れます。</p>
              </div>
              <span className="step-indicator">
                Step {activeStepIndex + 1} / {stepCount}
              </span>
            </header>
            <div className="step-tabs">
              {orderSteps.map((step, idx) => (
                <button
                  key={step.id}
                  type="button"
                  className={`step-tab${idx === activeStepIndex ? " active" : ""}${
                    idx < activeStepIndex ? " done" : ""
                  }`}
                  disabled={idx > 0 && stepInteractionLocked}
                  onClick={() => void goStep(idx)}
                  title={getStepBlockedReason(idx) || undefined}
                >
                  <span className="step-number">{idx + 1}</span>
                  <span className="step-label">{step.label}</span>
                  {getStepBlockedReason(idx) ? (
                    <span className="step-note">{getStepBlockedReason(idx)}</span>
                  ) : null}
                </button>
              ))}
            </div>
            <div className="step-meta">
              <div>
                <p className="step-title">{activeStepMeta.title}</p>
                <p className="subtle">{activeStepMeta.description}</p>
              </div>
              <div className="step-actions">
                <button className="btn ghost" type="button" onClick={goPrevStep} disabled={!canStepPrev}>
                  {canStepPrev ? `戻る: ${prevStepLabel}` : "戻る"}
                </button>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() => void goNextStep()}
                  disabled={!isLastStep && (activeStepIndex >= stepCount - 1 || stepInteractionLocked)}
                >
                  {nextStepButtonLabel}
                </button>
              </div>
            </div>
          </section>

          {activeStepIndex === 0 ? (
            <section className="panel">
              <header className="panel-header">
                <div>
                  <h2>{showingArtifactPdf ? artifactPdfLabel : "注文書 (FAX PDF)"}</h2>
                  <p className="subtle">
                    {showingArtifactPdf
                      ? "原本FAX PDFが見つからないため、OCR処理由来のPDFを表示しています。施設と週設定の確認用として扱ってください。"
                      : "原本PDFを確認し、施設と週設定を完了してください。"}
                  </p>
                </div>
                {pdfUrl ? (
                  <a href={pdfUrl} target="_blank" rel="noreferrer" className="ghost-link">
                    {showingArtifactPdf ? `${artifactPdfLabel}を開く` : "原本を開く"}
                  </a>
                ) : (
                  <span className="subtle">{pdfError || "PDFを読み込み中..."}</span>
                )}
              </header>
              <div className="step1-facility-block">
                <div className="summary-actions">
                  <label className="field">
                    <span className="field-label">施設 (Step1 必須)</span>
                      <select
                        className="input"
                        value={facility}
                        onChange={(e) => setFacility(e.target.value)}
                        disabled={facilityOptionsLoading || step1Saving}
                      >
                      <option value="">施設を選択</option>
                      {facility && !facilityOptions.some((opt) => opt.id === facility) ? (
                        <option value={facility}>{facility} (未登録)</option>
                      ) : null}
                      {facilityOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {formatFacilityLabel(option)}
                        </option>
                      ))}
                    </select>
                    {facilityOptionsError ? (
                      <span className="subtle">{facilityOptionsError}</span>
                    ) : facilityOptionsLoading ? (
                      <span className="subtle">施設一覧を取得中...</span>
                    ) : null}
                  </label>
                  <label className="field">
                    <span className="field-label">週 (Step1 必須)</span>
                    <select
                      className="input"
                      value={weekDraft}
                      onChange={(e) => setWeekDraft(e.target.value)}
                      disabled={weekOptionsLoading || step1Saving}
                    >
                      <option value="">週を選択</option>
                      {isConcreteWeekValue(weekDraft) && !weekOptions.some((option) => option.week_id === weekDraft) ? (
                        <option value={weekDraft}>{formatWeekLabel(weekDraft) || weekDraft} (現在値)</option>
                      ) : null}
                      {weekOptions.map((option) => (
                        <option key={option.week_id} value={option.week_id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    {weekOptionsError ? (
                      <span className="subtle">{weekOptionsError}</span>
                    ) : weekOptionsLoading ? (
                      <span className="subtle">週候補を取得中...</span>
                    ) : (
                      <span className="subtle">日曜から土曜の固定週を表示します。</span>
                    )}
                    <div className="step1-week-range">
                      <span className="subtle">例外時だけ範囲指定します。</span>
                      <div className="summary-actions">
                        <input
                          className="input"
                          type="date"
                          value={customWeekRangeStart}
                          onChange={(e) => setCustomWeekRangeStart(e.target.value)}
                          disabled={step1Saving}
                        />
                        <input
                          className="input"
                          type="date"
                          value={customWeekRangeEnd}
                          onChange={(e) => setCustomWeekRangeEnd(e.target.value)}
                          disabled={step1Saving}
                        />
                        <button type="button" className="btn secondary" onClick={applyCustomWeekRange} disabled={step1Saving}>
                          例外範囲を設定
                        </button>
                      </div>
                      {getPendingStep1WeekSelection(weekDraft, customWeekRangeStart, customWeekRangeEnd) ? (
                        <span className="subtle">
                          設定予定: {
                            formatWeekLabel(
                              getPendingStep1WeekSelection(weekDraft, customWeekRangeStart, customWeekRangeEnd),
                            )
                            || getPendingStep1WeekSelection(weekDraft, customWeekRangeStart, customWeekRangeEnd)
                          }
                        </span>
                      ) : null}
                    </div>
                  </label>
                  <button className="btn" onClick={updateStep1} disabled={!canSaveStep1 || step1Saving}>
                    {step1Saving
                      ? "設定中..."
                      : facilitySelectionPending || weekSelectionPending || step1Incomplete
                        ? "設定を保存"
                        : "設定済み"}
                  </button>
                </div>
                {step1Incomplete ? (
                  <div className="warning-banner">
                    {facilityMissing ? <p>この注文は施設が未設定です。Step2 に進む前に施設設定が必要です。</p> : null}
                    {weekMissing ? <p>この注文は週が未設定です。Step2 に進む前に週設定が必要です。</p> : null}
                    {facilitySelectionPending ? <p>施設の選択内容が未保存です。「設定を保存」を押してください。</p> : null}
                    {weekSelectionPending ? <p>週の選択内容が未保存です。「設定を保存」を押してください。</p> : null}
                  </div>
                ) : null}
                {sheetWeeklyMenuMissing ? (
                  <div className="warning-banner">
                    <p>
                      この週の月次メニューがシステムに未登録です。現在のシートは OCR テーブルからの暫定表示です。
                      「メニュー編集」で対象月の月次メニューを登録してから、シートを再読込してください。
                    </p>
                    {monthlyMenuHref ? (
                      <p>
                        <Link href={monthlyMenuHref} className="ghost-link">
                          対象月のメニュー編集を開く
                        </Link>
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {facilityCandidates.length ? (
                  <div className="facility-suggestions">
                    <span className="field-label">推定施設</span>
                    <div className="facility-suggestion-list">
                      {facilityCandidates.map((candidate) => (
                        <button
                          key={`${candidate.facility_id}-${candidate.reason || "auto"}`}
                          type="button"
                          className={`facility-chip${candidate.auto ? " auto" : ""}`}
                          onClick={() => setFacility(candidate.facility_id)}
                        >
                          <span className="facility-chip-name">
                            {candidate.facility_name || candidate.facility_id}
                          </span>
                          {candidate.score != null ? (
                            <span className="facility-chip-score">
                              {Math.round(candidate.score * 100)}%
                            </span>
                          ) : null}
                          {candidate.reason ? (
                            <span className="facility-chip-reason">
                              {formatFacilityReason(candidate.reason)}
                            </span>
                          ) : null}
                        </button>
                      ))}
                    </div>
                    <span className="facility-suggestion-note">
                      クリックで施設選択に反映します。反映後に「設定を保存」を押してください。
                    </span>
                  </div>
                ) : null}
                {renderCriticalDecisionPanel(step1CriticalDecisions, {
                  title: "先に確定する候補",
                  note: "施設や週が曖昧な場合だけ表示されます。ここで確定すると、以降のOCR修正と一覧整理に反映されます。",
                })}
              </div>
              {pdfUrl ? (
                <iframe title="order-pdf" src={pdfUrl} className="pdf-frame pdf-frame-wide" />
              ) : (
                <div className="pdf-frame pdf-placeholder">{pdfError || "PDFを読み込み中..."}</div>
              )}
            </section>
          ) : null}

          {activeStepIndex === 1 ? (
            <section className="panel">
                  <header className="panel-header">
                    <div>
                      <h2>OCR修正</h2>
                      <p className="subtle">OCRオーバーレイとシートを左右で比較しながら、そのままシートを修正します。</p>
                    </div>
                    <div className="panel-actions">
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => void loadOcrPages({ force: true })}
                      >
                        {ocrPagesLoading ? "再取得中..." : "OCRページを更新"}
                      </button>
                    </div>
                  </header>
                  {step1Incomplete ? (
                    <div className="warning-banner">
                      <p>Step1 の施設または週が未設定、または未保存のため、OCR修正はまだ開始できません。</p>
                      <p>Step1（注文書）で施設と週を設定して保存してから、このステップを再読込してください。</p>
                      <div className="panel-actions">
                        <button
                          className="btn"
                          type="button"
                          onClick={() => {
                            goStep(0);
                            if (typeof window !== "undefined") {
                              window.scrollTo({ top: 0, behavior: "smooth" });
                            }
                          }}
                        >
                          施設設定へ戻る
                        </button>
                      </div>
                    </div>
                  ) : null}
                  {visibleStep2CriticalBannerMessages.length ? (
                    <div className="critical-alert">
                      <p className="critical-alert-title">
                        {shouldFallbackToRawPdfPreview
                          ? "OCR結果を利用できないため、原本PDFへフォールバックしています。"
                          : usingSyntheticOverlay
                            ? "OCR overlay artifact が無いため、PDFプレビューを比較表示しています。"
                            : "OCR結果に注意が必要です。"}
                      </p>
                      <ul className="critical-alert-list">
                        {visibleStep2CriticalBannerMessages.map((message, idx) => (
                          <li key={`step2-critical-${idx}`}>{message}</li>
                        ))}
                      </ul>
                      {showOcrRecoveryAction ? (
                        <p className="subtle">
                          「OCR基盤を復旧」は、OCRの基盤(生データ/構造化表/ページ参照)を再構築し、手入力前の作業土台を戻します。
                        </p>
                      ) : null}
                      {!step1Incomplete ? (
                        <div className="ocr-flow-branch-actions">
                          {showNewEvidenceChoice ? (
                            <button
                              className="btn primary"
                              type="button"
                              onClick={() => void switchDraftToLatestEvidence()}
                              disabled={switchEvidencePending || keepCurrentPending || reparsePending || rerunInProgressState || ocrRecoverPending}
                            >
                              {switchEvidencePending ? "切替中..." : "新しいOCR候補に切り替える"}
                            </button>
                          ) : null}
                          {showNewEvidenceChoice ? (
                            <button
                              className="btn ghost"
                              type="button"
                              onClick={() => void keepCurrentDraft()}
                              disabled={switchEvidencePending || keepCurrentPending}
                            >
                              {keepCurrentPending ? "維持を記録中..." : "現状を維持"}
                            </button>
                          ) : null}
                          {(ocrPagesUnavailable || overlayUnavailableMode || (!hasUsableOverlayPreview && !ocrPagesLoading)) ? (
                            <button
                              className="btn ghost"
                              type="button"
                              onClick={() => void loadOcrPages({ force: true })}
                            >
                              OCR表示を再取得
                            </button>
                          ) : null}
                          <button
                            className="btn primary"
                            type="button"
                            onClick={() => void rerunOcrPipeline()}
                            disabled={reparsePending || rerunInProgressState || ocrRecoverPending}
                          >
                            {reparsePending || rerunInProgressState ? "再実行中..." : "OCRパイプラインを再実行"}
                          </button>
                          {(showOcrRecoveryAction || ocrHardRecoveryMode) ? (
                            <button
                              className="btn"
                              type="button"
                              onClick={() => void recoverOcrFoundation()}
                              disabled={ocrRecoverPending || ocrProcessingNow}
                            >
                              {ocrRecoverPending ? "復旧中..." : "OCR基盤を復旧"}
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  {ocrPagesMessage ? <p className="subtle">{ocrPagesMessage}</p> : null}
                  {ocrPages.length > 1 ? (
                    <div className="page-tabs">
                      {ocrPages.map((page, pageIdx) => (
                        <button
                          key={`ocr-page-${page.page_index ?? pageIdx}`}
                          type="button"
                          className={`page-tab ${pageIdx === activeOcrPageIndex ? "active" : ""}`}
                          onClick={() => selectOcrPage(pageIdx)}
                        >
                          Page {page.page_index ?? pageIdx + 1}
                        </button>
                      ))}
                    </div>
                  ) : null}
                  {sheetWeeklyMenuMissing ? (
                    <div className="warning-banner">
                      <p>
                        警告: この週の月次メニューが未登録のため、OCRテーブルを暫定ソースとして表示しています。
                        「メニュー編集」で対象月を登録してから、シートを再読込してください。
                      </p>
                      {monthlyMenuHref ? (
                        <p>
                          <Link href={monthlyMenuHref} className="ghost-link">
                            対象月のメニュー編集を開く
                          </Link>
                        </p>
                      ) : null}
                    </div>
                  ) : ocrTableFallbackWarning ? (
                    <p className="warning-banner">
                      警告: OCR テーブルを暫定ソースとして表示しています。内容を確認してから保存・反映してください。
                    </p>
                  ) : null}
                  {ocrSheetProjectionMessage ? (
                    <div className="warning-banner">
                      <p>数量投影の状態: {ocrSheetProjectionMessage}</p>
                    </div>
                  ) : null}
                  <div className={`ocr-workspace ${ocrWorkspaceLayoutMode === "vertical" ? "ocr-workspace--vertical" : ""}`}>
                    <div className="ocr-workspace-tools">
                      <div className="ocr-edit">
                        <div className="ocr-edit-header">
                          <div>
                            <p className="subtle">編集対象: シートテンプレート</p>
                            <h3 className="ocr-edit-title">左を見ながら、右のシートの数字だけを確認します。</h3>
                            <p className="subtle">いま必要な操作だけを下の分岐に沿って進めてください。</p>
                          </div>
                          <div className="ocr-editor-mode-switch">
                            <span className="subtle">表示配置</span>
                            <div className="preview-mode-toggle" role="tablist" aria-label="ocr workspace layout">
                              <button
                                className={`btn ghost ${ocrWorkspaceLayoutMode === "horizontal" ? "active" : ""}`}
                                type="button"
                                onClick={() => setOcrWorkspaceLayoutMode("horizontal")}
                                aria-pressed={ocrWorkspaceLayoutMode === "horizontal"}
                              >
                                左右
                              </button>
                              <button
                                className={`btn ghost ${ocrWorkspaceLayoutMode === "vertical" ? "active" : ""}`}
                                type="button"
                                onClick={() => setOcrWorkspaceLayoutMode("vertical")}
                                aria-pressed={ocrWorkspaceLayoutMode === "vertical"}
                              >
                                上下
                              </button>
                            </div>
                          </div>
                          <div className="ocr-editor-mode-switch">
                            <span className="subtle">数量割当</span>
                            <span className="ocr-fixed-mode-pill">箱館方式</span>
                          </div>
                        </div>
                        {ocrSheetMessage ? <p className="subtle">{ocrSheetMessage}</p> : null}
                        <div className="ocr-flow-card">
                          <div className="ocr-flow-header">
                            <div className="ocr-flow-header-copy">
                              <p className="ocr-flow-eyebrow">次にすること</p>
                              <p className="ocr-flow-title">{ocrPrimaryActionHint}</p>
                              {ocrPrimaryActionNote ? (
                                <p className="ocr-flow-note">{ocrPrimaryActionNote}</p>
                              ) : null}
                            </div>
                            {visiblePrimaryReviewLabel ? (
                              <span className="ocr-review-pill ocr-review-pill--state">
                                {visiblePrimaryReviewLabel}
                              </span>
                            ) : null}
                          </div>
                          <div className="ocr-flow-track">
                            <div className={`ocr-flow-step ${ocrHasEditableSheet ? "active" : ""}`}>
                              <span className="ocr-flow-step-index">1</span>
                              <div>
                                <p className="ocr-flow-step-title">シートを確認</p>
                                <p className="ocr-flow-step-note">
                                  左のオーバーレイを見ながら、右のシートの数字が合っているかを確認します。
                                </p>
                              </div>
                            </div>
                            <div className="ocr-flow-divider" />
                            <div className={`ocr-flow-step ${ocrNeedsDraftSave || step2WizardChoice === "yes" || canApplyOcrSheet ? "active" : ""}`}>
                              <span className="ocr-flow-step-index">2</span>
                              <div>
                                <p className="ocr-flow-step-title">数字は正しい？</p>
                                <p className="ocr-flow-step-note">
                                  正しければ明細へ反映します。怪しければ、手で直すか再解析を使います。
                                </p>
                              </div>
                            </div>
                          </div>
                          <div className={`ocr-flow-branches ocr-flow-branches--wizard ${step2WizardChoice === "no" ? "has-repair-panel" : ""}`}>
                            <div className="ocr-flow-rail">
                              <div className="ocr-flow-question-card ocr-flow-question-card--compact">
                                <div className="ocr-flow-question-main">
                                  <div className="ocr-flow-question-copy">
                                    <p className="ocr-flow-branch-label">選択 1</p>
                                    <h4>数字は正しい？</h4>
                                    <p className="subtle">
                                      左のプレビューを見ながら、右のシートの数量が合っているかだけを決めます。
                                    </p>
                                  </div>
                                  <div className="ocr-flow-choice-grid ocr-flow-choice-grid--compact">
                                    <button
                                      className={`ocr-flow-choice-button ${step2WizardChoice === "yes" ? "active" : ""}`}
                                      type="button"
                                      onClick={() => {
                                        if (workflowCandidateEvidenceRunId) {
                                          void keepCurrentDraft();
                                          return;
                                        }
                                        setStep2WizardChoice("yes");
                                        setStep2RepairStage("");
                                      }}
                                    >
                                      <span className="ocr-flow-choice-title">はい / 修正済み</span>
                                      <span className="ocr-flow-choice-note">
                                        {workflowCandidateEvidenceRunId ? "今のシートを維持して進む" : "そのまま明細へ反映する"}
                                      </span>
                                    </button>
                                    <button
                                      className={`ocr-flow-choice-button ${step2WizardChoice === "no" ? "active" : ""}`}
                                      type="button"
                                      onClick={() => {
                                        setStep2WizardChoice("no");
                                        activateStep2RepairStage(step2SuggestedRepairStage);
                                      }}
                                    >
                                      <span className="ocr-flow-choice-title">いいえ / 迷う</span>
                                      <span className="ocr-flow-choice-note">次の手段を選んで整える</span>
                                    </button>
                                    <div className="ocr-flow-choice-select-card">
                                      <label htmlFor="expanded-cell-copy-mode" className="ocr-flow-choice-title">
                                        拡大セル
                                      </label>
                                      <select
                                        id="expanded-cell-copy-mode"
                                        className="input"
                                        value={expandedCellCopyMode}
                                        onChange={(event) => void handleExpandedCellCopyModeChange(event.target.value as ExpandedCellCopyMode)}
                                        disabled={expandedCellCopySaving || !facility.trim()}
                                      >
                                        <option value="disabled">無効</option>
                                        <option value="enabled">有効にする</option>
                                        <option value="persisted">この施設で永続化して有効化する</option>
                                      </select>
                                      <p className="ocr-flow-choice-note">
                                        同じ日付・区分で数字が1つだけあるとき、同じ区分の空欄へコピーします。
                                      </p>
                                    </div>
                                  </div>
                                </div>
                              </div>
                              {step2FinishVisible ? (
                                <section className="ocr-flow-finish-card">
                                  <div className="ocr-flow-finish-copy">
                                    <p className="ocr-flow-branch-label">修正完了</p>
                                    <h4>保存して明細に反映</h4>
                                    <p className="subtle">{step2FinishActionNote}</p>
                                  </div>
                                  <div className="ocr-flow-branch-actions">
                                    <button
                                      className={applySheetButtonClassName}
                                      type="button"
                                      onClick={() => void completeStep2AndMoveToDetails()}
                                      disabled={!canAttemptApplyOcrSheet || !ocrHasEditableSheet || ocrProcessingNow}
                                    >
                                      {ocrTableSaving ? "保存中..." : STEP2_APPLY_NEXT_LABEL}
                                    </button>
                                    <button
                                      className="btn ghost"
                                      type="button"
                                      onClick={() => setStep2WizardChoice("")}
                                    >
                                      選び直す
                                    </button>
                                  </div>
                                </section>
                              ) : null}
                            </div>
                            {step2WizardChoice === "no" ? (
                              <section className={`ocr-flow-panel ocr-flow-panel--repair ${ocrProcessingNow ? "is-processing" : ""}`}>
                                <div className="ocr-flow-panel-header">
                                  <div>
                                    <p className="ocr-flow-branch-label">選択 2</p>
                                    <h4>どう直す？</h4>
                                    <p className="subtle">
                                      {ocrProcessingNow
                                        ? describeReparseProgressMessage(effectiveProcessingStage, {
                                            llmAssist: true,
                                            providerLabel: llmReparseProvider,
                                          }) || "いま再解析中です。完了後にもう一度シートを確認してください。"
                                        : "必要な手段だけを1つずつ選んで進めます。"}
                                    </p>
                                  </div>
                                  <button
                                    className="btn ghost"
                                    type="button"
                                    onClick={() => {
                                      setStep2WizardChoice("");
                                      setStep2RepairStage("");
                                    }}
                                  >
                                    戻る
                                  </button>
                                </div>
                                {ocrProcessingNow ? (
                                  <div className="ocr-processing-banner">
                                    <strong>いまは OCR パイプラインの完了待ちです。</strong>
                                    <span>完了までは「シートを保存（暫定）」以外の操作は不要です。</span>
                                  </div>
                                ) : null}
                                <div className="ocr-flow-subchoices">
                                  <button
                                    className={`ocr-flow-subchoice ${activeStep2RepairStage === "foundation" ? "active" : ""}`}
                                    type="button"
                                    onClick={() => activateStep2RepairStage("foundation")}
                                  >
                                    OCRを立て直す
                                  </button>
                                  <button
                                    className={`ocr-flow-subchoice ${activeStep2RepairStage === "candidate" ? "active" : ""}`}
                                    type="button"
                                    onClick={() => activateStep2RepairStage("candidate")}
                                  >
                                    新しい結果を選ぶ
                                  </button>
                                  {mergedCellLlmRecommended && ocrHasEditableSheet ? (
                                    <button
                                      className={`ocr-flow-subchoice ${activeStep2RepairStage === "merged" ? "active" : ""}`}
                                      type="button"
                                      onClick={() => activateStep2RepairStage("merged")}
                                    >
                                      結合セルをAIで読む
                                    </button>
                                  ) : null}
                                  <button
                                    className={`ocr-flow-subchoice ${activeStep2RepairStage === "llm" ? "active" : ""}`}
                                    type="button"
                                    onClick={() => activateStep2RepairStage("llm")}
                                  >
                                    AIに任せる
                                  </button>
                                </div>
                                {activeStep2RepairStage === "foundation" ? (
                                  <section className="ocr-remediation-group">
                                    <p className="ocr-remediation-group-label">基盤</p>
                                    <h5>OCRを立て直す</h5>
                                    <p className="subtle">
                                      {ocrProcessingNow
                                        ? "現在のシートを残しつつ OCR を立て直しています。完了後に次の結果確認へ進みます。"
                                        : "数量が怪しい時は、まず現在のシートを残しつつ OCR を立て直します。"}
                                    </p>
                                    <div className="ocr-flow-branch-actions">
                                      <button
                                        className={saveSheetButtonClassName}
                                        type="button"
                                        onClick={() => void saveOcrSheetExact()}
                                        disabled={ocrTableSaving || !canSaveDraftSheet}
                                      >
                                        {ocrTableSaving ? "保存中..." : "シートを保存（暫定）"}
                                      </button>
                                      {showOcrPipelineRerunAction ? (
                                        <button
                                          className={semanticShellOnly || showNewEvidenceChoice ? "btn primary" : "btn ghost"}
                                          type="button"
                                          onClick={() => void rerunOcrPipeline()}
                                          disabled={reparsePending || rerunInProgressState || step1Incomplete || ocrRecoverPending}
                                        >
                                          {reparsePending || rerunInProgressState ? "再実行中..." : "OCRパイプラインを再実行"}
                                        </button>
                                      ) : null}
                                      {showOcrRecoveryAction || ocrHardRecoveryMode || semanticShellOnly ? (
                                        <button
                                          className="btn"
                                          type="button"
                                          onClick={() => void recoverOcrFoundation()}
                                          disabled={ocrRecoverPending || step1Incomplete || ocrProcessingNow}
                                        >
                                          {ocrRecoverPending ? "復旧中..." : "OCR基盤を復旧"}
                                        </button>
                                      ) : null}
                                      <button
                                        className="btn ghost"
                                        type="button"
                                        onClick={() => void forceWeeklyMenuOverwrite()}
                                        disabled={
                                          forcedSheetRecoveryPending !== ""
                                          || reparsePending
                                          || rerunInProgressState
                                          || step1Incomplete
                                          || ocrRecoverPending
                                          || ocrProcessingNow
                                        }
                                      >
                                        {forcedSheetRecoveryPending === "weekly"
                                          ? "週次で復元中..."
                                          : "週次メニューで日付・区分・メニューを強制復元"}
                                      </button>
                                      <button
                                        className="btn ghost"
                                        type="button"
                                        onClick={() => void forceFacilitySchemaOverwrite()}
                                        disabled={
                                          forcedSheetRecoveryPending !== ""
                                          || reparsePending
                                          || rerunInProgressState
                                          || step1Incomplete
                                          || ocrRecoverPending
                                          || ocrProcessingNow
                                        }
                                      >
                                        {forcedSheetRecoveryPending === "facility"
                                          ? "施設設定で復元中..."
                                          : "施設設定の列構成で強制復元（数量は空白）"}
                                      </button>
                                    </div>
                                  </section>
                                ) : null}
                                {activeStep2RepairStage === "candidate" ? (
                                  <section className="ocr-remediation-group">
                                    <p className="ocr-remediation-group-label">候補</p>
                                    <h5>新しい結果を選ぶ</h5>
                                    <p className="subtle">
                                      {ocrProcessingNow
                                        ? "完了後に新しい OCR 結果や解釈候補があれば、ここに表示されます。"
                                        : "新しい OCR 結果や複数候補がある時だけ、ここで選んでから修正を続けます。"}
                                    </p>
                                    {showNewEvidenceChoice ? (
                                      <div className="ocr-evidence-switch-card">
                                        <p className="ocr-evidence-switch-title">新しいOCR候補があります</p>
                                        <p className="subtle">
                                          現在のシートは維持したままです。切り替えると、最新の OCR 結果から下書きを作り直します。
                                        </p>
                                        <div className="ocr-flow-branch-actions">
                                          <button
                                            className="btn primary"
                                            type="button"
                                            onClick={() => void switchDraftToLatestEvidence()}
                                            disabled={switchEvidencePending || reparsePending || rerunInProgressState || ocrRecoverPending}
                                          >
                                            {switchEvidencePending ? "切替中..." : "新しいOCR候補に切り替える"}
                                          </button>
                                          <button
                                            className="btn ghost"
                                            type="button"
                                            onClick={() => void keepCurrentDraft()}
                                            disabled={switchEvidencePending || keepCurrentPending}
                                          >
                                            {keepCurrentPending ? "維持を記録中..." : "今のシートを維持"}
                                          </button>
                                        </div>
                                        {candidateSheetPreviewLoading ? (
                                          <p className="subtle ocr-remediation-empty">候補シートを取得中...</p>
                                        ) : (
                                          renderReadonlySheetPreview(candidateSheetPreview, {
                                            title: "候補シートのプレビュー",
                                            note: "切り替える前に、最新 OCR 候補のシート内容をここで確認できます。",
                                            emptyMessage: candidateSheetPreviewMessage,
                                          })
                                        )}
                                      </div>
                                    ) : null}
                                    {renderCriticalDecisionPanel(step2CriticalDecisions, {
                                      title: "OCR修正前に候補を確定",
                                      note: "列やテンプレート解釈が競合したときだけ表示されます。ここで選ぶと、下のシート確認と反映にそのまま使います。",
                                    }) || (
                                      <p className="subtle ocr-remediation-empty">
                                        {ocrProcessingNow
                                          ? "完了後に候補が必要なら、ここに選択肢が表示されます。"
                                          : "現在、追加で選ぶ OCR 候補はありません。"}
                                      </p>
                                    )}
                                  </section>
                                ) : null}
                                {activeStep2RepairStage === "llm" || activeStep2RepairStage === "merged" ? (
                                  <section className="ocr-remediation-group ocr-remediation-group--llm">
                                    <p className="ocr-remediation-group-label">
                                      {activeStep2RepairStage === "merged" ? "結合セル" : "LLM"}
                                    </p>
                                    <h5>
                                      {activeStep2RepairStage === "merged"
                                        ? "結合セルの数量を Gemini Pro で推論する"
                                        : "どう補完するかを選ぶ"}
                                    </h5>
                                    <p className="subtle">
                                      {activeStep2RepairStage === "merged"
                                        ? "結合セルで複数行にまたがる数量を、Gemini Pro で span ごとに推論します。日付・区分の境界は維持し、結合された数字だけを展開します。"
                                        : ocrProcessingNow
                                          ? "OCR 完了後にだけ使います。完了したら、どの補完方針で見るかをここで選べます。"
                                          : "基盤や候補が固まってから、必要な時だけ LLM 補完再解析を使います。"}
                                    </p>
                                    <div className="ocr-flow-branch-actions">
                                      {activeStep2RepairStage === "merged" ? (
                                        <span className="ocr-review-pill">方針: 結合セルまたがり数量</span>
                                      ) : (
                                        <select
                                          className="input llm-model-select"
                                          value={llmReparsePromptPreset}
                                          onChange={(event) =>
                                            setLlmReparsePromptPreset(event.target.value as LlmPromptPreset)
                                          }
                                          disabled={reparsePending || step1Incomplete || ocrRecoverPending || ocrHardRecoveryMode || !ocrHasEditableSheet}
                                        >
                                          <option value="numeric_verification">数字検証優先</option>
                                          <option value="column_missing">列欠損・見切れ補完</option>
                                          <option value="row_alignment">行ずれ・区分ずれ補正</option>
                                          <option value="special_diet_semantics">特殊食・禁食優先</option>
                                          <option value="merged_cell_quantity_spans">結合セルまたがり数量</option>
                                          <option value="freeform">自由入力中心</option>
                                        </select>
                                      )}
                                      <select
                                        className="input llm-provider-select"
                                        value={llmReparseProvider}
                                        onChange={(event) => {
                                          const nextProvider = event.target.value;
                                          setLlmReparseProvider(nextProvider);
                                          if (nextProvider !== "gemini") {
                                            setLlmReparseModelMode("flash");
                                            setLlmReparseCustomModel("");
                                          }
                                        }}
                                        disabled={reparsePending || step1Incomplete || ocrRecoverPending || ocrHardRecoveryMode || !ocrHasEditableSheet}
                                      >
                                        <option value="openai">OpenAI</option>
                                        <option value="gemini">Gemini</option>
                                      </select>
                                      {llmReparseProvider === "gemini" ? (
                                        <select
                                          className="input llm-model-select"
                                          value={llmReparseModelMode}
                                          onChange={(event) =>
                                            setLlmReparseModelMode(event.target.value as "flash" | "pro" | "other")
                                          }
                                          disabled={reparsePending || step1Incomplete || ocrRecoverPending || ocrHardRecoveryMode || !ocrHasEditableSheet}
                                        >
                                          <option value="flash">Flash</option>
                                          <option value="pro">Pro</option>
                                          <option value="other">Other</option>
                                        </select>
                                      ) : null}
                                      {llmReparseProvider === "gemini" && llmReparseModelMode === "other" ? (
                                        <input
                                          className="input llm-model-input"
                                          type="text"
                                          placeholder="例: gemini-1.5-flash"
                                          value={llmReparseCustomModel}
                                          onChange={(event) => setLlmReparseCustomModel(event.target.value)}
                                          disabled={
                                            reparsePending ||
                                            step1Incomplete ||
                                            ocrRecoverPending ||
                                            ocrHardRecoveryMode ||
                                            !ocrHasEditableSheet
                                          }
                                        />
                                      ) : null}
                                      <button
                                        className="btn ghost"
                                        type="button"
                                        onClick={() => loadOcrSheet()}
                                        disabled={ocrSheetLoading || step1Incomplete || ocrHardRecoveryMode}
                                      >
                                        {ocrSheetLoading ? "再読込中..." : "シート再読込"}
                                      </button>
                                      <button
                                        className="btn ghost"
                                        onClick={() => void reparse({ ocrProvider: llmReparseProvider, llmAssist: true })}
                                        disabled={
                                          reparsePending ||
                                          step1Incomplete ||
                                          ocrRecoverPending ||
                                          ocrHardRecoveryMode ||
                                          !ocrHasEditableSheet ||
                                          (llmReparseProvider === "gemini" &&
                                            llmReparseModelMode === "other" &&
                                            !llmReparseCustomModel.trim())
                                        }
                                      >
                                        {reparsePending ? "再解析中..." : "LLM補完再解析"}
                                      </button>
                                    </div>
                                    {order.ocr_prompt_enabled === false ? null : llmReparsePromptPreset === "freeform" ? (
                                      <div className="ocr-inline-prompt">
                                        <label className="input-label" htmlFor="llm-freeform-prompt">
                                          LLM追加指示
                                        </label>
                                        <p className="subtle">
                                          自由入力中心を選んでいるため、この内容を追加指示としてそのまま渡します。
                                        </p>
                                        <textarea
                                          id="llm-freeform-prompt"
                                          className="input ocr-llm-prompt-textarea"
                                          rows={12}
                                          value={ocrPrompt}
                                          onChange={(e) => setOcrPrompt(e.target.value)}
                                          placeholder="例: 読みづらい手書き数量は前後セルの連続性を見て補完する"
                                        />
                                      </div>
                                    ) : (
                                      <details className="ocr-inline-details">
                                        <summary>LLM追加指示（任意）</summary>
                                        <p className="subtle">
                                          選んだプリセットに加えて、ここに書いた内容だけを追加指示として渡します。
                                        </p>
                                        <textarea
                                          className="input ocr-llm-prompt-textarea"
                                          rows={10}
                                          value={ocrPrompt}
                                          onChange={(e) => setOcrPrompt(e.target.value)}
                                          placeholder="例: 読みづらい手書き数量は前後セルの連続性を見て補完する"
                                        />
                                      </details>
                                    )}
                                  </section>
                                ) : null}
                              </section>
                            ) : null}
                          </div>
                          {reviewBlockerText || reviewWarningText || ocrReparseBlockedHint || ocrHardRecoveryMode ? (
                            <div className="warning-banner warning-banner--compact">
                              <p>
                                {ocrHardRecoveryMode
                                  ? "オーバーレイが取得できないため、先に基盤の復旧を完了してから反映を進めてください。"
                                  : "気になる点があるため、迷ったら先に「シートを保存（暫定）」で下書きを残してください。"}
                              </p>
                              <ul className="critical-alert-list">
                                {reviewBlockerText ? <li>{reviewBlockerText}</li> : null}
                                {reviewWarningText ? <li>{reviewWarningText}</li> : null}
                                {ocrReparseBlockedHint ? <li>{ocrReparseBlockedHint}</li> : null}
                              </ul>
                              {!step1Incomplete ? (
                                <div className="ocr-flow-branch-actions">
                                  <button
                                    className="btn primary"
                                    type="button"
                                    onClick={() => void rerunOcrPipeline()}
                                    disabled={reparsePending || rerunInProgressState || ocrRecoverPending}
                                  >
                                    {reparsePending || rerunInProgressState ? "再実行中..." : "OCRパイプラインを再実行"}
                                  </button>
                                  {(showOcrRecoveryAction || ocrHardRecoveryMode) ? (
                                    <button
                                      className="btn"
                                      type="button"
                                      onClick={() => void recoverOcrFoundation()}
                                      disabled={ocrRecoverPending || ocrProcessingNow}
                                    >
                                      {ocrRecoverPending ? "復旧中..." : "OCR基盤を復旧"}
                                    </button>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                        <details className="ocr-review-details ocr-secondary-tools">
                          <summary>補助操作と内部情報</summary>
                          <div className="ocr-secondary-tools-body">
                            <div className="ocr-edit-actions">
                              <button
                                className="btn ghost"
                                type="button"
                                onClick={() => loadOcrHistory()}
                                disabled={ocrHistoryLoading || ocrHardRecoveryMode}
                              >
                                {ocrHistoryLoading ? "履歴取得中..." : "履歴更新"}
                              </button>
                              {!ocrHardRecoveryMode ? (
                                <button
                                  className="btn ghost"
                                  type="button"
                                  onClick={addOcrTableRow}
                                  disabled={ocrTableSaving || !ocrHasEditableSheet}
                                >
                                  行を追加
                                </button>
                              ) : null}
                            </div>
                            {!ocrHardRecoveryMode ? (
                              <div className="ocr-shift-toolbar">
                                <span className="ocr-shift-label">数量列の範囲シフト</span>
                                <label className="ocr-shift-field">
                                  <span>開始行</span>
                                  <input
                                    className="input"
                                    inputMode="numeric"
                                    value={ocrShiftStartRow}
                                    onChange={(e) => setOcrShiftStartRow(e.target.value.replace(/[^\d]/g, ""))}
                                    placeholder="1"
                                  />
                                </label>
                                <label className="ocr-shift-field">
                                  <span>終了行</span>
                                  <input
                                    className="input"
                                    inputMode="numeric"
                                    value={ocrShiftEndRow}
                                    onChange={(e) => setOcrShiftEndRow(e.target.value.replace(/[^\d]/g, ""))}
                                    placeholder={String(Math.max(ocrSheetRows.length, 1))}
                                  />
                                </label>
                                <button
                                  className="btn ghost"
                                  type="button"
                                  onClick={() => shiftOcrTableRange(-1)}
                                  disabled={!ocrSheetRows.length || ocrTableSaving}
                                >
                                  1行上へ
                                </button>
                                <button
                                  className="btn ghost"
                                  type="button"
                                  onClick={() => shiftOcrTableRange(1)}
                                  disabled={!ocrSheetRows.length || ocrTableSaving}
                                >
                                  1行下へ
                                </button>
                                <span className="subtle">数量列の入替は上のシートツールから操作してください。</span>
                              </div>
                            ) : (
                              <p className="subtle">現在は基盤復旧待ちのため、列シフト/入替操作は停止しています。</p>
                            )}
                            {ocrHistoryMessage ? <p className="subtle">{ocrHistoryMessage}</p> : null}
                            {ocrHistoryLatest ? (
                              <div className="ocr-history-summary">
                                <span>
                                  {latestOcrRevisionActionLabel}: {formatTimestamp(ocrHistoryLatest.edited_at)} / UI: {latestOcrRevisionMode}
                                </span>
                                <span>
                                  行数:{" "}
                                  {typeof ocrHistoryLatest.row_count === "number"
                                    ? ocrHistoryLatest.row_count
                                    : ocrHistoryLatest.rows?.length || 0}
                                </span>
                                <span>差分: {latestOcrRevisionChanged}</span>
                                <span>Revision: {ocrHistoryLatest.revision_id || "-"}</span>
                              </div>
                            ) : null}
                            {recentOcrHistory.length ? (
                              <details className="ocr-history-list">
                                <summary>反映履歴（最新5件）</summary>
                                <div className="ocr-history-rows">
                                  {recentOcrHistory.map((item, idx) => (
                                    <div className="ocr-history-row" key={`ocr-history-${item.revision_id || idx}`}>
                                      <span>{formatTimestamp(item.edited_at)}</span>
                                      <span>
                                        UI:{" "}
                                        {item.ui_mode === "sheet"
                                          ? "シートUI"
                                          : item.ui_mode === "legacy"
                                            ? "履歴UI"
                                            : item.ui_mode || "-"}
                                      </span>
                                      <span>
                                        行:{" "}
                                        {typeof item.row_count === "number"
                                          ? item.row_count
                                          : item.rows?.length || 0}
                                      </span>
                                      <span>
                                        差分:{" "}
                                        {typeof item.changed === "boolean"
                                          ? item.changed
                                            ? "あり"
                                            : "なし"
                                          : "-"}
                                      </span>
                                      <span>{item.revision_id || "-"}</span>
                                    </div>
                                  ))}
                                </div>
                              </details>
                            ) : null}
                            {ocrTechnicalDetails.length ? (
                              <div className="ocr-review-details-content">
                                {ocrTechnicalDetails.map((item) => (
                                  <span className="ocr-review-pill ocr-review-pill--muted" key={`ocr-review-tech-${item}`}>
                                    {item}
                                  </span>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        </details>
                        {reparseWarningReasons.length ? (
                          <div className="warning-banner">
                            <p>
                              再解析は警告付きでシートに反映しました。警告:{" "}
                              {reparseWarningReasons
                                .map((code) => describeReparseWarningReason(code))
                                .filter(Boolean)
                                .join(" / ")}
                            </p>
                            {reparseWarningDetailText ? (
                              <pre className="raw-output">{reparseWarningDetailText}</pre>
                            ) : null}
                          </div>
                        ) : null}
                        {ocrTableMessage ? <p className="subtle">{ocrTableMessage}</p> : null}
                      </div>
                    </div>
                    <div className="ocr-workspace-preview">
                      <div className="ocr-preview-card">
                        <div className="preview-header">
                          <div className="preview-header-copy">
                            <span className="subtle">{overlayPreviewModeLabel}</span>
                            {activeOcrPageLabel != null ? (
                              <span className="subtle">Page {activeOcrPageLabel}</span>
                            ) : null}
                          </div>
                          <div className="preview-header-tools">
                            {canSwitchPreviewMode ? (
                              <div className="preview-mode-toggle" role="tablist" aria-label="preview source">
                                <button
                                  className={`btn ghost ${ocrPreviewMode === "overlay" ? "active" : ""}`}
                                  type="button"
                                  onClick={() => {
                                    ocrPreviewForcedFallbackRef.current = false;
                                    setOcrPreviewMode("overlay");
                                  }}
                                  aria-pressed={ocrPreviewMode === "overlay"}
                                >
                                  OCR
                                </button>
                                <button
                                  className={`btn ghost ${ocrPreviewMode === "original" ? "active" : ""}`}
                                  type="button"
                                  onClick={() => {
                                    ocrPreviewForcedFallbackRef.current = false;
                                    setOcrPreviewMode("original");
                                  }}
                                  aria-pressed={ocrPreviewMode === "original"}
                                >
                                  原本PDF
                                </button>
                              </div>
                            ) : null}
                            {primaryPreviewOpenUrl ? (
                              <a href={primaryPreviewOpenUrl} target="_blank" rel="noreferrer" className="ghost-link">
                                別タブで開く
                              </a>
                            ) : null}
                          </div>
                        </div>
	                          <div className="edit-hint active">
	                            {isHakodateOverlayMode
	                            ? "左はbackendが生成した箱館方式の数量込みoverlay画像です。frontendは画像を再合成せず、シートカーソルだけを薄く重ねます。"
                            : shouldShowOriginalPdfPreview
                            ? step2FallbackSummary || (showingArtifactPdf
                              ? "OCR生成PDFを見ながら、右のシートだけを更新します。"
                              : "原本PDFを見ながら、右のシートだけを更新します。")
                            : "左は比較用です。編集は右のシートだけを更新します。"}
                        </div>
                        <div className="preview-surface">
                          {isHakodateOverlayMode ? (
                            <div className="hakodate-overlay-preview" data-testid="hakodate-overlay-preview">
                              {hakodatePreviewImageUrl ? (
                                <div
                                  ref={ocrPreviewWrapperRef}
                                  className="ocr-preview-wrapper ocr-preview-wrapper--framed"
                                  data-testid="ocr-preview-wrapper"
                                  data-preview-mode="hakodate-overlay"
                                >
                                  <img
                                    ref={overlayImageRef}
                                    src={hakodatePreviewImageUrl}
                                    alt="Hakodate quantity overlay base"
                                    className="ocr-preview"
                                  />
                                  {hakodateFocusedOverlayHighlights.column ? (
                                    <div
                                      className="ocr-overlay-column-highlight"
                                      data-testid="ocr-overlay-column-highlight"
                                      style={{
                                        left: `${hakodateFocusedOverlayHighlights.column.left}px`,
                                        top: `${hakodateFocusedOverlayHighlights.column.top}px`,
                                        width: `${hakodateFocusedOverlayHighlights.column.width}px`,
                                        height: `${hakodateFocusedOverlayHighlights.column.height}px`,
                                      }}
                                    />
                                  ) : null}
                                  {hakodateFocusedOverlayHighlights.row ? (
                                    <div
                                      className="ocr-overlay-row-highlight"
                                      data-testid="ocr-overlay-row-highlight"
                                      style={{
                                        left: `${hakodateFocusedOverlayHighlights.row.left}px`,
                                        top: `${hakodateFocusedOverlayHighlights.row.top}px`,
                                        width: `${hakodateFocusedOverlayHighlights.row.width}px`,
                                        height: `${hakodateFocusedOverlayHighlights.row.height}px`,
                                      }}
                                    />
                                  ) : null}
                                </div>
                              ) : ocrPagesLoading || hakodateOverlayLoading ? (
                                <div className="preview-placeholder">箱館オーバーレイを取得中...</div>
                              ) : isHakodateOverlayMode ? (
                                <div className="hakodate-overlay-blocked" data-testid="hakodate-overlay-image-missing">
                                  箱館方式のoverlay成果物がありません。yomitoku overlayへの自動フォールバックは停止しています。
                                </div>
                              ) : (
                                <div className="preview-placeholder">箱館オーバーレイを表示できるPDF/画像がありません。</div>
                              )}
                              <div
                                className={`hakodate-overlay-status ${hakodateOverlayHasBlocker ? "blocked" : "ready"}`}
                                data-testid="hakodate-overlay-status"
                              >
                                <div className="hakodate-overlay-status-header">
                                  <strong>{hakodateOverlayHasBlocker ? "箱館オーバーレイ要確認" : "箱館オーバーレイ表示中"}</strong>
                                  <span>
                                    対象セル {hakodateTargetCellCount} / evidence {hakodateEvidenceCount} / 割当 {hakodateAssignedCount} / 表示数量 {hakodateQuantityLabelCount}
                                  </span>
                                </div>
                                <div className="hakodate-job-status" data-testid="hakodate-job-status">
                                  <strong>OCRジョブ</strong>
                                  <span>{hakodateJobStatusLabel}</span>
                                  {hakodateJobDetail ? <span className="subtle">{hakodateJobDetail}</span> : null}
                                </div>
                                {hakodateOverlayBlockerMessages.length ? (
                                  <ul className="hakodate-overlay-blocker-list" data-testid="hakodate-overlay-blockers">
                                    {hakodateOverlayBlockerMessages.map((message) => (
                                      <li key={message}>{message}</li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="subtle">backend生成画像に、推定セル・インク判定・OCR数量を含めています。frontendの追加描画はカーソル表示だけです。</p>
                                )}
                              </div>
                              <div className="hakodate-frame-editor" data-testid="hakodate-frame-editor">
                                <div className="hakodate-frame-editor-header">
                                  <div>
                                    <strong>枠位置合わせ</strong>
                                    <p className="subtle">
                                      施設テンプレートの外枠・列線・行線を調整します。保存後に再計算すると、左の緑枠と右のシートに反映されます。
                                    </p>
                                  </div>
                                  <div className="hakodate-frame-editor-actions">
                                    <button className="btn ghost" type="button" onClick={toggleTableBoxEditor}>
                                      {showTableBoxEditor ? "枠編集を閉じる" : "枠を編集"}
                                    </button>
                                    <button
                                      className="btn ghost"
                                      type="button"
                                      onClick={() => void detectGridEdges()}
                                      disabled={gridDetecting}
                                    >
                                      {gridDetecting ? "自動検出中..." : "自動検出"}
                                    </button>
                                    <button
                                      className="btn primary"
                                      type="button"
                                      onClick={() => void saveTableBox()}
                                      disabled={!showTableBoxEditor || tableBoxSaving || !tableBoxReady}
                                    >
                                      {tableBoxSaving ? "保存中..." : "施設テンプレートに保存"}
                                    </button>
                                    <button
                                      className="btn ghost"
                                      type="button"
                                      onClick={() => {
                                        void (async () => {
                                          await loadOcrSheet({ silent: true, force: true });
                                          await loadHakodateOverlayPreview({ force: true });
                                        })();
                                      }}
                                      disabled={ocrSheetLoading || ocrPagesLoading || hakodateOverlayLoading}
                                    >
                                      再計算して表示
                                    </button>
                                  </div>
                                </div>
                                {showTableBoxEditor ? (
                                  <div className="hakodate-frame-editor-body">
                                    <div className="hakodate-frame-grid">
                                      {["x0", "y0", "x1", "y1"].map((label, idx) => (
                                        <label className="input-label" key={`hakodate-frame-${label}`}>
                                          <span>{label}</span>
                                          <input
                                            className="input"
                                            inputMode="decimal"
                                            value={tableBoxDraft?.[idx]?.toFixed(tableBoxDecimals) ?? ""}
                                            onChange={(event) => updateTableBoxIndex(idx, event.target.value)}
                                          />
                                        </label>
                                      ))}
                                      <label className="input-label">
                                        <span>移動量</span>
                                        <input
                                          className="input"
                                          inputMode="decimal"
                                          value={tableBoxStep}
                                          onChange={(event) => {
                                            const parsed = Number(event.target.value);
                                            if (!Number.isNaN(parsed)) {
                                              setTableBoxStep(parsed);
                                            }
                                          }}
                                        />
                                      </label>
                                    </div>
                                    <div className="hakodate-frame-editor-actions hakodate-frame-editor-actions--wrap">
                                      <button className="btn ghost" type="button" onClick={() => nudgeTableBox(-tableBoxNudge, 0)} disabled={!tableBoxReady}>
                                        左へ
                                      </button>
                                      <button className="btn ghost" type="button" onClick={() => nudgeTableBox(tableBoxNudge, 0)} disabled={!tableBoxReady}>
                                        右へ
                                      </button>
                                      <button className="btn ghost" type="button" onClick={() => nudgeTableBox(0, -tableBoxNudge)} disabled={!tableBoxReady}>
                                        上へ
                                      </button>
                                      <button className="btn ghost" type="button" onClick={() => nudgeTableBox(0, tableBoxNudge)} disabled={!tableBoxReady}>
                                        下へ
                                      </button>
                                      <button className="btn ghost" type="button" onClick={() => expandTableBox(tableBoxNudge)} disabled={!tableBoxReady}>
                                        外へ広げる
                                      </button>
                                      <button className="btn ghost" type="button" onClick={() => expandTableBox(-tableBoxNudge)} disabled={!tableBoxReady}>
                                        内へ縮める
                                      </button>
                                      <button className="btn ghost" type="button" onClick={resetTableBoxDraft}>
                                        取得値に戻す
                                      </button>
                                    </div>
                                    <div className="hakodate-frame-grid hakodate-frame-grid--wide">
                                      <label className="input-label">
                                        <span>列数</span>
                                        <input
                                          className="input"
                                          inputMode="numeric"
                                          placeholder={String(Math.max(overlayColumnCount, 1))}
                                          onChange={(event) => setColumnCount(Number(event.target.value))}
                                        />
                                      </label>
                                      <label className="input-label">
                                        <span>行数</span>
                                        <input
                                          className="input"
                                          inputMode="numeric"
                                          placeholder={String(Math.max(overlayRowCount, 1))}
                                          onChange={(event) => setRowCount(Number(event.target.value))}
                                        />
                                      </label>
                                      <label className="input-label hakodate-frame-wide-field">
                                        <span>列線(%)</span>
                                        <input
                                          className="input"
                                          value={columnEdgesText}
                                          onChange={(event) => {
                                            setColumnEdgesText(event.target.value);
                                            updateColumnEdgesText(event.target.value);
                                          }}
                                          placeholder="例: 24.8, 32.1, 39.4"
                                        />
                                      </label>
                                      <label className="input-label hakodate-frame-wide-field">
                                        <span>行線(%)</span>
                                        <input
                                          className="input"
                                          value={rowEdgesText}
                                          onChange={(event) => {
                                            setRowEdgesText(event.target.value);
                                            updateRowEdgesText(event.target.value);
                                          }}
                                          placeholder="例: 18.5, 20.9, 23.3"
                                        />
                                      </label>
                                    </div>
                                  </div>
                                ) : null}
                                {tableBoxMessage || gridDetectMessage ? (
                                  <p className="subtle">
                                    {[tableBoxMessage, gridDetectMessage].filter(Boolean).join(" / ")}
                                  </p>
                                ) : null}
                              </div>
                            </div>
                          ) : shouldShowOriginalPdfPreview ? (
                            canHighlightOriginalPreview ? (
                              <div
                                ref={ocrPreviewWrapperRef}
                                className="ocr-preview-wrapper ocr-preview-wrapper--framed"
                                data-testid="ocr-preview-wrapper"
                                data-preview-mode="original-image"
                              >
                                <img
                                  ref={overlayImageRef}
                                  src={originalPreviewImageUrl}
                                  alt={shouldFallbackToRawPdfPreview ? "Original PDF preview fallback" : "Original PDF preview"}
                                  className="ocr-preview"
                                />
                                {focusedOverlayHighlight ? (
                                  <div
                                    className="ocr-overlay-row-highlight"
                                    data-testid="ocr-overlay-row-highlight"
                                    data-overlay-page={focusedOverlayHighlight.pageIndex ?? focusedOverlayHighlight.pageArrayIndex + 1}
                                    data-overlay-row={focusedOverlayHighlight.localRowIndex + 1}
                                    data-match-reason={focusedOverlayHighlight.matchReason}
                                    style={{
                                      left: `${focusedOverlayHighlight.left}px`,
                                      top: `${focusedOverlayHighlight.top}px`,
                                      width: `${focusedOverlayHighlight.width}px`,
                                      height: `${focusedOverlayHighlight.height}px`,
                                    }}
                                  />
                                ) : null}
                                {focusedOverlayMarker ? (
                                  <div
                                    className="ocr-overlay-row-marker"
                                    data-testid="ocr-overlay-row-marker"
                                    data-overlay-page={focusedOverlayMarker.pageIndex ?? focusedOverlayMarker.pageArrayIndex + 1}
                                    data-overlay-row={focusedOverlayMarker.localRowIndex + 1}
                                    data-marker-side={focusedOverlayMarker.side}
                                    data-match-reason={focusedOverlayMarker.matchReason}
                                    style={{
                                      left: `${focusedOverlayMarker.left}px`,
                                      top: `${focusedOverlayMarker.top}px`,
                                    }}
                                  />
                                ) : null}
                              </div>
                            ) : pdfUrl ? (
                              <iframe
                                title={shouldFallbackToRawPdfPreview ? "order-pdf-fallback" : "order-pdf-original"}
                                src={pdfUrl}
                                className="pdf-frame pdf-frame-compact preview-frame"
                              />
                            ) : (
                              <div className="preview-placeholder">{pdfError || "PDFを読み込み中..."}</div>
                            )
                          ) : showOcrOverlay ? (
                            <div
                              ref={ocrPreviewWrapperRef}
                              className="ocr-preview-wrapper ocr-preview-wrapper--framed"
                              data-testid="ocr-preview-wrapper"
                            >
                              <img
                                ref={overlayImageRef}
                                src={ocrOverlayUrl || ""}
                                alt="OCR overlay"
                                className="ocr-preview"
                                onError={() => {
                                  setOcrOverlayError(true);
                                  if (!ocrOverlayRetry && !ocrPagesLoading) {
                                    setOcrOverlayRetry(true);
                                    loadOcrPages();
                                  }
                                }}
                              />
                              {focusedOverlayHighlight ? (
                                <div
                                  className="ocr-overlay-row-highlight"
                                  data-testid="ocr-overlay-row-highlight"
                                  data-overlay-page={focusedOverlayHighlight.pageIndex ?? focusedOverlayHighlight.pageArrayIndex + 1}
                                  data-overlay-row={focusedOverlayHighlight.localRowIndex + 1}
                                  data-match-reason={focusedOverlayHighlight.matchReason}
                                  style={{
                                    left: `${focusedOverlayHighlight.left}px`,
                                    top: `${focusedOverlayHighlight.top}px`,
                                    width: `${focusedOverlayHighlight.width}px`,
                                    height: `${focusedOverlayHighlight.height}px`,
                                  }}
                                />
                              ) : null}
                              {focusedOverlayMarker ? (
                                <div
                                  className="ocr-overlay-row-marker"
                                  data-testid="ocr-overlay-row-marker"
                                  data-overlay-page={focusedOverlayMarker.pageIndex ?? focusedOverlayMarker.pageArrayIndex + 1}
                                  data-overlay-row={focusedOverlayMarker.localRowIndex + 1}
                                  data-marker-side={focusedOverlayMarker.side}
                                  data-match-reason={focusedOverlayMarker.matchReason}
                                  style={{
                                    left: `${focusedOverlayMarker.left}px`,
                                    top: `${focusedOverlayMarker.top}px`,
                                  }}
                                />
                              ) : null}
                            </div>
                          ) : (
                            <div className="preview-placeholder">
                              <div>{ocrPagesLoading ? "OCRページを取得中..." : ocrOverlayPlaceholder}</div>
                              <button
                                className="btn ghost"
                                type="button"
                                onClick={() => void loadOcrPages({ force: true })}
                              >
                                OCR表示を再取得
                              </button>
                            </div>
                          )}
                        </div>
                        {!isHakodateOverlayMode && !shouldShowOriginalPdfPreview && usingSyntheticOverlay ? (
                          <p className="subtle">
                            OCR overlay artifact が無いため、PDFレンダリング画像を比較表示しています。
                          </p>
                        ) : null}
                        {!isHakodateOverlayMode && !shouldShowOriginalPdfPreview ? (
                          <div className="layout-toggle">
                            <button
                              className="btn ghost"
                              type="button"
                              onClick={() => setShowLayoutOverlay((prev) => !prev)}
                              disabled={!canToggleLayoutOverlay}
                            >
                              {showLayoutOverlay ? "レイアウトを閉じる" : "レイアウトを表示"}
                            </button>
                            <span className="subtle">
                              {canToggleLayoutOverlay ? "レイアウトオーバーレイ" : layoutOverlayPlaceholder}
                            </span>
                          </div>
                        ) : null}
                        {!isHakodateOverlayMode && !shouldShowOriginalPdfPreview && showLayoutOverlay ? (
                          showLayoutOverlayImage ? (
                            <div className="preview-surface preview-surface--secondary">
                              <div className="ocr-preview-wrapper ocr-preview-wrapper--framed">
                                <img
                                  src={layoutOverlayUrl || ""}
                                  alt="Layout overlay"
                                  className="ocr-preview"
                                  onError={() => {
                                    setLayoutOverlayError(true);
                                    if (!layoutOverlayRetry && !ocrPagesLoading) {
                                      setLayoutOverlayRetry(true);
                                      loadOcrPages();
                                    }
                                  }}
                                />
                              </div>
                            </div>
                          ) : (
                            <div className="preview-placeholder">{layoutOverlayPlaceholder}</div>
                          )
                        ) : null}
                      </div>
                    </div>
                    <div className="ocr-workspace-editor">
                      <div className="ocr-edit ocr-edit--sheet">
                        <div className="ocr-sheet-save-banner">
                          <div>
                            <p className="ocr-flow-branch-label">シート保存</p>
                            <h4>右のシートを直したら、ここで保存</h4>
                            <p className="subtle">
                              分岐の選択に関係なく、修正した内容をこの場で下書き保存できます。
                            </p>
                          </div>
                          <button
                            className="btn primary"
                            type="button"
                            onClick={() => void saveOcrSheetExact()}
                            disabled={ocrTableSaving || !canSaveDraftSheet}
                          >
                            {ocrTableSaving ? "保存中..." : "シートを保存（暫定）"}
                          </button>
                        </div>
	                        {activeEditorRows.length ? (
	                          <>
		                            <div className="ocr-sheet-toolbar">
		                              <div className="ocr-sheet-toolbar-actions">
		                                <button
		                                  className="btn ghost"
		                                  type="button"
		                                  onClick={() => selectAllOcrSheetCells()}
		                                  disabled={!activeEditorRows.length}
		                                >
		                                  全選択
		                                </button>
		                                <button
		                                  className="btn ghost"
		                                  type="button"
		                                  onClick={() => void copyOcrSheetSelection()}
	                                  disabled={!ocrSheetSelectionBounds}
	                                >
	                                  コピー
	                                </button>
	                                <button
	                                  className="btn ghost"
	                                  type="button"
	                                  onClick={() => void cutOcrSheetSelection()}
	                                  disabled={!ocrSheetSelectionBounds}
	                                >
	                                  切り取り
	                                </button>
	                                <button
	                                  className="btn ghost"
	                                  type="button"
	                                  onClick={() => void pasteOcrSheetSelection()}
	                                  disabled={!focusedSheetCell || !ocrSheetClipboardReady}
	                                >
	                                  貼り付け
	                                </button>
		                                <button
		                                  className="btn ghost"
		                                  type="button"
		                                  onClick={() => fillOcrSheetSelectionDown()}
		                                  disabled={!ocrSheetSelectionBounds || ocrSheetSelectionBounds.rowCount < 2}
		                                >
		                                  下へコピー
		                                </button>
		                                <button
		                                  className="btn ghost"
		                                  type="button"
		                                  onClick={() => fillOcrSheetSelectionRight()}
		                                  disabled={!ocrSheetSelectionBounds || ocrSheetSelectionBounds.cellCount < 2}
		                                >
		                                  右へコピー
		                                </button>
		                                <button
		                                  className="btn ghost"
		                                  type="button"
		                                  onClick={() => clearOcrSheetSelectionContents()}
		                                  disabled={!ocrSheetSelectionBounds}
		                                >
		                                  クリア
		                                </button>
		                              </div>
		                              <p className="subtle">
		                                Tab/Shift+Tab: 上下移動 / Enter: 上下移動 / Shift+矢印: 範囲選択 / Ctrl/Cmd+A: 全選択 / Ctrl/Cmd+D: 下へコピー / Ctrl/Cmd+R: 右へコピー / Delete: クリア
		                              </p>
		                            </div>
                                {!ocrHardRecoveryMode ? (
                                  <div className="ocr-shift-toolbar ocr-shift-toolbar--visible">
                                    <span className="ocr-shift-label">数量列の入替</span>
                                    <label className="ocr-shift-field">
                                      <span>入替元数量列</span>
                                      <select
                                        aria-label="入替元数量列"
                                        className="input"
                                        ref={ocrSwapLeftColumnRef}
                                        value={ocrSwapLeftColumn}
                                        onChange={(e) => setOcrSwapLeftColumn(e.target.value)}
                                        disabled={ocrSheetQuantityColumnOptions.length < 2 || ocrTableSaving}
                                      >
                                        <option value="">数量列</option>
                                        {ocrSheetQuantityColumnOptions.map((option) => (
                                          <option key={`ocr-qty-swap-left-${option.value}`} value={option.value}>
                                            {option.label}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <label className="ocr-shift-field">
                                      <span>入替先数量列</span>
                                      <select
                                        aria-label="入替先数量列"
                                        className="input"
                                        ref={ocrSwapRightColumnRef}
                                        value={ocrSwapRightColumn}
                                        onChange={(e) => setOcrSwapRightColumn(e.target.value)}
                                        disabled={ocrSheetQuantityColumnOptions.length < 2 || ocrTableSaving}
                                      >
                                        <option value="">数量列</option>
                                        {ocrSheetQuantityColumnOptions.map((option) => (
                                          <option key={`ocr-qty-swap-right-${option.value}`} value={option.value}>
                                            {option.label}
                                          </option>
                                        ))}
                                      </select>
                                    </label>
                                    <button
                                      className="btn ghost"
                                      type="button"
                                      onClick={applySelectedOcrSheetColumnSwap}
                                      disabled={ocrSheetQuantityColumnOptions.length < 2 || ocrTableSaving}
                                    >
                                      数量列を入替
                                    </button>
                                    <span className="subtle">
                                      {ocrSheetQuantityColumnOptions.length >= 2
                                        ? "数量列の数字だけを入れ替えます。列名と列意味は固定です。"
                                        : "数量列が2つ以上あるときに使えます。"}
                                    </span>
                                  </div>
                                ) : null}
                                <div className="ocr-column-fill-toolbar">
                                  <span className="ocr-shift-label">数量列一括入力</span>
                                  <label className="ocr-shift-field">
                                    <span>対象列</span>
                                    <select
                                      aria-label="数量列一括入力の対象列"
                                      className="input"
                                      value={ocrSheetColumnFillTarget}
                                      onChange={(e) => setOcrSheetColumnFillTarget(e.target.value)}
                                      disabled={!ocrSheetBulkFillColumnOptions.length || ocrTableSaving}
                                    >
                                      <option value="">数量列</option>
                                      {ocrSheetBulkFillColumnOptions.map((option) => (
                                        <option key={`ocr-qty-fill-${option.value}`} value={option.value}>
                                          {option.label}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label className="ocr-shift-field">
                                    <span>入力値</span>
                                    <input
                                      aria-label="数量列一括入力の値"
                                      className="input"
                                      inputMode="numeric"
                                      value={ocrSheetColumnFillValue}
                                      onChange={(e) => setOcrSheetColumnFillValue(e.target.value)}
                                      onKeyDown={(event) => {
                                        if (event.key !== "Enter") return;
                                        event.preventDefault();
                                        applySelectedOcrSheetColumnFill();
                                      }}
                                      disabled={!ocrSheetBulkFillColumnOptions.length || ocrTableSaving}
                                      placeholder="数字"
                                    />
                                  </label>
                                  <button
                                    className="btn ghost"
                                    type="button"
                                    onClick={applySelectedOcrSheetColumnFill}
                                    disabled={
                                      !ocrSheetBulkFillColumnOptions.length
                                      || !ocrSheetColumnFillTarget
                                      || !String(ocrSheetColumnFillValue || "").trim()
                                      || !ocrSheetRows.length
                                      || ocrTableSaving
                                    }
                                  >
                                    列全体へ入力
                                  </button>
                                  <span className="subtle">
                                    数量列の全行を同じ数字で上書きします。既存値が入っていてもそのまま更新します。
                                  </span>
                                </div>
                                <div className="ocr-confidence-toolbar">
                                  <span className="ocr-shift-label">OCR信頼度表示</span>
                                  <label className="ocr-shift-field">
                                    <span>表示閾値</span>
                                    <select
                                      aria-label="OCR信頼度表示閾値"
                                      className="input"
                                      value={ocrConfidenceDisplayMode}
                                      onChange={(e) =>
                                        setOcrConfidenceDisplayMode(e.target.value as OcrConfidenceDisplayMode)}
                                    >
                                      <option value="strict">厳格表示</option>
                                      <option value="assisted">補助表示</option>
                                      <option value="suggestion">提案表示</option>
                                    </select>
                                  </label>
                                  <button
                                    className="btn ghost"
                                    type="button"
                                    onClick={applyVisibleOcrOverlaySuggestions}
                                    disabled={ocrHardRecoveryMode || ocrTableSaving || ocrSheetVisibleOverlayCount < 1}
                                  >
                                    表示中提案を採用
                                  </button>
                                  <span
                                    className="subtle"
                                    data-testid="ocr-sheet-overlay-summary"
                                    data-raw-count={ocrSheetRawNumericCount}
                                    data-accepted-count={ocrSheetAcceptedCount}
                                    data-deterministic-count={ocrSheetDeterministicCandidateCount}
                                    data-weak-count={ocrSheetWeakCandidateCount}
                                    data-unresolved-count={ocrSheetUnresolvedCount}
                                    data-visible-count={ocrSheetVisibleCellCount}
                                    data-visible-overlay-count={ocrSheetVisibleOverlayCount}
                                  >
                                    {ocrSheetRawNumericCount > 0 || ocrSheetAcceptedCount > 0
                                      ? `${ocrSheetConfidenceModeDescription} (${ocrSheetConfidenceLegendText})`
                                      : "OCR由来の confidence/provenance は未表示です。"}
                                  </span>
                                </div>
	                            <div className="ocr-sheet-wrap">
	                              <table className="ocr-sheet-table">
	                              <colgroup>
	                                <col style={{ width: `${OCR_SHEET_ROW_INDEX_WIDTH}px` }} />
	                                {ocrSheetColumnSpecs.map((spec, idx) => (
                                  <col
                                    key={`ocr-sheet-col-${idx}`}
                                    className={spec.className}
                                    style={{ width: `${spec.width}px` }}
                                  />
                                ))}
                                <col style={{ width: "132px" }} />
                              </colgroup>
                              <thead>
                                <tr>
                                  <th className="ocr-sheet-row-index ocr-sheet-sticky-top">#</th>
                                  {ocrSheetHeaders.map((cell, idx) => (
                                    <th
                                      key={`ocr-sheet-header-${idx}`}
                                      className={`ocr-sheet-sticky-top ${ocrSheetColumnSpecs[idx]?.className || ""} ${ocrSheetStickyColumnOffsets[idx] != null ? "ocr-sheet-sticky-left" : ""}`}
                                      style={
                                        ocrSheetStickyColumnOffsets[idx] != null
                                          ? { left: `${ocrSheetStickyColumnOffsets[idx]}px` }
                                          : undefined
                                      }
                                    >
                                      {cell}
                                    </th>
                                  ))}
                                  <th className="ocr-sheet-sticky-top">操作</th>
                                </tr>
                              </thead>
                              <tbody>
                                {ocrSheetRows.map((row, rowIdx) => (
                                  <tr
                                    key={`ocr-sheet-row-${rowIdx}`}
                                    data-row-index={rowIdx + 1}
                                    data-focused={focusedSheetRowIndex === rowIdx ? "true" : "false"}
                                    className={[
                                      "ocr-sheet-row",
                                      ocrSheetRowDateStripeClasses[rowIdx] || "ocr-sheet-row-date-a",
                                      ocrSheetRowBoundaryClasses[rowIdx] || "",
                                      focusedSheetRowIndex === rowIdx ? "ocr-sheet-row-focused" : "",
                                    ]
                                      .filter(Boolean)
                                      .join(" ")}
                                  >
                                    <th
                                      className="ocr-sheet-row-index"
                                      title={activeEditorRowIds[rowIdx] || ""}
                                    >
                                      {rowIdx + 1}
                                    </th>
                                    {ocrSheetHeaders.map((_, cellIdx) => (
                                      (() => {
                                        const selected = isOcrSheetCellWithinSelection(ocrSheetSelection, rowIdx, cellIdx);
                                        const isSelectionAnchor =
                                          ocrSheetSelection?.anchorRowIndex === rowIdx &&
                                          ocrSheetSelection?.anchorCellIndex === cellIdx;
                                        const isDropTarget =
                                          ocrSheetDropTarget?.rowIndex === rowIdx &&
                                          ocrSheetDropTarget?.cellIndex === cellIdx;
                                        const confidenceTier = normalizeOcrCellConfidenceTier(
                                          ocrSheetCellConfidenceRows[rowIdx]?.[cellIdx],
                                        );
                                        const provenance = String(
                                          ocrSheetCellProvenanceRows[rowIdx]?.[cellIdx] || "",
                                        ).trim();
                                        const overlayItem = ocrSheetVisibleOverlayCellMap.get(`${rowIdx}:${cellIdx}`);
                                        const overlayValue =
                                          !String(row[cellIdx] ?? "").trim() && overlayItem
                                            ? String(overlayItem.value ?? "").trim()
                                            : "";
                                        const overlayClassification = normalizeOcrNumericCellClassification(
                                          overlayItem?.classification,
                                        );
                                        const belowConfidenceThreshold =
                                          Boolean(confidenceTier)
                                          && !confidenceTierVisibleInMode(confidenceTier, ocrConfidenceDisplayMode);
                                        const cellTitle = [
                                          confidenceTier ? `confidence: ${confidenceTier}` : "",
                                          provenance ? `provenance: ${provenance}` : "",
                                          overlayClassification ? `overlay: ${overlayClassification}` : "",
                                          overlayItem?.placement_basis
                                            ? `overlay_basis: ${overlayItem.placement_basis}`
                                            : "",
                                        ]
                                          .filter(Boolean)
                                          .join("\n");
                                        return (
                                      <td
                                        key={`ocr-sheet-cell-${rowIdx}-${cellIdx}`}
                                        className={[
                                          ocrSheetColumnSpecs[cellIdx]?.className || "",
                                          ocrSheetStickyColumnOffsets[cellIdx] != null ? "ocr-sheet-sticky-left-cell" : "",
                                          confidenceTier ? `ocr-sheet-cell-confidence-${confidenceTier}` : "",
                                          belowConfidenceThreshold ? "ocr-sheet-cell-below-threshold" : "",
                                          overlayClassification ? `ocr-sheet-cell-overlay-${overlayClassification}` : "",
                                          selected ? "ocr-sheet-cell-selected" : "",
                                          isSelectionAnchor ? "ocr-sheet-cell-anchor" : "",
                                          isDropTarget ? "ocr-sheet-cell-drop-target" : "",
                                        ]
                                          .filter(Boolean)
                                          .join(" ")}
                                        title={cellTitle || undefined}
                                        style={
                                          ocrSheetStickyColumnOffsets[cellIdx] != null
                                            ? { left: `${ocrSheetStickyColumnOffsets[cellIdx]}px` }
                                            : undefined
                                        }
                                        onMouseEnter={() => handleOcrSheetCellMouseEnter(rowIdx, cellIdx)}
                                        onDragOver={(event) => handleOcrSheetSelectionDragOver(event, rowIdx, cellIdx)}
                                        onDrop={(event) => handleOcrSheetSelectionDrop(event, rowIdx, cellIdx)}
                                      >
                                        <div className="ocr-sheet-input-wrap">
                                          {overlayValue ? (
                                            <span
                                              className={`ocr-sheet-input-overlay ocr-sheet-input-overlay-${overlayClassification || "suggestion"}`}
                                              data-testid="ocr-sheet-overlay-value"
                                              data-overlay-classification={overlayClassification || undefined}
                                              data-overlay-row={rowIdx + 1}
                                              data-overlay-col={cellIdx + 1}
                                            >
                                              {overlayValue}
                                            </span>
                                          ) : null}
                                          <input
                                            className={`input ocr-sheet-input ${ocrSheetColumnSpecs[cellIdx]?.className || ""} ${overlayValue ? "ocr-sheet-input-has-overlay" : ""}`}
                                            ref={(element) => {
                                              ocrSheetCellRefs.current[`${rowIdx}:${cellIdx}`] = element;
                                            }}
                                            data-confidence-tier={confidenceTier || undefined}
                                            data-confidence-visible={confidenceTier ? String(!belowConfidenceThreshold) : undefined}
                                            data-provenance={provenance || undefined}
                                            data-overlay-classification={overlayClassification || undefined}
                                            value={row[cellIdx] ?? ""}
                                            draggable={Boolean(ocrSheetSelectionBounds && selected)}
	                                            onMouseDown={(event) => handleOcrSheetCellMouseDown(event, rowIdx, cellIdx)}
	                                            onFocus={() => handleOcrSheetCellFocus(rowIdx, cellIdx)}
	                                            onChange={(e) => updateOcrTableCell(rowIdx, cellIdx, e.target.value)}
	                                            onKeyDown={(event) => handleOcrSheetCellKeyDown(event, rowIdx, cellIdx)}
	                                            onPaste={(event) => void handleOcrSheetCellPaste(event, rowIdx, cellIdx)}
	                                            onDragStart={(event) => handleOcrSheetSelectionDragStart(event, rowIdx, cellIdx)}
	                                            onDragEnd={handleOcrSheetSelectionDragEnd}
	                                          />
                                        </div>
                                      </td>
                                        );
                                      })()
                                    ))}
                                    <td className="ocr-sheet-action-cell">
                                      <div className="ocr-row-actions">
                                        <button
                                          className="btn ghost ocr-row-action-btn"
                                          type="button"
                                          aria-label="提案採用"
                                          title="提案採用"
                                          onFocus={() => focusOcrSheetRow(rowIdx)}
                                          onClick={() => applyVisibleOcrOverlaySuggestionsForRow(rowIdx)}
                                          disabled={
                                            ocrHardRecoveryMode
                                            || ocrTableSaving
                                            || (ocrSheetVisibleOverlayItemsByRow.get(rowIdx)?.length || 0) < 1
                                          }
                                        >
                                          採用
                                        </button>
                                        <button
                                          className="btn ghost ocr-row-action-btn"
                                          type="button"
                                          onFocus={() => focusOcrSheetRow(rowIdx)}
                                          onClick={() => duplicateOcrTableRow(rowIdx)}
                                          disabled={ocrHardRecoveryMode}
                                        >
                                          複製
                                        </button>
                                        <button
                                          className="btn ghost ocr-row-action-btn"
                                          type="button"
                                          onFocus={() => focusOcrSheetRow(rowIdx)}
                                          onClick={() => removeOcrTableRow(rowIdx)}
                                          disabled={ocrHardRecoveryMode}
                                        >
                                          削除
                                        </button>
                                      </div>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
	                              </table>
	                            </div>
	                          </>
	                        ) : showEditorBlockedPanel ? (
                          <div className="warning-banner">
                            <p className="critical-alert-title">編集可能な正解シートをまだ作れません</p>
                            <p className="subtle">
                              OCR ではなく canonical menu を基準にシートを作るため、必要なメニュー設定が無い間はここで止めています。
                            </p>
                            {ocrSheetMessage.trim() ? <p>{ocrSheetMessage}</p> : null}
                            {editorBlockedReasons.length ? (
                              <ul className="critical-alert-list">
                                {editorBlockedReasons.map((reason) => (
                                  <li key={reason}>{reason}</li>
                                ))}
                              </ul>
                            ) : null}
                            {blockedMenuMonthId ? (
                              <div className="ocr-flow-branch-actions">
                                <Link className="btn ghost" href={`/menus/${blockedMenuMonthId}`}>
                                  月次メニューを確認
                                </Link>
                              </div>
                            ) : null}
                          </div>
                        ) : ocrSheetInitialLoadPending ? (
                          <p className="subtle">シートを取得中です。表示されるまでそのまま待ってください。</p>
                        ) : (
                          <p className="subtle">編集できる表がありません。</p>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="facility-template-editor">
                    <div className="table-box-header">
                      <button
                        className="facility-template-toggle"
                        type="button"
                        onClick={() => setShowFacilityTemplateEditor((prev) => !prev)}
                        aria-expanded={showFacilityTemplateEditor}
                      >
                        <span className="facility-template-toggle-title">施設全体の列設定（通常は触らない）</span>
                        <span className="facility-template-toggle-meta">
                          {showFacilityTemplateEditor ? "閉じる" : "開く"}
                        </span>
                      </button>
                    </div>
                    <p className="subtle">
                      この注文だけ数値を直す時は、上のシートだけ編集して保存してください。
                    </p>
                    {showFacilityTemplateEditor ? (
                      <>
                        <div className="facility-template-callout">
                          <p className="facility-template-callout-title">保存すると施設全体に反映されます</p>
                          <p className="subtle">
                            ユーザー2で保存できます。施設区分の列名と数量列定義を、現在選択中の施設のテンプレートへ保存します。
                          </p>
                          <p className="subtle">
                            保存ボタンを押すまでは施設設定は変わりません。1件だけではなく、この施設の今後の注文にも反映されます。
                          </p>
                        </div>
                        <div className="table-box-header">
                          <span className="field-label">施設区分列</span>
                          <div className="facility-template-actions">
                            <select
                              className="input"
                              value={facilityTemplateSwapLeft}
                              onChange={(event) => setFacilityTemplateSwapLeft(event.target.value)}
                              disabled={!facilityTemplateColumnDraft.length}
                            >
                              <option value="">入替元</option>
                              {facilityTemplateColumnDraft.map((column, idx) => (
                                <option key={`facility-template-swap-left-${column.index}-${idx}`} value={String(column.index)}>
                                  {column.index + 1}: {column.header || column.name || column.role}
                                </option>
                              ))}
                            </select>
                            <select
                              className="input"
                              value={facilityTemplateSwapRight}
                              onChange={(event) => setFacilityTemplateSwapRight(event.target.value)}
                              disabled={!facilityTemplateColumnDraft.length}
                            >
                              <option value="">入替先</option>
                              {facilityTemplateColumnDraft.map((column, idx) => (
                                <option key={`facility-template-swap-right-${column.index}-${idx}`} value={String(column.index)}>
                                  {column.index + 1}: {column.header || column.name || column.role}
                                </option>
                              ))}
                            </select>
                            <button
                              className="btn ghost"
                              type="button"
                              onClick={applySelectedFacilityTemplateColumnSwap}
                              disabled={!facilityTemplateColumnDraft.length}
                            >
                              列を入れ替える
                            </button>
                            <button
                              className="btn ghost"
                              type="button"
                              onClick={appendFacilityTemplateColumn}
                              disabled={facilityTemplateSaving || !facility || step1Incomplete}
                            >
                              列を追加
                            </button>
                            <button
                              className="btn primary"
                              type="button"
                              onClick={saveFacilityTemplateColumns}
                              disabled={!facilityTemplateDirty || facilityTemplateSaving || !facility || step1Incomplete}
                            >
                              {facilityTemplateSaving ? "保存中..." : "施設テンプレに保存"}
                            </button>
                          </div>
                        </div>
                        <p className="subtle">
                          表示名・内部名・区分・エリアは基本的に選択式です。既定にないものだけ「個別入力」を使ってください。
                        </p>
                        {!facility ? (
                          <p className="subtle">施設を選択すると施設区分列を編集できます。</p>
                        ) : facilityTemplateColumnDraft.length ? (
                          <div className="facility-template-table-wrap">
                            <table className="ocr-sheet-table facility-template-table">
                              <thead>
                                <tr>
                                  <th>列番号</th>
                                  <th>役割</th>
                                  <th>表示名</th>
                                  <th>内部名</th>
                                  <th>区分</th>
                                  <th>エリア</th>
                                  <th>操作</th>
                                </tr>
                              </thead>
                              <tbody>
                                {facilityTemplateColumnDraft.map((column, idx) => {
                                  const isQuantityColumn = isQuantityRole(column.role);
                                  const headerOptions = buildFacilityTemplateHeaderOptions(
                                    column,
                                    facilityTemplateColumnDraft,
                                    facilityTemplateAreaOptions,
                                  );
                                  const nameOptions = buildFacilityTemplateNameOptions(
                                    column,
                                    facilityTemplateColumnDraft,
                                  );
                                  const headerEditorValue = resolveFacilityTemplateHeaderEditorValue(
                                    column,
                                    headerOptions,
                                  );
                                  const nameEditorValue = resolveFacilityTemplateNameEditorValue(
                                    column,
                                    nameOptions,
                                  );
                                  const dietEditorValue = resolveFacilityTemplateDietEditorValue(column);
                                  const areaEditorValue = resolveFacilityTemplateAreaEditorValue(
                                    column,
                                    facilityTemplateAreaOptions,
                                  );
                                  return (
                                    <tr key={`facility-template-column-${column.index}-${idx}`}>
                                      <td>{column.index + 1}</td>
                                      <td>
                                        <select
                                          className="input"
                                          value={column.role}
                                          onChange={(event) =>
                                            updateFacilityTemplateColumn(idx, "role", event.target.value)
                                          }
                                        >
                                          {columnRoleOptions.map((option) => (
                                            <option key={option.value} value={option.value}>
                                              {option.label}
                                            </option>
                                          ))}
                                        </select>
                                      </td>
                                      <td>
                                        <div className="facility-template-cell-stack">
                                          <select
                                            className="input"
                                            value={headerEditorValue}
                                            onChange={(event) =>
                                              updateFacilityTemplateColumn(
                                                idx,
                                                "header",
                                                event.target.value === facilityTemplateCustomHeaderValue
                                                  ? ""
                                                  : event.target.value,
                                              )
                                            }
                                          >
                                            {headerOptions.map((option) => (
                                              <option key={option.value} value={option.value}>
                                                {option.label}
                                              </option>
                                            ))}
                                            <option value={facilityTemplateCustomHeaderValue}>個別入力</option>
                                          </select>
                                          {headerEditorValue === facilityTemplateCustomHeaderValue ? (
                                            <input
                                              className="input"
                                              value={column.header || ""}
                                              placeholder="個別表示名"
                                              onChange={(event) =>
                                                updateFacilityTemplateColumn(idx, "header", event.target.value)
                                              }
                                            />
                                          ) : null}
                                        </div>
                                      </td>
                                      <td>
                                        <div className="facility-template-cell-stack">
                                          <select
                                            className="input"
                                            value={nameEditorValue}
                                            onChange={(event) =>
                                              updateFacilityTemplateColumn(
                                                idx,
                                                "name",
                                                event.target.value === facilityTemplateCustomNameValue
                                                  ? ""
                                                  : event.target.value,
                                              )
                                            }
                                          >
                                            {nameOptions.map((option) => (
                                              <option key={option.value} value={option.value}>
                                                {option.label}
                                              </option>
                                            ))}
                                            <option value={facilityTemplateCustomNameValue}>個別入力</option>
                                          </select>
                                          {nameEditorValue === facilityTemplateCustomNameValue ? (
                                            <input
                                              className="input"
                                              value={column.name || ""}
                                              placeholder="個別内部名"
                                              onChange={(event) =>
                                                updateFacilityTemplateColumn(idx, "name", event.target.value)
                                              }
                                            />
                                          ) : null}
                                        </div>
                                      </td>
                                      <td>
                                        <div className="facility-template-cell-stack">
                                          <select
                                            className="input"
                                            value={dietEditorValue}
                                            disabled={!isQuantityColumn}
                                            onChange={(event) =>
                                              updateFacilityTemplateColumn(
                                                idx,
                                                "diet_type",
                                                event.target.value === facilityTemplateCustomDietTypeValue
                                                  ? ""
                                                  : event.target.value,
                                              )
                                            }
                                          >
                                            {facilityTemplateDietTypeOptions.map((option) => (
                                              <option key={option.value} value={option.value}>
                                                {option.label}
                                              </option>
                                            ))}
                                            <option value={facilityTemplateCustomDietTypeValue}>個別入力</option>
                                          </select>
                                          {isQuantityColumn && dietEditorValue === facilityTemplateCustomDietTypeValue ? (
                                            <input
                                              className="input"
                                              value={column.diet_type || ""}
                                              placeholder="個別対応コード"
                                              onChange={(event) =>
                                                updateFacilityTemplateColumn(idx, "diet_type", event.target.value)
                                              }
                                            />
                                          ) : null}
                                        </div>
                                      </td>
                                      <td>
                                        <div className="facility-template-cell-stack">
                                          <select
                                            className="input"
                                            value={areaEditorValue}
                                            disabled={!isQuantityColumn}
                                            onChange={(event) =>
                                              updateFacilityTemplateColumn(
                                                idx,
                                                "area_id",
                                                event.target.value === facilityTemplateCustomAreaValue
                                                  ? ""
                                                  : event.target.value,
                                              )
                                            }
                                          >
                                            {facilityTemplateAreaOptions.map((option) => (
                                              <option key={option.value} value={option.value}>
                                                {option.label}
                                              </option>
                                            ))}
                                            <option value={facilityTemplateCustomAreaValue}>個別入力</option>
                                          </select>
                                          {isQuantityColumn && areaEditorValue === facilityTemplateCustomAreaValue ? (
                                            <input
                                              className="input"
                                              value={column.area_id || ""}
                                              placeholder="個別エリア"
                                              onChange={(event) =>
                                                updateFacilityTemplateColumn(idx, "area_id", event.target.value)
                                              }
                                            />
                                          ) : null}
                                        </div>
                                      </td>
                                      <td>
                                        <div className="facility-template-row-actions">
                                          <button
                                            className="btn ghost"
                                            type="button"
                                            onClick={() => applyFacilityTemplateColumnSwap(idx, idx - 1)}
                                            disabled={idx <= 0}
                                          >
                                            前と入替
                                          </button>
                                          <button
                                            className="btn ghost"
                                            type="button"
                                            onClick={() => applyFacilityTemplateColumnSwap(idx, idx + 1)}
                                            disabled={idx >= facilityTemplateColumnDraft.length - 1}
                                          >
                                            次と入替
                                          </button>
                                          <button
                                            className="btn ghost danger"
                                            type="button"
                                            onClick={() => deleteFacilityTemplateColumn(idx)}
                                          >
                                            削除
                                          </button>
                                        </div>
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        ) : (
                          <p className="subtle">施設テンプレートの列定義がありません。</p>
                        )}
                        {facilityTemplateMessage ? <p className="subtle">{facilityTemplateMessage}</p> : null}
                      </>
                    ) : null}
                  </div>
                </section>
          ) : null}

          {activeStepIndex === 2 ? (
            <>
          <section className="panel">
            <header className="panel-header">
              <div>
                <h2>区分別一覧</h2>
                {detailUsesDraftPreview ? (
                  <p className="subtle">
                    最新のシートをもとに区分一覧を表示しています。保存済み明細より新しいため、ここでは draft を優先しています。
                  </p>
                ) : null}
              </div>
            </header>
            {pivotGroups.length === 0 ? (
              <p className="subtle">データなし</p>
            ) : (
              <div className="wrap-grid">
                {pivotGroups.map((group) => {
                  const groupKey = `${group.date}__${group.categoryKey}`;
                  const isOpen = openPivotGroupKey === groupKey;
                  return (
                  <div key={`pivot-${group.date}-${group.categoryKey}`} className="date-group">
                    <button
                      className="date-group-header date-group-toggle"
                      type="button"
                      onClick={() => setOpenPivotGroupKey((prev) => (prev === groupKey ? null : groupKey))}
                    >
                      <span className="date-group-title">{group.date}</span>
                      <span className="group-separator">/</span>
                      <span className="group-tag">{group.categoryLabel}</span>
                      <span className="group-count">{group.rows.length}件</span>
                      <span className="group-toggle-label">{isOpen ? "閉じる" : "開く"}</span>
                    </button>
                    {isOpen ? (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>メニュー</th>
                            <th>時間帯</th>
                            <th>袋</th>
                            <th>数量</th>
                            <th>備考</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map((row, rowIdx) => (
                            <tr key={`pivot-row-${group.date}-${group.categoryKey}-${rowIdx}`}>
                              <td>{row.menu_name}</td>
                              <td>{row.daypart}</td>
                              <td>{formatBagTypeLabel(row.bag_type, bagTypeLabelMap)}</td>
                              <td>{row.quantity}</td>
                              <td>{row.notes.size ? Array.from(row.notes).join(" / ") : "-"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    ) : null}
                  </div>
                )})}
              </div>
            )}
          </section>

          <section className="panel">
            <header className="panel-header">
              <div>
                <h2>{detailUsesDraftPreview ? "明細 (最新シートの確認)" : "明細 (編集)"}</h2>
                {detailUsesDraftPreview ? (
                  <p className="subtle">
                    施設区分の最新シートを表示中です。数量修正は Step2 のシート側で行ってください。
                  </p>
                ) : null}
              </div>
            </header>
            {lineGroups.length === 0 ? (
              <p className="subtle">データなし</p>
            ) : (
              <div className="wrap-grid">
                {lineGroups.map((group) => {
                  const groupKey = `${group.date}__${group.categoryKey}`;
                  const isOpen = openLineGroupKey === groupKey;
                  return (
                  <div key={`line-${group.date}-${group.categoryKey}`} className="date-group">
                    <button
                      className="date-group-header date-group-toggle"
                      type="button"
                      onClick={() => setOpenLineGroupKey((prev) => (prev === groupKey ? null : groupKey))}
                    >
                      <span className="date-group-title">{group.date}</span>
                      <span className="group-separator">/</span>
                      <span className="group-tag">{group.categoryLabel}</span>
                      <span className="group-count">{group.rows.length}件</span>
                      <span className="group-toggle-label">{isOpen ? "閉じる" : "開く"}</span>
                    </button>
                    {isOpen ? (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>メニュー</th>
                            <th>時間帯</th>
                            <th>袋</th>
                            {detailUsesDraftPreview ? (
                              <th>数量</th>
                            ) : (
                              <>
                                <th>OCR</th>
                                <th>修正</th>
                                <th>差分</th>
                                <th>実量</th>
                              </>
                            )}
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map(({ line, idx }) => (
                            <tr key={line.line_id || idx}>
                              <td>{line.menu_name || "-"}</td>
                              <td>{line.daypart || "-"}</td>
                              <td>{formatBagTypeLabel(line.bag_type, bagTypeLabelMap)}</td>
                              {detailUsesDraftPreview ? (
                                <td>{line.quantity_corrected ?? line.quantity_original ?? "-"}</td>
                              ) : (
                                <>
                                  <td>{line.quantity_original ?? "-"}</td>
                                  <td>
                                    <input
                                      className="input"
                                      type="number"
                                      value={line.quantity_corrected ?? ""}
                                      onChange={(e) => updateLineQuantity(idx, Number(e.target.value))}
                                    />
                                  </td>
                                  <td>
                                    {line.quantity_original == null
                                      ? "-"
                                      : (line.quantity_corrected ?? line.quantity_original) -
                                        (line.quantity_original ?? 0)}
                                  </td>
                                  <td>{formatActualAmount(line)}</td>
                                </>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    ) : null}
                  </div>
                )})}
              </div>
            )}
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>明細の確認・保存</h2>
              <p className="subtle">
                {detailUsesDraftPreview
                  ? "現在は最新シートの区分を確認中です。Step2 でシートを反映した後に明細保存を使ってください。"
                  : "数量や袋数を確認し、必要なら保存してから次の作業へ進みます。"}
              </p>
            </header>
            <div className="actions">
              <button className="btn ghost" onClick={saveLines} disabled={detailUsesDraftPreview}>
                明細を保存して作業続行
              </button>
            </div>
          </section>
            </>
          ) : null}

          {activeStepIndex === 3 ? (
            <section className="panel">
            <header className="panel-header">
              <div>
                <h2>袋分け結果</h2>
                <p className="subtle">OCR反映後に自動更新します。必要に応じて再計算できます。</p>
              </div>
              <div className="panel-actions">
                <button className="btn ghost" type="button" onClick={loadBags} disabled={bagLoading}>
                  {bagLoading ? "取得中..." : "更新"}
                </button>
                <button className="btn ghost" type="button" onClick={rebuildBags} disabled={bagLoading}>
                  {bagLoading ? "再計算中..." : "再計算"}
                </button>
              </div>
            </header>
            {bagMessage ? <p className="subtle">{bagMessage}</p> : null}
            {bagAppliedOverrides.length > 0 ? (
              <div className="bag-override-panel">
                <div className="bag-override-header">
                  <strong>日別単位補正が適用されています</strong>
                  <span className="subtle">この値は袋分けと個別出力の両方に反映されています。</span>
                </div>
                <div className="bag-override-list">
                  {bagAppliedOverrides.map((item) => (
                    <div
                      key={[
                        item.override_id || "-",
                        item.date || "-",
                        item.daypart || "-",
                        item.menu_name || "-",
                        item.diet_type || "-",
                      ].join("__")}
                      className="bag-override-item"
                    >
                      <p className="bag-override-main">
                        {item.date || "-"} / {item.daypart || "-"} / {item.menu_name || "-"} /{" "}
                        {item.diet_type ? formatDietType(item.diet_type) : "-"}
                      </p>
                      <p className="bag-override-meta">
                        {item.qty_per_serving != null ? item.qty_per_serving : "-"}
                        {normalizeUnitType(item.unit_type)}
                        /人
                        {item.menu_category ? ` / ${item.menu_category}` : ""}
                      </p>
                      {item.note ? <p className="bag-override-note">{item.note}</p> : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {bagRows.length === 0 ? (
              <p className="subtle">袋分け結果がありません。</p>
            ) : (
              <div className="wrap-grid">
                <p className="bag-summary-note subtle">
                  同じ日付・食区・メニュー・区分・エリア・袋種は1行にまとめて表示します。
                </p>
                {bagSummaryGroups.map((group) => (
                  <div key={`bag-${group.date}`} className="date-group">
                    <div className="date-group-header">
                      <span className="date-group-title">{group.date}</span>
                      <span className="group-count">{group.rows.length}グループ</span>
                    </div>
                    <div className="table-wrap">
                      <table className="bag-summary-table">
                        <thead>
                          <tr>
                            <th>食区</th>
                            <th>メニュー</th>
                            <th>区分</th>
                            <th>エリア</th>
                            <th>袋種</th>
                            <th>注文数</th>
                            <th>計算結果</th>
                            <th>総量(計算)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map((bag) => (
                            <tr key={bag.id}>
                              <td>{bag.daypart || "-"}</td>
                              <td>{bag.menu_name || "-"}</td>
                              <td>{bag.diet_type ? formatDietType(bag.diet_type) : "-"}</td>
                              <td>{bag.area_id || "-"}</td>
                              <td>{formatBagTypeLabel(bag.bag_type, bagTypeLabelMap)}</td>
                              <td className="bag-total-qty">{formatQuantity(bag.total_quantity)}</td>
                              <td className="bag-calc-result-cell">
                                <span className={`bag-count-badge${bag.bag_count > 1 ? " split" : ""}`}>
                                  {bag.bag_count}袋
                                </span>
                                {bag.bag_count > 1 ? (
                                  <span className="bag-calc-breakdown">{formatBagCalculationResult(bag)}</span>
                                ) : null}
                              </td>
                              <td>
                                {formatBagAmountFromTotals(
                                  resolveBagAmountTotals(
                                    {
                                      id: bag.id,
                                      date: bag.date,
                                      daypart: bag.daypart,
                                      menu_name: bag.menu_name,
                                      diet_type: bag.diet_type,
                                      area_id: bag.area_id,
                                      bag_type: bag.bag_type,
                                      quantity: bag.total_quantity,
                                    },
                                    bagAmountStats,
                                  ),
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
          ) : null}

          {activeStepIndex === 4 ? (
            <section className="panel">
            <header className="panel-header">
              <div>
                <h2>出力</h2>
                <p className="subtle">出力を確認して確定します。</p>
              </div>
              <div className="panel-actions">
                <button
                  className="btn ghost"
                  type="button"
                  onClick={() => {
                    void registerTrainingSample();
                  }}
                  disabled={trainingSampleSaving || confirmSaving}
                >
                  {trainingSampleSaving ? "登録中..." : "学習データ登録"}
                </button>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() => {
                    void confirm();
                  }}
                  disabled={orderAlreadyConfirmed || confirmSaving || !effectiveCanConfirm}
                >
                  {orderAlreadyConfirmed ? "確定済み" : confirmSaving ? "確定中..." : "確定"}
                </button>
              </div>
            </header>
            {sheetWeeklyMenuMissing ? (
              <div className="warning-banner">
                <p>
                  この週の月次メニューが未登録のため、まだ確定できません。先に対象月のメニューを登録し、シートを再読込してください。
                </p>
                {monthlyMenuHref ? (
                  <p>
                    <Link href={monthlyMenuHref} className="ghost-link">
                      対象月のメニュー編集を開く
                    </Link>
                  </p>
                ) : null}
              </div>
            ) : !effectiveCanConfirm && (reviewBlockerText || reviewWarningText) ? (
              <div className="warning-banner">
                <p>まだ確定できません。Step2でシートを整えて明細へ反映してから、もう一度確定してください。</p>
                {reviewBlockerText ? <p className="subtle">{reviewBlockerText}</p> : null}
                {reviewWarningText ? (
                  <details className="ocr-review-details">
                    <summary>確認メモ</summary>
                    <div className="ocr-review-details-content">
                      {reviewWarningText.split(" / ").map((item) => (
                        <span className="ocr-review-pill ocr-review-pill--muted" key={`confirm-warning-${item}`}>
                          {item}
                        </span>
                      ))}
                    </div>
                  </details>
                ) : null}
              </div>
            ) : null}
            <div className="outputs">
              <div className="output-card">
                <span className="output-link">ラベルCSV</span>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() => openOutput(`/outputs/labels?order_id=${order.id}`, "ラベルCSV")}
                >
                  ダウンロード
                </button>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={(e) => {
                    loadOutputPreview("labels");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                    }
                  }}
                  disabled={outputPreviewLoading}
                >
                  プレビュー
                </button>
              </div>
              <div className="output-card">
                <span className="output-link">納品書Excel</span>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() => openOutput(`/outputs/delivery-notes?order_id=${order.id}`, "納品書Excel")}
                >
                  ダウンロード
                </button>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={(e) => {
                    loadOutputPreview("delivery");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                    }
                  }}
                  disabled={outputPreviewLoading}
                >
                  プレビュー
                </button>
              </div>
              <div className="output-card">
                <span className="output-link">総量CSV</span>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() =>
                    openOutput(`/outputs/manufacturing-aggregate?order_id=${order.id}`, "総量CSV")
                  }
                >
                  ダウンロード
                </button>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={(e) => {
                    loadOutputPreview("aggregate");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                    }
                  }}
                  disabled={outputPreviewLoading}
                >
                  プレビュー
                </button>
              </div>
            </div>
            <div className="shipping-panel">
              <div className="shipping-panel-header">
                <div>
                  <h3>送り状・追跡</h3>
                  <p className="subtle">この施設の最近の追跡状況を表示します。</p>
                </div>
                <div className="panel-actions">
                  <Link href="/shipping" className="btn ghost">
                    送り状ページ
                  </Link>
                  <button className="btn ghost" type="button" onClick={() => void loadShippingStatuses()} disabled={shippingLoading}>
                    {shippingLoading ? "取得中..." : "追跡更新"}
                  </button>
                </div>
              </div>
              {shippingMessage ? <p className="subtle">{shippingMessage}</p> : null}
              {shippingSummary ? (
                <div className="shipping-summary-strip">
                  <span>総件数: {shippingSummary.total ?? shippingStatuses.length}</span>
                  <span>完了: {shippingSummary.delivered ?? 0}</span>
                  <span>未完了: {shippingSummary.pending ?? 0}</span>
                </div>
              ) : null}
              {shippingStatuses.length === 0 ? (
                <p className="subtle">この施設に紐づく送り状追跡はまだありません。</p>
              ) : (
                <div className="table-wrap">
                  <table className="bag-table">
                    <thead>
                      <tr>
                        <th>追跡番号</th>
                        <th>状態</th>
                        <th>到着</th>
                        <th>確認日時</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shippingStatuses.map((item) => (
                        <tr key={`${item.tracking_key || item.tracking_number}-${item.looked_up_at || ""}`}>
                          <td>{item.tracking_number || item.tracking_key || "-"}</td>
                          <td>{item.error ? `照会失敗 (${item.error})` : item.status || "-"}</td>
                          <td>{item.arrival_text || (item.delivered ? "完了" : "-")}</td>
                          <td>{item.looked_up_at ? formatTimestamp(item.looked_up_at) : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            {downloadMessage ? <p className="subtle">{downloadMessage}</p> : null}
            {outputPreviewMessage ? <p className="subtle">{outputPreviewMessage}</p> : null}
            {outputPreview ? (
              <details className="output-preview" open>
                <summary>
                  プレビュー: {outputPreviewLabels[outputPreview.type]}
                  {outputPreview.rows.length ? ` (${outputPreview.rows.length}件)` : ""}
                </summary>
                <div className="table-wrap">
                  <table>
                    {outputPreview.headers.length ? (
                      <thead>
                        <tr>
                          {outputPreview.headers.map((header, idx) => (
                            <th key={`preview-head-${idx}`}>{header}</th>
                          ))}
                        </tr>
                      </thead>
                    ) : null}
                    <tbody>
                      {outputPreview.rows.map((row, rowIdx) => (
                        <tr key={`preview-row-${rowIdx}`}>
                          {row.map((cell, idx) => (
                            <td key={`preview-cell-${rowIdx}-${idx}`}>
                              {formatOutputPreviewCell(cell, outputPreview.headers[idx], bagTypeLabelMap)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ) : null}
          </section>
          ) : null}

          <div className="step-footer">
            <button className="btn ghost" type="button" onClick={goPrevStep} disabled={!canStepPrev}>
              {canStepPrev ? `戻る: ${prevStepLabel}` : "戻る"}
            </button>
            <button
              className="btn primary"
              type="button"
              onClick={() => void goNextStep()}
              disabled={
                isLastStep
                  ? orderAlreadyConfirmed
                    ? confirmSaving || trainingSampleSaving
                    : confirmSaving || trainingSampleSaving || !effectiveCanConfirm
                  : !canStepNext
              }
            >
              {nextStepButtonLabel}
            </button>
          </div>
        </>
      )}

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
        }

        :global(a) {
          color: inherit;
          text-decoration: none;
        }

        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }

        .hero {
          display: flex;
          flex-wrap: wrap;
          justify-content: space-between;
          gap: 24px;
          align-items: center;
          margin-bottom: 32px;
        }

        .eyebrow {
          letter-spacing: 0.12em;
          text-transform: uppercase;
          font-size: 12px;
          color: #5f7b74;
          margin-bottom: 8px;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }

        .subtle {
          color: #51615c;
          margin: 0;
        }

        .nav {
          display: flex;
          gap: 12px;
        }

        .nav-link {
          padding: 10px 18px;
          border-radius: 999px;
          background: #1f2a2a;
          color: #f7f2e7;
          font-weight: 600;
          transition: transform 0.2s ease;
        }

        .nav-link:hover {
          transform: translateY(-2px);
        }

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 16px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 16px;
        }

        .step-panel {
          background: #fbfaf6;
        }

        .step-indicator {
          font-size: 12px;
          font-weight: 600;
          color: #5f7b74;
        }

        .step-tabs {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }

        .step-tab {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
          padding: 8px 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #ffffff;
          cursor: pointer;
          font-size: 13px;
        }

        .step-tab:disabled {
          cursor: not-allowed;
          opacity: 0.7;
          background: #f5f3ee;
        }

        .step-tab.done {
          border-color: rgba(36, 110, 87, 0.35);
          background: rgba(36, 110, 87, 0.08);
        }

        .step-tab.active {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: #1f2a2a;
        }

        .step-number {
          width: 26px;
          height: 26px;
          border-radius: 50%;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          background: #f4f1ea;
          font-weight: 700;
          font-size: 12px;
          color: inherit;
        }

        .step-tab.active .step-number {
          background: rgba(255, 255, 255, 0.2);
        }

        .step-label {
          font-weight: 600;
        }

        .step-note {
          width: 100%;
          font-size: 11px;
          color: #7b5b2c;
        }

        .step-meta {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 16px;
          flex-wrap: wrap;
        }

        .step-title {
          margin: 0 0 4px;
          font-weight: 600;
        }

        .step-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }

        .step-footer {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          flex-wrap: wrap;
          margin: 8px 0 28px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }

        .panel-header > div {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .panel-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          align-items: center;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
        }

        .status-pill {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .summary-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
        }

        .summary-grid--compact {
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          align-items: stretch;
        }

        .summary-primary-card {
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #f8fbfa;
        }

        .summary-value {
          margin: 4px 0 0;
          font-weight: 600;
        }

        .summary-actions {
          display: flex;
          gap: 12px;
          align-items: flex-end;
          flex-wrap: wrap;
          margin-top: 12px;
        }

        .step1-facility-block {
          margin: 14px 0 16px;
          padding: 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #f8fbfa;
        }

        .step1-facility-block .summary-actions {
          margin-top: 0;
        }

        .summary-actions .field {
          min-width: 220px;
          flex: 1;
        }

        .summary-actions-right {
          justify-content: flex-end;
        }

        .llm-provider-select {
          min-width: 130px;
          flex: 0 0 auto;
        }

        .llm-model-select {
          min-width: 130px;
          flex: 0 0 auto;
        }

        .llm-model-input {
          min-width: 220px;
          flex: 0 0 auto;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .facility-suggestions {
          margin-top: 8px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .facility-suggestion-list {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .facility-chip {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          border-radius: 999px;
          border: 1px solid rgba(25, 32, 30, 0.14);
          background: #fbfbf9;
          padding: 6px 10px;
          font-size: 12px;
          cursor: pointer;
        }

        .facility-chip.auto {
          border-color: rgba(36, 110, 87, 0.5);
          background: rgba(36, 110, 87, 0.08);
        }

        .facility-chip-name {
          font-weight: 600;
        }

        .facility-chip-score {
          color: #4b5c57;
        }

        .facility-chip-reason {
          color: #6b7b76;
        }

        .facility-suggestion-note {
          font-size: 12px;
          color: #6b7b76;
        }

        .critical-choice-card {
          display: flex;
          flex-direction: column;
          gap: 8px;
          width: 100%;
        }

        .critical-choice-panel {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .critical-choice-list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .workflow-summary-card {
          margin-top: 10px;
          padding: 12px 14px;
          border-radius: 12px;
          background: #f4f7f6;
          border: 1px solid rgba(25, 32, 30, 0.08);
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
        }

        .workflow-summary-title {
          margin: 4px 0 0;
          font-size: 14px;
          font-weight: 700;
          color: #21302d;
        }

        .workflow-summary-actions {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 8px;
        }

        .workflow-summary-action-btn {
          white-space: nowrap;
        }

        .summary-details {
          margin-top: 10px;
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
        }

        .summary-details summary {
          cursor: pointer;
          font-weight: 700;
          color: #2f3e3b;
          list-style: none;
        }

        .summary-details summary::-webkit-details-marker {
          display: none;
        }

        .summary-details-grid {
          margin-top: 10px;
          display: grid;
          gap: 10px;
        }

        .summary-detail-list {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        }

        .summary-details-stack {
          margin-top: 10px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .sheet-context-dialog-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(18, 24, 22, 0.38);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 20px;
          z-index: 1200;
        }

        .sheet-context-dialog {
          width: min(560px, 100%);
          border-radius: 16px;
          background: #fffdf8;
          border: 1px solid rgba(25, 32, 30, 0.14);
          box-shadow: 0 20px 50px rgba(18, 24, 22, 0.18);
          padding: 18px;
        }

        .sheet-context-dialog h3 {
          margin: 4px 0 10px;
          font-size: 20px;
          color: #21302d;
        }

        .sheet-context-dialog-summary {
          margin-top: 12px;
          padding: 12px;
          border-radius: 12px;
          background: #f6f3ea;
          border: 1px solid rgba(25, 32, 30, 0.08);
          display: grid;
          gap: 10px;
        }

        .sheet-context-dialog-row {
          display: grid;
          grid-template-columns: 56px minmax(0, 1fr) 24px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          font-size: 13px;
        }

        .sheet-context-dialog-row strong {
          word-break: break-word;
        }

        .sheet-context-dialog-arrow {
          text-align: center;
          color: #5f6d69;
        }

        .sheet-context-dialog-actions {
          margin-top: 14px;
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }

        .warning-banner {
          margin-top: 10px;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid rgba(176, 92, 0, 0.28);
          background: #fff4df;
          color: #7a4100;
          font-size: 13px;
        }

        .critical-alert {
          margin-top: 10px;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(150, 32, 32, 0.28);
          background: #fff0ee;
          color: #7a1d1d;
        }

        .critical-alert-title {
          margin: 0 0 8px;
          font-size: 13px;
          font-weight: 700;
        }

        .critical-alert-list {
          margin: 0;
          padding-left: 18px;
          font-size: 13px;
        }

        .critical-alert-list li + li {
          margin-top: 4px;
        }

        .prompt-panel {
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
        }

        .prompt-panel summary {
          cursor: pointer;
          font-weight: 600;
          color: #354341;
          list-style: none;
        }

        .prompt-panel summary::-webkit-details-marker {
          display: none;
        }

        .prompt-panel textarea {
          margin-top: 10px;
          width: 100%;
          min-height: 120px;
        }

        .raw-actions {
          display: flex;
          align-items: center;
          gap: 12px;
          margin-top: 10px;
          flex-wrap: wrap;
        }

        .raw-message {
          font-size: 12px;
          color: #5f7b74;
        }

        .raw-output {
          margin-top: 10px;
          padding: 12px;
          border-radius: 12px;
          background: #f4f1ea;
          border: 1px solid rgba(25, 32, 30, 0.08);
          font-size: 12px;
          white-space: pre-wrap;
          max-height: 240px;
          overflow: auto;
          font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        }

        .reparse-debug-panel {
          margin-top: 10px;
          padding: 10px;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #ffffff;
        }

        .reparse-debug-panel p {
          margin: 0 0 6px;
          font-size: 12px;
        }

        .order-history-list {
          margin-top: 10px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          overflow: hidden;
        }

        .order-history-row {
          display: grid;
          grid-template-columns: minmax(140px, 180px) minmax(100px, 140px) minmax(80px, 120px) 1fr;
          gap: 10px;
          padding: 8px 10px;
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          font-size: 12px;
          align-items: center;
          background: #ffffff;
        }

        .order-history-row:last-child {
          border-bottom: none;
        }

        .ocr-preview {
          width: 100%;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fff;
          display: block;
        }

        .ocr-preview-wrapper {
          position: relative;
          width: 100%;
          overflow: auto;
          max-height: calc(100vh - 280px);
          border-radius: 10px;
          background: #f5f2eb;
        }

        .ocr-preview-wrapper--framed {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 14px;
          background: #f5f2eb;
        }

        .ocr-preview-wrapper .ocr-preview {
          border: none;
        }

        .ocr-preview-wrapper.editable {
          cursor: crosshair;
          box-shadow: 0 0 0 2px rgba(31, 42, 42, 0.35);
          border-radius: 10px;
        }

        .ocr-overlay-grid {
          position: absolute;
          display: grid;
          border: 2px solid rgba(31, 42, 42, 0.5);
          background: rgba(255, 255, 255, 0.08);
          z-index: 2;
        }

        .ocr-overlay-cell {
          border: none;
          outline: 1px solid rgba(31, 42, 42, 0.25);
          background: rgba(255, 255, 255, 0.18);
          padding: 0;
          margin: 0;
          text-align: left;
          font-size: 10px;
          color: #1f2a2a;
          overflow: hidden;
          box-sizing: border-box;
          cursor: pointer;
        }

        .ocr-overlay-cell.active {
          outline-color: rgba(31, 42, 42, 0.9);
          background: rgba(255, 255, 255, 0.55);
        }

        .ocr-overlay-row-highlight {
          position: absolute;
          border: 3px solid rgba(193, 83, 28, 0.95);
          background: rgba(255, 195, 136, 0.22);
          box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.72), 0 8px 22px rgba(193, 83, 28, 0.18);
          border-radius: 8px;
          pointer-events: none;
          z-index: 3;
          transition: top 120ms ease, height 120ms ease;
        }

        .ocr-overlay-column-highlight {
          position: absolute;
          border: 3px solid rgba(37, 129, 176, 0.85);
          background: rgba(124, 202, 238, 0.18);
          box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.62), 0 8px 22px rgba(37, 129, 176, 0.14);
          border-radius: 8px;
          pointer-events: none;
          z-index: 2;
          transition: left 120ms ease, width 120ms ease;
        }

        .ocr-overlay-row-marker {
          position: absolute;
          width: 14px;
          height: 14px;
          border-radius: 999px;
          background: rgba(193, 83, 28, 0.98);
          border: 2px solid rgba(255, 255, 255, 0.9);
          box-shadow: 0 3px 12px rgba(193, 83, 28, 0.28);
          pointer-events: none;
          transform: translate(-50%, -50%);
          z-index: 4;
        }

        .ocr-overlay-row-marker::after {
          content: "";
          position: absolute;
          left: 100%;
          top: 50%;
          transform: translateY(-50%);
          border-top: 5px solid transparent;
          border-bottom: 5px solid transparent;
          border-left: 8px solid rgba(193, 83, 28, 0.98);
        }

        .hakodate-overlay-preview {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .hakodate-overlay-status,
        .hakodate-overlay-blocked {
          border: 1px solid rgba(34, 139, 78, 0.24);
          background: rgba(238, 248, 241, 0.92);
          border-radius: 12px;
          padding: 10px 12px;
          color: #1f2a2a;
          font-size: 12px;
        }

        .hakodate-overlay-status.blocked,
        .hakodate-overlay-blocked {
          border-color: rgba(193, 83, 28, 0.35);
          background: rgba(255, 246, 236, 0.96);
        }

        .hakodate-overlay-status-header {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          justify-content: space-between;
          align-items: center;
        }

        .hakodate-job-status {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          align-items: center;
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px solid rgba(31, 42, 42, 0.12);
        }

        .hakodate-overlay-blocker-list {
          margin: 8px 0 0;
          padding-left: 18px;
        }

        .hakodate-frame-editor {
          display: flex;
          flex-direction: column;
          gap: 10px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          background: rgba(255, 255, 255, 0.88);
          border-radius: 14px;
          padding: 12px;
        }

        .hakodate-frame-editor-header,
        .hakodate-frame-editor-actions {
          display: flex;
          gap: 8px;
          align-items: center;
          justify-content: space-between;
        }

        .hakodate-frame-editor-actions {
          flex-wrap: wrap;
          justify-content: flex-end;
        }

        .hakodate-frame-editor-actions--wrap {
          justify-content: flex-start;
        }

        .hakodate-frame-editor-body {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .hakodate-frame-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
          gap: 8px;
        }

        .hakodate-frame-grid--wide {
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        }

        .hakodate-frame-wide-field {
          grid-column: span 2;
        }

        .ocr-overlay-text {
          display: block;
          padding: 2px 4px;
          font-size: 10px;
          line-height: 1.2;
          opacity: 0.6;
          white-space: nowrap;
          text-overflow: ellipsis;
          overflow: hidden;
        }

        .ocr-overlay-cell:hover .ocr-overlay-text,
        .ocr-overlay-cell.active .ocr-overlay-text {
          opacity: 1;
        }

        .ocr-overlay-input {
          position: absolute;
          z-index: 3;
          padding: 2px 4px;
          font-size: 12px;
          border-radius: 6px;
          box-sizing: border-box;
        }

        .edit-hint {
          font-size: 12px;
          color: #4b5c57;
          background: rgba(31, 42, 42, 0.06);
          border: 1px solid rgba(31, 42, 42, 0.12);
          border-radius: 10px;
          padding: 8px 10px;
        }

        .edit-hint.active {
          color: #1f2a2a;
          background: rgba(31, 42, 42, 0.12);
          border-color: rgba(31, 42, 42, 0.3);
          font-weight: 600;
        }

        .page-tabs {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }

        .page-tab {
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbfbf9;
          border-radius: 999px;
          padding: 6px 12px;
          font-size: 12px;
          cursor: pointer;
        }

        .page-tab.active {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: #1f2a2a;
        }

        .ocr-summary-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          margin-bottom: 16px;
        }

        .ocr-summary-actions {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
          flex-wrap: wrap;
        }

        .ocr-showcase {
          display: grid;
          gap: 16px;
          grid-template-columns: 1fr;
          margin-bottom: 0;
        }

        .ocr-workspace {
          display: grid;
          gap: 16px;
          grid-template-columns: minmax(360px, 0.92fr) minmax(520px, 1.08fr);
          margin-bottom: 16px;
          align-items: start;
        }

        .ocr-workspace--vertical {
          grid-template-columns: minmax(0, 1fr);
        }

        .ocr-workspace-tools {
          grid-column: 1 / -1;
        }

        .ocr-workspace--vertical .ocr-workspace-tools {
          grid-column: auto;
        }

        .ocr-workspace-preview,
        .ocr-workspace-editor {
          min-width: 0;
        }

        .ocr-edit--sheet {
          height: 100%;
        }

        .ocr-sheet-save-banner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 10px;
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid rgba(31, 42, 42, 0.16);
          background: linear-gradient(135deg, #f6efe0 0%, #fffaf1 100%);
          box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.45);
        }

        .ocr-sheet-save-banner h4 {
          margin: 2px 0 4px;
          font-size: 15px;
          color: #243431;
        }

        .ocr-sheet-save-banner :global(.btn) {
          min-width: 180px;
        }

        .ocr-preview-card {
          padding: 14px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
          display: flex;
          flex-direction: column;
          gap: 10px;
          position: sticky;
          top: 12px;
        }

        .preview-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          font-size: 12px;
        }

        .preview-header-copy {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }

        .preview-header-tools {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
          flex-wrap: wrap;
        }

        .preview-mode-toggle {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 4px;
          border-radius: 999px;
          background: #f1ece1;
        }

        .preview-mode-toggle :global(.btn.active) {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: rgba(25, 32, 30, 0.18);
        }

        .preview-surface {
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #f5f2eb;
          overflow: hidden;
        }

        .preview-surface--secondary {
          margin-top: 8px;
        }

        .preview-frame {
          display: block;
          border: none;
          border-radius: 0;
        }

        .preview-placeholder {
          padding: 16px;
          border-radius: 10px;
          background: #f4f1ea;
          color: #6b7b76;
          font-size: 12px;
          text-align: center;
        }

        .layout-toggle {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }

        .table-box-editor {
          display: none;
        }

        .table-box-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        }

        .table-box-grid {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        }

        .table-box-primary {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }

        .table-box-advanced {
          margin-top: 10px;
          padding: 10px;
          border-radius: 12px;
          border: 1px dashed rgba(25, 32, 30, 0.14);
          background: #ffffff;
        }

        .table-box-advanced summary {
          cursor: pointer;
          font-weight: 600;
          color: #354341;
          list-style: none;
          margin-bottom: 8px;
        }

        .table-box-advanced summary::-webkit-details-marker {
          display: none;
        }

        .table-box-field {
          display: flex;
          flex-direction: column;
          gap: 4px;
          font-size: 12px;
          color: #4b5c57;
        }

        .table-box-nudge {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }

        .table-box-footer {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          align-items: center;
        }

        .facility-template-editor {
          margin-top: 16px;
          padding-top: 12px;
          border-top: 1px dashed rgba(25, 32, 30, 0.12);
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .facility-template-toggle {
          width: 100%;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #f7f5ee;
          color: #24312f;
          cursor: pointer;
          text-align: left;
        }

        .facility-template-toggle-title {
          font-size: 14px;
          font-weight: 700;
        }

        .facility-template-toggle-meta {
          font-size: 12px;
          color: #667774;
          white-space: nowrap;
        }

        .facility-template-callout {
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbfbf9;
        }

        .facility-template-callout-title {
          margin: 0 0 6px;
          font-size: 13px;
          font-weight: 700;
          color: #24312f;
        }

        .facility-template-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          align-items: center;
          justify-content: flex-end;
        }

        .facility-template-table-wrap {
          overflow: auto;
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 12px;
          background: #ffffff;
        }

        .facility-template-table {
          min-width: 780px;
        }

        .facility-template-table th,
        .facility-template-table td {
          padding: 8px;
        }

        .facility-template-cell-stack {
          display: grid;
          gap: 6px;
          min-width: 140px;
        }

        .facility-template-row-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .ocr-result {
          margin-top: 6px;
        }

        .ocr-result-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 8px;
        }

        .markdown-preview {
          padding: 10px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #f7f5ee;
          overflow: auto;
        }

        .markdown-table table {
          font-size: 12px;
        }

        .markdown-text {
          margin-bottom: 8px;
        }

        .markdown-line {
          margin: 0 0 6px;
          font-size: 12px;
          color: #3f4d4a;
        }

        .markdown-image {
          max-width: 100%;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fff;
          margin-bottom: 8px;
        }

        .markdown-raw {
          margin-top: 10px;
          padding: 12px;
          border-radius: 12px;
          border: 1px dashed rgba(25, 32, 30, 0.12);
          background: #fbfbf9;
          font-size: 12px;
          color: #3f4d4a;
          white-space: pre-wrap;
          word-break: break-word;
          max-height: 280px;
          overflow: auto;
        }

        .ocr-edit {
          margin-top: 0;
          padding: 8px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
        }

        .ocr-edit-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 8px;
        }

        .ocr-edit-title {
          margin: 2px 0 0;
          font-size: 16px;
          color: #223431;
        }

        .ocr-editor-mode-switch {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          align-items: center;
        }

        .ocr-fixed-mode-pill {
          display: inline-flex;
          align-items: center;
          min-height: 32px;
          padding: 0 12px;
          border-radius: 999px;
          border: 1px solid rgba(34, 139, 78, 0.25);
          background: rgba(238, 248, 241, 0.92);
          color: #1f2a2a;
          font-size: 13px;
          font-weight: 700;
        }

        .ocr-edit-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }

        .ocr-meta-details,
        .ocr-review-details {
          margin-top: 6px;
        }

        .ocr-meta-details summary,
        .ocr-review-details summary {
          font-size: 12px;
          color: #556168;
          cursor: pointer;
          list-style: none;
          user-select: none;
        }

        .ocr-meta-details summary::-webkit-details-marker,
        .ocr-review-details summary::-webkit-details-marker {
          display: none;
        }

        .ocr-meta-details-content,
        .ocr-review-details-content {
          margin-top: 6px;
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .ocr-guidance-card {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 10px;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.1);
          background: #f3f5f2;
        }

        .ocr-guidance-copy {
          flex: 1;
          min-width: 0;
        }

        .ocr-guidance-label {
          margin: 0 0 4px;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.04em;
          color: #66716f;
          text-transform: uppercase;
        }

        .ocr-guidance-title {
          margin: 0;
          font-size: 15px;
          font-weight: 700;
          color: #223431;
        }

        .ocr-guidance-note {
          margin: 6px 0 0;
          font-size: 13px;
          color: #556168;
        }

        .ocr-flow-card {
          margin-bottom: 10px;
          padding: 10px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.1);
          background: #f3f5f2;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ocr-flow-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        }

        .ocr-flow-header-copy {
          flex: 1;
          min-width: 0;
        }

        .ocr-flow-eyebrow {
          margin: 0 0 4px;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.04em;
          color: #66716f;
          text-transform: uppercase;
        }

        .ocr-flow-title {
          margin: 0;
          font-size: 16px;
          font-weight: 700;
          color: #223431;
        }

        .ocr-flow-note {
          margin: 6px 0 0;
          font-size: 13px;
          color: #556168;
        }

        .ocr-flow-statuses {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .ocr-flow-track {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr);
          gap: 8px;
          align-items: stretch;
        }

        .ocr-flow-step {
          display: flex;
          gap: 8px;
          align-items: flex-start;
          padding: 9px 10px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.12);
          background: #ffffff;
        }

        .ocr-flow-step.active {
          border-color: rgba(31, 42, 42, 0.24);
          box-shadow: inset 0 0 0 1px rgba(31, 42, 42, 0.08);
        }

        .ocr-flow-step-index {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 24px;
          border-radius: 999px;
          background: #1f2a2a;
          color: #f7f2e7;
          font-size: 12px;
          font-weight: 700;
          flex: 0 0 auto;
        }

        .ocr-flow-step-title {
          margin: 0;
          font-size: 13px;
          font-weight: 700;
          color: #243431;
        }

        .ocr-flow-step-note {
          margin: 2px 0 0;
          font-size: 11px;
          color: #556168;
        }

        .ocr-flow-divider {
          align-self: center;
          height: 2px;
          border-radius: 999px;
          background: rgba(31, 42, 42, 0.16);
        }

        .ocr-flow-branches {
          display: grid;
          gap: 12px;
          grid-template-columns: minmax(0, 1fr);
        }

        .ocr-flow-branches--wizard {
          gap: 10px;
        }

        .ocr-flow-question-card,
        .ocr-flow-panel,
        .ocr-flow-finish-card {
          padding: 10px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.12);
          background: #ffffff;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ocr-flow-rail {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ocr-flow-question-card--compact {
          display: block;
        }

        .ocr-flow-question-main {
          display: grid;
          gap: 10px;
          min-width: 0;
        }

        .ocr-flow-question-copy {
          display: grid;
          gap: 4px;
        }

        .ocr-flow-question-copy h4 {
          margin: 0;
        }

        .ocr-flow-finish-card {
          background: linear-gradient(135deg, #f7f0e1 0%, #fff9ef 100%);
          border-color: rgba(31, 42, 42, 0.16);
        }

        .ocr-flow-finish-copy {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .ocr-flow-panel.is-processing {
          background: #fcfcfa;
        }

        .ocr-flow-panel--repair {
          min-width: 0;
        }

        .ocr-flow-panel-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        }

        .ocr-flow-choice-grid {
          display: grid;
          gap: 8px;
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .ocr-flow-choice-grid--compact {
          align-items: stretch;
          grid-template-columns: repeat(3, minmax(0, 1fr));
        }

        .ocr-flow-choice-button,
        .ocr-flow-subchoice,
        .ocr-flow-choice-select-card {
          border: 1px solid rgba(24, 42, 40, 0.12);
          background: #f8fbfa;
          color: #243431;
          border-radius: 12px;
          padding: 10px 12px;
          text-align: left;
          display: flex;
          flex-direction: column;
          gap: 3px;
          min-height: 100%;
        }

        .ocr-flow-choice-button,
        .ocr-flow-subchoice {
          cursor: pointer;
        }

        .ocr-flow-choice-button.active,
        .ocr-flow-subchoice.active {
          border-color: rgba(31, 42, 42, 0.28);
          background: #edf3f1;
          box-shadow: inset 0 0 0 1px rgba(31, 42, 42, 0.08);
        }

        .ocr-flow-choice-title {
          font-size: 13px;
          font-weight: 700;
        }

        .ocr-flow-choice-note {
          font-size: 11px;
          color: #556168;
        }

        .ocr-flow-choice-select-card .input {
          width: 100%;
          margin-top: 2px;
          margin-bottom: 2px;
        }

        .ocr-flow-subchoices {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 8px;
        }

        .ocr-flow-subchoice {
          min-width: 0;
        }

        .ocr-flow-branch {
          padding: 12px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.12);
          background: #ffffff;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .ocr-flow-branch.is-primary {
          border-color: rgba(31, 42, 42, 0.26);
          box-shadow: inset 0 0 0 1px rgba(31, 42, 42, 0.08);
        }

        .ocr-flow-branch.is-processing {
          background: #fcfcfa;
        }

        .ocr-flow-branch-label {
          margin: 0;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.04em;
          color: #66716f;
          text-transform: uppercase;
        }

        .ocr-flow-branch h4 {
          margin: 0;
          font-size: 15px;
          color: #243431;
        }

        .ocr-flow-branch-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          align-items: center;
        }

        .ocr-remediation-groups {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .ocr-remediation-group {
          padding: 9px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.1);
          background: #f9faf8;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .ocr-remediation-group--llm {
          background: #fcfcfb;
          grid-column: 1 / -1;
        }

        .ocr-remediation-group-label {
          margin: 0;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.04em;
          color: #66716f;
          text-transform: uppercase;
        }

        .ocr-remediation-group h5 {
          margin: 0;
          font-size: 15px;
          color: #243431;
        }

        .ocr-remediation-empty {
          margin: 0;
        }

        .ocr-evidence-switch-card {
          padding: 10px;
          border-radius: 12px;
          border: 1px solid rgba(31, 42, 42, 0.14);
          background: #ffffff;
        }

        .ocr-evidence-switch-card--muted {
          background: #f7f7f5;
        }

        .ocr-evidence-switch-title {
          margin: 0 0 4px;
          font-size: 14px;
          font-weight: 700;
          color: #243431;
        }

        .ocr-inline-details {
          border-top: 1px dashed rgba(24, 42, 40, 0.12);
          padding-top: 10px;
        }

        .ocr-inline-prompt {
          width: 100%;
          border-top: 1px dashed rgba(24, 42, 40, 0.12);
          padding-top: 10px;
        }

        .ocr-inline-prompt textarea {
          margin-top: 8px;
          width: 100%;
          min-height: 120px;
          resize: vertical;
          font-size: 14px;
          line-height: 1.6;
        }

        .ocr-inline-details summary {
          cursor: pointer;
          font-size: 12px;
          color: #556168;
          list-style: none;
          user-select: none;
        }

        .ocr-inline-details summary::-webkit-details-marker {
          display: none;
        }

        .ocr-inline-details textarea {
          margin-top: 8px;
        }

        .ocr-llm-prompt-textarea {
          width: 100%;
          min-height: 120px;
          font-size: 14px;
          line-height: 1.6;
        }

        .ocr-processing-banner {
          display: flex;
          flex-wrap: wrap;
          gap: 6px 10px;
          align-items: center;
          padding: 8px 10px;
          border-radius: 10px;
          border: 1px solid rgba(31, 42, 42, 0.12);
          background: #f7f3eb;
          color: #31423f;
          font-size: 13px;
        }

        .ocr-processing-banner strong {
          font-size: 13px;
          color: #243431;
        }

        @media (min-width: 1120px) {
          .ocr-flow-branches {
            grid-template-columns: minmax(280px, 0.9fr) minmax(0, 1.4fr);
            align-items: start;
          }

          .ocr-flow-branches--wizard {
            grid-template-columns: minmax(0, 1fr);
          }

          .ocr-flow-branches--wizard.has-repair-panel {
            grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
            align-items: start;
          }
        }

        @media (max-width: 920px) {
          .ocr-remediation-groups {
            grid-template-columns: minmax(0, 1fr);
          }

          .ocr-remediation-group--llm {
            grid-column: auto;
          }

          .ocr-flow-subchoices {
            grid-template-columns: minmax(0, 1fr);
          }

          .ocr-flow-choice-grid--compact {
            grid-template-columns: 1fr;
          }
        }

        .ocr-review-pill {
          display: inline-flex;
          align-items: center;
          padding: 4px 10px;
          border-radius: 999px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #f4f1ea;
          color: #31423f;
          font-size: 12px;
          font-weight: 600;
        }

        .ocr-review-pill--state {
          background: #e8f0ec;
          border-color: rgba(36, 110, 87, 0.2);
        }

        .ocr-review-pill--muted {
          background: #f7f7f7;
          border-color: rgba(24, 42, 40, 0.12);
          color: #47525a;
          font-weight: 500;
        }

        .warning-banner--compact {
          margin-top: 8px;
        }

        .ocr-secondary-tools {
          margin-bottom: 10px;
          padding: 10px 12px;
          border: 1px dashed rgba(25, 32, 30, 0.12);
          border-radius: 12px;
          background: #fcfcfb;
        }

        .ocr-secondary-tools-body {
          margin-top: 10px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ocr-shift-toolbar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 10px;
          margin-bottom: 10px;
          padding: 8px 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 10px;
          background: #f9fbfa;
        }

        .ocr-column-fill-toolbar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 10px;
          margin-bottom: 10px;
          padding: 8px 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 10px;
          background: #f9fbfa;
        }

        .ocr-shift-label {
          font-size: 12px;
          font-weight: 700;
          color: #243431;
        }

        .ocr-shift-field {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
          color: #44524f;
        }

        .ocr-shift-field input {
          width: 72px;
          padding: 6px 8px;
          font-size: 12px;
        }

        .ocr-shift-field select {
          min-width: 128px;
          padding: 6px 8px;
          font-size: 12px;
        }

        .ocr-confidence-toolbar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 10px;
          margin-bottom: 10px;
          padding: 8px 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 10px;
          background: #fbfcfb;
        }

        .ocr-history-summary {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          padding: 8px 10px;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #f9fbfa;
          font-size: 12px;
          color: #31423f;
          margin-bottom: 10px;
        }

        .ocr-history-list {
          margin-bottom: 10px;
          border: 1px dashed rgba(25, 32, 30, 0.12);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fcfcfb;
        }

        .ocr-history-list summary {
          cursor: pointer;
          font-size: 12px;
          font-weight: 600;
          color: #2f3e3b;
        }

        .ocr-history-rows {
          margin-top: 8px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .ocr-history-row {
          display: grid;
          gap: 8px;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
          font-size: 12px;
          color: #3c4b48;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 8px;
          padding: 6px 8px;
          background: #ffffff;
        }

        .ocr-row-actions {
          display: flex;
          gap: 4px;
          align-items: center;
          justify-content: center;
        }

        .ocr-row-action-btn {
          flex: 0 0 auto;
          min-width: 0;
          padding: 4px 6px;
          min-height: 26px;
          line-height: 1.1;
          font-size: 11px;
          white-space: nowrap;
        }

        .ocr-edit-table th,
        .ocr-edit-table td {
          padding: 6px;
          font-size: 12px;
        }

        .ocr-edit-input {
          padding: 6px 8px;
          font-size: 12px;
          min-width: 80px;
        }

        .ocr-sheet-wrap {
          overflow: auto;
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 12px;
          background: #ffffff;
          max-height: 70vh;
        }

        .ocr-sheet-toolbar {
          display: flex;
          flex-wrap: wrap;
          justify-content: space-between;
          align-items: center;
          gap: 10px;
          margin-bottom: 12px;
        }

        .ocr-sheet-toolbar-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .ocr-sheet-table {
          border-collapse: separate;
          border-spacing: 0;
          width: max-content;
          min-width: 100%;
          table-layout: fixed;
        }

        .ocr-sheet-table th,
        .ocr-sheet-table td {
          border-right: 1px solid rgba(25, 32, 30, 0.08);
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          padding: 0;
          font-size: 12px;
          background: #ffffff;
        }

        .ocr-sheet-table th:last-child,
        .ocr-sheet-table td:last-child {
          border-right: none;
        }

        .ocr-sheet-sticky-top {
          position: sticky;
          top: 0;
          z-index: 3;
          background: #f3f6f5;
          padding: 8px;
          white-space: nowrap;
        }

        .ocr-sheet-row-index {
          position: sticky;
          left: 0;
          z-index: 4;
          min-width: 28px;
          width: 28px;
          text-align: center;
          background: #f3f6f5;
          font-weight: 600;
          padding: 8px 4px;
        }

        .ocr-sheet-sticky-left {
          position: sticky;
          z-index: 4;
          background: #f3f6f5;
        }

        .ocr-sheet-sticky-left-cell {
          position: sticky;
          z-index: 2;
          background: inherit;
          box-shadow: 1px 0 0 rgba(25, 32, 30, 0.08);
        }

        .ocr-sheet-cell-selected {
          box-shadow: inset 0 0 0 2px rgba(193, 83, 28, 0.24);
          background: rgba(193, 83, 28, 0.08) !important;
        }

        .ocr-sheet-cell-anchor {
          box-shadow: inset 0 0 0 2px rgba(193, 83, 28, 0.48);
        }

        .ocr-sheet-cell-drop-target {
          box-shadow: inset 0 0 0 2px rgba(45, 102, 84, 0.48);
        }

        .ocr-sheet-cell-confidence-high {
          background: rgba(39, 126, 85, 0.08);
        }

        .ocr-sheet-cell-confidence-medium {
          background: rgba(203, 142, 20, 0.1);
        }

        .ocr-sheet-cell-confidence-low {
          background: rgba(170, 78, 56, 0.1);
        }

        .ocr-sheet-cell-overlay-deterministic_candidate {
          box-shadow: inset 0 0 0 1px rgba(203, 142, 20, 0.18);
        }

        .ocr-sheet-cell-overlay-weak_candidate {
          box-shadow: inset 0 0 0 1px rgba(170, 78, 56, 0.18);
        }

        .ocr-sheet-cell-below-threshold .ocr-sheet-input {
          color: rgba(31, 42, 42, 0.58);
          background-image: linear-gradient(
            135deg,
            rgba(31, 42, 42, 0.04) 25%,
            transparent 25%,
            transparent 50%,
            rgba(31, 42, 42, 0.04) 50%,
            rgba(31, 42, 42, 0.04) 75%,
            transparent 75%,
            transparent
          );
          background-size: 10px 10px;
        }

        .ocr-sheet-input-wrap {
          position: relative;
        }

        .ocr-sheet-input-overlay {
          position: absolute;
          inset: 50% auto auto 10px;
          transform: translateY(-50%);
          pointer-events: none;
          font-size: 13px;
          font-weight: 600;
          letter-spacing: 0.01em;
          z-index: 1;
          max-width: calc(100% - 20px);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .ocr-sheet-input-overlay-deterministic_candidate {
          color: #0f5f99;
        }

        .ocr-sheet-input-overlay-weak_candidate {
          color: #0a7f8f;
        }

        .ocr-sheet-input-has-overlay {
          background-color: transparent;
        }

        .ocr-sheet-input {
          border: none;
          border-radius: 0;
          width: 100%;
          min-width: 0;
          box-sizing: border-box;
          background: transparent;
          padding: 8px;
          font-size: 12px;
        }

        .ocr-sheet-cell-selected .ocr-sheet-input {
          cursor: move;
        }

        .ocr-sheet-preview-cell {
          padding: 8px;
          min-width: 96px;
          white-space: pre-wrap;
        }

        .ocr-sheet-col-date,
        .ocr-sheet-col-daypart,
        .ocr-sheet-col-qty {
          text-align: center;
        }

        .ocr-sheet-col-date .ocr-sheet-input,
        .ocr-sheet-col-daypart .ocr-sheet-input,
        .ocr-sheet-col-qty .ocr-sheet-input {
          text-align: center;
        }

        .ocr-sheet-col-menu .ocr-sheet-input,
        .ocr-sheet-col-note .ocr-sheet-input {
          text-align: left;
        }

        .ocr-sheet-input:focus {
          outline: 2px solid rgba(31, 42, 42, 0.35);
          outline-offset: -2px;
        }

        .ocr-sheet-action-cell {
          min-width: 132px;
          white-space: nowrap;
        }

        .ocr-sheet-row.ocr-sheet-row-date-a td,
        .ocr-sheet-row.ocr-sheet-row-date-a th.ocr-sheet-row-index {
          background: #ffffff;
        }

        .ocr-sheet-row.ocr-sheet-row-date-b td,
        .ocr-sheet-row.ocr-sheet-row-date-b th.ocr-sheet-row-index {
          background: #f3f4f5;
        }

        .ocr-sheet-row.ocr-sheet-boundary-date td,
        .ocr-sheet-row.ocr-sheet-boundary-date th.ocr-sheet-row-index {
          border-top: 4px solid #8a8f94;
        }

        .ocr-sheet-row.ocr-sheet-boundary-daypart td,
        .ocr-sheet-row.ocr-sheet-boundary-daypart th.ocr-sheet-row-index {
          border-top: 2px solid #aeb4b9;
        }

        .ocr-sheet-row.ocr-sheet-row-focused td,
        .ocr-sheet-row.ocr-sheet-row-focused th.ocr-sheet-row-index {
          box-shadow: inset 0 0 0 2px rgba(193, 83, 28, 0.38);
        }

        .failed-list {
          margin: 8px 0 0;
          padding-left: 18px;
          font-size: 12px;
          color: #5f7b74;
        }

        .failed-list li {
          margin-bottom: 4px;
        }

        .pdf-frame {
          width: 100%;
          height: 520px;
          border: 1px solid #ddd;
          border-radius: 12px;
        }

        .pdf-frame-compact {
          height: auto;
          aspect-ratio: 1 / 1.414;
          min-height: 520px;
        }

        .pdf-frame-wide {
          min-height: 640px;
        }

        .pdf-placeholder {
          display: flex;
          align-items: center;
          justify-content: center;
          color: #6b7b76;
          background: #f9f7f2;
          font-size: 14px;
        }

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
        }

        thead {
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .actions {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: #f4f1ea;
        }

        .outputs {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }

        .output-card {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
          padding: 8px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: rgba(255, 255, 255, 0.9);
          cursor: default;
        }

        .output-link {
          padding: 10px 16px;
          border-radius: 12px;
          background: #fbfbf9;
          border: 1px solid rgba(25, 32, 30, 0.08);
          color: inherit;
          font-weight: 600;
          cursor: default;
        }

        .output-preview {
          margin-top: 12px;
          padding: 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
        }

        .shipping-panel {
          margin-top: 16px;
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: rgba(255, 255, 255, 0.9);
        }

        .shipping-panel-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
          margin-bottom: 8px;
        }

        .shipping-panel-header h3 {
          margin: 0 0 4px;
          font-size: 16px;
        }

        .shipping-summary-strip {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          margin: 8px 0 12px;
          color: #566160;
          font-size: 13px;
        }

        .output-preview summary {
          cursor: pointer;
          font-weight: 600;
          font-size: 13px;
          color: #354341;
          margin-bottom: 10px;
          list-style: none;
        }

        .output-preview summary::-webkit-details-marker {
          display: none;
        }

        .wrap-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr));
          align-items: start;
        }

        .bag-summary-note {
          grid-column: 1 / -1;
          margin: 0;
        }

        .bag-override-panel {
          margin-bottom: 14px;
          padding: 14px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.09);
          background: #f4f7f5;
        }

        .bag-override-header {
          display: flex;
          flex-direction: column;
          gap: 4px;
          margin-bottom: 10px;
        }

        .bag-override-list {
          display: grid;
          gap: 10px;
        }

        .bag-override-item {
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
          padding: 10px 12px;
        }

        .bag-override-main {
          margin: 0;
          font-size: 13px;
          font-weight: 700;
          color: #1f2a2a;
        }

        .bag-override-meta {
          margin: 4px 0 0;
          font-size: 12px;
          color: #5d6a66;
        }

        .bag-override-note {
          margin: 6px 0 0;
          font-size: 12px;
          color: #4d5d58;
        }

        .date-group {
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
        }

        .date-group-header {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 13px;
          font-weight: 600;
          color: #354341;
          margin-bottom: 10px;
        }

        .date-group-toggle {
          width: 100%;
          border: none;
          background: transparent;
          padding: 0;
          text-align: left;
          cursor: pointer;
        }

        .date-group-toggle:hover .group-count,
        .date-group-toggle:hover .group-toggle-label {
          color: #1f2a2a;
        }

        .date-group .table-wrap {
          max-height: 360px;
          overflow: auto;
        }

        .date-group table {
          min-width: 560px;
        }

        .date-group th,
        .date-group td {
          white-space: nowrap;
        }

        .bag-summary-table td {
          vertical-align: middle;
        }

        .bag-total-qty {
          font-weight: 700;
          color: #1f2a2a;
        }

        .bag-count-badge {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 56px;
          padding: 4px 10px;
          border-radius: 999px;
          background: #edf3ef;
          color: #355047;
          font-size: 12px;
          font-weight: 700;
        }

        .bag-count-badge.split {
          background: #efe6d6;
          color: #7d4a18;
        }

        .bag-calc-result-cell {
          min-width: 148px;
        }

        .bag-calc-breakdown {
          display: inline-block;
          margin-left: 8px;
          color: #566663;
          font-size: 12px;
        }

        .date-group-title {
          white-space: nowrap;
        }

        .group-separator {
          color: #a2aaa8;
        }

        .group-tag {
          background: #f4f1ea;
          border-radius: 999px;
          padding: 2px 8px;
          font-size: 12px;
          font-weight: 600;
          color: #354341;
        }

        .group-count {
          margin-left: auto;
          font-size: 12px;
          font-weight: 500;
          color: #6b7b76;
        }

        .group-toggle-label {
          font-size: 12px;
          font-weight: 600;
          color: #6b7b76;
        }

        @media (max-width: 720px) {
          .ocr-workspace {
            grid-template-columns: 1fr;
          }

          .ocr-workspace-tools {
            grid-column: auto;
          }
          .ocr-flow-track,
          .ocr-flow-branches {
            grid-template-columns: 1fr;
          }
          .ocr-flow-divider {
            width: 2px;
            height: 24px;
            justify-self: center;
          }
          .ocr-flow-header {
            flex-direction: column;
            align-items: stretch;
          }
          .ocr-preview-card {
            position: static;
          }
          .summary-actions {
            flex-direction: column;
            align-items: stretch;
          }
          .ocr-flow-choice-grid {
            grid-template-columns: 1fr;
          }
          .ocr-flow-panel-header {
            flex-direction: column;
            align-items: stretch;
          }
          .order-history-row {
            grid-template-columns: 1fr;
            gap: 4px;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
