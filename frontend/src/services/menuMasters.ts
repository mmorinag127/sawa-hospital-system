import type { components } from "../generated/menu-master-api";

export type MenuMasterResponse = components["schemas"]["MenuMasterResponse"];
export type MenuMaster = Omit<MenuMasterResponse, "condiments"> & {
  // The API faithfully returns legacy arbitrary JSON lists. This is narrowed only
  // after parseMenuMaster validates the screen's string-list requirement.
  condiments: string[];
};

export type MenuMasterDraft = {
  name: string;
  unit_type: string | null;
  qty_per_serving: number | null;
  bag_max_qty: number | null;
  bag_max_unit: string | null;
  temp_type: string | null;
  daypart: string | null;
  category: string | null;
  condiments: string[];
};

export type MenuMasterListResponse = components["schemas"]["MenuMasterListResponse"];
export type MenuMasterList = Omit<MenuMasterListResponse, "items"> & {
  items: MenuMaster[];
};

export const MENU_MASTER_PAGE_SIZE = 50;
export const MENU_MASTER_PAGE_SIZES = [25, 50, 100] as const;

export const menuMasterErrorMessage = (error: any) => {
  if (typeof error?.message === "string" && /メニュー名|数量|保存済み版/.test(error.message)) return error.message;
  switch (error?.response?.status) {
    case 401: return "ログイン期限が切れました。ログインし直してください。";
    case 403: return "この操作を行う権限がありません。";
    case 404: return "対象が存在しません。一覧を確認してください。";
    case 409: return "新しい内容があります。再読込してください。";
    case 422: return "入力内容を確認してください。";
    default: return "通信または保存に失敗しました。";
  }
};

const nullableText = (value: unknown): string | null => {
  const text = String(value ?? "").trim();
  return text || null;
};

export const parseCondiments = (value: string): string[] =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

export const normalizeMenuMasterDraft = (
  draft: Partial<MenuMasterDraft>,
): MenuMasterDraft => {
  const name = String(draft.name ?? "").trim();
  if (!name) throw new Error("メニュー名は必須です。");

  const numberOrNull = (value: unknown, field: string): number | null => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(parsed))
      throw new Error(`${field}は数値で入力してください。`);
    return parsed;
  };

  return {
    name,
    unit_type: nullableText(draft.unit_type),
    qty_per_serving: numberOrNull(draft.qty_per_serving, "1人前数量"),
    bag_max_qty: numberOrNull(draft.bag_max_qty, "袋上限数量"),
    bag_max_unit: nullableText(draft.bag_max_unit),
    temp_type: nullableText(draft.temp_type),
    daypart: nullableText(draft.daypart),
    category: nullableText(draft.category),
    condiments: Array.isArray(draft.condiments)
      ? draft.condiments.map((item) => String(item).trim()).filter(Boolean)
      : [],
  };
};

export const createMenuMasterPayload = (draft: Partial<MenuMasterDraft>) =>
  normalizeMenuMasterDraft(draft);

export const updateMenuMasterPayload = (
  record: MenuMaster,
  draft: Partial<MenuMasterDraft>,
) => {
  if (!Number.isInteger(record.revision) || record.revision < 1)
    throw new Error("保存済み版が不正です。再読込してください。");
  return { ...normalizeMenuMasterDraft(draft), revision: record.revision };
};

export const listMenuMasterParams = (
  query: string,
  page: number,
  pageSize = MENU_MASTER_PAGE_SIZE,
) => {
  if (!Number.isInteger(page) || page < 0)
    throw new Error("ページ番号が不正です。");
  if (!Number.isInteger(pageSize) || pageSize < 1)
    throw new Error("ページサイズが不正です。");
  return {
    q: query.trim() || undefined,
    offset: page * pageSize,
    limit: pageSize,
    sort: "name",
    order: "asc",
  };
};

