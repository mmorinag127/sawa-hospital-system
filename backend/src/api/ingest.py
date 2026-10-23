from src.staging_data import order_runtime_setting as _runtime_setting

import asyncio
from datetime import datetime
from typing import Any


from fastapi import APIRouter, HTTPException, status, Depends, File, Form, UploadFile, Request
import time

from src.workers.ingest_worker import (
    enqueue_ingest,
    enqueue_ingest_job_async,
    enqueue_uploaded_pdf_async,
    process_uploaded_pdf_job,
    run_ocr_job_recovery_once,
)
from src.services.ingest_job_service import list_pending_jobs, list_jobs, reset_stale_processing, restart_ingest_job
from src.api.auth import require_role
from src.services.ingest_policy import ingest_chunk_delay_seconds
from src.services.manual_upload_service import (
    ManualUploadConfigError,
    ManualUploadSavedFile,
    save_uploaded_pdf_pages,
)
from src.services.ingest_job_service import create_ingest_job, get_ingest_job_snapshot
from src.services.uploaded_pdf_service import (
    build_ingest_payload,
    create_uploaded_pdf_from_upload,
    get_uploaded_pdf,
    is_uploaded_pdf_completion_ready,
    list_ready_uploaded_pdf_ids,
    list_uploaded_pdfs,
    requeue_uploaded_pdf,
)
from src.workers.ingest_mail_adapter import IngestEmailPayload
from src.services import order_service, week_candidate_service

router = APIRouter()


@router.get("/pending-faxes", dependencies=[Depends(require_role("operator"))])
def pending_faxes():
    """Unfinished intake and OCR results awaiting workflow confirmation; no cutoff."""
    from sqlalchemy import or_, select
    from src.db import session_scope
    from src.models.uploaded_pdf import UploadedPdf
    from src.models.order_workflow_state import OrderWorkflowState
    with session_scope() as session:
        rows = session.execute(select(UploadedPdf).outerjoin(OrderWorkflowState,
            OrderWorkflowState.order_id == UploadedPdf.current_order_id).where(or_(
                UploadedPdf.status.in_(["pending", "processing"]),
                (UploadedPdf.status == "completed") & or_(OrderWorkflowState.state != "confirmed", OrderWorkflowState.order_id.is_(None))
            )).order_by(UploadedPdf.received_at, UploadedPdf.id)).scalars().all()
        items = [{"id": row.id, "name": row.original_filename, "facility": row.facility_name,
                  "facility_id": row.facility_hint, "week": row.week_hint, "status": row.status,
                  "sha256": row.content_sha256, "order_id": row.current_order_id,
                  "document_id": row.current_document_id, "skip_ocr": row.skip_ocr} for row in rows]
    return {"items": items, "total": len(items)}


@router.get("/review-faxes", dependencies=[Depends(require_role("operator"))])
def review_faxes():
    from sqlalchemy import select
    from src.db import session_scope
    from src.models.uploaded_pdf import UploadedPdf
    with session_scope() as session:
        rows = session.execute(select(UploadedPdf).where(UploadedPdf.status.in_(["failed", "manual_review", "retry_wait"])).order_by(UploadedPdf.received_at, UploadedPdf.id)).scalars().all()
        items = [{"id": row.id, "name": row.original_filename, "facility": row.facility_name,
                  "facility_id": row.facility_hint, "week": row.week_hint, "status": row.status,
                  "sha256": row.content_sha256, "order_id": row.current_order_id,
                  "document_id": row.current_document_id, "skip_ocr": row.skip_ocr} for row in rows]
    return {"items": items, "total": len(items)}


@router.post("/uploads/{uploaded_pdf_id}/start", status_code=202, dependencies=[Depends(require_role("operator"))])
def start_pending_fax(uploaded_pdf_id: str, body: dict):
    """Dispatch an existing pending intent; do not reset a lease or restart a job."""
    row = get_uploaded_pdf(uploaded_pdf_id)
    if row is None:
        raise HTTPException(404, "uploaded_pdf_not_found")
    if set(body) != {"sha256"} or body["sha256"] != row["content_sha256"] or row["skip_ocr"]:
        raise HTTPException(409, "fax_original_changed_or_ocr_skipped")
    if row["status"] == "processing":
        return {"accepted": False, "already_processing": True}
    if row["status"] != "pending":
        raise HTTPException(409, "fax_is_not_pending")
    try:
        build_ingest_payload(row)  # Verify the captured original facility/week selection.
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    enqueue_uploaded_pdf_async(uploaded_pdf_id)
    return {"accepted": True, "uploaded_pdf_id": uploaded_pdf_id}

