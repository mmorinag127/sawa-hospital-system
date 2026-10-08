import { apiClient } from "./apiClient";
import type { components } from "../generated/facility-version-api";

export type FacilityNameMap = Record<string, string>;

export type CanonicalFacilityVersion = components["schemas"]["FacilityVersion"];

export type CanonicalFacilityVersions = components["schemas"]["FacilityVersionsResponse"]
  & Partial<Pick<components["schemas"]["FacilityVersionSaveResponse"], "version" | "updated" | "projection">>;

export type CanonicalDatedFacility = components["schemas"]["DatedFacilityResponse"];

export const requireDatedFacility = (data: CanonicalDatedFacility, target: string, effectiveDate: string): string => {
  const revision = requireCanonicalRevision(data);
  if (data.facility_id !== target || data.effective_date !== effectiveDate || !data.version?.version_id ||
      !data.facility || data.facility.facility_id !== target ||
      JSON.stringify(data.version.facility) !== JSON.stringify(data.facility) || JSON.stringify(data.config) !== JSON.stringify(data.facility)) {
    throw new Error("指定日の完全施設版と正本 revision が一致しません。");
  }
  return revision;
};

export const requireFacilityVersions = (data: CanonicalFacilityVersions, target: string): string => {
  const revision = requireCanonicalRevision(data);
  if (data.facility_id !== target || !Array.isArray(data.versions) || !data.reference || data.reference.facility_id !== target ||
      data.reference_status !== "unversioned_reference_only" ||
      data.versions.some((version) => !version || typeof version.version_id !== "string" || !version.version_id ||
        typeof version.valid_from !== "string" || !("valid_to" in version) ||
        (version.valid_to !== null && typeof version.valid_to !== "string") ||
        !version.facility || version.facility.facility_id !== target) ||
      new Set(data.versions.map((version) => version.version_id)).size !== data.versions.length) {
    throw new Error("施設版と正本 revision の組を確認できません。明示的に再読込してください。");
  }
  if (data.version && (data.versions.filter((version) => version.version_id === data.version!.version_id).length !== 1 ||
      JSON.stringify(data.versions.find((version) => version.version_id === data.version!.version_id)) !== JSON.stringify(data.version))) {
    throw new Error("own 保存版と同一 revision の版一覧が一致しません。自動再保存せず明示的に再読込してください。");
  }
  return revision;
};

export type CanonicalFacility = {
  id: string;
  name: string;
  areas: unknown[] | null;
};

export type CanonicalFacilityResponse = {
  facility: CanonicalFacility;
  config: Record<string, unknown>;
  resolved_config: Record<string, unknown>;
  revision: string;
  source: "canonical_json";
  order_form_patterns: { pattern_id: string; label?: string; description?: string }[];
  updated?: boolean;
  validation?: { errors: string[]; warnings: string[] };
  projection?: { status: "synced" | "reconcile_required"; revision: string };
  event?: { status: "recorded" | "record_failed" };
};

export const requireCanonicalRevision = (data: { revision?: unknown; source?: unknown }): string => {
  if (!data || data.source !== "canonical_json" || typeof data.revision !== "string" || !data.revision) {
    throw new Error("JSON 正本の revision がありません。保存せず明示的に再読込してください。");
  }
  return data.revision;
};

export const canonicalErrorText = (error: any, fallback: string): string => {
  const detail = error?.response?.data?.detail;
  if (detail?.saved === true || detail?.config_saved === true) {
    return "JSON 正本は書込済みですが、保存結果または永続化を確認できません。自動再保存せず明示的に再読込してください。";
  }
  if (detail?.outcome === "unknown" || detail?.config_saved === "unknown") {
    return "JSON 書込結果が未確認です。自動再保存せず明示的に再読込してください。";
  }
  if (error?.response?.status === 409 || error?.response?.status === 428) {
    return "JSON 正本の編集 revision が欠落または変更されています。入力を確認し、明示的に再読込してください。";
  }
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  const message = detail?.message || detail?.error;
  if (!error?.response && error instanceof Error && error.message.trim()) return error.message;
  return typeof message === "string" && message.trim() ? message.trim() : fallback;
};

