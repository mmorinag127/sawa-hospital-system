// Non-secret, per-document dataset identity. Credentials remain server-owned.
export const DATASET_KEY = "sawa_stg_dataset";
export const DATASET_GENERATION = "sawa_stg_dataset_generation";
export type DocumentDataset = { dataset: string } | { invalid: true };
let documentDataset: DocumentDataset | undefined;

const validDataset = (value: string) => value === "test" || /^replay-[a-z0-9][a-z0-9-]{0,47}$/.test(value);

// Only the document request's HttpOnly cookie supplies the initial identity.
// Browser navigation and selector bootstrap must never retarget an existing form.
export function datasetFromCookie(cookie: string | undefined): DocumentDataset {
  const values = (cookie || "").split(";").map(value => value.trim()).filter(value => value.startsWith(`${DATASET_KEY}=`));
  if (!values.length) return { dataset: "test" };
  if (values.length !== 1) return { invalid: true };
  try {
    const dataset = decodeURIComponent(values[0].slice(DATASET_KEY.length + 1));
    return validDataset(dataset) ? { dataset } : { invalid: true };
  } catch { return { invalid: true }; }
}

export function initializeDocumentDataset(value: DocumentDataset | undefined): void {
  if (typeof window !== "undefined" && documentDataset === undefined && value !== undefined) documentDataset = value;
}

export function documentDatasetMatches(dataset: string): boolean {
  return documentDataset !== undefined && "dataset" in documentDataset && documentDataset.dataset === dataset;
}

export const datasetHeaders = (): Record<string, string> => {
  if (typeof window === "undefined") return {};
  if (!documentDataset || !("dataset" in documentDataset) || !validDataset(documentDataset.dataset)) throw new Error("dataset_unconfirmed");
  return { "X-Sawa-Dataset": documentDataset.dataset };
};

export function datasetStorageKey(key: string): string {
  if (typeof window === "undefined") throw new Error("dataset_unconfirmed");
  const dataset = datasetHeaders()["X-Sawa-Dataset"];
  return dataset === "test" ? key : `${key}:${dataset}`;
}

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
