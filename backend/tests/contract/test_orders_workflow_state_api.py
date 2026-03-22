import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


client = TestClient(app)


def _seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    return order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-22",
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )


def test_get_workflow_state_endpoint_returns_workflow(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-001")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "apply_ready",
            "headline": "下書きを明細へ反映できます",
            "primary_action": "apply_draft",
            "candidate_resolution": {"requires_user_choice": False},
            "critical_decisions": [],
            "apply_gate": {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
        },
    )

    res = client.get(f"/orders/{order['id']}/workflow-state")

    assert res.status_code == 200
    body = res.json()
    assert body["order_id"] == order["id"]
    assert body["state"] == "apply_ready"
    assert body["apply_gate"]["can_apply"] is True


def test_get_critical_decisions_endpoint_returns_decisions(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-002")

    monkeypatch.setattr(
        orders_api.order_service,
        "list_order_critical_decisions",
        lambda order_id, refresh_workflow=False: [
            {
                "id": "OCD0001",
                "decision_type": "facility",
                "candidate_set_json": {
                    "decision_type": "facility",
                    "candidates": [{"value": "FAC00001", "label": "施設A"}],
                },
                "selected_value": None,
            }
        ],
    )

    res = client.get(f"/orders/{order['id']}/critical-decisions")

    assert res.status_code == 200
    body = res.json()
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["decision_type"] == "facility"


def test_choose_critical_decision_endpoint_returns_result(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-003")

    monkeypatch.setattr(
        orders_api.order_service,
        "choose_critical_decision",
        lambda order_id, decision_type, selected_value, selected_by=None: (
            {
                "decision": {
                    "decision_type": decision_type,
                    "selected_value": selected_value,
                },
                "workflow_state": {
                    "order_id": order_id,
                    "state": "apply_ready",
                },
            },
            None,
        ),
    )

    res = client.post(
        f"/orders/{order['id']}/critical-decisions/facility",
        json={"selected_value": "FAC00001"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["decision"]["decision_type"] == "facility"
    assert body["decision"]["selected_value"] == "FAC00001"
    assert body["workflow_state"]["state"] == "apply_ready"


def test_choose_critical_decision_endpoint_validates_and_maps_errors(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-004")

    missing_value = client.post(f"/orders/{order['id']}/critical-decisions/facility", json={})
    assert missing_value.status_code == 400
    assert missing_value.json()["detail"] == "selected_value missing"

    monkeypatch.setattr(
        orders_api.order_service,
        "choose_critical_decision",
        lambda *_args, **_kwargs: (None, "decision_not_found"),
    )
    not_found = client.post(
        f"/orders/{order['id']}/critical-decisions/facility",
        json={"selected_value": "FAC00001"},
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(
        orders_api.order_service,
        "choose_critical_decision",
        lambda *_args, **_kwargs: (None, "week_invalid"),
    )
    bad_request = client.post(
        f"/orders/{order['id']}/critical-decisions/week",
        json={"selected_value": "bad"},
    )
    assert bad_request.status_code == 400

    monkeypatch.setattr(
        orders_api.order_service,
        "choose_critical_decision",
        lambda *_args, **_kwargs: (None, "unexpected_failure"),
    )
    failed = client.post(
        f"/orders/{order['id']}/critical-decisions/template",
        json={"selected_value": "layout-a"},
    )
    assert failed.status_code == 500


def test_confirm_endpoint_blocks_on_workflow_apply_gate(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-005")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "identity_choice_required",
            "headline": "重要候補の選択が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["facility_choice_required"],
                "warnings": [],
            },
        },
    )

    res = client.post(f"/orders/{order['id']}/confirm")

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "facility_choice_required"
    assert "facility_choice_required" in detail["blockers"]
    assert detail["workflow_state"]["state"] == "identity_choice_required"


def test_apply_endpoint_blocks_on_workflow_apply_gate(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "layout_choice_required",
            "headline": "重要候補の選択が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["template_choice_required"],
                "warnings": [],
            },
        },
    )

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "template_choice_required"
    assert "template_choice_required" in detail["blockers"]
    assert detail["workflow_state"]["state"] == "layout_choice_required"


def test_apply_endpoint_blocks_on_column_mapping_choice_required(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006b")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "layout_choice_required",
            "headline": "OCR候補の選択が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["column_mapping_choice_required"],
                "warnings": [],
            },
        },
    )

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "column_mapping_choice_required"
    assert "column_mapping_choice_required" in detail["blockers"]


def test_apply_endpoint_blocks_on_quantity_choice_required(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006c")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "layout_choice_required",
            "headline": "重要候補の選択が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["quantity_choice_required"],
                "warnings": [],
            },
        },
    )

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "quantity_choice_required"
    assert "quantity_choice_required" in detail["blockers"]


def test_apply_endpoint_blocks_on_quantity_choice_required(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006c")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "layout_choice_required",
            "headline": "OCR候補の選択が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["quantity_choice_required"],
                "warnings": [],
            },
        },
    )

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "quantity_choice_required"
    assert "quantity_choice_required" in detail["blockers"]


def test_apply_endpoint_ignores_draft_rows_empty_when_request_rows_exist(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-007")
    apply_called: dict[str, bool] = {"value": False}

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "draft_ready",
            "headline": "下書きを確認してください",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["draft_rows_empty"],
                "warnings": [],
            },
        },
    )

    def _fake_apply_ocr_table(*_args, **_kwargs):
        apply_called["value"] = True
        return {"id": order["id"]}, None

    monkeypatch.setattr(orders_api.order_service, "apply_ocr_table", _fake_apply_ocr_table)

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 200
    assert apply_called["value"] is True
