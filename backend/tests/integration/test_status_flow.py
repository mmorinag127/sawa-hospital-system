import pytest
from modernization_input_support import registration_environment, authored_ingest_master, register_ingest  # noqa: F401

pytestmark = pytest.mark.usefixtures("authored_ingest_master")

from test_line_corrections import _save_canonical_sheet

from fastapi.testclient import TestClient

from src.api import orders as orders_api
from src.main import app
from tests.contract.auth_support import hospital_operator  # noqa: F401
from src.services import order_service, output_builder, order_workflow_v2_service


def test_status_flow_confirm():
    order_service.clear_all()
    order = _save_canonical_sheet(message_id="m2", rows=[["12/23", "朝", "Menu A", "5", "", ""]])
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    _, error = order_workflow_v2_service.run_bagging(order["id"], expected_revision=current["revision"])
    assert error is None, error
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    _, error = order_workflow_v2_service.confirm_bagging(order["id"], expected_revision=current["revision"])
    assert error is None, error
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    _, error = order_workflow_v2_service.final_confirm(order["id"], expected_revision=current["revision"])
    assert error is None, error
    confirmed = order_service.get_order_by_id(order["id"])
    assert confirmed["status"] == "確定"
    output_builder.build_outputs(order["id"])


def test_confirm_endpoint_triggers_outputs(monkeypatch, hospital_operator):
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
    response = TestClient(app, headers=hospital_operator.headers).post(f"/orders/{order['id']}/confirm")
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error"] == "legacy_order_workflow_disabled"
    assert detail["replacement"] == "workflow-v2"
