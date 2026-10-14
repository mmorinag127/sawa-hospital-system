"""Owned synthetic connection tests; no claim of live-order migration parity."""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from test_initial_saved_source_service import saved, all_rows, SHEET
from test_canonical_facility_lineage import authored
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_store as store, facility_master_service
from src.services import initial_saved_import_service as service
from src.services import initial_saved_source_service as source
from src.services import order_workflow_v2_service as workflow


@pytest.fixture
def imported(saved, monkeypatch):
    assert saved.bind.dialect.name == "sqlite"
    # The shared saved fixture applies the formal Alembic chain before capture.
    snapshot = source.capture_saved_source(saved, "O")
    version = store.initial_facility_version(authored()["facility"], "a" * 64)
    master = {"facilities": [{"facility_id": "F", "versions": [version]}]}
    prepared = service.prepare_import_master(master, [snapshot], {"recorded_policy": True})
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: store.MasterSnapshot(prepared, "synthetic", "owned"))
    return saved, prepared, snapshot


@contextmanager
def command(order_id="O", revision=1):
    token = workflow._workflow_command_identity.set({"order_id": order_id, "command": "synthetic-command", "token": "unique-command"})
    expected = workflow._expected_workflow_revision.set(revision)
    try:
        yield
    finally:
        workflow._expected_workflow_revision.reset(expected)
        workflow._workflow_command_identity.reset(token)


@pytest.fixture
def editable_import(saved, monkeypatch, tmp_path):
    import hashlib
    import fitz
    from src import db
    from src.models.document import OrderDocument
    from src.models.uploaded_pdf import UploadedPdf
    from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem, MenuMaster
    from src.services import order_service, output_builder, menu_service, menu_rule_service, daily_output_override_service
    @contextmanager
    def read_scope():
        yield saved
    @contextmanager
    def write_scope():
        try:
            yield saved
            saved.commit()
        except BaseException:
            saved.rollback()
            raise
    @contextmanager
    def monthly_scope():
        from sqlalchemy.orm import Session
        with Session(saved.bind) as reader:
            yield reader
    for module in (db, order_service, output_builder, menu_rule_service, daily_output_override_service):
        monkeypatch.setattr(module, "session_scope", read_scope)
    monkeypatch.setattr(menu_service, "session_scope", monthly_scope)
    monkeypatch.setattr(workflow, "session_scope", write_scope)
    monkeypatch.setattr(menu_service, "engine", saved.bind)
    facility = authored()["facility"]
    facility["fax_template"]["columns"] = [
        {"index": 0, "source_index": 0, "role": "date"},
        {"index": 1, "source_index": 1, "role": "daypart"},
        {"index": 2, "source_index": 2, "role": "menu"},
        {"index": 3, "source_index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
        {"index": 4, "source_index": 4, "role": "note"},
    ]
    facility["bag_types"] = [{"bag_type_id": "UNIT-1000G", "label": "Unit 1000g", "max_qty": 1000, "unit": "g"}]
    facility["packaging_policy"] = {"split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"]}
    sheet = {"fields": ["date", "daypart", "menu_name", "qty.regular_x", "remarks"],
             "rows": [["2026-01-02", "朝", "Synthetic A", 0, None],
                      ["2026-01-02", "朝", "Synthetic B", 6, "raw"]],
             "row_ids": ["original-A", "original-B"], "unknown": None}
    saved.get(OrderSheetDraft, "D").draft_sheet_json = deepcopy(sheet)
    saved.get(Order, "O").week_code = "2026-01@2026-01-01~2026-01-07"
    pdf = tmp_path / "synthetic-F-2026-01-01-07.pdf"
    document = fitz.open()
    document.new_page().insert_text((50, 50), "Synthetic F / 2026-01-01..07; unit fixture only")
    document.save(pdf)
    document.close()
    saved.add(OrderDocument(id="DOC", facility_code="F", week_code=saved.get(Order, "O").week_code,
                           storage_uri=str(pdf), source_email_id="synthetic", status="done"))
    saved.flush()
    saved.get(Order, "O").current_document_id = "DOC"
    saved.get(Order, "O").document_uri = str(pdf)
    saved.add(UploadedPdf(id="PDF", message_id="synthetic", storage_uri=str(pdf),
                         content_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                         received_at=datetime(2026, 1, 1, 9),
                         original_filename=pdf.name, current_document_id="DOC", current_order_id="O", page_count=1))
    saved.add(MonthlyMenu(id="2026-01", month_start=date(2026, 1, 1)))
    saved.flush()
    for index, name in enumerate(("Synthetic A", "Synthetic B")):
        saved.add(MenuMaster(id="M" + str(index), name=name, normalized_name=menu_service._normalize_menu_name(name),
                             unit_type="g", qty_per_serving=100, category="主", daypart="朝"))
        saved.flush()
        saved.add(MonthlyMenuItem(id="I" + str(index), monthly_menu_id="2026-01", menu_master_id="M" + str(index),
                                 name=name, diet_type="regular", category="主", daypart="朝", unit_type="g", qty_per_serving=100))
        saved.add(MonthlyMenuEntry(id="R" + str(index), monthly_menu_id="2026-01", menu_date=date(2026, 1, 2),
                                  daypart="朝", category="主", diet_type="regular", name=name, slot_index=index))
    saved.commit()
    snapshot = source.capture_saved_source(saved, "O")
    master = service.prepare_import_master({"facilities": [{"facility_id": "F", "versions": [
        store.initial_facility_version(facility, "a" * 64)]}]}, [snapshot], {})
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: store.MasterSnapshot(master, "synthetic-source", "owned"))
    monkeypatch.setattr(store, "read_snapshot", lambda *args, **kwargs: store.MasterSnapshot(master, "synthetic-source", "owned"))
    return saved, sheet, snapshot


