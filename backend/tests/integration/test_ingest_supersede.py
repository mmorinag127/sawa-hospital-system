from modernization_input_support import prepare_supersede_recognition, owned_recognition_pdf, recognize_ingest
import pytest
from modernization_input_support import registration_environment, authored_ingest_master, register_ingest  # noqa: F401

pytestmark = pytest.mark.usefixtures("authored_ingest_master")

import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.workers.ingest_mail_adapter import IngestEmailPayload
from src.services import order_service


def test_ingest_supersede_records_upload_time_versions_and_line_snapshots(tmp_path, monkeypatch):
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="msg-version-1",
        pdf_uri="file://version1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    second = IngestEmailPayload(
        message_id="msg-version-2",
        pdf_uri="file://version2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )

    prepare_supersede_recognition(monkeypatch, tmp_path)
    first_rows = [["12/23", "昼", "初回メニュー", "3"]]
    second_rows = [["12/24", "夕", "変更後メニュー", "7"]]
    first = owned_recognition_pdf(first, tmp_path, first_rows)
    second = owned_recognition_pdf(second, tmp_path, second_rows)
    created = recognize_ingest(first, monkeypatch, first_rows)
    updated = recognize_ingest(second, monkeypatch, second_rows)

    assert updated["id"] == created["id"]
    assert updated["document"] == second.pdf_uri
    assert updated["version_count"] == 2
    assert [version["version_no"] for version in updated["versions"]] == [2, 1]
    assert [version["message_id"] for version in updated["versions"]] == ["msg-version-2", "msg-version-1"]
    assert updated["current_version"]["message_id"] == "msg-version-2"
    assert updated["versions"][0]["received_at"].isoformat() == "2025-12-23T11:00:00"
    assert updated["versions"][1]["received_at"].isoformat() == "2025-12-23T10:00:00"
    assert updated["versions"][0]["line_snapshot"][0]["menu_name"] == "変更後メニュー"
    assert updated["versions"][1]["line_snapshot"][0]["menu_name"] == "初回メニュー"
    assert updated["versions"][0]["line_snapshot"][0]["quantity_original"] == 7
    assert updated["versions"][1]["line_snapshot"][0]["quantity_original"] == 3

    retried = recognize_ingest(second, monkeypatch, second_rows)

    assert retried["version_count"] == 2
    assert [version["version_no"] for version in retried["versions"]] == [2, 1]
    assert [version["message_id"] for version in retried["versions"]] == ["msg-version-2", "msg-version-1"]


def test_ingest_supersede_replaces_prior():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="msg-1",
        pdf_uri="file://dummy1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    second = IngestEmailPayload(
        message_id="msg-2",
        pdf_uri="file://dummy2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    register_ingest(first)
    register_ingest(second)
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
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    second = IngestEmailPayload(
        message_id="msg-cache-2",
        pdf_uri="file://cache2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    register_ingest(first)
    warmed = order_service.list_orders()
    assert len(warmed) == 1
    assert warmed[0]["document"] == "file://cache1.pdf"

    register_ingest(second)
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
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    second = IngestEmailPayload(
        message_id="msg-preview-2",
        pdf_uri="file://preview2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    created = register_ingest(first)
    preview = order_service.preview_existing_order_for_ingest(second)
    assert created is not None
    assert preview is not None
    assert preview["order_id"] == created["id"]
    assert preview["match_reason"] == "facility_week"
    assert preview["facility_code"] == "FAC001"
    assert preview["week_code"] == "2025-12@2025-12-22~2025-12-28"
    assert preview["existing_document"]["message_id"] == "msg-preview-1"
    assert preview["incoming_document"]["message_id"] == "msg-preview-2"


def test_split_page_ingest_does_not_supersede_facility_week_match():
    order_service.clear_all()
    first = IngestEmailPayload(
        message_id="upload:sha256:page-one:split:group-abc:1of2",
        pdf_uri="file://page1.pdf",
        received_at="2025-12-23T10:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    second = IngestEmailPayload(
        message_id="upload:sha256:page-two:split:group-abc:2of2",
        pdf_uri="file://page2.pdf",
        received_at="2025-12-23T11:00:00",
        facility_hint="FAC001",
        week_hint="2025-12@2025-12-22~2025-12-28",
    )

    created_first = register_ingest(first)
    preview = order_service.preview_existing_order_for_ingest(second)
    created_second = register_ingest(second)

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
        week_hint="2026-04@2026-04-13~2026-04-19",
    )
    created = register_ingest(first)
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
        week_hint="2026-04@2026-04-13~2026-04-19",
    )
    preview = order_service.preview_existing_order_for_ingest(retry)
    assert preview is not None
    assert preview["week_code"] == explicit_week

    updated = register_ingest(retry)
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
        week_hint="2025-12@2025-12-22~2025-12-28",
    )
    register_ingest(payload)
    assert len(order_service.list_orders()) == 1

    order_service.clear_all()
    assert order_service.list_orders() == []
