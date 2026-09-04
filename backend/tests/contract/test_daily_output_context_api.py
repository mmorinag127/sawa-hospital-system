from datetime import date
from copy import deepcopy

from fastapi.testclient import TestClient

from src.api import orders as orders_api
from src.main import app


def test_daily_output_context_returns_all_daily_sections(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")

    def fake_list_orders_by_line_date(target_date, facility_id=None, status=None):
        assert target_date == date(2026, 5, 15)
        assert facility_id is None
        assert status == "確定"
        return [{"id": "ORD-1", "facility": "FAC00005", "line_count": 3}]

    monkeypatch.setattr(orders_api.order_service, "list_orders_by_line_date", fake_list_orders_by_line_date)
    monkeypatch.setattr(
        orders_api.order_service,
        "get_daily_bag_summary",
        lambda *_args, **_kwargs: {"date": "2026-05-15", "groups": [{"menu_name": "チキンカツ"}]},
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_daily_bag_audit",
        lambda *_args, **_kwargs: {"date": "2026-05-15", "rule_based": {"finding_count": 0}},
    )
    monkeypatch.setattr(
        orders_api.total_service,
        "build_totals",
        lambda *_args, **_kwargs: [{"date": "2026-05-15", "menu_name": "チキンカツ", "quantity": 1}],
    )
    monkeypatch.setattr(
        orders_api.total_service,
        "build_daily_meal_counts",
        lambda *_args, **_kwargs: {
            "date": "2026-05-15",
            "groups": [{"daypart": "昼食", "counts": [{"diet_type": "diabetes", "quantity": 2}]}],
            "unconfirmed_orders": [],
            "inconsistent_counts": [],
        },
    )

    res = TestClient(app).get("/orders/daily-output-context?date=2026-05-15&status=%E7%A2%BA%E5%AE%9A")

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["sections"]["orders"]["data"]["orders"][0]["id"] == "ORD-1"
    assert payload["sections"]["daily_bags"]["data"]["groups"][0]["menu_name"] == "チキンカツ"
    assert payload["sections"]["daily_bags_audit"]["data"]["rule_based"]["finding_count"] == 0
    assert payload["sections"]["totals"]["data"]["rows"][0]["quantity"] == 1
    assert payload["sections"]["meal_counts"]["data"]["groups"][0]["counts"][0] == {
        "diet_type": "diabetes",
        "quantity": 2,
    }


def test_daily_output_context_reports_section_errors_without_hiding_source(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(orders_api.order_service, "list_orders_by_line_date", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_summary", lambda *_args, **_kwargs: {"groups": []})
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_audit", lambda *_args, **_kwargs: {"rule_based": {}})

    def raise_totals(*_args, **_kwargs):
        raise RuntimeError("postgres json distinct failure")

    monkeypatch.setattr(orders_api.total_service, "build_totals", raise_totals)
    monkeypatch.setattr(
        orders_api.total_service,
        "build_daily_meal_counts",
        lambda *_args, **_kwargs: {
            "groups": [],
            "unconfirmed_orders": [],
            "inconsistent_counts": [],
        },
    )

    res = TestClient(app).get("/orders/daily-output-context?date=2026-05-15")

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["sections"]["totals"]["status"] == "rejected"
    assert payload["sections"]["totals"]["error"]["type"] == "RuntimeError"
    assert "postgres json distinct failure" in payload["sections"]["totals"]["error"]["message"]


def test_daily_output_context_reports_meal_counts_section_errors(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(orders_api.order_service, "list_orders_by_line_date", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_summary", lambda *_args, **_kwargs: {"groups": []})
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_audit", lambda *_args, **_kwargs: {"rule_based": {}})
    monkeypatch.setattr(
        orders_api.total_service,
        "build_totals",
        lambda *_args, **_kwargs: [{"date": "2026-05-15", "menu_name": "チキンカツ", "quantity": 1}],
    )

    def raise_meal_counts(*_args, **_kwargs):
        raise RuntimeError("meal count summary failed")

    monkeypatch.setattr(orders_api.total_service, "build_daily_meal_counts", raise_meal_counts)

    res = TestClient(app).get("/orders/daily-output-context?date=2026-05-15")

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["sections"]["meal_counts"]["status"] == "rejected"
    assert payload["sections"]["meal_counts"]["error"]["type"] == "RuntimeError"
    assert "meal count summary failed" in payload["sections"]["meal_counts"]["error"]["message"]


def test_primary_section_does_not_run_output_materialization(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(orders_api.order_service, "list_orders_by_line_date", lambda *_a, **_k: [{"id": "ORD-1"}])
    monkeypatch.setattr(orders_api.total_service, "build_daily_meal_counts", lambda *_a: {"groups": []})

    def forbidden(*args, **kwargs):
        raise AssertionError("primary must not wait for output materialization")

    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_summary", forbidden)
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_audit", forbidden)
    monkeypatch.setattr(orders_api.total_service, "build_totals", forbidden)
    response = TestClient(app).get("/orders/daily-output-context?date=2026-09-13&section=primary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert set(payload["sections"]) == {"orders", "meal_counts"}
    assert payload["order_count"] == 1


def test_bags_section_reuses_exact_summary_for_audit(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    summary = {"date": "2026-09-13", "groups": []}
    calls = []

    def build_summary(*args, **kwargs):
        calls.append("summary")
        return summary

    def audit(*args, **kwargs):
        assert kwargs["summary"] is summary
        calls.append("audit")
        return {"rule_based": {"finding_count": 0}}

    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_summary", build_summary)
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_audit", audit)
    response = TestClient(app).get("/orders/daily-output-context?date=2026-09-13&section=bags")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert set(response.json()["sections"]) == {"daily_bags", "daily_bags_audit"}
    assert calls == ["summary", "audit"]


def test_failed_bag_source_blocks_audit_without_retry(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    calls = []

    def fail(*args, **kwargs):
        calls.append("summary")
        raise ValueError("saved sheet invalid")

    def forbidden(*args, **kwargs):
        calls.append("audit")
        raise AssertionError("must not retry a failed source")

    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_summary", fail)
    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_audit", forbidden)
    response = TestClient(app).get("/orders/daily-output-context?date=2026-09-13&section=bags")
    payload = response.json()
    assert payload["ok"] is False
    assert calls == ["summary"]
    for section in ("daily_bags", "daily_bags_audit"):
        assert payload["sections"][section]["status"] == "rejected"
        assert payload["sections"][section]["error"]["message"] == "saved sheet invalid"


def test_supplied_summary_audit_matches_standalone_without_mutation(monkeypatch):
    summary = {"groups": [{"daypart": "昼食", "menu_category": "主菜", "menu_name": "テスト", "diet_groups": [
        {"diet_type": "regular", "total_quantity": 10, "bag_type_groups": []},
    ]}]}
    original = deepcopy(summary)
    calls = []

    def build(*args, **kwargs):
        calls.append("build")
        return summary

    monkeypatch.setattr(orders_api.order_service, "get_daily_bag_summary", build)
    standalone = orders_api.order_service.get_daily_bag_audit(date(2026, 9, 13))
    supplied = orders_api.order_service.get_daily_bag_audit(date(2026, 9, 13), summary=summary)
    assert supplied == standalone
    assert summary == original
    assert calls == ["build"]


def test_unknown_section_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    response = TestClient(app).get("/orders/daily-output-context?date=2026-09-13&section=unknown")
    assert response.status_code == 422
