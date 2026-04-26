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


def test_preview_existing_order_for_ingest_reports_facility_week_match():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="msg-preview-1",
        pdf_uri="file://preview1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    second = IngestEmailPayload(
        message_id="msg-preview-2",
        pdf_uri="file://preview2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    created = order_service.create_order_from_ingest(first)
    preview = order_service.preview_existing_order_for_ingest(second)
    assert created is not None
    assert preview is not None
    assert preview["order_id"] == created["id"]
    assert preview["match_reason"] == "facility_week"
    assert preview["facility_code"] == "FAC001"
    assert preview["week_code"] == "WEK2025W52"
    assert preview["existing_document"]["message_id"] == "msg-preview-1"
    assert preview["incoming_document"]["message_id"] == "msg-preview-2"


def test_split_page_ingest_does_not_supersede_facility_week_match():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="upload:sha256:page-one:split:group-abc:1of2",
        pdf_uri="file://page1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )
    second = IngestEmailPayload(
        message_id="upload:sha256:page-two:split:group-abc:2of2",
        pdf_uri="file://page2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="WEK2025W52",
    )

    created_first = order_service.create_order_from_ingest(first)
    preview = order_service.preview_existing_order_for_ingest(second)
    created_second = order_service.create_order_from_ingest(second)

    assert created_first is not None
    assert created_second is not None
    assert preview is None
    orders = order_service.list_orders()
    assert len(orders) == 2
    assert {item["document"] for item in orders} == {"file://page1.pdf", "file://page2.pdf"}


def test_ingest_message_retry_preserves_explicit_selected_week():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="msg-explicit-week-retry-001",
        pdf_uri="file://explicit-week-1.pdf",
        received_at="2026-04-16T10:00:00",
        facility_hint="FAC001",
        week_hint="2026-04",
    )
    created = order_service.create_order_from_ingest(first)
    assert created is not None
    assert order_service.set_week(created["id"], "2026-05@2026-05-01~2026-05-02") is True
    refreshed = order_service.get_order_by_id(created["id"])
    assert refreshed is not None
    explicit_week = refreshed["persisted_week_value"]

    retry = IngestEmailPayload(
        message_id="msg-explicit-week-retry-001",
        pdf_uri="file://explicit-week-2.pdf",
        received_at="2026-04-16T11:00:00",
        facility_hint="FAC001",
        week_hint="2026-04",
    )
    preview = order_service.preview_existing_order_for_ingest(retry)
    assert preview is not None
    assert preview["week_code"] == explicit_week

    updated = order_service.create_order_from_ingest(retry)
    assert updated is not None
    assert updated["id"] == created["id"]
    refreshed_after = order_service.get_order_by_id(created["id"])
    assert refreshed_after is not None
    assert refreshed_after["persisted_week_value"] == explicit_week


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
