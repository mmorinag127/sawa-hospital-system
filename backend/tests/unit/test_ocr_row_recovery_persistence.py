"""Synthetic source receipts; not live PDF/recognition parity proof."""
from copy import deepcopy

import pytest

from test_h2_operator_repair_revision import repair_order
from src.db import session_scope
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_service
from src.services import order_workflow_v2_service as workflow


@pytest.mark.parametrize("invalid", [False, True, "source"])
def test_authored_day_boundaries_persist_without_changing_selected_evidence(repair_order, monkeypatch, invalid):
    order_id, revision, configured = repair_order
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        selected = state.evidence_run_id
        receipt, _ = workflow._axis_source_receipt(session, session.get(Order, order_id), state)
    days = [{"date": f"2026-10-{day:02d}", "expected_band_count": 8} for day in range(4, 11)]
    space = {"mode": "template_canvas", "width": 100, "height": 600}
    monkeypatch.setattr(workflow, "get_row_axis_review", lambda *a, **kw: ({
        "calendar_days": days, "header_band_count": 3, "coordinate_space": space,
        "source_receipt": deepcopy(receipt)}, None))
    ys = [i * 10 + 0.123456 for i in range(60)]
    boundaries = [0, 8, 16, 24, 32, 40, 48, 56]
    if invalid is True:
        boundaries[3] = 25
    if invalid == "source":
        receipt["geometry_source_digest"] = "changed-source"
    _, error = workflow.save_row_axis_review_decision(
        order_id, corrected_ys=ys, coordinate_space=space, source_receipt=receipt,
        body_day_boundary_indexes=boundaries, day_labels=[day["date"] for day in days],
        expected_revision=revision,
    )
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        assert state.evidence_run_id == selected
        meta = workflow._workflow_meta(state)
        if invalid:
            if invalid is True:
                assert error.startswith("row_axis_day_band_count_mismatch:")
            else:
                assert error is not None
            assert "row_axis_override" not in meta
            assert state.revision == revision
        else:
            assert error is None
            saved = meta["row_axis_override"]
            assert saved["corrected_ys"] == ys
            assert saved["body_day_boundary_indexes"] == boundaries
            assert saved["day_labels"] == [day["date"] for day in days]
            consumed = order_service._normalize_operator_row_axis_override(
                saved, selected_pdf_receipt=receipt["selected_pdf_receipt"], configured_input=configured,
                order_input_identity=receipt["order_input_identity"], session=session,
            )
            assert consumed == saved


def test_review_returns_image_on_day_validation_failure(repair_order, monkeypatch):
    from PIL import Image
    import numpy as np
    from src.services import hakodate_cell_ocr_batch_service as batch
    from src.services import hakodate_assignment_service as assignment
    from src.services import master_order_form_template_service as master
    order_id, _, _ = repair_order
    with session_scope() as session:
        receipt, captured = workflow._axis_source_receipt(session, session.get(Order, order_id), session.get(OrderWorkflowState, order_id))
    monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", lambda **kw: {
        "step2_png": "synthetic", "template_axes_x": [0, 100], "template_axes_y": [0, 600],
        "original_physical_draft": {}, "row_axis_override": None})
    monkeypatch.setattr(batch.cv2, "imread", lambda path: np.zeros((600, 100, 3), dtype=np.uint8))
    monkeypatch.setattr(assignment, "_worksheet_for_manifest_structure_template", lambda **kw: object())
    monkeypatch.setattr(batch, "_fax_template_for_manifest_item", lambda *a: {})
    monkeypatch.setattr(master, "validated_body_row_plan", lambda ws: [{"date": "2026-10-07"}])
    monkeypatch.setattr(master, "validated_body_week_value", lambda ws: captured["order"]["week_code"])
    monkeypatch.setattr(batch, "_align_axes", lambda **kw: ([0, 100], [0, 50, 100],
        {"row_intersection_y_match": {"corrected_ys": [0, 50, 100]}}, Image.new("RGB", (100, 600))))
    monkeypatch.setattr(batch, "_draw_row_intersections_overlay", lambda **kw: kw["image"])
    def fail(*a):
        raise ValueError("fax_day_boundaries_disagree_with_monthly_menu")
    monkeypatch.setattr(batch, "_validate_menu_day_boundaries", fail)
    result = order_service.build_hakodate_row_axis_review(order_id, source_receipt=receipt, start_input=captured)
    assert result["status"] == "needs_correction"
    assert result["error_detail"] == "fax_day_boundaries_disagree_with_monthly_menu"
    assert result["image_png_base64"].startswith("iVBOR")
    assert result["y_positions"] == [0, 50, 100]


def test_api_forwards_authored_calendar_unchanged(monkeypatch):
    from src.api import orders
    values = {"corrected_ys": [0, 1, 2], "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 600},
              "source_receipt": {"original": True}, "body_day_boundary_indexes": [0, 8, 16, 24, 32, 40, 48, 56],
              "day_labels": [f"2026-10-{i}" for i in range(25, 32)]}
    received = []
    monkeypatch.setattr(workflow, "save_row_axis_review_decision", lambda oid, **kw: (received.append(kw) or {"order_id": oid}, None))
    orders.save_order_workflow_v2_row_axis_review("synthetic", orders.WorkflowV2RowAxisReviewBody(**values), expected_revision=7)
    assert received == [{**values, "expected_revision": 7}]
