"""Read-work reductions preserve exact JSON and never become write receipts."""
from types import SimpleNamespace

import pytest
from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: F401
from tests.helpers.owned_output import owned_outputs  # noqa: F401

from src.services import snapshot_serialization as snapshots
from src.services import system_process_log_service as logs
from src.services.ingest_delivery_service import _receipt


def test_native_snapshot_avoids_round_trip_without_changing_types(monkeypatch):
    def forbidden(_value):
        raise AssertionError("native JSON was normalized again")
    monkeypatch.setattr(snapshots, "_json_snapshot", forbidden)
    assert snapshots.canonical_snapshot_json({"b": (False, 1, 1.0), "a": None}) == '{"a": null, "b": [false, 1, 1.0]}'
    assert not snapshots.snapshots_equal({"x": True}, {"x": 1})
    assert not snapshots.snapshots_equal({"x": 1.0}, {"x": 1})


def test_ocr_log_summary_never_loads_deferred_metrics():
    class Row(SimpleNamespace):
        @property
        def metrics(self):
            raise AssertionError("summary loaded full metrics")
    row = Row(id="job", status="done", input_reference="source", updated_at=None, created_at=None,
              error_message=None, output_reference=None, template_id="template")
    result = logs._ocr_job_row(row, include_details=False)
    assert result["id"] == "ocr:job" and result["summary"] == "template"
    assert result["details"] is None
    with pytest.raises(AssertionError, match="full metrics"):
        logs._ocr_job_row(row)


def test_terminal_lineage_projection_cannot_validate_a_pipeline_write():
    with pytest.raises(ValueError, match="terminal_read_evidence_is_not_pipeline_write_proof"):
        _receipt(None, None, None, _read_evidence={})


def test_workflow_read_creates_one_pure_analysis_per_request(monkeypatch):
    from src.services import order_workflow_v2_service as workflow
    from src.services.canonical_ocr_artifact_service import _EvidenceAnalysis

    seen = []
    order, state = SimpleNamespace(id="read-order"), SimpleNamespace(order_id="read-order")

    def serialize(session, **options):
        assert options["_read_context"] == (order, state)
        seen.append(options["_analysis"])
        return {"context_suggestion": {"present": True}, "blockers": []}

    def resolution(session, actual_order, actual_state, **options):
        assert actual_order is order and actual_state is state
        seen.append(options["_analysis"])
        return {"status": "resolved"}

    monkeypatch.setattr(workflow, "_serialize_workflow_checked", serialize)
    monkeypatch.setattr(workflow, "_workflow_menu_amount_resolution", resolution)
    for _ in range(2):
        result = workflow._workflow_read_projection(object(), order, state)
        assert result["menu_amount_resolution"] == {"status": "resolved"}
    assert all(isinstance(analysis, _EvidenceAnalysis) for analysis in seen)
    assert seen[0] is seen[1] and seen[2] is seen[3]
    assert seen[0] is not seen[2]


def test_workflow_metadata_borrow_is_explicit_and_editors_keep_owned_copies():
    from src.services import order_workflow_v2_service as workflow

    stored = {"ocr_job_id": "job", "nested": {"source": ["original"]}}
    state = SimpleNamespace(secondary_actions_json={workflow.WORKFLOW_V2_META_KEY: stored})
    borrowed = workflow._workflow_meta(state, copy_meta=False)
    owned = workflow._workflow_meta(state)
    assert borrowed is stored and owned == stored and owned is not stored
    owned["nested"]["source"].append("editor change")
    assert stored["nested"]["source"] == ["original"]


