import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.workers.ingest_mail_adapter import IngestEmailPayload
from src.services import order_service


def test_facility_unresolved_sets_unknown():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-3",
        pdf_uri="file://dummy.pdf",
        received_at="2025-12-23T12:00:00",
        facility_hint=None,
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    with pytest.raises(ValueError, match="ingest_facility_selection_missing"):
        order_service.create_order_from_ingest(payload)
    assert order_service.list_orders() == []
