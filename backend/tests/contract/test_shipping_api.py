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

    def _record(statuses, *, source, facility_by_tracking=None):
        captured["count"] = len(list(statuses))
        captured["source"] = source
        captured["facility_by_tracking"] = dict(facility_by_tracking or {})
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