def test_real_saved_edit_rebags_new_values_without_old_bags_or_confirmation(editable_import):
    session, sheet, original = editable_import
    changed = deepcopy(sheet)
    changed["rows"][1][3] = 8
    saved_result, error = workflow.save_sheet(order_id="O", sheet=changed, expected_revision=1)
    assert error is None, error
    result, error = workflow.run_bagging("O", expected_revision=saved_result["workflow"]["revision"])
    assert error is None, error
    assert result["bagging_result"]["bagging_result_id"] != "B"
    assert result["bagging_result"]["summary"]["total_quantity"] == 8
    assert sum(row["quantity"] for row in result["bagging_result"]["bag_rows"]) == 8
    assert [line["quantity_original"] for line in result["bagging_result"]["materialization_candidate"]["lines"]] == [0, 8]
    snapshot = service.get_binding(session, "O")["snapshot"]
    assert snapshot["draft"]["draft_sheet_json"] == changed
    assert snapshot["evidence"] == original["evidence"] and snapshot["template"] == original["template"]
    assert snapshot["confirmed"] is None
    assert service.output_candidate(service.get_binding(session, "O"))["initial_saved_bag_rows"] == result["bagging_result"]["bag_rows"]
    confirmed, error = workflow.final_confirm("O", expected_revision=result["workflow"]["revision"])
    assert error is None, error
    assert confirmed["confirmed_snapshot_id"] != "C"
    assert service.get_binding(session, "O")["snapshot"]["draft"]["draft_sheet_json"] == changed


def test_rebag_missing_monthly_source_stops_without_reusing_old_output(editable_import):
    from sqlalchemy import delete
    from src.models.menu import MonthlyMenuEntry
    session, _, _ = editable_import
    session.execute(delete(MonthlyMenuEntry))
    session.commit()
    before = all_rows(session)
    result, error = workflow.run_bagging("O", expected_revision=1)
    assert result is None and error == "menu_entries_missing"
    assert all_rows(session) == before


