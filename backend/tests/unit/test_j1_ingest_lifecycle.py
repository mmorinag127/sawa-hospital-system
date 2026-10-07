"""Source-defined lifecycle behavior cases; no recognizer or transport execution."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import models
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.services import ingest_job_service as ingest
from src.services import uploaded_pdf_service as uploads
from src.services import workflow_attempt_guard_service as guard
from src.workers import ingest_worker as worker
from src.workers.ingest_mail_adapter import parse_ingest_payload
from src.api import ingest as ingest_api


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.sqlite'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True, autoflush=False)

    @contextmanager
    def scope():
        with factory() as session:
            with session.begin():
                yield session

    for module in (ingest, uploads, guard):
        monkeypatch.setattr(module, "session_scope", scope)
    yield scope
    engine.dispose()


@pytest.fixture
def payload():
    return {"message_id": "authored-source", "pdf_uri": "fixture://authored-source",
            "received_at": "2026-10-07T00:00:00", "facility_hint": "authored-facility",
            "week_hint": "2026-10@2026-10-05~2026-10-11", "skip_ocr": False,
            "content_sha256": sha256(b"unit metadata source, not OCR evidence").hexdigest(),
            "unknown": {"zero": 0, "null": None}}


def claim(receipt, **kwargs):
    return ingest.claim_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"], **kwargs)


@pytest.mark.parametrize("state", ["processing", "done", "error"])
def test_duplicate_does_not_replace_persisted_input(store, payload, state):
    receipt = ingest.create_ingest_receipt(payload)
    lease = claim(receipt)
    if state == "done":
        assert ingest.complete_ingest_job(receipt["id"], lease=lease)
    elif state == "error":
        assert ingest.fail_ingest_job(receipt["id"], "business failure", lease=lease)
    changed = {**payload, "pdf_uri": "fixture://different-source", "unknown": {"zero": 7}}
    assert not ingest.create_ingest_receipt(changed)["enqueued"]
    with store() as session:
        row = session.get(IngestJob, receipt["id"])
        assert row.payload == payload
        assert row.input_digest == ingest.payload_digest(payload)
        assert row.status == state


@pytest.mark.parametrize("restart", ["restart", "force"])
def test_old_attempt_cannot_finish_fail_or_capture_new_attempt(store, payload, restart):
    first = ingest.create_ingest_receipt(payload)
    old = claim(first)
    parsed = parse_ingest_payload(payload)
    ingest.prepare_ingest_ocr_job(old, "own-ocr", parsed)
    if restart == "restart":
        assert ingest.restart_ingest_job(first["id"])
        current = ingest.get_ingest_job_snapshot(first["id"])
    else:
        current = ingest.create_ingest_receipt({**payload, "unknown": {"zero": 0, "new": None}}, force=True)
    new = claim(current)
    prepared = ingest.prepare_ingest_ocr_job(new, "own-ocr", parsed)
    assert new["claim_id"] != old["claim_id"]
    assert new["attempts"] == old["attempts"] + 1
    assert not ingest.complete_ingest_job(first["id"], lease=old)
    assert not ingest.fail_ingest_job(first["id"], "old error", lease=old)
    assert not ingest.fail_ingest_ocr_capture(old, "own-ocr", "old capture error")
    with ingest.ingest_execution_context(old), pytest.raises(ValueError, match="ingest_attempt_changed"):
        guard.capture_nonworkflow_execution("own-ocr", order_id="not-read", input_reference=payload["pdf_uri"])
    with store() as session:
        assert session.get(OcrJob, "own-ocr").metrics == prepared["metrics"]
        assert session.get(IngestJob, first["id"]).claim_id == new["claim_id"]
    assert ingest.complete_ingest_job(first["id"], lease=new)


def test_hard_failure_is_terminal_until_explicit_restart(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    lease = claim(receipt)
    assert ingest.fail_ingest_attempt(lease, "unresolved canonical source")
    assert receipt["id"] not in ingest.list_pending_jobs()
    assert not claim(receipt)
    assert not ingest.create_ingest_receipt(payload)["enqueued"]
    assert ingest.restart_ingest_job(receipt["id"])
    retried = claim(ingest.get_ingest_job_snapshot(receipt["id"]))
    assert retried["input_digest"] == lease["input_digest"]
    assert retried["claim_id"] != lease["claim_id"]


def test_worker_pending_is_not_a_completed_result(store, payload, monkeypatch):
    receipt = ingest.create_ingest_receipt(payload)
    monkeypatch.setattr(worker, "_process_ingest_inline", lambda **kwargs: None)
    worker.process_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"])
    row = ingest.get_ingest_job_snapshot(receipt["id"])
    assert row["status"] == "processing"
    assert row["finished_at"] is None


@pytest.mark.parametrize("restart", ["restart", "force"])
def test_manual_new_generation_has_own_recovery_budget_without_rewinding_history(store, payload, monkeypatch, restart):
    monkeypatch.setenv("INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", "3")
    receipt = ingest.create_ingest_receipt(payload)
    for _ in range(4):
        lease = claim(receipt)
        assert ingest.fail_ingest_job(receipt["id"], "business failure", lease=lease)
        if restart == "restart":
            assert ingest.restart_ingest_job(receipt["id"])
            receipt = ingest.get_ingest_job_snapshot(receipt["id"])
        else:
            receipt = ingest.create_ingest_receipt(payload, force=True)
    own = ingest.get_ingest_job_snapshot(receipt["id"])
    assert own["attempts"] == 4
    assert own["generation_attempts"] == 0
    assert receipt["id"] in ingest.list_pending_jobs()
    claimed = claim(receipt)
    assert claimed["attempts"] == 5
    with store() as session:
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
    assert ingest.reset_stale_processing() == [receipt["id"]]
    recovered = ingest.get_ingest_job_snapshot(receipt["id"])
    assert recovered["generation_attempts"] == 1
    assert claim(recovered)["attempts"] == 6
    assert ingest.get_ingest_job_snapshot(receipt["id"])["generation_attempts"] == 2


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
        assert claim(receipt)["claim_refusal"] == "budget_exhausted"
    elif entrance == "direct_stale":
        assert claim(receipt)["claim_refusal"] == "budget_exhausted"
    elif entrance == "stale_redispatch":
        assert worker.process_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"])["claim_refusal"] == "budget_exhausted"
    else:
        def dispatch_original(job_id, *, generation, input_digest):
            worker.process_ingest_job(job_id, generation=generation, input_digest=input_digest)

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
    assert after["claim_id"] == (None if entrance in {"direct_pending", "reset_route"} else exhausted["claim_id"])
    if entrance in {"direct_stale", "stale_redispatch"}:
        assert after["generation"] == exhausted["generation"]
    else:
        assert after["generation"] != exhausted["generation"]
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
    worker.process_uploaded_pdf_job("own-upload")
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
    worker.process_uploaded_pdf_job("own-upload")
    assert uploads.get_uploaded_pdf("own-upload")["attempt_count"] == stopped["attempt_count"]
    assert ingest.get_ingest_job_snapshot(receipt["id"])["generation"] == receipt["generation"]


def test_actual_worker_capture_failure_records_only_own_ocr_job(store, payload, monkeypatch):
    receipt = ingest.create_ingest_receipt(payload)
    monkeypatch.setattr(worker, "create_order_from_ingest", lambda *args, **kwargs: {"id": "deliberately-missing-order",
        "ingest_registration_receipt": {"order_id": "deliberately-missing-order", "document_id": "deliberately-missing-document"}})
    worker.process_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"])
    assert ingest.get_ingest_job_snapshot(receipt["id"])["status"] == "error"
    with store() as session:
        job = session.get(OcrJob, f"OCR-{receipt['id']}")
        assert job.status == "failed"
        assert job.error_message == "ocr_order_input_missing"


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


def test_uploaded_actual_wrapper_invalid_source_is_not_retry_wait(store, payload):
    add_upload(store, payload)
    with store() as session:
        session.get(UploadedPdf, "own-upload").week_hint = None
    worker.process_uploaded_pdf_job("own-upload")
    row = uploads.get_uploaded_pdf("own-upload")
    assert row["status"] == "failed"
    assert row["next_retry_at"] is None
    assert "own-upload" not in uploads.list_ready_uploaded_pdf_ids()


def test_uploaded_actual_business_failure_stops_both_recovery_lanes(store, payload, monkeypatch):
    add_upload(store, payload)

    def reject_original_registration(*args, **kwargs):
        raise ValueError("canonical source unresolved")

    monkeypatch.setattr(worker, "create_order_from_ingest", reject_original_registration)
    worker.process_uploaded_pdf_job("own-upload")
    first = uploads.get_uploaded_pdf("own-upload")
    assert first["status"] == "failed"
    assert first["next_retry_at"] is None
    assert ingest.get_ingest_job_snapshot(payload["message_id"])["status"] == "error"
    assert payload["message_id"] not in ingest.list_pending_jobs()
    assert "own-upload" not in uploads.list_ready_uploaded_pdf_ids()
    worker.process_uploaded_pdf_job("own-upload")
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


def test_interrupted_recovery_keeps_original_capture_and_blocks_old_claim(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    old = claim(receipt)
    parsed = parse_ingest_payload(payload)
    ingest.prepare_ingest_ocr_job(old, "own-ocr", parsed)
    boundary = {"job_id": "own-ocr", "execution_id": "authored-capture", "order_id": "authored-order",
                "input_reference": payload["pdf_uri"], "ingest_lease": old,
                "start_orders": {"authored-order": {"raw": {"zero": 0, "null": None}}}}
    with store() as session:
        session.get(OcrJob, "own-ocr").metrics = {"nonworkflow_execution": boundary, "ingest_lease": old}
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
    assert ingest.reset_stale_processing() == [receipt["id"]]
    current = claim(ingest.get_ingest_job_snapshot(receipt["id"]))
    prepared = ingest.prepare_ingest_ocr_job(current, "own-ocr", parsed)
    expected = deepcopy(boundary)
    expected["ingest_lease"] = current
    assert prepared["metrics"]["nonworkflow_execution"] == expected
    assert not ingest.complete_ingest_job(receipt["id"], lease=old)


def test_waiting_exact_async_delivery_does_not_become_stale_ingest_retry(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    lease = claim(receipt)
    with store() as session:
        session.get(IngestJob, receipt["id"]).started_at = datetime.utcnow() - timedelta(hours=2)
        session.add(OcrJob(id="own-ocr", status="awaiting_output", input_reference=payload["pdf_uri"],
            metrics={"nonworkflow_execution": {"ingest_lease": lease}}))
    assert ingest.reset_stale_processing() == []
    assert receipt["id"] not in ingest.list_pending_jobs()
    assert claim(receipt) is None


@pytest.mark.parametrize("status", ["done", "failed"])
def test_closed_session_terminal_producer_uses_original_receipt(store, payload, status):
    created = ingest.create_ingest_receipt(payload)
    lease = claim(created)
    execution = {"job_id": "own-ocr", "execution_id": "own-execution", "input_reference": payload["pdf_uri"], "ingest_lease": lease}
    receipt = {"job_id": "own-ocr", "execution_id": "own-execution", "status": status}
    with store() as session:
        session.add(OcrJob(id="own-ocr", status=status, input_reference=payload["pdf_uri"],
            metrics={"nonworkflow_execution": execution, "first_pass_terminal_receipt": receipt}))
    settled = ingest.settle_ingest_ocr_terminal(execution)
    assert settled["status"] == status
    assert settled["lease"]["payload"]["unknown"] == {"zero": 0, "null": None}
    assert ingest.get_ingest_job_snapshot(created["id"])["status"] == ("done" if status == "done" else "error")
    assert ingest.settle_ingest_ocr_terminal(execution) is None


def test_concurrent_duplicate_claim_has_one_original_execution_lease(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(lambda _: claim(receipt), range(2)))
    accepted = [lease for lease in leases if lease is not None]
    assert len(accepted) == 1
    assert ingest.get_ingest_job_snapshot(receipt["id"])["attempts"] == 1
    assert ingest.complete_ingest_job(receipt["id"], lease=accepted[0])
    assert claim(receipt) is None


def test_new_ocr_execution_is_not_adopted_as_old_terminal_result(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    lease = claim(receipt)
    original = {"job_id": "reused-ocr", "execution_id": "original-execution", "input_reference": payload["pdf_uri"], "ingest_lease": lease}
    changed = {**original, "execution_id": "different-execution"}
    with store() as session:
        session.add(OcrJob(id="reused-ocr", status="done", input_reference=payload["pdf_uri"],
            metrics={"nonworkflow_execution": changed, "first_pass_terminal_receipt": {
                "job_id": "reused-ocr", "execution_id": "different-execution", "status": "done"}}))
    assert ingest.settle_ingest_ocr_terminal(original) is None
    assert ingest.get_ingest_job_snapshot(receipt["id"])["status"] == "processing"


def test_business_terminal_does_not_consume_ocr_recovery_slots(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    lease = claim(receipt)
    with store() as session:
        session.add(OcrJob(id="terminal-ocr", status="failed", input_reference=payload["pdf_uri"],
            error_message="ocr pipeline output not found", metrics={"nonworkflow_execution": {"ingest_lease": lease}}))
    assert ingest.fail_ingest_attempt(lease, "hard failure")
    assert ingest.list_ocr_recovery_candidates(1) == []


def test_worker_old_capture_failure_does_not_fail_new_prepared_ocr(store, payload, monkeypatch):
    receipt = ingest.create_ingest_receipt(payload)
    prepared_new = {}

    def interrupted_registration(*args, **kwargs):
        assert ingest.restart_ingest_job(receipt["id"])
        new = claim(ingest.get_ingest_job_snapshot(receipt["id"]))
        prepared_new.update(ingest.prepare_ingest_ocr_job(new, f"OCR-{receipt['id']}", parse_ingest_payload(payload)))
        return {"id": "missing-order", "ingest_registration_receipt": {"order_id": "missing-order", "document_id": "missing-document"}}

    monkeypatch.setattr(worker, "create_order_from_ingest", interrupted_registration)
    worker.process_ingest_job(receipt["id"], generation=receipt["generation"], input_digest=receipt["input_digest"])
    with store() as session:
        job = session.get(OcrJob, f"OCR-{receipt['id']}")
        assert job.status == "running"
        assert job.metrics == prepared_new["metrics"]
    assert ingest.get_ingest_job_snapshot(receipt["id"])["status"] == "processing"


def test_actual_upload_wrapper_reconciles_terminal_ack_gap_without_recognition(store, payload, monkeypatch):
    add_upload(store, payload)
    uploaded = uploads.claim_uploaded_pdf("own-upload", worker_instance="interrupted-worker")
    recorded = uploads.build_ingest_payload(uploaded)
    created = ingest.create_ingest_receipt(recorded)
    lease = claim(created, uploaded_lease=uploaded)
    document = {"id": "own-document", "storage_uri": recorded["pdf_uri"], "source_email_id": recorded["message_id"]}
    boundary = {"job_id": "own-ocr", "execution_id": "saved-own-execution", "order_id": "own-order",
        "input_reference": recorded["pdf_uri"], "ingest_lease": lease,
        "orders": {"own-order": {"document": document}}}
    receipt = {"job_id": "own-ocr", "execution_id": "saved-own-execution", "document_id": "own-document", "status": "done"}
    with store() as session:
        session.add(OrderDocument(id=document["id"], storage_uri=document["storage_uri"], source_email_id=document["source_email_id"],
            received_at=datetime.fromisoformat(recorded["received_at"]), status="processed", ocr_attempts=1))
        session.add(Order(id="own-order", current_document_id="own-document", document_uri=recorded["pdf_uri"],
            message_id=recorded["message_id"], facility_code=recorded["facility_hint"], week_code=recorded["week_hint"],
            received_at=datetime.fromisoformat(recorded["received_at"]), status="\u8981\u78ba\u8a8d", superseded_document_ids=[]))
        session.add(OcrJob(id="own-ocr", status="done", input_reference=recorded["pdf_uri"], uploaded_pdf_id=uploaded["id"],
            metrics={"ingest_lease": lease, "nonworkflow_execution": boundary, "non_ocr_terminal_receipt": receipt}))
    assert ingest.settle_ingest_ocr_terminal(boundary)["status"] == "done"
    with store() as session:
        session.get(UploadedPdf, "own-upload").lease_expires_at = datetime.utcnow() - timedelta(seconds=1)

    def forbid_recognition(**kwargs):
        pytest.fail("Terminal result recovery must not recognize again")

    monkeypatch.setattr(worker, "_process_ingest_inline", forbid_recognition)
    worker.process_uploaded_pdf_job("own-upload")
    result = uploads.get_uploaded_pdf("own-upload")
    assert result["status"] == "completed"
    assert result["current_order_id"] == "own-order"
    assert result["current_document_id"] == "own-document"
    assert result["storage_uri"] == uploaded["storage_uri"]
    assert result["attempt_count"] == uploaded["attempt_count"] + 1
    assert ingest.get_ingest_job_snapshot(created["id"])["attempts"] == lease["attempts"]
