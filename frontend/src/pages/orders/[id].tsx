import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";
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
  extractWeekMonthId,
  formatBagCalculationResult,
  formatBagSplitBreakdown,
  formatWeekLabel,
  groupBagSummaryRowsByDate,
  normalizeBagGroupToken,
  normalizeWeekId,
  normalizeWeekValue,
  type BagRow,
  type BagSummaryRow,
} from "../../features/orders/orderDetailUtils";

type OrderDetail = {
  id: string;
  status: string;
  document: string;
  week?: string | null;
  week_value?: string | null;
  persisted_week_value?: string | null;
  week_label?: string | null;
  message_id?: string | null;
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
  candidate_evidence_run_id?: string | null;
  reparse_state?: ReparseStatePayload | null;
};

type ReparseStatePayload = {
  status?: string | null;
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
};

type CandidateResolutionPayload = {
  order_id?: string | null;
  requires_user_choice?: boolean | null;
  confidence_band?: string | null;
  critical_choices?: Array<Record<string, unknown>> | null;
  resolutions?: Record<string, CandidateResolutionEntryPayload> | null;
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
  warnings?: string[];
  table_raw?: string;
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
  figure_urls?: string[];
  synthetic?: boolean | null;
  synthetic_source?: string | null;
  pdf_variant_used?: string | null;
};

type OcrPagesMeta = {
  table_box?: number[] | null;
  table_units?: string | null;
};

type OcrSheetPayload = {
  order_id: string;
  facility_id?: string | null;
  week_id?: string | null;
  fields?: string[];
  header?: string[];
  rows?: string[][];
  row_ids?: string[];
  source?: string;
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
};

type DraftSheetJsonPayload = {
  fields?: string[] | null;
  header?: string[] | null;
  rows?: string[][] | null;
  row_ids?: string[] | null;
  rowIds?: string[] | null;
  source?: string | null;
  warnings?: string[] | null;
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
  source?: string | null;
  warnings?: string[] | null;
  review_state?: string | null;
  workflow_state?: WorkflowStatePayload | null;
  apply_gate?: ApplyGatePayload | null;
  candidate_resolution?: CandidateResolutionPayload | null;
  critical_decisions?: CriticalDecisionPayload[] | null;
  evidence_capabilities?: Record<string, boolean> | null;
  evidence_degraded_reasons?: string[] | null;
};

type NormalizedEditorSheetPayload = {
  fields: string[];
  header: string[];
  rows: string[][];
  rowIds: string[];
  source: string;
  warnings: string[];
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

type OutputPreview = {
  type: "labels" | "delivery" | "aggregate";
  headers: string[];
  rows: string[][];
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
  if (compact.includes("tea") || compact.includes("お茶")) return "tea";
  if (compact.includes("business") || compact.includes("事業")) return "business";
  if (compact.includes("diabetes") || compact.includes("糖尿")) return "diabetes";
  if (compact.includes("pregnancy") || compact.includes("妊娠")) return "pregnancy";
  if ((compact.includes("ごま") || compact.includes("sesame")) && (raw.includes("アレル") || compact.includes("allergy"))) {
    return "sesame_allergy";
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
  if (token === "tea") return "お茶";
  if (token === "business") return "事業";
  if (token === "diabetes") return "糖尿";
  if (token === "pregnancy") return "妊娠";
  if (token === "no_meat") return "禁食(肉禁)";
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
    const diet = normalizeDietTypeToken(column.header || column.name || column.diet_type || "") || "unknown";
    const area = normalizeFacilityAreaToken(column.area_id || column.header || column.name || "");
    const base = formatDietType(diet);
    return area === "X" ? base : `${base}${area}`;
  }
  return "";
};

const isQuantityRole = (role?: string | null) => String(role || "").trim().toLowerCase() === "quantity";

const normalizeFacilityTemplateColumns = (columns: unknown): FacilityTemplateColumn[] => {
  if (!Array.isArray(columns)) return [];
  return columns
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item, idx) => {
      const role = String(item.role || "").trim().toLowerCase() || "quantity";
      const header = String(item.header || "").trim();
      const name = String(item.name || "").trim();
      const dietType =
        role === "quantity"
          ? normalizeDietTypeToken(header || name || String(item.diet_type || "")) || "unknown"
          : String(item.diet_type || "");
      const areaId =
        role === "quantity"
          ? normalizeFacilityAreaToken(String(item.area_id || "") || header || name)
          : String(item.area_id || "");
      return {
        index:
          typeof item.index === "number" && Number.isFinite(item.index)
            ? Number(item.index)
            : idx,
        role,
        header: header || defaultHeaderForFacilityTemplateColumn({ role, header, name, diet_type: dietType, area_id: areaId }),
        name,
        diet_type: dietType,
        area_id: areaId,
      };
    })
    .sort((left, right) => left.index - right.index);
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

