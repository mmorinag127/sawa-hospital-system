from test_line_corrections import _save_canonical_sheet

from fastapi.testclient import TestClient

from src.api import orders as orders_api
from src.main import app
from src.services import order_service, output_builder


def test_status_flow_confirm():
    order_service.clear_all()
    order = _save_canonical_sheet(message_id="m2", rows=[["12/23", "朝", "Menu A", "5", "", ""]])
    confirmed = order_service.confirm_order(order["id"])
    assert confirmed["status"] == "確定"
    output_builder.build_outputs(order["id"])


def test_confirm_endpoint_triggers_outputs(monkeypatch):
    order_service.clear_all()
    order = _save_canonical_sheet(message_id="m3", rows=[["12/23", "朝", "Menu A", "5", "", ""]])
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "apply_ready",
            "apply_gate": {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
        },
    )
    response = TestClient(app).post(f"/orders/{order['id']}/confirm")
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error"] == "legacy_order_workflow_disabled"
    assert detail["replacement"] == "workflow-v2"
