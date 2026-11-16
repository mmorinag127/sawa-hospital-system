export type FaxUploadPreview = {
  facility_id: string;
  week_hint: string;
  date_from: string;
  date_to: string;
  calendar_from: string;
  calendar_to: string;
  cross_month: boolean;
  requires_review: boolean;
  files: Array<{file_index: number; filename: string; sha256: string;
    pages: Array<{page_number: number; sha256: string; image_url: string}>}>;
};

export const faxPageKey = (fileIndex: number, page: number) => `${fileIndex}:${page}`;

export function faxWeekCrossesMonth(start?: string, end?: string) {
  if (!start || !end) return false;
  const first = new Date(`${start}T12:00:00Z`);
  if (!Number.isFinite(first.getTime())) return false;
  first.setUTCDate(first.getUTCDate() - first.getUTCDay());
  const last = new Date(first);
  last.setUTCDate(first.getUTCDate() + 6);
  return first.toISOString().slice(0,7) !== last.toISOString().slice(0,7);
}

export function faxPageSelection(preview: FaxUploadPreview, choices: Record<string, boolean>) {
  return {version: 1, facility_id: preview.facility_id, week_hint: preview.week_hint,
    files: preview.files.map(file => ({file_index: file.file_index, sha256: file.sha256,
      pages: file.pages.map(page => ({page_number: page.page_number, sha256: page.sha256,
        include: choices[faxPageKey(file.file_index, page.page_number)] === true}))}))};
}

export function faxUploadError(detail: unknown) {
  const messages: Record<string, string> = {
    fax_page_selection_required: "登録するページを確認してください。",
    fax_page_selection_context_changed: "施設・対象期間が変わりました。ページを再確認してください。",
    fax_page_selection_original_changed: "原本PDFが変わりました。ページを再確認してください。",
    fax_target_page_required: "対象期間が記入されたページを選択してください。",
    fax_target_page_ambiguous: "同じ施設・期間に異なる原本が選択されています。登録するページを確認してください。",
    fax_page_selection_force_not_allowed: "月別の受付は再登録で上書きできません。既存注文から原本を確認してください。",
    fax_existing_order_requires_review: "この施設・期間には既存注文があります。原本を自動で差し替えず、既存注文の確認画面から確認してください。",
  };
  return typeof detail === "string" ? messages[detail] || detail : "PDFの確認に失敗しました。";
}
