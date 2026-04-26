export type OcrSheetColumnSpec = {
  className: string;
  width: number;
};

export type MarkdownBlock =
  | { type: "table"; header: string[]; rows: string[][] }
  | { type: "text"; lines: string[] };

type OcrPreviewPage = {
  page_index?: number | null;
  markdown_text?: string | null;
  tables?: {
    rows?: string[][];
    row_count?: number | null;
    col_count?: number | null;
  }[];
};

type NormalizeDietTypeToken = (value?: string | null) => string;

export const normalizeHeaderToken = (value: string) => {
  const normalized = value
    .replace(/[　\s]+/g, "")
    .replace(/[（）()[\]【】]/g, "")
    .toLowerCase();
  return normalized
    .replace(/[０-９]/g, (match) => String.fromCharCode(match.charCodeAt(0) - 0xfee0))
    .replace("ｆ", "f")
    .replace("Ｆ", "f");
};

export const getOcrSheetColumnSpec = (
  field: string | null | undefined,
  header: string | null | undefined,
  normalizeDietTypeToken: NormalizeDietTypeToken,
): OcrSheetColumnSpec => {
  const fieldRaw = String(field || "").trim().toLowerCase();
  const fieldToken = normalizeHeaderToken(String(field || ""));
  const headerToken = normalizeHeaderToken(String(header || ""));
  const combined = `${fieldToken} ${headerToken}`;
  const normalizedDiet = normalizeDietTypeToken(`${fieldRaw} ${headerToken}`);
  const isKnownDiet =
    normalizedDiet === "regular" ||
    normalizedDiet === "daycare" ||
    normalizedDiet === "staff" ||
    normalizedDiet === "no_fried" ||
    normalizedDiet === "no_meat" ||
    normalizedDiet === "forbidden_other" ||
    normalizedDiet === "no_fish" ||
    normalizedDiet === "soft" ||
    normalizedDiet === "mixer" ||
    normalizedDiet === "soft_mixer" ||
    normalizedDiet === "change_1" ||
    normalizedDiet === "change_2" ||
    normalizedDiet === "unknown";

  if (
    fieldRaw.startsWith("qty.") ||
    isKnownDiet ||
    combined.includes("肉禁") ||
    combined.includes("肉卵魚禁") ||
    combined.includes("魚禁") ||
    combined.includes("揚げ物禁") ||
    combined.includes("揚物禁") ||
    combined.includes("変更1") ||
    combined.includes("変更2") ||
    combined.includes("軟菜") ||
    combined.includes("ミキサ") ||
    combined.includes("職員") ||
    combined.includes("糖尿") ||
    combined.includes("妊娠") ||
    combined.includes("ゴマ")
  ) {
    return { className: "ocr-sheet-col-qty", width: 86 };
  }
  if (fieldToken.startsWith("date") || headerToken.includes("日付") || headerToken === "date") {
    return { className: "ocr-sheet-col-date", width: 48 };
  }
  if (
    fieldToken === "daypart" ||
    fieldToken === "meal" ||
    fieldToken === "time" ||
    headerToken.includes("区分") ||
    headerToken === "daypart"
  ) {
    return { className: "ocr-sheet-col-daypart", width: 44 };
  }
  if (fieldRaw === "menu_name" || fieldToken === "menuname" || headerToken.includes("メニュー")) {
    return { className: "ocr-sheet-col-menu", width: 168 };
  }
  if (fieldToken === "note" || headerToken.includes("備考")) {
    return { className: "ocr-sheet-col-note", width: 220 };
  }
  return { className: "ocr-sheet-col-default", width: 116 };
};

const isSubheaderRow = (row: string[]) => {
  if (!row.length) return false;
  let nonEmpty = 0;
  let markers = 0;
  row.forEach((cell) => {
    const token = normalizeHeaderToken(cell);
    if (!token) return;
    nonEmpty += 1;
    if (token === "2f" || token === "3f" || token === "2階" || token === "3階") {
      markers += 1;
    }
  });
  return nonEmpty >= 2 && nonEmpty === markers;
};

