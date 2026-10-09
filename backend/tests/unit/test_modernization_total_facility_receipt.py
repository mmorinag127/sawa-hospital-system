"""Single-order owned SQLite receipt proof, not native OCR, UI or live proof.

The unchanged fixture manually seeds evidence; real bagging and final confirmation
produce the accepted output. No multi-order or two-version isolation is claimed.
"""

from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from test_modernization_real_bagging_confirmation import (
    actual_bagging,
    owned_bagging,
    revision,
)
from test_modernization_workflow_surface_parity import database_snapshot, surface
from src.models.order import Order, OrderLine
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_workflow_v2_service as workflow
from src.services import total_service


@pytest.fixture
def confirmed_receipt(owned_bagging):
    fixture = owned_bagging
    bagged = actual_bagging(fixture)
    result, error = workflow.final_confirm(
        fixture.order_id,
        confirmed_by="synthetic-unit-operator",
        expected_revision=revision(fixture),
    )
    assert error is None, error
    assert result is not None
    with fixture.session_scope() as session:
        assert session.get(Order, fixture.order_id).status == "確定"
        lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == fixture.order_id).order_by(OrderLine.menu_name),
        ).scalars().all()
        assert [(line.menu_name, line.quantity_original, line.quantity_corrected) for line in lines] == [
            ("選択献立0", 7, None), ("選択献立3", 0, None),
        ]
    candidate = bagged["bagging_result"]["materialization_candidate"]
    assert candidate["configured_input"]["facility"]["facility_name"] == "保存済み施設"
    assert [(line["source_row_index"], line["quantity_original"])
            for line in candidate["lines"]] == [(0, 7), (3, 0)]
    return fixture, candidate


@pytest.mark.parametrize("diverge_root", [False, True], ids=["original-root", "renamed-root"])
def test_totals_name_is_bound_to_accepted_facility_without_read_writes(confirmed_receipt, diverge_root):
    fixture, candidate = confirmed_receipt
    original_json = fixture.master_path.read_bytes()
    try:
        master = json.loads(original_json)
        selected_versions = deepcopy(master["facilities"][0]["versions"])
        if diverge_root:
            master["facilities"][0]["facility_name"] = "Different unversioned root name"
            fixture.master_path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
        assert json.loads(fixture.master_path.read_bytes())["facilities"][0]["versions"] == selected_versions
        before = database_snapshot()
        totals = total_service.build_totals(None, None, include_order_refs=True)
        expected = [("選択献立0", 7)]
        if not candidate["configured_input"]["policy"].get("quantity_rules", {}).get("zero_as_empty", True):
            expected.append(("選択献立3", 0))
        assert sorted((row["menu_name"], row["quantity"]) for row in totals) == expected
        for row in totals:
            assert len(row["order_refs"]) == 1
            ref = row["order_refs"][0]
            assert ref["order_id"] == fixture.order_id
            assert ref["facility_id"] == "F"
            assert ref["facility_name"] == "保存済み施設"
            assert ref["quantity"] == row["quantity"]
        assert database_snapshot() == before
        assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]
    finally:
        fixture.master_path.write_bytes(original_json)
    assert fixture.master_path.read_bytes() == original_json


@pytest.mark.parametrize("fault,expected_error", [
    ("missing-saved-source", "accepted saved sheet missing"),
    ("missing-output-receipt", "workflow-v2 output bundle artifact missing or invalid"),
])
def test_totals_missing_accepted_source_or_receipt_stops_without_read_writes(
    confirmed_receipt, fault, expected_error,
):
    fixture, _candidate = confirmed_receipt
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        if fault == "missing-saved-source":
            state.draft_id = None
        else:
            meta = deepcopy(state.secondary_actions_json)
            meta["workflow_v2"]["output_bundle_id"] = "MISSING-OUTPUT-RECEIPT"
            state.secondary_actions_json = meta
    before = database_snapshot()
    with pytest.raises(ValueError) as raised:
        total_service.build_totals(None, None, include_order_refs=True)
    assert str(raised.value) == expected_error
    assert database_snapshot() == before
    assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]
