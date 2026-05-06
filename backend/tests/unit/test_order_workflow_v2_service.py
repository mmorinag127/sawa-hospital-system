from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.models.order import OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.facility import Facility
from src.models.ocr_job import OcrJob
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


def _stamp_evidence_with_workflow_template(order_id: str, *evidence_ids: str) -> str | None:
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        template_version_id = str(getattr(workflow, "template_version_id", "") or "").strip() if workflow else ""
        for evidence_id in evidence_ids:
            evidence = session.get(OrderOcrEvidenceRun, evidence_id)
            if evidence is not None:
                evidence.template_version_id = template_version_id or None
        return template_version_id or None


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


def _install_fake_ocr_prerequisite(monkeypatch, error: str | None = None) -> None:
    fake_order_service = SimpleNamespace(
        _build_hakodate_weekly_menu_base_sheet=lambda _order_id: (
            None if error else {"fields": ["date", "daypart", "menu", "qty.regular"], "rows": [["04/26", "朝", "A", ""]]},
            error,
        ),
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)


def _registered_template_config(facility_id: str) -> dict:
    return {
        "facility_id": facility_id,
        "fax_template_id": "fax_layout_regular_forbidden_v1",
        "fax_template": {
            "template_id": "fax_layout_regular_forbidden_v1",
            "columns": [
                {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
                {"index": 2, "role": "menu_name", "header": "献立", "source_index": 2},
                {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 3},
                {"index": 4, "role": "quantity", "header": "-", "diet_type": "placeholder", "area_id": "X", "source_index": 4},
                {"index": 5, "role": "note", "header": "備考欄", "source_index": 5},
            ],
        },
    }


def _template_columns() -> list[dict]:
    return [
        {"index": 0, "role": "date", "header": "日付", "source_index": 0},
        {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
        {"index": 2, "role": "menu_name", "header": "献立", "source_index": 2},
        {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 3},
        {"index": 4, "role": "note", "header": "備考欄", "source_index": 4},
    ]


def _install_active_template_version(
    facility_id: str = "FAC00001",
    template_id: str = "template-fac00001",
    *,
    columns: list[dict] | None = None,
) -> str:
    normalized_columns = order_workflow_v2_service.facility_template_version_service.normalize_template_columns(
        columns if columns is not None else _template_columns()
    )
    digest = order_workflow_v2_service.facility_template_version_service.template_digest(
        template_id=template_id,
        columns=normalized_columns,
    )
    version_id = _id("FTV")
    with session_scope() as session:
        if session.get(Facility, facility_id) is None:
            session.add(Facility(id=facility_id, name=f"Template Facility {facility_id}"))
        for active in (
            session.query(FacilityTemplateVersion)
            .filter(
                FacilityTemplateVersion.facility_id == facility_id,
                FacilityTemplateVersion.status == "active",
            )
            .all()
        ):
            active.status = "archived"
            active.archived_at = datetime.utcnow()
        session.add(
            FacilityTemplateVersion(
                id=version_id,
                facility_id=facility_id,
                version="test",
                status="active",
                template_id=template_id,
                source="test-active-template",
                columns_json=normalized_columns,
                cells_json=[],
                template_digest=digest,
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
                activated_at=datetime.utcnow(),
            )
        )
    return version_id


def _add_ocr_job(order_id: str, job_id: str = "OCR-job", *, job_order_id: str | None = None) -> str:
    with session_scope() as session:
        row = session.get(OcrJob, job_id)
        if row is None:
            row = OcrJob(
                id=job_id,
                created_at=datetime.utcnow(),
            )
            session.add(row)
        row.order_id = job_order_id if job_order_id is not None else order_id
        row.template_version_id = None
        row.status = "running"
        row.input_reference = "file:///input.pdf"
        row.updated_at = datetime.utcnow()
    return job_id


@pytest.fixture(autouse=True)
def _standard_active_template_for_workflow_tests() -> None:
    _install_active_template_version("FAC00001", "template-fac00001")


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


def test_context_suggestion_is_recorded_without_confirming_step1() -> None:
    order_id, _, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.record_context_suggestion(
        order_id=order_id,
        suggestion={
            "source": "ingest_first_pass_ocr",
            "facility_id": "FAC00002",
            "facility_name": "シルバーホームなごみ",
            "week_code": "2026-04@2026-04-26~2026-04-30",
            "date_hints": ["4/26", "4/30"],
            "confidence": "high",
        },
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "uploaded"
    assert workflow["facility_id"] is None
    assert workflow["week_start"] is None
    assert workflow["context_suggestion"]["facility_id"] == "FAC00002"
    assert workflow["context_suggestion"]["week_start"] == "2026-04-26"
    assert workflow["context_suggestion"]["week_end"] == "2026-04-30"
    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order.facility_code == "FAC_TEST"
        assert order.week_code == "2026-04-26"


def test_workflow_v2_get_endpoints_do_not_create_workflow_rows() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert workflow["state"] == "not_initialized"
    assert workflow["blockers"] == ["workflow_not_initialized"]

    results, error = order_workflow_v2_service.list_ocr_results(order_id)
    assert error is None
    assert results["workflow_state"] == "not_initialized"
    assert results["selected_ocr_result_id"] is None
    assert any(item["ocr_result_id"] == evidence_id_1 and not item["selected"] for item in results["results"])

    saved_sheet, error = order_workflow_v2_service.get_saved_sheet(order_id)
    assert saved_sheet is None
    assert error == "workflow_not_initialized"

    sheet_source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)
    assert sheet_source is None
    assert error == "workflow_not_initialized"

    inspection, error = order_workflow_v2_service.get_inspection(order_id)
    assert error is None
    assert inspection["workflow"]["state"] == "not_initialized"
    assert inspection["artifact_lineage"]["selected_ocr_result_id"] is None

    with session_scope() as session:
        assert session.get(OrderWorkflowState, order_id) is None


def test_list_ocr_results_only_returns_current_template_candidates() -> None:
    order_id, current_evidence_id, mismatched_evidence_id = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    assert error is None
    current_template_version_id = workflow["template_version_id"]
    archived_template_version_id = _id("FTVarchived")
    null_template_evidence_id = _id("OEV")
    with session_scope() as session:
        session.add(
            FacilityTemplateVersion(
                id=archived_template_version_id,
                facility_id="FAC00001",
                version="archived",
                status="archived",
                template_id="template-fac00001",
                source="test-archived-template",
                columns_json=_template_columns(),
                cells_json=[],
                template_digest="archived-digest",
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
            )
        )
        session.get(OrderOcrEvidenceRun, current_evidence_id).template_version_id = current_template_version_id
        session.get(OrderOcrEvidenceRun, mismatched_evidence_id).template_version_id = archived_template_version_id
        session.add(
            OrderOcrEvidenceRun(
                id=null_template_evidence_id,
                order_id=order_id,
                schema_version="workflow-v2-test",
                producer_version="test",
                source="test",
                status="ready",
                payload_json={"pipeline_version": "test-pipeline"},
                artifact_manifest_json={"overlay": f"{null_template_evidence_id}.pdf"},
                artifact_digest=f"digest-{null_template_evidence_id}",
                capabilities_json={},
                degraded_reasons_json=[],
                created_at=datetime.utcnow(),
            )
        )

    results, error = order_workflow_v2_service.list_ocr_results(order_id)

    assert error is None
    assert results["candidate_template_version_id"] == current_template_version_id
    assert results["hidden_template_mismatch_result_count"] == 2
    assert [item["ocr_result_id"] for item in results["results"]] == [current_evidence_id]
    assert results["results"][0]["template_version_id"] == current_template_version_id


def test_get_workflow_does_not_refresh_prerequisite_state(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    monkeypatch.setattr(
        order_workflow_v2_service,
        "_hakodate_weekly_menu_base_sheet_error",
        lambda _order_id: "menu_entries_missing",
    )

    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow["state"] == "context_confirmed"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.state == "context_confirmed"
        assert row.blockers_json == []


def test_select_ocr_result_requires_confirmed_context() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)

    assert workflow is None
    assert error == "context_not_confirmed"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is None or row.evidence_run_id is None


def test_context_confirm_blocks_when_facility_template_unresolved(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {"facility_id": facility_id, "fax_template": {"columns": []}},
    )

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC_TEMPLATELESS",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )

    assert workflow is None
    assert error == "facility_template_unresolved"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        order = session.get(Order, order_id)
        assert row is not None
        assert row.state == "facility_template_unresolved"
        assert row.primary_action == "register_facility_template"
        assert row.blockers_json == ["facility_template_unresolved"]
        assert row.secondary_actions_json["workflow_v2"]["facility_id"] == "FAC_TEMPLATELESS"
        assert row.secondary_actions_json["workflow_v2"]["template_id"] is None
        assert order.facility_code == "FAC_TEMPLATELESS"


def test_ocr_run_blocks_legacy_context_without_active_template_version(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    facility_id = _id("FAC_NO_ACTIVE")
    job_id = _id("OCRnoactive")
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order is not None
        order.facility_code = facility_id
        order.week_code = "2026-04@2026-04-26~2026-04-30"
        session.add(OcrJob(id=job_id, status="queued", input_reference="file:///input.pdf"))
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                state="context_confirmed",
                headline="施設・週次・テンプレートが確定しました",
                primary_action="run_ocr",
                secondary_actions_json={
                    order_workflow_v2_service.WORKFLOW_V2_META_KEY: {
                        "facility_id": facility_id,
                        "week_start": "2026-04-26",
                        "week_end": "2026-04-30",
                        "week_code": "2026-04@2026-04-26~2026-04-30",
                        "template_id": "fax_layout_regular_forbidden_v1",
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    workflow, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, job_id)

    assert workflow is not None
    assert error == "template_version_required"
    assert workflow["state"] == "template_version_required"
    assert workflow["blockers"] == ["template_version_required"]
    with session_scope() as session:
        versions = session.query(FacilityTemplateVersion).filter(FacilityTemplateVersion.facility_id == facility_id).all()
        assert versions == []
        assert session.get(OcrJob, job_id).template_version_id is None


def test_context_confirm_uses_registered_facility_template(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    facility_id = _id("FACREG")
    _install_active_template_version(facility_id, "fax_layout_regular_forbidden_v1")
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": {
                "columns": [
                    {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                    {"index": 1, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 3},
                ],
            },
        },
    )

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id=facility_id,
        week_start="2026-04-26",
        week_end="2026-04-30",
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "context_confirmed"
    assert workflow["template_id"] == "fax_layout_regular_forbidden_v1"
    assert workflow["template_version_id"]


def test_context_confirm_blocks_template_id_only_without_source_indexes(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    facility_id = _id("FACBAD")
    _install_active_template_version(
        facility_id,
        "fax_layout_regular_forbidden_v1",
        columns=[
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
        ],
    )
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": {
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
                ],
            },
        },
    )

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id=facility_id,
        week_start="2026-04-26",
        week_end="2026-04-30",
    )

    assert workflow is None
    assert error == "facility_template_unresolved"


