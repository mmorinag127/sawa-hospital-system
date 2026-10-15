type Context = {facility_id?: string | null; week_start?: string | null; week_end?: string | null; template_version_id?: string | null};
export function facilityReadSelection(orderId: string, facilityId: string, start: string, end: string, saved: Context | null, confirmed: boolean) {
  if (!facilityId || !/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || end < start) {
    throw new Error("施設と対象週を明示的に選択してください");
  }
  const selected = confirmed && saved?.facility_id === facilityId && saved?.week_start === start && saved?.week_end === end;
  if (selected && (!orderId || !saved?.template_version_id)) throw new Error("保存済み注文の施設版がありません");
  return {orderId, facilityId, start, end, version: selected ? saved!.template_version_id : null,
    url: selected ? `/orders/${encodeURIComponent(orderId)}/workflow-v2/facility-config`
      : `/facilities/${encodeURIComponent(facilityId)}/resolved?effective_date=${encodeURIComponent(start)}`};
}
export function facilityReadConfig(selection: ReturnType<typeof facilityReadSelection>, data: any): Record<string, any> {
  if (data?.source !== "canonical_json" || data?.facility_id !== selection.facilityId
      || (selection.version ? data.order_id !== selection.orderId || data.template_version_id !== selection.version
        || data.week_start !== selection.start || data.week_end !== selection.end
        : data.effective_date !== selection.start || !data.version?.version_id)
      || !data.config || typeof data.config !== "object" || Array.isArray(data.config)) {
    throw new Error("施設設定の注文・施設・週・版が一致しません");
  }
  return data.config;
}
