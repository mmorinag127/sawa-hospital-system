import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from fastapi.testclient import TestClient
from src.services import order_service
from src.services import output_builder
from src.api import orders as orders_api
from src.main import app  # noqa: E402


def _save_simple_draft(order_id: str, quantity: str = "5") -> None:
    saved, error = order_service.save_ocr_sheet_exact(
        order_id,
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[["12/23", "朝", "Menu A", quantity]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None


def test_status_flow_confirm():
    order_service.clear_all()
    order = order_service.create_order_from_ingest(
        payload=type(
            "obj",
            (),
            {
                "message_id": "m2",
                "pdf_uri": "file://dummy.pdf",
                "received_at": "2025-12-23T10:10:00",
                "facility_hint": "FAC00001",
                "week_hint": "WEK2025W52",
            },
        )
    )
    _save_simple_draft(order["id"], quantity="5")
    confirmed = order_service.confirm_order(order["id"])
    assert confirmed["status"] == "確定"
    # Outputs should be buildable without error even if empty lines
    output_builder.build_outputs(order["id"])


def test_confirm_endpoint_triggers_outputs(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = order_service.create_order_from_ingest(
        payload=type(
            "obj",
            (),
            {
                "message_id": "m3",
                "pdf_uri": "file://dummy.pdf",
                "received_at": "2025-12-23T10:10:00",
                "facility_hint": "FAC00001",
                "week_hint": "WEK2025W52",
            },
        )
    )
    _save_simple_draft(order["id"], quantity="5")
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "apply_ready",
            "apply_gate": {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
        },
    )
    res2 = client.post(f"/orders/{order['id']}/confirm")
    assert res2.status_code == 202
