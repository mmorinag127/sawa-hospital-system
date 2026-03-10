export type BagRow = {
  id: string;
  date?: string | null;
  daypart?: string | null;
  menu_name?: string | null;
  diet_type?: string | null;
  area_id?: string | null;
  bag_type?: string | null;
  quantity?: number | null;
};

export type BagSummaryRow = {
  id: string;
  date: string;
  daypart: string;
  menu_name: string;
  diet_type: string;
  area_id: string;
  bag_type: string;
  total_quantity: number;
  bag_count: number;
  bag_quantities: number[];
};

const formatQuantity = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("ja-JP");
};

export const normalizeWeekId = (value?: string | null) => {
  const text = String(value || "").trim();
  const match = text.match(/^(\d{4})-(\d{2})$/);
  if (!match) return "";
  const month = Number(match[2]);
  if (!Number.isInteger(month) || month < 1 || month > 12) return "";
  return `${match[1]}-${match[2]}`;
};

export const normalizeWeekValue = (value?: string | null) => {
  const text = String(value || "").trim();
  const monthId = normalizeWeekId(text);
  if (monthId === text) return text;
  const match = text.match(/^(\d{4}-\d{2})@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
  if (!match) return "";
  const normalizedMonth = normalizeWeekId(match[1]);
  if (!normalizedMonth) return "";
  if (!match[2].startsWith(`${normalizedMonth}-`) || !match[3].startsWith(`${normalizedMonth}-`)) {
    return "";
  }
  return `${normalizedMonth}@${match[2]}~${match[3]}`;
};

export const extractWeekMonthId = (value?: string | null) => {
  const normalizedRange = normalizeWeekValue(value);
  if (!normalizedRange) return normalizeWeekId(value);
  const directMonth = normalizeWeekId(normalizedRange);
  if (directMonth) return directMonth;
  const match = normalizedRange.match(/^(\d{4}-\d{2})@/);
  return match ? match[1] : "";
};

export const formatWeekLabel = (value?: string | null, fallbackLabel?: string | null) => {
  const explicit = String(fallbackLabel || "").trim();
  if (explicit) return explicit;
  const normalizedRange = normalizeWeekValue(value);
  if (normalizedRange) {
    const match = normalizedRange.match(/^(\d{4}-\d{2})@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$/);
    if (match) {
      return `${match[1]} (${match[2].slice(5).replace("-", "/")}-${match[3].slice(5).replace("-", "/")})`;
    }
    return normalizedRange;
  }
  return normalizeWeekId(value) || "";
};

export const normalizeBagGroupToken = (value: unknown, fallback = "") =>
  String(value ?? "")
    .replace(/\s+/g, " ")
    .trim() || fallback;

export const buildBagSummaryRows = (rows: BagRow[]) => {
  const map = new Map<string, BagSummaryRow>();
  rows.forEach((row) => {
    const date = normalizeBagGroupToken(row.date, "-");
    const daypart = normalizeBagGroupToken(row.daypart, "-");
    const menu_name = normalizeBagGroupToken(row.menu_name, "-");
    const diet_type = normalizeBagGroupToken(row.diet_type);
    const area_id = normalizeBagGroupToken(row.area_id);
    const bag_type = normalizeBagGroupToken(row.bag_type);
    const key = [date, daypart, menu_name, diet_type, area_id, bag_type].join("__");
    const existing =
      map.get(key) || {
        id: key,
        date,
        daypart,
        menu_name,
        diet_type,
        area_id,
        bag_type,
        total_quantity: 0,
        bag_count: 0,
        bag_quantities: [],
      };
    const quantity = Number(row.quantity);
    if (Number.isFinite(quantity)) {
      existing.total_quantity += quantity;
      existing.bag_quantities.push(quantity);
    }
    existing.bag_count += 1;
    map.set(key, existing);
  });
  return Array.from(map.values()).sort((left, right) => {
    return (
      left.date.localeCompare(right.date, "ja") ||
      left.daypart.localeCompare(right.daypart, "ja") ||
      left.menu_name.localeCompare(right.menu_name, "ja") ||
      left.diet_type.localeCompare(right.diet_type, "ja") ||
      left.area_id.localeCompare(right.area_id, "ja") ||
      left.bag_type.localeCompare(right.bag_type, "ja")
    );
  });
};

export const groupBagSummaryRowsByDate = (rows: BagSummaryRow[]) => {
  const map = new Map<string, BagSummaryRow[]>();
  rows.forEach((row) => {
    const group = map.get(row.date) || [];
    group.push(row);
    map.set(row.date, group);
  });
  return Array.from(map.entries())
    .sort(([dateA], [dateB]) => dateA.localeCompare(dateB, "ja"))
    .map(([date, group]) => ({ date, rows: group }));
};

export const formatBagSplitBreakdown = (row: BagSummaryRow) => {
  if (row.bag_count <= 1) return "-";
  const quantities = [...row.bag_quantities]
    .filter((value) => Number.isFinite(value))
    .sort((left, right) => right - left);
  if (!quantities.length) return `${row.bag_count}袋`;
  return quantities.map((value) => formatQuantity(value)).join(" + ");
};

export const formatBagCalculationResult = (row: BagSummaryRow) => {
  const breakdown = formatBagSplitBreakdown(row);
  if (breakdown === "-" || !breakdown) return "";
  return breakdown;
};
