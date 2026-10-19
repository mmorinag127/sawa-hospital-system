import { apiClient } from "./apiClient";

export class BulkOcrCommandFailure extends Error {
  constructor(message: string, readonly terminal: boolean) { super(message); }
}

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("OCR応答の識別情報がありません。");
  return value as Record<string, unknown>;
};
const revision = (value: unknown): number => {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("注文のrevisionを確認できません。");
  return value;
};
const identity = (value: unknown): string => {
  if (typeof value !== "string" || !value.trim() || value !== value.trim()) throw new Error("OCR実行の識別情報がありません。");
  return value;
};
const workflow = (value: unknown, orderId: string) => {
  const data = object(value);
  if (data.order_id !== orderId) throw new Error("OCR応答の注文が一致しません。");
  revision(data.revision);
  return data;
};

// One accepted workflow attempt, never the mutable OCR job ID alone.
export async function runBulkOcrOrder(orderId: string, current: () => boolean, signal: AbortSignal) {
  const check = () => {
    if (signal.aborted || !current()) throw new Error("OCR一括操作の所有権が終了しました。");
  };
  const url = `/orders/${encodeURIComponent(orderId)}/workflow-v2`;
  check();
  const before = await apiClient.get(url, { timeout: 60000, signal });
  check();
  const initial = workflow(before.data, orderId);
  const expected = revision(initial.revision);
  if (expected === 0 && (initial.state !== "not_initialized" || initial.source !== "workflow_v2_projection")) {
    throw new Error("注文のbootstrap revisionを確認できません。");
  }
  const response = await apiClient.post(`${url}/ocr-runs`, { mode: "hakodate", stale_action: "retry" },
    { timeout: 60000, signal, headers: { "If-Match": String(expected) } });
  check();
  const accepted = object(response.data);
  if (accepted.accepted !== true) throw new Error("OCR実行の受付を確認できません。");
  const queued = workflow(accepted.workflow, orderId);
  const job = object(queued.ocr_job);
  const jobId = identity(accepted.ocr_job_id);
  const attempt = identity(job.workflow_attempt_id);
  const acceptedRevision = revision(queued.revision);
  if (job.ocr_job_id !== jobId || job.order_id !== orderId || "native_ingest" in job
      || acceptedRevision < 1 || acceptedRevision < expected || job.workflow_completion_revision !== acceptedRevision) {
    throw new Error("受付済みOCR実行の識別情報が一致しません。");
  }
  const startedAt = Date.now();
  while (Date.now() - startedAt < 20 * 60 * 1000) {
    check();
    const result = await apiClient.get(url, { timeout: 60000, signal });
    check();
    if (Date.now() - startedAt >= 20 * 60 * 1000) throw new Error("OCR再実行の完了待ちがタイムアウトしました。");
    const state = workflow(result.data, orderId);
    const recorded = object(state.ocr_job);
    const common = object(state.common_job_status);
    if (recorded.ocr_job_id !== jobId || recorded.order_id !== orderId
        || recorded.workflow_attempt_id !== attempt || "native_ingest" in recorded
        || common.domain !== "hospital" || common.jobId !== jobId || common.attemptIdentity !== attempt
        || common.sourceState !== recorded.status
        || revision(state.revision) < acceptedRevision
        || revision(recorded.workflow_completion_revision) < acceptedRevision
        || revision(recorded.workflow_completion_revision) > revision(state.revision)) {
      throw new Error("待機中のOCR実行が変更されています。");
    }
    // Selection/sheet/apply blockers are not failed OCR executions.
    if (!Array.isArray(state.blockers) || ["ocr_failed", "ocr_blocked", "template_version_required",
      "template_version_mismatch", "facility_template_unresolved", "context_not_confirmed"].includes(String(state.state))
        || recorded.status === "failed" || common.state === "failed"
        || recorded.error || recorded.error_message || recorded.error_detail || common.failure !== null) {
      throw new BulkOcrCommandFailure(String(recorded.error_user_message || recorded.error_message || recorded.error
        || state.headline || "OCR処理が停止しました。"),
      (recorded.status === "failed" && common.state === "failed")
        || (recorded.status === "done" && common.state === "succeeded"));
    }
    if (recorded.status === "done") {
      if (common.state !== "succeeded" || common.sourceState !== "done"
          || revision(recorded.workflow_completion_revision) > revision(state.revision)
          || state.state === "ocr_running") throw new Error("OCR実行の正常完了を確認できません。");
      return;
    }
    if (!["running", "pending", "queued", "awaiting_output", "recovering"].includes(String(recorded.status))) {
      throw new Error("OCR実行の状態を確認できません。");
    }
    await new Promise<void>((resolve, reject) => {
      const aborted = () => { window.clearTimeout(timer); reject(new Error("OCR一括操作の所有権が終了しました。")); };
      const timer = window.setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, 5000);
      signal.addEventListener("abort", aborted, { once: true });
    });
  }
  throw new Error("OCR再実行の完了待ちがタイムアウトしました。");
}
