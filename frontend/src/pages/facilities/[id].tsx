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

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

const readFacilityPrompt = (config?: Record<string, unknown>) => {
  if (!config) return "";
  const direct =
    config.main_ocr_facility_prompt || config.ocr_prompt || config.facility_prompt;
  return typeof direct === "string" ? direct : "";
};

const parseJson = (text: string) => {
  try {
    return { value: JSON.parse(text), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON";
    return { value: null, error: message };
  }
};

export default function FacilityConfigPage() {
  const router = useRouter();
  const { id } = router.query;
  const [facility, setFacility] = useState<Facility | null>(null);
  const [name, setName] = useState("");
  const [areasText, setAreasText] = useState("[]");
  const [configText, setConfigText] = useState("{}");
  const [resolvedText, setResolvedText] = useState("{}");
  const [facilityPrompt, setFacilityPrompt] = useState("");
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");

  const loadFacility = async () => {
    if (!id || Array.isArray(id)) return;
    setLoadError("");
    try {
      const res = await apiClient.get<FacilityResponse>(`/facilities/${id}`);
      const data = res.data;
      setFacility(data.facility);
      setName(data.facility.name || "");
      setAreasText(prettyJson(data.facility.areas || []));
      setConfigText(prettyJson(data.config || {}));
      setResolvedText(prettyJson(data.resolved_config || {}));
      setFacilityPrompt(readFacilityPrompt(data.config));
      setValidation(data.validation || null);
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
    const nextConfig = { ...(parsed.value as Record<string, unknown>) };
    const prompt = facilityPrompt.trim();
    if (prompt) {
      nextConfig.main_ocr_facility_prompt = prompt;
    } else {
      delete nextConfig.main_ocr_facility_prompt;
    }
    try {
      setConfigText(prettyJson(nextConfig));
      const res = await apiClient.put(`/facilities/${facility.id}/config`, nextConfig);
      if (res.data?.validation) {
        setValidation(res.data.validation);
      }
      if (res.data?.resolved_config) {
        setResolvedText(prettyJson(res.data.resolved_config));
      }
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
                <span className="field-label">OCR Prompt (施設別)</span>
                <textarea
                  className="textarea"
                  value={facilityPrompt}
                  onChange={(e) => setFacilityPrompt(e.target.value)}
                  rows={10}
                  placeholder="未設定の場合はデフォルトプロンプトが使われます。"
                />
              </label>
            </div>
            <textarea
              className="textarea"
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              rows={14}
            />
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
            <pre className="code-block">{resolvedText}</pre>
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
