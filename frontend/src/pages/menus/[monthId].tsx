import { useRouter } from "next/router";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { MenuItem as MuiMenuItem } from "@mui/material";
import { DataTable, EditForm, FormTextField, SawaProvider, type TableColumn } from "@sawa/ui";
import TopNav from "../../components/TopNav";
import { apiClient, hasActiveSessionAuthHeader } from "../../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../../services/browserSession";
import { DIET_TYPE_OPTIONS, formatDietTypeLabel } from "../../services/menuVocabulary";
import { useH1LeaveGuard } from "../../hooks/useH1LeaveGuard";
import { useMenuInputOwner } from "../../hooks/useMenuInputOwner";

type MenuItem = {
  id: string;
  month_id: string;
  name: string;
  unit_type?: string | null;
  qty_per_serving?: number | string | null;
  temp_type?: string | null;
  daypart?: string | null;
  category?: string | null;
  diet_type?: string | null;
  facility_override?: string | null;
  bag_max_qty?: number | string | null;
  bag_max_unit?: string | null;
};

type MenuEntry = {
  id: string;
  month_id: string;
  menu_date?: string | null;
  daypart?: string | null;
  name: string;
  category?: string | null;
  diet_type?: string | null;
  slot_index?: number | null;
  facility_override?: string | null;
};

type MonthlyMenu = {
  id: string;
  filename?: string | null;
  month_start?: string | null;
  display_name?: string | null;
  uploaded_at?: string | null;
};

type MenuUploadEntry = {
  id: string;
  month_id: string;
  uploaded_at?: string | null;
  filename?: string | null;
  sheet_name?: string | null;
  item_count?: number | null;
  replaced?: boolean;
  actor?: string | null;
  download_available?: boolean;
  archive_error?: string | null;
  scope_override?: string | null;
};

type FacilityOption = {
  id: string;
  name: string;
};

type TagScopeOption = {
  value: string;
  scope_override?: string | null;
  facility_ids?: string[];
  facility_names?: string[];
  facility_count?: number;
};

type MenuMasterCandidate = {
  id: string;
  name: string;
  unit_type?: string | null;
  qty_per_serving?: number | string | null;
  bag_max_qty?: number | string | null;
  bag_max_unit?: string | null;
  condiments?: string[] | null;
  daypart?: string | null;
  category?: string | null;
  similarity?: number | null;
  match_score?: number | null;
  match_reason?: string | null;
};

type MenuMasterSuggestedPatch = {
  name?: string | null;
  unit_type?: string | null;
  qty_per_serving?: number | string | null;
  bag_max_qty?: number | string | null;
  bag_max_unit?: string | null;
  condiments?: string[] | null;
  daypart?: string | null;
  category?: string | null;
};

type MenuMasterReviewIssue = {
  item_id?: string | null;
  issue_key?: string | null;
  resolution_key?: string | null;
  key?: string | null;
  source_name?: string | null;
  normalized_name?: string | null;
  issue_type?: string | null;
  reason?: string | null;
  suggested_patch?: MenuMasterSuggestedPatch | null;
  candidates?: MenuMasterCandidate[] | null;
  current_master?: MenuMasterCandidate | null;
  field_diffs?: { field: string; label?: string | null; monthly_value?: string | number | null; master_value?: string | number | null }[] | null;
  condiment_issues?: { condiment_name?: string | null; reason?: string | null; message?: string | null }[] | null;
};

type ReviewResolutionAction = "" | "existing" | "create" | "update" | "month_only" | "category_only";

type MenuMasterReviewResolution = {
  issue_key: string;
  source_name: string;
  action: ReviewResolutionAction;
  menu_master_id: string;
  name: string;
  unit_type: string;
  qty_per_serving: string;
  bag_max_qty: string;
  bag_max_unit: string;
  condiments: string;
  category: string;
};

type PendingMenuMasterReview = {
  receipt: SavedMenuImport;
  saved: boolean;
  ownsInput: () => boolean;
  issues: MenuMasterReviewIssue[];
  resolutions: Record<string, MenuMasterReviewResolution>;
};

type SavedMenuImport = {
  id: string;
  month_id: string;
  state: string;
  revision: string;
  input_revision: string;
  current_input_revision: string;
  source: { filename: string; file_uri: string; content_sha256: string; sheet_name: string; media_type: string; scope_override: string | null; diet_types: string[] | null };
  review: { issues: MenuMasterReviewIssue[]; resolutions: MenuMasterReviewResolution[]; item_count: number; master_diffs: MenuMasterReviewIssue[];
    plans: Record<string, object>; items: object[]; entries: object[]; scope_override: string | null; input_snapshot: { revision: number; digest: string } };
  current_review: SavedMenuImport["review"];
};

function hasSavedImportSource(source: SavedMenuImport["source"]) {
  return !!source && ["filename", "file_uri", "content_sha256", "sheet_name", "media_type", "scope_override", "diet_types"]
    .every((key) => Object.prototype.hasOwnProperty.call(source, key))
    && [source.filename, source.file_uri, source.sheet_name, source.media_type].every((value) => typeof value === "string" && !!value.trim())
    && typeof source.content_sha256 === "string" && /^[0-9a-f]{64}$/.test(source.content_sha256)
    && (source.scope_override === null || (typeof source.scope_override === "string" && !!source.scope_override.trim()))
    && (source.diet_types === null || (Array.isArray(source.diet_types) && source.diet_types.length > 0
      && source.diet_types.every((value) => typeof value === "string" && !!value.trim())));
}

function hasSavedImportReview(review: SavedMenuImport["review"], source: SavedMenuImport["source"]) {
  const isObject = (value: unknown) => !!value && typeof value === "object" && !Array.isArray(value);
  return isObject(review) && ["issues", "resolutions", "master_diffs", "item_count", "plans", "items", "entries", "scope_override", "input_snapshot"]
    .every((key) => Object.prototype.hasOwnProperty.call(review, key))
    && [review.issues, review.resolutions, review.master_diffs, review.items, review.entries]
      .every((values) => Array.isArray(values) && values.every(isObject))
    && isObject(review.plans) && Object.values(review.plans).every(isObject)
    && Number.isInteger(review.item_count) && review.item_count >= 0 && review.scope_override === source.scope_override
    && isObject(review.input_snapshot) && Number.isInteger(review.input_snapshot.revision) && review.input_snapshot.revision > 0
    && typeof review.input_snapshot.digest === "string" && /^[0-9a-f]{64}$/.test(review.input_snapshot.digest);
}

type MenuMasterCheckState = {
  issues: MenuMasterReviewIssue[];
  resolutions: Record<string, MenuMasterReviewResolution>;
};

type PendingReviewIssueState = {
  issue: MenuMasterReviewIssue;
  index: number;
  issueKey: string;
  sourceName: string;
  complete: boolean;
  missingRequirements: string[];
};

type MenuSheetColumn = {
  key: string;
  scope_label: string;
  scope_sort: string;
  daypart: string;
  category: string;
  diet_type: string;
  slot_index: number;
};

type MenuSheetDateRow = {
  key: string;
  menu_date: string;
  cells: Record<string, MenuEntry[]>;
};

type MenuEntryExceptionDraft = {
  facilityIds: string[];
  name: string;
  unit_type: string;
  qty_per_serving: string;
  bag_max_qty: string;
  bag_max_unit: string;
  temp_type: string;
  category: string;
  diet_type: string;
};

type MenuItemDraft = {
  name: string; unit_type: string; qty_per_serving: string; temp_type: string;
  daypart: string; category: string; diet_type: string; facility_override: string;
  bag_max_qty: string; bag_max_unit: string;
};

const itemDraft = (item: MenuItem): MenuItemDraft => ({
  name: item.name || "", unit_type: item.unit_type || "", qty_per_serving: item.qty_per_serving == null ? "" : String(item.qty_per_serving),
  temp_type: item.temp_type || "", daypart: item.daypart || "", category: item.category || "", diet_type: item.diet_type || "",
  facility_override: item.facility_override || "", bag_max_qty: item.bag_max_qty == null ? "" : String(item.bag_max_qty), bag_max_unit: item.bag_max_unit || "",
});
const numberOrNull = (value: string) => value.trim() === "" ? null : Number(value);
const mergeItemDraft = (item: MenuItem, values: MenuItemDraft): MenuItem => ({ ...item, ...values, qty_per_serving: numberOrNull(values.qty_per_serving), bag_max_qty: numberOrNull(values.bag_max_qty) });

const unitChoices = [
  { value: "g", label: "グラム(g)" },
  { value: "cut", label: "切れ" },
  { value: "count", label: "個" },
];

const tempTypeChoices = [
  { value: "", label: "未選択" },
  { value: "hot", label: "温" },
  { value: "cold", label: "冷" },
];

const daypartChoices = [
  { value: "", label: "未選択" },
  { value: "朝食", label: "朝食" },
  { value: "昼食", label: "昼食" },
  { value: "夕食", label: "夕食" },
];

const daypartSortOrder: Record<string, number> = {
  朝食: 0,
  朝: 0,
  昼食: 1,
  昼: 1,
  夕食: 2,
  夕: 2,
};

const categorySortOrder: Record<string, number> = {
  主食: 0,
  主菜: 1,
  副菜: 2,
  添え: 3,
  付属: 4,
};

const uniqueValues = (items: MenuItem[], field: keyof MenuItem) => {
  const values = items
    .map((item) => item[field])
    .filter((value): value is string => typeof value === "string" && value.trim() !== "");
  return Array.from(new Set(values));
};

