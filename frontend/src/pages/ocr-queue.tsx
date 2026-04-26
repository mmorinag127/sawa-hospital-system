import { useEffect, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type QueueEntry = {
  id: string;
  data: Record<string, any>;
};

type SystemStatus = {
  ocr_pipeline?: {
    status?: string | null;
    last_success_at?: string | null;
    last_error_at?: string | null;
    last_error?: string | null;
    inflight?: number | null;
    max_inflight?: number | null;
  };
  uploaded_pdfs?: {
    pending_count?: number;
    processing_count?: number;
    completed_count?: number;
    eligible_backlog_count?: number;
  };
  ingest_jobs?: {
    pending_count?: number;
    processing_count?: number;
    stale_processing_count?: number;
    eligible_backlog_count?: number;
  };
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

const formatDate = (value?: string | null) => {
  if (!value) return "未取得";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未取得";
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const formatStatus = (value?: string | null) => {
  const raw = (value || "").toLowerCase();
  if (!raw) return "未取得";
  if (raw === "ok") return "OK";
  if (raw === "error") return "エラー";
  if (raw === "running") return "実行中";
  if (raw === "misconfigured") return "未設定";
  return value || "未取得";
};

export default function OcrQueuePage() {
  const [items, setItems] = useState<QueueEntry[]>([]);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [statusFilter, setStatusFilter] = useState("pending");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [bulkRecoverPending, setBulkRecoverPending] = useState(false);
  const [templateInputs, setTemplateInputs] = useState<Record<string, string>>({});

  const loadQueue = async () => {
    setLoading(true);
    const nextMessages: string[] = [];
    try {
      const [queueResult, statusResult] = await Promise.allSettled([
        apiClient.get("/ocr/unclassified", {
          params: statusFilter ? { status: statusFilter } : undefined,
        }),
        apiClient.get("/system/status"),
      ]);

      if (statusResult.status === "fulfilled") {
        setSystemStatus(statusResult.value.data || {});
      } else {
        setSystemStatus(null);
        nextMessages.push("OCR状態の取得に失敗しました。");
      }

      if (queueResult.status === "fulfilled") {
        const data = Array.isArray(queueResult.value.data.items) ? queueResult.value.data.items : [];
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
      } else {
        setItems([]);
        nextMessages.push("OCRキューの取得に失敗しました。");
      }
    } catch (err) {
      nextMessages.push("OCRキューの取得に失敗しました。");
    } finally {
      setMessage(nextMessages.join(" "));
      setLoading(false);
    }
  };

  useEffect(() => {
    loadQueue();
  }, [statusFilter]);

  const uploaded = systemStatus?.uploaded_pdfs;
  const ingest = systemStatus?.ingest_jobs;
  const pipeline = systemStatus?.ocr_pipeline;

  const resolveJob = async (jobId: string, templateId: string) => {
    if (!templateId) {
      setMessage("テンプレートIDを入力してください。");
      return;
    }
    setLoading(true);
    try {
      await apiClient.post(`/ocr/unclassified/${jobId}/resolve`, {
        template_id: templateId,
      });
      await loadQueue();
      setMessage(`${jobId} を解決しました。`);
    } catch (err) {
      setMessage("テンプレート解決に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  const recoverReadyQueue = async () => {
    if (bulkRecoverPending) return;
    setBulkRecoverPending(true);
    setMessage("滞留しているOCRジョブをまとめて再試行しています。");
    try {
      const res = await apiClient.post("/ingest/recover-ready");
      const body = res.data || {};
      await loadQueue();
      setMessage(
        `まとめて再試行を開始しました。ingest=${body.ingest_enqueued ?? 0}件 / uploaded=${body.uploaded_enqueued ?? 0}件 / ocr=${body.ocr_recovered ?? 0}件`
      );
    } catch (_err) {
      setMessage("まとめて再試行に失敗しました。");
    } finally {
      setBulkRecoverPending(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">OCR Operations</p>
          <h1>OCRキュー</h1>
          <p className="subtle">未分類ジョブと OCR パイプラインの滞留状況を同じ画面で確認します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <div className="queue-status-banner">
          <div className="queue-status-main">
            <p className="queue-status-label">OCR パイプライン</p>
            <p className="queue-status-value">{formatStatus(pipeline?.status)}</p>
            <p className="queue-status-meta">
              最終成功: {formatDate(pipeline?.last_success_at)} / 最終失敗: {formatDate(pipeline?.last_error_at)}
            </p>
            <p className="queue-status-meta">
              稼働中: {pipeline?.inflight ?? "未取得"} / {pipeline?.max_inflight ?? "未取得"}
            </p>
            {pipeline?.last_error ? <p className="queue-status-error">エラー: {pipeline.last_error}</p> : null}
          </div>
          <div className="queue-status-counts" aria-label="OCRキュー件数">
            <div className="queue-status-count-card">
              <p className="queue-status-count-label">uploaded</p>
              <p className="queue-status-count-value">
                {uploaded?.pending_count ?? "未取得"} / {uploaded?.processing_count ?? "未取得"} / {uploaded?.completed_count ?? "未取得"}
              </p>
              <p className="queue-status-count-help">未処理 / 処理中 / 完了</p>
              <p className="queue-status-count-help">backlog: {uploaded?.eligible_backlog_count ?? "未取得"}</p>
            </div>
            <div className="queue-status-count-card">
              <p className="queue-status-count-label">ingest</p>
              <p className="queue-status-count-value">
                {ingest?.pending_count ?? "未取得"} / {ingest?.processing_count ?? "未取得"} / {ingest?.stale_processing_count ?? "未取得"}
              </p>
              <p className="queue-status-count-help">未処理 / 処理中 / stale</p>
              <p className="queue-status-count-help">backlog: {ingest?.eligible_backlog_count ?? "未取得"}</p>
            </div>
          </div>
        </div>
        <header className="panel-header">
          <div className="filters">
            <label className="field">
              <span className="field-label">Status</span>
              <select
                className="input"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">すべて</option>
                <option value="pending">未処理</option>
                <option value="resolved">解決済み</option>
              </select>
            </label>
          </div>
          <div className="panel-actions">
            <button className="btn" onClick={recoverReadyQueue} disabled={loading || bulkRecoverPending}>
              {bulkRecoverPending ? "まとめて再試行中..." : "滞留をまとめて再試行"}
            </button>
            <button className="btn ghost" onClick={loadQueue} disabled={loading || bulkRecoverPending}>
              再取得
            </button>
          </div>
        </header>

        {items.length === 0 && <p className="subtle">対象ジョブはありません。</p>}

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
                    <p className="subtle">成果物はありません。</p>
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
                  <summary>診断情報</summary>
                  <pre className="code-block">{prettyJson(diagnostics)}</pre>
                </details>
                <div className="resolve">
                  <input
                    className="input"
                    placeholder="テンプレートID"
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
                    解決
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
          font-size: clamp(28px, 4vw, 40px);
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
        .queue-status-banner {
          display: grid;
          grid-template-columns: minmax(240px, 1.2fr) minmax(320px, 1fr);
          gap: 16px;
          margin-bottom: 18px;
          padding-bottom: 18px;
          border-bottom: 1px solid rgba(24, 32, 30, 0.08);
        }
        .queue-status-main {
          display: grid;
          gap: 8px;
          align-content: start;
        }
        .queue-status-label {
          margin: 0;
          font-size: 11px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: #5f7b74;
          font-weight: 700;
        }
        .queue-status-value {
          margin: 0;
          font-size: 20px;
          font-weight: 700;
        }
        .queue-status-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }
        .queue-status-error {
          margin: 0;
          font-size: 12px;
          color: #b94014;
          line-height: 1.5;
          word-break: break-word;
        }
        .queue-status-counts {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }
        .queue-status-count-card {
          background: #f7f8f5;
          border: 1px solid rgba(24, 32, 30, 0.08);
          border-radius: 14px;
          padding: 12px 14px;
          display: grid;
          gap: 4px;
        }
        .queue-status-count-label {
          margin: 0;
          font-size: 11px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: #5f7b74;
          font-weight: 700;
        }
        .queue-status-count-value {
          margin: 0;
          font-size: 22px;
          font-weight: 700;
          color: #1f2a2a;
        }
        .queue-status-count-help {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
          gap: 12px;
        }
        .panel-actions {
          display: flex;
          gap: 10px;
          align-items: center;
          flex-wrap: wrap;
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
          font: inherit;
          border-radius: 12px;
          border: 1px solid rgba(28, 33, 31, 0.14);
          padding: 10px 12px;
          background: #fbfaf7;
        }

        .btn {
          font: inherit;
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
          font-weight: 700;
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
        @media (max-width: 980px) {
          .queue-status-banner {
            grid-template-columns: 1fr;
          }
          .queue-status-counts {
            grid-template-columns: 1fr;
          }
          .panel-header {
            align-items: stretch;
          }
          .panel-actions {
            width: 100%;
            justify-content: flex-start;
          }
        }
      `}</style>
    </main>
  );
}
