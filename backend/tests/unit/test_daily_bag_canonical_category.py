"""Owned synthetic saved/normal bag projections; not live/auth/OCR proof."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date

import pytest

from test_canonical_facility_lineage import authored
from test_initial_saved_source_service import saved as saved, all_rows
from test_modernization_real_bagging_confirmation import owned_bagging as owned_bagging
from test_modernization_workflow_surface_parity import surface as surface
from test_modernization_real_daily_delivery_html import confirm, preservation, assert_preserved
from src.models.order_output_artifact import OrderBaggingResult
from src.services import canonical_facility_store as store, facility_master_service
from src.services import initial_saved_import_service as imports, initial_saved_source_service as source
from src.services import order_service as orders, output_builder as output


DAY = date(2026, 9, 13)
CATEGORY = "Synthetic saved category"


def bag(**changes):
    return {"date": DAY.isoformat(), "daypart": "昼", "menu_name": "Synthetic category menu",
        "menu_category": CATEGORY, "diet_type": "regular", "area_id": "X", "bag_type": "standard",
        "quantity": 2, "source_refs": [{"source_row_index": 0, "source_col_index": 3,
            "source_field": "qty.regular_x", "quantity": 2, "value_source": "operator",
            "was_user_edited": True}], **changes}


@pytest.mark.parametrize("metadata", [{}, {"menu_unit_type": "g"},
    {"menu_unit_type": "g", "menu_qty_per_serving": None},
    {"menu_unit_type": "g", "actual_amount": "unknown"}])
def test_missing_amount_metadata_does_not_remove_canonical_category(metadata):
    row = bag()
    line = {key: row[key] for key in ("date", "daypart", "menu_name", "diet_type", "area_id", "bag_type")}
    line.update(quantity_original=2, menu_category="Conflicting line category", **metadata)
    before = deepcopy((row, line))
    stats = orders._build_daily_bag_amount_stats([line])
    assert orders._resolve_daily_bag_menu_category(row) == CATEGORY
    assert orders._resolve_daily_bag_amount_totals(row, stats) is None
    assert orders._resolve_daily_bag_basis_label(row, stats) is None
    assert stats == {"condiment_totals": {}, "per_serving_by_group": {}}
    assert (row, line) == before


def test_weight_stats_cannot_override_category_and_weight_calculation_is_unchanged():
    row = bag()
    line = {**row, "quantity_original": 2, "menu_category": "Conflicting line category",
        "menu_unit_type": "g", "menu_qty_per_serving": 100}
    before = deepcopy((row, line))
    stats = orders._build_daily_bag_amount_stats([line])
    original_stats = deepcopy(stats)
    assert "menu_category_by_group" not in stats
    assert orders._resolve_daily_bag_menu_category(row) == CATEGORY
    assert orders._resolve_daily_bag_amount_totals(row, stats) == {"g": 200.0}
    assert orders._resolve_daily_bag_basis_label(row, stats) is not None
    assert stats == original_stats and (row, line) == before


@pytest.mark.parametrize("category", [None, "", "   "])
def test_absent_canonical_category_remains_unknown_despite_enriched_lines(category):
    row = bag(menu_category=category)
    line = {**row, "quantity_original": 2, "menu_category": "Do not borrow",
        "menu_unit_type": "g", "menu_qty_per_serving": 100}
    before = deepcopy((row, line))
    stats = orders._build_daily_bag_amount_stats([line])
    assert orders._resolve_daily_bag_menu_category(row) is None
    assert orders._resolve_daily_bag_amount_totals(row, stats) == {"g": 200.0}
    assert (row, line) == before


@pytest.mark.parametrize("category", [CATEGORY, None])
def test_condiment_display_classification_and_totals_are_preserved(category):
    row = bag(bag_type="condiment", menu_category=category)
    line = {**row, "quantity_original": 2, "menu_unit_type": "g", "menu_qty_per_serving": 5}
    before = deepcopy((row, line))
    stats = orders._build_daily_bag_amount_stats([line])
    assert orders._resolve_daily_bag_menu_category(row) == "付属品"
    assert orders._resolve_daily_bag_amount_totals(row, stats) == {"g": 10.0}
    assert orders._resolve_daily_bag_basis_label(row, stats) is None
    assert (row, line) == before


@pytest.mark.parametrize("count,category", [(11, CATEGORY), (27, CATEGORY), (32, CATEGORY),
    (42, CATEGORY), (11, CATEGORY), (2, None)],
    ids=["saved-1", "saved-2", "saved-3", "saved-4", "saved-5", "missing-category"])
def test_saved_bare_lines_and_serialized_category_rows_keep_unknown_weight(saved, monkeypatch, count, category):
    # Match the reported shape/counts with synthetic values, not copied live quantities.
    record = saved.get(OrderBaggingResult, "B")
    payload = deepcopy(record.payload_json)
    payload["materialization_candidate"]["lines"] = [{
        "date": DAY.isoformat(), "daypart": "昼", "menu_name": "Synthetic category menu",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard",
        "quantity_original": 2 * count, "quantity_corrected": None}]
    payload["bag_rows"] = [bag(menu_category=category) for _ in range(count)]
    record.payload_json = payload
    saved.commit()
    snapshot = source.capture_saved_source(saved, "O")
    assert "output_lines" not in snapshot["bagging"]["payload_json"]["materialization_candidate"]
    master = {"facilities": [{"facility_id": "F", "versions": [
        store.initial_facility_version(authored()["facility"], "a" * 64)]}]}
    prepared = imports.prepare_import_master(master, [snapshot], {"recorded_policy": True})
    monkeypatch.setattr(facility_master_service, "get_snapshot",
        lambda: store.MasterSnapshot(prepared, "synthetic", "owned"))
    @contextmanager
    def scope():
        yield saved
    monkeypatch.setattr(output, "session_scope", scope)
    monkeypatch.setattr(orders, "list_orders_by_line_date", lambda *a, **k: [{"id": "O"}])
    monkeypatch.setattr(orders, "get_order_for_output_read",
        lambda identity: {"id": identity, "facility": "F", "template_version_id": "T"})
    before = deepcopy((all_rows(saved), snapshot, prepared, payload))
    result = orders.get_daily_bag_summary(DAY)
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["menu_category"] == (category if category is not None else "-")
    diet = group["diet_groups"][0]
    assert diet["total_quantity"] == 2 * count and diet["total_amounts"] == {}
    assert diet["calculation_basis_label"] is None
    bags = diet["bag_type_groups"][0]
    assert bags["bag_count"] == count and bags["total_quantity"] == 2 * count
    assert bags["total_amounts"] == {}
    assert bags["breakdowns"][0]["amount_label"] == "計算不可"
    assert bags["breakdowns"][0]["count"] == count
    assert len(bags["breakdowns"][0]["order_refs"]) == count
    assert (all_rows(saved), snapshot, prepared, payload) == before


def test_normal_confirmed_summary_matches_accepted_serialized_bag_category(owned_bagging, surface):
    fixture = owned_bagging
    confirm(fixture)
    before = preservation(fixture)
    order = orders.get_order_for_output_read(fixture.order_id)
    accepted = output._accepted_output_candidate(order)
    rows = output.build_bag_payload_for_outputs(order, accepted_candidate=accepted)
    assert accepted.get("initial_saved_import") is None
    assert rows and all(row["menu_category"] for row in rows)
    originals = deepcopy((order, accepted, rows))
    day = date.fromisoformat(rows[0]["date"])
    response = surface[1].get("/orders/daily-bags", params={"date": day.isoformat()})
    assert response.status_code == 200, response.text
    groups = response.json()["groups"]
    assert {group["menu_category"] for group in groups} == {row["menu_category"] for row in rows}
    assert groups[0]["diet_groups"][0]["total_quantity"] == 7
    assert groups[0]["diet_groups"][0]["total_amounts"] == {"g": 700}
    assert (order, accepted, rows) == originals
    assert_preserved(fixture, before)