@pytest.mark.parametrize("mint_transport", [False, True])
def test_unchanged_owned_operation_cannot_implicitly_mint_receipt(imported, mint_transport):
    session, _, original = imported
    with command():
        row = workflow._get_workflow(session, "O")
        if mint_transport:
            actions = deepcopy(row.secondary_actions_json)
            actions[service.TRANSPORT] = {"root_source_sha256": source.source_receipt(original)["source_sha256"],
                                          "receipt": source.source_receipt(original)}
            row.secondary_actions_json = actions
            with pytest.raises(ValueError, match="unfinished"):
                session.commit()
            session.rollback()
        else:
            session.commit()
    assert source.capture_saved_source(session, "O") == original


def test_outer_command_ownership_is_valid_inside_successful_savepoint(imported):
    session, _, _ = imported
    with command():
        row = workflow._get_workflow(session, "O")
        row.headline = "Owned edit before savepoint"
        session.flush()
        with session.begin_nested():
            assert service.get_binding(session, "O")["snapshot"]["workflow"]["headline"] == row.headline
            assert workflow._finalize_workflow_mutation(session, row) is None
        session.commit()
    assert service.OWNED not in session.info
    assert service.get_binding(session, "O")["snapshot"]["workflow"]["headline"] == "Owned edit before savepoint"


def test_explicit_new_ocr_selection_keeps_initial_config_and_retires_old_import(editable_import):
    from src.services import canonical_facility_lineage as lineage
    session, sheet, original = editable_import
    with command():
        row = workflow._get_workflow(session, "O")
        service.prepare_new_ocr_selection(session, session.get(Order, "O"), row)
        selected = lineage.read_projection(session, row.template_version_id)
        assert lineage.verify_projection(selected)["authored_version"]["initial_snapshot"]
        assert selected.valid_from is None
        assert selected.config_json == store.facility_versions(
            facility_master_service.get_snapshot().master, "F")[0]["facility"]
        assert workflow._finalize_workflow_mutation(session, row) is None
        session.commit()
    assert service.get_binding(session, "O") is None
    assert session.get(OrderSheetDraft, "D").draft_sheet_json == sheet
    assert session.get(OrderSheetDraft, "D").template_version_id == original["template"]["id"]
    session.execute(update(Order).where(Order.id == "O").values(template_version_id="T"))
    with pytest.raises(ValueError, match="retired"):
        service.get_binding(session, "O")
    session.rollback()


@pytest.mark.parametrize("fault", [None, "attempt", "revision", "pdf", "monthly", "source"])
def test_async_real_job_fence_accepts_owned_update_and_refuses_changed_inputs(editable_import, fault):
    from src.models.ocr_job import OcrJob
    from src.models.uploaded_pdf import UploadedPdf
    from src.models.menu import MonthlyMenuEntry
    from src.services import workflow_attempt_guard_service as guard
    session, _, original = editable_import
    captured = guard.capture_reparse_read_input(session, session.get(Order, "O"))
    pdf = guard.capture_selected_pdf_receipt(session, session.get(Order, "O"))
    session.add(OcrJob(id="JOB", order_id="O", template_version_id="T", input_reference=pdf["storage_uri"],
                       status="running", metrics={"workflow_attempt_id": "ATTEMPT", "reparse_read_input": captured,
                                                  "selected_pdf_receipt": pdf}))
    session.commit()
    if fault == "pdf":
        session.get(UploadedPdf, "PDF").content_sha256 = "b" * 64
    elif fault == "monthly":
        session.get(MonthlyMenuEntry, "R1").name = "changed monthly source"
    elif fault == "source":
        changed = deepcopy(original["draft"]["draft_sheet_json"])
        changed["rows"][1][3] = 9
        session.get(OrderSheetDraft, "D").draft_sheet_json = changed
    session.flush()
    with guard.workflow_execution_context("JOB", "O", "OTHER" if fault == "attempt" else "ATTEMPT", 2 if fault == "revision" else 1):
        if fault is not None:
            with pytest.raises(ValueError, match="ownership_rejected"):
                service.begin_owned_import(session, "O", expected_revision=2 if fault == "revision" else 1)
            session.rollback()
            assert service.get_binding(session, "O")["snapshot"] == original
        else:
            service.begin_owned_import(session, "O", expected_revision=1)
            row = session.get(OrderWorkflowState, "O")
            row.headline = "Owned asynchronous result"
            assert workflow._finalize_workflow_mutation(session, row) is None
            session.commit()
            assert service.get_binding(session, "O")["snapshot"]["workflow"]["headline"] == "Owned asynchronous result"
            assert service.OWNED not in session.info


