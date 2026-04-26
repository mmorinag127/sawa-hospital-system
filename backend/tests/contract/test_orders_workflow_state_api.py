import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
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
        lambda *_args, **_kwargs: (None, "decision_stale"),
    )
    stale = client.post(
        f"/orders/{order['id']}/critical-decisions/week",
        json={"selected_value": "2026-03"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "decision_stale"

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


def test_switch_draft_sheet_evidence_endpoint_maps_results(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-switch-001")

    monkeypatch.setattr(
        orders_api.order_service,
        "switch_draft_to_latest_evidence",
        lambda *_args, **_kwargs: (
            {
                "id": "ODR001",
                "order_id": order["id"],
                "base_evidence_run_id": "EVD002",
                "draft_sheet_json": {
                    "source": "weekly_menu",
                    "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                    "header": ["日付", "区分", "メニュー", "常食2F"],
                    "rows": [["03/22", "朝", "Menu A", "3"]],
                    "row_ids": ["row-1"],
                },
                "draft_state": "draft_ready",
                "blockers_json": [],
                "warnings_json": [],
                "latest_patch_candidate_id": None,
                "edited_by": "switch-evidence",
                "edited_at": None,
                "created_at": None,
            },
            None,
        ),
    )
    ok = client.post(f"/orders/{order['id']}/draft-sheet/switch-evidence")
    assert ok.status_code == 200
    assert ok.json()["base_evidence_run_id"] == "EVD002"

    monkeypatch.setattr(
        orders_api.order_service,
        "switch_draft_to_latest_evidence",
        lambda *_args, **_kwargs: (None, "already_current"),
    )
    conflict = client.post(f"/orders/{order['id']}/draft-sheet/switch-evidence")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "already_current"

    monkeypatch.setattr(
        orders_api.order_service,
        "switch_draft_to_latest_evidence",
        lambda *_args, **_kwargs: (None, "evidence_not_found"),
    )
    missing = client.post(f"/orders/{order['id']}/draft-sheet/switch-evidence")
    assert missing.status_code == 404


def test_candidate_draft_sheet_preview_endpoint_maps_results(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-candidate-preview-001")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_candidate_draft_preview",
        lambda *_args, **_kwargs: (
            {
                "id": None,
                "order_id": order["id"],
                "base_evidence_run_id": "EVD002",
                "draft_sheet_json": {
                    "source": "weekly_menu",
                    "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                    "header": ["日付", "区分", "メニュー", "常食2F"],
                    "rows": [["03/22", "朝", "Menu A", "8"]],
                    "row_ids": ["row-1"],
                },
                "draft_state": "draft_ready",
                "blockers_json": [],
                "warnings_json": [],
                "latest_patch_candidate_id": None,
                "edited_by": None,
                "edited_at": None,
                "created_at": None,
            },
            None,
        ),
    )
    ok = client.get(f"/orders/{order['id']}/draft-sheet/candidate-preview")
    assert ok.status_code == 200
    assert ok.json()["base_evidence_run_id"] == "EVD002"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_candidate_draft_preview",
        lambda *_args, **_kwargs: (None, "candidate_preview_unavailable"),
    )
    conflict = client.get(f"/orders/{order['id']}/draft-sheet/candidate-preview")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "candidate_preview_unavailable"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_candidate_draft_preview",
        lambda *_args, **_kwargs: (None, "template_unresolved"),
    )
    blocked = client.get(f"/orders/{order['id']}/draft-sheet/candidate-preview")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "template_unresolved"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_candidate_draft_preview",
        lambda *_args, **_kwargs: (None, "candidate_not_found"),
    )
    missing = client.get(f"/orders/{order['id']}/draft-sheet/candidate-preview")
    assert missing.status_code == 404


