import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import TopNav from "../../components/TopNav";
import { apiClient } from "../../services/apiClient";
import { DIET_TYPE_OPTIONS, formatDietTypeLabel } from "../../services/menuVocabulary";

type MenuItem = {
  id: string;
  month_id: string;
  name: string;
  unit_type?: string | null;
  qty_per_serving?: number | string | null;
  temp_type?: string | null;
  daypart?: string | null;
  category?: string | null;
  diet_type?: string | null;
  facility_override?: string | null;
  bag_max_qty?: number | string | null;
  bag_max_unit?: string | null;
};

type MenuEntry = {
  id: string;
  month_id: string;
  menu_date?: string | null;
  daypart?: string | null;
  name: string;
  category?: string | null;
  diet_type?: string | null;
  slot_index?: number | null;
  facility_override?: string | null;
};

type MonthlyMenu = {
  id: string;
  filename?: string | null;
  display_name?: string | null;
  uploaded_at?: string | null;
};

type MenuUploadEntry = {
  id: string;
  month_id: string;
  uploaded_at?: string | null;
  filename?: string | null;
  sheet_name?: string | null;
  item_count?: number | null;
  replaced?: boolean;
  actor?: string | null;
  download_available?: boolean;
  archive_error?: string | null;
  scope_override?: string | null;
};

type FacilityOption = {
  id: string;
  name: string;
};

type TagScopeOption = {
  value: string;
  scope_override?: string | null;
  facility_ids?: string[];
  facility_names?: string[];
  facility_count?: number;
};

const unitChoices = [
  { value: "g", label: "グラム(g)" },
  { value: "count", label: "個数" },
  { value: "cut", label: "切" },
];

const tempTypeChoices = [
  { value: "", label: "未選択" },
  { value: "hot", label: "温" },
  { value: "cold", label: "冷" },
];

const uniqueValues = (items: MenuItem[], field: keyof MenuItem) => {
  const values = items
    .map((item) => item[field])
    .filter((value): value is string => typeof value === "string" && value.trim() !== "");
  return Array.from(new Set(values));
};