def test_new_ocr_queue_and_real_claim_keep_current_attempt_fence(editable_import, monkeypatch):
    from sqlalchemy.orm import Session
    from src.services import workflow_attempt_guard_service as guard, workflow_ocr_delivery_service as delivery
    session, _, _ = editable_import
    values = {"PROJECT_ID": "synthetic", "LOCATION": "synthetic", "QUEUE": "synthetic",
              "TARGET_URL": "https://synthetic.invalid", "SERVICE_ACCOUNT_EMAIL": "worker@synthetic.iam.gserviceaccount.com",
              "AUDIENCE": "https://synthetic.invalid", "DISPATCH_DEADLINE_SECONDS": "600"}
    for name, value in values.items():
        monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_" + name, value)
    @contextmanager
    def delivery_scope():
        with Session(session.bind) as writer:
            try:
                yield writer
                writer.commit()
            except BaseException:
                writer.rollback()
                raise
    monkeypatch.setattr(delivery, "session_scope", delivery_scope)
    queued, error = workflow.mark_ocr_run_queued("O", "NEW-JOB", input_reference=session.get(Order, "O").document_uri,
                                               request_mode="ocr_rerun", expected_revision=1)
    assert error is None, error
    assert service.get_binding(session, "O") is None
    assert session.get(OrderWorkflowState, "O").secondary_actions_json[service.RETIRED]
    session.rollback()
    claim = delivery.claim_delivery(queued["delivery"]["id"], 1, lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert not claim.get("duplicate")
    with delivery.claim_context(claim), guard.workflow_execution_context("NEW-JOB", "O", claim["workflow_attempt_id"], claim["workflow_revision"]):
        assert guard.lock_current_execution_write(session, "O")
        assert not guard.lock_current_execution_write(session, "OTHER")
    session.rollback()
    with delivery.claim_context({**claim, "claim_token": "wrong"}), guard.workflow_execution_context(
            "NEW-JOB", "O", claim["workflow_attempt_id"], claim["workflow_revision"]):
        assert not guard.lock_current_execution_write(session, "O")
    session.rollback()


def test_reads_keep_raw_saved_source_and_do_not_grant_write_ownership(imported):
    session, _, original = imported
    before = all_rows(session)
    binding = service.get_binding(session, "O")
    assert binding["snapshot"] == original
    assert service.OWNED not in session.info
    assert service.sheet_context(binding)["draft_payload"] == SHEET
    assert service.output_candidate(binding)["lines"] == original["bagging"]["payload_json"]["materialization_candidate"]["lines"]
    assert service.output_candidate(binding)["initial_saved_bag_rows"] == original["bagging"]["payload_json"]["bag_rows"]
    assert all_rows(session) == before
    assert service.OWNED not in session.info


@pytest.mark.parametrize("kind", ["orm", "core"])
def test_unowned_change_cannot_mint_receipt_and_next_reader_rejects(imported, kind):
    session, _, _ = imported
    service.get_binding(session, "O")
    before = all_rows(session)
    sheet = deepcopy(SHEET)
    sheet["rows"][0][1] = 9
    if kind == "core":
        session.execute(update(OrderSheetDraft).where(OrderSheetDraft.id == "D").values(draft_sheet_json=sheet))
    else:
        session.get(OrderSheetDraft, "D").draft_sheet_json = sheet
        session.flush()
    service.finish_owned_import(session, "O")
    assert service.TRANSPORT not in session.get(OrderWorkflowState, "O").secondary_actions_json
    with pytest.raises(ValueError, match="initial_saved_source_changed"):
        service.get_binding(session, "O")
    session.rollback()
    assert all_rows(session) == before


@pytest.mark.parametrize("fault", ["revision-only", "other-order", "wrong-revision"])
def test_begin_requires_this_order_command_and_actual_locked_revision(imported, fault):
    session, _, _ = imported
    if fault == "revision-only":
        token = workflow._expected_workflow_revision.set(1)
        try:
            with pytest.raises(ValueError, match="command_required"):
                service.begin_owned_import(session, "O", expected_revision=1)
        finally:
            workflow._expected_workflow_revision.reset(token)
    else:
        with command("OTHER" if fault == "other-order" else "O"):
            with pytest.raises(ValueError, match="command_required|ownership_rejected"):
                service.begin_owned_import(session, "O", expected_revision=2 if fault == "wrong-revision" else 1)
    assert service.OWNED not in session.info


def test_real_revision_finalizer_persists_explicit_receipt_after_flush(imported):
    session, _, original = imported
    with command():
        row = workflow._get_workflow(session, "O")
        draft = session.get(OrderSheetDraft, "D")
        changed = deepcopy(SHEET)
        changed["rows"][1][1] = 8
        draft.draft_sheet_json = changed
        row.state = "output_review"
        session.flush()
        assert service.get_binding(session, "O")["snapshot"]["draft"]["draft_sheet_json"] == changed
        assert workflow._finalize_workflow_mutation(session, row) is None
        actions = row.secondary_actions_json
        assert actions[service.TRANSPORT]["root_source_sha256"] == source.source_receipt(original)["source_sha256"]
        assert service.get_binding(session, "O")["snapshot"]["workflow"]["revision"] == row.revision > 1
        session.commit()
    assert service.OWNED not in session.info
    snapshot = service.get_binding(session, "O")["snapshot"]
    assert snapshot["draft"]["draft_sheet_json"] == changed
    assert snapshot["confirmed"]["snapshot_json"] == original["confirmed"]["snapshot_json"]


@pytest.mark.parametrize("rollback", ["root", "savepoint"])
def test_rollback_invalidates_owned_evidence(imported, rollback):
    session, _, original = imported
    with command():
        nested = session.begin_nested() if rollback == "savepoint" else None
        service.begin_owned_import(session, "O", expected_revision=1)
        session.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "O").values(headline="pending"))
        if nested is not None:
            nested.rollback()
        else:
            session.rollback()
        assert service.OWNED not in session.info
        session.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "O").values(headline="after-rollback"))
        service.finish_owned_import(session, "O")
        with pytest.raises(ValueError, match="initial_saved_source_changed"):
            service.get_binding(session, "O")
        session.rollback()
    assert service.get_binding(session, "O")["snapshot"] == original


