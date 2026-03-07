import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type TotalRow = {
  date?: string | null;
  daypart?: string | null;
  menu_category?: string | null;
  menu_name?: string | null;
  diet_type?: string | null;
  quantity?: number | null;
};

const dietTypeLabels: Record<string, string> = {
  regular: "常食",
  soft: "軟菜",
  mixer: "ミキサー",
  daycare: "通所",
  staff: "職員",
  no_meat: "禁食(肉禁)",
  no_fish: "禁食(魚禁)",
  change_1: "変更1",
  change_2: "変更2",
  unknown: "不明",
};

const preferredDietOrder = [
  "regular",
  "soft",
  "mixer",
  "daycare",
  "staff",
  "no_meat",
  "no_fish",
  "change_1",
  "change_2",
  "unknown",
];

const formatQuantity = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString("ja-JP");
};

const normalizeDietType = (value?: string | null) => {
  if (!value) return "unknown";
  return value;
};

const formatDateInput = (value: Date) => {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const getLastWeekRange = (endDate: string) => {
  const base = new Date(`${endDate}T00:00:00`);
  if (Number.isNaN(base.getTime())) return null;
  const from = new Date(base);
  from.setDate(base.getDate() - 6);
  return { date_from: formatDateInput(from), date_to: formatDateInput(base) };
};

export default function TotalsPage() {
  const [date, setDate] = useState<string>("");
  const [rows, setRows] = useState<TotalRow[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");
  const [range, setRange] = useState<{ date_from: string; date_to: string } | null>(null);

  useEffect(() => {
    if (!date) {
      setDate(formatDateInput(new Date()));
    }
  }, [date]);

  const loadTotals = async () => {
    if (!date) return;
    const nextRange = getLastWeekRange(date);
    if (!nextRange) return;
    setLoading(true);
    setMessage("");
    setRange(nextRange);
    try {
      const res = await apiClient.get("/totals", { params: nextRange });
      setRows(res.data?.rows || []);
      if (!res.data?.rows?.length) {
        setMessage("対象期間の確定注文がありません。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setRows([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (date) {
      loadTotals();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  const { grouped, dietKeys } = useMemo(() => {
    const byDate: Record<string, Record<string, Record<string, any>>> = {};
    const dietSet = new Set<string>();
    rows.forEach((row) => {
      const dateKey = row.date || "未設定";
      const daypart = row.daypart || "未設定";
      const menuName = row.menu_name || "名称不明";
      const menuCategory = row.menu_category || "";
      const dietKey = normalizeDietType(row.diet_type);
      dietSet.add(dietKey);
      const key = `${menuCategory}|||${menuName}`;
      byDate[dateKey] = byDate[dateKey] || {};
      byDate[dateKey][daypart] = byDate[dateKey][daypart] || {};
      const existing = byDate[dateKey][daypart][key] || {
        menu_name: menuName,
        menu_category: menuCategory,
        totals: {},
      };
      existing.totals[dietKey] = (existing.totals[dietKey] || 0) + Number(row.quantity || 0);
      byDate[dateKey][daypart][key] = existing;
    });
    const diets = Array.from(dietSet);
    diets.sort((a, b) => {
      const ia = preferredDietOrder.indexOf(a);
      const ib = preferredDietOrder.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
    return { grouped: byDate, dietKeys: diets };
  }, [rows]);

  const dates = Object.keys(grouped).sort((a, b) => {
    if (a === "未設定") return 1;
    if (b === "未設定") return -1;
    return a < b ? 1 : -1;
  });

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Totals</p>
          <h1>総量</h1>
          <p className="subtle">確定注文から直近1週間の総量を集計します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>フィルタ</h2>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">基準日</span>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <button className="btn primary" onClick={loadTotals} disabled={loading}>
            {loading ? "集計中..." : "再取得"}
          </button>
        </div>
        {range ? <p className="range-note">対象期間: {range.date_from} 〜 {range.date_to}</p> : null}
      </section>

      {message ? <p className="message">{message}</p> : null}

      {dates.map((dateKey) => {
        const dayparts = Object.keys(grouped[dateKey] || {}).sort();
        return (
          <section key={dateKey} className="panel">
            <header className="panel-header">
              <h2>{dateKey === "未設定" ? "日付未設定" : dateKey}</h2>
            </header>
            {dayparts.map((daypart) => {
              const items = Object.values(grouped[dateKey][daypart] || {});
              return (
                <div key={daypart} className="daypart-block">
                  <h3 className="daypart-title">{daypart}</h3>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>献立区分</th>
                          <th>メニュー</th>
                          {dietKeys.map((diet) => (
                            <th key={diet}>{dietTypeLabels[diet] || diet}</th>
                          ))}
                          <th>合計</th>
                        </tr>
                      </thead>
                      <tbody>
                        {items.map((item: any) => {
                          const total = dietKeys.reduce((sum, key) => sum + (item.totals[key] || 0), 0);
                          return (
                            <tr key={`${daypart}-${item.menu_category}-${item.menu_name}`}>
                              <td>{item.menu_category || "-"}</td>
                              <td>{item.menu_name}</td>
                              {dietKeys.map((diet) => (
                                <td key={diet}>{formatQuantity(item.totals[diet])}</td>
                              ))}
                              <td className="total">{formatQuantity(total)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })}
          </section>
        );
      })}

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

        .range-note {
          margin: 12px 0 0;
          color: #51615c;
          font-size: 13px;
        }

        .daypart-block + .daypart-block {
          margin-top: 18px;
        }

        .daypart-title {
          margin: 0 0 10px;
          font-size: 16px;
          color: #1f2a2a;
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

        .total {
          font-weight: 700;
          color: #1f2a2a;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
