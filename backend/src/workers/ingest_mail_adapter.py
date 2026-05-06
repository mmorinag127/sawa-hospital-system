from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Iterable


@dataclass
class IngestEmailPayload:
    message_id: str
    pdf_uri: str
    received_at: datetime
    facility_hint: Optional[str] = None
    week_hint: Optional[str] = None
    facility_name: Optional[str] = None
    date_hints: Optional[Iterable[str]] = None
    skip_ocr: Optional[bool] = None
    source_kind: Optional[str] = None
    original_filename: Optional[str] = None
    content_sha256: Optional[str] = None
    ocr_job_id: Optional[str] = None
    order_id: Optional[str] = None
    uploaded_pdf_id: Optional[str] = None
    order_document_id: Optional[str] = None


def parse_ingest_payload(data: dict) -> IngestEmailPayload:
    """
    Convert inbound mail metadata into a typed payload.
    Expects: message_id, pdf_uri, received_at (ISO), optional hints.
    """
    return IngestEmailPayload(
        message_id=data["message_id"],
        pdf_uri=data["pdf_uri"],
        received_at=datetime.fromisoformat(data["received_at"]),
        facility_hint=data.get("facility_hint"),
        week_hint=data.get("week_hint"),
        facility_name=data.get("facility_name"),
        date_hints=data.get("date_hints"),
        skip_ocr=data.get("skip_ocr"),
        source_kind=data.get("source_kind"),
        original_filename=data.get("original_filename"),
        content_sha256=data.get("content_sha256"),
        ocr_job_id=data.get("ocr_job_id"),
        order_id=data.get("order_id"),
        uploaded_pdf_id=data.get("uploaded_pdf_id"),
        order_document_id=data.get("order_document_id"),
    )


def to_job_kwargs(payload: IngestEmailPayload) -> dict:
    """Serialize payload for enqueueing."""
    return {
        "message_id": payload.message_id,
        "pdf_uri": payload.pdf_uri,
        "received_at": payload.received_at.isoformat(),
        "facility_hint": payload.facility_hint,
        "week_hint": payload.week_hint,
        "facility_name": payload.facility_name,
        "date_hints": payload.date_hints,
        "skip_ocr": payload.skip_ocr,
        "source_kind": payload.source_kind,
        "original_filename": payload.original_filename,
        "content_sha256": payload.content_sha256,
        "ocr_job_id": payload.ocr_job_id,
        "order_id": payload.order_id,
        "uploaded_pdf_id": payload.uploaded_pdf_id,
        "order_document_id": payload.order_document_id,
    }
