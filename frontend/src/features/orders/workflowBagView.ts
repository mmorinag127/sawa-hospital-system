

export const normalizeDietLabel = (value?: unknown) => {
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

export const formatWorkflowQuantity = (value?: unknown) => {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString("ja-JP", { maximumFractionDigits: 2 });
};

export const formatWorkflowBagType = (value?: unknown) => {
  const raw = String(value || "").trim();
  if (!raw) return "標準";
  if (raw === "standard") return "標準";
  if (raw === "condiment") return "付属品";
  return raw;
};

export const extractWorkflowBagRows = (baggingResult?: Record<string, unknown> | null) => {
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

export const buildWorkflowBagSummaryRows = (bagRows: ReturnType<typeof extractWorkflowBagRows>) => {
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

export const groupWorkflowBagRowsByDate = (rows: ReturnType<typeof buildWorkflowBagSummaryRows>) => {
  const map = new Map<string, typeof rows>();
  rows.forEach((row) => {
    const key = row.date || "-";
    const current = map.get(key) || [];
    current.push(row);
    map.set(key, current);
  });
  return Array.from(map.entries()).map(([date, rows]) => ({ date, rows }));
};

export const isBagColumnHeader = (header?: string) => {
  const normalized = String(header || "").trim().toLowerCase();
  return Boolean(normalized && (normalized.includes("bag") || normalized.includes("袋")));
};

export const formatOutputPreviewCell = (cell: string, header?: string) => {
  if (!isBagColumnHeader(header)) return cell;
  return formatWorkflowBagType(cell);
};
