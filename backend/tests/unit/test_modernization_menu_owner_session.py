"""Owned SQLite component proofs, not native recognition/HTTP/PG/live proofs.

Existing fixtures supply real models, canonical JSON, PDFs and capture guards.
Listeners only observe connection/transaction events; no service is replaced.
The ingest caller wiring check is static, not a full ingest/recognizer proof.
"""

import ast
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event, select, text, update

from test_modernization_real_bagging_confirmation import owned_bagging
from test_modernization_workflow_surface_parity import database_snapshot, surface
from test_modernization_owned_ocr_workflow import (
    JOB, ORDER, OwnedProducer, _queue_and_claim_owned_workflow_attempt,
)
from src.models.document import OrderDocument
from src.models.menu import MenuFacilityOverride, MenuMaster, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import menu_service as menus
from src.services import ocr_job_service, order_service as orders
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_attempt_guard_service as guards
from src.services import workflow_ocr_delivery_service as delivery


NAMES = [f"選択献立{i}" for i in range(4)]
SAVED_WEEK = "2026-03@2026-03-01~2026-03-07"


@pytest.fixture
def geometry_bagging(owned_bagging):
    f = owned_bagging
    f.authored["fax_template"]["page"] = 1
    f.republish()
    with f.session_scope() as session:
        orders._record_current_order_version(
            session, session.get(Order, f.order_id), session.get(OrderDocument, "UNIT-DOC"),
        )
    return f


def writer_lock(session, order_id):
    session.execute(update(Order).where(Order.id == order_id).values(status=Order.status))
    session.flush()


@contextmanager
def same_connection(session, engine, *, require_menu_read=True):
    connection = session.connection()
    transactions = []
    checkouts = []
    statements = []

    def checkout(*_args):
        checkouts.append(True)

    def transaction(_connection):
        transactions.append(True)

    def sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    listeners = [("checkout", checkout), ("begin", transaction),
                 ("commit", transaction), ("rollback", transaction),
                 ("before_cursor_execute", sql)]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        yield
        assert session.in_transaction()
        assert session.connection() is connection
        assert not checkouts
        assert not transactions
        assert not any(statement.split()[0] in {"BEGIN", "COMMIT", "ROLLBACK"}
                       for statement in statements)
        if require_menu_read:
            assert any("MONTHLY_MENU" in statement or "MENU_MASTERS" in statement
                       for statement in statements)
    finally:
        for name, listener in reversed(listeners):
            event.remove(engine, name, listener)


def reader(kind, *, session=None):
    kwargs = {"session": session} if session is not None else {}
    if kind == "global":
        return menus.get_menu("2026-03", **kwargs)
    if kind == "facility":
        return menus.get_menu_for_facility("2026-03", "F", **kwargs)
    if kind == "items":
        return menus.get_menu_items_for_facility("2026-03", "F", **kwargs)
    if kind == "entries":
        return menus.get_menu_entries_for_facility("2026-03", "F", **kwargs)
    if kind == "defaults":
        return menus.resolve_menu_defaults(NAMES, "F", **kwargs)
    if kind == "weekly-payload":
        return orders._load_menu_payloads_for_week(SAVED_WEEK, "F", **kwargs)
    if kind == "weekly-items":
        return orders._collect_menu_items_for_week(SAVED_WEEK, "F", **kwargs)
    if kind == "weekly-entries":
        return orders._collect_menu_entries_for_week(SAVED_WEEK, "F", **kwargs)
    assert kind == "physical"
    return orders._build_position_menu_entries_safe(SAVED_WEEK, "F", **kwargs)


@pytest.mark.parametrize("kind", ["global", "facility", "items", "entries", "defaults",
                                 "weekly-payload", "weekly-items", "weekly-entries", "physical"])
def test_readers_under_real_writer_lock_equal_standalone_readonly(owned_bagging, kind):
    f = owned_bagging
    expected = reader(kind)
    assert expected
    before = database_snapshot()
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        with same_connection(session, f.engine):
            assert reader(kind, session=session) == expected
        session.rollback()
    assert database_snapshot() == before
    assert [row[4] for row in f.saved()["rows"]] == ["7", "", "", "0"]


def test_unflushed_defaults_are_not_autoflushed_and_outer_rollback_is_retained(owned_bagging):
    f = owned_bagging
    before = database_snapshot()
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        master = session.get(MenuMaster, "UNIT-MASTER-0")
        master.qty_per_serving = 101
        with same_connection(session, f.engine):
            values, revisions = menus.resolve_menu_defaults_in_session(session, NAMES, "F")
            assert values[NAMES[0]]["qty_per_serving"] == 101
            assert revisions
            assert master in session.dirty
            with session.no_autoflush:
                assert session.execute(select(MenuMaster.qty_per_serving).where(
                    MenuMaster.id == master.id)).scalar_one() == 100
        session.rollback()
    assert database_snapshot() == before