def test_terminal_read_borrows_internal_payload_but_persister_owns_copy(monkeypatch):
    from src.services import ingest_job_service

    captured = {"job_id": "job", "input_reference": "original.pdf", "execution_id": "execution",
                "ingest_lease": {"id": "ingest"}, "orders": {"order": {"original": ["value"]}}}
    terminal = {"job_id": "job", "execution_id": "execution", "status": "done"}
    job = SimpleNamespace(id="job", input_reference="original.pdf", status="done",
                          metrics={"nonworkflow_execution": captured})
    row = SimpleNamespace(owner_lease={"id": "ingest"})
    monkeypatch.setattr(ingest_job_service, "ingest_terminal_receipt", lambda *args, **kwargs: terminal)
    kind, read = _receipt(None, row, job, validate_pipeline=False, _read_evidence={})
    assert kind == "terminal" and read["capture"] is captured and read["receipt"] is terminal
    kind, write = _receipt(None, row, job)
    assert kind == "terminal" and write == read
    write["capture"]["orders"]["order"]["original"].append("persister change")
    write["receipt"]["status"] = "changed"
    assert captured["orders"]["order"]["original"] == ["value"]
    assert terminal["status"] == "done"


@pytest.mark.parametrize("phase", ["uploaded", "running", "selected", "saved"])
def test_display_summary_never_rebuilds_or_verifies_ocr_on_read(history_case, monkeypatch, phase):
    from src.services import order_workflow_v2_service as workflow
    from src.services import workflow_display_read_service as display
    from src.services import canonical_ocr_artifact_service as artifacts
    case = history_case()
    if phase == "running":
        case.queue()
    elif phase in {"selected", "saved"}:
        produced = case.produce(cell_values="5")
        if phase == "saved":
            case.save(produced["sheet"])

    def forbidden(*args, **kwargs):
        raise AssertionError("display invoked an upstream producer or physical OCR verification")
    monkeypatch.setattr(workflow, "_build_materialization_candidate_for_saved_sheet", forbidden)
    monkeypatch.setattr(workflow, "_serialize_workflow_checked", forbidden)
    monkeypatch.setattr(workflow, "_serialize_ocr_result", forbidden)
    monkeypatch.setattr(artifacts, "_verify", forbidden)
    with case.readonly():
        value, error = display.get_display(case.id)
    assert error is None
    assert value["order_id"] == case.id
    assert value["workflow"]["validation_state"] == "references_only"
    assert value["loaded_sections"] == []
    assert "ocr_candidates" not in value
    assert "menu_amount_resolution" not in value["workflow"]
    assert "sheet" not in (value.get("saved_sheet") or {})
    job = value["workflow"]["ocr_job"]
    if job is not None:
        common = value["workflow"]["common_job_status"]
        assert common["jobId"] == job["ocr_job_id"]
        assert common["sourceState"] == job["status"]


def test_saved_output_display_does_not_materialize_and_sheet_scope_preserves_edits(history_case, monkeypatch):
    from src.services import workflow_display_read_service as display
    from src.services import order_workflow_v2_service as workflow
    case = history_case()
    source = case.produce(cell_values="5")
    sheet = source["sheet"]
    case.save(sheet)

    def forbidden(*args, **kwargs):
        raise AssertionError("read rebuilt output")
    monkeypatch.setattr(workflow, "_build_materialization_candidate_for_saved_sheet", forbidden)
    with case.readonly():
        output, error = display.get_display(case.id, scope="output")
        assert error is None
        assert output["loaded_sections"] == ["output"]
        assert output["bagging_result"] is None and output["output_bundle"] is None
        saved, error = display.get_display(case.id, scope="sheet")
    assert error is None
    assert saved["saved_sheet"]["sheet"]["rows"] == sheet["rows"]
    assert saved["loaded_sections"] == ["ocr", "sheet"]
    assert all(row["detail_state"] == "not_loaded" for row in saved["ocr_candidates"]["results"])


