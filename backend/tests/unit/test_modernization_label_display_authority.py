"""Explicit synthetic display settings; component/owned SQL, not OCR or live proof."""
from copy import deepcopy
from datetime import date
import json

import pytest
from sqlalchemy import MetaData, Table, event, inspect, select

from src.db import session_scope
from src.services import config_validator as validator, output_builder as output
from tests.helpers import newnarrowbaggingfixture as helper


@pytest.mark.parametrize("values,suffix", [([], ""), (["staff"], "職員")])
@pytest.mark.parametrize("quantity", [2, 0, None])
def test_explicit_settings_always_travel_without_quantity_change(values, suffix, quantity):
    config = {"daily_label_comparable_diet_types": values, "areas": []}
    bag = {"facility": "FAC00004", "area_id": None, "diet_type": "staff", "quantity": quantity}
    original = deepcopy(bag)
    result = output._apply_daily_label_facility_rules_to_bags([bag], config, "FAC00004")
    assert result[0]["daily_label_comparable_diet_types"] == values
    assert result[0]["daily_label_comparable_diet_types"] is not values
    assert result[0]["quantity"] is quantity
    assert output._daily_label_category_suffix_for_bag(result[0]) == suffix
    assert bag == original


@pytest.mark.parametrize("value", [None, "staff", {}, 0, [None], [0], [""]])
def test_invalid_configuration_stops_in_transport_and_consumer(value):
    config = {"daily_label_comparable_diet_types": value, "areas": []}
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_invalid"):
        output._apply_daily_label_facility_rules_to_bags([{"diet_type": "staff", "area_id": None}], config, "FAC00004")
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_invalid"):
        output._daily_label_category_suffix_for_bag({**config, "diet_type": "staff"})


def test_missing_never_uses_facility_table_or_fax_columns():
    config = {"areas": [], "fax_template": {"columns": [{"role": "quantity", "diet_type": "staff"}]}}
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_required"):
        output._apply_daily_label_facility_rules_to_bags([{"diet_type": "staff", "area_id": None}], config, "FAC00004")
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_required"):
        output._daily_label_category_suffix_for_bag({"facility": "FAC00004", "diet_type": "staff"})


@pytest.mark.parametrize("diet,expected", [("regular_bag", "袋分け"), ("regular", ""), ("standard", "")])
def test_noncomparable_display_does_not_require_comparison_setting(diet, expected):
    assert output._daily_label_category_suffix_for_bag({"diet_type": diet}) == expected


@pytest.mark.parametrize("diet", ["regular", "standard", "regular_bag"])
def test_none_config_independent_display_preserves_missing(diet):
    bag = {"diet_type": diet, "area_id": None, "quantity": 0}
    result = output._apply_daily_label_facility_rules_to_bags([bag], None, "synthetic")
    assert "daily_label_comparable_diet_types" not in result[0]
    assert result[0]["quantity"] == 0
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_required"):
        output._apply_daily_label_facility_rules_to_bags([{**bag, "diet_type": "staff"}], None, "synthetic")


def test_zero_quantity_validation_precedes_missing_comparison_and_preserves_group():
    line = {"date": "2026-10-04", "daypart": "朝", "menu_name": "Synthetic zero menu", "diet_type": "staff", "area_id": None, "quantity_original": 0, "quantity_corrected": None}
    group = {"bags": [], "facility_config": {"areas": []}}
    before = deepcopy(group)
    ctx = {"order_lines": [line, {**line, "menu_name": "Invalid quantity", "quantity_original": "invalid"}]}
    with pytest.raises(ValueError, match="^label_quantity_unresolved$"):
        output._append_zero_quantity_label_bags(group, ctx, date(2026, 10, 4))
    assert group == before


@pytest.mark.parametrize("quantity", [0, 2])
@pytest.mark.parametrize("diet", ["regular", "standard", "regular_bag"])
def test_missing_regular_transport_preserves_unconfigured_and_explicit_empty_overwrites(quantity, diet):
    bag = {"diet_type": diet, "area_id": None, "quantity": quantity}
    result = output._apply_daily_label_facility_rules_to_bags([bag], {"areas": []}, "synthetic")
    assert "daily_label_comparable_diet_types" not in result[0]
    assert result[0]["quantity"] == quantity
    if quantity == 0:
        ctx = {"order_for_outputs": {"id": "synthetic", "facility": "synthetic"}, "facility_config": {"areas": []},
               "order_lines": [{"date": "2026-10-04", "daypart": "朝", "menu_name": "Synthetic zero menu", "diet_type": diet, "area_id": None, "quantity_original": 0, "quantity_corrected": None}]}
        zero_bags = output._append_zero_quantity_label_bags_for_order_context([], ctx, date(2026, 10, 4))
        assert len(zero_bags) == 1
        assert zero_bags[0]["quantity"] == 0
        assert "daily_label_comparable_diet_types" not in zero_bags[0]
    stale = {**bag, "daily_label_comparable_diet_types": ["staff"]}
    result = output._apply_daily_label_facility_rules_to_bags([stale], {"areas": [], "daily_label_comparable_diet_types": []}, "synthetic")
    assert result[0]["daily_label_comparable_diet_types"] == []
    assert stale["daily_label_comparable_diet_types"] == ["staff"]


@pytest.mark.parametrize("zero", [False, True])
def test_mixed_diets_missing_setting_stops_normal_and_zero(zero):
    bags = [{"diet_type": "regular", "area_id": None, "quantity": 0 if zero else 2}]
    before = deepcopy(bags)
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_required"):
        if zero:
            ctx = {"order_for_outputs": {"id": "synthetic", "facility": "synthetic"}, "facility_config": {"areas": []},
                   "order_lines": [{"date": "2026-10-04", "daypart": "朝", "menu_name": "Synthetic zero menu", "diet_type": "staff", "area_id": None, "quantity_original": 0, "quantity_corrected": None}]}
            output._append_zero_quantity_label_bags_for_order_context(bags, ctx, date(2026, 10, 4))
        else:
            output._apply_daily_label_facility_rules_to_bags(bags + [{"diet_type": "staff", "area_id": None}], {"areas": []}, "synthetic")
    assert bags == before


