import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/router";

import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";
import {
  fetchFacilityNameMap,
  fetchOrderFacilityCandidates,
  pickBestFacilityCandidate,
  type FacilityHint,
  type FacilityNameMap,
} from "../../services/facilityData";

type Order = {
  status: string;
  document: string;
  facility?: string | null;
  week?: string | null;
  id?: string;
  received_at?: string | null;
  message_id?: string | null;
  ocr_review_state?: string | null;
  ocr_review_badges?: string[] | null;
  ocr_has_saved_draft?: boolean | null;
  ocr_draft_newer_than_lines?: boolean | null;
  ocr_auto_apply_blocked?: boolean | null;
  ocr_reject_reasons?: string[] | null;
  ocr_processing_stage?: string | null;
  ocr_result_state?: string | null;
  ocr_confirmed_lines_retained?: boolean | null;
};

const compareOrdersByReceivedAt = (left: Order, right: Order) => {
  const leftTime = left.received_at ? new Date(left.received_at).getTime() : 0;
  const rightTime = right.received_at ? new Date(right.received_at).getTime() : 0;
  if (rightTime !== leftTime) return rightTime - leftTime;
  return String(right.id || "").localeCompare(String(left.id || ""), "ja");
};

export default function OrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<Order[]>([]);
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});
  const [facilityHints, setFacilityHints] = useState<Record<string, FacilityHint>>({});
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [unresolvedOnly, setUnresolvedOnly] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string>("");
  const [reloadToken, setReloadToken] = useState<number>(0);

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
    if (!router.isReady) return;
    const statusParam = router.query.status;
    const searchParam = router.query.search;
    const unresolvedParam = router.query.unresolved;
    setStatusFilter(typeof statusParam === "string" ? statusParam : "");
    setSearch(typeof searchParam === "string" ? searchParam : "");
    setUnresolvedOnly(
      typeof unresolvedParam === "string" ? unresolvedParam === "1" || unresolvedParam === "true" : false,
    );
  }, [router.isReady, router.query.status, router.query.search, router.query.unresolved]);

  useEffect(() => {
    let cancelled = false;
    const params = statusFilter ? { status: statusFilter, include_ocr: false } : { include_ocr: false };
    setIsLoading(true);
    setLoadError("");
    apiClient
      .get("/orders", { params })
      .then((res) => {
        if (cancelled) return;
        setOrders(res.data.orders || []);
      })
      .catch((err) => {
        if (cancelled) return;
        const detail =
          err?.response?.data?.detail ||
          err?.response?.data?.message ||
          err?.message ||
          "注文データの取得に失敗しました。";
        setLoadError(String(detail));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [statusFilter, reloadToken]);

  useEffect(() => {
    let cancelled = false;
    const unresolved = orders
      .filter((order) => !order.facility && order.id)
      .sort(compareOrdersByReceivedAt)
      .slice(0, 60)
      .map((order) => String(order.id || ""))
      .filter((orderId) => orderId && !facilityHints[orderId]);

    if (unresolved.length === 0) return;

    const queue = [...unresolved];
    const concurrency = 4;
    const results: Record<string, FacilityHint> = {};

    const workers = Array.from({ length: concurrency }, async () => {
      while (queue.length > 0) {
        const orderId = queue.shift();
        if (!orderId) continue;
        try {
          const candidates = await fetchOrderFacilityCandidates(orderId);
          const best = pickBestFacilityCandidate(candidates);
          if (best) results[orderId] = { ...best, order_id: orderId };
        } catch {
          // ignore per-order failures
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

  const facilityLabel = (order: Order) => {
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

  const filteredOrders = useMemo(() => {
    return orders.filter((order) => {
      if (unresolvedOnly && order.facility) return false;
      if (!search) return true;
      const token = search.toLowerCase();
      const facilityId = order.facility || "";
      const facilityName = facilityId ? facilityNameMap[facilityId] || "" : "";
      const hint = order.id ? facilityHints[order.id] : null;
      const hintName = hint?.facility_name ? String(hint.facility_name) : "";
      return (
        (order.id || "").toLowerCase().includes(token) ||
        (order.message_id || "").toLowerCase().includes(token) ||
        facilityId.toLowerCase().includes(token) ||
        facilityName.toLowerCase().includes(token) ||
        hintName.toLowerCase().includes(token) ||
        (order.week || "").toLowerCase().includes(token) ||
        (order.document || "").toLowerCase().includes(token)
      );
    });
  }, [facilityHints, facilityNameMap, orders, search, unresolvedOnly]);

  const sortedOrders = useMemo(() => [...filteredOrders].sort(compareOrdersByReceivedAt), [filteredOrders]);

  const groupedRows = useMemo(() => {
    const groups = new Map<string, { facility: string; week: string; counts: Record<string, number> }>();
    filteredOrders.forEach((order) => {
      const facility = order.facility || "未確定";
      const week = order.week || "未確定";
      const key = `${facility}__${week}`;
      const current =
        groups.get(key) || {
          facility,
          week,
          counts: { 未着: 0, 要確認: 0, 確定: 0, エラー: 0 },
        };
      if (current.counts[order.status] !== undefined) {
        current.counts[order.status] += 1;
      }
      groups.set(key, current);
    });
    return Array.from(groups.values()).sort((left, right) => {
      if (left.week !== right.week) return right.week.localeCompare(left.week, "ja");
      return left.facility.localeCompare(right.facility, "ja");
    });
  }, [filteredOrders]);

  const formatReceivedAt = (value?: string | null) => {
    if (!value) return "不明";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString("ja-JP");
  };

  const statusClass = (status?: string | null) => {
    switch (status) {
      case "未着":
        return "status-pending";
      case "要確認":
        return "status-review";
      case "確定":
        return "status-confirmed";
      case "エラー":
        return "status-error";
      default:
        return "";
    }
  };

  const reviewToneClass = (order: Order) => {
    const reviewState = String(order.ocr_review_state || "").trim().toLowerCase();
    if (reviewState === "processing_failed") return "list-item-error";
    if (reviewState === "auto_apply_blocked" || reviewState === "draft_ready") return "list-item-review";
    return "";
  };

  const processingStageLabel = (value?: string | null) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "ocr_pipeline") return "OCR準備";
    if (normalized === "inference") return "推論";
    if (normalized === "validation") return "検証";
    if (normalized === "draft_saved") return "下書き保存";
    if (normalized === "apply" || normalized === "applied") return "明細更新";
    return "";
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Orders</p>
          <h1>注文一覧</h1>
          <p className="subtle">施設ごとの進捗と注文の状態を一覧で確認できます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
          <span className="badge">合計 {filteredOrders.length} 件</span>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">ステータス</span>
            <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">全て</option>
              <option value="未着">未着</option>
              <option value="要確認">要確認</option>
              <option value="確定">確定</option>
              <option value="エラー">エラー</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">検索</span>
            <input
              className="input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="施設名 / 週 / 注文ID / 受付ID"
            />
          </label>
          <label className="field checkbox">
            <span className="field-label">施設未確定のみ</span>
            <input
              type="checkbox"
              checked={unresolvedOnly}
              onChange={(e) => setUnresolvedOnly(e.target.checked)}
            />
          </label>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>施設×週 進捗</h2>
          <button className="ghost-link" type="button" onClick={() => setReloadToken((value) => value + 1)}>
            最新に更新
          </button>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>施設</th>
                <th>週</th>
                <th>未着</th>
                <th>要確認</th>
                <th>確定</th>
                <th>エラー</th>
              </tr>
            </thead>
            <tbody>
              {groupedRows.length === 0 ? (
                <tr>
                  <td colSpan={6}>該当データなし</td>
                </tr>
              ) : (
                groupedRows.map((row) => (
                  <tr key={`${row.facility}-${row.week}`}>
                    <td>
                      {row.facility === "未確定"
                        ? "未確定"
                        : facilityNameMap[row.facility]
                          ? `${facilityNameMap[row.facility]} (${row.facility})`
                          : row.facility}
                    </td>
                    <td>{row.week}</td>
                    <td>{row.counts["未着"]}</td>
                    <td>{row.counts["要確認"]}</td>
                    <td>{row.counts["確定"]}</td>
                    <td>{row.counts["エラー"]}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>注文リスト</h2>
          <span className="subtle">詳細を開いて処理します。</span>
        </header>
        <div className="list">
          {isLoading ? (
            <p className="subtle">読み込み中...</p>
          ) : loadError ? (
            <div className="error-box">
              <p>{loadError}</p>
              <button className="retry-button" type="button" onClick={() => setReloadToken((value) => value + 1)}>
                再読み込み
              </button>
            </div>
          ) : sortedOrders.length === 0 ? (
            <p className="subtle">
              {orders.length === 0 ? "注文データがありません。" : "フィルタ条件に一致する注文がありません。"}
            </p>
          ) : (
            sortedOrders.map((order) => (
              <div key={order.id || order.document} className={`list-item ${reviewToneClass(order)}`.trim()}>
                <div className="list-main">
                  <p className="list-title">{order.id || "注文ID未発行"}</p>
                  <p className="list-meta">
                    施設: {facilityLabel(order)} / 週: {order.week || "未確定"} / 受信:{" "}
                    {formatReceivedAt(order.received_at)} / 受付ID: {order.message_id || "不明"}
                  </p>
                  {(Array.isArray(order.ocr_review_badges) && order.ocr_review_badges.length) ||
                  processingStageLabel(order.ocr_processing_stage) ||
                  order.ocr_confirmed_lines_retained ? (
                    <div className="review-badges">
                      {(order.ocr_review_badges || []).map((badge) => (
                        <span className="review-badge" key={`${order.id || order.document}-${badge}`}>
                          {badge}
                        </span>
                      ))}
                      {order.ocr_review_state === "processing" && processingStageLabel(order.ocr_processing_stage) ? (
                        <span className="review-badge">
                          {processingStageLabel(order.ocr_processing_stage)}
                        </span>
                      ) : null}
                      {order.ocr_confirmed_lines_retained ? (
                        <span className="review-badge">確定明細保持</span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                <div className="list-actions">
                  <span className={`status-pill ${statusClass(order.status)}`}>{order.status}</span>
                  <Link href={`/orders/${order.id}`} className="list-link">
                    詳細
                  </Link>
                  {order.week ? (
                    <Link href={`/menus/${order.week}`} className="list-link">
                      メニュー
                    </Link>
                  ) : null}
                </div>
              </div>
            ))
          )}
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
          gap: 12px;
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
          white-space: nowrap;
        }

        .filters {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
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


        .error-box {
          border: 1px solid rgba(122, 47, 42, 0.25);
          background: #fceceb;
          color: #7a2f2a;
          padding: 12px 14px;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }

        .ghost-link {
          border: none;
          background: transparent;
          padding: 0;
          font-size: 13px;
          color: #5f7b74;
          cursor: pointer;
        }

        .ghost-link:hover {
          text-decoration: underline;
          text-underline-offset: 2px;
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

        .list {
          display: grid;
          gap: 12px;
        }

        .list-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 14px;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
        }

        .list-item-review {
          border-color: rgba(171, 125, 35, 0.22);
          background: #fffaf0;
        }

        .list-item-error {
          border-color: rgba(148, 47, 44, 0.18);
          background: #fff4f3;
        }

        .list-main {
          min-width: 0;
        }

        .list-title {
          margin: 0 0 4px;
          font-weight: 700;
        }

        .list-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
          line-height: 1.5;
        }

        .review-badges {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-top: 8px;
        }

        .review-badge {
          display: inline-flex;
          align-items: center;
          padding: 3px 9px;
          border-radius: 999px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #f4f1ea;
          color: #31423f;
          font-size: 11px;
          font-weight: 600;
          white-space: nowrap;
        }

        .list-actions {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
          justify-content: flex-end;
        }

        .retry-button {
          border: none;
          background: #7a2f2a;
          color: #fff;
          padding: 6px 12px;
          border-radius: 999px;
          cursor: pointer;
          font-size: 12px;
          font-weight: 600;
        }

        .status-pill {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
          white-space: nowrap;
        }

        .status-pill.status-pending {
          background: #f6dfe6;
          color: #7a2f4b;
        }

        .status-pill.status-review {
          background: #f5e2c9;
          color: #7a4a1f;
        }

        .status-pill.status-confirmed {
          background: #dce8f5;
          color: #2f4f7a;
        }

        .status-pill.status-error {
          background: #f4dedb;
          color: #7a2f2a;
        }

        :global(.list-link) {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
          color: #1f2a2a;
          text-decoration: none;
          cursor: pointer;
        }

        :global(.list-link:hover) {
          background: #d8e0dd;
          text-decoration: underline;
          text-underline-offset: 2px;
        }

        @media (max-width: 720px) {
          .list-item {
            align-items: flex-start;
            flex-direction: column;
          }

          .list-actions {
            width: 100%;
            justify-content: flex-start;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
