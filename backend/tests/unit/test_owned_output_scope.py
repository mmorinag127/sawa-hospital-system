"""Authored input scoping refusals; no OCR, receipt or quantity invention."""
from datetime import date, datetime

import pytest

from src.db import session_scope
from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.workers.ingest_mail_adapter import IngestEmailPayload
from tests.helpers.owned_output import owned_outputs  # noqa: F401
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order, selected_bagging_order
from tests.unit.test_disabled_facility_columns import _snapshot


def _payload():
    return IngestEmailPayload(message_id="scope-component", pdf_uri="component:unregistered",
        received_at=datetime(2026, 4, 18), facility_hint="owned-facility",
        week_hint="2026-04@2026-04-18~2026-04-18")


def _line():
    return {"date": "2026-04-18", "daypart": "昼", "menu_name": "Scoped dish",
            "diet_type": "regular", "area_id": "2F", "bag_type": "standard",
            "quantity_original": 9}


@pytest.mark.parametrize("field,value", [
    ("date", "2026-04-19"), ("menu_name", "Foreign dish"), ("daypart", "夕"),
])
def test_owned_output_rejects_mixed_identity_before_preparation(owned_outputs, isolated_formal_schema, field, value):
    before = _snapshot(isolated_formal_schema)
    second = {**_line(), field: value, "area_id": "3F", "quantity_original": 8}
    with pytest.raises(AssertionError, match="one exact date/menu/daypart"):
        owned_outputs.create_order_from_ingest(_payload(), lines=[_line(), second],
            recipe={"qty_per_serving": 40, "unit_type": "g", "category": "副菜"}, physical_slot=0)
    assert _snapshot(isolated_formal_schema) == before


@pytest.mark.parametrize("fault", ["other-facility", "other-diet", "foreign-slot"])
def test_owned_output_cannot_borrow_recipe_or_slot_from_another_scope(owned_outputs, isolated_formal_schema, fault):
    with session_scope() as session:
        session.add(MonthlyMenu(id="2026-04", month_start=date(2026, 4, 1)))
        session.add(MenuMaster(id="scope-master", name="Scoped dish", normalized_name="scopeddish",
            qty_per_serving=40, unit_type="g", category="副菜"))
        session.flush()
        session.add(MonthlyMenuItem(id="scope-recipe", monthly_menu_id="2026-04",
            menu_master_id="scope-master", name="Scoped dish", qty_per_serving=40, unit_type="g",
            category="副菜", daypart="lunch", diet_type="soft" if fault == "other-diet" else "regular",
            facility_override=None if fault == "foreign-slot" else
                "foreign-facility" if fault == "other-facility" else "owned-facility",
            master_resolution_mode="month_only"))
        session.add(MonthlyMenuEntry(id="foreign-slot", monthly_menu_id="2026-04",
            menu_date=date(2026, 4, 18), name="Scoped dish", category="副菜", daypart="lunch",
            diet_type="regular", facility_override="foreign-facility", slot_index=7))
    before = _snapshot(isolated_formal_schema)
    message = "explicit authored physical slot" if fault == "foreign-slot" else "explicit monthly recipe missing"
    with pytest.raises(AssertionError, match=message):
        owned_outputs.create_order_from_ingest(_payload(), lines=[_line()])
    assert _snapshot(isolated_formal_schema) == before


def test_owned_output_does_not_drop_duplicate_quantity_cells(owned_outputs, isolated_formal_schema):
    before = _snapshot(isolated_formal_schema)
    with pytest.raises(AssertionError, match="duplicate authored cell identity"):
        owned_outputs.create_order_from_ingest(_payload(), lines=[_line(), {**_line(), "quantity_original": 8}])
    assert _snapshot(isolated_formal_schema) == before


@pytest.mark.parametrize("fault", ["duplicate-cell", "different-date"])
def test_selected_bagging_preparation_does_not_collapse_declared_cells(
    monkeypatch, tmp_path, isolated_formal_schema, fault,
):
    second = {**_line(), "quantity_original": 8}
    if fault == "different-date":
        second["date"] = "2026-04-19"
    before = _snapshot(isolated_formal_schema)
    before_files = set(tmp_path.rglob("*"))
    message = "duplicate authored cell identity" if fault == "duplicate-cell" else "one exact authored date"
    with pytest.raises(AssertionError, match=message):
        selected_bagging_order(monkeypatch, tmp_path, [_line(), second],
            facility_id="owned-facility", week_start="2026-04-18", week_end="2026-04-19")
    assert _snapshot(isolated_formal_schema) == before
    assert set(tmp_path.rglob("*")) == before_files


