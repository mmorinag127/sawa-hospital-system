import { useEffect, useState } from "react";
import { apiClient } from "../../services/apiClient";
import Link from "next/link";
import { useRouter } from "next/router";
import TopNav from "../../components/TopNav";

type Order = {
  status: string;
  document: string;
  facility?: string | null;
  week?: string | null;
  id?: string;
  received_at?: string | null;
  message_id?: string | null;
};

export default function OrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<Order[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [unresolvedOnly, setUnresolvedOnly] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string>("");
  const [reloadToken, setReloadToken] = useState<number>(0);

  useEffect(() => {
    if (!router.isReady) return;
    const statusParam = router.query.status;
    if (typeof statusParam === "string") {
      setStatusFilter(statusParam);
    } else if (statusParam === undefined) {
      setStatusFilter("");
    }
    const unresolvedParam = router.query.unresolved;
    if (typeof unresolvedParam === "string") {
      setUnresolvedOnly(unresolvedParam === "1" || unresolvedParam === "true");
    } else if (unresolvedParam === undefined) {
      setUnresolvedOnly(false);
    }
  }, [router.isReady, router.query.status, router.query.unresolved]);

  useEffect(() => {
    let cancelled = false;
    const params = statusFilter
      ? { status: statusFilter, include_ocr: false }
      : { include_ocr: false };
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

  const filteredOrders = orders.filter((order) => {
    if (unresolvedOnly && order.facility) return false;
    if (!search) return true;
    const token = search.toLowerCase();
    return (
      (order.id || "").toLowerCase().includes(token) ||
      (order.facility || "").toLowerCase().includes(token) ||
      (order.week || "").toLowerCase().includes(token) ||
      (order.document || "").toLowerCase().includes(token)
    );
  });

  const sortedOrders = [...filteredOrders].sort((a, b) => {
    const aTime = a.received_at ? new Date(a.received_at).getTime() : 0;
    const bTime = b.received_at ? new Date(b.received_at).getTime() : 0;
    return bTime - aTime;
  });

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

  const groups = new Map<
    string,
    { facility: string; week: string; counts: Record<string, number> }
  >();
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
  const groupedRows = Array.from(groups.values());

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Orders</p>
          <h1>注文一覧</h1>
          <p className="subtle">施設×週の進捗と注文明細をまとめて確認できます。</p>
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
          <label className="field">
            <span className="field-label">検索</span>
            <input
              className="input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="施設/週/IDなど"
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
          <Link href="/orders" className="ghost-link">
            最新に更新
          </Link>
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
                    <td>{row.facility}</td>
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
          <span className="subtle">クリックで詳細へ</span>
        </header>
        <div className="list">
          {isLoading ? (
            <p className="subtle">読み込み中...</p>
          ) : loadError ? (
            <div className="error-box">
              <p>{loadError}</p>
              <button
                className="retry-button"
                type="button"
                onClick={() => setReloadToken((value) => value + 1)}
              >
                再読み込み
              </button>
            </div>
          ) : sortedOrders.length === 0 ? (
            <p className="subtle">
              {orders.length === 0
                ? "注文データがありません。"
                : "フィルタ条件に一致する注文がありません。"}
            </p>
          ) : (
            sortedOrders.map((o) => (
              <div key={o.id || o.document} className="list-item">
                <div>
                  <p className="list-title">{o.id}</p>
                  <p className="list-meta">
                    施設: {o.facility || "未確定"} / 週: {o.week || "未確定"} / 受信:{" "}
                    {formatReceivedAt(o.received_at)} / Message: {o.message_id || "不明"}
                  </p>
                </div>
                <div className="list-actions">
                  <span className={`status-pill ${statusClass(o.status)}`}>{o.status}</span>
                  <Link href={`/orders/${o.id}`} className="list-link">
                    詳細
                  </Link>
                  {o.week ? (
                    <Link href={`/menus/${o.week}`} className="list-link">
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
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
        }

        .list-title {
          margin: 0 0 4px;
          font-weight: 600;
        }

        .list-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .list-actions {
          display: flex;
          align-items: center;
          gap: 10px;
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

        .retry-button:hover {
          background: #62221e;
        }

        .status-pill {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 600;
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

        :global(.list-link:focus-visible) {
          outline: 2px solid #5f7b74;
          outline-offset: 2px;
        }

        @media (max-width: 720px) {
          .list-item {
            flex-direction: column;
            align-items: flex-start;
            gap: 10px;
          }
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