const mergeHeaderRows = (primary: string[], secondary: string[]) => {
  const maxLen = Math.max(primary.length, secondary.length);
  const merged: string[] = [];
  let currentGroup = "";
  for (let idx = 0; idx < maxLen; idx += 1) {
    const h1 = primary[idx]?.trim() || "";
    const h2 = secondary[idx]?.trim() || "";
    if (h1) currentGroup = h1;
    if (h2) {
      const group = currentGroup || h1;
      merged.push(group ? `${group} ${h2}`.trim() : h2);
    } else {
      merged.push(h1);
    }
  }
  return merged;
};

export const buildPreviewBlocks = (blocks: MarkdownBlock[], lineLimit: number) => {
  let remaining = lineLimit;
  const preview: MarkdownBlock[] = [];
  blocks.forEach((block) => {
    if (remaining <= 0) return;
    if (block.type === "table") {
      const hasHeader = block.header.length > 0;
      const headerCost = hasHeader ? 1 : 0;
      if (remaining <= 0 || (hasHeader && remaining < headerCost)) return;
      const available = Math.max(remaining - headerCost, 0);
      const rows = available > 0 ? block.rows.slice(0, available) : [];
      if (!hasHeader && rows.length === 0) return;
      preview.push({ type: "table", header: block.header, rows });
      remaining -= headerCost + rows.length;
      return;
    }
    const lines = block.lines.filter((line) => line.trim().length > 0);
    const slice = lines.slice(0, remaining);
    if (!slice.length) return;
    preview.push({ type: "text", lines: slice });
    remaining -= slice.length;
  });
  return preview;
};

export const countMarkdownLines = (blocks: MarkdownBlock[]) =>
  blocks.reduce((sum, block) => {
    if (block.type === "table") {
      return sum + (block.header.length ? 1 : 0) + block.rows.length;
    }
    return sum + block.lines.length;
  }, 0);

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

export const extractTableFromPage = (page?: OcrPreviewPage | null) => {
  const structuredTables = Array.isArray(page?.tables) ? page.tables : [];
  for (const table of structuredTables) {
    const rows = Array.isArray(table?.rows)
      ? table.rows
          .filter((row): row is string[] => Array.isArray(row))
          .map((row) => row.map((cell) => String(cell ?? "").trim()))
      : [];
    if (rows.length >= 2) {
      let header = rows[0];
      let dataRows = rows.slice(1);
      if (header.length && dataRows.length && isSubheaderRow(dataRows[0])) {
        header = mergeHeaderRows(header, dataRows[0]);
        dataRows = dataRows.slice(1);
      }
      if (dataRows.length) {
        return { header, rows: dataRows };
      }
    }
  }
  if (!page?.markdown_text) return null;
  const blocks = parseMarkdownBlocks(page.markdown_text);
  const table = blocks.find(
    (block) => block.type === "table" && block.rows.length > 0,
  ) as MarkdownBlock | undefined;
  if (table && table.type === "table") {
    let header = table.header;
    let rows = table.rows;
    if (header.length && rows.length && isSubheaderRow(rows[0])) {
      header = mergeHeaderRows(header, rows[0]);
      rows = rows.slice(1);
    }
    return { header, rows };
  }
  return null;
};

export const extractFirstTable = (pages: OcrPreviewPage[]) => {
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

export const buildMarkdownTable = (header: string[], rows: string[][]) => {
  const safeHeader = header.length ? header : rows[0]?.map((_, idx) => `col${idx + 1}`) || [];
  const headerLine = `| ${safeHeader.join(" | ")} |`;
  const separatorLine = `| ${safeHeader.map(() => "---").join(" | ")} |`;
  const bodyLines = rows.map((row) => `| ${row.join(" | ")} |`);
  return [headerLine, separatorLine, ...bodyLines].join("\n");
};
