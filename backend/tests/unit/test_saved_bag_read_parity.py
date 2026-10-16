"""Owned synthetic saved imports; not proof of live output parity."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy import event

from test_initial_saved_source_service import saved, all_rows
from test_canonical_facility_lineage import authored
from test_modernization_real_bagging_confirmation import owned_bagging, surface, actual_bagging
from src.models.order import Order
from src.models.order_output_artifact import OrderBaggingResult
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_store as store, facility_master_service
from src.services import initial_saved_import_service as imports, initial_saved_source_service as source
from src.services import output_builder as output


@pytest.fixture
def bag_import(saved, monkeypatch):
    rows = [
        {"date": "2026-09-13", "daypart": "昼", "menu_name": "Saved menu",
         "menu_category": "Saved category", "diet_type": diet, "area_id": "X",
         "bag_type": "saved", "quantity": qty,
         "source_refs": [{"line_id": diet, "quantity": qty, "unknown": None}]}
        for diet, qty in [("staff", 5), ("mixer", 3), ("regular", 68)]
    ]
    bagging = saved.get(OrderBaggingResult, "B")
    payload = deepcopy(bagging.payload_json)
    payload["bag_rows"] = rows
    bagging.payload_json = payload
    saved.commit()
    snapshot = source.capture_saved_source(saved, "O")
    facility = authored()["facility"]
    facility.pop("daily_label_comparable_diet_types", None)
    master = {"facilities": [{"facility_id": "F", "versions": [store.initial_facility_version(facility, "a" * 64)]}]}
    prepared = imports.prepare_import_master(master, [snapshot], {"recorded_policy": True})
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: store.MasterSnapshot(prepared, "synthetic", "owned"))
    @contextmanager
    def scope():
        yield saved
    monkeypatch.setattr(output, "session_scope", scope)
    rebuild = Mock(side_effect=AssertionError("saved bags must not be regenerated"))
    monkeypatch.setattr(output, "_build_final_label_bags", rebuild)
    return saved, prepared, deepcopy(rows), rebuild


def test_saved_wire_and_native_rows_preserve_values_and_source(bag_import):
    session, _, original, rebuild = bag_import
    before = all_rows(session)
    order = {"id": "O", "facility": "F", "template_version_id": "T"}
    statements = []
    def read_only(_conn, _cursor, statement, *_args):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT")
    event.listen(session.bind, "before_cursor_execute", read_only)
    try:
        candidate = output._accepted_output_candidate(order)
        frozen = deepcopy(candidate)
        assert output.build_bag_payload_for_outputs(
            order, accepted_candidate=candidate, configured_input=candidate["configured_input"],
            order_lines=candidate["output_lines"], facility_config=candidate["configured_input"]["facility"],
        ) == original
        assert output.build_bag_payload_for_outputs(
            order, configured_input=candidate["configured_input"], order_lines=candidate["output_lines"],
        ) == original
        for supplied in (False, True):
            kwargs = {"accepted_candidate": candidate} if supplied else {}
            actual = output.build_bag_payload_for_outputs(order, **kwargs)
            assert actual == original
            assert all("area_name" not in row for row in actual)
            native = output.build_bag_rows_for_outputs(order, **kwargs)
            assert [row["date"] for row in native] == [date(2026, 9, 13)] * 3
            assert [{**row, "date": row["date"].isoformat()} for row in native] == original
            actual[0]["source_refs"][0]["quantity"] = -1
            native[0]["quantity"] = -1
            assert candidate == frozen
        assert all_rows(session) == before
    finally:
        event.remove(session.bind, "before_cursor_execute", read_only)
    assert statements
    rebuild.assert_not_called()


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("mismatch", ["lines", "facility", "configuration"])
def test_saved_read_rejects_mismatch_even_with_configured_input(bag_import, configured, supplied, mismatch):
    _, _, _, rebuild = bag_import
    order = {"id": "O", "facility": "F", "template_version_id": "T"}
    candidate = output._accepted_output_candidate(order)
    kwargs = {"accepted_candidate": candidate, "order_lines": deepcopy(candidate["output_lines"])}
    if not supplied:
        del kwargs["accepted_candidate"]
    if configured or mismatch == "configuration":
        kwargs["configured_input"] = deepcopy(candidate["configured_input"])
    if mismatch == "lines":
        kwargs["order_lines"] = []
    elif mismatch == "facility":
        kwargs["facility_config"] = {"changed": True}
    else:
        kwargs["configured_input"]["policy"] = {"changed": True}
    with pytest.raises(ValueError, match="accepted .* mismatch"):
        output.build_bag_payload_for_outputs(order, **kwargs)
    rebuild.assert_not_called()


@pytest.mark.parametrize("change", ["source", "retired", "corrupt"])
@pytest.mark.parametrize("configured", [False, True])
def test_import_validation_stops_saved_read(bag_import, change, configured):
    session, master, _, rebuild = bag_import
    order = {"id": "O", "template_version_id": "T"}
    candidate = output._accepted_output_candidate(order)
    kwargs = {"configured_input": candidate["configured_input"], "order_lines": candidate["output_lines"]} if configured else {}
    if change == "source":
        session.get(Order, "O").document_uri = "changed.pdf"
        session.flush()
    elif change == "retired":
        workflow = session.get(OrderWorkflowState, "O")
        workflow.secondary_actions_json = {**workflow.secondary_actions_json, imports.RETIRED: {}}
        session.flush()
    else:
        master[imports.IMPORTS]["O"] = {"invalid": True}
    with pytest.raises(ValueError, match="initial_saved_"):
        output.build_bag_payload_for_outputs(order, **kwargs)
    rebuild.assert_not_called()


def test_context_uses_same_saved_rows(bag_import):
    _, _, original, rebuild = bag_import
    order = {"id": "O", "facility": "F", "template_version_id": "T"}
    candidate = output._accepted_output_candidate(order)
    context = output._prepare_output_context("O", original_adoption={"order": order, "candidate": candidate}, require_invoice=False)
    assert [{**row, "date": row["date"].isoformat()} for row in context["bags"]] == original
    rebuild.assert_not_called()


def test_nonimport_comparable_diet_guard_remains_strict():
    with pytest.raises(ValueError, match="daily_label_comparable_diet_types_required"):
        output._apply_daily_label_facility_rules_to_bags(
            [{"diet_type": "mixer", "area_id": "X", "quantity": 3}], {}, "F")


@pytest.mark.parametrize("rows", [None, {}, [None], *[
    [{"date": value}] for value in ["invalid", "9/13/2026", "20260913", "2026-9-13", "2026-02-30", "2026-09-13T00:00:00", date(2026, 9, 13)]
]])
def test_malformed_saved_rows_never_regenerate(bag_import, rows):
    _, _, _, rebuild = bag_import
    order = {"id": "O", "facility": "F", "template_version_id": "T"}
    candidate = output._accepted_output_candidate(order)
    candidate["initial_saved_bag_rows"] = rows
    with pytest.raises(ValueError, match="initial_saved_source_output_invalid"):
        output.build_bag_payload_for_outputs(order, accepted_candidate=candidate)
    rebuild.assert_not_called()


def test_explicit_new_workflow_command_still_materializes(owned_bagging, monkeypatch):
    producer = Mock(wraps=output.build_bag_payload_for_materialization)
    rebuild = Mock(wraps=output._build_final_label_bags)
    monkeypatch.setattr(output, "build_bag_payload_for_materialization", producer)
    monkeypatch.setattr(output, "_build_final_label_bags", rebuild)
    result = actual_bagging(owned_bagging)
    assert result is not None
    producer.assert_called_once()
    rebuild.assert_called_once()
