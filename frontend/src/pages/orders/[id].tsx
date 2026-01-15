import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";

type OrderDetail = {
  id: string;
  status: string;
  document: string;
  week?: string | null;
  message_id?: string | null;
  lines: {
    id?: string;
    line_id?: string;
    date?: string | null;
    daypart?: string | null;
    menu_name?: string | null;
    diet_type?: string | null;
    area_id?: string | null;
    bag_type?: string | null;
    quantity_original?: number | null;
    quantity_corrected?: number | null;
    change_note?: string | null;
  }[];
  facility?: string | null;
  ocr_status?: string | null;
  ocr_error?: string | null;
  ocr_metrics?: { failed_cells?: number } | null;
  ocr_prompt_enabled?: boolean | null;
  ocr_updated_at?: string | null;
};

type OcrOutput = {
  status?: string;
  stage?: string;
  template_id?: string | null;
  failed_cells?: { row?: string; col?: string; reason?: string }[];
  warnings?: string[];
  table_raw?: string;
  facility_candidates?: FacilityCandidate[];
};

type OcrPage = {
  page_index?: number | null;
  markdown_text?: string | null;
  ocr_overlay_url?: string | null;
  layout_overlay_url?: string | null;
  figure_urls?: string[];
};

type BagRow = {
  id: string;
  date?: string | null;
  daypart?: string | null;
  menu_name?: string | null;
  diet_type?: string | null;
  area_id?: string | null;
  bag_type?: string | null;
  quantity?: number | null;
};

type OutputPreview = {
  type: "labels" | "delivery" | "aggregate";
  headers: string[];
  rows: string[][];
};

type FacilityOption = {
  id: string;
  name: string;
};

type FacilityCandidate = {
  facility_id: string;
  facility_name?: string | null;
  score?: number | null;
  reason?: string | null;
  auto?: boolean | null;
};

const toNumber = (value?: number | null) => (value == null || Number.isNaN(value) ? 0 : Number(value));

const formatDietType = (value?: string | null) => {
  if (!value) return "不明";
  const normalized = value.toLowerCase();
  if (normalized === "regular") return "常食";
  if (normalized === "daycare") return "通所";
  if (normalized === "staff") return "職員";
  if (normalized === "no_meat") return "禁食(肉禁)";
  if (normalized === "no_fish") return "禁食(魚禁)";
  return value;
};

const dietTypeLabels: Record<string, string> = {
  regular: "常食",
  daycare: "通所",
  staff: "職員",
  soft: "軟菜",
  mixer: "ミキサー",
  no_meat: "禁食(肉禁)",
  no_fish: "禁食(魚禁)",
  change_1: "変更1",
  change_2: "変更2",
  unknown: "不明",
};

const preferredDietOrder = ["regular", "daycare", "staff", "no_meat", "no_fish", "change_1", "change_2", "unknown"];

const formatTimestamp = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

const formatQuantity = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toLocaleString("ja-JP");
};
const DEFAULT_OCR_PROMPT =
  "Return a JSON object only.\\n" +
  'Schema: {"rows":[[date, menu_name, regular_2f, regular_3f, soft_2f, soft_3f, mixer_2f, mixer_3f, note], ...], "errors":[{"row":0,"col":0,"reason":"unreadable"}]}\\n' +
  "Do not output header rows or date-only headers. Output menu rows only.\\n" +
  'If no menu rows are readable, return {"rows":[], "errors":[{"row":0,"col":0,"reason":"unreadable"}]}.\\n' +
  "Use null when a cell is unreadable or missing. Use ASCII digits 0-9 only in numeric cells.";

const makeCategoryKey = (dietType?: string | null, areaId?: string | null) => {
  const diet = (dietType || "").toLowerCase() || "unknown";
  const area = areaId || "";
  if (!area || diet === "unknown") {
    return diet;
  }
  return `${diet}__${area}`;
};

const formatCategoryLabel = (key: string) => {
  if (!key.includes("__")) {
    return dietTypeLabels[key] || key;
  }
  const [diet, area] = key.split("__");
  const dietLabel = dietTypeLabels[diet] || diet;
  return `${dietLabel}${area}`;
};

const buildCategoryColumns = (lines: OrderDetail["lines"]) => {
  const seen = new Set<string>();
  lines.forEach((line) => {
    seen.add(makeCategoryKey(line.diet_type, line.area_id));
  });
  const columns: { key: string; label: string }[] = [];
  preferredDietOrder.forEach((diet) => {
    const withArea = Array.from(seen)
      .filter((key) => key === diet || key.startsWith(`${diet}__`))
      .sort();
    withArea.forEach((key) => columns.push({ key, label: formatCategoryLabel(key) }));
  });
  const extras = Array.from(seen)
    .filter((key) => !preferredDietOrder.some((diet) => key === diet || key.startsWith(`${diet}__`)))
    .sort();
  extras.forEach((key) => columns.push({ key, label: formatCategoryLabel(key) }));
  return columns;
};

type PivotRow = {
  date: string;
  menu_name: string;
  daypart: string;
  area_id: string;
  bag_type: string;
  totals: Record<string, number>;
  notes: Set<string>;
};

type PivotCategoryRow = {
  date: string;
  categoryKey: string;
  categoryLabel: string;
  menu_name: string;
  daypart: string;
  bag_type: string;
  quantity: number;
  notes: Set<string>;
};

const buildPivotRows = (lines: OrderDetail["lines"]): PivotRow[] => {
  const map = new Map<string, PivotRow>();
  lines.forEach((line) => {
    const dietKey = makeCategoryKey(line.diet_type, line.area_id);
    const qty =
      line.quantity_corrected == null ? toNumber(line.quantity_original) : toNumber(line.quantity_corrected);
    const date = line.date || "-";
    const menu = line.menu_name || "不明";
    const daypart = line.daypart || "-";
    const area = line.area_id || "-";
    const bag = line.bag_type || "-";
    const key = `${date}__${menu}__${daypart}__${area}__${bag}`;
    const current =
      map.get(key) || {
        date,
        menu_name: menu,
        daypart,
        area_id: area,
        bag_type: bag,
        totals: {},
        notes: new Set<string>(),
      };
    current.totals[dietKey] = (current.totals[dietKey] || 0) + qty;
    if (line.change_note) {
      current.notes.add(line.change_note);
    }
    map.set(key, current);
  });
  return Array.from(map.values());
};

const buildPivotCategoryRows = (rows: PivotRow[]): PivotCategoryRow[] => {
  const result: PivotCategoryRow[] = [];
  rows.forEach((row) => {
    Object.entries(row.totals).forEach(([categoryKey, quantity]) => {
      if (!quantity) return;
      result.push({
        date: row.date,
        categoryKey,
        categoryLabel: formatCategoryLabel(categoryKey),
        menu_name: row.menu_name,
        daypart: row.daypart,
        bag_type: row.bag_type,
        quantity,
        notes: row.notes,
      });
    });
  });
  return result;
};

const groupByDateAndCategory = <
  T extends { date?: string | null; categoryKey?: string | null; categoryLabel?: string | null },
