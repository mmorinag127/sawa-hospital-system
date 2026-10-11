"""Geometry components with external storage/render/DB adapters; not OCR proof."""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from src.services import workflow_attempt_guard_service as guard
from src.services import order_workflow_v2_service as workflow
from tests.unit.test_h2_quad_geometry_roundtrip import geometry_roundtrip, geometry_source_events


@pytest.mark.parametrize("fault,error", [
    ("missing", "original_template_projection_missing"),
    ("foreign-facility", "original_template_configured_source_mismatch"),
])
def test_public_geometry_get_save_never_repairs_original_identity(geometry_roundtrip, fault, error):
    state, order, pdf, _, _, quad = geometry_roundtrip
    review, initial_error = workflow.get_quad_review(order.id, document_id=pdf["document_id"])
    assert initial_error is None
    receipt = deepcopy(review["source_receipt"])
    before = deepcopy(workflow._workflow_meta(state))
    if fault == "missing":
        order.template_version_id = None
    else:
        order.facility_code = "foreign-facility"
    identity = (order.template_version_id, order.facility_code, state.template_version_id)
    path = Path(os.environ["FACILITY_MASTER_PATH"])
    source_before = path.read_bytes()
    with workflow.session_scope() as session:
        rows_before = deepcopy(session.rows)
    assert workflow.get_quad_review(order.id, document_id=pdf["document_id"]) == (None, error)
    assert workflow.save_quad_review_decision(
        order.id, decision="manual_override", quad_px=quad,
        source_receipt=receipt, expected_revision=receipt["workflow_revision"],
    ) == (None, error)
    with workflow.session_scope() as session:
        assert session.rows == rows_before
    assert path.read_bytes() == source_before
    assert (order.template_version_id, order.facility_code, state.template_version_id) == identity
    assert state.revision == 7
    assert workflow._workflow_meta(state) == before


def test_real_pdf_receipt_rejects_foreign_document_without_mutation(geometry_roundtrip):
    state, order, pdf, _, _, _ = geometry_roundtrip
    with workflow.session_scope() as session:
        receipt = guard.capture_selected_pdf_receipt(session, order)
        assert receipt == pdf
        foreign = {**receipt, "document_id": "foreign-document"}
        assert guard.verify_selected_pdf_receipt(session, foreign) is False
    assert state.revision == 7
    assert workflow._workflow_meta(state) == {}


def test_storage_adapter_checks_bytes_instead_of_accepting_receipt(geometry_roundtrip):
    state, order, pdf, _, _, _ = geometry_roundtrip
    with workflow.session_scope() as session:
        assert guard.verify_selected_pdf_receipt(session, pdf) is True
        # Both DB and storage receipt must agree; changing just the receipt stops first.
        assert guard.verify_selected_pdf_receipt(session, {**pdf, "sha256": "0" * 64}) is False
    assert state.revision == 7