def test_unfinalized_core_mutation_cannot_commit(imported):
    session, _, original = imported
    with command():
        service.begin_owned_import(session, "O", expected_revision=1)
        session.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "O").values(headline="pending"))
        with pytest.raises(ValueError, match="unfinished"):
            session.commit()
        session.rollback()
    assert source.capture_saved_source(session, "O") == original


def test_change_after_explicit_finish_cannot_commit(imported):
    session, _, original = imported
    with command():
        row = workflow._get_workflow(session, "O")
        assert workflow._finalize_workflow_mutation(session, row) is None
        session.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "O").values(headline="after-finish"))
        with pytest.raises(ValueError, match="initial_saved_source_changed"):
            session.commit()
        session.rollback()
    assert source.capture_saved_source(session, "O") == original


def test_replacement_receipt_after_finish_cannot_hide_source_change(imported):
    session, _, original = imported
    with command():
        row = workflow._get_workflow(session, "O")
        assert workflow._finalize_workflow_mutation(session, row) is None
        session.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "O").values(headline="forged"))
        snapshot = source.capture_saved_source(session, "O")
        actions = deepcopy(snapshot["workflow"]["secondary_actions_json"])
        actions[service.TRANSPORT]["receipt"] = source.source_receipt(snapshot)
        session.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "O").values(secondary_actions_json=actions))
        with pytest.raises(ValueError, match="initial_saved_source_changed"):
            session.commit()
        session.rollback()
    assert source.capture_saved_source(session, "O") == original


