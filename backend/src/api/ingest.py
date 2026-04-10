from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status, Depends, File, Form, UploadFile
import time
from sqlalchemy import desc, select

from src.workers.ingest_worker import (
    enqueue_ingest,
    enqueue_ingest_job_async,
    enqueue_uploaded_pdf_async,
    process_uploaded_pdf_job,
)
from src.services.ingest_job_service import list_pending_jobs, list_jobs, reset_stale_processing, restart_ingest_job
from src.api.auth import require_role
from src.services.ingest_policy import ingest_chunk_delay_seconds
from src.services.manual_upload_service import ManualUploadConfigError, ManualUploadSavedFile, save_uploaded_pdf
from src.services.ingest_job_service import create_ingest_job
from src.services.uploaded_pdf_service import (
    build_ingest_payload,
    create_uploaded_pdf_from_upload,
    get_uploaded_pdf,
    is_uploaded_pdf_completion_ready,
    list_uploaded_pdfs,
    requeue_uploaded_pdf,
)
from src.db import session_scope
from src.models.order import Order

router = APIRouter()


def _normalize_upload_files(
    pdf_file: UploadFile | None,
    pdf_files: list[UploadFile] | None,
) -> list[UploadFile]:
    files: list[UploadFile] = []
    if pdf_file is not None:
        files.append(pdf_file)
    if pdf_files:
        files.extend(file for file in pdf_files if file is not None)
    if not files:
        raise HTTPException(status_code=400, detail="at least one pdf_file is required")
    return files


def _parse_optional_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="received_at must be ISO-8601") from exc


def _parse_form_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise HTTPException(status_code=400, detail="invalid boolean form value")


def _find_latest_order_id_by_message_id(message_id: str) -> str | None:
    with session_scope() as session:
        rows = session.execute(
            select(Order.id)
            .where(Order.message_id == message_id)
            .order_by(desc(Order.received_at), desc(Order.id))
        ).scalars().all()
        return rows[0] if rows else None


async def _handle_uploaded_pdf(
    *,
    pdf_file: UploadFile,
    facility_hint: str | None,
    week_hint: str | None,
    facility_name: str | None,
    received_at_value: datetime,
    force_value: bool,
    skip_ocr_value: bool,
) -> dict[str, Any]:
    raw_filename = str(pdf_file.filename or "").strip()
    if not raw_filename:
        raise HTTPException(status_code=400, detail="pdf_file filename is required")
    try:
        file_bytes = await pdf_file.read()
        saved = save_uploaded_pdf(
            pdf_bytes=file_bytes,
            original_filename=raw_filename,
            received_at=received_at_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ManualUploadConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    payload = {
        "message_id": saved.message_id,
        "pdf_uri": saved.pdf_uri,
        "received_at": saved.received_at.isoformat(),
        "facility_hint": facility_hint or None,
        "week_hint": week_hint or None,
        "facility_name": facility_name or None,
        "skip_ocr": skip_ocr_value,
        "source_kind": "manual_upload",
        "original_filename": saved.original_filename,
        "content_sha256": saved.content_sha256,
    }
    uploaded_pdf, duplicate_blocked = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint=facility_hint,
        week_hint=week_hint,
        facility_name=facility_name,
        skip_ocr=skip_ocr_value,
        source_kind="manual_upload",
        force=force_value,
    )
    job_id, enqueued = create_ingest_job(payload, force=force_value)
    if not duplicate_blocked:
        enqueue_uploaded_pdf_async(uploaded_pdf["id"])
    order_id = _find_latest_order_id_by_message_id(saved.message_id) if enqueued and not duplicate_blocked else None
    existing_order_id = _find_latest_order_id_by_message_id(saved.message_id) if duplicate_blocked else None
    return {
        "accepted": True,
        "filename": saved.original_filename,
        "uploaded_pdf_id": uploaded_pdf["id"],
        "message_id": saved.message_id,
        "ingest_job_id": job_id,
        "pdf_uri": saved.pdf_uri,
        "received_at": saved.received_at.isoformat(),
        "duplicate_blocked": duplicate_blocked or not enqueued,
        "order_id": order_id,
        "existing_order_id": existing_order_id,
        "source_kind": "manual_upload",
        "status": uploaded_pdf["status"],
        "current_stage": uploaded_pdf["current_stage"],
    }


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
async def ingest_upload(
    pdf_file: UploadFile | None = File(None),
    pdf_files: list[UploadFile] | None = File(None),
    facility_hint: str | None = Form(None),
    week_hint: str | None = Form(None),
    facility_name: str | None = Form(None),
    received_at: str | None = Form(None),
    force: str | None = Form(None),
    skip_ocr: str | None = Form(None),
):
    upload_files = _normalize_upload_files(pdf_file, pdf_files)
    received_at_value = _parse_optional_datetime(received_at)
    force_value = _parse_form_bool(force, default=False)
    skip_ocr_value = _parse_form_bool(skip_ocr, default=False)
    items = [
        await _handle_uploaded_pdf(
            pdf_file=current_file,
            facility_hint=facility_hint,
            week_hint=week_hint,
            facility_name=facility_name,
            received_at_value=received_at_value,
            force_value=force_value,
            skip_ocr_value=skip_ocr_value,
        )
        for current_file in upload_files
    ]
    primary = items[0]
    response = {**primary, "accepted": True, "count": len(items), "items": items}
    return response


@router.post("/retry", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def retry_pending_jobs(limit: int = 10):
    job_ids = list_pending_jobs(limit=limit)
    for job_id in job_ids:
        enqueue_ingest_job_async(job_id)
    return {"accepted": len(job_ids)}


@router.get("/jobs", dependencies=[Depends(require_role("admin"))])
def list_ingest_jobs(status: str | None = None, limit: int = 50):
    return {"items": list_jobs(status=status, limit=limit)}


@router.get("/uploads", dependencies=[Depends(require_role("operator"))])
def list_uploaded_pdf_rows(status: str | None = None, limit: int = 100):
    return {"items": list_uploaded_pdfs(status=status, limit=limit)}


@router.get("/uploads/{uploaded_pdf_id}", dependencies=[Depends(require_role("operator"))])
def get_uploaded_pdf_row(uploaded_pdf_id: str):
    row = get_uploaded_pdf(uploaded_pdf_id)
    if row is None:
        raise HTTPException(status_code=404, detail="uploaded_pdf_not_found")
    return row


@router.post("/uploads/{uploaded_pdf_id}/retry", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def retry_uploaded_pdf(uploaded_pdf_id: str):
    row = get_uploaded_pdf(uploaded_pdf_id)
    if row is None:
        raise HTTPException(status_code=404, detail="uploaded_pdf_not_found")
    status_value = str(row.get("status") or "").strip().lower()
    if status_value == "completed" and is_uploaded_pdf_completion_ready(uploaded_pdf_id):
        raise HTTPException(status_code=409, detail="uploaded_pdf_already_completed")
    message_id = str(row.get("message_id") or "").strip()
    updated = requeue_uploaded_pdf(uploaded_pdf_id)
    if message_id:
        restart_ingest_job(message_id)
    process_uploaded_pdf_job(uploaded_pdf_id)
    refreshed = get_uploaded_pdf(uploaded_pdf_id)
    return {"accepted": True, "item": refreshed or updated}


@router.post("/reset-stale", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def reset_stale(limit: int = 100, minutes: int | None = None):
    reset_ids = reset_stale_processing(minutes=minutes, limit=limit)
    for job_id in reset_ids:
        enqueue_ingest_job_async(job_id)
    return {"reset": len(reset_ids)}
