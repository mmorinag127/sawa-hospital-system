import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import { fetchFacilityNameMap, type FacilityNameMap } from "../services/facilityData";

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

export default function FacilityOrdersPage() {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [facility, setFacility] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [facilityNameMap, setFacilityNameMap] = useState<FacilityNameMap>({});

  const [dailyDate, setDailyDate] = useState<string>("");
  const [dailyOrders, setDailyOrders] = useState<OrderSummary[]>([]);
  const [dailyLoading, setDailyLoading] = useState(false);
  const [dailyMessage, setDailyMessage] = useState("");

  const loadOrders = async () => {
    setLoading(true);
    setMessage("");
    try {
      const res = await apiClient.get("/orders", { params: { include_ocr: false } });
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
  }, []);

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
    if (!dailyDate) {
      const today = new Date();
      setDailyDate(today.toISOString().slice(0, 10));
    }
  }, [dailyDate]);

  const facilityOptions = useMemo(() => {
    const set = new Set<string>();
    orders.forEach((order) => {
      if (order.facility) {
        set.add(order.facility);
      } else {
        set.add("未確定");
      }
    });
    return Array.from(set).sort();
  }, [orders]);

  const filteredOrders = useMemo(() => {
    if (!facility) return orders;
    if (facility === "未確定") {
      return orders.filter((order) => !order.facility);
    }
    return orders.filter((order) => order.facility === facility);
  }, [orders, facility]);

  const weeklyGroups = useMemo(() => {
    const map: Record<string, { counts: Record<string, number>; orders: OrderSummary[] }> = {};
    filteredOrders.forEach((order) => {
      const week = order.week || "未確定";
      if (!map[week]) {
        map[week] = {
          counts: { 未着: 0, 要確認: 0, 確定: 0, エラー: 0 },
          orders: [],
        };
      }
      const status = order.status || "";
      if (map[week].counts[status] !== undefined) {
        map[week].counts[status] += 1;
      }
      map[week].orders.push(order);
    });
    Object.values(map).forEach((group) => {
      group.orders.sort((a, b) => {
        const at = a.received_at ? new Date(a.received_at).getTime() : 0;
        const bt = b.received_at ? new Date(b.received_at).getTime() : 0;
        return bt - at;
      });
    });
    return map;
  }, [filteredOrders]);

  const weeklyKeys = useMemo(() => Object.keys(weeklyGroups).sort(), [weeklyGroups]);

  const loadDailyOrders = async () => {
    if (!dailyDate) return;
    setDailyLoading(true);
    setDailyMessage("");
    try {
      const params: Record<string, string> = { date: dailyDate };
      if (facility && facility !== "未確定") {
        params.facility = facility;
      }
      const res = await apiClient.get("/orders/by-line-date", { params });
      let next = res.data?.orders || [];
      if (facility === "未確定") {
        next = next.filter((order: OrderSummary) => !order.facility);
      }
      setDailyOrders(next);
      if (!next.length) {
        setDailyMessage("該当する注文がありません。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setDailyMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setDailyOrders([]);
    } finally {
      setDailyLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Facility Orders</p>
          <h1>施設別注文</h1>
          <p className="subtle">施設ごとの週次進捗と日別注文を確認します。</p>
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
            <span className="field-label">施設</span>
            <select className="input" value={facility} onChange={(e) => setFacility(e.target.value)}>
              <option value="">全施設</option>
              {facilityOptions.map((name) => (
                <option key={name} value={name}>
                  {name === "未確定"
                    ? "未確定"
                    : facilityNameMap[name]
                      ? `${facilityNameMap[name]} (${name})`
                      : name}
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" onClick={loadOrders} disabled={loading}>
            {loading ? "更新中..." : "更新"}
          </button>
        </div>
      </section>

      {message ? <p className="message">{message}</p> : null}

      <section className="panel">
        <header className="panel-header">
          <h2>週次進捗</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>週</th>
                <th>未着</th>
                <th>要確認</th>
                <th>確定</th>
                <th>エラー</th>
              </tr>
            </thead>
            <tbody>
              {weeklyKeys.length === 0 ? (
                <tr>
                  <td colSpan={5}>該当データなし</td>
                </tr>
              ) : (
                weeklyKeys.map((week) => (
                  <tr key={week}>
                    <td>{week}</td>
                    <td>{weeklyGroups[week].counts["未着"]}</td>
                    <td>{weeklyGroups[week].counts["要確認"]}</td>
                    <td>{weeklyGroups[week].counts["確定"]}</td>
                    <td>{weeklyGroups[week].counts["エラー"]}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>週次の注文一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>週</th>
                <th>ステータス</th>
                <th>受信日時</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredOrders.length === 0 ? (
                <tr>
                  <td colSpan={4}>該当データなし</td>
                </tr>
              ) : (
                filteredOrders.map((order) => (
                  <tr key={order.id}>
                    <td>{order.week || "未確定"}</td>
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
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>日別注文</h2>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">日付</span>
            <input
              className="input"
              type="date"
              value={dailyDate}
              onChange={(e) => setDailyDate(e.target.value)}
            />
          </label>
          <button className="btn primary" onClick={loadDailyOrders} disabled={dailyLoading}>
            {dailyLoading ? "取得中..." : "取得"}
          </button>
        </div>
        {dailyMessage ? <p className="message">{dailyMessage}</p> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ステータス</th>
                <th>週</th>
                <th>受信日時</th>
                <th>行数</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {dailyOrders.length === 0 ? (
                <tr>
                  <td colSpan={5}>該当データなし</td>
                </tr>
              ) : (
                dailyOrders.map((order) => (
                  <tr key={order.id}>
                    <td>{order.status || "-"}</td>
                    <td>{order.week || "未確定"}</td>
                    <td>{formatTimestamp(order.received_at)}</td>
                    <td>{order.line_count ?? "-"}</td>
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
