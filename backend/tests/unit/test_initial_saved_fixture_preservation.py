"""Owned SQLite tests for explicitly authored verification sheets, not OCR proof."""
from contextlib import contextmanager
from copy import deepcopy

import pytest
from sqlalchemy import insert, update

from test_initial_saved_source_service import saved, all_rows
from test_canonical_facility_lineage import authored
from test_initial_saved_import_service import command
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import initial_saved_source_service as source
from src.services import initial_saved_import_service as imports
from src.services import canonical_facility_store as store, facility_master_service


@pytest.fixture
def fixture_import(saved, monkeypatch):
    sheet = {"source": "codex_stg_verification", "fields": ["date", "qty.regular_x"],
             "rows": [["2026-01-02", "4"]], "resolved_week_id": "2026-01@2026-01-01~2026-01-07"}
    saved.execute(insert(Order).values(id="FIX", facility_code="F", status="sheet_saved",
        week_code=sheet["resolved_week_id"], message_id="verification", document_uri="stg://verification"))
    saved.execute(insert(OrderSheetDraft).values(id="FIX-D", order_id="FIX", draft_state="saved",
        edited_by="codex-stg-verification", draft_sheet_json=sheet))
    saved.execute(insert(OrderWorkflowState).values(order_id="FIX", draft_id="FIX-D", state="sheet_saved",
        revision=1, secondary_actions_json={}))
    saved.commit()
    snapshots = [source.capture_saved_source(saved, identity) for identity in ("O", "FIX")]
    version = store.initial_facility_version(authored()["facility"], "a" * 64)
    master = imports.prepare_import_master({"facilities": [{"facility_id": "F", "versions": [version]}]}, snapshots, {})
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: store.MasterSnapshot(master, "test", "owned"))
    return saved, master, snapshots[1], sheet


def test_preserves_both_formats_and_all_original_rows(fixture_import):
    session, master, original, sheet = fixture_import
    before = all_rows(session)
    assert set(master[imports.IMPORTS]) == {"O", "FIX"}
    receipt = master[imports.IMPORTS]["FIX"]["receipt"]
    assert receipt["selected"]["template_version_id"] is None
    assert receipt["selected"]["evidence_run_id"] is None
    assert source.validate_saved_source(session, receipt) == original
    binding = imports.get_binding(session, "FIX")
    assert source.current_sheet_payload(binding["snapshot"]) == sheet
    assert "row_ids" not in imports.sheet_context(binding)
    captured = imports.execution_input(session, binding)
    assert captured["evidence"] is captured["original_template_projection"] is None
    assert all_rows(session) == before


@pytest.mark.parametrize("column,value", [
    ("edited_by", "operator"), ("draft_state", "working"),
    ("template_version_id", "T"), ("base_evidence_run_id", "E"), ("order_id", "OTHER"),
])
def test_rejects_missing_provenance_or_inconsistent_reference(fixture_import, column, value):
    session, _, _, _ = fixture_import
    session.execute(update(OrderSheetDraft).where(OrderSheetDraft.id == "FIX-D").values(**{column: value}))
    with pytest.raises(ValueError):
        source.capture_saved_source(session, "FIX")


@pytest.mark.parametrize("change", ["source", "quantity", "row_ids", "week"])
def test_receipt_detects_original_sheet_changes(fixture_import, change):
    session, master, _, sheet = fixture_import
    changed = deepcopy(sheet)
    if change == "source":
        changed["source"] = "ocr"
    elif change == "quantity":
        changed["rows"][0][1] = "5"
    elif change == "row_ids":
        changed["row_ids"] = ["fabricated"]
    else:
        changed["resolved_week_id"] = "other"
    session.execute(update(OrderSheetDraft).where(OrderSheetDraft.id == "FIX-D").values(draft_sheet_json=changed))
    with pytest.raises(ValueError, match="initial_saved_source_changed"):
        source.validate_saved_source(session, master[imports.IMPORTS]["FIX"]["receipt"])


def test_ocr_dependent_operations_require_evidence(fixture_import, monkeypatch):
    from src.services import order_workflow_v2_service as workflow
    from src.services import order_service
    session, _, snapshot, _ = fixture_import
    binding = imports.get_binding(session, "FIX")
    with pytest.raises(ValueError, match="selected_ocr_required"):
        imports.processing_input(session, binding)
    with pytest.raises(ValueError, match="selected_ocr_required"):
        imports.output_candidate(binding)
    assert not imports.materialization_input_matches(session, "FIX", {"input_versions": {}})
    @contextmanager
    def scope():
        yield session
    monkeypatch.setattr(workflow, "session_scope", scope)
    monkeypatch.setattr(order_service, "session_scope", scope)
    payload, error = workflow.get_saved_sheet("FIX")
    assert error is None
    assert payload["sheet"] == snapshot["draft"]["draft_sheet_json"]
    assert payload["template_version_id"] is payload["source_ocr_result_id"] is None
    assert order_service.get_ocr_sheet("FIX") == (snapshot["draft"]["draft_sheet_json"], None)
    assert workflow.get_workflow_readonly("FIX")[1] is None
    assert workflow._build_sheet_from_selected_ocr("FIX") == (None, "selected_ocr_required")
    assert source.capture_saved_source(session, "FIX") == snapshot


def test_explicit_new_ocr_selection_keeps_saved_sheet(fixture_import):
    session, _, _, sheet = fixture_import
    with command("FIX", 1):
        imports.begin_owned_import(session, "FIX", expected_revision=1)
        order = session.get(Order, "FIX")
        workflow = session.get(OrderWorkflowState, "FIX")
        imports.prepare_new_ocr_selection(session, order, workflow)
        assert order.template_version_id == workflow.template_version_id
        assert order.template_version_id is not None
        assert session.get(OrderSheetDraft, "FIX-D").draft_sheet_json == sheet
    session.rollback()


def test_receipt_cannot_be_reassigned_to_other_order(fixture_import):
    session, master, _, _ = fixture_import
    receipt = deepcopy(master[imports.IMPORTS]["FIX"]["receipt"])
    receipt["order_id"] = "O"
    with pytest.raises(ValueError, match="initial_saved_source_changed"):
        source.validate_saved_source(session, receipt)


@pytest.mark.parametrize("operation", ["context", "ocr"])
def test_public_selection_entrypoints_with_missing_original_lineage(fixture_import, monkeypatch, operation):
    from src import db
    from src.services import order_workflow_v2_service as workflow
    from src.services import order_service
    session, master, original, _ = fixture_import
    @contextmanager
    def scope():
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
    for module in (db, workflow, order_service):
        monkeypatch.setattr(module, "session_scope", scope)
    monkeypatch.setattr(store, "read_snapshot", lambda *args, **kwargs: store.MasterSnapshot(master, "test", "owned"))
    if operation == "context":
        result, error = workflow.confirm_context(order_id="FIX", facility_id="F",
            week_start="2026-01-01", week_end="2026-01-07", expected_revision=1)
        assert error is None, error
        assert result is not None
        assert session.get(Order, "FIX").template_version_id is not None
        assert imports.get_binding(session, "FIX") is None
    else:
        # No real PDF belongs to this verification fixture. Keep the explicit
        # PDF prerequisite at the public entry rather than crashing on NULL.
        result, error = workflow.mark_ocr_run_queued("FIX", "FIX-JOB",
            input_reference=original["order"]["document_uri"], request_mode="ocr_rerun", expected_revision=1)
        assert error == "selected_pdf_document_required"
