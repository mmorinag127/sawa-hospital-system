"""Owned SQLite/poll components; synthetic GCS/OIDC, not live provider proof.

Poll successes contain transport status/lineage only, never invented OCR rows.
Numeric preservation uses the unchanged real native numeric FAX fixture and
explicit human entry. Failure injection replaces external GCS or recognizer
construction only. Terminal commit refusal is a real SQLAlchemy IO event.
"""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from google.api_core import exceptions as api_errors
from google.api_core.retry import if_transient_error
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.cloud import storage
from requests.exceptions import ConnectionError, Timeout
import pytest
from sqlalchemy import event, select
from starlette.requests import Request

from modernization_owned_pipeline_transport import install_owned_pipeline_transport
from test_modernization_internal_failure_delivery import (
    execute_delivery, owned_failure, stable_data,
)
from test_modernization_owned_numeric_fax import numeric_fax
from test_modernization_owned_ocr_workflow import (
    FACILITY, JOB, ORDER, owned_producer, _owned_workflow_database_snapshot,
    _queue_and_claim_owned_workflow_attempt,
)
from src import db
from src.api import orders, worker
from src.models.ocr_job import OcrJob
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import ocr_job_service, order_service
from src.services import ocr_pipeline_service as pipeline
from src.services import ocr_execution_lock_service as slots
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_ocr_delivery_execution_service as execution
from src.services import workflow_ocr_delivery_service as delivery
from src.services.storage_uri import parse_gcs_uri
from src.services.workflow_attempt_guard_service import (
    capture_nonworkflow_execution, nonworkflow_execution_context,
)


class PollClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def configure_pipeline(f, monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "explicit-component-input")
    monkeypatch.delenv("OCR_PIPELINE_INPUT_BUCKET", raising=False)
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", str(f.root / "pipeline-state.json"))
    monkeypatch.setenv("OCR_PIPELINE_MAX_INFLIGHT", "0")
    monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("OCR_PIPELINE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("OCR_PIPELINE_OUTPUT_READ_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("OCR_MAIN_PROVIDER", "pipeline")
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(f.root / "published"))
    monkeypatch.setattr(execution, "engine", f.engine)
    monkeypatch.setattr(slots, "engine", f.engine)


def response_from_receipt(receipt, status="done"):
    """Synthetic provider envelope bound to the actual uploaded PDF receipt."""
    return {
        "status": status, "stage": status,
        "job_id": receipt["pipeline_job_id"],
        "input_reference": receipt["input_reference"],
        "input_generation": receipt["generation"],
        "output_reference": receipt["output_reference"],
        "input_sha256": receipt["input_sha256"],
        "configured_input_sha256": receipt["configured_input_sha256"],
        "execution_owner": {
            "job_id": receipt["job_id"], "order_id": receipt["order_id"],
            "workflow_attempt_id": receipt["workflow_attempt_id"],
            "workflow_revision": receipt["workflow_revision"],
            "nonworkflow_execution_id": receipt["execution_id"],
        },
        "canonical_adoption_allowed": True,
        "canonical_stages": {"classification": "passed", "roi": "passed", "correction": "passed"},
    }