# Backward-compatible import alias for tests and internal helpers that still
# patch or call the older single-save name.
save_uploaded_pdf = save_uploaded_pdf_pages


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
    max_files = max(1, int(_runtime_setting("MANUAL_UPLOAD_MAX_FILES", "10") or 10))
    if len(files) > max_files:
        raise HTTPException(status_code=413, detail=f"too many files; max is {max_files}")
    return files


def _enforce_upload_content_length(request: Request) -> None:
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        content_length = int(raw_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid content-length") from None
    max_mb = int(_runtime_setting("MANUAL_UPLOAD_MAX_MB", "20") or 20)
    max_files = max(1, int(_runtime_setting("MANUAL_UPLOAD_MAX_FILES", "10") or 10))
    # Multipart overhead is small but non-zero, so allow a narrow envelope over
    # the per-PDF limit while still rejecting oversized bodies before parsing.
    max_bytes = (max_mb * max_files * 1024 * 1024) + (max_files * 1024 * 1024)
    if content_length > max_bytes:
        raise HTTPException(status_code=413, detail=f"upload body exceeds {max_mb}MB x {max_files} limit")


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


def _normalize_required_upload_context(
    *,
    facility_hint: str | None,
    week_hint: str | None,
) -> tuple[str, str]:
    normalized_facility = str(facility_hint or "").strip()
    normalized_week = str(week_hint or "").strip()
    missing: list[str] = []
    if not normalized_facility:
        missing.append("facility_hint")
    if not normalized_week:
        missing.append("week_hint")
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "upload_context_required",
                "message": "facility_hint and week_hint are required for PDF upload",
                "missing": missing,
            },
        )
    from src.services.ingest_policy import require_explicit_ingest_week

    try:
        normalized_week = require_explicit_ingest_week(normalized_week)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "missing": ["week_hint"]}) from exc
    return normalized_facility, normalized_week


def _build_upload_existing_order_preview(
    *,
    saved: ManualUploadSavedFile,
    facility_hint: str | None,
    week_hint: str | None,
    facility_name: str | None,
    skip_ocr_value: bool,
) -> dict[str, Any] | None:
    payload = IngestEmailPayload(
        message_id=saved.message_id,
        pdf_uri=saved.pdf_uri,
        received_at=saved.received_at,
        facility_hint=facility_hint,
        week_hint=week_hint,
        facility_name=facility_name,
        skip_ocr=skip_ocr_value,
        source_kind="manual_upload",
        original_filename=saved.original_filename,
        content_sha256=saved.content_sha256,
    )
    return order_service.preview_existing_order_for_ingest(payload)


def _intake_protocol(request: Request):
    values = request.query_params.getlist("intake_protocol")
    if not values:
        return None
    if values != ["native_ingest_v2"]:
        raise HTTPException(status_code=422, detail="intake_protocol_invalid")
    return values[0]


