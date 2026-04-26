import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";
import { DIET_TYPE_OPTIONS, formatDietTypeLabel } from "../../services/menuVocabulary";

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
  category: string;
};

type PendingMenuMasterReview = {
  file: File;
  issues: MenuMasterReviewIssue[];
  resolutions: Record<string, MenuMasterReviewResolution>;
};

type MenuMasterCheckState = {
  issues: MenuMasterReviewIssue[];
  resolutions: Record<string, MenuMasterReviewResolution>;
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

const formatErrorDetail = (detail: unknown, fallback: string) => {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
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
    issue.issue_type === "diff"
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
    menu_master_id: selectedCandidate?.id || "",
    name: normalizeValue(suggestedPatch.name) || getReviewSourceName(issue),
    unit_type: normalizeUnitChoice(selectedCandidate?.unit_type || suggestedPatch.unit_type),
    qty_per_serving: toResolutionQty(selectedCandidate?.qty_per_serving ?? suggestedPatch.qty_per_serving ?? ""),
    category: normalizeValue(String(suggestedPatch.category ?? issue.current_master?.category ?? "")),
  };
};

const isReviewResolutionComplete = (
  issue: MenuMasterReviewIssue,
  resolution?: MenuMasterReviewResolution | null,
) => {
  if (!resolution) return false;
  if (resolution.action === "existing") {
    return Boolean(resolution.menu_master_id);
  }
  if (resolution.action === "update") {
    const qty = Number(resolution.qty_per_serving);
    return (
      Boolean(resolution.name.trim()) &&
      Boolean(normalizeUnitChoice(resolution.unit_type)) &&
      Number.isFinite(qty) &&
      qty > 0
    );
  }
  if (resolution.action === "month_only") {
    if (!Boolean(normalizeUnitChoice(resolution.unit_type))) {
      return false;
    }
    const rawQty = String(resolution.qty_per_serving ?? "").trim();
    if (!rawQty) {
      return true;
    }
    const qty = Number(rawQty);
    return Number.isFinite(qty) && qty > 0;
  }
  if (resolution.action === "category_only") {
    return Boolean(resolution.category.trim());
  }
  if (resolution.action === "create") {
    const qty = Number(resolution.qty_per_serving);
    return (
      Boolean(resolution.name.trim()) &&
      Boolean(normalizeUnitChoice(resolution.unit_type)) &&
      Number.isFinite(qty) &&
      qty > 0
    );
  }
  if ((issue.candidates || []).length === 0) {
    return false;
  }
  return false;
};

const findMatchingItemIndex = (items: MenuItem[], entry: MenuEntry | null) => {
  if (!entry) return -1;
  const entryName = normalizeValue(entry.name);
  const entryScope = normalizeValue(entry.facility_override);
  const entryDietType = normalizeValue(entry.diet_type);

  const exactMatch = items.findIndex(
    (item) =>
      normalizeValue(item.name) === entryName &&
      normalizeValue(item.facility_override) === entryScope &&
      normalizeValue(item.diet_type) === entryDietType,
  );
  if (exactMatch >= 0) return exactMatch;

  const scopeMatch = items.findIndex(
    (item) => normalizeValue(item.name) === entryName && normalizeValue(item.facility_override) === entryScope,
  );
  if (scopeMatch >= 0) return scopeMatch;

  return items.findIndex((item) => normalizeValue(item.name) === entryName);
};