>(
  rows: T[],
  categoryOrder: string[],
) => {
  const map = new Map<string, { date: string; categoryKey: string; categoryLabel: string; rows: T[] }>();
  rows.forEach((row) => {
    const date = row.date || "-";
    const categoryKey = row.categoryKey || "unknown";
    const categoryLabel = row.categoryLabel || formatCategoryLabel(categoryKey);
    const groupKey = `${date}__${categoryKey}`;
    const group =
      map.get(groupKey) || { date, categoryKey, categoryLabel, rows: [] as T[] };
    group.rows.push(row);
    map.set(groupKey, group);
  });
  const orderIndex = new Map(categoryOrder.map((key, idx) => [key, idx]));
  return Array.from(map.values()).sort((a, b) => {
    const dateCompare = a.date.localeCompare(b.date, "ja");
    if (dateCompare !== 0) return dateCompare;
    const aRank = orderIndex.get(a.categoryKey) ?? Number.MAX_SAFE_INTEGER;
    const bRank = orderIndex.get(b.categoryKey) ?? Number.MAX_SAFE_INTEGER;
    if (aRank !== bRank) return aRank - bRank;
    return a.categoryLabel.localeCompare(b.categoryLabel, "ja");
  });
};

const groupBagsByDate = (rows: BagRow[]) => {
  const map = new Map<string, BagRow[]>();
  rows.forEach((row) => {
    const date = row.date || "-";
    const group = map.get(date) || [];
    group.push(row);
    map.set(date, group);
  });
  return Array.from(map.entries())
    .sort(([dateA], [dateB]) => dateA.localeCompare(dateB, "ja"))
    .map(([date, group]) => ({ date, rows: group }));
};

type MarkdownBlock =
  | { type: "table"; header: string[]; rows: string[][] }
  | { type: "text"; lines: string[] };

const splitLines = (text: string) => text.split(/\r\n|\n|\r/);

const parseMarkdownBlocks = (markdown: string): MarkdownBlock[] => {
  const lines = splitLines(markdown);
  const blocks: MarkdownBlock[] = [];
  let buffer: string[] = [];
  let tableBuffer: string[] = [];

  const flushText = () => {
    if (!buffer.length) return;
    blocks.push({ type: "text", lines: buffer });
    buffer = [];
  };

  const flushTable = () => {
    if (!tableBuffer.length) return;
    const rows = tableBuffer.map((line) =>
      line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim()),
    );
    let header: string[] = [];
    let dataRows = rows;
    if (rows.length >= 2 && rows[1].every((cell) => /^:?-+:?$/.test(cell))) {
      header = rows[0];
      dataRows = rows.slice(2);
    }
    blocks.push({ type: "table", header, rows: dataRows });
    tableBuffer = [];
  };

  lines.forEach((line) => {
    if (line.trim().startsWith("|")) {
      flushText();
      tableBuffer.push(line);
    } else {
      flushTable();
      if (line.trim()) {
        buffer.push(line);
      }
    }
  });
  flushTable();
  flushText();
  return blocks;
};

const extractTableFromPage = (page?: OcrPage | null) => {
  if (!page?.markdown_text) return null;
  const blocks = parseMarkdownBlocks(page.markdown_text);
  const table = blocks.find(
    (block) => block.type === "table" && block.rows.length > 0,
  ) as MarkdownBlock | undefined;
  if (table && table.type === "table") {
    return { header: table.header, rows: table.rows };
  }
  return null;
};

const extractFirstTable = (pages: OcrPage[]) => {
  for (let index = 0; index < pages.length; index += 1) {
    const page = pages[index];
    const table = extractTableFromPage(page);
    if (table) {
      return {
        pageArrayIndex: index,
        pageIndex: page.page_index ?? index + 1,
        header: table.header,
        rows: table.rows,
      };
    }
  }
  return null;
};

const buildMarkdownTable = (header: string[], rows: string[][]) => {
  const safeHeader = header.length ? header : rows[0]?.map((_, idx) => `col${idx + 1}`) || [];
  const headerLine = `| ${safeHeader.join(" | ")} |`;
  const separatorLine = `| ${safeHeader.map(() => "---").join(" | ")} |`;
  const bodyLines = rows.map((row) => `| ${row.join(" | ")} |`);
  return [headerLine, separatorLine, ...bodyLines].join("\n");
};

const getColumnCount = (header: string[], rows: string[][]) => {
  const rowMax = rows.reduce((max, row) => Math.max(max, row.length), 0);
  const base = Math.max(header.length, rowMax);
  return base > 0 ? base : 1;
};

