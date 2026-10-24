"""Real selected-save/current publication boundaries; recognizer component fixture."""
from copy import deepcopy
from datetime import datetime, timedelta
import os
from types import SimpleNamespace

import pytest

from src.db import session_scope
from src.models.menu import MonthlyMenuEntry
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import apply_gate_service, draft_sheet_service, order_current_state_service, order_service, workflow_state_service
from src.services import workflow_attempt_guard_service as guard
from tests.helpers.authored_sheet_case import authored_sheet_case, publish_current, save_draft


@pytest.fixture
def publication_case(request, tmp_path, monkeypatch):
    if "C2_MENU_READONLY_PG_URI" not in os.environ:
        yield request.getfixturevalue("authored_sheet_case")
        return
    from tests.integration.test_menu_readonly_boundary import readonly_database
    database = readonly_database.__wrapped__(SimpleNamespace(param="postgresql"), tmp_path, monkeypatch)
    engine = next(database)
    case = authored_sheet_case.__wrapped__(engine, tmp_path, monkeypatch)
    try:
        yield next(case)
    finally:
        try:
            next(case, None)
        finally:
            next(database, None)


@pytest.mark.parametrize("quantity", [8, 0, None, "", "empty"])
def test_selected_saved_identity_survives_source_label_and_empty_rows(publication_case, quantity):
    case = publication_case(quantity=5)
    sheet = deepcopy(case.selected_sheet)
    sheet.update(source="review_blocked", header=["operator header"])
    sheet["rows"][0][3] = quantity
    sheet["rows"][0][-1] = "operator remark"
    if quantity == "empty":
        sheet.update(rows=[], row_ids=[])
        sheet["fields"][2] = " menu "
    saved = save_draft(case, sheet)
    with case.readonly():
        context = order_service.get_current_sheet_context(case.id)
        initial = draft_sheet_service.build_initial_sheet_draft(case.id)
        payload, error = order_service.get_ocr_sheet(case.id)
        recovered, recovery_error = order_service.build_recoverable_ocr_sheet_payload(case.id, "diagnostic")
    assert initial == sheet
    assert context["draft_id"] == saved["id"]
    assert context["source_draft_record"] == saved
    assert context["draft_payload"] == sheet
    assert error is recovery_error is None
    for key in ("fields", "header", "rows", "row_ids"):
        assert context[key] == payload[key] == recovered[key] == sheet[key]
    workflow_state_service.refresh_workflow_state(case.id)
    with case.readonly():
        snapshot = order_current_state_service.get_current_state_payload(case.id)
    assert snapshot["draft_payload"] == sheet
    assert snapshot.input_receipt["input"]["workflow_revision"] == case.revision()


def test_generic_selected_save_is_not_upgraded_from_ocr(publication_case):
    case = publication_case(quantity=6)
    assert case.selected_sheet["rows"][0][3] == "6"
    sheet = deepcopy(case.selected_sheet)
    sheet["fields"] = [f"col{index + 1}" for index in range(len(sheet["fields"]))]
    sheet["header"] = ["saved generic header"]
    sheet["row_ids"] = ["operator-generic-row"]
    sheet["rows"][0][3:] = [5, "saved generic remark"]
    saved = save_draft(case, sheet)
    code = "quantity_template_field_missing"
    with case.readonly():
        context = order_service.get_current_sheet_context(case.id)
        payload, error = order_service.get_ocr_sheet(case.id)
        recovered, recovery_error = order_service.build_recoverable_ocr_sheet_payload(case.id, "diagnostic")
        assert error is recovery_error is None
        assert context["draft_id"] == saved["id"]
        assert context["draft_payload"] == sheet
        for result in (context, payload, recovered):
            for key in ("fields", "header", "rows", "row_ids"):
                assert result[key] == sheet[key]
        gate = apply_gate_service.evaluate_sheet_gate_from_context(context)
        for result in (gate, payload):
            assert code in result["apply_blockers"] and code in result["confirm_blockers"]
        assert payload["can_apply"] is payload["can_confirm"] is False
        assert apply_gate_service.gate_can_apply(apply_blockers=gate["apply_blockers"]) is False
        assert apply_gate_service.gate_can_confirm(confirm_blockers=gate["confirm_blockers"]) is False
        assert order_service.build_confirm_materialization_candidate(case.id) == {
            "source": "draft_sheet", "draft_id": saved["id"], "error": code,
            "lines": [], "line_count": 0,
        }
    refreshed = order_service.refresh_current_sheet_context(case.id)
    assert refreshed["draft_payload"] == sheet
    workflow_state_service.refresh_workflow_state(case.id)
    revision = case.revision()
    with case.readonly():
        snapshot = order_current_state_service.get_current_state_payload(case.id)
        assert snapshot["draft_id"] == saved["id"]
        assert snapshot["draft_payload"] == sheet
        assert snapshot.input_receipt["input"]["workflow_revision"] == revision
        for key in ("fields", "header", "rows", "row_ids"):
            assert refreshed[key] == snapshot[key] == sheet[key]
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
        candidate = order_service.build_confirm_materialization_candidate(case.id)
        assert candidate["error"] == code and candidate["lines"] == []


