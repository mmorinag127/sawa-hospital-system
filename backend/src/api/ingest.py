from fastapi import APIRouter, HTTPException, status, Depends
import time
from google.auth.exceptions import RefreshError
from loguru import logger

from src.workers.ingest_worker import enqueue_ingest, enqueue_ingest_async, enqueue_ingest_job_async
from src.services.ingest_job_service import list_pending_jobs, list_jobs, reset_stale_processing
from src.api.auth import require_role
from src.services.gmail_ingest_service import GmailIngestConfigError, ingest_from_notification
from src.services.gmail_state_store import save_watch_error
from src.services.ingest_policy import ingest_chunk_delay_seconds

router = APIRouter()


@router.post("/email", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def ingest_email(payload: dict, force: bool = False):
    required = {"message_id", "pdf_uri", "received_at"}
    if not required.issubset(payload):
        raise HTTPException(status_code=400, detail="missing fields")
    enqueue_ingest_async(payload, force=force)
    return {"accepted": True}


@router.post("/retry", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def retry_pending_jobs(limit: int = 10):
    job_ids = list_pending_jobs(limit=limit)
    for job_id in job_ids:
        enqueue_ingest_job_async(job_id)
    return {"accepted": len(job_ids)}


@router.get("/jobs", dependencies=[Depends(require_role("admin"))])
def list_ingest_jobs(status: str | None = None, limit: int = 50):
    return {"items": list_jobs(status=status, limit=limit)}


@router.post("/reset-stale", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def reset_stale(limit: int = 100, minutes: int | None = None):
    reset_ids = reset_stale_processing(minutes=minutes, limit=limit)
    for job_id in reset_ids:
        enqueue_ingest_job_async(job_id)
    return {"reset": len(reset_ids)}


@router.post("/gmail-scan", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def gmail_scan(
    force: bool = False,
    sync: bool = False,
    max_jobs: int | None = None,
    skip_ocr: bool = False,
    query: str | None = None,
    max_results: int | None = None,
    mark_read: bool | None = None,
    label_ids: str | None = None,
    prefix: str | None = None,
    force_full_scan: bool = False,
):
    try:
        overrides: dict = {}
        if query:
            overrides["query"] = query
        if max_results is not None:
            overrides["max_results"] = max_results
        if mark_read is not None:
            overrides["mark_read"] = mark_read
        if label_ids:
            overrides["label_ids"] = label_ids
        if prefix:
            overrides["prefix"] = prefix
        if force_full_scan:
            overrides["force_full_scan"] = True
        ingests = ingest_from_notification(overrides)
    except RefreshError as exc:
        # Keep scheduler green when refresh token is revoked/expired; state is surfaced in /system/status.
        try:
            save_watch_error("invalid_grant", str(exc))
        except Exception as state_exc:  # noqa: BLE001
            logger.warning("Failed to save Gmail watch error state", error=str(state_exc))
        logger.warning("Gmail scan skipped due to invalid_grant", detail=str(exc))
        return {"accepted": 0, "forced": force, "error": "invalid_grant"}
    except GmailIngestConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if max_jobs is not None:
        ingests = ingests[: max_jobs if max_jobs > 0 else 0]
    for idx, payload in enumerate(ingests):
        if skip_ocr:
            payload["skip_ocr"] = True
        chunk_delay = ingest_chunk_delay_seconds(idx)
        if chunk_delay > 0:
            time.sleep(chunk_delay)
        if sync:
            enqueue_ingest(payload, force=force)
        else:
            enqueue_ingest_async(payload, force=force)
    return {"accepted": len(ingests), "forced": force}