def test_nonimported_writes_do_not_read_json_or_force_extra_flush(imported, monkeypatch):
    session, _, _ = imported
    service.get_binding(session, "O")
    def unavailable():
        pytest.fail("Native write must not add a facility JSON availability dependency")
    monkeypatch.setattr(facility_master_service, "get_snapshot", unavailable)
    session.add(Order(id="native-new", document_uri="explicit.pdf", message_id="synthetic"))
    session.flush()
    session.execute(update(Order).where(Order.id == "OTHER").values(status="native-change"))
    session.commit()
    assert session.get(Order, "native-new").document_uri == "explicit.pdf"
    assert session.get(Order, "OTHER").status == "native-change"


def test_later_configuration_never_rebinds_imported_saved_results(imported):
    session, master, original = imported
    record = master["facilities"][0]["versions"][0]
    later = authored("future", "2026-11-01", None)
    later["facility"]["facility_name"] = "Future configuration"
    master["facilities"][0]["versions"] = store.append_facility_version([record], later)
    binding = service.get_binding(session, "O")
    assert binding["snapshot"] == original
    assert binding["facility"]["facility_name"] == record["facility"]["facility_name"]
    assert binding["snapshot"]["template"]["valid_from"] is None


def test_canonical_transition_retires_old_receipt_without_relabeling_old_rows(imported):
    session, master, original = imported
    from src.services import canonical_facility_lineage as lineage
    record = master["facilities"][0]["versions"][0]
    later = authored("future", "2026-11-01", None)
    master["facilities"][0]["versions"] = store.append_facility_version([record], later)
    with command():
        row = workflow._get_workflow(session, "O")
        selection, _ = lineage.resolve(session, "F", "2026-11-01")
        with session.begin_nested():
            _, version = lineage.materialize(session, selection)
        assert service.OWNED in session.info
        session.get(Order, "O").template_version_id = version.id
        row.template_version_id = version.id
        assert workflow._finalize_workflow_mutation(session, row) is None
        assert service.TRANSPORT not in row.secondary_actions_json
        assert service.RETIRED in row.secondary_actions_json
        assert service.get_binding(session, "O") is None
        session.commit()
    assert session.get(OrderSheetDraft, "D").draft_sheet_json == SHEET
    assert session.get(OrderSheetDraft, "D").template_version_id == original["draft"]["template_version_id"]
    session.execute(update(Order).where(Order.id == "O").values(template_version_id="T"))
    with pytest.raises(ValueError, match="retired"):
        service.get_binding(session, "O")
    session.rollback()


@pytest.mark.parametrize("owner_order,job,attempt,revision", [
    ("OTHER", "missing", "attempt", 1), ("O", None, "attempt", 1),
    ("O", "missing", None, 1), ("O", "missing", "attempt", 1),
])
def test_async_owner_must_pass_actual_target_and_job_fence(imported, owner_order, job, attempt, revision):
    session, _, _ = imported
    from src.services.workflow_attempt_guard_service import workflow_execution_context
    with workflow_execution_context(job, owner_order, attempt, revision):
        with pytest.raises(ValueError, match="command_required|ownership_rejected"):
            service.begin_owned_import(session, "O", expected_revision=revision)
    assert service.OWNED not in session.info


