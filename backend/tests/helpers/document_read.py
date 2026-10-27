"""Persist document metadata for read-route contracts; no OCR or upload claim."""
from datetime import datetime
from src.db import session_scope
from src.models.order import Order
from src.models.order_version import OrderVersion


def seed_document_read(order_id, uri, *, versions=()):
    with session_scope() as session:
        session.add(Order(id=order_id, document_uri=uri, message_id=f"mail-{order_id}"))
        for number, document_id, storage_uri, current in versions:
            session.add(OrderVersion(
                id=f"{order_id}-{number}", order_id=order_id, version_no=number,
                document_id=document_id, storage_uri=storage_uri,
                message_id=f"mail-{order_id}", is_current=current,
                received_at=datetime(2026, 10, 1),
            ))
