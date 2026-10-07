import { Button, Input } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { sawaTheme } from "@sawa/ui";
import { useEffect, useState } from "react";
import Link from "next/link";

import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type TrainingSample = {
  id: string;
  order_id: string;
  facility_code?: string | null;
  week_code?: string | null;
  line_count?: number | null;
  has_corrections?: boolean;
  source?: string | null;
  note?: string | null;
  document_uri?: string | null;
  updated_at?: string | null;
};

const extractFilename = (value?: string | null) => {
  if (!value) return "";
  const match = value.match(/filename\\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
  const rawName = match?.[1] || match?.[2] || "";
  if (!rawName) return "";
  try {
    return decodeURIComponent(rawName);
  } catch {
    return rawName;
  }
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
};

const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
};

const headerValueToString = (value: unknown) => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => String(item)).join("; ");
  if (value == null) return "";
  return String(value);
};

export default function OcrTrainingDataPage() {
  const [items, setItems] = useState<TrainingSample[]>([]);
  const [limit, setLimit] = useState<number>(300);
  const [loading, setLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");

  const loadItems = async () => {
    setLoading(true);
    setMessage("学習データを取得中です...");
    try {
      const res = await apiClient.get("/ocr/training-samples", {
        params: { limit: Math.max(1, Math.min(limit, 1000)) },
      });
      const nextItems = Array.isArray(res.data?.items) ? res.data.items : [];
      setItems(nextItems);
      setMessage(`学習データを取得しました（${nextItems.length}件）。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setItems([]);
      setMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const exportMetadata = async (fileFormat: "jsonl" | "csv") => {
    setMessage(`学習データ${fileFormat.toUpperCase()}を作成中です...`);
    try {
      const res = await apiClient.get("/ocr/training-samples/export", {
        params: { file_format: fileFormat, limit: 1000000 },
        responseType: "blob",
      });
      const contentDisposition = headerValueToString(
        res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"],
      );
      const filename =
        extractFilename(contentDisposition) || `ocr_training_samples.${fileFormat}`;
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      downloadBlob(blob, filename);
      setMessage(`学習データ${fileFormat.toUpperCase()}をダウンロードしました。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `ダウンロードに失敗しました: ${detail}` : "ダウンロードに失敗しました。");
    }
  };

  const exportPdfs = async (clearAfterExport: boolean) => {
    if (clearAfterExport) {
      const ok = window.confirm(
        "PDF ZIPを作成後に学習データをクリアします。復元できません。実行してよいですか？",
      );
      if (!ok) return;
    }
    setMessage(clearAfterExport ? "PDF ZIP作成とクリアを実行中です..." : "PDF ZIPを作成中です...");
    try {
      const res = await apiClient.get("/ocr/training-samples/export-pdfs", {
        params: { limit: 1000000, clear_after_export: clearAfterExport ? "true" : "false" },
        responseType: "blob",
      });
      const headers = res.headers || {};
      const total = Number(headers["x-ocr-training-total-samples"] || 0);
      const exported = Number(headers["x-ocr-training-exported-pdfs"] || 0);
      const failed = Number(headers["x-ocr-training-failed-pdfs"] || 0);
      const removed = Number(headers["x-ocr-training-removed"] || 0);
      const clearSkipped = String(headers["x-ocr-training-clear-skipped"] || "0") === "1";

      const contentDisposition =
        headerValueToString(headers["content-disposition"] || headers["Content-Disposition"]);
      const filename = extractFilename(contentDisposition) || "ocr_training_pdfs.zip";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      downloadBlob(blob, filename);

      if (clearAfterExport) {
        if (clearSkipped) {
          setMessage(
            `PDF ZIPをダウンロードしました（対象${total}件 / 成功${exported}件 / 失敗${failed}件）。失敗があるためクリアは未実行です。`,
          );
        } else {
          setMessage(
            `PDF ZIPをダウンロードし、学習データをクリアしました（対象${total}件 / 成功${exported}件 / 削除${removed}件）。`,
          );
        }
      } else {
        setMessage(`PDF ZIPをダウンロードしました（対象${total}件 / 成功${exported}件 / 失敗${failed}件）。`);
      }
      await loadItems();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `PDF出力に失敗しました: ${detail}` : "PDF出力に失敗しました。");
    }
  };

  const clearAllSamples = async () => {
    const ok = window.confirm("学習データを全件削除します。復元できません。実行してよいですか？");
    if (!ok) return;
    setMessage("学習データを削除中です...");
    try {
      const res = await apiClient.delete("/ocr/training-samples");
      const removed = Number(res.data?.removed || 0);
      setMessage(`学習データを削除しました（${removed}件）。`);
      await loadItems();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setMessage(detail ? `削除に失敗しました: ${detail}` : "削除に失敗しました。");
    }
  };

  return (
    <ThemeProvider theme={sawaTheme}>
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">OCR Training</p>
          <h1>OCR学習データ</h1>
          <p className="subtle">登録済みの学習データを確認し、PDF一括ダウンロードとクリアを実行できます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>操作</h2>
          <Link href="/ocr-results" className="ghost-link">
            OCR結果へ
          </Link>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">表示件数</span>
            <Input disableUnderline
              className="input ocr-training-data-ordinary-control"
              type="number"
              inputProps={{ min: 1, max: 1000 }}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value || 300))}
            />
          </label>
          <Button type="submit" className="btn primary ocr-training-data-ordinary-control" onClick={loadItems} disabled={loading}>
            {loading ? "更新中..." : "一覧を更新"}
          </Button>
          <Button type="submit" className="btn ghost ocr-training-data-ordinary-control" onClick={() => exportPdfs(false)} disabled={loading}>
            PDF一括ダウンロード
          </Button>
          <Button type="submit" className="btn danger ocr-training-data-ordinary-control" onClick={() => exportPdfs(true)} disabled={loading}>
            ダウンロードしてクリア
          </Button>
          <Button type="submit" className="btn ghost ocr-training-data-ordinary-control" onClick={() => exportMetadata("jsonl")} disabled={loading}>
            ラベルJSONL保存
          </Button>
          <Button type="submit" className="btn ghost ocr-training-data-ordinary-control" onClick={() => exportMetadata("csv")} disabled={loading}>
            ラベルCSV保存
          </Button>
          <Button type="submit" className="btn danger ocr-training-data-ordinary-control" onClick={clearAllSamples} disabled={loading}>
            全件クリア（管理者）
          </Button>
        </div>
        {message ? <p className="message">{message}</p> : null}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>登録一覧</h2>
          <span className="badge">合計 {items.length} 件</span>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>更新日時</th>
                <th>施設</th>
                <th>注文ID</th>
                <th>週</th>
                <th>行数</th>
                <th>修正あり</th>
                <th>登録元</th>
                <th>PDF URI</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={8}>データがありません。</td>
                </tr>
              ) : (
                items.map((item) => (
                  <tr key={item.id}>
                    <td>{formatTimestamp(item.updated_at)}</td>
                    <td>{item.facility_code || "-"}</td>
                    <td>
                      <Link href={`/orders/${item.order_id}`} className="link">
                        {item.order_id}
                      </Link>
                    </td>
                    <td>{item.week_code || "-"}</td>
                    <td>{item.line_count ?? "-"}</td>
                    <td>{item.has_corrections ? "あり" : "なし"}</td>
                    <td>{item.source || "-"}</td>
                    <td className="uri-cell" title={item.document_uri || ""}>
                      {item.document_uri || "-"}
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
          box-shadow: 0 14px 36px rgba(31, 42, 42, 0.08);
          margin-bottom: 20px;
        }

        .panel-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          margin-bottom: 14px;
        }

        .panel h2 {
          margin: 0;
          font-size: 18px;
        }

        .ghost-link {
          color: #51615c;
          font-size: 14px;
        }

        .filters {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          align-items: flex-end;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          min-width: 140px;
        }

        .field-label {
          font-size: 12px;
          color: #5f7b74;
        }

        .input,
        :global(.ocr-training-data-ordinary-control.input) {
          border: 1px solid rgba(25, 32, 30, 0.18);
          border-radius: 10px;
          padding: 10px 12px;
          font-size: 14px;
          background: #fff;
          color: inherit;
        }

        .btn,
        :global(.ocr-training-data-ordinary-control.btn) {
          border: 1px solid transparent;
          border-radius: 10px;
          padding: 10px 14px;
          font-size: 14px;
          cursor: pointer;
        }

        .btn.primary,
        :global(.ocr-training-data-ordinary-control.btn.primary) {
          background: #1f2a2a;
          color: #fff;
        }

        .btn.ghost,
        :global(.ocr-training-data-ordinary-control.btn.ghost) {
          background: #fff;
          border-color: rgba(25, 32, 30, 0.2);
          color: #1f2a2a;
        }

        .btn.danger,
        :global(.ocr-training-data-ordinary-control.btn.danger) {
          background: #fff2f0;
          border-color: rgba(199, 75, 58, 0.35);
          color: #9d2418;
        }

        .btn:disabled,
        :global(.ocr-training-data-ordinary-control.btn:disabled) {
          opacity: 0.55;
          cursor: not-allowed;
        }

        .message {
          margin: 14px 0 0;
          color: #35524d;
          font-size: 14px;
        }

        .badge {
          background: #eef4f2;
          color: #35524d;
          border-radius: 999px;
          padding: 4px 10px;
          font-size: 12px;
        }

        .table-wrap {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
          min-width: 980px;
        }

        th,
        td {
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          padding: 10px 8px;
          text-align: left;
          font-size: 13px;
          vertical-align: top;
        }

        th {
          color: #51615c;
          font-weight: 600;
        }

        .uri-cell {
          max-width: 380px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 12px;
        }

        .link {
          color: #245a9a;
        }

        :global(.ocr-training-data-ordinary-control.MuiButton-root) {
          min-width: 0;
          font-family: inherit;
          font-size: 14px;
          font-weight: normal;
          line-height: inherit;
          letter-spacing: inherit;
          text-transform: none;
        }

        :global(.ocr-training-data-ordinary-control.MuiInput-root) {
          display: inline-flex;
          line-height: inherit;
          letter-spacing: inherit;
        }

        :global(.ocr-training-data-ordinary-control .MuiInput-input) {
          border: 0;
          border-radius: 0;
          padding: 0;
          font: inherit;
          color: inherit;
          background: transparent;
        }

        @media (max-width: 720px) {
          .page {
            padding: 28px 14px 40px;
          }

          .panel {
            padding: 14px;
          }

          table {
            min-width: 760px;
          }
        }
      `}</style>
    </main>
    </ThemeProvider>
  );
}
