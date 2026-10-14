from src.db import session_scope
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_workflow_v2_service as workflow
from test_ocr_operator_evidence_boundary import (
    owned_boundary, repair_order, test_failed_job_review_save_preserves_selected_results as save_failed_job,
)


def test_new_authored_cells_clear_warning_without_erasing_previous_audit(owned_boundary):
    order_id = owned_boundary[0]
    audit = [{"recognition_override": {"values": {"E11": "12"}, "edited_by": "previous"},
              "reason": "recognition_checkpoint_geometry_changed", "geometry_kind": "quad",
              "invalidated_at": "2026-10-15T00:00:00Z"}]
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        meta = workflow._workflow_meta(state)
        meta["invalidated_recognition_overrides"] = audit
        meta["recognition_override_invalidation"] = {"reason": "recognition_checkpoint_geometry_changed"}
        workflow._write_workflow_meta(state, meta)
    save_failed_job(owned_boundary, stale=False)
    with session_scope() as session:
        meta = workflow._workflow_meta(session.get(OrderWorkflowState, order_id))
        assert "recognition_override_invalidation" not in meta
        assert meta["invalidated_recognition_overrides"] == audit
        assert meta["recognition_override"]["edited_by"] == "test-operator"


def test_corrupt_history_blocks_recognition_save_and_keeps_warning(owned_boundary):
    from copy import deepcopy
    from src.services import ocr_recognition_recovery_service as recovery

    save_failed_job(owned_boundary, stale=False)
    order_id = owned_boundary[0]
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        meta = workflow._workflow_meta(state)
        meta["invalidated_recognition_overrides"] = [None]
        meta["recognition_override_invalidation"] = {"reason": "recognition_checkpoint_geometry_changed"}
        workflow._write_workflow_meta(state, meta)
        before = deepcopy(meta)
    review, error = recovery.get_review(order_id)
    assert error is None
    revision = review["source_receipt"]["workflow_revision"]
    result, error = recovery.save_review(order_id, source_receipt=review["source_receipt"],
        values={cell["cell"]: "8" for cell in review["cells"]}, edited_by="new-operator",
        expected_revision=revision)
    assert result is None and error == "recognition_override_archive_invalid"
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        assert state.revision == revision
        assert workflow._workflow_meta(state) == before
