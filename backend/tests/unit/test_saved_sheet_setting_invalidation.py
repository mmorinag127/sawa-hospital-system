"""Real SQL/source/CAS and output acceptance, synthetic OCR provider input only."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import fitz
import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from test_ingest_delivery_contract import store as original_store
from test_native_ingest_sql_component import finish_authored_native, snapshot
from test_initial_saved_source_service import saved as original_saved
from test_initial_saved_import_service import editable_import  # noqa: F401
from tests.helpers.captured_input_fixture import authored_input, PERIOD
from src import db
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.menu import MonthlyMenuEntry
from src.services import facility_master_service, menu_service, output_builder
from src.services import ingest_delivery_service as delivery, ingest_job_service as ingest
from src.services import ingest_facility_selection_service as selection
from src.services import order_service, order_workflow_v2_service as workflow
from src.services import uploaded_pdf_service as uploads
from src.services import initial_saved_import_service as imports
from src.services import initial_saved_source_service as import_source
from src.workers.ingest_mail_adapter import parse_ingest_payload


@pytest.fixture
def saved(tmp_path, monkeypatch):
    # The unchanged import fixture explicitly sets DB_URI/ALEMBIC_DB_URI for its
    # own database. Do not leave the runner's different parent URL alongside it.
    with monkeypatch.context() as owned_environment:
        owned_environment.delenv("DATABASE_URL", raising=False)
        yield from original_saved.__wrapped__(owned_environment, tmp_path)


@pytest.fixture
def store(tmp_path, monkeypatch, delivery_sqlite_template):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str((tmp_path / "artifacts").absolute()))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    def enforce(connection, _record):
        assert isinstance(connection, sqlite3.Connection)
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        for _, _, filename in connection.execute("PRAGMA database_list"):
            assert not filename or Path(filename).resolve().is_relative_to(tmp_path.resolve())
    event.listen(Engine, "connect", enforce)
    generator = original_store.__wrapped__(tmp_path, monkeypatch, delivery_sqlite_template)
    try:
        factory = next(generator)
        monkeypatch.setattr(menu_service, "get_engine", lambda: factory.kw["bind"])
        yield factory
    finally:
        try:
            next(generator)
        except StopIteration:
            pass
        event.remove(Engine, "connect", enforce)


@pytest.fixture
def owned(store, tmp_path, monkeypatch):
    configured = {"facility": {
        "fax_template_id": "authored-component",
        "fax_template": {"template_id": "authored-component"},
        "bag_types": [{"bag_type_id": "UNIT-1000G", "label": "Unit 1000g", "max_qty": 1000, "unit": "g"}],
        "packaging_policy": {"split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"]},
    }, "policy": {"quantity_rules": {"minimum": 0}}}
    projection, _ = authored_input(monkeypatch, tmp_path, configured, "SQL-COMPONENT")
    source = tmp_path / "authored.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Authored saved-sheet component, not live OCR")
        pdf.save(source)
    chosen = selection.capture_selection("SQL-COMPONENT", "2026-10-07",
        facility_master_service.get_snapshot().revision, "captured-original", PERIOD)
    saved = SimpleNamespace(message_id="saved-sheet-setting-component", pdf_uri=str(source),
        received_at=datetime(2026, 10, 7), original_filename=source.name,
        content_sha256=sha256(source.read_bytes()).hexdigest())
    upload, duplicate = uploads.create_uploaded_pdf_from_upload(saved=saved,
        facility_hint="SQL-COMPONENT", week_hint=PERIOD, facility_name=None,
        skip_ocr=False, source_kind="manual_upload", page_count=1,
        facility_selection=chosen, intake_protocol="native_ingest_v2")
    assert not duplicate
    receipt = upload["ingest_receipt"]
    identity, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    parsed = parse_ingest_payload(claim["lease"]["payload"])
    job_id = "authored-setting-native-job"
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest.prepare_ingest_ocr_job(claim["lease"], job_id, parsed)
        registered = order_service.create_order_from_ingest(parsed, lines=None,
            document_status="processing", error_message="ocr_pending", registration_job_id=job_id)
        linked = registered["ingest_registration_receipt"]
        assert uploads.refresh_uploaded_pdf_links(upload["id"], lease=claim["lease"]["uploaded_lease"],
            order_id=linked["order_id"], document_id=linked["document_id"]) is not None
    return {"factory": store, "claim": claim, "selection": chosen, "order_id": registered["id"],
            "job_id": job_id, "upload_id": upload["id"], "projection": projection, "source": source}


def revision(f):
    with f["factory"]() as session:
        return session.get(OrderWorkflowState, f["order_id"]).revision


def saved_operator(f, value):
    finish_authored_native(f, "7")
    with f["factory"]() as session:
        evidence = session.execute(select(OrderOcrEvidenceRun).where(
            OrderOcrEvidenceRun.order_id == f["order_id"])).scalar_one()
        evidence_id = evidence.id
    _, error = workflow.select_ocr_result(f["order_id"], evidence_id, expected_revision=revision(f))
    assert error is None
    generated, error = workflow.build_sheet_from_selected_ocr(f["order_id"])
    assert error is None
    sheet = deepcopy(generated["sheet"])
    columns = [i for i, name in enumerate(sheet["fields"]) if name.startswith("qty.")]
    assert len(columns) == 1
    sheet["rows"][0][columns[0]] = value
    result, error = workflow.save_sheet(order_id=f["order_id"], sheet=sheet,
        edited_by="authored-operator", expected_revision=revision(f))
    assert error is None
    with db.session_scope() as session:
        order = session.get(Order, f["order_id"])
        session.add(Order(id="unrelated-order", message_id="unrelated-source",
            document_uri="authored:unrelated", facility_code=order.facility_code, week_code=order.week_code))
        session.flush()
        session.add(OrderSheetDraft(id="unrelated-draft", order_id="unrelated-order",
            draft_sheet_json={"operator": 0}, edited_by="unrelated-operator"))
    return result["saved_sheet"]


def accepted(f, *, confirmed):
    result, error = workflow.run_bagging(f["order_id"], expected_revision=revision(f))
    assert error is None, error
    assert result["bagging_result"]["summary"]["total_quantity"] == 26
    if confirmed:
        _, error = workflow.final_confirm(f["order_id"], confirmed_by="authored-operator",
            expected_revision=revision(f))
        assert error is None, error
    # Required precondition: a real reader accepts the real generated materialization.
    candidate = output_builder._accepted_output_candidate({"id": f["order_id"]})
    assert [line["quantity_original"] for line in candidate["lines"]] == [26]
    assert [line["quantity_original"] for line in candidate["output_lines"]] == [26]
    summary, error = order_service.get_bag_summary(f["order_id"])
    assert error is None and summary["generated"]
    assert sum(bag["quantity"] for bag in summary["bags"]) == 26
    return result


def protected(before, after):
    # All input, historical and neighboring rows remain byte/value exact.
    for table in before:
        if table not in {"orders", "order_workflow_states"}:
            assert after[table] == before[table], table
    old_orders = {row["id"]: row for row in before["orders"]}
    new_orders = {row["id"]: row for row in after["orders"]}
    assert new_orders["unrelated-order"] == old_orders["unrelated-order"]
    for identity, old in old_orders.items():
        assert {key: value for key, value in new_orders[identity].items() if key != "status"} == {
            key: value for key, value in old.items() if key != "status"}


@pytest.mark.parametrize("confirmed", [False, True])
def test_same_mode_preserves_every_row_revision_reference_and_warning(owned, confirmed):
    f = owned
    saved_operator(f, "26")
    accepted(f, confirmed=confirmed)
    before = snapshot(f)
    result, error = workflow.set_expanded_cell_copy_mode(f["order_id"], "auto", expected_revision=revision(f))
    assert error is None
    assert result["revision"] == revision(f)
    assert snapshot(f) == before


def test_same_mode_preserves_nonempty_saved_sheet_warnings(owned):
    f = owned
    saved_operator(f, "26")
    with db.session_scope() as session:
        session.get(OrderWorkflowState, f["order_id"]).warnings_json = [{"message": "authored saved warning"}]
    before = snapshot(f)
    _, error = workflow.set_expanded_cell_copy_mode(f["order_id"], "auto", expected_revision=revision(f))
    assert error is None
    assert snapshot(f) == before


@pytest.mark.parametrize("confirmed", [False, True])
def test_changed_mode_keeps_saved26_and_history_but_rejects_previous_current_output(owned, confirmed):
    f = owned
    saved = saved_operator(f, "26")
    accepted(f, confirmed=confirmed)
    before = snapshot(f)
    result, error = workflow.set_expanded_cell_copy_mode(f["order_id"], "enabled", expected_revision=revision(f))
    assert error is None
    assert result["state"] == "sheet_saved"
    after = snapshot(f)
    protected(before, after)
    current, error = workflow.get_saved_sheet(f["order_id"])
    assert error is None and current == saved
    inspection, error = workflow.get_inspection(f["order_id"])
    assert error is None and inspection["saved_sheet"]["sheet"]["rows"] == saved["sheet"]["rows"]
    context = order_service.get_current_sheet_context(f["order_id"])
    assert context["draft_id"] == saved["saved_sheet_id"]
    assert context["draft_payload"]["rows"] == saved["sheet"]["rows"]
    with f["factory"]() as session:
        row = session.get(OrderWorkflowState, f["order_id"])
        meta = row.secondary_actions_json["workflow_v2"]
        assert row.draft_id == saved["saved_sheet_id"]
        assert row.confirmed_snapshot_id is None
        assert all(meta.get(key) is None for key in (
            "bagging_result_id", "bagging_result", "output_bundle_id", "output_bundle", "confirmed_snapshot_id"))
        assert "bagging_input_proof" not in meta
    with pytest.raises(ValueError, match="saved-sheet acceptance receipt missing"):
        output_builder._accepted_output_candidate({"id": f["order_id"]})
    with pytest.raises(ValueError, match="saved-sheet acceptance receipt missing"):
        order_service.get_bag_summary(f["order_id"])
    _, error = workflow.prepare_output_review(f["order_id"], expected_revision=revision(f))
    assert error == "bagging_result_required"
    _, error = workflow.final_confirm(f["order_id"], expected_revision=revision(f))
    assert error == "output_review_required"
    assert snapshot(f) == after
    # Actual regeneration of output must use saved26, never old raw7.
    accepted(f, confirmed=False)
    current, error = workflow.get_saved_sheet(f["order_id"])
    assert error is None and current == saved


@pytest.mark.parametrize("value", [0, "", None])
def test_mode_changes_keep_exact_zero_blank_null_saved_inputs(owned, value):
    f = owned
    saved = saved_operator(f, value)
    before = snapshot(f)
    for mode in ("enabled", "disabled"):
        result, error = workflow.set_expanded_cell_copy_mode(f["order_id"], mode, expected_revision=revision(f))
        assert error is None and result["state"] == "sheet_saved"
        current, error = workflow.get_saved_sheet(f["order_id"])
        assert error is None and current == saved
        protected(before, snapshot(f))


@pytest.mark.parametrize("fault", ["stale_revision", "source_conflict"])
@pytest.mark.parametrize("mode", ["auto", "enabled"])
def test_rejected_setting_command_rolls_back_without_losing_any_input(owned, fault, mode):
    f = owned
    saved_operator(f, "26")
    expected = revision(f)
    if fault == "stale_revision":
        expected -= 1
    else:
        import os
        source = Path(os.environ["FACILITY_MASTER_PATH"])
        payload = json.loads(source.read_text())
        payload["facilities"][0]["versions"][0]["facility"]["facility_name"] = "Changed authored source"
        source.write_text(json.dumps(payload))
    before = snapshot(f)
    _, error = workflow.set_expanded_cell_copy_mode(f["order_id"], mode, expected_revision=expected)
    assert error == ("workflow_revision_conflict" if fault == "stale_revision" else "template_version_mismatch")
    assert snapshot(f) == before


@pytest.mark.parametrize("mode", ["auto", "enabled"])
@pytest.mark.parametrize("source", ["menu", "pdf"])
def test_existing_bagging_source_fence_runs_even_when_proof_will_be_retired(owned, mode, source):
    f = owned
    saved_operator(f, "26")
    accepted(f, confirmed=False)
    if source == "menu":
        with db.session_scope() as session:
            session.get(MonthlyMenuEntry, "captured-own-row").name = "Changed authored menu"
    else:
        f["source"].write_bytes(b"Changed original PDF bytes")
    before = snapshot(f)
    if source == "pdf":
        with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
            workflow.set_expanded_cell_copy_mode(f["order_id"], mode, expected_revision=revision(f))
    else:
        _, error = workflow.set_expanded_cell_copy_mode(f["order_id"], mode, expected_revision=revision(f))
        assert error == "bagging_input_conflict"
    assert snapshot(f) == before


@pytest.mark.parametrize("mode", ["auto", "enabled"])
def test_real_import_command_commit_leaves_no_unfinished_owner(editable_import, mode):
    session, sheet, _ = editable_import
    assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    before = import_source.capture_saved_source(session, "O")
    current_revision = session.get(OrderWorkflowState, "O").revision
    result, error = workflow.set_expanded_cell_copy_mode("O", mode, expected_revision=current_revision)
    assert error is None, error
    assert imports.OWNED not in session.info
    assert imports._owner("O") is None
    assert imports.get_binding(session, "O")["snapshot"]["draft"]["draft_sheet_json"] == sheet
    if mode == "auto":
        assert result["revision"] == current_revision
        assert import_source.capture_saved_source(session, "O") == before
    else:
        assert result["revision"] > current_revision
        assert imports.get_binding(session, "O")["snapshot"]["draft"] == before["draft"]

