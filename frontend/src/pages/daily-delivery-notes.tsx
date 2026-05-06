import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
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

type DailyBagBreakdown = {
  amount_label?: string | null;
  count?: number | null;
  order_refs?: {
    order_id?: string | null;
    facility_label?: string | null;
    area_id?: string | null;
    quantity?: number | null;
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
  order_refs?: {
    order_id?: string | null;
    facility_id?: string | null;
    facility_name?: string | null;
    source_diet_type?: string | null;
    aggregated_diet_type?: string | null;
    area_id?: string | null;
    quantity?: number | null;
  }[];
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

const buildTotalRowKey = (row: TotalRow, index: number) =>
  [
    row.date || "-",
    row.daypart || "-",
    row.menu_category || "-",
    row.menu_name || "-",
    row.diet_type || "-",
    index,
  ].join("__");

const formatBagOrderRef = (value: NonNullable<DailyBagBreakdown["order_refs"]>[number]) => {
  const parts = [
    value.facility_label || value.order_id || "注文",
    value.area_id ? `${value.area_id}` : "",
    value.quantity != null && !Number.isNaN(value.quantity) ? `${formatQuantity(value.quantity)}食` : "",
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

export default function DailyDeliveryNotesPage() {
  const [date, setDate] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [bagMessage, setBagMessage] = useState("");
  const [totalsMessage, setTotalsMessage] = useState("");
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});
  const [dailyBagSummary, setDailyBagSummary] = useState<DailyBagSummaryResponse>({});
  const [totalsRows, setTotalsRows] = useState<TotalRow[]>([]);
  const [expandedTotalRows, setExpandedTotalRows] = useState<Set<string>>(() => new Set());
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

  useEffect(() => {
    if (!date) {
      const today = new Date();
      setDate(today.toISOString().slice(0, 10));
    }
  }, [date]);

  useEffect(() => {
    let cancelled = false;
    fetchFacilityNameMap()
      .then((map) => {
        if (!cancelled) setFacilityNameMap(map);
      })
      .catch(() => {
        if (!cancelled) setFacilityNameMap({});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
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
        const orderId = queue.shift();
        if (!orderId) continue;
        try {
          const candidates = await fetchOrderFacilityCandidates(orderId);
          const best = pickBestFacilityCandidate(candidates);
          if (best) results[orderId] = { ...best, order_id: orderId };
        } catch {
          // ignore
        }
      }
    });

    Promise.all(workers).then(() => {
      if (cancelled) return;
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

  const loadOrders = async () => {
    if (!date) return;
    setLoading(true);
    setMessage("");
    setBagMessage("");
    setTotalsMessage("");
    setDailyBagSummary({});
    setTotalsRows([]);
    setExpandedTotalRows(new Set());
    try {
      const params: Record<string, string> = { date };
      if (status) params.status = status;
      const [ordersRes, bagRes, totalsRes] = await Promise.allSettled([
        apiClient.get("/orders/by-line-date", { params }),
        apiClient.get("/orders/daily-bags", { params }),
        apiClient.get("/totals", { params: { date, include_order_refs: true } }),
      ]);

      if (ordersRes.status === "fulfilled") {
        const items = Array.isArray(ordersRes.value.data?.orders) ? ordersRes.value.data.orders : [];
        setOrders(items);
        if (!items.length) {
          setMessage("該当する注文がありません。");
        }
      } else {
        throw ordersRes.reason;
      }

      if (bagRes.status === "fulfilled") {
        const payload = bagRes.value.data || {};
        setDailyBagSummary(payload);
        const count = Array.isArray(payload.groups) ? payload.groups.length : 0;
        if (!count) {
          setBagMessage("袋分け結果がまだ生成されていません。");
        }
      } else {
        setDailyBagSummary({});
        setBagMessage("袋分け結果の取得に失敗しました。");
      }

      if (totalsRes.status === "fulfilled") {
        const rows = Array.isArray(totalsRes.value.data?.rows) ? totalsRes.value.data.rows : [];
        setTotalsRows(rows);
        if (!rows.length) {
          setTotalsMessage("総量は確定注文のみ集計されるため、対象データがありません。");
        }
      } else {
        setTotalsRows([]);
        setTotalsMessage("総量の取得に失敗しました。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setOrders([]);
      setDailyBagSummary({});
      setTotalsRows([]);
    } finally {
      setLoading(false);
    }
  };

  const closeOverrideEditor = () => {
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

  const openOverrideEditor = async (menuGroup: DailyBagMenuGroup) => {
    if (!date) return;
    setOverrideEditorLoading(true);
    setOverrideEditorMessage("");
    setOverrideEditorDrafts({});
    setOverrideEditorBulkSaving(false);
    try {
      const res = await apiClient.get("/orders/daily-output-overrides", {
        params: {
          date,
          daypart: menuGroup.daypart || menuGroup.daypart_key || "",
          menu_name: menuGroup.menu_name || "",
          menu_category: menuGroup.menu_category || "",
        },
      });
      const payload: DailyOutputOverrideResponse = res.data || {};
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
      setOverrideEditorDrafts(nextDrafts);
      setOverrideEditorBulkDraft({
        qty_per_serving:
          rows[0]?.override?.qty_per_serving != null
            ? String(rows[0].override?.qty_per_serving)
            : rows[0]?.current_qty_per_serving != null
              ? String(rows[0].current_qty_per_serving)
            : "",
        unit_type: normalizeOverrideUnitValue(rows[0]?.override?.unit_type || rows[0]?.current_unit_type || "g"),
        note: "",
      });
      if (!overrideEditorSelectedFacilityId) {
        setOverrideEditorSelectedFacilityId(String(rows[0]?.facility_id || "").trim());
      }
      if (!overrideEditorSelectedDietType) {
        setOverrideEditorSelectedDietType(normalizeDietType(rows[0]?.diet_type));
      }
      if (!rows.length) {
        setOverrideEditorMessage("編集対象の施設別行がありません。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setOverrideEditor({ date, daypart: menuGroup.daypart || "", menu_name: menuGroup.menu_name || "", menu_category: menuGroup.menu_category || "", rows: [] });
      setOverrideEditorMessage(detail ? `設定一覧の取得に失敗しました: ${detail}` : "設定一覧の取得に失敗しました。");
      setOverrideEditorSelectedFacilityId("");
      setOverrideEditorSelectedDietType("");
    } finally {
      setOverrideEditorLoading(false);
    }
  };

  const updateOverrideDraft = (
    rowKey: string,
    patch: Partial<DailyOutputOverrideDraft>,
  ) => {
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
    if (!selectedFacilityRows.length) return null;
    return (
      selectedFacilityRows.find((row) => normalizeDietType(row.diet_type) === overrideEditorSelectedDietType) ||
      selectedFacilityRows[0]
    );
  }, [overrideEditorSelectedDietType, selectedFacilityRows]);

  useEffect(() => {
    if (!overrideEditorRows.length) {
      if (overrideEditorSelectedFacilityId) setOverrideEditorSelectedFacilityId("");
      if (overrideEditorSelectedDietType) setOverrideEditorSelectedDietType("");
      return;
    }
    const facilityIds = new Set(overrideEditorRows.map((row) => String(row.facility_id || "").trim()).filter(Boolean));
    let nextFacilityId = overrideEditorSelectedFacilityId;
    if (!nextFacilityId || !facilityIds.has(nextFacilityId)) {
      nextFacilityId = String(overrideEditorRows[0]?.facility_id || "").trim();
      if (nextFacilityId !== overrideEditorSelectedFacilityId) {
        setOverrideEditorSelectedFacilityId(nextFacilityId);
      }
    }
    if (!nextFacilityId) return;
    const facilityRows = overrideEditorRows.filter((row) => String(row.facility_id || "").trim() === nextFacilityId);
    if (!facilityRows.length) return;
    const hasSelectedDiet = facilityRows.some(
      (row) => normalizeDietType(row.diet_type) === overrideEditorSelectedDietType,
    );
    if (!hasSelectedDiet) {
      const nextDietType = normalizeDietType(facilityRows[0]?.diet_type);
      if (nextDietType !== overrideEditorSelectedDietType) {
        setOverrideEditorSelectedDietType(nextDietType);
      }
    }
  }, [overrideEditorRows, overrideEditorSelectedFacilityId, overrideEditorSelectedDietType]);

  const saveOverrideRow = async (row: DailyOutputOverrideRow) => {
    if (!overrideEditor?.date) return;
    const rowKey = buildDailyOutputOverrideRowKey(row);
    const draft = overrideEditorDrafts[rowKey];
    if (!draft) return;
    const validationError = validateOverrideDraft(draft);
    if (validationError) {
      setOverrideEditorMessage(validationError);
      return;
    }
    const qtyPerServing = Number(draft.qty_per_serving);
    setOverrideEditorSavingKey(rowKey);
    setOverrideEditorMessage("");
    try {
      await apiClient.post("/orders/daily-output-overrides/upsert", {
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
      await Promise.all([openOverrideEditor({
        daypart: overrideEditor.daypart,
        menu_name: overrideEditor.menu_name,
        menu_category: overrideEditor.menu_category,
      }), loadOrders()]);
      setOverrideEditorMessage("施設別単位設定を保存しました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.code === "daily_output_override_ambiguous") {
        setOverrideEditorMessage("現状が複数候補です。候補を確認し、理解したうえで保存に進んでください。");
        updateOverrideDraft(rowKey, { acknowledge_ambiguous: true });
      } else if (detail?.code === "daily_output_override_target_not_found") {
        setOverrideEditorMessage("対象の施設別行が見つかりません。日別一覧を再取得してからやり直してください。");
      } else if (typeof detail === "string" && detail) {
        setOverrideEditorMessage(detail);
      } else {
        setOverrideEditorMessage("施設別単位設定の保存に失敗しました。");
      }
    } finally {
      setOverrideEditorSavingKey("");
    }
  };

  const saveBulkOverride = async () => {
    if (!overrideEditor?.date) return;
    const validationError = validateOverrideDraft(overrideEditorBulkDraft);
    if (validationError) {
      setOverrideEditorMessage(validationError);
      return;
    }
    setOverrideEditorBulkSaving(true);
    setOverrideEditorMessage("");
    try {
      await apiClient.post("/orders/daily-output-overrides/upsert-bulk", {
        date: overrideEditor.date,
        menu_name: overrideEditor.menu_name,
        daypart: overrideEditor.daypart,
        menu_category: overrideEditor.menu_category,
        qty_per_serving: Number(overrideEditorBulkDraft.qty_per_serving),
        unit_type: overrideEditorBulkDraft.unit_type,
        note: overrideEditorBulkDraft.note,
      });
      await Promise.all([
        openOverrideEditor({
          daypart: overrideEditor.daypart,
          menu_name: overrideEditor.menu_name,
          menu_category: overrideEditor.menu_category,
        }),
        loadOrders(),
      ]);
      setOverrideEditorMessage("全施設の単位設定を保存しました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.code === "daily_output_override_bulk_requires_intervention") {
        setOverrideEditorMessage("曖昧な施設が含まれるため一括保存できません。施設を選んで個別に確定してください。");
      } else if (typeof detail === "string" && detail) {
        setOverrideEditorMessage(detail);
      } else {
        setOverrideEditorMessage("全施設の単位設定保存に失敗しました。");
      }
    } finally {
      setOverrideEditorBulkSaving(false);
    }
  };

  const deleteOverrideRow = async (row: DailyOutputOverrideRow) => {
    const overrideId = row.override?.id;
    if (!overrideId || !overrideEditor) return;
    const rowKey = buildDailyOutputOverrideRowKey(row);
    setOverrideEditorSavingKey(rowKey);
    setOverrideEditorMessage("");
    try {
      await apiClient.delete(`/orders/daily-output-overrides/${overrideId}`);
      await Promise.all([openOverrideEditor({
        daypart: overrideEditor.daypart,
        menu_name: overrideEditor.menu_name,
        menu_category: overrideEditor.menu_category,
      }), loadOrders()]);
      setOverrideEditorMessage("施設別単位設定を解除しました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setOverrideEditorMessage(detail ? `解除に失敗しました: ${detail}` : "解除に失敗しました。");
    } finally {
      setOverrideEditorSavingKey("");
    }
  };

  const openOutput = async (path: string, label: string) => {
    const timestamp = new Date().toLocaleString("ja-JP");
    setMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "blob", timeout: 0 });
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
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const downloadDailyBundle = async (bundleType: "labels" | "delivery" | "both") => {
    if (!date) {
      setMessage("日付を指定してください。");
      return;
    }
    const label =
      bundleType === "labels"
        ? "当日ラベルExcel"
        : bundleType === "delivery"
          ? "当日納品書Excel"
          : "当日一括Excel（ラベル+納品書）";
    setMessage(`${label}を作成中です...`);
    try {
      const res = await apiClient.get("/outputs/daily-bundle", {
        params: { date, bundle_type: bundleType, status: status || undefined },
        responseType: "blob",
        timeout: 0,
      });
      const contentDisposition = headerValueToString(
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"],
      );
      const filename = extractFilename(contentDisposition) || `daily_outputs_${date}_${bundleType}.xlsx`;
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

  const toggleTotalRow = (rowKey: string) => {
    setExpandedTotalRows((prev) => {
      const next = new Set(prev);
      if (next.has(rowKey)) {
        next.delete(rowKey);
      } else {
        next.add(rowKey);
      }
      return next;
    });
  };

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
        <div className="filters">
          <label className="field">
            <span className="field-label">日付</span>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">ステータス</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <button className="btn primary" onClick={loadOrders} disabled={loading}>
            {loading ? "取得中..." : "取得"}
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("labels")} disabled={loading}>
            当日ラベルExcel
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("delivery")} disabled={loading}>
            当日納品書Excel
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("both")} disabled={loading}>
            当日一括Excel
          </button>
        </div>
        <p className="subtle helper-text">一括Excelと袋分けは選択したステータス、総量は確定注文ベースです。</p>
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
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => openOutput(`/outputs/labels?order_id=${order.id}`, "ラベルCSV")}
                      >
                        ラベル
                      </button>
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => openOutput(`/outputs/delivery-notes?order_id=${order.id}`, "納品書Excel")}
                      >
                        納品書
                      </button>
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() => openOutput(`/outputs/manufacturing-aggregate?order_id=${order.id}`, "総量CSV")}
                      >
                        総量CSV
                      </button>
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
                          <button
                            className="btn ghost"
                            type="button"
                            onClick={() => openOverrideEditor(menuGroup)}
                          >
                            施設別単位設定
                          </button>
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
                <th>施設別</th>
              </tr>
            </thead>
            <tbody>
              {totalsSummaryRows.length === 0 ? (
                <tr>
                  <td colSpan={6}>該当データなし</td>
                </tr>
              ) : (
                totalsSummaryRows.map((row, index) => {
                  const rowKey = buildTotalRowKey(row, index);
                  const refs = Array.isArray(row.order_refs) ? row.order_refs : [];
                  const expanded = expandedTotalRows.has(rowKey);
                  return (
                    <Fragment key={rowKey}>
                      <tr key={rowKey}>
                        <td>{row.daypart || "-"}</td>
                        <td>{row.menu_category || "-"}</td>
                        <td>{row.menu_name || "-"}</td>
                        <td>{formatDietType(row.diet_type)}</td>
                        <td className="numeric">{formatQuantity(row.quantity)}</td>
                        <td>
                          <button
                            className="btn ghost total-breakdown-toggle"
                            type="button"
                            onClick={() => toggleTotalRow(rowKey)}
                            disabled={!refs.length}
                            aria-expanded={expanded}
                          >
                            {expanded ? "閉じる" : "施設別"}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr key={`${rowKey}__breakdown`} className="total-breakdown-row">
                          <td colSpan={6}>
                            <div className="total-breakdown-panel">
                              {refs.length ? (
                                <table className="total-breakdown-table">
                                  <thead>
                                    <tr>
                                      <th>施設</th>
                                      <th>元区分</th>
                                      <th>エリア</th>
                                      <th>注文ID</th>
                                      <th>数量</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {refs.map((ref, refIndex) => (
                                      <tr
                                        key={[
                                          ref.order_id || "-",
                                          ref.facility_id || "-",
                                          ref.source_diet_type || "-",
                                          ref.area_id || "-",
                                          refIndex,
                                        ].join("__")}
                                      >
                                        <td>
                                          {ref.facility_name || ref.facility_id || "-"}
                                          {ref.facility_id ? <span className="total-breakdown-facility-id"> {ref.facility_id}</span> : null}
                                        </td>
                                        <td>{formatDietType(ref.source_diet_type)}</td>
                                        <td>{ref.area_id || "-"}</td>
                                        <td>
                                          {ref.order_id ? (
                                            <Link className="link" href={`/orders/${ref.order_id}/workflow-v2`}>
                                              {ref.order_id}
                                            </Link>
                                          ) : (
                                            "-"
                                          )}
                                        </td>
                                        <td className="numeric">{formatQuantity(ref.quantity)}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              ) : (
                                <p className="subtle">施設別内訳がありません。</p>
                              )}
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })
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
              <button className="btn ghost" type="button" onClick={closeOverrideEditor}>
                閉じる
              </button>
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
                      <input
                        className="input"
                        type="number"
                        step="0.1"
                        min="0"
                        value={overrideEditorBulkDraft.qty_per_serving}
                        onChange={(event) => updateOverrideBulkDraft({ qty_per_serving: event.target.value })}
                      />
                    </label>
                    <label className="field">
                      <span className="field-label">単位</span>
                      <select
                        className="input"
                        value={overrideEditorBulkDraft.unit_type}
                        onChange={(event) => updateOverrideBulkDraft({ unit_type: event.target.value })}
                      >
                        {overrideUnitOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field override-note-field">
                      <span className="field-label">理由メモ</span>
                      <input
                        className="input"
                        type="text"
                        value={overrideEditorBulkDraft.note}
                        onChange={(event) => updateOverrideBulkDraft({ note: event.target.value })}
                        placeholder={`例: 全施設 ${formatOverrideUnitLabel(overrideEditorBulkDraft.unit_type)}を統一`}
                      />
                    </label>
                  </div>
                  <div className="actions override-actions">
                    <button className="btn primary" type="button" onClick={saveBulkOverride} disabled={overrideEditorBulkSaving}>
                      {overrideEditorBulkSaving ? "保存中..." : "全施設に保存"}
                    </button>
                  </div>
                </section>

                <section className="override-editor-card">
                  <div className="override-selector-grid">
                    <label className="field">
                      <span className="field-label">施設を選ぶ</span>
                      <select
                        className="input"
                        value={overrideEditorSelectedFacilityId}
                        onChange={(event) => setOverrideEditorSelectedFacilityId(event.target.value)}
                      >
                        {overrideFacilityOptions.map((option) => (
                          <option key={option.facility_id} value={option.facility_id}>
                            {option.facility_label}
                            {option.override_count > 0 ? " / override有" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    {selectedFacilityRows.length > 1 ? (
                      <label className="field">
                        <span className="field-label">区分を選ぶ</span>
                        <select
                          className="input"
                          value={overrideEditorSelectedDietType}
                          onChange={(event) => setOverrideEditorSelectedDietType(event.target.value)}
                        >
                          {selectedFacilityRows.map((row) => (
                            <option key={buildDailyOutputOverrideRowKey(row)} value={normalizeDietType(row.diet_type)}>
                              {formatDietType(row.diet_type)}
                            </option>
                          ))}
                        </select>
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
                              <input
                                type="checkbox"
                                checked={draft.acknowledge_ambiguous}
                                onChange={(event) =>
                                  updateOverrideDraft(rowKey, { acknowledge_ambiguous: event.target.checked })
                                }
                              />
                              <span>候補を確認し、この値で上書きすることを理解した</span>
                            </label>
                          </div>
                        ) : null}
                        <div className="override-form-grid">
                          <label className="field">
                            <span className="field-label">1単位量</span>
                            <input
                              className="input"
                              type="number"
                              step="0.1"
                              min="0"
                              value={draft.qty_per_serving}
                              onChange={(event) => updateOverrideDraft(rowKey, { qty_per_serving: event.target.value })}
                            />
                          </label>
                          <label className="field">
                            <span className="field-label">単位</span>
                            <select
                              className="input"
                              value={draft.unit_type}
                              onChange={(event) => updateOverrideDraft(rowKey, { unit_type: event.target.value })}
                            >
                              {overrideUnitOptions.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="field override-note-field">
                            <span className="field-label">理由メモ</span>
                            <input
                              className="input"
                              type="text"
                              value={draft.note}
                              onChange={(event) => updateOverrideDraft(rowKey, { note: event.target.value })}
                              placeholder={`例: この施設のみ${formatOverrideUnitLabel(draft.unit_type)}を調整`}
                            />
                          </label>
                        </div>
                        <div className="actions override-actions">
                          <button className="btn primary" type="button" onClick={() => saveOverrideRow(row)} disabled={saving}>
                            {saving ? "保存中..." : "保存"}
                          </button>
                          {row.override?.id ? (
                            <button className="btn ghost" type="button" onClick={() => deleteOverrideRow(row)} disabled={saving}>
                              解除
                            </button>
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

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          justify-self: start;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: #eef2f0;
          color: #1f2a2a;
          border: 1px solid rgba(25, 32, 30, 0.12);
        }

        .btn:disabled {
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

        .total-breakdown-toggle {
          padding: 6px 10px;
          font-size: 12px;
        }

        .total-breakdown-row {
          background: #f8fbfa !important;
        }

        .total-breakdown-panel {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 14px;
          background: #ffffff;
          padding: 10px;
        }

        .total-breakdown-table {
          min-width: 100%;
          font-size: 12px;
        }

        .total-breakdown-table th,
        .total-breakdown-table td {
          padding: 7px 8px;
          white-space: nowrap;
        }

        .total-breakdown-facility-id {
          color: #778680;
          font-size: 11px;
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

        @media (max-width: 720px) {
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

          .override-actions .btn {
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