def test_template_version_lineage_flows_to_job_evidence_draft_and_snapshot(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    job_id = _id("OCRlineage")
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    with session_scope() as session:
        session.add(OcrJob(id=job_id, order_id=order_id, status="running", input_reference="file:///input.pdf"))

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )
    assert error is None
    template_version_id = workflow["template_version_id"]

    _install_fake_ocr_prerequisite(monkeypatch)
    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, job_id)
    assert error is None
    assert queued["template_version_id"] == template_version_id

    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    completed, error = order_workflow_v2_service.mark_ocr_run_completed(
        order_id,
        job_id=job_id,
        evidence_run_id=evidence_id_1,
    )
    assert error is None
    assert completed["state"] == "ocr_completed"

    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    assert error is None
    assert selected["template_version_id"] == template_version_id

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]},
        edited_by="test",
    )
    assert error is None
    _install_fake_materialization(monkeypatch)
    bagging, error = order_workflow_v2_service.run_bagging(order_id)
    assert error is None
    assert bagging["workflow"]["template_version_id"] == template_version_id
    order_workflow_v2_service.confirm_bagging(order_id)
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester")
    assert error is None

    with session_scope() as session:
        order = session.get(Order, order_id)
        job = session.get(OcrJob, job_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        snapshot = session.get(OrderConfirmedSnapshot, confirmed["confirmed_snapshot_id"])
        assert order.template_version_id == template_version_id
        assert job.template_version_id == template_version_id
        assert evidence.template_version_id == template_version_id
        assert draft.template_version_id == template_version_id
        assert snapshot.template_version_id == template_version_id


def test_save_sheet_blocks_legacy_selected_evidence_without_template_version(monkeypatch) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )
    assert error is None
    assert workflow["template_version_id"]
    with session_scope() as session:
        order = session.get(Order, order_id)
        row = session.get(OrderWorkflowState, order_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        assert order is not None
        assert row is not None
        assert evidence is not None
        meta = order_workflow_v2_service._workflow_meta(row)
        meta["template_version_id"] = None
        row.secondary_actions_json = {"workflow_v2": meta}
        row.template_version_id = None
        row.evidence_run_id = evidence_id_1
        row.state = "ocr_selected"
        row.primary_action = "edit_sheet"
        order.template_version_id = None
        evidence.template_version_id = None

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
    )

    assert saved is None
    assert error == "template_version_required"
    with session_scope() as session:
        order = session.get(Order, order_id)
        row = session.get(OrderWorkflowState, order_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        assert order.template_version_id is None
        assert row.template_version_id is None
        assert row.state == "template_version_required"
        assert row.blockers_json == ["template_version_required"]
        assert evidence.template_version_id is None


def test_select_ocr_result_blocks_template_version_mismatch(monkeypatch) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )
    assert error is None
    with session_scope() as session:
        session.add(
            FacilityTemplateVersion(
                id=_id("FTVbad"),
                facility_id="FAC00001",
                version="mismatch",
                status="archived",
                template_id="fax_layout_regular_forbidden_v1",
                source="test",
                columns_json=[],
                cells_json=[],
                template_digest="different-digest",
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
            )
        )
        session.get(OrderOcrEvidenceRun, evidence_id_1).template_version_id = "FTVbad"

    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)

    assert selected is not None
    assert error == "template_version_mismatch"
    assert selected["state"] == "template_version_mismatch"
    assert selected["blockers"] == ["template_version_mismatch"]
    assert selected["template_version_id"] == workflow["template_version_id"]


