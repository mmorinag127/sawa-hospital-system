import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type ProcessLogItem = {
  id: string;
  source: string;
  process_type: string;
  status?: string | null;
  title?: string | null;
  target?: string | null;
  occurred_at?: string | null;
  actor?: string | null;
  summary?: string | null;
  details?: Record<string, unknown>;
};

type ProcessLogResponse = {
  limit?: number;
  count?: number;
  items?: ProcessLogItem[];
};

const formatDate = (value?: string | null) => {
  if (!value) return "未取得";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未取得";
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const typeLabel = (value?: string | null) => {
  const normalized = (value || "").toLowerCase();
  if (normalized === "audit") return "監査";
  if (normalized === "ingest") return "取込";
  if (normalized === "ocr") return "OCR";
  if (normalized === "uploaded_pdf") return "PDF";
  if (normalized === "shipping_tracking") return "配送";
  return value || "処理";
};

const statusLabel = (value?: string | null) => {
  const normalized = (value || "").toLowerCase();
  if (!normalized) return "未取得";
  if (["done", "completed", "success", "ok", "recorded"].includes(normalized)) return "完了";
  if (["running", "processing", "pending", "queued", "awaiting_output", "recovering"].includes(normalized)) {
    return "処理中";
  }
  if (["failed", "error", "hard_failed"].includes(normalized)) return "失敗";
  return value || "未取得";
};

const statusTone = (value?: string | null) => {
  const normalized = (value || "").toLowerCase();
  if (["done", "completed", "success", "ok", "recorded"].includes(normalized)) return "ok";
  if (["failed", "error", "hard_failed"].includes(normalized)) return "error";
  if (["running", "processing", "pending", "queued", "awaiting_output", "recovering"].includes(normalized)) {
    return "active";
  }
  return "neutral";
};

export default function SystemProcessLogsPage() {
  const [items, setItems] = useState<ProcessLogItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) || items[0] || null,
    [items, selectedId]
  );

  const loadLogs = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get<ProcessLogResponse>("/system/process-logs", {
        params: { limit: 100 },
      });
      const nextItems = Array.isArray(res.data?.items) ? res.data.items : [];
      setItems(nextItems);
      setSelectedId((current) => (nextItems.some((item) => item.id === current) ? current : nextItems[0]?.id || ""));
      setError("");
    } catch {
      setItems([]);
      setSelectedId("");
      setError("処理ログの取得に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLogs();
  }, []);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">System Process Logs</p>
          <h1>処理ログ</h1>
          <p className="subtle">直近100件の処理を1行ずつ確認できます。</p>
        </div>
        <TopNav />
      </header>

      <section className="toolbar" aria-label="処理ログ操作">
        <div>
          <p className="toolbar-count">{loading ? "読込中" : `${items.length.toLocaleString("ja-JP")} 件`}</p>
          <p className="subtle">取込、OCR、アップロード、配送、監査ログを時系列で表示します。</p>
        </div>
        <button className="btn" type="button" onClick={loadLogs} disabled={loading}>
          再取得
        </button>
      </section>

      {error ? <p className="error">{error}</p> : null}

      <section className="log-layout">
        <div className="log-list" aria-label="処理ログ一覧">
          <div className="log-header">
            <span>時刻</span>
            <span>種別</span>
            <span>状態</span>
            <span>処理</span>
            <span>対象</span>
          </div>
          {items.length > 0 ? (
            items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`log-row${selected?.id === item.id ? " active" : ""}`}
                onClick={() => setSelectedId(item.id)}
              >
                <span className="time">{formatDate(item.occurred_at)}</span>
                <span>{typeLabel(item.process_type)}</span>
                <span className={`status ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                <span className="title">{item.title || "-"}</span>
                <span className="target">{item.target || item.summary || "-"}</span>
              </button>
            ))
          ) : (
            <div className="empty">{loading ? "読み込んでいます。" : "処理ログがありません。"}</div>
          )}
        </div>

        <aside className="detail" aria-label="処理ログ詳細">
          {selected ? (
            <>
              <div className="detail-head">
                <p className="eyebrow">{typeLabel(selected.process_type)}</p>
                <h2>{selected.title || selected.id}</h2>
                <span className={`status ${statusTone(selected.status)}`}>{statusLabel(selected.status)}</span>
              </div>
              <dl className="detail-list">
                <div>
                  <dt>時刻</dt>
                  <dd>{formatDate(selected.occurred_at)}</dd>
                </div>
                <div>
                  <dt>対象</dt>
                  <dd>{selected.target || "-"}</dd>
                </div>
                <div>
                  <dt>実行者</dt>
                  <dd>{selected.actor || "-"}</dd>
                </div>
                <div>
                  <dt>ソース</dt>
                  <dd>{selected.source}</dd>
                </div>
                <div>
                  <dt>概要</dt>
                  <dd>{selected.summary || "-"}</dd>
                </div>
              </dl>
              <pre className="json">{JSON.stringify(selected.details || selected, null, 2)}</pre>
            </>
          ) : (
            <p className="empty">行を選択すると詳細を表示します。</p>
          )}
        </aside>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }
        :global(*) {
          box-sizing: border-box;
        }
        .page {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }
        .hero {
          align-items: center;
          display: flex;
          flex-wrap: wrap;
          gap: 24px;
          justify-content: space-between;
          margin-bottom: 32px;
        }
        .eyebrow {
          color: #5f7b74;
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.12em;
          margin: 0 0 8px;
          text-transform: uppercase;
        }
        h1,
        h2,
        p {
          margin: 0;
        }
        h1 {
          font-size: clamp(28px, 4vw, 40px);
          line-height: 1.2;
          margin-bottom: 12px;
        }
        h2 {
          font-size: 18px;
          line-height: 1.35;
        }
        .subtle {
          color: #51615c;
          font-size: 14px;
          line-height: 1.6;
        }
        .toolbar,
        .log-layout {
          margin: 0;
          max-width: none;
        }
        .toolbar {
          align-items: center;
          background: #ffffff;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 18px;
          box-shadow: 0 10px 30px rgba(27, 35, 33, 0.08);
          display: flex;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 18px;
          padding: 18px 20px;
        }
        .toolbar-count {
          font-size: 22px;
          font-weight: 800;
        }
        .btn {
          background: #1f2a2a;
          border: 0;
          border-radius: 999px;
          color: #f7f2e7;
          cursor: pointer;
          font-size: 14px;
          font-weight: 700;
          min-height: 40px;
          padding: 0 16px;
        }
        .btn:disabled {
          cursor: not-allowed;
          opacity: 0.55;
        }
        .error {
          background: #fff2f0;
          border: 1px solid #f1b2ab;
          border-radius: 12px;
          color: #9b2c22;
          margin: 0 0 16px;
          padding: 12px 14px;
        }
        .log-layout {
          align-items: start;
          display: grid;
          gap: 18px;
          grid-template-columns: minmax(0, 1fr) 420px;
        }
        .log-list,
        .detail {
          background: #fff;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 18px;
          box-shadow: 0 10px 30px rgba(27, 35, 33, 0.08);
          overflow: hidden;
        }
        .log-header,
        .log-row {
          display: grid;
          grid-template-columns: 190px 72px 82px minmax(140px, 0.85fr) minmax(180px, 1.15fr);
          gap: 10px;
        }
        .log-header {
          background: #f6f1e6;
          color: #55625e;
          font-size: 12px;
          font-weight: 800;
          padding: 11px 14px;
        }
        .log-row {
          align-items: center;
          background: #fff;
          border: 0;
          border-top: 1px solid #edf1ef;
          color: inherit;
          cursor: pointer;
          font: inherit;
          min-height: 48px;
          padding: 9px 14px;
          text-align: left;
          width: 100%;
        }
        .log-row:hover,
        .log-row.active {
          background: #f4f7f6;
        }
        .log-row span,
        .log-header span {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .time {
          color: #47524f;
          font-variant-numeric: tabular-nums;
        }
        .title {
          font-weight: 700;
        }
        .target {
          color: #56635f;
        }
        .status {
          border-radius: 999px;
          display: inline-flex;
          font-size: 12px;
          font-weight: 800;
          justify-content: center;
          line-height: 1;
          padding: 6px 8px;
          width: fit-content;
        }
        .status.ok {
          background: #e7f5ed;
          color: #14633f;
        }
        .status.active {
          background: #fff4d7;
          color: #7c5700;
        }
        .status.error {
          background: #ffe6e1;
          color: #9b2c22;
        }
        .status.neutral {
          background: #eef0f2;
          color: #4f5b64;
        }
        .detail {
          padding: 18px;
          position: sticky;
          top: 18px;
        }
        .detail-head {
          align-items: start;
          display: grid;
          gap: 8px;
          margin-bottom: 16px;
        }
        .detail-list {
          display: grid;
          gap: 10px;
          margin: 0 0 14px;
        }
        .detail-list div {
          display: grid;
          gap: 4px;
        }
        dt {
          color: #66736f;
          font-size: 12px;
          font-weight: 800;
        }
        dd {
          margin: 0;
          overflow-wrap: anywhere;
        }
        .json {
          background: #17211f;
          border-radius: 12px;
          color: #e8f4ef;
          font-size: 12px;
          line-height: 1.55;
          margin: 0;
          max-height: 520px;
          overflow: auto;
          padding: 14px;
          white-space: pre-wrap;
        }
        .empty {
          color: #66736f;
          padding: 18px;
        }
        @media (max-width: 980px) {
          .page {
            padding: 28px 16px 48px;
          }
          .toolbar {
            align-items: stretch;
            flex-direction: column;
          }
          .log-layout {
            grid-template-columns: 1fr;
          }
          .detail {
            position: static;
          }
          .log-list {
            overflow-x: auto;
          }
          .log-header,
          .log-row {
            grid-template-columns: 170px 64px 76px 150px 220px;
            min-width: 720px;
          }
        }
      `}</style>
    </main>
  );
}
