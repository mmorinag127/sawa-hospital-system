export type ShippingLatestView = "active" | "all" | "attention" | "recent";

export type ShippingHistorySummary = {
  total: number;
  delivered: number;
  pending: number;
  errors: number;
  all_delivered?: boolean;
  facility_missing?: number;
  attention?: number;
};

export type QuotaStatus = {
  resource?: string;
  unit?: string;
  used?: number;
  limit?: number;
  ratio?: number | null;
  alert_level?: "ok" | "warning" | "critical" | "unknown" | string;
  message?: string;
};

export type ShippingLatestItem = {
  id?: string;
  tracking_key: string;
  tracking_number: string;
  events?: ShippingHistoryEvent[];
  ship_date?: string | null;
  facility_name?: string | null;
  facility_name_source?: string | null;
  status: string;
  delivered: boolean;
  arrival_text?: string | null;
  error?: string | null;
  source?: string | null;
  looked_up_at?: string | null;
  attention_reasons?: string[];
};

export type ShippingHistoryEvent = {
  status: string;
  occurred_at?: string | null;
  facility_name?: string | null;
  facility?: string | null;
  note?: string | null;
};

export type ShippingFacilityGroup = {
  ship_date?: string | null;
  facility_name?: string | null;
  facility_name_source?: string | null;
  item_count: number;
  pending_count: number;
  delivered_count: number;
  latest_looked_up_at?: string | null;
  items: ShippingLatestItem[];
};

export type ShippingDateGroup = {
  ship_date?: string | null;
  item_count: number;
  pending_count: number;
  delivered_count: number;
  latest_looked_up_at?: string | null;
  facilities: ShippingFacilityGroup[];
};

export type ShippingLatestResponse = {
  generated_at?: string | null;
  timezone?: string | null;
  view: ShippingLatestView;
  base_date?: string | null;
  window_days: number;
  summary: ShippingHistorySummary;
  date_groups: ShippingDateGroup[];
  quota: QuotaStatus | null;
  last_scheduled_refresh_at?: string | null;
  next_scheduled_refresh_at?: string | null;
};

const ATTENTION_REASON_LABELS: Record<string, string> = {
  error: "照会失敗",
  failed: "照会失敗",
  lookup_error: "照会失敗",
  not_found: "該当なし",
  no_match: "該当なし",
  status_not_found: "該当なし",
  facility_missing: "施設未設定",
  missing_facility: "施設未設定",
  stale: "停滞",
  stagnant: "停滞",
  delayed: "停滞",
};

const normalizeText = (value: unknown, fallback = "") => {
  const text = String(value ?? "").trim();
  return text || fallback;
};

const normalizeOptionalText = (value: unknown) => {
  const text = String(value ?? "").trim();
  return text || null;
};

const normalizeEvent = (raw: any): ShippingHistoryEvent => {
  const status = normalizeText(
    raw?.status || raw?.event_status || raw?.state || raw?.label || raw?.event || raw?.memo || "",
    "-",
  );
  const occurredAt = normalizeOptionalText(
    raw?.occurred_at ||
      raw?.event_at ||
      raw?.datetime ||
      raw?.timestamp ||
      raw?.time ||
      raw?.date ||
      raw?.time_text ||
      raw?.event_at_text,
  );
  return {
    status,
    occurred_at: occurredAt,
    facility_name: normalizeOptionalText(
      raw?.facility_name || raw?.office_name || raw?.facility || raw?.office || raw?.station,
    ),
    facility: normalizeOptionalText(raw?.facility || raw?.station || raw?.office),
    note: normalizeOptionalText(raw?.note || raw?.description || raw?.memo),
  };
};

const normalizeEvents = (value: unknown): ShippingHistoryEvent[] => {
  if (!Array.isArray(value)) return [];
  const events = value.map((raw) => normalizeEvent(raw)).filter((event) => event.status);
  return events.filter(
    (event, index, all) =>
      all.findIndex(
        (item) =>
          item.status === event.status &&
          item.occurred_at === event.occurred_at &&
          item.facility_name === event.facility_name &&
          item.facility === event.facility,
      ) === index,
  );
};