const formatSheetDateLabel = (value?: string | null) => {
  if (!value) return "-";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${String(parsed.getMonth() + 1).padStart(2, "0")}/${String(parsed.getDate()).padStart(2, "0")}`;
};

const formatMonthOptionLabel = (menu: MonthlyMenu) => {
  const parsed = new Date(`${menu.id}-01T00:00:00`);
  const monthLabel = Number.isNaN(parsed.getTime())
    ? menu.id
    : `${parsed.getFullYear()}年${String(parsed.getMonth() + 1).padStart(2, "0")}月`;
  if (menu.uploaded_at) {
    const uploaded = new Date(menu.uploaded_at);
    if (!Number.isNaN(uploaded.getTime())) {
      return `${monthLabel} / 最終登録 ${uploaded.toLocaleDateString("ja-JP")}`;
    }
  }
  return monthLabel;
};

const buildMenuSheetGrid = (
  entries: MenuEntry[],
  formatScopeLabel: (scopeOverride?: string | null) => string,
) => {
  const menuDates = Array.from(
    new Set(entries.map((entry) => (entry.menu_date || "").trim()).filter((value) => value.length > 0)),
  ).sort();
  const groupedColumns = new Map<string, MenuSheetColumn>();
  const groupedRows = new Map<string, MenuSheetDateRow>();
  for (const entry of entries) {
    const menuDate = (entry.menu_date || "").trim();
    if (!menuDate) continue;
    const columnKey = [
      entry.facility_override || "__base__",
      (entry.daypart || "-").trim() || "-",
      (entry.category || "-").trim() || "-",
      formatDietTypeLabel(entry.diet_type),
      String(entry.slot_index ?? 99),
    ].join("::");
    if (!groupedColumns.has(columnKey)) {
      groupedColumns.set(columnKey, {
        key: columnKey,
        scope_label: formatScopeLabel(entry.facility_override),
        scope_sort: entry.facility_override || "",
        daypart: (entry.daypart || "-").trim() || "-",
        category: (entry.category || "-").trim() || "-",
        diet_type: formatDietTypeLabel(entry.diet_type),
        slot_index: entry.slot_index ?? 99,
      });
    }

    const row =
      groupedRows.get(menuDate) ||
      {
        key: menuDate,
        menu_date: menuDate,
        cells: {},
      };
    const cellEntries = row.cells[columnKey] || [];
    cellEntries.push(entry);
    row.cells[columnKey] = cellEntries;
    groupedRows.set(menuDate, row);
  }
  const columns = Array.from(groupedColumns.values()).sort((left, right) => {
    const scopeCompare = left.scope_sort.localeCompare(right.scope_sort, "ja");
    if (scopeCompare !== 0) return scopeCompare;
    const daypartCompare = (daypartSortOrder[left.daypart] ?? 99) - (daypartSortOrder[right.daypart] ?? 99);
    if (daypartCompare !== 0) return daypartCompare;
    const categoryCompare =
      (categorySortOrder[left.category] ?? 99) - (categorySortOrder[right.category] ?? 99);
    if (categoryCompare !== 0) return categoryCompare;
    const slotCompare = left.slot_index - right.slot_index;
    if (slotCompare !== 0) return slotCompare;
    return left.diet_type.localeCompare(right.diet_type, "ja");
  });
  const rows = menuDates.map((menuDate) => groupedRows.get(menuDate) || { key: menuDate, menu_date: menuDate, cells: {} });
  return { columns, rows };
};

const normalizeValue = (value?: string | null) => (value || "").trim();

const splitGarnishMenuName = (value?: string | null) => {
  const text = normalizeValue(value);
  if (!text) return null;
  const match = text.match(/^(.*?)\s*(?:添え|添[)）:：])\s*(.+)$/);
  if (!match) return null;
  const mainName = normalizeValue(match[1]);
  const garnishName = normalizeValue(match[2]);
  if (!mainName || !garnishName) return null;
  return { mainName, garnishName };
};

const formatErrorDetail = (detail: unknown, fallback: string) => {
  const translate = (message: string) => {
    const text = message.trim();
    if (!text) return fallback;
    if (text.startsWith("menu_month_mismatch:")) {
      return "アップロードしたファイルの対象月が、開いている月と一致していません。月次メニューの月を確認してください。";
    }
    if (text === "weekday columns not found") {
      return "曜日列を読み取れませんでした。月次メニューのシート名、またはファイル形式を確認してください。";
    }
    if (text === "no menu items found" || text === "menu file is empty") {
      return "メニュー行を読み取れませんでした。対象シートと入力済み範囲を確認してください。";
    }
    if (text === "invalid menu file") {
      return "月次メニューファイルを読み取れませんでした。Excel形式のファイルを指定してください。";
    }
    return text;
  };
  if (typeof detail === "string" && detail.trim()) return translate(detail);
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return translate(message);
    const code = (detail as { code?: unknown }).code;
    if (typeof code === "string" && code.trim()) return translate(code);
  }
  return fallback;
};

const normalizeUnitChoice = (value?: string | null) => {
  const normalized = normalizeValue(value).toLowerCase();
  if (!normalized) return "";
  if (["cut", "slice", "切", "切れ", "枚"].includes(normalized)) return "cut";
  if (["count", "個", "個数", "piece", "pieces"].includes(normalized)) return "count";
  if (["g", "gram", "grams", "グラム"].includes(normalized)) return "g";
  return normalized;
};

const buildEntryExceptionDraft = (entry: MenuEntry | null, item: MenuItem | null): MenuEntryExceptionDraft | null => {
  if (!entry) return null;
  return {
    facilityIds: entry.facility_override ? [entry.facility_override] : [],
    name: entry.name || "",
    unit_type: item?.unit_type || "",
    qty_per_serving: item?.qty_per_serving == null ? "" : String(item.qty_per_serving),
    bag_max_qty: item?.bag_max_qty == null ? "" : String(item.bag_max_qty),
    bag_max_unit: item?.bag_max_unit || "",
    temp_type: item?.temp_type || "",
    category: item?.category || entry.category || "",
    diet_type: item?.diet_type || entry.diet_type || "",
  };
};

const formatUnitChoiceLabel = (value?: string | null) => {
  const normalized = normalizeUnitChoice(value);
  return unitChoices.find((choice) => choice.value === normalized)?.label || normalized || "-";
};

const toResolutionQty = (value?: string | number | null) => {
  if (value == null || value === "") return "";
  return String(value);
};

const getReviewIssueKey = (issue: MenuMasterReviewIssue, index: number) =>
  normalizeValue(issue.item_id) ||
  normalizeValue(issue.issue_key) ||
  normalizeValue(issue.resolution_key) ||
  normalizeValue(issue.key) ||
  `${normalizeValue(issue.source_name) || "issue"}::${index}`;

const getReviewSourceName = (issue: MenuMasterReviewIssue) =>
  normalizeValue(issue.source_name) || normalizeValue(issue.suggested_patch?.name) || "未登録メニュー";

const isCategoryOnlyDiffIssue = (issue: MenuMasterReviewIssue) => {
  const diffs = issue.field_diffs || [];
  return diffs.length > 0 && diffs.every((diff) => normalizeValue(diff.field) === "category");
};

const buildInitialReviewResolution = (issue: MenuMasterReviewIssue, index: number): MenuMasterReviewResolution => {
  const candidates = issue.candidates || [];
  const suggestedPatch = issue.suggested_patch || {};
  const action: ReviewResolutionAction =
    issue.issue_type === "bagging_settings_missing"
      ? "update"
      : issue.issue_type === "diff"
      ? isCategoryOnlyDiffIssue(issue)
        ? "category_only"
        : "update"
      : candidates.length === 0
        ? "create"
        : candidates.length === 1
          ? "existing"
          : "";
  const selectedCandidate = candidates.length === 1 ? candidates[0] : null;
  return {
    issue_key: getReviewIssueKey(issue, index),
    source_name: getReviewSourceName(issue),
    action,
    menu_master_id: selectedCandidate?.id || issue.current_master?.id || "",
    name: normalizeValue(suggestedPatch.name) || getReviewSourceName(issue),
    unit_type: normalizeUnitChoice(selectedCandidate?.unit_type || suggestedPatch.unit_type),
    qty_per_serving: toResolutionQty(selectedCandidate?.qty_per_serving ?? suggestedPatch.qty_per_serving ?? ""),
    bag_max_qty: toResolutionQty(selectedCandidate?.bag_max_qty ?? suggestedPatch.bag_max_qty ?? ""),
    bag_max_unit: normalizeUnitChoice(selectedCandidate?.bag_max_unit || suggestedPatch.bag_max_unit),
    condiments: (suggestedPatch.condiments || selectedCandidate?.condiments || []).join(", "),
    category: normalizeValue(String(suggestedPatch.category ?? issue.current_master?.category ?? "")),
  };
};

const buildPendingReviewResolution = (issue: MenuMasterReviewIssue, index: number): MenuMasterReviewResolution => {
  const suggestedPatch = issue.suggested_patch || {};
  return {
    issue_key: getReviewIssueKey(issue, index),
    source_name: getReviewSourceName(issue),
    action: "",
    menu_master_id: "",
    name: normalizeValue(suggestedPatch.name) || getReviewSourceName(issue),
    unit_type: "",
    qty_per_serving: "",
    bag_max_qty: "",
    bag_max_unit: "",
    condiments: (suggestedPatch.condiments || []).join(", "),
    category: normalizeValue(String(suggestedPatch.category ?? issue.current_master?.category ?? "")),
  };
};

const isReviewResolutionComplete = (
  issue: MenuMasterReviewIssue,
  resolution?: MenuMasterReviewResolution | null,
  options?: { requireBaggingSettings?: boolean },
) => {
  const requireBaggingSettings = options?.requireBaggingSettings ?? true;
  if (!resolution) return false;
  if (resolution.action === "existing") {
    return Boolean(resolution.menu_master_id);
  }
  if (resolution.action === "update") {
    const qty = Number(resolution.qty_per_serving);
    const bagQtyRaw = String(resolution.bag_max_qty ?? "").trim();
    const needsBagMax = requireBaggingSettings && ["cut", "count"].includes(normalizeUnitChoice(resolution.unit_type));
    return (
      Boolean(resolution.name.trim()) &&
      Boolean(normalizeUnitChoice(resolution.unit_type)) &&
      Number.isFinite(qty) &&
      qty > 0 &&
      (!needsBagMax || (Number.isFinite(Number(bagQtyRaw)) && Number(bagQtyRaw) > 0 && Boolean(normalizeUnitChoice(resolution.bag_max_unit))))
    );
  }
  if (resolution.action === "month_only") {
    if (!Boolean(normalizeUnitChoice(resolution.unit_type))) {
      return false;
    }
    const rawQty = String(resolution.qty_per_serving ?? "").trim();
    if (!rawQty) {
      return !requireBaggingSettings || !["cut", "count"].includes(normalizeUnitChoice(resolution.unit_type)) || (
        Number.isFinite(Number(resolution.bag_max_qty)) &&
        Number(resolution.bag_max_qty) > 0 &&
        Boolean(normalizeUnitChoice(resolution.bag_max_unit))
      );
    }
    const qty = Number(rawQty);
    const needsBagMax = requireBaggingSettings && ["cut", "count"].includes(normalizeUnitChoice(resolution.unit_type));
    return Number.isFinite(qty) && qty > 0 && (!needsBagMax || (
      Number.isFinite(Number(resolution.bag_max_qty)) &&
      Number(resolution.bag_max_qty) > 0 &&
      Boolean(normalizeUnitChoice(resolution.bag_max_unit))
    ));
  }
  if (resolution.action === "category_only") {
    return Boolean(resolution.category.trim());
  }
  if (resolution.action === "create") {
    const qty = Number(resolution.qty_per_serving);
    const needsBagMax = requireBaggingSettings && ["cut", "count"].includes(normalizeUnitChoice(resolution.unit_type));
    return (
      Boolean(resolution.name.trim()) &&
      Boolean(normalizeUnitChoice(resolution.unit_type)) &&
      Number.isFinite(qty) &&
      qty > 0 &&
      (!needsBagMax || (
        Number.isFinite(Number(resolution.bag_max_qty)) &&
        Number(resolution.bag_max_qty) > 0 &&
        Boolean(normalizeUnitChoice(resolution.bag_max_unit))
      ))
    );
  }
  if ((issue.candidates || []).length === 0) {
    return false;
  }
  return false;
};

const getPendingReviewMissingRequirements = (
  resolution?: MenuMasterReviewResolution | null,
): string[] => {
  if (!resolution?.action) return ["対応方法を選択"];
  if (resolution.action === "existing") {
    return resolution.menu_master_id ? [] : ["候補マスターを選択"];
  }
  if (resolution.action === "create" || resolution.action === "update") {
    const missing: string[] = [];
    if (!resolution.name.trim()) missing.push("メニュー名を入力");
    if (!normalizeUnitChoice(resolution.unit_type)) missing.push("基準単位を選択");
    const qty = Number(resolution.qty_per_serving);
    if (!Number.isFinite(qty) || qty <= 0) missing.push("基準量を入力");
    return missing;
  }
  if (resolution.action === "category_only") {
    return resolution.category.trim() ? [] : ["カテゴリを入力"];
  }
  if (resolution.action === "month_only") {
    const missing: string[] = [];
    if (!normalizeUnitChoice(resolution.unit_type)) missing.push("基準単位を選択");
    const qtyRaw = String(resolution.qty_per_serving ?? "").trim();
    if (qtyRaw && (!Number.isFinite(Number(qtyRaw)) || Number(qtyRaw) <= 0)) {
      missing.push("基準量を正しく入力");
    }
    return missing;
  }
  return ["対応方法を選択"];
};

const buildPendingReviewIssueStates = (
  review: PendingMenuMasterReview | null | undefined,
): PendingReviewIssueState[] => {
  if (!review) return [];
  return review.issues.map((issue, index) => {
    const issueKey = getReviewIssueKey(issue, index);
    const resolution = review.resolutions[issueKey];
    return {
      issue,
      index,
      issueKey,
      sourceName: getReviewSourceName(issue),
      complete: isReviewResolutionComplete(issue, resolution, {
        requireBaggingSettings: false,
      }),
      missingRequirements: getPendingReviewMissingRequirements(resolution),
    };
  });
};

const findMatchingItemIndex = (items: MenuItem[], entry: MenuEntry | null) => {
  if (!entry) return -1;
  const entryName = normalizeValue(entry.name);
  const entryScope = normalizeValue(entry.facility_override);
  const entryDietType = normalizeValue(entry.diet_type);
  const entryDaypart = normalizeValue(entry.daypart);
  const entryCategory = normalizeValue(entry.category);

  const exactMatches = items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) =>
      normalizeValue(item.name) === entryName &&
      normalizeValue(item.facility_override) === entryScope &&
      normalizeValue(item.daypart) === entryDaypart &&
      normalizeValue(item.category) === entryCategory &&
      normalizeValue(item.diet_type) === entryDietType,
    );
  return exactMatches.length === 1 ? exactMatches[0].index : -1;
};

function MonthlyMenuEditorScreen({ authenticated }: { authenticated: boolean }) {
  const router = useRouter();
  const { monthId } = router.query;
  const owner = useMenuInputOwner(router.asPath);
  const [revision, setRevision] = useState<string | null>(null);
  const [savedImports, setSavedImports] = useState<SavedMenuImport[]>([]);
  const [menu, setMenu] = useState<MonthlyMenu | null>(null);
  const [items, setItems] = useState<MenuItem[]>([]);
  const [entries, setEntries] = useState<MenuEntry[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState<string>("");
  const [scopeType, setScopeType] = useState<string>("base");
  const [scopeValue, setScopeValue] = useState<string>("");
  const [importDiets, setImportDiets] = useState<string[]>([]);
  const [message, setMessage] = useState<string>("");
  const [lastUpload, setLastUpload] = useState<string>("");
  const [uploadHistory, setUploadHistory] = useState<MenuUploadEntry[]>([]);
  const [uploadHistoryPage, setUploadHistoryPage] = useState(0);
  const [uploadHistoryRowsPerPage, setUploadHistoryRowsPerPage] = useState(25);
  const [uploading, setUploading] = useState<boolean>(false);
  const [downloadingUploadId, setDownloadingUploadId] = useState<string | null>(null);
  const [facilities, setFacilities] = useState<FacilityOption[]>([]);
  const [tagOptions, setTagOptions] = useState<TagScopeOption[]>([]);
  const [condimentFile, setCondimentFile] = useState<File | null>(null);
  const [condimentUploading, setCondimentUploading] = useState<boolean>(false);
  const [condimentMessage, setCondimentMessage] = useState<string>("");
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [editorSelection, setEditorSelection] = useState<{ month_id: string; entry: MenuEntry; item: MenuItem | null } | null>(null);
  const [uploadInputKey, setUploadInputKey] = useState<number>(0);
  const [pendingReview, setPendingReview] = useState<PendingMenuMasterReview | null>(null);
  const [reviewSubmitting, setReviewSubmitting] = useState<boolean>(false);
  const [masterCheckState, setMasterCheckState] = useState<MenuMasterCheckState>({ issues: [], resolutions: {} });
  const [masterCheckSavingId, setMasterCheckSavingId] = useState<string | null>(null);
  const [masterCheckNotice, setMasterCheckNotice] = useState<string>("");
  const [entryExceptionDraft, setEntryExceptionDraft] = useState<MenuEntryExceptionDraft | null>(null);
  const [entryExceptionSaving, setEntryExceptionSaving] = useState<boolean>(false);
  const [exceptionReceipt, setExceptionReceipt] = useState<{ month_id: string; entry_id: string; entries: MenuEntry[] } | null>(null);
  const [selectedMonth, setSelectedMonth] = useState<string>("");
  const [menuOptions, setMenuOptions] = useState<MonthlyMenu[]>([]);
  const [highlightedReviewIssueKey, setHighlightedReviewIssueKey] = useState<string | null>(null);
  const [pendingReviewNotice, setPendingReviewNotice] = useState<string>("");
  const [itemFormDirty, setItemFormDirty] = useState(false);
  const [exceptionDirty, setExceptionDirty] = useState(false);
  const itemRevision = useRef<string | null>(null);
  const exceptionRevision = useRef<string | null>(null);
  const masterRevisions = useRef<Record<string, string | null>>({});
  const masterItems = useRef<Record<string, MenuItem | null>>({});
  const [itemEditGeneration, setItemEditGeneration] = useState(0);
  const [selectedMasterCheckKey, setSelectedMasterCheckKey] = useState<string | null>(null);
  const [masterCheckPage, setMasterCheckPage] = useState(0);
  const [masterCheckRowsPerPage, setMasterCheckRowsPerPage] = useState(25);
  const reviewIssueRefs = useRef<Record<string, HTMLElement | null>>({});
  const menuLoadGeneration = useRef(0);
  const itemSaveGeneration = useRef(0);
  const selectedEntryRef = useRef<string | null>(null);
  const hasUnsavedChanges = itemFormDirty || exceptionDirty || entryExceptionSaving || Boolean(file)
    || Object.keys(masterRevisions.current).length > 0 || Boolean(pendingReview && !pendingReview.saved);
  useH1LeaveGuard(hasUnsavedChanges, ["/menus", "/hospital/menus"]);

  const formatScopeLabel = (scopeOverride?: string | null) => {
    const value = (scopeOverride || "").trim();
    if (!value) return "共通(base)";
    if (value.startsWith("TAG:")) {
      return `タグ:${value.slice(4)}`;
    }
    const facility = facilities.find((item) => item.id === value);
    return facility ? `施設:${facility.name}` : `施設:${value}`;
  };

  const getMasterCheckScopeLabel = (issue?: MenuMasterReviewIssue | null) => {
    const itemId = normalizeValue(issue?.item_id);
    if (!itemId) return "共通(base)";
    const item = masterRevisions.current[itemId] !== undefined
      ? masterItems.current[itemId] : items.find((row) => normalizeValue(row.id) === itemId);
    if (!item) return "適用先を確認できません";
    return formatScopeLabel(item?.facility_override);
  };

  const selectedEntry = (itemFormDirty || exceptionDirty) && editorSelection?.month_id === monthId && editorSelection?.entry.id === selectedEntryId
    ? editorSelection.entry : entries.find((entry) => entry.id === selectedEntryId) || null;
  const selectedItemIndex = findMatchingItemIndex(items, selectedEntry);
  const selectedItem = itemFormDirty && editorSelection?.month_id === monthId && editorSelection?.entry.id === selectedEntryId
    ? editorSelection.item : selectedItemIndex >= 0 ? items[selectedItemIndex] : null;
  selectedEntryRef.current = selectedEntryId;
  const selectEntry = (entryId: string | null) => {
    if (typeof monthId !== "string") return;
    if (entryId !== null && !entries.some((entry) => entry.id === entryId)) return;
    if (entryId === selectedEntryRef.current) return;
    if ((itemFormDirty || exceptionDirty) && !window.confirm("未保存の変更を破棄して献立を切り替えますか？")) return;
    owner.changed();
    itemRevision.current = revision;
    exceptionRevision.current = revision;
    setExceptionDirty(false);
    setExceptionReceipt(null);
    itemSaveGeneration.current += 1;
    setItemFormDirty(false);
    setItemEditGeneration((current) => current + 1);
    const entry = entries.find((candidate) => candidate.id === entryId);
    const itemIndex = findMatchingItemIndex(items, entry || null);
    setEditorSelection(entry ? { month_id: monthId, entry, item: itemIndex >= 0 ? items[itemIndex] : null } : null);
    setSelectedEntryId(entryId);
  };
  const itemColumns: readonly TableColumn<MenuItem>[] = [
    { id: "name", label: "献立名", render: (item) => item.name }, { id: "scope", label: "適用先", render: (item) => formatScopeLabel(item.facility_override) },
    { id: "daypart", label: "朝昼夕", render: (item) => item.daypart || "-" }, { id: "category", label: "区分", render: (item) => item.category || "-" },
    { id: "diet", label: "食種", render: (item) => formatDietTypeLabel(item.diet_type || "") || "-" }, { id: "qty", label: "量 / 単位", render: (item) => item.qty_per_serving == null ? "-" : `${item.qty_per_serving}${item.unit_type || ""}` },
    { id: "bag", label: "袋上限", render: (item) => item.bag_max_qty == null ? "-" : `${item.bag_max_qty}${item.bag_max_unit || ""}` },
    { id: "edit", label: "", render: (item) => <button className="btn compact" onClick={() => {
      const targets = entries.filter((entry) => findMatchingItemIndex(items, entry) >= 0 && items[findMatchingItemIndex(items, entry)]?.id === item.id);
      if (targets.length !== 1) { setMessage("シートで編集する位置を選択してください。"); return; }
      selectEntry(targets[0].id);
    }}>編集</button> },
  ];
  const selectedGarnish = splitGarnishMenuName(selectedEntry?.name);
  const selectedGarnishItem = selectedGarnish
    ? items.find((item) => normalizeValue(item.name) === selectedGarnish.garnishName)
    : null;

  const loadUploadHistory = async (expectedGeneration?: number) => {
    if (!monthId || Array.isArray(monthId)) return;
    const current = owner.capture("history", false);
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/uploads`);
      if (!current()) return;
      if (expectedGeneration != null && expectedGeneration !== menuLoadGeneration.current) return;
      setUploadHistory(res.data?.items || []);
    } catch {
      if (!current()) return;
      if (expectedGeneration != null && expectedGeneration !== menuLoadGeneration.current) return;
      setUploadHistory([]);
    }
  };

  const loadScopeOptions = async () => {
    const current = owner.capture("scope-options", false);
    try {
      const res = await apiClient.get("/monthly-menus/scope-options");
      if (!current()) return;
      setFacilities(res.data?.facilities || []);
      setTagOptions(res.data?.tags || []);
    } catch {
      if (!current()) return;
      setFacilities([]);
      setTagOptions([]);
    }
  };

  const loadRecentMenus = async () => {
    const current = owner.capture("recent", false);
    try {
      const res = await apiClient.get("/monthly-menus", { params: { limit: 12 } });
      if (!current()) return;
      setMenuOptions(res.data?.items || []);
    } catch {
      if (!current()) return;
      setMenuOptions([]);
    }
  };

  const adoptSnapshot = (data: any, command?: "item" | "exceptions" | "master") => {
    if (!data || !Array.isArray(data.items) || !Array.isArray(data.entries)
      || !Array.isArray(data.master_checks?.issues) || typeof data.revision !== "string") {
      throw new Error("月次メニューと入力版の応答を確認できませんでした。");
    }
    setMenu(data.menu);
    setItems(data.items);
    setEntries(data.entries);
    setRevision(data.revision);
    if (command === "item" || (!itemFormDirty && !command)) itemRevision.current = data.revision;
    if (command === "exceptions" || (!exceptionDirty && !command)) exceptionRevision.current = data.revision;
    const issues: MenuMasterReviewIssue[] = data.master_checks.issues;
    setMasterCheckState((previous) => {
      const working = previous.issues.flatMap((issue, index) => {
        const key = getReviewIssueKey(issue, index);
        return masterRevisions.current[key] !== undefined ? [{ ...issue, issue_key: key }] : [];
      });
      const workingKeys = new Set(working.map((issue, index) => getReviewIssueKey(issue, index)));
      const nextIssues = [...working, ...issues.filter((issue: MenuMasterReviewIssue, index: number) =>
        !workingKeys.has(getReviewIssueKey(issue, index)))];
      return { issues: nextIssues, resolutions: Object.fromEntries(nextIssues.map((issue, index) => {
        const resolution = buildInitialReviewResolution(issue, index);
        return [resolution.issue_key, workingKeys.has(resolution.issue_key)
          ? previous.resolutions[resolution.issue_key] : resolution];
      })) };
    });
    owner.changed();
  };

  const loadSavedImports = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    const current = owner.capture("imports", false);
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/imports`);
      if (!current()) return;
      if (!Array.isArray(res.data?.items)) throw new Error("保存importの応答を確認できませんでした。");
      if (res.data.items.some((receipt: SavedMenuImport) => receipt.month_id !== monthId || !hasSavedImportSource(receipt.source)
        || !hasSavedImportReview(receipt.review, receipt.source) || !hasSavedImportReview(receipt.current_review, receipt.source))) {
        throw new Error("保存原本のscope・食種を確認できませんでした。");
      }
      setSavedImports(res.data.items);
    } catch (error: any) {
      if (current()) setMessage(formatErrorDetail(error?.response?.data?.detail, "保存importの読込に失敗しました。"));
    }
  };

  const loadMenu = async (replaceWorking = false) => {
    if (!monthId || Array.isArray(monthId)) return;
    const generation = menuLoadGeneration.current + 1;
    menuLoadGeneration.current = generation;
    const current = owner.capture("menu-read");
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}`, { params: { editor: true } });
      if (!current() || generation !== menuLoadGeneration.current) return;
      if (!Array.isArray(res.data.items) || !Array.isArray(res.data.entries)
        || !Array.isArray(res.data.master_checks?.issues) || typeof res.data.revision !== "string") {
        throw new Error("月次メニュー正本の応答形式を確認できませんでした。");
      }
      if (replaceWorking) {
        masterRevisions.current = {};
        masterItems.current = {};
        itemRevision.current = res.data.revision;
        exceptionRevision.current = res.data.revision;
        setItemFormDirty(false);
        setExceptionDirty(false);
        setItemEditGeneration((value) => value + 1);
        const entry = res.data.entries.find((value: MenuEntry) => value.id === selectedEntryRef.current) || null;
        const index = findMatchingItemIndex(res.data.items, entry);
        setEntryExceptionDraft(buildEntryExceptionDraft(entry, index >= 0 ? res.data.items[index] : null));
      }
      adoptSnapshot(res.data);
      const issues = res.data?.master_checks?.issues;
      if (!Array.isArray(issues)) throw new Error("メニューマスター差分の応答形式を確認できませんでした。");
      setSelectedMasterCheckKey((current) => current && (
        masterRevisions.current[current] !== undefined
        || issues.some((issue: MenuMasterReviewIssue, index: number) => getReviewIssueKey(issue, index) === current)
      ) ? current : null);
      setMasterCheckPage(0);
      setMessage("");
    } catch (err: any) {
      if (!current() || generation !== menuLoadGeneration.current) return;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("権限がありません。月次メニューの操作にはユーザー2以上の権限が必要です。");
      } else if (status === 404) {
        setMessage("月次メニューがまだ登録されていません。");
      } else {
        setMessage("月次メニューの読込に失敗しました。");
      }
    }
    await loadUploadHistory(generation);
  };

  const reloadMonthlyEditor = () => {
    if (hasUnsavedChanges && !window.confirm("未保存の変更を破棄して月次メニューを再読込しますか？")) return;
    owner.changed();
    void loadMenu(true);
  };

  useEffect(() => {
    loadMenu();
    loadScopeOptions();
    loadRecentMenus();
    loadSavedImports();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monthId]);

  useEffect(() => {
    setMasterCheckNotice("");
    if (monthId && !Array.isArray(monthId)) {
      setSelectedMonth(monthId);
    }
  }, [monthId]);

  const handleMonthChange = (nextMonth: string) => {
    owner.changed();
    setSelectedMonth(nextMonth);
    if (/^\d{4}-\d{2}$/.test(nextMonth)) {
      router.push(`/menus/${nextMonth}`);
    }
  };

  useEffect(() => {
    if (selectedEntryId && !entries.some((entry) => entry.id === selectedEntryId) && !itemFormDirty && !exceptionDirty) {
      selectEntry(null);
    }
  }, [entries, selectedEntryId, itemFormDirty, exceptionDirty]);

  useEffect(() => {
    setEntryExceptionDraft(buildEntryExceptionDraft(selectedEntry, selectedItem));
  }, [selectedEntry?.id, selectedItem?.id]);

  const handleUpload = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    if (!file) {
      setMessage("アップロードするファイルを選択してください。");
      return;
    }
    if (scopeType !== "base" && !scopeValue.trim()) {
      setMessage(scopeType === "facility" ? "施設差分を登録する施設を選択してください。" : "タグ差分のタグを選択してください。");
      return;
    }
    if ((itemFormDirty || exceptionDirty) && !window.confirm("未保存の編集を破棄してimportを開始しますか？")) return;
    const current = owner.capture("upload");
    const targetFile = file;
    setUploading(true);
    const formData = new FormData();
    formData.append("file", targetFile);
    try {
      const res = await apiClient.post("/monthly-menus", formData, {
        params: {
          month_id: monthId,
          sheet_name: sheetName || undefined,
          scope_type: scopeType,
          scope_value: scopeType === "base" ? undefined : scopeValue || undefined,
          diet_types: importDiets.length ? JSON.stringify(importDiets) : undefined,
        },
        headers: { "Content-Type": "multipart/form-data" },
      });
      if (!current()) return;
      adoptImport(res.data);
      setSavedImports((rows) => [res.data, ...rows.filter((row) => row.id !== res.data.id)]);
      setMessage(`${targetFile.name} の原本を保存しました。差分を確認して明示反映してください。`);
    } catch (err: any) {
      if (!current()) return;
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("アップロード失敗: 権限がありません。");
      } else {
        setMessage(`アップロード失敗: ${formatErrorDetail(detail, "アップロードに失敗しました。")}`);
      }
    } finally {
      if (current.active()) setUploading(false);
    }
  };

  const adoptImport = (receipt: SavedMenuImport) => {
    if (receipt.month_id !== monthId || !receipt.id || !receipt.revision || !hasSavedImportSource(receipt.source)
      || !hasSavedImportReview(receipt.review, receipt.source) || !hasSavedImportReview(receipt.current_review, receipt.source)
      || !receipt.current_input_revision || !receipt.input_revision
      || !["review", "applied"].includes(receipt.state)) {
      throw new Error("保存原本と差分の対応を確認できませんでした。");
    }
    const saved = Object.fromEntries(receipt.current_review.resolutions.map((resolution) => [resolution.issue_key, resolution]));
    const resolutions = Object.fromEntries(receipt.current_review.issues.map((issue, index) => {
      const resolution = buildPendingReviewResolution(issue, index);
      return [resolution.issue_key, saved[resolution.issue_key] || resolution];
    }));
    owner.changed();
    setPendingReview({ receipt, saved: receipt.current_input_revision === receipt.input_revision,
      ownsInput: owner.checkpoint(), issues: receipt.current_review.issues, resolutions });
    setPendingReviewNotice("");
  };

  const resumeImport = async (receiptId: string) => {
    if (!monthId || Array.isArray(monthId)) return;
    if (hasUnsavedChanges && !window.confirm("未保存の変更を破棄して保存importを開きますか？")) return;
    owner.changed();
    const current = owner.capture("resume");
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/imports/${receiptId}`);
      if (!current()) return;
      adoptImport(res.data);
    } catch (error: any) {
      if (current()) setMessage(formatErrorDetail(error?.response?.data?.detail, "保存importを確認できませんでした。"));
    }
  };

  const downloadImport = async (receipt: SavedMenuImport) => {
    if (!hasSavedImportSource(receipt.source) || receipt.month_id !== monthId) {
      setMessage("保存原本の対象を確認できませんでした。");
      return;
    }
    if (receipt.month_id !== monthId) return;
    const current = owner.capture("import-download");
    try {
      const res = await apiClient.get(`/monthly-menus/${receipt.month_id}/imports/${receipt.id}/download`, { responseType: "blob" });
      if (!current()) return;
      const url = URL.createObjectURL(res.data);
      try {
        const link = document.createElement("a");
        link.href = url;
        link.download = receipt.source.filename;
        link.click();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (error: any) {
      if (current()) setMessage(formatErrorDetail(error?.response?.data?.detail, "保存原本を取得できませんでした。"));
    }
  };

  const updateReviewResolution = (issueKey: string, patch: Partial<MenuMasterReviewResolution>) => {
    owner.changed();
    const ownsInput = owner.checkpoint();
    setPendingReviewNotice("");
    setPendingReview((current) => {
      if (!current) return current;
      const existing = current.resolutions[issueKey];
      if (!existing) return current;
      return {
        ...current,
        saved: false,
        ownsInput,
        resolutions: {
          ...current.resolutions,
          [issueKey]: { ...existing, ...patch },
        },
      };
    });
  };

  const updateMasterCheckResolution = (issueKey: string, patch: Partial<MenuMasterReviewResolution>) => {
    owner.changed();
    if (masterRevisions.current[issueKey] === undefined) {
      masterRevisions.current[issueKey] = revision;
      const item = items.find((row) => row.id === issueKey);
      masterItems.current[issueKey] = item ? { ...item } : null;
    }
    setMasterCheckState((current) => {
      const existing = current.resolutions[issueKey];
      if (!existing) return current;
      return {
        ...current,
        resolutions: {
          ...current.resolutions,
          [issueKey]: { ...existing, ...patch },
        },
      };
    });
  };

  const closePendingReview = () => {
    if (reviewSubmitting) return;
    owner.changed();
    setPendingReview(null);
    setHighlightedReviewIssueKey(null);
    setPendingReviewNotice("");
  };

  const applyPendingReviewIssues = (receipt: SavedMenuImport, issues: MenuMasterReviewIssue[]) => {
    const filteredIssues = issues.filter(Boolean);
    const resolutions = Object.fromEntries(
      filteredIssues.map((issue, index) => {
        const nextResolution = buildPendingReviewResolution(issue, index);
        const existingResolution = pendingReview?.resolutions[nextResolution.issue_key];
        return [nextResolution.issue_key, existingResolution || nextResolution];
      }),
    );
    setPendingReview({
      receipt,
      saved: false,
      ownsInput: owner.checkpoint(),
      issues: filteredIssues,
      resolutions,
    });
    setPendingReviewNotice("");
  };

  const focusPendingReviewIssue = (issueKey: string) => {
    const target = reviewIssueRefs.current[issueKey];
    if (!target) return;
    setHighlightedReviewIssueKey(issueKey);
    target.scrollIntoView({ block: "center" });
    target.focus();
  };

  const confirmPendingReview = async (apply = false) => {
    if (!monthId || Array.isArray(monthId) || !pendingReview) return;
    if (!pendingReview.ownsInput()) {
      setPendingReviewNotice("対象または別の編集中入力が変わりました。保存importを明示的に開き直してください。編集中の値は保持しています。");
      return;
    }
    const issueStates = buildPendingReviewIssueStates(pendingReview);
    const incompleteIssues = issueStates.filter((issueState) => !issueState.complete);
    const unresolved = incompleteIssues[0];
    if (unresolved) {
      const notice = `未登録メニューの確認が${incompleteIssues.length}件残っています。最初の未完了項目: ${unresolved.sourceName}（${unresolved.missingRequirements.join("、")}）`;
      setMessage(notice);
      setPendingReviewNotice(notice);
      focusPendingReviewIssue(unresolved.issueKey);
      return;
    }
    setReviewSubmitting(true);
    const current = owner.capture("review");
    const receipt = pendingReview.receipt;
    if (apply && !pendingReview.saved) {
      setReviewSubmitting(false);
      setMessage("この差分設定を保存してから反映してください。");
      return;
    }
    try {
      const res = await apiClient.post(`/monthly-menus/${monthId}/imports/${receipt.id}/${apply ? "apply" : "review"}`,
        apply ? undefined : { resolutions: Object.values(pendingReview.resolutions), input_revision: receipt.current_input_revision },
        { headers: { "If-Match": receipt.revision } });
      if (!current()) return;
      if (!apply) {
        adoptImport(res.data);
        setMessage("差分設定を保存しました。対象と件数を確認して反映してください。");
      } else {
        adoptSnapshot(res.data.snapshot);
        setFile(null);
        setPendingReview(null);
        setUploadInputKey((prev) => prev + 1);
        setLastUpload(`${receipt.source.filename} を反映（${res.data.item_count}件 / ${formatScopeLabel(receipt.source.scope_override)}）`);
        setMessage("保存済みimportを反映しました。");
        void loadUploadHistory();
      }
    } catch (err: any) {
      if (!current()) return;
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("アップロード失敗: 権限がありません。");
      } else if (
        status === 409 &&
        detail &&
        typeof detail === "object" &&
        (detail as { code?: string }).code === "menu_master_review_required" &&
        Array.isArray((detail as { issues?: unknown[] }).issues)
      ) {
        const issues = ((detail as { issues?: MenuMasterReviewIssue[] }).issues || []).filter(Boolean);
        applyPendingReviewIssues(receipt, issues);
        setMessage("未登録メニューの確認がまだ残っています。表示された項目を確認してください。");
      } else {
        setMessage(`アップロード失敗: ${formatErrorDetail(detail, "アップロードに失敗しました。")}`);
      }
    } finally {
      if (current.active()) setReviewSubmitting(false);
    }
  };

  const applyMasterCheck = async (issue: MenuMasterReviewIssue, index: number) => {
    if (!monthId || Array.isArray(monthId) || !issue.item_id) return;
    const issueKey = getReviewIssueKey(issue, index);
    if (masterRevisions.current[issueKey] !== undefined && !masterItems.current[issueKey]) {
      setMessage("差分の捕捉済み適用先を確認できません。編集中の入力は保持しています。");
      return;
    }
    if (masterRevisions.current[issueKey] !== undefined && masterRevisions.current[issueKey] !== revision) {
      setMessage("月次メニューの入力版が変わりました。差分の編集中入力は保持しています。正本を明示的に読込し直して確認してください。");
      return;
    }
    const resolution = masterCheckState.resolutions[issueKey];
    if (!isReviewResolutionComplete(issue, resolution)) {
      setMessage(`差分チェックの入力が不足しています: ${getReviewSourceName(issue)}`);
      return;
    }
    setMasterCheckSavingId(issue.item_id);
    const current = owner.capture("master");
    try {
      const previousCount = masterCheckState.issues.length;
      const body: Record<string, unknown> = {
        action: resolution.action,
      };
      if (resolution.action === "existing") {
        body.menu_master_id = resolution.menu_master_id;
      } else if (resolution.action === "category_only") {
        body.category = resolution.category.trim();
        if (issue.current_master?.id) {
          body.menu_master_id = issue.current_master.id;
        }
      } else {
        body.name = resolution.name;
        body.unit_type = normalizeUnitChoice(resolution.unit_type);
        const rawQty = String(resolution.qty_per_serving ?? "").trim();
        if (resolution.action !== "month_only" || rawQty) {
          body.qty_per_serving = Number(rawQty);
        }
        const rawBagQty = String(resolution.bag_max_qty ?? "").trim();
        if (rawBagQty) {
          body.bag_max_qty = Number(rawBagQty);
        }
        if (normalizeUnitChoice(resolution.bag_max_unit)) {
          body.bag_max_unit = normalizeUnitChoice(resolution.bag_max_unit);
        }
        const condiments = resolution.condiments
          .split(",")
          .map((part) => part.trim())
          .filter((part) => part.length > 0);
        if (condiments.length > 0) {
          body.condiments = condiments;
        }
        if (resolution.category.trim()) {
          body.category = resolution.category.trim();
        }
        if (issue.current_master?.id) {
          body.menu_master_id = issue.current_master.id;
        }
      }
      const res = await apiClient.post(`/monthly-menus/${monthId}/master-checks/${issue.item_id}/resolve`, body,
        { headers: { "If-Match": masterRevisions.current[issueKey] === undefined ? revision : masterRevisions.current[issueKey] } });
      if (!current()) return;
      delete masterRevisions.current[issueKey];
      delete masterItems.current[issueKey];
      adoptSnapshot(res.data.snapshot, "master");
      const sourceName = getReviewSourceName(issue);
      const successMessage =
        resolution.action === "month_only"
          ? `当月だけ反映しました: ${sourceName}`
          : resolution.action === "category_only"
            ? `区分だけメニューマスターへ反映しました: ${sourceName}`
            : issue.issue_type === "diff"
              ? `メニューマスターを更新しました: ${sourceName}`
              : `メニューマスターへ登録しました: ${sourceName}`;
      const resolvedIssueKey = getReviewIssueKey(issue, index);
      setMasterCheckState((current) => {
        const nextIssues = current.issues.filter((currentIssue, currentIndex) => {
          return getReviewIssueKey(currentIssue, currentIndex) !== resolvedIssueKey;
        });
        const nextResolutions = Object.fromEntries(
          nextIssues.map((currentIssue, nextIndex) => {
            const currentIssueKey = getReviewIssueKey(currentIssue, nextIndex);
            const existing = current.resolutions[currentIssueKey];
            return [currentIssueKey, existing || buildInitialReviewResolution(currentIssue, nextIndex)];
          }),
        );
        return {
          issues: nextIssues,
          resolutions: nextResolutions,
        };
      });
      setMasterCheckNotice(`${successMessage}（${previousCount}件 → ${Math.max(previousCount - 1, 0)}件）`);
      setMessage(successMessage);
    } catch (err: any) {
      if (!current()) return;
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("差分反映に失敗しました: 権限がありません。");
      } else {
        setMessage(`差分反映に失敗しました: ${formatErrorDetail(detail, "差分反映に失敗しました。")}`);
      }
    } finally {
      if (current.active()) setMasterCheckSavingId(null);
    }
  };

  const handleDownloadUpload = async (upload: MenuUploadEntry) => {
    if (!monthId || Array.isArray(monthId)) return;
    setDownloadingUploadId(upload.id);
    const current = owner.capture("download");
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/uploads/${upload.id}/download`, {
        responseType: "blob",
      });
      if (!current()) return;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const disposition = String(res.headers?.["content-disposition"] || "");
      const matched = disposition.match(/filename=\"?([^\";]+)\"?/i);
      const filename = matched?.[1] || upload.filename || "monthly-menu.xlsx";
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      if (!current()) return;
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    } finally {
      if (current.active()) setDownloadingUploadId(null);
    }
  };

  const handleCondimentUpload = async () => {
    if (!condimentFile) {
      setCondimentMessage("付属品フラグのファイルを選択してください。");
      return;
    }
    setCondimentUploading(true);
    const current = owner.capture("condiment");
    setCondimentMessage("アップロード中...");
    const formData = new FormData();
    formData.append("file", condimentFile);
    try {
      const res = await apiClient.post("/monthly-menus/condiments", formData, {
        params: { sheet_name: "主菜" },
        headers: { "Content-Type": "multipart/form-data" },
      });
      if (!current()) return;
      const itemsCount = res.data?.items ?? 0;
      setCondimentMessage(`付属品フラグを反映しました（${itemsCount}件）。`);
      setCondimentFile(null);
    } catch (err: any) {
      if (!current()) return;
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setCondimentMessage("反映に失敗しました: 権限がありません。");
      } else {
        setCondimentMessage(detail ? `反映に失敗しました: ${detail}` : "反映に失敗しました。");
      }
    } finally {
      if (current.active()) setCondimentUploading(false);
    }
  };

  const saveItem = async (item: MenuItem): Promise<MenuItem> => {
    if (!monthId || Array.isArray(monthId)) throw new Error("対象月が確定していません。");
    const generation = itemSaveGeneration.current + 1;
    itemSaveGeneration.current = generation;
    const selectedAtSaveStart = selectedEntryRef.current;
    const menuAtSaveStart = menuLoadGeneration.current;
    const monthAtSaveStart = monthId;
    const current = owner.capture("item");
    const qtyValue =
      item.qty_per_serving == null || item.qty_per_serving === ""
        ? null
        : Number(item.qty_per_serving);
    const bagMaxValue =
      item.bag_max_qty == null || item.bag_max_qty === "" ? null : Number(item.bag_max_qty);
    try {
      const result = await apiClient.put(`/monthly-menus/${monthId}/items/${item.id}`, {
        name: item.name,
        unit_type: item.unit_type,
        qty_per_serving: qtyValue,
        temp_type: item.temp_type,
        daypart: item.daypart,
        category: item.category,
        diet_type: item.diet_type,
        facility_override: item.facility_override,
        bag_max_qty: bagMaxValue,
        bag_max_unit: item.bag_max_unit,
      }, { headers: { "If-Match": itemRevision.current } });
      if (result.data?.updated !== true) throw new Error("メニュー保存の応答を確認できませんでした。");
      if (!current() || generation !== itemSaveGeneration.current || selectedAtSaveStart !== selectedEntryRef.current || menuAtSaveStart !== menuLoadGeneration.current || monthAtSaveStart !== monthId) {
        throw new Error("選択変更後の保存応答を適用しませんでした。");
      }
      const fresh = { data: result.data.snapshot };
      if (!Array.isArray(fresh.data?.items)) throw new Error("保存後の月次メニュー正本を確認できませんでした。");
      const saved = fresh.data.items.find((current: MenuItem) => current.id === item.id);
      if (!saved || generation !== itemSaveGeneration.current || selectedAtSaveStart !== selectedEntryRef.current || menuAtSaveStart !== menuLoadGeneration.current || monthAtSaveStart !== monthId) {
        throw new Error("保存後の項目を正本から確認できませんでした。");
      }
      adoptSnapshot(fresh.data, "item");
      setMessage(`保存しました: ${item.name}`);
      return saved;
    } catch (err: any) {
      if (!current() || generation !== itemSaveGeneration.current || selectedAtSaveStart !== selectedEntryRef.current || menuAtSaveStart !== menuLoadGeneration.current || monthAtSaveStart !== monthId) {
        throw new Error("選択変更後の保存応答を適用しませんでした。");
      }
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("保存に失敗しました: 権限がありません。");
      } else {
        setMessage(detail ? `保存に失敗しました: ${detail}` : "保存に失敗しました。");
      }
      throw err;
    }
  };

  const updateEntryExceptionField = (field: keyof Omit<MenuEntryExceptionDraft, "facilityIds">, value: string) => {
    owner.changed();
    setExceptionDirty(true);
    setEntryExceptionDraft((current) => {
      if (!current) return current;
      return { ...current, [field]: value };
    });
  };

  const toggleEntryExceptionFacility = (facilityId: string) => {
    owner.changed();
    setExceptionDirty(true);
    setEntryExceptionDraft((current) => {
      if (!current) return current;
      const exists = current.facilityIds.includes(facilityId);
      return {
        ...current,
        facilityIds: exists
          ? current.facilityIds.filter((item) => item !== facilityId)
          : [...current.facilityIds, facilityId],
      };
    });
  };

  const saveEntryException = async () => {
    if (!monthId || Array.isArray(monthId) || !selectedEntry || !entryExceptionDraft) return;
    if (entryExceptionDraft.facilityIds.length === 0) {
      setMessage("例外メニューの対象施設を1つ以上選択してください。");
      return;
    }
    if (!entryExceptionDraft.name.trim()) {
      setMessage("例外メニュー名を入力してください。");
      return;
    }
    setEntryExceptionSaving(true);
    const current = owner.capture("exceptions");
    try {
      const res = await apiClient.post(
        `/monthly-menus/${monthId}/entries/${selectedEntry.id}/exceptions`,
        {
          facility_ids: entryExceptionDraft.facilityIds,
          name: entryExceptionDraft.name.trim(),
          unit_type: entryExceptionDraft.unit_type || null,
          qty_per_serving:
            entryExceptionDraft.qty_per_serving.trim() === ""
              ? null
              : Number(entryExceptionDraft.qty_per_serving),
          bag_max_qty:
            entryExceptionDraft.bag_max_qty.trim() === ""
              ? null
              : Number(entryExceptionDraft.bag_max_qty),
          bag_max_unit: entryExceptionDraft.bag_max_unit || null,
          temp_type: entryExceptionDraft.temp_type || null,
          category: entryExceptionDraft.category.trim() || null,
          diet_type: entryExceptionDraft.diet_type || null,
        },
        { headers: { "If-Match": exceptionRevision.current } },
      );
      if (!current()) return;
      const updatedEntries = Array.isArray(res.data?.result?.entries) ? res.data.result.entries : [];
      if (res.data?.result?.entry_id !== selectedEntry.id || updatedEntries.length !== entryExceptionDraft.facilityIds.length
        || updatedEntries.some((entry: MenuEntry) => typeof entry.id !== "string" || !entry.id
          || typeof entry.name !== "string" || !entry.name || !entry.facility_override
          || !entryExceptionDraft.facilityIds.includes(entry.facility_override))
        || new Set(updatedEntries.map((entry: MenuEntry) => entry.facility_override)).size !== updatedEntries.length) {
        throw new Error("例外保存の対象を確認できませんでした。");
      }
      adoptSnapshot(res.data.snapshot, "exceptions");
      setExceptionDirty(false);
      setExceptionReceipt({ month_id: monthId, entry_id: selectedEntry.id, entries: updatedEntries });
      setMessage(`例外メニューを保存しました: ${entryExceptionDraft.name.trim()}`);
    } catch (err: any) {
      if (!current()) return;
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("例外メニューの保存に失敗しました: 権限がありません。");
      } else {
        setMessage(
          detail
            ? `例外メニューの保存に失敗しました: ${formatErrorDetail(detail, "例外メニューの保存に失敗しました。")}`
            : "例外メニューの保存に失敗しました。",
        );
      }
    } finally {
      if (current.active()) setEntryExceptionSaving(false);
    }
  };

  const daypartOptions = uniqueValues(items, "daypart");
  const categoryOptions = uniqueValues(items, "category");
  const { columns: sheetColumns, rows: sheetDateRows } = buildMenuSheetGrid(entries, formatScopeLabel);
  const pendingReviewIssueStates = buildPendingReviewIssueStates(pendingReview);
  const incompletePendingReviewIssues = pendingReviewIssueStates.filter((issueState) => !issueState.complete);
  const canSubmitPendingReview =
    Boolean(pendingReview) && incompletePendingReviewIssues.length === 0;
  const uploadHistoryColumns: readonly TableColumn<MenuUploadEntry>[] = [
    { id: "uploaded", label: "登録日時", render: (upload) => upload.uploaded_at ? new Date(upload.uploaded_at).toLocaleString("ja-JP") : "-" },
    { id: "filename", label: "ファイル名", render: (upload) => upload.filename || "-" },
    { id: "sheet", label: "シート名", render: (upload) => upload.sheet_name || "-" },
    { id: "scope", label: "適用先", render: (upload) => formatScopeLabel(upload.scope_override) },
    { id: "count", label: "件数", align: "right", render: (upload) => upload.item_count ?? "-" },
    { id: "kind", label: "更新種別", render: (upload) => upload.replaced ? "置換" : "新規" },
    { id: "download", label: "", render: (upload) => upload.download_available ? <button className="btn" type="button" onClick={() => handleDownloadUpload(upload)} disabled={downloadingUploadId === upload.id}>{downloadingUploadId === upload.id ? "取得中..." : "ダウンロード"}</button> : <span className="history-note">{upload.archive_error ? "保存失敗" : "履歴のみ"}</span> },
  ];
  const masterCheckColumns: readonly TableColumn<MenuMasterReviewIssue>[] = [
    { id: "name", label: "献立名", render: (issue) => getReviewSourceName(issue) },
    { id: "kind", label: "種別", render: (issue) => issue.issue_type === "diff" ? "差分あり" : "新規登録候補" },
    { id: "scope", label: "適用先", render: (issue) => getMasterCheckScopeLabel(issue) },
    { id: "detail", label: "差分", render: (issue) => `${(issue.field_diffs || []).length + (issue.condiment_issues || []).length}件` },
    { id: "edit", label: "", render: (issue) => <button className="btn compact" type="button" onClick={() => { owner.changed(); setSelectedMasterCheckKey(getReviewIssueKey(issue, masterCheckState.issues.indexOf(issue))); }}>設定</button> },
  ];
  if (!authenticated) return <main className="page">ログインを確認しています。</main>;

  return (
    <main className="page" onChangeCapture={owner.changed} onInputCapture={owner.changed}>
      <header className="hero">
        <div>
          <p className="eyebrow">Monthly Menu</p>
          <h1>月次メニュー編集</h1>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>アップロード</h2>
        </header>
        <div className="upload-grid">
          <div>
            <p className="field-label">最終登録</p>
            <p className="summary-value">{menu?.display_name || "未登録"}</p>
          </div>
          <div>
            <p className="field-label">項目数</p>
            <p className="summary-value">{items.length}</p>
          </div>
          <div>
            <p className="field-label">最終アップロード</p>
            <p className="summary-value">{lastUpload || "未実施"}</p>
          </div>
        </div>
        <div className="upload-actions">
          <input
            key={uploadInputKey}
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <input
            className="input"
            placeholder="シート名 (任意)"
            value={sheetName}
            onChange={(e) => setSheetName(e.target.value)}
          />
          <select
            className="input scope-select"
            value={scopeType}
            onChange={(e) => {
              setScopeType(e.target.value);
              setScopeValue("");
            }}
          >
            <option value="base">共通(base)</option>
            <option value="facility">施設差分</option>
            <option value="tag">タグ差分</option>
          </select>
          {scopeType === "facility" && (
            <select className="input scope-input" value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
              <option value="">施設を選択</option>
              {facilities.map((facility) => (
                <option key={facility.id} value={facility.id}>
                  {facility.name} ({facility.id})
                </option>
              ))}
            </select>
          )}
          {scopeType === "tag" && (
            <select className="input scope-input" value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
              <option value="">タグを選択</option>
              {tagOptions.map((tag) => (
                <option key={tag.value} value={tag.value}>
                  {tag.value}
                  {tag.facility_count ? ` (${tag.facility_count}施設)` : ""}
                </option>
              ))}
            </select>
          )}
          <button className="btn primary" onClick={handleUpload} disabled={uploading}>
            {uploading ? "アップロード中..." : "アップロード"}
          </button>
        </div>
        <fieldset>
          <legend>対象食種（未選択は原本に含まれる食種）</legend>
          {DIET_TYPE_OPTIONS.filter((choice) => choice.value).map((choice) => <label key={choice.value}>
            <input type="checkbox" checked={importDiets.includes(choice.value)} onChange={(event) => {
              const checked = event.target.checked;
              setImportDiets((current) => checked ? [...current, choice.value] : current.filter((value) => value !== choice.value));
            }} />
            {choice.label}
          </label>)}
        </fieldset>
        <div className="month-selector">
          <label className="field compact-field">
            <span className="field-label">登録済み月次</span>
            <div className="month-option-list" aria-label="登録済み月次">
              {menuOptions.length ? (
                menuOptions.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className={`month-option${option.id === monthId ? " active" : ""}`}
                    onClick={() => handleMonthChange(option.id)}
                  >
                    {formatMonthOptionLabel(option)}
                  </button>
                ))
              ) : (
                <span className="subtle">登録済み月次がありません</span>
              )}
            </div>
          </label>
          <label className="field compact-field">
            <span className="field-label">対象月を直接指定</span>
            <input
              className="input"
              type="month"
              value={selectedMonth}
              onChange={(e) => handleMonthChange(e.target.value)}
            />
          </label>
          <p className="subtle">月ID: {Array.isArray(monthId) ? monthId.join(",") : monthId}</p>
        </div>
        <p className="subtle scope-note">
          通常は <strong>共通(base)</strong> を使います。施設だけ違う献立は <strong>施設差分</strong>、複数施設で共通の差分は
          <strong> タグ差分</strong> を選びます。
        </p>
        {scopeType === "tag" && tagOptions.length === 0 && (
          <p className="subtle scope-note">タグがまだありません。施設設定でタグを登録してから選択してください。</p>
        )}
        {message && <p className="message">{message}</p>}
        <h3>保存済みimport</h3>
        <button className="btn" type="button" onClick={() => void loadSavedImports()}>一覧を再読込</button>
        <ul>
          {savedImports.map((receipt) => <li key={receipt.id}>
            <button className="btn" type="button" onClick={() => void resumeImport(receipt.id)}>
              {receipt.source.filename} / {formatScopeLabel(receipt.source.scope_override)} / {receipt.state === "applied" ? "反映済み" : receipt.state === "review" ? "差分確認" : "状態未解決"}
            </button>
            <button className="btn" type="button" onClick={() => void downloadImport(receipt)}>原本</button>
          </li>)}
        </ul>
        <div className="upload-history">
          <div className="history-header">
            <h3>これまでのアップロード</h3>
            <p className="subtle">新しい履歴はダウンロードできます。過去分はファイル未保存のため一覧のみです。</p>
          </div>
          <DataTable
            label="取込履歴"
            rows={uploadHistory.slice(uploadHistoryPage * uploadHistoryRowsPerPage, (uploadHistoryPage + 1) * uploadHistoryRowsPerPage)}
            columns={uploadHistoryColumns}
            rowKey={(upload) => upload.id}
            total={uploadHistory.length}
            page={uploadHistoryPage}
            rowsPerPage={uploadHistoryRowsPerPage}
            onPageChange={setUploadHistoryPage}
            onRowsPerPageChange={(next) => { setUploadHistoryRowsPerPage(next); setUploadHistoryPage(0); }}
          />
        </div>
      </section>

      {pendingReview && (
        <div className="review-backdrop">
          <section
            className="review-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="menu-master-review-title"
          >
            <div className="review-header">
              <div>
                <p className="field-label">Upload Review</p>
                <h2 id="menu-master-review-title">未登録メニューの確認</h2>
              </div>
              <button className="btn" type="button" onClick={closePendingReview} disabled={reviewSubmitting}>
                閉じる
              </button>
            </div>
            <p className="subtle">
              既存メニューマスターにない献立、または候補が複数ある献立があります。アップロード前に、既存マスターを使うか新規登録するかを確定してください。
            </p>
            <div className="review-status" data-testid="menu-master-review-incomplete-summary" aria-live="polite">
              {pendingReviewNotice && <p className="message review-validation-message" role="alert">{pendingReviewNotice}</p>}
              {incompletePendingReviewIssues.length > 0 ? (
                <>
                  <p className="message">
                    未完了 {incompletePendingReviewIssues.length}件 / {pendingReviewIssueStates.length}件
                  </p>
                  <div className="review-summary-list">
                    {incompletePendingReviewIssues.map((issueState) => (
                      <button
                        key={`pending-review-summary-${issueState.issueKey}`}
                        type="button"
                        className={`review-summary-link${highlightedReviewIssueKey === issueState.issueKey ? " active" : ""}`}
                        onClick={() => focusPendingReviewIssue(issueState.issueKey)}
                      >
                        {issueState.sourceName}（{issueState.missingRequirements.join("、")}）
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <p className="message">全{pendingReviewIssueStates.length}件の確認が完了しています。</p>
              )}
            </div>
            <div className="review-list">
              {pendingReview.issues.map((issue, index) => {
                const issueKey = getReviewIssueKey(issue, index);
                const resolution = pendingReview.resolutions[issueKey];
                const candidates = issue.candidates || [];
                const suggestedPatch = issue.suggested_patch || {};
                const isIncomplete = incompletePendingReviewIssues.some((issueState) => issueState.issueKey === issueKey);
                return (
                  <article
                    key={issueKey}
                    className={`review-card${isIncomplete ? " incomplete" : ""}${highlightedReviewIssueKey === issueKey ? " active-target" : ""}`}
                    data-testid={`menu-master-review-card-${index}`}
                    tabIndex={-1}
                    ref={(node) => {
                      reviewIssueRefs.current[issueKey] = node;
                    }}
                    onFocusCapture={() => setHighlightedReviewIssueKey(issueKey)}
                  >
                    <div className="review-card-head">
                      <div>
                        <p className="review-card-title">{getReviewSourceName(issue)}</p>
                        <div className="review-card-meta">
                          {issue.normalized_name && <span>正規化: {issue.normalized_name}</span>}
                          {suggestedPatch.daypart && <span>{suggestedPatch.daypart}</span>}
                          {suggestedPatch.category && <span>{suggestedPatch.category}</span>}
                          {normalizeUnitChoice(suggestedPatch.unit_type) && (
                            <span>
                              月次案: {formatUnitChoiceLabel(suggestedPatch.unit_type)} / {toResolutionQty(suggestedPatch.qty_per_serving) || "-"}
                            </span>
                          )}
                        </div>
                      </div>
                      <span className="review-card-reason">
                        {candidates.length > 0 ? "候補確認が必要" : "新規登録が必要"}
                      </span>
                    </div>

                    {isIncomplete && (
                      <p className="review-missing-requirements">
                        未完了: {incompletePendingReviewIssues.find((issueState) => issueState.issueKey === issueKey)?.missingRequirements.join("、")}
                      </p>
                    )}

                    <div className="review-mode-row">
                      {candidates.length > 0 && (
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`review-action-${issueKey}`}
                            checked={resolution.action === "existing"}
                            onChange={() => updateReviewResolution(issueKey, { action: "existing" })}
                          />
                          <span>既存マスターを使う</span>
                        </label>
                      )}
                      {issue.current_master?.id && (
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`review-action-${issueKey}`}
                            checked={resolution.action === "update"}
                            onChange={() =>
                              updateReviewResolution(issueKey, {
                                action: "update",
                                menu_master_id: issue.current_master?.id || resolution.menu_master_id,
                              })
                            }
                          />
                          <span>既存マスターを更新する</span>
                        </label>
                      )}
                      <label className="review-radio">
                        <input
                          type="radio"
                          name={`review-action-${issueKey}`}
                          checked={resolution.action === "create"}
                          onChange={() => updateReviewResolution(issueKey, { action: "create" })}
                        />
                        <span>新規登録する</span>
                      </label>
                    </div>

                    {resolution.action === "existing" && candidates.length > 0 && (
                      <div className="review-existing">
                        <label>
                          <span>候補マスター</span>
                          <select
                            className="input"
                            aria-label={`候補マスター-${index + 1}`}
                            value={resolution.menu_master_id}
                            onChange={(e) =>
                              updateReviewResolution(issueKey, {
                                menu_master_id: e.target.value,
                              })
                            }
                          >
                            <option value="">候補を選択</option>
                            {candidates.map((candidate) => (
                              <option key={candidate.id} value={candidate.id}>
                                {candidate.name} / {formatUnitChoiceLabel(candidate.unit_type)} / {toResolutionQty(candidate.qty_per_serving) || "-"}
                                {candidate.daypart ? ` / ${candidate.daypart}` : ""}
                                {candidate.category ? ` / ${candidate.category}` : ""}
                              </option>
                            ))}
                          </select>
                        </label>
                        <div className="review-candidate-list">
                          {candidates.map((candidate) => (
                            <button
                              key={candidate.id}
                              type="button"
                              className={`review-candidate${resolution.menu_master_id === candidate.id ? " active" : ""}`}
                              onClick={() =>
                                updateReviewResolution(issueKey, {
                                  action: "existing",
                                  menu_master_id: candidate.id,
                                })
                              }
                            >
                              <strong>{candidate.name}</strong>
                              <span>{formatUnitChoiceLabel(candidate.unit_type)}</span>
                              <span>{toResolutionQty(candidate.qty_per_serving) || "-"}</span>
                              {candidate.daypart && <span>{candidate.daypart}</span>}
                              {candidate.category && <span>{candidate.category}</span>}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {(resolution.action === "create" || resolution.action === "update") && (
                      <div className="review-create-grid">
                        <label>
                          <span>{resolution.action === "update" ? "更新後メニュー名" : "新規メニュー名"}</span>
                          <input
                            className="input"
                            aria-label={`新規メニュー名-${index + 1}`}
                            value={resolution.name}
                            onChange={(e) => updateReviewResolution(issueKey, { name: e.target.value })}
                          />
                        </label>
                        <label>
                          <span>基準単位</span>
                          <select
                            className="input"
                            aria-label={`新規単位-${index + 1}`}
                            value={normalizeUnitChoice(resolution.unit_type)}
                            onChange={(e) => updateReviewResolution(issueKey, { unit_type: e.target.value })}
                          >
                            <option value="">単位を選択</option>
                            {unitChoices.map((choice) => (
                              <option key={choice.value} value={choice.value}>
                                {choice.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>基準量</span>
                          <input
                            className="input"
                            aria-label={`新規量-${index + 1}`}
                            type="number"
                            min="0"
                            step="1"
                            value={resolution.qty_per_serving}
                            onChange={(e) => updateReviewResolution(issueKey, { qty_per_serving: e.target.value })}
                          />
                        </label>
                        <label>
                          <span>袋上限数</span>
                          <input
                            className="input"
                            aria-label={`新規袋上限数-${index + 1}`}
                            type="number"
                            min="0"
                            step="1"
                            value={resolution.bag_max_qty}
                            onChange={(e) => updateReviewResolution(issueKey, { bag_max_qty: e.target.value })}
                          />
                        </label>
                        <label>
                          <span>袋上限単位</span>
                          <select
                            className="input"
                            aria-label={`新規袋上限単位-${index + 1}`}
                            value={normalizeUnitChoice(resolution.bag_max_unit)}
                            onChange={(e) => updateReviewResolution(issueKey, { bag_max_unit: e.target.value })}
                          >
                            <option value="">単位を選択</option>
                            {unitChoices.map((choice) => (
                              <option key={choice.value} value={choice.value}>
                                {choice.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>付属品</span>
                          <input
                            className="input"
                            aria-label={`新規付属品-${index + 1}`}
                            value={resolution.condiments}
                            onChange={(e) => updateReviewResolution(issueKey, { condiments: e.target.value })}
                          />
                        </label>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
            <div className="review-footer">
              <div>
                <p>{pendingReview.receipt.source.filename} / {pendingReview.receipt.month_id} / {formatScopeLabel(pendingReview.receipt.source.scope_override)} / {pendingReview.receipt.current_review.item_count}件</p>
                <button className="btn" type="button" onClick={() => void downloadImport(pendingReview.receipt)}>原本をダウンロード</button>
                <ul>{pendingReview.receipt.current_review.master_diffs.map((diff, index) => <li key={index}>
                  {diff.source_name}: {diff.field_diffs?.map((field) => `${field.label}: ${field.master_value ?? "未登録"} → ${field.monthly_value ?? "未登録"}`).join("、")}
                </li>)}</ul>
              </div>
              <button className="btn" type="button" onClick={closePendingReview} disabled={reviewSubmitting}>
                キャンセル
              </button>
              <button
                className="btn primary"
                type="button"
                onClick={() => void confirmPendingReview(false)}
                disabled={reviewSubmitting || pendingReview.receipt.state !== "review"}
              >
                {reviewSubmitting ? "確認中..." : canSubmitPendingReview ? "差分設定を保存" : "未完了項目を確認する"}
              </button>
              <button className="btn primary" type="button" onClick={() => void confirmPendingReview(true)}
                disabled={reviewSubmitting || !pendingReview.saved || !canSubmitPendingReview || pendingReview.receipt.state !== "review"}>
                保存済み差分を反映
              </button>
            </div>
          </section>
        </div>
      )}

      <div className="panel-row master-tools-row">
        <section className="panel master-check-panel">
          <header className="panel-header">
            <div>
              <h2>メニューマスター差分チェック</h2>
              <button className="btn" type="button" onClick={reloadMonthlyEditor}>差分を再読込</button>
              <p className="subtle">
                月次メニューとメニューマスターを突き合わせ、新規登録が必要なものと、マスター更新が必要な差分を確認します。
              </p>
            </div>
          </header>
          {masterCheckNotice && <p className="message">{masterCheckNotice}</p>}
          <p className="subtle">検出件数: {masterCheckState.issues.length}</p>
          {masterCheckState.issues.length === 0 ? (
            <p className="subtle">差分はありません。</p>
          ) : (
            <>
            <DataTable
              label="メニューマスター差分一覧"
              rows={masterCheckState.issues.slice(masterCheckPage * masterCheckRowsPerPage, (masterCheckPage + 1) * masterCheckRowsPerPage)}
              columns={masterCheckColumns}
              rowKey={(issue) => getReviewIssueKey(issue, masterCheckState.issues.indexOf(issue))}
              total={masterCheckState.issues.length}
              page={masterCheckPage}
              rowsPerPage={masterCheckRowsPerPage}
              onPageChange={setMasterCheckPage}
              onRowsPerPageChange={(next) => { setMasterCheckRowsPerPage(next); setMasterCheckPage(0); }}
            />
            <div className="review-list">
            {masterCheckState.issues.map((issue, index) => {
              const issueKey = getReviewIssueKey(issue, index);
              if (selectedMasterCheckKey !== issueKey) return null;
              const resolution = masterCheckState.resolutions[issueKey];
              const candidates = issue.candidates || [];
              const suggestedPatch = issue.suggested_patch || {};
              const isDiff = issue.issue_type === "diff";
              const currentMaster = issue.current_master || null;
              return (
                <article key={`master-check-${issueKey}`} className="review-card" data-testid={`menu-master-check-card-${index}`}>
                  <div className="review-card-head">
                    <div>
                      <p className="review-card-title">{getReviewSourceName(issue)}</p>
                      <div className="review-card-meta">
                        <span>{isDiff ? "マスター更新候補" : "新規登録候補"}</span>
                        <span>適用範囲: {getMasterCheckScopeLabel(issue)}</span>
                        {issue.normalized_name && <span>正規化: {issue.normalized_name}</span>}
                        {suggestedPatch.daypart && <span>{suggestedPatch.daypart}</span>}
                        {suggestedPatch.category && <span>{suggestedPatch.category}</span>}
                        {normalizeUnitChoice(suggestedPatch.unit_type) && (
                          <span>
                            月次: {formatUnitChoiceLabel(suggestedPatch.unit_type)} / {toResolutionQty(suggestedPatch.qty_per_serving) || "-"}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="review-card-reason">{isDiff ? "差分あり" : "未登録"}</span>
                  </div>

                  {masterRevisions.current[issueKey] !== undefined && masterRevisions.current[issueKey] !== revision && (
                    <p role="alert">月次メニューの入力版が変わりました。差分の編集中入力は保持しています。正本を明示的に読込し直して確認してください。</p>
                  )}
                  {isDiff ? (
                    <>
                      <div className="review-mode-row">
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`master-check-action-${issueKey}`}
                            checked={resolution.action === "update"}
                            onChange={() =>
                              updateMasterCheckResolution(issueKey, {
                                action: "update",
                              })
                            }
                          />
                          <span>マスターを更新</span>
                        </label>
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`master-check-action-${issueKey}`}
                            checked={resolution.action === "month_only"}
                            onChange={() =>
                              updateMasterCheckResolution(issueKey, {
                                action: "month_only",
                                unit_type: normalizeUnitChoice(resolution.unit_type || suggestedPatch.unit_type),
                                qty_per_serving: resolution.qty_per_serving || toResolutionQty(suggestedPatch.qty_per_serving),
                              })
                            }
                          />
                          <span>当月にのみ適用</span>
                        </label>
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`master-check-action-${issueKey}`}
                            checked={resolution.action === "category_only"}
                            onChange={() =>
                              updateMasterCheckResolution(issueKey, {
                                action: "category_only",
                                category: resolution.category || normalizeValue(String(suggestedPatch.category ?? currentMaster?.category ?? "")),
                              })
                            }
                          />
                          <span>区分だけマスターへ反映</span>
                        </label>
                      </div>
                      {currentMaster && (
                        <div className="master-check-compare">
                          <div className="master-check-column">
                            <h3>現在のマスター</h3>
                            <p>{currentMaster.name}</p>
                            <p>
                              {formatUnitChoiceLabel(currentMaster.unit_type)} / {toResolutionQty(currentMaster.qty_per_serving) || "-"}
                            </p>
                            <p>{currentMaster.daypart || "-"}</p>
                            <p>{currentMaster.category || "-"}</p>
                          </div>
                          <div className="master-check-column">
                            <h3>月次メニュー</h3>
                            <p>{resolution.name}</p>
                            <p>
                              {formatUnitChoiceLabel(resolution.unit_type)} / {resolution.qty_per_serving || "-"}
                            </p>
                            <p>{suggestedPatch.daypart || "-"}</p>
                            <p>{suggestedPatch.category || "-"}</p>
                          </div>
                        </div>
                      )}
                      {(issue.field_diffs || []).length > 0 && (
                        <div className="master-check-diffs">
                          {(issue.field_diffs || []).map((diff) => (
                            <div key={`${issueKey}-${diff.field}`} className="master-check-diff-row">
                              <strong>{diff.label || diff.field}</strong>
                              <span>月次: {String(diff.monthly_value ?? "-")}</span>
                              <span>マスター: {String(diff.master_value ?? "-")}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {(issue.condiment_issues || []).length > 0 && (
                        <div className="master-check-diffs">
                          {(issue.condiment_issues || []).map((condiment, condimentIndex) => (
                            <div key={`${issueKey}-condiment-${condimentIndex}`} className="master-check-diff-row">
                              <strong>{condiment.condiment_name || "付属品"}</strong>
                              <span>{condiment.message || "付属品の袋上限を設定してください"}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {resolution.action === "update" && (
                        <div className="review-create-grid">
                          <label>
                            <span>更新後メニュー名</span>
                            <input
                              className="input"
                              aria-label={`差分メニュー名-${index + 1}`}
                              value={resolution.name}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { name: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>単位</span>
                            <select
                              className="input"
                              aria-label={`差分単位-${index + 1}`}
                              value={normalizeUnitChoice(resolution.unit_type)}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { unit_type: e.target.value })}
                            >
                              <option value="">単位を選択</option>
                              {unitChoices.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span>量</span>
                            <input
                              className="input"
                              aria-label={`差分量-${index + 1}`}
                              type="number"
                              min="0"
                              step="1"
                              value={resolution.qty_per_serving}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { qty_per_serving: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>袋上限数</span>
                            <input
                              className="input"
                              aria-label={`差分袋上限数-${index + 1}`}
                              type="number"
                              min="0"
                              step="1"
                              value={resolution.bag_max_qty}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { bag_max_qty: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>袋上限単位</span>
                            <select
                              className="input"
                              aria-label={`差分袋上限単位-${index + 1}`}
                              value={normalizeUnitChoice(resolution.bag_max_unit)}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { bag_max_unit: e.target.value })}
                            >
                              <option value="">単位を選択</option>
                              {unitChoices.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span>付属品</span>
                            <input
                              className="input"
                              aria-label={`差分付属品-${index + 1}`}
                              value={resolution.condiments}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { condiments: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>区分</span>
                            <input
                              className="input"
                              aria-label={`差分区分-${index + 1}`}
                              value={resolution.category}
                              list="category-options"
                              onChange={(e) => updateMasterCheckResolution(issueKey, { category: e.target.value })}
                            />
                          </label>
                        </div>
                      )}
                      {resolution.action === "month_only" && (
                        <div className="review-create-grid">
                          <label>
                            <span>単位</span>
                            <select
                              className="input"
                              aria-label={`当月のみ単位-${index + 1}`}
                              value={normalizeUnitChoice(resolution.unit_type)}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { unit_type: e.target.value })}
                            >
                              <option value="">単位を選択</option>
                              {unitChoices.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span>量</span>
                            <input
                              className="input"
                              aria-label={`当月のみ量-${index + 1}`}
                              type="number"
                              min="0"
                              step="1"
                              value={resolution.qty_per_serving}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { qty_per_serving: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>袋上限数</span>
                            <input
                              className="input"
                              aria-label={`当月のみ袋上限数-${index + 1}`}
                              type="number"
                              min="0"
                              step="1"
                              value={resolution.bag_max_qty}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { bag_max_qty: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>袋上限単位</span>
                            <select
                              className="input"
                              aria-label={`当月のみ袋上限単位-${index + 1}`}
                              value={normalizeUnitChoice(resolution.bag_max_unit)}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { bag_max_unit: e.target.value })}
                            >
                              <option value="">単位を選択</option>
                              {unitChoices.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span>区分</span>
                            <input
                              className="input"
                              aria-label={`当月のみ区分-${index + 1}`}
                              value={resolution.category}
                              list="category-options"
                              onChange={(e) => updateMasterCheckResolution(issueKey, { category: e.target.value })}
                            />
                          </label>
                        </div>
                      )}
                      {resolution.action === "month_only" && (
                        <p className="subtle">量を空欄にすると、この月の規定量をそのまま使います。</p>
                      )}
                      {resolution.action === "category_only" && (
                        <div className="review-create-grid">
                          <label>
                            <span>メニューマスターの区分</span>
                            <input
                              className="input"
                              aria-label={`区分だけ-${index + 1}`}
                              value={resolution.category}
                              list="category-options"
                              onChange={(e) => updateMasterCheckResolution(issueKey, { category: e.target.value })}
                            />
                          </label>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="review-mode-row">
                        {candidates.length > 0 && (
                          <label className="review-radio">
                            <input
                              type="radio"
                              name={`master-check-action-${issueKey}`}
                              checked={resolution.action === "existing"}
                              onChange={() =>
                                updateMasterCheckResolution(issueKey, {
                                  action: "existing",
                                  menu_master_id:
                                    resolution.menu_master_id || (candidates.length === 1 ? candidates[0].id : ""),
                                })
                              }
                            />
                            <span>既存マスターを使う</span>
                          </label>
                        )}
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`master-check-action-${issueKey}`}
                            checked={resolution.action === "create"}
                            onChange={() =>
                              updateMasterCheckResolution(issueKey, {
                                action: "create",
                                unit_type: normalizeUnitChoice(resolution.unit_type || suggestedPatch.unit_type),
                                qty_per_serving: resolution.qty_per_serving || toResolutionQty(suggestedPatch.qty_per_serving),
                              })
                            }
                          />
                          <span>新規登録する</span>
                        </label>
                      </div>

                      {resolution.action === "existing" && candidates.length > 0 && (
                        <div className="review-existing">
                          <label>
                            <span>候補マスター</span>
                            <select
                              className="input"
                              aria-label={`差分候補マスター-${index + 1}`}
                              value={resolution.menu_master_id}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { menu_master_id: e.target.value })}
                            >
                              <option value="">候補を選択</option>
                              {candidates.map((candidate) => (
                                <option key={candidate.id} value={candidate.id}>
                                  {candidate.name} / {formatUnitChoiceLabel(candidate.unit_type)} / {toResolutionQty(candidate.qty_per_serving) || "-"}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                      )}

                      {resolution.action === "create" && (
                        <div className="review-create-grid">
                          <label>
                            <span>新規メニュー名</span>
                            <input
                              className="input"
                              aria-label={`差分新規メニュー名-${index + 1}`}
                              value={resolution.name}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { name: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>基準単位</span>
                            <select
                              className="input"
                              aria-label={`差分新規単位-${index + 1}`}
                              value={normalizeUnitChoice(resolution.unit_type)}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { unit_type: e.target.value })}
                            >
                              <option value="">単位を選択</option>
                              {unitChoices.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span>基準量</span>
                            <input
                              className="input"
                              aria-label={`差分新規量-${index + 1}`}
                              type="number"
                              min="0"
                              step="1"
                              value={resolution.qty_per_serving}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { qty_per_serving: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>袋上限数</span>
                            <input
                              className="input"
                              aria-label={`差分新規袋上限数-${index + 1}`}
                              type="number"
                              min="0"
                              step="1"
                              value={resolution.bag_max_qty}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { bag_max_qty: e.target.value })}
                            />
                          </label>
                          <label>
                            <span>袋上限単位</span>
                            <select
                              className="input"
                              aria-label={`差分新規袋上限単位-${index + 1}`}
                              value={normalizeUnitChoice(resolution.bag_max_unit)}
                              onChange={(e) => updateMasterCheckResolution(issueKey, { bag_max_unit: e.target.value })}
                            >
                              <option value="">単位を選択</option>
                              {unitChoices.map((choice) => (
                                <option key={choice.value} value={choice.value}>
                                  {choice.label}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                      )}
                    </>
                  )}

                  <div className="review-footer review-footer-inline">
                    <button
                      className="btn primary"
                      type="button"
                      onClick={() => applyMasterCheck(issue, index)}
                      disabled={!isReviewResolutionComplete(issue, resolution) || masterCheckSavingId === issue.item_id}
                    >
                      {masterCheckSavingId === issue.item_id
                        ? "反映中..."
                        : isDiff
                          ? resolution.action === "month_only"
                            ? "この月だけ反映"
                            : resolution.action === "category_only"
                              ? "区分だけ反映"
                              : "マスターを更新"
                          : "この内容で登録"}
                    </button>
                  </div>
                </article>
              );
              })}
            </div>
            </>
          )}
        </section>

        <section className="panel condiment-panel">
          <header className="panel-header">
            <div>
              <h2>付属品フラグ</h2>
              <p className="subtle">献立メニューからソース等の付属品フラグを反映します。</p>
            </div>
          </header>
          <div className="condiment-help" data-testid="condiment-flag-help">
            <p>
              別ファイルは必須ではありません。月次メニューに <strong>添)キャベツ</strong> のように入っている場合は、出力時に主菜と添えへ分離します。
            </p>
            <p>
              添え側の温冷・単位・量は、メニューマスターに <strong>キャベツ</strong> などの添え名を別メニューとして登録して設定します。
            </p>
            <p>付属品フラグは、ソース等の付属品情報がまとまったファイルを一括反映するための補助機能です。</p>
          </div>
          <div className="upload-actions condiment-actions">
            <input type="file" onChange={(e) => setCondimentFile(e.target.files?.[0] || null)} />
            <button className="btn primary" onClick={handleCondimentUpload} disabled={condimentUploading}>
              {condimentUploading ? "反映中..." : "反映する"}
            </button>
          </div>
          {condimentMessage && <p className="message">{condimentMessage}</p>}
        </section>
      </div>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>月次シート</h2>
            <p className="subtle">シートを主画面にして、右側で設定を確認・調整します。</p>
          </div>
        </header>
        <div className="sheet-stats">
          <p className="subtle">件数: {entries.length}</p>
          <p className="subtle">日付行: {sheetDateRows.length}</p>
          <p className="subtle">献立列: {sheetColumns.length}</p>
        </div>
        <div className="sheet-workspace">
          <div className="table-wrap menu-sheet-wrap">
            <table className="menu-sheet-table" data-testid="monthly-menu-sheet">
              <thead>
                <tr>
                  <th>日付</th>
                  {sheetColumns.length === 0 ? (
                    <th>献立列</th>
                  ) : (
                    sheetColumns.map((column) => (
                      <th key={column.key}>
                        <div className="sheet-column-head">
                          <span>{column.scope_label}</span>
                          <span>{column.daypart}</span>
                          <span>{column.category}</span>
                          <span>{column.diet_type}</span>
                          <span>{column.slot_index >= 99 ? "枠-" : `枠${column.slot_index}`}</span>
                        </div>
                      </th>
                    ))
                  )}
                </tr>
              </thead>
              <tbody>
                {sheetDateRows.length === 0 ? (
                  <tr>
                    <td colSpan={sheetColumns.length + 1}>日付別の献立がありません。</td>
                  </tr>
                ) : (
                  sheetDateRows.map((row) => (
                    <tr key={row.key}>
                      <th scope="row" className="sticky-date">
                        {formatSheetDateLabel(row.menu_date)}
                      </th>
                      {sheetColumns.map((column) => {
                        const cellEntries = row.cells[column.key] || [];
                        return (
                          <td key={`${row.key}-${column.key}`}>
                            {cellEntries.length === 0 ? (
                              <span className="sheet-empty">-</span>
                            ) : (
                              <div className="sheet-cell-stack">
                                {cellEntries.map((entry) => (
                                  <button
                                    key={entry.id}
                                    type="button"
                                    className={`sheet-chip${selectedEntryId === entry.id ? " active" : ""}`}
                                    onClick={() => selectEntry(entry.id)}
                                  >
                                    {entry.name}
                                  </button>
                                ))}
                              </div>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <aside className="sheet-inspector">
            <div className="sheet-inspector__header">
              <p className="field-label">選択中の献立</p>
              <p className="subtle">シートのメニューを押すと右側で設定を確認できます。</p>
            </div>
            {selectedEntry ? (
              <>
                <div className="inspector-summary">
                  <p className="inspector-title" data-testid="selected-entry-name">
                    {selectedEntry.name}
                  </p>
                  <div className="inspector-meta">
                    <span>{formatSheetDateLabel(selectedEntry.menu_date)}</span>
                    <span>{selectedEntry.daypart || "-"}</span>
                    <span data-testid="selected-entry-category">{selectedEntry.category || "-"}</span>
                    <span>{formatDietTypeLabel(selectedEntry.diet_type)}</span>
                    <span>{formatScopeLabel(selectedEntry.facility_override)}</span>
                  </div>
                </div>
                {selectedGarnish && (
                  <div className="garnish-guidance" data-testid="selected-garnish-guidance">
                    <p className="field-label">添えの分離</p>
                    <div className="garnish-split">
                      <span>主菜: {selectedGarnish.mainName}</span>
                      <span>添え: {selectedGarnish.garnishName}</span>
                    </div>
                    <p>
                      ラベル・総量では上記のように分離して扱います。添え側の温冷・単位・量は、メニューマスターで
                      <strong> {selectedGarnish.garnishName} </strong>
                      を別メニューとして登録・設定してください。
                    </p>
                    <p>
                      {selectedGarnishItem
                        ? `現在、${selectedGarnish.garnishName} の月次メニュー設定があります。必要に応じてメニューマスター側も確認してください。`
                        : `現在、この月次シート内には ${selectedGarnish.garnishName} 単体の設定が見つかりません。メニューマスターに登録すると添え側の設定として使われます。`}
                    </p>
                  </div>
                )}
                {selectedItem ? (
                  <EditForm<MenuItemDraft>
                    key={`${selectedItem.id}-${itemEditGeneration}`} label="献立を編集"
                    defaultValues={itemDraft(selectedItem)} onDirtyChange={(dirty) => {
                      if (dirty && !itemFormDirty && selectedEntry && typeof monthId === "string") setEditorSelection({ month_id: monthId, entry: selectedEntry, item: selectedItem });
                      setItemFormDirty(dirty);
                    }}
                    classifyError={(error) => ({ kind: (error as any)?.response?.status === 409 ? "conflict" : "error", message: error instanceof Error ? error.message : "保存に失敗しました。" })}
                    onSave={async (values) => {
                      const saved = await saveItem(mergeItemDraft(selectedItem, values));
                      setItemFormDirty(false);
                      return itemDraft(saved);
                    }}
                    onReload={async () => {
                      if (!monthId || Array.isArray(monthId)) throw new Error("対象月が確定していません。");
                      if (itemFormDirty && !window.confirm("未保存の変更を破棄して再読込しますか？")) throw new Error("再読込を取り消しました。");
                      const current = owner.capture("item-reload");
                      const entry = selectedEntryRef.current;
                      const itemId = selectedItem.id;
                      const fresh = await apiClient.get(`/monthly-menus/${monthId}`, { params: { editor: true } });
                      if (!current() || entry !== selectedEntryRef.current) throw new Error("対象または入力が変わったため再読込結果を適用しませんでした。");
                      if (!Array.isArray(fresh.data?.items)) throw new Error("正本の応答を確認できませんでした。");
                      const refreshed = fresh.data.items.find((item: MenuItem) => item.id === itemId);
                      if (!refreshed) throw new Error("対象の献立を確認できませんでした。");
                      adoptSnapshot(fresh.data, "item");
                      setItemFormDirty(false);
                      return itemDraft(refreshed);
                    }}>
                    <FormTextField<MenuItemDraft> name="name" label="献立名" rules={{ required: "献立名は必須です。" }} />
                    <FormTextField<MenuItemDraft> name="unit_type" label="単位" select><MuiMenuItem value="">未選択</MuiMenuItem>{unitChoices.map((choice) => <MuiMenuItem key={choice.value} value={choice.value}>{choice.label}</MuiMenuItem>)}</FormTextField>
                    <FormTextField<MenuItemDraft> name="qty_per_serving" label="1人前の量" type="number" /><FormTextField<MenuItemDraft> name="bag_max_qty" label="袋最大量" type="number" />
                    <FormTextField<MenuItemDraft> name="bag_max_unit" label="袋単位" select><MuiMenuItem value="">未選択</MuiMenuItem>{unitChoices.map((choice) => <MuiMenuItem key={choice.value} value={choice.value}>{choice.label}</MuiMenuItem>)}</FormTextField>
                    <FormTextField<MenuItemDraft> name="temp_type" label="温冷" select>{tempTypeChoices.map((choice) => <MuiMenuItem key={choice.value} value={choice.value}>{choice.label}</MuiMenuItem>)}</FormTextField>
                    <FormTextField<MenuItemDraft> name="daypart" label="朝昼夕" select>{daypartChoices.map((choice) => <MuiMenuItem key={choice.value} value={choice.value}>{choice.label}</MuiMenuItem>)}</FormTextField><FormTextField<MenuItemDraft> name="category" label="メニュー区分" /><FormTextField<MenuItemDraft> name="diet_type" label="食種" select>{DIET_TYPE_OPTIONS.map((choice) => <MuiMenuItem key={choice.value} value={choice.value}>{choice.label}</MuiMenuItem>)}</FormTextField><FormTextField<MenuItemDraft> name="facility_override" label="適用scope" />
                  </EditForm>
                ) : (
                  <p className="subtle">対応するメニュー設定がまだありません。必要なら下の補助一覧を開いて確認してください。</p>
                )}
                <div className="exception-form" data-testid="entry-exception-form">
                  {exceptionReceipt && exceptionReceipt.month_id === monthId && exceptionReceipt.entry_id === selectedEntryId && <p role="status">保存済み例外: {exceptionReceipt.entries.map((entry) => `${formatScopeLabel(entry.facility_override)} / ${entry.name}`).join("、")}</p>}
                  <div className="exception-form__header">
                    <p className="field-label">例外メニュー</p>
                    <p className="subtle">この位置だけ、選択した施設へ別メニューを割り当てます。</p>
                  </div>
                  {entryExceptionDraft ? (
                    <>
                      <label>
                        <span>例外メニュー名</span>
                        <input
                          className="input"
                          value={entryExceptionDraft.name}
                          onChange={(e) => updateEntryExceptionField("name", e.target.value)}
                        />
                      </label>
                      <label>
                        <span>単位</span>
                        <select
                          className="input"
                          value={entryExceptionDraft.unit_type}
                          onChange={(e) => updateEntryExceptionField("unit_type", e.target.value)}
                        >
                          <option value="">未選択</option>
                          {unitChoices.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>量</span>
                        <input
                          className="input"
                          type="number"
                          value={entryExceptionDraft.qty_per_serving}
                          onChange={(e) => updateEntryExceptionField("qty_per_serving", e.target.value)}
                        />
                      </label>
                      <label>
                        <span>袋最大量</span>
                        <input
                          className="input"
                          type="number"
                          value={entryExceptionDraft.bag_max_qty}
                          onChange={(e) => updateEntryExceptionField("bag_max_qty", e.target.value)}
                        />
                      </label>
                      <label>
                        <span>袋単位</span>
                        <select
                          className="input"
                          value={entryExceptionDraft.bag_max_unit}
                          onChange={(e) => updateEntryExceptionField("bag_max_unit", e.target.value)}
                        >
                          <option value="">未選択</option>
                          {unitChoices.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>温冷</span>
                        <select
                          className="input"
                          value={entryExceptionDraft.temp_type}
                          onChange={(e) => updateEntryExceptionField("temp_type", e.target.value)}
                        >
                          {tempTypeChoices.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>区分</span>
                        <input
                          className="input"
                          value={entryExceptionDraft.category}
                          list="category-options"
                          onChange={(e) => updateEntryExceptionField("category", e.target.value)}
                        />
                      </label>
                      <label>
                        <span>食種</span>
                        <select
                          className="input"
                          value={entryExceptionDraft.diet_type}
                          onChange={(e) => updateEntryExceptionField("diet_type", e.target.value)}
                        >
                          {DIET_TYPE_OPTIONS.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="exception-targets">
                        <p className="field-label">対象施設</p>
                        <div className="exception-targets__list">
                          {facilities.length === 0 ? (
                            <p className="subtle">対象施設がありません。</p>
                          ) : (
                            facilities.map((facility) => (
                              <label key={facility.id} className="exception-target">
                                <input
                                  type="checkbox"
                                  checked={entryExceptionDraft.facilityIds.includes(facility.id)}
                                  onChange={() => toggleEntryExceptionFacility(facility.id)}
                                />
                                <span>
                                  {facility.name} ({facility.id})
                                </span>
                              </label>
                            ))
                          )}
                        </div>
                      </div>
                      <button className="btn primary" onClick={saveEntryException} disabled={entryExceptionSaving}>
                        {entryExceptionSaving
                          ? "保存中..."
                          : selectedEntry.facility_override
                            ? "この例外メニューを更新"
                            : "この位置に例外メニューを追加"}
                      </button>
                    </>
                  ) : (
                    <p className="subtle">例外メニューを編集できません。</p>
                  )}
                </div>
              </>
            ) : (
              <p className="subtle">表示できる献立がありません。</p>
            )}
          </aside>
        </div>
      </section>

      <section className="panel">
        <details className="menu-list-details">
          <summary>メニュー一覧</summary>
          <DataTable label="月次メニュー一覧" rows={items} columns={itemColumns} rowKey={(item) => item.id} />
        </details>
        <datalist id="daypart-options">
          {daypartOptions.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
        <datalist id="category-options">
          {categoryOptions.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", "Meiryo", sans-serif;
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

        .month-selector {
          display: flex;
          flex-wrap: wrap;
          gap: 10px 14px;
          align-items: end;
        }

        .compact-field {
          display: grid;
          gap: 6px;
        }

        .compact-field .field-label {
          margin-bottom: 0;
        }

        .compact-field .input {
          min-width: 160px;
        }

        .month-option-list {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          max-width: min(720px, 80vw);
        }

        .month-option {
          border: 1px solid #1f2a2a24;
          background: #ffffff;
          color: #1f2a2a;
          border-radius: 999px;
          padding: 8px 12px;
          font-weight: 700;
          cursor: pointer;
          white-space: nowrap;
        }

        .month-option.active {
          background: #1f2a2a;
          border-color: #1f2a2a;
          color: #f7f2e7;
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

        .panel-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
          gap: 20px;
          align-items: start;
          margin-bottom: 20px;
        }

        .panel-row .panel {
          margin-bottom: 0;
        }

        .master-check-panel {
          min-width: 0;
        }

        .condiment-panel {
          min-width: 0;
        }

        .condiment-actions {
          align-items: stretch;
        }

        .condiment-actions input[type="file"] {
          width: 100%;
        }

        .condiment-help,
        .garnish-guidance {
          display: grid;
          gap: 8px;
          padding: 12px 14px;
          border: 1px solid rgba(20, 82, 67, 0.14);
          border-radius: 14px;
          background: #f2f7f4;
          color: #29443d;
          font-size: 13px;
          line-height: 1.6;
        }

        .condiment-help {
          margin-bottom: 12px;
        }

        .condiment-help p,
        .garnish-guidance p {
          margin: 0;
        }

        .garnish-split {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .garnish-split span {
          padding: 6px 10px;
          border-radius: 999px;
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.08);
          font-size: 12px;
          font-weight: 700;
          color: #17302c;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .upload-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
        }

        .upload-actions {
          display: flex;
          gap: 12px;
          align-items: center;
          flex-wrap: wrap;
          margin-top: 12px;
        }

        .upload-actions input[type="file"] {
          max-width: 100%;
        }

        .upload-actions .input {
          min-width: 220px;
          flex: 1;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          margin: 0 0 6px;
        }

        .summary-value {
          margin: 0;
          font-weight: 600;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .upload-history {
          margin-top: 18px;
        }

        .review-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(16, 22, 20, 0.48);
          display: grid;
          place-items: center;
          padding: 24px;
          z-index: 50;
        }

        .review-modal {
          width: min(980px, 100%);
          max-height: min(88vh, 980px);
          overflow: auto;
          background: #fdfcf8;
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 24px;
          box-shadow: 0 26px 60px rgba(18, 24, 23, 0.24);
          padding: 24px;
          display: grid;
          gap: 18px;
        }

        .review-header {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: start;
        }

        .review-list {
          display: grid;
          gap: 14px;
        }

        .review-status {
          display: grid;
          gap: 8px;
        }

        .review-status .message {
          margin-top: 0;
        }

        .review-validation-message {
          border: 1px solid #d97706;
          background: #fff3d6;
          color: #6b3d00;
        }

        .review-summary-list {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }

        .review-summary-link {
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 6px;
          background: #ffffff;
          color: #17302c;
          padding: 6px 12px;
          font: inherit;
          cursor: pointer;
        }

        .review-summary-link.active {
          border-color: #d97706;
          background: #fff3d6;
        }

        .review-card {
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 18px;
          padding: 16px;
          background: #ffffff;
          display: grid;
          gap: 14px;
          scroll-margin-block: 96px;
          outline: none;
        }

        .review-card.incomplete {
          border-color: #d97706;
          background: #fffaf0;
        }

        .review-card.active-target {
          box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.22);
        }

        .review-missing-requirements {
          margin: 0;
          color: #8a4b00;
          font-size: 13px;
          font-weight: 700;
        }

        .review-card-head {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: start;
        }

        .review-card-title {
          margin: 0;
          font-size: 18px;
          font-weight: 700;
          color: #17302c;
        }

        .review-card-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 8px;
        }

        .review-card-meta span,
        .review-card-reason {
          padding: 5px 9px;
          border-radius: 999px;
          background: #eef2ef;
          color: #48605a;
          font-size: 12px;
          font-weight: 600;
        }

        .review-mode-row {
          display: flex;
          gap: 16px;
          flex-wrap: wrap;
        }

        .review-radio {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          font-weight: 600;
          color: #22312e;
        }

        .review-existing,
        .review-create-grid {
          display: grid;
          gap: 12px;
        }

        .review-existing label,
        .review-create-grid label {
          display: grid;
          gap: 6px;
        }

        .review-existing label span,
        .review-create-grid label span {
          font-size: 12px;
          color: #5f7b74;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }

        .review-create-grid {
          grid-template-columns: minmax(0, 1.4fr) minmax(180px, 0.8fr) minmax(140px, 0.6fr);
        }

        .review-candidate-list {
          display: grid;
          gap: 8px;
        }

        .review-candidate {
          display: grid;
          grid-template-columns: minmax(0, 2fr) repeat(4, minmax(0, 1fr));
          gap: 8px;
          align-items: center;
          text-align: left;
          padding: 12px 14px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #f7f7f2;
          color: inherit;
          cursor: pointer;
        }

        .review-candidate.active {
          border-color: rgba(20, 82, 67, 0.34);
          background: #ecf5f2;
          box-shadow: 0 0 0 1px rgba(20, 82, 67, 0.1);
        }

        .review-candidate span {
          color: #51615c;
          font-size: 13px;
        }

        .review-footer {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
          padding-top: 4px;
        }

        .review-footer-inline {
          padding-top: 0;
        }

        .master-check-compare {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }

        .master-check-column {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 14px;
          background: #f8f9f5;
          padding: 12px 14px;
          display: grid;
          gap: 6px;
        }

        .master-check-column h3 {
          margin: 0 0 4px;
          font-size: 13px;
          color: #17302c;
        }

        .master-check-column p {
          margin: 0;
          color: #425451;
          font-size: 14px;
        }

        .master-check-diffs {
          display: grid;
          gap: 8px;
        }

        .master-check-diff-row {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          padding: 10px 12px;
          border-radius: 12px;
          background: #f7efe8;
          border: 1px solid rgba(122, 76, 36, 0.14);
          color: #6a4b33;
        }

        .history-header {
          margin-bottom: 12px;
        }

        .history-header h3 {
          margin: 0 0 4px;
          font-size: 15px;
        }

        .history-note {
          color: #667570;
          font-size: 13px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .table-wrap {
          overflow-x: auto;
        }

        .menu-sheet-wrap {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 16px;
          background: #fbfbf8;
          max-height: 72vh;
          overflow: auto;
          overscroll-behavior: contain;
        }

        .sheet-stats {
          display: flex;
          gap: 16px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }

        .sheet-workspace {
          display: grid;
          gap: 18px;
          grid-template-columns: minmax(0, 2.1fr) minmax(300px, 0.9fr);
          align-items: start;
        }

        .menu-sheet-table {
          min-width: 1240px;
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

        .menu-sheet-table thead th {
          position: sticky;
          top: 0;
          z-index: 2;
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .sticky-date {
          position: sticky;
          left: 0;
          background: #f8f5ed;
          z-index: 1;
          min-width: 96px;
        }

        .menu-sheet-table thead .sticky-date,
        .menu-sheet-table thead th:first-child {
          z-index: 3;
        }

        .sheet-cell-stack {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .sheet-column-head {
          display: grid;
          gap: 4px;
          min-width: 150px;
        }

        .sheet-column-head span {
          display: block;
          line-height: 1.35;
        }

        .sheet-chip {
          display: inline-flex;
          align-items: center;
          min-height: 36px;
          padding: 6px 10px;
          border-radius: 10px;
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
          cursor: pointer;
          font: inherit;
          color: inherit;
          text-align: left;
        }

        .sheet-chip.active {
          border-color: rgba(20, 82, 67, 0.34);
          background: #ecf5f2;
          box-shadow: 0 0 0 1px rgba(20, 82, 67, 0.12);
        }

        .sheet-empty {
          color: #8b9793;
        }

        .sheet-inspector {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 16px;
          background: #fcfcfa;
          padding: 16px;
          display: grid;
          gap: 14px;
          position: sticky;
          top: 20px;
        }

        .sheet-inspector__header {
          display: grid;
          gap: 4px;
        }

        .inspector-summary {
          display: grid;
          gap: 8px;
        }

        .inspector-title {
          margin: 0;
          font-size: 18px;
          font-weight: 700;
          color: #17302c;
        }

        .inspector-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .inspector-meta span {
          padding: 5px 9px;
          border-radius: 999px;
          background: #eef2ef;
          color: #48605a;
          font-size: 12px;
          font-weight: 600;
        }

        .inspector-form {
          display: grid;
          gap: 12px;
        }

        .inspector-form label {
          display: grid;
          gap: 6px;
        }

        .inspector-form label span {
          font-size: 12px;
          color: #5f7b74;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }

        .exception-form {
          display: grid;
          gap: 12px;
          padding-top: 8px;
          border-top: 1px solid rgba(25, 32, 30, 0.08);
        }

        .exception-form__header {
          display: grid;
          gap: 4px;
        }

        .exception-form label {
          display: grid;
          gap: 6px;
        }

        .exception-form label span {
          font-size: 12px;
          color: #5f7b74;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }

        .exception-targets {
          display: grid;
          gap: 8px;
        }

        .exception-targets__list {
          display: grid;
          gap: 8px;
          max-height: 220px;
          overflow: auto;
          padding-right: 4px;
        }

        .exception-target {
          display: flex;
          align-items: center;
          gap: 8px;
          color: #2b403b;
          font-size: 14px;
        }

        .menu-list-details summary {
          cursor: pointer;
          font-weight: 700;
          color: #17302c;
        }

        .details-note {
          margin: 10px 0 14px;
        }

        @media (max-width: 1080px) {
          .sheet-workspace {
            grid-template-columns: 1fr;
          }

          .sheet-inspector {
            position: static;
          }

          .review-create-grid {
            grid-template-columns: 1fr;
          }

          .review-candidate {
            grid-template-columns: 1fr 1fr;
          }

          .master-check-compare {
            grid-template-columns: 1fr;
          }

          .panel-row {
            grid-template-columns: 1fr;
          }
        }

        @media (max-width: 720px) {
          .review-backdrop {
            padding: 12px;
          }

          .review-modal {
            padding: 18px;
          }

          .review-header,
          .review-card-head,
          .review-footer {
            grid-template-columns: 1fr;
            display: grid;
          }

          .review-footer {
            justify-content: stretch;
          }
        }
      `}</style>
    </main>
  );
}

export default function MonthlyMenuEditorPage() {
  const router = useRouter();
  const sessionKey = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  const authenticated = useSyncExternalStore(subscribeBrowserSession, hasActiveSessionAuthHeader, () => false);
  return <SawaProvider sessionKey={`hospital-monthly-menu:${sessionKey}`}><MonthlyMenuEditorScreen key={`${sessionKey}:${router.asPath}:${authenticated}`} authenticated={authenticated} /></SawaProvider>;
}