/** Retains unrelated route query values while changing only this screen's search term. */
export const menuMasterSearchUrl = (asPath: string, query: string): string => {
  const [pathWithQuery, hash = ""] = asPath.split("#", 2);
  const [pathname, rawQuery = ""] = pathWithQuery.split("?", 2);
  const params = new URLSearchParams(rawQuery);
  const value = query.trim();
  if (value) params.set("q", value);
  else params.delete("q");
  const serialized = params.toString();
  return `${pathname}${serialized ? `?${serialized}` : ""}${hash ? `#${hash}` : ""}`;
};

export const parseMenuMasterUrl = (asPath: string) => {
  const [pathWithQuery] = asPath.split("#", 1);
  const [, rawQuery = ""] = pathWithQuery.split("?", 2);
  const params = new URLSearchParams(rawQuery);
  const parseInteger = (name: string, fallback: number) => {
    const raw = params.get(name);
    if (raw === null) return fallback;
    if (!/^\d+$/.test(raw))
      throw new Error("一覧のページ指定を確認してください。");
    return Number(raw);
  };
  const page = parseInteger("page", 0);
  const pageSize = parseInteger("pageSize", MENU_MASTER_PAGE_SIZE);
  if (
    !Number.isSafeInteger(page) ||
    page < 0 ||
    !MENU_MASTER_PAGE_SIZES.includes(pageSize as 25 | 50 | 100)
  ) {
    throw new Error("一覧のページ指定を確認してください。");
  }
  return { q: params.get("q") || "", page, pageSize };
};

export const menuMasterListUrl = (
  asPath: string,
  state: { q: string; page: number; pageSize: number },
) => {
  const [pathWithQuery, hash = ""] = asPath.split("#", 2);
  const [pathname, rawQuery = ""] = pathWithQuery.split("?", 2);
  const params = new URLSearchParams(rawQuery);
  state.q.trim() ? params.set("q", state.q.trim()) : params.delete("q");
  state.page ? params.set("page", String(state.page)) : params.delete("page");
  state.pageSize === MENU_MASTER_PAGE_SIZE
    ? params.delete("pageSize")
    : params.set("pageSize", String(state.pageSize));
  const serialized = params.toString();
  return `${pathname}${serialized ? `?${serialized}` : ""}${hash ? `#${hash}` : ""}`;
};

export const parseMenuMasterList = (value: unknown): MenuMasterList => {
  const source = value as Partial<MenuMasterList>;
  if (
    !source ||
    !Array.isArray(source.items) ||
    !Number.isInteger(source.total) ||
    source.total < 0 ||
    !Number.isInteger(source.offset) ||
    !Number.isInteger(source.limit) || source.offset < 0 || source.limit < 1
  ) {
    throw new Error("メニューマスター一覧の応答を確認できませんでした。");
  }
  return {
    items: source.items.map(parseMenuMaster),
    total: source.total,
    offset: source.offset,
    limit: source.limit,
  };
};

export const parseMenuMaster = (value: unknown): MenuMaster => {
  const item = value as Partial<MenuMaster>;
  const nullableTextFields = [
    item?.unit_type,
    item?.bag_max_unit,
    item?.temp_type,
    item?.daypart,
    item?.category,
  ];
  const nullableNumbers = [item?.qty_per_serving, item?.bag_max_qty];
  if (
    !item ||
    typeof item.id !== "string" ||
    !item.id ||
    typeof item.name !== "string" ||
    !Number.isInteger(item.revision) ||
    item.revision < 1 ||
    !nullableTextFields.every(
      (field) => field === null || typeof field === "string",
    ) ||
    !nullableNumbers.every(
      (field) =>
        field === null || (typeof field === "number" && Number.isFinite(field)),
    ) ||
    !Array.isArray(item.condiments) ||
    !item.condiments.every((item) => typeof item === "string")
  ) {
    throw new Error("保存結果を確認できませんでした。再読込してください。");
  }
  return item as MenuMaster;
};