const normalizeBoolean = (value: unknown) => {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    return lowered === "true" || lowered === "1" || lowered === "yes";
  }
  return false;
};

const normalizeNumber = (value: unknown, fallback = 0) => {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
};

const isLatestView = (value: unknown): value is ShippingLatestView =>
  value === "active" || value === "all" || value === "attention" || value === "recent";

const compareDateValueAsc = (left?: string | null, right?: string | null) => {
  if (left && right) return left.localeCompare(right, "ja");
  if (left) return -1;
  if (right) return 1;
  return 0;
};

const compareTextAsc = (left?: string | null, right?: string | null) => {
  const leftText = normalizeText(left, "未設定");
  const rightText = normalizeText(right, "未設定");
  return leftText.localeCompare(rightText, "ja");
};

const compareLookedUpAtDesc = (left?: string | null, right?: string | null) => {
  if (left && right) return right.localeCompare(left, "ja");
  if (left) return -1;
  if (right) return 1;
  return 0;
};

const normalizeAttentionReasons = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => normalizeText(item))
    .filter(Boolean)
    .map((item) => ATTENTION_REASON_LABELS[item] || item)
    .filter((item, index, items) => items.indexOf(item) === index);
};

const normalizeLatestItem = (
  raw: any,
  defaults: Partial<Pick<ShippingLatestItem, "ship_date" | "facility_name" | "facility_name_source">> = {},
): ShippingLatestItem => {
  const trackingKey = normalizeText(raw?.tracking_key ?? raw?.tracking_number);
  const trackingNumber = normalizeText(raw?.tracking_number ?? raw?.tracking_key);
  return {
    id: normalizeOptionalText(raw?.id) || undefined,
    tracking_key: trackingKey || trackingNumber,
    tracking_number: trackingNumber || trackingKey,
    ship_date: normalizeOptionalText(raw?.ship_date ?? defaults.ship_date),
    facility_name: normalizeOptionalText(raw?.facility_name ?? defaults.facility_name),
    facility_name_source: normalizeOptionalText(
      raw?.facility_name_source ?? defaults.facility_name_source,
    ),
    status: normalizeText(raw?.status, "不明"),
    delivered: normalizeBoolean(raw?.delivered),
    arrival_text: normalizeOptionalText(raw?.arrival_text),
    error: normalizeOptionalText(raw?.error),
    source: normalizeOptionalText(raw?.source),
    looked_up_at: normalizeOptionalText(raw?.looked_up_at),
    events: normalizeEvents(raw?.events),
    attention_reasons: normalizeAttentionReasons(raw?.attention_reasons),
  };
};

const describeAttentionReasonsFromShape = (item: ShippingLatestItem, staleHours = 24) => {
  const reasons = [...(item.attention_reasons || [])];
  if (item.error) reasons.push("照会失敗");
  if (item.status.includes("該当なし")) reasons.push("該当なし");
  if (!item.facility_name) reasons.push("施設未設定");
  if (!item.delivered && item.looked_up_at) {
    const lookedUpAt = new Date(item.looked_up_at);
    if (!Number.isNaN(lookedUpAt.getTime())) {
      const hours = (Date.now() - lookedUpAt.getTime()) / (1000 * 60 * 60);
      if (hours >= staleHours) reasons.push("停滞");
    }
  }
  return reasons.filter((reason, index, items) => items.indexOf(reason) === index);
};

const summarizeItems = (items: ShippingLatestItem[]): ShippingHistorySummary => {
  let delivered = 0;
  let errors = 0;
  let facilityMissing = 0;
  let attention = 0;
  items.forEach((item) => {
    if (item.delivered) delivered += 1;
    if (item.error) errors += 1;
    if (!item.facility_name) facilityMissing += 1;
    if (describeAttentionReasonsFromShape(item).length > 0) attention += 1;
  });
  const total = items.length;
  const pending = Math.max(total - delivered, 0);
  return {
    total,
    delivered,
    pending,
    errors,
    all_delivered: total > 0 && pending === 0,
    facility_missing: facilityMissing,
    attention,
  };
};

