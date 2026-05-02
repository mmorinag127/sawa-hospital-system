import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";
import Link from "next/link";

import { apiClient } from "../../../services/apiClient";
import TopNav from "../../../components/TopNav";

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
};

type OcrResult = {
  ocr_result_id: string;
  status?: string | null;
  source?: string | null;
  selected?: boolean;
  artifact_digest?: string | null;
  artifact_manifest?: Record<string, unknown> | null;
  created_at?: string | null;
};

type InspectionPayload = {
  workflow?: WorkflowV2;
  ocr_results?: OcrResult[];
  saved_sheet?: {
    saved_sheet_id: string;
    source_ocr_result_id?: string | null;
    sheet?: Record<string, unknown>;
  } | null;
  artifact_lineage?: Record<string, unknown>;
  bagging_result?: Record<string, unknown> | null;
  output_bundle?: Record<string, unknown> | null;
};

type SheetPayload = {
  fields: string[];
  header: string[];
  rows: string[][];
  row_ids?: string[];
  [key: string]: unknown;
};

const emptyContext = {
  facility_id: "",
  week_start: "",
  week_end: "",
  template_id: "",
};

const defaultSheet = {
  rows: [
    {
      date: "",
      daypart: "",
      menu_name: "",
    },
  ],
};

const formatJson = (value: unknown) => JSON.stringify(value ?? null, null, 2);

const normalizeSheetPayload = (value: unknown): SheetPayload | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const rows = Array.isArray(raw.rows)
    ? raw.rows
        .filter((row): row is unknown[] => Array.isArray(row))
        .map((row) => row.map((cell) => String(cell ?? "")))
    : [];
  const width = Math.max(
    Array.isArray(raw.fields) ? raw.fields.length : 0,
    Array.isArray(raw.header) ? raw.header.length : 0,
    ...rows.map((row) => row.length),
    1,
  );
  const fields = Array.from({ length: width }, (_, idx) => {
    const valueAtIndex = Array.isArray(raw.fields) ? raw.fields[idx] : undefined;
    return String(valueAtIndex ?? `col${idx + 1}`);
  });
  const header = Array.from({ length: width }, (_, idx) => {
    const valueAtIndex = Array.isArray(raw.header) ? raw.header[idx] : undefined;
    return String(valueAtIndex ?? fields[idx] ?? `col${idx + 1}`);
  });
  return {
    ...raw,
    fields,
    header,
    rows: rows.map((row) => Array.from({ length: width }, (_, idx) => String(row[idx] ?? ""))),
  };
};

const stateLabel = (state?: string | null) => {
  const normalized = String(state || "").trim();
  const labels: Record<string, string> = {
    uploaded: "Step1: PDF/施設/週次確認",
    context_confirmed: "Step1完了: OCR実行待ち",
    ocr_running: "Step1: OCR実行中",
    ocr_selected: "Step2完了: 正解OCR選択済み",
    sheet_saved: "Step3完了: シート保存済み",
    bagging_ready: "Step4: 袋分け確認",
    bagging_confirmed: "Step4完了: 出力確認待ち",
    output_review: "Step5: 出力確認",
    confirmed: "確定済み",
  };
  return labels[normalized] || normalized || "未開始";
};

