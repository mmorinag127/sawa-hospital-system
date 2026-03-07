from datetime import datetime, timedelta
import os
from typing import Any

from sqlalchemy import select, update, func, desc

from src.db import session_scope
from src.models.ingest_job import IngestJob


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
    with session_scope() as session:
        rows = session.execute(
            select(IngestJob.status, func.count(IngestJob.id))
            .group_by(IngestJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        return {
            "total": sum(counts.values()),
            "counts": counts,
        }
