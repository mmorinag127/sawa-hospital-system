import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ChangeEvent } from "react";
import { useFormContext, type FieldValues } from "react-hook-form";
import { Button, Checkbox, Input, MenuItem, NativeSelect } from "@mui/material";
import { EditForm, FormTextField, SawaProvider } from "@sawa/ui";
import TopNav from "../../components/TopNav";
import { apiClient, hasActiveSessionAuthHeader } from "../../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../../services/browserSession";
import { useH1LeaveGuard } from "../../hooks/useH1LeaveGuard";
import { canonicalErrorText, canonicalSaveNotice, requireFacilitySnapshot, type CanonicalFacility, type CanonicalFacilityResponse } from "../../services/facilityData";
import { requireFacilityVersions, requireDatedFacility, type CanonicalDatedFacility, type CanonicalFacilityVersions, type CanonicalFacilityVersion } from "../../services/facilityData";

type Facility = CanonicalFacility;

type ValidationResult = {
  errors: string[];
  warnings: string[];
};

type FacilityResponse = CanonicalFacilityResponse;

type FacilityDraft = {
  name: string;
};

type ConfigDraft = {
  order_form_pattern_id: string;
  menu_override_tags: string;
  main_ocr_provider: string;
  openai_ocr_enabled: string;
  openai_ocr_model: string;
  openai_ocr_prompt: string;
  openai_ocr_fallback_provider: string;
  gemini_ocr_enabled: string;
  gemini_ocr_model: string;
  gemini_ocr_prompt: string;
  gemini_ocr_fallback_provider: string;
  hakodate_header_rows: string;
  hakodate_ocr_resolution: string;
  hakodate_min_edge_margin_ratio: string;
  large_cell_mode: string;
};

type OrderFormPattern = {
  pattern_id: string;
  label?: string;
  description?: string;
};

function DraftBridge<T extends FieldValues>({ onValuesChange }: { onValuesChange: (values: T) => void }) {
  const { watch, getValues } = useFormContext<T>();
  useEffect(() => {
    onValuesChange(getValues());
    const subscription = watch((values) => onValuesChange(values as T));
    return () => subscription.unsubscribe();
  }, [watch, getValues, onValuesChange]);
  return null;
}

const prettyJson = (value: unknown) => JSON.stringify(value === undefined ? {} : value, null, 2);

const parseJson = (text: string) => {
  try {
    return { value: JSON.parse(text), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON";
    return { value: null, error: message };
  }
};

const errorDetailText = (err: any, fallback: string) => {
  return canonicalErrorText(err, fallback);
};

const parseStringList = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item || "").trim())
      .filter((item) => Boolean(item));
  }
  if (typeof value === "string") {
    return value
      .split(/[,\n\r\t ]+/)
      .map((item) => item.trim())
      .filter((item) => Boolean(item));
  }
  return [];
};

const parseConfigString = (value: unknown, defaultValue = ""): string => {
  return typeof value === "string" ? value : defaultValue;
};

const parseConfigNumberText = (value: unknown, defaultValue = ""): string => {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value.trim()) return value.trim();
  return defaultValue;
};

const optionalBooleanText = (value: unknown) => (typeof value === "boolean" ? String(value) : "");

const configDraft = (config: Record<string, unknown>): ConfigDraft => ({
  order_form_pattern_id: parseConfigString(config.order_form_pattern_id),
  menu_override_tags: parseStringList(config.menu_override_tags).join(","),
  main_ocr_provider: parseConfigString(config.main_ocr_provider),
  openai_ocr_enabled: optionalBooleanText(config.openai_ocr_enabled),
  openai_ocr_model: parseConfigString(config.openai_ocr_model),
  openai_ocr_prompt: parseConfigString(config.openai_ocr_prompt),
  openai_ocr_fallback_provider: parseConfigString(config.openai_ocr_fallback_provider),
  gemini_ocr_enabled: optionalBooleanText(config.gemini_ocr_enabled),
  gemini_ocr_model: parseConfigString(config.gemini_ocr_model),
  gemini_ocr_prompt: parseConfigString(config.gemini_ocr_prompt),
  gemini_ocr_fallback_provider: parseConfigString(config.gemini_ocr_fallback_provider),
  hakodate_header_rows: parseConfigNumberText(config.hakodate_header_rows),
  hakodate_ocr_resolution: parseConfigNumberText(config.hakodate_ocr_resolution),
  hakodate_min_edge_margin_ratio: parseConfigNumberText(config.hakodate_min_edge_margin_ratio),
  large_cell_mode: optionalBooleanText(config.large_cell_mode),
});

const setOptionalString = (target: Record<string, unknown>, key: string, value: string) => {
  if (value.trim()) target[key] = value;
  else delete target[key];
};

const setOptionalNumber = (target: Record<string, unknown>, key: string, value: string, minimum: number) => {
  if (!value.trim()) {
    delete target[key];
    return;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < minimum) throw new Error(`${key} の値が不正です。`);
  target[key] = parsed;
};

