import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import TopNav from "../components/TopNav";
import { useCurrentUserRole } from "../hooks/useCurrentUserRole";
import { apiClient } from "../services/apiClient";
import {
  describeAttentionReasons,
  type ShippingDateGroup as LatestShippingDateGroup,
  type ShippingFacilityGroup as LatestShippingFacilityGroup,
  type ShippingHistoryEvent,
  normalizeLatestResponse,
  type QuotaStatus,
  type ShippingHistorySummary,
  type ShippingLatestResponse,
  type ShippingLatestView,
  type ShippingLatestItem,
} from "../features/shipping/shippingHistory";

type HistoryTab = ShippingLatestView | "logs";

type ShippingHistoryItem = {
  id: string;
  tracking_key: string;
  tracking_number: string;
  ship_date?: string | null;
  facility_name?: string | null;
  status: string;
  delivered: boolean;
  arrival_text?: string | null;
  error?: string | null;
  source?: string | null;
  looked_up_at?: string | null;
};

type TrackingNumberCard = ShippingLatestItem & {
  events: ShippingHistoryEvent[];
};

type TrackingDateGroup = {
  key: string;
  label: string;
  sortKey: number;
  total: number;
  delivered: number;
  pending: number;
  notShipped: number;
  errors: number;
  attention: number;
  facilityGroups: TrackingFacilityGroup[];
  notFoundFacilityGroups: TrackingFacilityGroup[];
  notShippedFacilityGroups: TrackingFacilityGroup[];
};

type TrackingFacilityGroup = {
  key: string;
  facilityName: string;
  total: number;
  delivered: number;
  pending: number;
  errors: number;
  attention: number;
  latestLookedUpAt: string | null;
  statusCounts: Record<string, number>;
  items: TrackingNumberCard[];
};

type TrackingFacilityDisplayGroup = {
  key: string;
  label: string;
  facilityNames: string[];
};

type CalendarDayCell = {
  key: string;
  dateKey: string | null;
  dayOfMonth: number | null;
  inMonth: boolean;
  group: TrackingDateGroup | null;
};

type CalendarMonth = {
  label: string;
  weeks: CalendarDayCell[][];
};

const TRACKING_FACILITY_DISPLAY_GROUPS: TrackingFacilityDisplayGroup[] = [
  {
    key: "FAC-GRP-IKOI",
    label: "いこいの森 / いこいの森プラス",
    facilityNames: ["いこいの森", "いこいの森プラス"],
  },
  {
    key: "FAC-GRP-SHIMANTO",
    label: "ケアハウス四万十 / ケアハウス四万十ピア",
    facilityNames: ["ケアハウス四万十", "ケアハウス四万十ピア"],
  },
];

const TRACKING_FACILITY_DISPLAY_GROUP_BY_NAME = new Map<string, TrackingFacilityDisplayGroup>(
  TRACKING_FACILITY_DISPLAY_GROUPS.flatMap((group) =>
    group.facilityNames.map((facilityName) => [facilityName, group] as const),
  ),
);

const toTrackingNumberCard = (
  item: ShippingLatestItem,
  defaults: Partial<Pick<TrackingNumberCard, "ship_date" | "facility_name" | "facility_name_source">>,
): TrackingNumberCard => ({
  ...item,
  ship_date: item.ship_date || defaults.ship_date || null,
  facility_name: item.facility_name || defaults.facility_name || null,
  facility_name_source: item.facility_name_source || defaults.facility_name_source || null,
  events: [...(item.events || [])].sort((left, right) => compareDateDesc(left.occurred_at, right.occurred_at)),
});

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

const formatLocalDate = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const formatCompactDateTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatCompactEventTime = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatDateOnly = (value?: string | null) => {
  if (!value) return "未設定";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return toDateInput(date);
};

const formatGroupDateLabel = (value?: string | null) => {
  if (!value || value === "unknown") return "取得日未設定";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value.replace(/-/g, "/");
  }
  return value;
};

const lookedUpDateKey = (value?: string | null) => {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return toDateInput(date);
};

const trackingSourceLabel = (value?: string | null) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "shipping_pdf_parse") return "PDF解析";
  if (normalized === "excel_enrich") return "Excel更新";
  if (normalized === "manual_track") return "手動照会";
  if (normalized === "scheduled_refresh") return "自動再照会";
  return normalized || "未設定";
};

const normalizeTrackingFacilityDisplayGroup = (facilityName?: string | null): TrackingFacilityDisplayGroup => {
  const normalized = String(facilityName || "").trim() || "施設未設定";
  return (
    TRACKING_FACILITY_DISPLAY_GROUP_BY_NAME.get(normalized) || {
      key: normalized,
      label: normalized,
      facilityNames: [normalized],
    }
  );
};

const trackingTone = (item: TrackingNumberCard, staleHours: number) => {
  const reasons = describeAttentionReasons(item, staleHours);
  if (item.error) return "danger";
  if (item.delivered) return "ok";
  if (reasons.length > 0) return "warning";
  return "neutral";
};

const trackingOrderCardToneClass = (item: TrackingNumberCard, staleHours: number) => {
  const tone = trackingTone(item, staleHours);
  if (tone === "danger") return "shipping-tracking-card-danger";
  if (tone === "warning") return "shipping-tracking-card-warning";
  if (tone === "ok") return "shipping-tracking-card-ok";
  return "";
};

const formatRatio = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
};

const extractEventFacility = (event: ShippingHistoryEvent) => {
  return event.facility_name || event.facility || "-";
};

const normalizeSortDate = (value?: string | null) => {
  const timestamp = value ? Date.parse(value) : NaN;
  return Number.isFinite(timestamp) ? timestamp : NaN;
};

const compareDateDesc = (left?: string | null, right?: string | null) => {
  const leftTime = normalizeSortDate(left);
  const rightTime = normalizeSortDate(right);
  if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) return 0;
  if (Number.isNaN(leftTime)) return 1;
  if (Number.isNaN(rightTime)) return -1;
  return rightTime - leftTime;
};

const toDateInput = (date: Date) => {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const parts = formatter.formatToParts(date);
  const year = parts.find((item) => item.type === "year")?.value ?? "1970";
  const month = parts.find((item) => item.type === "month")?.value ?? "01";
  const day = parts.find((item) => item.type === "day")?.value ?? "01";
  return `${year}-${month}-${day}`;
};

const buildDefaultRange = () => {
  const end = new Date();
  const start = new Date(end.getTime() - 29 * 24 * 60 * 60 * 1000);
  return { start: toDateInput(start), end: toDateInput(end) };
};