@pytest.mark.parametrize("missing", ["template", "physical_menu"])
def test_missing_source_keeps_selected_saved_display_but_refuses_output(publication_case, missing):
    case = publication_case(quantity=5)
    sheet = deepcopy(case.selected_sheet)
    sheet.update(source="review_blocked", header=["original operator header"])
    sheet["rows"][0][3:] = [0, "operator remark"]
    saved = save_draft(case, sheet)
    with session_scope() as session:
        if missing == "template":
            session.get(Order, case.id).template_version_id = None
        else:
            session.query(MonthlyMenuEntry).delete()
    with case.readonly():
        context = order_service.get_current_sheet_context(case.id)
        payload, error = order_service.get_ocr_sheet(case.id)
        recovered, recovery_error = order_service.build_recoverable_ocr_sheet_payload(case.id, "diagnostic")
    assert context["draft_id"] == saved["id"]
    assert context["draft_payload"] == sheet
    assert context["sheet_unavailable"] is True
    assert payload is recovered is None
    assert error == recovery_error == context["blockers"][0]
    for key in ("fields", "header", "rows", "row_ids"):
        assert context[key] == sheet[key]
    if missing == "physical_menu":
        with case.readonly():
            candidate = order_service.build_confirm_materialization_candidate(case.id)
        assert candidate["error"] == "menu_entries_missing"
        assert candidate["lines"] == []
        assert candidate["draft_id"] == saved["id"]


@pytest.mark.parametrize("rejected", ["unadopted", "candidate", "foreign", "changed_payload"])
def test_saved_identity_does_not_authorize_unselected_or_modified_records(publication_case, rejected):
    case = publication_case(quantity=5)
    saved = save_draft(case, deepcopy(case.selected_sheet))
    if rejected == "changed_payload":
        saved["draft_sheet_json"]["rows"][0][3] = 70
    else:
        with session_scope() as session:
            row = session.get(OrderSheetDraft, saved["id"])
            payload = deepcopy(row.draft_sheet_json)
            if rejected == "unadopted":
                payload["adopted"] = False
            elif rejected == "candidate":
                payload["source"] = "reparse_candidate"
            else:
                # A real unowned record must not be accepted through its editor/source.
                saved["order_id"] = "foreign-order"
            row.draft_sheet_json = payload
            session.flush()
            if rejected != "foreign":
                saved = draft_sheet_service._serialize_draft(row)
    with case.readonly():
        assert order_service._draft_record_is_authoritative_current_sheet(saved) is False


@pytest.mark.parametrize("boundary", ["after_workflow_commit", "before_snapshot_write"])
def test_refresh_publication_refuses_real_input_change(publication_case, monkeypatch, boundary):
    case = publication_case(quantity=5)
    saved = save_draft(case, deepcopy(case.selected_sheet))
    publish_current(case)
    physical_before = order_current_state_service.get_current_state(case.id)
    original_revision = case.revision()
    calls = []

    def change_lines_timestamp():
        with session_scope() as session:
            row = session.get(Order, case.id)
            row.lines_updated_at = datetime.utcnow() + timedelta(seconds=10)
        calls.append(case.revision())

    if boundary == "after_workflow_commit":
        original = order_service.get_current_sheet_context
        project = workflow_state_service._build_locked_workflow_state_projection
        projected = []

        def record_projection(*args, **kwargs):
            result = project(*args, **kwargs)
            projected.append(True)
            return result

        monkeypatch.setattr(workflow_state_service, "_build_locked_workflow_state_projection", record_projection)

        def race(*args, **kwargs):
            if projected and not calls:
                change_lines_timestamp()
            return original(*args, **kwargs)

        monkeypatch.setattr(order_service, "get_current_sheet_context", race)
    else:
        original = order_current_state_service.persist_current_state

        def race(**kwargs):
            change_lines_timestamp()
            return original(**kwargs)

        monkeypatch.setattr(order_current_state_service, "persist_current_state", race)
    with pytest.raises(guard.CurrentSheetInputChanged, match="^current_sheet_input_changed$"):
        workflow_state_service.refresh_workflow_state(case.id)
    assert len(calls) == 1
    assert calls[0] == original_revision
    assert order_current_state_service.get_current_state(case.id) == physical_before
    with case.readonly():
        assert order_current_state_service.get_current_state_payload(case.id) is None
        assert draft_sheet_service.get_selected_sheet_draft(case.id) == saved
