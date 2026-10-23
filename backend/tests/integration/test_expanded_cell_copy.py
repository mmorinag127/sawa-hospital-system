"""Merged layout declaration is not permission to invent adjacent quantities."""
from copy import deepcopy

import pytest

from src.db import session_scope
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_form_service, order_service, order_workflow_v2_service as workflow, output_builder
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


@pytest.mark.parametrize("rows", [
    [["04/05", "朝", "Menu A", "44", "", ""],
     ["04/05", "朝", "Menu B", "", "", ""],
     ["04/05", "昼", "Menu C", "", "", ""]],
    [["04/05", "朝", "Menu A", "48", ""],
     ["04/05", "朝", "Menu B", "", ""],
     ["04/05", "朝", "Menu C", "", ""],
     ["04/05", "朝", "Menu D", "", ""]],
], ids=["two-row-cluster-keeps-blank", "four-row-cluster-keeps-blank"])
def test_removed_position_copy_cannot_fill_adjacent_operator_quantities(rows):
    before = deepcopy(rows)
    with pytest.raises(AttributeError):
        order_service._apply_expanded_cell_same_daypart_copy(rows=rows, fields=[], quantity_index={})
    assert rows == before


def test_manual_facility_flag_enables_expanded_cell_copy_for_unmerged_template():
    config = {"expanded_cell_same_daypart_copy_enabled": True,
              "fax_template": {"columns": [{"role": "quantity", "name": "qty.regular_x"}]}}
    before = deepcopy(config)
    assert order_form_service.facility_template_has_vertical_merged_quantity_cells(config)
    assert config == before


@pytest.mark.parametrize("policy", [
    {"body_merge_policy": {"mode": "daypart", "columns": ["qty.regular_x"]}},
    {"columns": [{"name": "qty.regular_x", "role": "quantity", "body_merge": "daypart"}]},
], ids=["declared-policy", "declared-column"])
def test_expanded_cell_copy_uses_explicit_facility_template_merged_quantity_cells(policy):
    config = {"fax_template": policy}
    before = deepcopy(config)
    assert order_form_service.facility_template_has_vertical_merged_quantity_cells(config)
    assert config == before


def test_expanded_cell_copy_does_not_enable_for_unmerged_template_without_manual_flag():
    config = {"fax_template": {"columns": [{"role": "quantity", "name": "qty.regular_x"}]}}
    before = deepcopy(config)
    assert not order_form_service.facility_template_has_vertical_merged_quantity_cells(config)
    assert config == before


@pytest.mark.parametrize("quantity", [44, 48], ids=["candidate-44", "output-48"])
def test_owned_saved_quantity_never_copies_into_empty_menu_rows(monkeypatch, tmp_path, isolated_formal_schema, quantity):
    lines = [
        {"date": "2026-03-21", "daypart": daypart, "menu_name": name, "diet_type": "regular",
         "area_id": "X", "bag_type": "standard", "quantity_original": amount}
        for name, daypart, amount in [("Menu A", "朝", quantity), ("Menu B", "朝", None), ("Menu C", "昼", None)]
    ]
    registered = accepted_bagging_order(monkeypatch, tmp_path, lines,
        menu_qty_per_serving=1, menu_unit_type="g", confirm=True)
    order_id = registered["id"]
    with session_scope() as session:
        order = session.get(Order, order_id)
        state = session.get(OrderWorkflowState, order_id)
        saved = session.get(OrderSheetDraft, state.draft_id)
        assert [row[3] for row in saved.draft_sheet_json["rows"]] == [quantity, None, None]
        candidate = workflow._build_materialization_candidate_for_saved_sheet(session=session, order=order, saved_sheet=saved)
        assert candidate["error"] is None
        assert [(line["menu_name"], line["quantity_original"]) for line in candidate["lines"]] == [("Menu A", quantity)]
    actual = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(order_id))
    assert [(line["menu_name"], line["quantity_original"]) for line in actual] == [("Menu A", quantity)]
