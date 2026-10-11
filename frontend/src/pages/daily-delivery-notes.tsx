import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { Button, Checkbox, Input, MenuItem, NativeSelect, Stack } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { FormTextField, SawaProvider } from "@sawa/ui";
import { FormProvider, useForm } from "react-hook-form";
import TopNav from "../components/TopNav";
import { apiClient, hasActiveSessionAuthHeader } from "../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../services/browserSession";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidates,
  pickBestFacilityCandidate,
  type FacilityHint,
  type FacilityNameMap,
} from "../services/facilityData";

type OrderSummary = {
  id: string;
  facility?: string | null;
  week?: string | null;
  status?: string | null;
  received_at?: string | null;
  line_count?: number | null;
};

type DailyConditions = {
  date: string;
  status: string;
};

type DailyBagBreakdown = {
  amount_label?: string | null;
  count?: number | null;
  order_refs?: {
    order_id?: string | null;
    facility_label?: string | null;
    area_id?: string | null;
    quantity?: number | null;
    value_source?: string | null;
    was_user_edited?: boolean | null;
    ocr_confidence?: number | null;
    ocr_confidence_tier?: string | null;
  }[];
};

type DailyBagTypeGroup = {
  bag_type?: string | null;
  bag_count?: number | null;
  total_quantity?: number | null;
  total_amount_label?: string | null;
  breakdowns?: DailyBagBreakdown[];
};

type DailyBagDietGroup = {
  diet_type?: string | null;
  total_quantity?: number | null;
  total_amount_label?: string | null;
  calculation_basis_label?: string | null;
  bag_type_groups?: DailyBagTypeGroup[];
};

type DailyBagMenuGroup = {
  daypart?: string | null;
  daypart_key?: string | null;
  menu_category?: string | null;
  menu_name?: string | null;
  diet_groups?: DailyBagDietGroup[];
};

type DailyBagSummaryResponse = {
  date?: string | null;
  order_count?: number | null;
  groups?: DailyBagMenuGroup[];
};

type DailyBagAuditFinding = {
  finding_id?: string | null;
  rule_code?: string | null;
  severity?: "high" | "medium" | "low" | string | null;
  title?: string | null;
  message?: string | null;
  score?: number | null;
  target?: {
    date?: string | null;
    daypart?: string | null;
    menu_category?: string | null;
    menu_name?: string | null;
    diet_type?: string | null;
    facility_label?: string | null;
    order_id?: string | null;
    quantity?: number | null;
    value_source?: string | null;
    was_user_edited?: boolean | null;
    ocr_confidence?: number | null;
    ocr_confidence_tier?: string | null;
    cell_image_ref?: string | null;
  } | null;
  reference_values?: string[] | null;
  suggested_action?: string | null;
};

type DailyBagAuditResponse = {
  date?: string | null;
  source?: string | null;
  rule_based?: {
    status?: string | null;
    finding_count?: number | null;
    cell_count?: number | null;
    summary_by_severity?: Record<string, number> | null;
    summary_by_rule?: Record<string, number> | null;
    findings?: DailyBagAuditFinding[] | null;
  } | null;
  ai?: {
    status?: string | null;
    model?: string | null;
    notes?: string | null;
    error?: string | null;
    items?: {
      finding_id?: string | null;
      priority?: string | null;
      summary?: string | null;
      operator_action?: string | null;
    }[] | null;
  } | null;
};

type DailyOutputOverrideVariant = {
  menu_name?: string | null;
  daypart?: string | null;
  menu_category?: string | null;
  unit_type?: string | null;
  qty_per_serving?: number | null;
  basis_label?: string | null;
  order_ids?: string[] | null;
};

type DailyOutputOverrideRow = {
  facility_id?: string | null;
  facility_label?: string | null;
  diet_type?: string | null;
  order_count?: number | null;
  total_quantity?: number | null;
  current_basis_label?: string | null;
  current_qty_per_serving?: number | null;
  current_unit_type?: string | null;
  requires_intervention?: boolean | null;
  current_variants?: DailyOutputOverrideVariant[] | null;
  override?: {
    id?: string | null;
    unit_type?: string | null;
    qty_per_serving?: number | null;
    note?: string | null;
  } | null;
};

type DailyOutputOverrideResponse = {
  original_receipt?: Record<string, unknown> | null;
  date?: string | null;
  daypart?: string | null;
  menu_name?: string | null;
  menu_category?: string | null;
  rows?: DailyOutputOverrideRow[];
};

type DailyOutputOverrideDraft = {
  qty_per_serving: string;
  unit_type: string;
  note: string;
  acknowledge_ambiguous: boolean;
};

type DailyOutputOverrideBulkDraft = {
  qty_per_serving: string;
  unit_type: string;
  note: string;
};

type TotalRow = {
  date?: string | null;
  daypart?: string | null;
  menu_category?: string | null;
  menu_name?: string | null;
  diet_type?: string | null;
  quantity?: number | null;
};

type DailyMealCount = {
  diet_type?: string | null;
  quantity?: number | null;
};

type DailyMealCountGroup = {
  daypart?: string | null;
  counts?: DailyMealCount[];
};

type DailyMealCountUnconfirmedOrder = {
  order_id?: string | null;
  facility_id?: string | null;
  status?: string | null;
};

type DailyMealCountSummary = {
  date?: string | null;
  groups?: DailyMealCountGroup[];
  unconfirmed_orders?: DailyMealCountUnconfirmedOrder[];
  inconsistent_counts?: (DailyMealCountUnconfirmedOrder & {
    daypart?: string | null;
    diet_type?: string | null;
    area_id?: string | null;
    quantities?: number[];
  })[];
};

const dietTypeLabels: Record<string, string> = {
  regular: "常食",
  regular_bag: "常食(袋分け)",
  soft: "軟菜",
  soft_mixer: "軟菜/ミキサー",
  mixer: "ミキサー",
  daycare: "通所",
  staff: "職員",
  forbidden: "禁食",
  tea: "お茶",
  business: "事業",
  diabetes: "糖尿",
  pregnancy: "妊娠",
  sesame_allergy: "ゴマアレルギー",
  no_fried: "禁食(揚げ物禁)",
  no_meat: "禁食(肉禁)",
  forbidden_other: "禁食(肉卵魚禁)",
  no_fish: "禁食(魚禁)",
  change_1: "変更1",
  change_2: "変更2",
  regular_1600kcal: "常食1600kcal",
  soft_1600kcal: "軟菜1600kcal",
  mixer_1600kcal: "ミキサー1600kcal",
  "1600kcal": "1600kcal",
  placeholder: "-",
  unknown: "不明",
};

const bagTypeLabels: Record<string, string> = {
  standard: "標準",
  condiment: "付属品",
  small: "小",
  medium: "中",
  large: "大",
};

const preferredDietOrder = [
  "regular",
  "soft",
  "mixer",
  "forbidden",
  "tea",
  "business",
  "diabetes",
  "pregnancy",
  "sesame_allergy",
  "change_1",
  "change_2",
  "1600kcal",
  "placeholder",
  "unknown",
];

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

const formatQuantity = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString("ja-JP");
};

const auditSeverityLabels: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const auditRuleLabels: Record<string, string> = {
  same_day_diet_total_outlier: "同日同区分の外れ値",
  facility_same_day_outlier: "同施設内の外れ値",
  low_confidence_unedited_ocr: "低信頼OCR未編集",
};

const formatAuditSeverity = (value?: string | null) => {
  const token = String(value || "").trim().toLowerCase();
  return auditSeverityLabels[token] || token || "-";
};

const formatAuditRule = (value?: string | null) => {
  const token = String(value || "").trim();
  return auditRuleLabels[token] || token || "-";
};

const formatAuditTarget = (finding: DailyBagAuditFinding) => {
  const target = finding.target || {};
  const parts = [
    target.facility_label || target.order_id || "",
    target.daypart || "",
    target.menu_name || "",
    target.diet_type ? formatDietType(target.diet_type) : "",
  ].filter(Boolean);
  return parts.join(" / ") || "-";
};

const formatOcrScore = (value?: { ocr_confidence?: number | null; ocr_confidence_tier?: string | null; value_source?: string | null; was_user_edited?: boolean | null }) => {
  if (!value) return "";
  const score = typeof value.ocr_confidence === "number" && Number.isFinite(value.ocr_confidence)
    ? `OCR ${value.ocr_confidence.toFixed(2)}`
    : "";
  const tier = value.ocr_confidence_tier ? `信頼度 ${value.ocr_confidence_tier}` : "";
  const source = value.value_source ? `由来 ${value.value_source}` : "";
  const edited = value.was_user_edited === true ? "編集済" : value.was_user_edited === false ? "未編集" : "";
  return [score, tier, source, edited].filter(Boolean).join(" / ");
};

const formatBagOrderRef = (value: NonNullable<DailyBagBreakdown["order_refs"]>[number]) => {
  const parts = [
    value.facility_label || value.order_id || "注文",
    value.area_id ? `${value.area_id}` : "",
    value.quantity != null && !Number.isNaN(value.quantity) ? `${formatQuantity(value.quantity)}食` : "",
    formatOcrScore(value),
  ].filter(Boolean);
  return parts.join(" / ");
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

const headerValueToString = (value: unknown) => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => String(item)).join("; ");
  if (value == null) return "";
  return String(value);
};

const extractErrorDetail = async (err: any) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  const data = err?.response?.data;
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      const text = await data.text();
      if (!text) return "";
      const parsed = JSON.parse(text);
      if (typeof parsed?.detail === "string" && parsed.detail) return parsed.detail;
      return text;
    } catch {
      return "";
    }
  }
  return "";
};

const normalizeDietType = (value?: string | null) => {
  const token = String(value || "").trim();
  return token || "unknown";
};

const formatDietType = (value?: string | null) => {
  const token = normalizeDietType(value);
  return dietTypeLabels[token] || token;
};

const formatBagType = (value?: string | null) => {
  const token = String(value || "").trim();
  if (!token) return "-";
  return bagTypeLabels[token] || bagTypeLabels[token.toLowerCase()] || token;
};

const buildDailyOutputOverrideRowKey = (row: DailyOutputOverrideRow) =>
  `${String(row.facility_id || "").trim()}__${String(row.diet_type || "").trim() || "unknown"}`;

const overrideUnitOptions = [
  { value: "g", label: "グラム" },
  { value: "切", label: "切れ" },
  { value: "個", label: "個" },
];

