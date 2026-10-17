"""Offline authored SQL mechanics, not recognition, image or live-flow proof.

The provider result is explicitly synthetic. Registration, source receipt,
canonical capture, ownership fences, evidence/terminal writes and delivery
settlement are real; none of those functions are replaced.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from sqlalchemy import select

from test_ingest_delivery_contract import store  # noqa: F401 -- real isolated SQL fixture
from tests.helpers.captured_input_fixture import authored_input, PERIOD
from src import db, models
from src.models.ingest_delivery import IngestDelivery
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_version import OrderVersion
from src.models.uploaded_pdf import UploadedPdf
from src.services import facility_master_service
from src.services import ingest_delivery_service as delivery
from src.services import ingest_facility_selection_service as selection
from src.services import ingest_job_service as ingest
from src.services import ocr_job_service
from src.services import order_service, order_workflow_v2_service as workflow
from src.services import uploaded_pdf_service as uploads
from src.services import workflow_attempt_guard_service as guard
from src.workers.ingest_mail_adapter import parse_ingest_payload
from src.workers import ingest_worker


@pytest.fixture
def native_sql(store, tmp_path, monkeypatch, request):
    configured = {"facility": {"fax_template": {"template_id": "authored-component"}},
                  "policy": {"quantity_rules": {"minimum": 0}}}
    projection, _ = authored_input(monkeypatch, tmp_path, configured, "SQL-COMPONENT")
    source = tmp_path / "authored.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Authored SQL component, not OCR proof")
        pdf.save(source)
    chosen = selection.capture_selection("SQL-COMPONENT", "2026-10-07",
        facility_master_service.get_snapshot().revision, "captured-original", PERIOD)
    saved = SimpleNamespace(message_id="sql-component-message", pdf_uri=str(source),
        received_at=datetime(2026, 10, 7), original_filename=source.name,
        content_sha256=sha256(source.read_bytes()).hexdigest())
    upload, duplicate = uploads.create_uploaded_pdf_from_upload(saved=saved,
        facility_hint="SQL-COMPONENT", week_hint=PERIOD, facility_name=None,
        skip_ocr=getattr(request, "param", False), source_kind="manual_upload", page_count=1, facility_selection=chosen)
    assert duplicate is False
    receipt = upload["ingest_receipt"]
    identity, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    parsed = parse_ingest_payload(claim["lease"]["payload"])
    job_id = "sql-component-native-job"
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest.prepare_ingest_ocr_job(claim["lease"], job_id, parsed)
        registered = order_service.create_order_from_ingest(parsed, lines=None, document_status="processing",
            error_message="ocr_pending", registration_job_id=job_id)
        linked = registered["ingest_registration_receipt"]
        assert uploads.refresh_uploaded_pdf_links(upload["id"], lease=claim["lease"]["uploaded_lease"],
            order_id=linked["order_id"], document_id=linked["document_id"]) is not None
    return {"factory": store, "claim": claim, "selection": chosen, "order_id": registered["id"],
            "job_id": job_id, "upload_id": upload["id"], "projection": projection, "source": source}


@pytest.mark.parametrize("native_sql", [True], indirect=True)
def test_registration_only_actual_source_failure_receipt_flush_runner_ack_and_redelivery(native_sql):
    from src.services.storage_service import load_selected_pdf_bytes
    f = native_sql
    claim = f["claim"]
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        with db.session_scope() as session:
            order = session.get(Order, f["order_id"])
            document_id = order.current_document_id
            job = session.get(OcrJob, f["job_id"])
            assert job.metrics["ingest_ocr_mode"] == "registration_only"
            assert claim["lease"]["payload"]["skip_ocr"] is True
        boundary = guard.capture_nonworkflow_execution(f["job_id"], order_id=f["order_id"],
            input_reference=str(f["source"]), expected_document_id=document_id,
            ingest_ocr_mode="registration_only")
        f["source"].write_bytes(b"explicitly replaced authored PDF after capture")
        with guard.nonworkflow_execution_context(boundary):
            with pytest.raises(ValueError, match="selected_pdf_bytes_changed") as raised:
                load_selected_pdf_bytes(str(f["source"]), boundary["selected_pdf_receipt"])
            terminal = ocr_job_service.complete_own_selected_pdf_failure(f["job_id"], str(raised.value))
            assert terminal["status"] == "failed"
            assert terminal["evidence_run_id"] is None
            assert terminal["adopted"] is False
            with f["factory"]() as session:
                job = session.get(OcrJob, f["job_id"])
                transport = session.get(IngestDelivery, claim["delivery_id"])
                assert job.metrics["nonworkflow_execution"]["ingest_ocr_mode"] == "registration_only"
                assert job.metrics["source_failure_receipt"] == job.metrics["non_ocr_terminal_receipt"] == job.metrics["rerun_terminal_receipt"] == terminal
                assert "first_pass_terminal_receipt" not in job.metrics
                assert job.metrics["result_state"] == "hard_failed"
                assert transport.terminal_receipt["receipt"] == terminal
                assert transport.state == "CLAIMED"
                assert session.query(OrderOcrEvidenceRun).count() == 0
            # before_flush has persisted the failure-only receipt; the runner must
            # validate it without copying evidence_run_id=None into first-pass.
            before = snapshot(f)
            delivery.persist_runner_receipt(claim["lease"], terminal)
            after = snapshot(f)
            # The real lease fence issues UPDATE status=status, invoking the
            # UploadedPdf.updated_at column's onupdate clock, not receipt writes.
            for old, current in zip(before["uploaded_pdfs"], after["uploaded_pdfs"]):
                assert old["id"] == current["id"]
                assert old["updated_at"] <= current["updated_at"] <= datetime.utcnow()
                old["updated_at"] = current["updated_at"]
            assert after == before
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"],
                order_id=f["order_id"], message_id=claim["lease"]["id"])
            assert delivery.acknowledge_claim(claim) == "business_failed"
    with f["factory"]() as session:
        job = session.get(IngestJob, claim["lease"]["id"])
        upload = session.get(UploadedPdf, f["upload_id"])
        assert job.status == "error" and job.failure_kind == "business"
        assert upload.status == "failed" and upload.next_retry_at is None
        assert session.get(OrderDocument, document_id).status == "error"
        assert session.query(OrderOcrEvidenceRun).count() == 0
        assert "first_pass_terminal_receipt" not in session.get(OcrJob, f["job_id"]).metrics
    assert delivery.claim_delivery(claim["delivery_id"], claim["generation"]) == {"duplicate": True}
    assert delivery.prepare_dispatch(claim["delivery_id"], claim["generation"]) is None


@pytest.mark.parametrize("native_sql", [True], indirect=True)
def test_registration_only_actual_success_receipt_is_durable_before_runner(native_sql):
    f = native_sql
    claim = f["claim"]
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        with db.session_scope() as session:
            document_id = session.get(Order, f["order_id"]).current_document_id
        boundary = guard.capture_nonworkflow_execution(f["job_id"], order_id=f["order_id"],
            input_reference=str(f["source"]), expected_document_id=document_id,
            ingest_ocr_mode="registration_only")
        parsed = parse_ingest_payload(claim["lease"]["payload"])
        parsed.order_id, parsed.order_document_id = f["order_id"], document_id
        with guard.nonworkflow_execution_context(boundary):
            result = order_service.create_order_from_ingest(parsed, lines=None, document_status="skipped",
                error_message="skipped_by_request", execution_input=boundary)
            terminal = result["non_ocr_terminal_receipt"]
            with f["factory"]() as session:
                job = session.get(OcrJob, f["job_id"])
                assert job.metrics["non_ocr_terminal_receipt"] == terminal
                assert "first_pass_terminal_receipt" not in job.metrics
                assert "rerun_terminal_receipt" not in job.metrics
                assert ingest.ingest_terminal_receipt(session, job, job.metrics["nonworkflow_execution"]) == terminal
                assert session.get(IngestDelivery, claim["delivery_id"]).terminal_receipt["receipt"] == terminal
            delivery.persist_runner_receipt(claim["lease"], terminal)
            delivery.settle_non_ocr_skip(claim["lease"], terminal)
            assert delivery.acknowledge_claim(claim) == "business_done"
    with f["factory"]() as session:
        assert session.get(UploadedPdf, f["upload_id"]).status == "completed"
        assert session.query(OrderOcrEvidenceRun).count() == 0
    assert delivery.claim_delivery(claim["delivery_id"], claim["generation"]) == {"duplicate": True}
    assert delivery.prepare_dispatch(claim["delivery_id"], claim["generation"]) is None


def initialize_and_capture(f):
    with db.session_scope() as session:
        order = session.get(Order, f["order_id"])
        workflow.initialize_ingest_context(session, order, f["selection"], f["job_id"])
        document_id = order.current_document_id
    captured = guard.capture_nonworkflow_execution(f["job_id"], order_id=f["order_id"],
        input_reference=str(f["source"]), expected_document_id=document_id, ingest_ocr_mode="hakodate")
    assert captured["selected_pdf_error"] is None
    assert captured["selected_pdf_receipt"]["sha256"] == sha256(f["source"].read_bytes()).hexdigest()
    assert captured["selected_pdf_receipt"]["uploaded_pdf_id"] == f["upload_id"]
    assert captured["orders"][f["order_id"]]["order"]["template_version_id"] == f["projection"]
    return captured


def snapshot(f):
    with f["factory"]() as session:
        return {table.name: deepcopy([dict(row) for row in session.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()])
            for table in models.Base.metadata.sorted_tables}


@pytest.mark.parametrize("failure", [None, "authored_geometry_failure"])
def test_actual_native_sql_completion_crash_before_ack_and_redelivery(native_sql, failure):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        original = deepcopy(boundary)
        # Only the external provider's result is authored, not any ownership receipt.
        payload = {"table_raw": "synthetic external result; no asserted OCR values", "status": "ready"}
        with guard.nonworkflow_execution_context(boundary):
            result, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"],
                payload if failure is None else None, error=failure,
                producer_version="hakodate_best_method_pipeline", project_sheet=False)
            terminal = result["terminal_receipt"]
            assert error == failure
            assert boundary["start_orders"] == original["start_orders"]
            assert boundary["selected_pdf_receipt"] == original["selected_pdf_receipt"]
            assert boundary["orders"][f["order_id"]]["workflow_revision"] > original["orders"][f["order_id"]]["workflow_revision"]
        with f["factory"]() as session:
            job = session.get(OcrJob, f["job_id"])
            row = session.get(OrderWorkflowState, f["order_id"])
            transport = session.get(IngestDelivery, f["claim"]["delivery_id"])
            assert job.metrics["rerun_terminal_receipt"] == terminal
            assert job.metrics["result_state"] == ("hard_failed" if failure else "evidence_ready")
            assert transport.terminal_receipt["receipt"] == terminal
            assert transport.state == "CLAIMED"
            assert row.state == ("ocr_failed" if failure else "ocr_completed")
            assert row.evidence_run_id is row.draft_id is row.confirmed_snapshot_id is None
            assert row.secondary_actions_json["workflow_v2"]["ocr_job_id"] == f["job_id"]
            version = session.query(OrderVersion).filter_by(order_id=f["order_id"]).one()
            assert version.document_id == original["selected_pdf_receipt"]["document_id"]
            assert version.storage_uri == str(f["source"])
            if not failure:
                evidence = session.get(OrderOcrEvidenceRun, terminal["evidence_run_id"])
                assert evidence.payload_json["_ocr_execution"]["selected_pdf_receipt"] == original["selected_pdf_receipt"]
                assert evidence.payload_json["_ocr_execution"]["nonworkflow_execution_id"] == original["execution_id"]
            else:
                assert terminal["evidence_run_id"] is None
        visible, read_error = workflow.get_workflow_readonly(f["order_id"])
        assert read_error is None
        assert visible["state"] == ("ocr_failed" if failure else "ocr_completed")
        assert visible["ocr_job"]["ocr_job_id"] == f["job_id"]
        assert visible["ocr_job"]["native_ingest"] == {
            "mode": "hakodate", "execution_id": original["execution_id"], "terminal_receipt": terminal}
        if not failure:
            candidate, read_error = workflow.get_ocr_result(f["order_id"], terminal["evidence_run_id"])
            assert read_error is None
            assert candidate["id"] == terminal["evidence_run_id"]
            assert candidate["payload_json"]["_ocr_execution"]["nonworkflow_execution_id"] == original["execution_id"]
        # A process can die here: the receipt is committed, business settlement/ack are not.
    replay = delivery.claim_delivery(f["claim"]["delivery_id"], f["claim"]["generation"])
    assert replay["lease"] == f["claim"]["lease"]
    with delivery.claim_context(replay), ingest.ingest_execution_context(replay["lease"]):
        with f["factory"]() as session:
            recovered = deepcopy(session.get(OcrJob, f["job_id"]).metrics["nonworkflow_execution"])
        with guard.nonworkflow_execution_context(recovered):
            delivery.persist_runner_receipt(replay["lease"], terminal)
            assert ingest_worker._recover_captured_ocr_job(ocr_job_service.get_job(f["job_id"])) is True
        assert delivery.acknowledge_claim(replay) == ("business_failed" if failure else "business_done")
    with f["factory"]() as session:
        assert session.get(UploadedPdf, f["upload_id"]).status == ("failed" if failure else "completed")
        assert session.get(IngestJob, replay["lease"]["id"]).status == ("error" if failure else "done")
        assert session.query(OrderOcrEvidenceRun).count() == (0 if failure else 1)
    assert delivery.inspect_generation(replay["delivery_id"], replay["generation"])["duplicate"] is True


def test_actual_stale_claim_between_registration_and_context_has_no_sql_mutation(native_sql):
    f = native_sql
    newer = delivery.claim_delivery(f["claim"]["delivery_id"], f["claim"]["generation"])
    assert newer["claim_token"] != f["claim"]["claim_token"]
    before = snapshot(f)
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        with pytest.raises(ValueError, match="ingest_attempt_changed"):
            initialize_and_capture(f)
    assert snapshot(f) == before


def test_actual_stale_workflow_cas_rolls_back_terminal_evidence_and_boundary(native_sql):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        with db.session_scope() as session:
            row = session.get(OrderWorkflowState, f["order_id"])
            row.revision += 1
        before = snapshot(f)
        captured = deepcopy(boundary)
        with guard.nonworkflow_execution_context(boundary):
            with pytest.raises(ValueError, match="reparse_attempt_superseded"):
                order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], None,
                    error="authored_runtime_failure", producer_version="hakodate_best_method_pipeline")
        assert snapshot(f) == before
        assert boundary == captured


@pytest.mark.parametrize("change", ["source_bytes", "document_owner", "job_owner", "canonical_source"])
def test_actual_changed_source_or_owner_rejects_without_terminal_mutation(native_sql, change):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        if change == "source_bytes":
            f["source"].write_bytes(b"explicitly replaced authored source")
        elif change == "canonical_source":
            path = Path(os.environ["FACILITY_MASTER_PATH"])
            path.write_text(path.read_text().replace("Synthetic captured input facility", "Changed authored source"))
        else:
            with db.session_scope() as session:
                if change == "document_owner":
                    document = session.get(OrderDocument, boundary["selected_pdf_receipt"]["document_id"])
                    document.source_email_id = "explicit-foreign-message"
                else:
                    job = session.get(OcrJob, f["job_id"])
                    job.metrics = {**job.metrics, "ingest_lease": {**job.metrics["ingest_lease"], "claim_id": "foreign"}}
        before = snapshot(f)
        captured = deepcopy(boundary)
        with guard.nonworkflow_execution_context(boundary):
            with pytest.raises(ValueError):
                order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], None,
                    error="authored_runtime_failure", producer_version="hakodate_best_method_pipeline")
        assert snapshot(f) == before
        assert boundary == captured
