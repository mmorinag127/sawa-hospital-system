import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
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
  expanded_cell_copy_mode?: ExpandedCellCopyMode | null;
  context_suggestion?: ContextSuggestion | null;
  bagging_result_id?: string | null;
  output_bundle_id?: string | null;
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
  } | null;
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
type OcrRunMode = "hakodate" | "llm";
type OutputPreviewType = "labels" | "delivery" | "aggregate";
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
  loading: boolean;
  error: string;
};

type ExpandedCellCopyMode = "auto" | "enabled" | "disabled";

type FacilityTemplateColumn = {
  index: number;
  source_index?: number;
  role: string;
  header?: string;
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
  if (detail === "fax_template_id_required") return "帳票レイアウトを選択してください。";
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const error = (detail as { error?: unknown }).error;
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

const facilityTemplateDietTypeOptions = preferredDietOrder.map((value) => ({
  value,
  label: dietTypeLabels[value] || value,
}));

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
    const name = String(column.name || "").trim();
    const payload: Record<string, unknown> = { index: idx, role };
    if (typeof column.source_index === "number" && Number.isFinite(column.source_index)) payload.source_index = Number(column.source_index);
    if (header) payload.header = header;
    if (name) payload.name = name;
    if (role === "quantity") {
      const dietType = normalizeDietTypeToken(column.diet_type || header || name || "") || "unknown";
      const areaId = normalizeFacilityAreaToken(column.area_id || header || name || "");
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

const buildFacilityTemplateAreaOptions = (facilityConfig: Record<string, any> | null, columns: FacilityTemplateColumn[]) => {
  const seen = new Set<string>();
  const options: { value: string; label: string }[] = [];
  const push = (value: string, label?: string) => {
    const normalized = normalizeFacilityAreaToken(value);
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    options.push({ value: normalized, label: label || normalized });
  };
  push("X", "共通");
  const areas = Array.isArray(facilityConfig?.areas) ? facilityConfig.areas : [];
  areas.forEach((area: any) => {
    const areaId = String(area?.area_id || area?.id || "").trim();
    const areaName = String(area?.name || "").trim();
    if (areaId || areaName) push(areaId || areaName, areaName && areaName !== areaId ? `${normalizeFacilityAreaToken(areaId || areaName)} (${areaName})` : undefined);
  });
  columns.forEach((column) => {
    if (isQuantityRole(column.role)) push(column.area_id || "X");
  });
  return options;
};

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
    ocr_running: "Step1: OCR実行中",
    ocr_selected: "Step2完了: 正解OCR選択済み",
    sheet_saved: "Step3完了: シート保存済み",
    bagging_ready: "Step4: 袋分け確認",
    bagging_confirmed: "Step4完了: 出力確認待ち",
    output_review: "Step5: 出力確認",
    confirmed: "確定済み",
  };
  return labels[normalized] || normalized || "未開始";
};

const stepIndexForState = (state?: string | null) => {
  const normalized = String(state || "").trim();
  if (["uploaded", "context_confirmed", "ocr_running", "ocr_failed"].includes(normalized)) return 1;
  if (["ocr_completed"].includes(normalized)) return 2;
  if (["ocr_selected"].includes(normalized)) return 3;
  if (["sheet_saved"].includes(normalized)) return 4;
  if (["bagging_ready"].includes(normalized)) return 4;
  if (["bagging_confirmed"].includes(normalized)) return 5;
  if (["output_review", "confirmed"].includes(normalized)) return 5;
  return 1;
};