const normalizeOverrideUnitValue = (value?: string | null) => {
  const raw = String(value || "").trim();
  if (!raw) return "g";
  const compact = raw.toLowerCase().replace(/[　\s]+/g, "");
  if (compact === "g" || compact === "ｇ" || raw.includes("グラム")) return "g";
  if (raw.includes("切") || raw.includes("枚") || compact === "cut" || compact === "slice" || compact === "slices")
    return "切";
  if (raw.includes("個") || compact === "count" || compact === "piece" || compact === "pieces") return "個";
  return "g";
};

const formatOverrideUnitLabel = (value?: string | null) => {
  const normalized = normalizeOverrideUnitValue(value);
  return overrideUnitOptions.find((option) => option.value === normalized)?.label || normalized;
};

const sumDietQuantity = (group?: DailyBagMenuGroup | null) => {
  const diets = Array.isArray(group?.diet_groups) ? group?.diet_groups : [];
  return diets.reduce((sum, diet) => sum + Number(diet?.total_quantity || 0), 0);
};

const buildDaypartGroups = (groups: DailyBagMenuGroup[]) => {
  const map = new Map<string, { daypart: string; rows: DailyBagMenuGroup[] }>();
  groups.forEach((group) => {
    const daypart = String(group.daypart || group.daypart_key || "-").trim() || "-";
    const existing = map.get(daypart) || { daypart, rows: [] as DailyBagMenuGroup[] };
    existing.rows.push(group);
    map.set(daypart, existing);
  });
  return Array.from(map.values()).sort((left, right) => left.daypart.localeCompare(right.daypart, "ja"));
};

const sectionData = <T,>(section: any): T | null => {
  if (!section || section.status !== "fulfilled") return null;
  return section.data || {};
};

const sectionErrorMessage = (section: any, fallback: string) => {
  if (!section || section.status !== "rejected") return fallback;
  const message = String(section.error?.message || "").trim();
  const type = String(section.error?.type || "").trim();
  if (message && type) return `${fallback} (${type}: ${message})`;
  if (message) return `${fallback} (${message})`;
  return fallback;
};

