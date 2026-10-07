"""Owned components, external recognition double; not operator/live OCR proof."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.models.menu import MenuRule
from src.models.order import Order
from src.models.order_output_artifact import OrderBaggingResult
from src.services import output_builder, order_workflow_v2_service as workflow
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


def rule(identity, *, priority=0, kind="menu", quantity=12):
    return {"id": identity, "rule_type": kind, "priority": priority, "menu_pattern": "own",
            "match_type": "exact", "qty_per_serving": quantity, "unit_type": "g"}


@pytest.mark.parametrize("reverse", [False, True])
def test_distinct_equal_best_rules_stop(reverse):
    rules = [rule("A"), rule("Z", quantity=99)]
    if reverse:
        rules.reverse()
    with pytest.raises(ValueError, match="menu_rule_source_ambiguous"):
        output_builder._apply_menu_rules([{"menu_name": "own", "quantity_original": 5}], None, rules=rules)


def test_unique_duplicate_same_source_and_higher_priority_preserve_values():
    line = {"menu_name": "own", "quantity_original": 5, "menu_qty_per_serving": 2, "menu_unit_type": "count"}
    chosen = rule("A", priority=1)
    expected = [{**line, "menu_qty_per_serving": 12, "menu_unit_type": "g"}]
    for rules in ([chosen], [chosen, deepcopy(chosen)], [rule("Z", quantity=99), chosen], [chosen, rule("Z", quantity=99)]):
        assert output_builder._apply_menu_rules([line], None, rules=rules) == expected


def test_existing_additive_type_weights_not_new_lexicographic_precedence():
    line = {"menu_name": "own"}
    assert output_builder._apply_menu_rules([line], None, rules=[rule("global", kind="global", priority=101), rule("menu")])[0]["menu_qty_per_serving"] == 12
    with pytest.raises(ValueError, match="menu_rule_source_ambiguous"):
        output_builder._apply_menu_rules([line], None, rules=[rule("global", kind="global", priority=100), rule("menu", quantity=99)])


@pytest.mark.parametrize("quantity", [0, 2])
def test_global_guard_preserves_existing_amount_even_with_tied_noops(quantity):
    line = {"menu_name": "own", "menu_qty_per_serving": quantity, "menu_unit_type": "count"}
    assert output_builder._apply_menu_rules([line], None, rules=[rule("A", kind="global"), rule("B", kind="global", quantity=99)]) == [line]


def test_global_missing_amount_is_not_a_noop():
    with pytest.raises(ValueError, match="menu_rule_source_ambiguous"):
        output_builder._apply_menu_rules([{"menu_name": "own"}], None, rules=[rule("A", kind="global"), rule("B", kind="global")])


def test_actual_active_duplicate_rules_stop_owned_bagging_and_preserve_receipt(isolated_formal_schema, monkeypatch, tmp_path):
    registered = accepted_bagging_order(monkeypatch, tmp_path, [{"date": "2026-10-07", "daypart": "昼", "menu_name": "own", "diet_type": "regular", "area_id": "2F", "quantity_original": 5, "bag_type": "normal"}])
    with session_scope() as session:
        before = {row.id: (deepcopy(row.payload_json), row.payload_digest) for row in session.execute(select(OrderBaggingResult)).scalars()}
        facility = session.get(Order, registered["id"]).facility_code
        for quantity in (12, 99):
            session.add(MenuRule(id=str(uuid4()), rule_type="facility", facility_id=facility, match_type="exact", menu_pattern="own", priority=1, qty_per_serving=quantity, unit_type="g", active=True))
    state, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None
    result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
    assert result is None
    assert error == "bagging_output_enrichment_failed"
    with session_scope() as session:
        assert {row.id: (row.payload_json, row.payload_digest) for row in session.execute(select(OrderBaggingResult)).scalars()} == before
    old_candidate = next(iter(before.values()))[0]["materialization_candidate"]
    assert output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=old_candidate) == old_candidate["output_lines"]
