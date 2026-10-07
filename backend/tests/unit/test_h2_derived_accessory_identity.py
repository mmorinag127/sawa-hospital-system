"""Synthetic components; recognition double, not customer/operator OCR proof."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.models.order_output_artifact import OrderBaggingResult
from src.models.order import OrderMenuSnapshot
from src.models.menu import MenuMaster
from src.services import output_builder, order_workflow_v2_service as workflow
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


def test_repeated_enrichment_validates_parent_without_adopting_parent_identity():
    entry = {"menu_date": "2026-06-12", "daypart": "昼食", "slot_index": 0,
             "name": "親　添)ソース", "diet_type": "soft", "category": "主菜"}
    lines = output_builder._apply_garnish_lines([{
        "date": "2026-06-12", "daypart": "昼", "menu_name": entry["name"],
        "diet_type": "soft", "menu_category": "主菜", "quantity_original": 5,
        "source_row_index": 0, "source_col_index": 3,
    }])
    items = [{"name": "親", "category": "主菜", "qty_per_serving": 2, "unit_type": "count"},
             {"name": "ソース", "category": "主菜", "qty_per_serving": 12, "unit_type": "g"}]
    lines = output_builder._apply_menu_snapshot(lines, {
        "ソース": {"category": "主菜", "qty_per_serving": 99, "unit_type": "count"}})
    assert lines[1]["menu_category"] == "添え"
    assert "menu_qty_per_serving" not in lines[1]
    for _ in range(2):
        lines = output_builder._apply_menu_entry_overrides(lines, [entry])
        lines = output_builder._apply_menu_overrides(lines, items)
    assert [(line["menu_name"], line["menu_category"], line["quantity_original"]) for line in lines] == [
        ("親", "主菜", 5), ("ソース", "添え", 5)]
    assert lines[1]["parent_menu_name"] == "親"
    assert lines[1]["menu_qty_per_serving"] == 12
    assert lines[1]["menu_unit_type"] == "g"
    invalid = deepcopy(lines[1])
    invalid["parent_menu_name"] = "別の親"
    with pytest.raises(ValueError, match="derived_accessory_parent_unresolved"):
        output_builder._apply_menu_entry_overrides([invalid], [entry])
    invalid = deepcopy(lines[1])
    invalid["menu_category"] = "主菜"
    with pytest.raises(ValueError, match="derived_accessory_identity_unresolved"):
        output_builder._apply_menu_snapshot([invalid], {"ソース": {"category": "主菜"}})
    invalid = deepcopy(lines[1])
    invalid.pop("parent_menu_name")
    with pytest.raises(ValueError, match="derived_accessory_identity_unresolved"):
        output_builder._apply_menu_entry_overrides([invalid], [entry])


@pytest.mark.parametrize("kind", ["plain", "garnish", "condiment"])
def test_owned_receipt_components_keep_distinct_names_and_source_accounting(isolated_formal_schema, monkeypatch, tmp_path, kind):
    name = "親　添)ソース" if kind == "garnish" else "親"
    registered = accepted_bagging_order(monkeypatch, tmp_path, [{
        "date": "2026-10-07", "daypart": "昼", "menu_name": name,
        "diet_type": "regular", "area_id": "2F", "quantity_original": 5,
        "bag_type": "condiment" if kind == "condiment" else "normal",
    }], menu_qty_per_serving=2, menu_unit_type="count",
       accessory_masters={"ソース": {"qty_per_serving": 12, "unit_type": "g"}})
    with session_scope() as session:
        receipt = session.execute(select(OrderBaggingResult).where(OrderBaggingResult.order_id == registered["id"])).scalar_one()
        candidate = deepcopy(receipt.payload_json["materialization_candidate"])
    lines = candidate["output_lines"]
    parent = next(line for line in lines if line["menu_name"] == "親")
    assert parent["quantity_original"] == 5
    assert parent["menu_qty_per_serving"] == 2
    bags = output_builder.build_bag_rows_for_outputs({"id": registered["id"]}, accepted_candidate=candidate)
    assert len([line for line in lines if line["menu_name"] == "親"]) == 1
    assert all(bag["quantity"] == 5 for bag in bags)
    assert all(len(bag["_source_refs"]) == 1 for bag in bags)
    if kind != "plain":
        child = next(line for line in lines if line["menu_name"] == "ソース")
        assert child["menu_category"] == "添え"
        assert child["parent_menu_name"] == "親"
        assert child["menu_qty_per_serving"] == 12
        assert child["menu_unit_type"] == "g"
    assert output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=candidate) == lines


def test_missing_accessory_amount_stops_owned_bagging_before_receipt(isolated_formal_schema, monkeypatch, tmp_path):
    with pytest.raises(AssertionError, match="bagging_output_enrichment_failed"):
        accepted_bagging_order(monkeypatch, tmp_path, [{
            "date": "2026-10-07", "daypart": "昼", "menu_name": "親　添)未設定ソース",
            "diet_type": "regular", "area_id": "2F", "quantity_original": 5,
            "bag_type": "normal",
        }], menu_qty_per_serving=2, menu_unit_type="count")
    with session_scope() as session:
        assert session.execute(select(OrderBaggingResult)).scalars().all() == []


def test_new_capture_uses_current_child_master_not_old_order_snapshot(isolated_formal_schema, monkeypatch, tmp_path):
    registered = accepted_bagging_order(monkeypatch, tmp_path, [{
        "date": "2026-10-07", "daypart": "昼", "menu_name": "親　添)ソース",
        "diet_type": "regular", "area_id": "2F", "quantity_original": 5, "bag_type": "normal",
    }], menu_qty_per_serving=2, menu_unit_type="count",
       accessory_masters={"ソース": {"qty_per_serving": 12, "unit_type": "g"}})
    with session_scope() as session:
        receipt = session.execute(select(OrderBaggingResult).where(OrderBaggingResult.order_id == registered["id"])).scalar_one()
        old_id, old_digest, old_payload = receipt.id, receipt.payload_digest, deepcopy(receipt.payload_json)
        snapshot = session.execute(select(OrderMenuSnapshot).where(OrderMenuSnapshot.order_id == registered["id"])).scalar_one_or_none()
        old_source = {"menu_items": {"ソース": {"qty_per_serving": 99, "unit_type": "count", "category": "主菜"}}}
        if snapshot is None:
            session.add(OrderMenuSnapshot(id="owned-old-snapshot", order_id=registered["id"], snapshot_json=old_source))
        else:
            snapshot.snapshot_json = old_source
        master = session.execute(select(MenuMaster).where(MenuMaster.name == "ソース")).scalar_one()
        master.qty_per_serving = 14
    state, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
    assert error is None, error
    candidate = result["bagging_result"]["materialization_candidate"]
    child = next(line for line in candidate["output_lines"] if line["menu_name"] == "ソース")
    assert (child["menu_category"], child["menu_qty_per_serving"], child["menu_unit_type"], child["quantity_original"]) == ("添え", 14, "g", 5)
    assert any(read["values"].get("ソース", {}).get("qty_per_serving") == 14 for read in candidate["output_source_snapshot"]["defaults"])
    bags = output_builder.build_bag_rows_for_outputs({"id": registered["id"]}, accepted_candidate=candidate)
    labels, _, _ = output_builder._build_label_rows(bags, {}, None)
    assert next(row for row in labels if row["商品名１"] == "親")["内容量"] == "10個"
    assert next(row for row in labels if row["商品名１"] == "ソース")["内容量"] == "70g"
    with session_scope() as session:
        receipt = session.get(OrderBaggingResult, old_id)
        assert (receipt.payload_json, receipt.payload_digest) == (old_payload, old_digest)
    old_lines = output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=old_payload["materialization_candidate"])
    assert next(line for line in old_lines if line["menu_name"] == "ソース")["menu_qty_per_serving"] == 12


@pytest.mark.parametrize("quantity,unit", [(None, "g"), (12, None), (True, "g"), (float("nan"), "g")])
def test_accessory_amount_has_no_implicit_quantity_or_unit(quantity, unit):
    with pytest.raises(ValueError, match="derived_accessory_amount_unresolved"):
        output_builder._apply_garnish_defaults([{"parent_menu_name": "親", "menu_name": "ソース",
            "menu_category": "添え", "menu_qty_per_serving": quantity, "menu_unit_type": unit}])


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_best_monthly_sources_stop_without_id_tiebreaker(reverse):
    line = {"menu_name": "ソース", "menu_category": "添え", "diet_type": "soft", "daypart": "昼", "parent_menu_name": "親"}
    items = [{"id": "A", "name": "ソース", "daypart": "昼食", "category": "添え", "diet_type": "soft", "qty_per_serving": 12, "unit_type": "g"},
             {"id": "Z", "name": "ソース", "daypart": "昼食", "category": "添え", "diet_type": "soft", "qty_per_serving": 99, "unit_type": "count"}]
    if reverse:
        items.reverse()
    with pytest.raises(ValueError, match="monthly_menu_item_source_ambiguous"):
        output_builder._apply_menu_overrides([line], items)


def test_monthly_source_unique_and_differing_ranks_keep_existing_precedence():
    line = {"menu_name": "ソース", "menu_category": "添え", "diet_type": "soft", "daypart": "昼", "parent_menu_name": "親", "quantity_original": 5}
    exact = {"id": "A", "name": "ソース", "daypart": "昼食", "category": "添え", "diet_type": "soft", "qty_per_serving": 12, "unit_type": "g"}
    lower = {**exact, "id": "Z", "diet_type": "regular", "qty_per_serving": 99}
    unique = output_builder._apply_menu_overrides([line], [exact])
    assert output_builder._apply_menu_overrides([line], [lower, exact]) == unique
    assert output_builder._apply_menu_overrides([line], [exact, deepcopy(exact)]) == unique
    assert (unique[0]["menu_name"], unique[0]["quantity_original"], unique[0]["menu_qty_per_serving"], unique[0]["menu_unit_type"]) == ("ソース", 5, 12, "g")


def test_ambiguous_monthly_read_stops_actual_owned_bagging_without_new_receipt(isolated_formal_schema, monkeypatch, tmp_path):
    """Ambiguous-source read double; receipt setup and adoption guard are actual."""
    registered = accepted_bagging_order(monkeypatch, tmp_path, [{"date": "2026-10-07", "daypart": "昼", "menu_name": "親　添)ソース", "diet_type": "regular", "area_id": "2F", "quantity_original": 5, "bag_type": "normal"}], menu_qty_per_serving=2, menu_unit_type="count", accessory_masters={"ソース": {"qty_per_serving": 12, "unit_type": "g"}})
    with session_scope() as session:
        before = {row.id: (deepcopy(row.payload_json), row.payload_digest) for row in session.execute(select(OrderBaggingResult)).scalars()}
    actual = output_builder._collect_current_menu_items_for_week
    def ambiguous(week, facility):
        return [*actual(week, facility),
                {"id": "source-A", "name": "ソース", "daypart": "昼食", "category": "添え", "diet_type": "regular", "qty_per_serving": 12, "unit_type": "g"},
                {"id": "source-Z", "name": "ソース", "daypart": "昼食", "category": "添え", "diet_type": "regular", "qty_per_serving": 99, "unit_type": "count"}]
    monkeypatch.setattr(output_builder, "_collect_current_menu_items_for_week", ambiguous)
    state, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None
    result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
    assert result is None
    assert error == "bagging_output_enrichment_failed"
    with session_scope() as session:
        assert {row.id: (row.payload_json, row.payload_digest) for row in session.execute(select(OrderBaggingResult)).scalars()} == before
