import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";

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

type OrderFormPattern = {
  pattern_id: string;
  label?: string;
  description?: string;
};

const DEFAULT_ORDER_FORM_PATTERNS: OrderFormPattern[] = [
  { pattern_id: "PATTERN_A", label: "標準A" },
  { pattern_id: "PATTERN_B", label: "標準B" },
  { pattern_id: "PATTERN_C", label: "標準C" },
  { pattern_id: "PATTERN_D", label: "標準D" },
  { pattern_id: "PATTERN_E", label: "標準E" },
];

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

const parseJson = (text: string) => {
  try {
    return { value: JSON.parse(text), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON";
    return { value: null, error: message };
  }
};

const parseBoolean = (value: unknown, defaultValue = false): boolean => {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "on"].includes(normalized)) return true;
    if (["false", "0", "no", "off", ""].includes(normalized)) return false;
  }
  return defaultValue;
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

export default function FacilityConfigPage() {
  const router = useRouter();
  const { id } = router.query;
  const [facility, setFacility] = useState<Facility | null>(null);
  const [name, setName] = useState("");
  const [areasText, setAreasText] = useState("[]");
  const [configText, setConfigText] = useState("{}");
  const [resolvedText, setResolvedText] = useState("{}");
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [orderFormPatterns, setOrderFormPatterns] = useState<OrderFormPattern[]>(
    DEFAULT_ORDER_FORM_PATTERNS
  );
  const [orderFormPatternId, setOrderFormPatternId] = useState("");
  const [mainOcrProvider, setMainOcrProvider] = useState<string>("pipeline");
  const [openaiOcrEnabled, setOpenaiOcrEnabled] = useState<boolean>(false);
  const [openaiOcrModel, setOpenaiOcrModel] = useState<string>("");
  const [openaiOcrPrompt, setOpenaiOcrPrompt] = useState<string>("");
  const [openaiFallbackProvider, setOpenaiFallbackProvider] = useState<string>("pipeline");
  const [geminiOcrEnabled, setGeminiOcrEnabled] = useState<boolean>(false);
  const [geminiOcrModel, setGeminiOcrModel] = useState<string>("");
  const [geminiOcrPrompt, setGeminiOcrPrompt] = useState<string>("");
  const [geminiFallbackProvider, setGeminiFallbackProvider] = useState<string>("pipeline");
  const [largeCellMode, setLargeCellMode] = useState<boolean>(false);
  const [menuOverrideTags, setMenuOverrideTags] = useState<string>("");

  const loadFacility = async () => {
    if (!id || Array.isArray(id)) return;
    setLoadError("");
    try {
      const [facilityRes, patternsRes] = await Promise.all([
        apiClient.get<FacilityResponse>(`/facilities/${id}`),
        apiClient.get("/order-forms/patterns").catch(() => null),
      ]);
      const data = facilityRes.data;
      setFacility(data.facility);
      setName(data.facility.name || "");
      setAreasText(prettyJson(data.facility.areas || []));
      setConfigText(prettyJson(data.config || {}));
      setResolvedText(prettyJson(data.resolved_config || {}));
      setValidation(data.validation || null);
      const configRecord =
        data.config && typeof data.config === "object"
          ? (data.config as Record<string, unknown>)
          : {};
      const selectedPattern = configRecord.order_form_pattern_id;
      setOrderFormPatternId(typeof selectedPattern === "string" ? selectedPattern : "");
      const provider = configRecord.main_ocr_provider;
      const normalizedProvider = typeof provider === "string" && provider.trim()
        ? provider.trim().toLowerCase()
        : "pipeline";
      setMainOcrProvider(normalizedProvider);
      setOpenaiOcrEnabled(parseBoolean(configRecord.openai_ocr_enabled));
      const model = configRecord.openai_ocr_model;
      setOpenaiOcrModel(typeof model === "string" ? model : "");
      const prompt = configRecord.openai_ocr_prompt;
      setOpenaiOcrPrompt(typeof prompt === "string" ? prompt : "");
      const fallback = configRecord.openai_ocr_fallback_provider;
      setOpenaiFallbackProvider(
        typeof fallback === "string" && fallback.trim() ? fallback.trim().toLowerCase() : "pipeline"
      );
      setGeminiOcrEnabled(parseBoolean(configRecord.gemini_ocr_enabled));
      const geminiModel = configRecord.gemini_ocr_model;
      setGeminiOcrModel(typeof geminiModel === "string" ? geminiModel : "");
      const geminiPrompt = configRecord.gemini_ocr_prompt;
      setGeminiOcrPrompt(typeof geminiPrompt === "string" ? geminiPrompt : "");
      const geminiFallback = configRecord.gemini_ocr_fallback_provider;
      setGeminiFallbackProvider(
        typeof geminiFallback === "string" && geminiFallback.trim()
          ? geminiFallback.trim().toLowerCase()
          : "pipeline"
      );
      setLargeCellMode(parseBoolean(configRecord.large_cell_mode));
      setMenuOverrideTags(parseStringList(configRecord.menu_override_tags).join(","));
      const rawPatterns = patternsRes?.data?.patterns;
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
        setOrderFormPatterns(normalized.length ? normalized : DEFAULT_ORDER_FORM_PATTERNS);
      } else {
        setOrderFormPatterns(DEFAULT_ORDER_FORM_PATTERNS);
      }
      setMessage("");
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404) {
        setLoadError("Facility not found. Create it first.");
      } else {
        setLoadError("Failed to load facility.");
      }
    }
  };

  useEffect(() => {
    loadFacility();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const saveFacility = async () => {
    if (!facility) return;
    const parsed = parseJson(areasText);
    if (parsed.error) {
      setMessage(`Areas JSON error: ${parsed.error}`);
      return;
    }
    if (!Array.isArray(parsed.value)) {
      setMessage("Areas JSON must be an array.");
      return;
    }
    const res = await apiClient.put(`/facilities/${facility.id}`, {
      name,
      areas: parsed.value,
    });
    setFacility(res.data);
    setMessage("Facility saved.");
  };

  const saveConfig = async () => {
    if (!facility) return;
    const parsed = parseJson(configText);
    if (parsed.error) {
      setMessage(`Config JSON error: ${parsed.error}`);
      return;
    }
    if (parsed.value == null || typeof parsed.value !== "object") {
      setMessage("Config JSON must be an object.");
      return;
    }
    try {
      const nextConfig = { ...(parsed.value as Record<string, unknown>) };
      const selectedPattern = orderFormPatternId.trim();
      if (selectedPattern) {
        nextConfig.order_form_pattern_id = selectedPattern;
      } else {
        delete nextConfig.order_form_pattern_id;
      }
      nextConfig.main_ocr_provider = mainOcrProvider || "pipeline";
      nextConfig.openai_ocr_enabled = openaiOcrEnabled;
      if (openaiOcrModel.trim()) {
        nextConfig.openai_ocr_model = openaiOcrModel.trim();
      } else {
        delete nextConfig.openai_ocr_model;
      }
      if (openaiOcrPrompt.trim()) {
        nextConfig.openai_ocr_prompt = openaiOcrPrompt.trim();
      } else {
        delete nextConfig.openai_ocr_prompt;
      }
      if (openaiFallbackProvider.trim()) {
        nextConfig.openai_ocr_fallback_provider = openaiFallbackProvider.trim();
      } else {
        delete nextConfig.openai_ocr_fallback_provider;
      }
      nextConfig.gemini_ocr_enabled = geminiOcrEnabled;
      if (geminiOcrModel.trim()) {
        nextConfig.gemini_ocr_model = geminiOcrModel.trim();
      } else {
        delete nextConfig.gemini_ocr_model;
      }
      if (geminiOcrPrompt.trim()) {
        nextConfig.gemini_ocr_prompt = geminiOcrPrompt.trim();
      } else {
        delete nextConfig.gemini_ocr_prompt;
      }
      if (geminiFallbackProvider.trim()) {
        nextConfig.gemini_ocr_fallback_provider = geminiFallbackProvider.trim();
      } else {
        delete nextConfig.gemini_ocr_fallback_provider;
      }
      nextConfig.large_cell_mode = largeCellMode;
      const tags = menuOverrideTags
        .split(",")
        .map((item) => item.trim())
        .filter((item) => Boolean(item));
      if (tags.length > 0) {
        nextConfig.menu_override_tags = tags;
      } else {
        delete nextConfig.menu_override_tags;
      }
      setConfigText(prettyJson(nextConfig));
      const res = await apiClient.put(`/facilities/${facility.id}/config`, nextConfig);
      if (res.data?.validation) {
        setValidation(res.data.validation);
      }
      if (res.data?.resolved_config) {
        setResolvedText(prettyJson(res.data.resolved_config));
      }
      setOrderFormPatternId(selectedPattern);
      setMessage("Config saved.");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.errors) {
        setValidation({ errors: detail.errors, warnings: [] });
        setMessage("Config validation failed.");
        return;
      }
      setMessage("Failed to save config.");
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
      setMessage("Config loaded from file.");
    };
    reader.readAsText(file);
  };

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
              <h2>Facility</h2>
              <span className="badge">{facility.id}</span>
            </header>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">Name</span>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="field">
                <span className="field-label">Areas (JSON array)</span>
                <textarea
                  className="textarea"
                  value={areasText}
                  onChange={(e) => setAreasText(e.target.value)}
                  rows={6}
                />
              </label>
              <button className="btn primary" onClick={saveFacility}>
                Save Facility
              </button>
            </div>
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>Config</h2>
              <input
                type="file"
                accept="application/json"
                onChange={(e) => handleConfigUpload(e.target.files?.[0] || null)}
              />
            </header>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">Order Form Pattern</span>
                <select
                  className="input"
                  value={orderFormPatternId}
                  onChange={(e) => setOrderFormPatternId(e.target.value)}
                >
                  <option value="">未設定</option>
                  {orderFormPatterns.map((pattern) => (
                    <option key={pattern.pattern_id} value={pattern.pattern_id}>
                      {pattern.label ? `${pattern.label} (${pattern.pattern_id})` : pattern.pattern_id}
                    </option>
                  ))}
                </select>
              </label>
              <p className="subtle">注文書自動生成とOCR補正の既定パターンです。</p>
              <label className="field">
                <span className="field-label">Menu Override Tags</span>
                <input
                  className="input"
                  value={menuOverrideTags}
                  onChange={(e) => setMenuOverrideTags(e.target.value)}
                  placeholder="larger"
                />
              </label>
              <p className="subtle">
                カンマ区切り。例: <code>larger</code>。`TAG:larger` のメニュー上書きを適用します。
              </p>
            </div>
            <div className="form-grid">
              <label className="field">
                <span className="field-label">Main OCR Provider</span>
                <select
                  className="input"
                  value={mainOcrProvider}
                  onChange={(e) => setMainOcrProvider(e.target.value)}
                >
                  <option value="pipeline">pipeline (default)</option>
                  <option value="tesseract">tesseract</option>
                  <option value="openai">openai</option>
                  <option value="gemini">gemini</option>
                </select>
              </label>
              <label className="field checkbox">
                <span className="field-label">OpenAI OCR Enabled</span>
                <input
                  type="checkbox"
                  checked={openaiOcrEnabled}
                  onChange={(e) => setOpenaiOcrEnabled(e.target.checked)}
                />
              </label>
              <label className="field">
                <span className="field-label">OpenAI Model</span>
                <input
                  className="input"
                  value={openaiOcrModel}
                  onChange={(e) => setOpenaiOcrModel(e.target.value)}
                  placeholder="gpt-4.1-mini"
                />
              </label>
              <label className="field">
                <span className="field-label">OpenAI Fallback</span>
                <select
                  className="input"
                  value={openaiFallbackProvider}
                  onChange={(e) => setOpenaiFallbackProvider(e.target.value)}
                >
                  <option value="pipeline">pipeline</option>
                  <option value="none">none</option>
                </select>
              </label>
              <label className="field">
                <span className="field-label">OpenAI Prompt</span>
                <textarea
                  className="textarea"
                  value={openaiOcrPrompt}
                  onChange={(e) => setOpenaiOcrPrompt(e.target.value)}
                  rows={5}
                  placeholder="施設固有のOCR指示（任意）"
                />
              </label>
              <label className="field checkbox">
                <span className="field-label">Gemini OCR Enabled</span>
                <input
                  type="checkbox"
                  checked={geminiOcrEnabled}
                  onChange={(e) => setGeminiOcrEnabled(e.target.checked)}
                />
              </label>
              <label className="field">
                <span className="field-label">Gemini Model</span>
                <input
                  className="input"
                  value={geminiOcrModel}
                  onChange={(e) => setGeminiOcrModel(e.target.value)}
                  placeholder="gemini-2.5-flash"
                />
              </label>
              <label className="field">
                <span className="field-label">Gemini Fallback</span>
                <select
                  className="input"
                  value={geminiFallbackProvider}
                  onChange={(e) => setGeminiFallbackProvider(e.target.value)}
                >
                  <option value="pipeline">pipeline</option>
                  <option value="none">none</option>
                </select>
              </label>
              <label className="field">
                <span className="field-label">Gemini Prompt</span>
                <textarea
                  className="textarea"
                  value={geminiOcrPrompt}
                  onChange={(e) => setGeminiOcrPrompt(e.target.value)}
                  rows={5}
                  placeholder="施設固有のOCR指示（任意）"
                />
              </label>
              <label className="field checkbox">
                <span className="field-label">Large Cell Mode</span>
                <input
                  type="checkbox"
                  checked={largeCellMode}
                  onChange={(e) => setLargeCellMode(e.target.checked)}
                />
                <span className="subtle">
                  結合セルを含む注文書向けに日付/時間帯/メニューの引き継ぎを強化します。
                </span>
              </label>
            </div>
            <details className="json-panel">
              <summary>Config JSON (上級)</summary>
              <textarea
                className="textarea json-textarea"
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
                rows={14}
                wrap="soft"
              />
            </details>
            <div className="actions">
              <button className="btn primary" onClick={saveConfig}>
                Save Config
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
