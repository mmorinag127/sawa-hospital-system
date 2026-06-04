import { type KeyboardEvent, type MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/router";
import Link from "next/link";

import { apiClient } from "../../../services/apiClient";
import TopNav from "../../../components/TopNav";
import {
  deriveWeekValueFromCalendarRange,
  formatWeekLabel,
  isConcreteWeekValue,
  normalizeConcreteWeekValue,
  normalizeWeekValue,
} from "../../../features/orders/orderDetailUtils";

type WorkflowV2 = {
  order_id: string;
  state: string;
  headline?: string | null;
  primary_action?: string | null;
  selected_ocr_result_id?: string | null;
  saved_sheet_id?: string | null;
  confirmed_snapshot_id?: string | null;
  facility_id?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  template_id?: string | null;
  template_version_id?: string | null;
  expanded_cell_copy_mode?: ExpandedCellCopyMode | null;
  context_suggestion?: ContextSuggestion | null;
  bagging_result_id?: string | null;
  output_bundle_id?: string | null;
  pre_save_checks?: PreSaveChecks | null;
  blockers?: string[] | null;
  warnings?: string[] | null;
  quad_override?: {
    quad_px?: number[][];
    quad_source?: string | null;
    decision?: string | null;
    created_at?: string | null;
  } | null;
  ocr_job?: {
    ocr_job_id?: string | null;
    status?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    elapsed_seconds?: number | null;
    processing_stage?: string | null;
    result_state?: string | null;
    progress_step?: number | null;
    progress_total?: number | null;
    progress_label?: string | null;
    error?: string | null;
    error_message?: string | null;
    error_detail?: string | null;
    error_user_message?: string | null;
    recovery_action?: string | null;
  } | null;
};

type OrderVersion = {
  version_no?: number | null;
  document_id?: string | null;
  message_id?: string | null;
  received_at?: string | null;
  is_current?: boolean | null;
};

type OrderDetail = {
  id?: string | null;
  version_count?: number | null;
  current_version?: OrderVersion | null;
  versions?: OrderVersion[] | null;
};

type PreSaveCheckEntry = {
  confirmed?: boolean;
  sheet_hash?: string | null;
  confirmed_at?: string | null;
  anomaly_review_id?: string | null;
};

type PreSaveChecks = {
  anomaly_review?: PreSaveCheckEntry | null;
  sheet_review?: PreSaveCheckEntry | null;
};

type PreSaveStatus = {
  sheet_hash?: string | null;
  anomaly_review_confirmed?: boolean;
  sheet_review_confirmed?: boolean;
  ready?: boolean;
};

type QuadReviewPayload = {
  order_id: string;
  status?: string | null;
  image_png_base64?: string | null;
  image_size?: number[] | null;
  suggested_quad_px?: number[][] | null;
  saved_override?: {
    quad_px?: number[][];
    quad_source?: string | null;
    decision?: string | null;
    created_at?: string | null;
  } | null;
  estimate?: {
    status?: string | null;
    reasons?: string[];
    warnings?: string[];
    refined_quad_px?: number[][];
    initial_quad_px?: number[][];
    corner_shift_px?: Record<string, number>;
    metrics?: Record<string, Record<string, number>>;
  } | null;
  tolerance_policy?: Record<string, number>;
  ocr_job?: WorkflowV2["ocr_job"];
};

type HeaderAxisReviewPayload = {
  order_id: string;
  status?: string | null;
  image_png_base64?: string | null;
  image_size?: number[] | null;
  crop_box?: number[] | null;
  canvas_size?: number[] | null;
  x_positions?: number[] | null;
  y_levels?: number[] | null;
  saved_override?: {
    corrected_xs?: number[];
    coordinate_space?: { mode?: string; width?: number; height?: number };
  } | null;
  axis_evidence?: Record<string, unknown> | null;
  coordinate_space?: { mode?: string; width?: number; height?: number } | null;
};

type FacilitySuggestionCandidate = {
  facility_id?: string | null;
  id?: string | null;
  facility_name?: string | null;
  name?: string | null;
  score?: number | string | null;
  reason?: string | null;
};

type ContextSuggestion = {
  source?: string | null;
  facility_id?: string | null;
  facility_name?: string | null;
  facility_candidates?: FacilitySuggestionCandidate[];
  week_code?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  week_label?: string | null;
  date_hints?: string[];
  confidence?: string | null;
  created_at?: string | null;
};

type OcrResult = {
  ocr_result_id: string;
  status?: string | null;
  source?: string | null;
  selected?: boolean;
  artifact_digest?: string | null;
  artifact_manifest?: Record<string, unknown> | null;
  overlay_url?: string | null;
  sheet_review_base_url?: string | null;
  overlay_status?: string | null;
  overlay_message?: string | null;
  created_at?: string | null;
};

type InspectionPayload = {
  workflow?: WorkflowV2;
  ocr_results?: OcrResult[];
  saved_sheet?: {
    saved_sheet_id: string;
    source_ocr_result_id?: string | null;
    sheet?: Record<string, unknown>;
    edited_at?: string | null;
    created_at?: string | null;
  } | null;
  artifact_lineage?: Record<string, unknown>;
  bagging_result?: Record<string, unknown> | null;
  anomaly_review?: Record<string, unknown> | null;
  pre_save_checks?: PreSaveChecks | null;
  pre_save_status?: PreSaveStatus | null;
  output_bundle?: Record<string, unknown> | null;
};

type SheetPayload = {
  fields: string[];
  header: string[];
  rows: string[][];
  row_ids?: string[];
  cell_confidence_rows?: string[][];
  cell_provenance_rows?: string[][];
  ocr_numeric_cell_items?: OcrNumericCellItem[];
  ocr_numeric_cell_summary?: Record<string, unknown>;
  target_cell_map?: TargetCellMapItem[];
  [key: string]: unknown;
};

type OcrNumericCellItem = {
  classification?: string | null;
  value?: string | null;
  confidence_tier?: string | null;
  target_row_index?: number | null;
  target_col_index?: number | null;
  placement_basis?: string | null;
};

type SheetAutoEditPatch = {
  row_index: number;
  col_index: number;
  field?: string | null;
  label?: string | null;
  current_value?: string | null;
  suggested_value?: string | null;
  confidence?: string | null;
  reason?: string | null;
  evidence?: string | null;
  alternatives?: string[];
  source?: string | null;
};

type SheetAutoEditResult = {
  status?: string | null;
  patches?: SheetAutoEditPatch[];
  rule_patches?: SheetAutoEditPatch[];
  llm_patches?: SheetAutoEditPatch[];
  llm?: Record<string, unknown> | null;
  job?: {
    job_id?: string | null;
    status?: string | null;
    started_at?: string | null;
    updated_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
  } | null;
};

type SheetAnomalyWarning = {
  type?: string | null;
  severity?: string | null;
  row_index?: number | null;
  col_index?: number | null;
  field?: string | null;
  label?: string | null;
  value?: string | null;
  suggested_value?: string | null;
  message?: string | null;
  date?: string | null;
  daypart?: string | null;
  menu?: string | null;
  context_label?: string | null;
  evidence?: Record<string, unknown> | null;
};

type TargetCellMapItem = {
  target_row_index: number;
  target_col_index: number;
  field?: string | null;
  sheet_cell?: string | null;
  target_cell_id?: string | null;
  bbox?: number[] | null;
  center?: number[] | null;
};

type OverlayBox = {
  left: number;
  top: number;
  width: number;
  height: number;
  centerLeft: number;
  centerTop: number;
};

type ConfidenceDisplayMode = "strict" | "assisted" | "suggestion";
type Step3LayoutMode = "side-by-side" | "stacked";
type OcrPreviewMode = "overlay" | "original" | "sheet";
type OcrRunMode = "hakodate" | "llm";
type OutputPreviewType = "labels" | "delivery" | "order_form_saved_sheet" | "aggregate";
type OutputPreview = {
  type: OutputPreviewType;
  headers: string[];
  rows: string[][];
};
type LlmPromptPreset =
  | "numeric_verification"
  | "column_missing"
  | "row_alignment"
  | "special_diet_semantics"
  | "merged_cell_quantity_spans"
  | "freeform";

const HAKODATE_REVIEW_CANVAS = {
  canvasXPadding: 40,
  pastedImageX: 20,
  pastedImageYGap: 20,
  rectifiedCanvasWidth: 2362,
  rectifiedCanvasHeight: 4273,
};
const AI_REVIEW_REQUEST_TIMEOUT_MS = 180000;
const DEFAULT_HEADER_AXIS_REQUEST_TIMEOUT_MS = 300000;
const MIN_HEADER_AXIS_REQUEST_TIMEOUT_MS = 30000;
const MAX_HEADER_AXIS_REQUEST_TIMEOUT_MS = 900000;

type FacilityOption = {
  id: string;
  name: string;
};

type FaxTemplateOption = {
  template_id: string;
  label?: string | null;
  description?: string | null;
  template_family?: string | null;
  template_version?: number | string | null;
  quantity_headers?: string[];
};

type FacilityTemplateStatus = {
  facilityId: string;
  templateId: string;
  templateIds: string[];
  facilityTemplateId: string;
  loading: boolean;
  error: string;
};

type ExpandedCellCopyMode = "auto" | "enabled" | "disabled";

type FacilityTemplateColumn = {
  index: number;
  source_index?: number;
  role: string;
  header?: string;
  header_group?: string;
  name?: string;
  diet_type?: string;
  area_id?: string;
};

type WeekOption = {
  week_id: string;
  label: string;
  date_from?: string | null;
  date_to?: string | null;
  selected?: boolean;
};

const emptyContext = {
  facility_id: "",
  week_start: "",
  week_end: "",
};

const defaultSheet = {
  rows: [
    {
      date: "",
      daypart: "",
      menu_name: "",
    },
  ],
};

const formatJson = (value: unknown) => JSON.stringify(value ?? null, null, 2);

const formatApiError = (err: any, fallback: string) => {
  const detail = err?.response?.data?.detail;
  if (detail === "facility_template_unresolved") {
    return "施設テンプレートが未登録です。下の「施設テンプレート登録」で帳票レイアウトを登録してから、Step1を保存してください。";
  }
  if (detail === "selected_ocr_required" || detail === "selected_ocr_missing") {
    return "正解OCRが未選択です。Step2で使用するOCR結果を一つ選んでから、シートを保存してください。";
  }
  if (detail === "fax_template_id_required") return "帳票レイアウトを選択してください。";
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const error = (detail as { error?: unknown }).error;
    if (error === "validation_error") {
      const validationErrorLabels: Record<string, string> = {
        template_source_index_missing: "列の物理位置を自動解決できませんでした。ページを再読み込みしてから、列設定をもう一度保存してください。",
        template_source_index_invalid: "列の物理位置が不正です。ページを再読み込みしてから、列設定をもう一度保存してください。",
        template_source_index_duplicate: "同じ物理列に複数の列が割り当てられています。列の追加/削除後に再読み込みしてから保存してください。",
        template_quantity_columns_missing: "数量列がありません。少なくとも1つの数量列を残してください。",
        template_columns_missing: "保存する列がありません。",
      };
      const validation = (detail as { validation?: { errors?: unknown; warnings?: unknown } }).validation;
      const errors = Array.isArray(validation?.errors)
        ? validation.errors
            .map((item) => String(item || "").trim())
            .filter(Boolean)
            .map((item) => validationErrorLabels[item] || item)
        : [];
      const message = (detail as { message?: unknown }).message;
      const prefix = typeof message === "string" && message.trim()
        ? message.trim()
        : "施設テンプレート列の検証に失敗しました。";
      return errors.length ? `${prefix} ${errors.join(" / ")}` : prefix;
    }
    if (error === "menu_entries_missing" || error === "monthly_menu_object_missing" || error === "monthly_menu_lookup_failed") {
      return "対象週の月次メニューが未登録です。メニューを登録してからOCRを実行してください。";
    }
    if (error === "monthly_menu_facility_scope_missing") {
      return "対象施設の月次メニュー差分を解決できません。メニュー設定を確認してください。";
    }
    if (error === "fax_template_not_found") return "存在しない帳票レイアウトが指定されています。";
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
    return JSON.stringify(detail);
  }
  return String(err?.message || fallback);
};

const formatFaxTemplateOptionLabel = (option: FaxTemplateOption | null | undefined) => {
  if (!option) return "";
  const main = option.description || option.label || option.template_family || option.template_id;
  return main === option.template_id ? option.template_id : `${main} (${option.template_id})`;
};

const normalizeExpandedCellCopyMode = (value?: unknown): ExpandedCellCopyMode => {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "enabled" || normalized === "disabled" || normalized === "auto") return normalized;
  return "auto";
};

const normalizeDietTypeToken = (value?: string | null) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const compact = raw
    .toLowerCase()
    .replace(/[\s　]+/g, "")
    .replace(/[＿_]/g, "")
    .replace(/[／/・+＋-]/g, "")
    .replace(/[()（）\[\]【】]/g, "");
  if (!compact) return "";
  if ((compact.includes("袋") || compact.includes("bag")) && (compact.includes("regular") || compact.includes("常食") || compact.includes("通常") || compact === "常")) return "regular_bag";
  if (compact.includes("regular") || compact.includes("常食") || compact.includes("通常")) return "regular";
  if (compact.includes("daycare") || compact.includes("通所")) return "daycare";
  if (compact.includes("staff") || compact.includes("職員")) return "staff";
  if (compact.includes("揚げ物禁") || compact.includes("揚物禁") || compact.includes("nofried") || compact.includes("friedfree")) return "no_fried";
  if (compact.includes("tea") || compact.includes("お茶")) return "tea";
  if (compact.includes("business") || compact.includes("事業")) return "business";
  if (compact.includes("diabetes") || compact.includes("糖尿")) return "diabetes";
  if (compact.includes("pregnancy") || compact.includes("妊娠")) return "pregnancy";
  if ((compact.includes("ごま") || compact.includes("sesame")) && (raw.includes("アレル") || compact.includes("allergy"))) return "sesame_allergy";
  if ((compact.includes("肉") || compact.includes("meat")) && (compact.includes("卵") || compact.includes("玉子") || compact.includes("egg")) && (compact.includes("魚") || compact.includes("鯖") || compact.includes("さば") || compact.includes("fish"))) return "forbidden_other";
  if (compact.includes("nomeat") || compact.includes("nobeef") || compact.includes("禁食肉禁") || compact.includes("肉禁")) return "no_meat";
  if (compact.includes("nofish") || compact.includes("禁食魚禁") || compact.includes("魚禁")) return "no_fish";
  if (compact.includes("change1") || compact.includes("変更1")) return "change_1";
  if (compact.includes("change2") || compact.includes("変更2")) return "change_2";
  if (compact === "-" || compact === "placeholder") return "placeholder";
  if (compact === "unknown" || compact === "不明" || compact === "none") return "unknown";
  const hasSoft = compact.includes("soft") || compact.includes("軟");
  const hasMixer = compact.includes("mixer") || compact.includes("mix") || compact.includes("ミキサ");
  if (hasSoft && hasMixer) return "soft_mixer";
  if (hasSoft) return "soft";
  if (hasMixer) return "mixer";
  return compact;
};

const normalizeFacilityAreaToken = (value?: string | null) => {
  const raw = String(value || "").trim();
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
  if (["x", "all", "common", "共通", "none", "null", "na", "n/a", "なし"].includes(compact)) return "X";
  return compact.toUpperCase();
};

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

const formatDietType = (value?: string | null) => {
  const token = normalizeDietTypeToken(value || "");
  return dietTypeLabels[token] || value || "不明";
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
    const diet = normalizeDietTypeToken(column.diet_type || column.name || "") || "unknown";
    const area = normalizeFacilityAreaToken(column.area_id || column.name || "");
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
  header_group: "",
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
      const headerGroup = String(item.header_group || "").trim();
      const name = String(item.name || "").trim();
      const dietType = role === "quantity"
        ? normalizeDietTypeToken(String(item.diet_type || header || name || "")) || "unknown"
        : String(item.diet_type || "");
      const areaId = role === "quantity"
        ? normalizeFacilityAreaToken(String(item.area_id || header || name || ""))
        : String(item.area_id || "");
      return {
        index: typeof item.index === "number" && Number.isFinite(item.index) ? Number(item.index) : idx,
        source_index: typeof item.source_index === "number" && Number.isFinite(item.source_index) ? Number(item.source_index) : undefined,
        role,
        header: header || defaultHeaderForFacilityTemplateColumn({ role, name, diet_type: dietType, area_id: areaId }),
        header_group: headerGroup,
        name: name || defaultNameForFacilityTemplateColumn({ role, diet_type: dietType, area_id: areaId }),
        diet_type: dietType,
        area_id: areaId,
      };
    })
    .sort((left, right) => left.index - right.index)
    .map((column, idx) => ({ ...column, index: idx }));
};

const reindexFacilityTemplateColumns = (columns: FacilityTemplateColumn[]) =>
  columns.map((column, idx) => ({ ...column, index: idx }));

const swapFacilityTemplateColumns = (columns: FacilityTemplateColumn[], leftIndex: number, rightIndex: number) => {
  if (leftIndex === rightIndex || leftIndex < 0 || rightIndex < 0 || leftIndex >= columns.length || rightIndex >= columns.length) {
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
    const headerGroup = String(column.header_group || "").trim();
    const payload: Record<string, unknown> = { index: idx, role };
    if (typeof column.source_index === "number" && Number.isFinite(column.source_index)) payload.source_index = Number(column.source_index);
    if (header) payload.header = header;
    if (role === "quantity" && headerGroup) payload.header_group = headerGroup;
    return payload;
  });

const columnRoleOptions = [
  { value: "date", label: "日付" },
  { value: "daypart", label: "区分" },
  { value: "menu_name", label: "メニュー" },
  { value: "quantity", label: "数量" },
  { value: "note", label: "備考" },
];

const normalizeSheetPayload = (value: unknown): SheetPayload | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const rows = Array.isArray(raw.rows)
    ? raw.rows
        .filter((row): row is unknown[] => Array.isArray(row))
        .map((row) => row.map((cell) => String(cell ?? "")))
    : [];
  const width = Math.max(
    Array.isArray(raw.fields) ? raw.fields.length : 0,
    Array.isArray(raw.header) ? raw.header.length : 0,
    ...rows.map((row) => row.length),
    1,
  );
  const fields = Array.from({ length: width }, (_, idx) => {
    const valueAtIndex = Array.isArray(raw.fields) ? raw.fields[idx] : undefined;
    return String(valueAtIndex ?? `col${idx + 1}`);
  });
  const header = Array.from({ length: width }, (_, idx) => {
    const valueAtIndex = Array.isArray(raw.header) ? raw.header[idx] : undefined;
    return String(valueAtIndex ?? fields[idx] ?? `col${idx + 1}`);
  });
  return {
    ...raw,
    fields,
    header,
    rows: rows.map((row) => Array.from({ length: width }, (_, idx) => String(row[idx] ?? ""))),
    cell_confidence_rows: Array.isArray(raw.cell_confidence_rows)
      ? raw.cell_confidence_rows
          .filter((row): row is unknown[] => Array.isArray(row))
          .map((row) => Array.from({ length: width }, (_, idx) => String(row[idx] ?? "")))
      : undefined,
    cell_provenance_rows: Array.isArray(raw.cell_provenance_rows)
      ? raw.cell_provenance_rows
          .filter((row): row is unknown[] => Array.isArray(row))
          .map((row) => Array.from({ length: width }, (_, idx) => String(row[idx] ?? "")))
      : undefined,
    ocr_numeric_cell_items: Array.isArray(raw.ocr_numeric_cell_items)
      ? raw.ocr_numeric_cell_items.filter((item): item is OcrNumericCellItem => Boolean(item && typeof item === "object"))
      : undefined,
    ocr_numeric_cell_summary: raw.ocr_numeric_cell_summary && typeof raw.ocr_numeric_cell_summary === "object"
      ? raw.ocr_numeric_cell_summary as Record<string, unknown>
      : undefined,
    target_cell_map: Array.isArray(raw.target_cell_map)
      ? raw.target_cell_map.filter((item): item is TargetCellMapItem => Boolean(item && typeof item === "object"))
      : undefined,
  };
};

const stateLabel = (state?: string | null) => {
  const normalized = String(state || "").trim();
  const labels: Record<string, string> = {
    uploaded: "Step1: PDF/施設/週次確認",
    context_confirmed: "Step1完了: OCR実行待ち",
    ocr_blocked: "Step1要対応: OCR実行不可",
    ocr_running: "Step1: OCR実行中",
    ocr_failed: "Step1要対応: OCR失敗",
    ocr_selected: "Step2完了: 正解OCR選択済み",
    sheet_saved: "Step3完了: シート保存済み",
    bagging_ready: "Step4: 出力確認",
    bagging_confirmed: "Step4: 出力確認",
    output_review: "Step4: 出力確認",
    confirmed: "確定済み",
  };
  return labels[normalized] || normalized || "未開始";
};

const describeWorkflowBlocker = (code: string) => {
  const normalized = String(code || "").trim();
  const labels: Record<string, string> = {
    menu_entries_missing: "対象週の月次メニューが未登録です。メニューを登録してからOCRを実行してください。",
    monthly_menu_object_missing: "対象月の月次メニューが未登録です。メニューを登録してからOCRを実行してください。",
    monthly_menu_lookup_failed: "対象週の月次メニューを解決できません。メニュー登録を確認してください。",
    monthly_menu_facility_scope_missing: "対象施設の月次メニュー差分を解決できません。メニュー設定を確認してください。",
    week_unresolved: "週次が未確定です。Step1で週次を確定してください。",
    facility_template_unresolved: "施設テンプレートが未登録です。",
    quad_estimation_failed: "FAXの表外枠4点を自動推定できませんでした。4点補正を確認してからOCRを再実行してください。",
    template_resolution_failed: "施設テンプレートを解決できませんでした。施設テンプレートと施設区分列を確認してください。",
    hakodate_live_rerun_failed: "箱館方式のOCR処理中に失敗しました。詳細を確認してから再実行してください。",
  };
  return labels[normalized] || normalized;
};

const stepIndexForState = (state?: string | null) => {
  const normalized = String(state || "").trim();
  if (["uploaded", "context_confirmed", "ocr_blocked", "ocr_running", "ocr_failed"].includes(normalized)) return 1;
  if (["ocr_completed"].includes(normalized)) return 2;
  if (["ocr_selected"].includes(normalized)) return 3;
  if (["sheet_saved"].includes(normalized)) return 4;
  if (["bagging_ready"].includes(normalized)) return 4;
  if (["bagging_confirmed"].includes(normalized)) return 4;
  if (["output_review", "confirmed"].includes(normalized)) return 4;
  return 1;
};

const baseStepLabels = [
  { step: 1, label: "PDF/施設/週次" },
  { step: 2, label: "OCR選択" },
  { step: 3, label: "シート編集" },
  { step: 4, label: "出力確認" },
];

const formatFacilityLabel = (facility: FacilityOption) => {
  if (!facility.name) return facility.id;
  return `${facility.name} (${facility.id})`;
};

const weekValueFromRange = (weekStart?: string | null, weekEnd?: string | null) =>
  normalizeConcreteWeekValue(deriveWeekValueFromCalendarRange(weekStart || "", weekEnd || ""));

const weekRangeFromValue = (value?: string | null) => {
  const normalized = normalizeConcreteWeekValue(value);
  const match = normalized.match(/^\d{4}-\d{2}@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
  if (!match) return { week_start: "", week_end: "" };
  return { week_start: match[1], week_end: match[2] };
};

const formatElapsedSeconds = (value?: number | null) => {
  if (typeof value !== "number" || Number.isNaN(value)) return "未計測";
  if (value < 60) return `${value.toFixed(1)}秒`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value - minutes * 60);
  return `${minutes}分${String(seconds).padStart(2, "0")}秒`;
};

const formatOcrProgress = (workflow?: WorkflowV2 | null) => {
  const job = workflow?.ocr_job;
  if (!job) return "-";
  const step = typeof job.progress_step === "number" ? job.progress_step : null;
  const total = typeof job.progress_total === "number" ? job.progress_total : null;
  const label = String(job.progress_label || job.processing_stage || "").trim();
  if (step && total) return `${step}/${total}${label ? ` ${label}` : ""}`;
  return label || String(job.status || "-");
};

const formatDateTime = (value?: string | null) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

const contextSuggestionWeekValue = (suggestion?: ContextSuggestion | null) => (
  normalizeConcreteWeekValue(suggestion?.week_code)
  || weekValueFromRange(suggestion?.week_start, suggestion?.week_end)
);

const contextSuggestionWeekLabel = (suggestion?: ContextSuggestion | null) => {
  const value = contextSuggestionWeekValue(suggestion);
  return formatWeekLabel(value) || suggestion?.week_label || value || "未推定";
};

const formatFacilityCandidate = (candidate: FacilitySuggestionCandidate) => {
  const id = String(candidate.facility_id || candidate.id || "").trim();
  const name = String(candidate.facility_name || candidate.name || "").trim();
  const score = candidate.score === undefined || candidate.score === null ? "" : ` / score ${candidate.score}`;
  const reason = candidate.reason ? ` / ${candidate.reason}` : "";
  return `${name || id || "施設候補"}${id && name ? ` (${id})` : ""}${score}${reason}`;
};

const confidenceTierVisible = (tier: string | null | undefined, mode: ConfidenceDisplayMode) => {
  const normalized = String(tier || "").trim().toLowerCase();
  if (!normalized) return true;
  if (mode === "suggestion") return ["high", "medium", "low"].includes(normalized);
  if (mode === "assisted") return ["high", "medium"].includes(normalized);
  return normalized === "high";
};

const classificationVisible = (classification: string | null | undefined, mode: ConfidenceDisplayMode) => {
  const normalized = String(classification || "").trim().toLowerCase();
  if (!normalized || normalized === "accepted") return true;
  if (mode === "suggestion") return ["deterministic_candidate", "weak_candidate"].includes(normalized);
  if (mode === "assisted") return normalized === "deterministic_candidate";
  return false;
};

const isLockedSheetField = (field: string) => ["date_mmdd", "date", "daypart", "menu", "menu_name"].includes(String(field || "").trim());

const stickyLeftForSheetField = (field: string, colIdx: number) => {
  const normalized = String(field || "").trim();
  if (!isLockedSheetField(normalized)) return undefined;
  if (normalized === "date_mmdd" || normalized === "date" || colIdx === 0) return 34;
  if (normalized === "daypart" || colIdx === 1) return 86;
  return 128;
};

const sheetWidthClass = (field: string) => {
  const normalized = String(field || "").trim();
  if (normalized === "date_mmdd" || normalized === "date") return "sheet-col-date";
  if (normalized === "daypart") return "sheet-col-daypart";
  if (normalized === "menu" || normalized === "menu_name") return "sheet-col-menu";
  return "sheet-col-quantity";
};

const sheetFieldIndex = (fields: string[], candidates: string[]) => {
  const normalizedCandidates = new Set(candidates.map((value) => String(value || "").trim()));
  return fields.findIndex((field) => normalizedCandidates.has(String(field || "").trim()));
};

const effectiveSheetValue = (rows: string[][], rowIndex: number, colIndex: number) => {
  if (colIndex < 0) return "";
  for (let idx = rowIndex; idx >= 0; idx -= 1) {
    const value = String(rows[idx]?.[colIndex] || "").trim();
    if (value) return value;
  }
  return "";
};

const daypartToneClass = (value: string) => {
  const normalized = String(value || "").trim();
  if (normalized.includes("朝")) return "sheet-row-morning";
  if (normalized.includes("昼")) return "sheet-row-noon";
  if (normalized.includes("夕") || normalized.includes("夜")) return "sheet-row-evening";
  return "";
};