@pytest.mark.parametrize("prepare", [selected_bagging_order, accepted_bagging_order], ids=["selected", "accepted"])
@pytest.mark.parametrize("pairs,reason", [
    ([("regular", "3F")], "input quantity has no declared physical column"),
    ([("regular", "2F"), ("regular", "2F")], "duplicate declared quantity pair"),
    ([("regular", "2F"), ("regular", "2f")], "duplicate canonical quantity column"),
])
def test_shared_bagging_entry_preserves_all_input_quantities_or_refuses_before_preparation(
    monkeypatch, tmp_path, isolated_formal_schema, prepare, pairs, reason,
):
    source = _line()
    before = _snapshot(isolated_formal_schema)
    before_files = set(tmp_path.rglob("*"))
    with pytest.raises(AssertionError, match=reason):
        prepare(monkeypatch, tmp_path, [source], facility_id="owned-facility",
            declared_quantity_pairs=pairs)
    assert source["quantity_original"] == 9
    assert _snapshot(isolated_formal_schema) == before
    assert set(tmp_path.rglob("*")) == before_files


def test_prepared_bagging_columns_bind_quantities_by_identity_not_declared_order(monkeypatch, tmp_path, isolated_formal_schema):
    from src.services import order_service

    lines = [_line(), {**_line(), "area_id": "3F", "quantity_original": 8}]
    selected_bagging_order(monkeypatch, tmp_path, lines, facility_id="prepared-column-owner",
        declared_quantity_pairs=[("regular", "2F"), ("regular", "3F")], prepare_only=True)
    master = tmp_path / "authored-facilities.json"
    original_master = master.read_bytes()
    order, authored_sheet = selected_bagging_order(monkeypatch, tmp_path, lines,
        facility_id="prepared-column-owner", prepared_sources=True,
        declared_quantity_pairs=[("regular", "3F"), ("regular", "2F")])
    actual, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and actual is not None
    for sheet in (authored_sheet, actual):
        fields = sheet["fields"]
        row = next(row for row in sheet["rows"] if row[fields.index("menu")] == "Scoped dish")
        assert str(row[fields.index("qty.regular_2f")]) == "9"
        assert str(row[fields.index("qty.regular_3f")]) == "8"
    assert master.read_bytes() == original_master
    assert [line["quantity_original"] for line in lines] == [9, 8]


def test_prepared_bagging_cannot_drop_input_area_absent_from_existing_columns(monkeypatch, tmp_path, isolated_formal_schema):
    selected_bagging_order(monkeypatch, tmp_path, [_line()], facility_id="prepared-column-owner",
        prepare_only=True)
    before, files = _snapshot(isolated_formal_schema), set(tmp_path.rglob("*"))
    with pytest.raises(AssertionError, match="input quantity has no prepared physical column"):
        selected_bagging_order(monkeypatch, tmp_path, [{**_line(), "area_id": "3F", "quantity_original": 8}],
            facility_id="prepared-column-owner", prepared_sources=True)
    assert _snapshot(isolated_formal_schema) == before
    assert set(tmp_path.rglob("*")) == files


@pytest.mark.parametrize("fault", ["missing-facility", "outside-period"])
def test_owned_output_requires_explicit_facility_and_matching_period(owned_outputs, isolated_formal_schema, fault):
    payload = _payload()
    line = _line()
    if fault == "missing-facility":
        payload.facility_hint = None
    else:
        line["date"] = "2026-04-19"
    before = _snapshot(isolated_formal_schema)
    message = "explicit authored facility" if fault == "missing-facility" else "outside explicit period"
    with pytest.raises(AssertionError, match=message):
        owned_outputs.create_order_from_ingest(payload, lines=[line],
            recipe={"qty_per_serving": 40, "unit_type": "g", "category": "副菜"}, physical_slot=0)
    assert _snapshot(isolated_formal_schema) == before


@pytest.mark.parametrize("fault", [
    "facility", "period", "quantity", "columns", "configuration", "hash", "outside-root",
])
def test_prepared_original_cannot_be_rebound_or_relabelled(
    monkeypatch, tmp_path, isolated_formal_schema, fault,
):
    from copy import deepcopy

    original = selected_bagging_order(monkeypatch, tmp_path, [_line()],
        facility_id="owned-facility", week_start="2026-04-18", prepare_only=True,
        menu_qty_per_serving=40, menu_unit_type="g")
    claimed = deepcopy(original)
    if fault == "facility":
        claimed["facility_id"] = "foreign-facility"
    elif fault == "period":
        claimed["week_id"] = "2026-04@2026-04-19~2026-04-19"
    elif fault == "quantity":
        claimed["lines"][0]["quantity_original"] = 8
    elif fault == "columns":
        claimed["quantity_pairs"] = [("soft", "2F")]
    elif fault == "configuration":
        claimed["facility_config"]["facility_name"] = "Foreign specimen"
    elif fault == "hash":
        claimed["content_sha256"] = "0" * 64
    else:
        claimed["path"] = str(tmp_path.parent / "outside-owned-original.pdf")
    before = _snapshot(isolated_formal_schema)
    files = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(AssertionError):
        selected_bagging_order(monkeypatch, tmp_path, [_line()],
            facility_id="owned-facility", week_start="2026-04-18", prepared_sources=True,
            prepared_original=claimed, menu_qty_per_serving=40, menu_unit_type="g")

    assert _snapshot(isolated_formal_schema) == before
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == files
