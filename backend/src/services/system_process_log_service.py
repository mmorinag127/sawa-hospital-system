from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, select

from src.db import session_scope
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.shipping_tracking import ShippingTrackingLog
from src.models.uploaded_pdf import UploadedPdf
from src.models.user import AuditLog


MAX_PROCESS_LOG_LIMIT = 200
DEFAULT_PROCESS_LOG_LIMIT = 100


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _audit_log_row(row: AuditLog) -> dict[str, Any]:
    return {
        "id": f"audit:{row.id}",
        "source": "audit_logs",
        "process_type": "audit",
        "status": "recorded",
        "title": row.action,
        "target": row.target,
        "occurred_at": _iso(row.created_at),
        "actor": row.actor,
        "summary": f"{row.action} / {row.target}",
        "details": {
            "id": row.id,
            "actor": row.actor,
            "action": row.action,
            "target": row.target,
            "facility_id": row.fac,
            "week": row.wek,
            "metadata": _json_safe(row.metadata_json or {}),
            "created_at": _iso(row.created_at),
        },
    }


def _ingest_job_row(row: IngestJob) -> dict[str, Any]:
    payload = _json_safe(row.payload or {})
    pdf_uri = payload.get("pdf_uri") if isinstance(payload, dict) else None
    message_id = payload.get("message_id") if isinstance(payload, dict) else None
    return {
        "id": f"ingest:{row.id}",
        "source": "ingest_jobs",
        "process_type": "ingest",
        "status": row.status,
        "title": "注文取込",
        "target": message_id or row.id,
        "occurred_at": _iso(row.updated_at or row.created_at),
        "actor": "system",
        "summary": f"{row.status} / attempts {row.attempts}",
        "details": {
            "id": row.id,
            "status": row.status,
            "attempts": row.attempts,
            "last_error": row.last_error,
            "pdf_uri": pdf_uri,
            "payload": payload,
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    }


def _ocr_job_row(row: OcrJob) -> dict[str, Any]:
    metrics = _json_safe(row.metrics or {})
    return {
        "id": f"ocr:{row.id}",
        "source": "ocr_jobs",
        "process_type": "ocr",
        "status": row.status,
        "title": "OCR処理",
        "target": row.input_reference,
        "occurred_at": _iso(row.updated_at or row.created_at),
        "actor": "system",
        "summary": row.error_message or row.output_reference or row.template_id or row.status,
        "details": {
            "id": row.id,
            "status": row.status,
            "input_reference": row.input_reference,
            "template_id": row.template_id,
            "output_reference": row.output_reference,
            "metrics": metrics,
            "error_message": row.error_message,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    }


def _uploaded_pdf_row(row: UploadedPdf) -> dict[str, Any]:
    return {
        "id": f"uploaded_pdf:{row.id}",
        "source": "uploaded_pdfs",
        "process_type": "uploaded_pdf",
        "status": row.status,
        "title": "PDFアップロード処理",
        "target": row.original_filename or row.message_id,
        "occurred_at": _iso(row.updated_at or row.created_at),
        "actor": row.source_kind,
        "summary": f"{row.current_stage} / attempt {row.attempt_count}",
        "details": {
            "id": row.id,
            "message_id": row.message_id,
            "status": row.status,
            "current_stage": row.current_stage,
            "source_kind": row.source_kind,
            "original_filename": row.original_filename,
            "storage_uri": row.storage_uri,
            "facility_hint": row.facility_hint,
            "facility_name": row.facility_name,
            "week_hint": row.week_hint,
            "skip_ocr": row.skip_ocr,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "lease_owner": row.lease_owner,
            "lease_expires_at": _iso(row.lease_expires_at),
            "next_retry_at": _iso(row.next_retry_at),
            "last_error_code": row.last_error_code,
            "last_error_message": row.last_error_message,
            "current_order_id": row.current_order_id,
            "current_document_id": row.current_document_id,
            "received_at": _iso(row.received_at),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    }


def _shipping_log_row(row: ShippingTrackingLog) -> dict[str, Any]:
    return {
        "id": f"shipping:{row.id}",
        "source": "shipping_tracking_logs",
        "process_type": "shipping_tracking",
        "status": row.status,
        "title": "送り状追跡",
        "target": row.tracking_number,
        "occurred_at": _iso(row.looked_up_at or row.created_at),
        "actor": row.source,
        "summary": row.error or row.arrival_text or row.facility_name or row.status,
        "details": {
            "id": row.id,
            "tracking_key": row.tracking_key,
            "tracking_number": row.tracking_number,
            "ship_date": _iso(row.ship_date),
            "facility_name": row.facility_name,
            "status": row.status,
            "delivered": row.delivered,
            "arrival_text": row.arrival_text,
            "error": row.error,
            "source": row.source,
            "looked_up_at": _iso(row.looked_up_at),
            "created_at": _iso(row.created_at),
        },
    }


def list_process_logs(limit: int = DEFAULT_PROCESS_LOG_LIMIT) -> dict[str, Any]:
    safe_limit = min(max(int(limit or DEFAULT_PROCESS_LOG_LIMIT), 1), MAX_PROCESS_LOG_LIMIT)
    per_source_limit = safe_limit
    items: list[dict[str, Any]] = []

    with session_scope() as session:
        items.extend(
            _audit_log_row(row)
            for row in session.execute(
                select(AuditLog).order_by(desc(AuditLog.created_at)).limit(per_source_limit)
            )
            .scalars()
            .all()
        )
        items.extend(
            _ingest_job_row(row)
            for row in session.execute(
                select(IngestJob)
                .order_by(desc(IngestJob.updated_at), desc(IngestJob.created_at))
                .limit(per_source_limit)
            )
            .scalars()
            .all()
        )
        items.extend(
            _ocr_job_row(row)
            for row in session.execute(
                select(OcrJob).order_by(desc(OcrJob.updated_at), desc(OcrJob.created_at)).limit(per_source_limit)
            )
            .scalars()
            .all()
        )
        items.extend(
            _uploaded_pdf_row(row)
            for row in session.execute(
                select(UploadedPdf)
                .order_by(desc(UploadedPdf.updated_at), desc(UploadedPdf.created_at))
                .limit(per_source_limit)
            )
            .scalars()
            .all()
        )
        items.extend(
            _shipping_log_row(row)
            for row in session.execute(
                select(ShippingTrackingLog)
                .order_by(desc(ShippingTrackingLog.looked_up_at), desc(ShippingTrackingLog.created_at))
                .limit(per_source_limit)
            )
            .scalars()
            .all()
        )

    items.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)
    return {
        "limit": safe_limit,
        "count": len(items[:safe_limit]),
        "items": items[:safe_limit],
    }