def test_flushed_defaults_and_snapshot_matching_share_writer_connection(owned_bagging):
    f = owned_bagging
    before = database_snapshot()
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        session.get(MenuMaster, "UNIT-MASTER-0").qty_per_serving = 102
        session.get(MonthlyMenuItem, "UNIT-ITEM-0").qty_per_serving = 102
        session.flush()
        lines = [{"menu_name": NAMES[0], "daypart": "朝", "quantity_original": 7},
                 {"menu_name": NAMES[3], "daypart": "朝", "quantity_original": 0},
                 {"menu_name": NAMES[1], "daypart": "朝", "quantity_original": None}]
        original = deepcopy(lines)
        with same_connection(session, f.engine):
            assert menus.resolve_menu_defaults(NAMES, "F", session=session)[NAMES[0]]["qty_per_serving"] == 102
            matched = orders._apply_menu_matching(lines, SAVED_WEEK, "F", 0.72, session=session)
            assert matched == original
            snapshot = orders._build_menu_snapshot_from_lines(
                facility_code="F", week_code=SAVED_WEEK, lines=lines, session=session)
            assert snapshot["version"] == 1
            assert snapshot["menu_items"][NAMES[0]]["qty_per_serving"] == 102
            assert snapshot["menu_items"][NAMES[3]]["qty_per_serving"] == 100
            assert lines == original
            empty_order_snapshot = orders._build_menu_snapshot(session.get(Order, f.order_id), session=session)
            assert empty_order_snapshot["version"] == 1
            assert empty_order_snapshot["menu_items"] == {}
        session.rollback()
    assert database_snapshot() == before


def test_same_session_schema_validation_rejects_missing_column_even_after_warmup(owned_bagging):
    f = owned_bagging
    menus.ensure_menu_schema()
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        session.execute(text("ALTER TABLE monthly_menu_items RENAME COLUMN bag_max_unit TO missing_bag_max_unit"))
        with same_connection(session, f.engine):
            with pytest.raises(menus.MenuSchemaNotMigrated, match=r"monthly_menu_items\.bag_max_unit"):
                menus.get_menu_entries_for_facility("2026-03", "F", session=session)
        session.rollback()


def test_explicit_override_and_cross_month_rows_equal_standalone(owned_bagging):
    f = owned_bagging
    with f.session_scope() as session:
        session.add(MenuFacilityOverride(id="OWNER-OVERRIDE", menu_master_id="UNIT-MASTER-0",
                                         facility_id="F", qty_per_serving=125, unit_type="g"))
        session.add(MonthlyMenu(id="2026-04", month_start=date(2026, 4, 1)))
        session.flush()
        session.add(MonthlyMenuItem(
            id="OWNER-APRIL-ITEM", monthly_menu_id="2026-04", menu_master_id="UNIT-MASTER-0",
            name=NAMES[0], unit_type="g", qty_per_serving=100,
            daypart="朝", category="主", diet_type="regular", facility_override="F",
        ))
        for identifier, day, month in [("OWNER-MARCH", date(2026, 3, 31), "2026-03"),
                                       ("OWNER-APRIL", date(2026, 4, 1), "2026-04")]:
            session.add(MonthlyMenuEntry(id=identifier, monthly_menu_id=month, menu_date=day,
                                        daypart="朝", name=NAMES[0], category="主",
                                        diet_type="regular", slot_index=0, facility_override="F"))
    week = "2026-03@2026-03-31~2026-04-01"
    expected = orders._build_position_menu_entries_safe(week, "F")
    assert [(entry["menu_date"], entry["menu_name"]) for entry in expected] == [
        (date(2026, 3, 31), NAMES[0]), (date(2026, 4, 1), NAMES[0])]
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        with same_connection(session, f.engine):
            assert orders._build_position_menu_entries_safe(week, "F", session=session) == expected
            assert menus.resolve_menu_defaults(NAMES, "F", session=session)[NAMES[0]]["qty_per_serving"] == 125
            assert [month for month, _ in orders._load_menu_payloads_for_week(week, "F", session=session)] == ["2026-03", "2026-04"]
        session.rollback()