const sheetRowClassName = (sheet: SheetPayload, rowIndex: number) => {
  const dateIndex = sheetFieldIndex(sheet.fields, ["date_mmdd", "date"]);
  const daypartIndex = sheetFieldIndex(sheet.fields, ["daypart"]);
  const dateValue = effectiveSheetValue(sheet.rows, rowIndex, dateIndex);
  const previousDateValue = rowIndex > 0 ? effectiveSheetValue(sheet.rows, rowIndex - 1, dateIndex) : "";
  const daypartValue = effectiveSheetValue(sheet.rows, rowIndex, daypartIndex);
  const previousDaypartValue = rowIndex > 0 ? effectiveSheetValue(sheet.rows, rowIndex - 1, daypartIndex) : "";
  const dateChanged = rowIndex > 0 && Boolean(dateValue && previousDateValue && dateValue !== previousDateValue);
  const daypartChanged = rowIndex > 0 && !dateChanged && Boolean(daypartValue && previousDaypartValue && daypartValue !== previousDaypartValue);
  return [
    daypartToneClass(daypartValue),
    dateChanged ? "sheet-row-date-boundary" : "",
    daypartChanged ? "sheet-row-daypart-boundary" : "",
  ].filter(Boolean).join(" ");
};

const normalizeDietLabel = (value?: unknown) => {
  const raw = String(value || "").trim();
  const normalized = raw.toLowerCase().replace(/[\s　_()-]+/g, "");
  if (!raw) return "-";
  if (["regular", "常食"].includes(normalized)) return "常食";
  if (["diabetes", "糖尿"].includes(normalized)) return "糖尿";
  if (["staff", "職員"].includes(normalized)) return "職員";
  if (["daycare", "通所"].includes(normalized)) return "通所";
  if (["nomeat", "肉禁"].includes(normalized)) return "肉禁";
  if (["nofish", "魚禁"].includes(normalized)) return "魚禁";
  if (["nofried", "揚げ物禁", "揚禁"].includes(normalized)) return "揚げ物禁";
  if (["change1", "変更1"].includes(normalized)) return "変更1";
  if (["change2", "変更2"].includes(normalized)) return "変更2";
  if (["remarks", "note", "備考"].includes(normalized)) return "備考";
  return raw;
};

const formatWorkflowQuantity = (value?: unknown) => {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
};

const formatWorkflowBagType = (value?: unknown) => {
  const raw = String(value || "").trim();
  if (!raw) return "標準";
  if (raw === "standard") return "標準";
  if (raw === "condiment") return "付属品";
  return raw;
};

const extractWorkflowBagRows = (baggingResult?: Record<string, unknown> | null) => {
  const raw = Array.isArray(baggingResult?.bag_rows)
    ? baggingResult.bag_rows
    : Array.isArray(baggingResult?.quantity_cells)
      ? baggingResult.quantity_cells
      : [];
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    .map((item, idx) => ({
      id: String(item.line_index ?? item.source_row_index ?? idx),
      date: String(item.date || "-"),
      daypart: String(item.daypart || "-"),
      menu_name: String(item.menu_name || "-"),
      diet_type: String(item.diet_type || ""),
      area_id: String(item.area_id || ""),
      bag_type: String(item.bag_type || "standard"),
      quantity: item.quantity,
    }));
};

const buildWorkflowBagSummaryRows = (bagRows: ReturnType<typeof extractWorkflowBagRows>) => {
  const map = new Map<string, {
    id: string;
    date: string;
    daypart: string;
    menu_name: string;
    diet_type: string;
    area_id: string;
    bag_type: string;
    total_quantity: number;
    bag_count: number;
    breakdowns: string[];
  }>();
  bagRows.forEach((row) => {
    const key = [row.date, row.daypart, row.menu_name, row.diet_type, row.area_id, row.bag_type].join("\u0000");
    const quantity = typeof row.quantity === "number" ? row.quantity : Number(row.quantity);
    const current = map.get(key) || {
      id: key,
      date: row.date,
      daypart: row.daypart,
      menu_name: row.menu_name,
      diet_type: row.diet_type,
      area_id: row.area_id,
      bag_type: row.bag_type,
      total_quantity: 0,
      bag_count: 0,
      breakdowns: [],
    };
    current.bag_count += 1;
    if (Number.isFinite(quantity)) {
      current.total_quantity += quantity;
      current.breakdowns.push(formatWorkflowQuantity(quantity));
    }
    map.set(key, current);
  });
  return Array.from(map.values()).sort((a, b) => (
    a.date.localeCompare(b.date, "ja")
    || a.daypart.localeCompare(b.daypart, "ja")
    || a.menu_name.localeCompare(b.menu_name, "ja")
    || a.diet_type.localeCompare(b.diet_type, "ja")
    || a.area_id.localeCompare(b.area_id, "ja")
    || a.bag_type.localeCompare(b.bag_type, "ja")
  ));
};

const groupWorkflowBagRowsByDate = (rows: ReturnType<typeof buildWorkflowBagSummaryRows>) => {
  const map = new Map<string, typeof rows>();
  rows.forEach((row) => {
    const key = row.date || "-";
    const current = map.get(key) || [];
    current.push(row);
    map.set(key, current);
  });
  return Array.from(map.entries()).map(([date, rows]) => ({ date, rows }));
};

const isBagColumnHeader = (header?: string) => {
  const normalized = String(header || "").trim().toLowerCase();
  return Boolean(normalized && (normalized.includes("bag") || normalized.includes("袋")));
};

const formatOutputPreviewCell = (cell: string, header?: string) => {
  if (!isBagColumnHeader(header)) return cell;
  return formatWorkflowBagType(cell);
};

const unionOverlayBoxes = (items: Array<{ box: OverlayBox }>): OverlayBox | null => {
  if (!items.length) return null;
  const left = Math.min(...items.map((item) => item.box.left));
  const top = Math.min(...items.map((item) => item.box.top));
  const right = Math.max(...items.map((item) => item.box.left + item.box.width));
  const bottom = Math.max(...items.map((item) => item.box.top + item.box.height));
  const width = Math.max(right - left, 1);
  const height = Math.max(bottom - top, 1);
  return {
    left,
    top,
    width,
    height,
    centerLeft: left + width / 2,
    centerTop: top + height / 2,
  };
};

const llmPromptPresetLabels: Record<LlmPromptPreset, string> = {
  numeric_verification: "数字検証優先",
  column_missing: "列欠損・見切れ補完",
  row_alignment: "行ずれ・区分ずれ補正",
  special_diet_semantics: "特殊食・禁食優先",
  merged_cell_quantity_spans: "結合セルまたがり数量",
  freeform: "自由入力中心",
};

const formatAiStatus = (status: unknown) => {
  const value = String(status || "").trim();
  if (!value) return "未実行";
  const labels: Record<string, string> = {
    ok: "完了",
    failed: "失敗",
    disabled: "無効",
    skipped_no_api_key: "APIキーなし",
    not_run: "未実行",
  };
  return labels[value] || value;
};

const formatAnomalySeverity = (severity: unknown) => {
  const value = String(severity || "").trim();
  const labels: Record<string, string> = {
    high: "高",
    medium: "中",
    low: "低",
  };
  return labels[value] || value || "-";
};

const anomalyEvidenceKeys = (warning: SheetAnomalyWarning) => {
  const evidence = warning.evidence;
  const keys = evidence?.keys;
  return keys && typeof keys === "object" && !Array.isArray(keys)
    ? keys as Record<string, unknown>
    : {};
};

const anomalyContextValue = (warning: SheetAnomalyWarning, key: "date" | "daypart" | "menu") => {
  const direct = String(warning[key] || "").trim();
  if (direct) return direct;
  const evidence = warning.evidence;
  const evidenceValue = evidence && typeof evidence === "object" ? String(evidence[key] || "").trim() : "";
  if (evidenceValue) return evidenceValue;
  return String(anomalyEvidenceKeys(warning)[key] || "").trim();
};

const formatAnomalyBasis = (warning: SheetAnomalyWarning) => {
  const evidence = warning.evidence || {};
  const baseline = typeof evidence.baseline === "number" || typeof evidence.baseline === "string"
    ? String(evidence.baseline)
    : "";
  const type = String(warning.type || "").trim();
  if (baseline) return `基準 ${baseline}`;
  return type || "-";
};

const sameSheetPatchTarget = (
  left: { row_index?: number | null; col_index?: number | null; suggested_value?: string | null },
  right: { row_index?: number | null; col_index?: number | null; suggested_value?: string | null },
) => (
  String(left.row_index ?? "").trim() === String(right.row_index ?? "").trim()
  && String(left.col_index ?? "").trim() === String(right.col_index ?? "").trim()
  && String(left.suggested_value || "").trim() === String(right.suggested_value || "").trim()
);

const removeFirstMatchingItem = <T,>(items: T[], matcher: (item: T) => boolean) => {
  let removed = false;
  return items.filter((item) => {
    if (!removed && matcher(item)) {
      removed = true;
      return false;
    }
    return true;
  });
};

const removeItemsForSheetCell = <T extends { row_index?: number | null; col_index?: number | null }>(
  items: T[],
  rowIndex: number,
  colIndex: number,
) => items.filter((item) => Number(item.row_index) !== rowIndex || Number(item.col_index) !== colIndex);

const anomalyReviewWithoutWarning = (
  review: Record<string, unknown> | null | undefined,
  warning: SheetAnomalyWarning,
) => {
  const source = review && typeof review === "object" && !Array.isArray(review) ? review : {};
  const warnings = Array.isArray(source.warnings)
    ? removeFirstMatchingItem(
        source.warnings.filter((item): item is SheetAnomalyWarning => Boolean(item && typeof item === "object")),
        (item) => sameSheetPatchTarget(item, warning),
      )
    : [];
  const summary = source.summary && typeof source.summary === "object" && !Array.isArray(source.summary)
    ? {
        ...source.summary,
        warning_count: warnings.length,
      }
    : { warning_count: warnings.length };
  return { ...source, warnings, summary };
};

