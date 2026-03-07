import { apiClient } from "./apiClient";

export type FacilityNameMap = Record<string, string>;

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
  const facilities = Array.isArray(res.data?.facilities) ? res.data.facilities : [];
  const map: FacilityNameMap = {};
  facilities.forEach((fac: any) => {
    const id = String(fac?.id || "").trim();
    if (!id) return;
    const name = String(fac?.name || "").trim();
    map[id] = name || id;
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