def test_selected_ocr_and_output_origin_do_not_require_new_native_receipts(imported, monkeypatch):
    session, _, original = imported
    from src.services import order_service, output_builder
    @contextmanager
    def scope():
        yield session
    monkeypatch.setattr(order_service, "session_scope", scope)
    monkeypatch.setattr(workflow, "session_scope", scope)
    from src.services import workflow_attempt_guard_service as guard
    monkeypatch.setattr(guard, "session_scope", scope)
    from src import db
    monkeypatch.setattr(db, "session_scope", scope)
    sheet, error = order_service.get_ocr_sheet("O")
    assert error is None and sheet == SHEET
    projected, error = workflow.build_sheet_from_selected_ocr("O")
    assert error is None and projected["sheet"] == SHEET
    candidate = output_builder._accepted_output_candidate({"id": "O", "template_version_id": "T"}, write_session=session)
    origin = output_builder._accepted_output_origin(session, "O", {}, candidate)
    assert origin == source.source_receipt(original)
    assert candidate["initial_saved_bag_rows"] == original["bagging"]["payload_json"]["bag_rows"]
    for context in (order_service.get_current_sheet_context("O"), order_service.refresh_current_sheet_context("O")):
        assert context["draft_payload"] == SHEET
        assert context["source_draft_record"]["id"] == "D"
    adoption = output_builder._capture_output_adoption("O", session)
    output_builder._require_output_adoption(session, adoption)
    assert adoption["accepted_origin"] == source.source_receipt(original)


def test_actual_save_sheet_retains_values_and_refreshes_receipt_in_same_transaction(imported, monkeypatch):
    session, _, original = imported
    @contextmanager
    def scope():
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
    monkeypatch.setattr(workflow, "session_scope", scope)
    changed = deepcopy(SHEET)
    changed["rows"][1][1] = 8
    result, error = workflow.save_sheet(order_id="O", sheet=changed, expected_revision=1)
    assert error is None
    assert result["saved_sheet"]["sheet"] == changed
    assert service.OWNED not in session.info
    snapshot = service.get_binding(session, "O")["snapshot"]
    assert snapshot["draft"]["draft_sheet_json"] == changed
    assert snapshot["evidence"] == original["evidence"]
    assert snapshot["template"] == original["template"]
    assert snapshot["workflow"]["state"] == "sheet_saved"
    assert snapshot["confirmed"] is None


def test_tampered_configuration_and_receipt_are_not_adopted(imported):
    session, master, _ = imported
    master[service.CONFIGURATIONS][next(iter(master[service.CONFIGURATIONS]))]["policy"]["unexpected"] = True
    with pytest.raises(ValueError, match="configuration_changed"):
        service.get_binding(session, "O")


def test_old_seven_key_candidate_is_read_without_new_proof_or_row_metadata(imported):
    session, prepared, _ = imported
    from src.models.order_output_artifact import OrderBaggingResult
    bagging = session.get(OrderBaggingResult, "B")
    payload = deepcopy(bagging.payload_json)
    old = {"derived_week_code": "saved-week", "draft_id": "D", "draft_state": "saved",
           "error": None, "line_count": 2, "lines": payload["materialization_candidate"]["lines"], "source": "draft_sheet"}
    payload["materialization_candidate"] = deepcopy(old)
    bagging.payload_json = payload
    session.flush()
    snapshot = source.capture_saved_source(session, "O")
    master = deepcopy(prepared)
    master.pop(service.IMPORTS)
    master.pop(service.CONFIGURATIONS)
    updated = service.prepare_import_master(master, [snapshot], {"recorded_policy": True})
    prepared.clear()
    prepared.update(updated)
    before = all_rows(session)
    binding = service.get_binding(session, "O")
    candidate = service.output_candidate(binding)
    assert candidate["lines"] == old["lines"] and candidate["output_lines"] == old["lines"]
    assert binding["snapshot"]["bagging"]["payload_json"]["materialization_candidate"] == old
    assert all_rows(session) == before


