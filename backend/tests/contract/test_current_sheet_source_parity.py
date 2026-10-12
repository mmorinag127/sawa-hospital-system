"""Real owned capture/producer/read contracts; external recognition is a component double."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.db import session_scope
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_ocr_revision import OrderOcrRevision
from src.models.order_ocr_cache import OrderOcrCache
from src.models.menu import MonthlyMenuEntry
from src.services import order_service as orders, order_workflow_v2_service as workflow
from src.services import ocr_evidence_service, workflow_attempt_guard_service as guard
from src.services import workflow_state_service
from test_order_workflow_v2_service import (
    _standard_active_template_for_workflow_tests, april_output_context, april_projection,
    _current_workflow_revision, _queue_owned_april_job, _claim_april_job, _id, delivery,
    _create_order_with_evidence,
)
from test_modernization_workflow_surface_parity import database_snapshot


@pytest.fixture(autouse=True)
def component_digits(monkeypatch):
    import test_h2_operator_repair_revision as helpers
    original = helpers._provider_output_files
    value = {"quantity": "2"}

    def provider(output_dir, *, records, regions, sheet_values):
        records, sheet_values = deepcopy(records), deepcopy(sheet_values)
        for record in records:
            if record["field"] == "qty.regular_x":
                record["ocr_text"] = value["quantity"]
                record["recognizer_accepted_candidate"]["normalized_digits"] = value["quantity"]
                sheet_values[record["sheet_cell"]] = value["quantity"]
        return original(output_dir, records=records, regions=regions, sheet_values=sheet_values)

    monkeypatch.setattr(helpers, "_provider_output_files", provider)
    return value


def files(tmp_path):
    return {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob('*') if p.is_file() and 'sqlite' not in p.name}


def quantity(sheet):
    return sheet["rows"][0][sheet["fields"].index("qty.regular_x")]


def assert_rows(actual, expected):
    for key in ("fields", "rows", "row_ids", "header"):
        assert actual[key] == expected[key], key
    assert [[type(v) for v in row] for row in actual["rows"]] == [[type(v) for v in row] for row in expected["rows"]]


def read_current(order_id):
    sheet, error = orders.get_ocr_sheet(order_id)
    assert error is None, error
    return sheet


def test_selected_no_saved_all_read_paths_match_owned_projection(april_projection, tmp_path):
    order_id, _, evidence_id, _ = april_projection
    with session_scope() as session:
        assert session.get(OrderWorkflowState, order_id).draft_id is None
    expected, error = workflow.build_sheet_from_selected_ocr(order_id)
    assert error is None, error
    assert quantity(expected["sheet"]) == "2"
    with session_scope() as session:
        history = deepcopy(expected["sheet"])
        history["rows"][0][3] = "9"
        history["rows"][0][2] = "Revision Menu"
        session.add(OrderOcrRevision(id="historical-nine", order_id=order_id,
            fields=history["fields"], header=history["header"], rows=history["rows"],
            row_ids=["historical-row"], row_count=1))
    before, original_files = database_snapshot(), files(tmp_path)
    evidence = ocr_evidence_service.get_evidence_run(evidence_id)
    original_evidence = deepcopy(evidence)
    for kwargs in ({}, {"use_saved_draft": False}, {"use_saved_draft": False, "evidence_run_override": evidence}):
        sheet, error = orders.get_ocr_sheet(order_id, **kwargs)
        assert error is None, error
        assert_rows(sheet, expected["sheet"])
        assert sheet["base_evidence_run_id"] == evidence_id
    context = orders.get_current_sheet_context(order_id)
    assert_rows(context, expected["sheet"])
    recovered, error = orders.build_recoverable_ocr_sheet_payload(order_id, "ocr_not_ready")
    assert error is None, error
    assert_rows(recovered, expected["sheet"])
    semantic, error = orders._build_best_available_semantic_draft_with_error(order_id, use_saved_draft=False)
    assert error is None, error
    assert_rows(semantic, expected["sheet"])
    assert semantic["base_evidence_run_id"] == evidence_id
    from src.main import app
    client = TestClient(app)
    try:
        response = client.get(f"/orders/{order_id}/workflow-v2/sheet-source")
    finally:
        client.close()
    assert response.status_code == 200, response.text
    assert_rows(response.json()["sheet"], expected["sheet"])
    assert evidence == original_evidence
    assert database_snapshot() == before
    assert files(tmp_path) == original_files


@pytest.mark.parametrize("value", ["3", "11", 0, None, "", "  "])
def test_saved_operator_values_are_exact_and_selected_preview_stays_separate(april_projection, value, tmp_path):
    order_id, _, _, _ = april_projection
    projected, error = workflow.build_sheet_from_selected_ocr(order_id)
    assert error is None
    sheet = deepcopy(projected["sheet"])
    sheet["rows"][0][sheet["fields"].index("qty.regular_x")] = value
    sheet["rows"][0][sheet["fields"].index("remarks")] = "  operator note  "
    saved, error = workflow.save_sheet(order_id=order_id, sheet=sheet, edited_by="operator-parity",
        expected_revision=_current_workflow_revision(order_id))
    assert error is None, error
    original = deepcopy(saved["saved_sheet"]["sheet"])
    assert quantity(original) == value and type(quantity(original)) is type(value)
    before, original_files = database_snapshot(), files(tmp_path)
    for current in (read_current(order_id), orders.get_current_sheet_context(order_id)):
        assert_rows(current, original)
    recovered, error = orders.build_recoverable_ocr_sheet_payload(order_id, "ocr_not_ready")
    assert error is None, error
    assert_rows(recovered, original)
    preview, error = orders.get_ocr_sheet(order_id, use_saved_draft=False)
    assert error is None, error
    assert_rows(preview, projected["sheet"])
    assert database_snapshot() == before
    assert files(tmp_path) == original_files


def test_no_selected_no_saved_explicit_stop_no_reconstruction(april_projection, tmp_path):
    order_id = april_projection[0]
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        state.evidence_run_id = None
        assert state.draft_id is None
    before, original_files = database_snapshot(), files(tmp_path)
    assert workflow.build_sheet_from_selected_ocr(order_id) == (None, "selected_ocr_required")
    assert orders.get_ocr_sheet(order_id, use_saved_draft=False) == (None, "selected_ocr_required")
    context = orders.get_current_sheet_context(order_id)
    assert context["draft_payload"] is None
    assert context["blockers"] == ["selected_ocr_required"]
    assert orders.build_recoverable_ocr_sheet_payload(order_id, "ocr_not_ready") == (None, "selected_ocr_required")
    assert database_snapshot() == before
    assert files(tmp_path) == original_files


@pytest.mark.parametrize("source", ["selected", "saved", "missing"])
def test_current_label_export_never_substitutes_exact_history(april_projection, source, tmp_path):
    order_id, _, evidence_id, _ = april_projection
    projected, error = workflow.build_sheet_from_selected_ocr(order_id)
    assert error is None, error
    expected = deepcopy(projected["sheet"])
    assert quantity(expected) == "2"
    if source == "saved":
        expected["rows"][0][expected["fields"].index("qty.regular_x")] = "3"
        saved, error = workflow.save_sheet(order_id=order_id, sheet=expected, edited_by="label-parity",
            expected_revision=_current_workflow_revision(order_id))
        assert error is None, error
        expected = deepcopy(saved["saved_sheet"]["sheet"])
        assert quantity(expected) == "3"
    history = deepcopy(expected)
    history["rows"][0][history["fields"].index("qty.regular_x")] = "9"
    with session_scope() as session:
        session.add(OrderOcrRevision(id="label-history-nine", order_id=order_id,
            fields=history["fields"], header=history["header"], rows=history["rows"],
            row_ids=history["row_ids"], row_count=len(history["rows"]), sheet_save_mode="exact",
            metadata_json={"base_evidence_run_id": evidence_id}))
        cache = session.get(OrderOcrCache, order_id)
        payload = deepcopy(cache.payload or {})
        payload["_edited_ocr"] = {"base_evidence_run_id": evidence_id, "selected_revision_id": "label-history-nine"}
        cache.payload = payload
    exact = orders._select_order_sheet_revision(order_id=order_id, payload=None, exact_only=True)
    assert exact["revision_id"] == "label-history-nine"
    assert quantity(exact) == "9"
    if source == "missing":
        with session_scope() as session:
            state = session.get(OrderWorkflowState, order_id)
            state.evidence_run_id = None
            assert state.draft_id is None
    before, original_files = database_snapshot(), files(tmp_path)
    output = tmp_path / "labels" / (f"{order_id}.expected_sheet.json" if source == "saved" else "current.json")
    kwargs = {"output_dir": output.parent} if source == "saved" else {"output_path": output}
    result, error = orders.export_ocr_sheet_label(order_id, **kwargs)
    if source == "missing":
        assert (result, error) == (None, "selected_ocr_required")
        assert not output.exists()
        assert not output.parent.exists()
    else:
        assert error is None, error
        assert result["order_id"] == order_id
        assert result["output_path"] == str(output)
        assert_rows(result["sheet"], expected)
        assert json.loads(output.read_text()) == result["sheet"]
        assert output.read_bytes().endswith(b"\n")
    assert database_snapshot() == before
    after_files = files(tmp_path)
    if source != "missing":
        del after_files[str(output.relative_to(tmp_path))]
    assert after_files == original_files


@pytest.mark.parametrize("damage", ["artifact", "artifact-bytes", "pdf-bytes", "menu", "template", "authored-source"])
def test_missing_or_changed_source_refuses_without_side_effects(april_projection, damage, tmp_path, record_property):
    order_id, _, evidence_id, _ = april_projection
    with session_scope() as session:
        if damage == "artifact":
            row = session.get(OrderOcrEvidenceRun, evidence_id)
            row.artifact_manifest_json = {}
        elif damage == "artifact-bytes":
            row = session.get(OrderOcrEvidenceRun, evidence_id)
            path = Path(row.payload_json["hakodate_canonical_pipeline"]["outputs"]["records"])
            path.write_bytes(path.read_bytes() + b" ")
        elif damage == "pdf-bytes":
            row = session.get(OrderOcrEvidenceRun, evidence_id)
            path = Path(row.payload_json["hakodate_repair_source_receipt"]["selected_pdf_receipt"]["storage_uri"])
            path.write_bytes(path.read_bytes() + b" ")
        elif damage == "authored-source":
            path = Path(os.environ["FACILITY_MASTER_PATH"])
            data = json.loads(path.read_text())
            data["facilities"][0]["versions"][0]["facility"]["fax_template"]["columns"][3]["header"] = "Changed source"
            path.write_text(json.dumps(data))
        elif damage == "menu":
            session.query(MonthlyMenuEntry).delete()
        else:
            state = session.get(OrderWorkflowState, order_id)
            state.template_version_id = None
    before, original_files = database_snapshot(), files(tmp_path)
    expected_error = {
        "artifact": "synchronous_input_missing",
        "artifact-bytes": "canonical_artifact_bytes_changed",
        "pdf-bytes": "selected_pdf_bytes_changed",
        "menu": "hakodate_original_expectation_required",
        "template": "template_version_required",
        "authored-source": "template_version_mismatch",
    }[damage]
    assert workflow.build_sheet_from_selected_ocr(order_id) == (None, expected_error)
    record_property("source_error", expected_error)
    assert orders.get_ocr_sheet(order_id, use_saved_draft=False) == (None, expected_error)
    # The saved/current boundary checks authored bytes before template projection.
    current_error = "original_template_configured_source_mismatch" if damage == "authored-source" else expected_error
    assert orders.build_recoverable_ocr_sheet_payload(order_id, "ocr_not_ready") == (None, current_error)
    assert database_snapshot() == before
    assert files(tmp_path) == original_files


@pytest.mark.parametrize("state", ["selected", "saved", "blocked"])
def test_current_gate_metadata_matches_real_projection_and_compare_api(april_projection, state, tmp_path, record_property):
    order_id = april_projection[0]
    projection, error = workflow.build_sheet_from_selected_ocr(order_id)
    assert error is None, error
    expected_sheet = deepcopy(projection["sheet"])
    if state != "selected":
        expected_sheet["rows"][0][expected_sheet["fields"].index("qty.regular_x")] = "3"
        if state == "blocked":
            # Persist an explicit blocked review input through the real save;
            # the gate is computed normally, never replaced by a success double.
            expected_sheet["warnings"] = ["sheet_quantity_column_unmapped"]
        saved, error = workflow.save_sheet(order_id=order_id, sheet=expected_sheet,
            edited_by="metadata-parity", expected_revision=_current_workflow_revision(order_id))
        assert error is None, error
        expected_sheet = deepcopy(saved["saved_sheet"]["sheet"])
    context = orders.get_current_sheet_context(order_id)
    expected = workflow_state_service.project_workflow_state(order_id, current_sheet_context=context)
    gate = expected["apply_gate"]
    assert all(type(gate[key]) is bool for key in ("can_apply", "can_confirm"))
    if state == "blocked":
        assert gate["can_apply"] is False and gate["can_confirm"] is False
        assert "sheet_quantity_column_unmapped" in gate["apply_blockers"]
        assert "sheet_quantity_column_unmapped" in gate["confirm_blockers"]
    record_property("canonical_apply_gate", json.dumps(gate, sort_keys=True))
    before, original_files, original_input = database_snapshot(), files(tmp_path), deepcopy(expected_sheet)
    current = read_current(order_id)
    assert_rows(current, expected_sheet)
    for key in ("can_apply", "can_confirm", "apply_blockers", "confirm_blockers", "confirm_warnings"):
        assert current[key] == gate[key], key
    assert current["review_state"] == expected["state"]
    for key, tokens, severity in (("apply_blocker_details", "apply_blockers", "blocker"),
                                  ("confirm_blocker_details", "confirm_blockers", "blocker"),
                                  ("confirm_warning_details", "confirm_warnings", "warning")):
        assert current[key] == orders._review_reason_details(gate[tokens], severity=severity)
    from src.main import app
    client = TestClient(app)
    try:
        response = client.post(f"/orders/{order_id}/quantity-assignment-compare", json={},
            headers={"If-Match": str(_current_workflow_revision(order_id))})
    finally:
        client.close()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["legacy"]["error"] is None
    assert result["hakodate"]["error"] is None
    summary = result["legacy"]["summary"]
    assert summary["available"] is True
    assert summary["rows_count"] == len(expected_sheet["rows"])
    for key in ("can_apply", "can_confirm", "apply_blockers", "confirm_blockers"):
        assert summary[key] == gate[key], key
    assert all(type(summary[key]) is bool for key in ("can_apply", "can_confirm"))
    assert database_snapshot() == before
    assert files(tmp_path) == original_files
    assert expected_sheet == original_input


@pytest.mark.parametrize("kind", ["forged", "foreign", "missing"])
def test_unverified_candidate_cannot_replace_current_selection(april_projection, kind):
    order_id, _, evidence_id, _ = april_projection
    candidate = ocr_evidence_service.get_evidence_run(evidence_id)
    if kind == "forged":
        candidate["payload_json"]["pipeline_version"] = "not-the-stored-producer"
        expected = "candidate_evidence_changed"
    elif kind == "foreign":
        _, foreign_id, _ = _create_order_with_evidence()
        candidate = ocr_evidence_service.get_evidence_run(foreign_id)
        expected = "selected_ocr_missing"
    else:
        candidate["id"] = "not-a-stored-candidate"
        expected = "selected_ocr_missing"
    before, original = database_snapshot(), deepcopy(candidate)
    assert orders.get_ocr_sheet(order_id, use_saved_draft=False, evidence_run_override=candidate) == (None, expected)
    assert orders.build_recoverable_ocr_sheet_payload(order_id, "ocr_not_ready", evidence_run_override=candidate) == (None, expected)
    assert database_snapshot() == before
    assert candidate == original


def test_supplied_capture_cannot_bypass_a_new_revision(april_projection):
    order_id = april_projection[0]
    captured = orders._capture_synchronous_cache_write_input(order_id)
    original = deepcopy(captured)
    with session_scope() as session:
        session.get(OrderWorkflowState, order_id).revision += 1
    before = database_snapshot()
    for saved in (True, False):
        assert orders.get_ocr_sheet(order_id, use_saved_draft=saved, synchronous_write_input=captured) == (None, "synchronous_input_conflict")
    assert database_snapshot() == before
    assert captured == original


def test_projection_revalidates_real_capture_at_publication(april_projection, monkeypatch):
    order_id = april_projection[0]
    project = orders._apply_hakodate_sheet_output_to_sheet_payload
    raced = {}

    def race(**kwargs):
        result = project(**kwargs)
        with session_scope() as session:
            session.get(OrderWorkflowState, order_id).revision += 1
        raced["database"] = database_snapshot()
        return result

    monkeypatch.setattr(orders, "_apply_hakodate_sheet_output_to_sheet_payload", race)
    assert workflow.build_sheet_from_selected_ocr(order_id) == (None, "current_sheet_input_changed")
    assert database_snapshot() == raced["database"]


def test_distinct_stored_candidate_retains_own_value_without_adoption(april_projection, component_digits, tmp_path, monkeypatch):
    order_id, _, candidate_id, _ = april_projection
    component_digits["quantity"] = "7"
    job_id = _id("candidate")
    queued = _queue_owned_april_job(order_id, job_id, tmp_path, monkeypatch)
    revision, attempt = queued["workflow"]["revision"], queued["ocr_job"]["workflow_attempt_id"]
    claim = _claim_april_job(queued)
    with (delivery.claim_context(claim), orders.workflow_ocr_write_context(attempt, revision, job_id, order_id),
          guard.workflow_execution_context(job_id, order_id, attempt, revision)):
        current, error = orders.rerun_ocr_evidence_only(order_id, job_id=job_id, project_sheet=False, refresh_workflow=False)
    assert error is None, error
    _, error = workflow.select_ocr_result(order_id, current["id"], expected_revision=_current_workflow_revision(order_id))
    assert error is None, error
    candidate = ocr_evidence_service.get_evidence_run(candidate_id)
    original = deepcopy(candidate)
    before, original_files = database_snapshot(), files(tmp_path)
    assert quantity(read_current(order_id)) == "7"
    preview, error = orders.get_ocr_sheet(order_id, use_saved_draft=False, evidence_run_override=candidate)
    assert error is None, error
    assert quantity(preview) == "2"
    assert preview["base_evidence_run_id"] == candidate_id
    contract = orders.resolve_candidate_evidence_preview_contract(order_id, candidate_evidence_run=candidate)
    assert contract["previewable"] is True, contract
    assert quantity(contract["draft_payload"]) == "2"
    assert contract["preview_draft"]["base_evidence_run_id"] == candidate_id
    assert database_snapshot() == before
    assert files(tmp_path) == original_files
    assert candidate == original
    project = orders._apply_hakodate_sheet_output_to_sheet_payload
    raced = {}

    def change_candidate(**kwargs):
        result = project(**kwargs)
        with session_scope() as session:
            row = session.get(OrderOcrEvidenceRun, candidate_id)
            row.artifact_digest = "changed-after-capture"
        raced["database"] = database_snapshot()
        return result

    monkeypatch.setattr(orders, "_apply_hakodate_sheet_output_to_sheet_payload", change_candidate)
    assert orders.get_ocr_sheet(order_id, use_saved_draft=False, evidence_run_override=candidate) == (None, "candidate_evidence_changed")
    assert database_snapshot() == raced["database"]
