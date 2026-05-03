from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.models.order import OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_workflow_v2_service


Base.metadata.create_all(bind=engine)


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


def _create_order_with_evidence() -> tuple[str, str, str]:
    order_id = _id("ORDv2")
    evidence_id_1 = _id("OEV")
    evidence_id_2 = _id("OEV")
    with session_scope() as session:
        session.add(
            Order(
                id=order_id,
                facility_code="FAC_TEST",
                week_code="2026-04-26",
                status="要確認",
                document_uri=f"file:///{order_id}.pdf",
                message_id=f"msg-{order_id}",
                received_at=datetime.utcnow(),
            )
        )
        for evidence_id in [evidence_id_1, evidence_id_2]:
            session.add(
                OrderOcrEvidenceRun(
                    id=evidence_id,
                    order_id=order_id,
                    schema_version="workflow-v2-test",
                    producer_version="test",
                    source="test",
                    status="ready",
                    payload_json={"pipeline_version": "test-pipeline"},
                    artifact_manifest_json={"overlay": f"{evidence_id}.pdf"},
                    artifact_digest=f"digest-{evidence_id}",
                    capabilities_json={},
                    degraded_reasons_json=[],
                    created_at=datetime.utcnow(),
                )
            )
    return order_id, evidence_id_1, evidence_id_2


def _install_fake_materialization(monkeypatch) -> None:
    def fake_candidate(_order_id, *, draft_record, facility_id, existing_week_code, received_at):
        _ = facility_id, existing_week_code, received_at
        sheet = draft_record.get("draft_sheet_json") if isinstance(draft_record, dict) else {}
        if isinstance(sheet, dict) and sheet.get("test_materialization_error"):
            return {"error": "test_materialization_error", "lines": [], "line_count": 0}
        lines = []
        rows = sheet.get("rows") if isinstance(sheet, dict) else []
        for row_idx, row in enumerate(rows if isinstance(rows, list) else []):
            if isinstance(row, dict):
                menu_name = str(row.get("menu_name") or row.get("menu") or "").strip()
                for field in ["regular", "soft", "qty.regular"]:
                    value = row.get(field)
                    if value in {"", None}:
                        continue
                    lines.append(
                        {
                            "date": "2026-04-26",
                            "daypart": "lunch",
                            "menu_name": menu_name,
                            "diet_type": field,
                            "area_id": "main",
                            "quantity_original": float(value),
                            "quantity_corrected": None,
                            "source_row_index": row_idx,
                        }
                    )
        return {"error": None, "lines": lines, "line_count": len(lines), "derived_week_code": "2026-04@2026-04-26~2026-04-30"}

    def fake_materialize(session, order, candidate):
        for idx, line in enumerate(candidate.get("lines") or []):
            session.add(
                OrderLine(
                    id=_id("OL"),
                    order_id=order.id,
                    line_id=f"line-{idx + 1}",
                    date=datetime.fromisoformat(str(line["date"])).date(),
                    daypart=line.get("daypart"),
                    menu_name=line.get("menu_name"),
                    diet_type=line.get("diet_type"),
                    area_id=line.get("area_id"),
                    quantity_original=line.get("quantity_original"),
                    quantity_corrected=line.get("quantity_corrected"),
                )
            )
        order.lines_updated_at = datetime.utcnow()

    fake_order_service = SimpleNamespace(
        _build_materialization_candidate_from_draft_record=fake_candidate,
        _materialize_confirmed_lines_from_candidate=fake_materialize,
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)


def test_context_confirm_requires_explicit_facility_week_and_template() -> None:
    order_id, _, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "context_confirmed"
    assert workflow["facility_id"] == "FAC00001"
    assert workflow["week_start"] == "2026-04-26"
    assert workflow["week_end"] == "2026-04-30"
    assert workflow["template_id"] == "template-fac00001"
    assert workflow["selected_ocr_result_id"] is None
    assert workflow["saved_sheet_id"] is None
    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order.facility_code == "FAC00001"
        assert order.week_code == "2026-04@2026-04-26~2026-04-30"


def test_context_confirm_normalizes_legacy_non_dict_workflow_meta() -> None:
    order_id, _, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                state="uploaded",
                headline="legacy",
                primary_action="confirm_context",
                secondary_actions_json="confirm_context",
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "context_confirmed"
    assert workflow["template_id"] == "template-fac00001"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert isinstance(row.secondary_actions_json, dict)
        assert row.secondary_actions_json["workflow_v2"]["facility_id"] == "FAC00001"


