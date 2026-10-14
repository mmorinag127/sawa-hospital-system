from copy import deepcopy

import pytest

from src.services import order_service
from src.services import hakodate_ocr_evidence_service as evidence
from src.services.ocr_recognition_recovery_service import operator_values
from test_modernization_repair_validation_boundary import owned_boundary
from test_h2_operator_repair_revision import repair_order, _attach_provider_artifact_packet


@pytest.mark.parametrize("value", ["", "0", "6", "9", "12"])
def test_operator_evidence_retains_quantity_and_actor(owned_boundary, value):
    _, _, _, source, payload = owned_boundary
    records = payload["hakodate_ocr_evidence"]["records"]
    for record in records:
        metadata = record["engine_metadata"]
        original = metadata.pop("recognizer_output")
        metadata.update(source="operator", edited_by="test-operator", operator_output={
            **original, "recognition_source": "operator", "operator_value": value,
        })
        record.update(raw_text=value, normalized_value=value, source_scope="operator_cell_review", engine="operator")
    _attach_provider_artifact_packet(payload)
    order_service._validate_hakodate_owned_artifacts(payload, source)
    assert all(evidence.validate_ocr_quantity_source(record) == value for record in records)
    damaged = deepcopy(payload)
    damaged["hakodate_ocr_evidence"]["records"][0]["engine_metadata"]["operator_output"]["operator_value"] = "99"
    damaged["hakodate_ocr_evidence_records"] = deepcopy(damaged["hakodate_ocr_evidence"]["records"])
    with pytest.raises(ValueError, match="hakodate_evidence_source_invalid|recognition_operator_record_mismatch"):
        order_service._validate_hakodate_owned_artifacts(damaged, source)


@pytest.mark.parametrize("values", [{}, {"E11": None}, {"E11": 0}, {"E11": "-1"}, {"E11": "1.5"}, {"E11": "x"}, {"E11": "1", "E12": "2"}])
def test_operator_requires_explicit_value_for_every_cell(values):
    with pytest.raises(ValueError, match="recognition_values"):
        operator_values(values, ["E11"])


def test_order_service_resumes_operator_checkpoint_without_preprocessing(owned_boundary, monkeypatch):
    import numpy as np
    from src.services import hakodate_ocr_checkpoint_service as checkpoints
    from src.services import ocr_recognition_recovery_service as recovery
    from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime

    order_id, _, start, source, _ = owned_boundary
    captured = {**start, "order": start["input_versions"]["order"],
                "workflow": {"secondary_actions_json": start["workflow_meta"]}}
    seed = {"resume_item": {"quad_px": [[0, 0], [99, 0], [99, 99], [0, 99]], "quad_source": "operator"}}
    item = recovery.current_item(seed, captured, {"selected_pdf_receipt": start["selected_pdf_receipt"]}, order_id)
    regions = [{**deepcopy(region), "bbox": [1, 1 + index * 4, 8, 4 + index * 4]}
               for index, region in enumerate(source["expected_target_semantics"])]
    height = len(regions) * 4 + 10
    checkpoint = checkpoints.persist_recognizer_checkpoint(
        item=item, raw_rectified_bgr=np.full((height, 20, 3), 255, dtype=np.uint8),
        regions=regions, expected_sheet_cells=source["expected_sheet_cells"],
        metadata={"axis_evidence": {"header_intersection_x_match": {}},
                  "rectified_quad_points": [[0, 0], [19, 0], [19, height - 1], [0, height - 1]],
                  "snap_debug": {"applied": True}},
    )
    values = {cell: "12" for cell in source["expected_sheet_cells"]}
    start["workflow_meta"].setdefault("workflow_v2", {})["recognition_override"] = {
        "checkpoint": checkpoint, "values": values, "source": "operator",
        "edited_by": "test-operator", "created_at": "2026-10-15T00:00:00Z",
    }
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("preprocessing repeated"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("operator values recognized again"))
    result = order_service._hakodate_canonical_payload_from_manifest_item(
        order_id=order_id, facility_id=captured["order"]["facility_code"], item={},
        synchronous_write_input=start,
    )
    records = result["hakodate_ocr_evidence_records"]
    assert len(records) == len(values)
    assert all(record["raw_text"] == "12" and record["engine"] == "operator" for record in records)
    assert all(record["engine_metadata"]["edited_by"] == "test-operator" for record in records)
    assert result["hakodate_preprocessing"]["quality_gate"]["ok"]


@pytest.mark.parametrize("stale", [False, True])
def test_failed_job_review_save_preserves_selected_results(owned_boundary, stale):
    import numpy as np
    from src.db import session_scope
    from src.models.order import Order
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.ocr_job import OcrJob
    from src.services import order_workflow_v2_service as workflow
    from src.services import hakodate_ocr_checkpoint_service as checkpoints
    from src.services import ocr_recognition_recovery_service as recovery

    order_id, revision, _, source, _ = owned_boundary
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        before = (state.evidence_run_id, state.draft_id, state.confirmed_snapshot_id)
        meta = workflow._workflow_meta(state)
        meta["ocr_job_id"] = "recognition-test-job"
        workflow._write_workflow_meta(state, meta)
        session.flush()
        revision = state.revision
        receipt, captured = workflow._axis_source_receipt(session, session.get(Order, order_id), state)
        seed = {"resume_item": {"quad_px": [[0, 0], [9, 0], [9, 9], [0, 9]], "quad_source": "operator"}}
        item = recovery.current_item(seed, captured, receipt, order_id)
        checkpoint = checkpoints.persist_recognizer_checkpoint(
            item=item, raw_rectified_bgr=np.full((10, 10, 3), 255, dtype=np.uint8),
            regions=[{**deepcopy(region), "bbox": [1, 1, 8, 8]} for region in source["expected_target_semantics"]],
            expected_sheet_cells=source["expected_sheet_cells"],
        )
        session.add(OcrJob(id="recognition-test-job", order_id=order_id, status="failed",
                           input_reference=receipt["selected_pdf_receipt"]["storage_uri"],
                           metrics={"recognition_checkpoint": checkpoint}, error_message="recognizer failed"))
    review, error = recovery.get_review(order_id)
    assert error is None
    assert all(cell["image_png_base64"].startswith("iVBOR") for cell in review["cells"])
    response, error = recovery.save_review(
        order_id, source_receipt=review["source_receipt"],
        values={cell["cell"]: "9" for cell in review["cells"]}, edited_by="test-operator",
        expected_revision=revision - 1 if stale else revision,
    )
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        assert (state.evidence_run_id, state.draft_id, state.confirmed_snapshot_id) == before
        saved = workflow._workflow_meta(state).get("recognition_override")
        if stale:
            assert error is not None and saved is None
            assert state.revision == revision
        else:
            assert error is None
            assert saved["edited_by"] == "test-operator"
            assert set(saved["values"].values()) == {"9"}
            assert state.revision == revision + 1