export const mergeConfigDraft = (base: Record<string, unknown>, values: ConfigDraft, original = configDraft(base)) => {
  const next = { ...base };
  const changes = new Set((Object.keys(values) as (keyof ConfigDraft)[]).filter((key) => values[key] !== original[key]));
  // Serialize only changed form fields; JSON edits and untouched null/zero keys remain exact.
  const strings = ["order_form_pattern_id", "main_ocr_provider", "openai_ocr_model", "openai_ocr_prompt",
    "openai_ocr_fallback_provider", "gemini_ocr_model", "gemini_ocr_prompt", "gemini_ocr_fallback_provider"] as const;
  strings.forEach((key) => { if (changes.has(key)) setOptionalString(next, key, values[key]); });
  ["openai_ocr_enabled", "gemini_ocr_enabled", "large_cell_mode"].forEach((key) => {
    const value = values[key as "openai_ocr_enabled" | "gemini_ocr_enabled" | "large_cell_mode"];
    if (!changes.has(key as keyof ConfigDraft)) return;
    if (value === "true" || value === "false") next[key] = value === "true";
    else delete next[key];
  });
  if (changes.has("hakodate_header_rows")) setOptionalNumber(next, "hakodate_header_rows", values.hakodate_header_rows, 0);
  if (changes.has("hakodate_ocr_resolution")) setOptionalNumber(next, "hakodate_ocr_resolution", values.hakodate_ocr_resolution, Number.MIN_VALUE);
  if (changes.has("hakodate_min_edge_margin_ratio")) setOptionalNumber(next, "hakodate_min_edge_margin_ratio", values.hakodate_min_edge_margin_ratio, 0);
  if (changes.has("menu_override_tags")) {
    const tags = values.menu_override_tags.split(",").map((item) => item.trim()).filter(Boolean);
    if (tags.length) next.menu_override_tags = tags;
    else delete next.menu_override_tags;
  }
  return next;
};

const validateConfigSave = (config: Record<string, unknown>, values: ConfigDraft) => {
  for (const key of ["openai_ocr_fallback_provider", "gemini_ocr_fallback_provider"] as const) {
    const value = values[key].trim();
    if (value && value.toLowerCase() !== "none") throw new Error(`${key} は none 以外を保存できません。`);
    const raw = config[key];
    if (typeof raw === "string" && raw.trim() && raw.trim().toLowerCase() !== "none" && raw !== values[key]) {
      throw new Error(`${key} に禁止値が登録されています。JSONで明示的に none へ修正してください。`);
    }
  }
};

