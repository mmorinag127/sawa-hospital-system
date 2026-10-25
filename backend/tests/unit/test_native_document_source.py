"""Offline real registration/capture/source fences; synthetic domain OCR only.

Only the native domain provider is stubbed in the rerun test. It creates the
existing authored canonical packet; registration, byte loading, resolver,
ownership, completion and persisted evidence all execute unchanged.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from test_ingest_delivery_contract import store as original_store
from test_native_ingest_sql_component import (
    native_sql, initialize_and_capture, authored_native_payload, snapshot,
)
from src import db
from src.models.document import OrderDocument
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_version import OrderVersion
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import menu_service, ocr_job_service, order_service
from src.services import ingest_delivery_service as delivery, ingest_job_service as ingest
from src.services import workflow_attempt_guard_service as guard


@pytest.fixture
def store(tmp_path, monkeypatch, delivery_sqlite_template):
    def foreign_keys(connection, _record):
        assert isinstance(connection, sqlite3.Connection)
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        for _, _, filename in connection.execute("PRAGMA database_list"):
            assert not filename or Path(filename).resolve().is_relative_to(tmp_path.resolve())

    event.listen(Engine, "connect", foreign_keys)
    # Delegate registration/setup to the unchanged shared fixture.
    generator = original_store.__wrapped__(tmp_path, monkeypatch, delivery_sqlite_template)
    try:
        factory = next(generator)
        monkeypatch.setattr(menu_service, "get_engine", lambda: factory.kw["bind"])
        with factory() as session:
            assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        yield factory
    finally:
        try:
            next(generator)
        except StopIteration:
            pass
        event.remove(Engine, "connect", foreign_keys)


@contextmanager
def native_capture(f):
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        assert boundary["selected_version_input"] is None
        with guard.nonworkflow_execution_context(boundary):
            yield boundary


def resolve(f, document_id):
    # Selection fences can touch the active upload lease. Inspect in a rollback-
    # only transaction; the real rerun test below exercises committed completion.
    with f["factory"]() as session:
        try:
            return order_service._captured_order_document_selection(
                session, session.get(Order, f["order_id"]), document_id)
        finally:
            session.rollback()


def test_native_registered_source_reaches_real_rerun_and_persists_owned_evidence(native_sql, monkeypatch):
    f = native_sql
    calls = []
    with native_capture(f) as boundary:
        receipt = deepcopy(boundary["selected_pdf_receipt"])
        assert resolve(f, receipt["document_id"]) == (str(f["source"]), {
            "selected_document_id": receipt["document_id"], "selected_document_version": None})

        def authored_provider(**kwargs):
            assert kwargs["document_uri_override"] == receipt["storage_uri"]
            loaded = kwargs["pdf_bytes_override"]
            assert bytes(loaded) == f["source"].read_bytes()
            assert loaded.selected_pdf_receipt == receipt
            calls.append(kwargs["order_id"])
            return authored_native_payload(f, boundary, "7")

        monkeypatch.setattr(order_service, "_hakodate_canonical_payload_from_manifest_item", authored_provider)
        result, error = order_service.rerun_ocr_evidence_only(
            f["order_id"], job_id=f["job_id"], project_sheet=False,
            refresh_workflow=False, selected_document_id=receipt["document_id"])
        assert error is None
        assert calls == [f["order_id"]]
        with f["factory"]() as session:
            job = session.get(OcrJob, f["job_id"])
            assert job.metrics["rerun_terminal_receipt"] == result["terminal_receipt"]
            assert job.metrics["nonworkflow_execution"]["selected_pdf_receipt"] == receipt
            terminal = result["terminal_receipt"]
            assert terminal["job_id"] == f["job_id"]
            assert terminal["order_id"] == f["order_id"]
            assert terminal["execution_id"] == boundary["execution_id"]
            assert terminal["workflow_attempt_id"] is None
            evidence = session.get(OrderOcrEvidenceRun, terminal["evidence_run_id"])
            assert evidence.order_id == f["order_id"]
            assert evidence.payload_json["_ocr_execution"]["selected_pdf_receipt"] == receipt
            assert job.status == "done"


@pytest.mark.parametrize("change", [
    "requested_document", "missing_document", "receipt_document", "receipt_uri",
    "missing_receipt", "input_uri", "source_bytes", "document_owner",
    "superseded_document", "stale_lease", "mixed_version", "wrong_mode", "missing_protocol",
])
def test_native_selection_rejects_foreign_missing_or_superseded_proof_without_mutation(native_sql, change):
    f = native_sql
    with native_capture(f) as boundary:
        document_id = boundary["selected_pdf_receipt"]["document_id"]
        if change == "requested_document":
            document_id = "foreign-document"
        elif change == "missing_document":
            document_id = None
        elif change == "receipt_document":
            boundary["selected_pdf_receipt"]["document_id"] = "foreign-document"
        elif change == "receipt_uri":
            boundary["selected_pdf_receipt"]["storage_uri"] = "foreign.pdf"
        elif change == "missing_receipt":
            boundary["selected_pdf_receipt"] = None
        elif change == "input_uri":
            boundary["input_reference"] = "foreign.pdf"
        elif change == "source_bytes":
            f["source"].write_bytes(b"replaced source")
        elif change == "mixed_version":
            boundary["selected_version_input"] = {"document_id": document_id}
        elif change == "wrong_mode":
            boundary["ingest_ocr_mode"] = "registration_only"
        elif change == "missing_protocol":
            boundary.pop("intake_protocol")
        else:
            with db.session_scope() as session:
                if change == "document_owner":
                    session.get(OrderDocument, document_id).source_email_id = "foreign-message"
                elif change == "superseded_document":
                    session.get(Order, f["order_id"]).current_document_id = None
                else:
                    job = session.get(OcrJob, f["job_id"])
                    job.metrics = {**job.metrics, "ingest_lease": {
                        **job.metrics["ingest_lease"], "claim_id": "foreign-claim"}}
        before = snapshot(f)
        try:
            uri, metadata = resolve(f, document_id)
        except ValueError as exc:
            assert change == "source_bytes"
            assert str(exc) == "selected_pdf_bytes_changed"
        else:
            assert uri is None
            assert metadata["error"] in {"selected_document_input_changed", "selected_document_input_missing"}
        assert snapshot(f) == before


@pytest.mark.parametrize("change", [None, "version_lines", "missing_selected", "wrong_document"])
def test_manual_selected_version_retains_exact_captured_version_contract(native_sql, change):
    f = native_sql
    job_id = "authored-manual-version-job"
    _, created = ocr_job_service.create_job(job_id, str(f["source"]), order_id=f["order_id"])
    assert created
    with f["factory"]() as session:
        document_id = session.get(Order, f["order_id"]).current_document_id
    boundary = order_service._capture_nonworkflow_order_execution(job_id, f["order_id"], document_id)
    assert boundary.get("intake_protocol") is None and boundary.get("ingest_ocr_mode") is None
    selected = deepcopy(boundary["selected_version_input"])
    assert selected["document_id"] == document_id
    if change == "version_lines":
        with db.session_scope() as session:
            version = session.execute(select(OrderVersion).where(OrderVersion.document_id == document_id)).scalar_one()
            version.line_snapshot = [{"explicit_operator_change": 26}]
    elif change == "missing_selected":
        boundary["selected_version_input"] = None
    elif change == "wrong_document":
        document_id = "foreign-document"
    before = snapshot(f)
    with guard.nonworkflow_execution_context(boundary):
        uri, metadata = resolve(f, document_id)
    if change is None:
        assert uri == selected["storage_uri"]
        assert metadata == {
            "selected_document_id": selected["document_id"],
            "selected_document_version": selected["version_no"],
            "selected_document_is_current": selected["is_current"],
            "selected_document_received_at": selected["received_at"],
        }
    else:
        assert uri is None
        assert metadata["error"] in {"selected_document_input_changed", "selected_document_input_missing"}
    assert snapshot(f) == before
