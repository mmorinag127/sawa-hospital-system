from fastapi import APIRouter, HTTPException, status, Depends

from src.workers.ingest_worker import enqueue_ingest_async, enqueue_ingest_job_async
from src.services.ingest_job_service import list_pending_jobs
from src.api.auth import require_role

router = APIRouter()


@router.post("/email", status_code=status.HTTP_202_ACCEPTED)
def ingest_email(payload: dict):
    required = {"message_id", "pdf_uri", "received_at"}
    if not required.issubset(payload):
        raise HTTPException(status_code=400, detail="missing fields")
    enqueue_ingest_async(payload)
    return {"accepted": True}


@router.post("/retry", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def retry_pending_jobs(limit: int = 10):
    job_ids = list_pending_jobs(limit=limit)
    for job_id in job_ids:
        enqueue_ingest_job_async(job_id)
    return {"accepted": len(job_ids)}