def test_select_ocr_result_blocks_legacy_cache_backfill(monkeypatch) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )
    assert error is None
    with session_scope() as session:
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        evidence.template_version_id = workflow["template_version_id"]
        evidence.source = "legacy-cache-backfill"
        evidence.status = "repair_blocked"

    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)

    assert selected is not None
    assert error == "legacy_ocr_evidence_not_selectable"
    assert selected["state"] == "legacy_ocr_evidence_not_selectable"
    assert selected["selected_ocr_result_id"] is None


def test_ocr_job_order_mismatch_blocks_queue(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    other_order_id = _id("ORDother")
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    _install_fake_ocr_prerequisite(monkeypatch)
    _add_ocr_job(order_id, "OCR-job-mismatch", job_order_id=other_order_id)

    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job-mismatch")

    assert queued is not None
    assert error == "ocr_job_order_mismatch"
    assert queued["state"] == "ocr_job_order_mismatch"


def test_workflow_serializes_effective_template_for_legacy_meta(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "fax_template_id": "fax_layout_floor_2f3f_v1",
        },
    )
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                state="context_confirmed",
                headline="legacy context",
                primary_action="run_ocr",
                secondary_actions_json={
                    "workflow_v2": {
                        "facility_id": "FAC_LEGACY",
                        "week_start": "2026-04-26",
                        "week_end": "2026-04-30",
                        "template_id": None,
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow is not None
    assert workflow["template_id"] == "fax_layout_floor_2f3f_v1"
    assert workflow["template_source"] == "facility_resolved_template"


def test_context_confirm_blocks_facility_columns_without_template_id(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "fax_template": {
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "menu_name", "header": "メニュー"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "header": "常食2F",
                    },
                    {
                        "index": 4,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "3F",
                        "header": "常食3F",
                    },
                    {"index": 5, "role": "note", "header": "備考"},
                ],
            },
        },
    )

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC_FLOOR",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )

    assert workflow is None
    assert error == "facility_template_unresolved"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        assert row.state == "facility_template_unresolved"
        assert row.secondary_actions_json["workflow_v2"]["template_id"] is None


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