export const requireFacilityConfigObject = (config: unknown): Record<string, unknown> => {
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new Error("config must be an object");
  }
  return Object.fromEntries(Object.entries(config));
};

export const requireFaxTemplateOverride = (input: unknown): Record<string, unknown> | null | undefined => {
  const config = requireFacilityConfigObject(input);
  const value = config.fax_template_override;
  if (value === undefined) return undefined;
  if (value === null) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error("fax_template_override must be an object");
  }
  return Object.fromEntries(Object.entries(value));
};

export const requireFacilitySnapshot = (data: CanonicalFacilityResponse, target: string): string => {
  const revision = requireCanonicalRevision(data);
  if (data.facility?.id !== target || typeof data.facility.name !== "string" || (data.facility.areas !== null && !Array.isArray(data.facility.areas)) ||
      !data.config || typeof data.config !== "object" || Array.isArray(data.config) ||
      !data.resolved_config || typeof data.resolved_config !== "object" || Array.isArray(data.resolved_config) ||
      !Array.isArray(data.order_form_patterns)) {
    throw new Error("同一 revision の施設・設定 snapshot がありません。保存せず再読込してください。");
  }
  requireFaxTemplateOverride(data.config);
  return revision;
};

export const canonicalSaveNotice = (data: {
  revision?: unknown;
  source?: unknown;
  updated?: boolean;
  projection?: { status: string; revision: string };
  event?: { status: string };
}): string => {
  const revision = requireCanonicalRevision(data);
  const projection = data.projection;
  if (data.updated !== true || !projection || projection.revision !== revision ||
      !["synced", "reconcile_required"].includes(projection.status)) {
    throw new Error("保存結果を確認できません。自動再保存せず明示的に再読込してください。");
  }
  return `${projection.status === "reconcile_required" ? " JSON 正本は保存済みですが、DB projection の再同期が必要です。" : ""}${data.event?.status === "record_failed" ? " 保存済みですが、操作イベントの記録に失敗しました。" : ""}`;
};

export type FacilityCandidate = {
  facility_id: string;
  facility_name?: string | null;
  score?: number | null;
  reason?: string | null;
  auto?: boolean | null;
};

export type FacilityHint = FacilityCandidate & {
  order_id: string;
};

export const fetchFacilityNameMap = async (): Promise<FacilityNameMap> => {
  const res = await apiClient.get("/facilities");
  requireCanonicalRevision(res.data);
  if (!Array.isArray(res.data?.facilities)) throw new Error("JSON 正本の施設一覧がありません。");
  const facilities = res.data.facilities;
  const map: FacilityNameMap = {};
  facilities.forEach((fac: any) => {
    const id = String(fac?.id || "").trim();
    const name = String(fac?.name || "").trim();
    if (!id || !name || Object.prototype.hasOwnProperty.call(map, id)) throw new Error("JSON 正本の施設ID・施設名が欠落または重複しています。");
    map[id] = name;
  });
  return map;
};

export const fetchOrderFacilityCandidates = async (
  orderId: string
): Promise<FacilityCandidate[]> => {
  const res = await apiClient.get(`/orders/${orderId}/ocr-output`);
  const raw = res.data?.facility_candidates;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item: any) => ({
      facility_id: String(item?.facility_id || ""),
      facility_name: item?.facility_name ?? null,
      score: item?.score ?? null,
      reason: item?.reason ?? null,
      auto: item?.auto ?? null,
    }))
    .filter((item: FacilityCandidate) => Boolean(item.facility_id));
};

export const pickBestFacilityCandidate = (
  candidates: FacilityCandidate[]
): FacilityCandidate | null => {
  if (!Array.isArray(candidates) || candidates.length === 0) return null;
  return candidates[0] || null;
};