@pytest.mark.parametrize("fault", [None, "menu", "defaults", "revision", "evidence"])
def test_synchronous_capture_and_fence_under_writer_lock(owned_bagging, fault):
    f = owned_bagging
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        with same_connection(session, f.engine):
            captured = orders._capture_synchronous_cache_write_input(f.order_id, write_session=session)
            assert captured is not None
            candidate = workflow._build_materialization_candidate_for_saved_sheet(
                session=session, order=session.get(Order, f.order_id),
                saved_sheet=session.get(OrderSheetDraft, f.draft_id))
            assert candidate["error"] is None
            assert [(line["source_row_index"], line["quantity_original"])
                    for line in candidate["lines"]] == [(0, 7), (3, 0)]
            captured = candidate["synchronous_write_input"]
            assert captured["output_ocr_menu_entries"]
            assert captured["output_ocr_menu_defaults"]
            if fault == "menu":
                session.get(MonthlyMenuEntry, "UNIT-ENTRY-0").name = "changed-menu"
            elif fault == "defaults":
                session.get(MenuMaster, "UNIT-MASTER-0").qty_per_serving = 103
            elif fault == "revision":
                session.get(OrderWorkflowState, f.order_id).revision += 1
            elif fault == "evidence":
                session.get(OrderWorkflowState, f.order_id).evidence_run_id = None
            session.flush()
            assert orders._synchronous_cache_write_fence(session, f.order_id, captured) is (fault is None)
        session.rollback()


@pytest.fixture
def owned_producer(tmp_path, monkeypatch, isolated_formal_sqlite_template):
    f = OwnedProducer(tmp_path, monkeypatch, formal_template=isolated_formal_sqlite_template)
    try:
        yield f
    finally:
        f.engine.dispose()


@pytest.mark.parametrize("owner", ["workflow", "nonworkflow"])
@pytest.mark.parametrize("fault", [None, "menu", "revision", "owner"])
def test_actual_owned_fences_reject_changed_input_without_extra_connection(owned_producer, owner, fault):
    f = owned_producer
    if owner == "workflow":
        claim = _queue_and_claim_owned_workflow_attempt(f)
        context = orders.workflow_ocr_write_context(claim["workflow_attempt_id"], claim["workflow_revision"], JOB, ORDER)
        delivery_context = delivery.claim_context(claim)
    else:
        _, created = ocr_job_service.create_job(JOB, str(f.pdf), order_id=ORDER,
            uploaded_pdf_id=f.receipt["uploaded_pdf_id"], order_document_id=f.receipt["document_id"],
            input_artifact_digest=f.receipt["sha256"], template_version_id=f.projection_id)
        assert created
        boundary = guards.capture_nonworkflow_execution(JOB, order_id=ORDER, input_reference=str(f.pdf))
        context = guards.nonworkflow_execution_context(boundary)
        delivery_context = nullcontext()
    before = database_snapshot()
    with delivery_context, context, f.sessions() as session:
        writer_lock(session, ORDER)
        if fault == "menu":
            session.get(MonthlyMenuEntry, "NATIVE-0-朝-0").name = "changed-menu"
        elif fault == "revision":
            session.get(OrderWorkflowState, ORDER).revision += 1
        elif fault == "owner":
            job = session.get(OcrJob, JOB)
            job.metrics = {**job.metrics, "workflow_attempt_id": "other-owned-attempt"}
        session.flush()
        with same_connection(session, f.engine, require_menu_read=fault in {None, "menu"}):
            assert orders._workflow_attempt_write_fence(session, JOB, ORDER) is (fault is None)
        session.rollback()
    assert database_snapshot() == before


def test_geometry_revalidation_and_quad_axis_saved_receipts_share_session(geometry_bagging):
    f = geometry_bagging
    before = database_snapshot()
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        order = session.get(Order, f.order_id)
        state = session.get(OrderWorkflowState, f.order_id)
        with same_connection(session, f.engine):
            receipt, captured = workflow._axis_source_receipt(session, order, state)
            assert workflow._validate_axis_receipt(session, order, state, receipt) is None
            orders._validate_geometry_source(receipt, receipt["selected_pdf_receipt"],
                captured["configured_input"], receipt["order_input_identity"], session=session)
            session.get(MonthlyMenuEntry, "UNIT-ENTRY-0").name = "changed-menu"
            session.flush()
            with pytest.raises(ValueError, match="^geometry_source_input_changed$"):
                orders._validate_geometry_source(receipt, receipt["selected_pdf_receipt"],
                    captured["configured_input"], receipt["order_input_identity"], session=session)
        session.rollback()
    assert database_snapshot() == before


def test_materialization_defaults_terminal_comparison_keeps_outer_transaction(owned_bagging):
    f = owned_bagging
    before = database_snapshot()
    with f.sessions() as session:
        writer_lock(session, f.order_id)
        state = session.get(OrderWorkflowState, f.order_id)
        revision = state.revision
        with same_connection(session, f.engine):
            candidate = workflow._build_materialization_candidate_for_saved_sheet(
                session=session, order=session.get(Order, f.order_id),
                saved_sheet=session.get(OrderSheetDraft, f.draft_id))
            assert candidate["error"] is None
            assert "materialization_menu_defaults" in session.info
            assert workflow._finalize_workflow_mutation(session, state) is None
            assert state.revision == revision + 1
        session.rollback()
    assert database_snapshot() == before