export default function MonthlyMenuEditorPage() {
  const router = useRouter();
  const { monthId } = router.query;
  const [menu, setMenu] = useState<MonthlyMenu | null>(null);
  const [items, setItems] = useState<MenuItem[]>([]);
  const [entries, setEntries] = useState<MenuEntry[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState<string>("");
  const [scopeType, setScopeType] = useState<string>("base");
  const [scopeValue, setScopeValue] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [lastUpload, setLastUpload] = useState<string>("");
  const [uploadHistory, setUploadHistory] = useState<MenuUploadEntry[]>([]);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const [downloadingUploadId, setDownloadingUploadId] = useState<string | null>(null);
  const [facilities, setFacilities] = useState<FacilityOption[]>([]);
  const [tagOptions, setTagOptions] = useState<TagScopeOption[]>([]);
  const [condimentFile, setCondimentFile] = useState<File | null>(null);
  const [condimentUploading, setCondimentUploading] = useState<boolean>(false);
  const [condimentMessage, setCondimentMessage] = useState<string>("");

  const formatScopeLabel = (scopeOverride?: string | null) => {
    const value = (scopeOverride || "").trim();
    if (!value) return "共通(base)";
    if (value.startsWith("TAG:")) {
      return `タグ:${value.slice(4)}`;
    }
    const facility = facilities.find((item) => item.id === value);
    return facility ? `施設:${facility.name}` : `施設:${value}`;
  };

  const loadUploadHistory = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/uploads`);
      setUploadHistory(res.data?.items || []);
    } catch {
      setUploadHistory([]);
    }
  };

  const loadScopeOptions = async () => {
    try {
      const res = await apiClient.get("/monthly-menus/scope-options");
      setFacilities(res.data?.facilities || []);
      setTagOptions(res.data?.tags || []);
    } catch {
      setFacilities([]);
      setTagOptions([]);
    }
  };

  const loadMenu = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}`);
      setMenu(res.data.menu);
      setItems(res.data.items || []);
      setEntries(res.data.entries || []);
      setMessage("");
    } catch (err: any) {
      setMenu(null);
      setItems([]);
      setEntries([]);
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("権限がありません。月次メニューの操作にはユーザー2以上の権限が必要です。");
      } else if (status === 404) {
        setMessage("月次メニューがまだ登録されていません。");
      } else {
        setMessage("月次メニューの読込に失敗しました。");
      }
    }
    await loadUploadHistory();
  };

  useEffect(() => {
    loadMenu();
    loadScopeOptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monthId]);

  const handleUpload = async () => {
    if (!monthId || Array.isArray(monthId)) return;
    if (!file) {
      setMessage("アップロードするファイルを選択してください。");
      return;
    }
    if (scopeType !== "base" && !scopeValue.trim()) {
      setMessage(scopeType === "facility" ? "施設差分を登録する施設を選択してください。" : "タグ差分のタグを選択してください。");
      return;
    }
    setUploading(true);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await apiClient.post("/monthly-menus", formData, {
        params: {
          month_id: monthId,
          sheet_name: sheetName || undefined,
          scope_type: scopeType,
          scope_value: scopeType === "base" ? undefined : scopeValue || undefined,
        },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setFile(null);
      await loadMenu();
      const itemCount = res.data?.item_count ?? 0;
      const replaced = res.data?.replaced ? "置換" : "新規";
      const scopeLabel = formatScopeLabel(res.data?.scope_override || (scopeType === "base" ? null : scopeValue));
      const uploadMessage = `${file.name} を${replaced}で反映（${itemCount}件 / ${scopeLabel}）`;
      setLastUpload(uploadMessage);
      setMessage("アップロードしました。");
      await loadUploadHistory();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("アップロード失敗: 権限がありません。");
      } else {
        setMessage(detail ? `アップロード失敗: ${detail}` : "アップロードに失敗しました。");
      }
    } finally {
      setUploading(false);
    }
  };

  const handleDownloadUpload = async (upload: MenuUploadEntry) => {
    if (!monthId || Array.isArray(monthId)) return;
    setDownloadingUploadId(upload.id);
    try {
      const res = await apiClient.get(`/monthly-menus/${monthId}/uploads/${upload.id}/download`, {
        responseType: "blob",
      });
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const disposition = String(res.headers?.["content-disposition"] || "");
      const matched = disposition.match(/filename=\"?([^\";]+)\"?/i);
      const filename = matched?.[1] || upload.filename || "monthly-menu.xlsx";
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    } finally {
      setDownloadingUploadId(null);
    }
  };

  const handleCondimentUpload = async () => {
    if (!condimentFile) {
      setCondimentMessage("付属品フラグのファイルを選択してください。");
      return;
    }
    setCondimentUploading(true);
    setCondimentMessage("アップロード中...");
    const formData = new FormData();
    formData.append("file", condimentFile);
    try {
      const res = await apiClient.post("/monthly-menus/condiments", formData, {
        params: { sheet_name: "主菜" },
        headers: { "Content-Type": "multipart/form-data" },
      });
      const itemsCount = res.data?.items ?? 0;
      setCondimentMessage(`付属品フラグを反映しました（${itemsCount}件）。`);
      setCondimentFile(null);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setCondimentMessage("反映に失敗しました: 権限がありません。");
      } else {
        setCondimentMessage(detail ? `反映に失敗しました: ${detail}` : "反映に失敗しました。");
      }
    } finally {
      setCondimentUploading(false);
    }
  };

  const updateItemField = (idx: number, field: keyof MenuItem, value: string) => {
    const next = [...items];
    next[idx] = { ...next[idx], [field]: value };
    setItems(next);
  };

  const saveItem = async (item: MenuItem) => {
    if (!monthId || Array.isArray(monthId)) return;
    const qtyValue =
      item.qty_per_serving == null || item.qty_per_serving === ""
        ? null
        : Number(item.qty_per_serving);
    const bagMaxValue =
      item.bag_max_qty == null || item.bag_max_qty === "" ? null : Number(item.bag_max_qty);
    setSavingId(item.id);
    try {
      await apiClient.put(`/monthly-menus/${monthId}/items/${item.id}`, {
        name: item.name,
        unit_type: item.unit_type,
        qty_per_serving: qtyValue,
        temp_type: item.temp_type,
        daypart: item.daypart,
        category: item.category,
        diet_type: item.diet_type,
        facility_override: item.facility_override,
        bag_max_qty: bagMaxValue,
        bag_max_unit: item.bag_max_unit,
      });
      setMessage(`保存しました: ${item.name}`);
      await loadMenu();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const status = err?.response?.status;
      if (status === 403) {
        setMessage("保存に失敗しました: 権限がありません。");
      } else {
        setMessage(detail ? `保存に失敗しました: ${detail}` : "保存に失敗しました。");
      }
    } finally {
      setSavingId(null);
    }
  };

  const daypartOptions = uniqueValues(items, "daypart");
  const categoryOptions = uniqueValues(items, "category");
  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Monthly Menu</p>
          <h1>月次メニュー編集</h1>
          <p className="subtle">月ID: {Array.isArray(monthId) ? monthId.join(",") : monthId}</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>アップロード</h2>
        </header>
        <div className="upload-grid">
          <div>
            <p className="field-label">最終登録</p>
            <p className="summary-value">{menu?.display_name || "未登録"}</p>
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
          <select
            className="input scope-select"
            value={scopeType}
            onChange={(e) => {
              setScopeType(e.target.value);
              setScopeValue("");
            }}
          >
            <option value="base">共通(base)</option>
            <option value="facility">施設差分</option>
            <option value="tag">タグ差分</option>
          </select>
          {scopeType === "facility" && (
            <select className="input scope-input" value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
              <option value="">施設を選択</option>
              {facilities.map((facility) => (
                <option key={facility.id} value={facility.id}>
                  {facility.name} ({facility.id})
                </option>
              ))}
            </select>
          )}
          {scopeType === "tag" && (
            <select className="input scope-input" value={scopeValue} onChange={(e) => setScopeValue(e.target.value)}>
              <option value="">タグを選択</option>
              {tagOptions.map((tag) => (
                <option key={tag.value} value={tag.value}>
                  {tag.value}
                  {tag.facility_count ? ` (${tag.facility_count}施設)` : ""}
                </option>
              ))}
            </select>
          )}
          <button className="btn primary" onClick={handleUpload} disabled={uploading}>
            {uploading ? "アップロード中..." : "アップロード"}
          </button>
        </div>
        <p className="subtle scope-note">
          通常は <strong>共通(base)</strong> を使います。施設だけ違う献立は <strong>施設差分</strong>、複数施設で共通の差分は
          <strong> タグ差分</strong> を選びます。
        </p>
        {scopeType === "tag" && tagOptions.length === 0 && (
          <p className="subtle scope-note">タグがまだありません。施設設定でタグを登録してから選択してください。</p>
        )}
        {message && <p className="message">{message}</p>}
        <div className="upload-history">
          <div className="history-header">
            <h3>これまでのアップロード</h3>
            <p className="subtle">新しい履歴はダウンロードできます。過去分はファイル未保存のため一覧のみです。</p>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>登録日時</th>
                  <th>ファイル名</th>
                  <th>シート名</th>
                  <th>適用先</th>
                  <th>件数</th>
                  <th>更新種別</th>
                  <th>ダウンロード</th>
                </tr>
              </thead>
              <tbody>
                {uploadHistory.length === 0 ? (
                  <tr>
                    <td colSpan={7}>履歴はまだありません。</td>
                  </tr>
                ) : (
                  uploadHistory.map((upload) => (
                    <tr key={upload.id}>
                      <td>{upload.uploaded_at ? new Date(upload.uploaded_at).toLocaleString("ja-JP") : "-"}</td>
                      <td>{upload.filename || "-"}</td>
                      <td>{upload.sheet_name || "-"}</td>
                      <td>{formatScopeLabel(upload.scope_override)}</td>
                      <td>{upload.item_count ?? "-"}</td>
                      <td>{upload.replaced ? "置換" : "新規"}</td>
                      <td>
                        {upload.download_available ? (
                          <button
                            className="btn"
                            type="button"
                            onClick={() => handleDownloadUpload(upload)}
                            disabled={downloadingUploadId === upload.id}
                          >
                            {downloadingUploadId === upload.id ? "取得中..." : "ダウンロード"}
                          </button>
                        ) : (
                          <span className="history-note">
                            {upload.archive_error ? "保存失敗" : "履歴のみ"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>付属品フラグ</h2>
          <p className="subtle">献立メニューからソース等の付属品フラグを反映します。</p>
        </header>
        <div className="upload-actions">
          <input type="file" onChange={(e) => setCondimentFile(e.target.files?.[0] || null)} />
          <button className="btn primary" onClick={handleCondimentUpload} disabled={condimentUploading}>
            {condimentUploading ? "反映中..." : "反映する"}
          </button>
        </div>
        {condimentMessage && <p className="message">{condimentMessage}</p>}
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
                <th>食種</th>
                <th>適用先</th>
                <th>保存</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={11}>メニューがありません。</td>
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
                      <select
                        className="input"
                        value={item.temp_type || ""}
                        onChange={(e) => updateItemField(idx, "temp_type", e.target.value)}
                      >
                        {tempTypeChoices.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
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
                      <select
                        className="input"
                        value={item.diet_type || ""}
                        onChange={(e) => updateItemField(idx, "diet_type", e.target.value)}
                      >
                        {DIET_TYPE_OPTIONS.map((choice) => (
                          <option key={choice.value} value={choice.value}>
                            {choice.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        value={item.facility_override || ""}
                        onChange={(e) => updateItemField(idx, "facility_override", e.target.value)}
                        placeholder="共通(base) / FACxxxx / TAG:xxx"
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

      <section className="panel">
        <header className="panel-header">
          <h2>日付別献立</h2>
        </header>
        <p className="subtle">件数: {entries.length}</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>日付</th>
                <th>時間帯</th>
                <th>メニュー名</th>
                <th>区分</th>
                <th>食種</th>
                <th>適用先</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={6}>日付別の献立がありません。</td>
                </tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.id}>
                    <td>{entry.menu_date || "-"}</td>
                    <td>{entry.daypart || "-"}</td>
                    <td>{entry.name}</td>
                    <td>{entry.category || "-"}</td>
                    <td>{formatDietTypeLabel(entry.diet_type)}</td>
                    <td>{formatScopeLabel(entry.facility_override)}</td>
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

        .upload-history {
          margin-top: 18px;
        }

        .history-header {
          margin-bottom: 12px;
        }

        .history-header h3 {
          margin: 0 0 4px;
          font-size: 15px;
        }

        .history-note {
          color: #667570;
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
