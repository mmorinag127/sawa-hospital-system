from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.ingest_job import IngestJob
from src.models.order import Order
from src.models.uploaded_pdf import UploadedPdf, UploadedPdfAttempt
from src.models.user import Notification
from src.services.ingest_policy import month_id_from_dates, parse_date_string, retry_backoff_seconds
from src.services.manual_upload_service import ManualUploadSavedFile
from src.services.week_candidate_service import calendar_week_ranges_for_month


def _now() -> datetime:
    return datetime.utcnow()


def _make_uploaded_pdf_id() -> str:
    return f"UPL{uuid4().hex[:8]}"


def _make_uploaded_pdf_attempt_id() -> str:
    return f"UPA{uuid4().hex[:8]}"


def _lease_seconds() -> int:
    raw = str(os.getenv("UPLOADED_PDF_LEASE_SECONDS", "1800") or "").strip()
    try:
        configured = max(int(raw), 60)
    except ValueError:
        configured = 1800
    wait_raw = str(os.getenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", "") or "").strip().lower()
    if wait_raw in {"0", "false", "no", "off"}:
        return configured
    if not wait_raw:
        pipeline_url = str(os.getenv("OCR_PIPELINE_URL", "") or "").strip()
        if not pipeline_url:
            return configured
    timeout_raw = str(os.getenv("OCR_PIPELINE_TIMEOUT_SECONDS", "600") or "").strip()
    grace_raw = str(os.getenv("UPLOADED_PDF_LEASE_GRACE_SECONDS", "120") or "").strip()
    try:
        timeout_seconds = max(int(timeout_raw), 60)
    except ValueError:
        timeout_seconds = 600
    try:
        grace_seconds = max(int(grace_raw), 0)
    except ValueError:
        grace_seconds = 120
    return max(60, min(configured, timeout_seconds + grace_seconds))


def _max_attempts() -> int:
    raw = str(os.getenv("UPLOADED_PDF_MAX_ATTEMPTS", "5") or "").strip()
    try:
        return max(int(raw), 1)
    except ValueError:
        return 5


def _alert_after_attempts() -> int:
    raw = str(os.getenv("UPLOADED_PDF_ALERT_AFTER_ATTEMPTS", "2") or "").strip()
    try:
        return max(int(raw), 1)
    except ValueError:
        return 2


def _backfill_scan_limit() -> int:
    raw = str(os.getenv("UPLOADED_PDF_BACKFILL_SCAN_LIMIT", "500") or "").strip()
    try:
        return max(int(raw), 1)
    except ValueError:
        return 500


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _looks_like_manual_upload_job(job: IngestJob) -> bool:
    payload = job.payload or {}
    source_kind = str(payload.get("source_kind") or "").strip().lower()
    pdf_uri = str(payload.get("pdf_uri") or "").strip()
    message_id = str(payload.get("message_id") or job.id or "").strip()
    if source_kind == "manual_upload":
        return True
    if "/manual-uploads/" in pdf_uri:
        return True
    return message_id.startswith("upload:sha256:")


def _guess_content_sha256(message_id: str, payload: dict[str, Any]) -> str:
    raw = str(payload.get("content_sha256") or "").strip()
    if raw:
        return raw
    token = str(message_id or "").strip()
    prefix = "upload:sha256:"
    if token.startswith(prefix):
        return token[len(prefix):] or token
    return token or f"legacy-backfill-{uuid4().hex[:12]}"


def _map_ingest_job_state(job: IngestJob, *, linked: bool, now: datetime) -> dict[str, Any]:
    raw_status = str(job.status or "").strip().lower()
    base_started_at = job.started_at or job.updated_at or job.created_at or now
    lease_expires_at = base_started_at + timedelta(seconds=_lease_seconds())
    if raw_status == "done":
        if linked:
            return {
                "status": "completed",
                "current_stage": "completed",
                "lease_owner": None,
                "lease_expires_at": None,
                "next_retry_at": None,
                "last_error_code": None,
                "last_error_message": None,
            }
        return {
            "status": "retry_wait",
            "current_stage": "retry_wait",
            "lease_owner": None,
            "lease_expires_at": None,
            "next_retry_at": now,
            "last_error_code": "order_attach_missing",
            "last_error_message": "backfilled ingest job completed without linked order/document",
        }
    if raw_status == "processing":
        return {
            "status": "processing",
            "current_stage": "ingest_running",
            "lease_owner": f"legacy-backfill:{job.id}",
            "lease_expires_at": lease_expires_at,
            "next_retry_at": None,
            "last_error_code": None,
            "last_error_message": None,
        }
    if raw_status == "error":
        return {
            "status": "retry_wait",
            "current_stage": "retry_wait",
            "lease_owner": None,
            "lease_expires_at": None,
            "next_retry_at": now,
            "last_error_code": "ingest_error",
            "last_error_message": str(job.last_error or "backfilled ingest error").strip(),
        }
    return {
        "status": "pending",
        "current_stage": "uploaded",
        "lease_owner": None,
        "lease_expires_at": None,
        "next_retry_at": None,
        "last_error_code": None,
        "last_error_message": None,
    }


def _serialize_uploaded_pdf(row: UploadedPdf) -> dict[str, Any]:
    return {
        "id": row.id,
        "message_id": row.message_id,
        "content_sha256": row.content_sha256,
        "source_kind": row.source_kind,
        "original_filename": row.original_filename,
        "storage_uri": row.storage_uri,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "page_count": row.page_count,
        "facility_hint": row.facility_hint,
        "week_hint": row.week_hint,
        "facility_name": row.facility_name,
        "skip_ocr": bool(row.skip_ocr),
        "status": row.status,
        "current_stage": row.current_stage,
        "attempt_count": int(row.attempt_count or 0),
        "max_attempts": int(row.max_attempts or 0),
        "lease_owner": row.lease_owner,
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
        "last_error_code": row.last_error_code,
        "last_error_message": row.last_error_message,
        "alerted_at": row.alerted_at.isoformat() if row.alerted_at else None,
        "current_order_id": row.current_order_id,
        "current_document_id": row.current_document_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _build_linked_order_summary(session, row: UploadedPdf) -> dict[str, Any] | None:
    order_id = str(row.current_order_id or "").strip()
    if not order_id:
        return None
    order = session.get(Order, order_id)
    if order is None:
        return None
    week_code = str(order.week_code or "").strip() or None
    current_document = None
    if str(order.current_document_id or "").strip():
        current_document = session.get(OrderDocument, order.current_document_id)
    prior_document = None
    superseded_ids = order.superseded_document_ids or []
    if superseded_ids:
        prior_document = session.get(OrderDocument, str(superseded_ids[-1] or "").strip())
    return {
        "id": order.id,
        "status": order.status,
        "facility_code": order.facility_code,
        "week_code": week_code,
        "message_id": order.message_id,
        "received_at": order.received_at.isoformat() if order.received_at else None,
        "current_document_id": order.current_document_id,
        "superseded_document_count": len(superseded_ids),
        "line_count": len(order.lines or []),
        "current_document": {
            "id": current_document.id,
            "storage_uri": current_document.storage_uri,
            "message_id": current_document.source_email_id,
            "received_at": current_document.received_at.isoformat() if current_document.received_at else None,
        }
        if current_document is not None
        else None,
        "prior_document": {
            "id": prior_document.id,
            "storage_uri": prior_document.storage_uri,
            "message_id": prior_document.source_email_id,
            "received_at": prior_document.received_at.isoformat() if prior_document.received_at else None,
        }
        if prior_document is not None
        else None,
    }


def _serialize_uploaded_pdf_with_context(session, row: UploadedPdf) -> dict[str, Any]:
    serialized = _serialize_uploaded_pdf(row)
    linked_order = _build_linked_order_summary(session, row)
    if linked_order is not None:
        serialized["linked_order"] = linked_order
        serialized["supersede_summary"] = {
            "has_prior_document": bool(linked_order.get("prior_document")),
            "superseded_document_count": linked_order.get("superseded_document_count"),
            "current_document": linked_order.get("current_document"),
            "prior_document": linked_order.get("prior_document"),
        }
    else:
        serialized["linked_order"] = None
        serialized["supersede_summary"] = None
    return serialized


def _derive_week_hint_from_filename(original_filename: object, received_at: object) -> str | None:
    filename = str(original_filename or "").strip()
    parsed_received_at = _parse_datetime(received_at)
    if not filename or parsed_received_at is None:
        return None
    match = re.search(r"(?:^|[_-])(\d{2})(\d{2})(?:[_.-]|$)", filename)
    if not match:
        return None
    parsed_date = parse_date_string(f"{match.group(1)}/{match.group(2)}", parsed_received_at)
    if parsed_date is None:
        return None
    month_id = month_id_from_dates([parsed_date], parsed_received_at)
    if not month_id:
        return None
    for start_date, end_date in calendar_week_ranges_for_month(month_id):
        if start_date <= parsed_date <= end_date:
            return f"{month_id}@{start_date.isoformat()}~{end_date.isoformat()}"
    return month_id


def backfill_uploaded_pdfs_from_ingest_jobs(*, limit: int | None = None) -> int:
    scan_limit = limit or _backfill_scan_limit()
    now = _now()
    created = 0
    with session_scope() as session:
        jobs = (
            session.execute(
                select(IngestJob).order_by(IngestJob.created_at.desc(), IngestJob.id.desc()).limit(scan_limit)
            )
            .scalars()
            .all()
        )
        existing_message_ids = set(
            session.execute(select(UploadedPdf.message_id)).scalars().all()
        )
        for job in jobs:
            if not _looks_like_manual_upload_job(job):
                continue
            payload = dict(job.payload or {})
            message_id = str(payload.get("message_id") or job.id or "").strip()
            storage_uri = str(payload.get("pdf_uri") or "").strip()
            if not message_id or not storage_uri or message_id in existing_message_ids:
                continue
            row = UploadedPdf(
                id=_make_uploaded_pdf_id(),
                message_id=message_id,
                content_sha256=_guess_content_sha256(message_id, payload),
                source_kind=str(payload.get("source_kind") or "manual_upload"),
                original_filename=str(payload.get("original_filename") or Path(storage_uri).name or message_id),
                storage_uri=storage_uri,
                received_at=_parse_datetime(payload.get("received_at")) or job.created_at or now,
                page_count=None,
                facility_hint=str(payload.get("facility_hint") or "").strip() or None,
                week_hint=str(payload.get("week_hint") or "").strip() or None,
                facility_name=str(payload.get("facility_name") or "").strip() or None,
                skip_ocr=bool(payload.get("skip_ocr")),
                status="pending",
                current_stage="uploaded",
                attempt_count=max(int(job.attempts or 0), 0),
                max_attempts=_max_attempts(),
                created_at=job.created_at or now,
                updated_at=job.updated_at or now,
            )
            session.add(row)
            session.flush()
            _link_current_entities(session, row)
            mapped_state = _map_ingest_job_state(
                job,
                linked=bool(row.current_order_id or row.current_document_id),
                now=now,
            )
            row.status = str(mapped_state["status"])
            row.current_stage = str(mapped_state["current_stage"])
            row.lease_owner = mapped_state["lease_owner"]
            row.lease_expires_at = mapped_state["lease_expires_at"]
            row.next_retry_at = mapped_state["next_retry_at"]
            row.last_error_code = mapped_state["last_error_code"]
            row.last_error_message = mapped_state["last_error_message"]
            row.updated_at = now
            existing_message_ids.add(message_id)
            created += 1
        session.flush()
    return created


def create_uploaded_pdf_from_upload(
    *,
    saved: ManualUploadSavedFile,
    facility_hint: str | None,
    week_hint: str | None,
    facility_name: str | None,
    skip_ocr: bool,
    source_kind: str,
    page_count: int | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    now = _now()
    duplicate_blocked = False
    with session_scope() as session:
        row = (
            session.execute(
                select(UploadedPdf).where(UploadedPdf.message_id == saved.message_id)
            )
            .scalars()
            .first()
        )
        if row is None:
            row = UploadedPdf(
                id=_make_uploaded_pdf_id(),
                message_id=saved.message_id,
                content_sha256=saved.content_sha256,
                source_kind=str(source_kind or "manual_upload"),
                original_filename=saved.original_filename,
                storage_uri=saved.pdf_uri,
                received_at=saved.received_at,
                page_count=page_count,
                facility_hint=facility_hint or None,
                week_hint=week_hint or None,
                facility_name=facility_name or None,
                skip_ocr=bool(skip_ocr),
                status="pending",
                current_stage="uploaded",
                attempt_count=0,
                max_attempts=_max_attempts(),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        elif force:
            row.content_sha256 = saved.content_sha256
            row.source_kind = str(source_kind or row.source_kind or "manual_upload")
            row.original_filename = saved.original_filename
            row.storage_uri = saved.pdf_uri
            row.received_at = saved.received_at
            row.page_count = page_count
            row.facility_hint = facility_hint or None
            row.week_hint = week_hint or None
            row.facility_name = facility_name or None
            row.skip_ocr = bool(skip_ocr)
            row.status = "pending"
            row.current_stage = "uploaded"
            row.attempt_count = 0
            row.max_attempts = _max_attempts()
            row.lease_owner = None
            row.lease_expires_at = None
            row.next_retry_at = None
            row.last_error_code = None
            row.last_error_message = None
            row.alerted_at = None
            row.current_order_id = None
            row.current_document_id = None
            row.updated_at = now
        else:
            duplicate_blocked = True
            row.updated_at = now
        session.flush()
        serialized = _serialize_uploaded_pdf(row)
    return serialized, duplicate_blocked


def build_ingest_payload(uploaded_pdf: dict[str, Any]) -> dict[str, Any]:
    week_hint = uploaded_pdf.get("week_hint") or None
    if not week_hint:
        week_hint = _derive_week_hint_from_filename(
            uploaded_pdf.get("original_filename"),
            uploaded_pdf.get("received_at"),
        )
    return {
        "message_id": uploaded_pdf.get("message_id"),
        "pdf_uri": uploaded_pdf.get("storage_uri"),
        "received_at": uploaded_pdf.get("received_at"),
        "facility_hint": uploaded_pdf.get("facility_hint") or None,
        "week_hint": week_hint,
        "facility_name": uploaded_pdf.get("facility_name") or None,
        "skip_ocr": bool(uploaded_pdf.get("skip_ocr")),
        "source_kind": uploaded_pdf.get("source_kind") or "manual_upload",
        "original_filename": uploaded_pdf.get("original_filename"),
        "content_sha256": uploaded_pdf.get("content_sha256"),
        "uploaded_pdf_id": uploaded_pdf.get("id"),
        "order_id": uploaded_pdf.get("current_order_id") or None,
        "order_document_id": uploaded_pdf.get("current_document_id") or None,
    }


def get_uploaded_pdf(uploaded_pdf_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        _link_current_entities(session, row)
        session.flush()
        return _serialize_uploaded_pdf_with_context(session, row)


def get_uploaded_pdf_by_message_id(message_id: str) -> dict[str, Any] | None:
    token = str(message_id or "").strip()
    if not token:
        return None
    with session_scope() as session:
        row = (
            session.execute(
                select(UploadedPdf)
                .where(UploadedPdf.message_id == token)
                .order_by(UploadedPdf.received_at.desc(), UploadedPdf.id.desc())
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        _link_current_entities(session, row)
        session.flush()
        return _serialize_uploaded_pdf_with_context(session, row)


def refresh_uploaded_pdf_links(uploaded_pdf_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        _link_current_entities(session, row)
        session.flush()
        return _serialize_uploaded_pdf_with_context(session, row)


def list_uploaded_pdfs(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    backfill_uploaded_pdfs_from_ingest_jobs(limit=max(limit, _backfill_scan_limit()))
    with session_scope() as session:
        query = select(UploadedPdf).order_by(UploadedPdf.received_at.desc(), UploadedPdf.id.desc())
        if status:
            query = query.where(UploadedPdf.status == status)
        rows = session.execute(query.limit(limit)).scalars().all()
        for row in rows:
            _link_current_entities(session, row)
        session.flush()
        return [_serialize_uploaded_pdf_with_context(session, row) for row in rows]


def _write_attempt(
    session,
    *,
    uploaded_pdf_id: str,
    attempt_no: int,
    stage: str,
    status: str,
    worker_instance: str | None,
    error_code: str | None = None,
    error_message: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    attempt = (
        session.execute(
            select(UploadedPdfAttempt).where(
                UploadedPdfAttempt.uploaded_pdf_id == uploaded_pdf_id,
                UploadedPdfAttempt.attempt_no == attempt_no,
            )
        )
        .scalars()
        .first()
    )
    now = _now()
    if attempt is None:
        attempt = UploadedPdfAttempt(
            id=_make_uploaded_pdf_attempt_id(),
            uploaded_pdf_id=uploaded_pdf_id,
            attempt_no=attempt_no,
            stage=stage,
            status=status,
            error_code=error_code,
            error_message=error_message,
            worker_instance=worker_instance,
            started_at=now,
            finished_at=finished_at,
            created_at=now,
            updated_at=now,
        )
        session.add(attempt)
        return
    attempt.stage = stage
    attempt.status = status
    attempt.error_code = error_code
    attempt.error_message = error_message
    if worker_instance:
        attempt.worker_instance = worker_instance
    if finished_at is not None:
        attempt.finished_at = finished_at
    attempt.updated_at = now


def claim_uploaded_pdf(uploaded_pdf_id: str, *, worker_instance: str) -> dict[str, Any] | None:
    now = _now()
    lease_until = now + timedelta(seconds=_lease_seconds())
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        next_attempt = int(row.attempt_count or 0) + 1
        result = session.execute(
            update(UploadedPdf)
            .where(UploadedPdf.id == uploaded_pdf_id)
            .where(
                or_(
                    UploadedPdf.status == "pending",
                    and_(
                        UploadedPdf.status == "retry_wait",
                        or_(UploadedPdf.next_retry_at.is_(None), UploadedPdf.next_retry_at <= now),
                    ),
                    and_(
                        UploadedPdf.status == "processing",
                        UploadedPdf.lease_expires_at.is_not(None),
                        UploadedPdf.lease_expires_at <= now,
                    ),
                )
            )
            .values(
                status="processing",
                current_stage="ingest_running",
                attempt_count=next_attempt,
                lease_owner=worker_instance,
                lease_expires_at=lease_until,
                next_retry_at=None,
                updated_at=now,
            )
        )
        if result.rowcount <= 0:
            return None
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        _write_attempt(
            session,
            uploaded_pdf_id=row.id,
            attempt_no=int(row.attempt_count or 0),
            stage=row.current_stage,
            status="running",
            worker_instance=worker_instance,
        )
        session.flush()
        return _serialize_uploaded_pdf(row)


def _link_current_entities(session, row: UploadedPdf) -> None:
    document = (
        session.execute(
            select(OrderDocument)
            .where(OrderDocument.source_email_id == row.message_id)
            .order_by(OrderDocument.received_at.desc(), OrderDocument.id.desc())
        )
        .scalars()
        .first()
    )
    order = None
    if document is not None:
        order = (
            session.execute(
                select(Order)
                .where(Order.current_document_id == document.id)
                .where(Order.archived_at.is_(None))
                .order_by(Order.received_at.desc(), Order.id.desc())
            )
            .scalars()
            .first()
        )
    if order is None:
        order = (
            session.execute(
                select(Order)
                .where(Order.message_id == row.message_id)
                .where(Order.archived_at.is_(None))
                .order_by(Order.received_at.desc(), Order.id.desc())
            )
            .scalars()
            .first()
        )
    if order is not None and str(order.current_document_id or "").strip():
        current_document = session.get(OrderDocument, order.current_document_id)
        if current_document is not None:
            document = current_document
    row.current_document_id = document.id if document is not None else None
    row.current_order_id = order.id if order is not None else None


def _is_linked_order_ready(session, row: UploadedPdf) -> bool:
    _link_current_entities(session, row)
    if not row.current_order_id:
        return False
    order = session.get(Order, row.current_order_id)
    if order is None:
        return False
    if order.archived_at is not None:
        row.current_order_id = None
        session.flush()
        return False
    return bool(str(order.week_code or "").strip())


def is_uploaded_pdf_completion_ready(uploaded_pdf_id: str) -> bool:
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return False
        return _is_linked_order_ready(session, row)


def mark_uploaded_pdf_completed(uploaded_pdf_id: str) -> dict[str, Any] | None:
    now = _now()
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        worker_instance = row.lease_owner
        _link_current_entities(session, row)
        row.status = "completed"
        row.current_stage = "completed"
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.updated_at = now
        _write_attempt(
            session,
            uploaded_pdf_id=row.id,
            attempt_no=int(row.attempt_count or 0),
            stage=row.current_stage,
            status="completed",
            worker_instance=worker_instance,
            finished_at=now,
        )
        session.flush()
        return _serialize_uploaded_pdf(row)


def mark_uploaded_pdf_completed_by_message_id_if_ready(message_id: str) -> dict[str, Any] | None:
    token = str(message_id or "").strip()
    if not token:
        return None
    now = _now()
    with session_scope() as session:
        row = (
            session.execute(
                select(UploadedPdf)
                .where(UploadedPdf.message_id == token)
                .order_by(UploadedPdf.received_at.desc(), UploadedPdf.id.desc())
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        _link_current_entities(session, row)
        if not _is_linked_order_ready(session, row):
            session.flush()
            return None
        if str(row.status or "").strip().lower() == "completed":
            session.flush()
            return _serialize_uploaded_pdf_with_context(session, row)
        worker_instance = row.lease_owner
        row.status = "completed"
        row.current_stage = "completed"
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.updated_at = now
        _write_attempt(
            session,
            uploaded_pdf_id=row.id,
            attempt_no=int(row.attempt_count or 0),
            stage=row.current_stage,
            status="completed",
            worker_instance=worker_instance,
            finished_at=now,
        )
        session.flush()
        return _serialize_uploaded_pdf_with_context(session, row)


def _maybe_alert(session, row: UploadedPdf) -> None:
    if row.alerted_at is not None:
        return
    if int(row.attempt_count or 0) < _alert_after_attempts():
        return
    now = _now()
    session.add(
        Notification(
            id=f"NTF{uuid4().hex[:8]}",
            target_role="admin",
            type="uploaded_pdf_retry_warning",
            message=(
                f"Uploaded PDF retry threshold reached: {row.original_filename} "
                f"({row.message_id}) attempts={int(row.attempt_count or 0)}"
            ),
            related_entity=row.id,
            sent_at=now,
        )
    )
    row.alerted_at = now


def schedule_uploaded_pdf_retry(
    uploaded_pdf_id: str,
    *,
    error_code: str,
    error_message: str,
    worker_instance: str | None = None,
) -> dict[str, Any] | None:
    now = _now()
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        row.last_error_code = error_code
        row.last_error_message = error_message
        row.lease_owner = None
        row.lease_expires_at = None
        attempt_no = int(row.attempt_count or 0)
        _maybe_alert(session, row)
        if attempt_no >= int(row.max_attempts or _max_attempts()):
            row.status = "manual_review"
            row.current_stage = "manual_review"
            row.next_retry_at = None
        else:
            row.status = "retry_wait"
            row.current_stage = "retry_wait"
            row.next_retry_at = now + timedelta(seconds=retry_backoff_seconds(attempt_no))
        row.updated_at = now
        _write_attempt(
            session,
            uploaded_pdf_id=row.id,
            attempt_no=attempt_no,
            stage=row.current_stage,
            status=row.status,
            worker_instance=worker_instance,
            error_code=error_code,
            error_message=error_message,
            finished_at=now,
        )
        session.flush()
        return _serialize_uploaded_pdf(row)


def requeue_uploaded_pdf(uploaded_pdf_id: str) -> dict[str, Any] | None:
    now = _now()
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
        row.status = "pending"
        row.current_stage = "uploaded"
        row.lease_owner = None
        row.lease_expires_at = None
        row.next_retry_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.updated_at = now
        session.flush()
        return _serialize_uploaded_pdf(row)


def list_ready_uploaded_pdf_ids(limit: int = 10) -> list[str]:
    now = _now()
    with session_scope() as session:
        rows = (
            session.execute(
                select(UploadedPdf.id)
                .where(
                    or_(
                        UploadedPdf.status == "pending",
                        (UploadedPdf.status == "retry_wait") & (
                            or_(UploadedPdf.next_retry_at.is_(None), UploadedPdf.next_retry_at <= now)
                        ),
                        (UploadedPdf.status == "processing") & (
                            (UploadedPdf.lease_expires_at.is_not(None)) & (UploadedPdf.lease_expires_at <= now)
                        ),
                    )
                )
                .order_by(UploadedPdf.received_at.asc(), UploadedPdf.id.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)


def summarize_uploaded_pdfs() -> dict[str, Any]:
    backfill_uploaded_pdfs_from_ingest_jobs()
    now = _now()
    with session_scope() as session:
        status_rows = session.execute(
            select(UploadedPdf.status, func.count(UploadedPdf.id)).group_by(UploadedPdf.status)
        ).all()
        stage_rows = session.execute(
            select(UploadedPdf.current_stage, func.count(UploadedPdf.id)).group_by(UploadedPdf.current_stage)
        ).all()
        status_counts = {status: int(count) for status, count in status_rows if status}
        stage_counts = {stage: int(count) for stage, count in stage_rows if stage}
        stale_leases = int(
            session.execute(
                select(func.count(UploadedPdf.id)).where(
                    (UploadedPdf.status == "processing")
                    & (UploadedPdf.lease_expires_at.is_not(None))
                    & (UploadedPdf.lease_expires_at <= now)
                )
            ).scalar_one()
            or 0
        )
        retry_ready = int(
            session.execute(
                select(func.count(UploadedPdf.id)).where(
                    (UploadedPdf.status == "retry_wait")
                    & (or_(UploadedPdf.next_retry_at.is_(None), UploadedPdf.next_retry_at <= now))
                )
            ).scalar_one()
            or 0
        )
        oldest_ready_at = session.execute(
            select(func.min(UploadedPdf.received_at)).where(
                or_(
                    UploadedPdf.status == "pending",
                    (UploadedPdf.status == "retry_wait")
                    & (or_(UploadedPdf.next_retry_at.is_(None), UploadedPdf.next_retry_at <= now)),
                    (UploadedPdf.status == "processing")
                    & (UploadedPdf.lease_expires_at.is_not(None))
                    & (UploadedPdf.lease_expires_at <= now),
                )
            )
        ).scalar_one_or_none()
    eligible = int(status_counts.get("pending", 0) or 0) + retry_ready + stale_leases
    return {
        "total": int(sum(status_counts.values())),
        "counts": status_counts,
        "stage_counts": stage_counts,
        "pending_count": int(status_counts.get("pending", 0) or 0),
        "processing_count": int(status_counts.get("processing", 0) or 0),
        "retry_wait_count": int(status_counts.get("retry_wait", 0) or 0),
        "completed_count": int(status_counts.get("completed", 0) or 0),
        "manual_review_count": int(status_counts.get("manual_review", 0) or 0),
        "stale_lease_count": stale_leases,
        "retry_ready_count": retry_ready,
        "eligible_backlog_count": eligible,
        "oldest_ready_at": oldest_ready_at.isoformat() if oldest_ready_at else None,
        "oldest_ready_seconds": (
            max(int((now - oldest_ready_at).total_seconds()), 0) if oldest_ready_at else None
        ),
    }
