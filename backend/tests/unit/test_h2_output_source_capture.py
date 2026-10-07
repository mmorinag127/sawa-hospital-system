"""Owned producer components; external recognition double, not operator OCR proof."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.models.menu import MonthlyMenuItem, MenuMaster, MenuRule
from src.models.order import Order
from src.models.order_output_artifact import OrderBaggingResult
from src.services import output_builder, order_workflow_v2_service as workflow, menu_rule_service
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


def owned(monkeypatch, tmp_path):
    return accepted_bagging_order(monkeypatch, tmp_path, [{"date": "2026-10-07", "daypart": "昼", "menu_name": "own", "diet_type": "regular", "area_id": "2F", "quantity_original": 7, "bag_type": "normal"}])


def receipts(order_id):
    with session_scope() as session:
        return {row.id: (deepcopy(row.payload_json), row.payload_digest) for row in session.execute(select(OrderBaggingResult).where(OrderBaggingResult.order_id == order_id)).scalars()}


@pytest.mark.parametrize("source", ["item", "master", "rule"])
def test_actual_new_capture_sees_updates_and_old_receipt_stays_frozen(isolated_formal_schema, monkeypatch, tmp_path, source):
    monkeypatch.setattr(output_builder.time, "time", lambda: 1800000001.0)
    registered = owned(monkeypatch, tmp_path)
    before = receipts(registered["id"])
    old_payload = next(iter(before.values()))[0]
    old_candidate = old_payload["materialization_candidate"]
    with session_scope() as session:
        facility = session.get(Order, registered["id"]).facility_code
        if source == "item":
            item = session.execute(select(MonthlyMenuItem).where(MonthlyMenuItem.facility_override == facility)).scalar_one()
            item.qty_per_serving = 2
        elif source == "master":
            master = session.execute(select(MenuMaster).where(MenuMaster.name == "own")).scalar_one()
            master.temp_type = "冷"
        else:
            session.add(MenuRule(id=str(uuid4()), rule_type="facility", facility_id=facility, match_type="exact", menu_pattern="own", qty_per_serving=3, unit_type="g", active=True))
    state, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None
    result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
    assert error is None, error
    candidate = result["bagging_result"]["materialization_candidate"]
    snapshot = candidate["output_source_snapshot"]
    output_builder._validate_output_sources(snapshot)
    assert snapshot != old_candidate["output_source_snapshot"]
    if source == "item":
        assert any(item["qty_per_serving"] == 2 for item in snapshot["items"])
        assert candidate["output_lines"][0]["menu_qty_per_serving"] == 2
    elif source == "master":
        assert any(read["values"]["own"].get("temp_type") == "cold" for read in snapshot["defaults"])
        assert candidate["output_lines"][0]["menu_temp_type"] == "冷"
    else:
        assert any(rule["qty_per_serving"] == 3 for rule in snapshot["rules"])
        assert candidate["output_lines"][0]["menu_qty_per_serving"] == 3
    after = receipts(registered["id"])
    for identity, value in before.items():
        assert after[identity] == value
    assert output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=old_candidate) == old_candidate["output_lines"]
    assert [line["quantity_original"] for line in candidate["output_lines"]] == [7]


@pytest.mark.parametrize("fault", ["read", "changed"])
def test_source_fault_stops_actual_bagging_before_artifact_save(isolated_formal_schema, monkeypatch, tmp_path, fault):
    registered = owned(monkeypatch, tmp_path)
    before = receipts(registered["id"])
    if fault == "read":
        def fail():
            raise ValueError("menu_rules_source_unresolved")
        monkeypatch.setattr(menu_rule_service, "list_active_rules", fail)
    else:
        original = output_builder._validate_output_sources
        def changed(snapshot):
            # Read-fault double: simulate a changed reread without a competing SQLite writer.
            actual = menu_rule_service.list_active_rules
            monkeypatch.setattr(menu_rule_service, "list_active_rules", lambda: [*actual(), {"id": "changed-read"}])
            return original(snapshot)
        monkeypatch.setattr(output_builder, "_validate_output_sources", changed)
    state, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None
    result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
    assert result is None
    assert error in {"bagging_source_changed", "bagging_output_enrichment_failed"}
    assert receipts(registered["id"]) == before


def test_repeated_overlapping_default_reads_refuse_changed_source(isolated_formal_schema, monkeypatch):
    """Read-fault double only; actual adoption coverage is above."""
    sources = {"defaults": []}
    monkeypatch.setattr(output_builder.menu_service, "resolve_menu_defaults_in_session", lambda session, names, facility: ({name: {"qty_per_serving": 1} for name in names}, [("own-master", 1)]))
    output_builder._read_captured_menu_defaults(["own"], "F", sources)
    monkeypatch.setattr(output_builder.menu_service, "resolve_menu_defaults_in_session", lambda session, names, facility: ({name: {"qty_per_serving": 2} for name in names}, [("own-master", 2)]))
    with pytest.raises(ValueError, match="bagging_source_changed"):
        output_builder._read_captured_menu_defaults(["own", "post-rule"], "F", sources)


def test_rule_database_read_failure_is_not_empty_rules(monkeypatch):
    def fail():
        raise RuntimeError("authored read fault")
    monkeypatch.setattr(menu_rule_service, "session_scope", fail)
    with pytest.raises(ValueError, match="menu_rules_source_unresolved"):
        menu_rule_service.list_active_rules()
