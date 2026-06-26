from datetime import datetime, timedelta
import os
from typing import Any

from sqlalchemy import and_, or_, select, update, func, desc

from src.db import session_scope
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.services.ocr_job_service import (
    get_job_stale_at,
    is_job_recoverable,
    is_job_stale,
)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    received_at = normalized.get("received_at")
    if hasattr(received_at, "isoformat"):
        normalized["received_at"] = received_at.isoformat()
    return normalized


def create_ingest_job(payload: dict[str, Any], force: bool = False) -> tuple[str, bool]:
    normalized = _normalize_payload(payload)
    job_id = normalized["message_id"]
    now = datetime.utcnow()
    should_enqueue = True
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if job:
            if force:
                job.status = "pending"
                job.started_at = None
                job.finished_at = None
                job.last_error = None
                should_enqueue = True
            else:
                if job.status == "done":
                    should_enqueue = False
                elif job.status == "processing":
                    should_enqueue = False
                elif job.status != "processing":
                    job.status = "pending"
            job.payload = normalized
            job.updated_at = now
        else:
            session.add(
                IngestJob(
                    id=job_id,
                    status="pending",
                    payload=normalized,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
    return job_id, should_enqueue


def restart_ingest_job(job_id: str) -> bool:
    now = datetime.utcnow()
    with session_scope() as session:
        result = session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .values(
                status="pending",
                started_at=None,
                finished_at=None,
                updated_at=now,
                last_error=None,
            )
        )
        return result.rowcount > 0


def get_ingest_job(job_id: str) -> IngestJob | None:
    with session_scope() as session:
        return session.get(IngestJob, job_id)


def get_ingest_job_snapshot(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return None
        payload = job.payload or {}
        return {
            "id": job.id,
            "status": job.status,
            "attempts": job.attempts,
            "last_error": job.last_error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "message_id": payload.get("message_id"),
            "pdf_uri": payload.get("pdf_uri"),
            "received_at": payload.get("received_at"),
        }


def is_processing_snapshot_stale(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if str(snapshot.get("status") or "").strip().lower() != "processing":
        return False
    started_at_raw = snapshot.get("started_at")
    if not started_at_raw:
        return True
    try:
        started_at = datetime.fromisoformat(str(started_at_raw))
    except ValueError:
        return True
    return started_at < _stale_threshold()


def get_ingest_payload(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if not job:
            return None
        payload = job.payload or {}
        return dict(payload)


def _stale_threshold() -> datetime:
    minutes = int(os.getenv("INGEST_JOB_STALE_MINUTES", "30"))
    return datetime.utcnow() - timedelta(minutes=minutes)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(value, minimum)


def _auto_recovery_window_hours() -> int:
    return _env_int("INGEST_JOB_AUTO_RECOVERY_WINDOW_HOURS", 24)


def _auto_recovery_cutoff() -> datetime:
    return datetime.utcnow() - timedelta(hours=_auto_recovery_window_hours())


def _max_auto_attempts() -> int:
    return _env_int("INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", 3)


def _eligible_ingest_job_condition(stale_before: datetime, recovery_cutoff: datetime):
    max_attempts = _max_auto_attempts()
    return or_(
        and_(
            IngestJob.status == "pending",
            IngestJob.updated_at >= recovery_cutoff,
            IngestJob.attempts < max_attempts,
        ),
        and_(
            IngestJob.status == "error",
            IngestJob.updated_at >= recovery_cutoff,
            IngestJob.attempts < max_attempts,
        ),
        and_(
            IngestJob.status == "processing",
            IngestJob.started_at < stale_before,
            IngestJob.attempts < max_attempts,
        ),
    )


def claim_ingest_job(job_id: str) -> bool:
    now = datetime.utcnow()
    stale_before = _stale_threshold()
    with session_scope() as session:
        result = session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .where(
                (IngestJob.status.in_(["pending", "error"]))
                | ((IngestJob.status == "processing") & (IngestJob.started_at < stale_before))
            )
            .values(
                status="processing",
                attempts=IngestJob.attempts + 1,
                started_at=now,
                updated_at=now,
            )
        )
        return result.rowcount > 0


def complete_ingest_job(job_id: str) -> None:
    now = datetime.utcnow()
    with session_scope() as session:
        session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .values(status="done", finished_at=now, updated_at=now, last_error=None)
        )


def fail_ingest_job(job_id: str, error_message: str) -> None:
    now = datetime.utcnow()
    with session_scope() as session:
        session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .values(status="error", updated_at=now, last_error=error_message)
        )


def list_pending_jobs(limit: int = 10) -> list[str]:
    stale_before = _stale_threshold()
    recovery_cutoff = _auto_recovery_cutoff()
    with session_scope() as session:
        rows = (
            session.execute(
                select(IngestJob.id)
                .where(_eligible_ingest_job_condition(stale_before, recovery_cutoff))
                .order_by(IngestJob.updated_at.desc(), IngestJob.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)


def list_jobs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        query = select(IngestJob).order_by(desc(IngestJob.updated_at))
        if status:
            query = query.where(IngestJob.status == status)
        rows = session.execute(query.limit(limit)).scalars().all()
        items: list[dict[str, Any]] = []
        for job in rows:
            payload = job.payload or {}
            items.append(
                {
                    "id": job.id,
                    "status": job.status,
                    "attempts": job.attempts,
                    "last_error": job.last_error,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "message_id": payload.get("message_id"),
                    "pdf_uri": payload.get("pdf_uri"),
                    "received_at": payload.get("received_at"),
                }
            )
        return items


def reset_stale_processing(minutes: int | None = None, limit: int = 100) -> list[str]:
    if minutes is None:
        minutes = int(os.getenv("INGEST_JOB_STALE_MINUTES", "30"))
    stale_before = datetime.utcnow() - timedelta(minutes=minutes)
    now = datetime.utcnow()
    with session_scope() as session:
        stale_ids = (
            session.execute(
                select(IngestJob.id)
                .where((IngestJob.status == "processing") & (IngestJob.started_at < stale_before))
                .order_by(IngestJob.started_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        if not stale_ids:
            return []
        session.execute(
            update(IngestJob)
            .where(IngestJob.id.in_(stale_ids))
            .values(status="pending", updated_at=now)
        )
        return list(stale_ids)


def summarize_ingest_jobs() -> dict[str, Any]:
    stale_before = _stale_threshold()
    recovery_cutoff = _auto_recovery_cutoff()
    max_attempts = _max_auto_attempts()
    now = datetime.utcnow()
    with session_scope() as session:
        rows = session.execute(
            select(IngestJob.status, func.count(IngestJob.id))
            .group_by(IngestJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        eligible_condition = _eligible_ingest_job_condition(stale_before, recovery_cutoff)
        oldest_pending_at = session.execute(
            select(func.min(IngestJob.created_at)).where(eligible_condition)
        ).scalar_one_or_none()
        oldest_processing_at = session.execute(
            select(func.min(IngestJob.started_at)).where(IngestJob.status == "processing")
        ).scalar_one_or_none()
        stale_processing_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(
                    (IngestJob.status == "processing") & (IngestJob.started_at < stale_before)
                )
            ).scalar_one()
            or 0
        )
        eligible_backlog_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(eligible_condition)
            ).scalar_one()
            or 0
        )
        blocked_old_pending_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(
                    IngestJob.status.in_(["pending", "error"]),
                    IngestJob.updated_at < recovery_cutoff,
                )
            ).scalar_one()
            or 0
        )
        blocked_attempt_exhausted_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(
                    IngestJob.status.in_(["pending", "error", "processing"]),
                    IngestJob.attempts >= max_attempts,
                )
            ).scalar_one()
            or 0
        )
        return {
            "total": sum(counts.values()),
            "counts": counts,
            "pending_count": counts.get("pending", 0),
            "error_count": counts.get("error", 0),
            "processing_count": counts.get("processing", 0),
            "done_count": counts.get("done", 0),
            "stale_processing_count": stale_processing_count,
            "eligible_backlog_count": eligible_backlog_count,
            "blocked_old_pending_count": blocked_old_pending_count,
            "blocked_attempt_exhausted_count": blocked_attempt_exhausted_count,
            "auto_recovery_window_hours": _auto_recovery_window_hours(),
            "auto_recovery_max_attempts": max_attempts,
            "oldest_pending_at": oldest_pending_at.isoformat() if oldest_pending_at else None,
            "oldest_pending_seconds": (
                max(int((now - oldest_pending_at).total_seconds()), 0) if oldest_pending_at else None
            ),
            "oldest_processing_at": oldest_processing_at.isoformat() if oldest_processing_at else None,
            "oldest_processing_seconds": (
                max(int((now - oldest_processing_at).total_seconds()), 0)
                if oldest_processing_at
                else None
            ),
        }


