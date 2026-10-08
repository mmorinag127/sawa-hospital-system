"""Cursor contract on owned formal SQLite; not production database proof."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src import db
from src.models.order import Order, OrderLine
from src.services import order_service


@pytest.mark.parametrize("value,expected", [
    (datetime(2026, 2, 15, 9, 3), "2026-02-15T09:03:00"),
    ("2026-02-15 09:03:00", "2026-02-15T09:03:00"),
    ("2026-02-15T09:03:00", "2026-02-15T09:03:00"),
    ("20260215T090300", "2026-02-15T09:03:00"),
    ("2026-W07-7T09:03:00", "2026-02-15T09:03:00"),
    ("2026-02-15 09:03:00.123456", "2026-02-15T09:03:00.123456"),
    (datetime(2026, 2, 15, 9, 3, 0, 123456), "2026-02-15T09:03:00.123456"),
    ("2026-02-15T09:03:00.123456Z", "2026-02-15T09:03:00.123456+00:00"),
    ("2026-02-15T09:03:00.123456+09:00", "2026-02-15T09:03:00.123456+09:00"),
    ("2026-02-15T09:03:00.123456-03:30", "2026-02-15T09:03:00.123456-03:30"),
    (datetime(2026, 2, 15, 9, 3, 0, 123456, tzinfo=timezone(timedelta(hours=9))),
        "2026-02-15T09:03:00.123456+09:00"),
])
def test_canonical_cursor_preserves_instant_offset_and_microseconds(value, expected):
    cursor = order_service.build_order_list_cursor([{"id": "owned-order", "received_at": value}])
    assert cursor == {"id": "owned-order", "received_at": expected}
    original = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    emitted = datetime.fromisoformat(cursor["received_at"])
    assert emitted == original
    assert emitted.utcoffset() == original.utcoffset()
    assert emitted.microsecond == original.microsecond
    consumed = order_service._parse_order_list_cursor_received_at(cursor["received_at"])
    expected_consumer = original.astimezone(timezone.utc).replace(tzinfo=None) if original.tzinfo else original
    assert consumed == expected_consumer


@pytest.mark.parametrize("value", ["not-a-timestamp", "2026-02-30T09:03:00",
    "2026-02-15T25:03:00", "2026-02-15T09:03:00+invalid", "2026-02-15 garbage"])
def test_nonempty_invalid_datetime_never_emits_cursor(value):
    with pytest.raises(ValueError, match="^order_list_cursor_datetime_invalid$"):
        order_service.build_order_list_cursor([{"id": "owned-order", "received_at": value}])


@pytest.mark.parametrize("value", [None, "", " "])
def test_missing_datetime_is_explicit_error(value):
    with pytest.raises(ValueError, match="^order_list_cursor_datetime_required$"):
        order_service.build_order_list_cursor([{"id": "owned-order", "received_at": value}])


@pytest.mark.parametrize("value", ["2026-02-15", "20260215", "2026-W07-7",
    "2026W077", "2026-W07", "2026W07", " 2026-02-15 "])
def test_date_only_never_guesses_midnight(value):
    assert isinstance(date.fromisoformat(value.strip()), date)
    with pytest.raises(ValueError, match="^order_list_cursor_datetime_invalid$"):
        order_service.build_order_list_cursor([{"id": "owned-order", "received_at": value}])


@pytest.mark.parametrize("value", [date(2026, 2, 15), 0, 20260215, False, {}, []])
def test_unsupported_object_never_emits_cursor(value):
    with pytest.raises(ValueError, match="^order_list_cursor_datetime_invalid$"):
        order_service.build_order_list_cursor([{"id": "owned-order", "received_at": value}])


@pytest.mark.parametrize("owner", [None, "", " "])
def test_missing_identity_never_emits_cursor(owner):
    with pytest.raises(ValueError, match="^order_list_cursor_identity_required$"):
        order_service.build_order_list_cursor([{"id": owner, "received_at": datetime(2026, 2, 15, 9, 3)}])


def test_empty_order_list_has_no_cursor():
    assert order_service.build_order_list_cursor([]) is None


def test_formal_sqlite_three_pages_preserve_timestamp_id_boundaries_and_quantities(isolated_formal_schema):
    stamp = datetime(2026, 10, 9, 9, 3, 0, 123456)
    identities = ["cursor-E", "cursor-D", "cursor-C", "cursor-B", "cursor-A"]
    times = [stamp + timedelta(seconds=1), stamp, stamp, stamp, stamp - timedelta(seconds=1)]
    order_service.invalidate_orders_cache()
    try:
        with db.session_scope() as session:
            for owner, received_at in zip(identities, times):
                session.add(Order(id=owner, message_id=owner, document_uri="file://cursor-only.pdf",
                    received_at=received_at, status="要確認"))
            session.flush()
            session.add_all([
                OrderLine(id="cursor-zero", order_id="cursor-D", quantity_original=0, quantity_corrected=None),
                OrderLine(id="cursor-null", order_id="cursor-C", quantity_original=None, quantity_corrected=0),
            ])
        pages = []
        cursor = None
        for page_index in range(3):
            page = order_service.list_orders_page(include_archived=False, limit=2,
                before_received_at=cursor["received_at"] if cursor else None,
                before_id=cursor["id"] if cursor else None)
            pages.append(page)
            assert [row["id"] for row in page["orders"]] == identities[page_index * 2:page_index * 2 + 2]
            cursor = page["next_cursor"]
            if page_index < 2:
                assert page["has_more"] is True
                assert cursor["id"] == identities[page_index * 2 + 1]
                assert cursor["received_at"] == stamp.isoformat()
            else:
                assert page["has_more"] is False and cursor is None
        observed = [row["id"] for page in pages for row in page["orders"]]
        assert observed == identities and len(set(observed)) == 5
        with db.session_scope() as session:
            assert session.execute(select(Order.id).order_by(Order.received_at.desc(), Order.id.desc())).scalars().all() == identities
            zero = session.get(OrderLine, "cursor-zero")
            null = session.get(OrderLine, "cursor-null")
            assert (zero.quantity_original, zero.quantity_corrected) == (0, None)
            assert (null.quantity_original, null.quantity_corrected) == (None, 0)
            assert [session.get(Order, owner).received_at for owner in identities] == times
    finally:
        order_service.invalidate_orders_cache()
