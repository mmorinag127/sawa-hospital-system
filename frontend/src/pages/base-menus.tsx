import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type BaseMenuItem = {
  id?: string;
  cycle_day?: number | string;
  daypart?: string | null;
  category?: string | null;
  name?: string | null;
  diet_type?: string | null;
  slot_index?: number | string | null;
  unit_type?: string | null;
  qty_per_serving?: number | null;
  temp_type?: string | null;
  bag_max_qty?: number | null;
  bag_max_unit?: string | null;
  condiments?: string[] | null;
};

const normalizeNumber = (value: string) => {
  if (!value) return null;
  const num = Number(value);
  return Number.isNaN(num) ? null : num;
};

export default function BaseMenusPage() {
  const [items, setItems] = useState<BaseMenuItem[]>([]);
  const [cycleDay, setCycleDay] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const loadItems = async (day?: string) => {
    setLoading(true);
    setMessage("");
    try {
      const res = await apiClient.get("/base-menus", {
        params: day ? { cycle_day: day } : undefined,
      });
      setItems(res.data?.items || []);
      if (!res.data?.items?.length) {
        setMessage("該当データがありません。");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadItems();
  }, []);

  const addRow = () => {
    setItems((prev) => [
      ...prev,
      {
        id: undefined,
        cycle_day: cycleDay ? Number(cycleDay) : 1,
        daypart: "",
        category: "",
        name: "",
        diet_type: "",
        slot_index: "",
      },
    ]);
  };

  const updateField = (idx: number, field: keyof BaseMenuItem, value: string) => {
    setItems((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], [field]: value };
      return next;
    });
  };

  const deleteRow = (idx: number) => {
    setItems((prev) => prev.filter((_, index) => index !== idx));
  };

  const saveAll = async () => {
    setSaving(true);
    setMessage("");
    const payload = items
      .map((item) => ({
        cycle_day: Number(item.cycle_day),
        daypart: item.daypart || null,
        category: item.category || null,
        name: item.name || "",
        diet_type: item.diet_type || null,
        slot_index: normalizeNumber(String(item.slot_index || "")),
      }))
      .filter((item) => item.name && item.cycle_day >= 1 && item.cycle_day <= 45);
    try {
      const res = await apiClient.post("/base-menus", { items: payload });
      setMessage(`保存しました（${res.data?.created ?? payload.length}件）。`);
      await loadItems(cycleDay || undefined);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `保存に失敗しました: ${detail}` : "保存に失敗しました。");
    } finally {
      setSaving(false);
    }
  };

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      const dayA = Number(a.cycle_day || 0);
      const dayB = Number(b.cycle_day || 0);
      if (dayA !== dayB) return dayA - dayB;
      const partA = a.daypart || "";
      const partB = b.daypart || "";
      if (partA !== partB) return partA.localeCompare(partB);
      return (a.slot_index || 0) > (b.slot_index || 0) ? 1 : -1;
    });
  }, [items]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Base Menus</p>
          <h1>基準メニュー</h1>
          <p className="subtle">45日サイクルの基準メニューを管理します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>操作</h2>
          <span className="badge">合計 {sortedItems.length} 件</span>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">サイクル日 (1-45)</span>
            <input
              className="input"
              type="number"
              min={1}
              max={45}
              value={cycleDay}
              onChange={(e) => setCycleDay(e.target.value)}
            />
          </label>
          <button className="btn primary" onClick={() => loadItems(cycleDay || undefined)} disabled={loading}>
            {loading ? "読込中..." : "読み込み"}
          </button>
          <button className="btn" onClick={() => loadItems()} disabled={loading}>
            全件
          </button>
          <button className="btn" onClick={addRow}>
            追加
          </button>
          <button className="btn primary" onClick={saveAll} disabled={saving}>
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
        {message ? <p className="message">{message}</p> : null}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>日</th>
                <th>区分</th>
                <th>献立区分</th>
                <th>メニュー名</th>
                <th>食種</th>
                <th>順番</th>
                <th>単位(参照)</th>
                <th>1人前(参照)</th>
                <th>温冷(参照)</th>
                <th>袋上限(参照)</th>
                <th>付属品(参照)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map((item, idx) => (
                <tr key={`${item.id || "new"}-${idx}`}>
                  <td>
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={45}
                      value={item.cycle_day ?? ""}
                      onChange={(e) => updateField(idx, "cycle_day", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={item.daypart ?? ""}
                      onChange={(e) => updateField(idx, "daypart", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={item.category ?? ""}
                      onChange={(e) => updateField(idx, "category", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={item.name ?? ""}
                      onChange={(e) => updateField(idx, "name", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={item.diet_type ?? ""}
                      onChange={(e) => updateField(idx, "diet_type", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      value={item.slot_index ?? ""}
                      onChange={(e) => updateField(idx, "slot_index", e.target.value)}
                    />
                  </td>
                  <td className="readonly">{item.unit_type || "-"}</td>
                  <td className="readonly">
                    {item.qty_per_serving == null || Number.isNaN(Number(item.qty_per_serving))
                      ? "-"
                      : Number(item.qty_per_serving)}
                  </td>
                  <td className="readonly">{item.temp_type || "-"}</td>
                  <td className="readonly">
                    {item.bag_max_qty == null || Number.isNaN(Number(item.bag_max_qty))
                      ? "-"
                      : `${Number(item.bag_max_qty)}${item.bag_max_unit || ""}`}
                  </td>
                  <td className="readonly">
                    {Array.isArray(item.condiments) && item.condiments.length ? item.condiments.join(", ") : "-"}
                  </td>
                  <td>
                    <button className="btn danger" onClick={() => deleteRow(idx)}>
                      削除
                    </button>
                  </td>
                </tr>
              ))}
              {!sortedItems.length && (
                <tr>
                  <td colSpan={12} className="empty">
                    データがありません。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="hint">保存は全件置換です。必要な項目を確認してから保存してください。</p>
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
          width: 100%;
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

        .btn.danger {
          background: #c95858;
          color: #fff;
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

        .empty {
          text-align: center;
          padding: 14px 0;
        }

        .readonly {
          color: #40524d;
          font-size: 13px;
          white-space: nowrap;
        }

        .hint {
          margin-top: 10px;
          font-size: 12px;
          color: #6b7a7a;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
