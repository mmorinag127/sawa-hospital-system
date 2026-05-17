import { useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

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

export default function ShippingPage() {
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [trackingText, setTrackingText] = useState<string>("");
  const [pdfMessage, setPdfMessage] = useState<string>("");
  const [excelMessage, setExcelMessage] = useState<string>("");
  const [trackingMessage, setTrackingMessage] = useState<string>("");
  const [pdfLoading, setPdfLoading] = useState<boolean>(false);
  const [excelLoading, setExcelLoading] = useState<boolean>(false);
  const [trackingLoading, setTrackingLoading] = useState<boolean>(false);
  const [summary, setSummary] = useState<{
    totalRows: number;
    lookupCount: number;
    deliveredRows: number;
    pendingRows: number;
    updatedArrivalRows: number;
    errorRows: number;
    allDelivered: boolean;
  } | null>(null);
  const [trackingSummary, setTrackingSummary] = useState<{
    total: number;
    delivered: number;
    pending: number;
    allDelivered: boolean;
  } | null>(null);
  const [trackingItems, setTrackingItems] = useState<
    {
      tracking_key?: string;
      tracking_number: string;
      status: string;
      delivered: boolean;
      arrival_text: string | null;
      error?: string | null;
    }[]
  >([]);
  const visibleTrackingItems = trackingItems.filter((item) => item.status !== "発送しなかった");
  const notShippedTrackingItems = trackingItems.filter((item) => item.status === "発送しなかった");

  const handleUpload = async () => {
    if (!pdfFile) {
      setPdfMessage("送り状PDFを選択してください。");
      return;
    }
    setPdfLoading(true);
    setPdfMessage("解析中です...");
    const formData = new FormData();
    formData.append("file", pdfFile);
    try {
      const res = await apiClient.post("/shipping/parse", formData, {
        headers: { "Content-Type": "multipart/form-data" },
        responseType: "blob",
      });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "伝票番号管理表.xlsx";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setPdfMessage("Excelをダウンロードしました。");
      setPdfFile(null);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setPdfMessage(detail ? `解析に失敗しました: ${detail}` : "解析に失敗しました。");
    } finally {
      setPdfLoading(false);
    }
  };

  const readHeader = (headers: any, key: string): string => {
    if (!headers) return "";
    return String(headers[key] ?? headers[key.toLowerCase()] ?? headers[key.toUpperCase()] ?? "");
  };

  const parseTrackingNumbers = (raw: string): string[] => {
    if (!raw.trim()) return [];
    const parts = raw
      .split(/[\s,、，]+/)
      .map((value) => value.trim())
      .filter(Boolean);
    const seen = new Set<string>();
    const ordered: string[] = [];
    for (const value of parts) {
      const key = value.replace(/\s/g, "");
      if (seen.has(key)) continue;
      seen.add(key);
      ordered.push(value);
    }
    return ordered;
  };

  const trackStatuses = async () => {
    const numbers = parseTrackingNumbers(trackingText);
    if (numbers.length === 0) {
      setTrackingMessage("伝票番号を入力してください。");
      setTrackingSummary(null);
      setTrackingItems([]);
      return;
    }
    setTrackingLoading(true);
    setTrackingMessage("追跡状況を取得中です...");
    setTrackingSummary(null);
    setTrackingItems([]);
    try {
      const res = await apiClient.post("/shipping/track-status", {
        tracking_numbers: numbers,
      });
      const items = Array.isArray(res.data?.items) ? res.data.items : [];
      const summaryData = res.data?.summary ?? null;
      setTrackingItems(items);
      setTrackingSummary(
        summaryData
          ? {
              total: Number(summaryData.total || items.length || 0),
              delivered: Number(summaryData.delivered || 0),
              pending: Number(summaryData.pending || 0),
              allDelivered: Boolean(summaryData.all_delivered),
            }
          : null
      );
      setTrackingMessage("追跡状況を取得しました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setTrackingMessage(detail ? `取得に失敗しました: ${detail}` : "取得に失敗しました。");
    } finally {
      setTrackingLoading(false);
    }
  };

  const enrichExcel = async () => {
    if (!excelFile) {
      setExcelMessage("伝票管理Excelを選択してください。");
      return;
    }
    setExcelLoading(true);
    setExcelMessage("到着日時を更新中です...");
    setSummary(null);
    const formData = new FormData();
    formData.append("file", excelFile);
    try {
      const res = await apiClient.post("/shipping/enrich-excel", formData, {
        headers: { "Content-Type": "multipart/form-data" },
        responseType: "blob",
      });
      const contentDisposition = res.headers?.["content-disposition"] || res.headers?.["Content-Disposition"];
      const filename = extractFilename(contentDisposition) || "伝票番号管理表_到着更新.xlsx";
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);

      const totalRows = Number(readHeader(res.headers, "x-shipping-total-rows") || 0);
      const lookupCount = Number(readHeader(res.headers, "x-shipping-lookup-count") || 0);
      const deliveredRows = Number(readHeader(res.headers, "x-shipping-delivered-rows") || 0);
      const pendingRows = Number(readHeader(res.headers, "x-shipping-pending-rows") || 0);
      const updatedArrivalRows = Number(readHeader(res.headers, "x-shipping-updated-arrival-rows") || 0);
      const errorRows = Number(readHeader(res.headers, "x-shipping-error-rows") || 0);
      const allDelivered = readHeader(res.headers, "x-shipping-all-delivered") === "1";
      setSummary({
        totalRows,
        lookupCount,
        deliveredRows,
        pendingRows,
        updatedArrivalRows,
        errorRows,
        allDelivered,
      });
      setExcelMessage("到着日時更新済みのExcelをダウンロードしました。");
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setExcelMessage(detail ? `更新に失敗しました: ${detail}` : "更新に失敗しました。");
    } finally {
      setExcelLoading(false);
    }
  };

  const markTrackingStatus = async (trackingNumber: string, status: "発送済み" | "発送しなかった") => {
    const label = status === "発送済み" ? "発送完了" : "発送しなかった";
    const ok = window.confirm(`${trackingNumber} を「${label}」として確定します。よろしいですか？`);
    if (!ok) return;
    setTrackingLoading(true);
    setTrackingMessage(`${label}を確定中です...`);
    try {
      const res = await apiClient.post("/shipping/status/manual", {
        tracking_number: trackingNumber,
        status,
      });
      const updated = res.data?.item;
      setTrackingItems((items) =>
        items.map((item) => {
          const key = item.tracking_key || item.tracking_number;
          const updatedKey = updated?.tracking_key || updated?.tracking_number;
          if (key !== updatedKey && item.tracking_number !== trackingNumber) return item;
          return {
            ...item,
            tracking_key: updated?.tracking_key || item.tracking_key,
            tracking_number: updated?.tracking_number || item.tracking_number,
            status: updated?.status || status,
            delivered: Boolean(updated?.delivered),
            arrival_text: updated?.arrival_text ?? item.arrival_text,
            error: updated?.error ?? null,
          };
        }),
      );
      setTrackingMessage(`${label}として確定しました。`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setTrackingMessage(detail ? `確定に失敗しました: ${detail}` : "確定に失敗しました。");
    } finally {
      setTrackingLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Shipping</p>
          <h1>佐川追跡・伝票管理</h1>
          <p className="subtle">PDF解析と伝票管理Excelの到着日時自動更新に対応します。</p>
          <p className="subtle">
            <Link href="/shipping-history" className="inline-link">
              これまでの追跡履歴を確認する
            </Link>
          </p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>PDF解析</h2>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">送り状PDF</span>
            <input
              className="input"
              type="file"
              accept="application/pdf"
              onChange={(e) => setPdfFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button className="btn primary" onClick={handleUpload} disabled={pdfLoading}>
            {pdfLoading ? "解析中..." : "解析してExcelをダウンロード"}
          </button>
        </div>
        {pdfFile ? <p className="file-name">選択中: {pdfFile.name}</p> : null}
        {pdfMessage ? <p className="message">{pdfMessage}</p> : null}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>伝票管理Excelの到着日時更新</h2>
        </header>
        <div className="filters">
          <label className="field">
            <span className="field-label">伝票管理Excel (.xlsx)</span>
            <input
              className="input"
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => setExcelFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button className="btn primary" onClick={enrichExcel} disabled={excelLoading}>
            {excelLoading ? "更新中..." : "到着日時を更新してExcelをダウンロード"}
          </button>
        </div>
        {excelFile ? <p className="file-name">選択中: {excelFile.name}</p> : null}
        {excelMessage ? <p className="message">{excelMessage}</p> : null}
        {summary ? (
          <div className="summary">
            <p>総対象行: {summary.totalRows}</p>
            <p>照会件数: {summary.lookupCount}</p>
            <p>配達完了: {summary.deliveredRows}</p>
            <p>未完了: {summary.pendingRows}</p>
            <p>到着日時更新行: {summary.updatedArrivalRows}</p>
            <p>照会失敗: {summary.errorRows}</p>
            <p>全件配達完了: {summary.allDelivered ? "はい" : "いいえ"}</p>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>伝票番号の追跡状況確認</h2>
        </header>
        <label className="field">
          <span className="field-label">伝票番号（改行・スペース・カンマ区切り）</span>
          <textarea
            className="input textarea"
            value={trackingText}
            onChange={(e) => setTrackingText(e.target.value)}
            rows={4}
            placeholder="4917-2168-9734"
          />
        </label>
        <div className="actions">
          <button className="btn primary" onClick={trackStatuses} disabled={trackingLoading}>
            {trackingLoading ? "取得中..." : "追跡状況を取得"}
          </button>
        </div>
        {trackingMessage ? <p className="message">{trackingMessage}</p> : null}
        {trackingSummary ? (
          <div className="summary">
            <p>総件数: {trackingSummary.total}</p>
            <p>配達完了: {trackingSummary.delivered}</p>
            <p>未完了: {trackingSummary.pending}</p>
            <p>全件配達完了: {trackingSummary.allDelivered ? "はい" : "いいえ"}</p>
          </div>
        ) : null}
        {trackingItems.length > 0 ? (
          <div className="track-table-wrap">
            <table className="track-table">
              <thead>
                <tr>
                  <th>伝票番号</th>
                  <th>状態</th>
                  <th>到着日時</th>
                  <th>エラー</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleTrackingItems.map((item, idx) => (
                  <tr key={`${item.tracking_number}-${idx}`}>
                    <td>{item.tracking_number}</td>
                    <td>{item.status}</td>
                    <td>{item.arrival_text || "-"}</td>
                    <td>{item.error || "-"}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="mini-btn"
                          onClick={() => markTrackingStatus(item.tracking_number, "発送済み")}
                          disabled={trackingLoading || item.status === "発送済み"}
                        >
                          発送完了
                        </button>
                        <button
                          type="button"
                          className="mini-btn muted"
                          onClick={() => markTrackingStatus(item.tracking_number, "発送しなかった")}
                          disabled={trackingLoading || item.status === "発送しなかった"}
                        >
                          発送しなかった
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {notShippedTrackingItems.length > 0 ? (
              <details className="not-shipped-tracking" data-testid="shipping-not-shipped-minimized">
                <summary>発送しなかった番号 {notShippedTrackingItems.length}件</summary>
                <table className="track-table minimized-table">
                  <thead>
                    <tr>
                      <th>伝票番号</th>
                      <th>状態</th>
                      <th>到着日時</th>
                      <th>エラー</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notShippedTrackingItems.map((item, idx) => (
                      <tr key={`${item.tracking_number}-not-shipped-${idx}`}>
                        <td>{item.tracking_number}</td>
                        <td>{item.status}</td>
                        <td>{item.arrival_text || "-"}</td>
                        <td>{item.error || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            ) : null}
          </div>
        ) : null}
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

        .inline-link {
          text-decoration: underline;
          text-underline-offset: 2px;
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

        .textarea {
          width: 100%;
          resize: vertical;
          min-height: 88px;
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

        .actions {
          margin-top: 10px;
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

        .summary {
          margin-top: 12px;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbf8ef;
          font-size: 13px;
          display: grid;
          gap: 4px;
        }

        .track-table-wrap {
          margin-top: 14px;
          overflow-x: auto;
        }

        .track-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
          background: #fbfbf9;
          border: 1px solid rgba(25, 32, 30, 0.08);
        }

        .track-table th,
        .track-table td {
          border-bottom: 1px solid rgba(25, 32, 30, 0.08);
          padding: 8px 10px;
          text-align: left;
          vertical-align: top;
          white-space: nowrap;
        }

        .track-table thead th {
          background: #eef4f2;
          font-weight: 700;
        }

        .not-shipped-tracking {
          margin-top: 12px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 10px;
          background: #faf8f2;
          padding: 10px 12px;
        }

        .not-shipped-tracking summary {
          cursor: pointer;
          font-size: 13px;
          font-weight: 800;
          color: #4d463c;
        }

        .minimized-table {
          margin-top: 10px;
        }

        .row-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .mini-btn {
          border: 1px solid rgba(25, 32, 30, 0.12);
          border-radius: 999px;
          padding: 5px 9px;
          background: #1f2a2a;
          color: #f7f2e7;
          font-size: 12px;
          font-weight: 700;
          cursor: pointer;
        }

        .mini-btn.muted {
          background: #eef3f1;
          color: #243330;
        }

        .mini-btn:disabled {
          opacity: 0.55;
          cursor: not-allowed;
        }

        .file-name {
          margin-top: 10px;
          font-size: 12px;
          color: #5f7b74;
        }
      `}</style>
      <style jsx global>{`
        @import url("https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Noto+Sans+JP:wght@400;600&display=swap");
      `}</style>
    </main>
  );
}