def test_mark_ocr_run_queued_requires_context_and_clears_downstream(monkeypatch) -> None:
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
    _install_fake_ocr_prerequisite(monkeypatch)
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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

    _add_ocr_job(order_id)
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


def test_mark_ocr_run_queued_blocks_when_weekly_menu_is_missing(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    _install_fake_ocr_prerequisite(monkeypatch, "menu_entries_missing")

    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job")

    assert queued is not None
    assert error == "menu_entries_missing"
    assert queued["state"] == "ocr_blocked"
    assert queued["blockers"] == ["menu_entries_missing"]
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.state == "ocr_blocked"
        assert row.blockers_json == ["menu_entries_missing"]


def test_workflow_ocr_job_serializes_progress_from_processing_stage(monkeypatch) -> None:
    order_id, _, _ = _create_order_with_evidence()
    job_id = _id("OCRprogress")
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    with session_scope() as session:
        session.add(
            OcrJob(
                id=job_id,
                order_id=order_id,
                status="running",
                input_reference="gs://bucket/input.pdf",
                metrics={
                    "processing_stage": "hakodate_live_pipeline",
                    "result_state": "processing",
                },
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
    _install_fake_ocr_prerequisite(monkeypatch)
    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, job_id)

    assert error is None
    assert queued["ocr_job"]["progress_step"] == 3
    assert queued["ocr_job"]["progress_total"] == 6
    assert queued["ocr_job"]["progress_label"] == "位置合わせ/OCR"
    assert queued["ocr_job"]["error_message"] is None
    assert queued["ocr_job"]["error"] is None


def test_mark_ocr_run_completed_preserves_context_and_does_not_select_result(monkeypatch) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    _install_fake_ocr_prerequisite(monkeypatch)
    _add_ocr_job(order_id)
    order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job")
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)

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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
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

    def fake_apply(*, base_sheet, assignment, **_kwargs):
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


def test_sheet_source_blocks_when_selected_ocr_has_no_confirmed_context() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    with session_scope() as session:
        order = session.get(Order, order_id)
        order.facility_code = "FAC00001"
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id_1,
                state="review_required",
                headline="legacy selected OCR without workflow-v2 context",
                primary_action="review",
                secondary_actions_json=None,
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    assert source is None
    assert error == "context_not_confirmed"


def test_sheet_source_requires_fixed_workflow_template_version(monkeypatch) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: _registered_template_config(facility_id),
    )
    with session_scope() as session:
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        evidence.template_version_id = None
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id_1,
                state="ocr_selected",
                headline="selected OCR without fixed template version",
                primary_action="edit_sheet",
                secondary_actions_json={
                    order_workflow_v2_service.WORKFLOW_V2_META_KEY: {
                        "facility_id": "FAC00001",
                        "week_start": "2026-04-26",
                        "week_end": "2026-04-30",
                        "week_code": "2026-04@2026-04-26~2026-04-30",
                        "template_id": "template-fac00001",
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    assert source is None
    assert error == "template_version_required"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.state == "ocr_selected"
        assert row.template_version_id is None
        assert row.blockers_json == []


def test_save_sheet_blocks_when_selected_ocr_has_no_confirmed_context() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id_1,
                state="review_required",
                headline="legacy selected OCR without workflow-v2 context",
                primary_action="review",
                secondary_actions_json=None,
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [["04/26", "朝", "大豆のトマト煮", "1"]]},
        edited_by="test",
    )

    assert saved is None
    assert error == "context_not_confirmed"


