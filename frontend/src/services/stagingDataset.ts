// Non-secret, per-document dataset identity. Credentials remain server-owned.
export const DATASET_KEY = "sawa_stg_dataset";
export const DATASET_GENERATION = "sawa_stg_dataset_generation";
export const datasetHeaders = (): Record<string, string> => {
  if (typeof window === "undefined") return {};
  const value = window.sessionStorage.getItem(DATASET_KEY);
  return value ? { "X-Sawa-Dataset": value } : {};
};

export function forwardDatasetHeaders(headers: Record<string, string | string[] | undefined>, cookies: Partial<Record<string, string>>, method: string): Record<string, string> {
  const explicit = headers["x-sawa-dataset"];
  const cookie = cookies[DATASET_KEY];
  if (Array.isArray(explicit)) throw new Error("dataset_invalid");
  const selected = explicit ?? cookie;
  if (selected === undefined) return {};
  if (selected !== "test" && !/^replay-[a-z0-9][a-z0-9-]{0,47}$/.test(selected)) throw new Error("dataset_invalid");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && explicit && cookie && explicit !== cookie) throw new Error("dataset_changed");
  return { "X-Sawa-Dataset": selected };
}