const buildFacilityGroup = (
  items: ShippingLatestItem[],
  rawGroup?: any,
  defaults: Partial<Pick<ShippingLatestItem, "ship_date" | "facility_name" | "facility_name_source">> = {},
): ShippingFacilityGroup => {
  const normalizedItems = items
    .map((item) => normalizeLatestItem(item, defaults))
    .filter((item) => Boolean(item.tracking_key || item.tracking_number))
    .sort((left, right) => compareLookedUpAtDesc(left.looked_up_at, right.looked_up_at));
  const summary = summarizeItems(normalizedItems);
  const latestLookedUpAt = normalizedItems.reduce<string | null>((latest, item) => {
    if (!item.looked_up_at) return latest;
    return compareLookedUpAtDesc(item.looked_up_at, latest) < 0 ? item.looked_up_at : latest;
  }, null);
  return {
    ship_date: normalizeOptionalText(rawGroup?.ship_date ?? defaults.ship_date),
    facility_name: normalizeOptionalText(rawGroup?.facility_name ?? defaults.facility_name),
    facility_name_source: normalizeOptionalText(
      rawGroup?.facility_name_source ?? defaults.facility_name_source,
    ),
    item_count: normalizeNumber(rawGroup?.item_count, summary.total),
    pending_count: normalizeNumber(rawGroup?.pending_count, summary.pending),
    delivered_count: normalizeNumber(rawGroup?.delivered_count, summary.delivered),
    latest_looked_up_at:
      normalizeOptionalText(rawGroup?.latest_looked_up_at) || latestLookedUpAt || null,
    items: normalizedItems,
  };
};

const groupFlatItems = (items: any[]) => {
  const grouped = new Map<string, { shipDate: string | null; facilityName: string | null; items: ShippingLatestItem[] }>();
  items.forEach((rawItem) => {
    const item = normalizeLatestItem(rawItem);
    if (!item.tracking_key && !item.tracking_number) return;
    const key = `${item.ship_date || ""}__${item.facility_name || ""}`;
    const bucket = grouped.get(key) || {
      shipDate: item.ship_date || null,
      facilityName: item.facility_name || null,
      items: [],
    };
    bucket.items.push(item);
    grouped.set(key, bucket);
  });
  return Array.from(grouped.values()).map((group) =>
    buildFacilityGroup(group.items, null, {
      ship_date: group.shipDate,
      facility_name: group.facilityName,
      facility_name_source: null,
    }),
  );
};

const buildDateGroups = (facilityGroups: ShippingFacilityGroup[]) => {
  const grouped = new Map<string, ShippingFacilityGroup[]>();
  facilityGroups.forEach((group) => {
    const shipDate = group.ship_date || "";
    const bucket = grouped.get(shipDate) || [];
    bucket.push(group);
    grouped.set(shipDate, bucket);
  });
  return Array.from(grouped.entries())
    .map(([shipDate, facilities]) => {
      const itemCount = facilities.reduce((total, item) => total + item.item_count, 0);
      const pendingCount = facilities.reduce((total, item) => total + item.pending_count, 0);
      const deliveredCount = facilities.reduce((total, item) => total + item.delivered_count, 0);
      const latestLookedUpAt = facilities.reduce<string | null>((latest, item) => {
        if (!item.latest_looked_up_at) return latest;
        return compareLookedUpAtDesc(item.latest_looked_up_at, latest) < 0
          ? item.latest_looked_up_at
          : latest;
      }, null);
      return {
        ship_date: shipDate || null,
        item_count: itemCount,
        pending_count: pendingCount,
        delivered_count: deliveredCount,
        latest_looked_up_at: latestLookedUpAt,
        facilities: [...facilities].sort((left, right) => {
          return (
            compareTextAsc(left.facility_name, right.facility_name) ||
            compareLookedUpAtDesc(left.latest_looked_up_at, right.latest_looked_up_at)
          );
        }),
      } satisfies ShippingDateGroup;
    })
    .sort((left, right) => compareDateValueAsc(left.ship_date, right.ship_date));
};