function DailyDeliveryNotesScreen() {
  const [date, setDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [status, setStatus] = useState<string>("");
  const conditionForm = useForm<DailyConditions>({ defaultValues: { date, status } });
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const loadGeneration = useRef(0);
  const [message, setMessage] = useState("");
  const [bagMessage, setBagMessage] = useState("");
  const [totalsMessage, setTotalsMessage] = useState("");
  const [auditMessage, setAuditMessage] = useState("");
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});
  const [dailyBagSummary, setDailyBagSummary] = useState<DailyBagSummaryResponse>({});
  const [dailyBagAudit, setDailyBagAudit] = useState<DailyBagAuditResponse>({});
  const [auditAiLoading, setAuditAiLoading] = useState(false);
  const [totalsRows, setTotalsRows] = useState<TotalRow[]>([]);
  const [mealCountSummary, setMealCountSummary] = useState<DailyMealCountSummary>({});
  const [mealCountMessage, setMealCountMessage] = useState("");
  const [overrideEditor, setOverrideEditor] = useState<DailyOutputOverrideResponse | null>(null);
  const [overrideEditorLoading, setOverrideEditorLoading] = useState(false);
  const [overrideEditorMessage, setOverrideEditorMessage] = useState("");
  const [overrideEditorDrafts, setOverrideEditorDrafts] = useState<Record<string, DailyOutputOverrideDraft>>({});
  const [overrideEditorBulkDraft, setOverrideEditorBulkDraft] = useState<DailyOutputOverrideBulkDraft>({
    qty_per_serving: "",
    unit_type: "g",
    note: "",
  });
  const [overrideEditorSavingKey, setOverrideEditorSavingKey] = useState("");
  const [overrideEditorBulkSaving, setOverrideEditorBulkSaving] = useState(false);
  const [overrideEditorSelectedFacilityId, setOverrideEditorSelectedFacilityId] = useState("");
  const [overrideEditorSelectedDietType, setOverrideEditorSelectedDietType] = useState("");
  const mounted = useRef(true), contextGeneration = useRef(0), editorGeneration = useRef(0);
  const operations = useRef<Record<string, number>>({});
  const rowEdits = useRef<Record<string, number>>({}), bulkEdits = useRef(0);
  const mainPending = useRef(false);
  const writeFeedback = useRef(0);
  const pendingLoadOwner = useRef<(() => boolean) | null>(null), pendingEditorOwner = useRef<(() => boolean) | null>(null);
  const appliedConditions = useRef<DailyConditions>({ date, status });
  const session = browserSessionKey();
  const retire = () => {
    ++contextGeneration.current; ++loadGeneration.current; ++writeFeedback.current;
    mainPending.current = false;
    pendingLoadOwner.current = null; pendingEditorOwner.current = null;
    setLoading(false); setAuditAiLoading(false); setOverrideEditorLoading(false);
    setOverrideEditorSavingKey(""); setOverrideEditorBulkSaving(false);
    setMessage(""); setBagMessage(""); setTotalsMessage(""); setMealCountMessage("");
    setAuditMessage(""); setOverrideEditorMessage(""); conditionForm.clearErrors("root");
  };
  const capture = (group: string, editor = false, rowKey?: string, bulk = false) => {
    const generation = contextGeneration.current, editorVersion = editorGeneration.current;
    const input = JSON.stringify(conditionForm.getValues());
    const applied = JSON.stringify(appliedConditions.current);
    const outputInput = JSON.stringify([rowEdits.current, bulkEdits.current]);
    const draft = rowKey ? rowEdits.current[rowKey] ?? 0 : bulk ? bulkEdits.current : 0;
    const operation = operations.current[group] = (operations.current[group] ?? 0) + 1;
    return () => mounted.current && session === browserSessionKey() && hasActiveSessionAuthHeader()
      && generation === contextGeneration.current && input === JSON.stringify(conditionForm.getValues())
      && (group === "load" || applied === JSON.stringify(appliedConditions.current))
      && (group !== "output" || outputInput === JSON.stringify([rowEdits.current, bulkEdits.current]))
      && operation === operations.current[group] && (!editor || editorVersion === editorGeneration.current)
      && (!rowKey || draft === (rowEdits.current[rowKey] ?? 0)) && (!bulk || draft === bulkEdits.current);
  };
  const releaseRetiredPending = () => {
    if (pendingLoadOwner.current && !pendingLoadOwner.current()) { pendingLoadOwner.current = null; mainPending.current = false; setLoading(false); }
    if (pendingEditorOwner.current && !pendingEditorOwner.current()) { pendingEditorOwner.current = null; setOverrideEditorLoading(false); }
  };
  const retireOutput = () => { operations.current.output = (operations.current.output ?? 0) + 1; setMessage(""); };
  useEffect(() => {
    mounted.current = true;
    let input = JSON.stringify(conditionForm.getValues());
    const subscription = conditionForm.watch(values => {
      const next = JSON.stringify(values);
      if (next !== input) { input = next; retire(); }
    });
    const unsubscribe = subscribeBrowserSession(retire);
    return () => {
      mounted.current = false; ++contextGeneration.current; ++loadGeneration.current; ++editorGeneration.current;
      subscription.unsubscribe(); unsubscribe();
    };
  }, [conditionForm]);

  const facilityNamesQuery = useQuery({
    queryKey: ["daily-delivery-notes", "facilities"],
    queryFn: fetchFacilityNameMap,
  });

  useEffect(() => {
    if (facilityNamesQuery.data) setFacilityNameMap(facilityNamesQuery.data);
    if (facilityNamesQuery.isError) setFacilityNameMap({});
  }, [facilityNamesQuery.data, facilityNamesQuery.isError]);

  useEffect(() => {
    let cancelled = false;
    const own = capture("facility-hints");
    const unresolved = orders
      .filter((order) => !order.facility && order.id)
      .slice(0, 50)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !facilityHints[orderId]);

    if (unresolved.length === 0) return;

    const queue = [...unresolved];
    const results: Record<string, FacilityHint> = {};
    const workers = Array.from({ length: 2 }, async () => {
      while (queue.length > 0) {
        if (cancelled || !own()) return;
        const orderId = queue.shift();
        if (!orderId) continue;
        try {
          const candidates = await fetchOrderFacilityCandidates(orderId);
          if (cancelled || !own()) return;
          const best = pickBestFacilityCandidate(candidates);
          if (best) results[orderId] = { ...best, order_id: orderId };
        } catch {
          // ignore
        }
      }
    });

    Promise.all(workers).then(() => {
      if (cancelled || !own()) return;
      if (Object.keys(results).length === 0) return;
      setFacilityHints((prev) => ({ ...prev, ...results }));
    });

    return () => {
      cancelled = true;
    };
  }, [orders, facilityHints]);

  const facilityLabel = (order: OrderSummary) => {
    const facilityId = order.facility || "";
    if (facilityId) {
      const name = facilityNameMap[facilityId];
      return name ? `${name} (${facilityId})` : facilityId;
    }
    const orderId = order.id || "";
    const hint = orderId ? facilityHints[orderId] : null;
    if (hint?.facility_name) {
      const score = hint.score != null ? ` / score=${hint.score}` : "";
      return `推定: ${hint.facility_name} (${hint.facility_id}${score})`;
    }
    return "未確定";
  };

  const loadOrders = async (conditions: DailyConditions = { date, status }, parentOwner?: () => boolean) => {
    if (!conditions.date) return;
    // An internal refresh cannot supersede an explicit display request.
    if (parentOwner && (!parentOwner() || mainPending.current)) return;
    if (!parentOwner) retire();
    operations.current.audit = (operations.current.audit ?? 0) + 1;
    let auditOperation = operations.current.audit;
    let outputOperation = operations.current.output;
    const ownsAudit = () => auditOperation === operations.current.audit;
    const ownsMessage = () => outputOperation === operations.current.output;
    setAuditAiLoading(false); setAuditMessage("");
    const generation = ++loadGeneration.current;
    const pageOwner = capture("load");
    const own = () => generation === loadGeneration.current && pageOwner() && (!parentOwner || parentOwner());
    pendingLoadOwner.current = own;
    if (!parentOwner) mainPending.current = true;
    setLoading(true);
    try {
      const params: Record<string, string> = { date: conditions.date };
      if (conditions.status) params.status = conditions.status;
      const responses = await Promise.all((["primary", "bags", "totals"] as const).map(async section => {
        try {
          const response = await apiClient.get("/orders/daily-output-context", { params: { ...params, section } });
          if (response.data?.date !== conditions.date || response.data?.status !== (conditions.status || null)
            || response.data?.facility !== null) throw new Error("集計の取得対象が一致しません。");
          return { section, response, error: null as any };
        } catch (error: any) { return { section, response: null, error }; }
      }));
      if (!own()) return;
      const primary = responses.find(result => result.section === "primary")!;
      if (primary.error || !sectionData<{ orders?: OrderSummary[] }>(primary.response?.data?.sections?.orders)) {
        const error = primary.error ?? new Error(sectionErrorMessage(primary.response?.data?.sections?.orders, "注文一覧の取得に失敗しました。"));
        const detail = error?.response?.data?.detail;
        const text = detail ? `取得に失敗しました: ${detail}` : error instanceof Error ? error.message : "取得に失敗しました。";
        if (ownsMessage()) setMessage(text);
        setMealCountMessage(text);
        throw error;
      }
      responses.forEach(({ section, response, error }) => {
        try {
          if (error) throw error;
          const contextRes = response!;
          const sections = contextRes.data?.sections || {};
          if (section === "primary") {
            const ordersPayload = sectionData<{ orders?: OrderSummary[] }>(sections.orders);
            if (!ordersPayload) {
              throw new Error(sectionErrorMessage(sections.orders, "注文一覧の取得に失敗しました。"));
            }
            const items = Array.isArray(ordersPayload?.orders) ? ordersPayload.orders : [];
            const appliedChanged = JSON.stringify(appliedConditions.current) !== JSON.stringify(conditions);
            appliedConditions.current = { ...conditions };
            releaseRetiredPending();
            if (appliedChanged) {
              auditOperation = operations.current.audit = (operations.current.audit ?? 0) + 1;
              retireOutput();
              outputOperation = operations.current.output;
              setAuditAiLoading(false); setOverrideEditorSavingKey(""); setOverrideEditorBulkSaving(false);
            }
            setDate(conditions.date);
            setStatus(conditions.status);
            setOrders(items);
            if (ownsMessage()) setMessage("");
            setMealCountMessage("");
            if (!items.length) {
              if (sections.orders?.status === "rejected") {
                if (ownsMessage()) setMessage(sectionErrorMessage(sections.orders, "注文一覧の取得に失敗しました。"));
              } else {
                if (ownsMessage()) setMessage("該当する注文がありません。");
              }
            }
          }
          if (section === "bags") {
            setBagMessage("");
            if (ownsAudit()) setAuditMessage("");
            const bagPayload = sectionData<DailyBagSummaryResponse>(sections.daily_bags);
            if (bagPayload) {
              const payload = bagPayload || {};
              setDailyBagSummary(payload);
              const count = Array.isArray(payload.groups) ? payload.groups.length : 0;
              if (!count) {
                setBagMessage("袋分け結果がまだ生成されていません。");
              }
            } else {
              setDailyBagSummary({});
              setBagMessage(sectionErrorMessage(sections.daily_bags, "袋分け結果の取得に失敗しました。"));
            }

            const auditPayload = sectionData<DailyBagAuditResponse>(sections.daily_bags_audit);
            if (ownsAudit() && auditPayload) {
              const payload = auditPayload || {};
              setDailyBagAudit(payload);
              if (!Number(payload?.rule_based?.finding_count || 0)) {
                setAuditMessage("ルールベース監査で確認候補はありません。");
              }
            } else if (ownsAudit()) {
              setDailyBagAudit({});
              setAuditMessage(sectionErrorMessage(sections.daily_bags_audit, "数量監査の取得に失敗しました。"));
            }
          }
          if (section === "totals") {
            setTotalsMessage("");
            const totalsPayload = sectionData<{ rows?: TotalRow[] }>(sections.totals);
            if (totalsPayload) {
              const rows = Array.isArray(totalsPayload.rows) ? totalsPayload.rows : [];
              setTotalsRows(rows);
              if (!rows.length) {
                setTotalsMessage("総量は確定注文のみ集計されるため、対象データがありません。");
              }
            } else {
              setTotalsRows([]);
              setTotalsMessage(sectionErrorMessage(sections.totals, "総量の取得に失敗しました。"));
            }
          }
          if (section === "primary") {
            const mealCountsPayload = sectionData<DailyMealCountSummary>(sections.meal_counts);
            if (mealCountsPayload) {
              const payload = mealCountsPayload || {};
              setMealCountSummary(payload);
              if (!Array.isArray(payload.groups) || payload.groups.length === 0) {
                setMealCountMessage("確定済みの食数がありません。");
              }
            } else {
              setMealCountSummary({});
              setMealCountMessage(sectionErrorMessage(sections.meal_counts, "食数集計の取得に失敗しました。"));
            }
          }
        } catch (err: any) {
          if (!own()) return;
          const detail = err?.response?.data?.detail;
          const error = detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。";
          if (section === "primary") {
            if (ownsMessage()) setMessage(error);
            setMealCountMessage(error);
            throw err;
          } else if (section === "bags") {
            setDailyBagSummary({});
            setBagMessage(error);
            if (ownsAudit()) { setDailyBagAudit({}); setAuditMessage(error); }
          } else {
            setTotalsRows([]);
            setTotalsMessage(error);
          }
        }
      });
    } catch (err) {
      if (own()) {
        if (!parentOwner) conditionForm.setError("root", { message: err instanceof Error ? err.message : "集計の取得に失敗しました。" });
        throw err;
      }
    } finally {
      if (own()) {
        mainPending.current = false;
        setLoading(false);
      }
    }
  };

  const runAuditAi = async () => {
    if (!date) return;
    const own = capture("audit");
    if (!own()) return;
    setAuditAiLoading(true);
    setAuditMessage("Gemini補助を実行中です...");
    try {
      const params: Record<string, string | boolean> = { date, use_ai: true };
      if (status) params.status = status;
      const res = await apiClient.get("/orders/daily-bags/audit", { params, timeout: 0 });
      if (!own()) return;
      setDailyBagAudit(res.data || {});
      const aiStatus = String(res.data?.ai?.status || "");
      if (aiStatus === "completed") {
        setAuditMessage("Gemini補助が完了しました。");
      } else if (aiStatus === "unavailable") {
        setAuditMessage("Gemini補助はAPIキー未設定のため実行できません。ルールベース監査は利用できます。");
      } else if (aiStatus === "failed") {
        setAuditMessage(`Gemini補助に失敗しました: ${res.data?.ai?.error || "unknown"}`);
      } else {
        setAuditMessage("Gemini補助は実行対象がありません。");
      }
    } catch (err: any) {
      if (!own()) return;
      const detail = err?.response?.data?.detail;
      setAuditMessage(detail ? `Gemini補助に失敗しました: ${detail}` : "Gemini補助に失敗しました。");
    } finally {
      if (own()) setAuditAiLoading(false);
    }
  };

  const closeOverrideEditor = () => {
    ++editorGeneration.current; ++writeFeedback.current;
    retireOutput();
    releaseRetiredPending();
    setOverrideEditor(null);
    setOverrideEditorLoading(false);
    setOverrideEditorMessage("");
    setOverrideEditorDrafts({});
    setOverrideEditorBulkDraft({ qty_per_serving: "", unit_type: "g", note: "" });
    setOverrideEditorSavingKey("");
    setOverrideEditorBulkSaving(false);
    setOverrideEditorSelectedFacilityId("");
    setOverrideEditorSelectedDietType("");
  };

  const openOverrideEditor = async (menuGroup: DailyBagMenuGroup, refresh?: { own: () => boolean; rowKey?: string; bulk?: boolean }) => {
    if (!date) return;
    if (refresh && !refresh.own()) return;
    if (!refresh) { closeOverrideEditor(); ++editorGeneration.current; }
    const pageOwner = capture("editor-read", true);
    const own = () => pageOwner() && (!refresh || refresh.own());
    if (!own()) return;
    pendingEditorOwner.current = own;
    setOverrideEditorLoading(true);
    setOverrideEditorMessage("");
    if (!refresh) { setOverrideEditorDrafts({}); setOverrideEditorBulkSaving(false); }
    try {
      const res = await apiClient.get("/orders/daily-output-overrides", {
        params: {
          date,
          daypart: menuGroup.daypart || menuGroup.daypart_key || "",
          menu_name: menuGroup.menu_name || "",
          menu_category: menuGroup.menu_category || "",
        },
      });
      if (!own()) return;
      const payload: DailyOutputOverrideResponse = res.data || {};
      if (payload.date !== date || typeof payload.daypart !== "string" || !payload.daypart.trim()
        || typeof payload.menu_name !== "string" || !payload.menu_name.trim()
        || (refresh && (payload.daypart !== overrideEditor?.daypart || payload.menu_name !== overrideEditor?.menu_name
          || payload.menu_category !== overrideEditor?.menu_category))) throw new Error("編集対象の取得値が一致しません。");
      const rows = Array.isArray(payload.rows) ? payload.rows : [];
      const nextDrafts: Record<string, DailyOutputOverrideDraft> = {};
      rows.forEach((row) => {
        const key = buildDailyOutputOverrideRowKey(row);
        nextDrafts[key] = {
          qty_per_serving:
            row.override?.qty_per_serving != null
              ? String(row.override.qty_per_serving)
              : row.current_qty_per_serving != null
                ? String(row.current_qty_per_serving)
                : "",
          unit_type: normalizeOverrideUnitValue(row.override?.unit_type || row.current_unit_type || "g"),
          note: String(row.override?.note || ""),
          acknowledge_ambiguous: false,
        };
      });
      setOverrideEditor(payload);
      setOverrideEditorDrafts(previous => refresh ? { ...nextDrafts, ...previous,
        ...(refresh.rowKey && nextDrafts[refresh.rowKey] ? { [refresh.rowKey]: nextDrafts[refresh.rowKey] } : {}) } : nextDrafts);
      if (!refresh) setOverrideEditorBulkDraft({
        qty_per_serving: rows[0]?.override?.qty_per_serving != null ? String(rows[0].override.qty_per_serving)
          : rows[0]?.current_qty_per_serving != null ? String(rows[0].current_qty_per_serving) : "",
        unit_type: normalizeOverrideUnitValue(rows[0]?.override?.unit_type || rows[0]?.current_unit_type || "g"), note: "",
      });
      if (!rows.length) {
        setOverrideEditorMessage("編集対象の施設別行がありません。");
      }
      return true;
    } catch (err: any) {
      if (!own()) return;
      const detail = err?.response?.data?.detail || (err instanceof Error ? err.message : "");
      if (!refresh) setOverrideEditor({ date, daypart: menuGroup.daypart || "", menu_name: menuGroup.menu_name || "", menu_category: menuGroup.menu_category || "", rows: [] });
      setOverrideEditorMessage(detail ? `設定一覧の取得に失敗しました: ${detail}` : "設定一覧の取得に失敗しました。");
      if (!refresh) { setOverrideEditorSelectedFacilityId(""); setOverrideEditorSelectedDietType(""); }
    } finally {
      if (own()) setOverrideEditorLoading(false);
    }
  };

  const updateOverrideDraft = (
    rowKey: string,
    patch: Partial<DailyOutputOverrideDraft>,
  ) => {
    rowEdits.current[rowKey] = (rowEdits.current[rowKey] ?? 0) + 1;
    retireOutput();
    ++writeFeedback.current; setOverrideEditorSavingKey(current => current === rowKey ? "" : current); setOverrideEditorMessage("");
    releaseRetiredPending();
    setOverrideEditorDrafts((prev) => {
      const current = prev[rowKey] || {
        qty_per_serving: "",
        unit_type: "g",
        note: "",
        acknowledge_ambiguous: false,
      };
      return {
        ...prev,
        [rowKey]: {
          ...current,
          ...patch,
          unit_type: normalizeOverrideUnitValue(patch.unit_type ?? current.unit_type),
        },
      };
    });
  };

  const updateOverrideBulkDraft = (patch: Partial<DailyOutputOverrideBulkDraft>) => {
    ++bulkEdits.current; ++writeFeedback.current; setOverrideEditorBulkSaving(false); setOverrideEditorMessage("");
    retireOutput();
    releaseRetiredPending();
    setOverrideEditorBulkDraft((prev) => ({
      ...prev,
      ...patch,
      unit_type: normalizeOverrideUnitValue(patch.unit_type ?? prev.unit_type),
    }));
  };

  const validateOverrideDraft = (draft: { qty_per_serving: string }) => {
    if (!draft.qty_per_serving.trim()) {
      return "1単位量を入力してください。";
    }
    const qtyPerServing = Number(draft.qty_per_serving);
    if (!Number.isFinite(qtyPerServing) || qtyPerServing < 0) {
      return "1単位量は0以上の数値で入力してください。";
    }
    return "";
  };

  const overrideEditorRows = useMemo(
    () => (Array.isArray(overrideEditor?.rows) ? overrideEditor?.rows : []),
    [overrideEditor],
  );

  const overrideFacilityOptions = useMemo(() => {
    const options: { facility_id: string; facility_label: string; row_count: number; override_count: number }[] = [];
    const seen = new Map<string, { facility_id: string; facility_label: string; row_count: number; override_count: number }>();
    overrideEditorRows.forEach((row) => {
      const facilityId = String(row.facility_id || "").trim();
      if (!facilityId) return;
      const current =
        seen.get(facilityId) ||
        {
          facility_id: facilityId,
          facility_label: String(row.facility_label || row.facility_id || "未確定"),
          row_count: 0,
          override_count: 0,
        };
      current.row_count += 1;
      if (row.override?.id) current.override_count += 1;
      seen.set(facilityId, current);
    });
    seen.forEach((value) => options.push(value));
    return options;
  }, [overrideEditorRows]);

  const selectedFacilityRows = useMemo(
    () =>
      overrideEditorRows.filter(
        (row) => String(row.facility_id || "").trim() === overrideEditorSelectedFacilityId,
      ),
    [overrideEditorRows, overrideEditorSelectedFacilityId],
  );

  const selectedOverrideRow = useMemo(() => {
    if (!overrideEditorSelectedDietType) return null;
    const matches = selectedFacilityRows.filter(row => String(row.diet_type || "").trim() === overrideEditorSelectedDietType);
    return matches.length === 1 ? matches[0] : null;
  }, [overrideEditorSelectedDietType, selectedFacilityRows]);
  const selectOverrideTarget = (facility: string, diet: string) => {
    ++editorGeneration.current; ++writeFeedback.current;
    retireOutput();
    releaseRetiredPending();
    setOverrideEditorLoading(false); setOverrideEditorSavingKey(""); setOverrideEditorBulkSaving(false); setOverrideEditorMessage("");
    setOverrideEditorSelectedFacilityId(facility); setOverrideEditorSelectedDietType(diet);
  };
  const ownsSelectedRow = (row: DailyOutputOverrideRow) => row === selectedOverrideRow && overrideEditorRows.includes(row)
    && !!String(row.facility_id || "").trim() && !!String(row.diet_type || "").trim()
    && String(row.diet_type).trim() !== "unknown";

  const saveOverrideRow = async (row: DailyOutputOverrideRow) => {
    if (!overrideEditor?.date) return;
    if (overrideEditor.date !== date) { setOverrideEditorMessage("表示日と編集対象の日付が一致しません。編集対象を開き直してください。"); return; }
    if (!ownsSelectedRow(row)) { setOverrideEditorMessage("施設・区分の編集対象を一意に選択してください。"); return; }
    const rowKey = buildDailyOutputOverrideRowKey(row);
    const draft = overrideEditorDrafts[rowKey];
    if (!draft) return;
    const validationError = validateOverrideDraft(draft);
    if (validationError) {
      setOverrideEditorMessage(validationError);
      return;
    }
    const qtyPerServing = Number(draft.qty_per_serving);
    const own = capture(`row:${rowKey}`, true, rowKey);
    if (!own()) return;
    const feedback = ++writeFeedback.current;
    setOverrideEditorSavingKey(rowKey);
    setOverrideEditorMessage("");
    try {
      await apiClient.post("/orders/daily-output-overrides/upsert", {
        original_receipt: overrideEditor.original_receipt,
        date: overrideEditor.date,
        facility_id: row.facility_id,
        menu_name: overrideEditor.menu_name,
        diet_type: row.diet_type,
        daypart: overrideEditor.daypart,
        menu_category: overrideEditor.menu_category,
        qty_per_serving: qtyPerServing,
        unit_type: draft.unit_type,
        note: draft.note,
        acknowledge_ambiguous: draft.acknowledge_ambiguous,
      });
      if (!own()) return;
      retireOutput();
      const [editorReloaded] = await Promise.all([openOverrideEditor({
        daypart: overrideEditor.daypart,
        menu_name: overrideEditor.menu_name,
        menu_category: overrideEditor.menu_category,
      }, { own, rowKey }), loadOrders({ date, status }, own)]);
      if (editorReloaded && own() && feedback === writeFeedback.current) setOverrideEditorMessage("施設別単位設定を保存しました。");
    } catch (err: any) {
      if (!own() || feedback !== writeFeedback.current) return;
      const detail = err?.response?.data?.detail;
      if (detail?.code === "daily_output_override_ambiguous") {
        updateOverrideDraft(rowKey, { acknowledge_ambiguous: true });
        setOverrideEditorMessage("現状が複数候補です。候補を確認し、理解したうえで保存に進んでください。");
      } else if (detail?.code === "daily_output_override_target_not_found") {
        setOverrideEditorMessage("対象の施設別行が見つかりません。日別一覧を再取得してからやり直してください。");
      } else if (typeof detail === "string" && detail) {
        setOverrideEditorMessage(detail);
      } else {
        setOverrideEditorMessage("施設別単位設定の保存に失敗しました。");
      }
    } finally {
      if (own()) setOverrideEditorSavingKey(current => current === rowKey ? "" : current);
    }
  };

  const saveBulkOverride = async () => {
    if (!overrideEditor?.date) return;
    if (overrideEditor.date !== date) { setOverrideEditorMessage("表示日と編集対象の日付が一致しません。編集対象を開き直してください。"); return; }
    const own = capture("bulk", true, undefined, true);
    if (!own()) return;
    const feedback = ++writeFeedback.current;
    const validationError = validateOverrideDraft(overrideEditorBulkDraft);
    if (validationError) {
      setOverrideEditorMessage(validationError);
      return;
    }
    setOverrideEditorBulkSaving(true);
    setOverrideEditorMessage("");
    try {
      await apiClient.post("/orders/daily-output-overrides/upsert-bulk", {
        original_receipt: overrideEditor.original_receipt,
        date: overrideEditor.date,
        menu_name: overrideEditor.menu_name,
        daypart: overrideEditor.daypart,
        menu_category: overrideEditor.menu_category,
        qty_per_serving: Number(overrideEditorBulkDraft.qty_per_serving),
        unit_type: overrideEditorBulkDraft.unit_type,
        note: overrideEditorBulkDraft.note,
      });
      if (!own()) return;
      retireOutput();
      const [editorReloaded] = await Promise.all([
        openOverrideEditor({
          daypart: overrideEditor.daypart,
          menu_name: overrideEditor.menu_name,
          menu_category: overrideEditor.menu_category,
        }, { own, bulk: true }),
        loadOrders({ date, status }, own),
      ]);
      if (editorReloaded && own() && feedback === writeFeedback.current) setOverrideEditorMessage("全施設の単位設定を保存しました。");
    } catch (err: any) {
      if (!own() || feedback !== writeFeedback.current) return;
      const detail = err?.response?.data?.detail;
      if (detail?.code === "daily_output_override_bulk_requires_intervention") {
        setOverrideEditorMessage("曖昧な施設が含まれるため一括保存できません。施設を選んで個別に確定してください。");
      } else if (typeof detail === "string" && detail) {
        setOverrideEditorMessage(detail);
      } else {
        setOverrideEditorMessage("全施設の単位設定保存に失敗しました。");
      }
    } finally {
      if (own()) setOverrideEditorBulkSaving(false);
    }
  };

  const deleteOverrideRow = async (row: DailyOutputOverrideRow) => {
    const overrideId = row.override?.id;
    if (!overrideId || !overrideEditor) return;
    if (overrideEditor.date !== date) { setOverrideEditorMessage("表示日と編集対象の日付が一致しません。編集対象を開き直してください。"); return; }
    if (!ownsSelectedRow(row)) { setOverrideEditorMessage("施設・区分の編集対象を一意に選択してください。"); return; }
    const rowKey = buildDailyOutputOverrideRowKey(row);
    const own = capture(`row:${rowKey}`, true, rowKey);
    if (!own()) return;
    const feedback = ++writeFeedback.current;
    setOverrideEditorSavingKey(rowKey);
    setOverrideEditorMessage("");
    try {
      await apiClient.delete(`/orders/daily-output-overrides/${overrideId}`, {
        data: { original_receipt: overrideEditor.original_receipt },
      });
      if (!own()) return;
      retireOutput();
      const [editorReloaded] = await Promise.all([openOverrideEditor({
        daypart: overrideEditor.daypart,
        menu_name: overrideEditor.menu_name,
        menu_category: overrideEditor.menu_category,
      }, { own, rowKey }), loadOrders({ date, status }, own)]);
      if (editorReloaded && own() && feedback === writeFeedback.current) setOverrideEditorMessage("施設別単位設定を解除しました。");
    } catch (err: any) {
      if (!own() || feedback !== writeFeedback.current) return;
      const detail = err?.response?.data?.detail;
      setOverrideEditorMessage(detail ? `解除に失敗しました: ${detail}` : "解除に失敗しました。");
    } finally {
      if (own()) setOverrideEditorSavingKey(current => current === rowKey ? "" : current);
    }
  };

  const openOutput = async (path: string, label: string) => {
    const own = capture("output", true);
    if (!own()) return;
    const timestamp = new Date().toLocaleString("ja-JP");
    setMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "blob", timeout: 0 });
      if (!own()) return;
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "output";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      if (!own()) return;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const capturePopup = (own: () => boolean) => {
    const win = window.open("", "_blank");
    if (!win) return null;
    const original = win.document;
    let retired = false;
    const retirePopup = () => { retired = true; if (own()) setMessage(""); };
    win.addEventListener("pagehide", retirePopup);
    win.addEventListener("beforeunload", retirePopup);
    win.addEventListener("hashchange", retirePopup);
    win.addEventListener("popstate", retirePopup);
    const current = () => {
      try { return own() && !retired && !win.closed && win.document === original && win.location.href === "about:blank"; }
      catch { return false; }
    };
    return {
      current,
      write: (html: string) => {
        try {
          if (!current()) return false;
          original.open(); original.write(html); original.close();
          return true;
        } catch { return false; }
      },
      release: () => {
        try {
          win.removeEventListener("pagehide", retirePopup); win.removeEventListener("beforeunload", retirePopup);
          win.removeEventListener("hashchange", retirePopup); win.removeEventListener("popstate", retirePopup);
        } catch { /* The popup may now be cross-origin. */ }
      },
    };
  };

  const openHtmlOutput = async (path: string, label: string) => {
    const own = capture("output", true);
    if (!own()) return;
    const popup = capturePopup(own);
    if (!popup) { setMessage("ブラウザで新しいタブを許可してください。"); return; }
    const timestamp = new Date().toLocaleString("ja-JP");
    setMessage(`${label}を開きます。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "text", timeout: 0 });
      if (!popup.write(String(res.data || ""))) return;
      setMessage(`${label}を別タブで開きました。`);
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      if (!popup.current()) return;
      setMessage(detail ? `${label}を開けませんでした: ${detail}` : `${label}を開けませんでした。`);
    } finally {
      popup.release();
    }
  };

  const openDailyDeliveryHtml = async () => {
    if (!date) {
      setMessage("日付を指定してください。");
      return;
    }
    const own = capture("output", true);
    if (!own()) return;
    const popup = capturePopup(own);
    if (!popup) { setMessage("ブラウザで新しいタブを許可してください。"); return; }
    setMessage("当日納品書HTMLを作成中です...");
    try {
      const res = await apiClient.get("/outputs/daily-delivery-notes/html", {
        params: { date, status: status || undefined },
        responseType: "text",
        timeout: 0,
      });
      if (!popup.write(String(res.data || ""))) return;
      setMessage("当日納品書HTMLを別タブで開きました。");
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      if (!popup.current()) return;
      setMessage(detail ? `当日納品書HTMLを開けませんでした: ${detail}` : "当日納品書HTMLを開けませんでした。");
    } finally {
      popup.release();
    }
  };

  const downloadDailyBundle = async (bundleType: "labels" | "labels_csv") => {
    if (!date) {
      setMessage("日付を指定してください。");
      return;
    }
    const own = capture("output", true);
    if (!own()) return;
    const label = bundleType === "labels_csv" ? "当日ラベルCSV" : "当日ラベルExcel";
    setMessage(`${label}を作成中です...`);
    try {
      const res = await apiClient.get("/outputs/daily-bundle", {
        params: { date, bundle_type: bundleType, status: status || undefined },
        responseType: "blob",
        timeout: 0,
      });
      if (!own()) return;
      const contentDisposition = headerValueToString(
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"],
      );
      const filename = extractFilename(contentDisposition) || `daily_outputs_${date}_${bundleType}.zip`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      const successOrders = Number(res.headers?.["x-daily-bundle-success-orders"] || 0);
      const errorOrders = Number(res.headers?.["x-daily-bundle-error-orders"] || 0);
      setMessage(`${label}をダウンロードしました。成功 ${successOrders}件 / 失敗 ${errorOrders}件`);
    } catch (err: any) {
      const detail = await extractErrorDetail(err);
      if (!own()) return;
      setMessage(detail ? `一括ダウンロードに失敗しました: ${detail}` : "一括ダウンロードに失敗しました。");
    }
  };

  const bagDayparts = useMemo(
    () => buildDaypartGroups(Array.isArray(dailyBagSummary.groups) ? dailyBagSummary.groups : []),
    [dailyBagSummary],
  );

  const totalsSummaryRows = useMemo(() => {
    const rows = [...totalsRows];
    const dietIndex = new Map(preferredDietOrder.map((value, index) => [value, index]));
    rows.sort((left, right) => {
      const daypart = String(left.daypart || "").localeCompare(String(right.daypart || ""), "ja");
      if (daypart !== 0) return daypart;
      const category = String(left.menu_category || "").localeCompare(String(right.menu_category || ""), "ja");
      if (category !== 0) return category;
      const menu = String(left.menu_name || "").localeCompare(String(right.menu_name || ""), "ja");
      if (menu !== 0) return menu;
      return (dietIndex.get(normalizeDietType(left.diet_type)) ?? 99) - (dietIndex.get(normalizeDietType(right.diet_type)) ?? 99);
    });
    return rows;
  }, [totalsRows]);

  const mealCountGroups = useMemo(() => {
    const daypartOrder = new Map([
      ["朝食", 0],
      ["朝", 0],
      ["昼食", 1],
      ["昼", 1],
      ["夕食", 2],
      ["夕", 2],
      ["夜", 2],
    ]);
    return [...(mealCountSummary.groups || [])]
      .map((group) => ({
        ...group,
        counts: [...(group.counts || [])].sort((left, right) =>
          formatDietType(left.diet_type).localeCompare(formatDietType(right.diet_type), "ja"),
        ),
      }))
      .sort(
        (left, right) =>
          (daypartOrder.get(String(left.daypart || "")) ?? 99) -
            (daypartOrder.get(String(right.daypart || "")) ?? 99) ||
          String(left.daypart || "").localeCompare(String(right.daypart || ""), "ja"),
      );
  }, [mealCountSummary]);

  const unconfirmedMealCountOrders = Array.isArray(mealCountSummary.unconfirmed_orders)
    ? mealCountSummary.unconfirmed_orders
    : [];
  const inconsistentMealCounts = Array.isArray(mealCountSummary.inconsistent_counts)
    ? mealCountSummary.inconsistent_counts
    : [];

  const auditFindings = useMemo(
    () => (Array.isArray(dailyBagAudit.rule_based?.findings) ? dailyBagAudit.rule_based?.findings || [] : []),
    [dailyBagAudit],
  );

  const auditAiItemsById = useMemo(() => {
    const map = new Map<string, { finding_id?: string | null; priority?: string | null; summary?: string | null; operator_action?: string | null }>();
    const items = Array.isArray(dailyBagAudit.ai?.items) ? dailyBagAudit.ai?.items || [] : [];
    items.forEach((item) => {
      const findingId = String(item?.finding_id || "").trim();
      if (findingId) map.set(findingId, item);
    });
    return map;
  }, [dailyBagAudit]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Daily Outputs</p>
          <h1>日別出力</h1>
          <p className="subtle">日付を軸に、注文・袋分け・ラベル・納品書・総量を全施設横断で確認します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>この画面で見ること</h2>
        </header>
        <div className="guide-grid">
          <article className="guide-card">
            <p className="guide-title">発送前の最終確認</p>
            <p className="guide-text">その日に出す注文、袋分け、納品書、ラベルをまとめて確認します。</p>
          </article>
          <article className="guide-card">
            <p className="guide-title">袋分けを見る</p>
            <p className="guide-text">「当日袋分け一覧」でメニューごとの袋数と計算結果を確認します。</p>
          </article>
          <article className="guide-card">
            <p className="guide-title">迷ったとき</p>
            <p className="guide-text">その注文の「詳細」を開いて、元のシートとOCR結果を確認します。</p>
          </article>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">合計 {orders.length} 件</span>
        </header>
        <FormProvider {...conditionForm}>
          <Stack
            component="form"
            direction={{ xs: "column", sm: "row" }}
            spacing={2}
            onSubmit={conditionForm.handleSubmit(async (values) => {
              conditionForm.clearErrors("root");
              try { await loadOrders(values); } catch { /* The owned load sets the root error. */ }
            })}
          >
            <FormTextField<DailyConditions> name="date" label="日付" type="date" rules={{ required: "日付を指定してください。" }} />
            <FormTextField<DailyConditions> name="status" label="ステータス" select>
              <MenuItem value="">全て</MenuItem>
              <MenuItem value="未着">未着</MenuItem>
              <MenuItem value="要確認">要確認</MenuItem>
              <MenuItem value="確定">確定</MenuItem>
              <MenuItem value="エラー">エラー</MenuItem>
            </FormTextField>
            <Button type="submit" variant="contained" disabled={loading}>
              {loading ? "集計中..." : "表示・集計"}
            </Button>
          </Stack>
          {conditionForm.formState.errors.root?.message ? (
            <p className="message message-error">{conditionForm.formState.errors.root.message}</p>
          ) : null}
        </FormProvider>
        <div className="filters">
          <Button className="btn ghost delivery-ordinary-control" type="button" onClick={() => downloadDailyBundle("labels_csv")} disabled={loading}>
            当日ラベルCSV
          </Button>
          <Button className="btn ghost delivery-ordinary-control" type="button" onClick={() => downloadDailyBundle("labels")} disabled={loading}>
            当日ラベルExcel
          </Button>
          <Button className="btn ghost delivery-ordinary-control" type="button" onClick={openDailyDeliveryHtml} disabled={loading}>
            当日納品書HTML
          </Button>
          <Link className="btn ghost" href={`/weekly-weight-output?date=${encodeURIComponent(date)}${status ? `&status=${encodeURIComponent(status)}` : ""}`}>
            週別重量表
          </Link>
        </div>
        <p className="subtle helper-text">
          ラベルと袋分けは選択したステータス、総量は確定注文ベースです。納品書HTMLは表示中の注文を編集してPDF保存できます。
        </p>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <section className="panel">
        <header className="panel-header">
          <h2>当日注文一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>施設</th>
                <th>週</th>
                <th>ステータス</th>
                <th>受信日時</th>
                <th>行数</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={6}>該当データなし</td>
                </tr>
              ) : (
                orders.map((order) => (
                  <tr key={order.id}>
                    <td>{facilityLabel(order)}</td>
                    <td>{order.week || "未確定"}</td>
                    <td>{order.status || "-"}</td>
                    <td>{formatTimestamp(order.received_at)}</td>
                    <td>{order.line_count ?? "-"}</td>
                    <td className="actions">
                      <Button
                        className="btn ghost delivery-ordinary-control"
                        type="button"
                        onClick={() => openOutput(`/outputs/labels?order_id=${order.id}&date=${date}`, "ラベルCSV")}
                      >
                        ラベルCSV
                      </Button>
                      <Button
                        className="btn ghost delivery-ordinary-control"
                        type="button"
                        onClick={() => openOutput(`/outputs/labels.xlsx?order_id=${order.id}&date=${date}`, "ラベルExcel")}
                      >
                        ラベルExcel
                      </Button>
                      <Button
                        className="btn ghost delivery-ordinary-control"
                        type="button"
                        onClick={() => openHtmlOutput(`/outputs/delivery-notes/html?order_id=${order.id}`, "納品書HTML")}
                      >
                        納品書
                      </Button>
                      <Button
                        className="btn ghost delivery-ordinary-control"
                        type="button"
                        onClick={() => openOutput(`/outputs/manufacturing-aggregate?order_id=${order.id}`, "総量CSV")}
                      >
                        総量CSV
                      </Button>
                      <Link href={`/orders/${order.id}`} className="link">
                        詳細
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>当日食数集計</h2>
          <span className="badge">全施設合計</span>
        </header>
        <p className="subtle">確定済み注文を、朝食・昼食・夕食ごとに食種別で集計しています。</p>
        {unconfirmedMealCountOrders.length > 0 ? (
          <div className="meal-count-warning" role="alert">
            <strong>未確定の注文があるため、この集計には含まれていません。</strong>
            <ul>
              {unconfirmedMealCountOrders.map((order) => (
                <li key={order.order_id || `${order.facility_id}-${order.status}`}>
                  {order.facility_id ? facilityNameMap[order.facility_id] || order.facility_id : "施設未確定"}
                  {order.order_id ? ` (${order.order_id})` : ""} / {order.status || "未確定"}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {inconsistentMealCounts.length > 0 ? (
          <div className="meal-count-warning" role="alert">
            <strong>同じ食数枠に異なる数量があるため、該当分は集計していません。</strong>
            <ul>
              {inconsistentMealCounts.map((count) => (
                <li key={`${count.order_id}-${count.daypart}-${count.diet_type}-${count.area_id}`}>
                  {count.facility_id ? facilityNameMap[count.facility_id] || count.facility_id : "施設未確定"}
                  {count.order_id ? ` (${count.order_id})` : ""} / {count.daypart || "食区分不明"} /{" "}
                  {formatDietType(count.diet_type)} / {count.area_id || "区画不明"}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {mealCountMessage ? <p className="subtle">{mealCountMessage}</p> : null}
        {mealCountGroups.length === 0 ? (
          <p className="subtle">該当データなし</p>
        ) : (
          <div className="meal-count-grid">
            {mealCountGroups.map((group) => (
              <section key={group.daypart || "unknown"} className="meal-count-group">
                <h3>{group.daypart || "食区分不明"}</h3>
                <dl>
                  {(group.counts || []).map((count) => (
                    <div key={count.diet_type || "unknown"} className="meal-count-row">
                      <dt>{formatDietType(count.diet_type)}</dt>
                      <dd>{formatQuantity(count.quantity)}食</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>当日袋分け一覧</h2>
          <span className="badge">{Array.isArray(dailyBagSummary.groups) ? dailyBagSummary.groups.length : 0} メニュー</span>
        </header>
        {bagMessage ? <p className="subtle">{bagMessage}</p> : null}
        {bagDayparts.length === 0 ? (
          <p className="subtle">該当データなし</p>
        ) : (
          <div className="bag-daypart-list">
            {bagDayparts.map((daypartGroup) => (
              <section key={daypartGroup.daypart} className="daypart-block">
                <header className="panel-header">
                  <h3 className="daypart-title">{daypartGroup.daypart}</h3>
                  <span className="badge">{daypartGroup.rows.length} メニュー</span>
                </header>
                <div className="menu-bag-grid">
                  {daypartGroup.rows.map((menuGroup) => (
                    <details key={`${daypartGroup.daypart}-${menuGroup.menu_name}`} className="menu-bag-card" open>
                      <summary className="menu-bag-summary">
                        <div>
                          <p className="menu-bag-name">{menuGroup.menu_name || "-"}</p>
                          <p className="menu-bag-meta">
                            {menuGroup.menu_category || "-"} /{" "}
                            {Array.isArray(menuGroup.diet_groups) ? menuGroup.diet_groups.length : 0}区分 /{" "}
                            {formatQuantity(sumDietQuantity(menuGroup))}食
                          </p>
                        </div>
                      </summary>
                      <div className="menu-bag-body">
                        <div className="menu-bag-actions">
                          <Button
                            className="btn ghost delivery-ordinary-control"
                            type="button"
                            onClick={() => openOverrideEditor(menuGroup)}
                          >
                            施設別単位設定
                          </Button>
                        </div>
                        <table className="menu-bag-table">
                          <thead>
                            <tr>
                              <th>献立区分</th>
                              <th>区分</th>
                              <th>注文数</th>
                              <th>計算基準</th>
                              <th>計算結果</th>
                              <th>袋種</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(menuGroup.diet_groups || []).map((dietGroup) => (
                              <tr key={`${menuGroup.menu_name}-${dietGroup.diet_type}`}>
                                <td>{menuGroup.menu_category || "-"}</td>
                                <td>{formatDietType(dietGroup.diet_type)}</td>
                                <td className="numeric">{formatQuantity(dietGroup.total_quantity)}</td>
                                <td>{dietGroup.calculation_basis_label || "-"}</td>
                                <td>{dietGroup.total_amount_label || "計算不可"}</td>
                                <td>
                                  <div className="bag-type-stack">
                                    {(dietGroup.bag_type_groups || []).map((bagTypeGroup) => (
                                      <details
                                        key={`${dietGroup.diet_type}-${bagTypeGroup.bag_type}`}
                                        className="bag-type-detail"
                                      >
                                        <summary className="bag-type-summary">
                                          <span className="bag-type-main">
                                            {formatBagType(bagTypeGroup.bag_type)} {bagTypeGroup.bag_count || 0}袋
                                          </span>
                                          <span className="bag-type-sub">
                                            {bagTypeGroup.total_amount_label || "計算不可"}
                                          </span>
                                        </summary>
                                        <div className="bag-breakdown-list">
                                          {(bagTypeGroup.breakdowns || []).map((breakdown, index) => (
                                            <div
                                              key={`${bagTypeGroup.bag_type}-${breakdown.amount_label}-${index}`}
                                              className="bag-breakdown-entry"
                                            >
                                              <div className="bag-breakdown-row">
                                                <span>{breakdown.amount_label || "計算不可"}</span>
                                                <strong>x {breakdown.count || 0}</strong>
                                              </div>
                                              {(breakdown.order_refs || []).length ? (
                                                <div className="bag-breakdown-refs">
                                                  {(breakdown.order_refs || []).map((orderRef, orderIndex) => (
                                                    <div
                                                      key={`${orderRef.order_id || "order"}-${orderIndex}`}
                                                      className="bag-breakdown-ref"
                                                    >
                                                      <span>{formatBagOrderRef(orderRef)}</span>
                                                      {orderRef.order_id ? (
                                                        <Link href={`/orders/${orderRef.order_id}`} className="link">
                                                          詳細
                                                        </Link>
                                                      ) : null}
                                                    </div>
                                                  ))}
                                                </div>
                                              ) : null}
                                            </div>
                                          ))}
                                        </div>
                                      </details>
                                    ))}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>数量監査</h2>
            <p className="subtle">
              正解差分がない運用で、日別出力の数量だけから確認候補を出します。Geminiは候補の説明補助で、ルールベース監査は単独で動きます。
            </p>
          </div>
          <div className="actions">
            <span className="badge">
              候補 {dailyBagAudit.rule_based?.finding_count ?? auditFindings.length} 件
            </span>
            <Button className="btn ghost delivery-ordinary-control" type="button" onClick={runAuditAi} disabled={loading || auditAiLoading || !auditFindings.length}>
              {auditAiLoading ? "Gemini実行中..." : "Gemini補助"}
            </Button>
          </div>
        </header>
        {auditMessage ? <p className="subtle">{auditMessage}</p> : null}
        <div className="audit-summary-row">
          <span>高: {dailyBagAudit.rule_based?.summary_by_severity?.high || 0}</span>
          <span>中: {dailyBagAudit.rule_based?.summary_by_severity?.medium || 0}</span>
          <span>低: {dailyBagAudit.rule_based?.summary_by_severity?.low || 0}</span>
          <span>対象セル: {dailyBagAudit.rule_based?.cell_count || 0}</span>
          <span>AI: {dailyBagAudit.ai?.status || "not_requested"}</span>
        </div>
        {auditFindings.length === 0 ? (
          <p className="subtle">確認候補なし</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>重要度</th>
                  <th>ルール</th>
                  <th>対象</th>
                  <th>数量</th>
                  <th>理由</th>
                  <th>Gemini補助</th>
                </tr>
              </thead>
              <tbody>
                {auditFindings.map((finding) => {
                  const aiItem = auditAiItemsById.get(String(finding.finding_id || ""));
                  return (
                    <tr key={finding.finding_id || `${finding.rule_code}-${finding.message}`}>
                      <td>
                        <span className={`audit-severity audit-severity-${String(finding.severity || "low").toLowerCase()}`}>
                          {formatAuditSeverity(finding.severity)}
                        </span>
                      </td>
                      <td>{formatAuditRule(finding.rule_code)}</td>
                      <td>
                        <div className="audit-target">
                          <span>{formatAuditTarget(finding)}</span>
                          {formatOcrScore(finding.target || undefined) ? (
                            <small>{formatOcrScore(finding.target || undefined)}</small>
                          ) : null}
                          {finding.target?.order_id ? (
                            <Link href={`/orders/${finding.target.order_id}/workflow-v2`} className="link">
                              注文
                            </Link>
                          ) : null}
                        </div>
                      </td>
                      <td className="numeric">{formatQuantity(finding.target?.quantity)}</td>
                      <td>
                        <div className="audit-reason">
                          <strong>{finding.title || "-"}</strong>
                          <span>{finding.message || "-"}</span>
                          {(finding.reference_values || []).length ? (
                            <small>参照: {(finding.reference_values || []).join(", ")}</small>
                          ) : null}
                        </div>
                      </td>
                      <td>
                        {aiItem ? (
                          <div className="audit-reason">
                            <strong>{aiItem.priority || "AI"}</strong>
                            <span>{aiItem.summary || "-"}</span>
                            {aiItem.operator_action ? <small>{aiItem.operator_action}</small> : null}
                          </div>
                        ) : (
                          <span className="subtle">-</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>当日総量</h2>
        </header>
        {totalsMessage ? <p className="subtle">{totalsMessage}</p> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>食区</th>
                <th>献立区分</th>
                <th>メニュー</th>
                <th>区分</th>
                <th>注文数</th>
              </tr>
            </thead>
            <tbody>
              {totalsSummaryRows.length === 0 ? (
                <tr>
                  <td colSpan={5}>該当データなし</td>
                </tr>
              ) : (
                totalsSummaryRows.map((row, index) => (
                  <tr
                    key={[
                      row.date || "-",
                      row.daypart || "-",
                      row.menu_category || "-",
                      row.menu_name || "-",
                      row.diet_type || "-",
                      index,
                    ].join("__")}
                  >
                    <td>{row.daypart || "-"}</td>
                    <td>{row.menu_category || "-"}</td>
                    <td>{row.menu_name || "-"}</td>
                    <td>{formatDietType(row.diet_type)}</td>
                    <td className="numeric">{formatQuantity(row.quantity)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {overrideEditor ? (
        <div className="override-modal-backdrop" role="presentation" onClick={closeOverrideEditor}>
          <div className="override-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <div>
                <h2>施設別単位設定</h2>
                <p className="subtle">
                  {overrideEditor.date || date} / {overrideEditor.daypart || "-"} / {overrideEditor.menu_name || "-"} /{" "}
                  {overrideEditor.menu_category || "-"}
                </p>
              </div>
              <Button className="btn ghost delivery-ordinary-control" type="button" onClick={closeOverrideEditor}>
                閉じる
              </Button>
            </div>
            <p className="subtle helper-text">
              ここで保存した値は、その日の袋分けと個別注文の出力に反映されます。曖昧な行は候補を確認してから保存してください。
            </p>
            {overrideEditorMessage ? <p className="message">{overrideEditorMessage}</p> : null}
            {overrideEditorLoading ? (
              <p className="subtle">読込中...</p>
            ) : !Array.isArray(overrideEditor.rows) || overrideEditor.rows.length === 0 ? (
              <p className="subtle">対象の施設別行がありません。</p>
            ) : (
              <div className="override-editor-shell">
                <section className="override-editor-card override-bulk-card">
                  <div className="override-editor-head">
                    <div>
                      <p className="override-facility">全施設に一括適用</p>
                      <p className="override-meta">
                        {overrideEditorRows.length}区分 / {overrideFacilityOptions.length}施設
                      </p>
                    </div>
                    <span className="badge">一括設定</span>
                  </div>
                  <p className="subtle override-bulk-note">
                    ここで保存した値は、このメニューの全施設・全区分に反映されます。曖昧な施設がある場合は一括保存を止めて、下で個別に介入します。
                  </p>
                  <div className="override-form-grid">
                    <label className="field">
                      <span className="field-label">1単位量</span>
                      <Input
                        className="input delivery-ordinary-control"
                        type="number"
                        value={overrideEditorBulkDraft.qty_per_serving}
                        onChange={(event) => updateOverrideBulkDraft({ qty_per_serving: event.target.value })}
                        disableUnderline
                        inputProps={{ "step": "0.1", "min": "0" }}
                      />
                    </label>
                    <label className="field">
                      <span className="field-label">単位</span>
                      <NativeSelect
                        className="input delivery-ordinary-control"
                        value={overrideEditorBulkDraft.unit_type}
                        onChange={(event) => updateOverrideBulkDraft({ unit_type: event.target.value })}
                        disableUnderline
                      >
                        {overrideUnitOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </NativeSelect>
                    </label>
                    <label className="field override-note-field">
                      <span className="field-label">理由メモ</span>
                      <Input
                        className="input delivery-ordinary-control"
                        type="text"
                        value={overrideEditorBulkDraft.note}
                        onChange={(event) => updateOverrideBulkDraft({ note: event.target.value })}
                        placeholder={`例: 全施設 ${formatOverrideUnitLabel(overrideEditorBulkDraft.unit_type)}を統一`}
                        disableUnderline
                      />
                    </label>
                  </div>
                  <div className="actions override-actions">
                    <Button className="btn primary delivery-ordinary-control" type="button" onClick={saveBulkOverride} disabled={overrideEditorBulkSaving}>
                      {overrideEditorBulkSaving ? "保存中..." : "全施設に保存"}
                    </Button>
                  </div>
                </section>

                <section className="override-editor-card">
                  <div className="override-selector-grid">
                    <label className="field">
                      <span className="field-label">施設を選ぶ</span>
                      <NativeSelect
                        className="input delivery-ordinary-control"
                        value={overrideEditorSelectedFacilityId}
                        onChange={(event) => selectOverrideTarget(event.target.value, "")}
                        disableUnderline
                      >
                        <option value="">施設を選択してください</option>
                        {overrideFacilityOptions.map((option) => (
                          <option key={option.facility_id} value={option.facility_id}>
                            {option.facility_label}
                            {option.override_count > 0 ? " / override有" : ""}
                          </option>
                        ))}
                      </NativeSelect>
                    </label>
                    {selectedFacilityRows.length > 0 ? (
                      <label className="field">
                        <span className="field-label">区分を選ぶ</span>
                        <NativeSelect
                          className="input delivery-ordinary-control"
                          value={overrideEditorSelectedDietType}
                          onChange={(event) => selectOverrideTarget(overrideEditorSelectedFacilityId, event.target.value)}
                          disableUnderline
                        >
                          <option value="">区分を選択してください</option>
                          {selectedFacilityRows.map((row) => (
                            <option key={buildDailyOutputOverrideRowKey(row)} value={normalizeDietType(row.diet_type)}>
                              {formatDietType(row.diet_type)}
                            </option>
                          ))}
                        </NativeSelect>
                      </label>
                    ) : null}
                  </div>
                  {selectedOverrideRow ? (() => {
                    const row = selectedOverrideRow;
                    const rowKey = buildDailyOutputOverrideRowKey(row);
                    const draft = overrideEditorDrafts[rowKey] || {
                      qty_per_serving: "",
                      unit_type: "g",
                      note: "",
                      acknowledge_ambiguous: false,
                    };
                    const saving = overrideEditorSavingKey === rowKey;
                    return (
                      <>
                        <div className="override-editor-head">
                          <div>
                            <p className="override-facility">{row.facility_label || row.facility_id || "未確定"}</p>
                            <p className="override-meta">
                              {formatDietType(row.diet_type)} / {formatQuantity(row.total_quantity)}食 / {row.order_count || 0}注文
                            </p>
                          </div>
                          <span className="badge">{row.override ? "override適用中" : "現在値"}</span>
                        </div>
                        <div className="override-current">
                          <span>現在の計算基準</span>
                          <strong>{row.current_basis_label || "未設定"}</strong>
                        </div>
                        {row.requires_intervention ? (
                          <div className="override-warning">
                            <p>現状が複数候補です。保存するとこの施設/区分の値を統一します。</p>
                            <div className="override-candidate-list">
                              {(row.current_variants || []).map((variant, index) => (
                                <div key={`${rowKey}-variant-${index}`} className="override-candidate-row">
                                  <span>
                                    {variant.menu_name || overrideEditor.menu_name || "-"} / {variant.menu_category || "-"} /{" "}
                                    {variant.basis_label || "未設定"}
                                  </span>
                                  <strong>{(variant.order_ids || []).length}件</strong>
                                </div>
                              ))}
                            </div>
                            <label className="checkbox-row">
                              <Checkbox
                                checked={draft.acknowledge_ambiguous}
                                onChange={(event) =>
                                  updateOverrideDraft(rowKey, { acknowledge_ambiguous: event.target.checked })
                                }
                                className="delivery-ordinary-control"
                              />
                              <span>候補を確認し、この値で上書きすることを理解した</span>
                            </label>
                          </div>
                        ) : null}
                        <div className="override-form-grid">
                          <label className="field">
                            <span className="field-label">1単位量</span>
                            <Input
                              className="input delivery-ordinary-control"
                              type="number"
                              value={draft.qty_per_serving}
                              onChange={(event) => updateOverrideDraft(rowKey, { qty_per_serving: event.target.value })}
                              disableUnderline
                              inputProps={{ "step": "0.1", "min": "0" }}
                            />
                          </label>
                          <label className="field">
                            <span className="field-label">単位</span>
                            <NativeSelect
                              className="input delivery-ordinary-control"
                              value={draft.unit_type}
                              onChange={(event) => updateOverrideDraft(rowKey, { unit_type: event.target.value })}
                              disableUnderline
                            >
                              {overrideUnitOptions.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </NativeSelect>
                          </label>
                          <label className="field override-note-field">
                            <span className="field-label">理由メモ</span>
                            <Input
                              className="input delivery-ordinary-control"
                              type="text"
                              value={draft.note}
                              onChange={(event) => updateOverrideDraft(rowKey, { note: event.target.value })}
                              placeholder={`例: この施設のみ${formatOverrideUnitLabel(draft.unit_type)}を調整`}
                              disableUnderline
                            />
                          </label>
                        </div>
                        <div className="actions override-actions">
                          <Button className="btn primary delivery-ordinary-control" type="button" onClick={() => saveOverrideRow(row)} disabled={saving}>
                            {saving ? "保存中..." : "保存"}
                          </Button>
                          {row.override?.id ? (
                            <Button className="btn ghost delivery-ordinary-control" type="button" onClick={() => deleteOverrideRow(row)} disabled={saving}>
                              解除
                            </Button>
                          ) : null}
                        </div>
                      </>
                    );
                  })() : (
                    <p className="subtle">施設を選択してください。</p>
                  )}
                </section>
              </div>
            )}
          </div>
        </div>
      ) : null}

      <style jsx>{`
        :global(.delivery-ordinary-control.MuiButton-root) {
          text-transform: none;
          letter-spacing: inherit;
          min-width: 0;
        }
        :global(.delivery-ordinary-control .MuiInputBase-input) {
          padding: 0;
          min-width: 0;
        }
        :global(.delivery-ordinary-control.MuiCheckbox-root) {
          padding: 0;
          margin-top: 2px;
        }
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

        .helper-text {
          margin-top: 12px;
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
          gap: 12px;
        }
        .guide-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 14px;
        }
        .guide-card {
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #fcfbf7;
          padding: 16px;
        }
        .guide-title {
          margin: 0 0 8px;
          font-weight: 800;
        }
        .guide-text {
          margin: 0;
          color: #51615c;
          line-height: 1.6;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .daypart-title {
          font-size: 17px;
          margin: 0;
        }

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          white-space: nowrap;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          align-items: center;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .input,
        :global(.delivery-ordinary-control.input) {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .btn,
        :global(.delivery-ordinary-control.btn) {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          justify-self: start;
        }

        .btn.primary,
        :global(.delivery-ordinary-control.btn.primary) {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost,
        :global(.delivery-ordinary-control.btn.ghost) {
          background: #eef2f0;
          color: #1f2a2a;
          border: 1px solid rgba(25, 32, 30, 0.12);
        }

        .btn:disabled,
        :global(.delivery-ordinary-control.btn:disabled) {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
          min-width: 720px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
          vertical-align: middle;
          white-space: nowrap;
        }

        thead {
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .actions {
          display: flex;
          gap: 8px;
          align-items: center;
          flex-wrap: wrap;
        }

        .link {
          color: #1f2a2a;
          text-decoration: underline;
          font-weight: 600;
        }

        .numeric {
          font-weight: 700;
          color: #1f2a2a;
        }

        .bag-daypart-list {
          display: flex;
          flex-direction: column;
          gap: 20px;
        }

        .daypart-block + .daypart-block {
          padding-top: 20px;
          border-top: 1px solid rgba(25, 32, 30, 0.08);
        }

        .menu-bag-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr));
          gap: 16px;
        }

        .menu-bag-card {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 16px;
          background: #fcfbf8;
          overflow: hidden;
        }

        .menu-bag-summary {
          cursor: pointer;
          list-style: none;
          padding: 16px 18px;
          background: linear-gradient(135deg, #f5efe2, #f8fbfa);
        }

        .menu-bag-summary::-webkit-details-marker {
          display: none;
        }

        .menu-bag-name {
          margin: 0;
          font-size: 16px;
          font-weight: 700;
          color: #1f2a2a;
        }

        .menu-bag-meta {
          margin: 6px 0 0;
          font-size: 12px;
          color: #5b6a66;
        }

        .menu-bag-body {
          padding: 12px 16px 16px;
        }

        .menu-bag-actions {
          display: flex;
          justify-content: flex-end;
          margin-bottom: 12px;
        }

        .menu-bag-table {
          min-width: 100%;
        }

        .menu-bag-table th,
        .menu-bag-table td {
          white-space: normal;
          vertical-align: top;
        }

        .bag-type-stack {
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-width: 220px;
        }

        .bag-type-detail {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 12px;
          background: #ffffff;
        }

        .bag-type-summary {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          padding: 10px 12px;
          list-style: none;
          font-size: 13px;
          font-weight: 600;
        }

        .bag-type-summary::-webkit-details-marker {
          display: none;
        }

        .bag-type-main {
          color: #1f2a2a;
        }

        .bag-type-sub {
          color: #5f7b74;
          font-size: 12px;
        }

        .bag-breakdown-list {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 0 12px 12px;
          border-top: 1px solid rgba(25, 32, 30, 0.06);
        }

        .bag-breakdown-entry {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding-top: 8px;
        }

        .bag-breakdown-row {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          font-size: 12px;
          color: #51615c;
        }

        .bag-breakdown-refs {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .bag-breakdown-ref {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          font-size: 12px;
          color: #758680;
        }

        .audit-summary-row {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 12px 0 14px;
        }

        .audit-summary-row span {
          border-radius: 999px;
          background: #edf3f0;
          color: #40524d;
          padding: 6px 10px;
          font-size: 12px;
          font-weight: 700;
        }

        .audit-severity {
          display: inline-flex;
          min-width: 34px;
          justify-content: center;
          border-radius: 999px;
          padding: 5px 9px;
          font-size: 12px;
          font-weight: 800;
        }

        .audit-severity-high {
          background: #ffe0d7;
          color: #8d2716;
        }

        .audit-severity-medium {
          background: #fff0c4;
          color: #73510b;
        }

        .audit-severity-low {
          background: #e4eef8;
          color: #244d76;
        }

        .audit-target {
          display: flex;
          flex-direction: column;
          gap: 5px;
          min-width: 220px;
        }

        .audit-reason {
          display: flex;
          flex-direction: column;
          gap: 5px;
          min-width: 260px;
        }

        .audit-reason strong {
          font-size: 13px;
          color: #243330;
        }

        .audit-reason span,
        .audit-reason small {
          color: #63736f;
          font-size: 12px;
          line-height: 1.5;
        }

        .override-modal-backdrop {
          position: fixed;
          inset: 0;
          z-index: 40;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px;
          background: rgba(20, 26, 25, 0.38);
          backdrop-filter: blur(3px);
        }

        .override-modal {
          width: min(960px, 100%);
          max-height: min(88vh, 920px);
          overflow: auto;
          border-radius: 22px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #fffdf9;
          box-shadow: 0 28px 70px rgba(17, 24, 22, 0.22);
          padding: 22px 22px 24px;
        }

        .override-editor-shell {
          display: grid;
          gap: 14px;
          margin-top: 14px;
        }

        .override-editor-card {
          border-radius: 18px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #ffffff;
          box-shadow: 0 10px 24px rgba(25, 32, 30, 0.06);
          padding: 16px;
        }

        .override-editor-head {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
          margin-bottom: 10px;
        }

        .override-facility {
          margin: 0;
          font-size: 16px;
          font-weight: 800;
          color: #1f2a2a;
        }

        .override-bulk-card {
          border-style: dashed;
        }

        .override-bulk-note {
          margin: 0 0 12px;
        }

        .override-selector-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          margin-bottom: 16px;
        }

        .override-meta {
          margin: 6px 0 0;
          font-size: 13px;
          color: #5f6d68;
        }

        .override-current {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 14px;
          border-radius: 12px;
          background: #f4f7f5;
          padding: 10px 12px;
          margin-bottom: 12px;
        }

        .override-current span {
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: #5f7b74;
        }

        .override-current strong {
          color: #1f2a2a;
          font-size: 14px;
        }

        .override-warning {
          border-radius: 14px;
          border: 1px solid rgba(158, 98, 36, 0.18);
          background: #f9efe2;
          color: #6b4217;
          padding: 12px;
          margin-bottom: 12px;
        }

        .override-warning p {
          margin: 0 0 10px;
          font-size: 13px;
        }

        .override-candidate-list {
          display: grid;
          gap: 8px;
          margin-bottom: 10px;
        }

        .override-candidate-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          border-radius: 10px;
          background: rgba(255, 255, 255, 0.72);
          padding: 8px 10px;
          font-size: 12px;
        }

        .checkbox-row {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          font-size: 13px;
          color: #5a4022;
        }

        .checkbox-row input {
          margin-top: 2px;
        }

        .override-form-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        }

        .override-note-field {
          grid-column: 1 / -1;
        }

        .override-actions {
          justify-content: flex-end;
          margin-top: 14px;
        }

        .meal-count-warning {
          margin: 14px 0;
          padding: 14px 16px;
          border: 1px solid rgba(139, 90, 22, 0.28);
          border-radius: 8px;
          background: #fff7e7;
          color: #6d4511;
        }

        .meal-count-warning ul {
          margin: 8px 0 0;
          padding-left: 20px;
        }

        .meal-count-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 16px;
          margin-top: 16px;
        }

        .meal-count-group {
          min-width: 0;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 8px;
          background: #fff;
          padding: 14px;
        }

        .meal-count-group h3 {
          margin: 0 0 10px;
          font-size: 16px;
        }

        .meal-count-group dl {
          margin: 0;
        }

        .meal-count-row {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding: 8px 0;
          border-top: 1px solid rgba(25, 32, 30, 0.08);
        }

        .meal-count-row dt,
        .meal-count-row dd {
          margin: 0;
        }

        .meal-count-row dd {
          font-weight: 700;
          white-space: nowrap;
        }

        @media (max-width: 720px) {
          .meal-count-grid {
            grid-template-columns: 1fr;
          }

          .override-modal-backdrop {
            padding: 12px;
          }

          .override-modal {
            padding: 18px 16px 18px;
            max-height: 92vh;
          }

          .override-editor-head,
          .override-current {
            flex-direction: column;
            align-items: stretch;
          }

          .override-actions {
            justify-content: stretch;
          }

          .override-actions .btn,
          :global(.override-actions .delivery-ordinary-control.btn) {
            flex: 1 1 0;
            text-align: center;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}

export default function DailyDeliveryNotesPage() {
  const sessionKey = useSyncExternalStore(
    subscribeBrowserSession,
    browserSessionKey,
    () => "server",
  );
  return (
    <SawaProvider sessionKey={`hospital-daily-delivery-notes:${sessionKey}`}>
      <DailyDeliveryNotesScreen />
    </SawaProvider>
  );
}