def test_initial_display_loads_only_current_scope_with_one_master_snapshot(history_case, monkeypatch):
    from src.services import workflow_display_read_service as display
    from src.services import facility_master_service
    case = history_case()
    source = case.produce(cell_values="5")
    case.save(source["sheet"])
    read = facility_master_service.get_snapshot
    calls = []

    def snapshot():
        value = read()
        calls.append(value.revision)
        return value

    monkeypatch.setattr(facility_master_service, "get_snapshot", snapshot)
    with case.readonly():
        initial, error = display.get_display(case.id, scope="initial")
        assert error is None
        assert len(calls) == 1
        explicit, error = display.get_display(case.id, scope="output")
    assert error is None
    assert initial == explicit
    assert initial["display_scope"] == "output"
    assert initial["loaded_sections"] == ["output"]
    assert "ocr_candidates" not in initial


def test_saved_cell_map_display_does_not_fetch_unused_evidence_payload(history_case):
    from sqlalchemy import event
    from src.services import workflow_display_read_service as display
    case = history_case()
    source = case.produce(cell_values="5")
    # This component's synthetic recognizer has no physical target assignment.
    # Author a saved map explicitly to exercise the stored-map read branch.
    source["sheet"]["target_cell_map"] = [{
        "target_row_index": 0,
        "target_col_index": source["sheet"]["fields"].index("qty.regular_2f"),
        "field": "qty.regular_2f", "sheet_cell": "component-quantity",
        "target_cell_id": "component-quantity", "bbox": [60.0, 0.0, 70.0, 10.0],
        "center": [65.0, 5.0],
    }]
    assert source["sheet"]["target_cell_map"]
    case.save(source["sheet"])
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with case.readonly():
        event.listen(case.engine, "before_cursor_execute", observe)
        try:
            value, error = display.get_display(case.id, scope="sheet")
        finally:
            event.remove(case.engine, "before_cursor_execute", observe)
    assert error is None
    assert value["saved_sheet"]["sheet"] == source["sheet"]
    assert not any("order_ocr_evidence_runs.payload_json" in sql for sql in statements)


def test_display_rejects_missing_saved_reference_instead_of_rebuilding(history_case):
    from src.db import session_scope
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services import workflow_display_read_service as display
    case = history_case()
    source = case.produce(cell_values="5")
    case.save(source["sheet"])
    with session_scope() as session:
        state = session.get(OrderWorkflowState, case.id)
        actions = dict(state.secondary_actions_json)
        actions["workflow_v2"] = {**actions["workflow_v2"], "output_bundle_id": "missing-output"}
        state.secondary_actions_json = actions
    with case.readonly():
        value, error = display.get_display(case.id)
        assert error is None
        assert "output_bundle_artifact_missing" in value["workflow"]["blockers"]
        value, error = display.get_display(case.id, scope="output")
    assert value is None and error == "output_bundle_artifact_missing"


def test_confirmed_display_reads_snapshot_without_rebuilding_ocr(owned_outputs, isolated_formal_schema, monkeypatch):
    from datetime import datetime
    from sqlalchemy import select
    from src.db import session_scope
    from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
    from src.workers.ingest_mail_adapter import IngestEmailPayload
    from tests.unit.test_disabled_facility_columns import _snapshot
    from src.services import workflow_display_read_service as display
    from src.services import order_workflow_v2_service as workflow
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(message_id="display-confirmed", pdf_uri="component:unregistered",
            received_at=datetime(2026, 4, 18), facility_hint="display-facility",
            week_hint="2026-04@2026-04-18~2026-04-18"),
        lines=[{"date": "2026-04-18", "daypart": "昼", "menu_name": "Display dish",
            "diet_type": "regular", "area_id": "2F", "bag_type": "standard", "quantity_original": 5}],
        recipe={"qty_per_serving": 40, "unit_type": "g", "category": "副菜", "temp_type": "cold"},
        physical_slot=0)
    with session_scope() as session:
        snapshot_id = session.scalar(select(OrderConfirmedSnapshot.id).where(OrderConfirmedSnapshot.order_id == order["id"]))
    before = _snapshot(isolated_formal_schema)

    def forbidden(*args, **kwargs):
        raise AssertionError("confirmed display rebuilt its producer")
    monkeypatch.setattr(workflow, "_build_materialization_candidate_for_saved_sheet", forbidden)
    monkeypatch.setattr(workflow, "_serialize_ocr_result", forbidden)
    value, error = display.get_display(order["id"], scope="output")
    assert error is None
    assert value["workflow"]["state"] == "confirmed"
    assert value["workflow"]["confirmed_snapshot_id"] == snapshot_id
    assert value["output_bundle"]["confirmed_snapshot_id"] == snapshot_id
    assert _snapshot(isolated_formal_schema) == before


