"""Dated physical identity components, not customer/live OCR proof."""
from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.models.order_output_artifact import OrderBaggingResult
from src.services import output_builder, order_workflow_v2_service as workflow
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


ENTRY = {"menu_date": "2026-10-07", "daypart": "昼食", "slot_index": 0,
         "name": "own", "diet_type": "regular", "category": "主菜"}


@pytest.mark.parametrize("value", ["2026-10-07", date(2026, 10, 7)])
@pytest.mark.parametrize("daypart", ["昼", "昼食"])
def test_aligned_date_preserves_exact_entry(value, daypart):
    assert output_builder._resolve_monthly_entry_by_source_row({"date": value, "daypart": daypart, "source_row_index": 0, "diet_type": "regular"}, [[ENTRY]]) is ENTRY


@pytest.mark.parametrize("changes", [{"date": None}, {"date": "bad"}, {"date": "2026-10-08"}, {"source_row_index": 99}, {"source_row_index": -1}, {"source_row_index": "0"}, {"diet_type": "diabetes"}, {"daypart": None}, {"daypart": ""}, {"daypart": "unknown"}, {"daypart": "朝"}, {"daypart": "夕食"}])
def test_invalid_identity_stops(changes):
    line = {"date": "2026-10-07", "daypart": "昼", "source_row_index": 0, "diet_type": "regular", **changes}
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        output_builder._resolve_monthly_entry_by_source_row(line, [[ENTRY]])


def test_no_source_index_keeps_existing_name_lookup_boundary():
    assert output_builder._resolve_monthly_entry_by_source_row({"date": None}, [[ENTRY]]) is None


@pytest.mark.parametrize("fault", ["date", "daypart"])
def test_owned_bagging_wrong_source_date_stops_without_new_receipt(isolated_formal_schema, monkeypatch, tmp_path, fault):
    registered = accepted_bagging_order(monkeypatch, tmp_path, [{"date": "2026-10-07", "daypart": "昼", "menu_name": "own", "diet_type": "regular", "area_id": "2F", "quantity_original": 5, "bag_type": "normal"}])
    with session_scope() as session:
        before = {row.id: (deepcopy(row.payload_json), row.payload_digest) for row in session.execute(select(OrderBaggingResult)).scalars()}
    # Source-read fault double after valid producer input, not a recaptured customer source.
    actual = output_builder._collect_current_menu_entries_for_week
    def changed(week, facility):
        entries = deepcopy(actual(week, facility))
        for entry in entries:
            entry["menu_date" if fault == "date" else "daypart"] = "2026-10-08" if fault == "date" else "夕食"
        return entries
    monkeypatch.setattr(output_builder, "_collect_current_menu_entries_for_week", changed)
    state, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None
    result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
    assert result is None
    assert error == "bagging_output_enrichment_failed"
    with session_scope() as session:
        assert {row.id: (row.payload_json, row.payload_digest) for row in session.execute(select(OrderBaggingResult)).scalars()} == before
    old = next(iter(before.values()))[0]["materialization_candidate"]
    assert output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=old) == old["output_lines"]