const normalizeQuota = (value: any): QuotaStatus | null => {
  if (!value || typeof value !== "object") return null;
  return {
    resource: normalizeOptionalText(value.resource) || undefined,
    unit: normalizeOptionalText(value.unit) || undefined,
    used: Number.isFinite(Number(value.used)) ? Number(value.used) : undefined,
    limit: Number.isFinite(Number(value.limit)) ? Number(value.limit) : undefined,
    ratio: value.ratio == null || value.ratio === "" ? null : Number(value.ratio),
    alert_level: normalizeOptionalText(value.alert_level) || undefined,
    message: normalizeOptionalText(value.message) || undefined,
  };
};

export const normalizeLatestResponse = (
  payload: any,
  defaults: {
    view: ShippingLatestView;
    base_date?: string | null;
    window_days?: number;
  },
): ShippingLatestResponse => {
  const rawGroups = Array.isArray(payload?.groups) ? payload.groups : [];
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];
  const facilityGroups = (rawGroups.length
    ? rawGroups.map((group) =>
        buildFacilityGroup(Array.isArray(group?.items) ? group.items : [], group, {
          ship_date: normalizeOptionalText(group?.ship_date),
          facility_name: normalizeOptionalText(group?.facility_name),
          facility_name_source: normalizeOptionalText(group?.facility_name_source),
        }),
      )
    : groupFlatItems(rawItems)
  )
    .filter((group) => group.items.length > 0)
    .sort((left, right) => {
      return (
        compareDateValueAsc(left.ship_date, right.ship_date) ||
        compareTextAsc(left.facility_name, right.facility_name) ||
        compareLookedUpAtDesc(left.latest_looked_up_at, right.latest_looked_up_at)
      );
    });

  const items = facilityGroups.flatMap((group) => group.items);
  const summary = summarizeItems(items);

  return {
    generated_at: normalizeOptionalText(payload?.generated_at),
    timezone: normalizeOptionalText(payload?.timezone),
    view: isLatestView(payload?.view) ? payload.view : defaults.view,
    base_date: normalizeOptionalText(payload?.base_date ?? defaults.base_date),
    window_days: Math.max(0, normalizeNumber(payload?.window_days, defaults.window_days ?? 14)),
    summary: {
      total: normalizeNumber(payload?.summary?.total, summary.total),
      delivered: normalizeNumber(payload?.summary?.delivered, summary.delivered),
      pending: normalizeNumber(payload?.summary?.pending, summary.pending),
      errors: normalizeNumber(payload?.summary?.errors, summary.errors),
      all_delivered:
        typeof payload?.summary?.all_delivered === "boolean"
          ? payload.summary.all_delivered
          : summary.all_delivered,
      facility_missing: normalizeNumber(payload?.summary?.facility_missing, summary.facility_missing),
      attention: normalizeNumber(payload?.summary?.attention, summary.attention),
    },
    date_groups: buildDateGroups(facilityGroups),
    quota: normalizeQuota(payload?.quota),
    last_scheduled_refresh_at: normalizeOptionalText(payload?.last_scheduled_refresh_at),
    next_scheduled_refresh_at: normalizeOptionalText(payload?.next_scheduled_refresh_at),
  };
};

export const describeAttentionReasons = (item: ShippingLatestItem, staleHours = 24) =>
  describeAttentionReasonsFromShape(item, staleHours);

export const normalizeTrackingSearchToken = (value: unknown) =>
  String(value ?? "")
    .trim()
    .replace(/[^0-9A-Za-z]/g, "")
    .toLowerCase();
