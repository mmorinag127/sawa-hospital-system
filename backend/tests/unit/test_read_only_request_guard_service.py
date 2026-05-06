from __future__ import annotations

import pytest

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.services.read_only_request_guard_service import read_only_request_guard


Base.metadata.create_all(bind=engine)


def test_read_only_guard_blocks_canonical_insert() -> None:
    with pytest.raises(RuntimeError, match="canonical write blocked"):
        with read_only_request_guard(method="GET", path="/orders"):
            with session_scope() as session:
                session.add(
                    Order(
                        id="ORDREADONLYGUARD",
                        document_uri="file:///guard.pdf",
                        message_id="msg-read-only-guard",
                    )
                )


def test_read_only_guard_allows_read_query() -> None:
    with read_only_request_guard(method="GET", path="/orders"):
        with session_scope() as session:
            assert session.get(Order, "missing-order") is None