function FacilityConfigScreen({ authenticated }: { authenticated: boolean }) {
  const router = useRouter();
  const { id } = router.query;
  const [facility, setFacility] = useState<Facility | null>(null);
  const [areasText, setAreasText] = useState("[]");
  const [configText, setConfigText] = useState("{}");
  const [resolvedText, setResolvedText] = useState("{}");
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [orderFormPatterns, setOrderFormPatterns] = useState<OrderFormPattern[]>([]);
  const [orderFormPatternsError, setOrderFormPatternsError] = useState("");
  const [sourceWorkbookMonthId, setSourceWorkbookMonthId] = useState<string>("");
  const [sourceWorkbookUploading, setSourceWorkbookUploading] = useState<boolean>(false);
  const loadGeneration = useRef(0);
  const saveGeneration = useRef(0);
  const commandGeneration = useRef(0);
  const uploadGeneration = useRef(0);
  const inputGeneration = useRef(0);
  const baseRevision = useRef<string | null>(null);
  const targetRef = useRef(id);
  targetRef.current = id;
  const dirtyRef = useRef(false);
  const [facilityFormDirty, setFacilityFormDirty] = useState(false);
  const [configFormDirty, setConfigFormDirty] = useState(false);
  const [specialDirty, setSpecialDirty] = useState(false);
  const [editorEpoch, setEditorEpoch] = useState(0);
  const areaJsonDirty = useRef(false);
  const configJsonDirty = useRef(false);
  const workbookDirty = useRef(false);
  const facilityDraftRef = useRef<FacilityDraft | null>(null);
  const [configRecord, setConfigRecord] = useState<Record<string, unknown>>({});
  const configDraftRef = useRef<ConfigDraft | null>(null);
  const [versionSnapshot, setVersionSnapshot] = useState<CanonicalFacilityVersions | null>(null);
  const versionSnapshotRef = useRef<CanonicalFacilityVersions | null>(null);
  const [versionId, setVersionId] = useState("");
  const [versionFrom, setVersionFrom] = useState("");
  const [versionTo, setVersionTo] = useState("");
  const [versionOpenEnd, setVersionOpenEnd] = useState(false);
  const [versionText, setVersionText] = useState("");
  const [versionExisting, setVersionExisting] = useState(false);
  const versionOriginal = useRef<CanonicalFacilityVersion | null>(null);
  const [versionDirty, setVersionDirty] = useState(false);
  const [versionBusy, setVersionBusy] = useState(false);
  const [versionBlocked, setVersionBlocked] = useState(false);
  const versionGeneration = useRef(0);
  const previewGeneration = useRef(0);
  const versionBusyOwner = useRef(0);
  const [versionMessage, setVersionMessage] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [datedResult, setDatedResult] = useState("");
  const clearVersionEditor = () => {
    versionGeneration.current += 1;
    previewGeneration.current += 1;
    versionSnapshotRef.current = null;
    setVersionSnapshot(null);
    versionOriginal.current = null;
    setVersionExisting(false);
    setVersionId(""); setVersionFrom(""); setVersionTo(""); setVersionText("");
    setVersionOpenEnd(false);
    setVersionDirty(false); setVersionMessage(""); setDatedResult("");
    setVersionBlocked(false); setVersionBusy(false); versionBusyOwner.current = 0;
  };
  const syncFacilityDraft = useCallback((values: FacilityDraft) => {
    if (JSON.stringify(facilityDraftRef.current) !== JSON.stringify(values)) inputGeneration.current += 1;
    facilityDraftRef.current = values;
  }, []);
  const syncConfigDraft = useCallback((values: ConfigDraft) => {
    if (JSON.stringify(configDraftRef.current) !== JSON.stringify(values)) inputGeneration.current += 1;
    configDraftRef.current = values;
  }, []);
  const hasUnsavedChanges = facilityFormDirty || configFormDirty || specialDirty || versionDirty;
  dirtyRef.current = hasUnsavedChanges;
  const facilityPath = !Array.isArray(id) && id ? `/facilities/${id}` : "/facilities";
  useH1LeaveGuard(hasUnsavedChanges, [facilityPath]);

  const requireEditingRevision = () => {
    if (!baseRevision.current) throw new Error("JSON 正本の編集 revision がありません。再読込してください。");
    return baseRevision.current;
  };

  const markJsonDirty = (kind: "areas" | "config") => {
    inputGeneration.current += 1;
    if (kind === "areas") areaJsonDirty.current = true;
    else configJsonDirty.current = true;
    setSpecialDirty(true);
  };

  const captureCommand = (requireBase = true) => {
    if (!facility || facility.id !== targetRef.current) throw new Error("施設の選択が変わっています。再読込してください。");
    const generation = ++commandGeneration.current;
    const target = targetRef.current;
    const owner = saveGeneration.current;
    const input = inputGeneration.current;
    const expectedRevision = requireBase ? requireEditingRevision() : "";
    const isCurrent = () => generation === commandGeneration.current && target === targetRef.current &&
      owner === saveGeneration.current && input === inputGeneration.current;
    return { generation, expectedRevision, isCurrent };
  };

  const editVersion = (setter: (value: string) => void, value: string) => {
    versionGeneration.current += 1;
    inputGeneration.current += 1;
    setVersionDirty(true);
    setter(value);
  };
  const captureVersionOwnership = () => {
    const target = targetRef.current;
    const generation = versionGeneration.current;
    const input = inputGeneration.current;
    const command = commandGeneration.current;
    const owner = saveGeneration.current;
    const session = browserSessionKey();
    return () => target === targetRef.current && generation === versionGeneration.current &&
      input === inputGeneration.current && command === commandGeneration.current &&
      owner === saveGeneration.current && session === browserSessionKey() && hasActiveSessionAuthHeader();
  };
  const loadVersions = async () => {
    if (!facility || versionBusyOwner.current !== 0 || versionBusy || facility.id !== targetRef.current || (versionDirty && !window.confirm("未保存の施設版入力を破棄し、版一覧を再読込しますか？"))) return;
    const target = facility.id;
    const generation = ++versionGeneration.current;
    const owns = captureVersionOwnership();
    const current = () => owns() && generation === versionGeneration.current && versionBusyOwner.current === 0;
    try {
      if (!current()) return;
      const res = await apiClient.get<CanonicalFacilityVersions>(`/facilities/${target}/versions`);
      if (!current()) return;
      requireFacilityVersions(res.data, target);
      clearVersionEditor();
      versionSnapshotRef.current = res.data;
      setVersionSnapshot(res.data);
    } catch (error) {
      if (current()) setVersionMessage(errorDetailText(error, "施設版を読み込めません。"));
    }
  };
  const selectVersion = (selected: string) => {
    if (versionDirty && !window.confirm("未保存の施設版入力を破棄し、選択を変更しますか？")) return;
    const versions = versionSnapshotRef.current;
    if (!versions || versions.facility_id !== targetRef.current) return;
    const matches = versions.versions.filter((version) => version.version_id === selected);
    if (selected && matches.length !== 1) { setVersionMessage("施設版の選択を一意に確認できません。"); return; }
    versionGeneration.current += 1;
    inputGeneration.current += 1;
    const version = selected ? matches[0] : null;
    versionOriginal.current = version;
    setVersionExisting(Boolean(version));
    setVersionId(version?.version_id ?? "");
    setVersionFrom(version?.valid_from ?? "");
    setVersionTo(version?.valid_to ?? "");
    setVersionOpenEnd(Boolean(version && version.valid_to === null));
    setVersionText(version ? prettyJson(version.facility) : "");
    setVersionDirty(false);
    setVersionMessage("");
  };
  const copyVersionForChange = () => {
    if (!versionOriginal.current || versionBusy || versionBlocked) return;
    const content = versionOriginal.current.facility;
    versionGeneration.current += 1;
    inputGeneration.current += 1;
    versionOriginal.current = null;
    setVersionExisting(false);
    setVersionId(`facility-${crypto.randomUUID()}`);
    setVersionFrom(new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" }).format(new Date()));
    setVersionTo("");
    setVersionOpenEnd(true);
    setVersionText(prettyJson(content));
    setVersionDirty(true);
    setVersionMessage("");
  };
  const saveVersion = async () => {
    if (!facility || facility.id !== targetRef.current || versionBusyOwner.current !== 0 || versionBusy || versionBlocked) return;
    const target = facility.id;
    const snapshot = versionSnapshotRef.current;
    if (!snapshot) { setVersionMessage("編集開始時の施設版 snapshot がありません。明示的に再読込してください。"); return; }
    const generation = versionGeneration.current;
    const command = ++commandGeneration.current;
    previewGeneration.current += 1;
    const owns = captureVersionOwnership();
    const current = () => owns() && generation === versionGeneration.current && command === commandGeneration.current &&
      versionBusyOwner.current === command;
    versionBusyOwner.current = command;
    setVersionBusy(true);
    let dispatched = false;
    try {
      const expectedRevision = requireFacilityVersions(snapshot, target);
      const parsed = parseJson(versionText);
      if (parsed.error || !parsed.value || typeof parsed.value !== "object" || Array.isArray(parsed.value)) throw new Error("完全施設版をJSONオブジェクトで入力してください。");
      const version = { ...versionOriginal.current, version_id: versionId, valid_from: versionFrom,
        valid_to: versionOpenEnd ? null : versionTo, facility: parsed.value };
      const url = `/facilities/${target}/versions`;
      if (!current()) return;
      dispatched = true;
      const res = versionExisting
        ? await apiClient.put<CanonicalFacilityVersions>(`${url}/${encodeURIComponent(versionId)}`, { version, expected_revision: expectedRevision })
        : await apiClient.post<CanonicalFacilityVersions>(url, { version, expected_revision: expectedRevision });
      if (!current()) return;
      requireFacilityVersions(res.data, target);
      const notice = canonicalSaveNotice(res.data);
      const own = res.data.version;
      if (!own || own.version_id !== versionId) throw new Error("保存済み施設版を確認できません。入力を保持して明示的に再読込してください。");
      versionSnapshotRef.current = res.data;
      setVersionSnapshot(res.data);
      versionOriginal.current = own;
      setVersionExisting(true);
      setVersionFrom(own.valid_from ?? ""); setVersionTo(own.valid_to ?? ""); setVersionText(prettyJson(own.facility));
      setVersionOpenEnd(own.valid_to === null);
      setVersionDirty(false);
      setVersionMessage(`施設版を保存しました。${notice}`);
    } catch (error) {
      if (current()) {
        const status = (error as any)?.response?.status;
        if (dispatched && (status === 409 || status === 428 || !status || status >= 500)) setVersionBlocked(true);
        setVersionMessage(errorDetailText(error, "施設版を保存できません。入力は保持しています。"));
      }
    } finally {
      if (versionBusyOwner.current === command) {
        versionBusyOwner.current = 0;
        setVersionBusy(false);
      }
    }
  };
  const resolveVersionDate = async () => {
    if (!facility || versionBusyOwner.current !== 0 || versionBusy) return;
    const target = facility.id;
    const generation = ++previewGeneration.current;
    const owns = captureVersionOwnership();
    const current = () => owns() && generation === previewGeneration.current && versionBusyOwner.current === 0;
    try {
      if (!current()) return;
      const res = await apiClient.get<CanonicalDatedFacility>(`/facilities/${target}/resolved`, { params: { effective_date: effectiveDate } });
      if (!current()) return;
      requireDatedFacility(res.data, target, effectiveDate);
      setDatedResult(prettyJson(res.data));
    } catch (error) {
      if (current()) { setDatedResult(""); setVersionMessage(errorDetailText(error, "指定日の施設版を解決できません。")); }
    }
  };

  const ownResponse = (data: FacilityResponse, target: string) => {
    const notice = canonicalSaveNotice(data);
    baseRevision.current = requireFacilitySnapshot(data, target);
    loadGeneration.current += 1;
    return notice;
  };

  const reloadEditor = async (kind: "facility" | "config") => {
    if (hasUnsavedChanges && !window.confirm("施設情報・設定・JSON の未保存変更を破棄して、施設全体を再読込しますか？")) {
      throw new Error("未保存入力を保持し、再読込を中止しました。");
    }
    const command = captureCommand(false);
    const res = await apiClient.get<FacilityResponse>(`/facilities/${facility!.id}`);
    if (!command.isCurrent() || res.data.facility?.id !== facility!.id || !res.data.config || !res.data.resolved_config) {
      throw new Error("再読込結果を適用できませんでした。");
    }
    baseRevision.current = requireFacilitySnapshot(res.data, facility!.id);
    loadGeneration.current += 1;
    setFacility(res.data.facility);
    setAreasText(prettyJson(res.data.facility.areas));
    setConfigRecord(res.data.config);
    setConfigText(prettyJson(res.data.config));
    setResolvedText(prettyJson(res.data.resolved_config));
    setValidation(res.data.validation || null);
    setOrderFormPatterns(res.data.order_form_patterns);
    setOrderFormPatternsError(res.data.order_form_patterns.length ? "" : "JSON 正本に注文書パターン候補がありません。既存値は保持します。");
    facilityDraftRef.current = { name: res.data.facility.name };
    configDraftRef.current = configDraft(res.data.config);
    setFacilityFormDirty(false);
    setConfigFormDirty(false);
    areaJsonDirty.current = false;
    configJsonDirty.current = false;
    workbookDirty.current = false;
    setSpecialDirty(false);
    setEditorEpoch((epoch) => epoch + 1);
    clearVersionEditor();
    setMessage(kind === "facility" ? "施設情報と設定を再読込しました。" : "設定と施設情報を再読込しました。");
    return res.data;
  };

  const loadFacility = async () => {
    if (!id || Array.isArray(id)) return;
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    const target = id;
    const input = inputGeneration.current;
    const command = commandGeneration.current;
    setLoadError("");
    try {
      const facilityRes = await apiClient.get<FacilityResponse>(`/facilities/${id}`);
      if (generation !== loadGeneration.current || target !== targetRef.current || input !== inputGeneration.current || command !== commandGeneration.current || dirtyRef.current) return;
      const data = facilityRes.data;
      if (!data?.facility || typeof data.facility !== "object") throw new Error("施設設定の応答に facility がありません。");
      baseRevision.current = requireFacilitySnapshot(data, target);
      setFacility(data.facility);
      facilityDraftRef.current = { name: data.facility.name };
      setAreasText(prettyJson(data.facility.areas));
      setConfigText(prettyJson(data.config));
      setResolvedText(prettyJson(data.resolved_config));
      setValidation(data.validation || null);
      const configRecord = data.config;
      setConfigRecord(configRecord);
      configDraftRef.current = configDraft(configRecord);
      const rawPatterns = data.order_form_patterns;
      if (Array.isArray(rawPatterns)) {
        const normalized = rawPatterns
          .map((item: any) => ({
            pattern_id: String(item?.pattern_id || "").trim(),
            label:
              typeof item?.label === "string" && item.label.trim()
                ? item.label.trim()
                : undefined,
            description:
              typeof item?.description === "string" && item.description.trim()
                ? item.description.trim()
                : undefined,
          }))
          .filter((item: OrderFormPattern) => Boolean(item.pattern_id));
        setOrderFormPatterns(normalized);
        setOrderFormPatternsError(normalized.length ? "" : "JSON 正本に注文書パターン候補がありません。既存値は保持し、候補から自動設定しません。");
      } else {
        setOrderFormPatterns([]);
        setOrderFormPatternsError("JSON 正本の注文書パターンが不正です。既存値は保持し、候補から自動設定しません。");
      }
      setMessage("");
      setFacilityFormDirty(false);
      setConfigFormDirty(false);
      setSpecialDirty(false);
      areaJsonDirty.current = false;
      configJsonDirty.current = false;
      workbookDirty.current = false;
    } catch (err: any) {
      if (generation !== loadGeneration.current || target !== targetRef.current || input !== inputGeneration.current || command !== commandGeneration.current) return;
      setOrderFormPatterns([]);
      setOrderFormPatternsError("注文書パターン API または施設設定の取得に失敗しました。既存値は変更していません。");
      const status = err?.response?.status;
      if (status === 404) {
        setLoadError("Facility not found. Create it first.");
      } else {
        setLoadError("Failed to load facility.");
      }
    }
  };

  useEffect(() => {
    saveGeneration.current += 1;
    clearVersionEditor();
    commandGeneration.current += 1;
    uploadGeneration.current += 1;
    setSourceWorkbookUploading(false);
    setFacility(null);
    baseRevision.current = null;
    areaJsonDirty.current = false;
    configJsonDirty.current = false;
    workbookDirty.current = false;
    setFacilityFormDirty(false);
    setConfigFormDirty(false);
    setSpecialDirty(false);
    loadFacility();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const saveFacility = async (values: FacilityDraft) => {
    if (!facility) throw new Error("施設が読み込まれていません。");
    const command = captureCommand();
    const parsed = parseJson(areasText);
    if (areaJsonDirty.current && parsed.error) {
      setMessage(`Areas JSON error: ${parsed.error}`);
      throw new Error(`Areas JSON error: ${parsed.error}`);
    }
    if (areaJsonDirty.current && parsed.value !== null && !Array.isArray(parsed.value)) {
      setMessage("Areas JSON must be an array.");
      throw new Error("Areas JSON must be an array.");
    }
    try {
      const res = await apiClient.put<FacilityResponse>(`/facilities/${facility.id}`, {
        name: values.name,
        ...(areaJsonDirty.current ? { areas: parsed.value } : {}),
        expected_revision: command.expectedRevision,
      });
      if (!command.isCurrent()) throw new Error("編集対象または入力が変わったため保存応答を適用しませんでした。");
      const notice = ownResponse(res.data, facility.id);
      setFacility(res.data.facility);
      setAreasText(prettyJson(res.data.facility.areas));
      setFacilityFormDirty(false);
      areaJsonDirty.current = false;
      setSpecialDirty(configJsonDirty.current || workbookDirty.current);
      setMessage(`Facility saved.${notice}`);
      return { name: res.data.facility.name };
    } catch (err: any) {
      if (!command.isCurrent()) throw err;
      setMessage(errorDetailText(err, "施設保存に失敗しました。"));
      throw err;
    }
  };

  const saveConfig = async (values: ConfigDraft) => {
    if (!facility) throw new Error("施設が読み込まれていません。");
    const command = captureCommand();
    const parsed = parseJson(configText);
    if (parsed.error) {
      setMessage(`Config JSON error: ${parsed.error}`);
      throw new Error(`Config JSON error: ${parsed.error}`);
    }
    if (parsed.value == null || typeof parsed.value !== "object") {
      setMessage("Config JSON must be an object.");
      throw new Error("Config JSON must be an object.");
    }
    try {
      const currentConfig = parsed.value as Record<string, unknown>;
      validateConfigSave(currentConfig, values);
      const nextConfig = mergeConfigDraft(currentConfig, values, configDraft(configRecord));
      const res = await apiClient.put<FacilityResponse>(`/facilities/${facility.id}/config`, {
        config: nextConfig, expected_revision: command.expectedRevision,
      });
      if (!command.isCurrent()) throw new Error("編集対象または入力が変わったため保存応答を適用しませんでした。");
      const notice = ownResponse(res.data, facility.id);
      const savedConfig = res.data.config;
      setConfigRecord(savedConfig);
      setConfigText(prettyJson(savedConfig));
      setResolvedText(prettyJson(res.data.resolved_config));
      setValidation(res.data.validation || null);
      configDraftRef.current = configDraft(savedConfig);
      setConfigFormDirty(false);
      configJsonDirty.current = false;
      setSpecialDirty(areaJsonDirty.current || workbookDirty.current);
      setMessage(`Config saved.${notice}`);
      return configDraft(savedConfig);
    } catch (err: any) {
      if (!command.isCurrent()) throw err;
      const detail = err?.response?.data?.detail;
      if (detail?.errors) {
        setValidation({ errors: detail.errors, warnings: [] });
        setMessage("Config validation failed.");
        throw err;
      }
      setMessage(errorDetailText(err, "Failed to save config. 明示的に再読込してください。"));
      throw err;
    }
  };

  const handleConfigUpload = (file: File | null) => {
    if (!file) return;
    const input = ++inputGeneration.current;
    const owner = saveGeneration.current;
    const target = targetRef.current;
    const reader = new FileReader();
    reader.onload = () => {
      if (input !== inputGeneration.current || owner !== saveGeneration.current || target !== targetRef.current) return;
      const text = String(reader.result || "");
      const parsed = parseJson(text);
      if (parsed.error) {
        setMessage(`Config JSON error: ${parsed.error}`);
        return;
      }
      setConfigText(prettyJson(parsed.value));
      markJsonDirty("config");
      setMessage("Config loaded from file.");
    };
    reader.onerror = () => {
      if (input === inputGeneration.current && owner === saveGeneration.current && target === targetRef.current) {
        setMessage("設定ファイルを読み込めませんでした。既存入力は変更していません。");
      }
    };
    reader.readAsText(file);
  };

  const uploadSourceWorkbook = async (file: File | null) => {
    if (!facility || !file) return;
    if (facilityFormDirty || configFormDirty || areaJsonDirty.current || configJsonDirty.current) {
      setMessage("未保存の施設情報または設定があります。保存または取消後にExcelを登録してください。");
      return;
    }
    let command: ReturnType<typeof captureCommand>;
    try { command = captureCommand(); } catch (err) {
      setMessage(err instanceof Error ? err.message : "JSON 正本を再読込してください。");
      return;
    }
    const form = new FormData();
    const uploadOwner = ++uploadGeneration.current;
    const uploadTarget = targetRef.current;
    form.append("file", file);
    form.append("expected_revision", command.expectedRevision);
    if (sourceWorkbookMonthId.trim()) {
      form.append("month_id", sourceWorkbookMonthId.trim());
    }
    setSourceWorkbookUploading(true);
    setMessage("");
    try {
      const res = await apiClient.post<FacilityResponse>(`/facilities/${facility.id}/order-form-source-workbook`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      if (!command.isCurrent()) throw new Error("編集対象または入力が変わったため upload 応答を適用しませんでした。");
      const notice = ownResponse(res.data, facility.id);
      setResolvedText(prettyJson(res.data.resolved_config));
      setConfigText(prettyJson(res.data.config));
      setConfigRecord(res.data.config);
      configDraftRef.current = configDraft(res.data.config);
      workbookDirty.current = false;
      setSpecialDirty(false);
      setMessage(`施設テンプレートExcelを登録しました。次回OCR再実行からこのExcelを使います。${notice}`);
    } catch (err: any) {
      if (!command.isCurrent()) return;
      setMessage(errorDetailText(err, "施設テンプレートExcelの登録に失敗しました。明示的に再読込してください。"));
    } finally {
      if (uploadOwner === uploadGeneration.current && uploadTarget === targetRef.current) setSourceWorkbookUploading(false);
    }
  };

  if (!authenticated) return <main className="page">ログインを確認しています。</main>;

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Facility Config</p>
          <h1>施設設定</h1>
          <p className="subtle">テンプレとルールをJSONで管理します。</p>
        </div>
        <TopNav />
      </header>

      {loadError ? (
        <p className="message">
          {loadError} <Link href="/facilities">Go to facilities list</Link>
        </p>
      ) : !facility ? (
        <p className="subtle">Loading...</p>
      ) : (
        <>
          <section className="panel">
            <header className="panel-header"><h2>施設情報の有効期間</h2><Button className="btn ghost" type="button" disabled={versionBusy} onClick={() => void loadVersions()}>版一覧を再読込</Button></header>
            <div className="form-grid">
              <label className="field"><span className="field-label">編集対象</span><NativeSelect className="input" value={versionExisting ? versionId : ""} onChange={(e) => selectVersion(e.target.value)} disabled={versionBusy || !versionSnapshot}><option value="">新規版</option>{versionSnapshot?.versions.map((version) => <option key={version.version_id} value={version.version_id}>{version.version_id} ({version.valid_from} - {version.valid_to ?? "終了日なし"})</option>)}</NativeSelect></label>
              <label className="field"><span className="field-label">版ID</span><Input disableUnderline className="input" value={versionId} onChange={(e) => editVersion(setVersionId, e.target.value)} disabled={versionBusy || versionExisting} /></label>
              <label className="field"><span className="field-label">開始日（含む）</span><Input disableUnderline className="input" type="date" value={versionFrom} onChange={(e) => editVersion(setVersionFrom, e.target.value)} disabled={versionBusy || versionExisting} /></label>
              <label className="field"><span className="field-label">終了日（含む）</span><Input disableUnderline className="input" type="date" value={versionTo} onChange={(e) => editVersion(setVersionTo, e.target.value)} disabled={versionBusy || versionExisting || versionOpenEnd} /><span><Checkbox checked={versionOpenEnd} disabled={versionBusy || versionExisting} onChange={(e) => { versionGeneration.current += 1; inputGeneration.current += 1; setVersionDirty(true); setVersionOpenEnd(e.target.checked); }} />終了日なし</span></label>
              <label className="field"><span className="field-label">完全施設版（名称・区域・設定・テンプレート）</span><Input disableUnderline multiline className="textarea json-textarea" rows={12} value={versionText} onChange={(e) => editVersion(setVersionText, e.target.value)} disabled={versionBusy || versionExisting} /></label>
            </div>
            {versionExisting ? <Button className="btn" type="button" onClick={copyVersionForChange} disabled={versionBusy || versionBlocked}>新しい版として変更</Button> : <Button className="btn" type="button" onClick={() => void saveVersion()} disabled={versionBusy || versionBlocked || !versionSnapshot}>施設版を保存</Button>}
            <div className="form-grid"><label className="field"><span className="field-label">参照日</span><Input disableUnderline className="input" type="date" disabled={versionBusy} value={effectiveDate} onChange={(e) => { previewGeneration.current += 1; setEffectiveDate(e.target.value); setDatedResult(""); }} /></label></div>
            <Button className="btn ghost" type="button" disabled={versionBusy} onClick={() => void resolveVersionDate()}>指定日の版を取得</Button>
            {versionMessage ? <p className="message">{versionMessage}</p> : null}
            {datedResult ? <pre className="json-view">{datedResult}</pre> : null}
          </section>
          <section className="panel">
            <header className="panel-header">
              <h2>施設情報</h2>
              <span className="badge">{facility.id}</span>
            </header>
            <EditForm<FacilityDraft>
              key={`${facility.id}-${saveGeneration.current}-${editorEpoch}`}
              label="通常施設情報"
              defaultValues={{ name: facility.name || "" }}
              onDirtyChange={setFacilityFormDirty}
              classifyError={(error) => ({ kind: (error as any)?.response?.status === 409 ? "conflict" : "error", message: errorDetailText(error, "施設保存に失敗しました。") })}
              onSave={saveFacility}
              onReload={async () => {
                const data = await reloadEditor("facility");
                return { name: data.facility.name };
              }}
            >
              <DraftBridge<FacilityDraft> onValuesChange={syncFacilityDraft} />
              <FormTextField<FacilityDraft> name="name" label="施設名" rules={{ required: "施設名は必須です。" }} />
            </EditForm>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">配達エリア（JSON配列）</span>
                <Input disableUnderline multiline
                  className="textarea"
                  value={areasText}
                  onChange={(e) => { setAreasText(e.target.value); markJsonDirty("areas"); }}
                  rows={6}
                />
              </label>
            </div>
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>施設設定</h2>
              <Input disableUnderline
                type="file"
                inputProps={{ accept: "application/json" }}
                onChange={(e: ChangeEvent<HTMLInputElement>) => handleConfigUpload(e.target.files?.[0] || null)}
              />
            </header>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">施設テンプレートExcel</span>
                <Input disableUnderline className="input" type="file" inputProps={{ accept: ".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12" }} onChange={(e: ChangeEvent<HTMLInputElement>) => void uploadSourceWorkbook(e.target.files?.[0] || null)} disabled={sourceWorkbookUploading} />
              </label>
              <label className="field">
                <span className="field-label">月指定（任意: YYYY-MM）</span>
                <Input disableUnderline className="input" value={sourceWorkbookMonthId} onChange={(e) => { inputGeneration.current += 1; workbookDirty.current = true; setSourceWorkbookMonthId(e.target.value); setSpecialDirty(true); }} disabled={sourceWorkbookUploading} />
              </label>
            </div>
            <EditForm<ConfigDraft>
              key={`${facility.id}-${saveGeneration.current}-${editorEpoch}-config`}
              label="通常施設設定"
              defaultValues={configDraft(configRecord)}
              onDirtyChange={setConfigFormDirty}
              classifyError={(error) => ({ kind: (error as any)?.response?.status === 409 ? "conflict" : "error", message: errorDetailText(error, "設定保存に失敗しました。") })}
              onSave={saveConfig}
              onReload={async () => {
                const data = await reloadEditor("config");
                return configDraft(data.config);
              }}
            >
              <DraftBridge<ConfigDraft> onValuesChange={syncConfigDraft} />
              <FormTextField<ConfigDraft> name="order_form_pattern_id" label="注文書パターン" select><MenuItem value="">未設定</MenuItem>{orderFormPatterns.length === 0 && configDraft(configRecord).order_form_pattern_id ? <MenuItem value={configDraft(configRecord).order_form_pattern_id}>{`現在値（API候補外）: ${configDraft(configRecord).order_form_pattern_id}`}</MenuItem> : null}{orderFormPatterns.map((pattern) => <MenuItem key={pattern.pattern_id} value={pattern.pattern_id}>{pattern.label ? `${pattern.label} (${pattern.pattern_id})` : pattern.pattern_id}</MenuItem>)}</FormTextField>
              {orderFormPatternsError ? <p className="message">{orderFormPatternsError}</p> : null}
              <FormTextField<ConfigDraft> name="menu_override_tags" label="メニュー上書きタグ" helperText="カンマ区切り" />
              <FormTextField<ConfigDraft> name="main_ocr_provider" label="主OCRプロバイダ" select><MenuItem value="">未設定</MenuItem><MenuItem value="pipeline">pipeline</MenuItem><MenuItem value="openai">openai</MenuItem><MenuItem value="gemini">gemini</MenuItem></FormTextField>
              <FormTextField<ConfigDraft> name="openai_ocr_enabled" label="OpenAI OCRを使う" select><MenuItem value="">未設定</MenuItem><MenuItem value="true">有効</MenuItem><MenuItem value="false">無効</MenuItem></FormTextField>
              <FormTextField<ConfigDraft> name="openai_ocr_model" label="OpenAIモデル" /><FormTextField<ConfigDraft> name="openai_ocr_fallback_provider" label="OpenAIフォールバック（noneのみ）" rules={{ validate: (value) => !value || value.trim().toLowerCase() === "none" || "フォールバックは none のみ指定できます。" }} />
              <FormTextField<ConfigDraft> name="openai_ocr_prompt" label="OpenAI OCR指示" multiline minRows={4} />
              <FormTextField<ConfigDraft> name="gemini_ocr_enabled" label="Gemini OCRを使う" select><MenuItem value="">未設定</MenuItem><MenuItem value="true">有効</MenuItem><MenuItem value="false">無効</MenuItem></FormTextField>
              <FormTextField<ConfigDraft> name="gemini_ocr_model" label="Geminiモデル" /><FormTextField<ConfigDraft> name="gemini_ocr_fallback_provider" label="Geminiフォールバック（noneのみ）" rules={{ validate: (value) => !value || value.trim().toLowerCase() === "none" || "フォールバックは none のみ指定できます。" }} />
              <FormTextField<ConfigDraft> name="gemini_ocr_prompt" label="Gemini OCR指示" multiline minRows={4} />
              <FormTextField<ConfigDraft> name="hakodate_header_rows" label="函館帳票の見出し行数" inputMode="numeric" />
              <FormTextField<ConfigDraft> name="hakodate_ocr_resolution" label="函館OCR解像度" inputMode="numeric" />
              <FormTextField<ConfigDraft> name="hakodate_min_edge_margin_ratio" label="函館OCRの最小余白比率" inputMode="decimal" />
              <FormTextField<ConfigDraft> name="large_cell_mode" label="結合セルを含む帳票モード" select><MenuItem value="">未設定</MenuItem><MenuItem value="true">有効</MenuItem><MenuItem value="false">無効</MenuItem></FormTextField>
            </EditForm>
            <details className="json-panel">
              <summary>Config JSON (上級)</summary>
              <Input disableUnderline multiline
                className="textarea json-textarea"
                value={configText}
                onChange={(e) => { setConfigText(e.target.value); markJsonDirty("config"); }}
                rows={14}
                inputProps={{ wrap: "soft" }}
              />
            </details>
            <div className="actions">
              <Button type="submit" className="btn primary" onClick={() => {
                if (!configDraftRef.current) {
                  setMessage("通常施設設定を読み込めていないため、JSON設定を保存できません。");
                  return;
                }
                void saveConfig(configDraftRef.current).catch(() => undefined);
              }}>
                JSON設定を保存
              </Button>
            </div>
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>Validation</h2>
            </header>
            {validation ? (
              <>
                <p className="subtle">Errors: {validation.errors.length}</p>
                {validation.errors.length > 0 && (
                  <pre className="code-block">{validation.errors.join("\n")}</pre>
                )}
                <p className="subtle">Warnings: {validation.warnings.length}</p>
                {validation.warnings.length > 0 && (
                  <pre className="code-block">{validation.warnings.join("\n")}</pre>
                )}
              </>
            ) : (
              <p className="subtle">No validation results yet.</p>
            )}
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>日付未指定の参照資料</h2>
            </header>
            <details className="json-panel">
              <summary>参照設定 JSON（適用版ではありません）</summary>
              <pre className="code-block">{resolvedText}</pre>
            </details>
          </section>
        </>
      )}

      {message && <p className="message">{message}</p>}

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

        h2 {
          font-size: 18px;
          margin: 0;
        }

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .form-grid {
          display: grid;
          gap: 16px;
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

        .input,
        .textarea,
        .page :global(.MuiInputBase-root.input),
        .page :global(.MuiInputBase-root.textarea) {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .field :global(.MuiInputBase-root) { width: 100%; min-width: 0; }
        .field :global(.MuiInputBase-input) { padding: 0; font: inherit; }

        .btn,
        .page :global(.MuiButton-root.btn) {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          width: fit-content;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .actions {
          margin-top: 12px;
        }

        .code-block {
          background: #fbfbf9;
          border-radius: 12px;
          padding: 12px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          white-space: pre-wrap;
          word-break: break-word;
        }

        .json-panel {
          margin-top: 12px;
        }

        .json-panel summary {
          cursor: pointer;
          font-weight: 600;
          color: #1f2a2a;
          margin-bottom: 8px;
        }

        .json-textarea {
          white-space: pre-wrap;
          word-break: break-word;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}

export default function FacilityConfigPage() {
  const sessionKey = useSyncExternalStore(subscribeBrowserSession, browserSessionKey, () => "server");
  const authenticated = useSyncExternalStore(subscribeBrowserSession, hasActiveSessionAuthHeader, () => false);
  return <SawaProvider sessionKey={`hospital-facility-config:${sessionKey}`}><FacilityConfigScreen authenticated={authenticated} /></SawaProvider>;
}