def test_readonly_prepare_tool_checks_all_source_hashes(imported, tmp_path):
    _, prepared, original = imported
    import hashlib
    spec = importlib.util.spec_from_file_location("prepare_initial_saved", Path(__file__).resolve().parents[3] / "scripts/prepare_initial_saved_import.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    master = deepcopy(prepared)
    master.pop(service.IMPORTS)
    master.pop(service.CONFIGURATIONS)
    inputs = {"initial": master, "snapshots": [original], "policy": {"explicit": True}}
    paths, hashes = {}, {}
    for name, value in inputs.items():
        paths[name] = tmp_path / (name + ".json")
        raw = json.dumps(value).encode()
        paths[name].write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    raw, report = module.prepare(paths["initial"], paths["snapshots"], paths["policy"], hashes=hashes)
    assert json.loads(raw)[service.IMPORTS]["O"]["receipt"] == source.source_receipt(original)
    assert report["orders"] == 1
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[name] for name, path in paths.items())
    with pytest.raises(ValueError, match="hash_mismatch"):
        module.prepare(paths["initial"], paths["snapshots"], paths["policy"], hashes={**hashes, "policy": "bad"})


def test_prepare_cli_writes_only_new_output_and_hash_report(imported, tmp_path, monkeypatch):
    _, prepared, snapshot = imported
    import hashlib
    import subprocess
    import sys
    spec = importlib.util.spec_from_file_location("prepare_initial_saved_cli", Path(__file__).resolve().parents[3] / "scripts/prepare_initial_saved_import.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    master = deepcopy(prepared)
    master.pop(service.IMPORTS)
    master.pop(service.CONFIGURATIONS)
    argv = [str(module.__file__)]
    inputs = {}
    for name, value in (("initial", master), ("snapshots", [snapshot]), ("policy", {"explicit": True})):
        path = tmp_path / (name + ".json")
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        inputs[path] = raw
        argv += ["--" + name, str(path), "--" + name + "-sha256", hashlib.sha256(raw).hexdigest()]
    output, report = tmp_path / "prepared.json", tmp_path / "report.json"
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=module.ROOT, text=True).strip()
    argv += ["--source-sha", source_sha, "--output", str(output), "--report", str(report)]
    monkeypatch.setattr(sys, "argv", argv)
    module.main()
    result = json.loads(report.read_text())
    assert result["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["orders"] == 1 and result["source_sha"] == source_sha
    assert all(path.read_bytes() == raw for path, raw in inputs.items())
    with pytest.raises(ValueError, match="output_exists"):
        module.main()


def test_prepare_cli_rejects_wrong_source_sha_without_output(imported, tmp_path, monkeypatch):
    _, prepared, snapshot = imported
    import hashlib
    import subprocess
    import sys
    spec = importlib.util.spec_from_file_location("prepare_initial_saved_cli_wrong_sha", Path(__file__).resolve().parents[3] / "scripts/prepare_initial_saved_import.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    master = deepcopy(prepared)
    master.pop(service.IMPORTS)
    master.pop(service.CONFIGURATIONS)
    argv = [str(module.__file__)]
    inputs = {}
    for name, value in (("initial", master), ("snapshots", [snapshot]), ("policy", {"explicit": True})):
        path = tmp_path / (name + ".json")
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        inputs[path] = raw
        argv += ["--" + name, str(path), "--" + name + "-sha256", hashlib.sha256(raw).hexdigest()]
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=module.ROOT, text=True).strip()
    wrong_sha = ("1" if source_sha[0] == "0" else "0") + source_sha[1:]
    output, report = tmp_path / "prepared.json", tmp_path / "report.json"
    argv += ["--source-sha", wrong_sha, "--output", str(output), "--report", str(report)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ValueError, match="^initial_saved_import_source_sha_mismatch$"):
        module.main()
    assert not output.exists()
    assert not report.exists()
    assert all(path.read_bytes() == raw for path, raw in inputs.items())
