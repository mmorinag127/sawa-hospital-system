from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from src.db import session_scope
from src.models.ocr_job import OcrJob

_RECOVERABLE_STATUSES = {"awaiting_output", "recovering"}
_RECOVERABLE_RESULT_STATES = {"awaiting_output", "recovering"}
_ORDER_REPARSE_REQUEST_MODES = {"ocr_rerun", "ocr_reparse", "llm_reparse"}


def _job_to_dict(job: OcrJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "input_reference": job.input_reference,
        "template_id": job.template_id,
        "template_version_id": job.template_version_id,
        "output_reference": job.output_reference,
        "metrics": job.metrics,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def get_job_request_mode(job: dict[str, Any] | None) -> str:
    if not isinstance(job, dict):
        return ""
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    direct_mode = str(metrics.get("request_mode") or metrics.get("rerun_mode") or "").strip().lower()
    if direct_mode:
        return direct_mode
    quality_track = str(metrics.get("quality_track") or "").strip().lower()
    if quality_track == "llm_reparse":
        return "llm_reparse"
    if quality_track == "non_llm_reparse":
        return "ocr_reparse"
    return ""


def is_order_reparse_job(job: dict[str, Any] | None, order_id: str) -> bool:
    if not isinstance(job, dict):
        return False
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return False
    normalized_job_id = str(job.get("id") or "").strip()
    if normalized_job_id != f"OCR-{normalized_order_id}":
        return False
    return get_job_request_mode(job) in _ORDER_REPARSE_REQUEST_MODES


def get_stale_minutes() -> int:
    raw = str(os.getenv("OCR_JOB_STALE_MINUTES", "30") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 30


def get_auto_recovery_interval_seconds() -> int:
    raw = str(os.getenv("OCR_JOB_AUTO_RECOVERY_INTERVAL_SECONDS", "120") or "").strip()
    try:
        return max(int(raw), 5)
    except ValueError:
        return 120


def get_auto_recovery_max_attempts() -> int:
    raw = str(os.getenv("OCR_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", "3") or "").strip()
    try:
        return max(int(raw), 0)
    except ValueError:
        return 3


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def get_job_progress_updated_at(job: dict[str, Any] | None) -> datetime | None:
    if not isinstance(job, dict):
        return None
    metrics = job.get("metrics")
    if isinstance(metrics, dict):
        stage_updated_at = _coerce_datetime(metrics.get("stage_updated_at"))
        if isinstance(stage_updated_at, datetime):
            return stage_updated_at
    return _coerce_datetime(job.get("updated_at"))


def get_job_recovery_attempts(job: dict[str, Any] | None) -> int:
    if not isinstance(job, dict):
        return 0
    metrics = job.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    raw = metrics.get("auto_recovery_count")
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def get_job_next_recovery_at(job: dict[str, Any] | None) -> datetime | None:
    if not isinstance(job, dict):
        return None
    metrics = job.get("metrics")
    if isinstance(metrics, dict):
        next_retry_at = _coerce_datetime(metrics.get("next_recovery_at"))
        if isinstance(next_retry_at, datetime):
            return next_retry_at
    progress_updated_at = get_job_progress_updated_at(job)
    if not isinstance(progress_updated_at, datetime):
        return None
    return progress_updated_at + timedelta(seconds=get_auto_recovery_interval_seconds())


def is_job_recoverable(job: dict[str, Any] | None) -> bool:
    if not isinstance(job, dict):
        return False
    normalized_status = str(job.get("status") or "").strip().lower()
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    result_state = str(metrics.get("result_state") or "").strip().lower()
    processing_stage = str(metrics.get("processing_stage") or "").strip().lower()
    error_message = str(job.get("error_message") or "").strip().lower()
    output_reference = str(job.get("output_reference") or "").strip()
    if normalized_status in _RECOVERABLE_STATUSES:
        return True
    if (
        normalized_status in {"running", "pending"}
        and is_job_stale(job)
        and (
            processing_stage == "ocr_pipeline"
            or result_state in {"processing", "awaiting_output"}
            or bool(output_reference)
        )
    ):
        return True
    if normalized_status in {"failed", "error"} and (
        result_state in _RECOVERABLE_RESULT_STATES
        or "ocr pipeline output not found" in error_message
    ):
        return True
    return False


def is_job_recovery_due(job: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    if not is_job_recoverable(job):
        return False
    due_at = get_job_next_recovery_at(job)
    if not isinstance(due_at, datetime):
        return True
    now_value = now or datetime.utcnow()
    return due_at <= now_value


def get_job_stale_at(job: dict[str, Any] | None) -> datetime | None:
    progress_updated_at = get_job_progress_updated_at(job)
    stale_minutes = get_stale_minutes()
    if stale_minutes <= 0 or not isinstance(progress_updated_at, datetime):
        return None
    return progress_updated_at + timedelta(minutes=stale_minutes)


def is_job_stale(job: dict[str, Any] | None) -> bool:
    stale_at = get_job_stale_at(job)
    if not isinstance(stale_at, datetime):
        return False
    return stale_at <= datetime.utcnow()


def describe_job_state(job: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {
            "status": "idle",
            "progress_updated_at": None,
            "stale_at": None,
            "stale_threshold_seconds": max(get_stale_minutes(), 0) * 60,
            "job_id": None,
        }
    normalized_status = str(job.get("status") or "").strip().lower() or "idle"
    effective_status = normalized_status
    if is_job_recoverable(job):
        effective_status = "recovering" if is_job_recovery_due(job) else "awaiting_output"
    elif normalized_status in {"running", "pending"} and is_job_stale(job):
        effective_status = "stalled"
    elif normalized_status in {"failed", "error"}:
        effective_status = "hard_failed"
    elif normalized_status in {"done", "success", "completed"}:
        effective_status = "done"
    progress_updated_at = get_job_progress_updated_at(job)
    stale_at = get_job_stale_at(job) if effective_status == "stalled" else None
    recovery_due_at = get_job_next_recovery_at(job) if is_job_recoverable(job) else None
    return {
        "status": effective_status,
        "progress_updated_at": progress_updated_at.isoformat() if isinstance(progress_updated_at, datetime) else None,
        "stale_at": stale_at.isoformat() if isinstance(stale_at, datetime) else None,
        "recovery_due_at": (
            recovery_due_at.isoformat() if isinstance(recovery_due_at, datetime) else None
        ),
        "recovery_attempts": get_job_recovery_attempts(job),
        "stale_threshold_seconds": max(get_stale_minutes(), 0) * 60,
        "job_id": str(job.get("id") or "").strip() or None,
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        if not job:
            return None
        return _job_to_dict(job)


def get_jobs(job_ids: list[str]) -> dict[str, dict[str, Any]]:
    unique_ids = [job_id for job_id in dict.fromkeys(job_ids) if job_id]
    if not unique_ids:
        return {}
    with session_scope() as session:
        jobs = session.execute(
            select(OcrJob).where(OcrJob.id.in_(unique_ids))
        ).scalars().all()
        return {job.id: _job_to_dict(job) for job in jobs}


def list_recoverable_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        jobs = (
            session.execute(
                select(OcrJob)
                .where(OcrJob.status.in_(["awaiting_output", "recovering", "failed", "error", "running", "pending"]))
                .order_by(OcrJob.updated_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            serialized
            for serialized in (_job_to_dict(job) for job in jobs)
            if is_job_recoverable(serialized)
        ]


def create_job(job_id: str, input_reference: str, status: str = "running") -> tuple[dict[str, Any], bool]:
    with session_scope() as session:
        existing = session.get(OcrJob, job_id)
        if existing:
            return _job_to_dict(existing), False
        now = datetime.utcnow()
        job = OcrJob(
            id=job_id,
            status=status,
            input_reference=input_reference,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        return _job_to_dict(job), True


def update_job(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        if not job:
            return None
        for key, value in updates.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        session.add(job)
        return _job_to_dict(job)
