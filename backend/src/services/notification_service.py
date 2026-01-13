import os
from typing import Any, Optional

from src.lib.logging import AuditEvent, log_structured, write_audit


def record_event(
    event_type: str,
    actor: str,
    target: str,
    fac: Optional[str] = None,
    wek: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    log_structured(
        "event",
        fac=fac,
        wek=wek,
        event_type=event_type,
        actor=actor,
        target=target,
        metadata=metadata or {},
    )
    if os.getenv("AUDIT_ENABLED", "true").lower() == "true":
        write_audit(
            AuditEvent(
                actor=actor,
                action=event_type,
                target=target,
                fac=fac,
                wek=wek,
                metadata=metadata,
            )
        )