export default function MonthlyMenuEditorPage() {
  const router = useRouter();
  const { monthId } = router.query;
  const [menu, setMenu] = useState<MonthlyMenu | null>(null);
  const [items, setItems] = useState<MenuItem[]>([]);
  const [entries, setEntries] = useState<MenuEntry[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState<string>("");
  const [scopeType, setScopeType] = useState<string>("base");
  const [scopeValue, setScopeValue] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [lastUpload, setLastUpload] = useState<string>("");
  const [uploadHistory, setUploadHistory] = useState<MenuUploadEntry[]>([]);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const [downloadingUploadId, setDownloadingUploadId] = useState<string | null>(null);
  const [facilities, setFacilities] = useState<FacilityOption[]>([]);
  const [tagOptions, setTagOptions] = useState<TagScopeOption[]>([]);
  const [condimentFile, setCondimentFile] = useState<File | null>(null);
  const [condimentUploading, setCondimentUploading] = useState<boolean>(false);
  const [condimentMessage, setCondimentMessage] = useState<string>("");
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [uploadInputKey, setUploadInputKey] = useState<number>(0);
  const [pendingReview, setPendingReview] = useState<PendingMenuMasterReview | null>(null);
  const [reviewSubmitting, setReviewSubmitting] = useState<boolean>(false);
  const [masterCheckState, setMasterCheckState] = useState<MenuMasterCheckState>({ issues: [], resolutions: {} });
  const [masterCheckSavingId, setMasterCheckSavingId] = useState<string | null>(null);
  const [masterCheckNotice, setMasterCheckNotice] = useState<string>("");
  const [entryExceptionDraft, setEntryExceptionDraft] = useState<MenuEntryExceptionDraft | null>(null);
  const [entryExceptionSaving, setEntryExceptionSaving] = useState<boolean>(false);

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
    const item = items.find((row) => normalizeValue(row.id) === itemId);
    return formatScopeLabel(item?.facility_override);
  };

  const selectedEntry = entries.find((entry) => entry.id === selectedEntryId) || null;
  const selectedItemIndex = findMatchingItemIndex(items, selectedEntry);
  const selectedItem = selectedItemIndex >= 0 ? items[selectedItemIndex] : null;

  const loadUploadHistory = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/uploads`);
      setUploadHistory(res.data?.items || []);
    } catch {
      setUploadHistory([]);
    }
  };

  const loadScopeOptions = async () => {
    try {
      const res = await apiClient.get("/monthly-menus/scope-options");
      setFacilities(res.data?.facilities || []);
      setTagOptions(res.data?.tags || []);
    } catch {
      setFacilities([]);
      setTagOptions([]);
    }
  };

  const loadMenu = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}`);
      setMenu(res.data.menu);
      setItems(res.data.items || []);
      setEntries(res.data.entries || []);
      const issues = res.data?.master_checks?.issues || [];
      setMasterCheckState({
        issues,
        resolutions: Object.fromEntries(
          issues.map((issue: MenuMasterReviewIssue, index: number) => {
            const resolution = buildInitialReviewResolution(issue, index);
            return [resolution.issue_key, resolution];
          }),
        ),
      });
      setMessage("");
    } catch (err: any) {
      setMenu(null);
      setItems([]);
      setEntries([]);
      setMasterCheckState({ issues: [], resolutions: {} });
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("権限がありません。月次メニューの操作にはユーザー2以上の権限が必要です。");
      } else if (status === 404) {
        setMessage("月次メニューがまだ登録されていません。");
      } else {
        setMessage("月次メニューの読込に失敗しました。");
      }
    }
    await loadUploadHistory();
  };

  useEffect(() => {
    loadMenu();
    loadScopeOptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monthId]);

  useEffect(() => {
    setMasterCheckNotice("");
  }, [monthId]);

  useEffect(() => {
    if (!entries.length) {
      setSelectedEntryId(null);
      return;
    }
    if (!selectedEntryId || !entries.some((entry) => entry.id === selectedEntryId)) {
      setSelectedEntryId(entries[0].id);
    }
  }, [entries, selectedEntryId]);

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
    setUploading(true);
    setPendingReview(null);
    const submitUpload = async (targetFile: File, reviewResolutions?: MenuMasterReviewResolution[]) => {
      const formData = new FormData();
      formData.append("file", targetFile);
      if (reviewResolutions && reviewResolutions.length > 0) {
        formData.append("review_resolutions", JSON.stringify(reviewResolutions));
      }
      return apiClient.post("/monthly-menus", formData, {
        params: {
          month_id: monthId,
          sheet_name: sheetName || undefined,
          scope_type: scopeType,
          scope_value: scopeType === "base" ? undefined : scopeValue || undefined,
        },
        headers: { "Content-Type": "multipart/form-data" },
      });
    };
    const finalizeUploadSuccess = async (targetFile: File, res: any) => {
      setFile(null);
      setPendingReview(null);
      setUploadInputKey((prev) => prev + 1);
      await loadMenu();
      const itemCount = res.data?.item_count ?? 0;
      const replaced = res.data?.replaced ? "置換" : "新規";
      const scopeLabel = formatScopeLabel(res.data?.scope_override || (scopeType === "base" ? null : scopeValue));
      const uploadMessage = `${targetFile.name} を${replaced}で反映（${itemCount}件 / ${scopeLabel}）`;
      setLastUpload(uploadMessage);
      setMessage("アップロードしました。");
      await loadUploadHistory();
    };
    try {
      const res = await submitUpload(file);
      await finalizeUploadSuccess(file, res);
    } catch (err: any) {
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
        const resolutions = Object.fromEntries(
          issues.map((issue, index) => {
            const resolution = buildInitialReviewResolution(issue, index);
            return [resolution.issue_key, resolution];
          }),
        );
        setPendingReview({
          file,
          issues,
          resolutions,
        });
        setMessage("未登録メニューがあります。既存マスターを使うか、新規登録するか確認してください。");
      } else {
        setMessage(`アップロード失敗: ${formatErrorDetail(detail, "アップロードに失敗しました。")}`);
      }
    } finally {
      setUploading(false);
    }
  };

  const updateReviewResolution = (issueKey: string, patch: Partial<MenuMasterReviewResolution>) => {
    setPendingReview((current) => {
      if (!current) return current;
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

  const updateMasterCheckResolution = (issueKey: string, patch: Partial<MenuMasterReviewResolution>) => {
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
    setPendingReview(null);
  };

  const confirmPendingReview = async () => {
    if (!monthId || Array.isArray(monthId) || !pendingReview) return;
    const unresolved = pendingReview.issues.find((issue, index) => {
      const issueKey = getReviewIssueKey(issue, index);
      return !isReviewResolutionComplete(issue, pendingReview.resolutions[issueKey]);
    });
    if (unresolved) {
      setMessage(`未登録メニューの確認が完了していません: ${getReviewSourceName(unresolved)}`);
      return;
    }
    setReviewSubmitting(true);
    const formData = new FormData();
    formData.append("file", pendingReview.file);
    formData.append("review_resolutions", JSON.stringify(Object.values(pendingReview.resolutions)));
    try {
      const res = await apiClient.post("/monthly-menus", formData, {
        params: {
          month_id: monthId,
          sheet_name: sheetName || undefined,
          scope_type: scopeType,
          scope_value: scopeType === "base" ? undefined : scopeValue || undefined,
        },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setFile(null);
      setPendingReview(null);
      setUploadInputKey((prev) => prev + 1);
      await loadMenu();
      const itemCount = res.data?.item_count ?? 0;
      const replaced = res.data?.replaced ? "置換" : "新規";
      const scopeLabel = formatScopeLabel(res.data?.scope_override || (scopeType === "base" ? null : scopeValue));
      const uploadMessage = `${pendingReview.file.name} を${replaced}で反映（${itemCount}件 / ${scopeLabel}）`;
      setLastUpload(uploadMessage);
      setMessage("アップロードしました。");
      await loadUploadHistory();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("アップロード失敗: 権限がありません。");
      } else {
        setMessage(`アップロード失敗: ${formatErrorDetail(detail, "アップロードに失敗しました。")}`);
      }
    } finally {
      setReviewSubmitting(false);
    }
  };

  const applyMasterCheck = async (issue: MenuMasterReviewIssue, index: number) => {
    if (!monthId || Array.isArray(monthId) || !issue.item_id) return;
    const issueKey = getReviewIssueKey(issue, index);
    const resolution = masterCheckState.resolutions[issueKey];
    if (!isReviewResolutionComplete(issue, resolution)) {
      setMessage(`差分チェックの入力が不足しています: ${getReviewSourceName(issue)}`);
      return;
    }
    setMasterCheckSavingId(issue.item_id);
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
        if (resolution.category.trim()) {
          body.category = resolution.category.trim();
        }
        if (issue.current_master?.id) {
          body.menu_master_id = issue.current_master.id;
        }
      }
      await apiClient.post(`/monthly-menus/${monthId}/master-checks/${issue.item_id}/resolve`, body);
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
      await loadMenu();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("差分反映に失敗しました: 権限がありません。");
      } else {
        setMessage(`差分反映に失敗しました: ${formatErrorDetail(detail, "差分反映に失敗しました。")}`);
      }
    } finally {
      setMasterCheckSavingId(null);
    }
  };

  const handleDownloadUpload = async (upload: MenuUploadEntry) => {
    if (!monthId || Array.isArray(monthId)) return;
    setDownloadingUploadId(upload.id);
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/uploads/${upload.id}/download`, {
        responseType: "blob",
      });
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
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    } finally {
      setDownloadingUploadId(null);
    }
  };

  const handleCondimentUpload = async () => {
    if (!condimentFile) {
      setCondimentMessage("付属品フラグのファイルを選択してください。");
      return;
    }
    setCondimentUploading(true);
    setCondimentMessage("アップロード中...");
    const formData = new FormData();
    formData.append("file", condimentFile);
    try {
      const res = await apiClient.post("/monthly-menus/condiments", formData, {
        params: { sheet_name: "主菜" },
        headers: { "Content-Type": "multipart/form-data" },
      });
      const itemsCount = res.data?.items ?? 0;
      setCondimentMessage(`付属品フラグを反映しました（${itemsCount}件）。`);
      setCondimentFile(null);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setCondimentMessage("反映に失敗しました: 権限がありません。");
      } else {
        setCondimentMessage(detail ? `反映に失敗しました: ${detail}` : "反映に失敗しました。");
      }
    } finally {
      setCondimentUploading(false);
    }
  };

  const updateItemField = (idx: number, field: keyof MenuItem, value: string) => {
    const next = [...items];
    next[idx] = { ...next[idx], [field]: value };
    setItems(next);
  };

  const saveItem = async (item: MenuItem) => {
    if (!monthId || Array.isArray(monthId)) return;
    const qtyValue =
      item.qty_per_serving == null || item.qty_per_serving === ""
        ? null
        : Number(item.qty_per_serving);
    const bagMaxValue =
      item.bag_max_qty == null || item.bag_max_qty === "" ? null : Number(item.bag_max_qty);
    setSavingId(item.id);
    try {
      await apiClient.put(`/monthly-menus/${monthId}/items/${item.id}`, {
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
      });
      setMessage(`保存しました: ${item.name}`);
      await loadMenu();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("保存に失敗しました: 権限がありません。");
      } else {
        setMessage(detail ? `保存に失敗しました: ${detail}` : "保存に失敗しました。");
      }
    } finally {
      setSavingId(null);
    }
  };

  const updateEntryExceptionField = (field: keyof Omit<MenuEntryExceptionDraft, "facilityIds">, value: string) => {
    setEntryExceptionDraft((current) => {
      if (!current) return current;
      return { ...current, [field]: value };
    });
  };

  const toggleEntryExceptionFacility = (facilityId: string) => {
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
      );
      const updatedEntries = Array.isArray(res.data?.entries) ? res.data.entries : [];
      await loadMenu();
      if (updatedEntries.length === 1 && updatedEntries[0]?.id) {
        setSelectedEntryId(String(updatedEntries[0].id));
      }
      setMessage(`例外メニューを保存しました: ${entryExceptionDraft.name.trim()}`);
    } catch (err: any) {
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
      setEntryExceptionSaving(false);
    }
  };

  const daypartOptions = uniqueValues(items, "daypart");
  const categoryOptions = uniqueValues(items, "category");
  const { columns: sheetColumns, rows: sheetDateRows } = buildMenuSheetGrid(entries, formatScopeLabel);
  const canSubmitPendingReview =
    pendingReview?.issues.every((issue, index) =>
      isReviewResolutionComplete(issue, pendingReview.resolutions[getReviewIssueKey(issue, index)]),
    ) ?? false;
  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Monthly Menu</p>
          <h1>月次メニュー編集</h1>
          <p className="subtle">月ID: {Array.isArray(monthId) ? monthId.join(",") : monthId}</p>
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
        <p className="subtle scope-note">
          通常は <strong>共通(base)</strong> を使います。施設だけ違う献立は <strong>施設差分</strong>、複数施設で共通の差分は
          <strong> タグ差分</strong> を選びます。
        </p>
        {scopeType === "tag" && tagOptions.length === 0 && (
          <p className="subtle scope-note">タグがまだありません。施設設定でタグを登録してから選択してください。</p>
        )}
        {message && <p className="message">{message}</p>}
        <div className="upload-history">
          <div className="history-header">
            <h3>これまでのアップロード</h3>
            <p className="subtle">新しい履歴はダウンロードできます。過去分はファイル未保存のため一覧のみです。</p>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>登録日時</th>
                  <th>ファイル名</th>
                  <th>シート名</th>
                  <th>適用先</th>
                  <th>件数</th>
                  <th>更新種別</th>
                  <th>ダウンロード</th>
                </tr>
              </thead>
              <tbody>
                {uploadHistory.length === 0 ? (
                  <tr>
                    <td colSpan={7}>履歴はまだありません。</td>
                  </tr>
                ) : (
                  uploadHistory.map((upload) => (
                    <tr key={upload.id}>
                      <td>{upload.uploaded_at ? new Date(upload.uploaded_at).toLocaleString("ja-JP") : "-"}</td>
                      <td>{upload.filename || "-"}</td>
                      <td>{upload.sheet_name || "-"}</td>
                      <td>{formatScopeLabel(upload.scope_override)}</td>
                      <td>{upload.item_count ?? "-"}</td>
                      <td>{upload.replaced ? "置換" : "新規"}</td>
                      <td>
                        {upload.download_available ? (
                          <button
                            className="btn"
                            type="button"
                            onClick={() => handleDownloadUpload(upload)}
                            disabled={downloadingUploadId === upload.id}
                          >
                            {downloadingUploadId === upload.id ? "取得中..." : "ダウンロード"}
                          </button>
                        ) : (
                          <span className="history-note">
                            {upload.archive_error ? "保存失敗" : "履歴のみ"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
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
            <div className="review-list">
              {pendingReview.issues.map((issue, index) => {
                const issueKey = getReviewIssueKey(issue, index);
                const resolution = pendingReview.resolutions[issueKey];
                const candidates = issue.candidates || [];
                const suggestedPatch = issue.suggested_patch || {};
                return (
                  <article
                    key={issueKey}
                    className="review-card"
                    data-testid={`menu-master-review-card-${index}`}
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
                              既定案: {formatUnitChoiceLabel(suggestedPatch.unit_type)} / {toResolutionQty(suggestedPatch.qty_per_serving) || "-"}
                            </span>
                          )}
                        </div>
                      </div>
                      <span className="review-card-reason">
                        {candidates.length > 0 ? "候補確認が必要" : "新規登録が必要"}
                      </span>
                    </div>

                    <div className="review-mode-row">
                      {candidates.length > 0 && (
                        <label className="review-radio">
                          <input
                            type="radio"
                            name={`review-action-${issueKey}`}
                            checked={resolution.action === "existing"}
                            onChange={() =>
                              updateReviewResolution(issueKey, {
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
                          name={`review-action-${issueKey}`}
                          checked={resolution.action === "create"}
                          onChange={() =>
                            updateReviewResolution(issueKey, {
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

                    {resolution.action === "create" && (
                      <div className="review-create-grid">
                        <label>
                          <span>新規メニュー名</span>
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
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
            <div className="review-footer">
              <button className="btn" type="button" onClick={closePendingReview} disabled={reviewSubmitting}>
                キャンセル
              </button>
              <button
                className="btn primary"
                type="button"
                onClick={confirmPendingReview}
                disabled={!canSubmitPendingReview || reviewSubmitting}
              >
                {reviewSubmitting ? "確認中..." : "この内容でアップロード"}
              </button>
            </div>
          </section>
        </div>
      )}

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>メニューマスター差分チェック</h2>
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
          <div className="review-list">
            {masterCheckState.issues.map((issue, index) => {
              const issueKey = getReviewIssueKey(issue, index);
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
        )}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>付属品フラグ</h2>
          <p className="subtle">献立メニューからソース等の付属品フラグを反映します。</p>
        </header>
        <div className="upload-actions">
          <input type="file" onChange={(e) => setCondimentFile(e.target.files?.[0] || null)} />
          <button className="btn primary" onClick={handleCondimentUpload} disabled={condimentUploading}>
            {condimentUploading ? "反映中..." : "反映する"}
          </button>
        </div>
        {condimentMessage && <p className="message">{condimentMessage}</p>}
      </section>

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
                                    onClick={() => setSelectedEntryId(entry.id)}
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
                {selectedItem ? (
                  <div className="inspector-form">
                    <label>
                      <span>単位</span>
                      <select
                        className="input"
                        value={selectedItem.unit_type || ""}
                        onChange={(e) => updateItemField(selectedItemIndex, "unit_type", e.target.value)}
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
                        value={selectedItem.qty_per_serving ?? ""}
                        onChange={(e) => updateItemField(selectedItemIndex, "qty_per_serving", e.target.value)}
                      />
                    </label>
                    <label>
                      <span>袋最大量</span>
                      <input
                        className="input"
                        type="number"
                        value={selectedItem.bag_max_qty ?? ""}
                        onChange={(e) => updateItemField(selectedItemIndex, "bag_max_qty", e.target.value)}
                      />
                    </label>
                    <label>
                      <span>袋単位</span>
                      <select
                        className="input"
                        value={selectedItem.bag_max_unit || ""}
                        onChange={(e) => updateItemField(selectedItemIndex, "bag_max_unit", e.target.value)}
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
                        value={selectedItem.temp_type || ""}
                        onChange={(e) => updateItemField(selectedItemIndex, "temp_type", e.target.value)}
                      >
                        {tempTypeChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>時間帯</span>
                      <input
                        className="input"
                        value={selectedItem.daypart || ""}
                        list="daypart-options"
                        onChange={(e) => updateItemField(selectedItemIndex, "daypart", e.target.value)}
                      />
                    </label>
                    <label>
                      <span>区分</span>
                      <input
                        className="input"
                        value={selectedItem.category || ""}
                        list="category-options"
                        onChange={(e) => updateItemField(selectedItemIndex, "category", e.target.value)}
                      />
                    </label>
                    <label>
                      <span>食種</span>
                      <select
                        className="input"
                        value={selectedItem.diet_type || ""}
                        onChange={(e) => updateItemField(selectedItemIndex, "diet_type", e.target.value)}
                      >
                        {DIET_TYPE_OPTIONS.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button className="btn primary" onClick={() => saveItem(selectedItem)} disabled={savingId === selectedItem.id}>
                      {savingId === selectedItem.id ? "保存中..." : "この設定を保存"}
                    </button>
                  </div>
                ) : (
                  <p className="subtle">対応するメニュー設定がまだありません。必要なら下の補助一覧を開いて確認してください。</p>
                )}
                <div className="exception-form" data-testid="entry-exception-form">
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
          <summary>メニュー一覧（補助）</summary>
          <p className="subtle details-note">補助の全件一覧です。通常は上の月次シートから選択して確認します。</p>
          <div className="table-wrap" data-testid="monthly-menu-item-table">
            <table>
              <thead>
                <tr>
                  <th>メニュー名</th>
                  <th>単位</th>
                  <th>量</th>
                  <th>袋最大量</th>
                  <th>袋単位</th>
                  <th>温冷</th>
                  <th>時間帯</th>
                  <th>区分</th>
                  <th>食種</th>
                  <th>適用先</th>
                  <th>保存</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={11}>メニューがありません。</td>
                  </tr>
                ) : (
                  items.map((item, idx) => (
                    <tr key={item.id}>
                      <td>
                        <input
                          className="input"
                          value={item.name}
                          onChange={(e) => updateItemField(idx, "name", e.target.value)}
                        />
                      </td>
                      <td>
                        <select
                          className="input"
                          value={item.unit_type || ""}
                          onChange={(e) => updateItemField(idx, "unit_type", e.target.value)}
                        >
                          <option value="">未選択</option>
                          {unitChoices.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          className="input"
                          type="number"
                          value={item.qty_per_serving ?? ""}
                          onChange={(e) => updateItemField(idx, "qty_per_serving", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="input"
                          type="number"
                          value={item.bag_max_qty ?? ""}
                          onChange={(e) => updateItemField(idx, "bag_max_qty", e.target.value)}
                        />
                      </td>
                      <td>
                        <select
                          className="input"
                          value={item.bag_max_unit || ""}
                          onChange={(e) => updateItemField(idx, "bag_max_unit", e.target.value)}
                        >
                          <option value="">未選択</option>
                          {unitChoices.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select
                          className="input"
                          value={item.temp_type || ""}
                          onChange={(e) => updateItemField(idx, "temp_type", e.target.value)}
                        >
                          {tempTypeChoices.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          className="input"
                          value={item.daypart || ""}
                          list="daypart-options"
                          onChange={(e) => updateItemField(idx, "daypart", e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="input"
                          value={item.category || ""}
                          list="category-options"
                          onChange={(e) => updateItemField(idx, "category", e.target.value)}
                        />
                      </td>
                      <td>
                        <select
                          className="input"
                          value={item.diet_type || ""}
                          onChange={(e) => updateItemField(idx, "diet_type", e.target.value)}
                        >
                          {DIET_TYPE_OPTIONS.map((choice) => (
                            <option key={choice.value} value={choice.value}>
                              {choice.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          className="input"
                          value={item.facility_override || ""}
                          onChange={(e) => updateItemField(idx, "facility_override", e.target.value)}
                          placeholder="共通(base) / FACxxxx / TAG:xxx"
                        />
                      </td>
                      <td>
                        <button className="btn" onClick={() => saveItem(item)} disabled={savingId === item.id}>
                          {savingId === item.id ? "保存中..." : "保存"}
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
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

        .review-card {
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 18px;
          padding: 16px;
          background: #ffffff;
          display: grid;
          gap: 14px;
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
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
