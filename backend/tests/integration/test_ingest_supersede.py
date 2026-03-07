import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.workers.ingest_mail_adapter import IngestEmailPayload
from src.services import order_service


def test_ingest_supersede_replaces_prior():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="msg-1",
        pdf_uri="file://dummy1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    second = IngestEmailPayload(
        message_id="msg-2",
        pdf_uri="file://dummy2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    order_service.create_order_from_ingest(first)
    order_service.create_order_from_ingest(second)
    orders = order_service.list_orders()
    assert len(orders) == 1
    assert orders[0]["document"] == "file://dummy2.pdf"
    assert len(orders[0]["superseded_document_ids"]) == 1


def test_ingest_supersede_replaces_prior_even_when_list_cache_is_warm():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="msg-cache-1",
        pdf_uri="file://cache1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    second = IngestEmailPayload(
        message_id="msg-cache-2",
        pdf_uri="file://cache2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    order_service.create_order_from_ingest(first)
    warmed = order_service.list_orders()
    assert len(warmed) == 1
    assert warmed[0]["document"] == "file://cache1.pdf"

    order_service.create_order_from_ingest(second)
    latest = order_service.list_orders()
    assert len(latest) == 1
    assert latest[0]["document"] == "file://cache2.pdf"
    assert len(latest[0]["superseded_document_ids"]) == 1


def test_clear_all_invalidates_list_orders_cache():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-clear-1",
        pdf_uri="file://clear1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    order_service.create_order_from_ingest(payload)
    assert len(order_service.list_orders()) == 1

    order_service.clear_all()
    assert order_service.list_orders() == []
