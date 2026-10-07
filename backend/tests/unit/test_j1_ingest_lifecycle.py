"""Formal-schema lifecycle components with real owned receipts and provider doubles."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import select
from src.db import session_scope
from tests.helpers.ingest_original import configure_delivery, authored_sources, authored_pipeline_provider, register_authored_payload
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.uploaded_pdf import UploadedPdf
from src.services import ingest_job_service as ingest
from src.services import uploaded_pdf_service as uploads
from src.services import workflow_attempt_guard_service as guard
from src.services import ingest_delivery_service as delivery
from src.services import order_service
from src.workers import ingest_worker as worker
from src.workers.ingest_mail_adapter import parse_ingest_payload
from src.api import ingest as ingest_api


@pytest.fixture
def store(isolated_formal_schema, monkeypatch):
    configure_delivery(monkeypatch)
    yield session_scope


@pytest.fixture
def payload(store, tmp_path, monkeypatch):
    uri, digest = authored_sources(monkeypatch, tmp_path,
        facility_id="authored-facility", menu_date=date(2026, 10, 7))
    return {"message_id": "authored-source", "pdf_uri": uri,
            "received_at": "2026-10-07T00:00:00", "facility_hint": "authored-facility",
            "week_hint": "2026-10@2026-10-05~2026-10-11", "skip_ocr": False,
            "content_sha256": digest,
            "unknown": {"zero": 0, "null": None}}


def claim(receipt, **kwargs):
    return ingest.claim_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"], **kwargs)


def process_original(receipt):
    delivery_id, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    return delivery.process(delivery_id, generation)


def prepare_original(payload, monkeypatch, *, failed=False, attach_upload=True):
    with session_scope() as session:
        present = session.get(UploadedPdf, "own-upload")
    if present is None and attach_upload:
        add_upload(session_scope, payload)
    original = ingest.get_ingest_job_snapshot(payload["message_id"])
    delivery_id, generation = delivery.delivery_for_job(original["id"], original["generation"], original["input_digest"])
    parsed = parse_ingest_payload(payload)
    job_id = f"OCR-{original['id']}"
    if parsed.uploaded_pdf_id is not None:
        register_authored_payload(parsed, effective_date=date(2026, 10, 7))
    else:
        owned = delivery.claim_delivery(delivery_id, generation)
        with delivery.claim_context(owned), ingest.ingest_execution_context(owned["lease"]):
            ingest.prepare_ingest_ocr_job(owned["lease"], job_id, parsed)
            register_authored_payload(parsed, effective_date=date(2026, 10, 7), registration_job_id=job_id)
    if failed:
        from src.services.ocr_job_service import OwnPipelineLineageError
        calls = []
        def rejected_provider(**kwargs):
            assert guard.current_nonworkflow_execution() is not None
            calls.append(kwargs["job_id"])
            raise OwnPipelineLineageError("authored canonical provider failure")
        rejected_provider.calls = calls
        monkeypatch.setattr(worker, "run_ocr_pipeline", rejected_provider)
    else:
        authored_pipeline_provider(monkeypatch, table_rows=[["10/07", "昼", "Menu A", "2"]])


def owned_uploaded_original(store, payload, monkeypatch, *, pending=False, failed=False, uploaded=True):
    if uploaded:
        add_upload(store, payload)
        original_payload = {**uploads.build_ingest_payload(uploads.get_uploaded_pdf("own-upload")),
                            "unknown": payload["unknown"]}
    else:
        original_payload = deepcopy(payload)
        # Physical PDF receipt remains required without an uploaded transport ID.
        add_upload(store, payload)
    receipt = ingest.create_ingest_receipt(original_payload)
    prepare_original(original_payload, monkeypatch, failed=failed, attach_upload=uploaded)
    if pending:
        from src.services import ocr_job_service as jobs
        def waiting_provider(**kwargs):
            input_reference = f"gs://authored-component/input/{kwargs['job_id']}.pdf"
            output_reference = f"gs://authored-component/output/{kwargs['job_id']}.json"
            assert jobs.record_own_pipeline_delivery(kwargs["job_id"],
                selected_input_reference=kwargs["input_reference"], input_reference=input_reference,
                output_reference=output_reference, generation="1",
                input_sha256=sha256(kwargs["pdf_bytes"]).hexdigest(), wait_timeout_seconds=15)
            return {"status": "pending", "input_reference": input_reference, "output_reference": output_reference}
        monkeypatch.setattr(worker, "run_ocr_pipeline", waiting_provider)
    return receipt


def original_ocr(store, receipt):
    with store() as session:
        row = session.query(OcrJob).filter(OcrJob.metrics["ingest_lease"]["id"].as_string() == receipt["id"]).one()
        return row.id, deepcopy(row.metrics)


@pytest.mark.parametrize("state", ["processing", "done", "error"])
def test_duplicate_does_not_replace_persisted_input(store, payload, state, monkeypatch):
    if state != "processing":
        add_upload(store, payload)
        payload = {**uploads.build_ingest_payload(uploads.get_uploaded_pdf("own-upload")), "unknown": payload["unknown"]}
    receipt = ingest.create_ingest_receipt(payload)
    if state == "processing":
        claim(receipt)
    else:
        prepare_original(payload, monkeypatch, failed=state == "error")
        assert process_original(receipt)["result"] == ("business_done" if state == "done" else "business_failed")
    changed = {**payload, "pdf_uri": "fixture://different-source", "unknown": {"zero": 7}}
    assert not ingest.create_ingest_receipt(changed)["enqueued"]
    with store() as session:
        row = session.get(IngestJob, receipt["id"])
        assert row.payload == payload
        assert row.input_digest == ingest.payload_digest(payload)
        assert row.status == state


@pytest.mark.parametrize("restart", ["restart", "force"])
def test_old_attempt_cannot_finish_fail_or_capture_new_attempt(store, payload, restart, monkeypatch):
    first = owned_uploaded_original(store, payload, monkeypatch, pending=True, uploaded=False)
    result = process_original(first)
    with store() as session:
        actual = session.query(OcrJob).one()
        diagnostic = (actual.status, actual.error_message, actual.metrics)
    assert result["result"] == "durable_handoff", repr(diagnostic)
    job_id, before = original_ocr(store, first)
    old = before["ingest_lease"]
    original_payload = ingest.get_ingest_payload(first["id"])
    if restart == "restart":
        assert ingest.restart_ingest_job(first["id"])
        current = ingest.get_ingest_job_snapshot(first["id"])
    else:
        current = ingest.create_ingest_receipt(original_payload, force=True)
    selected_generation = current["generation"]
    assert selected_generation != first["generation"]
    assert current["input_digest"] == first["input_digest"]
    advances = []
    actual_advance = guard.advance_nonworkflow_order_input

    def verified_advance(session, boundary, order):
        original_start = deepcopy(boundary["start_orders"])
        original_pdf = deepcopy(boundary["selected_pdf_receipt"])
        actual_advance(session, boundary, order)
        stored = session.connection().execute(select(OcrJob.order_id, OcrJob.metrics).where(
            OcrJob.id == boundary["job_id"])).one()
        assert stored.order_id == order.id == boundary["job_order_id"]
        assert guard.snapshots_equal(stored.metrics["nonworkflow_execution"], boundary)
        assert guard.snapshots_equal(boundary["start_orders"], original_start)
        assert guard.snapshots_equal(boundary["selected_pdf_receipt"], original_pdf)
        advances.append(order.id)

    monkeypatch.setattr(order_service, "advance_nonworkflow_order_input", verified_advance)
    authored_pipeline_provider(monkeypatch, table_rows=[["10/07", "昼", "Menu A", "2"]])
    assert process_original(current)["result"] == "business_done"
    assert advances
    assert ingest.get_ingest_job_snapshot(first["id"])["generation"] == selected_generation
    current_job_id, prepared = original_ocr(store, current)
    assert current_job_id == job_id
    new = prepared["ingest_lease"]
    assert new["claim_id"] != old["claim_id"]
    assert new["attempts"] == old["attempts"] + 1
    assert not ingest.complete_ingest_job(first["id"], lease=old)
    assert not ingest.fail_ingest_job(first["id"], "old error", lease=old)
    assert not ingest.fail_ingest_ocr_capture(old, job_id, ValueError("old capture error"))
    with ingest.ingest_execution_context(old), pytest.raises(ValueError, match="ingest_attempt_changed"):
        guard.capture_nonworkflow_execution(job_id, order_id="not-read", input_reference=payload["pdf_uri"])
    with store() as session:
        assert session.get(OcrJob, job_id).metrics == prepared
        assert session.get(IngestJob, first["id"]).claim_id == new["claim_id"]
    assert ingest.get_ingest_job_snapshot(first["id"])["status"] == "done"
    assert prepared["first_pass_terminal_receipt"]["execution_id"] == prepared["nonworkflow_execution"]["execution_id"]


def test_hard_failure_is_terminal_until_explicit_restart(store, payload, monkeypatch):
    receipt = ingest.create_ingest_receipt(payload)
    prepare_original(payload, monkeypatch, failed=True)
    assert process_original(receipt)["result"] == "business_failed"
    lease = ingest.get_ingest_job_snapshot(receipt["id"])
    assert receipt["id"] not in ingest.list_pending_jobs()
    assert not claim(receipt)
    assert not ingest.create_ingest_receipt(payload)["enqueued"]
    assert ingest.restart_ingest_job(receipt["id"])
    restarted = ingest.get_ingest_job_snapshot(receipt["id"])
    assert restarted["generation"] != lease["generation"]
    delivery_id, generation = delivery.delivery_for_job(restarted["id"], restarted["generation"], restarted["input_digest"])
    retried = delivery.claim_delivery(delivery_id, generation)["lease"]
    assert retried["input_digest"] == lease["input_digest"]
    assert retried["claim_id"] != lease["claim_id"]


def test_worker_pending_is_not_a_completed_result(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, pending=True)
    with pytest.raises(ValueError, match="ingest_delivery_owned_runner_required"):
        worker.process_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"])
    assert process_original(receipt)["result"] == "durable_handoff"
    row = ingest.get_ingest_job_snapshot(receipt["id"])
    assert row["status"] == "processing"
    assert row["finished_at"] is None


@pytest.mark.parametrize("restart", ["restart", "force"])
def test_manual_new_generation_has_own_recovery_budget_without_rewinding_history(store, payload, monkeypatch, restart):
    monkeypatch.setenv("INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", "3")
    receipt = owned_uploaded_original(store, payload, monkeypatch, failed=True, uploaded=False)
    original_payload = ingest.get_ingest_payload(receipt["id"])
    for index in range(4):
        selected_generation = receipt["generation"]
        assert process_original(receipt)["result"] == "business_failed"
        settled = ingest.get_ingest_job_snapshot(receipt["id"])
        assert settled["generation"] == selected_generation
        assert settled["generation_attempts"] == 1
        job_id, metrics = original_ocr(store, receipt)
        assert metrics["nonworkflow_execution"]["selected_pdf_receipt"]["sha256"] == payload["content_sha256"]
        assert len(worker.run_ocr_pipeline.calls) == index + 1
        assert settled["input_digest"] == ingest.payload_digest(original_payload)
        if restart == "restart":
            assert ingest.restart_ingest_job(receipt["id"])
            receipt = ingest.get_ingest_job_snapshot(receipt["id"])
        else:
            receipt = ingest.create_ingest_receipt(original_payload, force=True)
        assert receipt["generation"] != selected_generation
    own = ingest.get_ingest_job_snapshot(receipt["id"])
    assert own["attempts"] == 4
    assert own["generation_attempts"] == 0
    assert receipt["id"] in ingest.list_pending_jobs()
    delivery_id, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    claimed = delivery.claim_delivery(delivery_id, generation)["lease"]
    assert claimed["attempts"] == 5
    with store() as session:
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
    assert ingest.reset_stale_processing() == [receipt["id"]]
    recovered = ingest.get_ingest_job_snapshot(receipt["id"])
    assert recovered["generation_attempts"] == 1
    assert recovered["generation"] == claimed["generation"]
    assert recovered["claim_id"] == claimed["claim_id"]
    assert process_original(receipt)["result"] == "business_failed"
    assert ingest.get_ingest_job_snapshot(receipt["id"])["attempts"] == 5
    assert ingest.get_ingest_job_snapshot(receipt["id"])["generation_attempts"] == 1


@pytest.mark.parametrize("entrance", ["direct_pending", "direct_stale", "stale_redispatch", "reset_route"])
def test_exhausted_generation_cannot_execute_through_actual_claim_boundaries(store, payload, monkeypatch, entrance):
    monkeypatch.setenv("INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", "3")
    receipt = ingest.create_ingest_receipt(payload)
    for attempt in range(1, 4):
        lease = claim(receipt)
        assert lease["attempts"] == attempt
        with store() as session:
            session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
    exhausted = ingest.get_ingest_job_snapshot(receipt["id"])
    assert exhausted["generation_attempts"] == 3
    assert receipt["id"] not in ingest.list_pending_jobs()

    def forbid_execution(**kwargs):
        pytest.fail("Exhausted generation must stop before OCR execution")

    monkeypatch.setattr(worker, "_process_ingest_inline", forbid_execution)
    if entrance == "direct_pending":
        assert ingest.reset_stale_processing() == [receipt["id"]]
        receipt = ingest.get_ingest_job_snapshot(receipt["id"])
        assert receipt["generation_attempts"] == 3
        assert process_original(receipt)["result"] == "business_failed"
    elif entrance == "direct_stale":
        assert process_original(receipt)["result"] == "business_failed"
    elif entrance == "stale_redispatch":
        with pytest.raises(ValueError, match="ingest_delivery_owned_runner_required"):
            worker.process_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"])
        assert process_original(receipt)["result"] == "business_failed"
    else:
        def dispatch_original(job_id, *, generation, input_digest):
            assert process_original({"id": job_id, "generation": generation, "input_digest": input_digest})["result"] == "business_failed"

        monkeypatch.setattr(ingest_api, "enqueue_ingest_job_async", dispatch_original)
        assert ingest_api.reset_stale(limit=10, minutes=30) == {"reset": 1}
        assert claim(receipt) is None
    after = ingest.get_ingest_job_snapshot(receipt["id"])
    assert after["attempts"] == exhausted["attempts"]
    assert after["generation_attempts"] == 3
    assert after["input_digest"] == exhausted["input_digest"]
    assert after["finished_at"] is not None
    assert after["status"] == "error"
    assert after["failure_kind"] == "budget_exhausted"
    assert after["claim_id"] == exhausted["claim_id"]
    assert after["generation"] == exhausted["generation"]
    assert receipt["id"] not in ingest.list_pending_jobs()
    assert claim(after) is None
    with store() as session:
        assert session.get(IngestJob, receipt["id"]).payload == payload


def test_uploaded_exhausted_generation_stops_after_expiry_without_more_claims(store, payload, monkeypatch):
    monkeypatch.setenv("INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", "1")
    add_upload(store, payload)
    uploaded = uploads.claim_uploaded_pdf("own-upload", worker_instance="original-worker")
    receipt = ingest.create_ingest_receipt(uploads.build_ingest_payload(uploaded))
    original = claim(receipt, uploaded_lease=uploaded)
    with store() as session:
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
        session.get(UploadedPdf, "own-upload").lease_expires_at = datetime.utcnow() - timedelta(seconds=1)

    def forbid_execution(**kwargs):
        pytest.fail("Budget refusal must not recognize or create a manual generation")

    monkeypatch.setattr(worker, "_process_ingest_inline", forbid_execution)
    assert process_original(receipt)["result"] == "business_failed"
    stopped = uploads.get_uploaded_pdf("own-upload")
    assert stopped["status"] == "failed"
    assert stopped["last_error_code"] == "ingest_recovery_budget_exhausted"
    assert stopped["next_retry_at"] is None
    assert stopped["attempt_count"] == uploaded["attempt_count"] + 1
    exhausted = ingest.get_ingest_job_snapshot(receipt["id"])
    assert exhausted["status"] == "error"
    assert exhausted["failure_kind"] == "budget_exhausted"
    assert exhausted["attempts"] == original["attempts"]
    assert exhausted["generation_attempts"] == 1
    assert not ingest.complete_ingest_job(receipt["id"], lease=original)
    assert uploads.schedule_uploaded_pdf_retry("own-upload", lease=uploaded, worker_instance="original-worker",
        error_code="old", error_message="old", terminal=True) is None
    with store() as session:
        session.get(UploadedPdf, "own-upload").lease_expires_at = datetime.utcnow() - timedelta(hours=2)
    assert "own-upload" not in uploads.list_ready_uploaded_pdf_ids()
    monkeypatch.setattr(worker, "_submit_uploaded_pdf_job", lambda *args: pytest.fail("Stopped upload must not dispatch"))
    monkeypatch.setattr(worker, "run_ocr_job_recovery_once", lambda **kwargs: 0)
    assert worker.run_uploaded_pdf_recovery_once(limit=10) == 0
    assert process_original(receipt)["duplicate"] is True
    assert uploads.get_uploaded_pdf("own-upload")["attempt_count"] == stopped["attempt_count"]
    assert ingest.get_ingest_job_snapshot(receipt["id"])["generation"] == receipt["generation"]


def test_actual_worker_capture_failure_records_only_own_ocr_job(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch)
    delivery_id, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    owned = delivery.claim_delivery(delivery_id, generation)
    job_id = f"OCR-{receipt['id']}"
    with delivery.claim_context(owned), ingest.ingest_execution_context(owned["lease"]):
        ingest.prepare_ingest_ocr_job(owned["lease"], job_id, parse_ingest_payload(ingest.get_ingest_payload(receipt["id"])))
        with pytest.raises(ValueError, match="ocr_order_input_missing") as failure:
            guard.capture_nonworkflow_execution(job_id, order_id="deliberately-missing-order", input_reference=payload["pdf_uri"])
        assert ingest.fail_ingest_ocr_capture(owned["lease"], job_id, failure.value)
    assert ingest.get_ingest_job_snapshot(receipt["id"])["status"] == "error"
    with store() as session:
        job = session.get(OcrJob, f"OCR-{receipt['id']}")
        assert job.status == "failed"
        assert job.error_message == "ocr_order_input_missing"
    assert ingest.restart_ingest_job(receipt["id"])
    uploads.requeue_uploaded_pdf("own-upload")
    current = ingest.get_ingest_job_snapshot(receipt["id"])
    assert process_original(current)["result"] == "business_done"


def add_upload(store, payload):
    with store() as session:
        row = UploadedPdf(id="own-upload", message_id=payload["message_id"], content_sha256=payload["content_sha256"],
            storage_uri=payload["pdf_uri"], received_at=datetime.fromisoformat(payload["received_at"]),
            facility_hint=payload["facility_hint"], week_hint=payload["week_hint"], source_kind="manual_upload",
            original_filename="authored-source.pdf", status="pending", current_stage="uploaded",
            attempt_count=0, max_attempts=5, skip_ocr=False)
        session.add(row)


def test_uploaded_old_same_worker_lease_cannot_fail_complete_retry_or_link(store, payload):
    add_upload(store, payload)
    ingest.create_ingest_receipt(uploads.build_ingest_payload(uploads.get_uploaded_pdf("own-upload")))
    old = uploads.claim_uploaded_pdf("own-upload", worker_instance="same-worker")
    uploads.requeue_uploaded_pdf("own-upload")
    current = uploads.claim_uploaded_pdf("own-upload", worker_instance="same-worker")
    assert current["attempt_count"] == old["attempt_count"] + 1
    assert current["lease_token"] != old["lease_token"]
    assert uploads.mark_uploaded_pdf_completed("own-upload", lease=old) is None
    assert uploads.schedule_uploaded_pdf_retry("own-upload", lease=old, worker_instance="same-worker",
        error_code="old", error_message="old", terminal=True) is None
    assert uploads.refresh_uploaded_pdf_links("own-upload", lease=old, order_id="other-order", document_id="other-document") is None
    assert uploads.get_uploaded_pdf("own-upload")["lease_token"] == current["lease_token"]
    assert uploads.get_uploaded_pdf("own-upload")["status"] == "processing"
    assert uploads.mark_uploaded_pdf_completed("own-upload", lease=current) is None
    assert uploads.get_uploaded_pdf("own-upload")["status"] == "processing"


def test_uploaded_actual_wrapper_invalid_source_is_not_retry_wait(store, payload, monkeypatch):
    add_upload(store, payload)
    with store() as session:
        session.get(UploadedPdf, "own-upload").week_hint = None
    before = uploads.get_uploaded_pdf("own-upload")
    monkeypatch.setattr(worker, "run_ocr_pipeline", lambda **kwargs: pytest.fail("Unknown date must stop before provider"))
    with pytest.raises(ValueError, match="week_hint_required"):
        uploads.build_ingest_payload(before)
    with pytest.raises(ValueError, match="ingest_delivery_original_missing"):
        worker.process_uploaded_pdf_job("own-upload")
    row = uploads.get_uploaded_pdf("own-upload")
    assert row == before
    assert row["next_retry_at"] is None
    assert ingest.get_ingest_job_snapshot(payload["message_id"]) is None
    with store() as session:
        session.get(UploadedPdf, "own-upload").week_hint = payload["week_hint"]
    original = uploads.build_ingest_payload(uploads.get_uploaded_pdf("own-upload"))
    receipt = ingest.create_ingest_receipt(original)
    prepare_original(original, monkeypatch)
    assert process_original(receipt)["result"] == "business_done"


def test_uploaded_actual_business_failure_stops_both_recovery_lanes(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, failed=True)
    assert process_original(receipt)["result"] == "business_failed"
    first = uploads.get_uploaded_pdf("own-upload")
    assert first["status"] == "failed"
    assert first["next_retry_at"] is None
    assert ingest.get_ingest_job_snapshot(payload["message_id"])["status"] == "error"
    assert payload["message_id"] not in ingest.list_pending_jobs()
    assert "own-upload" not in uploads.list_ready_uploaded_pdf_ids()
    assert process_original(receipt)["duplicate"] is True
    assert uploads.get_uploaded_pdf("own-upload")["attempt_count"] == first["attempt_count"]


def test_incomplete_upload_lease_cannot_establish_source_authority(store, payload):
    add_upload(store, payload)
    own = uploads.claim_uploaded_pdf("own-upload", worker_instance="own-worker")
    incomplete = {key: value for key, value in own.items() if key != "page_count"}
    assert uploads.schedule_uploaded_pdf_retry("own-upload", lease=incomplete, worker_instance="own-worker",
        error_code="missing source", error_message="missing source", terminal=True) is None
    current = uploads.get_uploaded_pdf("own-upload")
    assert current["status"] == "processing"
    assert current["lease_token"] == own["lease_token"]


def test_interrupted_recovery_keeps_original_capture_and_blocks_old_claim(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, pending=True)
    acknowledge = delivery.acknowledge_claim
    def interrupt_ack(claim):
        raise RuntimeError("authored acknowledgement interruption")
    monkeypatch.setattr(delivery, "acknowledge_claim", interrupt_ack)
    with pytest.raises(RuntimeError, match="authored acknowledgement interruption"):
        process_original(receipt)
    job_id, metrics = original_ocr(store, receipt)
    old = metrics["ingest_lease"]
    before = ingest.get_ingest_job_snapshot(receipt["id"])
    boundary = deepcopy(metrics["nonworkflow_execution"])
    with store() as session:
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
    monkeypatch.setattr(delivery, "acknowledge_claim", acknowledge)
    delivery_id, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    assert delivery.settle_saved(delivery_id, generation)["action"] == "durable_handoff"
    assert ingest.reset_stale_processing() == []
    current = ingest.get_ingest_job_snapshot(receipt["id"])
    assert current["generation"] == old["generation"]
    assert current["input_digest"] == old["input_digest"]
    assert current["claim_id"] == old["claim_id"]
    assert current["generation_attempts"] == before["generation_attempts"]
    assert original_ocr(store, receipt)[1]["nonworkflow_execution"] == boundary
    assert ingest.restart_ingest_job(receipt["id"])
    uploads.requeue_uploaded_pdf("own-upload")
    restarted = ingest.get_ingest_job_snapshot(receipt["id"])
    assert restarted["generation"] != old["generation"]
    assert not ingest.complete_ingest_job(receipt["id"], lease=old)
    assert process_original(restarted)["result"] == "durable_handoff"


def test_waiting_exact_async_delivery_does_not_become_stale_ingest_retry(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, pending=True)
    assert process_original(receipt)["result"] == "durable_handoff"
    job_id, metrics = original_ocr(store, receipt)
    lease = metrics["nonworkflow_execution"]["ingest_lease"]
    with store() as session:
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
        assert session.get(OcrJob, job_id).status == "awaiting_output"
    assert ingest.reset_stale_processing() == []
    assert receipt["id"] not in ingest.list_pending_jobs()
    assert claim(receipt) is None
    after = ingest.get_ingest_job_snapshot(receipt["id"])
    assert after["claim_id"] == lease["claim_id"]
    assert after["generation"] == receipt["generation"]


@pytest.mark.parametrize("status", ["done", "failed"])
def test_closed_session_terminal_producer_uses_original_receipt(store, payload, status, monkeypatch):
    created = owned_uploaded_original(store, payload, monkeypatch, failed=status == "failed")
    assert process_original(created)["result"] == ("business_done" if status == "done" else "business_failed")
    job_id, metrics = original_ocr(store, created)
    execution = metrics["nonworkflow_execution"]
    terminal = metrics.get("first_pass_terminal_receipt") or metrics.get("non_ocr_terminal_receipt")
    assert terminal["status"] == status
    assert terminal["job_id"] == job_id
    assert terminal["execution_id"] == execution["execution_id"]
    assert execution["ingest_lease"]["payload"]["unknown"] == {"zero": 0, "null": None}
    assert ingest.get_ingest_job_snapshot(created["id"])["status"] == ("done" if status == "done" else "error")
    assert ingest.settle_ingest_ocr_terminal(execution) is None
    assert process_original(created)["duplicate"] is True


def test_concurrent_duplicate_claim_has_one_original_execution_lease(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch)
    def execute(_):
        try:
            return process_original(receipt)
        except ValueError as exc:
            assert str(exc) == "ingest_delivery_execution_busy"
            return {"busy": True}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    accepted = [result for result in results if result.get("result") == "business_done"]
    assert len(accepted) == 1
    assert ingest.get_ingest_job_snapshot(receipt["id"])["attempts"] == 1
    assert ingest.get_ingest_job_snapshot(receipt["id"])["status"] == "done"
    _, metrics = original_ocr(store, receipt)
    assert metrics["first_pass_terminal_receipt"]["execution_id"] == metrics["nonworkflow_execution"]["execution_id"]
    assert claim(receipt) is None


def test_new_ocr_execution_is_not_adopted_as_old_terminal_result(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, pending=True)
    assert process_original(receipt)["result"] == "durable_handoff"
    job_id, old_metrics = original_ocr(store, receipt)
    original = old_metrics["nonworkflow_execution"]
    assert ingest.restart_ingest_job(receipt["id"])
    uploads.requeue_uploaded_pdf("own-upload")
    current = ingest.get_ingest_job_snapshot(receipt["id"])
    authored_pipeline_provider(monkeypatch, table_rows=[["10/07", "昼", "Menu A", "2"]])
    assert process_original(current)["result"] == "business_done"
    current_id, metrics = original_ocr(store, current)
    assert current_id == job_id
    assert metrics["nonworkflow_execution"]["execution_id"] != original["execution_id"]
    before = ingest.get_ingest_job_snapshot(receipt["id"])
    assert ingest.settle_ingest_ocr_terminal(original) is None
    assert ingest.get_ingest_job_snapshot(receipt["id"]) == before
    assert original_ocr(store, current)[1] == metrics


def test_business_terminal_does_not_consume_ocr_recovery_slots(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, failed=True)
    assert process_original(receipt)["result"] == "business_failed"
    assert ingest.list_ocr_recovery_candidates(1) == []


def test_worker_old_capture_failure_does_not_fail_new_prepared_ocr(store, payload, monkeypatch):
    receipt = owned_uploaded_original(store, payload, monkeypatch, pending=True)
    assert process_original(receipt)["result"] == "durable_handoff"
    job_id, old_metrics = original_ocr(store, receipt)
    assert ingest.restart_ingest_job(receipt["id"])
    uploads.requeue_uploaded_pdf("own-upload")
    current = ingest.get_ingest_job_snapshot(receipt["id"])
    assert process_original(current)["result"] == "durable_handoff"
    _, prepared_new = original_ocr(store, current)
    assert not ingest.fail_ingest_ocr_capture(old_metrics["ingest_lease"], job_id, ValueError("old capture failed"))
    with store() as session:
        job = session.get(OcrJob, job_id)
        assert job.status == "awaiting_output"
        assert job.metrics == prepared_new
    assert ingest.get_ingest_job_snapshot(receipt["id"])["status"] == "processing"


def test_actual_upload_wrapper_reconciles_terminal_ack_gap_without_recognition(store, payload, monkeypatch):
    created = owned_uploaded_original(store, payload, monkeypatch)
    acknowledge = delivery.acknowledge_claim
    def interrupt_ack(claim):
        raise RuntimeError("authored terminal acknowledgement interruption")
    monkeypatch.setattr(delivery, "acknowledge_claim", interrupt_ack)
    with pytest.raises(RuntimeError, match="authored terminal acknowledgement interruption"):
        process_original(created)
    _, metrics = original_ocr(store, created)
    lease = metrics["ingest_lease"]
    before = uploads.get_uploaded_pdf("own-upload")
    assert before["status"] == "completed"
    assert metrics["first_pass_terminal_receipt"]["execution_id"] == metrics["nonworkflow_execution"]["execution_id"]

    def forbid_recognition(**kwargs):
        pytest.fail("Terminal result recovery must not recognize again")

    monkeypatch.setattr(worker, "_process_ingest_inline", forbid_recognition)
    monkeypatch.setattr(delivery, "acknowledge_claim", acknowledge)
    delivery_id, generation = delivery.delivery_for_job(created["id"], created["generation"], created["input_digest"])
    assert delivery.settle_saved(delivery_id, generation)["action"] == "business_done"
    result = uploads.get_uploaded_pdf("own-upload")
    assert result["status"] == "completed"
    assert result["current_order_id"] == before["current_order_id"]
    assert result["current_document_id"] == before["current_document_id"]
    assert result["storage_uri"] == before["storage_uri"]
    assert result["attempt_count"] == before["attempt_count"]
    assert original_ocr(store, created)[1] == metrics
    assert ingest.get_ingest_job_snapshot(created["id"])["attempts"] == lease["attempts"]