const stepLabels = [
  { step: 1, label: "PDF/施設/週次" },
  { step: 2, label: "OCR選択" },
  { step: 3, label: "シート編集" },
  { step: 4, label: "袋分け" },
  { step: 5, label: "出力確認" },
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

export default function OrderWorkflowV2Page() {
  const router = useRouter();
  const orderId = typeof router.query.id === "string" ? router.query.id : "";
  const [workflow, setWorkflow] = useState<WorkflowV2 | null>(null);
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
  const [customWeekRangeStart, setCustomWeekRangeStart] = useState<string>("");
  const [customWeekRangeEnd, setCustomWeekRangeEnd] = useState<string>("");
  const [sheetJson, setSheetJson] = useState(formatJson(defaultSheet));
  const [sheetPayload, setSheetPayload] = useState<SheetPayload | null>(null);
  const [visibleStep, setVisibleStep] = useState(1);
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [pdfError, setPdfError] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [step3LayoutMode, setStep3LayoutMode] = useState<Step3LayoutMode>("side-by-side");
  const [focusedSheetCell, setFocusedSheetCell] = useState<{ rowIndex: number; colIndex: number } | null>(null);
  const [ocrConfidenceDisplayMode, setOcrConfidenceDisplayMode] = useState<ConfidenceDisplayMode>("strict");
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

  const selectedOcr = useMemo(
    () => ocrResults.find((item) => item.selected || item.ocr_result_id === workflow?.selected_ocr_result_id) || null,
    [ocrResults, workflow?.selected_ocr_result_id],
  );

  const selectedWeekValue = useMemo(
    () => normalizeConcreteWeekValue(weekDraft) || weekValueFromRange(contextForm.week_start, contextForm.week_end),
    [contextForm.week_end, contextForm.week_start, weekDraft],
  );

  const contextSuggestion = workflow?.context_suggestion || null;
  const contextReady = Boolean(contextForm.facility_id.trim() && contextForm.week_start && contextForm.week_end);
  const workflowContextConfirmed = Boolean(
    workflow?.facility_id
      && workflow?.week_start
      && workflow?.week_end
      && !["uploaded", "facility_template_unresolved"].includes(String(workflow?.state || "")),
  );
  const selectedFacility = useMemo(
    () => facilityOptions.find((option) => option.id === contextForm.facility_id) || null,
    [contextForm.facility_id, facilityOptions],
  );
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
  const facilityTemplateAreaOptions = useMemo(
    () => buildFacilityTemplateAreaOptions(facilityResolvedConfig, facilityTemplateColumnDraft),
    [facilityResolvedConfig, facilityTemplateColumnDraft],
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
    const [workflowRes, ocrRes, inspectionRes] = await Promise.all([
      apiClient.get<WorkflowV2>(`/orders/${orderId}/workflow-v2`),
      apiClient.get<{ results: OcrResult[] }>(`/orders/${orderId}/workflow-v2/ocr-results`),
      apiClient.get<InspectionPayload>(`/orders/${orderId}/workflow-v2/inspection`),
    ]);
    setWorkflow(workflowRes.data);
    setExpandedCellCopyMode(normalizeExpandedCellCopyMode(workflowRes.data.expanded_cell_copy_mode));
    setOcrResults(Array.isArray(ocrRes.data.results) ? ocrRes.data.results : []);
    setInspection(inspectionRes.data);
    const savedSheet = inspectionRes.data.saved_sheet?.sheet;
    if (savedSheet) {
      const normalizedSavedSheet = normalizeSheetPayload(savedSheet);
      setSheetPayload(normalizedSavedSheet);
      setSheetJson(formatJson(normalizedSavedSheet || savedSheet));
    } else {
      setSheetPayload(null);
      setSheetJson(formatJson(defaultSheet));
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
    if (!router.isReady || !orderId || workflow?.state !== "ocr_running") return undefined;
    const timer = window.setInterval(() => {
      refreshAll().catch((err) => {
        setError(formatApiError(err, "OCR進捗の取得に失敗しました"));
      });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [router.isReady, orderId, workflow?.state]);

  const loadFacilityTemplateStatus = async (facilityId: string) => {
    const normalizedFacilityId = facilityId.trim();
    if (!normalizedFacilityId) {
      setFacilityTemplateStatus({
        facilityId: "",
        templateId: "",
        templateIds: [],
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
        loading: false,
        error: "",
      });
      setSelectedFacilityTemplateId(templateId || "");
    } catch (err: any) {
      setFacilityTemplateStatus({
        facilityId: normalizedFacilityId,
        templateId: "",
        templateIds: [],
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
          return options.find((item: WeekOption) => item.selected)?.week_id || "";
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
  }, [selectedOcr?.overlay_url, step3LayoutMode, visibleStep]);

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    let active = true;
    let objectUrl = "";
    setPdfUrl("");
    setPdfError("");
    apiClient
      .get<Blob>(`/orders/${orderId}/document`, { responseType: "blob" })
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
  }, [router.isReady, orderId]);

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
      await apiClient.post(`/orders/${orderId}/workflow-v2/context`, {
        facility_id: contextForm.facility_id,
        week_start: contextForm.week_start,
        week_end: contextForm.week_end,
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
          }
          next.header = defaultHeaderForFacilityTemplateColumn(next);
          next.name = defaultNameForFacilityTemplateColumn(next);
        }
        if (next.role === "quantity" && (key === "diet_type" || key === "area_id")) {
          next.diet_type = normalizeDietTypeToken(next.diet_type || "") || "unknown";
          next.area_id = normalizeFacilityAreaToken(next.area_id || "");
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
      await refreshAll();
      setVisibleStep(3);
      setMessage("拡大セルコピー設定を変更しました。選択OCRからシートを再生成してください。");
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

  const runOcr = () =>
    runAction(
      "Step1 OCR run",
      async () => {
        const payload: Record<string, unknown> = {
          stale_action: "retry",
          mode: ocrRunMode,
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
        successMessage: ocrRunMode === "llm" ? "Step1 LLM OCR run が開始しました" : "Step1 OCR run が開始しました",
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
      setSheetJson(formatJson(normalized));
    }, {
      successMessage: "選択OCRからシートを生成しました",
      refreshAfter: false,
    });

  const updateSheetCell = (rowIndex: number, colIndex: number, value: string) => {
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row, idx) => (
        idx === rowIndex ? row.map((cell, cellIdx) => (cellIdx === colIndex ? value : cell)) : row
      ));
      const nextSheet = { ...current, rows };
      setSheetJson(formatJson(nextSheet));
      return nextSheet;
    });
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

  const fillQuantityColumn = () => {
    const colIndex = Number(columnFillTarget);
    if (!sheetPayload || !Number.isInteger(colIndex) || colIndex < 0) return;
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row) => row.map((cell, idx) => (idx === colIndex ? columnFillValue : cell)));
      const nextSheet = { ...current, rows };
      setSheetJson(formatJson(nextSheet));
      return nextSheet;
    });
  };

  const swapQuantityColumns = () => {
    const left = Number(swapLeftColumn);
    const right = Number(swapRightColumn);
    if (!sheetPayload || !Number.isInteger(left) || !Number.isInteger(right) || left === right) return;
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
      setSheetJson(formatJson(nextSheet));
      return nextSheet;
    });
  };

  const applyVisibleOcrSuggestions = () => {
    if (!sheetPayload || !ocrOverlayItemMap.size) return;
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
      setSheetJson(formatJson(nextSheet));
      return nextSheet;
    });
  };

  const saveSheet = () =>
    runAction("Step3 sheet save", async () => {
      const parsed = sheetPayload || normalizeSheetPayload(JSON.parse(sheetJson));
      if (!parsed) {
        throw new Error("保存できるシートがありません");
      }
      await apiClient.put(`/orders/${orderId}/workflow-v2/sheet`, {
        sheet: parsed,
        edited_by: "operator",
      });
    }, {
      successMessage: "シートを保存しました",
      nextStep: 4,
    });

  const runBagging = () =>
    runAction("Step4 bagging", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/bagging`);
    });

  const confirmBagging = () =>
    runAction(
      "Step4 bagging confirm",
      async () => {
        await apiClient.post(`/orders/${orderId}/workflow-v2/bagging/confirm`);
      },
      {
        successMessage: "袋分けを確定しました",
        nextStep: 5,
      },
    );

  const prepareOutputReview = () =>
    runAction(
      "Step5 output review",
      async () => {
        await apiClient.post(`/orders/${orderId}/workflow-v2/outputs/review`);
      },
      {
        successMessage: "出力確認を作成しました",
        nextStep: 5,
      },
    );

  const finalConfirm = () =>
    runAction(
      "Step5 final confirm",
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
      setDownloadMessage(`${label}のダウンロードに失敗しました。${suffix}`);
      if (popup) {
        popup.close();
      }
    }
  };

  const outputPreviewLabels: Record<OutputPreviewType, string> = {
    labels: "ラベルCSV",
    delivery: "納品書Excel",
    aggregate: "総量CSV",
  };

  const loadOutputPreview = async (type: OutputPreviewType) => {
    if (!orderId) return;
    setOutputPreviewLoading(true);
    setOutputPreviewMessage("プレビューを取得中...");
    try {
      const res = await apiClient.get("/outputs/preview", {
        params: { order_id: orderId, type, limit: 10 },
      });
      const headers = Array.isArray(res.data?.headers) ? res.data.headers.map((item: unknown) => String(item ?? "")) : [];
      const rows = Array.isArray(res.data?.rows)
        ? res.data.rows
            .filter((row: unknown): row is unknown[] => Array.isArray(row))
            .slice(0, 10)
            .map((row: unknown[]) => row.map((cell) => String(cell ?? "")))
        : [];
      setOutputPreview({ type, headers, rows });
      setOutputPreviewMessage(rows.length ? "" : "プレビューが空です。");
    } catch {
      setOutputPreview(null);
      setOutputPreviewMessage("プレビューの取得に失敗しました。");
    } finally {
      setOutputPreviewLoading(false);
    }
  };

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
        <div>
          <p className="eyebrow">Current State</p>
          <h2>{stateLabel(workflow?.state)}</h2>
          <p className="subtle">{workflow?.headline || "workflow-v2 を読み込み中です。"}</p>
          {workflow?.state === "ocr_running" ? (
            <p className="ocr-progress-inline">OCR進捗: {formatOcrProgress(workflow)}</p>
          ) : null}
        </div>
        <div className="state-actions">
          <button className="btn ghost" type="button" onClick={() => void refreshAll()} disabled={Boolean(busy)}>
            再読込
          </button>
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

      <section className="panel order-info-panel">
        <div className="panel-header">
          <div>
            <p className="step-tag">Order Info</p>
            <h2>注文情報</h2>
            <p className="subtle">前工程と照合するための基本情報です。</p>
          </div>
        </div>
        <div className="summary-grid summary-grid--compact">
          <div className="summary-primary-card">
            <span className="field-label">注文ID</span>
            <p className="summary-value">{orderId || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">処理状態</span>
            <p className="summary-value">{stateLabel(workflow?.state)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">施設</span>
            <p className="summary-value">{workflow?.facility_id || "未設定"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">週次</span>
            <p className="summary-value">
              {formatWeekLabel(weekValueFromRange(workflow?.week_start, workflow?.week_end)) || "未設定"}
            </p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">テンプレート</span>
            <p className="summary-value">{workflow?.template_id || "施設設定から自動解決"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR結果</span>
            <p className="summary-value">{ocrResults.length}件</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCRジョブ</span>
            <p className="summary-value">{workflow?.ocr_job?.ocr_job_id || "-"}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR進捗</span>
            <p className="summary-value">{formatOcrProgress(workflow)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR処理時間</span>
            <p className="summary-value">{formatElapsedSeconds(workflow?.ocr_job?.elapsed_seconds)}</p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">OCR開始/更新</span>
            <p className="summary-value">
              {formatDateTime(workflow?.ocr_job?.started_at)} / {formatDateTime(workflow?.ocr_job?.finished_at || workflow?.ocr_job?.updated_at)}
            </p>
          </div>
          <div className="summary-primary-card">
            <span className="field-label">シート最終保存</span>
            <p className="summary-value">{formatDateTime(inspection?.saved_sheet?.edited_at || inspection?.saved_sheet?.created_at)}</p>
          </div>
        </div>
      </section>

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
              <p className="subtle">原本PDFを確認し、施設と週設定を完了してください。</p>
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
            <div className="summary-grid summary-grid--compact">
              <div className="summary-primary-card">
                <span className="field-label">注文ID</span>
                <p className="summary-value">{orderId || "-"}</p>
              </div>
              <div className="summary-primary-card">
                <span className="field-label">現在の施設</span>
                <p className="summary-value">{workflow?.facility_id || "未設定"}</p>
              </div>
              <div className="summary-primary-card">
                <span className="field-label">現在の週</span>
                <p className="summary-value">
                  {formatWeekLabel(weekValueFromRange(workflow?.week_start, workflow?.week_end)) || "未設定"}
                </p>
              </div>
              <div className="summary-primary-card">
                <span className="field-label">施設テンプレート</span>
                <p className="summary-value">{workflow?.template_id || "施設設定から自動解決"}</p>
              </div>
            </div>
            {contextSuggestion ? (
              <div className="context-suggestion-card">
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
                    <p className="subtle">日付候補: {contextSuggestion.date_hints.slice(0, 8).join(" / ")}</p>
                  ) : null}
                  {Array.isArray(contextSuggestion.facility_candidates) && contextSuggestion.facility_candidates.length ? (
                    <ul className="context-suggestion-candidates">
                      {contextSuggestion.facility_candidates.slice(0, 3).map((candidate, index) => (
                        <li key={`facility-suggestion-${index}`}>{formatFacilityCandidate(candidate)}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
                <button className="btn secondary" type="button" onClick={applyContextSuggestion} disabled={Boolean(busy)}>
                  推定をフォームに反映
                </button>
              </div>
            ) : null}
            <div className="summary-actions">
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
                <div className="step1-week-range">
                  <span className="subtle">例外時だけ範囲指定します。</span>
                  <div className="summary-actions">
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
                      例外範囲を設定
                    </button>
                  </div>
                  {selectedWeekValue ? (
                    <span className="subtle">設定予定: {formatWeekLabel(selectedWeekValue) || selectedWeekValue}</span>
                  ) : null}
                </div>
              </label>
              <button className="btn primary" type="button" onClick={confirmContext} disabled={Boolean(busy || !contextReady || facilityTemplateMissing)}>
                {facilityTemplateMissing ? "先にテンプレート登録" : contextReady ? "設定を保存" : "施設と週を選択"}
              </button>
              <div className="ocr-run-options">
                <label className="toolbar-field">
                  <span>OCR実行方式</span>
                  <select value={ocrRunMode} onChange={(event) => setOcrRunMode(event.target.value as OcrRunMode)} disabled={Boolean(busy)}>
                    <option value="hakodate">箱館方式</option>
                    <option value="llm">AIに任せる</option>
                  </select>
                </label>
                {ocrRunMode === "llm" ? (
                  <>
                    <label className="toolbar-field">
                      <span>自動調整プリセット</span>
                      <select value={llmPromptPreset} onChange={(event) => setLlmPromptPreset(event.target.value as LlmPromptPreset)} disabled={Boolean(busy)}>
                        {(Object.keys(llmPromptPresetLabels) as LlmPromptPreset[]).map((preset) => (
                          <option key={preset} value={preset}>{llmPromptPresetLabels[preset]}</option>
                        ))}
                      </select>
                    </label>
                    <label className="toolbar-field">
                      <span>AI provider</span>
                      <select value={llmProvider} onChange={(event) => setLlmProvider(event.target.value)} disabled={Boolean(busy)}>
                        <option value="openai">OpenAI</option>
                        <option value="gemini">Gemini</option>
                      </select>
                    </label>
                    {llmProvider === "gemini" ? (
                      <label className="toolbar-field">
                        <span>Gemini model</span>
                        <select value={llmModelMode} onChange={(event) => setLlmModelMode(event.target.value as "flash" | "pro" | "other")} disabled={Boolean(busy)}>
                          <option value="flash">Flash</option>
                          <option value="pro">Pro</option>
                          <option value="other">Other</option>
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
                      <summary>LLM追加指示（任意）</summary>
                      <textarea
                        className="ocr-llm-prompt-textarea"
                        value={ocrPrompt}
                        onChange={(event) => setOcrPrompt(event.target.value)}
                        placeholder="例: 読みづらい手書き数量は前後セルの連続性を見て補完する"
                      />
                    </details>
                  </>
                ) : null}
              </div>
              <button
                className="btn"
                type="button"
                onClick={runOcr}
                disabled={Boolean(
                  busy
                  || !workflowContextConfirmed
                  || (ocrRunMode === "llm" && llmProvider === "gemini" && llmModelMode === "other" && !llmCustomModel.trim())
                )}
              >
                OCRを実行
              </button>
            </div>
            {contextForm.facility_id ? (
              <div className={`facility-template-resolution ${facilityTemplateMissing ? "blocked" : "resolved"}`}>
                <div className="facility-template-resolution-copy">
                  <strong>施設テンプレート登録</strong>
                  <p>
                    {facilityTemplateStatus.loading
                      ? "施設テンプレート設定を確認中です。"
                      : facilityTemplateStatus.templateId
                        ? `登録済み: ${formatFaxTemplateOptionLabel(selectedFacilityRegisteredTemplateOption) || facilityTemplateStatus.templateId}`
                        : "この施設には帳票レイアウトが登録されていません。未登録のままOCRは実行できません。"}
                  </p>
                  {selectedFacility ? (
                    <p className="subtle">対象施設: {formatFacilityLabel(selectedFacility)}</p>
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
                            <th>内部名</th>
                            <th>区分</th>
                            <th>エリア</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {facilityTemplateColumnDraft.map((column, idx) => {
                            const quantityColumn = isQuantityRole(column.role);
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
                                    value={column.name || ""}
                                    onChange={(event) => updateFacilityTemplateColumn(idx, "name", event.target.value)}
                                    placeholder="qty.regular_x"
                                  />
                                </td>
                                <td>
                                  <select
                                    className="input"
                                    value={quantityColumn ? normalizeDietTypeToken(column.diet_type || "") || "unknown" : ""}
                                    disabled={!quantityColumn}
                                    onChange={(event) => updateFacilityTemplateColumn(idx, "diet_type", event.target.value)}
                                  >
                                    {facilityTemplateDietTypeOptions.map((option) => (
                                      <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                  </select>
                                </td>
                                <td>
                                  <select
                                    className="input"
                                    value={quantityColumn ? normalizeFacilityAreaToken(column.area_id || "") : ""}
                                    disabled={!quantityColumn}
                                    onChange={(event) => updateFacilityTemplateColumn(idx, "area_id", event.target.value)}
                                  >
                                    {facilityTemplateAreaOptions.map((option) => (
                                      <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                  </select>
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

        {visibleStep === 2 ? (
        <section className="panel">
          <p className="step-tag">Step2</p>
          <h2>正解 OCR を一つ選ぶ</h2>
          <p className="subtle">選択変更または削除時は、派生 sheet / bagging / output / confirmed snapshot を無効化します。</p>
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
                          <span className="field-label">OCR Overlay</span>
                          <p className="subtle">このOCR結果に紐づくoverlay成果物です。</p>
                        </div>
                        {item.overlay_url ? (
                          <a className="ghost-link" href={item.overlay_url} target="_blank" rel="noreferrer">
                            別タブで開く
                          </a>
                        ) : null}
                      </div>
                      {item.overlay_url ? (
                        <img className="ocr-overlay-preview-image" src={item.overlay_url} alt={`${item.ocr_result_id} overlay`} />
                      ) : (
                        <div className="preview-placeholder">{item.overlay_message || "overlay成果物がありません。"}</div>
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
          <div className="row-actions step3-top-actions">
            <button
              className="btn"
              type="button"
              onClick={generateSheetFromSelectedOcr}
              disabled={Boolean(busy || !workflow?.selected_ocr_result_id)}
            >
              選択OCRからシート生成
            </button>
            <button className="btn primary" type="button" onClick={saveSheet} disabled={Boolean(busy || !workflow?.selected_ocr_result_id || !sheetPayload)}>
              {busy === "Step3 sheet save" ? "保存中..." : "シートを保存"}
            </button>
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
          <p className="subtle">
            拡大セルコピーは、merged cell施設で同じ食区分内へ数量を反映する設定です。変更後は選択OCRからシートを再生成してください。
          </p>
          {sheetPayload ? (
            <div className={`step3-workspace ${step3LayoutMode}`}>
              <div className="step3-overlay-pane">
                <div className="preview-header">
                  <div>
                    <span className="field-label">OCR Overlay</span>
                    <p className="subtle">現在セルに対応する行・列をoverlay上に表示します。</p>
                  </div>
                  {selectedOcr?.overlay_url ? (
                    <a className="ghost-link" href={selectedOcr.overlay_url} target="_blank" rel="noreferrer">
                      別タブで開く
                    </a>
                  ) : null}
                </div>
                <div className="step3-overlay-canvas">
                  {selectedOcr?.overlay_url ? (
                    <>
                      <img
                        ref={overlayImageRef}
                        className="step3-overlay-image"
                        src={selectedOcr.overlay_url}
                        alt={`${selectedOcr.ocr_result_id} overlay`}
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
                    </>
                  ) : (
                    <div className="preview-placeholder">{selectedOcr?.overlay_message || "overlay成果物がありません。"}</div>
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
                            return (
                              <td
                                key={`${field}-${colIdx}`}
                                className={[
                                  isLockedSheetField(field) ? "sticky-structural-col" : "",
                                  sheetWidthClass(field),
                                  confidenceTier ? `confidence-${confidenceTier}` : "",
                                  belowThreshold ? "below-confidence-threshold" : "",
                                  overlayValue ? "has-overlay-suggestion" : "",
                                ].filter(Boolean).join(" ")}
                                style={isLockedSheetField(field) ? { left: `${stickyLeftForSheetField(field, colIdx)}px` } : undefined}
                              >
                                <div className="sheet-input-wrap">
                                  {overlayValue ? <span className="sheet-overlay-suggestion">{overlayValue}</span> : null}
                                  <input
                                    data-sheet-row={rowIdx}
                                    data-sheet-col={colIdx}
                                    value={row[colIdx] || ""}
                                    readOnly={isLockedSheetField(field)}
                                    onFocus={() => setFocusedSheetCell({ rowIndex: rowIdx, colIndex: colIdx })}
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
          <details className="json-details">
            <summary>保存予定JSONを確認</summary>
            <textarea
              value={sheetJson}
              onChange={(event) => {
                const nextJson = event.target.value;
                setSheetJson(nextJson);
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
              <h2>袋分け結果</h2>
              <p className="subtle">保存済みシートから作成した袋分け対象を、日付ごとに確認します。</p>
            </div>
            <div className="row-actions">
              <button className="btn primary" type="button" onClick={runBagging} disabled={Boolean(busy || !workflow?.saved_sheet_id)}>
                袋分けを計算
              </button>
              <button className="btn" type="button" onClick={confirmBagging} disabled={Boolean(busy || !workflow?.bagging_result_id)}>
                確定して次へ
              </button>
            </div>
          </header>
          {inspection?.bagging_result ? (
            <div className="result-summary">
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
              <p className="subtle">袋分け結果はまだありません。保存済みシートから袋分けを計算してください。</p>
            </div>
          )}
        </section>
        ) : null}

        {visibleStep === 5 ? (
        <section className="panel">
          <p className="step-tag">Step5</p>
          <header className="panel-header">
            <div>
              <h2>出力確認</h2>
              <p className="subtle">出力対象を確認して、問題なければ注文を確定します。</p>
            </div>
            <div className="row-actions">
              <button className="btn" type="button" onClick={prepareOutputReview} disabled={Boolean(busy || !workflow?.bagging_result_id)}>
                出力確認を作成
              </button>
              <button className="btn primary" type="button" onClick={finalConfirm} disabled={Boolean(busy || !workflow?.output_bundle_id)}>
                確定して一覧にもどる
              </button>
            </div>
          </header>
          {inspection?.output_bundle ? (
            <div className="result-summary">
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
                  <span className="output-link">納品書Excel</span>
                  <button className="btn primary" type="button" onClick={() => openOutput(`/outputs/delivery-notes?order_id=${orderId}`, "納品書Excel")}>
                    ダウンロード
                  </button>
                  <button className="btn ghost" type="button" onClick={() => loadOutputPreview("delivery")} disabled={outputPreviewLoading}>
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
              <p className="subtle">出力確認はまだ作成されていません。袋分けを確定してから、出力確認を作成してください。</p>
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
          align-items: center;
          display: flex;
          justify-content: space-between;
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
        .step-page {
          display: block;
        }
        .step-nav {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(5, minmax(0, 1fr));
          margin: 16px 36px;
        }
        .step-nav-btn {
          background: rgba(255, 255, 255, 0.74);
          border: 1px solid rgba(54, 82, 68, 0.14);
          border-radius: 16px;
          color: #526258;
          cursor: pointer;
          display: grid;
          gap: 4px;
          min-height: 68px;
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
        .summary-primary-card {
          background: #f8fbfa;
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 12px;
          padding: 10px 12px;
        }
        .summary-value {
          font-weight: 700;
          margin: 4px 0 0;
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
        .ocr-overlay-preview-image {
          background: #ffffff;
          display: block;
          max-height: 78vh;
          object-fit: contain;
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
          .step3-workspace.side-by-side,
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