def test_expanded_cell_copy_mode_override_is_passed_to_sheet_projection(monkeypatch) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    changed, error = order_workflow_v2_service.set_expanded_cell_copy_mode(order_id, "enabled")
    assert error is None
    assert changed["expanded_cell_copy_mode"] == "enabled"

    captured: dict[str, object] = {}

    def fake_assignment(**_kwargs):
        return {"metrics": {}, "blockers": [], "warnings": [], "sheet_output": {"cells": {}}}

    def fake_base_sheet(_order_id):
        return {
            "fields": ["date", "menu_name", "qty.regular_x"],
            "header": ["日付", "メニュー", "常食"],
            "rows": [["04/26", "大豆のトマト煮", ""]],
            "row_ids": ["row-1"],
        }, None

    def fake_apply(*, base_sheet, assignment, facility_config, **_kwargs):
        _ = assignment
        captured["facility_config"] = facility_config
        projected = dict(base_sheet)
        projected["blockers"] = []
        projected["warnings"] = []
        return projected

    fake_order_service = SimpleNamespace(
        _build_hakodate_evidence_assignment_from_payload=fake_assignment,
        _build_hakodate_weekly_menu_base_sheet=fake_base_sheet,
        _apply_hakodate_sheet_output_to_sheet_payload=fake_apply,
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {**_registered_template_config(facility_id), "expanded_cell_same_daypart_copy_enabled": False},
    )

    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    assert error is None
    assert source["sheet"]["expanded_cell_copy_mode"] == "enabled"
    assert captured["facility_config"]["expanded_cell_same_daypart_copy_enabled"] is True