def test_comparison_trims_without_rewriting_authoritative_values():
    values = [" staff ", " future-authored-value "]
    config = {"daily_label_comparable_diet_types": values}
    assert validator.resolve_daily_label_comparable_diet_types(config) == ["staff", "future-authored-value"]
    assert output._daily_label_category_suffix_for_bag({**config, "diet_type": "staff"}) == "職員"
    assert values == [" staff ", " future-authored-value "]


def test_versioned_envelope_does_not_require_duplicate_display_setting():
    master = {"facilities": [{"facility_id": "synthetic", "facility_name": "Synthetic display",
                             "versions": [{"facility": {"daily_label_comparable_diet_types": []}}]}]}
    assert not any("daily_label_comparable_diet_types" in item
                   for item in validator.validate_facility_master(master)["errors"])


def test_regular_bag_and_unknown_explicit_values_are_preserved():
    values = ["future-authored-value", "staff"]
    assert validator.resolve_daily_label_comparable_diet_types({"daily_label_comparable_diet_types": values}) == values
    assert output._daily_label_category_suffix_for_bag({"diet_type": "regular_bag", "daily_label_comparable_diet_types": []}) == "袋分け"


@pytest.mark.parametrize("values", [[], ["staff"]])
def test_zero_supplement_uses_same_frozen_setting(values):
    line = {"date": "2026-10-04", "daypart": "朝", "menu_name": "Synthetic zero menu",
            "diet_type": "staff", "area_id": None, "quantity_original": 0, "quantity_corrected": None}
    ctx = {"order_for_outputs": {"id": "synthetic", "facility": "FAC00004"},
           "facility_config": {"areas": [], "daily_label_comparable_diet_types": values}, "order_lines": [line]}
    bags = output._append_zero_quantity_label_bags_for_order_context([], ctx, date(2026, 10, 4))
    assert len(bags) == 1
    assert bags[0]["quantity"] == 0
    assert bags[0]["daily_label_comparable_diet_types"] == values
    assert line["quantity_corrected"] is None


def test_validator_distinguishes_missing_and_empty_including_authored_versions():
    facility = {"facility_id": "synthetic", "facility_name": "Synthetic display", "daily_label_comparable_diet_types": []}
    assert not validator.validate_facility_config(facility)["errors"]
    missing = {key: value for key, value in facility.items() if key != "daily_label_comparable_diet_types"}
    assert not any("daily_label_comparable_diet_types" in item for item in validator.validate_facility_config(missing)["errors"])
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_required"):
        validator.resolve_daily_label_comparable_diet_types(missing)
    master = {"facilities": [{**facility, "versions": [{"facility": missing}]}]}
    assert not any("daily_label_comparable_diet_types" in item
                   for item in validator.validate_facility_master(master)["errors"])
    master["facilities"][0]["versions"][0]["facility"]["daily_label_comparable_diet_types"] = None
    assert any("versions[0].facility.daily_label_comparable_diet_types_invalid" in item
               for item in validator.validate_facility_master(master)["errors"])


def test_real_sql_receipt_reads_frozen_setting_not_current_json(monkeypatch, tmp_path, isolated_formal_schema):
    real_author = helper.authored_sources

    def explicit_source(*args, **kwargs):
        result = real_author(*args, **kwargs)
        path = tmp_path / "authored-facilities.json"
        master = json.loads(path.read_text())
        facility = master["facilities"][0]
        facility["daily_label_comparable_diet_types"] = ["staff"]
        facility["versions"][0]["facility"]["daily_label_comparable_diet_types"] = ["staff"]
        path.write_text(json.dumps(master))
        return result

    monkeypatch.setattr(helper, "authored_sources", explicit_source)
    order = helper.accepted_bagging_order(monkeypatch, tmp_path, [{
        "line_id": "synthetic-display-line", "date": "2026-10-04", "daypart": "朝",
        "menu_name": "Synthetic display menu", "diet_type": "regular", "area_id": "2F",
        "bag_type": "standard", "quantity_original": 2,
    }])
    path = tmp_path / "authored-facilities.json"
    current = json.loads(path.read_text())
    current["facilities"][0]["daily_label_comparable_diet_types"] = []
    current["facilities"][0]["versions"][0]["facility"]["daily_label_comparable_diet_types"] = []
    path.write_text(json.dumps(current))

    def snapshot():
        with session_scope() as session:
            return {name: [dict(row) for row in session.execute(select(Table(name, MetaData(), autoload_with=session.bind))).mappings()]
                    for name in inspect(session.bind).get_table_names()}

    before = snapshot()
    writes = []

    def observe(_conn, _cursor, statement, _params, context, _many):
        if context.isinsert or context.isupdate or context.isdelete or statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "REPLACE")):
            writes.append(statement)

    event.listen(isolated_formal_schema, "before_cursor_execute", observe)
    try:
        bags = output.build_bag_rows_for_outputs(order)
        assert bags and sum(bag["quantity"] for bag in bags) == 2
        assert all(bag["daily_label_comparable_diet_types"] == ["staff"] for bag in bags)
        assert all(output._daily_label_category_suffix_for_bag(bag) == "" for bag in bags)
        assert snapshot() == before
        assert writes == []
    finally:
        event.remove(isolated_formal_schema, "before_cursor_execute", observe)