const renderMarkdownLine = (line: string, key: string) => {
  const imageMatch = line.match(/!\[[^\]]*]\(([^)]+)\)/);
  if (imageMatch?.[1]) {
    return <img key={key} src={imageMatch[1]} alt="figure" className="markdown-image" />;
  }
  const headingMatch = line.match(/^(#{1,4})\s+(.*)$/);
  if (headingMatch) {
    const level = headingMatch[1].length;
    const Tag = (`h${Math.min(level + 2, 6)}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6") || "h4";
    return <Tag key={key}>{headingMatch[2]}</Tag>;
  }
  return (
    <p key={key} className="markdown-line">
      {line}
    </p>
  );
};

const formatFacilityLabel = (facility: FacilityOption) => {
  if (!facility.name) return facility.id;
  return `${facility.name} (${facility.id})`;
};

const formatFacilityReason = (reason?: string | null) => {
  if (!reason) return "";
  if (reason === "name_exact") return "名称一致";
  if (reason === "phone") return "電話一致";
  if (reason === "fuzzy") return "類似";
  return reason;
};

export default function OrderDetailPage() {
  const router = useRouter();
  const { id } = router.query;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [facility, setFacility] = useState<string>("");
  const [facilityOptions, setFacilityOptions] = useState<FacilityOption[]>([]);
  const [facilityOptionsLoading, setFacilityOptionsLoading] = useState(false);
  const [facilityOptionsError, setFacilityOptionsError] = useState("");
  const [actionMessage, setActionMessage] = useState<string>("");
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [pdfError, setPdfError] = useState<string>("");
  const [ocrPrompt, setOcrPrompt] = useState<string>(DEFAULT_OCR_PROMPT);
  const [ocrRawText, setOcrRawText] = useState<string>("");
  const [ocrRawMessage, setOcrRawMessage] = useState<string>("");
  const [ocrRawLoading, setOcrRawLoading] = useState<boolean>(false);
  const [ocrOutput, setOcrOutput] = useState<OcrOutput | null>(null);
  const [ocrOutputMessage, setOcrOutputMessage] = useState<string>("");
  const [ocrPages, setOcrPages] = useState<OcrPage[]>([]);
  const [ocrPagesMessage, setOcrPagesMessage] = useState<string>("");
  const [ocrPagesLoading, setOcrPagesLoading] = useState<boolean>(false);
  const [activeOcrPageIndex, setActiveOcrPageIndex] = useState<number>(0);
  const [ocrTableHeader, setOcrTableHeader] = useState<string[]>([]);
  const [ocrTableRows, setOcrTableRows] = useState<string[][]>([]);
  const [ocrTablePageIndex, setOcrTablePageIndex] = useState<number | null>(null);
  const [ocrTableMessage, setOcrTableMessage] = useState<string>("");
  const [ocrTableSaving, setOcrTableSaving] = useState<boolean>(false);
  const [showOcrEdit, setShowOcrEdit] = useState<boolean>(false);
  const [reparsePending, setReparsePending] = useState<boolean>(false);
  const [bagRows, setBagRows] = useState<BagRow[]>([]);
  const [bagMessage, setBagMessage] = useState<string>("");
  const [bagLoading, setBagLoading] = useState<boolean>(false);
  const [outputPreview, setOutputPreview] = useState<OutputPreview | null>(null);
  const [outputPreviewMessage, setOutputPreviewMessage] = useState<string>("");
  const [outputPreviewLoading, setOutputPreviewLoading] = useState<boolean>(false);
  const [showMarkdownRaw, setShowMarkdownRaw] = useState<boolean>(false);
  const reparseTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!id) return;
    apiClient.get(`/orders/${id}`).then((res) => {
      setOrder(res.data);
      setFacility(res.data.facility || "");
    });
    setOcrPrompt(DEFAULT_OCR_PROMPT);
  }, [id]);

  useEffect(() => {
    setOcrPages([]);
    setOcrPagesMessage("");
    setOcrTableHeader([]);
    setOcrTableRows([]);
    setOcrTablePageIndex(null);
    setOcrTableMessage("");
    setActiveOcrPageIndex(0);
    setShowOcrEdit(false);
  }, [id]);

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
          .map((item) => ({
            id: String(item?.id || ""),
            name: String(item?.name || ""),
          }))
          .filter((item) => item.id);
        normalized.sort(
          (a, b) => a.name.localeCompare(b.name, "ja") || a.id.localeCompare(b.id, "ja"),
        );
        setFacilityOptions(normalized);
      } catch (err) {
        if (!active) return;
        setFacilityOptions([]);
        setFacilityOptionsError("施設一覧の取得に失敗しました。");
      } finally {
        if (active) {
          setFacilityOptionsLoading(false);
        }
      }
    };
    loadFacilities();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (reparseTimerRef.current !== null) {
        window.clearTimeout(reparseTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!order?.id) {
      setPdfUrl("");
      return;
    }
    let active = true;
    let objectUrl = "";
    setPdfError("");
    apiClient
      .get(`/orders/${order.id}/document`, { responseType: "blob" })
      .then((res) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(res.data);
        setPdfUrl(objectUrl);
      })
      .catch(() => {
        if (!active) return;
        setPdfError("PDFの取得に失敗しました。");
        setPdfUrl("");
      });
    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [order?.id]);

  const refreshOcrOutput = async (orderId: string) => {
    setOcrOutputMessage("OCR結果を取得中...");
    try {
      const res = await apiClient.get(`/orders/${orderId}/ocr-output`);
      if (res.status === 202 || res.data?.pending) {
        setOcrOutput(null);
        setOcrOutputMessage("OCR結果は処理中です。");
        return;
      }
      setOcrOutput(res.data);
      setOcrOutputMessage("");
    } catch (err: any) {
      if (err?.response?.status === 404) {
        setOcrOutputMessage("OCR結果はまだありません。");
      } else {
        setOcrOutputMessage("OCR結果の取得に失敗しました。");
      }
      setOcrOutput(null);
    }
  };

  useEffect(() => {
    if (!order?.id) return;
    refreshOcrOutput(order.id);
  }, [order?.id]);

  const loadOcrRaw = async () => {
    if (!order) return;
    setOcrRawLoading(true);
    setOcrRawMessage("生OCRを取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/ocr-raw`);
      const rawText = typeof res.data?.raw_text === "string" ? res.data.raw_text : "";
      setOcrRawText(rawText);
      setOcrRawMessage(rawText ? "生OCRを取得しました。" : "生OCRが空です。");
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setOcrRawMessage("生OCRが見つかりません。再解析後に取得してください。");
      } else {
        setOcrRawMessage("生OCRの取得に失敗しました。");
      }
    } finally {
      setOcrRawLoading(false);
    }
  };

  const setTableFromPage = (page: OcrPage | undefined, fallbackIndex?: number) => {
    if (!page) {
      setOcrTableHeader([]);
      setOcrTableRows([]);
      setOcrTablePageIndex(null);
      setOcrTableMessage("OCRページが未取得です。");
      return;
    }
    const table = extractTableFromPage(page);
    const pageIndex = page.page_index ?? (fallbackIndex != null ? fallbackIndex + 1 : null);
    setOcrTablePageIndex(pageIndex);
    if (table) {
      setOcrTableHeader(table.header);
      setOcrTableRows(table.rows.map((row) => [...row]));
      setOcrTableMessage("");
    } else {
      setOcrTableHeader([]);
      setOcrTableRows([]);
      setOcrTableMessage("編集できる表が見つかりません。");
    }
  };

  const loadOcrPages = async () => {
    if (!order) return;
    setOcrPagesLoading(true);
    setOcrPagesMessage("OCRページを取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/ocr-pages`);
      if (res.status === 202 || res.data?.pending) {
        setOcrPagesMessage("OCRページは処理中です。");
        setOcrPages([]);
        setOcrTableHeader([]);
        setOcrTableRows([]);
        setOcrTablePageIndex(null);
        setOcrTableMessage("");
        setActiveOcrPageIndex(0);
        return;
      }
      const pages = Array.isArray(res.data?.pages) ? res.data.pages : [];
      if (pages.length) {
        const table = extractFirstTable(pages);
        if (table) {
          setActiveOcrPageIndex(table.pageArrayIndex);
          setOcrTableHeader(table.header);
          setOcrTableRows(table.rows.map((row) => [...row]));
          setOcrTablePageIndex(table.pageIndex);
          setOcrTableMessage("");
        } else {
          setActiveOcrPageIndex(0);
          setTableFromPage(pages[0], 0);
        }
        setOcrPages(pages);
        setOcrPagesMessage("");
      } else {
        setOcrPages([]);
        setOcrPagesMessage("OCRページがありません。");
        setOcrTableHeader([]);
        setOcrTableRows([]);
        setOcrTablePageIndex(null);
        setOcrTableMessage("編集できる表が見つかりません。");
        setActiveOcrPageIndex(0);
      }
    } catch (err: any) {
      const status = err?.response?.status;
      setOcrPagesMessage(status === 404 ? "OCRページが見つかりません。" : "OCRページの取得に失敗しました。");
      setOcrPages([]);
      setOcrTableHeader([]);
      setOcrTableRows([]);
      setOcrTablePageIndex(null);
      setOcrTableMessage("");
      setActiveOcrPageIndex(0);
    } finally {
      setOcrPagesLoading(false);
    }
  };

  const loadBags = async () => {
    if (!order) return;
    setBagLoading(true);
    setBagMessage("袋分け結果を取得中...");
    try {
      const res = await apiClient.get(`/orders/${order.id}/bags`);
      const rows = Array.isArray(res.data?.bags) ? res.data.bags : [];
      setBagRows(rows);
      setBagMessage(rows.length ? "" : "袋分け結果がまだ生成されていません。");
    } catch (err: any) {
      setBagRows([]);
      setBagMessage("袋分け結果の取得に失敗しました。");
    } finally {
      setBagLoading(false);
    }
  };

  const rebuildBags = async () => {
    if (!order) return false;
    setBagLoading(true);
    setBagMessage("袋分けを再計算中...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/bags/rebuild`);
      const rows = Array.isArray(res.data?.bags) ? res.data.bags : [];
      setBagRows(rows);
      setBagMessage(rows.length ? "袋分けを更新しました。" : "袋分け結果がありません。");
      return true;
    } catch (err: any) {
      setBagRows([]);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailText = detail ? ` (${detail})` : "";
      const statusText = status ? ` [${status}]` : "";
      setBagMessage(`袋分けの再計算に失敗しました。${statusText}${detailText}`);
      return false;
    } finally {
      setBagLoading(false);
    }
  };

  const selectOcrPage = (pageIndex: number) => {
    setActiveOcrPageIndex(pageIndex);
    setTableFromPage(ocrPages[pageIndex], pageIndex);
  };

  useEffect(() => {
    if (!order?.id) return;
    loadOcrPages();
  }, [order?.id]);

  useEffect(() => {
    if (!order?.id) return;
    loadBags();
  }, [order?.id]);

  const updateOcrTableCell = (rowIndex: number, cellIndex: number, value: string) => {
    setOcrTableRows((prev) => {
      const next = prev.map((row) => [...row]);
      if (!next[rowIndex]) return prev;
      while (next[rowIndex].length <= cellIndex) {
        next[rowIndex].push("");
      }
      next[rowIndex][cellIndex] = value;
      return next;
    });
  };

  const addOcrTableRow = () => {
    const columnCount = getColumnCount(ocrTableHeader, ocrTableRows);
    setOcrTableRows((prev) => [...prev, Array.from({ length: columnCount }, () => "")]);
  };

  const duplicateOcrTableRow = (rowIndex: number) => {
    setOcrTableRows((prev) => {
      const next = prev.map((row) => [...row]);
      const row = next[rowIndex];
      if (!row) return prev;
      next.splice(rowIndex + 1, 0, [...row]);
      return next;
    });
  };

  const removeOcrTableRow = (rowIndex: number) => {
    setOcrTableRows((prev) => prev.filter((_, idx) => idx !== rowIndex));
  };

  const applyOcrTable = async (): Promise<boolean> => {
    if (!order) return;
    if (!ocrTableRows.length) {
      setOcrTableMessage("編集できる表がありません。");
      return false;
    }
    const markdown = buildMarkdownTable(ocrTableHeader, ocrTableRows);
    setOcrTableSaving(true);
    setOcrTableMessage("OCRテーブルを反映中...");
    try {
      const res = await apiClient.post(`/orders/${order.id}/ocr-apply`, { markdown });
      setOrder(res.data);
      setOcrTableMessage("OCRテーブルを反映しました。");
      await rebuildBags();
      return true;
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 400) {
        setOcrTableMessage("OCRテーブルの反映に失敗しました。");
      } else {
        setOcrTableMessage("OCRテーブルの反映中にエラーが発生しました。");
      }
      return false;
    } finally {
      setOcrTableSaving(false);
    }
  };

  const applyOcrPreview = async () => {
    if (!order) return;
    if (!ocrTableRows.length) {
      setActionMessage("編集できる表がありません。");
      return;
    }
    setActionMessage("OCR結果を明細に反映中...");
    const ok = await applyOcrTable();
    if (ok) {
      setActionMessage("OCR結果を明細に反映しました。");
    } else {
      setActionMessage("OCR結果の反映に失敗しました。");
    }
  };

  const saveLines = async () => {
    if (!order) return;
    await apiClient.put(`/orders/${order.id}/lines`, { lines: order.lines || [] });
    setActionMessage("保存しました。");
  };

  const confirm = async () => {
    if (!order) return;
    try {
      await apiClient.post(`/orders/${order.id}/confirm`);
      setOrder({ ...order, status: "確定" });
      setActionMessage("確定しました。");
      loadBags();
    } catch (err: any) {
      setActionMessage("確定に失敗しました。");
    }
  };

  const updateFacility = async () => {
    if (!order) return;
    const trimmed = facility.trim();
    if (!trimmed) {
      setActionMessage("施設IDを入力してください。");
      return;
    }
    try {
      await apiClient.post(`/orders/${order.id}/facility`, { facility: trimmed });
      setOrder({ ...order, facility: trimmed });
      setActionMessage("施設を設定しました。");
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setActionMessage("施設が見つかりません。");
      } else {
        setActionMessage("施設の設定に失敗しました。");
      }
    }
  };

  const reparse = async () => {
    if (!order) return;
    if (reparseTimerRef.current !== null) {
      window.clearTimeout(reparseTimerRef.current);
      reparseTimerRef.current = null;
    }
    setActionMessage("再解析中...");
    setReparsePending(true);
    const beforeCount = order.lines?.length ?? 0;
    const orderId = order.id;
    const errorContext = (id: string) => {
      const timestamp = new Date().toLocaleString("ja-JP");
      return ` [注文ID: ${id} / 時刻: ${timestamp}]`;
    };
    const withErrorContext = (message: string, id: string) => `${message}${errorContext(id)}`;
    let accepted = false;
    try {
      const payload = ocrPrompt.trim() ? { ocr_prompt: ocrPrompt.trim() } : null;
      const res = await apiClient.post(`/orders/${orderId}/reparse`, payload, { timeout: 900000 });
      if (res.status === 202 || res.data?.accepted) {
        accepted = true;
        setActionMessage("再解析を開始しました。完了まで数分かかります。");
        setOrder({
          ...order,
          ocr_status: "running",
          ocr_error: null,
          ocr_updated_at: new Date().toISOString(),
        });
        const pollReparse = async () => {
          try {
            const statusRes = await apiClient.get(`/orders/${orderId}`);
            const updated = statusRes.data as OrderDetail;
            setOrder(updated);
            const status = updated.ocr_status || "";
            if (status && status !== "running") {
              setReparsePending(false);
              const afterCount = updated.lines?.length ?? 0;
              const changedText = beforeCount === afterCount ? "変更なし" : "変更あり";
              const error = updated.ocr_error || "";
              const errorDetail = error ? ` (${error})` : "";
              if (status === "failed" || status === "empty") {
                if (error === "lines_empty") {
                  setActionMessage(
                    withErrorContext(
                      `解析結果が空でした。OCR設定を見直してください。${errorDetail}`,
                      orderId
                    )
                  );
                } else if (error === "document_ai_missing") {
                  setActionMessage(
                    withErrorContext(`OCRの設定が必要です。${errorDetail}`, orderId)
                  );
                } else if (error === "document_ai_failed") {
                  setActionMessage(
                    withErrorContext(`OCRの処理に失敗しました。${errorDetail}`, orderId)
                  );
                } else if (error.startsWith("main_ocr_failed")) {
                  const provider = error.includes(":") ? error.split(":")[1] : "";
                  const providerLabel = provider || "OCR";
                  setActionMessage(
                    withErrorContext(`${providerLabel}の処理に失敗しました。${errorDetail}`, orderId)
                  );
                } else {
                  setActionMessage(withErrorContext(`再解析に失敗しました。${errorDetail}`, orderId));
                }
              } else {
                setActionMessage(`再解析しました。${beforeCount}→${afterCount} (${changedText})`);
                await rebuildBags();
              }
              reparseTimerRef.current = null;
              await refreshOcrOutput(orderId);
              return;
            }
            reparseTimerRef.current = window.setTimeout(pollReparse, 5000);
          } catch {
            setReparsePending(false);
            reparseTimerRef.current = null;
            setActionMessage(withErrorContext("再解析の状態取得に失敗しました。", orderId));
          }
        };
        reparseTimerRef.current = window.setTimeout(pollReparse, 2000);
        return;
      }
      if (res.data?.order) {
        setOrder(res.data.order);
      }
      const reparse = res.data?.order?.reparse;
      if (reparse) {
        const changedText = reparse.changed ? "変更あり" : "変更なし";
        const provider = typeof reparse.provider === "string" ? reparse.provider : "";
        const providerText = provider ? ` / ${provider}` : "";
        setActionMessage(
          `再解析しました。${reparse.before_count}→${reparse.after_count} (${changedText})${providerText}`
        );
      } else {
        setActionMessage("再解析しました。");
      }
      await rebuildBags();
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const detailError = typeof detail === "string" ? detail : detail?.error;
      const detailProvider = typeof detail === "object" ? detail?.provider : "";
      if (status === 400) {
        setActionMessage(withErrorContext("施設IDを設定してから再解析してください。", orderId));
      } else if (status === 404) {
        setActionMessage(withErrorContext("注文または施設が見つかりません。", orderId));
      } else if (status === 409) {
        if (detailError === "document_ai_missing") {
          setActionMessage(withErrorContext("OCRの設定が必要です。", orderId));
        } else if (detailError === "document_ai_failed") {
          setActionMessage(withErrorContext("OCRの処理に失敗しました。", orderId));
        } else if (detailError === "main_ocr_failed" || detailError?.startsWith("main_ocr_failed")) {
          const inlineProvider =
            typeof detailError === "string" && detailError.includes(":")
              ? detailError.split(":")[1]
              : "";
          const provider = detailProvider || inlineProvider;
          const providerLabel = provider || "OCR";
          setActionMessage(withErrorContext(`${providerLabel}の処理に失敗しました。`, orderId));
        } else if (detailError === "lines_empty") {
          setActionMessage(
            withErrorContext("解析結果が空でした。OCR設定を見直してください。", orderId)
          );
        } else {
          setActionMessage(
            withErrorContext("解析結果が空でした。OCR設定を見直してください。", orderId)
          );
        }
      } else {
        const suffix = status ? ` (status ${status})` : " (network error)";
        const detailText = detail ? ` ${detail}` : "";
        setActionMessage(
          withErrorContext(`再解析に失敗しました。${suffix}${detailText}`, orderId)
        );
      }
    } finally {
      if (!accepted) {
        setReparsePending(false);
      }
    }
  };

  const updateLineQuantity = (idx: number, qty: number) => {
    if (!order) return;
    const next = [...order.lines];
    next[idx] = { ...next[idx], quantity_corrected: qty };
    setOrder({ ...order, lines: next });
  };

  const openOutput = async (path: string) => {
    try {
      const res = await apiClient.get(path, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch {
      setActionMessage("出力の取得に失敗しました。");
    }
  };

  const outputPreviewLabels: Record<OutputPreview["type"], string> = {
    labels: "ラベルCSV",
    delivery: "納品書Excel",
    aggregate: "総量CSV",
  };

  const loadOutputPreview = async (type: OutputPreview["type"]) => {
    if (!order) return;
    setOutputPreviewLoading(true);
    setOutputPreviewMessage("プレビューを取得中...");
    try {
      const res = await apiClient.get("/outputs/preview", {
        params: { order_id: order.id, type, limit: 10 },
      });
      const headers = Array.isArray(res.data?.headers) ? res.data.headers : [];
      const rows = Array.isArray(res.data?.rows) ? res.data.rows : [];
      const cappedRows = rows.slice(0, 10);
      setOutputPreview({ type, headers, rows: cappedRows });
      if (!cappedRows.length) {
        setOutputPreviewMessage("プレビューが空です。");
      } else if (rows.length > cappedRows.length) {
        setOutputPreviewMessage("先頭10件のみ表示しています。");
      } else {
        setOutputPreviewMessage("");
      }
    } catch {
      setOutputPreview(null);
      setOutputPreviewMessage("プレビューの取得に失敗しました。");
    } finally {
      setOutputPreviewLoading(false);
    }
  };

  const lines = order?.lines || [];
  const pivotRows = buildPivotRows(lines);
  const categoryOrder = buildCategoryColumns(lines).map((col) => col.key);
  const pivotGroups = groupByDateAndCategory(buildPivotCategoryRows(pivotRows), categoryOrder);
  const lineGroups = groupByDateAndCategory(
    lines.map((line, idx) => {
      const categoryKey = makeCategoryKey(line.diet_type, line.area_id);
      return {
        line,
        idx,
        date: line.date,
        categoryKey,
        categoryLabel: formatCategoryLabel(categoryKey),
      };
    }),
    categoryOrder,
  );
  const bagGroups = groupBagsByDate(bagRows);
  const activeOcrPage = ocrPages[activeOcrPageIndex];
  const activeOcrPageLabel = activeOcrPage
    ? activeOcrPage.page_index ?? activeOcrPageIndex + 1
    : null;
  const previewLineLimit = 10;
  const activeMarkdownLines = activeOcrPage?.markdown_text
    ? splitLines(activeOcrPage.markdown_text)
    : [];
  const previewMarkdownText = activeMarkdownLines.slice(0, previewLineLimit).join("\n");
  const activeOcrBlocks = previewMarkdownText ? parseMarkdownBlocks(previewMarkdownText) : [];
  const fullOcrBlocks = activeOcrPage?.markdown_text
    ? parseMarkdownBlocks(activeOcrPage.markdown_text)
    : [];
  const facilityCandidates = ocrOutput?.facility_candidates || [];
  const displayedOcrBlocks = showMarkdownRaw ? fullOcrBlocks : activeOcrBlocks;

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Order Detail</p>
          <h1>注文詳細</h1>
          <p className="subtle">修正・確定・出力までをまとめて操作できます。</p>
        </div>
        <TopNav />
      </header>

      {!order ? (
        <p className="subtle">Loading...</p>
      ) : (
        <>
          <section className="panel">
            <header className="panel-header">
              <h2>概要</h2>
              <span className="status-pill">{order.status}</span>
            </header>
            <div className="summary-grid">
              <div>
                <p className="field-label">注文ID</p>
                <p className="summary-value">{order.id}</p>
              </div>
              <div>
                <p className="field-label">Message ID</p>
                <p className="summary-value">{order.message_id || "不明"}</p>
              </div>
              <div>
                <p className="field-label">月</p>
                <p className="summary-value">
                  {order.week || "未確定"}{" "}
                  {order.week ? <Link href={`/menus/${order.week}`}>メニュー編集</Link> : null}
                </p>
              </div>
              <div>
                <p className="field-label">解析ステータス</p>
                <p className="summary-value">
                  {order.ocr_status || "未実行"}
                  {order.ocr_updated_at ? ` / ${formatTimestamp(order.ocr_updated_at)}` : ""}
                </p>
              </div>
            </div>
            <div className="summary-actions">
              <label className="field">
                <span className="field-label">施設</span>
                <select
                  className="input"
                  value={facility}
                  onChange={(e) => setFacility(e.target.value)}
                  disabled={facilityOptionsLoading}
                >
                  <option value="">施設を選択</option>
                  {facility && !facilityOptions.some((opt) => opt.id === facility) ? (
                    <option value={facility}>{facility} (未登録)</option>
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
              <button className="btn" onClick={updateFacility}>
                施設を設定
              </button>
            </div>
            {facilityCandidates.length ? (
              <div className="facility-suggestions">
                <span className="field-label">推定施設</span>
                <div className="facility-suggestion-list">
                  {facilityCandidates.map((candidate) => (
                    <button
                      key={`${candidate.facility_id}-${candidate.reason || "auto"}`}
                      type="button"
                      className={`facility-chip${candidate.auto ? " auto" : ""}`}
                      onClick={() => setFacility(candidate.facility_id)}
                    >
                      <span className="facility-chip-name">
                        {candidate.facility_name || candidate.facility_id}
                      </span>
                      {candidate.score != null ? (
                        <span className="facility-chip-score">
                          {Math.round(candidate.score * 100)}%
                        </span>
                      ) : null}
                      {candidate.reason ? (
                        <span className="facility-chip-reason">
                          {formatFacilityReason(candidate.reason)}
                        </span>
                      ) : null}
                    </button>
                  ))}
                </div>
                <span className="facility-suggestion-note">クリックで施設選択に反映します。</span>
              </div>
            ) : null}
            <div className="summary-actions summary-actions-right">
              <button className="btn ghost" onClick={reparse} disabled={reparsePending}>
                {reparsePending ? "再解析中..." : "再解析"}
              </button>
            </div>
            {order.ocr_prompt_enabled === false ? null : (
              <details className="prompt-panel">
                <summary>OCRプロンプト（任意）</summary>
                <textarea
                  className="input"
                  rows={4}
                  value={ocrPrompt}
                  onChange={(e) => setOcrPrompt(e.target.value)}
                />
              </details>
            )}
            <details className="prompt-panel">
              <summary>生OCR（最新）</summary>
              <div className="raw-actions">
                <button className="btn ghost" type="button" onClick={loadOcrRaw} disabled={ocrRawLoading}>
                  {ocrRawLoading ? "取得中..." : "生OCRを取得"}
                </button>
                {ocrRawMessage ? <span className="raw-message">{ocrRawMessage}</span> : null}
              </div>
              {ocrRawText ? <pre className="raw-output">{ocrRawText}</pre> : null}
            </details>
            <details className="prompt-panel">
              <summary>OCR結果（失敗セル）</summary>
              {ocrOutputMessage ? <p className="subtle">{ocrOutputMessage}</p> : null}
              {ocrOutput ? (
                <div className="raw-output">
                  <p>
                    ステータス: {ocrOutput.status || "不明"}
                    {ocrOutput.stage ? ` / ステージ: ${ocrOutput.stage}` : ""}
                    {" / "}テンプレート:{" "}
                    {ocrOutput.template_id || "未分類"}
                  </p>
                  {ocrOutput.warnings?.length ? (
                    <p>警告: {ocrOutput.warnings.join(" / ")}</p>
                  ) : null}
                  {ocrOutput.failed_cells?.length ? (
                    <ul className="failed-list">
                      {ocrOutput.failed_cells.map((cell, idx) => (
                        <li key={`${cell.row}-${cell.col}-${idx}`}>
                          {cell.row || "-"} / {cell.col || "-"} {cell.reason ? `(${cell.reason})` : ""}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>失敗セルなし</p>
                  )}
                  {typeof ocrOutput.table_raw === "string" && ocrOutput.table_raw ? (
                    <>
                      <p className="subtle">OCR生出力</p>
                      <pre className="raw-output">{ocrOutput.table_raw}</pre>
                    </>
                  ) : null}
                </div>
              ) : null}
            </details>
            {actionMessage && <p className="message">{actionMessage}</p>}
          </section>

          <section className="panel">
            <header className="panel-header">
              <div>
                <h2>メニュー×区分 サマリー</h2>
                <p className="subtle">OCRオーバーレイと原本、読み取り結果を確認できます。</p>
              </div>
              <div className="panel-actions">
                <button className="btn ghost" type="button" onClick={loadOcrPages} disabled={ocrPagesLoading}>
                  {ocrPagesLoading ? "取得中..." : "OCRページを更新"}
                </button>
                <button
                  className="btn primary"
                  type="button"
                  onClick={applyOcrPreview}
                  disabled={ocrTableSaving || !ocrTableRows.length}
                >
                  {ocrTableSaving ? "反映中..." : "OCR結果を明細に反映"}
                </button>
                <button
                  className={`btn ${showOcrEdit ? "primary" : "ghost"}`}
                  type="button"
                  onClick={() => setShowOcrEdit((prev) => !prev)}
                >
                  {showOcrEdit ? "編集を閉じる" : "編集"}
                </button>
              </div>
            </header>
            {ocrPagesMessage ? <p className="subtle">{ocrPagesMessage}</p> : null}
            {ocrPages.length > 1 ? (
              <div className="page-tabs">
                {ocrPages.map((page, pageIdx) => (
                  <button
                    key={`ocr-page-${page.page_index ?? pageIdx}`}
                    type="button"
                    className={`page-tab ${pageIdx === activeOcrPageIndex ? "active" : ""}`}
                    onClick={() => selectOcrPage(pageIdx)}
                  >
                    Page {page.page_index ?? pageIdx + 1}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="ocr-showcase">
              <div className="ocr-preview-card">
                <div className="preview-header">
                  <span className="subtle">OCRオーバーレイ</span>
                  {activeOcrPageLabel != null ? (
                    <span className="subtle">Page {activeOcrPageLabel}</span>
                  ) : null}
                </div>
                {activeOcrPage?.ocr_overlay_url ? (
                  <img src={activeOcrPage.ocr_overlay_url} alt="OCR overlay" className="ocr-preview" />
                ) : (
                  <div className="preview-placeholder">OCRオーバーレイなし</div>
                )}
                {activeOcrPage?.layout_overlay_url ? (
                  <>
                    <p className="subtle">レイアウトオーバーレイ</p>
                    <img src={activeOcrPage.layout_overlay_url} alt="Layout overlay" className="ocr-preview" />
                  </>
                ) : null}
              </div>
              <div className="ocr-preview-card">
                <div className="preview-header">
                  <span className="subtle">原本PDF</span>
                  {pdfUrl ? (
                    <a href={pdfUrl} target="_blank" rel="noreferrer" className="ghost-link">
                      原本を開く
                    </a>
                  ) : (
                    <span className="subtle">{pdfError || "PDFを読み込み中..."}</span>
                  )}
                </div>
                {pdfUrl ? (
                  <iframe title="order-pdf" src={pdfUrl} className="pdf-frame pdf-frame-compact" />
                ) : (
                  <div className="pdf-frame pdf-placeholder">{pdfError || "PDFを読み込み中..."}</div>
                )}
              </div>
            </div>
            <div className="ocr-result">
              <div className="ocr-result-header">
                <p className="subtle">読み取り結果</p>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={() => setShowMarkdownRaw((prev) => !prev)}
                >
                  {showMarkdownRaw ? "全体を閉じる" : "全体を表示"}
                </button>
              </div>
              {!showMarkdownRaw && activeMarkdownLines.length > previewLineLimit ? (
                <p className="subtle">先頭10行のみ表示しています。</p>
              ) : null}
              <div className="markdown-preview">
                {displayedOcrBlocks.length ? (
                  displayedOcrBlocks.map((block, blockIdx) =>
                    block.type === "table" ? (
                      <div key={`table-${blockIdx}`} className="markdown-table">
                        <table>
                          {block.header.length ? (
                            <thead>
                              <tr>
                                {block.header.map((cell, idx) => (
                                  <th key={`header-${idx}`}>{cell}</th>
                                ))}
                              </tr>
                            </thead>
                          ) : null}
                          <tbody>
                            {block.rows.map((row, rowIdx) => (
                              <tr key={`row-${rowIdx}`}>
                                {row.map((cell, idx) => (
                                  <td key={`cell-${rowIdx}-${idx}`}>{cell}</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div key={`text-${blockIdx}`} className="markdown-text">
                        {block.lines.map((line, idx) => renderMarkdownLine(line, `${blockIdx}-${idx}`))}
                      </div>
                    ),
                  )
                ) : (
                  <p className="subtle">Markdownがありません。</p>
                )}
              </div>
            </div>
            {showOcrEdit ? (
              <div className="ocr-edit">
                <div className="ocr-edit-header">
                  <div>
                    <p className="subtle">
                      編集対象: {ocrTablePageIndex != null ? `Page ${ocrTablePageIndex}` : "未選択"}
                    </p>
                  </div>
                  <div className="ocr-edit-actions">
                    <button className="btn ghost" type="button" onClick={addOcrTableRow}>
                      行を追加
                    </button>
                    <button className="btn primary" onClick={applyOcrTable} disabled={ocrTableSaving}>
                      {ocrTableSaving ? "反映中..." : "OCRテーブルを反映"}
                    </button>
                  </div>
                </div>
                {ocrTableMessage ? <p className="subtle">{ocrTableMessage}</p> : null}
                {ocrTableRows.length ? (
                  <div className="table-wrap">
                    <table className="ocr-edit-table">
                      {ocrTableHeader.length ? (
                        <thead>
                          <tr>
                            {ocrTableHeader.map((cell, idx) => (
                              <th key={`ocr-header-${idx}`}>{cell}</th>
                            ))}
                            <th>操作</th>
                          </tr>
                        </thead>
                      ) : null}
                      <tbody>
                        {ocrTableRows.map((row, rowIdx) => (
                          <tr key={`ocr-row-${rowIdx}`}>
                            {row.map((cell, cellIdx) => (
                              <td key={`ocr-cell-${rowIdx}-${cellIdx}`}>
                                <input
                                  className="input ocr-edit-input"
                                  value={cell}
                                  onChange={(e) => updateOcrTableCell(rowIdx, cellIdx, e.target.value)}
                                />
                              </td>
                            ))}
                            <td>
                              <div className="ocr-row-actions">
                                <button
                                  className="btn ghost"
                                  type="button"
                                  onClick={() => duplicateOcrTableRow(rowIdx)}
                                >
                                  複製
                                </button>
                                <button
                                  className="btn ghost"
                                  type="button"
                                  onClick={() => removeOcrTableRow(rowIdx)}
                                >
                                  削除
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>区分別一覧</h2>
            </header>
            {pivotGroups.length === 0 ? (
              <p className="subtle">データなし</p>
            ) : (
              <div className="wrap-grid">
                {pivotGroups.map((group) => (
                  <details key={`pivot-${group.date}-${group.categoryKey}`} className="date-group">
                    <summary className="date-group-header">
                      <span className="date-group-title">{group.date}</span>
                      <span className="group-separator">/</span>
                      <span className="group-tag">{group.categoryLabel}</span>
                      <span className="group-count">{group.rows.length}件</span>
                    </summary>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>メニュー</th>
                            <th>時間帯</th>
                            <th>袋</th>
                            <th>数量</th>
                            <th>備考</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map((row, rowIdx) => (
                            <tr key={`pivot-row-${group.date}-${group.categoryKey}-${rowIdx}`}>
                              <td>{row.menu_name}</td>
                              <td>{row.daypart}</td>
                              <td>{row.bag_type}</td>
                              <td>{row.quantity}</td>
                              <td>{row.notes.size ? Array.from(row.notes).join(" / ") : "-"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>明細 (編集)</h2>
            </header>
            {lineGroups.length === 0 ? (
              <p className="subtle">データなし</p>
            ) : (
              <div className="wrap-grid">
                {lineGroups.map((group) => (
                  <details key={`line-${group.date}-${group.categoryKey}`} className="date-group">
                    <summary className="date-group-header">
                      <span className="date-group-title">{group.date}</span>
                      <span className="group-separator">/</span>
                      <span className="group-tag">{group.categoryLabel}</span>
                      <span className="group-count">{group.rows.length}件</span>
                    </summary>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>メニュー</th>
                            <th>時間帯</th>
                            <th>袋</th>
                            <th>OCR</th>
                            <th>修正</th>
                            <th>差分</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map(({ line, idx }) => (
                            <tr key={line.line_id || idx}>
                              <td>{line.menu_name || "-"}</td>
                              <td>{line.daypart || "-"}</td>
                              <td>{line.bag_type || "-"}</td>
                              <td>{line.quantity_original ?? "-"}</td>
                              <td>
                                <input
                                  className="input"
                                  type="number"
                                  value={line.quantity_corrected ?? ""}
                                  onChange={(e) => updateLineQuantity(idx, Number(e.target.value))}
                                />
                              </td>
                              <td>
                                {line.quantity_original == null
                                  ? "-"
                                  : (line.quantity_corrected ?? line.quantity_original) -
                                    (line.quantity_original ?? 0)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>操作</h2>
            </header>
            <div className="actions">
              <button className="btn ghost" onClick={saveLines}>
                保存 (要確認のまま)
              </button>
              <button className="btn primary" onClick={confirm}>
                確定
              </button>
            </div>
          </section>

          <section className="panel">
            <header className="panel-header">
              <div>
                <h2>袋分け結果</h2>
                <p className="subtle">OCR反映後に自動更新します。必要に応じて再計算できます。</p>
              </div>
              <div className="panel-actions">
                <button className="btn ghost" type="button" onClick={loadBags} disabled={bagLoading}>
                  {bagLoading ? "取得中..." : "更新"}
                </button>
                <button className="btn ghost" type="button" onClick={rebuildBags} disabled={bagLoading}>
                  {bagLoading ? "再計算中..." : "再計算"}
                </button>
              </div>
            </header>
            {bagMessage ? <p className="subtle">{bagMessage}</p> : null}
            {bagRows.length === 0 ? (
              <p className="subtle">袋分け結果がありません。</p>
            ) : (
              <div className="wrap-grid">
                {bagGroups.map((group) => (
                  <div key={`bag-${group.date}`} className="date-group">
                    <div className="date-group-header">
                      <span className="date-group-title">{group.date}</span>
                      <span className="group-count">{group.rows.length}件</span>
                    </div>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>食区</th>
                            <th>メニュー</th>
                            <th>区分</th>
                            <th>エリア</th>
                            <th>袋</th>
                            <th>数量</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map((bag) => (
                            <tr key={bag.id}>
                              <td>{bag.daypart || "-"}</td>
                              <td>{bag.menu_name || "-"}</td>
                              <td>{bag.diet_type ? formatDietType(bag.diet_type) : "-"}</td>
                              <td>{bag.area_id || "-"}</td>
                              <td>{bag.bag_type || "-"}</td>
                              <td>{formatQuantity(bag.quantity)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>出力</h2>
            </header>
            <div className="outputs">
              <div className="output-card">
                <button
                  className="output-link"
                  type="button"
                  onClick={() => openOutput(`/outputs/labels?order_id=${order.id}`)}
                >
                  ラベルCSV
                </button>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={() => loadOutputPreview("labels")}
                  disabled={outputPreviewLoading}
                >
                  プレビュー
                </button>
              </div>
              <div className="output-card">
                <button
                  className="output-link"
                  type="button"
                  onClick={() => openOutput(`/outputs/delivery-notes?order_id=${order.id}`)}
                >
                  納品書Excel
                </button>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={() => loadOutputPreview("delivery")}
                  disabled={outputPreviewLoading}
                >
                  プレビュー
                </button>
              </div>
              <div className="output-card">
                <button
                  className="output-link"
                  type="button"
                  onClick={() => openOutput(`/outputs/manufacturing-aggregate?order_id=${order.id}`)}
                >
                  総量CSV
                </button>
                <button
                  className="btn ghost"
                  type="button"
                  onClick={() => loadOutputPreview("aggregate")}
                  disabled={outputPreviewLoading}
                >
                  プレビュー
                </button>
              </div>
            </div>
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
                            <td key={`preview-cell-${rowIdx}-${idx}`}>{cell}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ) : null}
          </section>
        </>
      )}

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

        .panel-header > div {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .panel-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          align-items: center;
        }

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
        }

        .status-pill {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .summary-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
        }

        .summary-value {
          margin: 4px 0 0;
          font-weight: 600;
        }

        .summary-actions {
          display: flex;
          gap: 12px;
          align-items: flex-end;
          flex-wrap: wrap;
          margin-top: 12px;
        }

        .summary-actions .field {
          min-width: 220px;
          flex: 1;
        }

        .summary-actions-right {
          justify-content: flex-end;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .facility-suggestions {
          margin-top: 8px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .facility-suggestion-list {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .facility-chip {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          border-radius: 999px;
          border: 1px solid rgba(25, 32, 30, 0.14);
          background: #fbfbf9;
          padding: 6px 10px;
          font-size: 12px;
          cursor: pointer;
        }

        .facility-chip.auto {
          border-color: rgba(36, 110, 87, 0.5);
          background: rgba(36, 110, 87, 0.08);
        }

        .facility-chip-name {
          font-weight: 600;
        }

        .facility-chip-score {
          color: #4b5c57;
        }

        .facility-chip-reason {
          color: #6b7b76;
        }

        .facility-suggestion-note {
          font-size: 12px;
          color: #6b7b76;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .prompt-panel {
          margin-top: 14px;
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
        }

        .prompt-panel summary {
          cursor: pointer;
          font-weight: 600;
          color: #354341;
          list-style: none;
        }

        .prompt-panel summary::-webkit-details-marker {
          display: none;
        }

        .prompt-panel textarea {
          margin-top: 10px;
          width: 100%;
          min-height: 120px;
        }

        .raw-actions {
          display: flex;
          align-items: center;
          gap: 12px;
          margin-top: 10px;
          flex-wrap: wrap;
        }

        .raw-message {
          font-size: 12px;
          color: #5f7b74;
        }

        .raw-output {
          margin-top: 10px;
          padding: 12px;
          border-radius: 12px;
          background: #f4f1ea;
          border: 1px solid rgba(25, 32, 30, 0.08);
          font-size: 12px;
          white-space: pre-wrap;
          max-height: 240px;
          overflow: auto;
          font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        }

        .ocr-preview {
          width: 100%;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fff;
        }

        .page-tabs {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }

        .page-tab {
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbfbf9;
          border-radius: 999px;
          padding: 6px 12px;
          font-size: 12px;
          cursor: pointer;
        }

        .page-tab.active {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: #1f2a2a;
        }

        .ocr-showcase {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          margin-bottom: 16px;
        }

        .ocr-preview-card {
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #fbfbf9;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .preview-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          font-size: 12px;
        }

        .preview-placeholder {
          padding: 16px;
          border-radius: 10px;
          background: #f4f1ea;
          color: #6b7b76;
          font-size: 12px;
          text-align: center;
        }

        .ocr-result {
          margin-top: 6px;
        }

        .ocr-result-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 8px;
        }

        .markdown-preview {
          padding: 10px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #f7f5ee;
          overflow: auto;
        }

        .markdown-table table {
          font-size: 12px;
        }

        .markdown-text {
          margin-bottom: 8px;
        }

        .markdown-line {
          margin: 0 0 6px;
          font-size: 12px;
          color: #3f4d4a;
        }

        .markdown-image {
          max-width: 100%;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fff;
          margin-bottom: 8px;
        }

        .markdown-raw {
          margin-top: 10px;
          padding: 12px;
          border-radius: 12px;
          border: 1px dashed rgba(25, 32, 30, 0.12);
          background: #fbfbf9;
          font-size: 12px;
          color: #3f4d4a;
          white-space: pre-wrap;
          word-break: break-word;
          max-height: 280px;
          overflow: auto;
        }

        .ocr-edit {
          margin-top: 16px;
          padding-top: 12px;
          border-top: 1px dashed rgba(25, 32, 30, 0.12);
        }

        .ocr-edit-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 10px;
        }

        .ocr-edit-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }

        .ocr-row-actions {
          display: flex;
          gap: 6px;
        }

        .ocr-edit-table th,
        .ocr-edit-table td {
          padding: 6px;
          font-size: 12px;
        }

        .ocr-edit-input {
          padding: 6px 8px;
          font-size: 12px;
          min-width: 80px;
        }

        .failed-list {
          margin: 8px 0 0;
          padding-left: 18px;
          font-size: 12px;
          color: #5f7b74;
        }

        .failed-list li {
          margin-bottom: 4px;
        }

        .pdf-frame {
          width: 100%;
          height: 520px;
          border: 1px solid #ddd;
          border-radius: 12px;
        }

        .pdf-frame-compact {
          height: 420px;
        }

        .pdf-placeholder {
          display: flex;
          align-items: center;
          justify-content: center;
          color: #6b7b76;
          background: #f9f7f2;
          font-size: 14px;
        }

        .table-wrap {
          overflow-x: auto;
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

        .actions {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: #f4f1ea;
        }

        .outputs {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }

        .output-card {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .output-link {
          padding: 10px 16px;
          border-radius: 12px;
          background: #fbfbf9;
          border: 1px solid rgba(25, 32, 30, 0.08);
          color: inherit;
          font-weight: 600;
          cursor: pointer;
        }

        .output-preview {
          margin-top: 12px;
          padding: 12px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
        }

        .output-preview summary {
          cursor: pointer;
          font-weight: 600;
          font-size: 13px;
          color: #354341;
          margin-bottom: 10px;
          list-style: none;
        }

        .output-preview summary::-webkit-details-marker {
          display: none;
        }

        .wrap-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr));
          align-items: start;
        }

        .date-group {
          padding: 12px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          background: #ffffff;
        }

        .date-group summary {
          display: flex;
          align-items: center;
          gap: 8px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 600;
          color: #354341;
          list-style: none;
        }

        .date-group-header {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 13px;
          font-weight: 600;
          color: #354341;
          margin-bottom: 10px;
        }

        .date-group summary::-webkit-details-marker {
          display: none;
        }

        .date-group[open] summary {
          margin-bottom: 10px;
        }

        .date-group .table-wrap {
          max-height: 360px;
          overflow: auto;
        }

        .date-group table {
          min-width: 560px;
        }

        .date-group th,
        .date-group td {
          white-space: nowrap;
        }

        .date-group-title {
          white-space: nowrap;
        }

        .group-separator {
          color: #a2aaa8;
        }

        .group-tag {
          background: #f4f1ea;
          border-radius: 999px;
          padding: 2px 8px;
          font-size: 12px;
          font-weight: 600;
          color: #354341;
        }

        .group-count {
          margin-left: auto;
          font-size: 12px;
          font-weight: 500;
          color: #6b7b76;
        }

        @media (max-width: 720px) {
          .summary-actions {
            flex-direction: column;
            align-items: stretch;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
