"""Explicit legacy context reselection on owned SQL; not a historical OCR migration."""
from copy import deepcopy

import pytest

from test_h2_operator_repair_revision import repair_order as repair_order
from test_modernization_workflow_surface_parity import database_snapshot
from src.db import session_scope
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_lineage as lineage
from src.services import order_workflow_v2_service as workflow


@pytest.fixture
def legacy_order(repair_order):
    order_id, revision, configured = repair_order
    old_id = "FTV-legacy-" + order_id
    config = deepcopy(configured["facility"])
    with session_scope() as session:
        session.add(FacilityTemplateVersion(
            id=old_id, facility_id="fixture", version="legacy", status="active",
            source="ocr-evidence-template-lineage", template_id="own",
            config_json=config, columns_json=deepcopy(config["fax_template"]["columns"]),
            template_digest="legacy-opaque-digest", validation_json={"errors": [], "warnings": []},
        ))
        session.flush()
        order = session.get(Order, order_id)
        state = session.get(OrderWorkflowState, order_id)
        order.template_version_id = state.template_version_id = old_id
        session.get(OrderOcrEvidenceRun, state.evidence_run_id).template_version_id = old_id
        session.flush()
        revision = state.revision
    return order_id, revision, old_id


def confirm(order_id, revision, **changes):
    values = dict(order_id=order_id, facility_id="fixture", week_start="2026-10-05",
                  week_end="2026-10-11", template_id="own", expected_revision=revision)
    values.update(changes)
    return workflow.confirm_context(**values)


def test_explicit_reselection_binds_authored_json_without_rewriting_legacy_history(legacy_order):
    order_id, revision, old_id = legacy_order
    with session_scope() as session:
        old = session.get(FacilityTemplateVersion, old_id)
        original = {column.name: deepcopy(getattr(old, column.name)) for column in old.__table__.columns}
    result, error = confirm(order_id, revision)
    assert error is None, (error, result)
    with session_scope() as session:
        order = session.get(Order, order_id)
        state = session.get(OrderWorkflowState, order_id)
        assert order.template_version_id == state.template_version_id != old_id
        projection = lineage.read_projection(session, order.template_version_id)
        receipt = lineage.verify_projection(projection)
        assert receipt["authored_version_id"] == "repair-October"
        assert projection.valid_from.isoformat() == "2026-10-01"
        assert projection.valid_to.isoformat() == "2026-10-31"
        assert projection.validation_json["creation_source_proof"]["effective_date"] == "2026-10-05"
        assert state.state == "context_confirmed" and state.evidence_run_id is None
        assert state.revision == result["revision"] and state.revision > revision
        old = session.get(FacilityTemplateVersion, old_id)
        assert {column.name: getattr(old, column.name) for column in old.__table__.columns} == original


@pytest.mark.parametrize("changes,expected_error", [
    ({"week_start": "2026-11-02", "week_end": "2026-11-08"}, "facility_version_date_unrecorded"),
    ({"template_id": "other"}, "template_version_mismatch"),
    ({"expected_revision": 999999}, "workflow_revision_conflict"),
])
def test_invalid_reselection_leaves_all_existing_rows_unchanged(legacy_order, changes, expected_error):
    order_id, revision, _ = legacy_order
    before = database_snapshot()
    result, error = confirm(order_id, revision, **changes)
    assert error == expected_error, (error, result)
    assert database_snapshot() == before


def test_mid_command_failure_rolls_back_rebinding_and_downstream_deletion(legacy_order, monkeypatch):
    order_id, revision, _ = legacy_order
    before = database_snapshot()

    def fail(*args, **kwargs):
        raise RuntimeError("owned failure after context mutation")

    monkeypatch.setattr(workflow, "_write_workflow_meta", fail)
    with pytest.raises(RuntimeError, match="owned failure after context mutation"):
        confirm(order_id, revision)
    assert database_snapshot() == before
