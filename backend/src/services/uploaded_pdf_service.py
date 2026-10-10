from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.ingest_job import IngestJob
from src.models.order import Order
from src.models.ocr_job import OcrJob
from src.models.uploaded_pdf import UploadedPdf, UploadedPdfAttempt
from src.models.user import Notification
from src.services.ingest_policy import require_explicit_ingest_week, retry_backoff_seconds
from src.services.manual_upload_service import ManualUploadSavedFile


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
        "lease_token": row.lease_token,
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


def backfill_uploaded_pdfs_from_ingest_jobs(*, limit: int | None = None) -> int:
    raise ValueError("original_uploaded_pdf_receipt_required")


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
    facility_selection: dict | None = None,
) -> tuple[dict[str, Any], bool]:
    from src.services.ingest_delivery_dispatch_service import required_config
    from src.services.ingest_delivery_service import transaction
    from src.services.ingest_job_service import create_ingest_receipt
    required_config()
    week_hint = require_explicit_ingest_week(week_hint)
    from src.services.ingest_facility_selection_service import validate_selection
    facility_selection = validate_selection(facility_selection, facility_hint, week_hint)
    now = _now()
    duplicate_blocked = False
    with transaction() as session:
        if session.bind.dialect.name == "postgresql":
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("uploaded-pdf:" + saved.message_id, 0))))
        session.execute(update(UploadedPdf).where(UploadedPdf.message_id == saved.message_id).values(status=UploadedPdf.status))
        row = (
            session.execute(
                select(UploadedPdf).where(UploadedPdf.message_id == saved.message_id).with_for_update()
            )
            .scalars()
            .first()
        )
        from src.workers.ingest_mail_adapter import IngestEmailPayload
        from src.services.order_service import require_existing_ingest_selection
        require_existing_ingest_selection(session, IngestEmailPayload(
            message_id=saved.message_id, pdf_uri=saved.pdf_uri, received_at=saved.received_at,
            facility_hint=facility_hint, week_hint=week_hint, facility_selection=facility_selection,
        ))
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
            original = build_ingest_payload(_serialize_uploaded_pdf(row), write_session=session)
            if original["facility_selection"] != facility_selection:
                raise ValueError("uploaded_pdf_facility_selection_changed")
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
            row.max_attempts = _max_attempts()
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            row.next_retry_at = None
            row.last_error_code = None
            row.last_error_message = None
            row.alerted_at = None
            row.current_order_id = None
            row.current_document_id = None
            row.updated_at = now
        else:
            original = build_ingest_payload(_serialize_uploaded_pdf(row), write_session=session)
            if original["facility_selection"] != facility_selection:
                raise ValueError("uploaded_pdf_facility_selection_changed")
            duplicate_blocked = True
            row.updated_at = now
        session.flush()
        serialized = _serialize_uploaded_pdf(row)
        if not duplicate_blocked:
            serialized["ingest_receipt"] = create_ingest_receipt(build_ingest_payload(serialized,
                facility_selection=facility_selection),
                force=force, write_session=session)
        else:
            serialized["ingest_receipt"] = {"id": row.message_id, "enqueued": False}
    return serialized, duplicate_blocked


