import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


client = TestClient(app)


def _create_order(*, message_id: str, week_hint: str, status: str = "要確認", facility_hint: str = "FAC00001") -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 6, 9, 0, 0),
        facility_hint=facility_hint,
        week_hint=week_hint,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service.set_status(str(order.get("id") or ""), status)
    return order


def test_archive_week_endpoint_allows_pending_orders() -> None:
    order_service.clear_all()
    pending = _create_order(
        message_id="msg-archive-block-001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="要確認",
        facility_hint="FAC00001",
    )
    ready = _create_order(
        message_id="msg-archive-block-002",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="確定",
        facility_hint="FAC00002",
    )

    res = client.post("/orders/archive-week", json={"week_value": "2026-04@2026-04-05~2026-04-11"})

    assert res.status_code == 200
    payload = res.json()
    assert payload["archived_count"] == 2
    assert set(payload["archived_order_ids"]) == {
        str(ready.get("id")),
        str(pending.get("id")),
    }


def test_list_orders_can_exclude_archived_and_include_them_explicitly() -> None:
    order_service.clear_all()
    archived = _create_order(
        message_id="msg-archive-list-001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="確定",
    )
    active = _create_order(
        message_id="msg-archive-list-002",
        week_hint="2026-04@2026-04-12~2026-04-18",
        status="確定",
    )

    archive_res = client.post("/orders/archive-week", json={"week_value": "2026-04@2026-04-05~2026-04-11"})
    assert archive_res.status_code == 200

    filtered_res = client.get("/orders?include_ocr=false&include_archived=false")
    assert filtered_res.status_code == 200
    default_ids = {item["id"] for item in filtered_res.json()["orders"]}
    assert str(active.get("id")) in default_ids
    assert str(archived.get("id")) not in default_ids

    default_res = client.get("/orders?include_ocr=false")
    assert default_res.status_code == 200
    all_default_ids = {item["id"] for item in default_res.json()["orders"]}
    assert str(active.get("id")) in all_default_ids
    assert str(archived.get("id")) in all_default_ids

    all_res = client.get("/orders?include_ocr=false&include_archived=true")
    assert all_res.status_code == 200
    rows = {item["id"]: item for item in all_res.json()["orders"]}
    assert str(active.get("id")) in rows
    assert str(archived.get("id")) in rows
    assert rows[str(archived.get("id"))]["is_archived"] is True
    assert rows[str(archived.get("id"))]["archived_at"]


def test_archive_and_unarchive_week_round_trip() -> None:
    order_service.clear_all()
    first = _create_order(
        message_id="msg-archive-roundtrip-001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="確定",
    )
    second = _create_order(
        message_id="msg-archive-roundtrip-002",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="エラー",
        facility_hint="FAC00002",
    )

    archive_res = client.post("/orders/archive-week", json={"week_value": "2026-04@2026-04-05~2026-04-11"})
    assert archive_res.status_code == 200
    archive_payload = archive_res.json()
    assert archive_payload["archived_count"] == 2
    assert set(archive_payload["archived_order_ids"]) == {str(first.get("id")), str(second.get("id"))}

    hidden_res = client.get("/orders?include_ocr=false&include_archived=false")
    assert hidden_res.status_code == 200
    hidden_ids = {item["id"] for item in hidden_res.json()["orders"]}
    assert str(first.get("id")) not in hidden_ids
    assert str(second.get("id")) not in hidden_ids

    restore_res = client.post("/orders/unarchive-week", json={"week_value": "2026-04@2026-04-05~2026-04-11"})
    assert restore_res.status_code == 200
    restore_payload = restore_res.json()
    assert restore_payload["restored_count"] == 2
    assert set(restore_payload["restored_order_ids"]) == {str(first.get("id")), str(second.get("id"))}

    visible_res = client.get("/orders?include_ocr=false&include_archived=false")
    assert visible_res.status_code == 200
    visible_ids = {item["id"] for item in visible_res.json()["orders"]}
    assert str(first.get("id")) in visible_ids
    assert str(second.get("id")) in visible_ids


def test_archive_week_can_target_visible_group_order_ids_even_when_stored_week_is_month_only() -> None:
    order_service.clear_all()
    month_only = _create_order(
        message_id="msg-archive-visible-group-001",
        week_hint="2026-04",
        status="確定",
    )

    archive_res = client.post(
        "/orders/archive-week",
        json={
            "week_value": "2026-04@2026-04-05~2026-04-11",
            "order_ids": [str(month_only.get("id"))],
        },
    )
    assert archive_res.status_code == 200
    archive_payload = archive_res.json()
    assert archive_payload["archived_count"] == 1
    assert archive_payload["archived_order_ids"] == [str(month_only.get("id"))]

    hidden_res = client.get("/orders?include_ocr=false&include_archived=false")
    assert hidden_res.status_code == 200
    hidden_ids = {item["id"] for item in hidden_res.json()["orders"]}
    assert str(month_only.get("id")) not in hidden_ids

    restore_res = client.post(
        "/orders/unarchive-week",
        json={
            "week_value": "2026-04@2026-04-05~2026-04-11",
            "order_ids": [str(month_only.get("id"))],
        },
    )
    assert restore_res.status_code == 200


def test_archive_single_order_endpoint_hides_only_target_order_from_default_list() -> None:
    order_service.clear_all()
    target = _create_order(
        message_id="msg-archive-single-001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="要確認",
    )
    sibling = _create_order(
        message_id="msg-archive-single-002",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="要確認",
        facility_hint="FAC00002",
    )

    archive_res = client.post(f"/orders/{target['id']}/archive")
    assert archive_res.status_code == 200
    payload = archive_res.json()
    assert payload["order_id"] == str(target["id"])
    assert payload["archived"] is True
    assert payload["changed"] is True
    assert payload["archived_at"]
    assert payload["archived_by"] == "operator"

    hidden_res = client.get("/orders?include_ocr=false&include_archived=false")
    assert hidden_res.status_code == 200
    hidden_ids = {item["id"] for item in hidden_res.json()["orders"]}
    assert str(target["id"]) not in hidden_ids
    assert str(sibling["id"]) in hidden_ids


def test_unarchive_single_order_endpoint_restores_target_order_to_default_list() -> None:
    order_service.clear_all()
    target = _create_order(
        message_id="msg-unarchive-single-001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        status="確定",
    )

    archive_res = client.post(f"/orders/{target['id']}/archive")
    assert archive_res.status_code == 200

    unarchive_res = client.post(f"/orders/{target['id']}/unarchive")
    assert unarchive_res.status_code == 200
    payload = unarchive_res.json()
    assert payload["order_id"] == str(target["id"])
    assert payload["archived"] is False
    assert payload["changed"] is True
    assert payload["archived_at"] is None
    assert payload["archived_by"] is None

    visible_res = client.get("/orders?include_ocr=false&include_archived=false")
    assert visible_res.status_code == 200
    visible_ids = {item["id"] for item in visible_res.json()["orders"]}
    assert str(target["id"]) in visible_ids
