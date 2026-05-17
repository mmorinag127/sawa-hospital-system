import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";
import Link from "next/link";

import { apiClient } from "../../../services/apiClient";

type WorkflowV2 = {
  order_id: string;
  state: string;
  headline?: string | null;
  primary_action?: string | null;
  selected_ocr_result_id?: string | null;
  saved_sheet_id?: string | null;
  confirmed_snapshot_id?: string | null;
  facility_id?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  template_id?: string | null;
  bagging_result_id?: string | null;
  output_bundle_id?: string | null;
  updated_at?: string | null;
  blockers?: string[];
  warnings?: string[];
};

type OcrResult = {
  ocr_result_id: string;
  status?: string | null;
  source?: string | null;
  selected?: boolean;
  artifact_digest?: string | null;
  artifact_manifest?: Record<string, unknown> | null;
  overlay_url?: string | null;
  overlay_status?: string | null;
  overlay_message?: string | null;
  created_at?: string | null;
};

type SavedSheetPayload = {
  saved_sheet_id: string;
  source_ocr_result_id?: string | null;
  edited_at?: string | null;
  created_at?: string | null;
  edited_by?: string | null;
  sheet?: Record<string, unknown>;
};

type InspectionPayload = {
  source?: string;
  workflow?: WorkflowV2;
  ocr_results?: OcrResult[];
  saved_sheet?: SavedSheetPayload | null;
  artifact_lineage?: Record<string, unknown>;
  bagging_result?: Record<string, unknown> | null;
  output_bundle?: Record<string, unknown> | null;
};

const formatJson = (value: unknown) => JSON.stringify(value ?? null, null, 2);

const stateLabel = (state?: string | null) => {
  const normalized = String(state || "").trim();
  const labels: Record<string, string> = {
    uploaded: "Step1: PDF/施設/週次確認",
    context_confirmed: "Step1完了: OCR実行待ち",
    ocr_running: "Step1: OCR実行中",
    ocr_completed: "Step2: OCR選択待ち",
    ocr_selected: "Step3: 正解OCR選択済み",
    sheet_saved: "Step3完了: シート保存済み",
    bagging_ready: "Step4: 出力確認",
    bagging_confirmed: "Step4: 出力確認",
    output_review: "Step4: 出力確認",
    confirmed: "確定済み",
  };
  return labels[normalized] || normalized || "未開始";
};

const formatDateTime = (value?: string | null) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

