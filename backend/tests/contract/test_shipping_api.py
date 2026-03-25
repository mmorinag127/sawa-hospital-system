import base64
import importlib
import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
import src.api.orders as orders_api  # noqa: E402
import src.api.shipping as shipping_api  # noqa: E402
from src.main import app  # noqa: E402
from src.services.shipping_service import ShippingRecord  # noqa: E402
from src.services.sagawa_tracking_service import TrackingStatus  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _enable_operator_auth(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    return _basic_header("operator", "secret")


def test_shipping_parse_auto_registers_tracking_statuses(monkeypatch, tmp_path):
    headers = _enable_operator_auth(monkeypatch)
    output_path = tmp_path / "shipping.xlsx"
    output_path.write_bytes(b"dummy-xlsx")

    monkeypatch.setattr(
        shipping_api.shipping_service,
        "extract_shipping_records",
        lambda _content: [
            ShippingRecord(
                ship_date=datetime(2026, 3, 12).date(),
                arrival_date=None,
                tracking_number="1234-5678-9012",
                facility_name="テスト施設",
            )
        ],
    )
    monkeypatch.setattr(shipping_api.shipping_service, "build_shipping_excel", lambda _records: output_path)
    monkeypatch.setattr(
        shipping_api.shipping_service,
        "get_tracking_status_records",
        lambda _numbers: [
            TrackingStatus(
                tracking_number="1234-5678-9012",
                tracking_key="123456789012",
                status="配達中",
                delivered=False,
                arrival_text=None,
            )
        ],
    )
    captured: dict[str, object] = {}

    def _record(statuses, *, source, facility_by_tracking=None, ship_date_by_tracking=None):
        captured["count"] = len(list(statuses))
        captured["source"] = source
        captured["facility_by_tracking"] = dict(facility_by_tracking or {})
        captured["ship_date_by_tracking"] = dict(ship_date_by_tracking or {})
        return 1

    monkeypatch.setattr(shipping_api.shipping_status_store, "record_tracking_statuses", _record)

    client = TestClient(app)
    res = client.post(
        "/shipping/parse",
        files={"file": ("shipping.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        headers=headers,
    )

    assert res.status_code == 200
    assert captured["count"] == 1
    assert captured["source"] == "shipping_pdf_parse"
    assert captured["facility_by_tracking"] == {"1234-5678-9012": "テスト施設"}
    assert captured["ship_date_by_tracking"] == {"1234-5678-9012": datetime(2026, 3, 12).date()}


def test_shipping_enrich_auto_registers_tracking_status_metadata(monkeypatch, tmp_path):
    headers = _enable_operator_auth(monkeypatch)
    output_path = tmp_path / "shipping_enriched.xlsx"
    output_path.write_bytes(b"dummy-xlsx")

    monkeypatch.setattr(
        shipping_api.shipping_service,
        "enrich_tracking_excel",
        lambda _content: (
            output_path,
            {
                "total_rows": 1,
                "lookup_count": 1,
                "delivered_rows": 0,
                "pending_rows": 1,
                "updated_arrival_rows": 0,
                "error_rows": 0,
                "all_delivered": False,
                "_status_items": [
                    TrackingStatus(
                        tracking_number="1234-5678-9012",
                        tracking_key="123456789012",
                        status="配達中",
                        delivered=False,
                        arrival_text=None,
                    ).serialize()
                ],
                "_facility_by_tracking": {"123456789012": "テスト施設"},
                "_ship_date_by_tracking": {"123456789012": datetime(2026, 3, 14).date()},
            },
        ),
    )
    captured: dict[str, object] = {}

    def _record(statuses, *, source, facility_by_tracking=None, ship_date_by_tracking=None):
        captured["count"] = len(list(statuses))
        captured["source"] = source
        captured["facility_by_tracking"] = dict(facility_by_tracking or {})
        captured["ship_date_by_tracking"] = dict(ship_date_by_tracking or {})
        return 1

    monkeypatch.setattr(shipping_api.shipping_status_store, "record_tracking_statuses", _record)

    client = TestClient(app)
    res = client.post(
        "/shipping/enrich-excel",
        files={"file": ("shipping.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )

    assert res.status_code == 200
    assert captured["count"] == 1
    assert captured["source"] == "excel_enrich"
    assert captured["facility_by_tracking"] == {"123456789012": "テスト施設"}
    assert captured["ship_date_by_tracking"] == {"123456789012": datetime(2026, 3, 14).date()}


def test_refresh_pending_shipping_statuses(monkeypatch):
    headers = _enable_operator_auth(monkeypatch)
    monkeypatch.setattr(
        shipping_api.shipping_status_store,
        "get_latest_pending_tracking_numbers",
        lambda **_kwargs: ["1234-5678-9012", "9999-0000-1111"],
    )
    monkeypatch.setattr(
        shipping_api.shipping_service,
        "get_tracking_status_records",
        lambda _numbers: [
            TrackingStatus(
                tracking_number="1234-5678-9012",
                tracking_key="123456789012",
                status="配達完了",
                delivered=True,
                arrival_text="3月12日 14時30分",
            ),
            TrackingStatus(
                tracking_number="9999-0000-1111",
                tracking_key="999900001111",
                status="配達中",
                delivered=False,
                arrival_text=None,
            ),
        ],
    )
    monkeypatch.setattr(shipping_api.shipping_status_store, "record_tracking_statuses", lambda *_args, **_kwargs: 2)

    client = TestClient(app)
    res = client.post("/shipping/status/refresh-pending", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["tracking_count"] == 2
    assert body["updated"] == 2
    assert body["delivered"] == 1
    assert body["pending"] == 1


def test_get_shipping_status_latest(monkeypatch):
    headers = _enable_operator_auth(monkeypatch)
    monkeypatch.setattr(
        shipping_api.shipping_status_store,
        "get_latest_status_view",
        lambda **kwargs: {
            "view": kwargs.get("view"),
            "window_days": kwargs.get("window_days"),
            "base_date": kwargs.get("base_date").isoformat() if kwargs.get("base_date") else None,
            "facility_names": kwargs.get("facility_names"),
            "source": kwargs.get("source"),
            "quota": None if not kwargs.get("include_quota") else {"alert_level": "ok"},
            "groups": [
                {
                    "ship_date": "2026-03-24",
                    "group_date": "2026-03-24",
                    "facility_name": "テスト施設",
                    "items": [
                        {
                            "tracking_number": "1234-5678-9012",
                            "status": "配達中",
                        }
                    ],
                }
            ],
            "summary": {"total": 1, "delivered": 0, "pending": 1, "errors": 0, "all_delivered": False},
        },
    )

    client = TestClient(app)
    res = client.get(
        "/shipping/status/latest",
        params=[
            ("view", "recent"),
            ("base_date", "2026-03-24"),
            ("window_days", "5"),
            ("facility_name", "テスト施設"),
            ("facility_name", "春日苑 松茂"),
            ("source", "scheduled_refresh"),
            ("include_quota", "false"),
        ],
        headers=headers,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["view"] == "recent"
    assert body["window_days"] == 5
    assert body["base_date"] == "2026-03-24"
    assert body["facility_names"] == ["テスト施設", "春日苑 松茂"]
    assert body["source"] == "scheduled_refresh"
    assert body["quota"] is None
    assert body["groups"][0]["items"][0]["tracking_number"] == "1234-5678-9012"


def test_get_shipping_status_latest_rejects_invalid_view(monkeypatch):
    headers = _enable_operator_auth(monkeypatch)

    def _raise(**_kwargs):
        raise ValueError("view must be active, all, attention, or recent")

    monkeypatch.setattr(shipping_api.shipping_status_store, "get_latest_status_view", _raise)

    client = TestClient(app)
    res = client.get("/shipping/status/latest", params={"view": "bogus"}, headers=headers)

    assert res.status_code == 400
    assert res.json()["detail"] == "view must be active, all, attention, or recent"


def test_order_shipping_statuses_endpoint(monkeypatch):
    headers = _enable_operator_auth(monkeypatch)
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda order_id: {"id": order_id, "facility": "FAC00001"},
    )
    monkeypatch.setattr(
        orders_api.config_service,
        "get_facility_config",
        lambda _facility_id: {"facility_name": "大和なでしこ", "aliases": ["大和"]},
    )
    monkeypatch.setattr(
        orders_api.shipping_status_store,
        "get_latest_statuses_for_facility",
        lambda facility_names, **_kwargs: {
            "facility_names": facility_names,
            "summary": {"total": 1, "delivered": 0, "pending": 1, "all_delivered": False},
            "items": [
                {
                    "tracking_number": "1234-5678-9012",
                    "status": "配達中",
                    "delivered": False,
                    "arrival_text": None,
                    "looked_up_at": "2026-03-12T10:00:00",
                }
            ],
        },
    )

    client = TestClient(app)
    res = client.get("/orders/ORD123/shipping-statuses", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["facility_names"] == ["大和なでしこ", "大和"]
    assert body["summary"]["pending"] == 1
    assert body["items"][0]["tracking_number"] == "1234-5678-9012"
