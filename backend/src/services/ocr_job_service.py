from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from src.db import session_scope
from src.models.ocr_job import OcrJob


def _job_to_dict(job: OcrJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "input_reference": job.input_reference,
        "template_id": job.template_id,
        "output_reference": job.output_reference,
        "metrics": job.metrics,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def get_stale_minutes() -> int:
    raw = str(os.getenv("OCR_JOB_STALE_MINUTES", "30") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 30


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
    if normalized_status in {"running", "pending"} and is_job_stale(job):
        effective_status = "stalled"
    elif normalized_status in {"failed", "error"}:
        effective_status = "hard_failed"
    elif normalized_status in {"done", "success", "completed"}:
        effective_status = "done"
    progress_updated_at = get_job_progress_updated_at(job)
    stale_at = get_job_stale_at(job) if effective_status == "stalled" else None
    return {
        "status": effective_status,
        "progress_updated_at": progress_updated_at.isoformat() if isinstance(progress_updated_at, datetime) else None,
        "stale_at": stale_at.isoformat() if isinstance(stale_at, datetime) else None,
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
