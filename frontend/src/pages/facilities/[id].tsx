import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { MenuItem } from "@mui/material";
import { EditForm, FormTextField, SawaProvider } from "@sawa/ui";
import TopNav from "../../components/TopNav";
import { apiClient, hasActiveSessionAuthHeader } from "../../services/apiClient";
import { browserSessionKey, subscribeBrowserSession } from "../../services/browserSession";
import { useH1LeaveGuard } from "../../hooks/useH1LeaveGuard";

type FacilityArea = {
  id: string;
  name: string;
};

type Facility = {
  id: string;
  name: string;
  areas: FacilityArea[];
};

type ValidationResult = {
  errors: string[];
  warnings: string[];
};

type FacilityResponse = {
  facility: Facility;
  config?: Record<string, unknown>;
  resolved_config?: Record<string, unknown>;
  validation?: ValidationResult;
};

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

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

const parseJson = (text: string) => {
  try {
    return { value: JSON.parse(text), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON";
    return { value: null, error: message };
  }
};

const errorDetailText = (err: any, fallback: string) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    const message = detail.message || detail.error;
    if (typeof message === "string" && message.trim()) return message.trim();
  }
  return fallback;
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

const mergeConfigDraft = (base: Record<string, unknown>, values: ConfigDraft) => {
  const next = { ...base };
  setOptionalString(next, "order_form_pattern_id", values.order_form_pattern_id);
  setOptionalString(next, "main_ocr_provider", values.main_ocr_provider);
  setOptionalString(next, "openai_ocr_model", values.openai_ocr_model);
  setOptionalString(next, "openai_ocr_prompt", values.openai_ocr_prompt);
  setOptionalString(next, "openai_ocr_fallback_provider", values.openai_ocr_fallback_provider);
  setOptionalString(next, "gemini_ocr_model", values.gemini_ocr_model);
  setOptionalString(next, "gemini_ocr_prompt", values.gemini_ocr_prompt);
  setOptionalString(next, "gemini_ocr_fallback_provider", values.gemini_ocr_fallback_provider);
  ["openai_ocr_enabled", "gemini_ocr_enabled", "large_cell_mode"].forEach((key) => {
    const value = values[key as "openai_ocr_enabled" | "gemini_ocr_enabled" | "large_cell_mode"];
    if (value === "true" || value === "false") next[key] = value === "true";
    else delete next[key];
  });
  setOptionalNumber(next, "hakodate_header_rows", values.hakodate_header_rows, 0);
  setOptionalNumber(next, "hakodate_ocr_resolution", values.hakodate_ocr_resolution, Number.MIN_VALUE);
  setOptionalNumber(next, "hakodate_min_edge_margin_ratio", values.hakodate_min_edge_margin_ratio, 0);
  const tags = values.menu_override_tags.split(",").map((item) => item.trim()).filter(Boolean);
  if (tags.length) next.menu_override_tags = tags;
  else delete next.menu_override_tags;
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
  const dirtyRef = useRef(false);
  const [facilityFormDirty, setFacilityFormDirty] = useState(false);
  const [configFormDirty, setConfigFormDirty] = useState(false);
  const [specialDirty, setSpecialDirty] = useState(false);
  const [configRecord, setConfigRecord] = useState<Record<string, unknown>>({});
  const configDraftRef = useRef<ConfigDraft | null>(null);
  const hasUnsavedChanges = facilityFormDirty || configFormDirty || specialDirty;
  dirtyRef.current = hasUnsavedChanges;
  const facilityPath = !Array.isArray(id) && id ? `/facilities/${id}` : "/facilities";
  useH1LeaveGuard(hasUnsavedChanges, [facilityPath]);

  const loadFacility = async () => {
    if (!id || Array.isArray(id)) return;
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setLoadError("");
    try {
      const [facilityRes, patternResult] = await Promise.all([
        apiClient.get<FacilityResponse>(`/facilities/${id}`),
        apiClient.get("/order-forms/patterns")
          .then((res) => ({ data: res.data, error: "" }))
          .catch(() => ({ data: null, error: "注文書パターン API の取得に失敗しました。既存値は変更していません。" })),
      ]);
      if (generation !== loadGeneration.current || dirtyRef.current) return;
      const data = facilityRes.data;
      if (!data?.facility || typeof data.facility !== "object") throw new Error("施設設定の応答に facility がありません。");
      setFacility(data.facility);
      setAreasText(prettyJson(data.facility.areas || []));
      setConfigText(prettyJson(data.config || {}));
      setResolvedText(prettyJson(data.resolved_config || {}));
      setValidation(data.validation || null);
      const configRecord =
        data.config && typeof data.config === "object"
          ? (data.config as Record<string, unknown>)
          : {};
      setConfigRecord(configRecord);
      configDraftRef.current = configDraft(configRecord);
      const rawPatterns = patternResult.data?.patterns;
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
        setOrderFormPatternsError(patternResult.error || (normalized.length ? "" : "注文書パターン API に候補がありません。既存値は保持し、候補から自動設定しません。"));
      } else {
        setOrderFormPatterns([]);
        setOrderFormPatternsError(patternResult.error || "注文書パターン API の応答が不正です。既存値は保持し、候補から自動設定しません。");
      }
      setMessage("");
      setFacilityFormDirty(false);
      setConfigFormDirty(false);
      setSpecialDirty(false);
    } catch (err: any) {
      if (generation !== loadGeneration.current) return;
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
    setFacilityFormDirty(false);
    setConfigFormDirty(false);
    setSpecialDirty(false);
    loadFacility();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const saveFacility = async (values: FacilityDraft) => {
    if (!facility) throw new Error("施設が読み込まれていません。");
    const owner = saveGeneration.current;
    const parsed = parseJson(areasText);
    if (parsed.error) {
      setMessage(`Areas JSON error: ${parsed.error}`);
      throw new Error(`Areas JSON error: ${parsed.error}`);
    }
    if (!Array.isArray(parsed.value)) {
      setMessage("Areas JSON must be an array.");
      throw new Error("Areas JSON must be an array.");
    }
    try {
      const res = await apiClient.put(`/facilities/${facility.id}`, {
        name: values.name,
        areas: parsed.value,
      });
      if (!res.data || typeof res.data !== "object" || typeof res.data.name !== "string") throw new Error("施設保存の応答を確認できませんでした。");
      if (owner !== saveGeneration.current) throw new Error("施設の選択が変わったため保存応答を適用しませんでした。");
      setFacility(res.data as Facility);
      setAreasText(prettyJson(res.data.areas || []));
      setFacilityFormDirty(false);
      setMessage("Facility saved.");
      return { name: res.data.name };
    } catch (err: any) {
      setMessage(`Failed to save facility: ${errorDetailText(err, "Unknown error")}`);
      throw err;
    }
  };

  const saveConfig = async (values: ConfigDraft) => {
    if (!facility) throw new Error("施設が読み込まれていません。");
    const owner = saveGeneration.current;
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
      const nextConfig = mergeConfigDraft(currentConfig, values);
      const res = await apiClient.put(`/facilities/${facility.id}/config`, nextConfig);
      if (res.data?.updated !== true) throw new Error("設定保存の応答を確認できませんでした。");
      if (owner !== saveGeneration.current) throw new Error("施設の選択が変わったため保存応答を適用しませんでした。");
      const fresh = await apiClient.get<FacilityResponse>(`/facilities/${facility.id}`);
      if (owner !== saveGeneration.current || !fresh.data?.config || typeof fresh.data.config !== "object") {
        throw new Error("保存後の設定正本を確認できませんでした。");
      }
      const savedConfig = fresh.data.config as Record<string, unknown>;
      setConfigRecord(savedConfig);
      setConfigText(prettyJson(savedConfig));
      setResolvedText(prettyJson(fresh.data.resolved_config || {}));
      setValidation(fresh.data.validation || res.data.validation || null);
      setConfigFormDirty(false);
      setMessage("Config saved.");
      return configDraft(savedConfig);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.errors) {
        setValidation({ errors: detail.errors, warnings: [] });
        setMessage("Config validation failed.");
        throw err;
      }
      setMessage("Failed to save config.");
      throw err;
    }
  };

  const handleConfigUpload = (file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      const parsed = parseJson(text);
      if (parsed.error) {
        setMessage(`Config JSON error: ${parsed.error}`);
        return;
      }
      setConfigText(prettyJson(parsed.value));
      setSpecialDirty(true);
      setMessage("Config loaded from file.");
    };
    reader.readAsText(file);
  };

  const uploadSourceWorkbook = async (file: File | null) => {
    if (!facility || !file) return;
    const owner = saveGeneration.current;
    const form = new FormData();
    form.append("file", file);
    if (sourceWorkbookMonthId.trim()) {
      form.append("month_id", sourceWorkbookMonthId.trim());
    }
    setSourceWorkbookUploading(true);
    setMessage("");
    try {
      const res = await apiClient.post(`/facilities/${facility.id}/order-form-source-workbook`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      if (owner !== saveGeneration.current) throw new Error("施設の選択が変わったため upload 応答を適用しませんでした。");
      if (res.data?.resolved_config) {
        setResolvedText(prettyJson(res.data.resolved_config));
      }
      const nextConfig = res.data?.config || res.data?.resolved_config;
      if (nextConfig) {
        setConfigText(prettyJson(nextConfig));
        if (res.data?.config && typeof res.data.config === "object") setConfigRecord(res.data.config as Record<string, unknown>);
      }
      setMessage("施設テンプレートExcelを登録しました。次回OCR再実行からこのExcelを使います。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(typeof detail === "string" ? detail : "施設テンプレートExcelの登録に失敗しました。");
    } finally {
      setSourceWorkbookUploading(false);
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
            <header className="panel-header">
              <h2>施設情報</h2>
              <span className="badge">{facility.id}</span>
            </header>
            <EditForm<FacilityDraft>
              key={`${facility.id}-${saveGeneration.current}`}
              label="通常施設情報"
              defaultValues={{ name: facility.name || "" }}
              onDirtyChange={setFacilityFormDirty}
              classifyError={(error) => ({ kind: (error as any)?.response?.status === 409 ? "conflict" : "error", message: errorDetailText(error, "施設保存に失敗しました。") })}
              onSave={saveFacility}
              onReload={async () => {
                if (specialDirty) throw new Error("JSON の未保存変更があるため再読込を中止しました。");
                const owner = saveGeneration.current;
                const res = await apiClient.get<FacilityResponse>(`/facilities/${facility.id}`);
                if (owner !== saveGeneration.current || !res.data?.facility) throw new Error("再読込結果を適用できませんでした。");
                setFacility(res.data.facility);
                setAreasText(prettyJson(res.data.facility.areas || []));
                return { name: res.data.facility.name || "" };
              }}
            >
              <FormTextField<FacilityDraft> name="name" label="施設名" rules={{ required: "施設名は必須です。" }} />
            </EditForm>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">配達エリア（JSON配列）</span>
                <textarea
                  className="textarea"
                  value={areasText}
                  onChange={(e) => { setAreasText(e.target.value); setSpecialDirty(true); }}
                  rows={6}
                />
              </label>
            </div>
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>施設設定</h2>
              <input
                type="file"
                accept="application/json"
                onChange={(e) => handleConfigUpload(e.target.files?.[0] || null)}
              />
            </header>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">施設テンプレートExcel</span>
                <input className="input" type="file" accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12" onChange={(e) => void uploadSourceWorkbook(e.target.files?.[0] || null)} disabled={sourceWorkbookUploading} />
              </label>
              <label className="field">
                <span className="field-label">月指定（任意: YYYY-MM）</span>
                <input className="input" value={sourceWorkbookMonthId} onChange={(e) => { setSourceWorkbookMonthId(e.target.value); setSpecialDirty(true); }} disabled={sourceWorkbookUploading} />
              </label>
            </div>
            <EditForm<ConfigDraft>
              key={`${facility.id}-${saveGeneration.current}-config`}
              label="通常施設設定"
              defaultValues={configDraft(configRecord)}
              onDirtyChange={setConfigFormDirty}
              onValuesChange={(values) => { configDraftRef.current = values; }}
              classifyError={(error) => ({ kind: (error as any)?.response?.status === 409 ? "conflict" : "error", message: errorDetailText(error, "設定保存に失敗しました。") })}
              onSave={saveConfig}
              onReload={async () => {
                if (specialDirty) throw new Error("JSON または workbook の未保存変更があるため再読込を中止しました。");
                const owner = saveGeneration.current;
                const res = await apiClient.get<FacilityResponse>(`/facilities/${facility.id}`);
                if (owner !== saveGeneration.current || !res.data?.config || typeof res.data.config !== "object") throw new Error("再読込結果を適用できませんでした。");
                const saved = res.data.config as Record<string, unknown>;
                setConfigRecord(saved); setConfigText(prettyJson(saved)); setResolvedText(prettyJson(res.data.resolved_config || {}));
                return configDraft(saved);
              }}
            >
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
              <textarea
                className="textarea json-textarea"
                value={configText}
                onChange={(e) => { setConfigText(e.target.value); setSpecialDirty(true); }}
                rows={14}
                wrap="soft"
              />
            </details>
            <div className="actions">
              <button className="btn primary" onClick={() => {
                if (!configDraftRef.current) {
                  setMessage("通常施設設定を読み込めていないため、JSON設定を保存できません。");
                  return;
                }
                void saveConfig(configDraftRef.current).catch(() => undefined);
              }}>
                JSON設定を保存
              </button>
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
              <h2>Resolved Preview</h2>
            </header>
            <details className="json-panel">
              <summary>Resolved JSON (上級)</summary>
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
        .textarea {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .btn {
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
