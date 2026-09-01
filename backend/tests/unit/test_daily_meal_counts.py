from contextlib import contextmanager
from datetime import date

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
        ("ORD-1", "FAC-001", "朝食", "regular", "2F", None, 10),
        ("ORD-1", "FAC-001", "朝食", "regular", "2F", None, 10),
        ("ORD-1", "FAC-001", "朝食", "diabetes", "2F", 3, 2),
        ("ORD-1", "FAC-001", "昼食", "regular", "2F", None, 8),
        ("ORD-1", "FAC-001", "夕食", "soft_mixer", "2F", None, 4),
        ("ORD-1", "FAC-001", "夕食", "soft", "2F", None, None),
        ("ORD-1", "FAC-001", "夕食", "regular", "3F", None, 4),
        ("ORD-1", "FAC-001", "夕食", "regular", "3F", None, 5),
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
