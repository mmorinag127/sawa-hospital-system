"""Canonical projection boundary components, not accepted/OCR flow proof."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services import facility_template_version_service as templates
from src.services import order_workflow_v2_service as workflow
from tests.unit.test_canonical_facility_lineage import Session, selected
from tests.unit.test_h2_canonical_workflow_template import canonical_authored_source


@pytest.mark.parametrize("boundary", ["context", "existing", "lineage"])
@pytest.mark.parametrize("fault, expected", [
    ("missing-order-identity", "template_version_required"),
    ("foreign-order-identity", "template_version_mismatch"),
    ("missing-period", "week_range_required"),
    ("month-only", "week_range_required"),
    ("period-mismatch", "template_version_mismatch"),
    ("outside-authored-period", "template_version_mismatch"),
    ("valid", "selected_ocr_required"),
])
def test_explicit_order_projection_and_period_boundary_is_nonwriting(monkeypatch, boundary, fault, expected):
    selection, _ = selected(monkeypatch)
    session = Session()
    _, projection = templates.materialize_facility_version_lineage(session, selection)
    order = SimpleNamespace(
        id="own-order", facility_code="F", received_at=None,
        template_version_id=projection.id, week_code="2026-01@2026-01-12~2026-01-18",
    )
    state = SimpleNamespace(template_version_id=projection.id, state="sheet_saved", evidence_run_id=None)
    meta = {"facility_id": "F", "week_start": "2026-01-12", "week_end": "2026-01-18"}
    if fault == "missing-order-identity":
        order.template_version_id = None
    elif fault == "foreign-order-identity":
        order.template_version_id = "foreign-projection"
    elif fault == "missing-period":
        order.week_code = None
    elif fault == "month-only":
        order.week_code = "2026-01"
    elif fault == "period-mismatch":
        meta["week_end"] = "2026-01-19"
    elif fault == "outside-authored-period":
        order.week_code = "2026-02@2026-02-01~2026-02-01"
        meta.update(week_start="2026-02-01", week_end="2026-02-01")
    before = deepcopy(session.rows)
    before_order, before_state, before_meta = deepcopy(vars(order)), deepcopy(vars(state)), deepcopy(meta)
    session.statements.clear()
    if boundary == "lineage":
        assert workflow._workflow_lineage_error(session, order=order, workflow=state, meta_override=meta) == expected
    else:
        method = (workflow._require_workflow_template_version_from_context if boundary == "context"
                  else workflow._require_existing_workflow_template_version)
        assert method(session, order=order, workflow=state, meta=meta) == (
            (projection.id, None) if fault == "valid" else (None, expected)
        )
    assert session.rows == before
    assert all(statement.is_select for statement in session.statements)
    assert not session.new and not session.dirty and not session.deleted
    assert vars(order) == before_order and vars(state) == before_state and meta == before_meta