def test_facility_template_columns_save_clears_stale_ocr_and_downstream(monkeypatch) -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    _install_active_template_version("FAC00016", "template-fac00016")
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00016",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00016",
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    columns = [
        {"index": 0, "role": "date", "header": "日付", "format": "MM/DD", "source_index": 0},
        {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
        {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 3},
        {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 4},
        {"index": 4, "role": "note", "header": "備考欄", "source_index": 5},
    ]

    fake_order_service = SimpleNamespace(
        save_order_facility_template_columns=lambda _order_id, _columns: (
            {
                "resolved_config": {"fax_template": {"columns": columns}},
                "validation": {"errors": [], "warnings": []},
            },
            None,
        ),
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)

    result, error = order_workflow_v2_service.save_facility_template_columns(order_id, columns)

    assert error is None
    assert result["workflow"]["state"] == "context_confirmed"
    assert result["workflow"]["selected_ocr_result_id"] is None
    assert result["workflow"]["saved_sheet_id"] is None
    assert result["workflow"]["template_version_id"]
    assert result["ocr_results_cleared"] == 2
    resolved_columns = ((result["resolved_config"] or {}).get("fax_template") or {}).get("columns") or []
    regular_column = next(item for item in resolved_columns if item.get("header") == "常食")
    assert regular_column["column_id"]
    assert regular_column["source_index"] == 4
    assert regular_column["semantic"]["diet_type"] == "regular"
    assert regular_column["semantic"]["aggregation_role"] == "include"
    with session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, evidence_id_1) is None
        assert session.get(OrderOcrEvidenceRun, evidence_id_2) is None
        assert session.get(OrderSheetDraft, saved_sheet_id) is None
        assert session.get(FacilityTemplateVersion, result["workflow"]["template_version_id"]) is not None


