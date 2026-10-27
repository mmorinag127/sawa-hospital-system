export type MenuInfo = {
  status: "登録済み" | "未登録" | "確認中" | "取得失敗";
  itemCount?: number;
  filename?: string | null;
  displayName?: string | null;
};

export function classifyDashboardMenuRead(month: string, response: { status?: unknown; data?: any } | undefined): MenuInfo {
  if (!response) return { status: "取得失敗" };
  const { status, data } = response;
  if (status === 404 && data?.detail === "not found") return { status: "未登録" };
  if (status !== 200 || !data || data.menu?.id !== month || !Array.isArray(data.items) ||
      !Array.isArray(data.entries) ||
      ![data.menu.filename, data.menu.display_name].every(value => value == null || typeof value === "string")) {
    return { status: "取得失敗" };
  }
  return {
    status: "登録済み", itemCount: data.items.length,
    filename: data.menu.filename || null,
    displayName: data.menu.display_name || data.menu.filename || null,
  };
}

export function classifyDashboardMenuSummary(month: string, value: any): MenuInfo {
  if (!value || value.month_id !== month) return {status: "取得失敗"};
  if (value.status === "missing" && value.menu === null && value.item_count === 0) return {status: "未登録"};
  if (value.status !== "registered" || value.menu?.id !== month || !Number.isInteger(value.item_count)
      || value.item_count < 0 || ![value.menu.filename, value.menu.display_name].every(item => item == null || typeof item === "string")) {
    return {status: "取得失敗"};
  }
  return {status: "登録済み", itemCount: value.item_count,
    filename: value.menu.filename || null, displayName: value.menu.display_name || value.menu.filename || null};
}