const parseFacilityFilter = (value: string) =>
  value
    .split(/[,\n、]+/)
    .map((item) => item.trim())
    .filter(Boolean);

const buildLatestPath = (params: {
  view: ShippingLatestView;
  limit: number;
  baseDate: string;
  windowDays: number;
  facilityFilter: string;
  source: string;
  attentionStaleHours: number;
}) => {
  const search = new URLSearchParams();
  search.set("view", params.view);
  search.set("limit", String(Math.max(1, Math.min(params.limit, 1000))));
  search.set("base_date", params.baseDate);
  search.set("window_days", String(Math.max(0, Math.min(params.windowDays, 90))));
  if (params.source) search.set("source", params.source);
  if (params.view === "attention") {
    search.set("attention_stale_hours", String(Math.max(1, Math.min(params.attentionStaleHours, 336))));
  }
  parseFacilityFilter(params.facilityFilter).forEach((facilityName) => {
    search.append("facility_name", facilityName);
  });
  return `/shipping/status/latest?${search.toString()}`;
};

const buildHistoryPath = (params: { dateFrom: string; dateTo: string; limit: number }) => {
  const search = new URLSearchParams();
  search.set("limit", String(Math.max(1, Math.min(params.limit, 1000))));
  if (params.dateFrom) search.set("date_from", params.dateFrom);
  if (params.dateTo) search.set("date_to", params.dateTo);
  return `/shipping/status/history?${search.toString()}`;
};

const summarizeHistory = (summary: ShippingHistorySummary | null) => {
  if (!summary) return [];
  return [
    { label: "総件数", value: summary.total },
    { label: "有効中", value: summary.pending },
    { label: "配達完了", value: summary.delivered },
    { label: "照会失敗", value: summary.errors },
    { label: "要確認", value: summary.attention ?? 0 },
    { label: "施設未設定", value: summary.facility_missing ?? 0 },
  ];
};

const quotaTone = (quota?: QuotaStatus | null) => quota?.alert_level || "unknown";

const buildTrackingDateGroups = (response: ShippingLatestResponse | null, staleHours: number) => {
  if (!response) return [];
  const grouped = new Map<
    string,
    TrackingDateGroup & {
      facilityGroupMap: Map<string, TrackingFacilityGroup>;
      notFoundFacilityGroupMap: Map<string, TrackingFacilityGroup>;
      notShippedFacilityGroupMap: Map<string, TrackingFacilityGroup>;
    }
  >();
  response.date_groups.forEach((dateGroup: LatestShippingDateGroup) => {
    const key = String(dateGroup.ship_date || "").trim() || lookedUpDateKey(dateGroup.latest_looked_up_at);
    const current =
      grouped.get(key) || {
        key,
        label: formatGroupDateLabel(key),
        sortKey: key === "unknown" ? 0 : Date.parse(`${key}T00:00:00+09:00`) || 0,
        total: 0,
        delivered: 0,
        pending: 0,
        notShipped: 0,
        errors: 0,
        attention: 0,
        facilityGroups: [],
        notFoundFacilityGroups: [],
        notShippedFacilityGroups: [],
        facilityGroupMap: new Map<string, TrackingFacilityGroup>(),
        notFoundFacilityGroupMap: new Map<string, TrackingFacilityGroup>(),
        notShippedFacilityGroupMap: new Map<string, TrackingFacilityGroup>(),
      };
    dateGroup.facilities.forEach((facilityGroupSource: LatestShippingFacilityGroup) => {
      const displayGroup = normalizeTrackingFacilityDisplayGroup(facilityGroupSource.facility_name);
      facilityGroupSource.items.forEach((rawItem) => {
        const item = toTrackingNumberCard(rawItem, {
          ship_date: rawItem.ship_date || facilityGroupSource.ship_date || dateGroup.ship_date || null,
          facility_name: rawItem.facility_name || facilityGroupSource.facility_name || null,
          facility_name_source:
            rawItem.facility_name_source || facilityGroupSource.facility_name_source || null,
        });
        const reasons = describeAttentionReasons(item, staleHours);
        const targetMap =
          item.status === "発送しなかった"
            ? current.notShippedFacilityGroupMap
            : String(item.status || "").includes("該当") || reasons.includes("該当なし")
              ? current.notFoundFacilityGroupMap
              : current.facilityGroupMap;
        const facilityGroup =
          targetMap.get(displayGroup.key) || {
            key: displayGroup.key,
            facilityName: displayGroup.label,
            total: 0,
            delivered: 0,
            pending: 0,
            errors: 0,
            attention: 0,
            latestLookedUpAt: null,
            statusCounts: {},
            items: [],
          };
        current.total += 1;
        if (item.delivered) current.delivered += 1;
        else if (item.status === "発送しなかった") current.notShipped += 1;
        else current.pending += 1;
        if (item.error) current.errors += 1;
        if (reasons.length > 0) current.attention += 1;
        facilityGroup.total += 1;
        if (item.delivered) facilityGroup.delivered += 1;
        else if (item.status !== "発送しなかった") facilityGroup.pending += 1;
        if (item.error) facilityGroup.errors += 1;
        if (reasons.length > 0) facilityGroup.attention += 1;
        facilityGroup.statusCounts[item.status || "不明"] =
          (facilityGroup.statusCounts[item.status || "不明"] || 0) + 1;
        facilityGroup.items.push(item);
        if (compareDateDesc(item.looked_up_at, facilityGroup.latestLookedUpAt) < 0) {
          facilityGroup.latestLookedUpAt = item.looked_up_at || facilityGroup.latestLookedUpAt;
        }
        targetMap.set(displayGroup.key, facilityGroup);
      });
    });
    grouped.set(key, current);
  });
  return Array.from(grouped.values())
    .map((group) => ({
      ...group,
      facilityGroups: Array.from(group.facilityGroupMap.values())
        .map((facilityGroup) => ({
          ...facilityGroup,
          items: [...facilityGroup.items].sort((left, right) => {
            const byTone =
              ["danger", "warning", "neutral", "ok"].indexOf(trackingTone(left, staleHours)) -
              ["danger", "warning", "neutral", "ok"].indexOf(trackingTone(right, staleHours));
            if (byTone !== 0) return byTone;
            return (left.tracking_key || left.tracking_number).localeCompare(
              right.tracking_key || right.tracking_number,
              "ja",
            );
          }),
        }))
        .sort((left, right) => left.facilityName.localeCompare(right.facilityName, "ja")),
      notFoundFacilityGroups: Array.from(group.notFoundFacilityGroupMap.values())
        .map((facilityGroup) => ({
          ...facilityGroup,
          items: [...facilityGroup.items].sort((left, right) =>
            (left.tracking_key || left.tracking_number).localeCompare(
              right.tracking_key || right.tracking_number,
              "ja",
            ),
          ),
        }))
        .sort((left, right) => left.facilityName.localeCompare(right.facilityName, "ja")),
      notShippedFacilityGroups: Array.from(group.notShippedFacilityGroupMap.values())
        .map((facilityGroup) => ({
          ...facilityGroup,
          items: [...facilityGroup.items].sort((left, right) =>
            (left.tracking_key || left.tracking_number).localeCompare(
              right.tracking_key || right.tracking_number,
              "ja",
            ),
          ),
        }))
        .sort((left, right) => left.facilityName.localeCompare(right.facilityName, "ja")),
    }))
    .sort((left, right) => {
      if (left.key === "unknown" && right.key !== "unknown") return 1;
      if (right.key === "unknown" && left.key !== "unknown") return -1;
      return right.sortKey - left.sortKey;
    });
};

