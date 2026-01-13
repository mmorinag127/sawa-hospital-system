import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";

type MenuItem = {
  id: string;
  week_id: string;
  name: string;
  unit_type?: string | null;
  qty_per_serving?: number | string | null;
  temp_type?: string | null;
  daypart?: string | null;
  category?: string | null;
  facility_override?: string | null;
  bag_max_qty?: number | string | null;
  bag_max_unit?: string | null;
};

type WeeklyMenu = {
  id: string;
  filename?: string | null;
};

const unitChoices = [
  { value: "g", label: "グラム(g)" },
  { value: "count", label: "個数" },
  { value: "cut", label: "切" },
];

const uniqueValues = (items: MenuItem[], field: keyof MenuItem) => {
  const values = items
    .map((item) => item[field])
    .filter((value): value is string => typeof value === "string" && value.trim() !== "");
  return Array.from(new Set(values));
};

export default function WeeklyMenuEditorPage() {
  const router = useRouter();
  const { weekId } = router.query;
  const [menu, setMenu] = useState<WeeklyMenu | null>(null);
  const [items, setItems] = useState<MenuItem[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [lastUpload, setLastUpload] = useState<string>("");
  const [savingId, setSavingId] = useState<string | null>(null);

  const loadMenu = async () => {
    if (!weekId || Array.isArray(weekId)) return;
    try {
      const res = await apiClient.get(`/weekly-menus/${weekId}`);
      setMenu(res.data.menu);
      setItems(res.data.items || []);
      setMessage("");
    } catch (err) {
      setMenu(null);
      setItems([]);
      setMessage("週次メニューがまだ登録されていません。");
    }
  };

  useEffect(() => {
    loadMenu();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekId]);

  const handleUpload = async () => {
    if (!weekId || Array.isArray(weekId)) return;
    if (!file) {
      setMessage("アップロードするファイルを選択してください。");
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    const res = await apiClient.post("/weekly-menus", formData, {
      params: { week_id: weekId, sheet_name: sheetName || undefined },
      headers: { "Content-Type": "multipart/form-data" },
    });
    setFile(null);
    await loadMenu();
    const itemCount = res.data?.item_count ?? 0;
    const replaced = res.data?.replaced ? "置換" : "新規";
    const uploadMessage = `${file.name} を${replaced}で反映（${itemCount}件）`;
    setLastUpload(uploadMessage);
    setMessage("アップロードしました。");
  };

  const updateItemField = (idx: number, field: keyof MenuItem, value: string) => {
    const next = [...items];
    next[idx] = { ...next[idx], [field]: value };
    setItems(next);
  };

  const saveItem = async (item: MenuItem) => {
    if (!weekId || Array.isArray(weekId)) return;
    const qtyValue =
      item.qty_per_serving == null || item.qty_per_serving === ""
        ? null
        : Number(item.qty_per_serving);
    const bagMaxValue =
      item.bag_max_qty == null || item.bag_max_qty === "" ? null : Number(item.bag_max_qty);
    setSavingId(item.id);
    await apiClient.put(`/weekly-menus/${weekId}/items/${item.id}`, {
      name: item.name,
      unit_type: item.unit_type,
      qty_per_serving: qtyValue,
      temp_type: item.temp_type,
      daypart: item.daypart,
      category: item.category,
      facility_override: item.facility_override,
      bag_max_qty: bagMaxValue,
      bag_max_unit: item.bag_max_unit,
    });
    setSavingId(null);
    setMessage(`保存しました: ${item.name}`);
  };

  const tempOptions = uniqueValues(items, "temp_type");
  const daypartOptions = uniqueValues(items, "daypart");
  const categoryOptions = uniqueValues(items, "category");

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Weekly Menu</p>
          <h1>週次メニュー編集</h1>
          <p className="subtle">週ID: {Array.isArray(weekId) ? weekId.join(",") : weekId}</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>アップロード</h2>
        </header>
        <div className="upload-grid">
          <div>
            <p className="field-label">ファイル</p>
            <p className="summary-value">{menu?.filename || "未登録"}</p>
          </div>
          <div>
            <p className="field-label">項目数</p>
            <p className="summary-value">{items.length}</p>
          </div>
          <div>
            <p className="field-label">最終アップロード</p>
            <p className="summary-value">{lastUpload || "未実施"}</p>
          </div>
        </div>
        <div className="upload-actions">
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <input
            className="input"
            placeholder="シート名 (任意)"
            value={sheetName}
            onChange={(e) => setSheetName(e.target.value)}
          />
          <button className="btn primary" onClick={handleUpload}>
            アップロード
          </button>
        </div>
        {message && <p className="message">{message}</p>}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>メニュー一覧</h2>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>メニュー名</th>
                <th>単位</th>
                <th>量</th>
                <th>袋最大量</th>
                <th>袋単位</th>
                <th>温冷</th>
                <th>時間帯</th>
                <th>区分</th>
                <th>施設上書き</th>
                <th>保存</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={10}>メニューがありません。</td>
                </tr>
              ) : (
                items.map((item, idx) => (
                  <tr key={item.id}>
                    <td>
                      <input
                        className="input"
                        value={item.name}
                        onChange={(e) => updateItemField(idx, "name", e.target.value)}
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={item.unit_type || ""}
                        onChange={(e) => updateItemField(idx, "unit_type", e.target.value)}
                      >
                        <option value="">未選択</option>
                        {unitChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={item.qty_per_serving ?? ""}
                        onChange={(e) => updateItemField(idx, "qty_per_serving", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        value={item.bag_max_qty ?? ""}
                        onChange={(e) => updateItemField(idx, "bag_max_qty", e.target.value)}
                      />
                    </td>
                    <td>
                      <select
                        className="input"
                        value={item.bag_max_unit || ""}
                        onChange={(e) => updateItemField(idx, "bag_max_unit", e.target.value)}
                      >
                        <option value="">未選択</option>
                        {unitChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        value={item.temp_type || ""}
                        list="temp-type-options"
                        onChange={(e) => updateItemField(idx, "temp_type", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={item.daypart || ""}
                        list="daypart-options"
                        onChange={(e) => updateItemField(idx, "daypart", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={item.category || ""}
                        list="category-options"
                        onChange={(e) => updateItemField(idx, "category", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="input"
                        value={item.facility_override || ""}
                        onChange={(e) => updateItemField(idx, "facility_override", e.target.value)}
                      />
                    </td>
                    <td>
                      <button className="btn" onClick={() => saveItem(item)} disabled={savingId === item.id}>
                        {savingId === item.id ? "保存中..." : "保存"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <datalist id="temp-type-options">
          {tempOptions.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
        <datalist id="daypart-options">
          {daypartOptions.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
        <datalist id="category-options">
          {categoryOptions.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
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

        .upload-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          align-items: end;
        }

        .upload-actions {
          display: flex;
          gap: 12px;
          align-items: center;
          flex-wrap: wrap;
          margin-top: 12px;
        }

        .upload-actions input[type="file"] {
          max-width: 100%;
        }

        .upload-actions .input {
          min-width: 220px;
          flex: 1;
        }

        .field-label {
          color: #5f7b74;
          font-size: 12px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          margin: 0 0 6px;
        }

        .summary-value {
          margin: 0;
          font-weight: 600;
        }

        .message {
          margin-top: 12px;
          padding: 8px 12px;
          border-radius: 10px;
          background: #f0f4f2;
          font-size: 13px;
        }

        .btn {
          border: none;
          border-radius: 999px;
          padding: 8px 14px;
          background: #e6ebe9;
          color: #1f2a2a;
          font-weight: 600;
          cursor: pointer;
        }

        .btn.primary {
          background: #1f2a2a;
          color: #f7f2e7;
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
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
