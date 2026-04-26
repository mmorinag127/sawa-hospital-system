import { useEffect, useState } from "react";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import { useCurrentUserRole } from "../hooks/useCurrentUserRole";

type SystemStatus = {
  intake?: {
    mode?: string | null;
    manual_upload_enabled?: boolean;
    manual_upload_storage?: {
      configured?: boolean;
      mode?: string | null;
      persisted?: boolean;
      bucket?: string | null;
    } | null;
  };
  oauth_config?: {
    configured?: boolean;
    google_client_ids?: string[];
  };
  ocr_pipeline?: {
    status?: string | null;
    last_success_at?: string | null;
    last_error_at?: string | null;
    last_error?: string | null;
    configured?: boolean;
    url_set?: boolean;
    bucket_set?: boolean;
    bucket?: string | null;
    input_prefix?: string | null;
    output_prefix?: string | null;
    trigger_mode?: string | null;
    http_trigger_enabled?: boolean;
    gcs_trigger_enabled?: boolean;
    wait_strategy?: string | null;
    sync_wait_supported?: boolean;
    sync_wait_note?: string | null;
    inflight?: number | null;
    max_inflight?: number | null;
  };
  uploaded_pdfs?: {
    total?: number;
    pending_count?: number;
    processing_count?: number;
    retry_wait_count?: number;
    completed_count?: number;
    manual_review_count?: number;
    stale_lease_count?: number;
    retry_ready_count?: number;
    eligible_backlog_count?: number;
    oldest_ready_at?: string | null;
    oldest_ready_seconds?: number | null;
  };
  ingest_jobs?: {
    total?: number;
    pending_count?: number;
    error_count?: number;
    processing_count?: number;
    done_count?: number;
    stale_processing_count?: number;
    eligible_backlog_count?: number;
    oldest_pending_at?: string | null;
    oldest_pending_seconds?: number | null;
    oldest_processing_at?: string | null;
    oldest_processing_seconds?: number | null;
  };
  db_quota?: {
    resource?: string;
    unit?: string;
    used?: number;
    limit?: number;
    ratio?: number | null;
    alert_level?: string;
    message?: string;
  };
  ocr_reparse_quality?: {
    sample?: {
      lookback_hours?: number;
      sample_limit?: number;
      min_samples?: number;
      evaluated_jobs?: number;
      generated_at?: string | null;
    };
    thresholds?: {
      min_success_rate?: number;
      max_truncated_rate?: number;
      max_empty_rate?: number;
      max_validation_failure_rate?: number;
    };
    providers?: Array<{
      provider?: string;
      total?: number;
      done?: number;
      failed?: number;
      success_rate?: number | null;
      truncated_rate?: number | null;
      empty_rate?: number | null;
      validation_failure_rate?: number | null;
      pipeline_fallback_rate?: number | null;
      gate_status?: string;
      violations?: string[];
      last_updated_at?: string | null;
    }>;
    gate?: {
      status?: string;
      fail_providers?: string[];
      warming_up_providers?: string[];
      checked_provider_count?: number;
      provider_count?: number;
    };
    error?: string;
  };
};

const formatDate = (value?: string | null) => {
  if (!value) return "未取得";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未取得";
  return date.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
};

const formatStatus = (value?: string | null) => {
  const raw = (value || "").toLowerCase();
  if (!raw) return "未取得";
  if (raw === "ok") return "OK";
  if (raw === "error") return "エラー";
  if (raw === "expired") return "期限切れ";
  if (raw === "misconfigured") return "未設定";
  if (raw === "running") return "実行中";
  return value || "未取得";
};

const formatBytes = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "未取得";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let idx = 0;
  let current = value;
  while (current >= 1024 && idx < units.length - 1) {
    current /= 1024;
    idx += 1;
  }
  const text = current >= 10 ? current.toFixed(1) : current.toFixed(2);
  return `${text}${units[idx]}`;
};

const formatQuotaNumber = (value?: number | null, unit?: string) => {
  if (value == null || Number.isNaN(value)) return "未取得";
  if (unit === "bytes") return formatBytes(value);
  return value.toLocaleString("ja-JP");
};

const formatQuotaLevel = (value?: string | null) => {
  const normalized = (value || "").toLowerCase();
  if (!normalized) return "未取得";
  if (normalized === "ok") return "正常";
  if (normalized === "warning") return "警告";
  if (normalized === "critical") return "危険";
  return value || "未取得";
};