const buildHistoryDateGroups = (items: ShippingHistoryItem[]): TrackingDateGroup[] => {
  const groupedItems = new Map<string, ShippingHistoryItem[]>();
  items.forEach((item) => {
    const key = item.ship_date || lookedUpDateKey(item.looked_up_at);
    groupedItems.set(key, [...(groupedItems.get(key) || []), item]);
  });
  const response: ShippingLatestResponse = {
    view: "all",
    window_days: 0,
    summary: {
      total: items.length,
      delivered: items.filter((item) => item.delivered).length,
      pending: items.filter((item) => !item.delivered && item.status !== "発送しなかった").length,
      errors: items.filter((item) => item.error).length,
    },
    date_groups: Array.from(groupedItems.entries()).map(([dateKey, dateItems]) => ({
      ship_date: dateKey === "unknown" ? null : dateKey,
      item_count: dateItems.length,
      pending_count: dateItems.filter((item) => !item.delivered && item.status !== "発送しなかった").length,
      delivered_count: dateItems.filter((item) => item.delivered).length,
      latest_looked_up_at: dateItems[0]?.looked_up_at || null,
      facilities: [
        {
          ship_date: dateKey === "unknown" ? null : dateKey,
          facility_name: "監査ログ",
          item_count: dateItems.length,
          pending_count: dateItems.filter((item) => !item.delivered && item.status !== "発送しなかった").length,
          delivered_count: dateItems.filter((item) => item.delivered).length,
          latest_looked_up_at: dateItems[0]?.looked_up_at || null,
          items: dateItems.map((item) => ({
            ...item,
            ship_date: item.ship_date || (dateKey === "unknown" ? null : dateKey),
            events: [],
            attention_reasons: item.error ? ["照会失敗"] : [],
          })),
        },
      ],
    })),
    quota: null,
  };
  return buildTrackingDateGroups(response, 24);
};

