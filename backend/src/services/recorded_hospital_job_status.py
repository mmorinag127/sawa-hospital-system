from typing import Any


def _recorded_hospital_job_status(job: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    job_id = job.get("id")
    if job_id is None or job_id == "":
        return None
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    execution = metrics.get("nonworkflow_execution")
    workflow_attempt = metrics.get("workflow_attempt_id")
    attempt = None
    if workflow_attempt is not None and execution is None:
        attempt = workflow_attempt
    elif workflow_attempt is None and isinstance(execution, dict):
        if execution.get("job_id") == job_id and execution.get("order_id") == job.get("order_id"):
            attempt = execution.get("execution_id")
    source_state = state.get("status")
    labels = {"idle": "未実行", "queued": "待機中", "pending": "待機中", "running": "実行中",
              "done": "完了", "awaiting_output": "成果物待ち", "recovering": "復旧中",
              "stalled": "停止", "hard_failed": "失敗", "failed": "失敗"}
    common_state = {"pending": "pending", "queued": "queued", "running": "running",
                    "hard_failed": "failed", "failed": "failed"}.get(source_state)
    if source_state == "done" and job.get("status") == "done":
        common_state = "succeeded"
    error = metrics.get("error")
    if error is None:
        error = job.get("error_message")
    return {
        "domain": "hospital", "jobId": job_id,
        "inputVersion": job.get("input_artifact_digest"),
        "inputReference": job.get("input_reference"), "attemptIdentity": attempt,
        "state": common_state, "sourceState": job.get("status"),
        "sourceLabel": labels.get(source_state, source_state or "状態未記録"),
        "failure": {"code": None, "message": error} if error is not None else None,
        "artifactReference": job.get("output_reference"),
    }
