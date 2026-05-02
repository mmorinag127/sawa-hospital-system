import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";
import Link from "next/link";

import TopNav from "../../../components/TopNav";
import { apiClient } from "../../../services/apiClient";

type WorkflowV2 = {
  order_id: string;
  state: string;
  headline?: string | null;
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
  source?: string;
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

const formatJson = (value: unknown) => JSON.stringify(value ?? null, null, 2);

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
          <h1>注文確認専用 v2</h1>
          <p className="subtle">この画面は workflow state を変更しません。PDF、OCR、保存シート、袋分け、出力状態だけを確認します。</p>
        </div>
        <TopNav />
      </header>

      <section className="panel state-panel">
        <div>
          <p className="eyebrow">Current State</p>
          <h2>{stateLabel(inspection?.workflow?.state)}</h2>
          <p className="subtle">{inspection?.workflow?.headline || (loading ? "読み込み中です。" : "workflow-v2 状態は未取得です。")}</p>
        </div>
        <div className="row-actions">
          {orderId ? (
            <Link className="btn" href={`/orders/${orderId}/workflow-v2`}>
              処理画面へ戻る
            </Link>
          ) : null}
        </div>
      </section>

      {error ? <div className="notice error">{error}</div> : null}

      <section className="inspection-grid">
        <section className="panel pdf-panel">
          <p className="step-tag">Original PDF</p>
          <h2>原本PDF</h2>
          {pdfUrl ? <iframe title="original pdf" src={pdfUrl} /> : <p className="subtle">PDFを読み込み中です。</p>}
        </section>

        <section className="panel">
          <p className="step-tag">Canonical OCR</p>
          <h2>選択OCR</h2>
          <pre>{formatJson(selectedOcr)}</pre>
          <h3>OCR Candidates</h3>
          <pre>{formatJson(inspection?.ocr_results || [])}</pre>
        </section>

        <section className="panel">
          <p className="step-tag">Saved Sheet</p>
          <h2>保存済みシート</h2>
          <pre>{formatJson(inspection?.saved_sheet || null)}</pre>
        </section>

        <section className="panel">
          <p className="step-tag">Bagging / Outputs</p>
          <h2>袋分け・出力</h2>
          <pre>{formatJson({ bagging_result: inspection?.bagging_result || null, output_bundle: inspection?.output_bundle || null })}</pre>
        </section>
      </section>

      <section className="panel">
        <p className="step-tag">Lineage</p>
        <h2>Artifact Lineage</h2>
        <pre>{formatJson({ workflow: inspection?.workflow || null, artifact_lineage: inspection?.artifact_lineage || null })}</pre>
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
        h2,
        h3 {
          color: #1d2822;
          margin: 0;
        }
        h3 {
          font-size: 15px;
          margin-top: 18px;
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
          align-items: center;
          display: flex;
          justify-content: space-between;
        }
        .row-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }
        .btn {
          align-items: center;
          background: #1d2822;
          border-radius: 999px;
          color: #fffdf7;
          display: inline-flex;
          font-weight: 800;
          justify-content: center;
          min-height: 40px;
          padding: 0 16px;
          text-decoration: none;
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
          grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
        }
        .pdf-panel {
          grid-row: span 2;
        }
        iframe {
          background: #fff;
          border: 1px solid #ddd5c2;
          border-radius: 14px;
          height: 880px;
          margin-top: 16px;
          width: 100%;
        }
        pre {
          background: #162019;
          border-radius: 14px;
          color: #e9f1e7;
          font-size: 12px;
          max-height: 420px;
          overflow: auto;
          padding: 14px;
        }
        @media (max-width: 1100px) {
          .hero,
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
