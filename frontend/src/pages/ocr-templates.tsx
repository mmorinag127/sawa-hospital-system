import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type TemplateEntry = {
  id: string;
  data: Record<string, unknown>;
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

const generateTemplateId = (existing: Set<string>) => {
  let candidate = "";
  for (let i = 0; i < 20; i += 1) {
    const suffix = Math.floor(Math.random() * 1_000_000)
      .toString()
      .padStart(6, "0");
    candidate = `TPL_${suffix}`;
    if (!existing.has(candidate)) {
      return candidate;
    }
  }
  return `TPL_${Date.now().toString().slice(-6)}`;
};

export default function OcrTemplatesPage() {
  const [templates, setTemplates] = useState<TemplateEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [templateText, setTemplateText] = useState("{}");
  const [templateId, setTemplateId] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const selectedTemplate = useMemo(() => {
    return templates.find((entry) => entry.id === selectedId) || null;
  }, [templates, selectedId]);

  const loadTemplates = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get("/ocr/templates");
      const items = Array.isArray(res.data.templates) ? res.data.templates : [];
      const normalized = items.map((entry: any) => ({
        id: String(entry.id || ""),
        data: (entry.data || {}) as Record<string, unknown>,
      }));
      setTemplates(normalized);
      if (normalized.length > 0) {
        setSelectedId(normalized[0].id);
        setTemplateId(normalized[0].id);
        setTemplateText(prettyJson(normalized[0].data));
      } else {
        setSelectedId("");
        setTemplateId("");
        setTemplateText("{}");
      }
      setMessage("");
    } catch (err) {
      setMessage("Failed to load templates.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTemplates();
  }, []);

  const selectTemplate = (id: string) => {
    const entry = templates.find((item) => item.id === id);
    if (!entry) return;
    setSelectedId(id);
    setTemplateId(id);
    setTemplateText(prettyJson(entry.data));
    setMessage("");
  };

  const createNewTemplate = () => {
    const existing = new Set(templates.map((item) => item.id));
    const id = generateTemplateId(existing);
    setSelectedId("");
    setTemplateId(id);
    setTemplateText(
      prettyJson({
        template_id: id,
        facility_id: "",
        version: 1,
        template_image_gcs_uri: "",
        match: { orb_nfeatures: 2000, min_matches: 25, min_inlier_ratio: 0.15 },
        warp: { output_size: [2480, 3508] },
        rois: {},
        postprocess: {},
      })
    );
    setMessage("New template draft created.");
  };

  const saveTemplate = async () => {
    if (!templateId) {
      setMessage("Template ID is required.");
      return;
    }
    const parsed = parseJson(templateText);
    if (parsed.error) {
      setMessage(`JSON error: ${parsed.error}`);
      return;
    }
    if (!parsed.value || typeof parsed.value !== "object") {
      setMessage("Template must be a JSON object.");
      return;
    }
    setLoading(true);
    try {
      await apiClient.put(`/ocr/templates/${templateId}`, {
        template: parsed.value,
      });
      await loadTemplates();
      setSelectedId(templateId);
      setMessage("Template saved.");
    } catch (err) {
      setMessage("Failed to save template.");
    } finally {
      setLoading(false);
    }
  };

  const deleteTemplate = async () => {
    if (!selectedId) {
      setMessage("Select a template to delete.");
      return;
    }
    setLoading(true);
    try {
      await apiClient.delete(`/ocr/templates/${selectedId}`);
      await loadTemplates();
      setMessage("Template deleted.");
    } catch (err) {
      setMessage("Failed to delete template.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">OCR Templates</p>
          <h1>OCR Template Registry</h1>
          <p className="subtle">Manage ROI templates and postprocess rules.</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>Templates</h2>
          <div className="actions">
            <button className="btn ghost" onClick={loadTemplates} disabled={loading}>
              Refresh
            </button>
            <button className="btn" onClick={createNewTemplate} disabled={loading}>
              New Template
            </button>
          </div>
        </header>
        <div className="template-grid">
          <div className="list">
            {templates.length === 0 && <p className="subtle">No templates found.</p>}
            {templates.map((entry) => (
              <button
                key={entry.id}
                className={`list-item ${entry.id === selectedId ? "active" : ""}`}
                onClick={() => selectTemplate(entry.id)}
              >
                <div>
                  <p className="list-title">{entry.id}</p>
                  <p className="list-meta">{String(entry.data.facility_id || "-")}</p>
                </div>
                <span className="ghost-link">Edit</span>
              </button>
            ))}
          </div>
          <div className="editor">
            <label className="field">
              <span className="field-label">Template ID</span>
              <input
                className="input"
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
                placeholder="TPL_000001"
              />
            </label>
            <label className="field">
              <span className="field-label">Template JSON</span>
              <textarea
                className="textarea"
                value={templateText}
                onChange={(e) => setTemplateText(e.target.value)}
                rows={20}
              />
            </label>
            <div className="actions">
              <button className="btn ghost" onClick={deleteTemplate} disabled={loading}>
                Delete
              </button>
              <button className="btn primary" onClick={saveTemplate} disabled={loading}>
                Save
              </button>
            </div>
          </div>
        </div>
      </section>

      {selectedTemplate?.data?.template_image_gcs_uri && (
        <section className="panel">
          <header className="panel-header">
            <h2>Template Image</h2>
          </header>
          <p className="subtle">
            {String(selectedTemplate.data.template_image_gcs_uri)}
          </p>
        </section>
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
          flex-wrap: wrap;
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
          box-shadow: 0 20px 50px rgba(23, 30, 28, 0.08);
          margin-bottom: 24px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        }

        .template-grid {
          display: grid;
          grid-template-columns: minmax(220px, 1fr) minmax(320px, 2fr);
          gap: 20px;
        }

        .list {
          display: grid;
          gap: 12px;
        }

        .list-item {
          background: #f7f7f4;
          border: 1px solid rgba(24, 32, 30, 0.08);
          border-radius: 14px;
          padding: 14px 16px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          text-align: left;
          cursor: pointer;
        }

        .list-item.active {
          border-color: rgba(55, 94, 86, 0.45);
          background: rgba(225, 236, 231, 0.65);
        }

        .list-title {
          margin: 0;
          font-weight: 600;
        }

        .list-meta {
          margin: 4px 0 0;
          font-size: 12px;
          color: #6a7c76;
        }

        .ghost-link {
          color: #1f2a2a;
          font-weight: 600;
          opacity: 0.6;
        }

        .editor {
          display: grid;
          gap: 14px;
        }

        .field {
          display: grid;
          gap: 8px;
        }

        .field-label {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.12em;
          color: #5f7b74;
        }

        .textarea,
        .input {
          width: 100%;
          border-radius: 12px;
          border: 1px solid rgba(28, 33, 31, 0.14);
          padding: 12px 14px;
          font-family: "JetBrains Mono", "SFMono-Regular", ui-monospace, monospace;
          background: #fbfaf7;
          color: #1f2a2a;
        }

        .actions {
          display: flex;
          justify-content: flex-end;
          gap: 12px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 18px;
          background: #dfe6e1;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: transparent;
          border: 1px solid rgba(24, 32, 30, 0.2);
        }

        .message {
          margin-top: 16px;
          color: #1f2a2a;
          font-weight: 600;
        }

        @media (max-width: 900px) {
          .template-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </main>
  );
}