def build_ingest_payload(uploaded_pdf: dict[str, Any], *, facility_selection=None, write_session=None) -> dict[str, Any]:
    week_hint = require_explicit_ingest_week(uploaded_pdf.get("week_hint"))
    payload = {
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
    from src.services.ingest_facility_selection_service import validate_selection
    if facility_selection is None:
        from src.services.ingest_job_service import payload_digest
        with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
            if any(isinstance(row, IngestJob) and row.id == payload["message_id"]
                   for row in list(session.new) + list(session.dirty) + list(session.deleted)):
                raise ValueError("uploaded_pdf_original_input_pending_changes")
            table = IngestJob.__table__
            with session.no_autoflush:
                job = session.execute(select(*table.c).where(
                    table.c.id == payload["message_id"])).mappings().one_or_none()
            if (job is None or not job["generation"] or not isinstance(job["payload"], dict)
                    or payload_digest(job["payload"]) != job["input_digest"]):
                raise ValueError("uploaded_pdf_original_selection_missing")
            for key in ("message_id", "pdf_uri", "received_at", "facility_hint", "week_hint", "facility_name",
                        "skip_ocr", "source_kind", "original_filename", "content_sha256", "uploaded_pdf_id"):
                if job["payload"].get(key) != payload.get(key):
                    raise ValueError("uploaded_pdf_original_input_changed")
            facility_selection = job["payload"].get("facility_selection")
    payload["facility_selection"] = validate_selection(facility_selection, payload["facility_hint"], week_hint)
    return payload


def get_uploaded_pdf(uploaded_pdf_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return None
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
        return _serialize_uploaded_pdf_with_context(session, row)


def refresh_uploaded_pdf_links(uploaded_pdf_id: str, *, lease, order_id: str, document_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        if not lock_uploaded_pdf_lease(session, lease) or uploaded_pdf_id != lease.get("id"):
            return None
        from src.services.ingest_job_service import current_ingest_lease, lock_ingest_lease
        original = current_ingest_lease()
        if original is None or original.get("uploaded_lease") != lease or not lock_ingest_lease(session, original):
            return None
        row = session.get(UploadedPdf, uploaded_pdf_id)
        from src.services.workflow_attempt_guard_service import lock_order_execution_input
        order = lock_order_execution_input(session, order_id)
        document = session.get(OrderDocument, document_id, populate_existing=True, with_for_update=True)
        if document is None or order is None or document.source_email_id != row.message_id or document.storage_uri != row.storage_uri or order.current_document_id != document.id:
            return None
        row.current_order_id = order_id
        row.current_document_id = document_id
        session.flush()
        return _serialize_uploaded_pdf_with_context(session, row)


def list_uploaded_pdfs(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with session_scope() as session:
        query = select(UploadedPdf).order_by(UploadedPdf.received_at.desc(), UploadedPdf.id.desc())
        if status:
            query = query.where(UploadedPdf.status == status)
        rows = session.execute(query.limit(limit)).scalars().all()
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


def claim_uploaded_pdf(uploaded_pdf_id: str, *, worker_instance: str, write_session=None) -> dict[str, Any] | None:
    now = _now()
    lease_until = now + timedelta(seconds=_lease_seconds())
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        session.execute(update(UploadedPdf).where(UploadedPdf.id == uploaded_pdf_id).values(status=UploadedPdf.status))
        row = session.get(UploadedPdf, uploaded_pdf_id, populate_existing=True, with_for_update=True)
        if row is None:
            return None
        if row.status == "processing":
            for job in session.execute(select(OcrJob).where(OcrJob.uploaded_pdf_id == uploaded_pdf_id, OcrJob.status.in_(["awaiting_output", "recovering"]))).scalars():
                recorded = (job.metrics or {}).get("nonworkflow_execution")
                lease = recorded.get("ingest_lease") if isinstance(recorded, dict) else None
                owner = lease.get("uploaded_lease") if isinstance(lease, dict) else None
                if isinstance(owner, dict) and owner.get("lease_token") == row.lease_token:
                    return None
        build_ingest_payload(_serialize_uploaded_pdf(row), write_session=session)
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
                lease_token=uuid4().hex,
                lease_expires_at=lease_until,
                next_retry_at=None,
                updated_at=now,
            )
        )
        if result.rowcount <= 0:
            return None
        row = session.get(UploadedPdf, uploaded_pdf_id, populate_existing=True)
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


def _is_linked_order_ready(session, row: UploadedPdf, *, lock=False) -> bool:
    if not row.current_order_id:
        return False
    if lock:
        from src.services.workflow_attempt_guard_service import lock_order_execution_input
        order = lock_order_execution_input(session, row.current_order_id)
    else:
        order = session.get(Order, row.current_order_id)
    if order is None:
        return False
    if order.archived_at is not None:
        return False
    document = session.get(OrderDocument, row.current_document_id, populate_existing=lock, with_for_update=lock) if row.current_document_id else None
    if document is None or order.current_document_id != document.id or document.source_email_id != row.message_id or document.storage_uri != row.storage_uri:
        return False
    return bool(str(order.week_code or "").strip())


def is_uploaded_pdf_completion_ready(uploaded_pdf_id: str) -> bool:
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if row is None:
            return False
        return _is_linked_order_ready(session, row)


def lock_uploaded_pdf_lease(session, lease, *, terminal=False, read_only=False):
    required = {"id", "lease_token", "lease_owner", "attempt_count", "message_id", "storage_uri",
                "content_sha256", "received_at", "facility_hint", "week_hint", "facility_name",
                "skip_ocr", "source_kind", "original_filename", "page_count"}
    if not isinstance(lease, dict) or not required.issubset(lease) or not lease.get("lease_token") or not lease.get("lease_owner"):
        return False
    conditions = (
        UploadedPdf.id == lease["id"], UploadedPdf.status.in_(["processing", "completed"] if terminal else ["processing"]),
        UploadedPdf.lease_token == lease["lease_token"], UploadedPdf.lease_owner == lease["lease_owner"],
        UploadedPdf.attempt_count == lease["attempt_count"],
        UploadedPdf.message_id == lease["message_id"], UploadedPdf.storage_uri == lease["storage_uri"],
        UploadedPdf.content_sha256 == lease["content_sha256"],
    )
    if read_only:
        if session.execute(select(UploadedPdf.id).where(*conditions).with_for_update(read=True)).scalar_one_or_none() is None:
            return False
    else:
        result = session.execute(update(UploadedPdf).where(*conditions).values(status=UploadedPdf.status))
        if result.rowcount != 1:
            return False
    row = session.get(UploadedPdf, lease["id"], populate_existing=True)
    return row.received_at.isoformat() == lease.get("received_at") and all(getattr(row, key) == lease.get(key) for key in ("facility_hint", "week_hint", "facility_name", "skip_ocr", "source_kind", "original_filename", "page_count"))


def mark_uploaded_pdf_completed(uploaded_pdf_id: str, *, lease, completion=None) -> dict[str, Any] | None:
    now = _now()
    with session_scope() as session:
        if not lock_uploaded_pdf_lease(session, lease) or uploaded_pdf_id != lease.get("id"):
            return None
        if not isinstance(completion, dict) or completion.get("status") != "done" or (completion.get("lease") or {}).get("uploaded_lease") != lease:
            return None
        from src.services.ingest_job_service import lock_ingest_lease
        if not lock_ingest_lease(session, completion["lease"], terminal=True):
            return None
        ingest = session.get(IngestJob, completion["lease"]["id"])
        if ingest.status != "done":
            return None
        row = session.get(UploadedPdf, uploaded_pdf_id)
        if not _is_linked_order_ready(session, row, lock=True):
            return None
        worker_instance = row.lease_owner
        row.status = "completed"
        row.current_stage = "completed"
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


def mark_uploaded_pdf_completed_by_message_id_if_ready(message_id: str, *, lease, completion=None) -> dict[str, Any] | None:
    token = str(message_id or "").strip()
    if not token:
        return None
    if not isinstance(lease, dict) or lease.get("message_id") != token:
        return None
    return mark_uploaded_pdf_completed(lease["id"], lease=lease, completion=completion)


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


def stop_uploaded_pdf_budget(session, lease):
    # Called under the upload -> ingest locks in the same refusal transaction.
    if not lock_uploaded_pdf_lease(session, lease):
        return False
    row = session.get(UploadedPdf, lease["id"])
    now = _now()
    row.status = "failed"
    row.current_stage = "failed"
    row.last_error_code = "ingest_recovery_budget_exhausted"
    row.last_error_message = "Ingest recovery budget exhausted; explicit manual retry is required"
    row.lease_owner = None
    row.lease_expires_at = None
    row.next_retry_at = None
    row.updated_at = now
    _maybe_alert(session, row)
    _write_attempt(session, uploaded_pdf_id=row.id, attempt_no=row.attempt_count,
                   stage="failed", status="failed", worker_instance=lease["lease_owner"],
                   error_code=row.last_error_code, error_message=row.last_error_message, finished_at=now)
    return True


def schedule_uploaded_pdf_retry(
    uploaded_pdf_id: str,
    *,
    error_code: str,
    error_message: str,
    worker_instance: str | None = None,
    lease,
    terminal: bool = False,
    write_session=None,
) -> dict[str, Any] | None:
    now = _now()
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        if not lock_uploaded_pdf_lease(session, lease) or uploaded_pdf_id != lease.get("id") or worker_instance != lease.get("lease_owner"):
            return None
        row = session.get(UploadedPdf, uploaded_pdf_id)
        row.last_error_code = error_code
        row.last_error_message = error_message
        row.lease_owner = None
        row.lease_expires_at = None
        attempt_no = int(row.attempt_count or 0)
        _maybe_alert(session, row)
        if terminal:
            row.status = "failed"
            row.current_stage = "failed"
            row.next_retry_at = None
        elif attempt_no >= int(row.max_attempts or _max_attempts()):
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
    from src.services.ingest_delivery_dispatch_service import required_config
    from src.services.ingest_delivery_service import transaction
    from src.services.ingest_job_service import restart_ingest_job
    required_config()
    now = _now()
    with transaction() as session:
        session.execute(update(UploadedPdf).where(UploadedPdf.id == uploaded_pdf_id).values(status=UploadedPdf.status))
        row = session.get(UploadedPdf, uploaded_pdf_id, populate_existing=True, with_for_update=True)
        if row is None:
            return None
        build_ingest_payload(_serialize_uploaded_pdf(row), write_session=session)
        row.status = "pending"
        row.current_stage = "uploaded"
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.next_retry_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.updated_at = now
        session.flush()
        if not restart_ingest_job(row.message_id, write_session=session):
            raise ValueError("ingest_delivery_restart_original_missing")
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