const parseDateKey = (value?: string | null) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00+09:00`);
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatCalendarMonthLabel = (date: Date) =>
  date.toLocaleDateString("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "long",
  });

const addDays = (date: Date, days: number) => {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
};

const buildCalendarMonth = (params: {
  groups: TrackingDateGroup[];
  fallbackDate: string;
}): CalendarMonth => {
  const groupByDate = new Map(
    params.groups
      .filter((group) => parseDateKey(group.key))
      .map((group) => [group.key, group] as const),
  );
  const firstGroupDate = params.groups.map((group) => parseDateKey(group.key)).find(Boolean);
  const fallback = parseDateKey(params.fallbackDate) || new Date();
  const anchor = firstGroupDate || fallback;
  const monthStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const monthEnd = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  const gridStart = addDays(monthStart, -monthStart.getDay());
  const gridEnd = addDays(monthEnd, 6 - monthEnd.getDay());
  const cells: CalendarDayCell[] = [];
  for (let cursor = new Date(gridStart); cursor <= gridEnd; cursor = addDays(cursor, 1)) {
    const dateKey = toDateInput(cursor);
    const inMonth = cursor.getMonth() === monthStart.getMonth();
    cells.push({
      key: dateKey,
      dateKey,
      dayOfMonth: cursor.getDate(),
      inMonth,
      group: groupByDate.get(dateKey) || null,
    });
  }
  const weeks: CalendarDayCell[][] = [];
  for (let index = 0; index < cells.length; index += 7) {
    weeks.push(cells.slice(index, index + 7));
  }
  return {
    label: formatCalendarMonthLabel(monthStart),
    weeks,
  };
};

export default function ShippingHistoryPage() {
  const { isAdmin } = useCurrentUserRole();
  const defaultRange = buildDefaultRange();
  const [activeTab, setActiveTab] = useState<HistoryTab>("active");
  const [limit, setLimit] = useState<number>(200);
  const [baseDate, setBaseDate] = useState<string>(defaultRange.end);
  const [windowDays, setWindowDays] = useState<number>(3);
  const [facilityFilter, setFacilityFilter] = useState<string>("");
  const [source, setSource] = useState<string>("");
  const [attentionStaleHours, setAttentionStaleHours] = useState<number>(24);
  const [dateFrom, setDateFrom] = useState<string>(defaultRange.start);
  const [dateTo, setDateTo] = useState<string>(defaultRange.end);
  const [loading, setLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");
  const [latest, setLatest] = useState<ShippingLatestResponse | null>(null);
  const [historyItems, setHistoryItems] = useState<ShippingHistoryItem[]>([]);
  const [historySummary, setHistorySummary] = useState<ShippingHistorySummary | null>(null);
  const [historyQuota, setHistoryQuota] = useState<QuotaStatus | null>(null);
  const [selectedDateGroupKey, setSelectedDateGroupKey] = useState<string | null>(null);
  const trackingDateGroups = useMemo(
    () => buildTrackingDateGroups(latest, attentionStaleHours),
    [attentionStaleHours, latest],
  );
  const historyDateGroups = useMemo(() => buildHistoryDateGroups(historyItems), [historyItems]);

  const loadLatest = async (viewOverride?: ShippingLatestView) => {
    const view = viewOverride || (activeTab === "logs" ? "active" : activeTab);
    setLoading(true);
    setMessage("集約済みの追跡状況を取得中です...");
    try {
      const res = await apiClient.get(
        buildLatestPath({
          view,
          limit,
          baseDate,
          windowDays,
          facilityFilter,
          source,
          attentionStaleHours,
        }),
      );
      const normalized = normalizeLatestResponse(res.data, {
        view,
        base_date: baseDate,
        window_days: windowDays,
      });
      setLatest(normalized);
      setMessage(
        view === "recent"
          ? `基準日 ${baseDate} の±${normalized.window_days}日を取得しました。`
          : `集約済みの追跡状況を取得しました（${normalized.summary.total}件）。`,
      );
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setLatest(null);
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  const loadHistory = async () => {
    setLoading(true);
    setMessage("監査ログを取得中です...");
    try {
      const res = await apiClient.get(buildHistoryPath({ dateFrom, dateTo, limit }));
      const nextItems = Array.isArray(res.data?.items) ? res.data.items : [];
      setHistoryItems(nextItems);
      setHistorySummary(res.data?.summary || null);
      setHistoryQuota(res.data?.quota || null);
      setMessage(`監査ログを取得しました（${nextItems.length}件）。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setHistoryItems([]);
      setHistorySummary(null);
      setHistoryQuota(null);
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  const downloadHistory = async (format: "csv" | "json") => {
    setMessage(`監査ログ${format.toUpperCase()}を作成中です...`);
    try {
      const res = await apiClient.get(
        `/shipping/status/export?${new URLSearchParams({
          format,
          limit: "1000000",
          ...(dateFrom ? { date_from: dateFrom } : {}),
          ...(dateTo ? { date_to: dateTo } : {}),
        }).toString()}`,
        { responseType: "blob" },
      );
      const contentDisposition =
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || `shipping_tracking_history.${format}`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setMessage(`監査ログ${format.toUpperCase()}をダウンロードしました。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const clearAllHistory = async () => {
    const ok = window.confirm(
      "佐川追跡の監査ログを全件削除します。取り消せません。実行してよいですか？",
    );
    if (!ok) return;
    setLoading(true);
    setMessage("監査ログを削除中です...");
    try {
      const res = await apiClient.delete("/shipping/status/history");
      const removed = Number(res.data?.removed || 0);
      setMessage(`監査ログを削除しました（${removed}件）。`);
      await loadHistory();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `削除に失敗しました: ${detail}` : "削除に失敗しました。");
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "logs") {
      loadHistory();
      return;
    }
    loadLatest(activeTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const currentSummary = activeTab === "logs" ? historySummary : latest?.summary || null;
  const currentQuota = activeTab === "logs" ? historyQuota : latest?.quota || null;
  const nonLogDateHeading = "日付ごとの出荷状況";
  const nonLogDateCopy = "日付にはショートサマリーだけを表示し、クリックで詳細を確認します。";
  const visibleDateGroups = activeTab === "logs" ? historyDateGroups : trackingDateGroups;
  const selectedDateGroup = visibleDateGroups.find((group) => group.key === selectedDateGroupKey) || null;
  const calendarMonth = useMemo(
    () =>
      buildCalendarMonth({
        groups: visibleDateGroups,
        fallbackDate: activeTab === "logs" ? dateTo || dateFrom : baseDate,
      }),
    [activeTab, baseDate, dateFrom, dateTo, visibleDateGroups],
  );

  const renderTrackingNumberRow = (item: TrackingNumberCard) => {
    const reasons = describeAttentionReasons(item, attentionStaleHours);
    const tone = trackingTone(item, attentionStaleHours);
    return (
      <article
        key={item.tracking_key || item.tracking_number}
        className={`shipping-tracking-card ${trackingOrderCardToneClass(
          item,
          attentionStaleHours,
        )}`.trim()}
        data-testid="shipping-tracking-card"
      >
        <div className="shipping-tracking-card-top">
          <div>
            <p className="shipping-tracking-card-number">
              {item.tracking_number || item.tracking_key || "-"}
            </p>
          </div>
          <span className={`status-pill tone-${tone}`}>{item.status || "-"}</span>
        </div>
        <div className="tracking-card-actions">
          <details className="tracking-events tracking-events-compact">
            <summary className="tracking-events-summary">履歴 {item.events.length}件</summary>
            {item.events.length ? (
              <ul className="event-list">
                {item.events.map((event, eventIndex) => (
                  <li key={`${event.occurred_at || "-"}-${eventIndex}-${event.status}`}>
                    <strong>{event.status || "-"}</strong>
                    <span>{formatCompactEventTime(event.occurred_at || null)}</span>
                    <span>{extractEventFacility(event)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="subtle compact-subtle">佐川履歴は未取得です。</p>
            )}
          </details>
          <details className="tracking-events tracking-events-compact">
            <summary className="tracking-events-summary">詳細</summary>
            <ul className="tracking-detail-list">
              <li>
                <span>更新</span>
                <strong>{formatCompactDateTime(item.looked_up_at)}</strong>
              </li>
              <li>
                <span>出荷日</span>
                <strong>{formatDateOnly(item.ship_date)}</strong>
              </li>
              <li>
                <span>到着</span>
                <strong>{item.arrival_text || "-"}</strong>
              </li>
              <li>
                <span>取得元</span>
                <strong>{trackingSourceLabel(item.source)}</strong>
              </li>
              {item.error ? (
                <li>
                  <span>エラー</span>
                  <strong>{item.error}</strong>
                </li>
              ) : null}
              {reasons.length ? (
                <li>
                  <span>要確認</span>
                  <strong>{reasons.join(" / ")}</strong>
                </li>
              ) : null}
            </ul>
          </details>
        </div>
      </article>
    );
  };

  const renderFacilityGroupCard = (
    facilityGroup: TrackingFacilityGroup,
    options?: { unmatched?: boolean },
  ) => {
    const statusParts = Object.entries(facilityGroup.statusCounts)
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "ja"))
      .map(([status, count]) => `${status} ${count}`);
    return (
      <section
        key={facilityGroup.key}
        className={`facility-slot shipping-facility-slot${
          options?.unmatched ? " facility-slot-unmatched shipping-unmatched-slot" : ""
        }`}
        data-testid="shipping-facility-card"
      >
        <div className="facility-slot-top">
          <div>
            <p className="facility-slot-kicker">{options?.unmatched ? "該当無し" : "施設"}</p>
            <h4 className="facility-slot-name">{facilityGroup.facilityName}</h4>
          </div>
          <span className="facility-slot-badge">{facilityGroup.total}件</span>
        </div>
        <div className="week-group-summary shipping-status-summary">
          {statusParts.map((part) => (
            <span key={`${facilityGroup.key}-${part}`} className="week-group-summary-item">
              {part}
            </span>
          ))}
        </div>
        <div className="shipping-number-list">
          {facilityGroup.items.map((item) => renderTrackingNumberRow(item))}
        </div>
      </section>
    );
  };

  return (
    <main className="page shipping-history-page">
      <header className="hero">
        <div>
          <p className="eyebrow">Shipping Status</p>
          <h1>佐川出荷状況</h1>
          <p className="subtle">
            伝票番号ごとの最新状態を中心に表示し、履歴は「佐川履歴」があれば下部に表示します。
          </p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <div>
            <h2>表示モード</h2>
            <p className="panel-copy">既定は有効中です。監査ログは後段の補助タブです。</p>
          </div>
          <Link href="/shipping" className="ghost-link">
            送り状ページへ
          </Link>
        </header>
        <div className="tab-row">
          {[
            { id: "active", label: "有効中" },
            { id: "recent", label: "直近 ±日数" },
            { id: "attention", label: "要確認" },
            { id: "all", label: "全件" },
            { id: "logs", label: "監査ログ" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id as HistoryTab)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "logs" ? (
          <div className="filters">
            <label className="field">
              <span className="field-label">開始日</span>
              <input
                className="input"
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">終了日</span>
              <input
                className="input"
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">件数上限</span>
              <input
                className="input"
                type="number"
                min={1}
                max={1000}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value || 200))}
              />
            </label>
            <button className="btn primary" onClick={loadHistory} disabled={loading}>
              {loading ? "取得中..." : "監査ログを更新"}
            </button>
            {isAdmin ? (
              <>
                <button className="btn ghost" onClick={() => downloadHistory("csv")} disabled={loading}>
                  CSV保存
                </button>
                <button className="btn ghost" onClick={() => downloadHistory("json")} disabled={loading}>
                  JSON保存
                </button>
                <button className="btn danger" onClick={clearAllHistory} disabled={loading}>
                  全件クリア
                </button>
              </>
            ) : null}
          </div>
        ) : (
          <div className="filters">
            <label className="field">
              <span className="field-label">基準日</span>
              <input
                className="input"
                type="date"
                value={baseDate}
                onChange={(e) => setBaseDate(e.target.value)}
              />
            </label>
            <label className="field narrow">
              <span className="field-label">±日数</span>
              <input
                className="input"
                type="number"
                min={0}
                max={90}
                value={windowDays}
                onChange={(e) => setWindowDays(Number(e.target.value || 0))}
              />
            </label>
            <label className="field">
              <span className="field-label">施設</span>
              <input
                className="input"
                type="text"
                placeholder="施設名、複数はカンマ区切り"
                value={facilityFilter}
                onChange={(e) => setFacilityFilter(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field-label">取得元</span>
              <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">すべて</option>
                <option value="shipping_pdf_parse">PDF解析</option>
                <option value="excel_enrich">Excel更新</option>
                <option value="manual_track">手動照会</option>
                <option value="scheduled_refresh">自動再照会</option>
              </select>
            </label>
            <label className="field narrow">
              <span className="field-label">停滞時間(h)</span>
              <input
                className="input"
                type="number"
                min={1}
                max={336}
                value={attentionStaleHours}
                onChange={(e) => setAttentionStaleHours(Number(e.target.value || 24))}
              />
            </label>
            <label className="field narrow">
              <span className="field-label">件数上限</span>
              <input
                className="input"
                type="number"
                min={1}
                max={1000}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value || 200))}
              />
            </label>
            <button className="btn primary" onClick={() => loadLatest()} disabled={loading}>
              {loading ? "取得中..." : "状態を更新"}
            </button>
          </div>
        )}

        {activeTab === "recent" ? (
          <p className="subtle note">
            `発送日` が保存されている行は発送日ベース、旧データは最終照会日のローカル日付で補完して表示します。
          </p>
        ) : null}
        {!isAdmin && activeTab === "logs" ? (
          <p className="subtle note">監査ログの保存と全件クリアは管理者のみ表示されます。</p>
        ) : null}
        {message ? <p className="message">{message}</p> : null}
      </section>

        {currentSummary ? (
        <section className="panel">
          <header className="panel-header">
            <div>
              <h2>{activeTab === "logs" ? "監査ログの集計" : "現在の集計"}</h2>
              <p className="panel-copy">
                {activeTab === "logs"
                  ? "生ログ件数です。重めの参照データなので運用主画面としては補助用途です。"
                  : "伝票番号を主軸に、最新状態を1件ずつ表示します。"}
              </p>
            </div>
          </header>
          <div className="summary-grid">
            {summarizeHistory(currentSummary).map((item) => (
              <article className="summary-card" key={item.label}>
                <p className="summary-label">{item.label}</p>
                <p className="summary-value">{item.value}</p>
              </article>
            ))}
          </div>
          {activeTab !== "logs" && latest ? (
            <div className="meta-grid">
              <p>生成時刻: {formatLocalDate(latest.generated_at)}</p>
              <p>最終自動照会: {formatLocalDate(latest.last_scheduled_refresh_at)}</p>
              <p>次回自動照会: {formatLocalDate(latest.next_scheduled_refresh_at)}</p>
            </div>
          ) : null}
        </section>
      ) : null}

      {currentQuota ? (
        <section className={`panel quota-alert quota-${quotaTone(currentQuota)}`}>
          <header className="panel-header">
            <div>
              <h2>Quota 状態</h2>
              <p className="panel-copy">生ログ（lookup）ベースの使用量です。</p>
            </div>
          </header>
          <div className="meta-grid">
            <p>
              使用量: {currentQuota.used ?? "-"} / 上限: {currentQuota.limit ?? "-"} (
              {formatRatio(currentQuota.ratio)})
            </p>
            <p>
              対象: {currentQuota.resource || "-"} / 単位: {currentQuota.unit || "-"}
            </p>
            <p>{currentQuota.message || "quota情報なし"}</p>
          </div>
        </section>
      ) : null}

      {(
        <section className="panel">
          <header className="panel-header">
            <div>
              <h2>{activeTab === "logs" ? "監査ログカレンダー" : nonLogDateHeading}</h2>
              <p className="panel-copy">
                {activeTab === "logs"
                  ? "照会ログも日付ごとのショートサマリーに集約し、詳細はポップアップで表示します。"
                  : nonLogDateCopy}
              </p>
            </div>
          </header>

          {calendarMonth.weeks.length ? (
            <div className="calendar-shell" data-testid="shipping-calendar">
              <div className="calendar-month-header">
                <h3>{calendarMonth.label}</h3>
              </div>
              <div className="calendar-weekdays" aria-hidden="true">
                {["日", "月", "火", "水", "木", "金", "土"].map((weekday) => (
                  <span key={weekday}>{weekday}</span>
                ))}
              </div>
              <div className="calendar-grid">
                {calendarMonth.weeks.flatMap((week) =>
                  week.map((cell) => {
                    const group = cell.group;
                    const notFoundCount =
                      group?.notFoundFacilityGroups.reduce(
                        (sum, facilityGroup) => sum + facilityGroup.total,
                        0,
                      ) || 0;
                    return (
                      <button
                        key={cell.key}
                        type="button"
                        className={`calendar-day${cell.inMonth ? "" : " outside-month"}${
                          group ? " has-items" : ""
                        }`}
                        data-testid={group ? "shipping-date-group" : "shipping-empty-day"}
                        onClick={() => {
                          if (group) setSelectedDateGroupKey(group.key);
                        }}
                        disabled={!group}
                      >
                        <span className="calendar-day-number">{cell.dayOfMonth}</span>
                        {group ? (
                          <>
                            <span className="calendar-day-main">{group.total}件</span>
                            <span className="calendar-day-summary">
                              完了 {group.delivered} / 未完了 {group.pending}
                            </span>
                            {group.notShipped > 0 ? (
                              <span className="calendar-day-summary muted">発送なし {group.notShipped}</span>
                            ) : null}
                            {group.attention > 0 || notFoundCount > 0 ? (
                              <span className="calendar-day-alert">
                                {group.attention > 0 ? `要確認 ${group.attention}` : ""}
                                {group.attention > 0 && notFoundCount > 0 ? " / " : ""}
                                {notFoundCount > 0 ? `該当無し ${notFoundCount}` : ""}
                              </span>
                            ) : null}
                          </>
                        ) : (
                          <>
                            <span className="calendar-day-main empty-count">0件</span>
                            <span className="calendar-day-summary muted">完了 0 / 未完了 0</span>
                          </>
                        )}
                      </button>
                    );
                  }),
                )}
              </div>
              {visibleDateGroups.some((group) => !parseDateKey(group.key)) ? (
                <div className="calendar-undated">
                  {visibleDateGroups
                    .filter((group) => !parseDateKey(group.key))
                    .map((group) => {
                      const notFoundCount = group.notFoundFacilityGroups.reduce(
                        (sum, facilityGroup) => sum + facilityGroup.total,
                        0,
                      );
                      return (
                        <button
                          key={group.key}
                          type="button"
                          className="calendar-day has-items"
                          data-testid="shipping-date-group"
                          onClick={() => setSelectedDateGroupKey(group.key)}
                        >
                          <span className="calendar-day-number">{group.label}</span>
                          <span className="calendar-day-main">{group.total}件</span>
                          <span className="calendar-day-summary">
                            完了 {group.delivered} / 未完了 {group.pending}
                          </span>
                          {group.notShipped > 0 ? (
                            <span className="calendar-day-summary muted">発送なし {group.notShipped}</span>
                          ) : null}
                          {group.attention > 0 || notFoundCount > 0 ? (
                            <span className="calendar-day-alert">
                              {group.attention > 0 ? `要確認 ${group.attention}` : ""}
                              {group.attention > 0 && notFoundCount > 0 ? " / " : ""}
                              {notFoundCount > 0 ? `該当無し ${notFoundCount}` : ""}
                            </span>
                          ) : null}
                        </button>
                      );
                    })}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="empty">{activeTab === "logs" ? "監査ログがありません。" : "条件に一致する伝票はありません。"}</p>
          )}
        </section>
      )}

      {selectedDateGroup ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setSelectedDateGroupKey(null)}>
          <section
            className="detail-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="shipping-detail-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <p className="week-group-kicker">取得日</p>
                <h2 id="shipping-detail-title">{selectedDateGroup.label}</h2>
              </div>
              <button type="button" className="week-group-toggle" onClick={() => setSelectedDateGroupKey(null)}>
                閉じる
              </button>
            </header>
            <div className="week-group-summary">
              <span className="week-group-summary-item">件数 {selectedDateGroup.total}件</span>
              <span className="week-group-summary-item">完了 {selectedDateGroup.delivered}件</span>
              <span className="week-group-summary-item">未完了 {selectedDateGroup.pending}件</span>
              {selectedDateGroup.notShipped > 0 ? (
                <span className="week-group-summary-item">発送なし {selectedDateGroup.notShipped}件</span>
              ) : null}
              {selectedDateGroup.attention > 0 ? (
                <span className="week-group-summary-item">要確認 {selectedDateGroup.attention}件</span>
              ) : null}
            </div>
            <div className="facility-slot-grid modal-facility-grid">
              {selectedDateGroup.notFoundFacilityGroups.map((facilityGroup) =>
                renderFacilityGroupCard(facilityGroup, { unmatched: true }),
              )}
              {selectedDateGroup.facilityGroups.map((facilityGroup) => renderFacilityGroupCard(facilityGroup))}
            </div>
            {selectedDateGroup.notShippedFacilityGroups.length ? (
              <details className="not-shipped-minimized" data-testid="not-shipped-minimized">
                <summary>
                  発送しなかった番号 {selectedDateGroup.notShipped}件
                </summary>
                <div className="facility-slot-grid modal-facility-grid">
                  {selectedDateGroup.notShippedFacilityGroups.map((facilityGroup) =>
                    renderFacilityGroupCard(facilityGroup),
                  )}
                </div>
              </details>
            ) : null}
          </section>
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
          margin: 0 0 8px;
        }

        h1 {
          font-size: clamp(26px, 4vw, 36px);
          margin: 0 0 12px;
        }

        h2,
        h3,
        h4 {
          margin: 0;
        }

        .subtle {
          color: #51615c;
          margin: 0;
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
          align-items: flex-start;
          gap: 16px;
          margin-bottom: 16px;
        }

        .panel-copy {
          color: #51615c;
          font-size: 13px;
          margin: 6px 0 0;
        }

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
          white-space: nowrap;
        }

        .week-group-toggle {
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #ffffff;
          color: #243330;
          padding: 8px 14px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 700;
          cursor: pointer;
          white-space: nowrap;
        }

        .tab-row {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin-bottom: 16px;
        }

        .tab {
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 999px;
          padding: 8px 14px;
          background: #f3f6f4;
          color: #31423d;
          font-weight: 600;
          cursor: pointer;
        }

        .tab.active {
          background: #1f2a2a;
          border-color: #1f2a2a;
          color: #f7f2e7;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          align-items: end;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
        }

        .field.narrow {
          max-width: 160px;
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
          min-height: 40px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 16px;
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
          background: #eef3f1;
        }

        .btn.danger {
          background: #7a2d2d;
          color: #fff6f6;
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

        .note {
          margin-top: 12px;
          font-size: 13px;
        }

        .summary-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        }

        .summary-card {
          border-radius: 14px;
          padding: 14px;
          background: #fbf8ef;
          border: 1px solid rgba(25, 32, 30, 0.08);
        }

        .summary-label {
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: #5f7b74;
          margin: 0 0 8px;
        }

        .summary-value {
          font-size: 24px;
          font-weight: 700;
          margin: 0;
        }

        .meta-grid {
          margin-top: 14px;
          display: grid;
          gap: 8px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          font-size: 13px;
        }

        .quota-alert {
          background: #f5f8f6;
        }

        .quota-warning {
          background: #fff4df;
          border-color: rgba(173, 102, 0, 0.35);
        }

        .quota-critical {
          background: #ffe7e7;
          border-color: rgba(170, 45, 45, 0.35);
        }

        .table-wrap {
          overflow-x: auto;
        }

        .tracking-card-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .tracking-events {
          margin: 0;
          padding-top: 0;
        }

        .tracking-events-compact {
          padding-top: 0;
        }

        :global(.shipping-history-page .tracking-events-summary) {
          cursor: pointer;
          color: #243330;
          font-weight: 700;
          font-size: 12px;
          list-style: none;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          min-height: 28px;
          padding: 6px 10px;
          border-radius: 999px;
          background: #f3f6f4;
          border: 1px solid rgba(25, 32, 30, 0.1);
          user-select: none;
          transition:
            background-color 0.16s ease,
            border-color 0.16s ease,
            box-shadow 0.16s ease,
            transform 0.16s ease;
        }

        :global(.shipping-history-page .tracking-events-summary)::-webkit-details-marker {
          display: none;
        }

        :global(.shipping-history-page .tracking-events-summary)::after {
          content: "▸";
          color: #62736d;
          font-size: 11px;
          line-height: 1;
          transition:
            transform 0.16s ease,
            color 0.16s ease;
        }

        :global(.shipping-history-page .tracking-events-summary:hover) {
          background: #e8efec;
          border-color: rgba(36, 51, 48, 0.18);
          box-shadow: 0 4px 10px rgba(27, 35, 33, 0.06);
        }

        :global(.shipping-history-page .tracking-events-summary:focus-visible) {
          outline: 2px solid rgba(82, 122, 110, 0.45);
          outline-offset: 2px;
          background: #e8efec;
          border-color: rgba(36, 51, 48, 0.18);
        }

        :global(.shipping-history-page .tracking-events[open] .tracking-events-summary) {
          background: #dde7e2;
          border-color: rgba(36, 51, 48, 0.22);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
        }

        :global(.shipping-history-page .tracking-events[open] .tracking-events-summary)::after {
          transform: rotate(90deg);
          color: #243330;
        }

        .tracking-detail-list {
          list-style: none;
          padding: 8px 0 0;
          margin: 0;
          display: grid;
          gap: 6px;
        }

        .tracking-detail-list li {
          display: flex;
          justify-content: space-between;
          gap: 10px;
          font-size: 12px;
          color: #31423d;
        }

        .tracking-detail-list span {
          color: #6a7a75;
        }

        .tracking-detail-list strong {
          text-align: right;
          font-weight: 600;
        }

        .event-list {
          list-style: none;
          padding: 8px 0 0;
          margin: 0;
          display: grid;
          gap: 6px;
        }

        .event-list li {
          display: grid;
          grid-template-columns: minmax(0, 76px) minmax(0, 76px) minmax(0, 1fr);
          gap: 7px;
          align-items: start;
          padding: 6px 7px;
          border-radius: 10px;
          background: #f7faf8;
          font-size: 11px;
          color: #31423d;
        }

        .event-list-empty {
          padding: 6px 7px;
          border-radius: 10px;
          background: #f7faf8;
          font-size: 11px;
          color: #51615c;
        }

        .compact-subtle {
          font-size: 12px;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
          white-space: nowrap;
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          vertical-align: top;
        }

        thead {
          background: #f4f1ea;
        }

        .date-stack {
          display: grid;
          gap: 18px;
        }

        .calendar-shell {
          display: grid;
          gap: 10px;
        }

        .calendar-month-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          min-height: 32px;
        }

        .calendar-month-header h3 {
          font-size: 20px;
          line-height: 1.25;
        }

        .calendar-weekdays {
          display: grid;
          grid-template-columns: repeat(7, minmax(120px, 1fr));
          gap: 10px;
        }

        .calendar-weekdays span {
          min-height: 30px;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 10px;
          background: #eef2f0;
          color: #4f625d;
          font-size: 12px;
          font-weight: 800;
        }

        .calendar-grid {
          display: grid;
          grid-template-columns: repeat(7, minmax(120px, 1fr));
          gap: 10px;
        }

        .calendar-day {
          min-height: 132px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          padding: 12px;
          background: #fbfbf9;
          color: #1f2a2a;
          text-align: left;
          display: flex;
          flex-direction: column;
          gap: 7px;
          cursor: pointer;
        }

        .calendar-day:disabled {
          cursor: default;
        }

        .calendar-day:hover,
        .calendar-day:focus-visible {
          border-color: rgba(36, 51, 48, 0.28);
          background: #f2f6f4;
          outline: none;
        }

        .calendar-day:disabled:hover {
          border-color: rgba(25, 32, 30, 0.08);
          background: #f7f7f4;
        }

        .calendar-day.outside-month {
          background: #f1f2ef;
          color: #8a938e;
        }

        .calendar-day.has-items {
          background: #fbfbf9;
          border-color: rgba(36, 51, 48, 0.18);
        }

        .calendar-day-number {
          font-size: 13px;
          font-weight: 800;
        }

        .calendar-day-main {
          font-size: 22px;
          font-weight: 800;
          line-height: 1.15;
        }

        .calendar-day-main.empty-count {
          color: #8b9590;
        }

        .calendar-day-summary,
        .calendar-day-alert {
          font-size: 12px;
          line-height: 1.35;
          color: #41514d;
        }

        .calendar-day-summary.muted {
          color: #6f6255;
        }

        .calendar-day-alert {
          margin-top: auto;
          color: #7a3d2d;
          font-weight: 700;
        }

        .calendar-undated {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          gap: 10px;
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px solid rgba(25, 32, 30, 0.08);
        }

        .modal-backdrop {
          position: fixed;
          inset: 0;
          z-index: 40;
          background: rgba(18, 24, 22, 0.46);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 28px;
        }

        .detail-modal {
          width: min(1080px, 100%);
          max-height: min(820px, calc(100vh - 56px));
          overflow: auto;
          border-radius: 16px;
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.1);
          box-shadow: 0 24px 80px rgba(12, 18, 16, 0.32);
          padding: 20px;
        }

        .modal-header {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
        }

        .modal-facility-grid {
          margin-top: 16px;
        }

        .not-shipped-minimized {
          margin-top: 16px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          background: #faf8f2;
          padding: 12px;
        }

        .not-shipped-minimized summary {
          cursor: pointer;
          font-size: 13px;
          font-weight: 800;
          color: #4d463c;
        }

        .week-group {
          border: 1px solid rgba(25, 32, 30, 0.08);
          border-radius: 18px;
          background: linear-gradient(180deg, rgba(250, 247, 240, 0.65), rgba(255, 255, 255, 0.96));
          padding: 18px;
        }

        @media (max-width: 980px) {
          .calendar-weekdays,
          .calendar-grid {
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          }
        }

        @media (max-width: 640px) {
          .modal-backdrop {
            padding: 12px;
          }

          .detail-modal {
            max-height: calc(100vh - 24px);
            padding: 16px;
          }
        }

        .week-group-unresolved {
          border: 1px solid rgba(61, 74, 71, 0.14);
          background:
            linear-gradient(180deg, rgba(248, 246, 242, 0.98), rgba(241, 237, 231, 0.98));
          box-shadow:
            inset 0 0 0 1px rgba(255, 255, 255, 0.7),
            0 16px 30px rgba(28, 36, 34, 0.08);
        }

        .week-group-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
        }

        .week-group-header h3 {
          margin: 2px 0 0;
          font-size: 22px;
          line-height: 1.2;
        }

        .week-group-kicker {
          margin: 0;
          color: #6f7f79;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
        }

        .week-group-header-actions {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 10px;
        }

        .week-counts {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 8px;
        }

        .week-count {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          background: #eef2f0;
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
        }

        .week-group-summary {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 14px;
        }

        .week-group-summary-item {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          background: rgba(236, 241, 239, 0.96);
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
        }

        .week-group-body {
          margin-top: 14px;
        }

        .facility-slot-grid {
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          display: grid;
          gap: 14px;
          align-items: start;
          grid-auto-rows: max-content;
        }

        .facility-slot {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 14px;
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: rgba(255, 255, 255, 0.92);
          align-self: start;
        }

        .facility-slot-top {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
        }

        .facility-slot-kicker {
          margin: 0;
          color: #6f7f79;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
        }

        .facility-slot-name {
          margin: 4px 0 0;
          font-size: 18px;
          line-height: 1.25;
        }

        .facility-slot-badge {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          background: #eef2f0;
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
          white-space: nowrap;
        }

        .facility-slot-unmatched {
          border-color: rgba(171, 125, 35, 0.28);
          background: #fffaf0;
        }

        .week-group-unresolved .facility-slot-unmatched {
          border-color: rgba(171, 125, 35, 0.28);
          background: rgba(255, 250, 240, 0.98);
        }

        .shipping-unmatched-slot {
          border-style: dashed;
        }

        :global(.shipping-history-page .facility-slot-grid) {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 14px;
          align-items: start;
          grid-auto-rows: max-content;
        }

        :global(.shipping-history-page .facility-slot) {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 14px;
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: rgba(255, 255, 255, 0.92);
        }

        :global(.shipping-history-page .facility-slot-top) {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
        }

        :global(.shipping-history-page .facility-slot-kicker) {
          margin: 0;
          color: #6f7f79;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
        }

        :global(.shipping-history-page .facility-slot-name) {
          margin: 4px 0 0;
          font-size: 18px;
          line-height: 1.25;
        }

        :global(.shipping-history-page .facility-slot-badge) {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          background: #eef2f0;
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
          white-space: nowrap;
        }

        :global(.shipping-history-page .facility-slot-unmatched) {
          border-color: rgba(171, 125, 35, 0.28);
          background: #fffaf0;
        }

        :global(.shipping-history-page .shipping-unmatched-slot) {
          border-style: dashed;
        }

        .shipping-status-summary {
          margin-top: 0;
        }

        .shipping-number-list {
          display: grid;
          gap: 8px;
        }

        .shipping-tracking-card {
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-height: 0;
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
          box-shadow: 0 6px 12px rgba(27, 35, 33, 0.04);
        }

        .shipping-tracking-card-warning {
          border-color: rgba(171, 125, 35, 0.28);
          background: #fff8ef;
        }

        .shipping-tracking-card-danger {
          border-color: rgba(148, 47, 44, 0.22);
          background: #fff2f1;
        }

        .shipping-tracking-card-ok {
          border-color: rgba(56, 128, 80, 0.18);
          background: #f7fcf8;
        }

        .shipping-tracking-card-top {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
        }

        .shipping-tracking-card-number {
          margin: 0;
          font-size: 15px;
          font-weight: 700;
          line-height: 1.25;
          word-break: break-word;
        }

        .summary-badge {
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 4px 10px;
          border-radius: 999px;
          font-weight: 700;
          background: #eef2ef;
          color: #31423d;
          font-size: 12px;
        }

        .summary-badge-soft {
          background: #f3f6f4;
          font-weight: 600;
        }

        .status-pill {
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 4px 10px;
          border-radius: 999px;
          font-weight: 700;
          background: #edf1ef;
        }

        .tone-ok {
          background: #e2f3e4;
          color: #1d5f33;
        }

        .tone-warning {
          background: #fff0cf;
          color: #8a5b00;
        }

        .tone-danger {
          background: #ffe3e3;
          color: #8d2323;
        }

        .tone-neutral {
          background: #edf1ef;
          color: #3f5350;
        }

        .empty {
          margin: 0;
          color: #51615c;
        }

        @media (max-width: 760px) {
          .page {
            padding: 24px 16px 48px;
          }

          .panel-header,
          .week-group-header {
            flex-direction: column;
          }

          .field.narrow {
            max-width: none;
          }

          .week-group-header-actions {
            align-items: flex-start;
            width: 100%;
          }

          .week-counts {
            justify-content: flex-start;
          }

          .event-list li {
            grid-template-columns: 1fr;
            gap: 2px;
          }

          .shipping-tracking-card-top {
            flex-direction: column;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