export default function OrderWorkflowV2Page() {
  const router = useRouter();
  const orderId = typeof router.query.id === "string" ? router.query.id : "";
  const [workflow, setWorkflow] = useState<WorkflowV2 | null>(null);
  const [ocrResults, setOcrResults] = useState<OcrResult[]>([]);
  const [inspection, setInspection] = useState<InspectionPayload | null>(null);
  const [contextForm, setContextForm] = useState(emptyContext);
  const [sheetJson, setSheetJson] = useState(formatJson(defaultSheet));
  const [sheetPayload, setSheetPayload] = useState<SheetPayload | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");

  const selectedOcr = useMemo(
    () => ocrResults.find((item) => item.selected || item.ocr_result_id === workflow?.selected_ocr_result_id) || null,
    [ocrResults, workflow?.selected_ocr_result_id],
  );

  const refreshAll = async () => {
    if (!orderId) return;
    const [workflowRes, ocrRes, inspectionRes] = await Promise.all([
      apiClient.get<WorkflowV2>(`/orders/${orderId}/workflow-v2`),
      apiClient.get<{ results: OcrResult[] }>(`/orders/${orderId}/workflow-v2/ocr-results`),
      apiClient.get<InspectionPayload>(`/orders/${orderId}/workflow-v2/inspection`),
    ]);
    setWorkflow(workflowRes.data);
    setOcrResults(Array.isArray(ocrRes.data.results) ? ocrRes.data.results : []);
    setInspection(inspectionRes.data);
    const savedSheet = inspectionRes.data.saved_sheet?.sheet;
    if (savedSheet) {
      const normalizedSavedSheet = normalizeSheetPayload(savedSheet);
      setSheetPayload(normalizedSavedSheet);
      setSheetJson(formatJson(normalizedSavedSheet || savedSheet));
    }
    setContextForm({
      facility_id: workflowRes.data.facility_id || "",
      week_start: workflowRes.data.week_start || "",
      week_end: workflowRes.data.week_end || "",
      template_id: workflowRes.data.template_id || "",
    });
  };

  useEffect(() => {
    if (!router.isReady || !orderId) return;
    refreshAll().catch((err) => {
      setError(String(err?.response?.data?.detail || err?.message || "workflow-v2 の取得に失敗しました"));
    });
  }, [router.isReady, orderId]);

  const runAction = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      await action();
      await refreshAll();
      setMessage(`${label} が完了しました`);
    } catch (err: any) {
      setError(String(err?.response?.data?.detail || err?.message || `${label} に失敗しました`));
    } finally {
      setBusy("");
    }
  };

  const confirmContext = () =>
    runAction("Step1 context confirm", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/context`, contextForm);
    });

  const runOcr = () =>
    runAction("Step1 OCR run", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-runs`, {
        stale_action: "retry",
      });
    });

  const selectOcr = (ocrResultId: string) =>
    runAction("Step2 OCR select", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/ocr-results/${ocrResultId}/select`);
    });

  const deleteOcr = (ocrResultId: string) =>
    runAction("OCR result delete", async () => {
      await apiClient.delete(`/orders/${orderId}/workflow-v2/ocr-results/${ocrResultId}`);
    });

  const generateSheetFromSelectedOcr = () =>
    runAction("Step3 sheet source", async () => {
      const response = await apiClient.get<{ sheet?: Record<string, unknown> }>(`/orders/${orderId}/workflow-v2/sheet-source`);
      const normalized = normalizeSheetPayload(response.data.sheet);
      if (!normalized) {
        throw new Error("選択OCRからシートを生成できませんでした");
      }
      setSheetPayload(normalized);
      setSheetJson(formatJson(normalized));
    });

  const updateSheetCell = (rowIndex: number, colIndex: number, value: string) => {
    setSheetPayload((current) => {
      if (!current) return current;
      const rows = current.rows.map((row, idx) => (
        idx === rowIndex ? row.map((cell, cellIdx) => (cellIdx === colIndex ? value : cell)) : row
      ));
      const nextSheet = { ...current, rows };
      setSheetJson(formatJson(nextSheet));
      return nextSheet;
    });
  };

  const saveSheet = () =>
    runAction("Step3 sheet save", async () => {
      const parsed = sheetPayload || normalizeSheetPayload(JSON.parse(sheetJson));
      if (!parsed) {
        throw new Error("保存できるシートがありません");
      }
      await apiClient.put(`/orders/${orderId}/workflow-v2/sheet`, {
        sheet: parsed,
        edited_by: "operator",
      });
    });

  const runBagging = () =>
    runAction("Step4 bagging", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/bagging`);
    });

  const confirmBagging = () =>
    runAction("Step4 bagging confirm", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/bagging/confirm`);
    });

  const prepareOutputReview = () =>
    runAction("Step5 output review", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/outputs/review`);
    });

  const finalConfirm = () =>
    runAction("Step5 final confirm", async () => {
      await apiClient.post(`/orders/${orderId}/workflow-v2/confirm`, {
        confirmed_by: "operator",
      });
    });

  return (
    <main className="page workflow-v2-page">
      <header className="hero">
        <div>
          <p className="eyebrow">Workflow V2</p>
          <h1>注文処理 v2</h1>
          <p className="subtle">DB workflow state と artifact lineage だけで step を進めます。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel state-panel">
        <div>
          <p className="eyebrow">Current State</p>
          <h2>{stateLabel(workflow?.state)}</h2>
          <p className="subtle">{workflow?.headline || "workflow-v2 を読み込み中です。"}</p>
        </div>
        <div className="state-actions">
          <button className="btn ghost" type="button" onClick={() => void refreshAll()} disabled={Boolean(busy)}>
            再読込
          </button>
          {orderId ? (
            <>
              <Link className="ghost-link" href={`/orders/${orderId}/inspection-v2`}>
                確認専用ページ
              </Link>
              <Link className="ghost-link" href="/orders">
                注文一覧へ戻る
              </Link>
            </>
          ) : null}
        </div>
      </section>

      {message ? <div className="notice success">{message}</div> : null}
      {error ? <div className="notice error">{error}</div> : null}

      <section className="step-grid">
        <section className="panel">
          <p className="step-tag">Step1</p>
          <h2>PDF / 施設 / 週次 / テンプレート確定</h2>
          <div className="form-grid">
            <label>
              施設ID
              <input
                value={contextForm.facility_id}
                onChange={(event) => setContextForm((current) => ({ ...current, facility_id: event.target.value }))}
              />
            </label>
            <label>
              週開始
              <input
                placeholder="2026-04-26"
                value={contextForm.week_start}
                onChange={(event) => setContextForm((current) => ({ ...current, week_start: event.target.value }))}
              />
            </label>
            <label>
              週終了
              <input
                placeholder="2026-04-30"
                value={contextForm.week_end}
                onChange={(event) => setContextForm((current) => ({ ...current, week_end: event.target.value }))}
              />
            </label>
            <label>
              テンプレートID
              <input
                value={contextForm.template_id}
                onChange={(event) => setContextForm((current) => ({ ...current, template_id: event.target.value }))}
              />
            </label>
          </div>
          <button className="btn primary" type="button" onClick={confirmContext} disabled={Boolean(busy)}>
            Step1を確定
          </button>
          <button
            className="btn"
            type="button"
            onClick={runOcr}
            disabled={Boolean(busy || !workflow?.facility_id || !workflow?.week_start || !workflow?.template_id)}
          >
            OCRを実行
          </button>
        </section>

        <section className="panel">
          <p className="step-tag">Step2</p>
          <h2>正解 OCR を一つ選ぶ</h2>
          <p className="subtle">選択変更または削除時は、派生 sheet / bagging / output / confirmed snapshot を無効化します。</p>
          <div className="ocr-result-list">
            {ocrResults.length ? (
              ocrResults.map((item) => (
                <div key={item.ocr_result_id} className={`ocr-card ${item === selectedOcr ? "selected" : ""}`}>
                  <div>
                    <strong>{item.ocr_result_id}</strong>
                    <p className="subtle">
                      {item.status || "unknown"} / {item.source || "-"} / {item.created_at || "-"}
                    </p>
                    <p className="digest">{item.artifact_digest || ""}</p>
                  </div>
                  <div className="row-actions">
                    <button className="btn" type="button" onClick={() => selectOcr(item.ocr_result_id)} disabled={Boolean(busy)}>
                      正解にする
                    </button>
                    <button className="btn danger" type="button" onClick={() => deleteOcr(item.ocr_result_id)} disabled={Boolean(busy)}>
                      完全削除
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <p className="subtle">OCR result はまだありません。Step1 から OCR を実行してください。</p>
            )}
          </div>
        </section>

        <section className="panel">
          <p className="step-tag">Step3</p>
          <h2>選択 OCR からシート作成 / 編集 / 保存</h2>
          <div className="row-actions">
            <button
              className="btn"
              type="button"
              onClick={generateSheetFromSelectedOcr}
              disabled={Boolean(busy || !workflow?.selected_ocr_result_id)}
            >
              選択OCRからシート生成
            </button>
            <button className="btn primary" type="button" onClick={saveSheet} disabled={Boolean(busy || !workflow?.selected_ocr_result_id || !sheetPayload)}>
              シートを保存
            </button>
          </div>
          {sheetPayload ? (
            <div className="sheet-table-wrap">
              <table className="sheet-table">
                <thead>
                  <tr>
                    <th>#</th>
                    {sheetPayload.header.map((label, colIdx) => (
                      <th key={`${sheetPayload.fields[colIdx] || "col"}-${colIdx}`}>{label || sheetPayload.fields[colIdx]}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sheetPayload.rows.map((row, rowIdx) => (
                    <tr key={sheetPayload.row_ids?.[rowIdx] || `row-${rowIdx}`}>
                      <th>{rowIdx + 1}</th>
                      {sheetPayload.fields.map((field, colIdx) => (
                        <td key={`${field}-${colIdx}`}>
                          <input
                            value={row[colIdx] || ""}
                            onChange={(event) => updateSheetCell(rowIdx, colIdx, event.target.value)}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="subtle">Step2で正解OCRを選択してから、選択OCRだけを使ってシートを生成してください。</p>
          )}
          <details className="json-details">
            <summary>保存予定JSONを確認</summary>
            <textarea
              value={sheetJson}
              onChange={(event) => {
                const nextJson = event.target.value;
                setSheetJson(nextJson);
                try {
                  setSheetPayload(normalizeSheetPayload(JSON.parse(nextJson)));
                } catch {
                  setSheetPayload(null);
                }
              }}
              spellCheck={false}
            />
          </details>
        </section>

        <section className="panel">
          <p className="step-tag">Step4</p>
          <h2>保存済みシートから袋分け</h2>
          <p className="subtle">この step は saved_sheet_id だけを入力にします。</p>
          <div className="row-actions">
            <button className="btn primary" type="button" onClick={runBagging} disabled={Boolean(busy || !workflow?.saved_sheet_id)}>
              袋分けを計算
            </button>
            <button className="btn" type="button" onClick={confirmBagging} disabled={Boolean(busy || !workflow?.bagging_result_id)}>
              袋分けを確認
            </button>
          </div>
          <pre>{formatJson(inspection?.bagging_result || null)}</pre>
        </section>

        <section className="panel">
          <p className="step-tag">Step5</p>
          <h2>出力確認 / 確定</h2>
          <div className="row-actions">
            <button className="btn" type="button" onClick={prepareOutputReview} disabled={Boolean(busy || !workflow?.bagging_result_id)}>
              出力確認を作成
            </button>
            <button className="btn primary" type="button" onClick={finalConfirm} disabled={Boolean(busy || !workflow?.output_bundle_id)}>
              確定
            </button>
          </div>
          <pre>{formatJson(inspection?.output_bundle || null)}</pre>
        </section>
      </section>

      <section className="panel">
        <p className="step-tag">Read Only Inspection</p>
        <h2>状態と lineage</h2>
        <pre>{formatJson(inspection || workflow)}</pre>
      </section>

      <style jsx>{`
        .workflow-v2-page {
          background:
            radial-gradient(circle at top left, rgba(62, 110, 89, 0.14), transparent 30%),
            linear-gradient(180deg, #faf8f1 0%, #f1efe6 100%);
          min-height: 100vh;
        }
        .hero {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 24px;
          padding: 32px 36px 18px;
        }
        .eyebrow {
          color: #6f6a5a;
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.08em;
          margin: 0 0 8px;
          text-transform: uppercase;
        }
        h1,
        h2 {
          color: #1c2822;
          margin: 0;
        }
        .subtle {
          color: #687269;
          margin: 8px 0 0;
        }
        .panel {
          background: rgba(255, 255, 255, 0.86);
          border: 1px solid rgba(54, 82, 68, 0.14);
          border-radius: 18px;
          box-shadow: 0 14px 36px rgba(28, 40, 34, 0.08);
          margin: 16px 36px;
          padding: 22px;
        }
        .state-panel {
          align-items: center;
          display: flex;
          justify-content: space-between;
        }
        .state-actions,
        .row-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .step-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .step-tag {
          color: #a15f2d;
          font-size: 12px;
          font-weight: 800;
          margin: 0 0 8px;
        }
        .form-grid {
          display: grid;
          gap: 12px;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          margin: 18px 0;
        }
        label {
          color: #455248;
          display: grid;
          font-size: 13px;
          font-weight: 700;
          gap: 6px;
        }
        input,
        textarea {
          background: #fffdf7;
          border: 1px solid #d7d1c0;
          border-radius: 12px;
          color: #1c2822;
          font: inherit;
          padding: 10px 12px;
        }
        textarea {
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          min-height: 260px;
          width: 100%;
        }
        .sheet-table-wrap {
          border: 1px solid #d7d1c0;
          border-radius: 14px;
          margin-top: 16px;
          max-height: 520px;
          overflow: auto;
        }
        .sheet-table {
          border-collapse: separate;
          border-spacing: 0;
          min-width: 100%;
          width: max-content;
        }
        .sheet-table th,
        .sheet-table td {
          border-bottom: 1px solid #e5dece;
          border-right: 1px solid #e5dece;
          padding: 0;
        }
        .sheet-table th {
          background: #f4eddd;
          color: #405045;
          font-size: 12px;
          min-width: 72px;
          padding: 8px 10px;
          position: sticky;
          top: 0;
          z-index: 1;
        }
        .sheet-table th:first-child {
          left: 0;
          min-width: 42px;
          position: sticky;
          z-index: 2;
        }
        .sheet-table tbody th {
          top: auto;
        }
        .sheet-table td input {
          background: #fffdf7;
          border: 0;
          border-radius: 0;
          min-width: 92px;
          padding: 8px 10px;
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
        .btn,
        .ghost-link {
          align-items: center;
          background: #f0eadc;
          border: 0;
          border-radius: 999px;
          color: #1c2822;
          cursor: pointer;
          display: inline-flex;
          font-weight: 800;
          justify-content: center;
          min-height: 40px;
          padding: 0 16px;
          text-decoration: none;
        }
        .btn.primary {
          background: #1c2822;
          color: #fffdf7;
        }
        .btn.ghost {
          background: #ebe5d5;
        }
        .btn.danger {
          background: #f5d5cb;
          color: #8a2c18;
        }
        .btn:disabled {
          cursor: not-allowed;
          opacity: 0.48;
        }
        .notice {
          border-radius: 14px;
          font-weight: 700;
          margin: 12px 36px;
          padding: 12px 16px;
        }
        .notice.success {
          background: #e7f4e8;
          color: #24552b;
        }
        .notice.error {
          background: #f8dfd8;
          color: #8a2c18;
        }
        .ocr-result-list {
          display: grid;
          gap: 12px;
          margin-top: 16px;
        }
        .ocr-card {
          align-items: center;
          background: #fffdf7;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          display: flex;
          gap: 16px;
          justify-content: space-between;
          padding: 14px;
        }
        .ocr-card.selected {
          border-color: #2f7d52;
          box-shadow: inset 4px 0 0 #2f7d52;
        }
        .digest {
          color: #8a826e;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 11px;
          margin: 6px 0 0;
          word-break: break-all;
        }
        pre {
          background: #162019;
          border-radius: 14px;
          color: #e9f1e7;
          font-size: 12px;
          max-height: 360px;
          overflow: auto;
          padding: 14px;
        }
        @media (max-width: 980px) {
          .hero,
          .state-panel {
            display: block;
          }
          .step-grid,
          .form-grid {
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