def _handle_uploaded_pdf_bytes(
    *,
    file_bytes: bytes,
    raw_filename: str,
    facility_hint: str | None,
    week_hint: str | None,
    facility_name: str | None,
    received_at_value: datetime,
    force_value: bool,
    skip_ocr_value: bool,
    facility_selection: dict,
    intake_protocol: str | None = None,
) -> dict[str, Any]:
    from src.services.ingest_facility_selection_service import validate_selection
    try:
        facility_selection = validate_selection(facility_selection, facility_hint, week_hint)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        saved_result = save_uploaded_pdf(
            pdf_bytes=file_bytes,
            original_filename=raw_filename,
            received_at=received_at_value,
        )
        if isinstance(saved_result, list):
            saved_items = saved_result
        else:
            saved_items = [saved_result]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ManualUploadConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    items: list[dict[str, Any]] = []
    for saved in saved_items:
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
        existing_order_preview = _build_upload_existing_order_preview(
            saved=saved,
            facility_hint=facility_hint,
            week_hint=week_hint,
            facility_name=facility_name,
            skip_ocr_value=skip_ocr_value,
        )
        try:
            uploaded_pdf, duplicate_blocked = create_uploaded_pdf_from_upload(
                saved=saved,
                facility_hint=facility_hint,
                week_hint=week_hint,
                facility_name=facility_name,
                skip_ocr=skip_ocr_value,
                source_kind="manual_upload",
                force=force_value,
                page_count=saved.total_pages or saved.page_number or 1,
                facility_selection=facility_selection,
                intake_protocol=intake_protocol,
            )
        except ValueError as exc:
            if str(exc).startswith(("ingest_facility_", "ingest_order_", "uploaded_pdf_original_",
                                    "uploaded_pdf_facility_")):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise
        payload["uploaded_pdf_id"] = uploaded_pdf["id"]
        receipt = uploaded_pdf["ingest_receipt"]
        job_id, enqueued = receipt["id"], receipt["enqueued"]
        if not duplicate_blocked:
            enqueue_uploaded_pdf_async(uploaded_pdf["id"])
            order_service.invalidate_orders_cache()
        order_id = uploaded_pdf["current_order_id"] if enqueued and not duplicate_blocked else None
        existing_order_id = uploaded_pdf["current_order_id"] if duplicate_blocked else None
        items.append(
            {
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
                "existing_order_preview": existing_order_preview,
                "intake_decision": "reuse_existing_order"
                if existing_order_preview is not None
                else ("duplicate_blocked" if duplicate_blocked or not enqueued else "create_new_order"),
                "source_kind": "manual_upload",
                "status": uploaded_pdf["status"],
                "current_stage": uploaded_pdf["current_stage"],
                "page_number": saved.page_number,
                "total_pages": saved.total_pages,
            }
        )
    primary = items[0]
    return {**primary, "accepted": True, "count": len(items), "items": items}


async def _handle_uploaded_pdf(
    *,
    pdf_file: UploadFile,
    facility_hint: str | None,
    week_hint: str | None,
    facility_name: str | None,
    received_at_value: datetime,
    force_value: bool,
    skip_ocr_value: bool,
    facility_selection: dict,
    intake_protocol: str | None = None,
) -> dict[str, Any]:
    raw_filename = str(pdf_file.filename or "").strip()
    if not raw_filename:
        raise HTTPException(status_code=400, detail="pdf_file filename is required")
    file_bytes = await pdf_file.read()
    return await asyncio.to_thread(
        _handle_uploaded_pdf_bytes,
        file_bytes=file_bytes,
        raw_filename=raw_filename,
        facility_hint=facility_hint,
        week_hint=week_hint,
        facility_name=facility_name,
        received_at_value=received_at_value,
        force_value=force_value,
        skip_ocr_value=skip_ocr_value,
        facility_selection=facility_selection,
        intake_protocol=intake_protocol,
    )


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
async def ingest_upload(
    request: Request,
    pdf_file: UploadFile | None = File(None),
    pdf_files: list[UploadFile] | None = File(None),
    facility_hint: str | None = Form(None),
    week_hint: str | None = Form(None),
    facility_name: str | None = Form(None),
    received_at: str | None = Form(None),
    force: str | None = Form(None),
    skip_ocr: str | None = Form(None),
    effective_date: str | None = Form(None),
    expected_canonical_revision: str | None = Form(None),
    selected_version_id: str | None = Form(None),
):
    intake_protocol = _intake_protocol(request)
    _enforce_upload_content_length(request)
    upload_files = _normalize_upload_files(pdf_file, pdf_files)
    facility_hint, week_hint = _normalize_required_upload_context(
        facility_hint=facility_hint,
        week_hint=week_hint,
    )
    from src.services.ingest_facility_selection_service import capture_selection
    from src.services.canonical_facility_store import CanonicalStoreError
    try:
        facility_selection = capture_selection(
            facility_hint, effective_date, expected_canonical_revision, selected_version_id, week_hint)
    except CanonicalStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    received_at_value = _parse_optional_datetime(received_at)
    force_value = _parse_form_bool(force, default=False)
    skip_ocr_value = _parse_form_bool(skip_ocr, default=False)
    results: list[dict[str, Any]] = []
    for current_file in upload_files:
        results.append(
            await _handle_uploaded_pdf(
                pdf_file=current_file,
                facility_hint=facility_hint,
                week_hint=week_hint,
                facility_name=facility_name,
                received_at_value=received_at_value,
                force_value=force_value,
                skip_ocr_value=skip_ocr_value,
                facility_selection=facility_selection,
                intake_protocol=intake_protocol,
            )
        )
    items: list[dict[str, Any]] = []
    for result in results:
        nested_items = result.get("items")
        if isinstance(nested_items, list) and nested_items:
            items.extend(nested_items)
        else:
            items.append(result)
    primary = items[0]
    response = {**primary, "accepted": True, "count": len(items), "items": items}
    return response


