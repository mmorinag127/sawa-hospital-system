import pathlib
import sys
from datetime import date

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.shipping_tracking import (  # noqa: E402
    ShippingTrackingCurrent,
    ShippingTrackingEvent,
    ShippingTrackingLog,
)
from src.services import shipping_status_store  # noqa: E402


def _reset_tracking_state():
    with session_scope() as session:
        session.execute(delete(ShippingTrackingEvent))
        session.execute(delete(ShippingTrackingCurrent))
        session.execute(delete(ShippingTrackingLog))


def test_get_latest_statuses_for_facility_returns_serialized_items():
    _reset_tracking_state()
    inserted = shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "facility_name": "大和なでしこ",
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            }
        ],
        source="test",
    )

    assert inserted == 1

    result = shipping_status_store.get_latest_statuses_for_facility(["大和なでしこ"], limit=5)

    assert result["summary"]["pending"] == 1
    assert result["items"][0]["tracking_number"] == "1234-5678-9012"
    assert result["items"][0]["status"] == "配達中"
    assert result["items"][0]["events"] == []


def test_record_tracking_statuses_backfills_facility_name_and_ship_date():
    _reset_tracking_state()
    shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "facility_name": "大和なでしこ",
                "ship_date": date(2026, 3, 20),
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            }
        ],
        source="shipping_pdf_parse",
    )
    shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            }
        ],
        source="scheduled_refresh",
    )

    result = shipping_status_store.get_status_history(limit=5)
    latest_view = shipping_status_store.get_latest_status_view(view="all", limit=5)

    assert len(result["items"]) == 1
    assert result["items"][0]["facility_name"] == "大和なでしこ"
    assert result["items"][0]["ship_date"] == "2026-03-20"
    assert latest_view["items"][0]["facility_name"] == "大和なでしこ"
    assert latest_view["items"][0]["ship_date"] == "2026-03-20"
    assert latest_view["items"][0]["source"] == "scheduled_refresh"


def test_record_tracking_statuses_stores_events_and_skips_duplicate_logs():
    _reset_tracking_state()
    payload = {
        "tracking_number": "4906-4011-5910",
        "tracking_key": "490640115910",
        "facility_name": "春日苑 松茂",
        "ship_date": date(2026, 3, 24),
        "status": "輸送中",
        "delivered": False,
        "arrival_text": None,
        "events": [
            {
                "event_order": 0,
                "status": "↓集荷",
                "event_at_text": "03/24 14:17",
                "facility_name": "徳島営業所",
            },
            {
                "event_order": 1,
                "status": "⇒輸送中",
                "event_at_text": "03/24 17:01",
                "facility_name": "四国中継センター",
            },
        ],
    }

    shipping_status_store.record_tracking_statuses([payload], source="shipping_pdf_parse")
    latest_view = shipping_status_store.get_latest_status_view(view="all", limit=5)

    assert latest_view["items"][0]["tracking_number"] == "4906-4011-5910"
    assert len(latest_view["items"][0]["events"]) == 2
    assert latest_view["items"][0]["events"][0]["status"] == "↓集荷"
    assert latest_view["items"][0]["events"][1]["facility_name"] == "四国中継センター"

    shipping_status_store.record_tracking_statuses([payload], source="scheduled_refresh")
    history_after_duplicate = shipping_status_store.get_status_history(limit=10)
    assert len(history_after_duplicate["items"]) == 1

    shipping_status_store.record_tracking_statuses(
        [
            {
                **payload,
                "status": "配達完了",
                "delivered": True,
                "arrival_text": "3月25日 10時30分",
                "events": payload["events"]
                + [
                    {
                        "event_order": 2,
                        "status": "◎配達完了",
                        "event_at_text": "03/25 10:30",
                        "facility_name": "徳島営業所",
                    }
                ],
            }
        ],
        source="scheduled_refresh",
    )
    history_after_change = shipping_status_store.get_status_history(limit=10)
    latest_after_change = shipping_status_store.get_latest_status_view(view="all", limit=5)

    assert len(history_after_change["items"]) == 2
    assert latest_after_change["items"][0]["status"] == "配達完了"
    assert len(latest_after_change["items"][0]["events"]) == 3
    assert latest_after_change["items"][0]["events"][2]["status"] == "◎配達完了"


def test_get_latest_status_view_recent_filters_by_window_days():
    _reset_tracking_state()
    shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "facility_name": "大和なでしこ",
                "ship_date": date(2026, 3, 22),
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            },
            {
                "tracking_number": "9999-0000-1111",
                "tracking_key": "999900001111",
                "facility_name": "春日苑 松茂",
                "ship_date": date(2026, 4, 2),
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            },
        ],
        source="shipping_pdf_parse",
    )

    result = shipping_status_store.get_latest_status_view(
        view="recent",
        base_date=date(2026, 3, 24),
        window_days=3,
    )

    assert result["summary"]["total"] == 1
    assert result["groups"][0]["facility_name"] == "大和なでしこ"
    assert result["groups"][0]["ship_date"] == "2026-03-22"
    assert result["groups"][0]["group_date"] == "2026-03-22"
    assert result["groups"][0]["group_date_source"] == "ship_date"
    assert result["groups"][0]["reference_date"] == "2026-03-22"


def test_get_latest_status_view_can_skip_quota_payload():
    _reset_tracking_state()
    shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "facility_name": "大和なでしこ",
                "ship_date": date(2026, 3, 24),
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            }
        ],
        source="shipping_pdf_parse",
    )

    result = shipping_status_store.get_latest_status_view(view="active", include_quota=False)

    assert result["quota"] is None


def test_manual_shipping_status_marks_shipped_and_not_shipped_in_shared_view():
    _reset_tracking_state()
    shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "facility_name": "大和なでしこ",
                "ship_date": date(2026, 3, 24),
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            },
            {
                "tracking_number": "9999-0000-1111",
                "tracking_key": "999900001111",
                "facility_name": "春日苑 松茂",
                "ship_date": date(2026, 3, 24),
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            },
        ],
        source="shipping_pdf_parse",
    )

    shipped = shipping_status_store.mark_tracking_status("1234-5678-9012", status="発送済み")
    not_shipped = shipping_status_store.mark_tracking_status("9999-0000-1111", status="発送しなかった")
    active = shipping_status_store.get_latest_status_view(view="active", limit=10)
    all_view = shipping_status_store.get_latest_status_view(view="all", limit=10)
    pending_numbers = shipping_status_store.get_latest_pending_tracking_numbers(limit=10)

    assert shipped["status"] == "発送済み"
    assert shipped["delivered"] is True
    assert not_shipped["status"] == "発送しなかった"
    assert not_shipped["delivered"] is False
    assert active["summary"]["total"] == 0
    assert all_view["summary"]["delivered"] == 1
    assert all_view["summary"]["not_shipped"] == 1
    assert "1234-5678-9012" not in pending_numbers
    assert "9999-0000-1111" not in pending_numbers


def test_get_latest_status_view_rejects_invalid_view():
    _reset_tracking_state()

    try:
        shipping_status_store.get_latest_status_view(view="bogus")
    except ValueError as exc:
        assert str(exc) == "view must be active, all, attention, or recent"
    else:
        raise AssertionError("expected ValueError for invalid latest status view")
