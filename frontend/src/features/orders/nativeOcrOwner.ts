export type NativeOcrOwner = { executionKind: "native"; jobId: string; executionId: string };

export function nativeOcrOwner(job: {
  ocr_job_id?: unknown; order_id?: unknown; workflow_attempt_id?: unknown; native_ingest?: unknown;
} | null | undefined, orderId: string): NativeOcrOwner | null {
  const native = job?.native_ingest;
  if (!job || job.order_id !== orderId || typeof job.ocr_job_id !== "string" || !job.ocr_job_id.trim()
      || job.workflow_attempt_id != null || !native || typeof native !== "object" || Array.isArray(native)) return null;
  const owner = native as Record<string, unknown>;
  if (owner.mode !== "hakodate" || owner.intake_protocol !== "native_ingest_v2"
      || typeof owner.execution_id !== "string" || !owner.execution_id.trim()) return null;
  return { executionKind: "native", jobId: job.ocr_job_id, executionId: owner.execution_id };
}
