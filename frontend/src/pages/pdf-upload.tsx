import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";

type UploadResponse = {
  accepted?: boolean;
  filename?: string;
  message_id?: string;
  ingest_job_id?: string;
  pdf_uri?: string;
  received_at?: string;
  duplicate_blocked?: boolean;
  source_kind?: string;
  order_id?: string;
  existing_order_id?: string;
  intake_decision?: string;
  existing_order_preview?: {
    match_reason?: string | null;
    order_id?: string | null;
    facility_code?: string | null;
    week_code?: string | null;
    week_label?: string | null;
    status?: string | null;
    line_count?: number | null;
    existing_document?: {
      id?: string | null;
      message_id?: string | null;
      received_at?: string | null;
    } | null;
    prior_document?: {
      id?: string | null;
      message_id?: string | null;
      received_at?: string | null;
    } | null;
    incoming_document?: {
      message_id?: string | null;
      received_at?: string | null;
    } | null;
    will_supersede?: boolean | null;
    superseded_document_count?: number | null;
  } | null;
  count?: number;
  items?: Array<
    UploadResponse & {
      filename?: string;
      error?: string;
    }
  >;
};

type UploadItemResult = UploadResponse & {
  file_name: string;
  error?: string;
};

type UploadedPdfRow = {
  id: string;
  message_id?: string | null;
  original_filename?: string | null;
  received_at?: string | null;
  status?: string | null;
  current_stage?: string | null;
  attempt_count?: number | null;
  max_attempts?: number | null;
  last_error_code?: string | null;
  last_error_message?: string | null;
  facility_hint?: string | null;
  week_hint?: string | null;
  current_order_id?: string | null;
  current_document_id?: string | null;
  linked_order?: {
    id?: string | null;
    status?: string | null;
    facility_code?: string | null;
    week_code?: string | null;
    message_id?: string | null;
    received_at?: string | null;
    current_document_id?: string | null;
    superseded_document_count?: number | null;
    line_count?: number | null;
  } | null;
  supersede_summary?: {
    has_prior_document?: boolean | null;
    superseded_document_count?: number | null;
    current_document?: {
      id?: string | null;
      message_id?: string | null;
      received_at?: string | null;
    } | null;
    prior_document?: {
      id?: string | null;
      message_id?: string | null;
      received_at?: string | null;
    } | null;
  } | null;
  lease_expires_at?: string | null;
};

const uploadedPdfStatusLabel = (value?: string | null) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "pending") return "未処理";
  if (normalized === "processing") return "処理中";
  if (normalized === "retry_wait") return "再試行待ち";
  if (normalized === "completed") return "完了";
  if (normalized === "manual_review") return "要介入";
  if (normalized === "failed_permanent") return "恒久失敗";
  return value || "-";
};

const uploadedPdfStageLabel = (value?: string | null) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "uploaded") return "アップロード済み";
  if (normalized === "ingest_running") return "取込処理中";
  if (normalized === "retry_wait") return "再試行待ち";
  if (normalized === "manual_review") return "要介入";
  if (normalized === "completed") return "完了";
  return value || "-";
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const uploadedPdfActiveProcessing = (row: UploadedPdfRow) => {
  if (String(row.status || "").trim().toLowerCase() !== "processing") return false;
  const leaseText = String(row.lease_expires_at || "").trim();
  if (!leaseText) return true;
  const leaseExpiresAt = new Date(leaseText);
  if (Number.isNaN(leaseExpiresAt.getTime())) return true;
  return leaseExpiresAt.getTime() > Date.now();
};

const uploadedPdfCanRetry = (row: UploadedPdfRow) => {
  const status = String(row.status || "").trim().toLowerCase();
  if (status === "completed") return false;
  if (uploadedPdfActiveProcessing(row)) return false;
  return true;
};

