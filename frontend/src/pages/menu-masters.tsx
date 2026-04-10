import { useEffect, useMemo, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type MenuMasterItem = {
  id: string;
  name: string;
  normalized_name?: string | null;
  unit_type?: string | null;
  qty_per_serving?: number | null;
  bag_max_qty?: number | null;
  bag_max_unit?: string | null;
  temp_type?: string | null;
  daypart?: string | null;
  category?: string | null;
  condiments?: string[] | null;
};

const TEMP_TYPE_OPTIONS = [
  { value: "", label: "未選択" },
  { value: "hot", label: "温" },
  { value: "cold", label: "冷" },
];

const UNIT_OPTIONS = [
  { value: "", label: "未選択" },
  { value: "g", label: "グラム(g)" },
  { value: "cut", label: "切れ" },
  { value: "count", label: "個" },
];

const parseCondiments = (text: string): string[] => {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
};

export default function MenuMastersPage() {
  const [items, setItems] = useState<MenuMasterItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [newItem, setNewItem] = useState<Partial<MenuMasterItem>>({
    name: "",
    unit_type: "",
    qty_per_serving: null,
    bag_max_qty: null,
    bag_max_unit: "",
    temp_type: "",
    daypart: "",
    category: "",
    condiments: [],
  });

  const loadItems = async (q?: string) => {
    setLoading(true);
    setMessage("");
    try {
      const res = await apiClient.get("/menu-masters", {
        params: { q: q || undefined, limit: 2000 },
      });
      setItems(res.data?.items || []);
      if (!(res.data?.items || []).length) {
        setMessage("該当メニューがありません。");
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

  const updateField = (idx: number, field: keyof MenuMasterItem, value: string) => {
    setItems((prev) => {
      const next = [...prev];
      const current = { ...next[idx] } as any;
      if (field === "qty_per_serving" || field === "bag_max_qty") {
        current[field] = value.trim() === "" ? null : Number(value);
      } else if (field === "condiments") {
        current[field] = parseCondiments(value);
      } else {
        current[field] = value;
      }
      next[idx] = current;
      return next;
    });
  };

  const saveRow = async (idx: number) => {
    const row = items[idx];
    if (!row?.id) return;
    setSavingId(row.id);
    setMessage("");
    try {
      await apiClient.put(`/menu-masters/${row.id}`, {
        name: row.name,
        unit_type: row.unit_type || null,
        qty_per_serving: row.qty_per_serving,
        bag_max_qty: row.bag_max_qty,
        bag_max_unit: row.bag_max_unit || null,
        temp_type: row.temp_type || null,
        daypart: row.daypart || null,
        category: row.category || null,
        condiments: row.condiments || [],
      });
      setMessage(`保存しました: ${row.name}`);
      await loadItems(query);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `保存に失敗しました: ${detail}` : "保存に失敗しました。");
    } finally {
      setSavingId(null);
    }
  };

  const createItem = async () => {
    if (!newItem.name || !newItem.name.trim()) {
      setMessage("メニュー名は必須です。");
      return;
    }
    setMessage("");
    try {
      await apiClient.post("/menu-masters", {
        name: newItem.name,
        unit_type: newItem.unit_type || null,
        qty_per_serving: newItem.qty_per_serving,
        bag_max_qty: newItem.bag_max_qty,
        bag_max_unit: newItem.bag_max_unit || null,
        temp_type: newItem.temp_type || null,
        daypart: newItem.daypart || null,
        category: newItem.category || null,
        condiments: newItem.condiments || [],
      });
      setNewItem({
        name: "",
        unit_type: "",
        qty_per_serving: null,
        bag_max_qty: null,
        bag_max_unit: "",
        temp_type: "",
        daypart: "",
        category: "",
        condiments: [],
      });
      setMessage("新規メニューを追加しました。");
      await loadItems(query);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `追加に失敗しました: ${detail}` : "追加に失敗しました。");
    }
  };

  const rowCount = useMemo(() => items.length, [items]);

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Menu Masters</p>
          <h1>メニューマスター</h1>
          <p className="subtle">メニュー名・単位・温冷・袋上限・付属品を管理します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>検索</h2>
          <span className="badge">合計 {rowCount} 件</span>
        </header>
        <div className="filters">
          <input
            className="input"
            placeholder="メニュー名で検索"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="btn primary" onClick={() => loadItems(query)} disabled={loading}>
            {loading ? "読込中..." : "検索"}
          </button>
          <button className="btn" onClick={() => loadItems()} disabled={loading}>
            全件
          </button>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>新規追加</h2>
        </header>
        <div className="form-grid">
          <input
            className="input"
            placeholder="メニュー名 *"
            value={newItem.name || ""}
            onChange={(event) => setNewItem((prev) => ({ ...prev, name: event.target.value }))}
          />
          <select
            className="input"
            data-testid="new-menu-master-unit-type"
            value={newItem.unit_type || ""}
            onChange={(event) => setNewItem((prev) => ({ ...prev, unit_type: event.target.value }))}
          >
            {UNIT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            className="input"
            type="number"
            placeholder="1人前数量"
            value={newItem.qty_per_serving ?? ""}
            onChange={(event) =>
              setNewItem((prev) => ({
                ...prev,
                qty_per_serving: event.target.value === "" ? null : Number(event.target.value),
              }))
            }
          />
          <input
            className="input"
            type="number"
            placeholder="袋上限数量"
            value={newItem.bag_max_qty ?? ""}
            onChange={(event) =>
              setNewItem((prev) => ({
                ...prev,
                bag_max_qty: event.target.value === "" ? null : Number(event.target.value),
              }))
            }
          />
          <select
            className="input"
            data-testid="new-menu-master-bag-max-unit"
            value={newItem.bag_max_unit || ""}
            onChange={(event) => setNewItem((prev) => ({ ...prev, bag_max_unit: event.target.value }))}
          >
            {UNIT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            className="input"
            value={newItem.temp_type || ""}
            onChange={(event) => setNewItem((prev) => ({ ...prev, temp_type: event.target.value }))}
          >
            {TEMP_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            className="input"
            placeholder="食事帯 (朝/昼/夕)"
            value={newItem.daypart || ""}
            onChange={(event) => setNewItem((prev) => ({ ...prev, daypart: event.target.value }))}
          />
          <input
            className="input"
            placeholder="分類 (主菜/副菜)"
            value={newItem.category || ""}
            onChange={(event) => setNewItem((prev) => ({ ...prev, category: event.target.value }))}
          />
          <input
            className="input"
            placeholder="付属品 (カンマ区切り)"
            value={(newItem.condiments || []).join(", ")}
            onChange={(event) =>
              setNewItem((prev) => ({ ...prev, condiments: parseCondiments(event.target.value) }))
            }
          />
        </div>
        <div className="actions">
          <button className="btn primary" onClick={createItem}>
            追加
          </button>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>一覧・編集</h2>
        </header>
        {message ? <p className="message">{message}</p> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>メニュー名</th>
                <th>単位</th>
                <th>1人前</th>
                <th>袋上限</th>
                <th>袋単位</th>
                <th>温冷</th>
                <th>食事帯</th>
                <th>分類</th>
                <th>付属品</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => (
                <tr key={item.id}>
                  <td>
                    <input
                      className="input"
                      value={item.name || ""}
                      onChange={(event) => updateField(idx, "name", event.target.value)}
                    />
                  </td>
                  <td>
                    <select
                      className="input"
                      data-testid={`menu-master-unit-type-${item.id}`}
                      value={item.unit_type || ""}
                      onChange={(event) => updateField(idx, "unit_type", event.target.value)}
                    >
                      {UNIT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      value={item.qty_per_serving ?? ""}
                      onChange={(event) => updateField(idx, "qty_per_serving", event.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      value={item.bag_max_qty ?? ""}
                      onChange={(event) => updateField(idx, "bag_max_qty", event.target.value)}
                    />
                  </td>
                  <td>
                    <select
                      className="input"
                      data-testid={`menu-master-bag-max-unit-${item.id}`}
                      value={item.bag_max_unit || ""}
                      onChange={(event) => updateField(idx, "bag_max_unit", event.target.value)}
                    >
                      {UNIT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      className="input"
                      value={item.temp_type || ""}
                      onChange={(event) => updateField(idx, "temp_type", event.target.value)}
                    >
                      {TEMP_TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      className="input"
                      value={item.daypart || ""}
                      onChange={(event) => updateField(idx, "daypart", event.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={item.category || ""}
                      onChange={(event) => updateField(idx, "category", event.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={(item.condiments || []).join(", ")}
                      onChange={(event) => updateField(idx, "condiments", event.target.value)}
                    />
                  </td>
                  <td>
                    <button className="btn ghost" onClick={() => saveRow(idx)} disabled={savingId === item.id}>
                      {savingId === item.id ? "保存中..." : "保存"}
                    </button>
                  </td>
                </tr>
              ))}
              {!items.length && (
                <tr>
                  <td colSpan={10} className="empty">
                    データがありません。
                  </td>
                </tr>
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
          grid-template-columns: 1fr auto auto;
          gap: 12px;
          align-items: center;
        }

        .form-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 10px;
        }

        .actions {
          margin-top: 12px;
          display: flex;
          justify-content: flex-end;
        }

        .table-wrap {
          overflow: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          min-width: 1100px;
        }

        th,
        td {
          border-bottom: 1px solid #ecf0ef;
          text-align: left;
          padding: 10px 8px;
          vertical-align: top;
        }

        th {
          font-size: 12px;
          color: #5f6f69;
          background: #f7faf9;
          position: sticky;
          top: 0;
        }

        .input {
          width: 100%;
          border: 1px solid #d3ddda;
          border-radius: 8px;
          padding: 8px 10px;
          font-size: 13px;
          background: #fff;
        }

        .btn {
          border: 1px solid #c8d5d1;
          border-radius: 10px;
          background: #fff;
          color: #21302d;
          padding: 8px 14px;
          cursor: pointer;
          font-weight: 600;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
          border-color: #1f2a2a;
        }

        .btn.ghost {
          background: #eef4f2;
        }

        .btn:disabled {
          opacity: 0.6;
          cursor: wait;
        }

        .message {
          color: #314542;
          margin: 0 0 12px;
          font-weight: 600;
        }

        .empty {
          text-align: center;
          color: #667874;
          padding: 18px;
        }

        @media (max-width: 900px) {
          .filters {
            grid-template-columns: 1fr 1fr;
          }
        }
      `}</style>
    </main>
  );
}
