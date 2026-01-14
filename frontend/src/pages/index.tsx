import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiClient } from "../services/apiClient";
import TopNav from "../components/TopNav";

type Order = {
  id?: string;
  status: string;
  facility?: string | null;
  week?: string | null;
  received_at?: string | null;
};

type MenuInfo = {
  status: "登録済み" | "未登録" | "確認中";
  itemCount?: number;
  filename?: string | null;
};

const STATUS_KEYS = ["未着", "要確認", "確定", "エラー"] as const;

const formatDate = (value?: string | null) => {
  if (!value) return "未取得";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未取得";
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

export default function HomePage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [menuInfo, setMenuInfo] = useState<Record<string, MenuInfo>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient
      .get("/orders", { params: { include_ocr: false } })
      .then((res) => {
        setOrders(res.data.orders || []);
        setError("");
      })
      .catch(() => {
        setError("データ取得に失敗しました。");
      });
  }, []);

  const stats = useMemo(() => {
    const counts: Record<string, number> = {};
    STATUS_KEYS.forEach((key) => {
      counts[key] = 0;
    });
    let unresolved = 0;
    let latestReceived = "";
    orders.forEach((order) => {
      if (counts[order.status] != null) {
        counts[order.status] += 1;
      }
      if (!order.facility) {
        unresolved += 1;
      }
      if (order.received_at && (!latestReceived || order.received_at > latestReceived)) {
        latestReceived = order.received_at;
      }
    });
    return {
      counts,
      unresolved,
      latestReceived,
      total: orders.length,
    };
  }, [orders]);

  const weekRows = useMemo(() => {
    const map = new Map<
      string,
      { week: string; counts: Record<string, number>; unresolved: number }
    >();
    orders.forEach((order) => {
      const week = order.week || "未確定";
      const current =
        map.get(week) || {
          week,
          counts: { 未着: 0, 要確認: 0, 確定: 0, エラー: 0 },
          unresolved: 0,
        };
      if (current.counts[order.status] != null) {
        current.counts[order.status] += 1;
      }
      if (!order.facility) {
        current.unresolved += 1;
      }
      map.set(week, current);
    });
    return Array.from(map.values()).sort((a, b) => a.week.localeCompare(b.week));
  }, [orders]);

  const weekList = useMemo(() => {
    const weeks = new Set<string>();
    orders.forEach((order) => {
      if (order.week) {
        weeks.add(order.week);
      }
    });
    return Array.from(weeks).sort();
  }, [orders]);

  useEffect(() => {
    if (weekList.length === 0) {
      setMenuInfo({});
      return;
    }
    let cancelled = false;
    const baseInfo: Record<string, MenuInfo> = {};
    weekList.forEach((week) => {
      baseInfo[week] = { status: "確認中" };
    });
    setMenuInfo(baseInfo);
    const loadMenus = async () => {
      const entries = await Promise.all(
        weekList.map(async (week) => {
          try {
            const res = await apiClient.get(`/monthly-menus/${week}`);
            const items = res.data?.items || [];
            return [
              week,
              {
                status: "登録済み",
                itemCount: items.length,
                filename: res.data?.menu?.filename || null,
              },
            ] as const;
          } catch {
            return [week, { status: "未登録" }] as const;
          }
        })
      );
      if (cancelled) return;
      const next: Record<string, MenuInfo> = {};
      entries.forEach(([week, info]) => {
        next[week] = info;
      });
      setMenuInfo(next);
    };
    loadMenus();
    return () => {
      cancelled = true;
    };
  }, [weekList.join("|")]);

  const pendingOrders = useMemo(() => {
    return orders.filter((order) => order.status === "要確認" || !order.facility).slice(0, 8);
  }, [orders]);

  return (
    <main className="dashboard">
      <header className="hero">
        <div>
          <p className="eyebrow">Hospital Order Dashboard</p>
          <h1>月次の進捗と未確定を一画面で確認</h1>
          <p className="subtle">
            施設×月の状態、未確定申請、OCR/取込状況をまとめて把握できます。
          </p>
        </div>
        <TopNav />
      </header>

      {error && <p className="error">{error}</p>}

      <section className="grid">
        <Link href="/orders?status=未着" className="card-link">
          <article className="card kpi tone-new" style={{ animationDelay: "40ms" }}>
            <p className="card-label">未着</p>
            <p className="card-value">{stats.counts["未着"]}</p>
          </article>
        </Link>
        <Link href="/orders?status=要確認" className="card-link">
          <article className="card kpi tone-warn" style={{ animationDelay: "80ms" }}>
            <p className="card-label">未確定申請</p>
            <p className="card-value">{stats.counts["要確認"]}</p>
          </article>
        </Link>
        <Link href="/orders?status=確定" className="card-link">
          <article className="card kpi tone-ok" style={{ animationDelay: "120ms" }}>
            <p className="card-label">確定</p>
            <p className="card-value">{stats.counts["確定"]}</p>
          </article>
        </Link>
        <Link href="/orders?status=エラー" className="card-link">
          <article className="card kpi tone-error" style={{ animationDelay: "160ms" }}>
            <p className="card-label">エラー</p>
            <p className="card-value">{stats.counts["エラー"]}</p>
          </article>
        </Link>
        <Link href="/orders?unresolved=1" className="card-link">
          <article className="card kpi tone-muted" style={{ animationDelay: "200ms" }}>
            <p className="card-label">施設未確定</p>
            <p className="card-value">{stats.unresolved}</p>
          </article>
        </Link>
        <Link href="/orders" className="card-link wide">
          <article className="card wide" style={{ animationDelay: "240ms" }}>
            <p className="card-label">OCR / 取込状況</p>
            <div className="processing">
              <div>
                <p className="mini-label">最新取込</p>
                <p className="mini-value">{formatDate(stats.latestReceived)}</p>
              </div>
              <div>
                <p className="mini-label">総注文数</p>
                <p className="mini-value">{stats.total}</p>
              </div>
              <div>
                <p className="mini-label">エラー件数</p>
                <p className="mini-value">{stats.counts["エラー"]}</p>
              </div>
            </div>
          </article>
        </Link>
      </section>

      <section className="columns">
        <article className="panel">
          <header className="panel-header">
            <h2>月ごとの進捗表</h2>
            <Link href="/orders" className="ghost-link">
              注文一覧へ
            </Link>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>月ID</th>
                  <th>未着</th>
                  <th>未確定</th>
                  <th>確定</th>
                  <th>エラー</th>
                  <th>施設未確定</th>
                </tr>
              </thead>
              <tbody>
                {weekRows.length === 0 ? (
                  <tr>
                    <td colSpan={6}>データがありません。</td>
                  </tr>
                ) : (
                  weekRows.map((row) => (
                    <tr key={row.week}>
                      <td>{row.week}</td>
                      <td>{row.counts["未着"]}</td>
                      <td>{row.counts["要確認"]}</td>
                      <td>{row.counts["確定"]}</td>
                      <td>{row.counts["エラー"]}</td>
                      <td>{row.unresolved}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel">
          <header className="panel-header">
            <h2>未確定申請</h2>
            <span className="badge">{stats.counts["要確認"]}</span>
          </header>
          <div className="pending-list">
            {pendingOrders.length === 0 ? (
              <p className="subtle">未確定申請はありません。</p>
            ) : (
              pendingOrders.map((order) => (
                <Link key={order.id} href={`/orders/${order.id}`} className="pending-item">
                  <div>
                    <p className="pending-title">{order.id}</p>
                    <p className="pending-meta">
                      施設: {order.facility || "未確定"} / 月: {order.week || "未確定"}
                    </p>
                  </div>
                  <span className="status-tag">{order.status}</span>
                </Link>
              ))
            )}
          </div>
        </article>
      </section>

      <section className="menu-section">
        <header className="panel-header">
          <h2>月ごとのメニュー</h2>
          <Link href="/orders" className="ghost-link">
            月を確認する
          </Link>
        </header>
        <div className="menu-grid">
          {weekList.length === 0 ? (
            <p className="subtle">月次メニューの対象月がありません。</p>
          ) : (
            weekList.map((week) => {
              const info = menuInfo[week];
              const status = info?.status || "未登録";
              const statusClass =
                status === "登録済み" ? "status-ok" : status === "未登録" ? "status-missing" : "status-pending";
              return (
                <Link key={week} href={`/menus/${week}`} className="menu-card">
                  <div>
                    <p className="menu-week">{week}</p>
                    <p className="menu-meta">
                      {status === "登録済み" ? `登録済み (${info?.itemCount ?? 0}件)` : status}
                    </p>
                    {info?.filename && <p className="menu-file">{info.filename}</p>}
                  </div>
                  <span className={`menu-status ${statusClass}`}>{status}</span>
                </Link>
              );
            })
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

        .dashboard {
          min-height: 100vh;
          padding: 48px 6vw 80px;
        }

        :global(a) {
          color: inherit;
          text-decoration: none;
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

        .grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          margin-bottom: 28px;
        }

        .card {
          background: #ffffff;
          border-radius: 16px;
          padding: 18px;
          box-shadow: 0 10px 30px rgba(27, 35, 33, 0.08);
          border: 1px solid rgba(25, 32, 30, 0.1);
          animation: rise 0.6s ease both;
        }

        .card-link {
          display: block;
        }

        .card-link .card {
          height: 100%;
        }

        .card-link:hover .card {
          transform: translateY(-3px);
          transition: transform 0.2s ease;
        }

        .card.kpi {
          position: relative;
          border-top: 3px solid var(--tone, #c7d2ce);
        }

        .card.kpi::after {
          content: "";
          position: absolute;
          top: 14px;
          right: 14px;
          width: 8px;
          height: 8px;
          border-radius: 999px;
          background: var(--tone, #c7d2ce);
          box-shadow: 0 0 0 4px var(--tone-bg, #f2f4f3);
        }

        .tone-new {
          --tone: #c7a86b;
          --tone-bg: #f3ede1;
        }

        .tone-warn {
          --tone: #d69e2e;
          --tone-bg: #f8edd6;
        }

        .tone-ok {
          --tone: #3aa66b;
          --tone-bg: #e7f4ec;
        }

        .tone-error {
          --tone: #c04343;
          --tone-bg: #f5e3e3;
        }

        .tone-muted {
          --tone: #667a74;
          --tone-bg: #e8eceb;
        }

        .card.muted {
          background: #1f2a2a;
          color: #f7f2e7;
        }

        .card.wide {
          grid-column: span 2;
        }

        .card-label {
          margin: 0 0 8px;
          font-size: 12px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: inherit;
          opacity: 0.7;
        }

        .card-value {
          font-size: 30px;
          font-weight: 700;
          margin: 0;
        }

        .processing {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        }

        .mini-label {
          font-size: 11px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          margin: 0 0 6px;
          color: inherit;
          opacity: 0.7;
        }

        .mini-value {
          margin: 0;
          font-weight: 600;
        }

        .columns {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          gap: 20px;
        }

        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          animation: rise 0.6s ease both;
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

        .pending-list {
          display: grid;
          gap: 12px;
        }

        .pending-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 14px;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
          transition: transform 0.2s ease;
        }

        .pending-item:hover {
          transform: translateY(-2px);
        }

        .pending-title {
          margin: 0 0 4px;
          font-weight: 600;
        }

        .pending-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .status-tag {
          background: #e6ebe9;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .badge {
          background: #1f2a2a;
          color: #f7f2e7;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
        }

        .menu-section {
          margin-top: 28px;
          background: #ffffff;
          border-radius: 18px;
          padding: 20px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
        }

        .menu-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        }

        .menu-card {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          padding: 14px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.06);
          background: #fbfbf9;
          transition: transform 0.2s ease;
        }

        .menu-card:hover {
          transform: translateY(-2px);
        }

        .menu-week {
          margin: 0 0 6px;
          font-weight: 600;
        }

        .menu-meta {
          margin: 0;
          font-size: 12px;
          color: #5f7b74;
        }

        .menu-file {
          margin: 6px 0 0;
          font-size: 11px;
          color: #8a9d97;
        }

        .menu-status {
          align-self: flex-start;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 11px;
          background: #e6ebe9;
        }

        .status-ok {
          background: #d7ece4;
          color: #1f5b46;
        }

        .status-missing {
          background: #f5d9d7;
          color: #7a2d2d;
        }

        .status-pending {
          background: #ece5d7;
          color: #7a5a2d;
        }

        .error {
          background: #ffe3e3;
          padding: 10px 14px;
          border-radius: 12px;
          margin-bottom: 16px;
        }

        @keyframes rise {
          from {
            opacity: 0;
            transform: translateY(10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @media (max-width: 720px) {
          .card.wide {
            grid-column: span 1;
          }

          .nav {
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
