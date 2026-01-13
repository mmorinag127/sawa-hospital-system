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
    gmail_message_id: Optional[str] = None
    gmail_mark_read: Optional[bool] = None


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
        gmail_message_id=data.get("gmail_message_id"),
        gmail_mark_read=data.get("gmail_mark_read"),
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
        "gmail_message_id": payload.gmail_message_id,
        "gmail_mark_read": payload.gmail_mark_read,
    }
