"""Quantity contents never block the editable sheet; source integrity still does."""
from copy import deepcopy

import pytest

from src.db import session_scope
from src.models.order_workflow_state import OrderWorkflowState
from src.models.ocr_job import OcrJob
from src.services import hakodate_ocr_evidence_service as evidence
from src.services import order_service, order_workflow_v2_service as workflow
from test_recognition_review_acceptance import reviewed_order, snapshot
from test_hakodate_raw_authority import run


@pytest.mark.parametrize("raw", ["-", "--", "I", "26", "", "０１", "2?", "-1"])
@pytest.mark.parametrize("score", [0.0, 0.01, 0.9])
def test_quantity_raw_content_and_score_do_not_require_review(monkeypatch, raw, score):
    source = run(monkeypatch, raw, score, white=True)
    assert source["resolution_state"] == "resolved"
    assert source["resolved_value"] == raw
    assert evidence.validate_ocr_quantity_source({"raw_text": raw,
        "engine_metadata": {"recognizer_output": source}}) == raw


@pytest.mark.parametrize("reviewed_order", [None, "numeric", "empty"], indirect=True)
def test_historic_content_rejection_builds_editable_sheet_without_acceptance(reviewed_order):
    identity, evidence_id, review, engine = reviewed_order
    original = deepcopy(snapshot(engine)["order_ocr_evidence_runs"])
    with session_scope() as session:
        revision = session.get(OrderWorkflowState, identity).revision
        job = session.get(OcrJob, "review-job")
        job.error_message = "evidence_unusable"
        job.metrics = {**job.metrics, "result_state": "review_required",
                       "recovery_action": "review_recognition_cells", "evidence_run_id": evidence_id}
        serialized = workflow._serialize_ocr_job(job, session=session)
        assert serialized["status"] == "failed"  # Actual execution history remains immutable.
        assert serialized["quantity_content_editable"] is True
    selected, error = workflow.select_ocr_result(identity, evidence_id, expected_revision=revision)
    assert error is None, error
    assert selected["state"] == "ocr_selected"
    result, error = workflow.build_sheet_from_selected_ocr(identity)
    assert error is None, error
    assert result["blockers"] == []
    sheet = result["sheet"]
    quantity_columns = [index for index, field in enumerate(sheet["fields"]) if field.startswith("qty.")]
    raw = review["review_cells"][0]["raw_text"]
    assert raw in [row[index] for row in sheet["rows"] for index in quantity_columns]
    saved, error = workflow.save_sheet(order_id=identity, sheet=sheet, edited_by="component-operator",
                                      expected_revision=selected["revision"])
    assert error is None, error
    reread, error = workflow.get_saved_sheet(identity)
    assert error is None
    assert reread["sheet"]["rows"] == sheet["rows"]
    edited = deepcopy(sheet)
    edited["rows"][0][quantity_columns[0]] = "13"
    current, error = workflow.get_workflow_readonly(identity)
    assert error is None
    saved, error = workflow.save_sheet(order_id=identity, sheet=edited, edited_by="component-operator",
                                      expected_revision=current["revision"])
    assert error is None, error
    reread, error = workflow.get_saved_sheet(identity)
    assert error is None
    assert reread["sheet"]["rows"][0][quantity_columns[0]] == "13"
    refreshed, error = workflow.get_workflow_readonly(identity)
    assert error is None and refreshed["state"] == "sheet_saved"
    draft = order_service.get_latest_sheet_draft(identity, backfill_from_revision=False)
    assert draft["draft_sheet_json"]["rows"] == edited["rows"]
    ocr_sheet, error = order_service.get_ocr_sheet(identity)
    assert error is None, error
    assert ocr_sheet["rows"] == edited["rows"]
    assert snapshot(engine)["order_ocr_evidence_runs"] == original


def test_quantity_source_identity_still_blocks_substitution():
    with pytest.raises(ValueError, match="source_conflict"):
        evidence.validate_ocr_quantity_source({"raw_text": "1", "engine_metadata": {
            "recognizer_output": {"recognizer_raw_text": "--"}}})


def test_assigned_empty_quantity_is_not_a_content_error():
    assert evidence.validate_assigned_ocr_quantity({
        "assignment_state": "assigned", "assigned_value": "", "raw_texts": [""],
    }) == ""


def test_structural_pipeline_blocker_is_not_a_quantity_content_rejection():
    from test_recognition_review_acceptance import ten_payload
    payload = ten_payload()
    assert evidence.quantity_content_rejection_only(payload)
    payload["hakodate_canonical_pipeline"]["blockers"].append("hakodate_target_cell_map_missing")
    assert not evidence.quantity_content_rejection_only(payload)