def test_mark_ocr_run_queued_requires_context_and_clears_downstream() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()

    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job")
    assert queued is None
    assert error == "context_not_confirmed"

    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]
    with session_scope() as session:
        session.add(
            OrderCurrentState(
                order_id=order_id,
                draft_id=saved_sheet_id,
                evidence_run_id=evidence_id_1,
                snapshot_version="v1",
                state_json={"source": "test-current-state"},
                updated_at=datetime.utcnow(),
            )
        )

    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job")

    assert error is None
    assert queued["state"] == "ocr_running"
    assert queued["selected_ocr_result_id"] is None
    assert queued["saved_sheet_id"] is None
    with session_scope() as session:
        assert session.get(OrderSheetDraft, saved_sheet_id) is None
        current_state = session.get(OrderCurrentState, order_id)
        assert current_state is not None
        assert current_state.draft_id is None
        assert current_state.evidence_run_id is None


def test_mark_ocr_run_completed_preserves_context_and_does_not_select_result() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job")

    completed, error = order_workflow_v2_service.mark_ocr_run_completed(
        order_id,
        job_id="OCR-job",
        evidence_run_id=evidence_id_1,
    )

    assert error is None
    assert completed["state"] == "ocr_completed"
    assert completed["facility_id"] == "FAC00001"
    assert completed["week_start"] == "2026-04-26"
    assert completed["week_end"] == "2026-04-30"
    assert completed["template_id"] == "template-fac00001"
    assert completed["selected_ocr_result_id"] is None
    assert completed["saved_sheet_id"] is None
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.evidence_run_id is None
        assert row.draft_id is None
        assert row.secondary_actions_json["workflow_v2"]["latest_ocr_result_id"] == evidence_id_1


def test_sheet_source_uses_only_selected_ocr_payload(monkeypatch) -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_2)
    with session_scope() as session:
        evidence_1 = session.get(OrderOcrEvidenceRun, evidence_id_1)
        evidence_2 = session.get(OrderOcrEvidenceRun, evidence_id_2)
        evidence_1.payload_json = {"selected_marker": "wrong"}
        evidence_2.payload_json = {"selected_marker": "expected"}

    captured: dict[str, object] = {}

    def fake_assignment(**kwargs):
        captured["payload"] = kwargs.get("payload")
        return {"metrics": {}, "blockers": [], "warnings": [], "sheet_output": {"cells": {}}}

    def fake_base_sheet(_order_id):
        return {
            "fields": ["date", "menu_name", "qty.regular"],
            "header": ["日付", "メニュー", "常食"],
            "rows": [["04/26", "大豆のトマト煮", ""]],
            "row_ids": ["row-1"],
        }, None

    def fake_apply(*, base_sheet, assignment):
        _ = assignment
        projected = dict(base_sheet)
        projected["rows"] = [["04/26", "大豆のトマト煮", "70"]]
        projected["blockers"] = []
        projected["warnings"] = []
        return projected

    fake_order_service = SimpleNamespace(
        _build_hakodate_evidence_assignment_from_payload=fake_assignment,
        _build_hakodate_weekly_menu_base_sheet=fake_base_sheet,
        _apply_hakodate_sheet_output_to_sheet_payload=fake_apply,
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)

    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    assert error is None
    assert captured["payload"] == {"selected_marker": "expected"}
    assert source["selected_ocr_result_id"] == evidence_id_2
    assert source["sheet"]["rows"][0][2] == "70"


def test_selecting_ocr_result_clears_downstream_sheet() -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    assert error is None
    assert selected["selected_ocr_result_id"] == evidence_id_1

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
    )
    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"
    first_saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    switched, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_2)

    assert error is None
    assert switched["selected_ocr_result_id"] == evidence_id_2
    assert switched["saved_sheet_id"] is None
    with session_scope() as session:
        assert session.get(OrderSheetDraft, first_saved_sheet_id) is None


def test_deleting_selected_ocr_result_deletes_derived_sheet_and_returns_to_step1_context() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    workflow, error = order_workflow_v2_service.delete_ocr_result(order_id, evidence_id_1)

    assert error is None
    assert workflow["selected_ocr_result_id"] is None
    assert workflow["saved_sheet_id"] is None
    assert workflow["state"] == "context_confirmed"
    with session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, evidence_id_1) is None
        assert session.get(OrderSheetDraft, saved_sheet_id) is None


