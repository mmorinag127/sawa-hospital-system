import { useEffect, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidates,
  pickBestFacilityCandidate,
  type FacilityHint,
  type FacilityNameMap,
} from "../services/facilityData";

type OrderSummary = {
  id: string;
  facility?: string | null;
  week?: string | null;
  status?: string | null;
  received_at?: string | null;
  line_count?: number | null;
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
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

const headerValueToString = (value: unknown) => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => String(item)).join("; ");
  if (value == null) return "";
  return String(value);
};

export default function DailyDeliveryNotesPage() {
  const [date, setDate] = useState<string>("");
  const [status, setStatus] = useState<string>("確定");
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});

  useEffect(() => {
    if (!date) {
      const today = new Date();
      setDate(today.toISOString().slice(0, 10));
    }
  }, [date]);

  useEffect(() => {
    let cancelled = false;
    fetchFacilityNameMap()
      .then((map) => {
        if (!cancelled) setFacilityNameMap(map);
      })
      .catch(() => {
        if (!cancelled) setFacilityNameMap({});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const unresolved = orders
      .filter((order) => !order.facility && order.id)
      .slice(0, 50)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !facilityHints[orderId]);

    if (unresolved.length === 0) return;

    const queue = [...unresolved];
    const results: Record<string, FacilityHint> = {};
    const workers = Array.from({ length: 2 }, async () => {
      while (queue.length > 0) {
        const orderId = queue.shift();
        if (!orderId) continue;
        try {
          const candidates = await fetchOrderFacilityCandidates(orderId);
          const best = pickBestFacilityCandidate(candidates);
          if (best) results[orderId] = { ...best, order_id: orderId };
        } catch {
          // ignore
        }
      }
    });

    Promise.all(workers).then(() => {
      if (cancelled) return;
      if (Object.keys(results).length === 0) return;
      setFacilityHints((prev) => ({ ...prev, ...results }));
    });

    return () => {
      cancelled = true;
    };
  }, [orders, facilityHints]);

  const facilityLabel = (order: OrderSummary) => {
    const facilityId = order.facility || "";
    if (facilityId) {
      const name = facilityNameMap[facilityId];
      return name ? `${name} (${facilityId})` : facilityId;
    }
    const orderId = order.id || "";
    const hint = orderId ? facilityHints[orderId] : null;
    if (hint?.facility_name) {
      const score = hint.score != null ? ` / score=${hint.score}` : "";
      return `推定: ${hint.facility_name} (${hint.facility_id}${score})`;
    }
    return "未確定";
  };

  const loadOrders = async () => {
    if (!date) return;
    setLoading(true);
    setMessage("");
    try {
      const params: Record<string, string> = { date };
      if (status) params.status = status;
      const res = await apiClient.get("/orders/by-line-date", { params });
      const items = res.data?.orders || [];
      setOrders(items);
      if (!items.length) {
        setMessage("該当する注文がありません。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setOrders([]);
    } finally {
      setLoading(false);
    }
  };

  const openOutput = async (path: string, label: string) => {
    const timestamp = new Date().toLocaleString("ja-JP");
    setMessage(`${label}のダウンロードを開始します。 (${timestamp})`);
    try {
      const res = await apiClient.get(path, { responseType: "blob" });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "output";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const downloadDailyBundle = async (bundleType: "labels" | "delivery" | "both") => {
    if (!date) {
      setMessage("日付を指定してください。");
      return;
    }
    const label =
      bundleType === "labels"
        ? "当日ラベル一括"
        : bundleType === "delivery"
          ? "当日納品書一括"
          : "当日一括（ラベル+納品書）";
    setMessage(`${label}を作成中です...`);
    try {
      const res = await apiClient.get("/outputs/daily-bundle", {
        params: { date, bundle_type: bundleType, status: status || undefined },
        responseType: "blob",
      });
      const contentDisposition = headerValueToString(
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"],
      );
      const filename = extractFilename(contentDisposition) || `daily_outputs_${date}_${bundleType}.zip`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      const successOrders = Number(res.headers?.["x-daily-bundle-success-orders"] || 0);
      const errorOrders = Number(res.headers?.["x-daily-bundle-error-orders"] || 0);
      setMessage(`${label}をダウンロードしました。成功 ${successOrders}件 / 失敗 ${errorOrders}件`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `一括ダウンロードに失敗しました: ${detail}` : "一括ダウンロードに失敗しました。");
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Delivery Notes</p>
          <h1>日別納品書</h1>
          <p className="subtle">対象日の納品書を注文単位で一覧化します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">合計 {orders.length} 件</span>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">日付</span>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">ステータス</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <button className="btn primary" onClick={loadOrders} disabled={loading}>
            {loading ? "取得中..." : "取得"}
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("labels")} disabled={loading}>
            当日ラベル一括
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("delivery")} disabled={loading}>
            当日納品書一括
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDailyBundle("both")} disabled={loading}>
            当日一括ZIP
          </button>
        </div>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <section className="panel">
        <header className="panel-header">
          <h2>納品書一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>施設</th>
                <th>週</th>
                <th>ステータス</th>
                <th>受信日時</th>
                <th>行数</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={6}>該当データなし</td>
                </tr>
              ) : (
                orders.map((order) => (
                  <tr key={order.id}>
                    <td>{facilityLabel(order)}</td>
                    <td>{order.week || "未確定"}</td>
                    <td>{order.status || "-"}</td>
                    <td>{formatTimestamp(order.received_at)}</td>
                    <td>{order.line_count ?? "-"}</td>
                    <td className="actions">
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() =>
                          openOutput(`/outputs/labels?order_id=${order.id}`, "ラベルCSV")
                        }
                      >
                        ラベル
                      </button>
                      <button
                        className="btn ghost"
                        type="button"
                        onClick={() =>
                          openOutput(`/outputs/delivery-notes?order_id=${order.id}`, "納品書Excel")
                        }
                      >
                        納品書
                      </button>
                      <Link href={`/orders/${order.id}`} className="link">
                        詳細
                      </Link>
                    </td>
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

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: center;
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
          background: #eef2f0;
          color: #1f2a2a;
          border: 1px solid rgba(25, 32, 30, 0.12);
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

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }

        th,
        td {
          padding: 10px;
          text-align: left;
        }

        thead {
          background: #f4f1ea;
        }

        tbody tr:nth-child(even) {
          background: #faf9f5;
        }

        .actions {
          display: flex;
          gap: 8px;
          align-items: center;
        }

        .link {
          color: #1f2a2a;
          text-decoration: underline;
          font-weight: 600;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
