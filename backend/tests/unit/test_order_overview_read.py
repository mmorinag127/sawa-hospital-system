"""Navigation pages keep complete facets and progress outside the visible page."""
from datetime import date, datetime

from src.db import session_scope
from src.models.order import Order, OrderLine
from src.services.order_overview_read import list_daily_order_facts, list_facility_orders


def test_paging_preserves_complete_scope_and_daily_line_count(isolated_formal_schema):
    with session_scope() as session:
        for index, (facility, status) in enumerate((("A", "要確認"), ("A", "確定"), ("B", "未着"))):
            identifier = f"fact-{index}"
            session.add(Order(id=identifier, facility_code=facility, week_code="2026-10",
                              status=status, document_uri="fixture", message_id=identifier,
                              received_at=datetime(2026, 10, 1, index)))
        session.flush()
        session.add_all([OrderLine(id=f"line-{index}", order_id="fact-0", date=date(2026, 10, 1),
                                  quantity_original=value) for index, value in enumerate((0, None, 7))])
    result = list_facility_orders(facility="A", week="2026-10", limit=1)
    assert result["total"] == 2
    assert [row["id"] for row in result["orders"]] == ["fact-1"]
    assert result["facility_options"] == ["A", "B"]
    assert result["progress"] == [{"week": "2026-10", "pending": 0, "review": 1, "confirmed": 1, "error": 0}]
    assert [row["id"] for row in list_facility_orders(facility="A", limit=1, offset=1)["orders"]] == ["fact-0"]
    daily = list_daily_order_facts(date(2026, 10, 1), facility="A")
    assert len(daily) == 1 and daily[0]["id"] == "fact-0" and daily[0]["line_count"] == 3
    with session_scope() as session:
        assert [row.quantity_original for row in session.query(OrderLine).order_by(OrderLine.id)] == [0, None, 7]
