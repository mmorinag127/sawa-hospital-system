from __future__ import annotations

from datetime import datetime
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