export default function PdfUploadPage() {
  const [pdfFiles, setPdfFiles] = useState<File[]>([]);
  const [facilityHint, setFacilityHint] = useState("");
  const [weekHint, setWeekHint] = useState("");
  const [facilityName, setFacilityName] = useState("");
  const [receivedAt, setReceivedAt] = useState("");
  const [force, setForce] = useState(false);
  const [skipOcr, setSkipOcr] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [results, setResults] = useState<UploadItemResult[]>([]);
  const [uploadedRows, setUploadedRows] = useState<UploadedPdfRow[]>([]);
  const [uploadedStatusFilter, setUploadedStatusFilter] = useState("");
  const [uploadsLoading, setUploadsLoading] = useState(false);
  const [uploadsError, setUploadsError] = useState("");
  const [uploadsNotice, setUploadsNotice] = useState("");
  const [retryBusyId, setRetryBusyId] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadUploadedRows = async () => {
    setUploadsLoading(true);
    setUploadsError("");
    try {
      const params = uploadedStatusFilter ? { status: uploadedStatusFilter, limit: 50 } : { limit: 50 };
      const res = await apiClient.get("/ingest/uploads", { params });
      setUploadedRows(Array.isArray(res.data?.items) ? res.data.items : []);
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "取込済みPDF一覧の取得に失敗しました。";
      setUploadsError(String(detail));
    } finally {
      setUploadsLoading(false);
    }
  };

  useEffect(() => {
    void loadUploadedRows();
  }, [uploadedStatusFilter]);

  const retryUploadedPdf = async (row: UploadedPdfRow) => {
    if (!uploadedPdfCanRetry(row)) return;
    if (!window.confirm(`「${row.original_filename || row.id}」を再処理に戻します。`)) return;
    setRetryBusyId(row.id);
    setUploadsError("");
    setUploadsNotice("");
    try {
      await apiClient.post(`/ingest/uploads/${row.id}/retry`);
      setUploadsNotice(`「${row.original_filename || row.id}」を再処理に戻しました。`);
      await loadUploadedRows();
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "再処理の投入に失敗しました。";
      setUploadsError(String(detail));
    } finally {
      setRetryBusyId("");
    }
  };

  const submit = async () => {
    if (pdfFiles.length === 0) {
      setMessage("PDFを1件以上選択してください。");
      return;
    }
    setLoading(true);
    setMessage(pdfFiles.length === 1 ? "アップロードしています..." : `${pdfFiles.length}件のPDFをまとめて登録しています...`);
    setResults([]);
    const formData = new FormData();
    if (pdfFiles.length === 1) {
      formData.append("pdf_file", pdfFiles[0]);
    } else {
      for (const pdfFile of pdfFiles) {
        formData.append("pdf_files", pdfFile);
      }
    }
    if (facilityHint.trim()) formData.append("facility_hint", facilityHint.trim());
    if (weekHint.trim()) formData.append("week_hint", weekHint.trim());
    if (facilityName.trim()) formData.append("facility_name", facilityName.trim());
    if (receivedAt.trim()) formData.append("received_at", new Date(receivedAt).toISOString());
    if (force) formData.append("force", "true");
    if (skipOcr) formData.append("skip_ocr", "true");
    let uploadedResults: UploadItemResult[] = [];
    try {
      const res = await apiClient.post("/ingest/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const payload: UploadResponse = res.data || {};
      const payloadItems = Array.isArray(payload.items) && payload.items.length > 0 ? payload.items : [payload];
      uploadedResults = payloadItems.map((item, index) => ({
        file_name: item.filename || pdfFiles[index]?.name || pdfFiles[0]?.name || `PDF ${index + 1}`,
        ...item,
      }));
      setResults(uploadedResults);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      uploadedResults = pdfFiles.map((file) => ({
        file_name: file.name,
        error: detail ? `アップロードに失敗しました: ${detail}` : "アップロードに失敗しました。",
      }));
      setResults(uploadedResults);
    }
    const acceptedCount = uploadedResults.filter((item) => !item.error).length;
    const errorCount = uploadedResults.filter((item) => item.error).length;
    const duplicateCount = uploadedResults.filter((item) => item.duplicate_blocked).length;
    const directOrderCount = uploadedResults.filter((item) => item.order_id || item.existing_order_id).length;
    if (pdfFiles.length === 1) {
      const payload = uploadedResults[0];
      const orderHint = payload?.order_id || payload?.existing_order_id;
      setMessage(
        payload?.error
          ? payload.error
          : payload?.existing_order_preview
          ? "同じ施設・同じ週の既存注文に取り込みます。対象注文と差し替え内容を確認してください。"
          : payload?.duplicate_blocked
          ? "同じPDFは既に取り込まれています。該当注文を開いて確認してください。"
          : orderHint
          ? "取り込みが受け付けられました。対象の注文をすぐ確認できます。"
          : "受付しました。注文一覧に反映されるまで少し待ってください。"
      );
    } else {
      setMessage(
        `完了: ${acceptedCount}件受付、${duplicateCount}件重複、${errorCount}件失敗。` +
          (directOrderCount > 0 ? " 各PDFはそれぞれ別の注文/取込として扱われます。" : "")
      );
    }
    setLoading(false);
    void loadUploadedRows();
    setPdfFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const primaryResult = results.length === 1 ? results[0] : null;

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">PDF Intake</p>
          <h1>注文書アップロード</h1>
          <p className="subtle">
            Google認証後にPDFを直接登録し、既存のOCR・確認・確定フローへ流します。
          </p>
        </div>
        <TopNav />
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>この画面でやること</h2>
        </header>
        <div className="steps">
          <article className="step-card">
            <p className="step-number">1</p>
            <p className="step-title">PDFを選ぶ</p>
            <p className="step-text">施設から届いた注文書PDFを1件または複数件選びます。</p>
          </article>
          <article className="step-card">
            <p className="step-number">2</p>
            <p className="step-title">そのまま登録</p>
            <p className="step-text">通常は補助入力なしでそのまま登録して大丈夫です。PDFごとに別の注文として受け付けます。</p>
          </article>
          <article className="step-card">
            <p className="step-number">3</p>
            <p className="step-title">注文一覧で確認</p>
            <p className="step-text">登録後は注文一覧を開き、OCR結果とシートを確認します。</p>
          </article>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>アップロード</h2>
          <p className="subtle">
            PDFのみ受け付けます。迷ったらPDFだけ選んで登録してください。細かい入力は下の「補助入力」を開いたときだけ使います。
          </p>
        </header>

        <div className="form-grid">
          <label className="field field-span">
            <span className="field-label">注文書PDF</span>
            <input
              ref={fileInputRef}
              className="input"
              type="file"
              accept="application/pdf"
              multiple
              onChange={(e) => setPdfFiles(Array.from(e.target.files || []))}
            />
          </label>

          <label className="field field-span">
            <span className="field-label">施設名メモ（わかるときだけ）</span>
            <input
              className="input"
              value={facilityName}
              onChange={(e) => setFacilityName(e.target.value)}
              placeholder="例: 大和なでしこ"
            />
          </label>
        </div>

        <details className="advanced">
          <summary>補助入力・管理者向け設定を開く</summary>
          <div className="advanced-body">
            <p className="subtle">
              通常は使いません。施設コードや週が分かっている場合、または管理者から指示があった場合だけ入力してください。
            </p>
            <div className="form-grid">
          <label className="field">
                <span className="field-label">施設コード（わかるときだけ）</span>
                <input
                  className="input"
                  value={facilityHint}
                  onChange={(e) => setFacilityHint(e.target.value)}
                  placeholder="例: FAC00001"
                />
          </label>

          <label className="field">
                <span className="field-label">対象の週ID（管理者向け）</span>
                <input
                  className="input"
                  value={weekHint}
                  onChange={(e) => setWeekHint(e.target.value)}
                  placeholder="例: 2026-02@2026-02-15~2026-02-21"
                />
          </label>

          <label className="field">
                <span className="field-label">受信日時（通常は空欄）</span>
                <input
                  className="input"
                  type="datetime-local"
                  value={receivedAt}
                  onChange={(e) => setReceivedAt(e.target.value)}
                />
          </label>
        </div>

            <div className="checks">
              <label>
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />{" "}
                同じPDFでも再登録する
              </label>
              <label>
                <input type="checkbox" checked={skipOcr} onChange={(e) => setSkipOcr(e.target.checked)} />{" "}
                OCRをあとで実行する
              </label>
            </div>
          </div>
        </details>

        <div className="actions">
          <button className="btn primary" onClick={submit} disabled={loading}>
            {loading ? "受付中..." : pdfFiles.length > 1 ? `${pdfFiles.length}件のPDFを登録` : "PDFを登録"}
          </button>
          <Link href="/orders" className="btn secondary">
            注文一覧を見る
          </Link>
        </div>

        {pdfFiles.length === 1 ? <p className="message">選択中: {pdfFiles[0]?.name}</p> : null}
        {pdfFiles.length > 1 ? (
          <div className="result">
            <div>{pdfFiles.length}件のPDFを選択中です。</div>
            <div>各PDFは個別の注文/取込として処理されます。</div>
            <div className="file-list">
              {pdfFiles.map((file) => (
                <span key={`${file.name}-${file.lastModified}`} className="file-chip">
                  {file.name}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {message ? <p className="message">{message}</p> : null}

        {primaryResult ? (
          <>
            <div className="result">
              <div>受付ID: {primaryResult.message_id || "-"}</div>
              <div>登録日時: {primaryResult.received_at || "-"}</div>
              <div>
                取込方針:{" "}
                {primaryResult.existing_order_preview
                  ? "既存注文を更新"
                  : primaryResult.duplicate_blocked
                  ? "同一PDFのため再利用"
                  : "新規受付"}
              </div>
              <div>対象注文ID: {primaryResult.order_id || primaryResult.existing_order_id || "-"}</div>
            </div>
            {primaryResult.existing_order_preview ? (
              <div className="warning-banner">
                <p>
                  同じ施設・同じ週の既存注文を更新します。対象注文:{" "}
                  {primaryResult.existing_order_preview.order_id || "-"} / 施設:{" "}
                  {primaryResult.existing_order_preview.facility_code || "-"} / 週:{" "}
                  {primaryResult.existing_order_preview.week_label ||
                    primaryResult.existing_order_preview.week_code ||
                    "-"}
                </p>
                <p>
                  現在の注文文書: {primaryResult.existing_order_preview.existing_document?.message_id || "-"} /{" "}
                  {formatTimestamp(primaryResult.existing_order_preview.existing_document?.received_at)}
                </p>
                <p>
                  新しい取込文書: {primaryResult.existing_order_preview.incoming_document?.message_id || "-"} /{" "}
                  {formatTimestamp(primaryResult.existing_order_preview.incoming_document?.received_at)}
                </p>
                {primaryResult.existing_order_preview.prior_document ? (
                  <p>
                    直前の差し替え履歴: {primaryResult.existing_order_preview.prior_document.message_id || "-"} /{" "}
                    {formatTimestamp(primaryResult.existing_order_preview.prior_document.received_at)}
                  </p>
                ) : null}
              </div>
            ) : null}
            <div className="next-steps">
              <p className="field-label">次にすること</p>
              {primaryResult.order_id || primaryResult.existing_order_id ? (
                <p className="subtle" style={{ marginTop: 0, marginBottom: 12 }}>
                  受付したPDFは、以下の注文詳細から直接確認できます。
                </p>
              ) : (
                <p className="subtle" style={{ marginTop: 0, marginBottom: 12 }}>
                  まずは「注文一覧を見る」を開いて、検索欄に受付IDを入れて対象を確認してください。
                </p>
              )}
              <div className="actions" style={{ marginBottom: 12 }}>
                {primaryResult.order_id || primaryResult.existing_order_id ? (
                  <Link href={`/orders/${primaryResult.order_id || primaryResult.existing_order_id}`} className="btn primary">
                    注文詳細を開く
                  </Link>
                ) : (
                  <Link
                    href={primaryResult.message_id ? `/orders?search=${encodeURIComponent(primaryResult.message_id)}` : "/orders"}
                    className="btn primary"
                  >
                    注文一覧を見る
                  </Link>
                )}
                <Link href="/orders" className="btn secondary">
                  注文一覧を見る
                </Link>
              </div>
              <ol>
                <li>注文詳細を開き、OCR結果とシートを確認します。</li>
                <li>必要なら行修正・再解析を実行し、「確定」まで進めます。</li>
                <li>完了後、袋分け・納品関連ページは同じ注文詳細から確認できます。</li>
              </ol>
              <details className="advanced result-raw">
                <summary>管理者向け詳細を開く</summary>
                <div className="result">
                  <div>message_id: {primaryResult.message_id || "-"}</div>
                  <div>ingest_job_id: {primaryResult.ingest_job_id || "-"}</div>
                  <div>received_at: {primaryResult.received_at || "-"}</div>
                  <div>order_id: {primaryResult.order_id || "-"}</div>
                  <div>existing_order_id: {primaryResult.existing_order_id || "-"}</div>
                  <div>duplicate_blocked: {primaryResult.duplicate_blocked ? "true" : "false"}</div>
                  <div>intake_decision: {primaryResult.intake_decision || "-"}</div>
                  <div>existing_order_preview.order_id: {primaryResult.existing_order_preview?.order_id || "-"}</div>
                </div>
              </details>
            </div>
          </>
        ) : null}
        {results.length > 1 ? (
          <div className="next-steps">
            <p className="field-label">アップロード結果</p>
            <p className="subtle" style={{ marginTop: 0, marginBottom: 12 }}>
              各PDFは別々の注文/取込として受け付けています。結果を確認してから注文一覧へ進んでください。
            </p>
            <div className="result-list">
              {results.map((item) => {
                const orderId = item.order_id || item.existing_order_id;
                return (
                  <div key={`${item.file_name}-${item.message_id || item.error || "pending"}`} className="result-card">
                    <div className="result-card-head">
                      <strong>{item.file_name}</strong>
                      <span>{item.error ? "失敗" : item.duplicate_blocked ? "重複" : "受付済み"}</span>
                    </div>
                    <div className="result-card-body">
                      <div>受付ID: {item.message_id || "-"}</div>
                      <div>対象注文ID: {orderId || "-"}</div>
                      <div>
                        {item.error ||
                          (item.existing_order_preview
                            ? "同じ施設・同じ週の既存注文を更新します。"
                            : item.duplicate_blocked
                            ? "既存注文を確認してください。"
                            : "注文一覧または注文詳細から確認できます。")}
                      </div>
                    </div>
                    <div className="actions">
                      {orderId ? (
                        <Link href={`/orders/${orderId}`} className="btn secondary">
                          注文詳細を開く
                        </Link>
                      ) : item.message_id ? (
                        <Link href={`/orders?search=${encodeURIComponent(item.message_id)}`} className="btn secondary">
                          注文一覧で探す
                        </Link>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
            <ol>
              <li>一覧の各PDFが、別々の注文として受け付けられていることを確認します。</li>
              <li>必要な注文だけ詳細を開き、OCR結果とシートを確認します。</li>
              <li>まとめて確認したい場合は注文一覧に戻り、受付IDや注文IDで探します。</li>
            </ol>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <header className="panel-header panel-header-stack">
          <div>
            <h2>最近の取込PDF</h2>
            <p className="subtle">
              受け付けたPDFを直接確認できます。未処理や処理落ちの行は、ここから再処理に戻します。
            </p>
          </div>
          <div className="history-toolbar">
            <label className="field history-filter">
              <span className="field-label">状態</span>
              <select
                className="input"
                value={uploadedStatusFilter}
                onChange={(e) => setUploadedStatusFilter(e.target.value)}
              >
                <option value="">すべて</option>
                <option value="pending">未処理</option>
                <option value="processing">処理中</option>
                <option value="retry_wait">再試行待ち</option>
                <option value="manual_review">要介入</option>
                <option value="completed">完了</option>
              </select>
            </label>
            <button className="btn secondary" type="button" onClick={() => void loadUploadedRows()} disabled={uploadsLoading}>
              {uploadsLoading ? "更新中..." : "最新に更新"}
            </button>
          </div>
        </header>

        {uploadsNotice ? <p className="message message-success">{uploadsNotice}</p> : null}
        {uploadsError ? <p className="message message-error">{uploadsError}</p> : null}

        {uploadsLoading && uploadedRows.length === 0 ? (
          <p className="subtle">読み込み中...</p>
        ) : uploadedRows.length === 0 ? (
          <p className="subtle">該当する取込済みPDFはありません。</p>
        ) : (
          <div className="history-list">
            {uploadedRows.map((row) => {
              const retryDisabled = !uploadedPdfCanRetry(row);
              const orderId = String(row.current_order_id || "").trim();
              const processingActive = uploadedPdfActiveProcessing(row);
              const linkedOrderId = String(row.linked_order?.id || orderId || "").trim();
              return (
                <article key={row.id} className="history-card">
                  <div className="history-card-head">
                    <div>
                      <p className="history-card-id">{row.id}</p>
                      <h3 className="history-card-title">{row.original_filename || row.message_id || row.id}</h3>
                    </div>
                    <div className="history-badges">
                      <span className="history-badge">{uploadedPdfStatusLabel(row.status)}</span>
                      <span className="history-badge history-badge-stage">{uploadedPdfStageLabel(row.current_stage)}</span>
                    </div>
                  </div>
                  <div className="history-meta">
                    <span>受付ID: {row.message_id || "-"}</span>
                    <span>受信日時: {formatTimestamp(row.received_at)}</span>
                    <span>
                      試行回数: {row.attempt_count || 0} / {row.max_attempts || "-"}
                    </span>
                    <span>施設ヒント: {row.facility_hint || "-"}</span>
                    <span>週ヒント: {row.week_hint || "-"}</span>
                    <span>対象注文ID: {linkedOrderId || "-"}</span>
                  </div>
                  {row.linked_order ? (
                    <div className="result" style={{ marginTop: 12 }}>
                      <div>反映先注文: {row.linked_order.id || "-"}</div>
                      <div>
                        施設/週: {row.linked_order.facility_code || "-"} / {row.linked_order.week_code || "-"}
                      </div>
                      <div>注文状態: {row.linked_order.status || "-"}</div>
                      <div>
                        現行文書: {row.supersede_summary?.current_document?.message_id || "-"} /{" "}
                        {formatTimestamp(row.supersede_summary?.current_document?.received_at)}
                      </div>
                      {row.supersede_summary?.has_prior_document ? (
                        <div>
                          直前文書: {row.supersede_summary?.prior_document?.message_id || "-"} /{" "}
                          {formatTimestamp(row.supersede_summary?.prior_document?.received_at)}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  {row.last_error_message ? (
                    <p className="history-error">
                      エラー: {row.last_error_message}
                    </p>
                  ) : null}
                  {processingActive ? (
                    <p className="history-hint">現在処理中のため、処理が止まった時だけ再処理に戻してください。</p>
                  ) : null}
                  <div className="actions">
                    {linkedOrderId ? (
                      <Link href={`/orders/${linkedOrderId}`} className="btn secondary">
                        注文詳細を開く
                      </Link>
                    ) : row.message_id ? (
                      <Link href={`/orders?search=${encodeURIComponent(row.message_id)}`} className="btn secondary">
                        注文一覧で探す
                      </Link>
                    ) : null}
                    <button
                      type="button"
                      className="btn primary"
                      onClick={() => void retryUploadedPdf(row)}
                      disabled={retryDisabled || retryBusyId === row.id}
                    >
                      {retryBusyId === row.id ? "再投入中..." : "再処理に戻す"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
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
          align-items: flex-start;
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
          max-width: 700px;
        }
        .panel {
          background: #fff;
          border-radius: 18px;
          padding: 24px;
          border: 1px solid rgba(25, 32, 30, 0.08);
          box-shadow: 0 12px 26px rgba(27, 35, 33, 0.06);
          max-width: 980px;
        }
        .panel-header {
          margin-bottom: 16px;
        }
        .panel-header-stack {
          display: flex;
          flex-wrap: wrap;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
        }
        .steps {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 14px;
        }
        .step-card {
          border-radius: 16px;
          background: #fcfbf7;
          border: 1px solid rgba(25, 32, 30, 0.1);
          padding: 16px;
        }
        .step-number {
          margin: 0 0 8px;
          font-size: 24px;
          font-weight: 800;
          color: #7b5c25;
        }
        .step-title {
          margin: 0 0 8px;
          font-weight: 800;
        }
        .step-text {
          margin: 0;
          color: #51615c;
          line-height: 1.6;
        }
        .form-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 16px;
        }
        .field {
          display: grid;
          gap: 8px;
        }
        .field-span {
          grid-column: 1 / -1;
        }
        .field-label {
          font-weight: 700;
        }
        .input {
          width: 100%;
          border-radius: 12px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          padding: 12px 14px;
          background: #fcfbf7;
        }
        .checks {
          display: flex;
          gap: 18px;
          flex-wrap: wrap;
          margin: 18px 0;
        }
        .advanced {
          margin-top: 18px;
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.12);
          background: #fbfaf6;
          padding: 0;
          overflow: hidden;
        }
        .advanced summary {
          cursor: pointer;
          list-style: none;
          padding: 14px 16px;
          font-weight: 700;
        }
        .advanced summary::-webkit-details-marker {
          display: none;
        }
        .advanced-body {
          padding: 0 16px 16px;
        }
        .actions {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          align-items: center;
        }
        .btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 44px;
          padding: 0 18px;
          border-radius: 999px;
          font-weight: 700;
          text-decoration: none;
          border: 1px solid transparent;
          cursor: pointer;
        }
        .primary {
          background: #1f2a2a;
          color: #f7f2e7;
        }
        .secondary {
          background: #f6f1e6;
          color: #1f2a2a;
          border-color: rgba(25, 32, 30, 0.12);
        }
        .message {
          margin-top: 16px;
          color: #374240;
        }
        .message-success {
          color: #204b34;
        }
        .message-error {
          color: #8b2d1f;
        }
        .result {
          margin-top: 16px;
          display: grid;
          gap: 8px;
          padding: 14px;
          border-radius: 14px;
          background: #f6f8f3;
          border: 1px solid rgba(72, 102, 84, 0.12);
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 13px;
        }
        .warning-banner {
          margin-top: 16px;
          display: grid;
          gap: 8px;
          padding: 14px 16px;
          border-radius: 14px;
          background: #fff6e7;
          border: 1px solid rgba(156, 106, 16, 0.2);
          color: #5c4315;
        }
        .warning-banner p {
          margin: 0;
        }
        .file-list {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }
        .file-chip {
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 0 10px;
          border-radius: 999px;
          background: #fff;
          border: 1px solid rgba(72, 102, 84, 0.16);
        }
        .next-steps {
          margin-top: 16px;
          padding: 16px;
          border-radius: 16px;
          background: #f6f1e6;
          border: 1px solid rgba(25, 32, 30, 0.08);
        }
        .result-list {
          display: grid;
          gap: 12px;
          margin-bottom: 12px;
        }
        .result-card {
          border-radius: 14px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #fff;
          padding: 14px;
        }
        .result-card-head {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin-bottom: 8px;
        }
        .result-card-body {
          display: grid;
          gap: 6px;
          margin-bottom: 10px;
          color: #374240;
        }
        .next-steps ol {
          margin: 10px 0 0;
          padding-left: 20px;
        }
        .result-raw {
          margin-top: 14px;
          background: #fff;
        }
        .history-toolbar {
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          align-items: flex-end;
        }
        .history-filter {
          min-width: 180px;
        }
        .history-list {
          display: grid;
          gap: 14px;
        }
        .history-card {
          border-radius: 16px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          background: #fcfbf7;
          padding: 16px;
          display: grid;
          gap: 12px;
        }
        .history-card-head {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          flex-wrap: wrap;
          align-items: flex-start;
        }
        .history-card-id {
          margin: 0 0 4px;
          color: #6f7f79;
          font-size: 12px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }
        .history-card-title {
          margin: 0;
          font-size: 18px;
        }
        .history-badges {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }
        .history-badge {
          display: inline-flex;
          align-items: center;
          min-height: 30px;
          padding: 0 10px;
          border-radius: 999px;
          background: #eef2f0;
          color: #31423f;
          font-size: 12px;
          font-weight: 700;
        }
        .history-badge-stage {
          background: #f6f1e6;
          color: #6f521c;
        }
        .history-meta {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 8px 12px;
          color: #374240;
          font-size: 13px;
        }
        .history-error {
          margin: 0;
          padding: 10px 12px;
          border-radius: 12px;
          background: #fff2ef;
          border: 1px solid rgba(139, 45, 31, 0.12);
          color: #8b2d1f;
        }
        .history-hint {
          margin: 0;
          color: #5f615c;
          font-size: 13px;
        }
      `}</style>
    </main>
  );
}