const formatPercent = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "未取得";
  return `${(value * 100).toFixed(1)}%`;
};

const formatQualityGate = (value?: string | null) => {
  const normalized = (value || "").toLowerCase();
  if (!normalized) return "未取得";
  if (normalized === "pass") return "PASS";
  if (normalized === "fail") return "FAIL";
  if (normalized === "insufficient_data") return "データ不足";
  if (normalized === "warming_up") return "収集中";
  if (normalized === "error") return "ERROR";
  return value || "未取得";
};

const formatDuration = (value?: number | null) => {
  if (value == null || Number.isNaN(value)) return "未取得";
  const total = Math.max(Math.floor(value), 0);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}時間${minutes}分`;
  return `${minutes}分`;
};

export default function SystemStatusPage() {
  const { isAdmin } = useCurrentUserRole();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState("");
  const [maintenanceMessage, setMaintenanceMessage] = useState("");
  const [maintenanceBusy, setMaintenanceBusy] = useState(false);
  const [clearConfirm, setClearConfirm] = useState("");

  const loadStatus = async (silent = false) => {
    try {
      const res = await apiClient.get("/system/status");
      setStatus(res.data || null);
      setError("");
      if (!silent) {
        setMaintenanceMessage("");
      }
    } catch {
      setStatus(null);
      setError("システム状態の取得に失敗しました。");
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  const downloadDb = async (snapshot = false) => {
    setMaintenanceBusy(true);
    setMaintenanceMessage(snapshot ? "DBスナップショットを作成中..." : "DBをダウンロード中...");
    try {
      const res = await apiClient.get(`/system/db/download${snapshot ? "?snapshot=1" : ""}`, {
        responseType: "blob",
      });
      const disposition = String(res.headers?.["content-disposition"] || "");
      const matched = disposition.match(/filename=\"?([^\";]+)\"?/i);
      const filename = matched?.[1] || (snapshot ? "db_snapshot.zip" : "system.db");
      const blob = new Blob([res.data], { type: res.data?.type || "application/octet-stream" });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      setMaintenanceMessage("DBダウンロードを開始しました。");
    } catch (err: any) {
      const statusCode = err?.response?.status;
      if (statusCode === 403) {
        setMaintenanceMessage("管理者権限が必要です。");
      } else {
        setMaintenanceMessage("DBダウンロードに失敗しました。");
      }
    } finally {
      setMaintenanceBusy(false);
    }
  };

  const clearAll = async () => {
    if (clearConfirm.trim() !== "CLEAR_ALL") {
      setMaintenanceMessage("確認文字列に CLEAR_ALL を入力してください。");
      return;
    }
    setMaintenanceBusy(true);
    setMaintenanceMessage("全権クリアを実行中...");
    try {
      const res = await apiClient.post("/system/clear-all", {
        confirm: "CLEAR_ALL",
        include_audit_logs: true,
      });
      const totalRemoved = Number(res.data?.result?.total_removed || 0);
      setMaintenanceMessage(`全権クリアを完了しました。削除件数: ${totalRemoved.toLocaleString("ja-JP")}`);
      setClearConfirm("");
      await loadStatus(true);
    } catch (err: any) {
      const statusCode = err?.response?.status;
      if (statusCode === 403) {
        setMaintenanceMessage("管理者権限が必要です。");
      } else if (statusCode === 400) {
        setMaintenanceMessage("確認文字列が不正です。");
      } else {
        setMaintenanceMessage("全権クリアに失敗しました。");
      }
    } finally {
      setMaintenanceBusy(false);
    }
  };

  const dbQuota = status?.db_quota;
  const quotaRatioText =
    dbQuota?.ratio == null || Number.isNaN(dbQuota.ratio)
      ? "未取得"
      : `${(dbQuota.ratio * 100).toFixed(1)}%`;
  const quotaLevel = formatQuotaLevel(dbQuota?.alert_level);
  const quotaLevelClass = (dbQuota?.alert_level || "").toLowerCase();
  const ocrQuality = status?.ocr_reparse_quality;
  const qualityGateStatus = (ocrQuality?.gate?.status || "").toLowerCase();
  const qualityProviders = ocrQuality?.providers || [];
  const uploadedPdfs = status?.uploaded_pdfs;
  const ingestJobs = status?.ingest_jobs;
  const ocrBacklogSeverity =
    (uploadedPdfs?.manual_review_count || 0) > 0 || (ingestJobs?.error_count || 0) > 0
      ? "error"
      : (uploadedPdfs?.eligible_backlog_count || 0) > 0 ||
          (uploadedPdfs?.processing_count || 0) > 0 ||
          (ingestJobs?.stale_processing_count || 0) > 0
        ? "warn"
        : "ok";
  const ocrBacklogLabel =
    ocrBacklogSeverity === "error" ? "要介入" : ocrBacklogSeverity === "warn" ? "滞留あり" : "正常";

  return (
    <main className="system-page">
      <header className="hero">
        <div>
          <p className="eyebrow">System Operations</p>
          <h1>システム状態と復帰手順</h1>
          <p className="subtle">
            取込方式・Google認証・OCRパイプライン・品質ゲートの状態を確認します。
          </p>
        </div>
        <TopNav />
      </header>

      {error && <p className="error">{error}</p>}

      <section className="status-grid">
        <article className="card">
          <h2>取込方式</h2>
          <p className="value">注文書アップロード</p>
          <p className="meta">
            受付方式は手動アップロードに固定しています。
          </p>
          <p className="meta">
            保存先: {status?.intake?.manual_upload_storage?.mode ?? "未取得"}
          </p>
          <p className="meta">
            永続化: {status?.intake?.manual_upload_storage?.persisted ? "あり" : "なし"}
          </p>
          {status?.intake?.manual_upload_enabled && !status?.intake?.manual_upload_storage?.configured ? (
            <p className="meta warn">注文書アップロード保存先が未設定です。</p>
          ) : null}
        </article>
        <article className="card">
          <h2>Google 認証</h2>
          <p className="value">{status?.oauth_config?.configured ? "OK" : "未設定"}</p>
          <p className="meta">許可されたクライアントID: {(status?.oauth_config?.google_client_ids || []).join(", ") || "未取得"}</p>
          <p className="meta">Googleログインで利用します。</p>
        </article>
        <article className="card card-wide ocr-card">
          <h2>OCR パイプライン</h2>
          <div className="ocr-card-top">
            <div className="ocr-card-status">
              <p className="value">
                {status?.ocr_pipeline?.configured
                  ? formatStatus(status?.ocr_pipeline?.status)
                  : "未設定"}
              </p>
              <p className={`ocr-backlog-badge ${ocrBacklogSeverity}`}>{ocrBacklogLabel}</p>
            </div>
            <div className="ocr-queue-summary" aria-label="OCR滞留サマリ">
              <div className="ocr-queue-summary-block">
                <p className="ocr-queue-summary-label">uploaded</p>
                <p className="ocr-queue-summary-value">
                  {uploadedPdfs?.pending_count ?? "未取得"} / {uploadedPdfs?.processing_count ?? "未取得"} /{" "}
                  {uploadedPdfs?.completed_count ?? "未取得"}
                </p>
                <p className="ocr-queue-summary-help">未処理 / 処理中 / 完了</p>
              </div>
              <div className="ocr-queue-summary-block">
                <p className="ocr-queue-summary-label">ingest</p>
                <p className="ocr-queue-summary-value">
                  {ingestJobs?.pending_count ?? "未取得"} / {ingestJobs?.processing_count ?? "未取得"} /{" "}
                  {ingestJobs?.stale_processing_count ?? "未取得"}
                </p>
                <p className="ocr-queue-summary-help">未処理 / 処理中 / stale</p>
              </div>
            </div>
          </div>
          <p className="meta">最終成功: {formatDate(status?.ocr_pipeline?.last_success_at)}</p>
          <p className="meta">最終失敗: {formatDate(status?.ocr_pipeline?.last_error_at)}</p>
          {status?.ocr_pipeline?.last_error && (
            <p className="meta warn">エラー: {status.ocr_pipeline.last_error}</p>
          )}
          <p className="meta">
            設定: Bucket={status?.ocr_pipeline?.bucket_set ? "OK" : "NG"} / URL=
            {status?.ocr_pipeline?.url_set ? "OK" : "未設定（バケット経由で動作）"}
          </p>
          <p className="meta">
            trigger_mode={status?.ocr_pipeline?.trigger_mode || "未取得"} / wait=
            {status?.ocr_pipeline?.wait_strategy || "未取得"}
          </p>
          <p className="meta">
            sync_wait={status?.ocr_pipeline?.sync_wait_supported ? "OK" : "利用条件あり"} / http_trigger=
            {status?.ocr_pipeline?.http_trigger_enabled ? "ON" : "OFF"}
          </p>
          <p className="meta">
            inflight={status?.ocr_pipeline?.inflight ?? "未取得"} / max=
            {status?.ocr_pipeline?.max_inflight ?? "未取得"}
          </p>
          <p className="meta">
            uploaded backlog={uploadedPdfs?.eligible_backlog_count ?? "未取得"} / ingest backlog=
            {ingestJobs?.eligible_backlog_count ?? "未取得"}
          </p>
          <p className="meta">
            oldest backlog: {formatDate(uploadedPdfs?.oldest_ready_at)} / 経過=
            {formatDuration(uploadedPdfs?.oldest_ready_seconds)}
          </p>
          {status?.ocr_pipeline?.sync_wait_note ? (
            <p className="meta warn">{status.ocr_pipeline.sync_wait_note}</p>
          ) : null}
          <p className="meta">
            Bucket: {status?.ocr_pipeline?.bucket || "未取得"} / input=
            {status?.ocr_pipeline?.input_prefix || "-"} / output=
            {status?.ocr_pipeline?.output_prefix || "-"}
          </p>
        </article>
        <article className="card">
          <h2>DB Quota</h2>
          <p className={`value quota-level ${quotaLevelClass}`}>{quotaLevel}</p>
          <p className="meta">
            使用量: {formatQuotaNumber(dbQuota?.used, dbQuota?.unit)} / 上限:{" "}
            {formatQuotaNumber(dbQuota?.limit, dbQuota?.unit)}
          </p>
          <p className="meta">利用率: {quotaRatioText}</p>
          <p className="meta">{dbQuota?.resource || "database"}</p>
          {dbQuota?.message ? <p className="meta warn">{dbQuota.message}</p> : null}
        </article>
        <article className="card">
          <h2>OCR 品質ゲート</h2>
          <p className={`value quality-gate ${qualityGateStatus}`}>
            {formatQualityGate(ocrQuality?.gate?.status)}
          </p>
          <p className="meta">評価ジョブ: {ocrQuality?.sample?.evaluated_jobs ?? "未取得"}</p>
          <p className="meta">評価provider: {ocrQuality?.gate?.checked_provider_count ?? "未取得"}</p>
          <p className="meta">対象期間: {ocrQuality?.sample?.lookback_hours ?? "未取得"} 時間</p>
          {ocrQuality?.error ? <p className="meta warn">エラー: {ocrQuality.error}</p> : null}
        </article>
      </section>

      <section className="panel">
        <h2>OCR再解析 品質メトリクス（provider別）</h2>
        <p className="meta">
          サンプル数最小値: {ocrQuality?.sample?.min_samples ?? "未取得"} / 成功率下限:{" "}
          {formatPercent(ocrQuality?.thresholds?.min_success_rate)} / truncated上限:{" "}
          {formatPercent(ocrQuality?.thresholds?.max_truncated_rate)} / empty上限:{" "}
          {formatPercent(ocrQuality?.thresholds?.max_empty_rate)}
        </p>
        <div className="quality-table-wrap">
          <table className="quality-table">
            <thead>
              <tr>
                <th>provider</th>
                <th>gate</th>
                <th>sample</th>
                <th>success</th>
                <th>truncated</th>
                <th>empty</th>
                <th>validation</th>
                <th>fallback</th>
                <th>最終更新</th>
              </tr>
            </thead>
            <tbody>
              {qualityProviders.length > 0 ? (
                qualityProviders.map((row, index) => (
                  <tr key={`${row.provider || "provider"}-${index}`}>
                    <td>{row.provider || "-"}</td>
                    <td>{formatQualityGate(row.gate_status)}</td>
                    <td>{row.total ?? "-"}</td>
                    <td>{formatPercent(row.success_rate)}</td>
                    <td>{formatPercent(row.truncated_rate)}</td>
                    <td>{formatPercent(row.empty_rate)}</td>
                    <td>{formatPercent(row.validation_failure_rate)}</td>
                    <td>{formatPercent(row.pipeline_fallback_rate)}</td>
                    <td>{formatDate(row.last_updated_at)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={9}>データがありません</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {qualityProviders.some((row) => (row.violations || []).length > 0) ? (
          <div className="quality-violations">
            {qualityProviders
              .filter((row) => (row.violations || []).length > 0)
              .map((row) => (
                <p key={`violation-${row.provider}`} className="meta warn">
                  {row.provider}: {(row.violations || []).join(", ")}
                </p>
              ))}
          </div>
        ) : null}
      </section>

      <section className="panel">
        <h2>管理者メンテナンス</h2>
        <p className="meta">
          DBダウンロードと全権クリアは管理者のみ実行できます。誤操作防止のため、クリア時は確認文字列が必要です。
        </p>
        <div className="admin-actions">
          <button className="btn ghost" type="button" onClick={() => loadStatus()} disabled={maintenanceBusy}>
            状態を再取得
          </button>
          {isAdmin ? (
            <>
              <button className="btn ghost" type="button" onClick={() => downloadDb(false)} disabled={maintenanceBusy}>
                DBをダウンロード
              </button>
              <button className="btn ghost" type="button" onClick={() => downloadDb(true)} disabled={maintenanceBusy}>
                スナップショットZIP
              </button>
            </>
          ) : null}
        </div>
        {isAdmin ? (
          <div className="clear-box">
            <label className="clear-label" htmlFor="clear-confirm">
              確認文字列
            </label>
            <input
              id="clear-confirm"
              className="input"
              value={clearConfirm}
              onChange={(event) => setClearConfirm(event.target.value)}
              placeholder="CLEAR_ALL"
              autoComplete="off"
            />
            <button className="btn danger" type="button" onClick={clearAll} disabled={maintenanceBusy}>
              {maintenanceBusy ? "実行中..." : "全権クリアを実行"}
            </button>
          </div>
        ) : (
          <p className="meta">管理者アカウントでログインすると、DBダウンロードと全権クリアが表示されます。</p>
        )}
        {maintenanceMessage ? <p className="meta warn">{maintenanceMessage}</p> : null}
      </section>

      <section className="panel">
        <h2>運用メモ</h2>
        <div className="steps">
          <h3>1. 注文書アップロード保存先</h3>
          <ol>
            <li>保存先が未設定なら、注文書アップロードは受け付けません。</li>
            <li>このページの「取込方式」で保存先モードを確認してください。</li>
          </ol>

          <h3>2. Google ログインの確認</h3>
          <ol>
            <li>ログイン障害が出たら Google Cloud の OAuth 設定を確認します。</li>
            <li>許可されたクライアントIDが未設定なら運用を止めて管理者へ連絡してください。</li>
          </ol>

          <h3>3. OCR パイプラインの確認</h3>
          <ol>
            <li>最終失敗時刻と last_error を見ます。</li>
            <li>必要なら Cloud Run の `ocr-pipeline-prod` ログを確認します。</li>
          </ol>
        </div>
      </section>

      <style jsx>{`
        :global(body) {
          background: radial-gradient(circle at top left, #f8f4ea, #f4f7f6 40%, #eef1f0 100%);
          color: #1f2a2a;
          font-family: "Manrope", "Noto Sans JP", sans-serif;
        }
        .system-page {
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
          font-size: clamp(28px, 4vw, 40px);
          margin: 0 0 12px;
        }
        .subtle {
          color: #51615c;
          margin: 0;
        }
        .status-grid {
          display: grid;
          gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          margin-bottom: 28px;
        }
        .card {
          background: #ffffff;
          border-radius: 16px;
          padding: 18px;
          box-shadow: 0 10px 30px rgba(27, 35, 33, 0.08);
          border: 1px solid rgba(25, 32, 30, 0.1);
        }
        .card-wide {
          grid-column: span 2;
        }
        .value {
          font-size: 20px;
          font-weight: 700;
          margin: 8px 0;
        }
        .meta {
          font-size: 12px;
          color: #5a6a66;
        }
        .meta.warn {
          color: #b94014;
        }
        .ocr-card-top {
          display: grid;
          gap: 12px;
          grid-template-columns: minmax(140px, 180px) minmax(0, 1fr);
          align-items: start;
          margin: 10px 0 14px;
        }
        .ocr-card-status {
          display: grid;
          gap: 8px;
          align-content: start;
        }
        .ocr-backlog-badge {
          margin: 0;
          font-size: 14px;
          font-weight: 700;
          color: #5a6a66;
        }
        .ocr-backlog-badge.warning,
        .ocr-backlog-badge.warn {
          color: #b77700;
        }
        .ocr-backlog-badge.critical,
        .ocr-backlog-badge.error {
          color: #b94014;
        }
        .ocr-queue-summary {
          display: grid;
          gap: 10px;
          grid-template-columns: repeat(2, minmax(180px, 1fr));
          margin: 0;
        }
        .ocr-queue-summary-block {
          border: 1px solid rgba(25, 32, 30, 0.1);
          border-radius: 12px;
          background: #f6f8f7;
          padding: 12px 14px;
        }
        .ocr-queue-summary-label {
          margin: 0 0 4px;
          font-size: 11px;
          color: #5a6a66;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          font-weight: 700;
        }
        .ocr-queue-summary-value {
          margin: 0;
          font-size: 22px;
          font-weight: 700;
          color: #1f2a2a;
        }
        .ocr-queue-summary-help {
          margin: 4px 0 0;
          font-size: 12px;
          color: #5a6a66;
        }
        .panel {
          background: #ffffff;
          border-radius: 18px;
          padding: 24px;
          border: 1px solid rgba(25, 32, 30, 0.1);
          box-shadow: 0 10px 30px rgba(27, 35, 33, 0.08);
          margin-bottom: 20px;
        }
        .quota-level.warning {
          color: #b77700;
        }
        .quota-level.warn {
          color: #b77700;
        }
        .quota-level.critical {
          color: #b94014;
        }
        .quota-level.error {
          color: #b94014;
        }
        .quality-gate.pass {
          color: #1f7a2f;
        }
        .quality-gate.fail,
        .quality-gate.error {
          color: #b94014;
        }
        .quality-gate.insufficient_data {
          color: #8f6f12;
        }
        .quality-gate.warming_up {
          color: #8f6f12;
        }
        .quality-table-wrap {
          overflow-x: auto;
          margin-top: 12px;
        }
        .quality-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 12px;
          min-width: 820px;
        }
        .quality-table th,
        .quality-table td {
          border-bottom: 1px solid rgba(25, 32, 30, 0.12);
          text-align: left;
          padding: 8px;
          white-space: nowrap;
        }
        .quality-table th {
          color: #5a6a66;
          font-weight: 700;
        }
        .quality-violations {
          margin-top: 8px;
        }
        .admin-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
          margin: 12px 0;
        }
        .clear-box {
          display: flex;
          gap: 10px;
          align-items: center;
          flex-wrap: wrap;
        }
        .clear-label {
          font-size: 12px;
          color: #5a6a66;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .input {
          border: 1px solid rgba(25, 32, 30, 0.14);
          border-radius: 10px;
          padding: 8px 10px;
          background: #fbfbf9;
          min-width: 180px;
        }
        .btn {
          border: none;
          border-radius: 999px;
          padding: 10px 16px;
          font-weight: 700;
          cursor: pointer;
          background: #1f2a2a;
          color: #f7f2e7;
        }
        .btn.ghost {
          background: #eef2f0;
          color: #1f2a2a;
        }
        .btn.danger {
          background: #b94014;
          color: #fff7f2;
        }
        .btn:disabled {
          cursor: not-allowed;
          opacity: 0.65;
        }
        .steps h3 {
          margin-top: 18px;
          margin-bottom: 8px;
        }
        ol {
          padding-left: 20px;
          margin: 6px 0 12px;
        }
        pre {
          background: #f4f7f6;
          padding: 10px 12px;
          border-radius: 10px;
          font-size: 12px;
          white-space: pre-wrap;
          border: 1px solid rgba(25, 32, 30, 0.08);
        }
        @media (max-width: 980px) {
          .card-wide {
            grid-column: auto;
          }
          .ocr-card-top {
            grid-template-columns: 1fr;
          }
          .ocr-queue-summary {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </main>
  );
}
