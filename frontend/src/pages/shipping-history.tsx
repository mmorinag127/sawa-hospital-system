import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type ShippingHistoryItem = {
  id: string;
  tracking_key: string;
  tracking_number: string;
  facility_name?: string | null;
  status: string;
  delivered: boolean;
  arrival_text?: string | null;
  error?: string | null;
  source?: string | null;
  looked_up_at?: string | null;
};

type ShippingHistorySummary = {
  total: number;
  delivered: number;
  pending: number;
  errors: number;
  all_delivered: boolean;
};

type QuotaStatus = {
  resource?: string;
  unit?: string;
  used?: number;
  limit?: number;
  ratio?: number | null;
  alert_level?: "ok" | "warning" | "critical" | "unknown" | string;
  message?: string;
};

const extractFilename = (value?: string | null) => {
  if (!value) return "";
  const match = value.match(/filename\\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
  const rawName = match?.[1] || match?.[2] || "";
  if (!rawName) return "";
  try {
    return decodeURIComponent(rawName);
  } catch {
    return rawName;
  }
};

const formatLocalDate = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const formatRatio = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
};

const toDateInput = (date: Date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

const buildDefaultRange = () => {
  const end = new Date();
  const start = new Date(end.getTime() - 29 * 24 * 60 * 60 * 1000);
  return { start: toDateInput(start), end: toDateInput(end) };
};

export default function ShippingHistoryPage() {
  const defaultRange = useMemo(buildDefaultRange, []);
  const [dateFrom, setDateFrom] = useState<string>(defaultRange.start);
  const [dateTo, setDateTo] = useState<string>(defaultRange.end);
  const [limit, setLimit] = useState<number>(200);
  const [loading, setLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");
  const [items, setItems] = useState<ShippingHistoryItem[]>([]);
  const [summary, setSummary] = useState<ShippingHistorySummary | null>(null);
  const [quota, setQuota] = useState<QuotaStatus | null>(null);

  const loadHistory = async () => {
    setLoading(true);
    setMessage("履歴を取得中です...");
    try {
      const res = await apiClient.get("/shipping/status/history", {
        params: {
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          limit: Math.max(1, Math.min(limit, 1000)),
        },
      });
      const nextItems = Array.isArray(res.data?.items) ? res.data.items : [];
      setItems(nextItems);
      setSummary(res.data?.summary || null);
      setQuota(res.data?.quota || null);
      setMessage(`履歴を取得しました（${nextItems.length}件）。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setItems([]);
      setSummary(null);
      setQuota(null);
    } finally {
      setLoading(false);
    }
  };

  const downloadHistory = async (format: "csv" | "json") => {
    setMessage(`履歴${format.toUpperCase()}を作成中です...`);
    try {
      const res = await apiClient.get("/shipping/status/export", {
        params: {
          format,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          limit: 1000000,
        },
        responseType: "blob",
      });
      const contentDisposition =
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || `shipping_tracking_history.${format}`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setMessage(`履歴${format.toUpperCase()}をダウンロードしました。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const clearAllHistory = async () => {
    const ok = window.confirm(
      "佐川追跡履歴を全件削除します。取り消せません。実行してよいですか？",
    );
    if (!ok) return;
    setMessage("履歴を削除中です...");
    try {
      const res = await apiClient.delete("/shipping/status/history");
      const removed = Number(res.data?.removed || 0);
      setMessage(`履歴を削除しました（${removed}件）。`);
      await loadHistory();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `削除に失敗しました: ${detail}` : "削除に失敗しました。");
    }
  };

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Shipping History</p>
          <h1>佐川追跡履歴</h1>
          <p className="subtle">これまで照会した伝票番号の履歴を確認できます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>検索条件</h2>
          <Link href="/shipping" className="ghost-link">
            送り状ページへ
          </Link>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">開始日</span>
            <input
              className="input"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">終了日</span>
            <input
              className="input"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">件数上限</span>
            <input
              className="input"
              type="number"
              min={1}
              max={1000}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value || 200))}
            />
          </label>
          <button className="btn primary" onClick={loadHistory} disabled={loading}>
            {loading ? "取得中..." : "履歴を更新"}
          </button>
          <button className="btn ghost" onClick={() => downloadHistory("csv")} disabled={loading}>
            DBをCSVで保存
          </button>
          <button className="btn ghost" onClick={() => downloadHistory("json")} disabled={loading}>
            DBをJSONで保存
          </button>
          <button className="btn danger" onClick={clearAllHistory} disabled={loading}>
            全件クリア（管理者）
          </button>
        </div>
        {message ? <p className="message">{message}</p> : null}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>履歴一覧</h2>
        </header>
        {summary ? (
          <div className="summary">
            <p>総件数: {summary.total}</p>
            <p>配達完了: {summary.delivered}</p>
            <p>未完了: {summary.pending}</p>
            <p>照会失敗: {summary.errors}</p>
          </div>
        ) : null}
        {quota ? (
          <div className={`quota-alert quota-${quota.alert_level || "unknown"}`}>
            <p className="quota-title">Quota 状態: {quota.alert_level || "unknown"}</p>
            <p className="quota-meta">
              使用量: {quota.used ?? "-"} / 上限: {quota.limit ?? "-"} ({formatRatio(quota.ratio)})
            </p>
            <p className="quota-meta">対象: {quota.resource || "-"} / 単位: {quota.unit || "-"}</p>
            <p className="quota-meta">{quota.message || "quota情報なし"}</p>
          </div>
        ) : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>照会日時</th>
                <th>伝票番号</th>
                <th>施設名</th>
                <th>状態</th>
                <th>到着日時</th>
                <th>取得元</th>
                <th>エラー</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={7}>履歴がありません。</td>
                </tr>
              ) : (
                items.map((item) => (
                  <tr key={item.id}>
                    <td>{formatLocalDate(item.looked_up_at)}</td>
                    <td>{item.tracking_number || item.tracking_key}</td>
                    <td>{item.facility_name || "-"}</td>
                    <td>{item.status || "-"}</td>
                    <td>{item.arrival_text || "-"}</td>
                    <td>{item.source || "-"}</td>
                    <td>{item.error || "-"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
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

        .ghost-link {
          font-size: 13px;
          color: #5f7b74;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          align-items: end;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          justify-self: start;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .btn.ghost {
          background: #eef3f1;
        }

        .btn.danger {
          background: #7a2d2d;
          color: #fff6f6;
        }

        .btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .summary {
          margin-bottom: 12px;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbf8ef;
          font-size: 13px;
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 8px;
        }

        .quota-alert {
          margin-bottom: 12px;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #f5f8f6;
          font-size: 13px;
          display: grid;
          gap: 4px;
        }

        .quota-warning {
          background: #fff4df;
          border-color: rgba(173, 102, 0, 0.35);
        }

        .quota-critical {
          background: #ffe7e7;
          border-color: rgba(170, 45, 45, 0.35);
        }

        .quota-title {
          margin: 0;
          font-weight: 700;
        }

        .quota-meta {
          margin: 0;
        }

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
          white-space: nowrap;
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
        }

        thead {
          background: #f4f1ea;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
