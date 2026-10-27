import { Button, Checkbox } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { sawaTheme } from "@sawa/ui";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidateBatch,
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

type OrderCursor = { received_at: string; id: string };

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

export default function OcrResultsPage() {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint | null>>({});
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [showOnlyUnassigned, setShowOnlyUnassigned] = useState(false);
  const [cursors, setCursors] = useState<Array<OrderCursor | null>>([null]);
  const [nextCursor, setNextCursor] = useState<OrderCursor | null>(null);
  const [refresh, setRefresh] = useState(0);
  const cursor = cursors[cursors.length - 1];

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setMessage("");
    setOrders([]);
    setFacilityHints({});
    setNextCursor(null);
    apiClient.get("/orders", { params: {
      include_ocr: true, response_view: "ocr_monitor", limit: 25, only_unassigned: showOnlyUnassigned,
      before_received_at: cursor?.received_at, before_id: cursor?.id,
    }, signal: controller.signal }).then(res => {
      if (controller.signal.aborted) return;
      setOrders(res.data?.orders || []);
      setNextCursor(res.data?.has_more ? res.data.next_cursor : null);
    }).catch((err: any) => {
      if (controller.signal.aborted) return;
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [cursor?.received_at, cursor?.id, showOnlyUnassigned, refresh]);

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
    const controller = new AbortController();
    const unresolved = orders
      .filter((order) => !order.facility && order.id)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !Object.prototype.hasOwnProperty.call(facilityHints, orderId));

    if (unresolved.length === 0) return;

    fetchOrderFacilityCandidateBatch(unresolved, controller.signal).then(batch => {
      if (cancelled) return;
      const results: Record<string, FacilityHint | null> = {};
      for (const orderId of unresolved) {
        const best = batch[orderId].error ? null : pickBestFacilityCandidate(batch[orderId].candidates);
        results[orderId] = best ? { ...best, order_id: orderId } : null;
      }
      setFacilityHints((prev) => ({ ...prev, ...results }));
    }).catch(() => {
      if (!cancelled) setFacilityHints(prev => ({ ...prev, ...Object.fromEntries(unresolved.map(id => [id, null])) }));
    });

    return () => {
      cancelled = true;
      controller.abort();
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

  const sorted = useMemo(() => {
    return [...orders].sort((a, b) => {
      const at = a.received_at ? new Date(a.received_at).getTime() : 0;
      const bt = b.received_at ? new Date(b.received_at).getTime() : 0;
      return bt - at;
    });
  }, [orders]);

  return (
    <ThemeProvider theme={sawaTheme}><main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">OCR Monitor</p>
          <h1>OCR結果監視</h1>
          <p className="subtle">OCRの例外や未確定案件を管理者向けに確認します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">{cursors.length}ページ / {sorted.length} 件</span>
        </header>
        <div className="filters">
          <label className="field checkbox">
            <span className="field-label">未確定</span>
            <Checkbox
              checked={showOnlyUnassigned}
              onChange={(e) => { setCursors([null]); setShowOnlyUnassigned(e.target.checked); }}
            />
            施設/週が未確定のみ
          </label>
          <Button type="button" className="btn primary" onClick={() => { setCursors([null]); setRefresh(value => value + 1); }} disabled={loading}>
            {loading ? "更新中..." : "更新"}
          </Button>
        </div>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <section className="panel">
        <header className="panel-header">
          <h2>結果一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>施設</th>
                <th>週</th>
                <th>OCR</th>
                <th>ステータス</th>
                <th>受信日時</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sorted.length === 0 ? (
                <tr>
                  <td colSpan={6}>{loading ? "読み込み中..." : message ? "未取得" : "該当データなし"}</td>
                </tr>
              ) : (
                sorted.map((order) => (
                  <tr key={order.id}>
                    <td>{facilityLabel(order)}</td>
                    <td>{order.week || "未確定"}</td>
                    <td>{order.ocr_status || "-"}</td>
                    <td>{order.status || "-"}</td>
                    <td>{formatTimestamp(order.received_at)}</td>
                    <td>
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
        <nav aria-label="OCR結果のページ">
          <Button type="button" disabled={loading || cursors.length <= 1} onClick={() => setCursors(values => values.slice(0, -1))}>前へ</Button>
          <Button type="button" disabled={loading || !nextCursor} onClick={() => { if (nextCursor) setCursors(values => [...values, nextCursor]); }}>次へ</Button>
        </nav>
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

        .btn,
        .page :global(.MuiButton-root.btn) {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
          justify-self: start;
        }

        .btn.primary,
        .page :global(.MuiButton-root.btn.primary) {
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
    </main></ThemeProvider>
  );
}
