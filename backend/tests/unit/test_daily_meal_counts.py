from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

from src.services import total_service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, confirmed_lines, unconfirmed_rows):
        self._results = iter((_Result(confirmed_lines), _Result(unconfirmed_rows)))
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return next(self._results)


def test_daily_meal_counts_preserve_diet_types_and_separate_dayparts(monkeypatch):
    confirmed_lines = [
        (SimpleNamespace(order_id="ORD-1", area_id="2F", daypart="朝食", diet_type="regular", quantity_corrected=None, quantity_original=10), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="2F", daypart="朝食", diet_type="regular", quantity_corrected=None, quantity_original=10), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="2F", daypart="朝食", diet_type="diabetes", quantity_corrected=3, quantity_original=2), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="2F", daypart="昼食", diet_type="regular", quantity_corrected=None, quantity_original=8), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="2F", daypart="夕食", diet_type="soft_mixer", quantity_corrected=None, quantity_original=4), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="2F", daypart="夕食", diet_type="soft", quantity_corrected=None, quantity_original=None), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="3F", daypart="夕食", diet_type="regular", quantity_corrected=None, quantity_original=4), "FAC-001"),
        (SimpleNamespace(order_id="ORD-1", area_id="3F", daypart="夕食", diet_type="regular", quantity_corrected=None, quantity_original=5), "FAC-001"),
    ]

    session = _Session(
        confirmed_lines,
        [("ORD-PENDING", "FAC-002", "要確認"), ("ORD-ERROR", None, "エラー")],
    )

    @contextmanager
    def fake_session_scope():
        yield session

    monkeypatch.setattr(total_service, "session_scope", fake_session_scope)

    payload = total_service.build_daily_meal_counts(date(2026, 9, 1))

    assert payload["date"] == "2026-09-01"
    assert payload["groups"] == [
        {
            "daypart": "朝食",
            "counts": [
                {"diet_type": "diabetes", "quantity": 3.0},
                {"diet_type": "regular", "quantity": 10.0},
            ],
        },
        {
            "daypart": "昼食",
            "counts": [{"diet_type": "regular", "quantity": 8.0}],
        },
        {
            "daypart": "夕食",
            "counts": [{"diet_type": "soft_mixer", "quantity": 4.0}],
        },
    ]
    assert payload["unconfirmed_orders"] == [
        {"order_id": "ORD-ERROR", "facility_id": None, "status": "エラー"},
        {"order_id": "ORD-PENDING", "facility_id": "FAC-002", "status": "要確認"},
    ]
    assert payload["inconsistent_counts"] == [
        {
            "order_id": "ORD-1",
            "facility_id": "FAC-001",
            "daypart": "夕食",
            "diet_type": "regular",
            "area_id": "3F",
            "quantities": [4.0, 5.0],
        }
    ]
    confirmed_query = str(session.queries[0])
    assert "orders.status" in confirmed_query
    assert "order_lines.confirmed_snapshot_id IS NOT NULL" in confirmed_query