const buildFacilityTemplateColumnsPayload = (columns: FacilityTemplateColumn[]) =>
  columns.map((column, idx) => {
    const role = String(column.role || "").trim().toLowerCase() || "quantity";
    const header = String(column.header || "").trim();
    const name = String(column.name || "").trim();
    const payload: Record<string, unknown> = {
      index: idx,
      role,
    };
    if (header) payload.header = header;
    if (name) payload.name = name;
    if (role === "quantity") {
      const dietType = normalizeDietTypeToken(header || name || String(column.diet_type || "").trim()) || "unknown";
      const areaId = normalizeFacilityAreaToken(String(column.area_id || "").trim() || header || name);
      payload.diet_type = dietType;
      payload.area_id = areaId;
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
  no_meat: "禁食(肉禁)",
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
  "no_meat",
  "no_fish",
  "change_1",
  "change_2",
  "placeholder",
  "unknown",
];

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
  if (normalized === "weekly_menu_missing" || normalized === "sheet_weekly_menu_missing") {
    return "対象週の月次メニューが未登録です";
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
  const unit = line.actual_unit_type || line.menu_unit_type || "";
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

const DEFAULT_OCR_PROMPT =
  "Return a JSON object only.\\n" +
  'Schema: {"rows":[[date, menu_name, regular_2f, regular_3f, soft_2f, soft_3f, mixer_2f, mixer_3f, note], ...], "errors":[{"row":0,"col":0,"reason":"unreadable"}]}\\n' +
  "Do not output header rows or date-only headers. Output menu rows only.\\n" +
  'If no menu rows are readable, return {"rows":[], "errors":[{"row":0,"col":0,"reason":"unreadable"}]}.\\n' +
  "Use null when a cell is unreadable or missing. Use ASCII digits 0-9 only in numeric cells.";

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
  const [actionMessage, setActionMessage] = useState<string>("");
  const [trainingSampleSaving, setTrainingSampleSaving] = useState<boolean>(false);
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [pdfError, setPdfError] = useState<string>("");
  const [ocrPrompt, setOcrPrompt] = useState<string>(DEFAULT_OCR_PROMPT);
  const [ocrRawText, setOcrRawText] = useState<string>("");
  const [ocrRawMessage, setOcrRawMessage] = useState<string>("");
  const [ocrRawLoading, setOcrRawLoading] = useState<boolean>(false);
  const [ocrOutput, setOcrOutput] = useState<OcrOutput | null>(null);
  const [ocrOutputMessage, setOcrOutputMessage] = useState<string>("");
  const [ocrPages, setOcrPages] = useState<OcrPage[]>([]);
  const [ocrPagesMessage, setOcrPagesMessage] = useState<string>("");
  const [ocrPagesLoading, setOcrPagesLoading] = useState<boolean>(false);
  const [ocrTableBox, setOcrTableBox] = useState<number[] | null>(null);
  const [ocrTableUnits, setOcrTableUnits] = useState<string | null>(null);
  const [tableBoxUnitsOverride, setTableBoxUnitsOverride] = useState<string | null>(null);
  const [activeOcrPageIndex, setActiveOcrPageIndex] = useState<number>(0);
  const [ocrTableHeader, setOcrTableHeader] = useState<string[]>([]);
  const [ocrTableRows, setOcrTableRows] = useState<string[][]>([]);
  const [ocrTablePageIndex, setOcrTablePageIndex] = useState<number | null>(null);
  const [ocrSheetFields, setOcrSheetFields] = useState<string[]>([]);
  const [ocrSheetHeader, setOcrSheetHeader] = useState<string[]>([]);
  const [ocrSheetRows, setOcrSheetRows] = useState<string[][]>([]);
  const [ocrSheetRowIds, setOcrSheetRowIds] = useState<string[]>([]);
  const [ocrSheetSource, setOcrSheetSource] = useState<string>("");
  const [ocrSheetWarnings, setOcrSheetWarnings] = useState<string[]>([]);
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
  const [ocrSheetMessage, setOcrSheetMessage] = useState<string>("");
  const [ocrSheetAutoRetryBlocked, setOcrSheetAutoRetryBlocked] = useState<boolean>(false);
  const [ocrHistoryLatest, setOcrHistoryLatest] = useState<OcrEditRevision | null>(null);
  const [ocrHistoryRows, setOcrHistoryRows] = useState<OcrEditRevision[]>([]);
  const [ocrHistoryLoading, setOcrHistoryLoading] = useState<boolean>(false);
  const [ocrHistoryMessage, setOcrHistoryMessage] = useState<string>("");
  const latestSavedSheetRevisionRef = useRef<OcrEditRevision | null>(null);
  const [orderHistoryRows, setOrderHistoryRows] = useState<OrderHistoryItem[]>([]);
  const [orderHistoryLoading, setOrderHistoryLoading] = useState<boolean>(false);
  const [orderHistoryMessage, setOrderHistoryMessage] = useState<string>("");
  const [ocrTableMessage, setOcrTableMessage] = useState<string>("");
  const [ocrTableSaving, setOcrTableSaving] = useState<boolean>(false);
  const [ocrShiftStartRow, setOcrShiftStartRow] = useState<string>("");
  const [ocrShiftEndRow, setOcrShiftEndRow] = useState<string>("");
  const [showOcrEdit, setShowOcrEdit] = useState<boolean>(false);
  const [showTableBoxEditor, setShowTableBoxEditor] = useState<boolean>(false);
  const [tableBoxDraft, setTableBoxDraft] = useState<number[] | null>(null);
  const [tableBoxStep, setTableBoxStep] = useState<number>(0.005);
  const [tableBoxMessage, setTableBoxMessage] = useState<string>("");
  const [tableBoxSaving, setTableBoxSaving] = useState<boolean>(false);
  const [gridDetecting, setGridDetecting] = useState<boolean>(false);
  const [gridDetectMessage, setGridDetectMessage] = useState<string>("");
  const [facilityConfig, setFacilityConfig] = useState<Record<string, any> | null>(null);
  const [facilityTemplateColumns, setFacilityTemplateColumns] = useState<FacilityTemplateColumn[]>([]);
  const [facilityTemplateColumnDraft, setFacilityTemplateColumnDraft] = useState<FacilityTemplateColumn[]>([]);
  const [facilityTemplateSwapLeft, setFacilityTemplateSwapLeft] = useState<string>("");
  const [facilityTemplateSwapRight, setFacilityTemplateSwapRight] = useState<string>("");
  const [facilityTemplateMessage, setFacilityTemplateMessage] = useState<string>("");
  const [facilityTemplateSaving, setFacilityTemplateSaving] = useState<boolean>(false);
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
  const [llmReparseProvider, setLlmReparseProvider] = useState<string>("gemini");
  const [llmReparseModelMode, setLlmReparseModelMode] = useState<"flash" | "pro" | "other">("flash");
  const [llmReparseCustomModel, setLlmReparseCustomModel] = useState<string>("");
  const [llmReparsePromptPreset, setLlmReparsePromptPreset] = useState<
    "numeric_verification" | "column_missing" | "row_alignment" | "special_diet_semantics" | "freeform"
  >("numeric_verification");
  const [criticalDecisionSaving, setCriticalDecisionSaving] = useState<string>("");
  const [ocrRecoverPending, setOcrRecoverPending] = useState<boolean>(false);
  const [switchEvidencePending, setSwitchEvidencePending] = useState<boolean>(false);
  const [keptCurrentCandidateEvidenceId, setKeptCurrentCandidateEvidenceId] = useState<string>("");
  const [bagRows, setBagRows] = useState<BagRow[]>([]);
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
  const [activeStep, setActiveStep] = useState<number>(0);
  const [ocrEditMode, setOcrEditMode] = useState<boolean>(false);
  const [lineEditsDirty, setLineEditsDirty] = useState<boolean>(false);
  const [shippingStatuses, setShippingStatuses] = useState<ShippingStatusItem[]>([]);
  const [shippingSummary, setShippingSummary] = useState<ShippingStatusesPayload["summary"] | null>(null);
  const [shippingMessage, setShippingMessage] = useState<string>("");
  const [shippingLoading, setShippingLoading] = useState<boolean>(false);
  const overlayImageRef = useRef<HTMLImageElement | null>(null);
  const reparseTimerRef = useRef<number | null>(null);
  const orderRefreshTimerRef = useRef<number | null>(null);
  const workspaceRefreshPromiseRef = useRef<Promise<OrderDetail | null> | null>(null);

  const loadOrderDetail = async (orderId: string, options: { preserveSelections?: boolean } = {}) => {
    const { preserveSelections = false } = options;
    const res = await apiClient.get(`/orders/${orderId}`);
    const nextOrder = (res.data || {}) as OrderDetail;
    const currentPersistedFacility = (order?.facility || "").trim();
    const currentPersistedWeek = normalizeWeekValue(
      order?.persisted_week_value || order?.week_value || order?.week || "",
    );
    const selectedFacility = facility.trim();
    const selectedWeek = normalizeWeekValue(weekDraft);
    const preserveFacilitySelection =
      preserveSelections && Boolean(selectedFacility && selectedFacility !== currentPersistedFacility);
    const preserveWeekSelection =
      preserveSelections && Boolean(selectedWeek && selectedWeek !== currentPersistedWeek);
    setOrder(nextOrder);
    if (!preserveFacilitySelection) {
      setFacility(nextOrder.facility || "");
    }
    if (!preserveWeekSelection) {
      setWeekDraft(normalizeWeekValue(nextOrder.week_value || nextOrder.week || ""));
    }
    return nextOrder;
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
    setOcrPrompt(DEFAULT_OCR_PROMPT);
  }, [id]);

  useEffect(() => {
    const candidateEvidenceRunId = String(order?.workflow_state?.candidate_evidence_run_id || "").trim();
    if (!candidateEvidenceRunId) {
      if (keptCurrentCandidateEvidenceId) {
        setKeptCurrentCandidateEvidenceId("");
      }
      return;
    }
    if (
      keptCurrentCandidateEvidenceId
      && keptCurrentCandidateEvidenceId !== candidateEvidenceRunId
    ) {
      setKeptCurrentCandidateEvidenceId("");
    }
  }, [order?.workflow_state?.candidate_evidence_run_id, keptCurrentCandidateEvidenceId]);

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
    setOcrPages([]);
    setOcrPagesMessage("");
    setOcrTableBox(null);
    setOcrTableUnits(null);
    setOcrTableHeader([]);
    setOcrTableRows([]);
    setOcrTablePageIndex(null);
    setOcrSheetFields([]);
    setOcrSheetHeader([]);
    setOcrSheetRows([]);
    setOcrSheetRowIds([]);
    setOcrSheetSource("");
    setOcrSheetWarnings([]);
    resetSheetReviewMeta();
    setOcrSheetLoading(false);
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
      setPdfUrl("");
      return;
    }
    let active = true;
    let objectUrl = "";
    setPdfError("");
    apiClient
      .get(`/orders/${order.id}/document`, { responseType: "blob" })
      .then((res) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(res.data);
        setPdfUrl(objectUrl);
      })
      .catch(() => {
        if (!active) return;
        setPdfError("PDFの取得に失敗しました。");
        setPdfUrl("");
      });
    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [order?.id]);

  const normalizeSheetEditorPayload = (payload: {
    fields?: unknown;
    header?: unknown;
    rows?: unknown;
    rowIds?: unknown;
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
    const warnings = Array.isArray(payload.warnings)
      ? payload.warnings.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    return {
      fields: normalizedFields,
      header: normalizedHeader,
      rows: normalizedRows,
      rowIds: normalizedRowIds,
      source: typeof payload.source === "string" ? payload.source : "",
      warnings,
    };
  };

  const applyNormalizedSheetEditorPayload = (payload: NormalizedEditorSheetPayload) => {
    setOcrSheetFields(payload.fields);
    setOcrSheetHeader(payload.header);
    setOcrSheetRows(payload.rows);
    setOcrSheetRowIds(payload.rowIds);
    setOcrSheetSource(payload.source);
    setOcrSheetWarnings(payload.warnings);
  };

  const normalizeDraftSheetPayload = (payload?: DraftSheetPayload | null): NormalizedEditorSheetPayload => {
    const draftSheetJson =
      payload && typeof payload.draft_sheet_json === "object" && payload.draft_sheet_json
        ? payload.draft_sheet_json
        : null;
    return normalizeSheetEditorPayload({
      fields: draftSheetJson?.fields ?? payload?.fields,
      header: draftSheetJson?.header ?? payload?.header,
      rows: draftSheetJson?.rows ?? payload?.rows,
      rowIds: draftSheetJson?.row_ids ?? draftSheetJson?.rowIds ?? payload?.row_ids,
      source: String(draftSheetJson?.source || payload?.source || payload?.draft_state || "draft").trim() || "draft",
      warnings: [
        ...(Array.isArray(draftSheetJson?.warnings) ? draftSheetJson!.warnings! : []),
        ...(Array.isArray(payload?.warnings) ? payload!.warnings! : []),
        ...(Array.isArray(payload?.warnings_json) ? payload!.warnings_json! : []),
      ],
    });
  };

  const resetSheetReviewMeta = () => {
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

  const getSheetEditorPayloadFromRevision = (
    revision: OcrEditRevision | null,
  ): NormalizedEditorSheetPayload | null => {
    if (!revision || revision.ui_mode !== "sheet" || !Array.isArray(revision.rows)) {
      return null;
    }
    return normalizeSheetEditorPayload({
      fields: revision.fields,
      header: revision.header,
      rows: revision.rows,
      rowIds: revision.row_ids,
      source:
        revision.sheet_save_only || revision.sheet_save_mode === "exact"
          ? "edited_sheet_exact"
          : "edited_sheet",
      warnings: [],
    });
  };

  const rebaseSavedSheetRevisionOntoPayload = (
    basePayload: NormalizedEditorSheetPayload,
    revision: OcrEditRevision | null,
  ): NormalizedEditorSheetPayload | null => {
    const revisionPayload = getSheetEditorPayloadFromRevision(revision);
    if (!revisionPayload || !basePayload.rows.length) {
      return revisionPayload;
    }
    const revisionRowById = new Map<string, string[]>();
    revisionPayload.rowIds.forEach((rowId, idx) => {
      if (!rowId || idx >= revisionPayload.rows.length) return;
      revisionRowById.set(rowId, revisionPayload.rows[idx]);
    });
    const rebasedRows = basePayload.rows.map((baseRow, rowIdx) => {
      const baseRowId = basePayload.rowIds[rowIdx] || "";
      const revisionRow =
        revisionRowById.get(baseRowId) ||
        (rowIdx < revisionPayload.rows.length ? revisionPayload.rows[rowIdx] : null);
      if (!revisionRow) {
        return [...baseRow];
      }
      const mergedRow = [...baseRow];
      const limit = Math.min(mergedRow.length, revisionRow.length);
      for (let colIdx = 0; colIdx < limit; colIdx += 1) {
        mergedRow[colIdx] = revisionRow[colIdx] ?? "";
      }
      return mergedRow;
    });
    return {
      fields: [...basePayload.fields],
      header: [...basePayload.header],
      rows: rebasedRows,
      rowIds: [...basePayload.rowIds],
      source:
        revision?.sheet_save_only || revision?.sheet_save_mode === "exact"
          ? "edited_sheet_exact"
          : "edited_sheet",
      warnings: [...basePayload.warnings],
    };
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

  useEffect(() => {
    if (!order?.id) return;
    refreshOcrOutput(order.id);
  }, [order?.id]);

  const loadWeekOptions = async (orderId: string) => {
    setWeekOptionsLoading(true);
    setWeekOptionsError("");
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
      setWeekOptions(options);
      setWeekDraft((current) => {
        const selectedOption = options.find((item) => item.selected);
        const normalizedCurrent = normalizeWeekValue(current);
        const persistedWeekValue = normalizeWeekValue(
          order?.persisted_week_value || order?.week_value || order?.week || "",
        );
        const preserveDirtyWeekSelection = Boolean(
          normalizedCurrent && normalizedCurrent !== persistedWeekValue,
        );
        if (preserveDirtyWeekSelection) {
          return current || normalizedCurrent;
        }
        if (normalizedCurrent.includes("@")) {
          return normalizedCurrent;
        }
        if (selectedOption?.week_id) {
          return selectedOption.week_id;
        }
        return normalizedCurrent || current;
      });
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setWeekOptions([]);
      } else {
        setWeekOptionsError("週候補の取得に失敗しました。手入力で設定してください。");
      }
    } finally {
      setWeekOptionsLoading(false);
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    loadWeekOptions(order.id);
  }, [order?.id]);

  const refreshOrderWorkspace = async (
    options: {
      preserveSelections?: boolean;
      reloadSheet?: boolean;
      reloadHistory?: boolean;
      reloadBags?: boolean;
      silent?: boolean;
    } = {},
  ) => {
    const {
      preserveSelections = true,
      reloadSheet = false,
      reloadHistory = false,
      reloadBags = false,
    } = options;
    if (!id) return null;
    if (workspaceRefreshPromiseRef.current) {
      return workspaceRefreshPromiseRef.current;
    }
    const orderId = String(id);
    const refreshPromise = (async () => {
      const nextOrder = await loadOrderDetail(orderId, { preserveSelections });
      await loadWeekOptions(orderId);
      if (reloadHistory) {
        await loadOcrHistory({ silent: true });
        await loadOrderHistory({ silent: true });
      }
      if (reloadSheet) {
        await loadOcrSheet({ silent: true });
      }
      if (reloadBags) {
        await loadBags();
      }
      return nextOrder;
    })();
    workspaceRefreshPromiseRef.current = refreshPromise;
    try {
      return await refreshPromise;
    } finally {
      if (workspaceRefreshPromiseRef.current === refreshPromise) {
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
      void safeRefreshOrderWorkspace({ preserveSelections: true }, "最新状態の取得に失敗しました。");
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
    const tick = () => {
      scheduleRefresh();
      orderRefreshTimerRef.current = window.setTimeout(tick, 15000);
    };
    orderRefreshTimerRef.current = window.setTimeout(tick, 15000);
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
    order?.facility,
    order?.persisted_week_value,
    order?.week_value,
    order?.week,
    facility,
    weekDraft,
  ]);

  const loadOcrSheet = async (
    options: { silent?: boolean } = {},
  ): Promise<{
    fields: string[];
    header: string[];
    rows: string[][];
    rowIds: string[];
    source: string;
  } | null> => {
  if (!order) return null;
  if (ocrSheetLoading) return null;
  const { silent = false } = options;
    if (!silent) {
      setOcrSheetMessage("シートを取得中...");
    }
    setOcrSheetAutoRetryBlocked(false);
    setOcrSheetLoading(true);
    try {
      const res = await apiClient.get(`/orders/${order.id}/draft-sheet`);
      const payload = (res.data || {}) as DraftSheetPayload;
      const normalizedPayload = normalizeDraftSheetPayload(payload);
      const effectivePayload =
        rebaseSavedSheetRevisionOntoPayload(normalizedPayload, latestSavedSheetRevisionRef.current) ||
        normalizedPayload;
      applyNormalizedSheetEditorPayload(effectivePayload);
      applySheetReviewMeta(buildSheetReviewMetaFromOrderState(order, payload));
      setOcrSheetAutoRetryBlocked(false);
      if (!silent) {
        const reviewStateLabel = describeReviewState(
          String(payload.review_state || payload.draft_state || order?.workflow_state?.state || "").trim(),
        );
        setOcrSheetMessage(
          reviewStateLabel
            ? `${reviewStateLabel}のシートを読み込みました。`
            : effectivePayload.rows.length
              ? effectivePayload.source.startsWith("edited_sheet")
                ? "保存済みシートを読み込みました。"
                : "シートを取得しました。"
              : "シートは取得しましたが、編集対象の行がありません。",
        );
      }
      return effectivePayload;
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const savedRevisionPayload = getSheetEditorPayloadFromRevision(latestSavedSheetRevisionRef.current);
      if (savedRevisionPayload) {
        applyNormalizedSheetEditorPayload(savedRevisionPayload);
        applySheetReviewMeta(buildSheetReviewMetaFromOrderState(order, null));
        setOcrSheetAutoRetryBlocked(false);
        if (!silent) {
          setOcrSheetMessage("保存済みシートを読み込みました。");
        }
        return savedRevisionPayload;
      }
      setOcrSheetAutoRetryBlocked(true);
      resetSheetReviewMeta();
      setOcrSheetFields([]);
      setOcrSheetHeader([]);
      setOcrSheetRows([]);
      setOcrSheetRowIds([]);
      setOcrSheetSource("");
      setOcrSheetWarnings([]);
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
      setOcrSheetLoading(false);
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    if (!(order.facility || "").trim()) {
      setOcrSheetAutoRetryBlocked(true);
      setOcrSheetMessage("シートを生成できません。先に Step1（注文書）で施設設定を完了してください。");
      return;
    }
    if (!normalizeWeekValue(order.persisted_week_value || order.week || "")) {
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
      if (ocrSheetRows.length) {
        const currentSheetPayload = normalizeSheetEditorPayload({
          fields: ocrSheetFields,
          header: ocrSheetHeader,
          rows: ocrSheetRows,
          rowIds: ocrSheetRowIds,
          source: ocrSheetSource,
          warnings: ocrSheetWarnings,
        });
        const rebasedPayload = rebaseSavedSheetRevisionOntoPayload(
          currentSheetPayload,
          latestSheetRevision,
        );
        if (rebasedPayload) {
          applyNormalizedSheetEditorPayload(rebasedPayload);
        }
      }
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

  const currentDraftRevisionId = () =>
    String(
      latestSavedSheetRevisionRef.current?.revision_id ||
        order?.ocr_draft_revision_id ||
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

const loadOcrPages = async () => {
    if (!order) return;
    if (ocrPagesLoading) return;
    setOcrPagesLoading(true);
    setOcrPagesMessage("OCRページを取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/ocr-pages`);
      if (res.status === 202 || res.data?.pending) {
        setOcrPagesMessage("OCRページは処理中です。");
        setOcrPages([]);
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
      if (pages.length) {
        const table = extractFirstTable(pages);
        if (table) {
          setActiveOcrPageIndex(table.pageArrayIndex);
          setOcrTableHeader(table.header);
          setOcrTableRows(table.rows.map((row) => [...row]));
          setOcrTablePageIndex(table.pageIndex);
          setOcrTableMessage("");
        } else {
          setActiveOcrPageIndex(0);
          setTableFromPage(pages[0], 0);
        }
        setOcrPages(pages);
        setOcrPagesMessage("");
      } else {
      setOcrPages([]);
      setOcrPagesMessage("OCRページがありません。");
      setOcrTableBox(metaTableBox);
      setOcrTableUnits(metaTableUnits);
      setTableBoxUnitsOverride(null);
        setOcrTableHeader([]);
        setOcrTableRows([]);
        setOcrTablePageIndex(null);
        setOcrTableMessage("編集できる表が見つかりません。");
        setActiveOcrPageIndex(0);
      }
    } catch (err: any) {
      const status = err?.response?.status;
      setOcrPagesMessage(status === 404 ? "OCRページが見つかりません。" : "OCRページの取得に失敗しました。");
      setOcrPages([]);
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
      setActiveOcrPageIndex(0);
    } finally {
      setOcrPagesLoading(false);
    }
  };

  const loadBags = async () => {
    if (!order) return;
    setBagLoading(true);
    setBagMessage("袋分け結果を取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/bags`);
      const rows = Array.isArray(res.data?.bags) ? res.data.bags : [];
      setBagRows(rows);
      setBagMessage(rows.length ? "" : "袋分け結果がまだ生成されていません。");
    } catch (err: any) {
      setBagRows([]);
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
      setBagRows(rows);
      setBagMessage(rows.length ? "袋分けを更新しました。" : "袋分け結果がありません。");
      return true;
    } catch (err: any) {
      setBagRows([]);
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

  const selectOcrPage = (pageIndex: number) => {
    setActiveOcrPageIndex(pageIndex);
    setTableFromPage(ocrPages[pageIndex], pageIndex);
  };

  useEffect(() => {
    if (!order?.id) return;
    loadOcrPages();
  }, [order?.id]);

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
    loadBags();
  }, [order?.id]);

  const updateOcrTableCell = (
    rowIndex: number,
    cellIndex: number,
    value: string,
  ) => {
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

  const updateOcrTableHeaderCell = (
    cellIndex: number,
    value: string,
  ) => {
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
  }, [ocrPages, activeOcrPageIndex, showOcrEdit]);

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
    setOcrSheetRows((prev) => [...prev, Array.from({ length: columnCount }, () => "")]);
    setOcrSheetRowIds((prev) => [...prev, makeSheetRowId("manual")]);
  };

  const duplicateOcrTableRow = (rowIndex: number) => {
    if (ocrHardRecoveryMode) {
      setOcrTableMessage("現在は基盤復旧待ちのため、行の複製操作を止めています。");
      return;
    }
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

  const applyOcrTable = async (): Promise<{ ok: boolean; message: string }> => {
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
    const normalizedWeek = normalizeWeekValue(weekDraft);
    const persistedWeek = normalizeWeekValue(order.persisted_week_value || order.week || "");
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
    if (!effectiveCanApply && reviewBlockerText) {
      const message = `先にシートを保存（暫定）して内容を整えてください: ${reviewBlockerText}`;
      setOcrTableMessage(message);
      return { ok: false, message };
    }
    if (trimmedFacility && persistedFacility !== trimmedFacility) {
      // Users often select a facility from suggestions but forget to persist it.
      // Persist first so backend OCR apply can load the correct template.
      const ok = await saveFacility(trimmedFacility);
      if (!ok) {
        const message = "施設の設定に失敗したため、OCRテーブルを反映できませんでした。";
        setOcrTableMessage(message);
        return { ok: false, message };
      }
    }
    if (normalizedWeek && persistedWeek !== normalizedWeek) {
      const ok = await saveWeek(normalizedWeek);
      if (!ok) {
        const message = "週の設定に失敗したため、OCRテーブルを反映できませんでした。";
        setOcrTableMessage(message);
        return { ok: false, message };
      }
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
        expected_revision_id: currentDraftRevisionId() || null,
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      setOrder(res.data);
      resetSheetReviewMeta();
      setLineEditsDirty(false);
      const message = "明細に反映しました。Step3で内容を確認してください。";
      setOcrTableMessage(message);
      await refreshOrderWorkspace({ preserveSelections: true });
      await refreshOcrOutput(order.id);
      await loadOcrHistory({ silent: true });
      await loadOcrSheet({ silent: true });
      await rebuildBags();
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

  const saveOcrSheetExact = async (): Promise<{ ok: boolean; message: string }> => {
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
      await apiClient.post(`/orders/${order.id}/draft-sheet`, {
        header: targetHeader,
        rows: targetRows,
        ui_mode: "sheet",
        fields: targetFields,
        row_ids: targetRowIds,
        expected_revision_id: currentDraftRevisionId() || null,
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      const message = "シートを保存しました。次に「明細に反映して次へ」を押してください。";
      setOcrTableMessage(message);
      await refreshOrderWorkspace({ preserveSelections: true });
      await refreshOcrOutput(order.id);
      await loadOcrHistory({ silent: true });
      await loadOcrSheet({ silent: true });
      return { ok: true, message };
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

  const applyOcrAndMoveToDetails = async () => {
    if (!order) return;
    if (ocrHardRecoveryMode) {
      setActionMessage("現在は基盤復旧待ちのため、明細反映処理を止めています。");
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
    const result = await applyOcrTable();
    if (result.ok) {
      setActionMessage("明細に反映しました。Step3で内容を確認してください。");
      exitOcrEditMode();
      setActiveStep(2);
    } else {
      setActionMessage(result.message);
    }
  };

  const saveLines = async () => {
    if (!order) return;
    try {
      await apiClient.put(`/orders/${order.id}/lines`, {
        lines: order.lines || [],
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      setLineEditsDirty(false);
      await refreshOrderWorkspace({ preserveSelections: true, reloadBags: true });
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

  const confirm = async () => {
    if (!order) return;
    if (sheetWeeklyMenuMissing) {
      setActionMessage("この週の月次メニューが未登録のため、まだ確定できません。先に月次メニューを登録してください。");
      return;
    }
    if (!ocrSheetCanConfirm && ocrSheetConfirmBlockers.length) {
      const blockerText = ocrSheetConfirmBlockers.map((item) => describeReviewBlocker(item)).filter(Boolean).join(" / ");
      setActionMessage(
        blockerText
          ? `まだ確定できません。Step2で内容を整えてから再度お試しください: ${blockerText}`
          : "まだ確定できません。Step2で内容を整えてから再度お試しください。",
      );
      return;
    }
    try {
      await apiClient.post(`/orders/${order.id}/confirm`, {
        expected_revision_id: currentDraftRevisionId() || null,
        expected_lines_updated_at: order.lines_updated_at || null,
      });
      setLineEditsDirty(false);
      await refreshOrderWorkspace({ preserveSelections: true, reloadBags: true, reloadHistory: true });
      setActionMessage("確定しました。");
      loadBags();
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
        return;
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
    }
  };

  const registerTrainingSample = async () => {
    if (!order) return;
    setTrainingSampleSaving(true);
    try {
      const res = await apiClient.post(`/ocr/training-samples/from-order/${order.id}`, {
        source: "manual",
        note: "registered from order detail",
      });
      const sampleId = typeof res.data?.sample?.id === "string" ? res.data.sample.id : "";
      const lineCount =
        typeof res.data?.sample?.line_count === "number" ? res.data.sample.line_count : null;
      const lineText = lineCount == null ? "" : ` (${lineCount}行)`;
      setActionMessage(
        sampleId
          ? `学習データに登録しました。sample_id=${sampleId}${lineText}`
          : `学習データに登録しました。${lineText}`,
      );
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
    } finally {
      setTrainingSampleSaving(false);
    }
  };

  const saveFacility = async (facilityId: string): Promise<boolean> => {
    if (!order) return false;
    const trimmed = facilityId.trim();
    if (!trimmed) {
      setActionMessage("施設IDを入力してください。");
      return false;
    }
    try {
      await apiClient.post(`/orders/${order.id}/facility`, {
        facility: trimmed,
        expected_current_facility: order.facility || null,
      });
      setLineEditsDirty(false);
      await refreshOrderWorkspace({ preserveSelections: true });
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
    if (!order) return false;
    const normalizedWeek = normalizeWeekValue(weekId);
    if (!normalizedWeek) {
      setActionMessage("週の形式が不正です。候補から選択してください。");
      return false;
    }
    try {
      await apiClient.post(`/orders/${order.id}/week`, {
        week: normalizedWeek,
        expected_current_week: order.persisted_week_value || order.week_value || order.week || null,
      });
      setLineEditsDirty(false);
      await refreshOrderWorkspace({ preserveSelections: true });
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

  const updateStep1 = async () => {
    const normalizedWeek = normalizeWeekValue(weekDraft);
    if (!facility.trim()) {
      setActionMessage("施設を選択してください。");
      return;
    }
    if (!normalizedWeek) {
      setActionMessage("週を選択してください。");
      return;
    }
    const persistedFacility = (order?.facility || "").trim();
    const persistedWeek = normalizeWeekValue(
      order?.persisted_week_value || order?.week || "",
    );
    if (facility.trim() !== persistedFacility) {
      const saved = await saveFacility(facility);
      if (!saved) return;
    }
    if (normalizedWeek !== persistedWeek) {
      const saved = await saveWeek(normalizedWeek);
      if (!saved) return;
    }
    if (order?.id) {
      await loadWeekOptions(order.id);
    }
    setOcrSheetAutoRetryBlocked(false);
    if (activeStep === 1) {
      await loadOcrSheet();
    }
  };

  const chooseCriticalDecision = async (decisionType: string, selectedValue: string) => {
    if (!order?.id) return;
    const normalizedType = String(decisionType || "").trim();
    const normalizedValue = String(selectedValue || "").trim();
    if (!normalizedType || !normalizedValue) return;
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

  const keepCurrentDraft = () => {
    const candidateEvidenceRunId = String(order?.workflow_state?.candidate_evidence_run_id || "").trim();
    if (!candidateEvidenceRunId) {
      setActionMessage("現在は新しいOCR候補がありません。");
      return;
    }
    setKeptCurrentCandidateEvidenceId(candidateEvidenceRunId);
    setActionMessage("現在のシートを維持します。必要ならあとで新しいOCR候補へ切り替えられます。");
  };

  const switchDraftToLatestEvidence = async () => {
    if (!order?.id) return;
    setSwitchEvidencePending(true);
    setActionMessage("新しいOCR候補に切り替えています...");
    try {
      await apiClient.post(`/orders/${order.id}/draft-sheet/switch-evidence`);
      setKeptCurrentCandidateEvidenceId("");
      await refreshOrderWorkspace({
        preserveSelections: true,
        reloadSheet: true,
        reloadHistory: true,
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
      setFacilityTemplateMessage("施設テンプレートに保存しました。シート再読込で反映されます。");
      if (order?.id && normalizeWeekValue(order.persisted_week_value || order.week || "")) {
        await loadOcrSheet({ silent: true });
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
        setOrder({
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
            setOrder(updated);
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
        setOrder(res.data.order);
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
    if (ocrHardRecoveryMode) {
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
        setOrder(updated);
        const nextState = String(updated?.workflow_state?.state || "").trim().toLowerCase();
        const nextReparseStatus = String(updated?.workflow_state?.reparse_state?.status || "").trim().toLowerCase();
        if (nextState === "new_evidence_available") {
          setReparsePending(false);
          reparseTimerRef.current = null;
          setActionMessage("新しいOCR候補ができました。候補ブロックから切り替えるか選んでください。");
          await refreshOrderWorkspace({ preserveSelections: true, reloadSheet: true, reloadHistory: true });
          return;
        }
        if (nextState === "rerun_failed_keep_current" || nextReparseStatus === "hard_failed" || nextReparseStatus === "failed") {
          setReparsePending(false);
          reparseTimerRef.current = null;
          setActionMessage("OCRパイプライン再実行に失敗しました。現在のシートは保持されています。");
          await refreshOrderWorkspace({ preserveSelections: true, reloadSheet: true, reloadHistory: true });
          return;
        }
        if (nextState === "rerun_in_progress" || ["running", "pending", "queued"].includes(nextReparseStatus)) {
          reparseTimerRef.current = window.setTimeout(pollRerun, 5000);
          return;
        }
        setReparsePending(false);
        reparseTimerRef.current = null;
        await refreshOrderWorkspace({ preserveSelections: true, reloadSheet: true, reloadHistory: true });
        setActionMessage("OCRパイプライン再実行が完了しました。最新状態を確認してください。");
      } catch {
        setReparsePending(false);
        reparseTimerRef.current = null;
        setActionMessage("OCRパイプライン再実行の状態取得に失敗しました。最新状態を再読込してください。");
      }
    };
    try {
      const res = await apiClient.post(`/orders/${orderId}/ocr-rerun`, { stale_action: "retry" }, { timeout: 900000 });
      if (res.status === 202 || res.data?.accepted) {
        setOrder({
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
        loadOcrPages(),
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
    setOrder({ ...order, lines: next });
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

  const lines = order?.lines || [];
  const pivotRows = buildPivotRows(lines);
  const categoryOrder = buildCategoryColumns(lines).map((col) => col.key);
  const pivotGroups = groupByDateAndCategory(buildPivotCategoryRows(pivotRows), categoryOrder);
  const lineGroups = groupByDateAndCategory(
    lines.map((line, idx) => {
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
  const bagSummaryGroups = groupBagSummaryRowsByDate(buildBagSummaryRows(bagRows));
  const bagAmountStats = buildBagAmountStats(lines);
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
  let ocrStatusLabel = "未実行";
  let ocrStatusDetail = "";
  if (rawOcrStatus === "running" || rawOcrStatus === "pending" || (!rawOcrStatus && ocrPagesPending)) {
    ocrStatusLabel = "実行中";
    ocrStatusDetail = "OCRを実行中です。完了まで数分かかります。";
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
  const usingSyntheticOverlay = Boolean(activeOcrPage?.synthetic);
  const hasUsableOverlayPreview = showOcrOverlay || usingSyntheticOverlay;
  const step2CriticalBannerMessages = (() => {
    const messages: string[] = [];
    const bannerWorkflowStateCode = String(order?.workflow_state?.state || "").trim().toLowerCase();
    const bannerCandidateEvidenceRunId = String(order?.workflow_state?.candidate_evidence_run_id || "").trim();
    const bannerReparseStateStatus = String(order?.workflow_state?.reparse_state?.status || "").trim().toLowerCase();
    const bannerShowNewEvidenceChoice =
      bannerWorkflowStateCode === "new_evidence_available"
      && Boolean(
        bannerCandidateEvidenceRunId
        && bannerCandidateEvidenceRunId !== keptCurrentCandidateEvidenceId,
      );
    const bannerKeepingCurrentDraftChoice =
      bannerWorkflowStateCode === "new_evidence_available"
      && Boolean(
        bannerCandidateEvidenceRunId
        && bannerCandidateEvidenceRunId === keptCurrentCandidateEvidenceId,
      );
    const bannerRerunInProgress =
      bannerWorkflowStateCode === "rerun_in_progress"
      || bannerReparseStateStatus === "running"
      || bannerReparseStateStatus === "pending";
    const bannerSemanticShellOnly = bannerWorkflowStateCode === "semantic_shell_only";
    if (bannerShowNewEvidenceChoice) {
      messages.push("新しいOCR候補があります。切り替えるか、現在のシートを維持するかを選んでください。");
    } else if (bannerKeepingCurrentDraftChoice) {
      messages.push("現在のシートを維持しています。必要ならあとで新しいOCR候補へ切り替えられます。");
    }
    if (bannerRerunInProgress) {
      messages.push("OCRパイプラインを再実行しています。完了後に新しいOCR候補を確認してください。");
    }
    if (bannerSemanticShellOnly) {
      messages.push("メニュー枠はありますが、数量はまだ信用できません。先にOCRパイプラインを再実行してください。");
    }
    if (rawOcrStatus === "failed" || rawOcrStatus === "error") {
      if (isReparseStaleTimeoutError(order?.ocr_error)) {
        messages.push(
          "LLM補完再解析がタイムアウトしました。OCR結果は残っているため、必要なら再試行してください。",
        );
      } else {
      messages.push(order?.ocr_error ? `OCRが失敗しました: ${order.ocr_error}` : "OCRが失敗しました。");
      }
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
  const step2FallbackSummary =
    shouldFallbackToRawPdfPreview && pdfUrl
      ? "原本PDFを表示しています。右のシートを直接修正してください。"
      : "";
  const overlayPreviewModeLabel = shouldFallbackToRawPdfPreview
    ? "原本PDF (fallback)"
    : usingSyntheticOverlay
      ? `OCRプレビュー (${activeOcrPage?.pdf_variant_used === "corrected" ? "corrected PDF" : "raw PDF"})`
      : "OCRオーバーレイ";
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
  const activeEditorRows = ocrSheetRows;
  const activeEditorRowIds = ocrSheetRowIds;
  const activeEditorFields = ocrSheetFields;
  const recentOcrHistory = [...ocrHistoryRows].reverse().slice(0, 5);
  const latestOcrRevisionMode =
    ocrHistoryLatest?.ui_mode === "sheet"
      ? "シートUI"
      : ocrHistoryLatest?.ui_mode === "legacy"
        ? "旧UI"
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
  const sheetWeeklyMenuMissing = ocrSheetWarnings.includes("sheet_weekly_menu_missing");
  const ocrTableFallbackWarning = ocrSheetSource.startsWith("ocr_table") && !sheetWeeklyMenuMissing;
  const orderReviewBadges = Array.isArray(order?.ocr_review_badges)
    ? order.ocr_review_badges.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const dedupedOrderReviewBadges = Array.from(new Set(orderReviewBadges));
  const workflowStateCode = String(order?.workflow_state?.state || "").trim().toLowerCase();
  const workflowHeadline = String(order?.workflow_state?.headline || "").trim();
  const workflowApplyGate = order?.apply_gate || order?.workflow_state?.apply_gate || null;
  const workflowCandidateEvidenceRunId = String(order?.workflow_state?.candidate_evidence_run_id || "").trim();
  const criticalDecisions = Array.isArray(order?.critical_decisions)
    ? order.critical_decisions.filter((item) => item && !String(item.selected_value || "").trim())
    : Array.isArray(order?.workflow_state?.critical_decisions)
      ? (order?.workflow_state?.critical_decisions || []).filter((item) => item && !String(item.selected_value || "").trim())
      : [];
  const workflowStateLabel = describeWorkflowState(workflowStateCode);
  const step1CriticalDecisions = criticalDecisions.filter((decision) => {
    const decisionType = String(decision?.decision_type || "").trim().toLowerCase();
    return decisionType === "facility" || decisionType === "week";
  });
  const step2CriticalDecisions = criticalDecisions.filter((decision) => {
    const decisionType = String(decision?.decision_type || "").trim().toLowerCase();
    return decisionType !== "facility" && decisionType !== "week";
  });
  const hasWorkflowState = Boolean(workflowStateCode || workflowHeadline || workflowApplyGate);
  const effectiveSheetReviewState =
    ocrSheetReviewState || workflowStateCode || (!hasWorkflowState ? String(order?.ocr_review_state || "").trim() : "");
  const effectiveSheetReviewLabel = describeReviewState(effectiveSheetReviewState);
  const effectiveProcessingStage =
    ocrSheetProcessingStage || (!hasWorkflowState ? String(order?.ocr_processing_stage || "").trim() : "");
  const effectiveProcessingStageLabel = describeProcessingStage(effectiveProcessingStage);
  const effectiveConfirmedLinesRetained =
    ocrSheetConfirmedLinesRetained || (!hasWorkflowState && Boolean(order?.ocr_confirmed_lines_retained));
  const workflowBlockers = Array.isArray(workflowApplyGate?.blockers)
    ? workflowApplyGate.blockers.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const workflowWarnings = Array.isArray(workflowApplyGate?.warnings)
    ? workflowApplyGate.warnings.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const unresolvedCriticalDecisionCount = criticalDecisions.length;
  const semanticShellOnly = workflowStateCode === "semantic_shell_only";
  const rerunInProgressState = workflowStateCode === "rerun_in_progress";
  const showNewEvidenceChoice =
    workflowStateCode === "new_evidence_available" &&
    Boolean(
      workflowCandidateEvidenceRunId &&
      workflowCandidateEvidenceRunId !== keptCurrentCandidateEvidenceId,
    );
  const keepingCurrentDraftChoice =
    workflowStateCode === "new_evidence_available" &&
    Boolean(
      workflowCandidateEvidenceRunId &&
      workflowCandidateEvidenceRunId === keptCurrentCandidateEvidenceId,
    );
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
  const ocrReparseBlockedHint = (() => {
    const status = String(order?.ocr_status || "").trim().toLowerCase();
    const error = String(order?.ocr_error || "").trim();
    if (!error) return "";
    if (status !== "failed" && status !== "empty") return "";
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
  ) => {
    const persistedFacilityValue = (currentOrder?.facility || "").trim();
    const selectedFacilityValue = currentFacilityDraft.trim();
    const persistedWeekValue = normalizeWeekValue(
      currentOrder?.persisted_week_value || currentOrder?.week || "",
    );
    const selectedWeekValue = normalizeWeekValue(currentWeekDraft);
    const facilityMissingValue = !persistedFacilityValue;
    const weekMissingValue = !persistedWeekValue;
    const facilitySelectionPendingValue = Boolean(
      selectedFacilityValue && selectedFacilityValue !== persistedFacilityValue,
    );
    const weekSelectionPendingValue = Boolean(
      selectedWeekValue && selectedWeekValue !== persistedWeekValue,
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
      persistedWeek: persistedWeekValue,
      selectedWeek: selectedWeekValue,
      facilityMissing: facilityMissingValue,
      weekMissing: weekMissingValue,
      facilitySelectionPending: facilitySelectionPendingValue,
      weekSelectionPending: weekSelectionPendingValue,
      canSaveStep1:
        Boolean(selectedFacilityValue && selectedFacilityValue !== persistedFacilityValue) ||
        Boolean(selectedWeekValue && selectedWeekValue !== persistedWeekValue),
      step1Incomplete:
        facilityMissingValue ||
        weekMissingValue ||
        facilitySelectionPendingValue ||
        weekSelectionPendingValue,
      step1BlockReasons: step1BlockReasonsValue,
    };
  };
  const step1State = computeStep1State(order, facility, weekDraft);
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
  const monthlyMenuMonthId = extractWeekMonthId(selectedWeek || persistedWeek || "");
  const monthlyMenuHref = monthlyMenuMonthId ? `/menus/${monthlyMenuMonthId}` : "";
  const step1ChoiceRequired = step1CriticalDecisions.length > 0;
  const step2ChoiceRequired = step2CriticalDecisions.length > 0;
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
    return "";
  };
  const canAccessStep = (index: number) => !getStepBlockedReason(index);
  const normalizedOcrStatus = String(order?.ocr_status || "").trim().toLowerCase();
  const ocrHasEditableSheet = activeEditorRows.length > 0;
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
    !showNewEvidenceChoice &&
    !rerunInProgressState;
  const showOcrPipelineRerunAction = !step1Incomplete;
  const ocrApplyBranchEmphasis =
    !step1Incomplete && !step2ChoiceRequired && ocrHasEditableSheet && effectiveCanApply && !ocrNeedsDraftSave;
  const ocrRepairBranchEmphasis =
    !step1Incomplete &&
    (!ocrHasEditableSheet ||
      ocrNeedsDraftSave ||
      ocrProcessingNow ||
      Boolean(reviewWarningText || ocrReparseBlockedHint));
  const ocrPrimaryActionHint = (() => {
    if (workflowHeadline) return workflowHeadline;
    if (showNewEvidenceChoice) {
      return "新しいOCR候補ができました。切り替えるか、現在のシートを維持するか選んでください";
    }
    if (keepingCurrentDraftChoice) {
      return "現在のシートを維持しています。必要ならあとで新しいOCR候補に切り替えられます";
    }
    if (rerunInProgressState) {
      return "OCRパイプラインを再実行しています。完了後に新しい候補を確認してください";
    }
    if (semanticShellOnly) {
      return "メニュー枠はありますが、数量はまだ信用できません。先にOCRパイプラインを再実行してください";
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
    if (showOcrPipelineRerunAction && !ocrHasEditableSheet) {
      return "先にOCRパイプラインを再実行してOCR基盤を更新してください。";
    }
    if ((normalizedOcrStatus === "running" || normalizedOcrStatus === "pending") && effectiveProcessingStageLabel) {
      return `${effectiveProcessingStageLabel}。完了後にシートを確認してください`;
    }
    if (!ocrHasEditableSheet) return "まずシートを再読込して編集対象を表示してください";
    if (!effectiveCanApply) return "まずシートを保存（暫定）して内容を整えてください";
    if (effectiveConfirmBlockers.includes("draft_newer_than_lines")) {
      return "内容を確認して「明細に反映して次へ」を押してください";
    }
    if (reviewWarningText) return "内容を確認してから明細に反映してください";
    if (effectiveCanConfirm) return "明細確認後に確定できます";
    return "必要な数値を修正したら「明細に反映して次へ」を押してください";
  })();
  const ocrPrimaryActionNote = (() => {
    if (step1ChoiceRequired) {
      return "Step1 に戻って、施設または週の候補を先に確定してください。";
    }
    if (showNewEvidenceChoice) {
      return "現在のシートは残したままです。新しいOCR候補に切り替えると、最新のOCR基盤からシートを作り直します。";
    }
    if (keepingCurrentDraftChoice) {
      return "現在のシートで作業を続けます。切り替えが必要になったら、候補ブロックから新しいOCR候補を選べます。";
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
  const highlightApplyAction = ocrHasEditableSheet && !step1Incomplete && effectiveCanApply;
  const canSaveDraftSheet = !step1Incomplete && ocrHasEditableSheet && !ocrHardRecoveryMode && !ocrSheetAutoRetryBlocked;
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
  const nextStepButtonLabel = isLastStep
    ? "注文一覧へ戻る"
    : canStepNext
      ? `次へ: ${nextStepLabel}`
      : "次へ";
  const stepInteractionLocked = facilitySelectionPending || weekSelectionPending;
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
          normalizeWeekValue(refreshedOrder?.persisted_week_value || refreshedOrder?.week_value || refreshedOrder?.week || ""),
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
      await router.push("/orders");
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
              <h2>概要</h2>
              <span className="status-pill">{order.status}</span>
            </header>
            <div className="summary-grid">
              <div>
                <p className="field-label">注文ID</p>
                <p className="summary-value">{order.id}</p>
              </div>
              <div>
                <p className="field-label">Message ID</p>
                <p className="summary-value">{order.message_id || "不明"}</p>
              </div>
              <div>
                <p className="field-label">対象週</p>
                <p className="summary-value">
                  {formatWeekLabel(order.week_value || order.week || "", order.week_label) || "未確定"}{" "}
                  {extractWeekMonthId(order.week_value || order.week || "")
                    ? <Link href={`/menus/${extractWeekMonthId(order.week_value || order.week || "")}`}>メニュー編集</Link>
                    : null}
                </p>
              </div>
              <div>
                <p className="field-label">解析ステータス</p>
                <p className="summary-value">
                  {ocrStatusLabel}
                  {order.ocr_updated_at ? ` / ${formatTimestamp(order.ocr_updated_at)}` : ""}
                  {order.lines_updated_at ? ` / 明細:${formatTimestamp(order.lines_updated_at)}` : ""}
                </p>
                {ocrStatusDetail ? <p className="subtle">{ocrStatusDetail}</p> : null}
              </div>
              <div>
                <p className="field-label">作業状態</p>
                <p className="summary-value">
                  {workflowStateLabel || "未判定"}
                </p>
                {workflowHeadline ? <p className="subtle">{workflowHeadline}</p> : null}
                {workflowSupportText ? <p className="subtle">{workflowSupportText}</p> : null}
              </div>
            </div>
            {workflowHeadline || workflowStateLabel ? (
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
                {order?.workflow_state?.primary_action ? (
                  <span className="ocr-review-pill ocr-review-pill--state">
                    {describeWorkflowPrimaryAction(order.workflow_state.primary_action)}
                  </span>
                ) : workflowStateLabel ? (
                  <span className="ocr-review-pill ocr-review-pill--state">{workflowStateLabel}</span>
                ) : null}
              </div>
            ) : null}
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
            {actionMessage && <p className="message">{actionMessage}</p>}
          </section>

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
                  <h2>注文書 (FAX PDF)</h2>
                  <p className="subtle">原本PDFを確認し、施設と週設定を完了してください。</p>
                </div>
                {pdfUrl ? (
                  <a href={pdfUrl} target="_blank" rel="noreferrer" className="ghost-link">
                    原本を開く
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
                      disabled={facilityOptionsLoading}
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
                    {weekOptions.length ? (
                      <select
                        className="input"
                        value={weekDraft}
                        onChange={(e) => setWeekDraft(e.target.value)}
                        disabled={weekOptionsLoading}
                      >
                        <option value="">週を選択</option>
                        {weekDraft && !weekOptions.some((option) => option.week_id === weekDraft) ? (
                          <option value={weekDraft}>{formatWeekLabel(weekDraft) || weekDraft} (現在値)</option>
                        ) : null}
                        {weekOptions.map((option) => (
                          <option key={option.week_id} value={option.week_id}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        className="input"
                        type="month"
                        value={weekDraft}
                        onChange={(e) => setWeekDraft(e.target.value)}
                      />
                    )}
                    {weekOptionsError ? (
                      <span className="subtle">{weekOptionsError}</span>
                    ) : weekOptionsLoading ? (
                      <span className="subtle">週候補を取得中...</span>
                    ) : weekOptions.length ? (
                      <span className="subtle">メニュー期間から選択できます。</span>
                    ) : (
                      <span className="subtle">候補がないため YYYY-MM を手入力してください。</span>
                    )}
                  </label>
                  <button className="btn" onClick={updateStep1} disabled={!canSaveStep1}>
                    {facilitySelectionPending || weekSelectionPending || step1Incomplete ? "設定を保存" : "設定済み"}
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
                      <button className="btn ghost" type="button" onClick={loadOcrPages} disabled={ocrPagesLoading}>
                        {ocrPagesLoading ? "取得中..." : "OCRページを更新"}
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
                  {step2CriticalBannerMessages.length ? (
                    <div className="critical-alert">
                      <p className="critical-alert-title">
                        {shouldFallbackToRawPdfPreview
                          ? "OCR結果を利用できないため、原本PDFへフォールバックしています。"
                          : usingSyntheticOverlay
                            ? "OCR overlay artifact が無いため、PDFプレビューを比較表示しています。"
                            : "OCR結果に注意が必要です。"}
                      </p>
                      <ul className="critical-alert-list">
                        {step2CriticalBannerMessages.map((message, idx) => (
                          <li key={`step2-critical-${idx}`}>{message}</li>
                        ))}
                      </ul>
                      {showOcrRecoveryAction ? (
                        <p className="subtle">
                          「OCR基盤を復旧」は、OCRの基盤(生データ/構造化表/ページ参照)を再構築し、手入力前の作業土台を戻します。
                        </p>
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
                  <div className="ocr-workspace">
                    <div className="ocr-workspace-tools">
                      <div className="ocr-edit">
                        <div className="ocr-edit-header">
                          <div>
                            <p className="subtle">編集対象: シートテンプレート</p>
                            <h3 className="ocr-edit-title">左を見ながら、右のシートの数字だけを確認します。</h3>
                            <p className="subtle">いま必要な操作だけを下の分岐に沿って進めてください。</p>
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
                            <div className={`ocr-flow-step ${ocrNeedsDraftSave || ocrApplyBranchEmphasis ? "active" : ""}`}>
                              <span className="ocr-flow-step-index">2</span>
                              <div>
                                <p className="ocr-flow-step-title">数字は正しい？</p>
                                <p className="ocr-flow-step-note">
                                  正しければ明細へ反映します。怪しければ、手で直すか再解析を使います。
                                </p>
                              </div>
                            </div>
                          </div>
                          <div className="ocr-flow-branches">
                            <section className={`ocr-flow-branch ${ocrApplyBranchEmphasis ? "is-primary" : ""}`}>
                              <p className="ocr-flow-branch-label">はい / 修正済み</p>
                              <h4>明細に反映して次へ進む</h4>
                              <p className="subtle">
                                {ocrNeedsDraftApply
                                  ? "保存済みの下書きがまだ明細に反映されていません。問題なければ反映します。"
                                  : "右のシート内容が正しければ、そのまま明細へ反映します。"}
                              </p>
                              <button
                                className={applySheetButtonClassName}
                                onClick={applyOcrAndMoveToDetails}
                                disabled={ocrTableSaving || step1Incomplete || !canApplyOcrSheet}
                              >
                                {ocrTableSaving ? "反映中..." : "明細に反映して次へ"}
                              </button>
                            </section>
                            <section className={`ocr-flow-branch ${ocrRepairBranchEmphasis ? "is-primary" : ""}`}>
                              <p className="ocr-flow-branch-label">いいえ / 迷う</p>
                              <h4>基盤を整えてから、必要なら候補選択とLLM補完へ進む</h4>
                              <p className="subtle">
                                {ocrProcessingNow
                                  ? describeReparseProgressMessage(effectiveProcessingStage, {
                                      llmAssist: true,
                                      providerLabel: llmReparseProvider,
                                    }) || "いま再解析中です。完了後にもう一度シートを確認してください。"
                                  : "先にシートを保存して下書きを残し、必要な時だけ再解析を使います。"}
                              </p>
                              <div className="ocr-remediation-groups">
                                <section className="ocr-remediation-group">
                                  <p className="ocr-remediation-group-label">基盤</p>
                                  <h5>作業土台を整える</h5>
                                  <p className="subtle">
                                    数量が怪しい時も、まずは現在のシートを残しつつ OCR 基盤を更新します。
                                  </p>
                                  <div className="ocr-flow-branch-actions">
                                    <button
                                      className={saveSheetButtonClassName}
                                      type="button"
                                      onClick={saveOcrSheetExact}
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
                                  </div>
                                </section>
                                <section className="ocr-remediation-group">
                                  <p className="ocr-remediation-group-label">候補</p>
                                  <h5>OCR候補や解釈候補を決める</h5>
                                  <p className="subtle">
                                    新しい OCR 候補や複数候補がある時だけ、ここで選んでから修正を続けます。
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
                                          onClick={keepCurrentDraft}
                                          disabled={switchEvidencePending}
                                        >
                                          今のシートを維持
                                        </button>
                                      </div>
                                    </div>
                                  ) : keepingCurrentDraftChoice ? (
                                    <div className="ocr-evidence-switch-card ocr-evidence-switch-card--muted">
                                      <p className="ocr-evidence-switch-title">現在のシートを維持中です</p>
                                      <p className="subtle">
                                        新しい OCR 候補は保持しています。必要になったら切り替えを再度選べます。
                                      </p>
                                    </div>
                                  ) : null}
                                  {renderCriticalDecisionPanel(step2CriticalDecisions, {
                                    title: "OCR修正前に候補を確定",
                                    note: "列やテンプレート解釈が競合したときだけ表示されます。ここで選ぶと、下のシート確認と反映にそのまま使います。",
                                  }) || (
                                    <p className="subtle ocr-remediation-empty">
                                      現在、追加で選ぶ OCR 候補はありません。
                                    </p>
                                  )}
                                </section>
                                <section className="ocr-remediation-group ocr-remediation-group--llm">
                                  <p className="ocr-remediation-group-label">LLM</p>
                                  <h5>どう補完するかを選ぶ</h5>
                                  <p className="subtle">
                                    基盤や候補が固まってから、必要な時だけ LLM 補完再解析を使います。
                                  </p>
                                  <div className="ocr-flow-branch-actions">
                                    <select
                                      className="input llm-model-select"
                                      value={llmReparsePromptPreset}
                                      onChange={(event) =>
                                        setLlmReparsePromptPreset(
                                          event.target.value as
                                            | "numeric_verification"
                                            | "column_missing"
                                            | "row_alignment"
                                            | "special_diet_semantics"
                                            | "freeform",
                                        )
                                      }
                                      disabled={reparsePending || step1Incomplete || ocrRecoverPending || ocrHardRecoveryMode || !ocrHasEditableSheet}
                                    >
                                      <option value="numeric_verification">数字検証優先</option>
                                      <option value="column_missing">列欠損・見切れ補完</option>
                                      <option value="row_alignment">行ずれ・区分ずれ補正</option>
                                      <option value="special_diet_semantics">特殊食・禁食優先</option>
                                      <option value="freeform">自由入力中心</option>
                                    </select>
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
                                        rows={18}
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
                                        rows={14}
                                        value={ocrPrompt}
                                        onChange={(e) => setOcrPrompt(e.target.value)}
                                        placeholder="例: 読みづらい手書き数量は前後セルの連続性を見て補完する"
                                      />
                                    </details>
                                  )}
                                </section>
                              </div>
                            </section>
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
                                onClick={() => loadOcrSheet()}
                                disabled={ocrSheetLoading || step1Incomplete || ocrHardRecoveryMode}
                              >
                                {ocrSheetLoading ? "再読込中..." : "シート再読込"}
                              </button>
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
                                <span className="subtle">日付・区分・メニューは固定し、数量列だけ動かします。</span>
                              </div>
                            ) : (
                              <p className="subtle">現在は基盤復旧待ちのため、数量シフト操作は停止しています。</p>
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
                                            ? "旧UI"
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
                          <span className="subtle">{overlayPreviewModeLabel}</span>
                          {activeOcrPageLabel != null ? (
                            <span className="subtle">Page {activeOcrPageLabel}</span>
                          ) : null}
                        </div>
                        <div className="edit-hint active">
                          {shouldFallbackToRawPdfPreview
                            ? step2FallbackSummary || "原本PDFを見ながら、右のシートだけを更新します。"
                            : "左は比較用です。編集は右のシートだけを更新します。"}
                        </div>
                        {shouldFallbackToRawPdfPreview ? (
                          pdfUrl ? (
                            <iframe
                              title="order-pdf-fallback"
                              src={pdfUrl}
                              className="pdf-frame pdf-frame-compact"
                            />
                          ) : (
                            <div className="preview-placeholder">{pdfError || "PDFを読み込み中..."}</div>
                          )
                        ) : showOcrOverlay ? (
                          <div className="ocr-preview-wrapper">
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
                          </div>
                        ) : (
                          <div className="preview-placeholder">{ocrOverlayPlaceholder}</div>
                        )}
                        {!shouldFallbackToRawPdfPreview && usingSyntheticOverlay ? (
                          <p className="subtle">
                            OCR overlay artifact が無いため、PDFレンダリング画像を比較表示しています。
                          </p>
                        ) : null}
                        {!shouldFallbackToRawPdfPreview ? (
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
                        {!shouldFallbackToRawPdfPreview && showLayoutOverlay ? (
                          showLayoutOverlayImage ? (
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
                          ) : (
                            <div className="preview-placeholder">{layoutOverlayPlaceholder}</div>
                          )
                        ) : null}
                      </div>
                    </div>
                    <div className="ocr-workspace-editor">
                      <div className="ocr-edit ocr-edit--sheet">
                        {activeEditorRows.length ? (
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
                                    className={[
                                      "ocr-sheet-row",
                                      ocrSheetRowDateStripeClasses[rowIdx] || "ocr-sheet-row-date-a",
                                      ocrSheetRowBoundaryClasses[rowIdx] || "",
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
                                      <td
                                        key={`ocr-sheet-cell-${rowIdx}-${cellIdx}`}
                                        className={`${ocrSheetColumnSpecs[cellIdx]?.className || ""} ${ocrSheetStickyColumnOffsets[cellIdx] != null ? "ocr-sheet-sticky-left-cell" : ""}`}
                                        style={
                                          ocrSheetStickyColumnOffsets[cellIdx] != null
                                            ? { left: `${ocrSheetStickyColumnOffsets[cellIdx]}px` }
                                            : undefined
                                        }
                                      >
                                        <input
                                          className={`input ocr-sheet-input ${ocrSheetColumnSpecs[cellIdx]?.className || ""}`}
                                          value={row[cellIdx] ?? ""}
                                          onChange={(e) => updateOcrTableCell(rowIdx, cellIdx, e.target.value)}
                                        />
                                      </td>
                                    ))}
                                    <td className="ocr-sheet-action-cell">
                                      <div className="ocr-row-actions">
                                        <button
                                          className="btn ghost"
                                          type="button"
                                          onClick={() => duplicateOcrTableRow(rowIdx)}
                                          disabled={ocrHardRecoveryMode}
                                        >
                                          複製
                                        </button>
                                        <button
                                          className="btn ghost"
                                          type="button"
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
                              className="btn primary"
                              type="button"
                              onClick={saveFacilityTemplateColumns}
                              disabled={!facilityTemplateDirty || facilityTemplateSaving || !facility || step1Incomplete}
                            >
                              {facilityTemplateSaving ? "保存中..." : "施設テンプレに保存"}
                            </button>
                          </div>
                        </div>
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
                                  <th>diet_type</th>
                                  <th>area_id</th>
                                  <th>操作</th>
                                </tr>
                              </thead>
                              <tbody>
                                {facilityTemplateColumnDraft.map((column, idx) => (
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
                                      <input
                                        className="input"
                                        value={column.header || ""}
                                        onChange={(event) =>
                                          updateFacilityTemplateColumn(idx, "header", event.target.value)
                                        }
                                      />
                                    </td>
                                    <td>
                                      <input
                                        className="input"
                                        value={column.name || ""}
                                        onChange={(event) =>
                                          updateFacilityTemplateColumn(idx, "name", event.target.value)
                                        }
                                      />
                                    </td>
                                    <td>
                                      <input
                                        className="input"
                                        value={column.diet_type || ""}
                                        disabled={!isQuantityRole(column.role)}
                                        onChange={(event) =>
                                          updateFacilityTemplateColumn(idx, "diet_type", event.target.value)
                                        }
                                      />
                                    </td>
                                    <td>
                                      <input
                                        className="input"
                                        value={column.area_id || ""}
                                        disabled={!isQuantityRole(column.role)}
                                        onChange={(event) =>
                                          updateFacilityTemplateColumn(idx, "area_id", event.target.value)
                                        }
                                      />
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
                                      </div>
                                    </td>
                                  </tr>
                                ))}
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
              <h2>区分別一覧</h2>
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
              <h2>明細 (編集)</h2>
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
                            <th>OCR</th>
                            <th>修正</th>
                            <th>差分</th>
                            <th>実量</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map(({ line, idx }) => (
                            <tr key={line.line_id || idx}>
                              <td>{line.menu_name || "-"}</td>
                              <td>{line.daypart || "-"}</td>
                              <td>{formatBagTypeLabel(line.bag_type, bagTypeLabelMap)}</td>
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
              <p className="subtle">数量や袋数を確認し、必要なら保存してから次の作業へ進みます。</p>
            </header>
            <div className="actions">
              <button className="btn ghost" onClick={saveLines}>
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
                  onClick={registerTrainingSample}
                  disabled={trainingSampleSaving}
                >
                  {trainingSampleSaving ? "登録中..." : "学習データ登録"}
                </button>
                <button className="btn primary" onClick={confirm} disabled={!effectiveCanConfirm}>
                  確定
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
              disabled={!isLastStep && !canStepNext}
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
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin-bottom: 20px;
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
          margin-bottom: 16px;
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
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
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
          margin-top: 14px;
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
          margin-top: 14px;
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
          grid-template-columns: minmax(420px, 0.92fr) minmax(640px, 1.28fr);
          margin-bottom: 16px;
          align-items: start;
        }

        .ocr-workspace-tools {
          grid-column: 1 / -1;
        }

        .ocr-workspace-preview,
        .ocr-workspace-editor {
          min-width: 0;
        }

        .ocr-edit--sheet {
          height: 100%;
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
          padding: 10px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
        }

        .ocr-edit-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 10px;
        }

        .ocr-edit-title {
          margin: 4px 0 0;
          font-size: 18px;
          color: #223431;
        }

        .ocr-editor-mode-switch {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
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
          padding: 14px;
          border-radius: 14px;
          border: 1px solid rgba(24, 42, 40, 0.1);
          background: #f3f5f2;
          display: flex;
          flex-direction: column;
          gap: 12px;
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
          gap: 10px;
          align-items: stretch;
        }

        .ocr-flow-step {
          display: flex;
          gap: 10px;
          align-items: flex-start;
          padding: 12px;
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
          font-size: 14px;
          font-weight: 700;
          color: #243431;
        }

        .ocr-flow-step-note {
          margin: 4px 0 0;
          font-size: 12px;
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

        .ocr-flow-branch {
          padding: 14px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.12);
          background: #ffffff;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ocr-flow-branch.is-primary {
          border-color: rgba(31, 42, 42, 0.26);
          box-shadow: inset 0 0 0 1px rgba(31, 42, 42, 0.08);
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
          gap: 10px;
          align-items: center;
        }

        .ocr-remediation-groups {
          display: grid;
          gap: 12px;
        }

        .ocr-remediation-group {
          padding: 12px;
          border-radius: 12px;
          border: 1px solid rgba(24, 42, 40, 0.1);
          background: #f9faf8;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ocr-remediation-group--llm {
          background: #fcfcfb;
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
          padding: 12px;
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
          min-height: 420px;
          resize: vertical;
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
          min-height: 420px;
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
          gap: 6px;
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