@router.get("/week-options", dependencies=[Depends(require_role("operator"))])
def list_upload_week_options(month_id: str, facility_id: str | None = None):
    normalized_month = str(month_id or "").strip()
    if not normalized_month:
        raise HTTPException(status_code=400, detail="month_id is required")
    options = week_candidate_service.build_week_option_entries(
        normalized_month,
        str(facility_id or "").strip() or None,
    )
    if not options:
        raise HTTPException(status_code=400, detail="invalid month_id")
    return {"options": options}


@router.post("/retry", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def retry_pending_jobs(limit: int = 10):
    job_ids = list_pending_jobs(limit=limit)
    for job_id in job_ids:
        captured = get_ingest_job_snapshot(job_id)
        if captured is not None and captured["generation"] and captured["input_digest"]:
            if captured.get("uploaded_pdf_id") is not None:
                enqueue_uploaded_pdf_async(captured["uploaded_pdf_id"])
            else:
                enqueue_ingest_job_async(job_id, generation=captured["generation"], input_digest=captured["input_digest"])
    return {"accepted": len(job_ids)}


@router.post("/recover-ready", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def recover_ready(limit: int = 20):
    reset_ids = reset_stale_processing(limit=limit)
    pending_ids = list_pending_jobs(limit=limit)
    ingest_ids: list[str] = []
    seen_ingest: set[str] = set()
    for job_id in [*reset_ids, *pending_ids]:
        normalized = str(job_id or "").strip()
        if not normalized or normalized in seen_ingest:
            continue
        seen_ingest.add(normalized)
        ingest_ids.append(normalized)
        captured = get_ingest_job_snapshot(normalized)
        if captured is not None and captured["generation"] and captured["input_digest"]:
            if captured.get("uploaded_pdf_id") is not None:
                enqueue_uploaded_pdf_async(captured["uploaded_pdf_id"])
            else:
                enqueue_ingest_job_async(normalized, generation=captured["generation"], input_digest=captured["input_digest"])

    uploaded_ids = [
        str(uploaded_pdf_id or "").strip()
        for uploaded_pdf_id in list_ready_uploaded_pdf_ids(limit=limit)
        if str(uploaded_pdf_id or "").strip()
    ]
    for uploaded_pdf_id in uploaded_ids:
        enqueue_uploaded_pdf_async(uploaded_pdf_id)

    ocr_recovered = run_ocr_job_recovery_once(limit=limit)
    return {
        "accepted": True,
        "limit": limit,
        "ingest_reset": len(reset_ids),
        "ingest_enqueued": len(ingest_ids),
        "uploaded_enqueued": len(uploaded_ids),
        "ocr_recovered": int(ocr_recovered or 0),
    }


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
def retry_uploaded_pdf(uploaded_pdf_id: str, request: Request):
    intake_protocol = _intake_protocol(request)
    row = get_uploaded_pdf(uploaded_pdf_id)
    if row is None:
        raise HTTPException(status_code=404, detail="uploaded_pdf_not_found")
    status_value = str(row.get("status") or "").strip().lower()
    if status_value == "completed" and is_uploaded_pdf_completion_ready(uploaded_pdf_id):
        raise HTTPException(status_code=409, detail="uploaded_pdf_already_completed")
    message_id = str(row.get("message_id") or "").strip()
    updated = requeue_uploaded_pdf(uploaded_pdf_id, intake_protocol=intake_protocol) if intake_protocol is not None else requeue_uploaded_pdf(uploaded_pdf_id)
    enqueue_uploaded_pdf_async(uploaded_pdf_id)
    refreshed = get_uploaded_pdf(uploaded_pdf_id)
    return {"accepted": True, "item": refreshed or updated}


@router.post("/reset-stale", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("admin"))])
def reset_stale(limit: int = 100, minutes: int | None = None):
    reset_ids = reset_stale_processing(minutes=minutes, limit=limit)
    for job_id in reset_ids:
        captured = get_ingest_job_snapshot(job_id)
        if captured is not None and captured["generation"] and captured["input_digest"]:
            if captured.get("uploaded_pdf_id") is not None:
                enqueue_uploaded_pdf_async(captured["uploaded_pdf_id"])
            else:
                enqueue_ingest_job_async(job_id, generation=captured["generation"], input_digest=captured["input_digest"])
    return {"reset": len(reset_ids)}