def test_read_operation_never_survives_response_or_changed_input(monkeypatch):
    from sqlalchemy.orm import Session
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.services import ocr_evidence_service as evidence
    calls = []
    row = OrderOcrEvidenceRun(id="owned", order_id="order", payload_json={"unknown": [1, "1"]},
        artifact_manifest_json={}, capabilities_json={}, degraded_reasons_json=[])
    monkeypatch.setattr(evidence, "evidence_run_selectability_error", lambda actual, **kwargs: calls.append(actual) or None)
    with Session() as session:
        session.add(row)
        with evidence._EvidenceReadOperation(session) as operation:
            assert operation.selectability_error(row) is None
            assert operation.selectability_error(row) is None
            assert len(calls) == 1
            row.payload_json = {"unknown": [1, "changed"]}
            assert operation.selectability_error(row) is None
            assert len(calls) == 2
        with pytest.raises(ValueError, match="evidence_read_operation_closed"):
            operation.selectability_error(row)
        with evidence._EvidenceReadOperation(session) as next_response:
            assert next_response.selectability_error(row) is None
            assert len(calls) == 3


def test_read_operation_is_not_a_command_receipt():
    from sqlalchemy.orm import Session
    from src.services import ocr_evidence_service as evidence, order_service
    with Session() as session, evidence._EvidenceReadOperation(session) as operation:
        with pytest.raises(ValueError, match="evidence_read_operation_not_command_proof"):
            order_service._capture_synchronous_cache_write_input("order", write_session=session, _read_operation=operation)


def test_dataset_choices_combine_status_before_reading_fresh_choices(monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    from src.api import staging_datasets as api
    calls = []
    monkeypatch.setattr(api.datasets, "staging_runtime", lambda: True)
    monkeypatch.setattr(api, "refresh_status", lambda response: calls.append("refresh") or {"configured": True, "job": None})
    monkeypatch.setattr(api.datasets, "snapshot_choices", lambda: calls.append("choices") or [])
    request = Request({"type": "http", "headers": []})
    plain = api.choices(request, Response())
    assert calls == ["choices"] and "refresh" not in plain
    calls.clear()
    combined = api.choices(request, Response(), include_refresh=True)
    assert calls == ["refresh", "choices"]
    assert combined["refresh"] == {"configured": True, "job": None}
    assert combined["selected"] == "test"


def test_failed_refresh_status_does_not_hide_dataset_choices(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from starlette.responses import Response
    from src.api import staging_datasets as api
    monkeypatch.setattr(api.datasets, "staging_runtime", lambda: True)
    monkeypatch.setattr(api.datasets, "snapshot_choices", lambda: [])
    def unavailable(response):
        raise HTTPException(503, "private refresh failure")
    monkeypatch.setattr(api, "refresh_status", unavailable)
    value = api.choices(Request({"type": "http", "headers": []}), Response(), include_refresh=True)
    assert value["selected"] == "test" and value["refresh"] is None
    assert value["refresh_error"] and "private" not in value["refresh_error"]


def test_http_startup_does_not_import_native_image_recognition():
    import os
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c",
        "import sys; import src.main; assert 'cv2' not in sys.modules, 'HTTP startup imported cv2'"],
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-2000:]
