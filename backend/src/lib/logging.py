from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import uuid4

from loguru import logger

from src.db import session_scope
from src.models.user import AuditLog


@dataclass
class AuditEvent:
    actor: str
    action: str
    target: str
    fac: Optional[str] = None
    wek: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


def log_structured(message: str, fac: Optional[str] = None, wek: Optional[str] = None, **kwargs):
    payload = {"fac": fac, "wek": wek, **kwargs}
    logger.bind(**payload).info(message)


def write_audit(event: AuditEvent):
    logger.bind(fac=event.fac, wek=event.wek, target=event.target).info(
        f"AUDIT {event.action} by {event.actor}", metadata=event.metadata
    )
    try:
        with session_scope() as session:
            session.add(
                AuditLog(
                    id=f"AUD{int(event.created_at.timestamp())}{uuid4().hex[:6]}",
                    actor=event.actor,
                    action=event.action,
                    target=event.target,
                    fac=event.fac,
                    wek=event.wek,
                    metadata_json=event.metadata,
                )
            )
    except Exception:
        logger.exception("Failed to persist audit log")
