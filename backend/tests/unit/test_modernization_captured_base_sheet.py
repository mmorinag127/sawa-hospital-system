"""Owned component definitions; not HTTP/auth, real FAX, or staging proof."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from src.db import session_scope
from src.models.ocr_job import OcrJob
from src.models.order import Order, OrderLine
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import config_service, order_service
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_attempt_guard_service as guard
from src.services.hakodate_physical_menu_row_service import physical_row_ids_from_entries
from tests.unit.test_h2_operator_repair_revision import repair_order  # noqa: F401


def _capture(order_id, kind, session):
    if kind == "synchronous":
        captured = order_service._capture_synchronous_cache_write_input(
            order_id, write_session=session,
        )
    else:
        captured = guard.capture_reparse_read_input(session, session.get(Order, order_id))
    assert isinstance(captured, dict)
    assert captured["physical_menu_entries"]
    return captured


def _build(order_id, kind, captured):
    keyword = "synchronous_write_input" if kind == "synchronous" else "processing_read_input"
    return order_service._build_hakodate_weekly_menu_base_sheet(order_id, **{keyword: captured})


def _order_input(kind, captured):
    return captured["input_versions"]["order"] if kind == "synchronous" else captured["order"]


@contextmanager
def _forbid_late_reads(monkeypatch, engine):
    def forbidden(*args, **kwargs):
        pytest.fail("captured base generation must not read DB/config/menu")

    with monkeypatch.context() as patch:
        patch.setattr(order_service, "session_scope", forbidden)
        patch.setattr(config_service, "get_authored_facility_config", forbidden)
        patch.setattr(config_service, "get_facility_config", forbidden)
        patch.setattr(config_service, "capture_authored_processing_config", forbidden)
        patch.setattr(config_service, "load_facility_master", forbidden)
        patch.setattr(order_service.menu_service, "get_menu_entries_for_facility", forbidden)
        event.listen(engine, "before_cursor_execute", forbidden)
        try:
            yield
        finally:
            event.remove(engine, "before_cursor_execute", forbidden)


def _assert_owned_sheet(sheet, captured):
    assert sheet["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    assert sheet["physical_menu_row_ids"] == physical_row_ids_from_entries(captured["physical_menu_entries"])
    assert sheet["physical_menu_row_count"] == len(captured["physical_menu_entries"])
    assert [row[sheet["fields"].index("menu")] for row in sheet["rows"]] == ["own"]
    assert sheet["blockers"] == []
    assert sheet["warnings"] == []


@pytest.mark.parametrize("kind", ["synchronous", "processing"])
def test_captured_inputs_survive_live_order_and_root_change(repair_order, isolated_formal_schema, monkeypatch, kind):
    order_id, _, _ = repair_order
    with session_scope() as session:
        captured = _capture(order_id, kind, session)
    original = deepcopy(captured)
    expected, error = _build(order_id, kind, captured)
    assert error is None
    _assert_owned_sheet(expected, captured)
    with session_scope() as session:
        order = session.get(Order, order_id)
        order.facility_code = None
        order.week_code = "2026-11@2026-11-01~2026-11-07"
    # Publish a different current root, keeping the selected authored version exact.
    import os
    from pathlib import Path
    path = Path(os.environ["FACILITY_MASTER_PATH"])
    original_bytes = path.read_bytes()
    master = json.loads(original_bytes)
    master["facilities"][0]["facility_name"] = "different current root"
    master["facilities"][0]["fax_template"]["columns"][-1]["diet_type"] = "soft"
    try:
        path.write_text(json.dumps(master), encoding="utf-8")
        with _forbid_late_reads(monkeypatch, isolated_formal_schema):
            actual, error = _build(order_id, kind, captured)
        assert error is None
        assert actual == expected
        assert captured == original
        assert actual["facility_id"] == "fixture"
        assert actual["week_id"] == "2026-10@2026-10-05~2026-10-11"
    finally:
        path.write_bytes(original_bytes)


def test_uncommitted_order_capture_uses_caller_transaction(repair_order, isolated_formal_schema, monkeypatch):
    order_id, _, _ = repair_order
    with session_scope() as session:
        first = session.get(Order, order_id)
        values = {column.key: deepcopy(getattr(first, column.key)) for column in Order.__table__.columns}
        values.update(id=str(uuid4()), message_id=str(uuid4()))
        second = Order(**values)
        session.add(second)
        session.flush()  # Visible in this owned transaction, not committed elsewhere.
        captured = guard.capture_reparse_read_input(session, second)
        original = deepcopy(captured)
        with _forbid_late_reads(monkeypatch, isolated_formal_schema):
            sheet, error = _build(second.id, "processing", captured)
        assert error is None
        assert sheet["order_id"] == second.id
        _assert_owned_sheet(sheet, captured)
        assert captured == original
        session.rollback()
    with session_scope() as session:
        assert session.get(Order, values["id"]) is None


@pytest.mark.parametrize("kind", ["synchronous", "processing"])
@pytest.mark.parametrize("fault, expected", [
    ("input", "hakodate_captured_config_required"),
    ("order", "hakodate_captured_config_required"),
    ("foreign", "hakodate_original_expectation_required"),
    ("missing_timestamp", "processing_start_received_at_required"),
    ("invalid_timestamp", "processing_start_received_at_invalid"),
])
def test_invalid_capture_stops_without_late_sources(repair_order, isolated_formal_schema, monkeypatch, kind, fault, expected):
    order_id, _, _ = repair_order
    with session_scope() as session:
        captured = _capture(order_id, kind, session)
    if fault == "input":
        captured = None
    elif fault == "order":
        if kind == "synchronous":
            captured["input_versions"].pop("order")
        else:
            captured.pop("order")
    else:
        row = _order_input(kind, captured)
        if fault == "foreign":
            row["id"] = str(uuid4())
        else:
            row["received_at"] = None if fault == "missing_timestamp" else "not-a-timestamp"
    original = deepcopy(captured)
    with _forbid_late_reads(monkeypatch, isolated_formal_schema):
        assert _build(order_id, kind, captured) == (None, expected)
    assert captured == original


@pytest.mark.parametrize("kind", ["synchronous", "processing"])
def test_json_timestamp_roundtrip_preserves_complete_sheet(repair_order, isolated_formal_schema, monkeypatch, kind):
    order_id, _, _ = repair_order
    with session_scope() as session:
        captured = _capture(order_id, kind, session)
    text = order_service._json_snapshot(captured)
    assert _order_input(kind, text)["received_at"] == "2026-10-07T00:00:00"
    native = deepcopy(text)
    _order_input(kind, native)["received_at"] = datetime.fromisoformat(_order_input(kind, text)["received_at"])
    with _forbid_late_reads(monkeypatch, isolated_formal_schema):
        from_text, error = _build(order_id, kind, json.loads(json.dumps(text)))
        from_native, native_error = _build(order_id, kind, native)
    assert error is native_error is None
    assert from_text == from_native
    _assert_owned_sheet(from_text, text)


def test_first_ocr_prerequisites_capture_without_selected_evidence(repair_order):
    order_id, revision, _ = repair_order
    confirmed, error = workflow.confirm_context(
        order_id=order_id, facility_id="fixture", template_id="own",
        week_start="2026-10-07", week_end="2026-10-07", expected_revision=revision,
    )
    assert error is None
    assert confirmed["selected_ocr_result_id"] is None
    with session_scope() as session:
        order = session.get(Order, order_id)
        captured = guard.capture_reparse_read_input(session, order)
        pdf = guard.capture_selected_pdf_receipt(session, order)
        assert captured["evidence"] is None
        assert pdf["order_id"] == order_id
        assert pdf["document_id"] == order.current_document_id
    base, error = _build(order_id, "processing", captured)
    assert error is None
    _assert_owned_sheet(base, captured)
    ready, error = workflow.ensure_ocr_prerequisites(order_id, expected_revision=confirmed["revision"])
    assert error is None
    assert ready["state"] == "context_confirmed"
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        assert state.blockers_json == []
        assert state.evidence_run_id is None
        assert state.draft_id is None
        assert state.state == "context_confirmed"


@pytest.mark.parametrize("entrance", ["ensure", "queue"])
def test_unknown_import_prerequisite_blocks_without_job_or_delivery(repair_order, monkeypatch, entrance):
    order_id, revision, _ = repair_order
    confirmed, error = workflow.confirm_context(
        order_id=order_id, facility_id="fixture", template_id="own",
        week_start="2026-10-07", week_end="2026-10-07", expected_revision=revision,
    )
    assert error is None
    assert confirmed["state"] == "context_confirmed"
    assert confirmed["selected_ocr_result_id"] is None
    with session_scope() as session:
        order = session.get(Order, order_id)
        captured = guard.capture_reparse_read_input(session, order)
        pdf = guard.capture_selected_pdf_receipt(session, order)
        assert captured["evidence"] is None
        assert pdf["order_id"] == order_id
        assert pdf["document_id"] == order.current_document_id
        assert order.received_at == datetime(2026, 10, 7)
    base, error = _build(order_id, "processing", captured)
    assert error is None
    _assert_owned_sheet(base, captured)
    ready, error = workflow.ensure_ocr_prerequisites(
        order_id, expected_revision=confirmed["revision"],
    )
    assert error is None
    assert ready["state"] == "context_confirmed"

    preserved_models = (Order, OrderLine, OrderOcrEvidenceRun, OrderSheetDraft, OcrJob, WorkflowOcrDelivery)

    def snapshot(session):
        return {
            model.__tablename__: [dict(row) for row in session.execute(
                select(model.__table__).order_by(*model.__table__.primary_key.columns)
            ).mappings()]
            for model in preserved_models
        }

    with session_scope() as session:
        before = deepcopy(snapshot(session))
        assert before[OcrJob.__tablename__] == []
        assert before[WorkflowOcrDelivery.__tablename__] == []
        current_revision = session.get(OrderWorkflowState, order_id).revision
    assert current_revision == ready["revision"]
    reason = "processing_start_received_at_required"
    assert reason not in workflow._OCR_PREREQUISITE_BLOCKERS
    import_attempts = []

    def unavailable():
        import_attempts.append(reason)
        raise ValueError(reason)

    # Inject an external import failure, not an invalid NOT NULL database value
    # or a successful capture/validator/queue double.
    monkeypatch.setattr(workflow, "_get_order_service_module", unavailable)
    job_id = str(uuid4())
    if entrance == "ensure":
        blocked, error = workflow.ensure_ocr_prerequisites(order_id, expected_revision=current_revision)
    else:
        blocked, error = workflow.mark_ocr_run_queued(
            order_id, job_id, input_reference=pdf["storage_uri"],
            delivery_selected_document_id=pdf["document_id"],
            expected_revision=current_revision,
        )
    assert import_attempts == [reason]
    assert error == reason
    assert blocked["state"] == "ocr_blocked"
    assert blocked["blockers"] == [reason]
    assert blocked["primary_action"] == "run_ocr"
    with session_scope() as session:
        assert snapshot(session) == before
        assert session.get(OcrJob, job_id) is None
        state = session.get(OrderWorkflowState, order_id)
        assert state.state == "ocr_blocked"
        assert state.blockers_json == [reason]
        assert state.primary_action == "run_ocr"
        assert state.revision == blocked["revision"] > current_revision
        assert state.evidence_run_id is None
        assert state.draft_id is None
        assert session.get(Order, order_id).received_at == datetime(2026, 10, 7)


@pytest.mark.parametrize("message", ["", "   "])
def test_empty_import_boundary_valueerror_is_explicit_blocker(repair_order, monkeypatch, message):
    order_id, _, _ = repair_order
    failure = ValueError(message)

    def unavailable():
        raise failure

    monkeypatch.setattr(workflow, "_get_order_service_module", unavailable)
    with session_scope() as session:
        assert workflow._hakodate_weekly_menu_base_sheet_error(session, session.get(Order, order_id)) == "ocr_prerequisite_check_failed"
