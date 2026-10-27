"""Owned SQLite pipeline records; synthetic OCR provider, never live FAX proof."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, update
from sqlalchemy.engine import Engine

from test_ingest_delivery_contract import store as original_store
from test_native_ingest_sql_component import (
    native_sql, initialize_and_capture, finish_authored_native, snapshot, db,
    delivery, ingest, guard, Order, OcrJob, OrderWorkflowState, workflow, order_service,
    authored_native_payload,
    prepare_successor, register_successor,
)
from src.models.order_version import OrderVersion
from test_native_recognition_recovery import native_review
from src.models.uploaded_pdf import UploadedPdf
from src.services import ocr_operations_read_service as operations
from src.services import ingest_delivery_dispatch_service as ingest_dispatch
from src.services import workflow_ocr_delivery_dispatch_service as dispatch
from src.services import workflow_ocr_delivery_service as workflow_delivery


@pytest.fixture
def store(tmp_path, monkeypatch, delivery_sqlite_template):
    def fk(connection, _record):
        assert isinstance(connection, sqlite3.Connection)
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert all(not row[2] or Path(row[2]).resolve().is_relative_to(tmp_path.resolve())
                   for row in connection.execute("PRAGMA database_list"))
    event.listen(Engine, "connect", fk)
    try:
        yield from original_store.__wrapped__(tmp_path, monkeypatch, delivery_sqlite_template)
    finally:
        event.remove(Engine, "connect", fk)


def read(f):
    before = snapshot(f)
    statements = []
    engine = f["factory"].kw["bind"]
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with f["factory"]() as session, patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = operations.read_operations(session)
            assert not session.dirty and not session.new and not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert snapshot(f) == before
    return result


def test_empty_does_not_mean_recognition_succeeded(store):
    with store() as session:
        result = operations.read_operations(session)
    assert result["summary"] == {"status": "idle", "counts": {}, "total": 0}


def test_native_exact_current_job_not_newest_error_or_process_local_status(native_sql):
    f = native_sql
    boundary = finish_authored_native(f, "1")
    with f["factory"]() as session:
        session.add(OcrJob(id="newer-historical", order_id=f["order_id"], status="failed",
            input_reference=str(f["source"]), error_message="historical_owner_mismatch",
            updated_at=datetime.utcnow() + timedelta(days=10)))
        session.commit()
    result = read(f)
    assert result["summary"] == {"status": "succeeded", "counts": {"succeeded": 1}, "total": 1}
    current = result["items"][0]
    assert current["job_id"] == f["job_id"]
    assert current["kind"] == "native_ingest_v2"
    assert current["execution_id"] == boundary["execution_id"]
    assert current["generation"] == boundary["ingest_lease"]["generation"]
    assert result["history"][0]["job_id"] == "newer-historical"
    assert result["history"][0]["updated_at"]


def test_real_native_review_is_not_succeeded(native_review):
    result = read(native_review)
    assert result["summary"]["counts"] == {"review_required": 1}
    assert result["items"][0]["source_status"] == "failed"


def test_real_native_active_execution_is_visible(native_sql):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
    result = read(f)
    assert result["items"][0]["status"] == "running", result
    assert result["items"][0]["execution_id"] == boundary["execution_id"]


def test_current_read_uses_bulk_rows_and_describes_each_job_once(native_sql, monkeypatch):
    f = native_sql
    finish_authored_native(f, "1")

    def command_read_forbidden(*args, **kwargs):
        raise AssertionError("read model entered a refreshing command guard")

    monkeypatch.setattr(delivery, "_original", command_read_forbidden)
    monkeypatch.setattr(delivery, "_require_uploaded_terminal", command_read_forbidden)
    real_describe = operations.ocr_job_service.describe_job_state
    with patch.object(operations.ocr_job_service, "describe_job_state", wraps=real_describe) as describe:
        result = read(f)
    assert result["summary"]["counts"] == {"succeeded": 1}
    assert describe.call_count == 1


def test_many_missing_generations_are_batched_and_history_metrics_not_loaded(native_sql):
    from sqlalchemy import inspect
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        initialize_and_capture(f)
    with f["factory"]() as session:
        original = session.get(OcrJob, f["job_id"])
        original_meta = deepcopy(session.get(OrderWorkflowState, f["order_id"]).secondary_actions_json)
        for index in range(24):
            order_id, job_id = f"missing-owner-order-{index}", f"missing-owner-job-{index}"
            session.add(Order(id=order_id, document_uri=str(f["source"]), message_id=f"authored-{index}"))
            session.flush()
            metrics = deepcopy(original.metrics)
            capture = metrics["nonworkflow_execution"]
            capture.update(job_id=job_id, order_id=order_id, job_order_id=order_id)
            capture["ingest_lease"]["id"] = f"absent-ingest-{index}"
            metrics["ingest_lease"] = deepcopy(capture["ingest_lease"])
            meta = deepcopy(original_meta)
            meta["workflow_v2"]["ocr_job_id"] = job_id
            session.add(OrderWorkflowState(order_id=order_id, state="ocr_running", secondary_actions_json=meta))
            session.add(OcrJob(id=job_id, order_id=order_id, status="running",
                              input_reference=str(f["source"]), metrics=metrics))
        for index, status in enumerate(("pending", "running", "done", "failed")):
            session.add(OcrJob(id=f"unowned-history-{index}", status=status,
                input_reference="authored-history.pdf", metrics={"historical_only": "x" * 100000}))
        session.commit()
    statements = []
    engine = f["factory"].kw["bind"]
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with f["factory"]() as session:
            loaded = []
            event.listen(session, "loaded_as_persistent", lambda _session, row: loaded.append(row))
            result = operations.read_operations(session)
            historical = [row for row in loaded
                          if isinstance(row, OcrJob) and row.id.startswith("unowned-history-")]
            assert len(historical) == 4
            assert all("metrics" in inspect(row).unloaded for row in historical)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    missing = [row for row in result["items"] if isinstance(row.get("order_id"), str)
               and row["order_id"].startswith("missing-owner-")]
    assert len(missing) == 24
    assert all(row["blockers"] == ["ocr_native_generation_required"] for row in missing)
    assert len(result["history"]) == 4
    assert sum(row["blockers"] == ["ocr_active_job_not_referenced_by_workflow"]
               for row in result["items"]) == 2
    assert len([sql for sql in statements if "FROM ingest_deliveries" in sql]) == 1
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


@pytest.mark.parametrize("damage", ["meta_missing", "missing_job", "mixed_attempt", "execution",
    "generation", "document", "source_uri", "source_digest", "terminal", "unknown_mode", "reference_type", "generation_type"])
def test_missing_tampered_or_mixed_ownership_is_visible_not_latest_fallback(native_sql, damage):
    f = native_sql
    finish_authored_native(f, "7")
    with f["factory"]() as session:
        state = session.get(OrderWorkflowState, f["order_id"])
        job = session.get(OcrJob, f["job_id"])
        metrics = deepcopy(job.metrics)
        if damage in {"missing_job", "meta_missing", "reference_type"}:
            secondary = deepcopy(state.secondary_actions_json)
            if damage == "missing_job":
                secondary["workflow_v2"]["ocr_job_id"] = "missing"
            elif damage == "reference_type":
                secondary["workflow_v2"]["ocr_job_id"] = {"invalid": "reference"}
            else:
                secondary["workflow_v2"].pop("ocr_job_id")
            state.secondary_actions_json = secondary
        elif damage == "document":
            session.get(Order, f["order_id"]).current_document_id = None
        elif damage == "source_uri":
            session.execute(update(OcrJob).where(OcrJob.id == job.id).values(input_reference="foreign.pdf"))
        elif damage == "source_digest":
            session.get(UploadedPdf, f["upload_id"]).content_sha256 = "f" * 64
        else:
            if damage == "mixed_attempt":
                metrics["workflow_attempt_id"] = "foreign"
            elif damage == "execution":
                metrics["nonworkflow_execution"]["execution_id"] = "foreign"
            elif damage == "generation":
                metrics["nonworkflow_execution"]["ingest_lease"]["generation"] = "foreign"
            elif damage == "generation_type":
                metrics["nonworkflow_execution"]["ingest_lease"]["generation"] = {"invalid": "generation"}
            elif damage == "unknown_mode":
                metrics["nonworkflow_execution"]["ingest_ocr_mode"] = "unknown"
            else:
                metrics["rerun_terminal_receipt"] = None
            session.execute(update(OcrJob).where(OcrJob.id == job.id).values(metrics=metrics))
        session.commit()
    result = read(f)
    assert result["summary"]["counts"] == {"blocked": 1}, result
    assert result["items"][0]["blockers"]


def queued_workflow(f, monkeypatch):
    finish_authored_native(f, "26")
    for key, value in ingest_dispatch.required_config().items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    with f["factory"]() as session:
        revision = session.get(OrderWorkflowState, f["order_id"]).revision
    queued, error = workflow.mark_ocr_run_queued(f["order_id"], "new-workflow-job",
        input_reference=str(f["source"]), request_mode="ocr_rerun", expected_revision=revision)
    assert error is None
    return queued


@pytest.mark.parametrize("terminal", [False, True])
def test_real_workflow_delivery_and_attempt_share_current_projection(native_sql, monkeypatch, terminal):
    f = native_sql
    queued = queued_workflow(f, monkeypatch)
    with f["factory"]() as session:
        job = session.get(OcrJob, "new-workflow-job")
        metrics = deepcopy(job.metrics)
    if terminal:
        claim = workflow_delivery.claim_delivery(queued["delivery"]["id"], queued["delivery"]["generation"],
            lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
        with workflow_delivery.claim_context(claim), order_service.workflow_ocr_write_context(
                metrics["workflow_attempt_id"], metrics["workflow_completion_revision"], "new-workflow-job", f["order_id"]):
            payload = authored_native_payload(f, {"selected_pdf_receipt": metrics["selected_pdf_receipt"],
                "execution_id": "authored-provider-packet-only"}, "26")
            completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], "new-workflow-job",
                payload, producer_version="hakodate_best_method_pipeline")
            assert error is None
            assert workflow_delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"])
    with f["factory"]() as session:
        from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
        row = session.get(WorkflowOcrDelivery, queued["delivery"]["id"])
        job = session.get(OcrJob, row.job_id)
        state = session.get(OrderWorkflowState, row.order_id)
        expected = workflow_delivery._business_status_from_rows(row, job, state)
        with patch.object(session, "get", wraps=session.get) as fresh_get:
            assert workflow_delivery._business_status(session, row) == expected
            assert len(fresh_get.call_args_list) == 2
            assert all(call.kwargs.get("populate_existing") is True for call in fresh_get.call_args_list)
    with patch.object(workflow_delivery, "_business_status", side_effect=AssertionError("read view must not reload command rows")):
        result = read(f)
    current = result["items"][0]
    assert current["blockers"] == [], current["blockers"]
    assert current["status"] == ("succeeded" if terminal else "running"), result
    assert current["kind"] == "workflow" and current["execution_id"] == metrics["workflow_attempt_id"]
    assert [row["job_id"] for row in result["history"]] == [f["job_id"]]


def test_unreferenced_active_job_remains_visible_without_claiming_an_order(store):
    with store() as session:
        session.add(OcrJob(id="unowned", input_reference="authored:unowned", status="pending"))
        session.commit()
    result = read({"factory": store})
    assert result["summary"]["counts"] == {"blocked": 1}
    assert result["items"][0]["blockers"] == ["ocr_active_job_not_referenced_by_workflow"]
    assert result["history"][0]["job_id"] == "unowned"


@pytest.mark.parametrize("tampered", [False, True])
def test_explicit_historical_document_is_not_replaced_by_current_document(native_sql, monkeypatch, tampered):
    f = native_sql
    old = finish_authored_native(f, "7")["selected_pdf_receipt"]
    source = f["source"].parent / "authored-new-registration.pdf"
    source.write_bytes(f["source"].read_bytes() + b"\n% Authored distinct component document\n")
    newer, parsed = prepare_successor(f, source, "authored-new-registration")
    register_successor(newer, parsed)
    finish_authored_native(newer, "7")
    for key, value in ingest_dispatch.required_config().items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    with f["factory"]() as session:
        version = session.query(OrderVersion).filter_by(document_id=old["document_id"]).one()
        selected = guard.selected_version_snapshot(version)
        assert selected["is_current"] is False
        assert session.get(Order, f["order_id"]).current_document_id != old["document_id"]
        revision = session.get(OrderWorkflowState, f["order_id"]).revision
    queued, error = workflow.mark_ocr_run_queued(f["order_id"], "explicit-old-document-job",
        input_reference=str(f["source"]), request_mode="ocr_rerun", expected_revision=revision,
        job_metrics={"selected_version_input": selected}, delivery_selected_document_id=old["document_id"])
    assert error is None
    if tampered:
        with f["factory"]() as session:
            session.get(OrderVersion, selected["id"]).line_snapshot = [{"explicit_component_change": 26}]
            session.commit()
    result = read(f)
    current = result["items"][0]
    assert current["job_id"] == "explicit-old-document-job"
    if tampered:
        assert current["status"] == "blocked" and current["blockers"] == ["ocr_selected_version_source_changed"]
    else:
        assert current["blockers"] == [], current["blockers"]
        assert current["status"] == "running" and current["document_id"] == old["document_id"]


@pytest.mark.parametrize("damage", ["attempt", "delivery", "receipt", "document", "job_column", "input"])
def test_workflow_source_attempt_and_delivery_neighbors_stop(native_sql, monkeypatch, damage):
    f = native_sql
    queued_workflow(f, monkeypatch)
    with f["factory"]() as session:
        job = session.get(OcrJob, "new-workflow-job")
        metrics = deepcopy(job.metrics)
        if damage == "document":
            session.get(Order, f["order_id"]).current_document_id = None
        elif damage == "job_column":
            session.execute(update(OcrJob).where(OcrJob.id == job.id).values(input_artifact_digest="f" * 64))
        else:
            if damage == "attempt":
                metrics["workflow_attempt_id"] = "foreign"
            elif damage == "delivery":
                metrics["workflow_ocr_delivery_id"] = "missing"
            elif damage == "input":
                metrics["reparse_read_input"]["order"]["document_uri"] = "foreign.pdf"
            else:
                metrics["selected_pdf_receipt"]["sha256"] = "f" * 64
            session.execute(update(OcrJob).where(OcrJob.id == job.id).values(metrics=metrics))
        session.commit()
    result = read(f)
    assert result["summary"]["counts"] == {"blocked": 1}, result
    assert result["items"][0]["blockers"]


def test_system_api_keeps_dated_legacy_history_separate_and_intake_counts(native_sql, tmp_path, monkeypatch):
    from src.api import system
    f = native_sql
    finish_authored_native(f, "0")
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"status": "running", "last_error": "historical_owner_mismatch",
        "last_job_id": "old-external", "updated_at": "2026-10-15T00:00:00"}))
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", str(history))
    # Avoid an unrelated machine quota query; no SQL ownership functions are replaced.
    monkeypatch.setattr(system, "get_db_quota_status", lambda: {"resource": "authored-component"})
    app = FastAPI()
    app.include_router(system.router)
    before = snapshot(f)
    with TestClient(app) as client:
        response = client.get("/system/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ocr_operations"]["summary"]["counts"] == {"succeeded": 1}
    assert body["ocr_pipeline"]["source"] == "recorded_sql_workflow/v1"
    assert body["ocr_pipeline"]["status"] == "succeeded"
    old = body["ocr_operations"]["external_history"]
    assert old["current"] is False and old["recorded_at"] == "2026-10-15T00:00:00"
    assert old["observation"]["last_job_id"] == "old-external"
    assert body["uploaded_pdfs"]["completed_count"] == 1
    assert body["ingest_jobs"]["done_count"] == 1
    assert body["ocr_operations"]["intake"] == {"uploaded_pdfs": body["uploaded_pdfs"], "ingest_jobs": body["ingest_jobs"]}
    assert snapshot(f) == before


def test_system_configuration_and_quality_views_do_not_scan_unrequested_sections(monkeypatch):
    from src.api import system

    def forbidden():
        pytest.fail("unrequested operational section was read")

    intake = {"mode": "manual_upload", "manual_upload_enabled": True}
    quality = {"gate": {"status": "pass"}, "sample": {"evaluated_jobs": 3}}
    monkeypatch.setattr(system, "get_intake_status", lambda: intake)
    monkeypatch.setattr(system, "GOOGLE_OAUTH_CLIENT_IDS", ["authored-client"])
    for name in ("get_operations", "summarize_ingest_jobs", "summarize_uploaded_pdfs",
                 "load_pipeline_state", "get_pipeline_config", "get_db_quota_status",
                 "summarize_reparse_quality"):
        monkeypatch.setattr(system, name, forbidden)
    configured = system.system_status(view="configuration")
    assert configured == {"intake": intake, "oauth_config": {"configured": True, "google_client_id_count": 1}}
    monkeypatch.setattr(system, "summarize_reparse_quality", lambda: quality)
    assert system.system_status(view="quality") == {**configured, "ocr_reparse_quality": quality}
    def failed_quality():
        raise RuntimeError("authored-quality-failure")
    monkeypatch.setattr(system, "summarize_reparse_quality", failed_quality)
    assert system.system_status(view="quality")["ocr_reparse_quality"] == {
        "gate": {"status": "error"}, "error": "quality_summary_failed:authored-quality-failure",
    }
