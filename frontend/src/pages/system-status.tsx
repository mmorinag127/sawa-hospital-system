import { useEffect, useState } from "react";
import Link from "next/link";
import TopNav from "../components/TopNav";
import { apiClient } from "../services/apiClient";
import GmailInvalidGrantRecoverySteps from "../components/GmailInvalidGrantRecoverySteps";

type SystemStatus = {
  gmail_watch?: {
    status?: string | null;
    expiration_iso?: string | null;
    updated_at?: string | null;
    error_code?: string | null;
  };
  gmail_config?: {
    configured?: boolean;
    client_id_set?: boolean;
    client_secret_set?: boolean;
    refresh_token_set?: boolean;
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
  if (raw === "invalid_grant") return "失効";
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

export default function SystemStatusPage() {
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

  return (
    <main className="system-page">
      <header className="hero">
        <div>
          <p className="eyebrow">System Operations</p>
          <h1>システム状態と復帰手順</h1>
          <p className="subtle">
            Gmail自動取込・Web OAuth・OCRパイプラインの状態と復帰手順を確認します。
          </p>
        </div>
        <TopNav />
      </header>

      {error && <p className="error">{error}</p>}

      <section className="status-grid">
        <article className="card">
          <h2>Gmail Watch</h2>
          <p className="value">{formatStatus(status?.gmail_watch?.status)}</p>
          <p className="meta">最終更新: {formatDate(status?.gmail_watch?.updated_at)}</p>
          <p className="meta">有効期限: {status?.gmail_watch?.expiration_iso ?? "未取得"}</p>
          {status?.gmail_watch?.error_code && (
            <p className="meta warn">エラー: {status.gmail_watch.error_code}</p>
          )}
        </article>
        <article className="card">
          <h2>Gmail 設定</h2>
          <p className="value">{status?.gmail_config?.configured ? "OK" : "未設定"}</p>
          <p className="meta">client_id: {status?.gmail_config?.client_id_set ? "OK" : "NG"}</p>
          <p className="meta">
            client_secret: {status?.gmail_config?.client_secret_set ? "OK" : "NG"}
          </p>
          <p className="meta">
            refresh_token: {status?.gmail_config?.refresh_token_set ? "OK" : "NG"}
          </p>
        </article>
        <article className="card">
          <h2>Web OAuth</h2>
          <p className="value">{status?.oauth_config?.configured ? "OK" : "未設定"}</p>
          <p className="meta">許可されたクライアントID: {(status?.oauth_config?.google_client_ids || []).join(", ") || "未取得"}</p>
        </article>
        <article className="card">
          <h2>OCR パイプライン</h2>
          <p className="value">
            {status?.ocr_pipeline?.configured
              ? formatStatus(status?.ocr_pipeline?.status)
              : "未設定"}
          </p>
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
          <button className="btn ghost" type="button" onClick={() => downloadDb(false)} disabled={maintenanceBusy}>
            DBをダウンロード
          </button>
          <button className="btn ghost" type="button" onClick={() => downloadDb(true)} disabled={maintenanceBusy}>
            スナップショットZIP
          </button>
        </div>
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
        {maintenanceMessage ? <p className="meta warn">{maintenanceMessage}</p> : null}
      </section>

      <section className="panel">
        <h2>復帰手順（詳細）</h2>
        <p className="meta">
          UIのみの完全手順:{" "}
          <Link href="/system-recovery-runbook">/system-recovery-runbook</Link>
        </p>

        <div className="steps">
          <h3>1. Gmail Watch の再設定（invalid_grant）</h3>
          <GmailInvalidGrantRecoverySteps />

          <h3>2. Web OAuth の復帰（ログインエラー時）</h3>
          <ol>
            <li>Google Cloud Console →「API とサービス」→「認証情報」へ移動。</li>
            <li>「ウェブ クライアント 1」を開く。</li>
            <li>「承認済みの JavaScript 生成元」に以下があることを確認（両方あるのが安全）。</li>
          </ol>
          <pre>{`https://web-prod-avlnzjjrca-dt.a.run.app
https://web-prod-167795504375.asia-northeast2.run.app`}</pre>
          <ol>
            <li>変更を保存 → ブラウザを再ログイン。</li>
          </ol>

          <h3>3. OCR パイプライン復帰</h3>
          <ol>
            <li>Cloud Run → `ocr-pipeline-prod` の稼働状況を確認。</li>
            <li>Worker の環境変数に `OCR_PIPELINE_URL` と `OCR_PIPELINE_BUCKET` があるか確認。</li>
            <li>`/system/status` の `ocr_pipeline.last_error` を確認。</li>
            <li>必要なら `ocr-pipeline-prod` の最小インスタンスを 1 に戻す。</li>
          </ol>
        </div>
      </section>

      <style jsx>{`
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
        .quota-level.critical {
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
      `}</style>
    </main>
  );
}