def summarize_backlog_metrics() -> dict[str, Any]:
    from src.services.uploaded_pdf_service import summarize_uploaded_pdfs

    ingest_summary = summarize_ingest_jobs()
    uploaded_pdf_summary = summarize_uploaded_pdfs()
    now = datetime.utcnow()
    recent_since = now - timedelta(hours=int(os.getenv("OCR_BACKLOG_RECENT_HOURS", "24")))
    with session_scope() as session:
        rows = session.execute(
            select(OcrJob.status, func.count(OcrJob.id)).group_by(OcrJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        active_count = counts.get("running", 0) + counts.get("pending", 0)
        awaiting_output_count = counts.get("awaiting_output", 0)
        recovering_count = counts.get("recovering", 0)
        oldest_active_at = session.execute(
            select(func.min(OcrJob.created_at)).where(OcrJob.status.in_(["running", "pending"]))
        ).scalar_one_or_none()
        recent_backlog_skipped_count = int(
            session.execute(
                select(func.count(OcrJob.id)).where(
                    (OcrJob.error_message == "backlog_skipped") & (OcrJob.updated_at >= recent_since)
                )
            ).scalar_one()
            or 0
        )
        recent_failed_count = int(
            session.execute(
                select(func.count(OcrJob.id)).where(
                    (OcrJob.status == "failed") & (OcrJob.updated_at >= recent_since)
                )
            ).scalar_one()
            or 0
        )
        active_jobs = [
            {
                "id": job.id,
                "status": job.status,
                "metrics": job.metrics,
                "updated_at": job.updated_at,
                "error_message": job.error_message,
                "output_reference": job.output_reference,
            }
            for job in (
                session.execute(
                    select(OcrJob).where(OcrJob.status.in_(["running", "pending"]))
                )
                .scalars()
                .all()
            )
        ]
        recoverable_jobs = [
            {
                "id": job.id,
                "status": job.status,
                "metrics": job.metrics,
                "updated_at": job.updated_at,
                "error_message": job.error_message,
                "output_reference": job.output_reference,
            }
            for job in (
                session.execute(
                    select(OcrJob).where(
                        OcrJob.status.in_(["awaiting_output", "recovering", "failed", "error", "running", "pending"])
                    )
                )
                .scalars()
                .all()
            )
        ]
    recoverable_jobs = [job for job in recoverable_jobs if is_job_recoverable(job)]
    active_jobs = [job for job in active_jobs if not is_job_recoverable(job)]
    active_count = len(active_jobs)
    stale_jobs = [job for job in active_jobs if is_job_stale(job)]
    stale_oldest_seconds = None
    if stale_jobs:
        stale_ats = [get_job_stale_at(job) for job in stale_jobs]
        stale_ats = [value for value in stale_ats if value is not None]
        if stale_ats:
            oldest_stale_at = min(stale_ats)
            stale_oldest_seconds = max(int((now - oldest_stale_at).total_seconds()), 0)
    ingest_queue_depth = int(ingest_summary.get("eligible_backlog_count") or 0)
    uploaded_pdf_queue_depth = int(uploaded_pdf_summary.get("eligible_backlog_count") or 0)
    oldest_pending_seconds = ingest_summary.get("oldest_pending_seconds")
    status = "ok"
    if ingest_queue_depth > 0 or uploaded_pdf_queue_depth > 0 or active_count > 0:
        status = "warn"
    if recent_backlog_skipped_count > 0:
        status = "fail"
    return {
        "status": status,
        "ingest_queue_depth": ingest_queue_depth,
        "uploaded_pdf_queue_depth": uploaded_pdf_queue_depth,
        "ocr_queue_depth": active_count,
        "exports_queue_depth": 0,
        "oldest_pending_seconds": oldest_pending_seconds or 0,
        "ingest": ingest_summary,
        "uploaded_pdfs": uploaded_pdf_summary,
        "ocr": {
            "total": sum(counts.values()),
            "counts": counts,
            "active_count": active_count,
            "running_count": counts.get("running", 0),
            "pending_count": counts.get("pending", 0),
            "awaiting_output_count": awaiting_output_count,
            "recovering_count": recovering_count,
            "recoverable_count": len(recoverable_jobs),
            "failed_count": counts.get("failed", 0),
            "done_count": counts.get("done", 0),
            "completed_count": counts.get("completed", 0),
            "oldest_active_at": oldest_active_at.isoformat() if oldest_active_at else None,
            "oldest_active_seconds": (
                max(int((now - oldest_active_at).total_seconds()), 0) if oldest_active_at else None
            ),
            "recent_backlog_skipped_count": recent_backlog_skipped_count,
            "recent_failed_count": recent_failed_count,
            "recent_window_hours": int(os.getenv("OCR_BACKLOG_RECENT_HOURS", "24")),
            "stale_count": len(stale_jobs),
            "stale_oldest_seconds": stale_oldest_seconds,
        },
    }
