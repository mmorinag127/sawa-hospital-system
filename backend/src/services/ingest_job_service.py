from datetime import datetime, timedelta
import os
from typing import Any

from sqlalchemy import select, update, func, desc

from src.db import session_scope
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.services.ocr_job_service import get_job_stale_at, is_job_stale


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


def get_ingest_job(job_id: str) -> IngestJob | None:
    with session_scope() as session:
        return session.get(IngestJob, job_id)


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
    with session_scope() as session:
        rows = (
            session.execute(
                select(IngestJob.id)
                .where(
                    (IngestJob.status.in_(["pending", "error"]))
                    | ((IngestJob.status == "processing") & (IngestJob.started_at < stale_before))
                )
                .order_by(IngestJob.created_at.asc())
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
    now = datetime.utcnow()
    with session_scope() as session:
        rows = session.execute(
            select(IngestJob.status, func.count(IngestJob.id))
            .group_by(IngestJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        oldest_pending_at = session.execute(
            select(func.min(IngestJob.created_at)).where(IngestJob.status.in_(["pending", "error"]))
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
        eligible_backlog_count = (
            counts.get("pending", 0)
            + counts.get("error", 0)
            + stale_processing_count
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
    ingest_summary = summarize_ingest_jobs()
    now = datetime.utcnow()
    recent_since = now - timedelta(hours=int(os.getenv("OCR_BACKLOG_RECENT_HOURS", "24")))
    with session_scope() as session:
        rows = session.execute(
            select(OcrJob.status, func.count(OcrJob.id)).group_by(OcrJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        active_count = counts.get("running", 0) + counts.get("pending", 0)
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
            }
            for job in (
                session.execute(
                    select(OcrJob).where(OcrJob.status.in_(["running", "pending"]))
                )
                .scalars()
                .all()
            )
        ]
    stale_jobs = [job for job in active_jobs if is_job_stale(job)]
    stale_oldest_seconds = None
    if stale_jobs:
        stale_ats = [get_job_stale_at(job) for job in stale_jobs]
        stale_ats = [value for value in stale_ats if value is not None]
        if stale_ats:
            oldest_stale_at = min(stale_ats)
            stale_oldest_seconds = max(int((now - oldest_stale_at).total_seconds()), 0)
    ingest_queue_depth = int(ingest_summary.get("eligible_backlog_count") or 0)
    oldest_pending_seconds = ingest_summary.get("oldest_pending_seconds")
    status = "ok"
    if ingest_queue_depth > 0 or active_count > 0:
        status = "warn"
    if recent_backlog_skipped_count > 0:
        status = "fail"
    return {
        "status": status,
        "ingest_queue_depth": ingest_queue_depth,
        "ocr_queue_depth": active_count,
        "exports_queue_depth": 0,
        "oldest_pending_seconds": oldest_pending_seconds or 0,
        "ingest": ingest_summary,
        "ocr": {
            "total": sum(counts.values()),
            "counts": counts,
            "active_count": active_count,
            "running_count": counts.get("running", 0),
            "pending_count": counts.get("pending", 0),
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