@pytest.fixture
def owned_poll(owned_producer, monkeypatch):
    f = owned_producer
    configure_pipeline(f, monkeypatch)
    objects, uploads = install_owned_pipeline_transport(monkeypatch)
    _, created = ocr_job_service.create_job(
        JOB, str(f.pdf), order_id=ORDER,
        uploaded_pdf_id=f.receipt["uploaded_pdf_id"],
        order_document_id=f.receipt["document_id"],
        input_artifact_digest=f.receipt["sha256"], template_version_id=f.projection_id,
    )
    assert created
    boundary = capture_nonworkflow_execution(
        JOB, order_id=ORDER, input_reference=str(f.pdf),
        expected_document_id=f.receipt["document_id"],
    )
    assert boundary["selected_pdf_error"] is None
    assert boundary["selected_pdf_receipt"] == f.receipt
    with nonworkflow_execution_context(boundary):
        pending = pipeline.run_ocr_pipeline(
            pdf_bytes=f.pdf.read_bytes(), job_id=JOB, facility_id=FACILITY,
            input_reference=str(f.pdf), force_upload=True, wait_for_output=False,
        )
        receipt = ocr_job_service.own_pipeline_delivery_for_output(pending["output_reference"])
        original = parse_gcs_uri(receipt["input_reference"])
        assert objects[(original.bucket, original.object_name)][0] == f.pdf.read_bytes()
        assert str(objects[(original.bucket, original.object_name)][2]) == receipt["generation"]
        assert len([entry for entry in uploads if entry[0] == "upload"]) == 2
        clock = PollClock()
        monkeypatch.setattr(pipeline, "time", clock)
        yield SimpleNamespace(f=f, receipt=receipt, clock=clock)


def install_poll_blob(monkeypatch, case, *, fault=None, phase="exists", data=None,
                      exists=True, repeat=False):
    """External GCS IO only; actual JSON/ownership validator is never replaced."""
    events = []
    injected = []
    payload = response_from_receipt(case.receipt) if data is None else data
    encoded = json.dumps(payload).encode() if isinstance(payload, dict) else payload
    output = parse_gcs_uri(case.receipt["output_reference"])

    def call(operation, kwargs):
        events.append((operation, deepcopy(kwargs)))
        assert kwargs["timeout"] == 4.5 and kwargs["retry"] is None
        if operation == phase and fault is not None and (repeat or not injected):
            injected.append(fault)
            raise fault

    class Blob:
        generation = "73"

        def exists(self, **kwargs):
            call("exists", kwargs)
            return exists

        def reload(self, **kwargs):
            call("reload", kwargs)

        def download_as_bytes(self, **kwargs):
            assert kwargs["if_generation_match"] == 73
            call("download", kwargs)
            return encoded

    class Client:
        def bucket(self, bucket):
            assert bucket == output.bucket

            def blob(name):
                assert name == output.object_name
                return Blob()

            return SimpleNamespace(blob=blob)

    monkeypatch.setattr(storage, "Client", Client)
    return payload, events, injected


def poll(case):
    source = parse_gcs_uri(case.receipt["output_reference"])
    return pipeline._wait_for_output(
        bucket=source.bucket, object_name=source.object_name,
        timeout_seconds=3, poll_interval=1,
    )


@pytest.mark.parametrize("fault", [
    api_errors.Forbidden("denied"), api_errors.Unauthorized("unauthorized"),
    RefreshError("refresh"), DefaultCredentialsError("credentials"),
    AttributeError("generation absent"), RuntimeError(),
], ids=["forbidden", "unauthorized", "refresh", "credentials", "programming", "empty-runtime"])
def test_permanent_io_propagates_identical_exception_without_sleep(owned_poll, monkeypatch, fault):
    before = _owned_workflow_database_snapshot()
    _, events, injected = install_poll_blob(monkeypatch, owned_poll, fault=fault)
    with pytest.raises(type(fault)) as raised:
        poll(owned_poll)
    assert raised.value is fault
    assert injected == [fault]
    assert events == [("exists", {"timeout": 4.5, "retry": None})]
    assert owned_poll.clock.sleeps == []
    assert _owned_workflow_database_snapshot() == before


@pytest.mark.parametrize("data,exception,reason", [
    (b"{", json.JSONDecodeError, None),
    ({"status": "done"}, ocr_job_service.OwnPipelineLineageError,
     "ocr_pipeline_result_generation_mismatch"),
], ids=["invalid-json", "actual-lineage-rejection"])
def test_decode_and_actual_lineage_rejection_never_retry(owned_poll, monkeypatch, data, exception, reason):
    before = _owned_workflow_database_snapshot()
    _, events, _ = install_poll_blob(monkeypatch, owned_poll, data=data)
    with pytest.raises(exception) as raised:
        poll(owned_poll)
    if reason is not None:
        assert str(raised.value) == reason
    assert [operation for operation, _ in events] == ["exists", "reload", "download"]
    assert owned_poll.clock.sleeps == []
    assert _owned_workflow_database_snapshot() == before