def test_ocr_rerun_endpoint_enqueues_pipeline_candidate(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-rerun-001")

    called: dict[str, object] = {}

    def _fake_enqueue(order_id, background_tasks, **kwargs):
        called["order_id"] = order_id
        called.update(kwargs)
        return {"accepted": True, "ocr_job_id": f"OCR-{order_id}"}

    monkeypatch.setattr(orders_api, "_enqueue_order_evidence_rerun", _fake_enqueue)

    res = client.post(f"/orders/{order['id']}/ocr-rerun")

    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["mode"] == "pipeline_rerun"
    assert called["order_id"] == order["id"]
    assert called["stale_action"] == "retry"


def test_ocr_rerun_endpoint_heals_active_job_when_workflow_is_terminal(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-rerun-terminal-heal")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")
    update_job(
        job_id,
        status="running",
        error_message=None,
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "processing",
        },
    )

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "rerun_failed_keep_current",
            "ocr_last_reparse_error": "ocr_pipeline_failed:SystemExit(1)",
            "ocr_processing_stage": "ocr_pipeline",
            "ocr_result_state": "hard_failed",
            "ocr_reparse_status": "hard_failed",
            "reparse_state": {
                "status": "hard_failed",
                "processing_stage": "ocr_pipeline",
                "result_state": "hard_failed",
                "error_message": "ocr_pipeline_failed:SystemExit(1)",
            },
        },
    )
    monkeypatch.setattr(orders_api, "_run_ocr_rerun_background", lambda *_args, **_kwargs: None)

    res = client.post(f"/orders/{order['id']}/ocr-rerun")

    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["mode"] == "pipeline_rerun"
    healed_job = get_job(job_id)
    assert healed_job is not None
    assert healed_job["status"] == "running"
    metrics = healed_job.get("metrics") or {}
    assert metrics["request_mode"] == "ocr_rerun"
    assert metrics["processing_stage"] == "queued"
    assert metrics["result_state"] == "processing"


def test_confirm_endpoint_blocks_on_weekly_menu_missing(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-005b")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "draft_blocked",
            "headline": "下書きはありますが、反映前に条件の解消が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["weekly_menu_missing"],
                "warnings": [],
            },
        },
    )

    res = client.post(f"/orders/{order['id']}/confirm")

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "weekly_menu_missing"
    assert "weekly_menu_missing" in detail["blockers"]
    assert detail["workflow_state"]["state"] == "draft_blocked"


def test_apply_endpoint_blocks_on_workflow_apply_gate(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "evidence_run_id": "EVDtest",
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


def test_apply_endpoint_ignores_weekly_menu_missing_when_request_rows_exist(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006c")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "evidence_run_id": "EVDtest",
            "state": "draft_blocked",
            "headline": "下書きはありますが、反映前に条件の解消が必要です",
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["weekly_menu_missing"],
                "warnings": [],
            },
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "apply_submitted_ocr_sheet",
        lambda *_args, **_kwargs: ({"id": order["id"], "status": "要確認"}, None),
    )

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 200


def test_apply_endpoint_blocks_on_column_mapping_choice_required(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-006b")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "draft_id": "ODRtest",
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
            "draft_id": "ODRtest",
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

    def _fake_apply_submitted_ocr_sheet(*_args, **_kwargs):
        apply_called["value"] = True
        return {"id": order["id"]}, None

    monkeypatch.setattr(orders_api.order_service, "apply_submitted_ocr_sheet", _fake_apply_submitted_ocr_sheet)

    res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
        },
    )

    assert res.status_code == 200
    assert apply_called["value"] is True


def test_apply_endpoint_returns_conflict_for_materialization_guard_errors(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-materialization-guard")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "draft_ready",
            "headline": "下書きを確認してください",
            "apply_gate": {
                "can_apply": True,
                "can_confirm": True,
                "blockers": [],
                "warnings": [],
            },
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "apply_submitted_ocr_sheet",
        lambda *_args, **_kwargs: (None, "draft_materialization_mismatch"),
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
    assert detail["error"] == "draft_materialization_mismatch"


def test_apply_endpoint_returns_conflict_for_draft_materialization_mismatch(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-workflow-api-008")

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda order_id, refresh=False: {
            "order_id": order_id,
            "state": "draft_ready",
            "headline": "下書きを確認してください",
            "apply_gate": {
                "can_apply": True,
                "can_confirm": True,
                "blockers": [],
                "warnings": [],
            },
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "apply_submitted_ocr_sheet",
        lambda *_args, **_kwargs: (None, "draft_materialization_mismatch"),
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
    assert detail["error"] == "draft_materialization_mismatch"