def test_facility_template_columns_save_on_confirmed_order_clears_snapshot_reference(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    _install_active_template_version("FAC00016", "template-fac00016")
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00016",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00016",
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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

    columns = [
        {"index": 0, "role": "date", "header": "日付", "format": "MM/DD", "source_index": 0},
        {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
        {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 3},
        {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 4},
        {"index": 4, "role": "quantity", "header": "不明(-)", "diet_type": "placeholder", "area_id": "X", "source_index": 5},
        {"index": 5, "role": "note", "header": "備考欄", "source_index": 6},
    ]

    result, error = order_workflow_v2_service.save_facility_template_columns(order_id, columns)

    assert error is None
    assert result["workflow"]["state"] == "context_confirmed"
    assert result["workflow"]["confirmed_snapshot_id"] is None
    assert result["workflow"]["selected_ocr_result_id"] is None
    with session_scope() as session:
        assert session.get(OrderWorkflowState, order_id).confirmed_snapshot_id is None
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is None


def test_selecting_ocr_result_blocks_template_version_mismatch() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    assert error is None
    assert workflow["template_version_id"]

    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)

    assert selected is not None
    assert error == "template_version_mismatch"
    assert selected["state"] == "template_version_mismatch"


def test_selecting_ocr_result_clears_downstream_sheet() -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
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
        lines = session.query(OrderLine).filter(OrderLine.order_id == order_id).all()
        assert len(lines) == 1
        assert lines[0].confirmed_snapshot_id == snapshot.id
        assert isinstance(lines[0].line_digest, str) and len(lines[0].line_digest) == 64
        assert snapshot.snapshot_json["order_lines"][0]["line_digest"] == lines[0].line_digest
        assert session.get(Order, order_id).status == "確定"


def test_saving_sheet_after_final_confirm_invalidates_snapshot_and_lines(monkeypatch) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]},
        edited_by="test",
    )
    assert order_workflow_v2_service.run_bagging(order_id)[1] is None
    assert order_workflow_v2_service.confirm_bagging(order_id)[1] is None
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester")
    assert error is None
    snapshot_id = confirmed["confirmed_snapshot_id"]

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "71"}]},
        edited_by="test",
    )

    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"
    assert saved["workflow"]["confirmed_snapshot_id"] is None
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        assert workflow.confirmed_snapshot_id is None
        assert workflow.draft_id == saved["saved_sheet"]["saved_sheet_id"]
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is None
        assert session.query(OrderLine).filter(OrderLine.order_id == order_id).count() == 0
        assert session.get(Order, order_id).status == "要確認"


def test_get_workflow_projects_blocker_for_inconsistent_confirmed_lineage() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    assert error is None
    assert workflow is not None
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        assert row is not None
        assert evidence is not None
        row.state = "confirmed"
        row.headline = "確定済み"
        row.primary_action = None
        row.evidence_run_id = evidence_id_1
        row.draft_id = None
        row.confirmed_snapshot_id = None
        row.blockers_json = []
        meta = order_workflow_v2_service._workflow_meta(row)
        meta.pop("template_version_id", None)
        row.secondary_actions_json = {order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta}
        evidence.template_version_id = None

    projected, error = order_workflow_v2_service.get_workflow(order_id)
    results, result_error = order_workflow_v2_service.list_ocr_results(order_id)

    assert error is None
    assert projected["state"] == "template_version_mismatch"
    assert projected["primary_action"] == "run_ocr"
    assert projected["blockers"] == ["template_version_mismatch"]
    assert result_error is None
    assert results["workflow_state"] == "template_version_mismatch"
    assert results["blockers"] == ["template_version_mismatch"]
    assert results["results"] == []


def test_get_workflow_projects_legacy_state_from_v2_lineage() -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    assert error is None
    assert workflow is not None
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1)
    assert error is None
    assert selected["state"] == "ocr_selected"
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]},
        edited_by="test",
    )
    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"

    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        row.state = "apply_ready"
        row.headline = "下書きを明細へ反映できます"
        row.primary_action = "apply_draft"
        row.blockers_json = ["draft_newer_than_lines"]
        row.warnings_json = ["draft_newer_than_lines"]

    projected, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert projected["state"] == "sheet_saved"
    assert projected["primary_action"] == "run_bagging"
    assert projected["blockers"] == []
    assert projected["warnings"] == []
    assert projected["legacy_state"] == "apply_ready"