@pytest.mark.parametrize("fault,public_transient", [
    (api_errors.InternalServerError("server"), True),
    (api_errors.TooManyRequests("rate"), True),
    (api_errors.ServiceUnavailable("service"), True),
    (ConnectionError("connection"), True), (TransportError("transport"), True),
    (api_errors.DeadlineExceeded("deadline"), False),
    (api_errors.BadGateway("gateway"), False), (Timeout("request timeout"), False),
], ids=["server", "rate", "unavailable", "connection", "auth-transport",
        "deadline", "bad-gateway", "requests-timeout"])
def test_known_transient_retries_then_actual_owned_success(owned_poll, monkeypatch, fault, public_transient):
    assert if_transient_error(fault) is public_transient
    before = _owned_workflow_database_snapshot()
    expected, events, injected = install_poll_blob(monkeypatch, owned_poll, fault=fault)
    assert poll(owned_poll) == expected
    assert injected == [fault]
    assert owned_poll.clock.sleeps == [1]
    assert [operation for operation, _ in events] == ["exists", "exists", "reload", "download"]
    assert events[-1][1] == {"if_generation_match": 73, "timeout": 4.5, "retry": None}
    assert _owned_workflow_database_snapshot() == before


@pytest.mark.parametrize("phase", ["exists", "reload", "download"])
@pytest.mark.parametrize("exception", [api_errors.NotFound, api_errors.PreconditionFailed])
def test_publication_race_requires_prior_exists_true(owned_poll, monkeypatch, phase, exception):
    fault = exception("publication race")
    before = _owned_workflow_database_snapshot()
    expected, events, injected = install_poll_blob(monkeypatch, owned_poll, fault=fault, phase=phase)
    if phase == "exists":
        with pytest.raises(exception) as raised:
            poll(owned_poll)
        assert raised.value is fault
        assert owned_poll.clock.sleeps == []
        assert len(events) == 1
    else:
        assert poll(owned_poll) == expected
        assert owned_poll.clock.sleeps == [1]
        assert events[0][0] == "exists"
        assert [operation for operation, _ in events].count("exists") == 2
    assert injected == [fault]
    assert _owned_workflow_database_snapshot() == before


@pytest.mark.parametrize("present", [False, True], ids=["absent-object", "partial-object"])
def test_absent_or_partial_output_retains_poll_deadline(owned_poll, monkeypatch, present):
    before = _owned_workflow_database_snapshot()
    install_poll_blob(monkeypatch, owned_poll, exists=present,
                      data=response_from_receipt(owned_poll.receipt, "running"))
    with pytest.raises(TimeoutError) as raised:
        poll(owned_poll)
    assert str(raised.value) == f"OCR pipeline output not found: {owned_poll.receipt['output_reference']}"
    assert owned_poll.clock.sleeps == [1, 1, 1]
    assert _owned_workflow_database_snapshot() == before


@pytest.mark.parametrize("status", ["error", "failed"])
def test_terminal_output_preserves_actual_validator_contract(owned_poll, monkeypatch, status):
    before = _owned_workflow_database_snapshot()
    expected = response_from_receipt(owned_poll.receipt, status)
    expected["error"] = "synthetic external provider failure"
    install_poll_blob(monkeypatch, owned_poll, data=expected)
    if status == "failed":
        with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                           match="^ocr_pipeline_required_stage_failed$"):
            poll(owned_poll)
    else:
        assert poll(owned_poll) == expected
    assert owned_poll.clock.sleeps == []
    assert _owned_workflow_database_snapshot() == before


