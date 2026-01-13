import { useEffect, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type QueueEntry = {
  id: string;
  data: Record<string, any>;
};

const prettyJson = (value: unknown) => JSON.stringify(value ?? {}, null, 2);

const toHttpUrl = (uri: string) => {
  if (uri.startsWith("gs://")) {
    const [, rest] = uri.split("gs://");
    const [bucket, ...parts] = rest.split("/");
    return `https://storage.googleapis.com/${bucket}/${parts.join("/")}`;
  }
  return uri;
};

export default function OcrQueuePage() {
  const [items, setItems] = useState<QueueEntry[]>([]);
  const [statusFilter, setStatusFilter] = useState("pending");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [templateInputs, setTemplateInputs] = useState<Record<string, string>>({});

  const loadQueue = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get("/ocr/unclassified", {
        params: statusFilter ? { status: statusFilter } : undefined,
      });
      const data = Array.isArray(res.data.items) ? res.data.items : [];
      const normalized = data.map((entry: any) => ({
        id: String(entry.id || ""),
        data: entry.data || {},
      }));
      setItems(normalized);
      setTemplateInputs((prev) => {
        const next: Record<string, string> = {};
        for (const item of normalized) {
          next[item.id] = prev[item.id] || "";
        }
        return next;
      });
      setMessage("");
    } catch (err) {
      setMessage("Failed to load queue.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadQueue();
  }, [statusFilter]);

  const resolveJob = async (jobId: string, templateId: string) => {
    if (!templateId) {
      setMessage("Template ID is required to resolve.");
      return;
    }
    setLoading(true);
    try {
      await apiClient.post(`/ocr/unclassified/${jobId}/resolve`, {
        template_id: templateId,
      });
      await loadQueue();
      setMessage(`Resolved ${jobId}.`);
    } catch (err) {
      setMessage("Failed to resolve job.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">OCR Queue</p>
          <h1>Unclassified Jobs</h1>
          <p className="subtle">Review failed template matches and assign a template.</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <div className="filters">
            <label className="field">
              <span className="field-label">Status</span>
              <select
                className="input"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">All</option>
                <option value="pending">Pending</option>
                <option value="resolved">Resolved</option>
              </select>
            </label>
          </div>
          <button className="btn ghost" onClick={loadQueue} disabled={loading}>
            Refresh
          </button>
        </header>

        {items.length === 0 && <p className="subtle">No jobs found.</p>}

        <div className="queue-grid">
          {items.map((entry) => {
            const artifacts = entry.data.artifacts || {};
            const diagnostics = entry.data.diagnostics || {};
            const input = entry.data.input || {};
            return (
              <article key={entry.id} className="queue-card">
                <header className="queue-header">
                  <div>
                    <p className="queue-title">{entry.id}</p>
                    <p className="queue-meta">
                      {String(input.bucket || "-")}/{String(input.name || "-")}
                    </p>
                  </div>
                  <span className="badge">{String(entry.data.status || "pending")}</span>
                </header>
                <div className="artifact-grid">
                  {Object.keys(artifacts).length === 0 && (
                    <p className="subtle">No artifacts.</p>
                  )}
                  {Object.entries(artifacts).map(([key, uri]) => (
                    <a
                      key={key}
                      className="artifact"
                      href={toHttpUrl(String(uri))}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <span className="artifact-label">{key}</span>
                      <span className="artifact-uri">{String(uri)}</span>
                    </a>
                  ))}
                </div>
                <details className="details">
                  <summary>Diagnostics</summary>
                  <pre className="code-block">{prettyJson(diagnostics)}</pre>
                </details>
                <div className="resolve">
                  <input
                    className="input"
                    placeholder="Template ID"
                    value={templateInputs[entry.id] || ""}
                    onChange={(e) =>
                      setTemplateInputs((prev) => ({
                        ...prev,
                        [entry.id]: e.target.value,
                      }))
                    }
                  />
                  <button
                    className="btn primary"
                    onClick={() => resolveJob(entry.id, templateInputs[entry.id] || "")}
                    disabled={loading}
                  >
                    Resolve
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>

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

        .filters {
          display: flex;
          gap: 12px;
          align-items: center;
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

        .input {
          border-radius: 12px;
          border: 1px solid rgba(28, 33, 31, 0.14);
          padding: 10px 12px;
          background: #fbfaf7;
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

        .queue-grid {
          display: grid;
          gap: 16px;
        }

        .queue-card {
          background: #f9f7f0;
          border-radius: 16px;
          padding: 16px;
          border: 1px solid rgba(24, 32, 30, 0.08);
          display: grid;
          gap: 12px;
        }

        .queue-header {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: center;
        }

        .queue-title {
          margin: 0;
          font-weight: 600;
        }

        .queue-meta {
          margin: 4px 0 0;
          font-size: 12px;
          color: #6a7c76;
        }

        .badge {
          padding: 6px 12px;
          border-radius: 999px;
          background: rgba(42, 82, 74, 0.12);
          font-size: 12px;
          font-weight: 600;
        }

        .artifact-grid {
          display: grid;
          gap: 8px;
        }

        .artifact {
          display: grid;
          gap: 4px;
          padding: 10px 12px;
          border-radius: 12px;
          background: #ffffff;
          border: 1px solid rgba(24, 32, 30, 0.12);
        }

        .artifact-label {
          font-size: 12px;
          color: #5f7b74;
          text-transform: uppercase;
          letter-spacing: 0.12em;
        }

        .artifact-uri {
          font-size: 12px;
          color: #1f2a2a;
          word-break: break-all;
        }

        .details summary {
          cursor: pointer;
          font-weight: 600;
        }

        .code-block {
          margin-top: 8px;
          background: #111413;
          color: #f4f0e6;
          padding: 12px;
          border-radius: 12px;
          font-size: 12px;
          overflow-x: auto;
        }

        .resolve {
          display: flex;
          gap: 12px;
          align-items: center;
        }

        .message {
          margin-top: 16px;
          color: #1f2a2a;
          font-weight: 600;
        }
      `}</style>
    </main>
  );
}