const compactText = (value: unknown, fallback = "-") => {
  if (value == null || value === "") return fallback;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const keyValueRows = (value: Record<string, unknown> | null | undefined) =>
  Object.entries(value || {}).filter(([, item]) => item !== undefined);

const normalizeSheet = (sheet: Record<string, unknown> | undefined | null) => {
  const rows = Array.isArray(sheet?.rows)
    ? (sheet?.rows as unknown[])
        .filter((row): row is unknown[] => Array.isArray(row))
        .map((row) => row.map((cell) => String(cell ?? "")))
    : [];
  const width = Math.max(
    Array.isArray(sheet?.fields) ? (sheet?.fields as unknown[]).length : 0,
    Array.isArray(sheet?.header) ? (sheet?.header as unknown[]).length : 0,
    ...rows.map((row) => row.length),
    0,
  );
  const fields = Array.from({ length: width }, (_, idx) => String((sheet?.fields as unknown[] | undefined)?.[idx] ?? `col${idx + 1}`));
  const header = Array.from({ length: width }, (_, idx) => String((sheet?.header as unknown[] | undefined)?.[idx] ?? fields[idx] ?? `col${idx + 1}`));
  return { fields, header, rows: rows.map((row) => Array.from({ length: width }, (_, idx) => String(row[idx] ?? ""))) };
};

const readSummary = (payload: Record<string, unknown> | null | undefined) => {
  const summary = payload?.summary;
  return summary && typeof summary === "object" && !Array.isArray(summary)
    ? summary as Record<string, unknown>
    : {};
};

export default function OrderInspectionV2Page() {
  const router = useRouter();
  const orderId = typeof router.query.id === "string" ? router.query.id : "";
  const [inspection, setInspection] = useState<InspectionPayload | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const selectedOcr = useMemo(
    () => (inspection?.ocr_results || []).find((item) => item.selected || item.ocr_result_id === inspection?.workflow?.selected_ocr_result_id) || null,
    [inspection],
  );
  const sheet = useMemo(() => normalizeSheet(inspection?.saved_sheet?.sheet), [inspection?.saved_sheet?.sheet]);
  const baggingSummary = useMemo(() => readSummary(inspection?.bagging_result), [inspection?.bagging_result]);
  const outputEntries = useMemo(() => keyValueRows(inspection?.output_bundle || null), [inspection?.output_bundle]);

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    let revokedUrl = "";
    setLoading(true);
    setError("");
    Promise.all([
      apiClient.get<InspectionPayload>(`/orders/${orderId}/workflow-v2/inspection`),
      apiClient.get<Blob>(`/orders/${orderId}/document`, { responseType: "blob" }),
    ])
      .then(([inspectionRes, documentRes]) => {
        setInspection(inspectionRes.data);
        const nextUrl = URL.createObjectURL(documentRes.data);
        revokedUrl = nextUrl;
        setPdfUrl(nextUrl);
      })
      .catch((err) => {
        setError(String(err?.response?.data?.detail || err?.message || "inspection の取得に失敗しました"));
      })
      .finally(() => setLoading(false));
    return () => {
      if (revokedUrl) URL.revokeObjectURL(revokedUrl);
    };
  }, [router.isReady, orderId]);

  return (
    <main className="page inspection-page">
      <header className="hero">
        <div>
          <p className="eyebrow">Read Only Inspection</p>
          <h1>注文確認専用</h1>
          <p className="subtle">処理を進めずに、原本PDF、正解OCR、保存シート、袋分け、出力状態を確認する画面です。</p>
        </div>
        <div className="hero-actions">
          {orderId ? (
            <Link className="btn primary" href={`/orders/${orderId}/workflow-v2`}>
              処理画面へ戻る
            </Link>
          ) : null}
          <Link className="ghost-link" href="/orders">
            注文一覧へ戻る
          </Link>
        </div>
      </header>

      {error ? <div className="notice error">{error}</div> : null}

      <section className="panel state-panel">
        <div>
          <p className="eyebrow">Current State</p>
          <h2>{stateLabel(inspection?.workflow?.state)}</h2>
          <p className="subtle">{inspection?.workflow?.headline || (loading ? "読み込み中です。" : "workflow-v2 状態は未取得です。")}</p>
        </div>
        <div className="state-cards">
          <div className="metric-card">
            <span>注文ID</span>
            <strong>{inspection?.workflow?.order_id || orderId || "-"}</strong>
          </div>
          <div className="metric-card">
            <span>施設</span>
            <strong>{inspection?.workflow?.facility_id || "-"}</strong>
          </div>
          <div className="metric-card">
            <span>週次</span>
            <strong>{inspection?.workflow?.week_start || "-"} ~ {inspection?.workflow?.week_end || "-"}</strong>
          </div>
          <div className="metric-card">
            <span>テンプレート</span>
            <strong>{inspection?.workflow?.template_id || "-"}</strong>
          </div>
          <div className="metric-card">
            <span>正解OCR</span>
            <strong>{inspection?.workflow?.selected_ocr_result_id || "-"}</strong>
          </div>
          <div className="metric-card">
            <span>保存シート</span>
            <strong>{inspection?.workflow?.saved_sheet_id || "-"}</strong>
          </div>
        </div>
      </section>

      <section className="inspection-grid">
        <section className="panel pdf-panel">
          <p className="step-tag">Original PDF</p>
          <h2>原本PDF</h2>
          {pdfUrl ? <iframe title="original pdf" src={pdfUrl} /> : <p className="subtle">PDFを読み込み中です。</p>}
        </section>

        <section className="panel">
          <div className="panel-title-row">
            <div>
              <p className="step-tag">Canonical OCR</p>
              <h2>選択された正解OCR</h2>
            </div>
            {selectedOcr?.overlay_url ? (
              <a className="ghost-link" href={selectedOcr.overlay_url} target="_blank" rel="noreferrer">
                overlayを別タブで開く
              </a>
            ) : null}
          </div>
          {selectedOcr ? (
            <div className="ocr-review">
              <div className="ocr-meta-grid">
                <div><span>ID</span><strong>{selectedOcr.ocr_result_id}</strong></div>
                <div><span>状態</span><strong>{selectedOcr.status || "-"}</strong></div>
                <div><span>生成元</span><strong>{selectedOcr.source || "-"}</strong></div>
                <div><span>生成日時</span><strong>{formatDateTime(selectedOcr.created_at)}</strong></div>
              </div>
              {selectedOcr.overlay_url ? (
                <img className="selected-overlay" src={selectedOcr.overlay_url} alt="selected OCR overlay" />
              ) : (
                <div className="preview-placeholder">{selectedOcr.overlay_message || "overlay成果物がありません。"}</div>
              )}
            </div>
          ) : (
            <p className="subtle">正解OCRはまだ選択されていません。</p>
          )}
        </section>
      </section>

      <section className="panel">
        <div className="panel-title-row">
          <div>
            <p className="step-tag">OCR Candidates</p>
            <h2>OCR候補</h2>
          </div>
          <span className="pill">{inspection?.ocr_results?.length || 0}件</span>
        </div>
        {inspection?.ocr_results?.length ? (
          <div className="candidate-grid">
            {inspection.ocr_results.map((item) => (
              <article key={item.ocr_result_id} className={`candidate-card ${item.selected ? "selected" : ""}`}>
                <div className="candidate-header">
                  <strong>{item.selected ? "正解" : "候補"}</strong>
                  <span>{formatDateTime(item.created_at)}</span>
                </div>
                <p className="candidate-id">{item.ocr_result_id}</p>
                <p className="subtle">{item.status || "unknown"} / {item.source || "-"}</p>
                {item.overlay_url ? (
                  <a className="ghost-link small-link" href={item.overlay_url} target="_blank" rel="noreferrer">overlayを開く</a>
                ) : (
                  <p className="subtle">{item.overlay_message || "overlayなし"}</p>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="subtle">OCR候補はまだありません。</p>
        )}
      </section>

      <section className="panel">
        <div className="panel-title-row">
          <div>
            <p className="step-tag">Saved Sheet</p>
            <h2>保存済みシート</h2>
          </div>
          <div className="sheet-stamps">
            <span>保存: {formatDateTime(inspection?.saved_sheet?.edited_at || inspection?.saved_sheet?.created_at)}</span>
            <span>元OCR: {inspection?.saved_sheet?.source_ocr_result_id || "-"}</span>
          </div>
        </div>
        {sheet.rows.length ? (
          <div className="sheet-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  {sheet.header.map((label, idx) => <th key={`${label}-${idx}`}>{label}</th>)}
                </tr>
              </thead>
              <tbody>
                {sheet.rows.map((row, rowIdx) => (
                  <tr key={`sheet-row-${rowIdx}`}>
                    <th>{rowIdx + 1}</th>
                    {sheet.fields.map((field, colIdx) => <td key={`${field}-${colIdx}`}>{row[colIdx] || ""}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="subtle">保存済みシートはまだありません。</p>
        )}
      </section>

      <section className="inspection-grid">
        <section className="panel">
          <p className="step-tag">Bagging</p>
          <h2>袋分け結果</h2>
          {inspection?.bagging_result ? (
            <>
              <div className="state-cards compact">
                {keyValueRows(baggingSummary).map(([key, value]) => (
                  <div key={key} className="metric-card">
                    <span>{key}</span>
                    <strong>{compactText(value)}</strong>
                  </div>
                ))}
              </div>
              {Array.isArray((inspection.bagging_result as any).quantity_cells) ? (
                <div className="sheet-table-wrap short-table">
                  <table>
                    <thead>
                      <tr>
                        <th>日付</th>
                        <th>区分</th>
                        <th>メニュー</th>
                        <th>食種</th>
                        <th>数量</th>
                      </tr>
                    </thead>
                    <tbody>
                      {((inspection.bagging_result as any).quantity_cells || []).slice(0, 80).map((item: any, idx: number) => (
                        <tr key={`bagging-cell-${idx}`}>
                          <td>{item.date || "-"}</td>
                          <td>{item.daypart || "-"}</td>
                          <td>{item.menu_name || "-"}</td>
                          <td>{item.diet_type || item.area_id || "-"}</td>
                          <td>{item.quantity ?? "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : (
            <p className="subtle">袋分け結果はまだありません。</p>
          )}
        </section>

        <section className="panel">
          <p className="step-tag">Output</p>
          <h2>出力状態</h2>
          {outputEntries.length ? (
            <div className="kv-table">
              {outputEntries.map(([key, value]) => (
                <div key={key}>
                  <span>{key}</span>
                  <strong>{compactText(value)}</strong>
                </div>
              ))}
            </div>
          ) : (
            <p className="subtle">出力確認はまだ作成されていません。</p>
          )}
        </section>
      </section>

      <section className="panel">
        <p className="step-tag">Lineage</p>
        <h2>処理のつながり</h2>
        <div className="lineage-table">
          {keyValueRows(inspection?.artifact_lineage).map(([key, value]) => (
            <div key={key}>
              <span>{key}</span>
              <strong>{compactText(value)}</strong>
            </div>
          ))}
        </div>
        <details className="json-details">
          <summary>デバッグ用 raw JSON を開く</summary>
          <pre>{formatJson(inspection)}</pre>
        </details>
      </section>

      <style jsx>{`
        .inspection-page {
          background:
            radial-gradient(circle at top left, rgba(45, 84, 70, 0.14), transparent 32%),
            linear-gradient(180deg, #fbf8ef 0%, #efe9db 100%);
          min-height: 100vh;
        }
        .hero {
          align-items: flex-start;
          display: flex;
          gap: 24px;
          justify-content: space-between;
          padding: 32px 36px 18px;
        }
        .hero-actions,
        .panel-title-row {
          align-items: center;
          display: flex;
          gap: 12px;
          justify-content: space-between;
        }
        .eyebrow,
        .step-tag {
          color: #7a6440;
          font-size: 12px;
          font-weight: 800;
          letter-spacing: 0.08em;
          margin: 0 0 8px;
          text-transform: uppercase;
        }
        h1,
        h2 {
          color: #1d2822;
          margin: 0;
        }
        .subtle {
          color: #687269;
          margin: 8px 0 0;
        }
        .panel {
          background: rgba(255, 255, 255, 0.9);
          border: 1px solid rgba(60, 82, 68, 0.15);
          border-radius: 18px;
          box-shadow: 0 14px 36px rgba(28, 40, 34, 0.08);
          margin: 16px 36px;
          padding: 22px;
        }
        .state-panel {
          display: grid;
          gap: 18px;
          grid-template-columns: minmax(260px, 0.6fr) minmax(0, 1.4fr);
        }
        .state-cards,
        .candidate-grid,
        .ocr-meta-grid {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        }
        .state-cards.compact {
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        }
        .metric-card,
        .candidate-card,
        .ocr-meta-grid > div {
          background: #f8fbfa;
          border: 1px solid rgba(60, 82, 68, 0.12);
          border-radius: 14px;
          padding: 12px;
        }
        .metric-card span,
        .ocr-meta-grid span,
        .lineage-table span,
        .kv-table span {
          color: #5f7b74;
          display: block;
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .metric-card strong,
        .ocr-meta-grid strong,
        .lineage-table strong,
        .kv-table strong {
          color: #203128;
          display: block;
          font-size: 13px;
          margin-top: 5px;
          overflow-wrap: anywhere;
        }
        .btn,
        .ghost-link {
          align-items: center;
          background: #f0eadc;
          border-radius: 999px;
          color: #1c2822;
          display: inline-flex;
          font-weight: 800;
          justify-content: center;
          min-height: 40px;
          padding: 0 16px;
          text-decoration: none;
        }
        .btn.primary {
          background: #1d2822;
          color: #fffdf7;
        }
        .small-link {
          font-size: 12px;
          min-height: 32px;
          padding: 0 12px;
        }
        .notice {
          border-radius: 14px;
          font-weight: 700;
          margin: 12px 36px;
          padding: 12px 16px;
        }
        .notice.error {
          background: #f8dfd8;
          color: #8a2c18;
        }
        .inspection-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
        }
        .pdf-panel {
          grid-row: span 1;
        }
        iframe {
          background: #fff;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          height: 900px;
          margin-top: 16px;
          width: 100%;
        }
        .selected-overlay {
          background: #fff;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          display: block;
          margin-top: 14px;
          max-height: 900px;
          object-fit: contain;
          width: 100%;
        }
        .preview-placeholder {
          align-items: center;
          background: #f6f3eb;
          border: 1px dashed #cfc6b1;
          border-radius: 14px;
          color: #687269;
          display: flex;
          font-weight: 800;
          min-height: 220px;
          justify-content: center;
          margin-top: 12px;
          padding: 16px;
        }
        .candidate-header,
        .sheet-stamps {
          align-items: center;
          color: #687269;
          display: flex;
          flex-wrap: wrap;
          font-size: 12px;
          gap: 8px;
          justify-content: space-between;
        }
        .candidate-card.selected {
          border-color: #2e7d5a;
          box-shadow: 0 0 0 2px rgba(46, 125, 90, 0.15);
        }
        .candidate-id {
          font-size: 12px;
          font-weight: 800;
          margin: 8px 0 0;
          overflow-wrap: anywhere;
        }
        .pill {
          background: #efe7d5;
          border-radius: 999px;
          color: #6d5734;
          font-weight: 900;
          padding: 8px 12px;
        }
        .sheet-table-wrap {
          border: 1px solid #d7d1c0;
          border-radius: 14px;
          margin-top: 16px;
          max-height: 720px;
          overflow: auto;
        }
        .short-table {
          max-height: 420px;
        }
        table {
          border-collapse: separate;
          border-spacing: 0;
          font-size: 12px;
          min-width: 100%;
          width: max-content;
        }
        th,
        td {
          border-bottom: 1px solid #e5dece;
          border-right: 1px solid #e5dece;
          max-width: 280px;
          padding: 7px 9px;
          vertical-align: top;
          white-space: nowrap;
        }
        th {
          background: #f4eddd;
          color: #405045;
          position: sticky;
          top: 0;
          z-index: 1;
        }
        .lineage-table,
        .kv-table {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          margin-top: 12px;
        }
        .lineage-table > div,
        .kv-table > div {
          background: #f8fbfa;
          border: 1px solid rgba(60, 82, 68, 0.12);
          border-radius: 12px;
          padding: 12px;
        }
        .json-details {
          margin-top: 16px;
        }
        .json-details summary {
          color: #687269;
          cursor: pointer;
          font-weight: 800;
          margin-bottom: 10px;
        }
        pre {
          background: #162019;
          border-radius: 14px;
          color: #e9f1e7;
          font-size: 12px;
          max-height: 520px;
          overflow: auto;
          padding: 14px;
        }
        @media (max-width: 1100px) {
          .hero,
          .panel-title-row,
          .state-panel {
            display: block;
          }
          .inspection-grid {
            grid-template-columns: 1fr;
          }
          .panel,
          .notice {
            margin-left: 16px;
            margin-right: 16px;
          }
        }
      `}</style>
    </main>
  );
}
