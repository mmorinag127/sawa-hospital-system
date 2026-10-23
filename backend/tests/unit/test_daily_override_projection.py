"""Pure projection inputs; these minimal candidates do not prove acceptance."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import event

from src.services import daily_output_override_service as overrides
from src.services import output_builder as output


@contextmanager
def _record_queries(engine):
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_replacing_and_removing_last_override_restores_embedded_original(isolated_formal_schema):
    line = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic menu",
            "diet_type": "regular", "menu_category": "副菜", "quantity_original": 9,
            "menu_qty_per_serving": 40, "menu_unit_type": "g"}
    configured = {"facility": {"facility_id": "projection-only"}}
    candidate = {"output_lines": [line], "configured_input": configured}
    order = {"facility": "projection-only"}
    def write(amount):
        return overrides.upsert_override(output_date=date(2026, 4, 18),
            facility_id=order["facility"], menu_name=line["menu_name"], diet_type="regular",
            daypart="昼", menu_category="副菜", unit_type="g", qty_per_serving=amount)
    original = deepcopy(candidate)
    receipt = write(50)
    embedded = output.build_order_lines_for_outputs(order, accepted_candidate=candidate)
    assert embedded[0]["actual_amount"] == 450
    assert candidate == original
    captured = {"output_lines": deepcopy(embedded), "configured_input": deepcopy(configured)}
    write(60)
    assert output.build_order_lines_for_outputs(order, accepted_candidate=captured)[0]["actual_amount"] == 540
    overrides.delete_override(receipt["id"])
    assert output.build_order_lines_for_outputs(order, accepted_candidate=captured) == [line]
    assert captured["output_lines"] == embedded
    broken = {"output_lines": [{**line, "_daily_output_override_applied": True}],
              "configured_input": deepcopy(configured)}
    with pytest.raises(ValueError, match="original line receipt missing"):
        output.build_order_lines_for_outputs(order, accepted_candidate=broken)


@pytest.mark.parametrize("owned_override", [False, True], ids=["foreign-only", "owned-and-foreign"])
def test_daily_projection_uses_saved_facility_not_the_callers_facility(isolated_formal_schema, owned_override):
    from tests.unit.test_disabled_facility_columns import _snapshot

    line = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic scoped menu",
            "diet_type": "regular", "menu_category": "副菜", "quantity_original": 9,
            "menu_qty_per_serving": 40, "menu_unit_type": "g"}
    candidate = {"output_lines": [line],
        "configured_input": {"facility": {"facility_id": "saved-projection-facility"}}}
    order = {"facility": "foreign-caller-facility"}
    overrides.upsert_override(output_date=date(2026, 4, 18),
        facility_id=order["facility"], menu_name=line["menu_name"], diet_type="regular",
        daypart="昼", menu_category="副菜", unit_type="g", qty_per_serving=90)
    if owned_override:
        overrides.upsert_override(output_date=date(2026, 4, 18),
            facility_id="saved-projection-facility", menu_name=line["menu_name"], diet_type="regular",
            daypart="昼", menu_category="副菜", unit_type="g", qty_per_serving=50)
    original, before = deepcopy(candidate), _snapshot(isolated_formal_schema)

    projected = output.build_order_lines_for_outputs(order, accepted_candidate=candidate)
    raw = output.build_order_lines_for_outputs(order, accepted_candidate=candidate,
        include_daily_overrides=False)

    if owned_override:
        assert projected[0]["actual_amount"] == 450
        assert projected[0]["actual_amount"] != 810
    else:
        assert projected == [line]
    assert raw == [line]
    assert raw is not candidate["output_lines"] and raw[0] is not line
    assert candidate == original
    assert _snapshot(isolated_formal_schema) == before


def test_raw_daily_projection_restores_embedded_forty_without_reading_current_sixty(isolated_formal_schema):
    from tests.unit.test_disabled_facility_columns import _snapshot

    line = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic raw restoration",
            "diet_type": "regular", "menu_category": "副菜", "quantity_original": 9,
            "menu_qty_per_serving": 40, "menu_unit_type": "g"}
    configured = {"facility": {"facility_id": "raw-projection-facility"}}
    order = {"facility": "raw-projection-facility"}
    candidate = {"configured_input": configured, "output_lines": [line]}
    overrides.upsert_override(output_date=date(2026, 4, 18),
        facility_id=order["facility"], menu_name=line["menu_name"], diet_type="regular",
        daypart="昼", menu_category="副菜", unit_type="g", qty_per_serving=50)
    embedded = output.build_order_lines_for_outputs(order, accepted_candidate=candidate)
    assert embedded[0]["actual_amount"] == 450
    captured = {"configured_input": deepcopy(configured), "output_lines": deepcopy(embedded)}
    overrides.upsert_override(output_date=date(2026, 4, 18),
        facility_id=order["facility"], menu_name=line["menu_name"], diet_type="regular",
        daypart="昼", menu_category="副菜", unit_type="g", qty_per_serving=60)
    original, before = deepcopy(captured), _snapshot(isolated_formal_schema)

    with _record_queries(isolated_formal_schema) as queries:
        raw = output.build_order_lines_for_outputs({"facility": "foreign-raw-caller"},
            accepted_candidate=captured, include_daily_overrides=False)

    assert queries == []
    assert raw == [line]
    assert raw[0]["menu_qty_per_serving"] == 40
    assert raw != embedded
    assert captured == original
    assert captured["output_lines"][0]["actual_amount"] == 450
    assert raw is not captured["output_lines"] and raw[0] is not captured["output_lines"][0]
    assert _snapshot(isolated_formal_schema) == before


@pytest.mark.parametrize("receipt", [
    {}, {"configured_input": None}, {"configured_input": {}},
    {"configured_input": {"facility": None}},
    {"configured_input": {"facility": {}}},
    {"configured_input": {"facility": {"facility_id": None}}},
    {"configured_input": {"facility": {"facility_id": ""}}},
    {"configured_input": {"facility": {"facility_id": "  "}}},
    {"configured_input": {"facility": {"facility_id": False}}},
    {"configured_input": {"facility": {"facility_id": 12}}},
])
def test_daily_projection_requires_saved_facility_but_raw_copy_does_not(isolated_formal_schema, receipt):
    from tests.unit.test_disabled_facility_columns import _snapshot

    line = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic receipt guard",
            "diet_type": "regular", "menu_category": "副菜", "quantity_original": 9,
            "menu_qty_per_serving": 40, "menu_unit_type": "g"}
    candidate = {"output_lines": [line], **deepcopy(receipt)}
    original, before = deepcopy(candidate), _snapshot(isolated_formal_schema)
    order = {"facility": "caller-cannot-supply-missing-receipt"}

    with pytest.raises(ValueError, match="^accepted output facility receipt missing$"):
        output.build_order_lines_for_outputs(order, accepted_candidate=candidate)
    copied = output.build_order_lines_for_outputs(order, accepted_candidate=candidate,
        include_daily_overrides=False)
    assert copied == [line]
    assert copied is not candidate["output_lines"] and copied[0] is not line
    copied[0]["quantity_original"] = 99
    assert candidate == original
    assert _snapshot(isolated_formal_schema) == before


@pytest.mark.parametrize("facility", [{}, {"facility_id": None}, {"facility_id": ""},
    {"facility_id": "  "}, {"facility_id": False}, {"facility_id": 12}])
def test_producer_projection_rejects_missing_captured_facility_before_queries(isolated_formal_schema, facility):
    from tests.unit.test_disabled_facility_columns import _snapshot

    candidate = {"configured_input": {"facility": deepcopy(facility), "policy": {}},
        "lines": [{"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic producer guard",
            "diet_type": "regular", "area_id": "2F", "quantity_original": 9}]}
    original, before = deepcopy(candidate), _snapshot(isolated_formal_schema)
    with _record_queries(isolated_formal_schema) as queries:
        with pytest.raises(ValueError, match="^bagging captured facility receipt missing$"):
            output.build_order_lines_for_outputs({"facility": "caller-cannot-authorize-producer"},
                producer_candidate=candidate)
    assert queries == []
    assert candidate == original
    assert _snapshot(isolated_formal_schema) == before


@pytest.mark.parametrize("field", ["menu_qty_per_serving", "menu_unit_type", "actual_unit_type", "actual_amount"])
def test_present_original_field_without_value_is_refused_before_queries(isolated_formal_schema, field):
    original_values = {"menu_qty_per_serving": 40, "menu_unit_type": "g",
        "actual_unit_type": "g", "actual_amount": 360}
    receipt = {key: {"present": True, "value": value} for key, value in original_values.items()}
    receipt[field].pop("value")
    line = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic incomplete restoration",
        "diet_type": "regular", "quantity_original": 9, "menu_qty_per_serving": 50,
        "menu_unit_type": "g", "actual_unit_type": "g", "actual_amount": 450,
        "_daily_output_override_applied": True, "_daily_output_override_original": receipt}
    candidate = {"output_lines": [line]}
    before = deepcopy(candidate)
    with _record_queries(isolated_formal_schema) as queries:
        with pytest.raises(ValueError, match="^daily output override original line receipt invalid$"):
            overrides.restore_original_lines(deepcopy(candidate["output_lines"]))
        with pytest.raises(ValueError, match="^daily output override original line receipt invalid$"):
            overrides.apply_overrides_to_lines(deepcopy(candidate["output_lines"]), None)
        with pytest.raises(ValueError, match="^daily output override original line receipt invalid$"):
            output.build_order_lines_for_outputs({}, accepted_candidate=candidate,
                include_daily_overrides=False)
    assert queries == []
    assert candidate == before


@pytest.mark.parametrize("field", ["menu_qty_per_serving", "menu_unit_type", "actual_unit_type", "actual_amount"])
@pytest.mark.parametrize("present", [True, False], ids=["explicit-null", "originally-absent"])
def test_restore_distinguishes_explicit_null_from_absent_original_fields(isolated_formal_schema, field, present):
    original_values = {"menu_qty_per_serving": 40, "menu_unit_type": "g",
        "actual_unit_type": "g", "actual_amount": 360}
    receipt = {key: {"present": True, "value": value} for key, value in original_values.items()}
    receipt[field] = {"present": True, "value": None} if present else {"present": False}
    expected = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic null restoration",
        "diet_type": "regular", "quantity_original": 9, **original_values}
    if present:
        expected[field] = None
    else:
        expected.pop(field)
    embedded = {"date": "2026-04-18", "daypart": "昼", "menu_name": "Synthetic null restoration",
        "diet_type": "regular", "quantity_original": 9, "menu_qty_per_serving": 50,
        "menu_unit_type": "g", "actual_unit_type": "g", "actual_amount": 450,
        "_daily_output_override_applied": True, "_daily_output_override_original": receipt}
    candidate = {"output_lines": [embedded]}
    before = deepcopy(candidate)

    with _record_queries(isolated_formal_schema) as queries:
        restored = overrides.restore_original_lines(deepcopy(candidate["output_lines"]))
        no_facility = overrides.apply_overrides_to_lines(deepcopy(candidate["output_lines"]), None)
        raw = output.build_order_lines_for_outputs({}, accepted_candidate=candidate,
            include_daily_overrides=False)

    assert queries == []
    assert restored == no_facility == raw == [expected]
    assert (field in raw[0]) is present
    if present:
        assert raw[0][field] is None
    assert not any(key.startswith("_daily_output_override_") for key in raw[0])
    assert candidate == before