def test_active_ingest_and_quad_axis_callers_explicitly_forward_session():
    # Static wiring complements real locked-DB calls above; not ingest/OCR proof.
    for module, function, callee in [
        (orders, "create_order_from_ingest", "_apply_menu_matching"),
        (workflow, "save_quad_review_decision", "_validate_geometry_source"),
        (workflow, "_validate_axis_receipt", "_validate_geometry_source"),
    ]:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
        calls = [node for node in ast.walk(definition) if isinstance(node, ast.Call)
                 and ((isinstance(node.func, ast.Name) and node.func.id == callee)
                      or (isinstance(node.func, ast.Attribute) and node.func.attr == callee))]
        assert len(calls) == 1
        assert any(keyword.arg == "session" and isinstance(keyword.value, ast.Name)
                   and keyword.value.id == "session" for keyword in calls[0].keywords)


@pytest.mark.parametrize("command", ["quad", "header", "row"])
def test_actual_quad_and_axis_save_commands_use_one_writer_after_readonly_cas(geometry_bagging, command, monkeypatch):
    from tests.helpers.authored_row_axis import install_image_provider, operator_input

    f = geometry_bagging
    original = f.saved()
    with f.session_scope() as session:
        receipt, _captured = workflow._axis_source_receipt(
            session, session.get(Order, f.order_id), session.get(OrderWorkflowState, f.order_id))
    row_values = None
    if command == "row":
        install_image_provider(monkeypatch)
        review, error = workflow.get_row_axis_review(f.order_id)
        assert error is None
        receipt = review["source_receipt"]
        row_values = operator_input(review)
    checkouts = []
    lifecycle = []
    transactions = []

    def checkout(*_args):
        checkouts.append(True)

    def begin(_connection):
        lifecycle.append("begin")
        transactions.append([])

    def sql(_connection, _cursor, statement, _parameters, _context, _many):
        transactions[-1].append(statement.lstrip().split(None, 1)[0].upper())

    def commit(_connection):
        lifecycle.append("commit")

    def rollback(_connection):
        lifecycle.append("rollback")

    listeners = [("checkout", checkout), ("begin", begin),
                 ("commit", commit), ("rollback", rollback), ("before_cursor_execute", sql)]
    for name, callback in listeners:
        event.listen(f.engine, name, callback)
    try:
        # Explicit synthetic operator coordinates; no estimator/recognizer proof.
        if command == "quad":
            result, error = workflow.save_quad_review_decision(
                f.order_id, decision="manual_override",
                quad_px=[[0, 0], [100, 0], [100, 100], [0, 100]],
                source_receipt=receipt, expected_revision=receipt["workflow_revision"])
        elif command == "header":
            result, error = workflow.save_header_axis_review_decision(
                f.order_id, corrected_xs=[0, 50, 100],
                coordinate_space={"mode": "template_canvas", "width": 100, "height": 100},
                source_receipt=receipt, expected_revision=receipt["workflow_revision"])
        else:
            result, error = workflow.save_row_axis_review_decision(
                f.order_id, **row_values,
                source_receipt=receipt, expected_revision=receipt["workflow_revision"])
        assert error is None, error
        assert result["revision"] == receipt["workflow_revision"] + 1
        expected_transactions = 4 if command == "row" else 2
        assert len(checkouts) == expected_transactions
        assert lifecycle == ["begin", "commit"] * expected_transactions
        writers = [index for index, statements in enumerate(transactions)
                   if set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}]
        assert len(writers) == 1 and writers[0] > 0, transactions
        assert all(statements and set(statements) <= {"SELECT", "PRAGMA"}
                   for index, statements in enumerate(transactions) if index != writers[0]), transactions
        assert set(transactions[writers[0]]) <= {"SELECT", "PRAGMA", "INSERT", "UPDATE"}, transactions
    finally:
        for name, callback in reversed(listeners):
            event.remove(f.engine, name, callback)
    with f.session_scope() as session:
        state = session.get(OrderWorkflowState, f.order_id)
        key = {"quad": "quad_override", "header": "header_axis_override", "row": "row_axis_override"}[command]
        assert state.secondary_actions_json["workflow_v2"][key]["source_receipt"] == receipt
        assert state.evidence_run_id == f.evidence_id
        assert state.draft_id == f.draft_id
    assert f.saved() == original
