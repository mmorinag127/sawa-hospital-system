"""Synthetic native FAX plus explicit operator input, not UI/auth/live proof.

The unchanged numeric_fax creates the original expected values in a real PDF.
Recognition/thresholds/geometry/adoption are real. The operator copies that
original oracle, never recognizer output, and uses actual pre-save commands.
This sibling does not waive the original numeric test's failure.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from test_modernization_owned_numeric_fax import numeric_fax as numeric_fax
from test_modernization_owned_ocr_workflow import ORDER
from src import db
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_ocr_artifact_service as artifacts
from src.services import order_workflow_v2_service as workflow


def revision():
    with db.session_scope() as session:
        return session.get(OrderWorkflowState, ORDER).revision


def test_real_low_score_hold_then_explicit_original_fax_operator_save(numeric_fax):
    f, identity = numeric_fax
    original_expected = deepcopy(f.expected)
    evidence = f.produce()
    payload = deepcopy(evidence["payload_json"])
    artifacts.validate_payload(payload)
    records = payload["hakodate_ocr_evidence_records"]
    by_cell = {record["engine_metadata"]["sheet_cell"]: record for record in records}
    assert len(records) == len(by_cell) == len(original_expected) == 112
    assert len(f.quantity_plan) == len(f.notes_plan) == 56
    assert set(by_cell) == set(original_expected)
    assert {cell: record["raw_text"] for cell, record in by_cell.items()} == original_expected
    assert {cell: record["normalized_value"] for cell, record in by_cell.items()} == original_expected
    packet = payload["hakodate_canonical_pipeline"]["artifact_packet"]
    assert set(packet["outputs"]) == set(artifacts.REQUIRED_OUTPUTS) | {"preprocess_observation"}
    assert packet["source_receipt"]["selected_pdf_receipt"] == f.receipt
    artifact_bytes = {Path(item["uri"]): Path(item["uri"]).read_bytes()
                      for item in packet["outputs"].values()}
    for item in packet["outputs"].values():
        data = artifact_bytes[Path(item["uri"])]
        assert len(data) == item["size_bytes"]
        assert sha256(data).hexdigest() == item["sha256"]

    low_cells = {cell for cell, record in by_cell.items()
                 if record["normalized_value"] != "" and record["confidence"] < 0.90}
    assert low_cells, "native fixture must actually exercise a nonblank low-score hold"
    f.select()
    before_projection_revision = revision()
    projected, error = workflow.build_sheet_from_selected_ocr(ORDER)
    assert error is None, (error, projected)
    assert projected["blockers"] == []
    original_sheet = deepcopy(projected["sheet"])
    sheet_before = deepcopy(projected["sheet"])
    assert original_sheet["physical_menu_row_ids"] == identity["physical_menu_row_ids"]
    assert original_sheet["physical_menu_row_count"] == len(original_sheet["rows"]) == 56
    projection = original_sheet["hakodate_evidence_projection"]
    deferred = projection["deferred"]
    assert projection["skipped"] == []
    assert projection["metrics"]["deferred_count"] == len(deferred)
    assert len(projection["applied"]) + len(deferred) == 112
    quantity_index = original_sheet["fields"].index("qty.regular_x")
    quantity_cells = {target["sheet_cell"] for target in f.quantity_plan}
    note_cells = {target["sheet_cell"] for target in f.notes_plan}
    assert {item["sheet_cell"] for item in projection["applied"] if item["field"] == "qty.regular_x"} == quantity_cells
    for item in deferred:
        assert item["sheet_cell"] in note_cells
        assert item["field"] == "remarks" and item["value"] == ""
        assert item["assignment_confidence"] == round(by_cell[item["sheet_cell"]]["confidence"], 4)
        assert item["classification"] != "accepted"
        assert item["defer_reason"] == "hakodate_ocr_confidence_below_strict_threshold"
        assert original_sheet["rows"][item["row_index"]][original_sheet["fields"].index("remarks")] == ""
    for item in projection["applied"]:
        assert item["value"] == original_expected[item["sheet_cell"]]
        assert item["assignment_cell"]["assignment_confidence"] == round(by_cell[item["sheet_cell"]]["confidence"], 4)
    assert low_cells <= {item["sheet_cell"] for item in projection["applied"]}
    with db.session_scope() as session:
        state = session.get(OrderWorkflowState, ORDER)
        assert state.revision == before_projection_revision
        assert state.draft_id is None
        assert state.evidence_run_id == evidence["id"]

    # Only placement identity comes from the canonical target map; values come
    # exclusively from the workbook/PDF oracle authored by numeric_fax.
    mapping = projected["target_cell_map"]
    assert len(mapping) == 112
    assert {item["sheet_cell"] for item in mapping} == set(original_expected)
    assert {item["target_row_index"] for item in mapping} == set(range(56))
    assert {item["target_col_index"] for item in mapping} == {quantity_index, original_sheet["fields"].index("remarks")}
    operator_sheet = deepcopy(original_sheet)
    for item in mapping:
        operator_sheet["rows"][item["target_row_index"]][item["target_col_index"]] = original_expected[item["sheet_cell"]]
    expected_quantities = [original_expected[target["sheet_cell"]] for target in f.quantity_plan]
    assert [row[quantity_index] for row in operator_sheet["rows"]] == expected_quantities
    for before, after in zip(original_sheet["rows"], operator_sheet["rows"], strict=True):
        assert before[:quantity_index] == after[:quantity_index]
        assert before[quantity_index + 1:] == after[quantity_index + 1:]
    assert {key: value for key, value in operator_sheet.items() if key != "rows"} == {
        key: value for key, value in original_sheet.items() if key != "rows"
    }
    operator_before = deepcopy(operator_sheet)
    reviewed, error = workflow.confirm_sheet_review(
        order_id=ORDER, sheet=operator_sheet, expected_revision=revision(),
    )
    assert error is None, (error, reviewed)
    anomaly, error = workflow.run_sheet_anomaly_review(
        ORDER, sheet=operator_sheet, use_llm=False, expected_revision=revision(),
    )
    assert error is None, (error, anomaly)
    assert anomaly["pre_save_status"]["sheet_review_confirmed"] is True
    assert anomaly["pre_save_status"]["anomaly_review_confirmed"] is True
    saved, error = workflow.save_sheet(
        order_id=ORDER, sheet=operator_sheet, edited_by="synthetic-original-fax-operator",
        require_pre_save_checks=True, expected_revision=revision(),
    )
    assert error is None, (error, saved)
    assert operator_sheet == operator_before
    assert projected["sheet"] == original_sheet == sheet_before
    assert f.expected == original_expected
    with db.session_scope() as session:
        state = session.get(OrderWorkflowState, ORDER)
        draft = session.get(OrderSheetDraft, state.draft_id)
        assert draft.id == saved["saved_sheet"]["saved_sheet_id"]
        assert draft.template_version_id == state.template_version_id == f.projection_id
        assert draft.base_evidence_run_id == state.evidence_run_id == evidence["id"]
        assert draft.edited_by == "synthetic-original-fax-operator"
        assert draft.draft_sheet_json == operator_before
        assert [row[quantity_index] for row in draft.draft_sheet_json["rows"]] == expected_quantities
        assert session.get(OrderOcrEvidenceRun, evidence["id"]).payload_json == payload
    readback, error = workflow.get_saved_sheet(ORDER)
    assert error is None, (error, readback)
    assert readback["sheet"] == operator_before
    assert readback["source_ocr_result_id"] == evidence["id"]
    assert readback["template_version_id"] == f.projection_id
    assert all(path.read_bytes() == data for path, data in artifact_bytes.items())
    artifacts.validate_payload(payload)