export default function OrderWorkflowV2Page() {
  const router = useRouter();
  const orderId = typeof router.query.id === "string" ? router.query.id : "";
  const [workflow, setWorkflow] = useState<WorkflowV2 | null>(null);
  const [orderDetail, setOrderDetail] = useState<OrderDetail | null>(null);
  const [ocrResults, setOcrResults] = useState<OcrResult[]>([]);
  const [inspection, setInspection] = useState<InspectionPayload | null>(null);
  const [contextForm, setContextForm] = useState(emptyContext);
  const [weekDraft, setWeekDraft] = useState<string>("");
  const [weekOptions, setWeekOptions] = useState<WeekOption[]>([]);
  const [weekOptionsLoading, setWeekOptionsLoading] = useState<boolean>(false);
  const [weekOptionsError, setWeekOptionsError] = useState<string>("");
  const [facilityOptions, setFacilityOptions] = useState<FacilityOption[]>([]);
  const [facilityOptionsLoading, setFacilityOptionsLoading] = useState(false);
  const [facilityOptionsError, setFacilityOptionsError] = useState("");
  const [faxTemplateOptions, setFaxTemplateOptions] = useState<FaxTemplateOption[]>([]);
  const [faxTemplateOptionsLoading, setFaxTemplateOptionsLoading] = useState(false);
  const [faxTemplateOptionsError, setFaxTemplateOptionsError] = useState("");
  const [facilityTemplateStatus, setFacilityTemplateStatus] = useState<FacilityTemplateStatus>({
    facilityId: "",
    templateId: "",
    templateIds: [],
    facilityTemplateId: "",
    loading: false,
    error: "",
  });
  const [facilityResolvedConfig, setFacilityResolvedConfig] = useState<Record<string, any> | null>(null);
  const [facilityTemplateColumns, setFacilityTemplateColumns] = useState<FacilityTemplateColumn[]>([]);
  const [facilityTemplateColumnDraft, setFacilityTemplateColumnDraft] = useState<FacilityTemplateColumn[]>([]);
  const [facilityTemplateMessage, setFacilityTemplateMessage] = useState("");
  const [facilityTemplateSaving, setFacilityTemplateSaving] = useState(false);
  const [showFacilityTemplateEditor, setShowFacilityTemplateEditor] = useState(false);
  const [facilityTemplateSwapLeft, setFacilityTemplateSwapLeft] = useState("");
  const [facilityTemplateSwapRight, setFacilityTemplateSwapRight] = useState("");
  const [selectedFacilityTemplateId, setSelectedFacilityTemplateId] = useState("");
  const [expandedCellCopyMode, setExpandedCellCopyMode] = useState<ExpandedCellCopyMode>("auto");
  const [expandedCellCopySaving, setExpandedCellCopySaving] = useState(false);
  const [showExceptionRange, setShowExceptionRange] = useState(false);
  const [customWeekRangeStart, setCustomWeekRangeStart] = useState<string>("");
  const [customWeekRangeEnd, setCustomWeekRangeEnd] = useState<string>("");
  const [sheetJson, setSheetJson] = useState(formatJson(defaultSheet));
  const [sheetJsonStale, setSheetJsonStale] = useState(false);
  const [sheetPayload, setSheetPayload] = useState<SheetPayload | null>(null);
  const [visibleStep, setVisibleStep] = useState(1);
  const [quadReview, setQuadReview] = useState<QuadReviewPayload | null>(null);
  const [quadReviewLoading, setQuadReviewLoading] = useState(false);
  const [quadReviewMessage, setQuadReviewMessage] = useState("");
  const [manualQuadMode, setManualQuadMode] = useState(false);
  const [manualQuadPoints, setManualQuadPoints] = useState<number[][]>([]);
  const [headerAxisReview, setHeaderAxisReview] = useState<HeaderAxisReviewPayload | null>(null);
  const [headerAxisLoading, setHeaderAxisLoading] = useState(false);
  const [headerAxisMessage, setHeaderAxisMessage] = useState("");
  const [headerAxisXs, setHeaderAxisXs] = useState<number[]>([]);
  const [draggingHeaderAxisIndex, setDraggingHeaderAxisIndex] = useState<number | null>(null);
  const [headerAxisTimeoutSeconds, setHeaderAxisTimeoutSeconds] = useState(
    String(DEFAULT_HEADER_AXIS_REQUEST_TIMEOUT_MS / 1000),
  );
  const [selectedHeaderAxisIndex, setSelectedHeaderAxisIndex] = useState<number | null>(null);
  const [headerAxisAddMode, setHeaderAxisAddMode] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [pdfError, setPdfError] = useState<string>("");
  const [selectedDocumentId, setSelectedDocumentId] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [step3LayoutMode, setStep3LayoutMode] = useState<Step3LayoutMode>("side-by-side");
  const [ocrPreviewMode, setOcrPreviewMode] = useState<OcrPreviewMode>("overlay");
  const [preSaveChecks, setPreSaveChecks] = useState<PreSaveChecks>({});
  const [preSaveStatus, setPreSaveStatus] = useState<PreSaveStatus>({});
  const [focusedSheetCell, setFocusedSheetCell] = useState<{ rowIndex: number; colIndex: number } | null>(null);
  const sheetPayloadRef = useRef<SheetPayload | null>(null);
  const pendingSheetCellEditsRef = useRef<Map<string, { rowIndex: number; colIndex: number; value: string }>>(new Map());
  const [ocrConfidenceDisplayMode, setOcrConfidenceDisplayMode] = useState<ConfidenceDisplayMode>("strict");
  const [sheetAutoEditResult, setSheetAutoEditResult] = useState<SheetAutoEditResult | null>(null);
  const [localAnomalyReview, setLocalAnomalyReview] = useState<Record<string, unknown> | null>(null);
  const [selectedAutoEditIndex, setSelectedAutoEditIndex] = useState<number | null>(null);
  const [selectedAnomalyIndex, setSelectedAnomalyIndex] = useState<number | null>(null);
  const [columnFillTarget, setColumnFillTarget] = useState<string>("");
  const [columnFillValue, setColumnFillValue] = useState<string>("");
  const [swapLeftColumn, setSwapLeftColumn] = useState<string>("");
  const [swapRightColumn, setSwapRightColumn] = useState<string>("");
  const [ocrRunMode, setOcrRunMode] = useState<OcrRunMode>("hakodate");
  const [llmProvider, setLlmProvider] = useState<string>("openai");
  const [llmPromptPreset, setLlmPromptPreset] = useState<LlmPromptPreset>("numeric_verification");
  const [llmModelMode, setLlmModelMode] = useState<"flash" | "pro" | "other">("flash");
  const [llmCustomModel, setLlmCustomModel] = useState<string>("");
  const [ocrPrompt, setOcrPrompt] = useState<string>("");
  const [downloadMessage, setDownloadMessage] = useState<string>("");
  const [outputPreview, setOutputPreview] = useState<OutputPreview | null>(null);
  const [outputPreviewMessage, setOutputPreviewMessage] = useState<string>("");
  const [outputPreviewLoading, setOutputPreviewLoading] = useState<boolean>(false);
  const [overlayImageSize, setOverlayImageSize] = useState({ naturalWidth: 0, naturalHeight: 0, width: 0, height: 0 });
  const overlayImageRef = useRef<HTMLImageElement | null>(null);
  const sheetAutoEditPollRef = useRef<number | null>(null);
  const selectedOcr = useMemo(
    () => ocrResults.find((item) => item.selected || item.ocr_result_id === workflow?.selected_ocr_result_id) || null,
    [ocrResults, workflow?.selected_ocr_result_id],
  );
  const documentVersionOptions = useMemo(() => {
    const versions = Array.isArray(orderDetail?.versions) ? orderDetail.versions : [];
    return [...versions]
      .filter((version) => String(version.document_id || "").trim())
      .sort((left, right) => Number(left.version_no || 0) - Number(right.version_no || 0));
  }, [orderDetail?.versions]);
  const currentDocumentId = String(
    orderDetail?.current_version?.document_id
    || documentVersionOptions.find((version) => version.is_current)?.document_id
    || "",
  ).trim();
  const effectiveSelectedDocumentId = selectedDocumentId || currentDocumentId;
  const selectedDocumentVersion = documentVersionOptions.find(
    (version) => String(version.document_id || "").trim() === effectiveSelectedDocumentId,
  ) || null;
  const invalidateSheetPreSaveChecks = () => {
    setPreSaveChecks({});
    setPreSaveStatus({});
  };
  const markSheetJsonStale = () => {
    setSheetJsonStale(true);
  };
  const applyPreSaveState = (
    checks: PreSaveChecks | null | undefined,
    status?: PreSaveStatus | null,
  ) => {
    const normalized = checks || {};
    setPreSaveChecks(normalized);
    setPreSaveStatus(status || {});
  };
  const selectedOcrSheetReviewBaseUrl = String(selectedOcr?.sheet_review_base_url || "").trim();
  const selectedOcrOverlayUrl = String(selectedOcr?.overlay_url || "").trim();
  const step3SheetReviewImageUrl = selectedOcrSheetReviewBaseUrl || selectedOcrOverlayUrl;
  const step3PreviewImageUrl = ocrPreviewMode === "sheet" ? step3SheetReviewImageUrl : selectedOcrOverlayUrl;
  const step3PreviewPdfUrl = ocrPreviewMode === "original" || (ocrPreviewMode === "sheet" && !step3PreviewImageUrl)
    ? pdfUrl
    : "";
  const canRenderSheetReviewValues = ocrPreviewMode === "sheet" && Boolean(selectedOcrSheetReviewBaseUrl);
  const effectiveAnomalyReviewConfirmed = Boolean(preSaveStatus.anomaly_review_confirmed);
  const effectiveSheetReviewConfirmed = Boolean(preSaveStatus.sheet_review_confirmed);
  const canSaveSheet = Boolean(
    workflow?.selected_ocr_result_id
    && sheetPayload
    && effectiveAnomalyReviewConfirmed
    && effectiveSheetReviewConfirmed,
  );
  const getHeaderAxisTimeoutMs = () => {
    const seconds = Number(headerAxisTimeoutSeconds);
    const normalizedSeconds = Number.isFinite(seconds) && seconds > 0
      ? seconds
      : DEFAULT_HEADER_AXIS_REQUEST_TIMEOUT_MS / 1000;
    return Math.min(
      MAX_HEADER_AXIS_REQUEST_TIMEOUT_MS,
      Math.max(MIN_HEADER_AXIS_REQUEST_TIMEOUT_MS, Math.round(normalizedSeconds * 1000)),
    );
  };
  const getHeaderAxisExpectedCount = () => {
    const evidence = headerAxisReview?.axis_evidence;
    if (!evidence || typeof evidence !== "object") return 0;
    const direct = Number((evidence as Record<string, unknown>).template_x_count);
    if (Number.isFinite(direct) && direct > 0) return Math.round(direct);
    const templateXs = (evidence as Record<string, unknown>).template_xs;
    if (Array.isArray(templateXs) && templateXs.length > 0) return templateXs.length;
    return 0;
  };

  const selectedWeekValue = useMemo(
    () => normalizeConcreteWeekValue(weekDraft) || weekValueFromRange(contextForm.week_start, contextForm.week_end),
    [contextForm.week_end, contextForm.week_start, weekDraft],
  );

  const contextSuggestion = workflow?.context_suggestion || null;
  const selectedWeekRange = useMemo(() => weekRangeFromValue(selectedWeekValue), [selectedWeekValue]);
  const effectiveWeekStart = contextForm.week_start || selectedWeekRange.week_start;
  const effectiveWeekEnd = contextForm.week_end || selectedWeekRange.week_end;
  const contextReady = Boolean(contextForm.facility_id.trim() && effectiveWeekStart && effectiveWeekEnd);
  const workflowContextConfirmed = Boolean(
    workflow?.facility_id
      && workflow?.week_start
      && workflow?.week_end
      && !["uploaded", "facility_template_unresolved"].includes(String(workflow?.state || "")),
  );
  const workflowBlockers = Array.isArray(workflow?.blockers)
    ? workflow.blockers.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const ocrJobErrorMessage = String(
    workflow?.ocr_job?.error_user_message
      || workflow?.ocr_job?.error_detail
      || workflow?.ocr_job?.error_message
      || workflow?.ocr_job?.error
      || "",
  ).trim();
  const ocrJobRecoveryAction = String(workflow?.ocr_job?.recovery_action || "").trim();
  const canReviewQuad = Boolean(orderId && workflowContextConfirmed && workflow?.template_version_id);
  const quadReviewRequired = ocrJobRecoveryAction === "review_or_edit_quad"
    || workflowBlockers.includes("quad_estimation_failed")
    || Boolean(workflow?.quad_override?.quad_px?.length);
  const stepLabels = useMemo(() => {
    const labels = [baseStepLabels[0]];
    if (canReviewQuad || quadReviewRequired || visibleStep === 1.5) labels.push({ step: 1.5, label: "4点確認/補正" });
    if (visibleStep === 1.6) labels.push({ step: 1.6, label: "ヘッダー補正" });
    labels.push(...baseStepLabels.slice(1));
    return labels;
  }, [canReviewQuad, quadReviewRequired, visibleStep]);
  const ocrPrerequisiteBlockers = workflowBlockers.filter((item) =>
    [
      "menu_entries_missing",
      "monthly_menu_object_missing",
      "monthly_menu_lookup_failed",
      "monthly_menu_facility_scope_missing",
      "week_unresolved",
    ].includes(item),
  );
  const selectedFacility = useMemo(
    () => facilityOptions.find((option) => option.id === contextForm.facility_id) || null,
    [contextForm.facility_id, facilityOptions],
  );
  const workflowFacilityOption = useMemo(
    () => facilityOptions.find((option) => option.id === workflow?.facility_id) || null,
    [facilityOptions, workflow?.facility_id],
  );
  const workflowFacilityLabel = workflow?.facility_id
    ? (workflowFacilityOption ? formatFacilityLabel(workflowFacilityOption) : workflow.facility_id)
    : "未設定";
  const contextFacilityLabel = contextForm.facility_id
    ? (selectedFacility ? formatFacilityLabel(selectedFacility) : contextForm.facility_id)
    : "未設定";
  const selectedFacilityTemplateOption = useMemo(
    () => faxTemplateOptions.find((option) => option.template_id === selectedFacilityTemplateId) || null,
    [faxTemplateOptions, selectedFacilityTemplateId],
  );
  const selectedFacilityRegisteredTemplateOption = useMemo(
    () => faxTemplateOptions.find((option) => option.template_id === facilityTemplateStatus.templateId) || null,
    [facilityTemplateStatus.templateId, faxTemplateOptions],
  );
  const facilityTemplateMissing = Boolean(
    contextForm.facility_id
      && !facilityTemplateStatus.loading
      && facilityTemplateStatus.facilityId === contextForm.facility_id
      && !facilityTemplateStatus.templateId,
  );
  const facilityTemplateDirty = useMemo(
    () => JSON.stringify(facilityTemplateColumns) !== JSON.stringify(facilityTemplateColumnDraft),
    [facilityTemplateColumnDraft, facilityTemplateColumns],
  );
  const facilityTemplateQuantitySummary = useMemo(
    () => facilityTemplateColumns
      .filter((column) => isQuantityRole(column.role))
      .map((column) => column.header || defaultHeaderForFacilityTemplateColumn(column))
      .filter(Boolean),
    [facilityTemplateColumns],
  );
  const facilityTemplateColumnsUnresolved = Boolean(
    contextForm.facility_id
      && !facilityTemplateStatus.loading
      && facilityTemplateStatus.facilityId === contextForm.facility_id
      && facilityTemplateStatus.templateId
      && facilityTemplateColumns.length === 0,
  );
  const facilityTemplateReadyForOcr = Boolean(
    contextForm.facility_id
      && !facilityTemplateStatus.loading
      && facilityTemplateStatus.facilityId === contextForm.facility_id
      && facilityTemplateStatus.templateId
      && facilityTemplateColumns.length > 0
      && !facilityTemplateMissing
      && !facilityTemplateColumnsUnresolved
      && !facilityTemplateDirty,
  );

  const quantityColumnOptions = useMemo(() => {
    if (!sheetPayload) return [];
    return sheetPayload.fields
      .map((field, idx) => ({ field, idx, label: sheetPayload.header[idx] || field }))
      .filter((item) => !isLockedSheetField(item.field));
  }, [sheetPayload]);

  const bagRows = useMemo(
    () => extractWorkflowBagRows(inspection?.bagging_result),
    [inspection?.bagging_result],
  );

  const bagSummaryRows = useMemo(
    () => buildWorkflowBagSummaryRows(bagRows),
    [bagRows],
  );

  const bagSummaryGroups = useMemo(
    () => groupWorkflowBagRowsByDate(bagSummaryRows),
    [bagSummaryRows],
  );

  const anomalyReview = useMemo(() => {
    const review = localAnomalyReview || inspection?.anomaly_review;
    return review && typeof review === "object" && !Array.isArray(review)
      ? review as Record<string, unknown>
      : null;
  }, [inspection?.anomaly_review, localAnomalyReview]);

  const anomalyWarnings = useMemo(() => {
    const warnings = anomalyReview?.warnings;
    return Array.isArray(warnings)
      ? warnings.filter((item): item is SheetAnomalyWarning => Boolean(item && typeof item === "object"))
      : [];
  }, [anomalyReview]);

  const anomalyCellMap = useMemo(() => {
    const severityRank: Record<string, number> = { low: 1, medium: 2, high: 3 };
    const map = new Map<string, { warning: SheetAnomalyWarning; index: number }>();
    anomalyWarnings.forEach((warning, index) => {
      if (typeof warning.row_index !== "number" || typeof warning.col_index !== "number") return;
      const key = `${warning.row_index}:${warning.col_index}`;
      const current = map.get(key);
      const currentRank = current ? severityRank[String(current.warning.severity || "medium")] || 2 : 0;
      const nextRank = severityRank[String(warning.severity || "medium")] || 2;
      if (!current || nextRank >= currentRank) {
        map.set(key, { warning, index });
      }
    });
    return map;
  }, [anomalyWarnings]);

  const autoEditPatches = useMemo(() => (
    (sheetAutoEditResult?.patches || []).filter((patch): patch is SheetAutoEditPatch => (
      Boolean(patch)
      && typeof patch.row_index === "number"
      && typeof patch.col_index === "number"
      && String(patch.suggested_value || "").trim().length > 0
    ))
  ), [sheetAutoEditResult]);

  const autoEditCellMap = useMemo(() => {
    const map = new Map<string, { patch: SheetAutoEditPatch; index: number }>();
    autoEditPatches.forEach((patch, index) => {
      const key = `${patch.row_index}:${patch.col_index}`;
      if (!map.has(key)) {
        map.set(key, { patch, index });
      }
    });
    return map;
  }, [autoEditPatches]);

  const ocrOverlayItemMap = useMemo(() => {
    const map = new Map<string, OcrNumericCellItem>();
    for (const item of sheetPayload?.ocr_numeric_cell_items || []) {
      if (typeof item.target_row_index !== "number" || typeof item.target_col_index !== "number") continue;
      if (!classificationVisible(item.classification, ocrConfidenceDisplayMode)) continue;
      map.set(`${item.target_row_index}:${item.target_col_index}`, item);
    }
    return map;
  }, [ocrConfidenceDisplayMode, sheetPayload]);

  const targetCells = useMemo(
    () => (sheetPayload?.target_cell_map || []).filter((item) => (
      typeof item.target_row_index === "number"
      && typeof item.target_col_index === "number"
      && Array.isArray(item.bbox)
      && item.bbox.length === 4
      && item.bbox.every((value) => typeof value === "number" && Number.isFinite(value))
    )),
    [sheetPayload],
  );

  const targetCellMap = useMemo(() => {
    const map = new Map<string, TargetCellMapItem>();
    for (const item of targetCells) {
      map.set(`${item.target_row_index}:${item.target_col_index}`, item);
    }
    return map;
  }, [targetCells]);

  const focusedTargetCell = focusedSheetCell
    ? targetCellMap.get(`${focusedSheetCell.rowIndex}:${focusedSheetCell.colIndex}`) || null
    : null;

  const overlayCoordinateMax = useMemo(() => {
    const maxX = Math.max(
      overlayImageSize.naturalWidth || 0,
      ...targetCells.map((cell) => Number(cell.bbox?.[2] ?? 0)).filter(Number.isFinite),
    );
    const maxY = Math.max(
      overlayImageSize.naturalHeight || 0,
      ...targetCells.map((cell) => Number(cell.bbox?.[3] ?? 0)).filter(Number.isFinite),
    );
    return { x: maxX, y: maxY };
  }, [overlayImageSize.naturalHeight, overlayImageSize.naturalWidth, targetCells]);

  const overlayCoordinateTransform = useMemo(() => {
    if (!overlayImageSize.naturalWidth || !overlayImageSize.naturalHeight) {
      return null;
    }
    const targetCoordinatesExceedReviewCanvas =
      overlayCoordinateMax.x > overlayImageSize.naturalWidth
      || overlayCoordinateMax.y > overlayImageSize.naturalHeight;
    if (!targetCoordinatesExceedReviewCanvas) {
      return null;
    }
    const innerImageWidth = overlayImageSize.naturalWidth - HAKODATE_REVIEW_CANVAS.canvasXPadding;
    if (innerImageWidth <= 0) {
      return null;
    }
    const rawToReviewScale = innerImageWidth / HAKODATE_REVIEW_CANVAS.rectifiedCanvasWidth;
    const reviewHeaderHeight = overlayImageSize.naturalHeight - (HAKODATE_REVIEW_CANVAS.rectifiedCanvasHeight * rawToReviewScale);
    const offsetY = reviewHeaderHeight - HAKODATE_REVIEW_CANVAS.pastedImageYGap;
    if (!Number.isFinite(rawToReviewScale) || !Number.isFinite(offsetY) || rawToReviewScale <= 0) {
      return null;
    }
    const renderScaleX = overlayImageSize.width / overlayImageSize.naturalWidth;
    const renderScaleY = overlayImageSize.height / overlayImageSize.naturalHeight;
    return {
      offsetX: HAKODATE_REVIEW_CANVAS.pastedImageX,
      offsetY,
      rawToReviewScale,
      renderScaleX,
      renderScaleY,
    };
  }, [
    overlayCoordinateMax.x,
    overlayCoordinateMax.y,
    overlayImageSize.height,
    overlayImageSize.naturalHeight,
    overlayImageSize.naturalWidth,
    overlayImageSize.width,
  ]);

  const resolveTargetOverlayBox = (item: TargetCellMapItem | null | undefined): OverlayBox | null => {
    const bbox = item?.bbox;
    if (!bbox || bbox.length !== 4 || !overlayImageSize.width || !overlayImageSize.height) return null;
    const center = Array.isArray(item?.center) && item.center.length >= 2 ? item.center : null;
    const values = [...bbox, ...(center || [])];
    const normalized = values.every((value) => value >= -0.02 && value <= 1.2);
    if (!normalized && overlayCoordinateTransform) {
      const mapX = (value: number) => (
        (overlayCoordinateTransform.offsetX + value * overlayCoordinateTransform.rawToReviewScale)
        * overlayCoordinateTransform.renderScaleX
      );
      const mapY = (value: number) => (
        (overlayCoordinateTransform.offsetY + value * overlayCoordinateTransform.rawToReviewScale)
        * overlayCoordinateTransform.renderScaleY
      );
      const left = mapX(bbox[0]);
      const top = mapY(bbox[1]);
      const right = mapX(bbox[2]);
      const bottom = mapY(bbox[3]);
      const width = Math.max(right - left, 1);
      const height = Math.max(bottom - top, 1);
      if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
        return null;
      }
      return {
        left,
        top,
        width,
        height,
        centerLeft: center ? mapX(center[0]) : left + width / 2,
        centerTop: center ? mapY(center[1]) : top + height / 2,
      };
    }
    const coordinateWidth = overlayCoordinateMax.x || overlayImageSize.naturalWidth || 1;
    const coordinateHeight = overlayCoordinateMax.y || overlayImageSize.naturalHeight || 1;
    const scaleX = normalized ? overlayImageSize.width : overlayImageSize.width / coordinateWidth;
    const scaleY = normalized ? overlayImageSize.height : overlayImageSize.height / coordinateHeight;
    const left = bbox[0] * scaleX;
    const top = bbox[1] * scaleY;
    const right = bbox[2] * scaleX;
    const bottom = bbox[3] * scaleY;
    const width = Math.max(right - left, 1);
    const height = Math.max(bottom - top, 1);
    if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
      return null;
    }
    return {
      left,
      top,
      width,
      height,
      centerLeft: center ? center[0] * scaleX : left + width / 2,
      centerTop: center ? center[1] * scaleY : top + height / 2,
    };
  };

  const renderedTargetCells = targetCells
    .map((item) => ({ item, box: resolveTargetOverlayBox(item) }))
    .filter((entry): entry is { item: TargetCellMapItem; box: OverlayBox } => Boolean(entry.box));

  const sheetReviewItems = useMemo(() => {
    if (!sheetPayload) return [];
    return renderedTargetCells
      .map(({ item, box }) => {
        const rowIndex = item.target_row_index;
        const colIndex = item.target_col_index;
        const field = String(sheetPayload.fields?.[colIndex] || item.field || "").trim();
        const value = String(sheetPayload.rows?.[rowIndex]?.[colIndex] || "").trim();
        if (!value || isLockedSheetField(field)) return null;
        return { item, box, value, rowIndex, colIndex, field };
      })
      .filter((item): item is { item: TargetCellMapItem; box: OverlayBox; value: string; rowIndex: number; colIndex: number; field: string } => Boolean(item));
  }, [renderedTargetCells, sheetPayload]);

  const focusedTargetBox = resolveTargetOverlayBox(focusedTargetCell);
  const focusedField = focusedSheetCell ? String(sheetPayload?.fields?.[focusedSheetCell.colIndex] || "").trim() : "";
  const focusedFieldAliases = new Set(
    [
      focusedField,
      focusedField === "qty.placeholder_x" ? "post_menu.F" : "",
      focusedField === "remarks" ? "note" : "",
    ].filter(Boolean),
  );
  const focusedRowBox = focusedSheetCell
    ? unionOverlayBoxes(renderedTargetCells.filter((entry) => entry.item.target_row_index === focusedSheetCell.rowIndex))
    : null;
  const focusedColumnBox = focusedSheetCell && focusedFieldAliases.size
    ? unionOverlayBoxes(renderedTargetCells.filter((entry) => focusedFieldAliases.has(String(entry.item.field || "").trim())))
    : null;

  const resolvedLlmModel = llmProvider === "gemini"
    ? llmModelMode === "pro"
      ? "gemini-1.5-pro"
      : llmModelMode === "other"
        ? llmCustomModel.trim()
        : "gemini-1.5-flash"
    : "";

  const applyWeekValue = (value: string) => {
    const range = weekRangeFromValue(value);
    setWeekDraft(value);
    setContextForm((current) => ({
      ...current,
      week_start: range.week_start,
      week_end: range.week_end,
    }));
  };

  const applyContextSuggestion = () => {
    if (!contextSuggestion) return;
    const weekValue = contextSuggestionWeekValue(contextSuggestion);
    const range = weekRangeFromValue(weekValue);
    setContextForm((current) => ({
      ...current,
      facility_id: String(contextSuggestion.facility_id || current.facility_id || ""),
      week_start: String(contextSuggestion.week_start || range.week_start || current.week_start || ""),
      week_end: String(contextSuggestion.week_end || range.week_end || current.week_end || ""),
    }));
    if (weekValue) {
      setWeekDraft(weekValue);
    }
    setMessage("PDFから推定した施設・週次候補をフォームに反映しました。Step1はまだ未確定です。");
    setError("");
  };

  const refreshAll = async () => {
    if (!orderId) return;
    const [workflowRes, ocrRes, inspectionRes, orderRes] = await Promise.all([
      apiClient.get<WorkflowV2>(`/orders/${orderId}/workflow-v2`),
      apiClient.get<{ results: OcrResult[] }>(`/orders/${orderId}/workflow-v2/ocr-results`),
      apiClient.get<InspectionPayload>(`/orders/${orderId}/workflow-v2/inspection`),
      apiClient.get<OrderDetail>(`/orders/${orderId}`),
    ]);
    setWorkflow(workflowRes.data);
    setOrderDetail(orderRes.data);
    applyPreSaveState(
      inspectionRes.data.pre_save_checks || workflowRes.data.pre_save_checks || {},
      inspectionRes.data.pre_save_status || null,
    );
    setExpandedCellCopyMode(normalizeExpandedCellCopyMode(workflowRes.data.expanded_cell_copy_mode));
    setOcrResults(Array.isArray(ocrRes.data.results) ? ocrRes.data.results : []);
    setInspection(inspectionRes.data);
    const savedSheet = inspectionRes.data.saved_sheet?.sheet;
    if (savedSheet) {
      const normalizedSavedSheet = normalizeSheetPayload(savedSheet);
      setSheetPayload(normalizedSavedSheet);
      markSheetJsonStale();
    } else {
      setSheetPayload(null);
      setSheetJson(formatJson(defaultSheet));
      setSheetJsonStale(false);
    }
    const suggestion = workflowRes.data.context_suggestion || null;
    const suggestedWeekValue = contextSuggestionWeekValue(suggestion);
    const suggestedRange = weekRangeFromValue(suggestedWeekValue);
    const suggestedContext = {
      facility_id: String(suggestion?.facility_id || ""),
      week_start: String(suggestion?.week_start || suggestedRange.week_start || ""),
      week_end: String(suggestion?.week_end || suggestedRange.week_end || ""),
    };
    const confirmedContext = {
      facility_id: workflowRes.data.facility_id || "",
      week_start: workflowRes.data.week_start || "",
      week_end: workflowRes.data.week_end || "",
    };
    setContextForm((current) => {
      if (confirmedContext.facility_id || confirmedContext.week_start || confirmedContext.week_end) {
        return confirmedContext;
      }
      if (current.facility_id || current.week_start || current.week_end) {
        return current;
      }
      if (suggestedContext.facility_id || suggestedContext.week_start || suggestedContext.week_end) {
        return suggestedContext;
      }
      return emptyContext;
    });
    const workflowWeekValue = weekValueFromRange(workflowRes.data.week_start, workflowRes.data.week_end);
    setWeekDraft((current) => workflowWeekValue || current || suggestedWeekValue || "");
  };

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    refreshAll().catch((err) => {
      setError(formatApiError(err, "workflow-v2 の取得に失敗しました"));
    });
  }, [router.isReady, orderId]);

  useEffect(() => {
    sheetPayloadRef.current = sheetPayload;
    pendingSheetCellEditsRef.current.clear();
  }, [sheetPayload]);

  useEffect(() => {
    if (!orderDetail || !currentDocumentId) return;
    if (!selectedDocumentId) {
      setSelectedDocumentId(currentDocumentId);
      return;
    }
    if (
      documentVersionOptions.length
      && !documentVersionOptions.some((version) => String(version.document_id || "").trim() === selectedDocumentId)
    ) {
      setSelectedDocumentId(currentDocumentId);
    }
  }, [currentDocumentId, documentVersionOptions, orderDetail, selectedDocumentId]);

  useEffect(() => () => {
    if (sheetAutoEditPollRef.current !== null) {
      window.clearTimeout(sheetAutoEditPollRef.current);
      sheetAutoEditPollRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!router.isReady || !orderId || workflow?.state !== "ocr_running") return undefined;
    const timer = window.setInterval(() => {
      refreshAll().catch((err) => {
        setError(formatApiError(err, "OCR進捗の取得に失敗しました"));
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [router.isReady, orderId, workflow?.state]);

  useEffect(() => {
    if (!router.isReady || !orderId || visibleStep !== 1.5) return;
    loadQuadReview().catch((err) => {
      setQuadReviewMessage(formatApiError(err, "4点確認データの取得に失敗しました"));
    });
  }, [router.isReady, orderId, visibleStep]);

  useEffect(() => {
    if (!router.isReady || !orderId || visibleStep !== 1.6) return;
    loadHeaderAxisReview().catch((err) => {
      setHeaderAxisMessage(formatApiError(err, "ヘッダー交点確認データの取得に失敗しました"));
    });
  }, [router.isReady, orderId, visibleStep]);

  const loadFacilityTemplateStatus = async (facilityId: string) => {
    const normalizedFacilityId = facilityId.trim();
    if (!normalizedFacilityId) {
      setFacilityTemplateStatus({
        facilityId: "",
        templateId: "",
        templateIds: [],
        facilityTemplateId: "",
        loading: false,
        error: "",
      });
      setFacilityResolvedConfig(null);
      setFacilityTemplateColumns([]);
      setFacilityTemplateColumnDraft([]);
      setFacilityTemplateMessage("");
      setFacilityTemplateSwapLeft("");
      setFacilityTemplateSwapRight("");
      setSelectedFacilityTemplateId("");
      return;
    }
    setFacilityTemplateStatus((current) => ({
      ...current,
      facilityId: normalizedFacilityId,
      loading: true,
      error: "",
    }));
    try {
      const res = await apiClient.get(`/facilities/${normalizedFacilityId}`);
      const resolved = res.data?.resolved_config || {};
      const resolvedColumns = normalizeFacilityTemplateColumns(resolved?.fax_template?.columns);
      const templateId = String(resolved?.fax_template_id || "").trim();
      const templateIds = Array.isArray(resolved?.fax_template_ids)
        ? resolved.fax_template_ids.map((item: unknown) => String(item || "").trim()).filter(Boolean)
        : [];
      const facilityTemplateId = String(resolved?.facility_template_id || resolved?.facility_template_name || "").trim();
      setFacilityResolvedConfig(resolved);
      setFacilityTemplateColumns(resolvedColumns);
      setFacilityTemplateColumnDraft(resolvedColumns);
      setFacilityTemplateSwapLeft("");
      setFacilityTemplateSwapRight("");
      setFacilityTemplateMessage("");
      setFacilityTemplateStatus({
        facilityId: normalizedFacilityId,
        templateId,
        templateIds,
        facilityTemplateId,
        loading: false,
        error: "",
      });
      setSelectedFacilityTemplateId(templateId || "");
    } catch (err: any) {
      setFacilityTemplateStatus({
        facilityId: normalizedFacilityId,
        templateId: "",
        templateIds: [],
        facilityTemplateId: "",
        loading: false,
        error: formatApiError(err, "施設テンプレート設定を取得できませんでした"),
      });
      setFacilityResolvedConfig(null);
      setFacilityTemplateColumns([]);
      setFacilityTemplateColumnDraft([]);
      setFacilityTemplateSwapLeft("");
      setFacilityTemplateSwapRight("");
    }
  };

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
          .map((item: any) => ({
            id: String(item?.id || ""),
            name: String(item?.name || ""),
          }))
          .filter((item: FacilityOption) => item.id);
        normalized.sort((a: FacilityOption, b: FacilityOption) => (
          a.name.localeCompare(b.name, "ja") || a.id.localeCompare(b.id, "ja")
        ));
        setFacilityOptions(normalized);
      } catch {
        if (!active) return;
        setFacilityOptions([]);
        setFacilityOptionsError("施設一覧の取得に失敗しました。");
      } finally {
        if (active) setFacilityOptionsLoading(false);
      }
    };
    loadFacilities();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const loadFaxTemplateOptions = async () => {
      setFaxTemplateOptionsLoading(true);
      setFaxTemplateOptionsError("");
      try {
        const res = await apiClient.get("/facilities/fax-template-options");
        if (!active) return;
        const raw = Array.isArray(res.data?.templates) ? res.data.templates : [];
        const normalized = raw
          .map((item: any) => ({
            template_id: String(item?.template_id || ""),
            label: typeof item?.label === "string" ? item.label : null,
            description: typeof item?.description === "string" ? item.description : null,
            template_family: typeof item?.template_family === "string" ? item.template_family : null,
            template_version: item?.template_version ?? null,
            quantity_headers: Array.isArray(item?.quantity_headers)
              ? item.quantity_headers.map((value: unknown) => String(value || "")).filter(Boolean)
              : [],
          }))
          .filter((item: FaxTemplateOption) => item.template_id);
        setFaxTemplateOptions(normalized);
      } catch {
        if (!active) return;
        setFaxTemplateOptions([]);
        setFaxTemplateOptionsError("帳票レイアウト候補を取得できませんでした。");
      } finally {
        if (active) setFaxTemplateOptionsLoading(false);
      }
    };
    loadFaxTemplateOptions();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    void loadFacilityTemplateStatus(contextForm.facility_id);
  }, [contextForm.facility_id]);

  useEffect(() => {
    const indices = new Set(facilityTemplateColumnDraft.map((column, idx) => String(column.index ?? idx)));
    if (facilityTemplateSwapLeft && !indices.has(facilityTemplateSwapLeft)) {
      setFacilityTemplateSwapLeft("");
    }
    if (facilityTemplateSwapRight && !indices.has(facilityTemplateSwapRight)) {
      setFacilityTemplateSwapRight("");
    }
  }, [facilityTemplateColumnDraft, facilityTemplateSwapLeft, facilityTemplateSwapRight]);

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    let active = true;
    const loadWeekOptions = async () => {
      setWeekOptionsLoading(true);
      setWeekOptionsError("");
      try {
        const res = await apiClient.get(`/orders/${orderId}/week-options`);
        if (!active) return;
        const options = Array.isArray(res.data?.options)
          ? res.data.options
              .map((item: any) => ({
                week_id: normalizeWeekValue(item?.week_id),
                label: String(item?.label || item?.week_id || ""),
                date_from: typeof item?.date_from === "string" ? item.date_from : null,
                date_to: typeof item?.date_to === "string" ? item.date_to : null,
                selected: Boolean(item?.selected),
              }))
              .filter((item: WeekOption) => item.week_id)
          : [];
        setWeekOptions(options);
        setWeekDraft((current) => {
          const currentNormalized = normalizeWeekValue(current);
          if (currentNormalized) return currentNormalized;
          const workflowWeekValue = weekValueFromRange(workflow?.week_start, workflow?.week_end);
          if (workflowWeekValue) return workflowWeekValue;
          const selectedOptionWeek = options.find((item: WeekOption) => item.selected)?.week_id || "";
          if (selectedOptionWeek) {
            const selectedRange = weekRangeFromValue(selectedOptionWeek);
            setContextForm((form) => (
              form.week_start || form.week_end
                ? form
                : { ...form, week_start: selectedRange.week_start, week_end: selectedRange.week_end }
            ));
          }
          return selectedOptionWeek;
        });
      } catch (err: any) {
        if (!active) return;
        if (err?.response?.status !== 404) {
          setWeekOptionsError("週候補の取得に失敗しました。必要なら例外範囲を設定してください。");
        }
        setWeekOptions([]);
      } finally {
        if (active) setWeekOptionsLoading(false);
      }
    };
    loadWeekOptions();
    return () => {
      active = false;
    };
  }, [router.isReady, orderId, workflow?.week_end, workflow?.week_start]);

  useEffect(() => {
    if (workflow?.state) {
      setVisibleStep(stepIndexForState(workflow.state));
    }
  }, [workflow?.state]);

  useEffect(() => {
    const syncOverlayImageSize = () => {
      const image = overlayImageRef.current;
      if (!image) return;
      setOverlayImageSize({
        naturalWidth: image.naturalWidth,
        naturalHeight: image.naturalHeight,
        width: image.clientWidth,
        height: image.clientHeight,
      });
    };
    syncOverlayImageSize();
    window.addEventListener("resize", syncOverlayImageSize);
    return () => window.removeEventListener("resize", syncOverlayImageSize);
  }, [selectedOcrOverlayUrl, selectedOcrSheetReviewBaseUrl, step3LayoutMode, visibleStep]);

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    let active = true;
    let objectUrl = "";
    setPdfUrl("");
    setPdfError("");
    apiClient
      .get<Blob>(`/orders/${orderId}/document`, {
        params: effectiveSelectedDocumentId ? { document_id: effectiveSelectedDocumentId } : undefined,
        responseType: "blob",
      })
      .then((res) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(res.data);
        setPdfUrl(objectUrl);
      })
      .catch((err) => {
        if (!active) return;
        const status = err?.response?.status;
        setPdfError(status === 404 ? "原本FAX PDFを現在取得できません。" : "PDFの取得に失敗しました。");
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [router.isReady, orderId, effectiveSelectedDocumentId]);

  const runAction = async (
    label: string,
    action: () => Promise<void>,
    options: { successMessage?: string; nextStep?: number; refreshAfter?: boolean } = {},
  ) => {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      await action();
      if (options.refreshAfter !== false) {
        await refreshAll();
      }
      if (options.nextStep) {
        setVisibleStep(options.nextStep);
      }
      setMessage(options.successMessage || `${label} が完了しました`);
    } catch (err: any) {
      if (err?.response?.data?.detail === "facility_template_unresolved") {
        setVisibleStep(1);
        await refreshAll().catch(() => undefined);
        await loadFacilityTemplateStatus(contextForm.facility_id).catch(() => undefined);
      }
      setError(formatApiError(err, `${label} に失敗しました`));
    } finally {
      setBusy("");
    }
  };

  const confirmContext = () =>
    runAction("Step1 context confirm", async () => {
      const fallbackRange = weekRangeFromValue(selectedWeekValue);
      await apiClient.post(`/orders/${orderId}/workflow-v2/context`, {
        facility_id: contextForm.facility_id,
        week_start: contextForm.week_start || fallbackRange.week_start,
        week_end: contextForm.week_end || fallbackRange.week_end,
      });
    });

  const registerFacilityTemplate = () =>
    runAction(
      "施設テンプレート登録",
      async () => {
        const facilityId = contextForm.facility_id.trim();
        const templateId = selectedFacilityTemplateId.trim();
        if (!facilityId) {
          throw new Error("施設を選択してください");
        }
        if (!templateId) {
          throw new Error("帳票レイアウトを選択してください");
        }
        const res = await apiClient.put(`/facilities/${facilityId}/fax-template`, {
          fax_template_id: templateId,
          fax_template_ids: [templateId],
        });
        const resolved = res.data?.resolved_config || {};
        setFacilityTemplateStatus({
          facilityId,
          templateId: String(resolved?.fax_template_id || templateId),
          templateIds: Array.isArray(resolved?.fax_template_ids)
            ? resolved.fax_template_ids.map((item: unknown) => String(item || "").trim()).filter(Boolean)
            : [templateId],
          facilityTemplateId: String(resolved?.facility_template_id || resolved?.facility_template_name || contextFacilityLabel || facilityId),
          loading: false,
          error: "",
        });
        if (contextReady) {
          await apiClient.post(`/orders/${orderId}/workflow-v2/context`, {
            facility_id: contextForm.facility_id,
            week_start: contextForm.week_start,
            week_end: contextForm.week_end,
          });
        } else {
          await loadFacilityTemplateStatus(facilityId);
        }
      },
      {
        successMessage: contextReady
          ? "施設テンプレートを登録し、Step1設定を保存しました"
          : "施設テンプレートを登録しました。施設と週を確認してStep1を保存してください。",
        refreshAfter: contextReady,
      },
    );

  const updateFacilityTemplateColumn = (
    rowIndex: number,
    key: keyof FacilityTemplateColumn,
    value: string,
  ) => {
    setFacilityTemplateColumnDraft((prev) =>
      prev.map((column, idx) => {
        if (idx !== rowIndex) return column;
        const next = { ...column, [key]: value };
        if (key === "role") {
          if (value === "quantity") {
            next.diet_type = normalizeDietTypeToken(next.diet_type || next.header || next.name || "") || "unknown";
            next.area_id = normalizeFacilityAreaToken(next.area_id || next.header || next.name || "");
          } else {
            next.diet_type = "";
            next.area_id = "";
            next.header_group = "";
          }
          next.header = defaultHeaderForFacilityTemplateColumn(next);
          next.name = defaultNameForFacilityTemplateColumn(next);
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
    setFacilityTemplateMessage("施設区分列の並びを入れ替えました。保存するとこの施設のテンプレートに反映されます。");
  };

  const applySelectedFacilityTemplateColumnSwap = () => {
    const leftIndex = Number(facilityTemplateSwapLeft);
    const rightIndex = Number(facilityTemplateSwapRight);
    if (!Number.isInteger(leftIndex) || !Number.isInteger(rightIndex) || leftIndex === rightIndex) {
      setFacilityTemplateMessage("入れ替える2つの列を選択してください。");
      return;
    }
    applyFacilityTemplateColumnSwap(leftIndex, rightIndex);
  };

  const appendFacilityTemplateColumn = () => {
    setShowFacilityTemplateEditor(true);
    setFacilityTemplateColumnDraft((prev) => reindexFacilityTemplateColumns([...prev, createEmptyFacilityTemplateColumn(prev.length)]));
    setFacilityTemplateMessage("施設区分列を追加しました。保存するとこの施設のテンプレートに反映されます。");
  };

  const deleteFacilityTemplateColumn = (rowIndex: number) => {
    setShowFacilityTemplateEditor(true);
    setFacilityTemplateColumnDraft((prev) => removeFacilityTemplateColumn(prev, rowIndex));
    setFacilityTemplateMessage("施設区分列を削除しました。保存するとこの施設のテンプレートに反映されます。");
  };

  const saveFacilityTemplateColumns = async () => {
    const facilityId = contextForm.facility_id.trim();
    if (!facilityId || !orderId) {
      setFacilityTemplateMessage("施設を選択してください。");
      return;
    }
    if (!facilityTemplateColumnDraft.length) {
      setFacilityTemplateMessage("保存できる施設区分列がありません。");
      return;
    }
    setFacilityTemplateSaving(true);
    setFacilityTemplateMessage("施設区分列を保存中...");
    setError("");
    try {
      const columns = buildFacilityTemplateColumnsPayload(facilityTemplateColumnDraft);
      const response = await apiClient.put(`/orders/${orderId}/workflow-v2/facility-template-columns`, { columns });
      const resolved = response.data?.resolved_config || null;
      const resolvedColumns = normalizeFacilityTemplateColumns(resolved?.fax_template?.columns ?? columns);
      setFacilityResolvedConfig(resolved);
      setFacilityTemplateColumns(resolvedColumns);
      setFacilityTemplateColumnDraft(resolvedColumns);
      setSheetPayload(null);
      setSheetJson(formatJson(defaultSheet));
      setSheetJsonStale(false);
      setFacilityTemplateMessage(
        `施設区分列を保存しました。OCR結果を${Number(response.data?.ocr_results_cleared || 0)}件破棄したため、Step1からOCRを再実行してください。`,
      );
      setVisibleStep(1);
      await refreshAll();
      await loadFacilityTemplateStatus(facilityId);
    } catch (err: any) {
      setFacilityTemplateMessage(formatApiError(err, "施設区分列の保存に失敗しました"));
    } finally {
      setFacilityTemplateSaving(false);
    }
  };

  const updateExpandedCellCopyMode = async (nextMode: ExpandedCellCopyMode) => {
    if (nextMode === expandedCellCopyMode) return;
    setExpandedCellCopySaving(true);
    setError("");
    setMessage("");
    try {
      const response = await apiClient.put(`/orders/${orderId}/workflow-v2/expanded-cell-copy-mode`, { mode: nextMode });
      setWorkflow(response.data);
      setExpandedCellCopyMode(normalizeExpandedCellCopyMode(response.data?.expanded_cell_copy_mode));
      setSheetPayload(null);
      setSheetJson(formatJson(defaultSheet));
      setSheetJsonStale(false);
      await refreshAll();
      if (response.data?.selected_ocr_result_id) {
        setVisibleStep(3);
        setMessage("拡大セルコピー設定を変更しました。選択OCRからシートを再生成してください。");
      } else {
        setVisibleStep(1);
        setMessage("拡大セルコピー設定を変更しました。OCR実行前に設定を確認できます。");
      }
    } catch (err: any) {
      setError(formatApiError(err, "拡大セルコピー設定の保存に失敗しました"));
    } finally {
      setExpandedCellCopySaving(false);
    }
  };

  const applyCustomWeekRange = () => {
    const weekValue = deriveWeekValueFromCalendarRange(customWeekRangeStart, customWeekRangeEnd);
    if (!weekValue) {
      setError("例外範囲の日付が不正です。");
      return;
    }
    applyWeekValue(weekValue);
    setError("");
    setMessage("例外範囲を設定しました。");
  };

  const loadQuadReview = async () => {
    if (!orderId) return;
    setQuadReviewLoading(true);
    setQuadReviewMessage("");
    try {
      const response = await apiClient.get<QuadReviewPayload>(`/orders/${orderId}/workflow-v2/quad-review`);
      setQuadReview(response.data);
      const savedQuad = response.data?.saved_override?.quad_px;
      if (Array.isArray(savedQuad) && savedQuad.length === 4) {
        setManualQuadPoints(savedQuad);
      }
    } catch (err: any) {
      setQuadReviewMessage(formatApiError(err, "4点確認データの取得に失敗しました"));
    } finally {
      setQuadReviewLoading(false);
    }
  };

  const saveQuadAndRerun = async (decision: "approved_estimate" | "manual_override", quadPx: number[][]) => {
    if (!orderId || !Array.isArray(quadPx) || quadPx.length !== 4) {
      setQuadReviewMessage("4点が揃っていません。");
      return;
    }
    setBusy("quad review save");
    setError("");
    setQuadReviewMessage("");
    try {
      await apiClient.put(`/orders/${orderId}/workflow-v2/quad-review`, {
        decision,
        quad_px: quadPx,
      });
      const response = await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-runs`, {
        stale_action: "retry",
        force: true,
        mode: "hakodate",
        document_id: effectiveSelectedDocumentId || undefined,
      });
      const nextWorkflow = response.data?.workflow || response.data || null;
      if (nextWorkflow) {
        setWorkflow(nextWorkflow);
      }
      setSheetPayload(null);
      setSheetJson(formatJson(defaultSheet));
      setSheetJsonStale(false);
      invalidateSheetPreSaveChecks();
      setMessage("4点補正を保存し、OCRを再実行中です。完了後に正解OCRを選択してください。");
      setVisibleStep(2);
    } catch (err: any) {
      setError(formatApiError(err, "4点補正の保存またはOCR再実行に失敗しました"));
    } finally {
      setBusy("");
    }
  };

  const loadHeaderAxisReview = async () => {
    if (!orderId) return;
    setHeaderAxisLoading(true);
    setHeaderAxisMessage("");
    try {
      const timeoutMs = getHeaderAxisTimeoutMs();
      const response = await apiClient.get<HeaderAxisReviewPayload>(
        `/orders/${orderId}/workflow-v2/header-axis-review`,
        { timeout: timeoutMs },
      );
      setHeaderAxisReview(response.data);
      const savedXs = response.data?.saved_override?.corrected_xs;
      const detectedXs = response.data?.x_positions;
      const nextXs = Array.isArray(savedXs) && savedXs.length >= 2 ? savedXs : detectedXs;
      setHeaderAxisXs((Array.isArray(nextXs) ? nextXs : []).map((value) => Number(value)).filter(Number.isFinite));
      setSelectedHeaderAxisIndex(null);
      setHeaderAxisAddMode(false);
    } catch (err: any) {
      setHeaderAxisMessage(formatApiError(err, "ヘッダー交点確認データの取得に失敗しました"));
    } finally {
      setHeaderAxisLoading(false);
    }
  };

  const saveHeaderAxisAndRerun = async () => {
    if (!orderId || headerAxisXs.length < 2) {
      setHeaderAxisMessage("ヘッダー交点の縦軸が不足しています。");
      return;
    }
    const coordinateSpace = headerAxisReview?.coordinate_space;
    if (!coordinateSpace?.width || !coordinateSpace?.height) {
      setHeaderAxisMessage("ヘッダー交点の座標系が取得できていません。");
      return;
    }
    setBusy("header axis save");
    setError("");
    setHeaderAxisMessage("");
    try {
      const timeoutMs = getHeaderAxisTimeoutMs();
      const expectedCount = getHeaderAxisExpectedCount();
      if (
        expectedCount > 0
        && headerAxisXs.length !== expectedCount
        && typeof window !== "undefined"
        && !window.confirm(`ヘッダー縦軸の本数がテンプレート期待値と一致しません。期待 ${expectedCount} 本 / 現在 ${headerAxisXs.length} 本です。このまま保存してOCRを再実行しますか？`)
      ) {
        return;
      }
      await apiClient.put(
        `/orders/${orderId}/workflow-v2/header-axis-review`,
        {
          corrected_xs: headerAxisXs,
          coordinate_space: coordinateSpace,
        },
        { timeout: timeoutMs },
      );
      await refreshAll();
      await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-runs`, {
        stale_action: "retry",
        force: true,
        mode: "hakodate",
        document_id: effectiveSelectedDocumentId || undefined,
      }, { timeout: timeoutMs });
      setSheetPayload(null);
      setSheetJson(formatJson(defaultSheet));
      setSheetJsonStale(false);
      setSheetAutoEditResult(null);
      setLocalAnomalyReview(null);
      setSelectedAnomalyIndex(null);
      setSelectedAutoEditIndex(null);
      setMessage("ヘッダー交点補正を保存し、OCRを再実行しました。");
      setVisibleStep(2);
    } catch (err: any) {
      setError(formatApiError(err, "ヘッダー交点補正の保存またはOCR再実行に失敗しました"));
    } finally {
      setBusy("");
    }
  };

  const handleQuadImageClick = (event: MouseEvent<HTMLImageElement>) => {
    if (!manualQuadMode || manualQuadPoints.length >= 4) return;
    const image = event.currentTarget;
    const rect = image.getBoundingClientRect();
    const naturalWidth = image.naturalWidth || rect.width;
    const naturalHeight = image.naturalHeight || rect.height;
    const x = ((event.clientX - rect.left) / rect.width) * naturalWidth;
    const y = ((event.clientY - rect.top) / rect.height) * naturalHeight;
    setManualQuadPoints((current) => [...current, [Number(x.toFixed(2)), Number(y.toFixed(2))]].slice(0, 4));
  };

  const headerAxisPointerToCanvasX = (event: MouseEvent<SVGSVGElement>) => {
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();
    const cropX0 = Number(headerAxisReview?.crop_box?.[0] || 0);
    const width = Number(headerAxisReview?.image_size?.[0] || rect.width || 1);
    const xInCrop = ((event.clientX - rect.left) / Math.max(rect.width, 1)) * width;
    return Number((cropX0 + xInCrop).toFixed(3));
  };

  const moveHeaderAxis = (index: number, nextX: number) => {
    const canvasWidth = Number(headerAxisReview?.canvas_size?.[0] || headerAxisReview?.coordinate_space?.width || 0);
    setHeaderAxisXs((current) => {
      if (index < 0 || index >= current.length) return current;
      const minX = index > 0 ? current[index - 1] + 1 : 0;
      const maxX = index < current.length - 1 ? current[index + 1] - 1 : canvasWidth || Number.MAX_SAFE_INTEGER;
      const clamped = Math.min(Math.max(nextX, minX), maxX);
      return current.map((value, idx) => (idx === index ? Number(clamped.toFixed(3)) : value));
    });
  };

  const normalizeHeaderAxisXs = (values: number[]) => {
    const canvasWidth = Number(headerAxisReview?.canvas_size?.[0] || headerAxisReview?.coordinate_space?.width || 0);
    const maxX = canvasWidth || Number.MAX_SAFE_INTEGER;
    const sorted = values
      .map((value) => Number(value))
      .filter(Number.isFinite)
      .map((value) => Math.min(Math.max(value, 0), maxX))
      .sort((a, b) => a - b);
    const normalized: number[] = [];
    for (const value of sorted) {
      if (normalized.length && Math.abs(value - normalized[normalized.length - 1]) < 1) continue;
      normalized.push(Number(value.toFixed(3)));
    }
    return normalized;
  };

  const addHeaderAxisAt = (nextX: number) => {
    setHeaderAxisXs((current) => {
      const next = normalizeHeaderAxisXs([...current, nextX]);
      const insertedIndex = next.findIndex((value) => Math.abs(value - Number(nextX)) < 1);
      setSelectedHeaderAxisIndex(insertedIndex >= 0 ? insertedIndex : null);
      return next;
    });
  };

  const deleteSelectedHeaderAxis = () => {
    if (selectedHeaderAxisIndex === null) {
      setHeaderAxisMessage("削除するヘッダー縦軸を選択してください。");
      return;
    }
    setHeaderAxisXs((current) => current.filter((_, idx) => idx !== selectedHeaderAxisIndex));
    setSelectedHeaderAxisIndex(null);
    setHeaderAxisMessage("");
  };

  const resetHeaderAxisXs = () => {
    const detectedXs = (Array.isArray(headerAxisReview?.x_positions) ? headerAxisReview?.x_positions : [])
      .map((value) => Number(value))
      .filter(Number.isFinite);
    setHeaderAxisXs(normalizeHeaderAxisXs(detectedXs));
    setSelectedHeaderAxisIndex(null);
    setHeaderAxisAddMode(false);
    setHeaderAxisMessage("");
  };

  const handleHeaderAxisPointerMove = (event: MouseEvent<SVGSVGElement>) => {
    if (draggingHeaderAxisIndex === null) return;
    moveHeaderAxis(draggingHeaderAxisIndex, headerAxisPointerToCanvasX(event));
  };

  const handleHeaderAxisCanvasClick = (event: MouseEvent<SVGSVGElement>) => {
    if (!headerAxisAddMode) return;
    addHeaderAxisAt(headerAxisPointerToCanvasX(event));
    setHeaderAxisAddMode(false);
  };

  const runOcr = () =>
    runAction(
      "Step1 OCR run",
      async () => {
        const payload: Record<string, unknown> = {
          stale_action: "retry",
          mode: ocrRunMode,
          document_id: effectiveSelectedDocumentId || undefined,
        };
        if (ocrRunMode === "llm") {
          payload.llm_assist = true;
          payload.prompt_preset = llmPromptPreset;
          payload.ocr_provider = llmProvider;
          if (resolvedLlmModel) {
            payload.ocr_model = resolvedLlmModel;
          }
          if (ocrPrompt.trim()) {
            payload.ocr_prompt = ocrPrompt.trim();
          }
        }
        await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-runs`, {
          ...payload,
        });
      },
      {
        successMessage: ocrRunMode === "llm" ? "Step1 AI OCR run が開始しました" : "Step1 OCR run が開始しました",
        nextStep: 2,
      },
    );

  const selectOcr = (ocrResultId: string) =>
    runAction(
      "Step2 OCR select",
      async () => {
        await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-results/${ocrResultId}/select`);
      },
      {
        successMessage: "正解OCRを選択しました",
        nextStep: 3,
      },
    );

  const deleteOcr = (ocrResultId: string) =>
    runAction("OCR result delete", async () => {
      await apiClient.delete(`/orders/${orderId}/workflow-v2/ocr-results/${ocrResultId}`);
    });

  const generateSheetFromSelectedOcr = () =>
    runAction("Step3 sheet source", async () => {
      const response = await apiClient.get<{ sheet?: Record<string, unknown> }>(`/orders/${orderId}/workflow-v2/sheet-source`);
      const normalized = normalizeSheetPayload(response.data.sheet);
      if (!normalized) {
        throw new Error("選択OCRからシートを生成できませんでした");
      }
      setSheetPayload(normalized);
      markSheetJsonStale();
      setSheetAutoEditResult(null);
      setLocalAnomalyReview(null);
      invalidateSheetPreSaveChecks();
      setSelectedAutoEditIndex(null);
      setSelectedAnomalyIndex(null);
    }, {
      successMessage: "選択OCRからシートを生成しました",
      refreshAfter: false,
    });

  const getSheetPayloadForAction = () => flushPendingSheetCellEdits() || sheetPayload || normalizeSheetPayload(JSON.parse(sheetJson));

  const refreshSheetJsonForDetails = () => {
    const latestSheet = flushPendingSheetCellEdits() || sheetPayloadRef.current || sheetPayload || defaultSheet;
    setSheetJson(formatJson(latestSheet));
    setSheetJsonStale(false);
  };

  const flushPendingSheetCellEdits = () => {
    const edits = Array.from(pendingSheetCellEditsRef.current.values());
    const baseSheet = sheetPayloadRef.current || sheetPayload;
    if (!edits.length) return baseSheet;
    if (!baseSheet) {
      pendingSheetCellEditsRef.current.clear();
      return null;
    }
    const rows = baseSheet.rows.map((row) => [...row]);
    for (const edit of edits) {
      if (!rows[edit.rowIndex] || edit.colIndex < 0 || edit.colIndex >= rows[edit.rowIndex].length) continue;
      rows[edit.rowIndex][edit.colIndex] = edit.value;
    }
    const nextSheet = { ...baseSheet, rows };
    sheetPayloadRef.current = nextSheet;
    pendingSheetCellEditsRef.current.clear();
    setSheetPayload(nextSheet);
    markSheetJsonStale();
    return nextSheet;
  };

  const updateSheetCell = (rowIndex: number, colIndex: number, value: string) => {
    setLocalAnomalyReview((current) => {
      const sourceReview = current || anomalyReview;
      if (!sourceReview) return current;
      const warnings = removeItemsForSheetCell(anomalyWarnings, rowIndex, colIndex);
      return { ...sourceReview, warnings };
    });
    setSheetAutoEditResult((current) => {
      if (!current) return current;
      return { ...current, patches: removeItemsForSheetCell(current.patches || [], rowIndex, colIndex) };
    });
    if (selectedAutoEditIndex !== null) setSelectedAutoEditIndex(null);
    if (selectedAnomalyIndex !== null) setSelectedAnomalyIndex(null);
    pendingSheetCellEditsRef.current.set(`${rowIndex}:${colIndex}`, { rowIndex, colIndex, value });
  };

  const focusSheetInput = (rowIndex: number, colIndex: number) => {
    requestAnimationFrame(() => {
      const selector = `[data-sheet-row="${rowIndex}"][data-sheet-col="${colIndex}"]`;
      const nextInput = document.querySelector<HTMLInputElement>(selector);
      nextInput?.focus();
      nextInput?.select();
    });
  };

  const handleSheetInputKeyDown = (event: KeyboardEvent<HTMLInputElement>, rowIndex: number, colIndex: number) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    const nextRowIndex = rowIndex + 1;
    if (!sheetPayload?.rows[nextRowIndex]) return;
    focusSheetInput(nextRowIndex, colIndex);
  };

  const selectAnomalyWarning = (warning: SheetAnomalyWarning, index: number) => {
    setSelectedAnomalyIndex(index);
    setSelectedAutoEditIndex(null);
    if (typeof warning.row_index === "number" && typeof warning.col_index === "number") {
      setFocusedSheetCell({ rowIndex: warning.row_index, colIndex: warning.col_index });
      focusSheetInput(warning.row_index, warning.col_index);
    }
  };

  const selectAutoEditPatch = (patch: SheetAutoEditPatch, index: number) => {
    setSelectedAutoEditIndex(index);
    setSelectedAnomalyIndex(null);
    if (typeof patch.row_index === "number" && typeof patch.col_index === "number") {
      setFocusedSheetCell({ rowIndex: patch.row_index, colIndex: patch.col_index });
      focusSheetInput(patch.row_index, patch.col_index);
    }
  };

  const fillQuantityColumn = () => {
    flushPendingSheetCellEdits();
    const colIndex = Number(columnFillTarget);
    if (!sheetPayload || !Number.isInteger(colIndex) || colIndex < 0) return;
    setLocalAnomalyReview(null);
    setSelectedAutoEditIndex(null);
    setSelectedAnomalyIndex(null);
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => row.map((cell, idx) => (idx === colIndex ? columnFillValue : cell)));
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
  };

  const swapQuantityColumns = () => {
    flushPendingSheetCellEdits();
    const left = Number(swapLeftColumn);
    const right = Number(swapRightColumn);
    if (!sheetPayload || !Number.isInteger(left) || !Number.isInteger(right) || left === right) return;
    setLocalAnomalyReview(null);
    setSelectedAutoEditIndex(null);
    setSelectedAnomalyIndex(null);
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => {
        const next = [...row];
        const temp = next[left] || "";
        next[left] = next[right] || "";
        next[right] = temp;
        return next;
      });
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
  };

  const applyVisibleOcrSuggestions = () => {
    flushPendingSheetCellEdits();
    if (!sheetPayload || !ocrOverlayItemMap.size) return;
    setLocalAnomalyReview(null);
    setSelectedAutoEditIndex(null);
    setSelectedAnomalyIndex(null);
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row, rowIdx) =>
        row.map((cell, colIdx) => {
          if (String(cell || "").trim()) return cell;
          const item = ocrOverlayItemMap.get(`${rowIdx}:${colIdx}`);
          const value = String(item?.value || "").trim();
          return value || cell;
        }),
      );
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
  };

  const pollSheetAutoEditJob = (jobId: string, attempt = 0) => {
    if (!orderId || !jobId) return;
    if (sheetAutoEditPollRef.current !== null) {
      window.clearTimeout(sheetAutoEditPollRef.current);
      sheetAutoEditPollRef.current = null;
    }
    sheetAutoEditPollRef.current = window.setTimeout(async () => {
      try {
        const response = await apiClient.get<{
          job?: SheetAutoEditResult["job"];
          result?: SheetAutoEditResult | null;
        }>(`/orders/${orderId}/workflow-v2/sheet/auto-edit/${jobId}`);
        const job = response.data.job || null;
        if (job?.status === "done" && response.data.result) {
          setSheetAutoEditResult({ ...response.data.result, job });
          setSelectedAutoEditIndex(null);
          setMessage("AI自動編集の候補を作成しました");
          return;
        }
        if (job?.status === "failed") {
          setSheetAutoEditResult({
            status: "failed",
            patches: [],
            llm: { status: "failed", error: job.error || "AI自動編集に失敗しました" },
            job,
          });
          setError(job.error || "AI自動編集に失敗しました");
          return;
        }
        setSheetAutoEditResult((current) => ({
          ...(current || {}),
          status: "running",
          patches: current?.patches || [],
          llm: { ...(current?.llm || {}), status: "running" },
          job,
        }));
        pollSheetAutoEditJob(jobId, attempt + 1);
      } catch (err) {
        if (attempt < 3) {
          pollSheetAutoEditJob(jobId, attempt + 1);
          return;
        }
        const formatted = formatApiError(err, "AI自動編集の状態取得に失敗しました");
        setSheetAutoEditResult({
          status: "failed",
          patches: [],
          llm: { status: "failed", error: formatted },
          job: { job_id: jobId, status: "failed", error: formatted },
        });
        setError(formatted);
      }
    }, attempt === 0 ? 1200 : 3000);
  };

  const proposeSheetAutoEdit = () =>
    runAction("Step3 AI auto edit", async () => {
      const parsed = getSheetPayloadForAction();
      if (!parsed) {
        throw new Error("AI自動編集に渡せるシートがありません");
      }
      const response = await apiClient.post<{
        job?: SheetAutoEditResult["job"];
      }>(
        `/orders/${orderId}/workflow-v2/sheet/auto-edit`,
        {
          sheet: parsed,
          use_llm: true,
        }
      );
      const job = response.data.job || null;
      setSheetAutoEditResult({
        status: "running",
        patches: [],
        llm: { status: "running" },
        job,
      });
      setSelectedAutoEditIndex(null);
      if (job?.job_id) {
        pollSheetAutoEditJob(job.job_id);
      }
    }, {
      successMessage: "AI自動編集を開始しました",
      refreshAfter: false,
    });

  const applySheetAutoEditPatches = () => {
    flushPendingSheetCellEdits();
    const patches = autoEditPatches;
    if (!sheetPayload || !patches.length) return;
    setLocalAnomalyReview(null);
    setSelectedAnomalyIndex(null);
    setSelectedAutoEditIndex(null);
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => [...row]);
      for (const patch of patches) {
        if (!rows[patch.row_index] || patch.col_index < 0 || patch.col_index >= rows[patch.row_index].length) continue;
        rows[patch.row_index][patch.col_index] = String(patch.suggested_value || "").trim();
      }
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
    setSheetAutoEditResult(null);
  };

  const applySingleSheetAutoEditPatch = (patch: SheetAutoEditPatch) => {
    flushPendingSheetCellEdits();
    if (!sheetPayload || typeof patch.row_index !== "number" || typeof patch.col_index !== "number") return;
    const suggestedValue = String(patch.suggested_value || "").trim();
    if (!suggestedValue) return;
    setLocalAnomalyReview((current) => {
      const sourceReview = current || anomalyReview;
      if (!sourceReview) return current;
      return {
        ...sourceReview,
        warnings: removeItemsForSheetCell(anomalyWarnings, patch.row_index, patch.col_index),
      };
    });
    setSelectedAnomalyIndex(null);
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => [...row]);
      if (!rows[patch.row_index] || patch.col_index < 0 || patch.col_index >= rows[patch.row_index].length) return current;
      rows[patch.row_index][patch.col_index] = suggestedValue;
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
    setSheetAutoEditResult((current) => {
      if (!current) return current;
      return {
        ...current,
        patches: removeFirstMatchingItem(current.patches || [], (item) => sameSheetPatchTarget(item, patch)),
      };
    });
    setSelectedAutoEditIndex(null);
  };

  const dismissSingleSheetAutoEditPatch = (patch: SheetAutoEditPatch) => {
    setSheetAutoEditResult((current) => {
      if (!current) return current;
      return {
        ...current,
        patches: removeFirstMatchingItem(current.patches || [], (item) => sameSheetPatchTarget(item, patch)),
      };
    });
    setSelectedAutoEditIndex(null);
  };

  const applyAnomalyCorrections = () => {
    flushPendingSheetCellEdits();
    const patches = anomalyWarnings.filter((warning) => (
      typeof warning.row_index === "number"
      && typeof warning.col_index === "number"
      && String(warning.suggested_value || "").trim()
    ));
    if (!sheetPayload || !patches.length) return;
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => [...row]);
      for (const warning of patches) {
        const rowIndex = Number(warning.row_index);
        const colIndex = Number(warning.col_index);
        if (!rows[rowIndex] || colIndex < 0 || colIndex >= rows[rowIndex].length) continue;
        rows[rowIndex][colIndex] = String(warning.suggested_value || "").trim();
      }
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
    setSelectedAnomalyIndex(null);
    setSelectedAutoEditIndex(null);
  };

  const applySingleAnomalyCorrection = (warning: SheetAnomalyWarning) => {
    flushPendingSheetCellEdits();
    if (!sheetPayload || typeof warning.row_index !== "number" || typeof warning.col_index !== "number") return;
    const suggestedValue = String(warning.suggested_value || "").trim();
    if (!suggestedValue) return;
    const rowIndex = Number(warning.row_index);
    const colIndex = Number(warning.col_index);
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => [...row]);
      if (!rows[rowIndex] || colIndex < 0 || colIndex >= rows[rowIndex].length) return current;
      rows[rowIndex][colIndex] = suggestedValue;
      const nextSheet = { ...current, rows };
      markSheetJsonStale();
      return nextSheet;
    });
    const sourceReview = anomalyReview || {};
    setLocalAnomalyReview({
      ...sourceReview,
      warnings: removeFirstMatchingItem(anomalyWarnings, (item) => sameSheetPatchTarget(item, warning)),
    });
    setSheetAutoEditResult((current) => {
      if (!current) return current;
      return { ...current, patches: removeItemsForSheetCell(current.patches || [], rowIndex, colIndex) };
    });
    setSelectedAnomalyIndex(null);
    setSelectedAutoEditIndex(null);
  };

  const dismissSingleAnomalyWarning = (warning: SheetAnomalyWarning) =>
    runAction("Step3 anomaly warning dismiss", async () => {
      const parsed = getSheetPayloadForAction();
      if (!parsed) {
        throw new Error("異常候補の却下に使えるシートがありません");
      }
      const response = await apiClient.post<{ anomaly_review?: Record<string, unknown>; pre_save_checks?: PreSaveChecks; pre_save_status?: PreSaveStatus }>(
        `/orders/${orderId}/workflow-v2/sheet/anomaly-review/dismiss`,
        { sheet: parsed, warning },
      );
      const nextAnomalyReview = anomalyReviewWithoutWarning(response.data.anomaly_review || anomalyReview, warning);
      setLocalAnomalyReview(nextAnomalyReview);
      setInspection((current) => current ? { ...current, anomaly_review: nextAnomalyReview } : current);
      applyPreSaveState(response.data.pre_save_checks || {}, response.data.pre_save_status || null);
      setSelectedAnomalyIndex(null);
    }, {
      successMessage: "異常候補を却下しました",
      refreshAfter: false,
    });

  const saveSheet = () =>
    runAction("Step3 sheet save", async () => {
      const latestWorkflow = await apiClient.get<WorkflowV2>(`/orders/${orderId}/workflow-v2`);
      setWorkflow(latestWorkflow.data);
      if (!latestWorkflow.data?.selected_ocr_result_id) {
        setVisibleStep(2);
        throw new Error("正解OCRが未選択です。Step2で使用するOCR結果を一つ選んでから、シートを保存してください。");
      }
      const parsed = getSheetPayloadForAction();
      if (!parsed) {
        throw new Error("保存できるシートがありません");
      }
      if (!effectiveAnomalyReviewConfirmed || !effectiveSheetReviewConfirmed) {
        throw new Error("シート保存前に、異常チェックとシート確認を完了してください。");
      }
      await apiClient.put(`/orders/${orderId}/workflow-v2/sheet`, {
        sheet: parsed,
        edited_by: "operator",
      });
      setSelectedAnomalyIndex(null);
      setSelectedAutoEditIndex(null);
    }, {
      successMessage: "シートを保存しました",
      nextStep: 4,
    });

  const runBagging = () =>
    runAction("Step4 bagging", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/bagging`);
    }, {
      successMessage: "出力確認を作成しました",
    });

  const runAnomalyReview = () =>
    runAction("Step3 anomaly review", async () => {
      const parsed = getSheetPayloadForAction();
      if (!parsed) {
        throw new Error("数量異常チェックに渡せるシートがありません");
      }
      const response = await apiClient.post<{ anomaly_review?: Record<string, unknown>; pre_save_checks?: PreSaveChecks; pre_save_status?: PreSaveStatus }>(
        `/orders/${orderId}/workflow-v2/sheet/anomaly-review`,
        { sheet: parsed, use_llm: true },
        { timeout: AI_REVIEW_REQUEST_TIMEOUT_MS },
      );
      setLocalAnomalyReview(response.data.anomaly_review || null);
      applyPreSaveState(response.data.pre_save_checks || {}, response.data.pre_save_status || null);
      setSelectedAnomalyIndex(null);
      setSelectedAutoEditIndex(null);
    }, {
      successMessage: "数量異常チェックを実行しました",
      refreshAfter: false,
    });

  const showSheetReview = () =>
    runAction("Step3 sheet review confirm", async () => {
      const parsed = getSheetPayloadForAction();
      if (!parsed) {
        throw new Error("シート確認に使えるシートがありません");
      }
      const response = await apiClient.post<{ pre_save_checks?: PreSaveChecks; pre_save_status?: PreSaveStatus }>(
        `/orders/${orderId}/workflow-v2/sheet/review-confirm`,
        { sheet: parsed },
      );
      applyPreSaveState(response.data.pre_save_checks || {}, response.data.pre_save_status || null);
      setOcrPreviewMode("sheet");
    }, {
      successMessage: "シート確認を記録しました",
      refreshAfter: false,
    });

  const finalConfirm = () =>
    runAction(
      "Step4 final confirm",
      async () => {
        await apiClient.post(`/orders/${orderId}/workflow-v2/confirm`, {
          confirmed_by: "operator",
        });
        await router.push("/orders");
      },
      {
        successMessage: "注文を確定しました",
        refreshAfter: false,
      },
    );

  const extractFilename = (contentDisposition?: string | null) => {
    if (!contentDisposition) return "";
    const utfMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
    const rawName = utfMatch?.[1] || contentDisposition.match(/filename="?([^";]+)"?/i)?.[1] || "";
    if (!rawName) return "";
    try {
      return decodeURIComponent(rawName);
    } catch {
      return rawName;
    }
  };

  const openOutput = async (path: string, label: string) => {
    const timestamp = new Date().toLocaleString("ja-JP");
    setDownloadMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "blob" });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "output";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setDownloadMessage(`${label}をダウンロードしました。 (${timestamp})`);
    } catch (err: any) {
      const status = err?.response?.status;
      const suffix = status ? ` (${status})` : "";
      setDownloadMessage(`${label}のダウンロードに失敗しました。${suffix}`);
    }
  };

  const openHtmlOutput = async (path: string, label: string) => {
    const timestamp = new Date().toLocaleString("ja-JP");
    setDownloadMessage(`${label}を開きます。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "text", timeout: 0 });
      const win = window.open("", "_blank");
      if (!win) {
        setDownloadMessage("ブラウザで新しいタブを許可してください。");
        return;
      }
      win.document.open();
      win.document.write(String(res.data || ""));
      win.document.close();
      setDownloadMessage(`${label}を別タブで開きました。`);
    } catch (err: any) {
      const status = err?.response?.status;
      const suffix = status ? ` (${status})` : "";
      setDownloadMessage(`${label}を開けませんでした。${suffix}`);
    }
  };

  const outputPreviewLabels: Record<OutputPreviewType, string> = {
    labels: "ラベルCSV",
    delivery: "納品書HTML",
    order_form_saved_sheet: "FAX読取シートExcel",
    aggregate: "総量CSV",
  };

  const loadOutputPreview = async (type: OutputPreviewType) => {
    if (!orderId) return;
    setOutputPreviewLoading(true);
    setOutputPreview(null);
    setOutputPreviewMessage("プレビューを開いています...");
    let popup: Window | null = null;
    const escapeHtml = (value: string) => value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
    const writePopupMessage = (title: string, message: string) => {
      if (!popup) return;
      const safeTitle = escapeHtml(title);
      const safeMessage = escapeHtml(message);
      popup.document.open();
      popup.document.write(`<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>${safeTitle}</title>
  <style>
    body { background:#f3f1ea; color:#1f2a2a; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; padding:20px; }
    .message { background:white; border:1px solid #d8d2c4; padding:16px; }
    h1 { font-size:18px; margin:0 0 12px; }
    p { margin:0; white-space:pre-wrap; }
  </style>
</head>
<body>
  <div class="message">
    <h1>${safeTitle}</h1>
    <p>${safeMessage}</p>
  </div>
</body>
</html>`);
      popup.document.close();
      popup.focus();
    };
    try {
      popup = window.open("", "_blank", "popup=yes,width=1180,height=840");
      if (popup) {
        popup.document.title = `${outputPreviewLabels[type]} プレビュー`;
        popup.document.body.innerHTML = "<p>プレビューを読み込み中...</p>";
      }
      const res = await apiClient.get("/outputs/file-preview", {
        params: { order_id: orderId, type },
        responseType: "text",
      });
      if (popup) {
        popup.document.open();
        popup.document.write(String(res.data || ""));
        popup.document.close();
        popup.focus();
        setOutputPreviewMessage("");
      } else {
        const blob = new Blob([String(res.data || "")], { type: "text/html;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 10000);
        setOutputPreviewMessage("");
      }
    } catch (err: any) {
      const status = err?.response?.status;
      let detail = err?.response?.data?.detail || "";
      if (!detail && typeof err?.response?.data === "string") {
        try {
          detail = JSON.parse(err.response.data)?.detail || err.response.data;
        } catch {
          detail = err.response.data;
        }
      }
      detail = detail || err?.message || "";
      const suffix = status ? ` (${status})` : "";
      const message = `${outputPreviewLabels[type]}のプレビュー取得に失敗しました。${suffix}${detail ? `\n${detail}` : ""}`;
      writePopupMessage(`${outputPreviewLabels[type]} プレビュー`, message);
      setOutputPreviewMessage(message);
    } finally {
      setOutputPreviewLoading(false);
    }
  };

  const quadImageSrc = quadReview?.image_png_base64 ? `data:image/png;base64,${quadReview.image_png_base64}` : "";
  const quadImageWidth = Number(quadReview?.image_size?.[0] || 2000);
  const quadImageHeight = Number(quadReview?.image_size?.[1] || 2800);
  const suggestedQuad = (
    (Array.isArray(quadReview?.suggested_quad_px) && quadReview?.suggested_quad_px?.length === 4)
      ? quadReview.suggested_quad_px
      : (Array.isArray(quadReview?.estimate?.refined_quad_px) ? quadReview?.estimate?.refined_quad_px : [])
  ) as number[][];
  const activeQuad = manualQuadMode ? manualQuadPoints : suggestedQuad;
  const quadPolylinePoints = Array.isArray(activeQuad)
    ? activeQuad.map((point) => `${Number(point?.[0] || 0)},${Number(point?.[1] || 0)}`).join(" ")
    : "";
  const headerAxisImageSrc = headerAxisReview?.image_png_base64 ? `data:image/png;base64,${headerAxisReview.image_png_base64}` : "";
  const headerAxisImageWidth = Number(headerAxisReview?.image_size?.[0] || 1400);
  const headerAxisImageHeight = Number(headerAxisReview?.image_size?.[1] || 360);
  const headerAxisCropX0 = Number(headerAxisReview?.crop_box?.[0] || 0);
  const headerAxisCropY0 = Number(headerAxisReview?.crop_box?.[1] || 0);
  const headerAxisYLevels = (Array.isArray(headerAxisReview?.y_levels) ? headerAxisReview?.y_levels : [])
    .map((value) => Number(value))
    .filter(Number.isFinite);
  const headerAxisDisplayYLevels = headerAxisYLevels.length
    ? headerAxisYLevels
    : [headerAxisCropY0 + headerAxisImageHeight * 0.35, headerAxisCropY0 + headerAxisImageHeight * 0.7];
  const headerAxisExpectedCount = getHeaderAxisExpectedCount();
  const headerAxisCountWarning = headerAxisExpectedCount > 0 && headerAxisXs.length !== headerAxisExpectedCount
    ? `ヘッダー縦軸の本数がテンプレート期待値と一致していません。期待 ${headerAxisExpectedCount} 本 / 現在 ${headerAxisXs.length} 本。`
    : "";
  const currentFaxVersion = orderDetail?.current_version || null;
  const faxVersionCount = Number(orderDetail?.version_count || orderDetail?.versions?.length || 0);
  const faxVersionLabel = currentFaxVersion?.version_no
    ? `v${currentFaxVersion.version_no}${faxVersionCount ? ` / ${faxVersionCount}版` : ""}`
    : "-";
  const selectedFaxVersionLabel = selectedDocumentVersion?.version_no
    ? `FAX v${selectedDocumentVersion.version_no}${selectedDocumentVersion.is_current ? " (現行)" : ""}`
    : "現行FAX";

  return (
    <main className="page workflow-v2-page">
      <header className="hero">
        <div>
          <p className="eyebrow">Workflow V2</p>
          <h1>注文処理 v2</h1>
          <p className="subtle">DB workflow state と artifact lineage だけで step を進めます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel state-panel">
        <div className="state-panel-main">
          <p className="eyebrow">注文情報 / 現在状態</p>
          <h2>{stateLabel(workflow?.state)}</h2>
          <p className="subtle">{workflow?.headline || "workflow-v2 を読み込み中です。"}</p>
          {workflow?.state === "ocr_running" ? (
            <p className="ocr-progress-inline">OCR進捗: {formatOcrProgress(workflow)}</p>
          ) : null}
        </div>
        <div className="summary-grid summary-grid--compact summary-grid--order-info">
          <div className="summary-primary-card">
            <span className="field-label">注文 / 状態</span>
            <p className="summary-value">{orderId || "-"}</p>
            <p className="summary-subline">{stateLabel(workflow?.state)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">施設</span>
            <p className="summary-value">{workflowFacilityLabel}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">週次 / テンプレート</span>
            <p className="summary-value">
              {formatWeekLabel(weekValueFromRange(workflow?.week_start, workflow?.week_end)) || "未設定"}
            </p>
            <p className="summary-subline">{workflow?.template_id || "施設設定から自動解決"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR</span>
            <p className="summary-value">{ocrResults.length}件 / {formatOcrProgress(workflow)}</p>
            <p className="summary-subline">処理時間: {formatElapsedSeconds(workflow?.ocr_job?.elapsed_seconds)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR開始 / 更新</span>
            <p className="summary-value">{formatDateTime(workflow?.ocr_job?.started_at)}</p>
            <p className="summary-subline">{formatDateTime(workflow?.ocr_job?.finished_at || workflow?.ocr_job?.updated_at)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">シート最終保存</span>
            <p className="summary-value">{formatDateTime(inspection?.saved_sheet?.edited_at || inspection?.saved_sheet?.created_at)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">FAX version</span>
            <p className="summary-value">{faxVersionLabel}</p>
            <p className="summary-subline">{formatDateTime(currentFaxVersion?.received_at || null)}</p>
          </div>
        </div>
        <div className="state-actions">
          <button className="btn ghost" type="button" onClick={() => void refreshAll()} disabled={Boolean(busy)}>
            再読込
          </button>
          {canReviewQuad ? (
            <button className="btn ghost" type="button" onClick={() => setVisibleStep(1.5)} disabled={Boolean(busy)}>
              4点確認/補正
            </button>
          ) : null}
          {orderId ? (
            <>
              <Link className="ghost-link" href={`/orders/${orderId}/inspection-v2`}>
                確認専用ページ
              </Link>
              <Link className="ghost-link" href="/orders">
                注文一覧へ戻る
              </Link>
            </>
          ) : null}
        </div>
      </section>

      {message ? <div className="notice success">{message}</div> : null}
      {error ? <div className="notice error">{error}</div> : null}
      {ocrJobErrorMessage && workflow?.ocr_job?.status === "failed" ? (
        <div className="notice error">
          <strong>OCR失敗:</strong> {ocrJobErrorMessage}
          {ocrJobRecoveryAction === "review_or_edit_quad" ? (
            <span>
              {" "}Step1.5で推定4点を確認してください。
              <button className="inline-action-btn" type="button" onClick={() => setVisibleStep(1.5)}>
                4点を確認/補正
              </button>
            </span>
          ) : null}
        </div>
      ) : null}
      {workflowBlockers.length ? (
        <div className="notice error">
          {workflowBlockers.map(describeWorkflowBlocker).join(" / ")}
        </div>
      ) : null}

      <nav className="step-nav" aria-label="workflow steps">
        {stepLabels.map((item) => (
          <button
            key={item.step}
            className={`step-nav-btn ${visibleStep === item.step ? "active" : ""}`}
            type="button"
            onClick={() => setVisibleStep(item.step)}
          >
            <span>Step{item.step}</span>
            <strong>{item.label}</strong>
          </button>
        ))}
      </nav>

      <section className="step-page">
        {visibleStep === 1 ? (
        <section className="panel">
          <p className="step-tag">Step1</p>
          <header className="panel-header">
            <div>
              <h2>注文書 (FAX PDF)</h2>
              <p className="subtle">原本PDFを確認し、施設と週設定を完了してください。OCR再実行は選択中のFAX PDFを入力にします。</p>
            </div>
            {pdfUrl ? (
              <a className="ghost-link" href={pdfUrl} target="_blank" rel="noreferrer">
                原本を開く
              </a>
            ) : (
              <span className="subtle">{pdfError || "PDFを読み込み中..."}</span>
            )}
          </header>
          <div className="step1-facility-block">
            {documentVersionOptions.length > 1 ? (
              <label className="field">
                <span className="field-label">OCR入力にするFAX PDF</span>
                <select
                  className="input"
                  value={effectiveSelectedDocumentId}
                  onChange={(event) => setSelectedDocumentId(event.target.value)}
                  disabled={Boolean(busy)}
                >
                  {documentVersionOptions.map((version) => {
                    const docId = String(version.document_id || "").trim();
                    const versionLabel = version.version_no ? `FAX v${version.version_no}` : "FAX";
                    const currentLabel = version.is_current ? " / 現行" : "";
                    return (
                      <option key={docId} value={docId}>
                        {versionLabel}{currentLabel} / 受信 {formatDateTime(version.received_at || null)}
                      </option>
                    );
                  })}
                </select>
                <span className="subtle">選択中: {selectedFaxVersionLabel}。このPDFを表示し、OCR再実行の入力にも使います。</span>
              </label>
            ) : null}
            <div className="step1-current-strip">
              <div className="step1-current-pills">
                <span>現在: {workflowFacilityLabel}</span>
                <span>{formatWeekLabel(weekValueFromRange(workflow?.week_start, workflow?.week_end)) || "週未設定"}</span>
                <span>{workflow?.template_id || "テンプレートは施設設定から自動解決"}</span>
              </div>
              <div className="step1-selection-summary">
                <strong>選択中: {contextFacilityLabel}</strong>
                <span>{formatWeekLabel(selectedWeekValue || "") || "週未選択"}</span>
              </div>
            </div>
            <div className="step1-control-grid">
              <div className="step1-control-column">
                {contextSuggestion ? (
                  <div className="context-suggestion-card compact">
                    <div>
                      <span className="field-label">PDF自動推定候補</span>
                      <h3>
                        {contextSuggestion.facility_name || contextSuggestion.facility_id || "施設未推定"}
                        {contextSuggestion.facility_id && contextSuggestion.facility_name ? ` (${contextSuggestion.facility_id})` : ""}
                      </h3>
                      <p className="subtle">
                        週次: {contextSuggestionWeekLabel(contextSuggestion)}
                        {contextSuggestion.confidence ? ` / confidence ${contextSuggestion.confidence}` : ""}
                        {contextSuggestion.source ? ` / ${contextSuggestion.source}` : ""}
                      </p>
                      {Array.isArray(contextSuggestion.date_hints) && contextSuggestion.date_hints.length ? (
                        <p className="subtle">日付候補: {contextSuggestion.date_hints.slice(0, 6).join(" / ")}</p>
                      ) : null}
                      {Array.isArray(contextSuggestion.facility_candidates) && contextSuggestion.facility_candidates.length ? (
                        <ul className="context-suggestion-candidates">
                          {contextSuggestion.facility_candidates.slice(0, 2).map((candidate, index) => (
                            <li key={`facility-suggestion-${index}`}>{formatFacilityCandidate(candidate)}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                    <button className="btn secondary" type="button" onClick={applyContextSuggestion} disabled={Boolean(busy)}>
                      推定を反映
                    </button>
                  </div>
                ) : (
                  <div className="context-suggestion-card compact muted">
                    <div>
                      <span className="field-label">PDF自動推定候補</span>
                      <p className="subtle">推定候補はありません。PDFを見て施設と週を選択してください。</p>
                    </div>
                  </div>
                )}
                <label className="field">
                  <span className="field-label">施設 (Step1 必須)</span>
                  <select
                    className="input"
                    value={contextForm.facility_id}
                    onChange={(event) => setContextForm((current) => ({ ...current, facility_id: event.target.value }))}
                    disabled={facilityOptionsLoading || Boolean(busy)}
                  >
                    <option value="">施設を選択</option>
                    {contextForm.facility_id && !facilityOptions.some((option) => option.id === contextForm.facility_id) ? (
                      <option value={contextForm.facility_id}>{contextForm.facility_id} (未登録)</option>
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
              </div>
              <div className="step1-control-column">
                <label className="field">
                  <span className="field-label">週 (Step1 必須)</span>
                  <select
                    className="input"
                    value={weekDraft}
                    onChange={(event) => applyWeekValue(event.target.value)}
                    disabled={weekOptionsLoading || Boolean(busy)}
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
                </label>
                <details
                  className="exception-range-details"
                  open={showExceptionRange}
                  onToggle={(event) => setShowExceptionRange(event.currentTarget.open)}
                >
                  <summary>例外範囲を指定する</summary>
                  <div className="step1-week-range">
                    <div className="summary-actions compact">
                      <input
                        className="input"
                        type="date"
                        value={customWeekRangeStart}
                        onChange={(event) => setCustomWeekRangeStart(event.target.value)}
                        disabled={Boolean(busy)}
                      />
                      <input
                        className="input"
                        type="date"
                        value={customWeekRangeEnd}
                        onChange={(event) => setCustomWeekRangeEnd(event.target.value)}
                        disabled={Boolean(busy)}
                      />
                      <button type="button" className="btn secondary" onClick={applyCustomWeekRange} disabled={Boolean(busy)}>
                        反映
                      </button>
                    </div>
                    {selectedWeekValue ? (
                      <span className="subtle">設定予定: {formatWeekLabel(selectedWeekValue) || selectedWeekValue}</span>
                    ) : null}
                  </div>
                </details>
              </div>
            </div>
            <div className="step1-action-row">
              <button className="btn primary" type="button" onClick={confirmContext} disabled={Boolean(busy || !contextReady || facilityTemplateMissing)}>
                {facilityTemplateMissing ? "先にテンプレート登録" : contextReady ? "設定を保存" : "施設と週を選択"}
              </button>
              <label className="toolbar-field expanded-cell-toggle">
                <span>拡大セルコピー</span>
                <select
                  value={expandedCellCopyMode}
                  onChange={(event) => void updateExpandedCellCopyMode(event.target.value as ExpandedCellCopyMode)}
                  disabled={Boolean(busy || expandedCellCopySaving || !workflow?.order_id)}
                >
                  <option value="auto">自動</option>
                  <option value="enabled">ON</option>
                  <option value="disabled">OFF</option>
                </select>
              </label>
              <label className="toolbar-field ocr-mode-field">
                <span>OCR実行方式</span>
                <select value={ocrRunMode} onChange={(event) => setOcrRunMode(event.target.value as OcrRunMode)} disabled={Boolean(busy)}>
                  <option value="hakodate">箱館方式</option>
                  <option value="llm">AIに任せる</option>
                </select>
              </label>
              <button
                className="btn"
                type="button"
                onClick={runOcr}
                disabled={Boolean(
                  busy
                  || !workflowContextConfirmed
                  || !facilityTemplateReadyForOcr
                  || ocrPrerequisiteBlockers.length > 0
                  || (ocrRunMode === "llm" && llmProvider === "gemini" && llmModelMode === "other" && !llmCustomModel.trim())
                )}
              >
                OCRを実行
              </button>
            </div>
            {!facilityTemplateReadyForOcr && contextForm.facility_id ? (
              <p className="workflow-warning">
                {facilityTemplateMissing
                  ? "施設テンプレートが未登録です。OCR実行前に帳票レイアウトを登録してください。"
                  : facilityTemplateColumnsUnresolved
                    ? "施設テンプレートの列定義が未解決です。OCR実行前に施設区分列を確認してください。"
                    : facilityTemplateDirty
                      ? "施設区分列に未保存の変更があります。保存してからOCRを実行してください。"
                      : "施設テンプレートを確認してからOCRを実行してください。"}
              </p>
            ) : null}
            {ocrRunMode === "llm" ? (
              <div className="ocr-run-options compact">
                <label className="toolbar-field">
                  <span>自動調整プリセット</span>
                  <select value={llmPromptPreset} onChange={(event) => setLlmPromptPreset(event.target.value as LlmPromptPreset)} disabled={Boolean(busy)}>
                    {(Object.keys(llmPromptPresetLabels) as LlmPromptPreset[]).map((preset) => (
                      <option key={preset} value={preset}>{llmPromptPresetLabels[preset]}</option>
                    ))}
                  </select>
                </label>
                <label className="toolbar-field">
                  <span>AIエンジン</span>
                  <select value={llmProvider} onChange={(event) => setLlmProvider(event.target.value)} disabled={Boolean(busy)}>
                    <option value="openai">OpenAI</option>
                    <option value="gemini">Gemini</option>
                  </select>
                </label>
                {llmProvider === "gemini" ? (
                  <label className="toolbar-field">
                    <span>Geminiモデル</span>
                    <select value={llmModelMode} onChange={(event) => setLlmModelMode(event.target.value as "flash" | "pro" | "other")} disabled={Boolean(busy)}>
                      <option value="flash">Flash</option>
                      <option value="pro">Pro</option>
                      <option value="other">カスタム</option>
                    </select>
                  </label>
                ) : null}
                {llmProvider === "gemini" && llmModelMode === "other" ? (
                  <input
                    className="compact-input llm-model-input"
                    value={llmCustomModel}
                    onChange={(event) => setLlmCustomModel(event.target.value)}
                    placeholder="gemini model"
                  />
                ) : null}
                <details className="inline-details">
                  <summary>AI追加指示（任意）</summary>
                  <textarea
                    className="ocr-llm-prompt-textarea"
                    value={ocrPrompt}
                    onChange={(event) => setOcrPrompt(event.target.value)}
                    placeholder="例: 読みづらい手書き数量は前後セルの連続性を見て補完する"
                  />
                </details>
              </div>
            ) : null}
            <p className="subtle">
              拡大セルコピーは、merged cell施設で同じ食区分内へ数量を反映する設定です。自動は施設テンプレート設定に従います。
            </p>
            {contextForm.facility_id ? (
              <div className={`facility-template-resolution ${facilityTemplateReadyForOcr ? "resolved" : "blocked"}`}>
                <div className="facility-template-resolution-copy">
                  <strong>施設テンプレート登録</strong>
                  <p>
                    {facilityTemplateStatus.loading
                      ? "施設テンプレート設定を確認中です。"
                      : facilityTemplateStatus.templateId
                        ? `登録済み: ${facilityTemplateStatus.facilityTemplateId || contextFacilityLabel}`
                        : "この施設には帳票レイアウトが登録されていません。未登録のままOCRは実行できません。"}
                  </p>
                  {facilityTemplateStatus.templateId ? (
                    <p className="subtle">
                      帳票レイアウト: {formatFaxTemplateOptionLabel(selectedFacilityRegisteredTemplateOption) || facilityTemplateStatus.templateId}
                    </p>
                  ) : null}
                  {selectedFacility ? (
                    <p className="subtle">対象施設: {formatFacilityLabel(selectedFacility)}</p>
                  ) : null}
                  {facilityTemplateQuantitySummary.length ? (
                    <p className="subtle">OCR前確認: {facilityTemplateQuantitySummary.join(" / ")}</p>
                  ) : null}
                  {facilityTemplateStatus.error ? <p className="subtle">{facilityTemplateStatus.error}</p> : null}
                </div>
                {facilityTemplateMissing ? (
                  <div className="facility-template-register">
                    <label className="toolbar-field">
                      <span>帳票レイアウト</span>
                      <select
                        value={selectedFacilityTemplateId}
                        onChange={(event) => setSelectedFacilityTemplateId(event.target.value)}
                        disabled={Boolean(busy || faxTemplateOptionsLoading)}
                      >
                        <option value="">レイアウトを選択</option>
                        {faxTemplateOptions.map((option) => (
                          <option key={option.template_id} value={option.template_id}>
                            {formatFaxTemplateOptionLabel(option)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selectedFacilityTemplateOption?.quantity_headers?.length ? (
                      <p className="subtle">数量列: {selectedFacilityTemplateOption.quantity_headers.join(" / ")}</p>
                    ) : null}
                    {faxTemplateOptionsError ? <p className="subtle">{faxTemplateOptionsError}</p> : null}
                    <button
                      className="btn primary"
                      type="button"
                      onClick={registerFacilityTemplate}
                      disabled={Boolean(busy || faxTemplateOptionsLoading || !selectedFacilityTemplateId)}
                    >
                      施設テンプレートに登録してStep1を保存
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {contextForm.facility_id && !facilityTemplateMissing ? (
              <div className="facility-template-editor">
                <div className="facility-template-editor-header">
                  <div>
                    <strong>施設区分列</strong>
                    <p className="subtle">
                      施設テンプレートと連動する数量列定義です。現在: {facilityTemplateQuantitySummary.length ? facilityTemplateQuantitySummary.join(" / ") : "未設定"}
                    </p>
                  </div>
                  <div className="row-actions">
                    <button className="btn ghost" type="button" onClick={() => setShowFacilityTemplateEditor((current) => !current)}>
                      {showFacilityTemplateEditor ? "列設定を閉じる" : "列設定を確認/修正"}
                    </button>
                    <button
                      className="btn primary"
                      type="button"
                      onClick={saveFacilityTemplateColumns}
                      disabled={Boolean(busy || facilityTemplateSaving || !facilityTemplateDirty || !facilityTemplateColumnDraft.length)}
                    >
                      {facilityTemplateSaving ? "保存中..." : "施設区分列を保存"}
                    </button>
                  </div>
                </div>
                {facilityTemplateMessage ? <p className="subtle">{facilityTemplateMessage}</p> : null}
                {showFacilityTemplateEditor ? (
                  <div className="facility-template-editor-body">
                    <div className="facility-template-callout">
                      <p>
                        表示名・役割・上段ヘッダー・並びだけを設定します。内部名・区分・エリアは保存時に表示名から自動生成します。
                        保存するとこの施設全体のテンプレート列定義を更新します。既存OCR結果・保存シート・袋分け・出力は破棄され、Step1からOCR再実行になります。
                      </p>
                    </div>
                    <div className="facility-template-actions">
                      <select
                        className="input"
                        value={facilityTemplateSwapLeft}
                        onChange={(event) => setFacilityTemplateSwapLeft(event.target.value)}
                      >
                        <option value="">入替元</option>
                        {facilityTemplateColumnDraft.map((column, idx) => (
                          <option key={`template-swap-left-${idx}`} value={String(column.index)}>
                            {column.index + 1}: {column.header || column.name || column.role}
                          </option>
                        ))}
                      </select>
                      <select
                        className="input"
                        value={facilityTemplateSwapRight}
                        onChange={(event) => setFacilityTemplateSwapRight(event.target.value)}
                      >
                        <option value="">入替先</option>
                        {facilityTemplateColumnDraft.map((column, idx) => (
                          <option key={`template-swap-right-${idx}`} value={String(column.index)}>
                            {column.index + 1}: {column.header || column.name || column.role}
                          </option>
                        ))}
                      </select>
                      <button className="btn ghost" type="button" onClick={applySelectedFacilityTemplateColumnSwap}>
                        列を入れ替える
                      </button>
                      <button className="btn ghost" type="button" onClick={appendFacilityTemplateColumn}>
                        列を追加
                      </button>
                    </div>
                    <div className="facility-template-table-wrap">
                      <table className="facility-template-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            <th>役割</th>
                            <th>表示名</th>
                            <th>上段ヘッダー</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {facilityTemplateColumnDraft.map((column, idx) => {
                            return (
                              <tr key={`facility-template-column-${column.index}-${idx}`}>
                                <td>{idx + 1}</td>
                                <td>
                                  <select
                                    className="input"
                                    value={column.role}
                                    onChange={(event) => updateFacilityTemplateColumn(idx, "role", event.target.value)}
                                  >
                                    {columnRoleOptions.map((option) => (
                                      <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                  </select>
                                </td>
                                <td>
                                  <input
                                    className="input"
                                    value={column.header || ""}
                                    onChange={(event) => updateFacilityTemplateColumn(idx, "header", event.target.value)}
                                    placeholder="表示名"
                                  />
                                </td>
                                <td>
                                  <input
                                    className="input"
                                    value={column.header_group || ""}
                                    onChange={(event) => updateFacilityTemplateColumn(idx, "header_group", event.target.value)}
                                    placeholder="空欄なら1段"
                                    disabled={!isQuantityRole(column.role)}
                                  />
                                </td>
                                <td>
                                  <div className="facility-template-row-actions">
                                    <button className="btn ghost" type="button" onClick={() => applyFacilityTemplateColumnSwap(idx, idx - 1)} disabled={idx <= 0}>
                                      前へ
                                    </button>
                                    <button className="btn ghost" type="button" onClick={() => applyFacilityTemplateColumnSwap(idx, idx + 1)} disabled={idx >= facilityTemplateColumnDraft.length - 1}>
                                      次へ
                                    </button>
                                    <button className="btn danger" type="button" onClick={() => deleteFacilityTemplateColumn(idx)}>
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
                  </div>
                ) : null}
              </div>
            ) : null}
            {!workflow?.facility_id || !workflow?.week_start || !workflow?.week_end ? (
              <div className="warning-banner">
                {!workflow?.facility_id ? <p>この注文は施設が未設定です。OCR実行前に施設設定が必要です。</p> : null}
                {!workflow?.week_start || !workflow?.week_end ? <p>この注文は週が未設定です。OCR実行前に週設定が必要です。</p> : null}
              </div>
            ) : null}
          </div>
          <div className="pdf-preview-panel">
            <div className="pdf-preview-header">
              <div>
                <h3>原本PDF</h3>
                <p className="subtle">施設と週次の確認用です。</p>
              </div>
            </div>
            {pdfUrl ? (
              <iframe title="workflow-v2-original-pdf" src={pdfUrl} className="pdf-frame pdf-frame-wide" />
            ) : (
              <div className="pdf-placeholder">{pdfError || "PDFを読み込み中..."}</div>
            )}
          </div>
        </section>
        ) : null}

        {visibleStep === 1.5 ? (
        <section className="panel quad-review-panel">
          <p className="step-tag">Step1.5</p>
          <header className="panel-header">
            <div>
              <h2>4点推定の確認 / 手動補正</h2>
              <p className="subtle">
                OCRが失敗した場合や、OCR結果の行・列位置がずれている場合に使います。推定4点が表外枠に合っていればOK、合っていなければ手動で左上→右上→右下→左下の順に指定します。
              </p>
            </div>
            <button className="btn ghost" type="button" onClick={() => void loadQuadReview()} disabled={quadReviewLoading || Boolean(busy)}>
              {quadReviewLoading ? "取得中..." : "4点推定を再取得"}
            </button>
          </header>
          {quadReviewMessage ? <div className="notice error">{quadReviewMessage}</div> : null}
          {quadReview?.estimate ? (
            <div className="quad-review-summary">
              <div>
                <span className="field-label">判定</span>
                <strong>{quadReview.estimate.status || "-"}</strong>
              </div>
              <div>
                <span className="field-label">NG理由</span>
                <p>{(quadReview.estimate.reasons || []).join(" / ") || "なし"}</p>
              </div>
              <div>
                <span className="field-label">警告</span>
                <p>{(quadReview.estimate.warnings || []).join(" / ") || "なし"}</p>
              </div>
              <div>
                <span className="field-label">許容範囲</span>
                <p>
                  平均offset OK上限 {quadReview.tolerance_policy?.mean_abs_offset_px_ok_max ?? 4.5}px /
                  NG {quadReview.tolerance_policy?.mean_abs_offset_px_hard_ng ?? 8}px /
                  hit率下限 {quadReview.tolerance_policy?.hit_rate_min ?? 0.78}
                </p>
              </div>
            </div>
          ) : null}
          <div className="quad-review-actions">
            <button
              className="btn primary"
              type="button"
              onClick={() => void saveQuadAndRerun("approved_estimate", suggestedQuad)}
              disabled={Boolean(busy || suggestedQuad.length !== 4)}
            >
              推定4点をOKにしてOCR再実行
            </button>
            <button
              className="btn ghost"
              type="button"
              onClick={() => {
                setManualQuadMode(true);
                setManualQuadPoints([]);
              }}
              disabled={Boolean(busy)}
            >
              NG: 手動で4点指定
            </button>
            {manualQuadMode ? (
              <>
                <button className="btn ghost" type="button" onClick={() => setManualQuadPoints([])} disabled={Boolean(busy)}>
                  手動点をクリア
                </button>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() => void saveQuadAndRerun("manual_override", manualQuadPoints)}
                  disabled={Boolean(busy || manualQuadPoints.length !== 4)}
                >
                  手動4点を保存してOCR再実行
                </button>
              </>
            ) : null}
          </div>
          {manualQuadMode ? (
            <p className="workflow-warning">
              手動指定中: {manualQuadPoints.length}/4 点。順序は 左上 → 右上 → 右下 → 左下 です。
            </p>
          ) : null}
          <div className="quad-review-canvas-wrap">
            {quadImageSrc ? (
              <div className="quad-review-canvas">
                <img
                  src={quadImageSrc}
                  alt="4点推定確認"
                  onClick={handleQuadImageClick}
                  className={manualQuadMode ? "quad-clickable-image" : ""}
                />
                <svg viewBox={`0 0 ${quadImageWidth} ${quadImageHeight}`} aria-hidden="true">
                  {suggestedQuad.length === 4 ? (
                    <polygon
                      points={suggestedQuad.map((point) => `${point[0]},${point[1]}`).join(" ")}
                      className="quad-estimate-polygon"
                    />
                  ) : null}
                  {quadPolylinePoints ? (
                    <polyline points={`${quadPolylinePoints} ${activeQuad.length === 4 ? `${activeQuad[0][0]},${activeQuad[0][1]}` : ""}`} className="quad-active-polyline" />
                  ) : null}
                  {(activeQuad || []).map((point, idx) => (
                    <g key={`quad-point-${idx}`}>
                      <circle cx={point[0]} cy={point[1]} r="18" className={manualQuadMode ? "quad-manual-point" : "quad-estimate-point"} />
                      <text x={point[0] + 24} y={point[1] - 18} className="quad-point-label">Q{idx + 1}</text>
                    </g>
                  ))}
                </svg>
              </div>
            ) : (
              <div className="pdf-placeholder">{quadReviewLoading ? "4点確認画像を取得中..." : "4点確認画像がありません。"}</div>
            )}
          </div>
        </section>
        ) : null}

        {visibleStep === 1.6 ? (
        <section className="panel header-axis-review-panel">
          <p className="step-tag">Step1.6</p>
          <header className="panel-header">
            <div>
              <h2>ヘッダー交点の手動補正</h2>
              <p className="subtle">
                2段ヘッダーの縦線判定が手書き訂正線などに引っ張られた場合だけ使います。点を横方向にドラッグして、実FAXのヘッダー交点に合わせてからOCRを再実行します。
              </p>
            </div>
            <div className="row-actions">
              <label className="header-axis-timeout-control">
                <span>timeout</span>
                <input
                  type="number"
                  min={String(MIN_HEADER_AXIS_REQUEST_TIMEOUT_MS / 1000)}
                  max={String(MAX_HEADER_AXIS_REQUEST_TIMEOUT_MS / 1000)}
                  step="30"
                  value={headerAxisTimeoutSeconds}
                  onChange={(event) => setHeaderAxisTimeoutSeconds(event.target.value)}
                  aria-label="ヘッダー補正API timeout 秒"
                />
                <span>秒</span>
              </label>
              <button className="btn ghost" type="button" onClick={() => void loadHeaderAxisReview()} disabled={headerAxisLoading || Boolean(busy)}>
                {headerAxisLoading ? "取得中..." : "ヘッダーを再取得"}
              </button>
              <button
                className={`btn ghost ${headerAxisAddMode ? "active" : ""}`}
                type="button"
                onClick={() => {
                  setHeaderAxisAddMode((current) => !current);
                  setHeaderAxisMessage("");
                }}
                disabled={headerAxisLoading || Boolean(busy) || !headerAxisImageSrc}
              >
                {headerAxisAddMode ? "追加位置をクリック" : "縦軸を追加"}
              </button>
              <button className="btn ghost" type="button" onClick={deleteSelectedHeaderAxis} disabled={headerAxisLoading || Boolean(busy) || selectedHeaderAxisIndex === null}>
                選択軸を削除
              </button>
              <button className="btn ghost" type="button" onClick={resetHeaderAxisXs} disabled={headerAxisLoading || Boolean(busy) || !headerAxisReview}>
                自動検出に戻す
              </button>
              <button className="btn primary" type="button" onClick={() => void saveHeaderAxisAndRerun()} disabled={Boolean(busy || headerAxisXs.length < 2)}>
                ヘッダー補正を保存してOCR再実行
              </button>
            </div>
          </header>
          {headerAxisCountWarning ? <div className="notice warning">{headerAxisCountWarning}</div> : null}
          <p className="subtle">
            現在 {headerAxisXs.length} 本{headerAxisExpectedCount ? ` / テンプレート期待 ${headerAxisExpectedCount} 本` : ""}。
            縦軸をクリックすると選択、ドラッグで移動、追加モード中は画像上のクリック位置に縦軸を追加します。
          </p>
          {headerAxisMessage ? <div className="notice error">{headerAxisMessage}</div> : null}
          <div className="header-axis-canvas-wrap">
            {headerAxisImageSrc ? (
              <div className="header-axis-canvas">
                <img src={headerAxisImageSrc} alt="ヘッダー交点補正" />
                <svg
                  viewBox={`0 0 ${headerAxisImageWidth} ${headerAxisImageHeight}`}
                  onMouseMove={handleHeaderAxisPointerMove}
                  onMouseUp={() => setDraggingHeaderAxisIndex(null)}
                  onMouseLeave={() => setDraggingHeaderAxisIndex(null)}
                  onClick={handleHeaderAxisCanvasClick}
                  aria-label="ヘッダー交点補正"
                >
                  {headerAxisXs.map((x, idx) => {
                    const displayX = x - headerAxisCropX0;
                    const selected = selectedHeaderAxisIndex === idx;
                    return (
                      <g key={`header-axis-${idx}`}>
                        <line x1={displayX} y1={0} x2={displayX} y2={headerAxisImageHeight} className={`header-axis-line ${selected ? "selected" : ""}`} />
                        {headerAxisDisplayYLevels.map((y, yIdx) => (
                          <circle
                            key={`header-axis-${idx}-${yIdx}`}
                            cx={displayX}
                            cy={y - headerAxisCropY0}
                            r="10"
                            className={`header-axis-point ${selected ? "selected" : ""}`}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              setSelectedHeaderAxisIndex(idx);
                              setDraggingHeaderAxisIndex(idx);
                            }}
                            onClick={(event) => {
                              event.stopPropagation();
                              setSelectedHeaderAxisIndex(idx);
                              setHeaderAxisAddMode(false);
                            }}
                          />
                        ))}
                        <text
                          x={displayX + 8}
                          y={20}
                          className={`header-axis-label ${selected ? "selected" : ""}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            setSelectedHeaderAxisIndex(idx);
                            setHeaderAxisAddMode(false);
                          }}
                        >
                          {idx + 1}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            ) : (
              <div className="pdf-placeholder">{headerAxisLoading ? "ヘッダー確認画像を取得中..." : "ヘッダー確認画像がありません。"}</div>
            )}
          </div>
          <p className="subtle">
            保存すると現在の注文だけにヘッダーX軸補正を記録し、その補正を使って箱館OCRを再実行します。
          </p>
        </section>
        ) : null}

        {visibleStep === 2 ? (
        <section className="panel">
          <p className="step-tag">Step2</p>
          <header className="panel-header">
            <div>
              <h2>正解 OCR を一つ選ぶ</h2>
              <p className="subtle">選択変更または削除時は、派生 sheet / bagging / output / confirmed snapshot を無効化します。</p>
            </div>
            <div className="row-actions">
              <button className="btn ghost" type="button" onClick={() => setVisibleStep(1.5)} disabled={Boolean(busy || !canReviewQuad)}>
                4点からOCR再実行
              </button>
              <button className="btn ghost" type="button" onClick={() => setVisibleStep(1.6)} disabled={Boolean(busy)}>
                ヘッダーを修正
              </button>
            </div>
          </header>
          <div className="ocr-result-list">
            {ocrResults.length ? (
              ocrResults.map((item) => (
                <div key={item.ocr_result_id} className={`ocr-card ${item === selectedOcr ? "selected" : ""}`}>
                  <div className="ocr-card-body">
                    <div>
                      <strong>{item.ocr_result_id}</strong>
                      <p className="subtle">
                        {item.status || "unknown"} / {item.source || "-"} / {formatDateTime(item.created_at)}
                      </p>
                      <p className="digest">{item.artifact_digest || ""}</p>
                    </div>
                    <div className="ocr-overlay-preview-card">
                      <div className="preview-header">
                        <div>
                          <span className="field-label">{ocrPreviewMode === "original" ? "原本PDF" : "OCR Overlay"}</span>
                          <p className="subtle">
                            {ocrPreviewMode === "original"
                              ? "この注文の原本FAX PDFです。"
                              : "このOCR結果に紐づくoverlay成果物です。"}
                          </p>
                        </div>
                        <div className="preview-header-actions">
                          <div className="preview-mode-toggle" aria-label="OCR preview display mode">
                            <button
                              className={ocrPreviewMode !== "original" ? "active" : ""}
                              type="button"
                              onClick={() => setOcrPreviewMode("overlay")}
                            >
                              オーバーレイ
                            </button>
                            <button
                              className={ocrPreviewMode === "original" ? "active" : ""}
                              type="button"
                              onClick={() => setOcrPreviewMode("original")}
                            >
                              原本PDF
                            </button>
                          </div>
                          {ocrPreviewMode !== "original" && item.overlay_url ? (
                            <a className="ghost-link" href={item.overlay_url} target="_blank" rel="noreferrer">
                              別タブで開く
                            </a>
                          ) : null}
                          {ocrPreviewMode === "original" && pdfUrl ? (
                            <a className="ghost-link" href={pdfUrl} target="_blank" rel="noreferrer">
                              別タブで開く
                            </a>
                          ) : null}
                        </div>
                      </div>
                      {ocrPreviewMode !== "original" && item.overlay_url ? (
                        <img className="ocr-overlay-preview-image" src={item.overlay_url} alt={`${item.ocr_result_id} overlay`} />
                      ) : ocrPreviewMode === "original" && pdfUrl ? (
                        <iframe title={`${item.ocr_result_id}-original-pdf`} src={pdfUrl} className="ocr-overlay-preview-pdf" />
                      ) : (
                        <div className="preview-placeholder">
                          {ocrPreviewMode === "overlay"
                            ? item.overlay_message || "overlay成果物がありません。"
                            : pdfError || "原本PDFを読み込み中..."}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="row-actions">
                    <button className="btn" type="button" onClick={() => selectOcr(item.ocr_result_id)} disabled={Boolean(busy)}>
                      正解にする
                    </button>
                    <button className="btn danger" type="button" onClick={() => deleteOcr(item.ocr_result_id)} disabled={Boolean(busy)}>
                      完全削除
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <p className="subtle">OCR result はまだありません。Step1 から OCR を実行してください。</p>
            )}
          </div>
        </section>
        ) : null}

        {visibleStep === 3 ? (
        <section className="panel step3-panel">
          <p className="step-tag">Step3</p>
          <h2>選択 OCR からシート作成 / 編集 / 保存</h2>
          {!workflow?.selected_ocr_result_id ? (
            <div className="notice warning">
              正解OCRが未選択です。Step2でOCR結果を一つ選択するまで、シート生成・保存はできません。
            </div>
          ) : null}
          <div className="row-actions step3-top-actions">
            <div className="step3-top-actions-left">
              <button
                className="btn"
                type="button"
                onClick={generateSheetFromSelectedOcr}
                disabled={Boolean(busy || !workflow?.selected_ocr_result_id)}
              >
                選択OCRからシート生成
              </button>
              <button className="btn ghost" type="button" onClick={proposeSheetAutoEdit} disabled={Boolean(busy || !sheetPayload)}>
                AI自動補正を提案
              </button>
              <button className="btn ghost" type="button" onClick={applySheetAutoEditPatches} disabled={!autoEditPatches.length || Boolean(busy)}>
                AI提案を反映
              </button>
              <button className="btn ghost" type="button" onClick={runAnomalyReview} disabled={Boolean(busy || !sheetPayload)}>
                異常チェック
              </button>
              <button className="btn ghost" type="button" onClick={() => setVisibleStep(1.5)} disabled={Boolean(busy || !canReviewQuad)}>
                4点からOCR再実行
              </button>
              <button className="btn ghost" type="button" onClick={applyAnomalyCorrections} disabled={!anomalyWarnings.some((warning) => String(warning.suggested_value || "").trim()) || Boolean(busy)}>
                異常を補正
              </button>
              <button className="btn ghost" type="button" onClick={showSheetReview} disabled={Boolean(!sheetPayload || !step3PreviewImageUrl)}>
                {effectiveSheetReviewConfirmed ? "シート確認済み" : "シート確認"}
              </button>
              <button
                className="btn primary"
                type="button"
                onClick={saveSheet}
                disabled={Boolean(busy || !canSaveSheet)}
                title={!effectiveAnomalyReviewConfirmed || !effectiveSheetReviewConfirmed ? "保存前に異常チェックとシート確認を完了してください。" : undefined}
              >
                {busy === "Step3 sheet save" ? "保存中..." : "シートを保存"}
              </button>
              <span className={["pre-save-checks", effectiveAnomalyReviewConfirmed && effectiveSheetReviewConfirmed ? "ready" : ""].filter(Boolean).join(" ")}>
                異常チェック: {effectiveAnomalyReviewConfirmed ? "済" : "未"} / シート確認: {effectiveSheetReviewConfirmed ? "済" : "未"}
              </span>
            </div>
            <div className="step3-top-actions-right">
              <button
                className="btn ghost"
                type="button"
                onClick={() => setStep3LayoutMode((current) => (current === "side-by-side" ? "stacked" : "side-by-side"))}
              >
                {step3LayoutMode === "side-by-side" ? "上下表示に切替" : "左右表示に切替"}
              </button>
              <label className="toolbar-field expanded-cell-toggle">
                <span>拡大セルコピー</span>
                <select
                  value={expandedCellCopyMode}
                  onChange={(event) => void updateExpandedCellCopyMode(event.target.value as ExpandedCellCopyMode)}
                  disabled={Boolean(busy || expandedCellCopySaving)}
                >
                  <option value="auto">自動</option>
                  <option value="enabled">ON</option>
                  <option value="disabled">OFF</option>
                </select>
              </label>
            </div>
          </div>
          <p className="subtle">
            拡大セルコピーは、merged cell施設で同じ食区分内へ数量を反映する設定です。変更後は選択OCRからシートを再生成してください。
          </p>
          {sheetPayload ? (
            <div className={`step3-workspace ${step3LayoutMode}`}>
              <div className="step3-overlay-pane">
                <div className="preview-header">
                  <div>
                    <span className="field-label">
                      {ocrPreviewMode === "sheet" ? "シート確認" : ocrPreviewMode === "overlay" ? "OCR Overlay" : "原本PDF"}
                    </span>
                    <p className="subtle">
                      {ocrPreviewMode === "sheet"
                        ? "現在のシート値を対象セル右上に重ねて確認します。"
                        : ocrPreviewMode === "overlay"
                          ? "現在セルに対応する行・列をoverlay上に表示します。"
                          : "原本FAX PDFを確認します。カーソル表示はoverlay/シート確認時だけ有効です。"}
                    </p>
                  </div>
                  <div className="preview-header-actions">
                    <div className="preview-mode-toggle" aria-label="Step3 preview display mode">
                      <button
                        className={ocrPreviewMode === "overlay" ? "active" : ""}
                        type="button"
                        onClick={() => setOcrPreviewMode("overlay")}
                      >
                        オーバーレイ
                      </button>
                      <button
                        className={ocrPreviewMode === "original" ? "active" : ""}
                        type="button"
                        onClick={() => setOcrPreviewMode("original")}
                      >
                        原本PDF
                      </button>
                      <button
                        className={ocrPreviewMode === "sheet" ? "active" : ""}
                        type="button"
                        onClick={() => setOcrPreviewMode("sheet")}
                      >
                        シート確認
                      </button>
                    </div>
                    {ocrPreviewMode !== "original" && step3PreviewImageUrl ? (
                      <a className="ghost-link" href={step3PreviewImageUrl} target="_blank" rel="noreferrer">
                        別タブで開く
                      </a>
                    ) : null}
                    {step3PreviewPdfUrl ? (
                      <a className="ghost-link" href={step3PreviewPdfUrl} target="_blank" rel="noreferrer">
                        別タブで開く
                      </a>
                    ) : null}
                  </div>
                </div>
                <div className="step3-overlay-canvas">
                  {step3PreviewPdfUrl ? (
                    <iframe title="workflow-v2-step3-original-pdf" src={step3PreviewPdfUrl} className="step3-overlay-pdf" />
                  ) : ocrPreviewMode !== "original" && step3PreviewImageUrl ? (
                    <>
                      <img
                        ref={overlayImageRef}
                        className="step3-overlay-image"
                        src={step3PreviewImageUrl}
                        alt={`${selectedOcr?.ocr_result_id || "selected"} ${ocrPreviewMode}`}
                        onLoad={(event) => {
                          const image = event.currentTarget;
                          setOverlayImageSize({
                            naturalWidth: image.naturalWidth,
                            naturalHeight: image.naturalHeight,
                            width: image.clientWidth,
                            height: image.clientHeight,
                          });
                        }}
                      />
                      {focusedSheetCell && (focusedRowBox || focusedColumnBox || focusedTargetBox) ? (
                        <>
                          {focusedRowBox ? (
                            <span
                              className="overlay-row-highlight"
                              style={{
                                left: `${focusedRowBox.left}px`,
                                top: `${focusedRowBox.top}px`,
                                width: `${focusedRowBox.width}px`,
                                height: `${focusedRowBox.height}px`,
                              }}
                            />
                          ) : null}
                          {focusedColumnBox ? (
                            <span
                              className="overlay-col-highlight"
                              style={{
                                left: `${focusedColumnBox.left}px`,
                                top: `${focusedColumnBox.top}px`,
                                width: `${focusedColumnBox.width}px`,
                                height: `${focusedColumnBox.height}px`,
                              }}
                            />
                          ) : null}
                          {focusedTargetBox ? (
                            <span
                              className="overlay-cell-highlight"
                              style={{
                                left: `${focusedTargetBox.left}px`,
                                top: `${focusedTargetBox.top}px`,
                                width: `${focusedTargetBox.width}px`,
                                height: `${focusedTargetBox.height}px`,
                              }}
                            />
                          ) : null}
                        </>
                      ) : null}
                      {focusedSheetCell ? (
                        <span className={`overlay-cursor-caption ${focusedTargetBox ? "ready" : "missing"}`}>
                          現在セル: R{focusedSheetCell.rowIndex + 1} C{focusedSheetCell.colIndex + 1}
                          {focusedTargetBox ? " / overlay対応あり" : " / overlay対応なし"}
                        </span>
                      ) : null}
                      {canRenderSheetReviewValues ? (
                        <div className="sheet-review-overlay" aria-label="sheet values overlay">
                          {sheetReviewItems.map((entry) => (
                            <span
                              key={`sheet-review-${entry.rowIndex}-${entry.colIndex}`}
                              className="sheet-review-value"
                              style={{
                                left: `${entry.box.left + entry.box.width + 6}px`,
                                top: `${entry.box.top + 3}px`,
                              }}
                              title={`R${entry.rowIndex + 1} C${entry.colIndex + 1}: ${entry.value}`}
                            >
                              {entry.value}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div className="preview-placeholder">
                      {ocrPreviewMode === "overlay"
                        ? selectedOcr?.overlay_message || "overlay成果物がありません。"
                        : ocrPreviewMode === "sheet"
                          ? pdfError || selectedOcr?.overlay_message || "シート確認に使える画像がありません。"
                        : pdfError || "原本PDFを読み込み中..."}
                    </div>
                  )}
                </div>
              </div>
              <div className="step3-sheet-pane">
                <div className="sheet-toolbar">
                  <div className="toolbar-row">
                    <button className="btn ghost" type="button" onClick={applyVisibleOcrSuggestions} disabled={!ocrOverlayItemMap.size || Boolean(busy)}>
                      表示中提案を採用
                    </button>
                    <label className="toolbar-field">
                      <span>OCR信頼度表示</span>
                      <select value={ocrConfidenceDisplayMode} onChange={(event) => setOcrConfidenceDisplayMode(event.target.value as ConfidenceDisplayMode)}>
                        <option value="strict">厳格表示</option>
                        <option value="assisted">補助表示</option>
                        <option value="suggestion">提案表示</option>
                      </select>
                    </label>
                  </div>
                  <div className="toolbar-row">
                    <label className="toolbar-field">
                      <span>入替元数量列</span>
                      <select value={swapLeftColumn} onChange={(event) => setSwapLeftColumn(event.target.value)}>
                        <option value="">数量列</option>
                        {quantityColumnOptions.map((option) => (
                          <option key={`swap-left-${option.idx}`} value={option.idx}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="toolbar-field">
                      <span>入替先数量列</span>
                      <select value={swapRightColumn} onChange={(event) => setSwapRightColumn(event.target.value)}>
                        <option value="">数量列</option>
                        {quantityColumnOptions.map((option) => (
                          <option key={`swap-right-${option.idx}`} value={option.idx}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                    <button className="btn ghost" type="button" onClick={swapQuantityColumns} disabled={!swapLeftColumn || !swapRightColumn || Boolean(busy)}>
                      数量列を入替
                    </button>
                    <label className="toolbar-field">
                      <span>数量列一括入力</span>
                      <select value={columnFillTarget} onChange={(event) => setColumnFillTarget(event.target.value)}>
                        <option value="">数量列</option>
                        {quantityColumnOptions.map((option) => (
                          <option key={`fill-${option.idx}`} value={option.idx}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                    <input className="compact-input" value={columnFillValue} onChange={(event) => setColumnFillValue(event.target.value)} placeholder="数字" />
                    <button className="btn ghost" type="button" onClick={fillQuantityColumn} disabled={!columnFillTarget || !columnFillValue.trim() || Boolean(busy)}>
                      列全体へ入力
                    </button>
                  </div>
                  <p className="subtle">
                    raw {Number(sheetPayload.ocr_numeric_cell_summary?.raw_ocr_numeric_count || 0)} / accepted {Number(sheetPayload.ocr_numeric_cell_summary?.accepted_count || 0)} / deterministic {Number(sheetPayload.ocr_numeric_cell_summary?.deterministic_candidate_count || 0)} / weak {Number(sheetPayload.ocr_numeric_cell_summary?.weak_candidate_count || 0)}
                  </p>
                  {sheetAutoEditResult ? (
                    <div className="llm-review-panel">
                      <div className="llm-review-header">
                        <strong>AI自動編集候補</strong>
                        <span>{sheetAutoEditResult.patches?.length || 0}件 / AI: {formatAiStatus(sheetAutoEditResult.llm?.status || sheetAutoEditResult.job?.status)}</span>
                      </div>
                      <p className="subtle">
                        原本FAX画像と現在のシートだけを照合し、100%一致と断定できない数量セルに候補を提案します。
                        {sheetAutoEditResult.llm?.fax_image && typeof sheetAutoEditResult.llm.fax_image === "object"
                          ? ` FAX画像: ${formatAiStatus((sheetAutoEditResult.llm.fax_image as Record<string, unknown>).status)}`
                          : ""}
                      </p>
                      {sheetAutoEditResult.patches?.length ? (
                        <div className="table-wrap anomaly-table-wrap step3-anomaly-table-wrap">
                          <table className="anomaly-table auto-edit-table">
                            <thead>
                              <tr>
                                <th>操作</th>
                                <th>行</th>
                                <th>列</th>
                                <th>現状</th>
                                <th>候補</th>
                                <th>理由</th>
                              </tr>
                            </thead>
                            <tbody>
                              {sheetAutoEditResult.patches.slice(0, 40).map((patch, idx) => (
                                <tr
                                  key={`auto-edit-${idx}`}
                                  className={[
                                    "auto-edit-candidate-row",
                                    selectedAutoEditIndex === idx ? "selected-auto-edit-row" : "",
                                  ].filter(Boolean).join(" ")}
                                  onClick={() => selectAutoEditPatch(patch, idx)}
                                >
                                  <td className="anomaly-row-actions-cell">
                                    <div className="inline-row-actions">
                                      <button
                                        className="btn tiny row-apply-button"
                                        type="button"
                                        disabled={!String(patch.suggested_value || "").trim()}
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          applySingleSheetAutoEditPatch(patch);
                                        }}
                                      >
                                        採用
                                      </button>
                                      <button
                                        className="btn tiny ghost"
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          dismissSingleSheetAutoEditPatch(patch);
                                        }}
                                      >
                                        却下
                                      </button>
                                    </div>
                                  </td>
                                  <td>R{patch.row_index + 1}</td>
                                  <td>{patch.label || patch.field || `C${patch.col_index + 1}`}</td>
                                  <td>{patch.current_value || "空"}</td>
                                  <td><strong>{patch.suggested_value}</strong></td>
                                  <td>{patch.reason || patch.evidence || ""}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : sheetAutoEditResult.llm?.status === "failed" || sheetAutoEditResult.llm?.status === "partial_failed" ? (
                        <p className="subtle error">
                          AI自動編集の解析に失敗しました。
                          {typeof sheetAutoEditResult.llm?.error === "string" && sheetAutoEditResult.llm.error
                            ? ` ${sheetAutoEditResult.llm.error}`
                            : " 候補なしとは判定していません。"}
                        </p>
                      ) : sheetAutoEditResult.job?.status === "running" || sheetAutoEditResult.llm?.status === "running" ? (
                        <p className="subtle">AI自動編集を実行中です。完了したら候補を表示します。</p>
                      ) : (
                        <p className="subtle">修正候補はありません。</p>
                      )}
                    </div>
                  ) : null}
                  {anomalyReview ? (
                    <div className={["anomaly-review-panel", anomalyWarnings.length ? "" : "ok"].filter(Boolean).join(" ")}>
                      <div className="llm-review-header">
                        <strong>数量異常チェック</strong>
                        <span>{anomalyWarnings.length}件 / AI: {formatAiStatus(anomalyReview?.llm && typeof anomalyReview.llm === "object" ? (anomalyReview.llm as Record<string, unknown>).status : null)}</span>
                      </div>
                      <p className="subtle">
                        保存前のシート内数値だけを使って異常候補を出します。行を選ぶと、右のシートと左のOCR overlayで該当セルを表示します。
                      </p>
                      {anomalyWarnings.length ? (
                        <div className="table-wrap anomaly-table-wrap step3-anomaly-table-wrap">
                          <table className="anomaly-table">
                            <thead>
                              <tr>
                                <th>操作</th>
                                <th>重要度</th>
                                <th>日付</th>
                                <th>食区分</th>
                                <th>メニュー</th>
                                <th>行</th>
                                <th>列</th>
                                <th>値</th>
                                <th>補正案</th>
                                <th>基準</th>
                                <th>内容</th>
                              </tr>
                            </thead>
                            <tbody>
                              {anomalyWarnings.slice(0, 80).map((warning, idx) => (
                                <tr
                                  key={`step3-anomaly-${idx}`}
                                  className={[
                                    `anomaly-${warning.severity || "medium"}`,
                                    selectedAnomalyIndex === idx ? "selected-anomaly-row" : "",
                                  ].filter(Boolean).join(" ")}
                                  onClick={() => selectAnomalyWarning(warning, idx)}
                                >
                                  <td className="anomaly-row-actions-cell">
                                    <div className="inline-row-actions">
                                      <button
                                        className="btn tiny row-apply-button"
                                        type="button"
                                        disabled={!String(warning.suggested_value || "").trim()}
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          applySingleAnomalyCorrection(warning);
                                        }}
                                      >
                                        反映
                                      </button>
                                      <button
                                        className="btn tiny ghost"
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          dismissSingleAnomalyWarning(warning);
                                        }}
                                      >
                                        却下
                                      </button>
                                    </div>
                                  </td>
                                  <td>{formatAnomalySeverity(warning.severity)}</td>
                                  <td>{anomalyContextValue(warning, "date") || "-"}</td>
                                  <td>{anomalyContextValue(warning, "daypart") || "-"}</td>
                                  <td className="anomaly-menu-cell">{anomalyContextValue(warning, "menu") || "-"}</td>
                                  <td>{typeof warning.row_index === "number" ? warning.row_index + 1 : "-"}</td>
                                  <td>{warning.label || warning.field || (typeof warning.col_index === "number" ? `C${warning.col_index + 1}` : "-")}</td>
                                  <td>{warning.value || "-"}</td>
                                  <td>{warning.suggested_value || "-"}</td>
                                  <td>{formatAnomalyBasis(warning)}</td>
                                  <td>{warning.message || warning.type || "確認対象"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <p className="subtle">保存前シートで明確な異常候補はありません。</p>
                      )}
                    </div>
                  ) : null}
                </div>
                <div className="sheet-table-wrap">
                  <table className="sheet-table compact-sheet-table">
                    <thead>
                      <tr>
                        <th className="sheet-row-index-col">#</th>
                        {sheetPayload.header.map((label, colIdx) => (
                          <th
                            key={`${sheetPayload.fields[colIdx] || "col"}-${colIdx}`}
                            className={[
                              sheetWidthClass(sheetPayload.fields[colIdx]),
                              isLockedSheetField(sheetPayload.fields[colIdx]) ? "sticky-structural-col" : "",
                            ].filter(Boolean).join(" ")}
                            style={isLockedSheetField(sheetPayload.fields[colIdx]) ? { left: `${stickyLeftForSheetField(sheetPayload.fields[colIdx], colIdx)}px` } : undefined}
                          >
                            {label || sheetPayload.fields[colIdx]}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sheetPayload.rows.map((row, rowIdx) => (
                        <tr className={sheetRowClassName(sheetPayload, rowIdx)} key={sheetPayload.row_ids?.[rowIdx] || `row-${rowIdx}`}>
                          <th className="sheet-row-index-col">{rowIdx + 1}</th>
                          {sheetPayload.fields.map((field, colIdx) => {
                            const confidenceTier = String(sheetPayload.cell_confidence_rows?.[rowIdx]?.[colIdx] || "").trim();
                            const belowThreshold = confidenceTier && !confidenceTierVisible(confidenceTier, ocrConfidenceDisplayMode);
                            const overlayItem = !String(row[colIdx] || "").trim() ? ocrOverlayItemMap.get(`${rowIdx}:${colIdx}`) : null;
                            const overlayValue = String(overlayItem?.value || "").trim();
                            const autoEditCell = autoEditCellMap.get(`${rowIdx}:${colIdx}`);
                            const autoEditSelected = autoEditCell && selectedAutoEditIndex === autoEditCell.index;
                            const autoEditCurrent = autoEditCell ? String(autoEditCell.patch.current_value ?? row[colIdx] ?? "").trim() : "";
                            const autoEditSuggested = autoEditCell ? String(autoEditCell.patch.suggested_value || "").trim() : "";
                            const anomalyCell = anomalyCellMap.get(`${rowIdx}:${colIdx}`);
                            const anomalySeverity = anomalyCell ? String(anomalyCell.warning.severity || "medium").trim() || "medium" : "";
                            const anomalySelected = anomalyCell && selectedAnomalyIndex === anomalyCell.index;
                            const anomalyCurrent = anomalyCell ? String(anomalyCell.warning.value ?? row[colIdx] ?? "").trim() : "";
                            const anomalySuggested = anomalyCell ? String(anomalyCell.warning.suggested_value || "").trim() : "";
                            return (
                              <td
                                key={`${field}-${colIdx}`}
                                className={[
                                  isLockedSheetField(field) ? "sticky-structural-col" : "",
                                  sheetWidthClass(field),
                                  confidenceTier ? `confidence-${confidenceTier}` : "",
                                  belowThreshold ? "below-confidence-threshold" : "",
                                  overlayValue ? "has-overlay-suggestion" : "",
                                  autoEditCell ? "sheet-auto-edit-candidate" : "",
                                  autoEditSelected ? "sheet-auto-edit-selected" : "",
                                  anomalySeverity ? `sheet-anomaly-${anomalySeverity}` : "",
                                  anomalySelected ? "sheet-anomaly-selected" : "",
                                ].filter(Boolean).join(" ")}
                                style={isLockedSheetField(field) ? { left: `${stickyLeftForSheetField(field, colIdx)}px` } : undefined}
                              >
                                <div className="sheet-input-wrap">
                                  {overlayValue ? <span className="sheet-overlay-suggestion">{overlayValue}</span> : null}
                                  {autoEditSuggested ? (
                                    <span className="sheet-cell-candidate sheet-cell-candidate-ai">
                                      {(autoEditCurrent || "空")}→{autoEditSuggested}
                                    </span>
                                  ) : null}
                                  {anomalySuggested ? (
                                    <span className="sheet-cell-candidate sheet-cell-candidate-anomaly">
                                      {(anomalyCurrent || "空")}→{anomalySuggested}
                                    </span>
                                  ) : null}
                                  <input
                                    key={`${sheetPayload.row_ids?.[rowIdx] || rowIdx}:${field}:${colIdx}:${row[colIdx] || ""}`}
                                    data-sheet-row={rowIdx}
                                    data-sheet-col={colIdx}
                                    defaultValue={row[colIdx] || ""}
                                    readOnly={isLockedSheetField(field)}
                                    onFocus={() => setFocusedSheetCell({ rowIndex: rowIdx, colIndex: colIdx })}
                                    onBlur={() => {
                                      flushPendingSheetCellEdits();
                                    }}
                                    onKeyDown={(event) => handleSheetInputKeyDown(event, rowIdx, colIdx)}
                                    onChange={(event) => updateSheetCell(rowIdx, colIdx, event.target.value)}
                                  />
                                </div>
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          ) : (
            <p className="subtle">Step2で正解OCRを選択してから、選択OCRだけを使ってシートを生成してください。</p>
          )}
          <details
            className="json-details"
            onToggle={(event) => {
              if (event.currentTarget.open && sheetJsonStale) {
                refreshSheetJsonForDetails();
              }
            }}
          >
            <summary>保存予定JSONを確認</summary>
            <textarea
              value={sheetJson}
              onChange={(event) => {
                const nextJson = event.target.value;
                setSheetJson(nextJson);
                setSheetJsonStale(false);
                try {
                  setSheetPayload(normalizeSheetPayload(JSON.parse(nextJson)));
                } catch {
                  setSheetPayload(null);
                }
              }}
              spellCheck={false}
            />
          </details>
        </section>
        ) : null}

        {visibleStep === 4 ? (
        <section className="panel">
          <p className="step-tag">Step4</p>
          <header className="panel-header">
            <div>
              <h2>出力確認</h2>
              <p className="subtle">袋分け、ラベル、納品書、総量をまとめて確認して、問題なければ注文を確定します。</p>
            </div>
            <div className="row-actions">
              <button className="btn primary" type="button" onClick={runBagging} disabled={Boolean(busy || !workflow?.saved_sheet_id)}>
                出力確認を作成
              </button>
              <button className="btn primary" type="button" onClick={finalConfirm} disabled={Boolean(busy || !workflow?.output_bundle_id)}>
                確定して一覧にもどる
              </button>
            </div>
          </header>
          {inspection?.bagging_result ? (
            <div className="result-summary">
              <h3>袋分け結果</h3>
              <div className="summary-grid summary-grid--compact">
                <div className="summary-primary-card">
                  <span className="field-label">対象行</span>
                  <p className="summary-value">{Number((inspection.bagging_result.summary as any)?.line_count || 0)}件</p>
                </div>
                <div className="summary-primary-card">
                  <span className="field-label">数量行</span>
                  <p className="summary-value">{Number((inspection.bagging_result.summary as any)?.quantity_line_count || 0)}件</p>
                </div>
                <div className="summary-primary-card">
                  <span className="field-label">合計数量</span>
                  <p className="summary-value">{formatWorkflowQuantity((inspection.bagging_result.summary as any)?.total_quantity)}</p>
                </div>
                <div className="summary-primary-card">
                  <span className="field-label">袋数</span>
                  <p className="summary-value">{bagRows.length}袋</p>
                </div>
              </div>
              {bagSummaryGroups.length ? (
                <div className="wrap-grid workflow-bag-groups">
                  <p className="bag-summary-note subtle">
                    同じ日付の数量セルをまとめて表示します。区分・メニュー・食種・エリアを確認してから確定してください。
                  </p>
                  {bagSummaryGroups.map((group) => (
                    <div key={`workflow-bag-${group.date}`} className="date-group">
                      <div className="date-group-header">
                        <span className="date-group-title">{group.date}</span>
                        <span className="group-count">{group.rows.length}件</span>
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
                            </tr>
                          </thead>
                          <tbody>
                            {group.rows.map((bag) => (
                              <tr key={`${group.date}-${bag.id}`}>
                                <td>{bag.daypart || "-"}</td>
                                <td>{bag.menu_name || "-"}</td>
                                <td>{normalizeDietLabel(bag.diet_type)}</td>
                                <td>{bag.area_id || "-"}</td>
                                <td>{formatWorkflowBagType(bag.bag_type)}</td>
                                <td className="bag-total-qty">{formatWorkflowQuantity(bag.total_quantity)}</td>
                                <td className="bag-calc-result-cell">
                                  <span className={`bag-count-badge${bag.bag_count > 1 ? " split" : ""}`}>
                                    {bag.bag_count}袋
                                  </span>
                                  {bag.bag_count > 1 ? (
                                    <span className="bag-calc-breakdown">{bag.breakdowns.join(" + ")}</span>
                                  ) : null}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="subtle">数量行がありません。</p>
              )}
            </div>
          ) : (
            <div className="empty-result-panel">
              <p className="subtle">出力確認はまだ作成されていません。保存済みシートから出力確認を作成してください。</p>
            </div>
          )}
          {inspection?.output_bundle ? (
            <div className="result-summary">
              <h3>出力</h3>
              <div className="summary-grid summary-grid--compact">
                <div className="summary-primary-card">
                  <span className="field-label">出力状態</span>
                  <p className="summary-value">{String(inspection.output_bundle.status || "-")}</p>
                </div>
                <div className="summary-primary-card">
                  <span className="field-label">出力ID</span>
                  <p className="summary-value">{String(inspection.output_bundle.output_bundle_id || "-")}</p>
                </div>
                <div className="summary-primary-card">
                  <span className="field-label">作成日時</span>
                  <p className="summary-value">{formatDateTime(String(inspection.output_bundle.created_at || ""))}</p>
                </div>
              </div>
              <div className="outputs">
                <div className="output-card">
                  <span className="output-link">ラベルCSV</span>
                  <button className="btn primary" type="button" onClick={() => openOutput(`/outputs/labels?order_id=${orderId}`, "ラベルCSV")}>
                    ダウンロード
                  </button>
                  <button className="btn ghost" type="button" onClick={() => loadOutputPreview("labels")} disabled={outputPreviewLoading}>
                    プレビュー
                  </button>
                </div>
                <div className="output-card">
                  <span className="output-link">納品書HTML</span>
                  <button className="btn primary" type="button" onClick={() => openHtmlOutput(`/outputs/delivery-notes/html?order_id=${orderId}`, "納品書HTML")}>
                    開く
                  </button>
                  <button className="btn ghost" type="button" onClick={() => loadOutputPreview("delivery")} disabled={outputPreviewLoading}>
                    プレビュー
                  </button>
                </div>
                <div className="output-card">
                  <span className="output-link">FAX読取シートExcel</span>
                  <button className="btn primary" type="button" onClick={() => openOutput(`/outputs/order-form-saved-sheet?order_id=${orderId}`, "FAX読取シートExcel")}>
                    ダウンロード
                  </button>
                  <button className="btn ghost" type="button" onClick={() => loadOutputPreview("order_form_saved_sheet")} disabled={outputPreviewLoading}>
                    プレビュー
                  </button>
                </div>
                <div className="output-card">
                  <span className="output-link">総量CSV</span>
                  <button className="btn primary" type="button" onClick={() => openOutput(`/outputs/manufacturing-aggregate?order_id=${orderId}`, "総量CSV")}>
                    ダウンロード
                  </button>
                  <button className="btn ghost" type="button" onClick={() => loadOutputPreview("aggregate")} disabled={outputPreviewLoading}>
                    プレビュー
                  </button>
                </div>
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
                                {formatOutputPreviewCell(cell, outputPreview.headers[idx])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              ) : null}
            </div>
          ) : (
            <div className="empty-result-panel">
              <p className="subtle">出力確認はまだ作成されていません。保存済みシートから出力確認を作成してください。</p>
            </div>
          )}
        </section>
        ) : null}
      </section>

      <section className="panel">
        <p className="step-tag">Read Only Inspection</p>
        <h2>状態と lineage</h2>
        <p className="subtle">現在の workflow state と、この注文に紐づく成果物 ID だけを表示します。詳細確認は確認専用ページを使います。</p>
        <div className="summary-grid summary-grid--compact lineage-summary">
          <div className="summary-primary-card">
            <span>現在状態</span>
            <p className="summary-value">{stateLabel(workflow?.state)}</p>
          </div>
          <div className="summary-primary-card">
            <span>正解OCR</span>
            <p className="summary-value">{workflow?.selected_ocr_result_id || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span>保存シート</span>
            <p className="summary-value">{workflow?.saved_sheet_id || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span>袋分け</span>
            <p className="summary-value">{workflow?.bagging_result_id || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span>出力</span>
            <p className="summary-value">{workflow?.output_bundle_id || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span>確定snapshot</span>
            <p className="summary-value">{workflow?.confirmed_snapshot_id || "-"}</p>
          </div>
        </div>
        {orderId ? (
          <Link className="ghost-link lineage-link" href={`/orders/${orderId}/inspection-v2`}>
            確認専用ページで見る
          </Link>
        ) : null}
        <details className="json-details">
          <summary>デバッグ用 raw JSON を開く</summary>
          <pre>{formatJson(inspection || workflow)}</pre>
        </details>
      </section>

      <style jsx>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Noto+Sans+JP:wght@400;600;700;800&display=swap");

        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }

        :global(*) {
          box-sizing: border-box;
        }

        .workflow-v2-page {
          background:
            radial-gradient(circle at top left, rgba(62, 110, 89, 0.14), transparent 30%),
            linear-gradient(180deg, #faf8f1 0%, #f1efe6 100%);
          min-height: 100vh;
        }
        .hero {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 24px;
          padding: 32px 36px 18px;
        }
        .eyebrow {
          color: #6f6a5a;
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.08em;
          margin: 0 0 8px;
          text-transform: uppercase;
        }
        h1,
        h2 {
          color: #1c2822;
          margin: 0;
        }
        .subtle {
          color: #687269;
          margin: 8px 0 0;
        }
        .panel {
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 18px;
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          margin: 16px 36px;
          padding: 22px;
        }
        .panel-header {
          align-items: center;
          display: flex;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 12px;
        }
        .panel-header > div {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .state-panel {
          align-items: start;
          display: grid;
          gap: 12px;
          grid-template-columns: minmax(220px, 0.9fr) minmax(420px, 2.8fr) auto;
          padding: 16px 22px;
        }
        .state-panel-main h2 {
          font-size: 22px;
          line-height: 1.15;
        }
        .ocr-progress-inline {
          background: #edf3ef;
          border: 1px solid rgba(53, 80, 71, 0.16);
          border-radius: 999px;
          color: #355047;
          display: inline-flex;
          font-size: 13px;
          font-weight: 800;
          margin: 10px 0 0;
          padding: 6px 10px;
        }
        .state-actions,
        .row-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .step3-top-actions {
          align-items: flex-end;
          justify-content: space-between;
        }
        .step3-top-actions-left,
        .step3-top-actions-right {
          align-items: flex-end;
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .step3-top-actions-right {
          justify-content: flex-end;
          margin-left: auto;
        }
        .state-actions {
          justify-content: flex-end;
        }
        .step-page {
          display: block;
        }
        .step-nav {
          display: flex;
          gap: 10px;
          margin: 16px 36px;
          overflow-x: auto;
        }
        .step-nav-btn {
          background: rgba(255, 255, 255, 0.74);
          border: 1px solid rgba(54, 82, 68, 0.14);
          border-radius: 16px;
          color: #526258;
          cursor: pointer;
          display: grid;
          gap: 4px;
          flex: 1 1 0;
          min-height: 68px;
          min-width: 0;
          padding: 12px 14px;
          text-align: left;
        }
        .step-nav-btn span {
          color: #a15f2d;
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }
        .step-nav-btn strong {
          color: #1c2822;
          font-size: 14px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .step-nav-btn.active {
          background: #1c2822;
          border-color: #1c2822;
          box-shadow: 0 12px 28px rgba(28, 40, 34, 0.16);
        }
        .step-nav-btn.active span,
        .step-nav-btn.active strong {
          color: #fffdf7;
        }
        .step-tag {
          color: #a15f2d;
          font-size: 12px;
          font-weight: 800;
          margin: 0 0 8px;
        }
        .form-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          margin: 18px 0;
        }
        .summary-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
        }
        .summary-grid--compact {
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          align-items: stretch;
        }
        .order-info-panel {
          padding: 14px 22px;
        }
        .order-info-panel .panel-header {
          margin-bottom: 8px;
        }
        .order-info-panel h2 {
          font-size: 22px;
          line-height: 1.15;
        }
        .order-info-panel .step-tag {
          margin-bottom: 2px;
        }
        .order-info-panel .subtle {
          font-size: 13px;
          margin-top: 2px;
        }
        .summary-grid--order-info {
          gap: 8px;
          grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
        }
        .summary-primary-card {
          background: #f8fbfa;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 12px;
          padding: 10px 12px;
        }
        .summary-grid--order-info .summary-primary-card {
          min-height: 62px;
          padding: 8px 10px;
        }
        .summary-grid--order-info .field-label {
          font-size: 11px;
          letter-spacing: 0.04em;
        }
        .summary-value {
          font-weight: 700;
          margin: 4px 0 0;
        }
        .summary-grid--order-info .summary-value {
          font-size: 16px;
          line-height: 1.15;
          margin-top: 2px;
        }
        .summary-subline {
          color: #687269;
          font-size: 12px;
          font-weight: 700;
          margin: 4px 0 0;
        }
        .summary-grid--order-info .summary-subline {
          font-size: 11px;
          line-height: 1.2;
          margin-top: 2px;
        }
        .wrap-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr));
          align-items: start;
          margin-top: 16px;
        }
        .bag-summary-note {
          grid-column: 1 / -1;
          margin: 0;
        }
        .date-group {
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 14px;
          padding: 12px;
        }
        .date-group-header {
          align-items: center;
          color: #354341;
          display: flex;
          font-size: 13px;
          font-weight: 700;
          gap: 8px;
          margin-bottom: 10px;
        }
        .date-group-title {
          white-space: nowrap;
        }
        .group-count {
          background: #edf3ef;
          border-radius: 999px;
          color: #355047;
          font-size: 12px;
          font-weight: 800;
          padding: 3px 9px;
        }
        .date-group .table-wrap,
        .output-preview .table-wrap {
          max-height: 360px;
          overflow: auto;
        }
        .date-group table,
        .output-preview table {
          border-collapse: collapse;
          min-width: 620px;
          width: 100%;
        }
        .date-group th,
        .date-group td,
        .output-preview th,
        .output-preview td {
          border-bottom: 1px solid #e6dfcf;
          font-size: 12px;
          padding: 7px 8px;
          text-align: left;
          white-space: nowrap;
        }
        .date-group th,
        .output-preview th {
          background: #f7f4eb;
          color: #344238;
          font-weight: 800;
          position: sticky;
          top: 0;
          z-index: 1;
        }
        .bag-total-qty {
          color: #1f2a2a;
          font-weight: 800;
        }
        .bag-count-badge {
          align-items: center;
          background: #edf3ef;
          border-radius: 999px;
          color: #355047;
          display: inline-flex;
          font-size: 12px;
          font-weight: 800;
          justify-content: center;
          min-width: 70px;
          padding: 4px 10px;
        }
        .bag-count-badge.split {
          background: #efe6d6;
          color: #7d4a18;
        }
        .bag-calc-breakdown {
          color: #566663;
          display: inline-block;
          font-size: 12px;
          margin-left: 8px;
        }
        .bag-calc-result-cell {
          min-width: 112px;
        }
        .outputs {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          margin-top: 16px;
        }
        .output-card {
          align-items: center;
          background: rgba(255, 255, 255, 0.9);
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 14px;
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          padding: 8px;
        }
        .output-link {
          background: #fbfbf9;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 12px;
          color: inherit;
          font-weight: 800;
          padding: 10px 16px;
        }
        .output-preview,
        .empty-result-panel {
          background: #fffdf7;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 14px;
          margin-top: 16px;
          padding: 12px;
        }
        .output-preview summary {
          color: #354341;
          cursor: pointer;
          font-size: 13px;
          font-weight: 800;
          list-style: none;
          margin-bottom: 10px;
        }
        .output-preview summary::-webkit-details-marker {
          display: none;
        }
        .summary-actions {
          align-items: flex-end;
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          margin-top: 12px;
        }
        .summary-actions.compact {
          margin-top: 0;
        }
        .summary-actions .field {
          flex: 1;
          min-width: 240px;
        }
        .step1-facility-block {
          background: #f8fbfa;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          margin: 14px 0 16px;
          padding: 12px;
        }
        .step1-current-strip {
          align-items: flex-end;
          background: #fffdf7;
          border: 1px solid #e5dece;
          border-radius: 12px;
          color: #4b5d54;
          display: flex;
          flex-wrap: wrap;
          font-size: 12px;
          font-weight: 800;
          gap: 8px;
          justify-content: space-between;
          margin-bottom: 12px;
          padding: 8px 10px;
        }
        .step1-current-pills {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .step1-current-pills span,
        .step1-selection-summary {
          background: #eef3ef;
          border-radius: 999px;
          padding: 4px 9px;
        }
        .step1-selection-summary {
          align-items: center;
          background: #f7f4eb;
          color: #354341;
          display: flex;
          gap: 8px;
          margin-left: auto;
        }
        .step1-selection-summary span {
          color: #687269;
          font-weight: 800;
        }
        .step1-control-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: minmax(320px, 0.9fr) minmax(360px, 1.1fr);
          align-items: start;
        }
        .step1-control-column {
          display: grid;
          gap: 12px;
        }
        .step1-week-range {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin-top: 10px;
        }
        .context-suggestion-card {
          align-items: flex-start;
          background: #fffaf0;
          border: 1px solid #ead7af;
          border-radius: 14px;
          display: flex;
          gap: 12px;
          justify-content: space-between;
          margin-top: 12px;
          padding: 12px;
        }
        .context-suggestion-card.compact {
          margin-top: 0;
          min-height: 98px;
        }
        .context-suggestion-card.muted {
          background: #f7f5ee;
          border-color: #e2ddcf;
        }
        .context-suggestion-card h3 {
          color: #1c2822;
          font-size: 16px;
          margin: 4px 0 0;
        }
        .context-suggestion-candidates {
          color: #5d665f;
          font-size: 12px;
          margin: 8px 0 0;
          padding-left: 18px;
        }
        .context-suggestion-candidates li + li {
          margin-top: 4px;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .exception-range-details {
          background: #fffdf7;
          border: 1px dashed #d7d1c0;
          border-radius: 12px;
          padding: 10px 12px;
        }
        .exception-range-details summary {
          color: #5f7b74;
          cursor: pointer;
          font-size: 12px;
          font-weight: 900;
          list-style: none;
        }
        .exception-range-details summary::-webkit-details-marker {
          display: none;
        }
        .step1-action-row {
          align-items: end;
          background: #fffdf7;
          border: 1px solid #e5dece;
          border-radius: 14px;
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          margin-top: 12px;
          padding: 12px;
        }
        .ocr-mode-field {
          min-width: 170px;
        }
        .workflow-warning {
          background: #fff8e6;
          border: 1px solid #ebd6a7;
          border-radius: 12px;
          color: #775316;
          font-size: 13px;
          font-weight: 700;
          margin: 10px 0 0;
          padding: 10px 12px;
        }
        .field-label {
          color: #5f7b74;
          font-size: 12px;
          font-weight: 800;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .warning-banner {
          background: #fff8e6;
          border: 1px solid #ebd6a7;
          border-radius: 12px;
          color: #775316;
          font-size: 13px;
          margin-top: 12px;
          padding: 10px 12px;
        }
        .warning-banner p {
          margin: 0;
        }
        .warning-banner p + p {
          margin-top: 6px;
        }
        .facility-template-resolution {
          align-items: flex-start;
          border-radius: 14px;
          display: flex;
          flex-wrap: wrap;
          gap: 14px;
          justify-content: space-between;
          margin-top: 12px;
          padding: 14px;
        }
        .facility-template-resolution.blocked {
          background: #fff1e8;
          border: 1px solid #e8b48f;
        }
        .facility-template-resolution.resolved {
          background: #eef8f1;
          border: 1px solid #b8dbc4;
        }
        .facility-template-resolution-copy {
          min-width: 260px;
          flex: 1;
        }
        .facility-template-resolution-copy strong {
          color: #1c2822;
          display: block;
          font-size: 14px;
          margin-bottom: 4px;
        }
        .facility-template-resolution-copy p {
          margin: 0;
        }
        .facility-template-register {
          display: flex;
          flex: 1;
          flex-wrap: wrap;
          gap: 10px;
          min-width: 320px;
        }
        .facility-template-register .toolbar-field {
          flex: 1;
          min-width: 260px;
        }
        .facility-template-register .subtle {
          flex-basis: 100%;
        }
        .facility-template-editor {
          background: #fffdf7;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 14px;
          margin-top: 12px;
          padding: 14px;
        }
        .facility-template-editor-header {
          align-items: flex-start;
          display: flex;
          justify-content: space-between;
          gap: 12px;
        }
        .facility-template-editor-header strong {
          color: #1c2822;
          display: block;
          font-size: 14px;
          margin-bottom: 4px;
        }
        .facility-template-editor-body {
          display: grid;
          gap: 12px;
          margin-top: 12px;
        }
        .facility-template-callout {
          background: #fff8e6;
          border: 1px solid #ebd6a7;
          border-radius: 12px;
          color: #775316;
          font-size: 13px;
          padding: 10px 12px;
        }
        .facility-template-callout p {
          margin: 0;
        }
        .facility-template-actions,
        .facility-template-row-actions {
          align-items: center;
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .facility-template-actions .input {
          min-width: 180px;
        }
        .facility-template-table-wrap {
          border: 1px solid #d7d1c0;
          border-radius: 12px;
          max-height: 420px;
          overflow: auto;
        }
        .facility-template-table {
          border-collapse: collapse;
          min-width: 920px;
          width: 100%;
        }
        .facility-template-table th,
        .facility-template-table td {
          border-bottom: 1px solid #e5dece;
          font-size: 12px;
          padding: 7px 8px;
          vertical-align: top;
        }
        .facility-template-table th {
          background: #f4eddd;
          color: #405045;
          font-weight: 800;
          position: sticky;
          text-align: left;
          top: 0;
          z-index: 1;
        }
        .facility-template-table .input {
          min-width: 120px;
          padding: 7px 8px;
        }
        .expanded-cell-toggle {
          min-width: 170px;
        }
        .pdf-preview-panel {
          border: 1px solid #d7d1c0;
          border-radius: 16px;
          margin: 18px 0;
          overflow: hidden;
          background: #fffdf7;
        }
        .pdf-preview-header {
          align-items: center;
          border-bottom: 1px solid #e5dece;
          display: flex;
          justify-content: space-between;
          gap: 16px;
          padding: 14px 16px;
        }
        .pdf-preview-header h3 {
          color: #1c2822;
          margin: 0;
        }
        .pdf-frame {
          background: #fff;
          border: 1px solid #ddd;
          border-radius: 12px;
          display: block;
          height: 520px;
          width: 100%;
        }
        .pdf-frame-wide {
          min-height: 640px;
        }
        .pdf-placeholder {
          align-items: center;
          color: #687269;
          background: #f9f7f2;
          display: flex;
          font-weight: 800;
          height: 420px;
          justify-content: center;
        }
        label {
          color: #455248;
          display: grid;
          font-size: 13px;
          font-weight: 700;
          gap: 6px;
        }
        input,
        select,
        textarea {
          background: #fffdf7;
          border: 1px solid #d7d1c0;
          border-radius: 12px;
          color: #1c2822;
          font: inherit;
          padding: 10px 12px;
        }
        textarea {
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          min-height: 260px;
          width: 100%;
        }
        .sheet-table-wrap {
          border: 1px solid #d7d1c0;
          border-radius: 14px;
          margin-top: 16px;
          max-height: 72vh;
          overflow: auto;
        }
        .step3-workspace {
          display: grid;
          gap: 16px;
          margin-top: 16px;
        }
        .step3-workspace.side-by-side {
          grid-template-columns: minmax(680px, 1.18fr) minmax(470px, 0.82fr);
        }
        .step3-workspace.stacked {
          grid-template-columns: 1fr;
        }
        .step3-overlay-pane,
        .step3-sheet-pane {
          border: 1px solid #d7d1c0;
          border-radius: 16px;
          overflow: hidden;
          background: #fffdf7;
          min-width: 0;
        }
        .step3-overlay-canvas {
          background: #fff;
          max-height: 82vh;
          overflow: auto;
          position: relative;
        }
        .step3-overlay-image {
          display: block;
          min-width: 920px;
          width: 100%;
        }
        .step3-overlay-pdf {
          background: #fff;
          border: 0;
          display: block;
          height: 82vh;
          min-height: 760px;
          min-width: 920px;
          width: 100%;
        }
        .overlay-row-highlight,
        .overlay-col-highlight,
        .overlay-cell-highlight {
          pointer-events: none;
          position: absolute;
          z-index: 3;
        }
        .overlay-row-highlight {
          background: rgba(255, 192, 64, 0.22);
          border: 2px solid rgba(222, 139, 28, 0.5);
        }
        .overlay-col-highlight {
          background: rgba(69, 142, 255, 0.18);
          border: 2px solid rgba(38, 110, 214, 0.45);
        }
        .overlay-cell-highlight {
          border: 3px solid #e6532e;
          box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.78);
        }
        .overlay-cursor-caption {
          border-radius: 999px;
          box-shadow: 0 6px 18px rgba(21, 28, 24, 0.16);
          font-size: 13px;
          font-weight: 900;
          left: 12px;
          padding: 7px 10px;
          position: sticky;
          top: 12px;
          z-index: 4;
        }
        .overlay-cursor-caption.ready {
          background: rgba(255, 247, 209, 0.94);
          color: #b53018;
        }
        .overlay-cursor-caption.missing {
          background: rgba(232, 238, 244, 0.94);
          color: #46515a;
        }
        .sheet-review-overlay {
          inset: 0;
          pointer-events: none;
          position: absolute;
          z-index: 5;
        }
        .sheet-review-value {
          color: #c42d1c;
          font-size: 16px;
          font-weight: 950;
          line-height: 1;
          padding: 0;
          position: absolute;
          text-align: center;
          transform: translateX(-100%);
          text-shadow: 0 1px 1px rgba(255, 255, 255, 0.8);
          white-space: nowrap;
        }
        .sheet-review-fallback-note {
          background: rgba(255, 247, 232, 0.92);
          border: 1px solid #ead6b0;
          border-radius: 999px;
          color: #8b5b1f;
          font-size: 12px;
          font-weight: 800;
          left: 12px;
          padding: 7px 10px;
          position: absolute;
          top: 12px;
        }
        .pre-save-checks {
          align-self: center;
          background: #fff7e8;
          border: 1px solid #ead6b0;
          border-radius: 999px;
          color: #8b5b1f;
          font-size: 12px;
          font-weight: 800;
          padding: 7px 10px;
        }
        .pre-save-checks.ready {
          background: #edf3ef;
          border-color: rgba(47, 125, 82, 0.24);
          color: #2f7d52;
        }
        .sheet-toolbar {
          border-bottom: 1px solid #e5dece;
          display: grid;
          gap: 10px;
          padding: 12px;
        }
        .toolbar-row {
          align-items: end;
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .toolbar-field {
          display: grid;
          gap: 5px;
          min-width: 150px;
        }
        .toolbar-field span {
          color: #5f7b74;
          font-size: 11px;
          font-weight: 800;
        }
        .compact-input {
          max-width: 120px;
          min-height: 38px;
          padding: 8px 10px;
        }
        .ocr-run-options {
          background: #f8fbfa;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 14px;
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          padding: 10px;
          width: 100%;
        }
        .ocr-run-options.compact {
          margin-top: 12px;
        }
        .inline-details {
          flex-basis: 100%;
        }
        .inline-details summary {
          cursor: pointer;
          font-weight: 800;
        }
        .ocr-llm-prompt-textarea {
          margin-top: 8px;
          min-height: 120px;
          width: 100%;
        }
        .sheet-table {
          border-collapse: separate;
          border-spacing: 0;
          min-width: 100%;
          width: max-content;
        }
        .compact-sheet-table {
          font-size: 11px;
        }
        .sheet-table th,
        .sheet-table td {
          border-bottom: 1px solid #e5dece;
          border-right: 1px solid #e5dece;
          padding: 0;
        }
        .sheet-table th {
          background: #f4eddd;
          color: #405045;
          font-size: 11px;
          min-width: 52px;
          padding: 6px 6px;
          position: sticky;
          top: 0;
          z-index: 1;
        }
        .sheet-table th:first-child {
          left: 0;
          min-width: 34px;
          position: sticky;
          z-index: 2;
        }
        .sheet-table .sheet-row-index-col {
          max-width: 34px;
          min-width: 34px;
          width: 34px;
        }
        .sheet-table tbody tr.sheet-row-date-boundary th,
        .sheet-table tbody tr.sheet-row-date-boundary td {
          border-top: 3px solid #7f8790;
        }
        .sheet-table tbody tr.sheet-row-daypart-boundary th,
        .sheet-table tbody tr.sheet-row-daypart-boundary td {
          border-top: 2px solid #aeb7bf;
        }
        .sheet-table tbody tr.sheet-row-morning th,
        .sheet-table tbody tr.sheet-row-morning td,
        .sheet-table tbody tr.sheet-row-morning td input {
          background: #fff7e8;
        }
        .sheet-table tbody tr.sheet-row-noon th,
        .sheet-table tbody tr.sheet-row-noon td,
        .sheet-table tbody tr.sheet-row-noon td input {
          background: #edf7ff;
        }
        .sheet-table tbody tr.sheet-row-evening th,
        .sheet-table tbody tr.sheet-row-evening td,
        .sheet-table tbody tr.sheet-row-evening td input {
          background: #eff9ee;
        }
        .sheet-table tbody th {
          top: auto;
        }
        .sheet-table td.sticky-structural-col,
        .sheet-table th.sticky-structural-col {
          position: sticky;
          z-index: 2;
        }
        .sheet-table thead th.sticky-structural-col {
          z-index: 3;
        }
        .sheet-input-wrap {
          position: relative;
        }
        .sheet-table td input {
          background: #fffdf7;
          border: 0;
          border-radius: 0;
          min-width: 48px;
          padding: 4px 5px;
          width: 100%;
        }
        .sheet-table th.sheet-col-date,
        .sheet-table td.sheet-col-date,
        .sheet-table td.sheet-col-date .sheet-input-wrap,
        .sheet-table td.sheet-col-date input {
          min-width: 52px;
          width: 52px;
        }
        .sheet-table th.sheet-col-daypart,
        .sheet-table td.sheet-col-daypart,
        .sheet-table td.sheet-col-daypart .sheet-input-wrap,
        .sheet-table td.sheet-col-daypart input {
          min-width: 42px;
          width: 42px;
        }
        .sheet-table th.sheet-col-menu,
        .sheet-table td.sheet-col-menu,
        .sheet-table td.sheet-col-menu .sheet-input-wrap,
        .sheet-table td.sheet-col-menu input {
          min-width: 170px;
          width: 170px;
        }
        .sheet-table th.sheet-col-quantity,
        .sheet-table td.sheet-col-quantity,
        .sheet-table td.sheet-col-quantity .sheet-input-wrap,
        .sheet-table td.sheet-col-quantity input {
          min-width: 58px;
          width: 58px;
        }
        .sheet-table td.sticky-structural-col input {
          background: inherit;
        }
        .sheet-table input[readonly] {
          color: #344238;
          cursor: default;
          font-weight: 700;
        }
        .confidence-high input {
          color: #111827;
          font-weight: 800;
        }
        .confidence-medium input {
          color: #0a6b89;
        }
        .confidence-low input {
          color: #a15f2d;
        }
        .below-confidence-threshold {
          opacity: 0.5;
        }
        .sheet-overlay-suggestion {
          color: #d7351d;
          font-size: 11px;
          font-weight: 900;
          position: absolute;
          right: 2px;
          top: -8px;
          z-index: 2;
        }
        .sheet-cell-candidate {
          border-radius: 999px;
          font-size: 10px;
          font-weight: 900;
          left: 2px;
          max-width: calc(100% - 4px);
          overflow: hidden;
          padding: 1px 5px;
          pointer-events: none;
          position: absolute;
          text-overflow: ellipsis;
          top: -9px;
          white-space: nowrap;
          z-index: 3;
        }
        .sheet-cell-candidate-ai {
          background: rgba(221, 246, 255, 0.94);
          color: #075985;
        }
        .sheet-cell-candidate-anomaly {
          background: rgba(255, 237, 213, 0.96);
          color: #b45309;
          top: 23px;
        }
        .sheet-table td.sheet-auto-edit-candidate,
        .sheet-table td.sheet-auto-edit-candidate input {
          background: #e0f7ff !important;
        }
        .sheet-table td.sheet-auto-edit-selected {
          box-shadow: inset 0 0 0 3px #0284c7;
        }
        .sheet-table td.sheet-anomaly-high,
        .sheet-table td.sheet-anomaly-high input {
          background: #ffd9cc !important;
        }
        .sheet-table td.sheet-anomaly-medium,
        .sheet-table td.sheet-anomaly-medium input {
          background: #fff0b8 !important;
        }
        .sheet-table td.sheet-anomaly-low,
        .sheet-table td.sheet-anomaly-low input {
          background: #e8f0ff !important;
        }
        .sheet-table td.sheet-anomaly-selected {
          box-shadow: inset 0 0 0 3px #e6532e;
        }
        .sheet-table td.sheet-auto-edit-selected.sheet-anomaly-selected {
          box-shadow: inset 0 0 0 3px #0284c7, inset 0 0 0 6px #e6532e;
        }
        .llm-review-panel,
        .anomaly-review-panel {
          background: #fff8e8;
          border: 1px solid #e8d6af;
          border-radius: 14px;
          display: grid;
          gap: 8px;
          margin-top: 10px;
          padding: 12px;
        }
        .anomaly-table-wrap {
          max-height: 360px;
          overflow: auto;
        }
        .anomaly-table {
          border-collapse: collapse;
          font-size: 12px;
          min-width: 1080px;
          width: 100%;
        }
        .anomaly-table th,
        .anomaly-table td {
          border-bottom: 1px solid #eadfcb;
          padding: 7px 8px;
          text-align: left;
          vertical-align: top;
        }
        .anomaly-table tr.anomaly-high td {
          background: #fff0e8;
        }
        .anomaly-table tr.anomaly-medium td {
          background: #fff8e8;
        }
        .anomaly-table tbody tr {
          cursor: pointer;
        }
        .anomaly-table tr.selected-anomaly-row td {
          box-shadow: inset 0 -2px 0 #e6532e, inset 0 2px 0 #e6532e;
        }
        .auto-edit-table tbody tr {
          cursor: pointer;
        }
        .auto-edit-table tr.auto-edit-candidate-row td {
          background: #f0fbff;
        }
        .auto-edit-table tr.selected-auto-edit-row td {
          box-shadow: inset 0 -2px 0 #0284c7, inset 0 2px 0 #0284c7;
        }
        .anomaly-menu-cell {
          min-width: 180px;
        }
        .step3-anomaly-table-wrap {
          max-height: 260px;
        }
        .anomaly-review-panel.ok {
          background: #edf7ef;
          border-color: #c8e1cc;
        }
        .anomaly-review-panel.pending {
          background: #f5f1e7;
          border-color: #ddd5c2;
        }
        .llm-review-header {
          align-items: center;
          display: flex;
          gap: 12px;
          justify-content: space-between;
        }
        .llm-review-list {
          display: grid;
          gap: 6px;
          list-style: none;
          margin: 0;
          padding: 0;
        }
        .llm-review-list li {
          background: rgba(255, 253, 247, 0.82);
          border-radius: 10px;
          cursor: pointer;
          padding: 8px 10px;
        }
        .llm-review-list li.auto-edit-candidate-row {
          border-left: 4px solid #38bdf8;
        }
        .llm-review-list li.selected-auto-edit-row {
          background: #e0f7ff;
          box-shadow: inset 0 0 0 2px #0284c7;
        }
        .candidate-location {
          color: #075985;
          display: inline-block;
          font-weight: 900;
          margin-right: 8px;
        }
        .candidate-change {
          color: #0f172a;
          display: inline-block;
          font-weight: 800;
          margin-right: 6px;
        }
        .row-apply-button {
          margin-left: 8px;
          vertical-align: middle;
        }
        .llm-review-list li.anomaly-high {
          box-shadow: inset 4px 0 0 #d7351d;
        }
        .llm-review-list li.anomaly-medium {
          box-shadow: inset 4px 0 0 #d58b2c;
        }
        .json-details {
          margin-top: 16px;
        }
        .json-details summary {
          color: #687269;
          cursor: pointer;
          font-weight: 800;
          margin-bottom: 10px;
        }
        .btn,
        .ghost-link {
          align-items: center;
          background: #f0eadc;
          border: 0;
          border-radius: 999px;
          color: #1c2822;
          cursor: pointer;
          display: inline-flex;
          font-weight: 800;
          justify-content: center;
          min-height: 40px;
          padding: 0 16px;
          text-decoration: none;
        }
        .btn.primary {
          background: #1c2822;
          color: #fffdf7;
        }
        .btn.ghost {
          background: #ebe5d5;
        }
        .btn.ghost.active {
          background: #f4c78d;
          color: #1c2822;
        }
        .btn.tiny {
          font-size: 12px;
          min-height: 30px;
          padding: 0 10px;
        }
        .btn.danger {
          background: #f5d5cb;
          color: #8a2c18;
        }
        .btn:disabled {
          cursor: not-allowed;
          opacity: 0.48;
        }
        .notice {
          border-radius: 14px;
          font-weight: 700;
          margin: 12px 36px;
          padding: 12px 16px;
        }
        .notice.success {
          background: #e7f4e8;
          color: #24552b;
        }
        .notice.error {
          background: #f8dfd8;
          color: #8a2c18;
        }
        .notice.warning {
          background: #fff3d8;
          color: #7a4a0f;
        }
        .inline-action-btn {
          background: #fffdf7;
          border: 1px solid #c9bda2;
          border-radius: 999px;
          color: #162019;
          cursor: pointer;
          font-weight: 800;
          margin-left: 10px;
          padding: 4px 10px;
        }
        .quad-review-summary {
          background: #fbf7ed;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          display: grid;
          gap: 10px;
          grid-template-columns: 120px 1fr 1fr 1.4fr;
          margin: 14px 0;
          padding: 12px;
        }
        .quad-review-summary p {
          margin: 2px 0 0;
        }
        .quad-review-actions {
          align-items: center;
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin: 12px 0;
        }
        .quad-review-canvas-wrap {
          background: #ffffff;
          border: 1px solid #ddd5c2;
          border-radius: 16px;
          max-height: 78vh;
          overflow: auto;
        }
        .quad-review-canvas {
          position: relative;
          width: min(100%, 1120px);
        }
        .quad-review-canvas img {
          display: block;
          width: 100%;
        }
        .quad-clickable-image {
          cursor: crosshair;
        }
        .quad-review-canvas svg {
          inset: 0;
          pointer-events: none;
          position: absolute;
          width: 100%;
          height: 100%;
        }
        .quad-estimate-polygon {
          fill: rgba(236, 74, 55, 0.08);
          stroke: #ec4a37;
          stroke-width: 6;
        }
        .quad-active-polyline {
          fill: none;
          stroke: #2f7dff;
          stroke-width: 8;
        }
        .quad-estimate-point {
          fill: #ec4a37;
          stroke: #fff;
          stroke-width: 5;
        }
        .quad-manual-point {
          fill: #2f7dff;
          stroke: #fff;
          stroke-width: 5;
        }
        .quad-point-label {
          fill: #162019;
          font-size: 42px;
          font-weight: 900;
          paint-order: stroke;
          stroke: #fffdf7;
          stroke-width: 8;
        }
        .header-axis-canvas-wrap {
          background: #ffffff;
          border: 1px solid #ddd5c2;
          border-radius: 16px;
          max-height: 76vh;
          overflow: auto;
        }
        .header-axis-timeout-control {
          align-items: center;
          background: rgba(255, 255, 255, 0.78);
          border: 1px solid rgba(54, 82, 68, 0.14);
          border-radius: 999px;
          color: #526258;
          display: inline-flex;
          font-size: 12px;
          font-weight: 800;
          gap: 6px;
          padding: 6px 10px;
        }
        .header-axis-timeout-control input {
          background: #fffdf7;
          border: 1px solid #d8cdb6;
          border-radius: 10px;
          color: #1c2822;
          font-size: 13px;
          font-weight: 900;
          padding: 6px 8px;
          width: 76px;
        }
        .header-axis-canvas {
          min-width: 1120px;
          position: relative;
          width: 100%;
        }
        .header-axis-canvas img {
          display: block;
          width: 100%;
        }
        .header-axis-canvas svg {
          cursor: ew-resize;
          inset: 0;
          position: absolute;
          width: 100%;
          height: 100%;
        }
        .header-axis-line {
          stroke: rgba(255, 145, 0, 0.65);
          stroke-width: 4;
        }
        .header-axis-line.selected {
          stroke: rgba(0, 104, 255, 0.92);
          stroke-width: 7;
        }
        .header-axis-point {
          cursor: ew-resize;
          fill: #ff9100;
          stroke: #fff;
          stroke-width: 3;
        }
        .header-axis-point.selected {
          fill: #0068ff;
          stroke: #fff;
          stroke-width: 5;
        }
        .header-axis-label {
          fill: #1c2822;
          font-size: 18px;
          font-weight: 900;
          paint-order: stroke;
          stroke: #fffdf7;
          stroke-width: 5;
        }
        .header-axis-label.selected {
          fill: #0068ff;
        }
        .ocr-result-list {
          display: grid;
          gap: 12px;
          margin-top: 16px;
        }
        .ocr-card {
          align-items: flex-start;
          background: #fffdf7;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          display: flex;
          gap: 16px;
          justify-content: space-between;
          padding: 14px;
        }
        .ocr-card.selected {
          border-color: #2f7d52;
          box-shadow: inset 4px 0 0 #2f7d52;
        }
        .ocr-card-body {
          display: grid;
          gap: 12px;
          grid-template-columns: minmax(220px, 0.34fr) minmax(680px, 1fr);
          min-width: 0;
          flex: 1;
        }
        .ocr-overlay-preview-card {
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 14px;
          overflow: hidden;
        }
        .preview-header {
          align-items: center;
          border-bottom: 1px solid #e5dece;
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding: 10px 12px;
        }
        .preview-header-actions {
          align-items: center;
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          justify-content: flex-end;
        }
        .preview-mode-toggle {
          background: #f3efe5;
          border: 1px solid #ded5c2;
          border-radius: 999px;
          display: inline-flex;
          gap: 2px;
          padding: 3px;
        }
        .preview-mode-toggle button {
          background: transparent;
          border: 0;
          border-radius: 999px;
          color: #516056;
          cursor: pointer;
          font-size: 12px;
          font-weight: 900;
          padding: 7px 10px;
        }
        .preview-mode-toggle button.active {
          background: #1b2a22;
          color: #fffdf7;
        }
        .ocr-overlay-preview-image {
          background: #ffffff;
          display: block;
          max-height: 78vh;
          object-fit: contain;
          width: 100%;
        }
        .ocr-overlay-preview-pdf {
          background: #fff;
          border: 0;
          display: block;
          height: 580px;
          width: 100%;
        }
        .digest {
          color: #8a826e;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 11px;
          margin: 6px 0 0;
          word-break: break-all;
        }
        pre {
          background: #162019;
          border-radius: 14px;
          color: #e9f1e7;
          font-size: 12px;
          max-height: 360px;
          overflow: auto;
          padding: 14px;
        }
        @media (max-width: 980px) {
          .hero,
          .state-panel {
            display: block;
          }
          .step-nav,
          .form-grid,
          .step1-control-grid,
          .step3-workspace.side-by-side,
          .quad-review-summary,
          .ocr-card-body {
            grid-template-columns: 1fr;
          }
          .step3-overlay-image {
            min-width: 720px;
          }
          .panel,
          .notice {
            margin-left: 16px;
            margin-right: 16px;
          }
        }
      `}</style>
    </main>
  );
}
