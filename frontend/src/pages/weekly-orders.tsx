import { useEffect, useMemo, useState } from "react";
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
  ocr_status?: string | null;
  ocr_error?: string | null;
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

export default function WeeklyOrdersPage() {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [includeUnassigned, setIncludeUnassigned] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");

  const loadOrders = async () => {
    setLoading(true);
    setMessage("");
    try {
      const params = statusFilter ? { status: statusFilter, include_ocr: true } : { include_ocr: true };
      const res = await apiClient.get("/orders", { params });
      setOrders(res.data?.orders || []);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setOrders([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadOrders();
  }, [statusFilter]);

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
      .slice(0, 60)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !facilityHints[orderId]);

    if (unresolved.length === 0) return;

    const queue = [...unresolved];
    const results: Record<string, FacilityHint> = {};
    const workers = Array.from({ length: 3 }, async () => {
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

  const grouped = useMemo(() => {
    const map: Record<string, OrderSummary[]> = {};
    orders.forEach((order) => {
      if (!includeUnassigned && !order.week) return;
      const week = order.week || "未確定";
      if (!map[week]) map[week] = [];
      map[week].push(order);
    });
    Object.values(map).forEach((list) => {
      list.sort((a, b) => {
        const at = a.received_at ? new Date(a.received_at).getTime() : 0;
        const bt = b.received_at ? new Date(b.received_at).getTime() : 0;
        return bt - at;
      });
    });
    return map;
  }, [orders, includeUnassigned]);

  const weekKeys = useMemo(() => Object.keys(grouped).sort(), [grouped]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Weekly Orders</p>
          <h1>週次注文</h1>
          <p className="subtle">OCRで週が判定された注文を週ごとに整理して表示します。</p>
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
            <span className="field-label">ステータス</span>
            <select
              className="input"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <label className="field checkbox">
            <span className="field-label">未確定</span>
            <input
              type="checkbox"
              checked={includeUnassigned}
              onChange={(e) => setIncludeUnassigned(e.target.checked)}
            />
            未確定も表示
          </label>
          <button className="btn primary" onClick={loadOrders} disabled={loading}>
            {loading ? "更新中..." : "更新"}
          </button>
        </div>
      </section>

      {message ? <p className="message">{message}</p> : null}

      {weekKeys.length === 0 ? (
        <p className="message">対象データがありません。</p>
      ) : (
        weekKeys.map((week) => (
          <section key={week} className="panel">
            <header className="panel-header">
              <h2>{week}</h2>
              <span className="badge">{grouped[week].length} 件</span>
            </header>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>施設</th>
                    <th>ステータス</th>
                    <th>OCR</th>
                    <th>受信日時</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {grouped[week].map((order) => (
                    <tr key={order.id}>
                      <td>{facilityLabel(order)}</td>
                      <td>{order.status || "-"}</td>
                      <td>{order.ocr_status || "-"}</td>
                      <td>{formatTimestamp(order.received_at)}</td>
                      <td>
                        <Link href={`/orders/${order.id}`} className="link">
                          詳細
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}

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

        .field.checkbox {
          flex-direction: row;
          align-items: center;
          gap: 10px;
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