def test_changing_selected_ocr_deletes_confirmed_snapshot_too(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]},
        edited_by="test",
    )
    order_workflow_v2_service.run_bagging(order_id)
    order_workflow_v2_service.confirm_bagging(order_id)
    order_workflow_v2_service.prepare_output_review(order_id)
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester")
    assert error is None
    snapshot_id = confirmed["confirmed_snapshot_id"]

    switched, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_2)

    assert error is None
    assert switched["selected_ocr_result_id"] == evidence_id_2
    assert switched["saved_sheet_id"] is None
    assert switched["confirmed_snapshot_id"] is None
    with session_scope() as session:
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is None


def test_sheet_cannot_be_saved_without_selected_ocr() -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": []},
        edited_by="test",
    )

    assert saved is None
    assert error == "selected_ocr_required"


def test_workflow_v2_does_not_use_order_lines_as_sheet_source() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)

    sheet, error = order_workflow_v2_service.get_saved_sheet(order_id)

    assert sheet is None
    assert error == "saved_sheet_missing"
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        assert workflow.evidence_run_id == evidence_id_1


def test_inspection_is_read_only_projection_of_current_lineage() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    inspection, error = order_workflow_v2_service.get_inspection(order_id)

    assert error is None
    assert inspection["source"] == "workflow_v2_inspection"
    assert inspection["workflow"]["selected_ocr_result_id"] == evidence_id_1
    assert inspection["artifact_lineage"]["selected_ocr_result_id"] == evidence_id_1
    assert inspection["artifact_lineage"]["saved_sheet_id"] == saved_sheet_id
    assert inspection["saved_sheet"]["saved_sheet_id"] == saved_sheet_id
    assert any(item["ocr_result_id"] == evidence_id_1 and item["selected"] for item in inspection["ocr_results"])


def test_bagging_requires_saved_sheet_and_uses_saved_sheet_as_source(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)

    missing, error = order_workflow_v2_service.run_bagging(order_id)
    assert missing is None
    assert error == "saved_sheet_required"

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={
            "rows": [
                {"menu_name": "大豆のトマト煮", "regular": "70", "soft": "5"},
                {"menu_name": "胡瓜のサラダ", "regular": ""},
            ]
        },
        edited_by="test",
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    bagging, error = order_workflow_v2_service.run_bagging(order_id)

    assert error is None
    assert bagging["workflow"]["state"] == "bagging_ready"
    assert bagging["bagging_result"]["source_saved_sheet_id"] == saved_sheet_id
    assert bagging["bagging_result"]["summary"]["total_quantity"] == 75.0
    assert bagging["bagging_result"]["summary"]["quantity_line_count"] == 2
    assert bagging["bagging_result"]["summary"]["bag_row_count"] >= 1
    assert bagging["bagging_result"]["bag_rows"]


def test_bagging_blocks_when_saved_sheet_cannot_materialize(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"test_materialization_error": True, "rows": [{"menu_name": "A", "regular": "1"}]},
        edited_by="test",
    )
    assert error is None
    assert saved is not None

    bagging, error = order_workflow_v2_service.run_bagging(order_id)

    assert bagging is None
    assert error == "test_materialization_error"


def test_step5_confirm_requires_output_review_and_writes_confirmed_snapshot(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]},
        edited_by="test",
    )
    blocked, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester")
    assert blocked is None
    assert error == "output_review_required"

    bagging, error = order_workflow_v2_service.run_bagging(order_id)
    assert error is None
    bagging_result_id = bagging["bagging_result"]["bagging_result_id"]
    confirmed_bagging, error = order_workflow_v2_service.confirm_bagging(order_id)
    assert error is None
    assert confirmed_bagging["workflow"]["state"] == "output_review"
    assert confirmed_bagging["output_bundle"]["source_bagging_result_id"] == bagging_result_id

    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester")

    assert error is None
    assert confirmed["workflow"]["state"] == "confirmed"
    assert confirmed["confirmed_snapshot_id"]
    with session_scope() as session:
        snapshot = session.get(OrderConfirmedSnapshot, confirmed["confirmed_snapshot_id"])
        assert snapshot is not None
        assert snapshot.snapshot_json["source"] == "workflow_v2"
        assert snapshot.snapshot_json["bagging_result"]["bagging_result_id"] == bagging_result_id
        assert session.query(OrderLine).filter(OrderLine.order_id == order_id).count() == 1
        assert session.get(Order, order_id).status == "確定"