def queue(f, mode, *, force=False):
    order = order_service.get_order_by_id(ORDER)
    selected = [version for version in order["versions"]
                if version["document_id"] == f.receipt["document_id"]]
    assert len(selected) == 1
    with db.session_scope() as session:
        revision = session.get(OrderWorkflowState, ORDER).revision
    queued, error = workflow.mark_ocr_run_queued(
        ORDER, JOB, input_reference=str(f.pdf), request_mode=mode,
        job_metrics={**orders._document_selection_metrics(selected[0]),
                     "reparse_ocr_prompt": None, "reparse_prompt_preset": None,
                     "reparse_requested_provider": None, "reparse_ocr_model": None,
                     "reparse_llm_assist": mode == "llm_reparse"},
        delivery_selected_document_id=f.receipt["document_id"],
        expected_revision=revision, force=force,
    )
    assert error is None, (error, queued)
    assert queued["workflow"]["state"] == "ocr_running"
    return queued


def claim_queued(queued):
    claim = delivery.claim_delivery(
        queued["delivery"]["id"], queued["delivery"]["generation"],
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    assert not claim.get("duplicate"), claim
    return claim


def invoke_worker_route(monkeypatch, queued):
    """Actual registered handler/claim/completion/ACK; only OIDC is synthetic."""
    identity = {
        "email": "component@synthetic.iam.gserviceaccount.com", "sub": "synthetic-subject",
        "email_verified": True, "aud": "https://reserved.invalid/component-worker",
    }
    for key, value in (("EMAIL", identity["email"]), ("SUBJECT", identity["sub"]),
                       ("AUDIENCE", identity["aud"])):
        monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_WORKER_" + key, value)
    monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS", "300")

    def synthetic_oidc(token, request, audience):
        assert token == "synthetic-token" and audience == identity["aud"]
        return deepcopy(identity)

    monkeypatch.setattr(worker.id_token, "verify_oauth2_token", synthetic_oidc)
    request = Request({"type": "http", "method": "POST", "path": "/worker/component",
                       "headers": []})
    return worker.process_workflow_ocr_delivery(
        queued["delivery"]["id"], queued["delivery"]["generation"], request,
        authorization="Bearer synthetic-token",
    )


@pytest.fixture
def saved_numeric(numeric_fax, monkeypatch):
    f, _identity = numeric_fax
    configure_pipeline(f, monkeypatch)
    evidence = f.produce()
    records = evidence["payload_json"]["hakodate_ocr_evidence_records"]
    by_cell = {record["engine_metadata"]["sheet_cell"]: record for record in records}
    assert len(records) == len(by_cell) == len(f.expected) == 56
    for cell, value in f.expected.items():
        assert by_cell[cell]["raw_text"] == by_cell[cell]["normalized_value"] == value
    f.select()
    projected, error = workflow.build_sheet_from_selected_ocr(ORDER)
    assert error is None, (error, projected)
    sheet = deepcopy(projected["sheet"])
    index = sheet["fields"].index("qty.regular_x")
    authored = list(f.expected.values())
    assert authored == [str((7, 0, 6, 9)[i % 4]) for i in range(56)]
    # Human confirmation, not recognition confidence/quantity substitution.
    for row, value in zip(sheet["rows"], authored, strict=True):
        row[index] = value
    with db.session_scope() as session:
        revision = session.get(OrderWorkflowState, ORDER).revision
    saved, error = workflow.save_sheet(
        order_id=ORDER, sheet=sheet, edited_by="synthetic-permanent-failure-review",
        expected_revision=revision,
    )
    assert error is None, (error, saved)
    with db.session_scope() as session:
        draft = session.get(OrderSheetDraft, session.get(OrderWorkflowState, ORDER).draft_id)
        assert draft.draft_sheet_json == sheet
        assert [row[index] for row in draft.draft_sheet_json["rows"]] == authored
    return f


def install_provider_failure(monkeypatch, mode, fault):
    observed = []

    def fail(*args, **kwargs):
        observed.append((args, kwargs))
        raise fault

    if mode == "ocr_rerun":
        from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as native
        monkeypatch.setattr(native, "_get_text_recognizer", fail)
    else:
        install_owned_pipeline_transport(monkeypatch, on_missing=fail)
    return observed


@pytest.mark.parametrize("mode,fault", [
    ("ocr_reparse", api_errors.Forbidden("synthetic denied")),
    ("llm_reparse", api_errors.Unauthorized("synthetic unauthorized")),
    ("ocr_reparse", Exception()),
    ("ocr_rerun", RuntimeError("synthetic recognizer construction failure")),
], ids=["ocr-reparse", "llm-reparse", "empty-message", "ocr-rerun-recognizer"])
def test_real_worker_permanent_failure_persists_owned_terminal_and_ack(
    saved_numeric, monkeypatch, mode, fault,
):
    f = saved_numeric
    queued = queue(f, mode, force=True)
    before = _owned_workflow_database_snapshot()
    observed = install_provider_failure(monkeypatch, mode, fault)
    assert invoke_worker_route(monkeypatch, queued) == {"acknowledged": True, "duplicate": False}
    assert len(observed) == 1
    after = _owned_workflow_database_snapshot()
    assert stable_data(after) == stable_data(before)
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        state = session.get(OrderWorkflowState, ORDER)
        row = session.get(WorkflowOcrDelivery, queued["delivery"]["id"])
        receipt = job.metrics["rerun_terminal_receipt"]
        assert job.status == "failed" and state.state == "ocr_failed"
        assert receipt == row.result_receipt
        assert receipt["status"] == "failed" and receipt["error"]
        assert receipt["order_id"] == ORDER and receipt["job_id"] == JOB
        assert receipt["workflow_attempt_id"] == job.metrics["workflow_attempt_id"]
        assert receipt["workflow_revision"] == queued["workflow"]["revision"]
        assert receipt["workflow_delivery_owner"] == {
            "delivery_id": row.id, "generation": row.delivery_generation, "claim_token": row.claim_token,
        }
        assert state.revision == queued["workflow"]["revision"] + 1
        assert row.state == delivery.ACKNOWLEDGED and row.lease_expires_at is None
        assert job.metrics.get("pending_receipt") is None
        assert job.metrics.get("auto_recovery_count", 0) == 0
        if mode != "ocr_rerun":
            assert receipt["error"] == f"{type(fault).__name__}: {fault}".strip()
        assert len(after[WorkflowOcrDelivery.__tablename__]) == len(before[WorkflowOcrDelivery.__tablename__])


@pytest.mark.parametrize("refuse_all", [False, True], ids=["first-terminal-only", "all-terminal-commits"])
def test_terminal_commit_refusal_is_not_reclassified_and_retried(
    owned_failure, refuse_all,
):
    """Regression must fail if a generic catch hides the first refused commit."""
    f = owned_failure
    claim = _queue_and_claim_owned_workflow_attempt(f)
    before = _owned_workflow_database_snapshot()
    attempted = []
    refusal = RuntimeError("synthetic first terminal commit refused")

    def reject_terminal_commit(session):
        for instance in session.identity_map.values():
            if (isinstance(instance, OcrJob) and instance.id == JOB
                    and isinstance((instance.metrics or {}).get("rerun_terminal_receipt"), dict)):
                attempted.append(deepcopy(instance.metrics["rerun_terminal_receipt"]))
                if refuse_all or len(attempted) == 1:
                    raise refusal

    event.listen(f.sessions, "before_commit", reject_terminal_commit)
    try:
        with pytest.raises(RuntimeError) as raised:
            execute_delivery(claim)
        assert raised.value is refusal
    finally:
        event.remove(f.sessions, "before_commit", reject_terminal_commit)
    assert len(attempted) == 1
    assert attempted[0]["workflow_attempt_id"] == claim["workflow_attempt_id"]
    after = _owned_workflow_database_snapshot()
    assert stable_data(after) == stable_data(before)
    assert after[OrderWorkflowState.__tablename__] == before[OrderWorkflowState.__tablename__]
    assert after[WorkflowOcrDelivery.__tablename__] == before[WorkflowOcrDelivery.__tablename__]
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        assert job.status == "running" and job.metrics.get("rerun_terminal_receipt") is None
    with pytest.raises(ValueError, match="^workflow_ocr_delivery_persisted_result_missing$"):
        delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"])
    assert _owned_workflow_database_snapshot() == after


@pytest.mark.parametrize("fault", ["successor", "revision", "claim-loss"])
def test_stale_owned_execution_cannot_persist_or_ack(owned_producer, monkeypatch, fault):
    f = owned_producer
    configure_pipeline(f, monkeypatch)
    old = _queue_and_claim_owned_workflow_attempt(f)
    if fault == "successor":
        _queue_and_claim_owned_workflow_attempt(f, force=True)
    elif fault == "revision":
        with db.session_scope() as session:
            state = session.get(OrderWorkflowState, ORDER)
            state.revision += 1
    else:
        with db.session_scope() as session:
            session.get(WorkflowOcrDelivery, old["delivery_id"]).lease_expires_at = (
                datetime.utcnow() - timedelta(seconds=1))
        successor = delivery.claim_delivery(
            old["delivery_id"], old["generation"],
            lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        assert successor["claim_token"] != old["claim_token"]
    before = _owned_workflow_database_snapshot()
    with pytest.raises(ValueError):
        execute_delivery(old)
    assert _owned_workflow_database_snapshot() == before
    with db.session_scope() as session:
        assert session.get(WorkflowOcrDelivery, old["delivery_id"]).result_receipt is None


def test_real_pending_handoff_ack_is_not_failed_or_requeued(owned_producer, monkeypatch):
    f = owned_producer
    configure_pipeline(f, monkeypatch)
    queued = queue(f, "ocr_reparse")
    before = _owned_workflow_database_snapshot()
    objects, events = install_owned_pipeline_transport(monkeypatch)
    clock = PollClock()
    monkeypatch.setattr(pipeline, "time", clock)
    assert invoke_worker_route(monkeypatch, queued) == {"acknowledged": True, "duplicate": False}
    assert clock.sleeps == [1, 1, 1]
    after = _owned_workflow_database_snapshot()
    assert stable_data(after) == stable_data(before)
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        row = session.get(WorkflowOcrDelivery, queued["delivery"]["id"])
        assert job.status == "awaiting_output"
        assert job.metrics.get("rerun_terminal_receipt") is None
        assert job.metrics["pending_receipt"]["workflow_attempt_id"] == job.metrics["workflow_attempt_id"]
        assert row.state == delivery.ACKNOWLEDGED and row.lease_expires_at is None
        assert row.result_receipt is None
    assert len([entry for entry in events if entry[0] == "upload"]) == len(objects) == 2


def test_pending_commit_refusal_is_not_reclassified_completed_or_acked(owned_producer, monkeypatch):
    """Real pending persistence; synthetic GCS/clock, first SQL commit refused."""
    f = owned_producer
    configure_pipeline(f, monkeypatch)
    queued = queue(f, "ocr_reparse")
    claim = claim_queued(queued)
    before = _owned_workflow_database_snapshot()
    pdf_before, expected_before = f.pdf.read_bytes(), deepcopy(f.expected)
    objects, events = install_owned_pipeline_transport(monkeypatch)
    clock = PollClock()
    monkeypatch.setattr(pipeline, "time", clock)
    attempted_pending = []
    attempted_terminal = []
    committed_before_pending = []
    refusal = RuntimeError("synthetic first pending commit refused")

    def reject_first_pending_commit(session):
        for instance in session.identity_map.values():
            if not isinstance(instance, OcrJob) or instance.id != JOB:
                continue
            metrics = instance.metrics or {}
            if isinstance(metrics.get("rerun_terminal_receipt"), dict):
                attempted_terminal.append(deepcopy(metrics["rerun_terminal_receipt"]))
            if instance.status != "awaiting_output":
                continue
            pending = deepcopy(metrics["pending_receipt"])
            attempted_pending.append(pending)
            assert pending["selected_document_id"] == f.receipt["document_id"]
            assert pending["workflow_attempt_id"] == claim["workflow_attempt_id"]
            assert pending["workflow_revision"] == claim["workflow_revision"]
            assert pending["workflow_delivery_owner"] == {
                key: claim[key] for key in ("delivery_id", "generation", "claim_token")
            }
            row = session.get(WorkflowOcrDelivery, claim["delivery_id"])
            assert row.handoff_receipt["pending_receipt"] == pending
            assert row.handoff_receipt["selected_pdf_receipt"] == claim["selected_pdf_receipt"]
            if len(attempted_pending) == 1:
                # A separate connection sees committed progress, not this flushed
                # pending transaction. Read Core rows without nested Session events.
                with f.engine.connect() as connection:
                    committed_before_pending.append({
                        table.name: deepcopy([
                            dict(record) for record in connection.execute(
                                select(table).order_by(*table.primary_key.columns),
                            ).mappings()
                        ])
                        for table in db.Base.metadata.sorted_tables
                    })
                raise refusal

    event.listen(f.sessions, "before_commit", reject_first_pending_commit)
    try:
        with pytest.raises(RuntimeError) as raised:
            execute_delivery(claim)
        assert raised.value is refusal
    finally:
        event.remove(f.sessions, "before_commit", reject_first_pending_commit)
    assert len(attempted_pending) == len(committed_before_pending) == 1
    assert attempted_terminal == []
    assert clock.sleeps == [1, 1, 1]
    assert len([entry for entry in events if entry[0] == "upload"]) == len(objects) == 2
    after = _owned_workflow_database_snapshot()
    assert after == committed_before_pending[0]
    assert stable_data(after) == stable_data(before)
    assert after[OrderWorkflowState.__tablename__] == before[OrderWorkflowState.__tablename__]
    assert after[WorkflowOcrDelivery.__tablename__] == before[WorkflowOcrDelivery.__tablename__]
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        row = session.get(WorkflowOcrDelivery, claim["delivery_id"])
        assert job.status == "running"
        assert job.metrics.get("pending_receipt") is None
        assert job.metrics.get("rerun_terminal_receipt") is None
        assert job.metrics.get("auto_recovery_count", 0) == 0
        assert row.state == delivery.CLAIMED
        assert row.claim_token == claim["claim_token"]
        assert row.delivery_generation == claim["generation"]
        assert row.lease_expires_at is not None
        assert row.handoff_receipt is None and row.result_receipt is None
    with pytest.raises(ValueError, match="^workflow_ocr_delivery_persisted_result_missing$"):
        delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"])
    assert _owned_workflow_database_snapshot() == after
    assert f.pdf.read_bytes() == pdf_before and f.expected == expected_before


def test_real_native_done_receipt_survives_duplicate_callback(numeric_fax, monkeypatch):
    f, _identity = numeric_fax
    configure_pipeline(f, monkeypatch)
    claim = claim_queued(queue(f, "ocr_rerun"))
    result = execute_delivery(claim)
    assert result["terminal_receipt"]["status"] == "done"
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        assert job.status == "done"
        receipt = deepcopy(job.metrics["rerun_terminal_receipt"])
        assert receipt["workflow_attempt_id"] == claim["workflow_attempt_id"]
    assert delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"]) is True
    before = _owned_workflow_database_snapshot()
    with pytest.raises(ValueError):
        execute_delivery(claim)
    assert _owned_workflow_database_snapshot() == before
    with db.session_scope() as session:
        assert session.get(OcrJob, JOB).metrics["rerun_terminal_receipt"] == receipt
