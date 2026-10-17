"""Offline authored SQL mechanics, not recognition, image or live-flow proof.

The provider result is explicitly synthetic. Registration, source receipt,
canonical capture, ownership fences, evidence/terminal writes and delivery
settlement are real; none of those functions are replaced.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import os
import json
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
from src.models.order import Order, OrderLine
from src.models.document import OrderDocument
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_version import OrderVersion
from src.models.order_sheet_draft import OrderSheetDraft
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
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str((tmp_path / "artifacts").absolute()))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    configured = {"facility": {"fax_template_id": "authored-component", "fax_template": {"template_id": "authored-component"}},
                  "policy": {"quantity_rules": {"minimum": 0}}}
    v1 = getattr(request, "param", None) in ("v1", "v1_note")
    with_note = getattr(request, "param", None) in ("fax_note", "v1_note")
    if with_note:
        configured["facility"]["fax_template"]["columns"] = [
            {"index": 0, "source_index": 0, "role": "date", "name": "date"},
            {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart"},
            {"index": 2, "source_index": 2, "role": "menu", "name": "menu"},
            {"index": 3, "source_index": 3, "role": "quantity", "name": "qty.regular", "name_locked": True,
             "diet_type": "regular", "area_id": None},
            {"index": 4, "source_index": 4, "role": "note", "name": "remarks"},
        ]
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
        skip_ocr=getattr(request, "param", False) is True, source_kind="manual_upload", page_count=1, facility_selection=chosen,
        intake_protocol=None if v1 else "native_ingest_v2")
    assert duplicate is False
    receipt = upload["ingest_receipt"]
    if v1:
        return {"factory": store, "selection": chosen, "upload_id": upload["id"],
                "projection": projection, "source": source, "v1": True, "fax_note": with_note}
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


def authored_native_payload(f, boundary, quantity, *, note=None):
    """Authored external OCR output; real packet, source, assignment and SQL.

    This does not exercise recognition or assert accuracy on an operator FAX.
    Quantities are explicit component inputs, never derived from old order lines.
    """
    from test_modernization_canonical_ocr_artifacts import authored_outputs
    from src.services import canonical_ocr_artifact_service as artifacts
    from src.services import hakodate_cell_ocr_batch_service as batch
    from src.services import hakodate_ocr_evidence_service as evidence_service
    from src.services.hakodate_source_receipt import source_reference
    original = guard.processing_start_read_input()
    source = order_service._hakodate_owned_source(original["configured_input"], boundary["selected_pdf_receipt"])
    plan = batch.capture_original_target_plan(configured_input=original["configured_input"],
        physical_menu_entries=original["physical_menu_entries"], week_code=original["order"]["week_code"])
    regions = [{**deepcopy(region), "bbox": [10.0 + index * 100, 10.0, 90.0 + index * 100, 90.0]}
               for index, region in enumerate(plan)]
    digits = []
    for region in regions:
        value = note if region["role"] == "note" else quantity
        assert isinstance(value, str), "Every authored source cell requires an explicit value"
        digits.append({**region, "raw_text": value, "recognizer_raw_text": value,
            "ocr_text": value, "ocr_normalized": value, "pred_digits": value,
            "resolved_value": value, "recognizer_score": 0.99})
    workspace = f["source"].parent / ("external-" + boundary["execution_id"])
    outputs = authored_outputs(workspace)
    review_required = quantity != "" and not quantity.isdecimal()
    if review_required:
        from PIL import Image
        from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence
        with Image.open(outputs["contact_sheet"]) as image:
            crop = contact_crop_evidence(image, [[0, 0], [24, 0], [24, 32], [0, 32]])
        for region in digits:
            region.update(ocr_text=None, ocr_normalized=None, pred_digits=None, resolved_value=None,
                resolution_state="review_required", recognizer_input_evidence=crop, ocr_contact_crop_box=crop["bbox"])
    Path(outputs["records"]).write_text(json.dumps(digits))
    Path(outputs["ocr_regions"]).write_text(json.dumps(digits))
    packet = artifacts.persist_outputs(order_id=f["order_id"], outputs=outputs, source_receipt=source,
        expected_sheet_cells=source["expected_sheet_cells"],
        producer_version=order_service.HAKODATE_CANONICAL_PIPELINE_VERSION, workspace=workspace)
    outputs = {key: receipt["uri"] for key, receipt in packet["outputs"].items()}
    targets = evidence_service.target_cells_from_regions(regions)
    for target in targets:
        target["source_receipt"] = source_reference(source)
    records = evidence_service.evidence_from_records([
        {"text": region["raw_text"], "normalized_value": region["raw_text"], "confidence": 0.99, "bbox": region["bbox"],
         "evidence_id": "authored-" + region["sheet_cell"], "engine_metadata": {
             "sheet_cell": region["sheet_cell"], "source_receipt": source_reference(source),
             "recognizer_output": region}}
        for region in digits], run_id=boundary["execution_id"], engine="authored-external-component",
        source_scope="hakodate_cell_crop_batch", raw_payload_ref=outputs["records"], retain_empty_records=True)
    payload = {"table_rows": [["10/07", "昼", "own", quantity] + ([note] if note is not None else [])],
        "hakodate_repair_source_receipt": source,
        "hakodate_canonical_pipeline": {"status": "blocked" if review_required else "ready", "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "expected_sheet_cells": source["expected_sheet_cells"], "requested_order_id": f["order_id"],
            "outputs": outputs, "artifact_packet": packet},
        "hakodate_overlay": {"uri": outputs["overlay"], "sheet_review_base_uri": outputs["sheet_review_base"]},
        "hakodate_preprocessing": {"target_cell_map": targets},
        "hakodate_ocr_evidence": {"records": records}, "hakodate_ocr_evidence_records": deepcopy(records)}
    if review_required:
        payload["hakodate_recognition_review"] = evidence_service.recognition_review(records)
    order_service._validate_hakodate_owned_artifacts(payload, source, allow_review=review_required)
    return payload


def finish_authored_native(f, quantity, *, failure=None, note=None):
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        with guard.nonworkflow_execution_context(boundary):
            payload = authored_native_payload(f, boundary, quantity, note=note) if failure is None else None
            result, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], payload,
                error=failure, producer_version="hakodate_best_method_pipeline")
            assert error == failure
            delivery.persist_runner_receipt(f["claim"]["lease"], result["terminal_receipt"])
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"], order_id=f["order_id"],
                message_id=f["claim"]["lease"]["id"])
            assert delivery.acknowledge_claim(f["claim"]) == ("business_failed" if failure else "business_done")
    return boundary


def prepare_successor(f, source, message_id):
    saved = SimpleNamespace(message_id=message_id, pdf_uri=str(source), received_at=datetime(2026, 10, 7, 1),
        original_filename=source.name, content_sha256=sha256(source.read_bytes()).hexdigest())
    upload, duplicate = uploads.create_uploaded_pdf_from_upload(saved=saved, facility_hint="SQL-COMPONENT",
        week_hint=PERIOD, facility_name=None, skip_ocr=False, source_kind="manual_upload", page_count=1,
        facility_selection=f["selection"], intake_protocol="native_ingest_v2")
    assert not duplicate
    receipt = upload["ingest_receipt"]
    identity, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    parsed = parse_ingest_payload(claim["lease"]["payload"])
    job_id = "authored-successor-" + str(generation)
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest.prepare_ingest_ocr_job(claim["lease"], job_id, parsed)
    return {**f, "claim": claim, "job_id": job_id, "source": source, "upload_id": upload["id"]}, parsed


def register_successor(f, parsed):
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        registered = order_service.create_order_from_ingest(parsed, lines=None, document_status="processing",
            error_message="ocr_pending", registration_job_id=f["job_id"])
        assert registered["id"] == f["order_id"]
        linked = registered["ingest_registration_receipt"]
        assert uploads.refresh_uploaded_pdf_links(f["upload_id"], lease=f["claim"]["lease"]["uploaded_lease"],
            order_id=linked["order_id"], document_id=linked["document_id"]) is not None


def finish_authored_v1(f, monkeypatch, *, note=None):
    from tests.helpers.ingest_original import authored_pipeline_provider, process_uploaded_original
    authored_pipeline_provider(monkeypatch, table_rows=[["10/07", "昼", "own", "3"] + ([note] if note is not None else [])])
    assert process_uploaded_original(f["upload_id"])["result"] == "business_done"
    upload = uploads.get_uploaded_pdf(f["upload_id"])
    assert upload["status"] == "completed"
    with f["factory"]() as session:
        owner = session.query(OcrJob).filter_by(order_id=upload["current_order_id"]).one()
        captured = deepcopy(owner.metrics["nonworkflow_execution"])
        assert owner.metrics["first_pass_terminal_receipt"]["status"] == "done"
        assert captured.get("ingest_ocr_mode") is None
        assert captured["ingest_lease"]["payload"].get("intake_protocol") is None
        row = session.get(OrderWorkflowState, upload["current_order_id"])
        assert "initial_lines_receipt" not in ((row.secondary_actions_json or {}).get("workflow_v2") or {})
        f.update(order_id=upload["current_order_id"], job_id=owner.id)
    return captured


@pytest.mark.parametrize("native_sql", ["fax_note", "v1_note"], indirect=True)
def test_actual_automatic_fax_note_supersede_preserves_receipt_values_and_history(native_sql, monkeypatch):
    f = native_sql
    note = "FAX original remark"
    if f.get("v1"):
        old = finish_authored_v1(f, monkeypatch, note=note)
    else:
        old = finish_authored_native(f, "3", note=note)
    with f["factory"]() as session:
        lines = order_service._snapshot_order_lines(session, f["order_id"])
        assert [(line["quantity_original"], line["change_note"]) for line in lines] == [(3, note)]
        row = session.get(OrderWorkflowState, f["order_id"])
        if not f.get("v1"):
            receipt = row.secondary_actions_json["workflow_v2"]["initial_lines_receipt"]
            assert receipt["lines"] == lines
            assert receipt["lines_updated_at"] == guard._json_snapshot(session.get(Order, f["order_id"]).lines_updated_at)
        old_metrics = deepcopy(session.get(OcrJob, f["job_id"]).metrics)
    source = f["source"].parent / "fax-note-successor.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Explicit authored successor source with its own note")
        pdf.save(source)
    new, parsed = prepare_successor(f, source, "fax-note-successor-message")
    register_successor(new, parsed)
    finish_authored_native(new, "7", note="New FAX remark")
    with f["factory"]() as session:
        versions = session.query(OrderVersion).filter_by(order_id=f["order_id"]).order_by(OrderVersion.version_no).all()
        assert [[(line["quantity_original"], line["change_note"]) for line in version.line_snapshot]
                for version in versions] == [[(3, note)], [(7, "New FAX remark")]]
        assert [version.is_current for version in versions] == [False, True]
        assert versions[0].document_id == old["selected_pdf_receipt"]["document_id"]
        assert [(line.quantity_original, line.change_note) for line in session.query(OrderLine).filter_by(order_id=f["order_id"])] == [(7, "New FAX remark")]
        assert session.get(OcrJob, f["job_id"]).metrics == old_metrics
        assert session.get(OrderWorkflowState, f["order_id"]).evidence_run_id is None


@pytest.mark.parametrize("native_sql", ["fax_note", "v1_note"], indirect=True)
def test_actual_operator_note_change_after_initial_capture_blocks_supersede_without_clobber(native_sql, monkeypatch):
    f = native_sql
    if f.get("v1"):
        finish_authored_v1(f, monkeypatch, note="FAX original remark")
    else:
        finish_authored_native(f, "3", note="FAX original remark")
    with db.session_scope() as session:
        line = session.query(OrderLine).filter_by(order_id=f["order_id"]).one()
        line.change_note = "Explicit operator replacement note"
        # Leave the original timestamp unchanged: exact row equality must still reject.
    source = f["source"].parent / "operator-note-protected.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Must not overwrite operator note")
        pdf.save(source)
    new, parsed = prepare_successor(f, source, "operator-note-protected-message")
    before = snapshot(f)
    with pytest.raises(ValueError, match="native_ingest_(operator_lines_in_use|v1_initial_proof_changed)"):
        register_successor(new, parsed)
    assert snapshot(f) == before


@pytest.mark.parametrize("native_sql", ["v1"], indirect=True)
@pytest.mark.parametrize("change", ["quantity", "original_quantity", "renamed_menu", "saved_draft", "confirmed", "missing_capture", "missing_terminal",
    "ambiguous", "source", "execution", "timestamp", "protocol", "evidence"])
def test_actual_v1_initial_proof_cannot_authorize_edited_missing_or_ambiguous_source(native_sql, monkeypatch, change):
    f = native_sql
    finish_authored_v1(f, monkeypatch)
    with db.session_scope() as session:
        owner = session.get(OcrJob, f["job_id"])
        metrics = deepcopy(owner.metrics)
        if change == "quantity":
            session.query(OrderLine).filter_by(order_id=f["order_id"]).one().quantity_corrected = 5
        elif change == "original_quantity":
            session.query(OrderLine).filter_by(order_id=f["order_id"]).one().quantity_original = 9
        elif change == "renamed_menu":
            session.query(OrderLine).filter_by(order_id=f["order_id"]).one().menu_name = "Explicit operator rename"
        elif change == "saved_draft":
            session.add(OrderSheetDraft(id="v1-operator-draft", order_id=f["order_id"], draft_sheet_json={"kept": True}))
        elif change == "confirmed":
            session.get(Order, f["order_id"]).status = "確定"
        elif change == "missing_capture":
            metrics.pop("nonworkflow_execution")
        elif change == "missing_terminal":
            metrics.pop("first_pass_terminal_receipt")
        elif change == "ambiguous":
            # Deliberately forged duplicate metadata is a rejection case, never a success fixture.
            session.add(OcrJob(id="forged-duplicate-owner", order_id=f["order_id"], status="done",
                input_reference=owner.input_reference, metrics=deepcopy(metrics)))
        elif change == "source":
            metrics["nonworkflow_execution"]["selected_pdf_receipt"]["sha256"] = "0" * 64
        elif change == "execution":
            metrics["first_pass_terminal_receipt"]["execution_id"] = "foreign"
        elif change == "timestamp":
            session.get(Order, f["order_id"]).lines_updated_at = datetime(2026, 10, 8)
        elif change == "protocol":
            metrics["nonworkflow_execution"]["intake_protocol"] = "native_ingest_v2"
        else:
            evidence = session.get(OrderOcrEvidenceRun, metrics["evidence_run_id"])
            payload = deepcopy(evidence.payload_json)
            payload["_ocr_execution"]["job_id"] = "foreign"
            evidence.payload_json = payload
        if metrics != owner.metrics:
            # Out-of-band corruption setup, not a completion or a bypass in the tested business flow.
            session.execute(OcrJob.__table__.update().where(OcrJob.id == owner.id).values(metrics=metrics))
    source = f["source"].parent / "invalid-v1-proof-successor.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Invalid old proof must block new source registration")
        pdf.save(source)
    new, parsed = prepare_successor(f, source, "invalid-v1-proof-message")
    before = snapshot(f)
    with pytest.raises(ValueError, match="native_ingest_(v1_initial_|operator_)"):
        register_successor(new, parsed)
    assert snapshot(f) == before


@pytest.mark.parametrize("native_sql", ["v1", "v1_note"], indirect=True)
def test_actual_v1_same_pdf_explicit_native_retry_excludes_current_native_capture_from_v1_owners(native_sql, monkeypatch):
    """Service-level explicit retry mechanics, not HTTP permission or live OCR proof."""
    f = native_sql
    note = "FAX retry remark" if f["fax_note"] else None
    old = finish_authored_v1(f, monkeypatch, note=note)
    with f["factory"]() as session:
        previous_metrics = deepcopy(session.get(OcrJob, f["job_id"]).metrics)
        previous_version = session.query(OrderVersion).filter_by(order_id=f["order_id"]).one().id
    uploads.requeue_uploaded_pdf(f["upload_id"], intake_protocol="native_ingest_v2")
    recorded = ingest.get_ingest_job_snapshot(old["ingest_lease"]["id"])
    assert recorded["generation"] != old["ingest_lease"]["generation"]
    identity, generation = delivery.delivery_for_job(recorded["id"], recorded["generation"], recorded["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    parsed = parse_ingest_payload(claim["lease"]["payload"])
    new = {**f, "claim": claim, "job_id": "v1-native-same-source-" + str(generation)}
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest.prepare_ingest_ocr_job(claim["lease"], new["job_id"], parsed)
    register_successor(new, parsed)
    current = finish_authored_native(new, "7", note=note)
    assert current["selected_pdf_receipt"] == old["selected_pdf_receipt"]
    assert current["intake_protocol"] == "native_ingest_v2"
    with f["factory"]() as session:
        assert session.get(OcrJob, f["job_id"]).metrics == previous_metrics
        assert [row["quantity_original"] for row in old["orders"][f["order_id"]]["lines"]] == [3]
        assert [(line.quantity_original, line.change_note) for line in session.query(OrderLine).filter_by(order_id=f["order_id"])] == [(7, note)]
        version = session.query(OrderVersion).filter_by(order_id=f["order_id"]).one()
        assert version.id == previous_version
        assert [(line["quantity_original"], line["change_note"]) for line in version.line_snapshot] == [(7, note)]


@pytest.mark.parametrize("quantity", ["3", "0", ""])
def test_actual_native_initial_pending_lines_preserve_digits_zero_and_blank(native_sql, quantity):
    f = native_sql
    boundary = finish_authored_native(f, quantity)
    with f["factory"]() as session:
        order = session.get(Order, f["order_id"])
        row = session.get(OrderWorkflowState, f["order_id"])
        lines = session.query(OrderLine).filter_by(order_id=order.id).all()
        assert [line.quantity_original for line in lines] == ([] if quantity == "" else [int(quantity)])
        assert all(line.quantity_corrected is None for line in lines)
        assert order.status == "要確認"
        assert row.evidence_run_id is row.draft_id is row.confirmed_snapshot_id is None
        version = session.query(OrderVersion).filter_by(order_id=order.id).one()
        assert version.line_snapshot == order_service._snapshot_order_lines(session, order.id)
        evidence = session.query(OrderOcrEvidenceRun).filter_by(order_id=order.id).one()
        assert [record["raw_text"] for record in evidence.payload_json["hakodate_ocr_evidence_records"]] == [quantity]
        assert evidence.payload_json["_ocr_execution"]["selected_pdf_receipt"] == boundary["selected_pdf_receipt"]


def test_actual_native_supersede_three_to_seven_seals_old_source_history(native_sql):
    f = native_sql
    old = finish_authored_native(f, "3")
    with f["factory"]() as session:
        old_metrics = deepcopy(session.get(OcrJob, f["job_id"]).metrics)
        old_lines = order_service._snapshot_order_lines(session, f["order_id"])
    source = f["source"].parent / "explicit-second-source.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Explicit second authored component source, not a live FAX")
        pdf.save(source)
    new, parsed = prepare_successor(f, source, "authored-second-message")
    register_successor(new, parsed)
    with f["factory"]() as session:
        assert order_service._snapshot_order_lines(session, f["order_id"]) == []
        historical = session.query(OrderVersion).filter_by(document_id=old["selected_pdf_receipt"]["document_id"]).one()
        assert historical.line_snapshot == old_lines and not historical.is_current
    current = finish_authored_native(new, "7")
    assert current["selected_pdf_receipt"]["document_id"] != old["selected_pdf_receipt"]["document_id"]
    assert current["selected_pdf_receipt"]["sha256"] != old["selected_pdf_receipt"]["sha256"]
    assert current["ingest_document_transition"]["old_source"] == old["selected_pdf_receipt"]
    with f["factory"]() as session:
        versions = session.query(OrderVersion).filter_by(order_id=f["order_id"]).order_by(OrderVersion.version_no).all()
        assert [[line["quantity_original"] for line in version.line_snapshot] for version in versions] == [[3], [7]]
        assert [version.is_current for version in versions] == [False, True]
        assert session.get(OcrJob, f["job_id"]).metrics == old_metrics
        assert [line.quantity_original for line in session.query(OrderLine).filter_by(order_id=f["order_id"])] == [7]


@pytest.mark.parametrize("edit", ["corrected_quantity", "selected_evidence", "saved_draft", "confirmed"])
def test_actual_native_supersede_cannot_overwrite_operator_state(native_sql, edit):
    f = native_sql
    finish_authored_native(f, "3")
    with db.session_scope() as session:
        if edit == "corrected_quantity":
            session.query(OrderLine).filter_by(order_id=f["order_id"]).one().quantity_corrected = 5
        elif edit == "selected_evidence":
            row = session.get(OrderWorkflowState, f["order_id"])
            row.evidence_run_id = session.query(OrderOcrEvidenceRun).filter_by(order_id=f["order_id"]).one().id
        elif edit == "saved_draft":
            session.add(OrderSheetDraft(id="explicit-operator-draft", order_id=f["order_id"],
                draft_sheet_json={"operator_authored": "must remain untouched"}, edited_by="authored-operator"))
        else:
            session.get(Order, f["order_id"]).status = "確定"
    source = f["source"].parent / "operator-protected-successor.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Authored successor must not overwrite operator state")
        pdf.save(source)
    new, parsed = prepare_successor(f, source, "operator-protected-message")
    before = snapshot(f)
    with pytest.raises(ValueError, match="native_ingest_operator_"):
        register_successor(new, parsed)
    assert snapshot(f) == before


def test_actual_native_retry_uses_new_generation_same_verified_source_and_keeps_old_failure(native_sql):
    f = native_sql
    old = finish_authored_native(f, "", failure="authored_runtime_failure")
    with f["factory"]() as session:
        previous = deepcopy(session.get(OcrJob, f["job_id"]).metrics)
    uploads.requeue_uploaded_pdf(f["upload_id"], intake_protocol="native_ingest_v2")
    recorded = ingest.get_ingest_job_snapshot(f["claim"]["lease"]["id"])
    assert recorded["generation"] != f["claim"]["lease"]["generation"]
    identity, generation = delivery.delivery_for_job(recorded["id"], recorded["generation"], recorded["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    parsed = parse_ingest_payload(claim["lease"]["payload"])
    successor = {**f, "claim": claim, "job_id": "explicit-retry-" + str(generation)}
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest.prepare_ingest_ocr_job(claim["lease"], successor["job_id"], parsed)
    register_successor(successor, parsed)
    new = finish_authored_native(successor, "7")
    assert new["selected_pdf_receipt"] == old["selected_pdf_receipt"]
    with f["factory"]() as session:
        assert session.get(OcrJob, f["job_id"]).metrics == previous
        assert session.get(OcrJob, f["job_id"]).status == "failed"
        assert new["intake_protocol"] == "native_ingest_v2"


def test_actual_native_diagnostic_review_does_not_infer_initial_quantities(native_sql):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        with guard.nonworkflow_execution_context(boundary):
            payload = authored_native_payload(f, boundary, "?")
            result, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], payload,
                error="hakodate_ocr_quantity_unresolved", producer_version="hakodate_best_method_pipeline")
            assert error == "hakodate_ocr_quantity_unresolved"
            delivery.persist_runner_receipt(f["claim"]["lease"], result["terminal_receipt"])
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"], order_id=f["order_id"],
                message_id=f["claim"]["lease"]["id"])
            assert delivery.acknowledge_claim(f["claim"]) == "business_failed"
    with f["factory"]() as session:
        assert session.query(OrderLine).filter_by(order_id=f["order_id"]).count() == 0
        evidence = session.query(OrderOcrEvidenceRun).filter_by(order_id=f["order_id"]).one()
        assert evidence.status == "diagnostic_only"
        assert evidence.payload_json["hakodate_ocr_evidence_records"][0]["raw_text"] == "?"
        assert evidence.payload_json["hakodate_ocr_evidence_records"][0]["normalized_value"] is None
        assert session.get(OcrJob, f["job_id"]).metrics["result_state"] == "review_required"


@pytest.mark.parametrize("change", ["capture_protocol", "lease_protocol", "transition"])
def test_actual_native_protocol_and_transition_tamper_rolls_back_before_terminal(native_sql, change):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        if change == "capture_protocol":
            boundary["intake_protocol"] = "unknown"
        elif change == "lease_protocol":
            boundary["ingest_lease"]["payload"]["intake_protocol"] = "unknown"
        else:
            boundary["ingest_document_transition"] = {"new_document_id": "foreign"}
        before = snapshot(f)
        with guard.nonworkflow_execution_context(boundary), pytest.raises(ValueError, match="reparse_attempt_superseded"):
            order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], None,
                error="authored_runtime_failure", producer_version="hakodate_best_method_pipeline")
        assert snapshot(f) == before


def test_actual_native_source_failure_never_creates_evidence_and_is_acknowledged_without_retry(native_sql):
    from src.services.storage_service import load_selected_pdf_bytes
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        f["source"].write_bytes(b"explicit authored bytes replacement after native capture")
        with guard.nonworkflow_execution_context(boundary):
            with pytest.raises(ValueError, match="selected_pdf_bytes_changed") as raised:
                load_selected_pdf_bytes(str(f["source"]), boundary["selected_pdf_receipt"])
            receipt = ocr_job_service.complete_own_selected_pdf_failure(f["job_id"], str(raised.value))
            assert receipt["status"] == "failed" and receipt["evidence_run_id"] is None
            delivery.persist_runner_receipt(f["claim"]["lease"], receipt)
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"], order_id=f["order_id"],
                message_id=f["claim"]["lease"]["id"])
            assert delivery.acknowledge_claim(f["claim"]) == "business_failed"
    with f["factory"]() as session:
        assert session.query(OrderOcrEvidenceRun).count() == session.query(OrderLine).count() == 0
        assert session.get(UploadedPdf, f["upload_id"]).next_retry_at is None
        assert session.get(OcrJob, f["job_id"]).metrics["result_state"] == "hard_failed"
        assert session.get(IngestDelivery, f["claim"]["delivery_id"]).terminal_receipt["receipt"] == receipt
    assert delivery.claim_delivery(f["claim"]["delivery_id"], f["claim"]["generation"]) == {"duplicate": True}


def test_actual_operator_line_created_during_native_recognition_causes_full_terminal_rollback(native_sql):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        with db.session_scope() as session:
            session.add(OrderLine(id="authored-concurrent-line", order_id=f["order_id"],
                date=datetime(2026, 10, 7).date(), daypart="昼", menu_name="own", diet_type="regular",
                quantity_original=0, quantity_corrected=5, change_note="explicit operator correction"))
        before = snapshot(f)
        with guard.nonworkflow_execution_context(boundary):
            payload = authored_native_payload(f, boundary, "3")
            with pytest.raises(ValueError, match="reparse_attempt_superseded"):
                order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], payload,
                    producer_version="hakodate_best_method_pipeline")
        assert snapshot(f) == before

@pytest.mark.parametrize("failure", [None, "authored_geometry_failure"])
def test_actual_native_sql_completion_crash_before_ack_and_redelivery(native_sql, failure):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        original = deepcopy(boundary)
        # Only the external provider's result is authored, not any ownership receipt.
        with guard.nonworkflow_execution_context(boundary):
            payload = authored_native_payload(f, boundary, "3") if failure is None else None
            result, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"],
                payload, error=failure,
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
            "mode": "hakodate", "intake_protocol": "native_ingest_v2", "execution_id": original["execution_id"], "terminal_receipt": terminal}
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
