"""Authored source cases only. Fixtures are metadata, never OCR/PDF parity proof."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src import db, models
from src.models.ingest_delivery import IngestDelivery
from src.models.ingest_job import IngestJob
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.services import ingest_delivery_service as delivery
from src.services import ingest_delivery_dispatch_service as dispatch
from src.services import ingest_delivery_auth_service as auth
from src.services import ingest_job_service as ingest, uploaded_pdf_service as uploads
from src.services import workflow_ocr_delivery_execution_service as exclusion


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'delivery-source-case.sqlite'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=True)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(exclusion, "engine", engine)
    values = {"PROJECT_ID": "fixture-project", "LOCATION": "fixture-location", "QUEUE": "fixture-queue",
        "TARGET_URL": "https://fixture.invalid", "SERVICE_ACCOUNT_EMAIL": "fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE": "fixture-audience", "DISPATCH_DEADLINE_SECONDS": "15"}
    for key, value in values.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ("WORKER_", "DRAIN_"):
        for key, value in {"ISSUER": "https://accounts.google.com", "AUDIENCE": "fixture-audience",
            "EMAIL": values["SERVICE_ACCOUNT_EMAIL"], "SUBJECT": "fixture-subject"}.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def payload(tmp_path, monkeypatch):
    from src.services import canonical_facility_store as canonical
    from src.services import ingest_facility_selection_service as selection

    data = {"message_id": "authored-ingest", "pdf_uri": "fixture://metadata-only",
        "received_at": "2026-10-07T00:00:00", "facility_hint": "authored-facility",
        "week_hint": "2026-10@2026-10-05~2026-10-11", "skip_ocr": False,
        "content_sha256": "a" * 64, "unknown": {"zero": 0, "null": None}}
    # Reuse captured's synthetic component record for this explicitly authored week.
    record = {"version_id": "authored-A", "valid_from": "2026-10-05", "valid_to": "2026-10-11",
        "facility": {"facility_id": "authored-facility", "facility_name": "Authored component", "areas": None,
            "unknown": {"zero": 0, "null": None}, "quantity_setting": 0,
            "fax_template": {"template_id": "authored-template", "units": "normalized",
                "table_box": [0, 0, 1, 1], "header_rows": 0,
                "columns": [{"index": 0, "source_index": 0, "role": "quantity",
                    "diet_type": "regular", "area_id": "X"}]}}}
    path = tmp_path / "component-canonical.json"
    path.write_text(json.dumps({"facilities": [{"facility_id": data["facility_hint"], "versions": [record]}]}))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    revision = canonical.read_snapshot().revision
    data["facility_selection"] = selection.capture_selection(
        data["facility_hint"], datetime.fromisoformat(data["received_at"]).date().isoformat(),
        revision, "authored-A", data["week_hint"],
    )
    return data


def intent(factory):
    with factory() as session:
        row = session.execute(select(IngestDelivery)).scalar_one()
        return row.id, row.generation, deepcopy(row.task_contract)


def test_direct_acceptance_and_intent_rollback_together(store, payload, monkeypatch):
    def refuse(*args):
        raise RuntimeError("intent_storage_refused")
    monkeypatch.setattr(delivery, "create_intent", refuse)
    with pytest.raises(RuntimeError, match="intent_storage_refused"):
        ingest.create_ingest_receipt(payload)
    with store() as session:
        assert session.get(IngestJob, payload["message_id"]) is None
        assert session.execute(select(IngestDelivery)).first() is None


def test_config_unavailable_stops_before_reservation(store, payload, monkeypatch):
    monkeypatch.delenv(dispatch.PREFIX + "QUEUE")
    with pytest.raises(ValueError, match="configuration_required"):
        ingest.create_ingest_receipt(payload)
    with store() as session:
        assert session.get(IngestJob, payload["message_id"]) is None


def test_upload_acceptance_rolls_back_upload_and_ingest(store, payload, monkeypatch):
    saved = SimpleNamespace(message_id=payload["message_id"], content_sha256=payload["content_sha256"],
        pdf_uri=payload["pdf_uri"], received_at=datetime(2026, 10, 7), original_filename="metadata-only.pdf")
    def refuse(*args):
        raise RuntimeError("intent_storage_refused")
    monkeypatch.setattr(delivery, "create_intent", refuse)
    with pytest.raises(RuntimeError, match="intent_storage_refused"):
        uploads.create_uploaded_pdf_from_upload(saved=saved, facility_hint=payload["facility_hint"],
            week_hint=payload["week_hint"], facility_name=None, skip_ocr=False, source_kind="manual_upload",
            facility_selection=payload["facility_selection"])
    with store() as session:
        assert session.execute(select(UploadedPdf)).first() is None
        assert session.get(IngestJob, payload["message_id"]) is None


def test_duplicate_and_transport_generation_keep_original_business_claim(store, payload):
    original = ingest.create_ingest_receipt(payload)
    delivery_id, generation, contract = intent(store)
    first = delivery.claim_delivery(delivery_id, generation)
    second = delivery.claim_delivery(delivery_id, generation)
    assert first["lease"] == second["lease"]
    assert first["claim_token"] != second["claim_token"]
    with delivery.claim_context(first), db.session_scope() as session:
        assert not delivery.fence_original_write(session, first["lease"])
    assert not ingest.create_ingest_receipt({**payload, "pdf_uri": "fixture://changed"})["enqueued"]
    renewed = delivery.renew_missing(delivery_id, generation)
    assert renewed["generation"] == generation + 1
    with store() as session:
        job = session.get(IngestJob, original["id"])
        row = session.get(IngestDelivery, delivery_id)
        assert job.generation == original["generation"] and job.input_digest == original["input_digest"]
        assert job.attempts == 1 and job.generation_attempts == 1
        assert row.owner_lease == first["lease"]
        assert row.task_contract["http_request"]["url"] == contract["http_request"]["url"]
    assert delivery.inspect_generation(delivery_id, generation)["duplicate"]


@pytest.mark.parametrize("generation", [None, True, 0, -1, "1", 99])
def test_unknown_generation_stops_before_duplicate_ack(store, payload, generation):
    ingest.create_ingest_receipt(payload)
    delivery_id, _, _ = intent(store)
    with pytest.raises(ValueError, match="generation"):
        delivery.process(delivery_id, generation)


def test_mutex_busy_is_not_an_ack(store, payload, monkeypatch):
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    @contextmanager
    def busy(*args):
        yield False
    monkeypatch.setattr(delivery, "execution_mutex", busy)
    with pytest.raises(ValueError, match="execution_busy"):
        delivery.process(delivery_id, generation)


def test_empty_runner_return_has_no_saved_business_ack(store, payload, monkeypatch):
    from src.workers import ingest_worker
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    monkeypatch.setattr(ingest_worker, "process_ingest_job", lambda *a, **k: None)
    with pytest.raises(ValueError, match="owned_receipt_missing"):
        delivery.process(delivery_id, generation)


def test_non_ocr_document_receipt_persists_without_ocr_job(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    claim = delivery.claim_delivery(delivery_id, generation)
    terminal = {"document_id": "metadata-document", "status": "failed"}
    with store.begin() as session:
        session.add(OrderDocument(id="metadata-document", storage_uri=payload["pdf_uri"],
            source_email_id=payload["message_id"], received_at=datetime(2026, 10, 7),
            status="error", error_message="actual persisted metadata failure"))
    with delivery.claim_context(claim):
        delivery.persist_runner_receipt(claim["lease"], terminal)
        assert ingest.fail_ingest_job(receipt["id"], "actual persisted metadata failure", lease=claim["lease"])
        assert delivery.acknowledge_claim(claim) == "business_failed"
    assert dispatch.dispatch(delivery_id, generation)["action"] == "acknowledged"


def test_hard_failure_without_receipt_never_queue_fills(store, payload):
    receipt = ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    claim = delivery.claim_delivery(delivery_id, generation)
    with delivery.claim_context(claim):
        assert ingest.fail_ingest_job(receipt["id"], "business failure", lease=claim["lease"])
    assert delivery.prepare_dispatch(delivery_id, generation) is None
    with pytest.raises(ValueError, match="owned_receipt_missing"):
        delivery.acknowledge_claim(claim)


@pytest.mark.parametrize("drain", [False, True])
def test_auth_is_independent_and_exact(store, monkeypatch, drain):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as missing:
        auth.require_identity(None, drain=drain)
    assert missing.value.status_code == 401
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda *a: {
        "iss": "https://accounts.google.com", "aud": "fixture-audience",
        "email": "fixture@fixture.iam.gserviceaccount.com", "sub": "foreign", "email_verified": True})
    with pytest.raises(HTTPException) as foreign:
        auth.require_identity("Bearer fixture", drain=drain)
    assert foreign.value.status_code == 403


@pytest.mark.parametrize("phase", ["registration", "prepare", "capture"])
def test_actual_early_failure_saves_owned_error_then_exact_duplicate_ack(store, payload, monkeypatch, phase):
    from src.workers import ingest_worker
    original = ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    def refuse(*args, **kwargs):
        raise ValueError("authored_original_source_refused")
    if phase == "registration":
        monkeypatch.setattr(ingest_worker, "create_order_from_ingest", refuse)
    elif phase == "prepare":
        monkeypatch.setattr(ingest, "prepare_ingest_ocr_job", refuse)
    else:
        monkeypatch.setattr(ingest_worker, "_capture_and_process_ingest", refuse)
    result = delivery.process(delivery_id, generation)
    assert result["result"] == "business_failed"
    with store() as session:
        job = session.get(IngestJob, original["id"])
        row = session.get(IngestDelivery, delivery_id)
        assert job.status == "error" and job.failure_kind == "business"
        assert job.last_error == row.terminal_receipt["error"]
        assert row.terminal_receipt["kind"] == "source_failure"
        assert row.terminal_receipt["input_digest"] == original["input_digest"]
    assert delivery.process(delivery_id, generation)["duplicate"]
    assert delivery.prepare_dispatch(delivery_id, generation) is None


def test_unpersisted_or_foreign_error_has_no_terminal_ack(store, payload, monkeypatch):
    from src.workers import ingest_worker
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    def fail_storage(*args, **kwargs):
        raise RuntimeError("source_storage_unavailable")
    monkeypatch.setattr(ingest_worker, "_process_ingest_inline", fail_storage)
    with pytest.raises(RuntimeError, match="storage_unavailable"):
        delivery.process(delivery_id, generation)
    with store() as session:
        row = session.get(IngestDelivery, delivery_id)
        assert row.state != "ACKNOWLEDGED" and row.terminal_receipt is None
        lease = deepcopy(row.owner_lease)
    foreign = {**lease, "claim_id": "foreign"}
    assert delivery.persist_uncaptured_failure(foreign, ValueError("foreign_failure")) is None


def test_real_uploaded_skip_closes_original_upload_without_ocr_success(store, payload):
    saved = SimpleNamespace(message_id=payload["message_id"], content_sha256=payload["content_sha256"],
        pdf_uri=payload["pdf_uri"], received_at=datetime(2026, 10, 7), original_filename="metadata-only.pdf")
    uploaded, _ = uploads.create_uploaded_pdf_from_upload(saved=saved, facility_hint=payload["facility_hint"],
        week_hint=payload["week_hint"], facility_name=None, skip_ocr=True, source_kind="manual_upload",
        facility_selection=payload["facility_selection"])
    delivery_id, generation, _ = intent(store)
    claim = delivery.claim_delivery(delivery_id, generation)
    terminal = {"document_id": "skipped-document", "status": "done", "result_state": "skipped"}
    with store.begin() as session:
        session.add(OrderDocument(id="skipped-document", storage_uri=payload["pdf_uri"], source_email_id=payload["message_id"],
            received_at=datetime(2026, 10, 7), status="skipped"))
        session.get(UploadedPdf, uploaded["id"]).current_document_id = "skipped-document"
    with delivery.claim_context(claim):
        delivery.persist_runner_receipt(claim["lease"], terminal)
        delivery.settle_non_ocr_skip(claim["lease"], terminal)
        assert delivery.acknowledge_claim(claim) == "business_done"
    with store() as session:
        row = session.get(UploadedPdf, uploaded["id"])
        assert row.status == "completed" and row.current_stage == "registration_skipped"
        assert row.lease_token == claim["lease"]["uploaded_lease"]["lease_token"]


@pytest.mark.parametrize("mutation", ["body", "oauth", "url", "audience", "deadline", "method", "name", "oidc_email", "header_changed", "header_missing", "authorization", "proxy-authorization", "cookie", "x-serverless-authorization"])
def test_full_task_lookup_refuses_decision_bearing_mismatch(store, payload, mutation):
    import base64
    from google.cloud import tasks_v2
    ingest.create_ingest_receipt(payload)
    _, _, contract = intent(store)
    values = deepcopy(contract)
    values["http_request"]["http_method"] = tasks_v2.HttpMethod.POST
    values["http_request"]["body"] = base64.b64decode(values["http_request"]["body"])
    task = tasks_v2.Task(values)
    if mutation == "body":
        task.http_request.body = b"{}"
    elif mutation == "oauth":
        task.http_request.oauth_token.service_account_email = "foreign@fixture.iam.gserviceaccount.com"
    elif mutation == "url":
        task.http_request.url += "/foreign"
    elif mutation == "audience":
        task.http_request.oidc_token.audience = "foreign"
    elif mutation == "method":
        task.http_request.http_method = tasks_v2.HttpMethod.GET
    elif mutation == "name":
        task.name += "-foreign"
    elif mutation == "oidc_email":
        task.http_request.oidc_token.service_account_email = "foreign@fixture.iam.gserviceaccount.com"
    elif mutation == "header_changed":
        task.http_request.headers["Content-Type"] = "text/plain"
    elif mutation == "header_missing":
        task.http_request.headers.clear()
    elif mutation in {"authorization", "proxy-authorization", "cookie", "x-serverless-authorization"}:
        task.http_request.headers[mutation.upper()] = "foreign-credential"
    else:
        tasks_v2.Task.pb(task).dispatch_deadline.nanos = 1
    client = SimpleNamespace(get_task=lambda **kwargs: task)
    with pytest.raises(ValueError, match="contract_mismatch"):
        dispatch.inspect_task(client, contract)


@pytest.mark.parametrize("successor", [False, True])
def test_full_lookup_preserves_own_headers_with_provider_extras(store, payload, successor):
    import base64
    from google.cloud import tasks_v2
    ingest.create_ingest_receipt(payload)
    _, _, contract = intent(store)
    if successor:
        contract = dispatch.renew_contract(contract, "authored-successor", 1)
        contract["http_request"]["url"] = contract["http_request"]["url"].rsplit("/", 1)[0] + "/auto-llm"
    values = deepcopy(contract)
    values["http_request"]["http_method"] = tasks_v2.HttpMethod.POST
    values["http_request"]["body"] = base64.b64decode(values["http_request"]["body"])
    values["http_request"]["headers"] = {"content-type": "application/json", "User-Agent": "Google-Cloud-Tasks",
        "Host": "fixture.invalid", "Content-Length": str(len(values["http_request"]["body"]))}
    task = tasks_v2.Task(values)
    requests = []
    def lookup(*, request):
        requests.append(request)
        return task
    assert dispatch.inspect_task(SimpleNamespace(get_task=lookup), contract) == "exists"
    assert requests == [{"name": contract["name"], "response_view": tasks_v2.Task.View.FULL}]
    assert contract["http_request"]["headers"] == {"Content-Type": "application/json"}


def test_lookup_header_case_aliases_are_not_last_wins():
    assert not dispatch._headers_match({"Content-Type": "application/json", "content-type": "text/plain"},
        {"Content-Type": "application/json"})


def test_lookup_unknown_does_not_renew_original(store, payload, monkeypatch):
    from google.cloud import tasks_v2
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, contract = intent(store)
    def unknown(**kwargs):
        raise RuntimeError("provider_unknown")
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", lambda: SimpleNamespace(get_task=unknown))
    with pytest.raises(RuntimeError, match="lookup_unknown"):
        dispatch.dispatch(delivery_id, generation)
    assert intent(store)[1:] == (generation, contract)


def test_early_failure_receipt_storage_refusal_is_not_ack(store, payload, monkeypatch):
    from src.workers import ingest_worker
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    def registration_failure(*args, **kwargs):
        raise ValueError("registration_refused")
    def storage_failure(*args, **kwargs):
        raise RuntimeError("receipt_storage_refused")
    monkeypatch.setattr(ingest_worker, "_process_ingest_inline", registration_failure)
    monkeypatch.setattr(delivery, "persist_uncaptured_failure", storage_failure)
    with pytest.raises(RuntimeError, match="receipt_storage_refused"):
        delivery.process(delivery_id, generation)
    with store() as session:
        row = session.get(IngestDelivery, delivery_id)
        assert row.state != "ACKNOWLEDGED" and row.terminal_receipt is None


def test_hardfailed_without_owned_receipt_is_excluded_from_drain(store, payload):
    ingest.create_ingest_receipt(payload)
    with store.begin() as session:
        job = session.get(IngestJob, payload["message_id"])
        job.status = "error"
        job.failure_kind = "business"
        job.last_error = "unproven_failure"
    assert delivery.drain_candidates(10) == []


def test_empty_runner_dictionary_is_not_business_failure_or_ack(store, payload, monkeypatch):
    from src.workers import ingest_worker
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    monkeypatch.setattr(ingest_worker, "_process_ingest_inline", lambda **kwargs: {})
    with pytest.raises(ValueError, match="terminal_receipt_missing"):
        delivery.process(delivery_id, generation)
    with store() as session:
        row = session.get(IngestDelivery, delivery_id)
        assert row.state != "ACKNOWLEDGED" and row.terminal_receipt is None
        assert session.get(IngestJob, payload["message_id"]).status == "processing"


@pytest.mark.parametrize("changed", [None, "selected_pdf_receipt", "configured_input_sha256", "execution_id"])
def test_saved_handoff_original_identity_and_reconciliation_owner(store, payload, monkeypatch, changed):
    """Real receipt transaction; external pipeline validation adapter is isolated."""
    import json
    from hashlib import sha256
    from src.models.ocr_job import OcrJob
    from src.services import ocr_job_service
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    claim = delivery.claim_delivery(delivery_id, generation)
    configured = {"raw": {"zero": 0, "null": None}}
    selected = {"sha256": payload["content_sha256"]}
    capture = {"job_id": "handoff-job", "order_id": "original-order", "job_order_id": None,
        "execution_id": "original-execution", "input_reference": payload["pdf_uri"],
        "ingest_lease": claim["lease"], "selected_pdf_receipt": selected,
        "orders": {"original-order": {"configured_input": configured}}}
    pipeline = {"job_id": "handoff-job", "order_id": "original-order", "execution_id": "original-execution",
        "workflow_attempt_id": None, "workflow_revision": None, "selected_input_reference": payload["pdf_uri"],
        "input_reference": "gs://fixture/input.pdf", "output_reference": "gs://fixture/output.json",
        "generation": "1", "pipeline_job_id": "fixture:input.pdf:1", "selected_pdf_receipt": selected,
        "input_sha256": selected["sha256"], "configured_input_sha256": sha256(json.dumps(configured,
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    if changed is not None:
        pipeline[changed] = {"sha256": "foreign"} if changed == "selected_pdf_receipt" else "foreign"
    with store.begin() as session:
        session.add(OcrJob(id="handoff-job", status="running", input_reference=payload["pdf_uri"],
            metrics={"nonworkflow_execution": capture}))
    if changed is not None:
        with pytest.raises(ValueError, match="pipeline_.*mismatch"):
            with delivery.claim_context(claim), store.begin() as session:
                job = session.get(OcrJob, "handoff-job")
                job.status = "awaiting_output"
                job.output_reference = pipeline["output_reference"]
                job.metrics = {**job.metrics, "pipeline_delivery_receipt": pipeline}
        with store() as session:
            assert session.get(IngestDelivery, delivery_id).handoff_receipt is None
        return
    validations = []
    monkeypatch.setattr(ocr_job_service, "validate_own_pipeline_result",
        lambda result, reference, **kwargs: validations.append((result, reference)))
    with delivery.claim_context(claim), store.begin() as session:
        job = session.get(OcrJob, "handoff-job")
        job.status = "awaiting_output"
        job.output_reference = pipeline["output_reference"]
        job.metrics = {**job.metrics, "pipeline_delivery_receipt": pipeline}
    with delivery.claim_context(claim):
        assert delivery.acknowledge_claim(claim) == "durable_handoff"
    assert validations[0][0]["input_generation"] == "1"
    with delivery.recovery_context(capture) as eligible:
        assert eligible
        with db.session_scope() as session:
            assert delivery.fence_original_write(session, claim["lease"])
    with delivery.recovery_context({**capture, "ingest_lease": {**claim["lease"], "claim_id": "foreign"}}) as eligible:
        assert not eligible


def test_nonpersisted_handoff_cannot_ack(store, payload):
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    claim = delivery.claim_delivery(delivery_id, generation)
    with delivery.claim_context(claim), pytest.raises(ValueError, match="owned_receipt_missing"):
        delivery.acknowledge_claim(claim)


def test_already_exists_tombstone_renews_only_transport_contract(store, payload, monkeypatch):
    from google.cloud import tasks_v2
    from google.api_core.exceptions import AlreadyExists, NotFound
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, contract = intent(store)
    def missing(**kwargs):
        raise NotFound("authored_tombstone")
    def tombstone(**kwargs):
        raise AlreadyExists("authored_tombstone")
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", lambda: SimpleNamespace(get_task=missing, create_task=tombstone))
    assert dispatch.dispatch(delivery_id, generation)["action"] == "renewed"
    with store() as session:
        row = session.get(IngestDelivery, delivery_id)
        job = session.get(IngestJob, row.job_id)
        assert row.generation == generation + 1 and row.business_generation == job.generation
        assert row.input_digest == job.input_digest and job.attempts == 0
        assert row.task_contract == dispatch.renew_contract(contract, delivery_id, generation + 1)


def test_fair_visits_are_saved_before_provider_failure(store, payload, monkeypatch):
    from google.cloud import tasks_v2
    for index in range(2):
        ingest.create_ingest_receipt({**payload, "message_id": f"fair-{index}"})
    observed = []
    def unavailable(**kwargs):
        with store() as session:
            name = kwargs["request"]["name"]
            row = next(row for row in session.execute(select(IngestDelivery)).scalars() if row.task_contract["name"] == name)
            assert row.visit_count > 0
            observed.append(row.id)
        raise RuntimeError("provider_unavailable")
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", lambda: SimpleNamespace(get_task=unavailable))
    dispatch.drain(1)
    dispatch.drain(1)
    assert len(set(observed)) == 2


def test_actual_mutex_excludes_second_owner_and_drain_renewal(store, payload):
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    with exclusion.execution_mutex("ingest-original:" + payload["message_id"]) as held:
        assert held
        with pytest.raises(ValueError, match="execution_busy"):
            delivery.process(delivery_id, generation)
        assert delivery.renew_missing(delivery_id, generation)["action"] == "active_owner"
    assert intent(store)[1] == generation


def test_budget_refusal_is_owned_terminal_not_new_business_retry(store, payload, monkeypatch):
    monkeypatch.setattr(ingest, "_max_auto_attempts", lambda: 1)
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    with store.begin() as session:
        session.get(IngestJob, payload["message_id"]).generation_attempts = 1
    assert delivery.process(delivery_id, generation)["result"] == "business_failed"
    with store() as session:
        row = session.get(IngestDelivery, delivery_id)
        job = session.get(IngestJob, row.job_id)
        assert row.owner_lease["claim_refusal"] == "budget_exhausted"
        assert job.failure_kind == "budget_exhausted" and job.generation_attempts == 1
    assert delivery.drain_candidates(10) == []


@pytest.fixture
def successor_producer(store, payload, monkeypatch):
    """Actual terminal producer; recognizer and order snapshot CAS are not exercised."""
    from src.models.order import Order
    from src.models.ocr_job import OcrJob
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.services import order_service, ingest_delivery_successor_service as successor
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_ON_INGEST", "1")
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_PROVIDER", "gemini")
    ingest.create_ingest_receipt(payload)
    delivery_id, generation, _ = intent(store)
    claim = delivery.claim_delivery(delivery_id, generation)
    capture = {"job_id": "producer-parent-job", "order_id": "producer-order", "job_order_id": "producer-order",
        "execution_id": "producer-original-execution", "input_reference": payload["pdf_uri"],
        "ingest_lease": claim["lease"], "start_orders": {}, "selected_pdf_receipt": {"sha256": payload["content_sha256"]},
        "orders": {"producer-order": {"configured_input": {"zero": 0, "unknown": None}}}}
    # This fixture isolates full configured/PDF/order snapshot materialization;
    # refusal tests below replace this adapter with an explicit observed difference.
    from src.services import workflow_attempt_guard_service as guard
    monkeypatch.setattr(guard, "_order_execution_input", lambda session, order, **kwargs: deepcopy(capture["orders"][order.id]))
    own_payload = {"table_raw": "authored metadata only", "unknown": {"zero": 0, "null": None},
        "_ocr_execution": {"job_id": capture["job_id"], "nonworkflow_execution_id": capture["execution_id"],
            "evidence_run_id": "producer-evidence", "workflow_attempt_id": None, "workflow_revision": None}}
    with store.begin() as session:
        session.add(Order(id="producer-order", facility_code=payload["facility_hint"],
            document_uri=payload["pdf_uri"], message_id=payload["message_id"]))
        session.add(OrderDocument(id="producer-document", storage_uri=payload["pdf_uri"],
            source_email_id=payload["message_id"], received_at=datetime(2026, 10, 7), status="success"))
        session.add(OcrJob(id=capture["job_id"], order_id="producer-order", status="running",
            input_reference=payload["pdf_uri"], metrics={"nonworkflow_execution": capture}))
        session.add(OrderOcrEvidenceRun(id="producer-evidence", order_id="producer-order", schema_version="v1_legacy",
            payload_json=own_payload, artifact_digest="recorded-authored-digest", status="ready"))
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence",
        lambda session, job_id, order_id, **kwargs: delivery.fence_original_write(session, claim["lease"]))
    def produce(*, aborted=False):
        with delivery.claim_context(claim), store.begin() as session:
            receipt = order_service._complete_first_pass_job_in_session(session,
                session.get(Order, "producer-order"), session.get(OrderDocument, "producer-document"), capture,
                {"id": "producer-evidence", "selectable": True, "payload_json": own_payload}, applied=True)
            if aborted:
                raise RuntimeError("authored_transaction_abort")
            return receipt
    return {"produce": produce, "capture": capture, "claim": claim, "payload": own_payload,
        "service": successor, "parent_id": delivery_id, "generation": generation}


def test_actual_first_pass_transaction_rollback_removes_successor(store, successor_producer):
    from src.models.ocr_job import OcrJob
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    with pytest.raises(RuntimeError, match="transaction_abort"):
        successor_producer["produce"](aborted=True)
    with store() as session:
        assert session.execute(select(IngestDeliverySuccessor)).first() is None
        assert session.get(OcrJob, "producer-parent-job").status == "running"


def test_actual_eligible_producer_saves_distinct_job_request_and_preserves_parent(store, successor_producer, monkeypatch):
    from src.models.ocr_job import OcrJob
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    receipt = successor_producer["produce"]()
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_PROVIDER", "openai")
    with store() as session:
        row = session.execute(select(IngestDeliverySuccessor)).scalar_one()
        child = session.get(OcrJob, row.job_id)
        assert row.job_id != receipt["job_id"]
        assert child.metrics["nonworkflow_execution"]["execution_id"] != receipt["execution_id"]
        assert row.original["request"] == {"ocr_prompt": None, "prompt_preset": None,
            "ocr_provider": "gemini", "ocr_model": None, "llm_assist": True}
        assert row.original["payload"] == successor_producer["payload"]
        assert session.get(OcrJob, receipt["job_id"]).metrics["first_pass_terminal_receipt"] == receipt
        assert "first_pass_terminal_receipt" not in child.metrics
        identity = row.id
    with pytest.raises(ValueError, match="predecessor_(ack_pending|business_unsettled)"):
        successor_producer["service"].process(identity, 1)


def test_disabled_actual_producer_has_no_successor(store, successor_producer, monkeypatch):
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_ON_INGEST", "0")
    successor_producer["produce"]()
    with store() as session:
        assert session.execute(select(IngestDeliverySuccessor)).first() is None


@pytest.mark.parametrize("outcome", ["done", "failed", "empty", "storage"])
def test_successor_owned_terminal_ack_and_no_parent_overwrite(store, successor_producer, monkeypatch, outcome):
    from src.models.ocr_job import OcrJob
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.workers import ingest_worker
    receipt = successor_producer["produce"]()
    claim = successor_producer["claim"]
    with delivery.claim_context(claim):
        assert ingest.complete_ingest_job(claim["lease"]["id"], lease=claim["lease"])
        assert delivery.acknowledge_claim(claim) == "business_done"
    with store() as session:
        row = session.execute(select(IngestDeliverySuccessor)).scalar_one()
        identity, child_id = row.id, row.job_id
    calls = []
    def actual_receipt_adapter(order_id, **kwargs):
        calls.append(kwargs)
        assert kwargs["original_request"]["llm_assist"] is True and kwargs["provider"] == "gemini"
        # The outer real process owns the original mutex during this callback.
        with store() as session:
            before = {table.name: [dict(row) for row in session.execute(
                select(table).order_by(*table.primary_key.columns)).mappings()]
                for table in models.Base.metadata.sorted_tables}
        assert service.dispatch(identity, 1) == {"action": "active_owner"}
        with pytest.raises(ValueError, match="^auto_llm_execution_busy$"):
            service.process(identity, 1)
        assert len(calls) == 1
        with store() as session:
            assert {table.name: [dict(row) for row in session.execute(
                select(table).order_by(*table.primary_key.columns)).mappings()]
                for table in models.Base.metadata.sorted_tables} == before
        if outcome == "storage":
            raise RuntimeError("authored_terminal_storage_refusal")
        if outcome == "empty":
            return None
        with store.begin() as session:
            job = session.get(OcrJob, child_id)
            if outcome == "done":
                session.add(OrderOcrEvidenceRun(id="successor-result", order_id=order_id, schema_version="v2_evidence_rerun",
                    payload_json={"metadata_only": True, "_ocr_execution": {"job_id": child_id,
                        "evidence_run_id": "successor-result", "nonworkflow_execution_id": kwargs["execution_input"]["execution_id"]}},
                    artifact_digest="authored-result-digest", status="ready"))
                session.flush()
            job.status = outcome
            job.metrics = {**job.metrics, "rerun_terminal_receipt": {"job_id": child_id, "order_id": order_id,
                "execution_id": kwargs["execution_input"]["execution_id"], "status": outcome,
                "evidence_run_id": "successor-result" if outcome == "done" else None, "error": None if outcome == "done" else "authored_business_failure"}}
    monkeypatch.setattr(ingest_worker, "_run_auto_llm_reparse", actual_receipt_adapter)
    service = successor_producer["service"]
    if outcome in {"empty", "storage"}:
        with pytest.raises((ValueError, RuntimeError)):
            service.process(identity, 1)
    else:
        assert service.process(identity, 1)["result"] == ("business_done" if outcome == "done" else "business_failed")
        assert service.process(identity, 1)["duplicate"]
        assert len(calls) == 1
    with store() as session:
        assert session.get(OcrJob, "producer-parent-job").metrics["first_pass_terminal_receipt"] == receipt
        assert session.get(IngestDelivery, successor_producer["parent_id"]).state == "ACKNOWLEDGED"


def test_successor_original_in_place_mutation_is_refused(store, successor_producer):
    from sqlalchemy.orm.attributes import flag_modified
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    successor_producer["produce"]()
    with pytest.raises(ValueError, match="original_immutable"):
        with store.begin() as session:
            row = session.execute(select(IngestDeliverySuccessor)).scalar_one()
            row.original["payload"]["unknown"]["zero"] = False
            flag_modified(row, "original")


@pytest.mark.parametrize("mutation", [None, "request", "digest", "execution"])
def test_successor_pending_handoff_and_original_recovery_fence(store, successor_producer, monkeypatch, mutation):
    import json
    from hashlib import sha256
    from src.models.ocr_job import OcrJob
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.workers import ingest_worker
    from src.services import ocr_job_service
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    successor_producer["produce"]()
    claim = successor_producer["claim"]
    with delivery.claim_context(claim):
        assert ingest.complete_ingest_job(claim["lease"]["id"], lease=claim["lease"])
        assert delivery.acknowledge_claim(claim) == "business_done"
    with store() as session:
        row = session.execute(select(IngestDeliverySuccessor)).scalar_one()
        identity, child_id, original = row.id, row.job_id, deepcopy(row.original)
    capture = original["execution_input"]
    configured = capture["orders"][capture["order_id"]]["configured_input"]
    pipeline = {"job_id": child_id, "order_id": capture["order_id"], "execution_id": capture["execution_id"],
        "workflow_attempt_id": None, "workflow_revision": None, "selected_input_reference": capture["input_reference"],
        "input_reference": "gs://fixture/successor.pdf", "output_reference": "gs://fixture/successor.json",
        "generation": "2", "pipeline_job_id": "fixture:successor.pdf:2",
        "selected_pdf_receipt": capture["selected_pdf_receipt"], "input_sha256": capture["selected_pdf_receipt"]["sha256"],
        "configured_input_sha256": sha256(json.dumps(configured, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()}
    waiting = {"job_id": child_id, "order_id": capture["order_id"], "execution_id": capture["execution_id"],
        "workflow_attempt_id": None, "workflow_revision": None, "output_reference": pipeline["output_reference"],
        "request_kwargs": deepcopy(original["request"])}
    if mutation == "request":
        waiting["request_kwargs"]["llm_assist"] = 1
    elif mutation == "digest":
        pipeline["input_sha256"] = "foreign"
    elif mutation == "execution":
        waiting["execution_id"] = "foreign"
    def pending_adapter(*args, **kwargs):
        with store.begin() as session:
            job = session.get(OcrJob, child_id)
            job.status, job.output_reference = "awaiting_output", pipeline["output_reference"]
            job.metrics = {**job.metrics, "pipeline_delivery_receipt": pipeline, "pending_receipt": waiting}
        return None
    monkeypatch.setattr(ingest_worker, "_run_auto_llm_reparse", pending_adapter)
    monkeypatch.setattr(ocr_job_service, "validate_own_pipeline_result", lambda *args, **kwargs: None)
    service = successor_producer["service"]
    if mutation is not None:
        with pytest.raises(ValueError):
            service.process(identity, 1)
        with store() as session:
            assert session.get(IngestDeliverySuccessor, identity).handoff_receipt is None
        return
    assert service.process(identity, 1)["result"] == "durable_handoff"
    with service.recovery_context(capture) as eligible, nonworkflow_execution_context(capture):
        assert eligible
        with db.session_scope() as session:
            assert ingest.lock_ingest_lease(session, capture["ingest_lease"], terminal=True)
    with service.owned({"id": identity, "generation": 1, "token": "old-token"}), nonworkflow_execution_context(capture):
        with db.session_scope() as session:
            assert not ingest.lock_ingest_lease(session, capture["ingest_lease"], terminal=True)
    # Exercise the actual recovery dispatcher and failure persister with the
    # snapshot CAS adapter isolated; original successor fencing remains real.
    from src.services import order_service
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution
    with store() as session:
        candidate = ocr_job_service._job_to_dict(session.get(OcrJob, child_id))
    monkeypatch.setattr(ingest, "list_ocr_recovery_candidates", lambda limit: [candidate])
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence",
        lambda session, job_id, order_id, **kwargs: service.fence(session,
            current_nonworkflow_execution(), capture["ingest_lease"]))
    monkeypatch.setattr(ingest_worker, "_recover_captured_ocr_job",
        lambda job: ingest_worker._mark_ocr_job_final_failed(job["id"], error_message="authored_downstream_failure") is not None)
    assert ingest_worker.run_ocr_job_recovery_once(1) == 1
    with store() as session:
        row = session.get(IngestDeliverySuccessor, identity)
        assert row.terminal_receipt["receipt"]["status"] == "failed"
        assert row.terminal_receipt["claim"] == row.handoff_receipt["claim"]
        assert session.get(IngestJob, capture["ingest_lease"]["id"]).status == "done"


def test_successor_missing_generation_before_duplicate_ack(store, successor_producer):
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    successor_producer["produce"]()
    with store() as session:
        identity = session.execute(select(IngestDeliverySuccessor.id)).scalar_one()
    for generation in (None, True, 0, 2):
        with pytest.raises(ValueError, match="generation"):
            successor_producer["service"].process(identity, generation)


@pytest.mark.parametrize("entry", ["process", "drain"])
@pytest.mark.parametrize("conflict", ["superseded", "evidence", "snapshot", "prepared", "predecessor_state"])
def test_owned_permanent_successor_refusal_is_saved_and_not_redispatched(store, successor_producer, monkeypatch, entry, conflict):
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.models.ocr_job import OcrJob
    from src.models.order import Order
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.services import workflow_attempt_guard_service as guard
    from src.workers import ingest_worker
    successor_producer["produce"]()
    claim = successor_producer["claim"]
    with delivery.claim_context(claim):
        assert ingest.complete_ingest_job(claim["lease"]["id"], lease=claim["lease"])
        delivery.acknowledge_claim(claim)
    with store() as session:
        row = session.execute(select(IngestDeliverySuccessor)).scalar_one()
        identity, child = row.id, row.job_id
        parent_receipt = deepcopy(session.get(OcrJob, row.predecessor_job_id).metrics["first_pass_terminal_receipt"])
        before_order = {column.key: getattr(session.get(Order, "producer-order"), column.key) for column in Order.__table__.columns}
    if conflict == "snapshot":
        monkeypatch.setattr(guard, "_order_execution_input", lambda *args, **kwargs: {"authored_observed_difference": True})
    else:
        with store.begin() as session:
            if conflict == "superseded":
                session.get(IngestJob, claim["lease"]["id"]).generation = "authored-new-generation"
            elif conflict == "evidence":
                session.get(OrderOcrEvidenceRun, "producer-evidence").artifact_digest = "authored-other-digest"
            elif conflict == "predecessor_state":
                # Physical corruption after terminal ownership ended, not an owned write.
                from sqlalchemy import update
                session.execute(update(OcrJob.__table__).where(OcrJob.id == "producer-parent-job").values(status="failed"))
            else:
                job = session.get(OcrJob, "producer-parent-job")
                capture = deepcopy(job.metrics["nonworkflow_execution"])
                capture["orders"] = {"producer-order": {"authored_observed_difference": True}}
                from sqlalchemy import update
                session.execute(update(OcrJob.__table__).where(OcrJob.id == job.id).values(
                    metrics={**job.metrics, "nonworkflow_execution": capture}))
    def forbidden(*args, **kwargs):
        raise AssertionError("Refused original must not compute or submit transport")
    monkeypatch.setattr(ingest_worker, "_run_auto_llm_reparse", forbidden)
    monkeypatch.setattr(dispatch, "inspect_task", forbidden)
    service = successor_producer["service"]
    if entry == "process":
        assert service.process(identity, 1)["acknowledged"] is True
    else:
        assert service.drain(10)[0]["action"] in {"business_refused", "business_superseded"}
    with store() as session:
        row = session.get(IngestDeliverySuccessor, identity)
        assert row.state == "REFUSED" and row.claim_token
        assert row.terminal_receipt["kind"] == "owned_refusal"
        assert row.terminal_receipt["warning"]["domain_adoption"] is False
        assert session.get(OcrJob, child).status == "failed"
        assert session.get(OcrJob, row.predecessor_job_id).metrics["first_pass_terminal_receipt"] == parent_receipt
        assert {column.key: getattr(session.get(Order, "producer-order"), column.key) for column in Order.__table__.columns} == before_order
    assert service.drain(10) == []
    assert service.process(identity, 1)["duplicate"] is True
    assert service.diagnostic(identity)["warning"]["domain_adoption"] is False
    from src.services.ocr_job_service import get_job
    assert get_job(child)["metrics"]["delivery_warning"]["domain_adoption"] is False
    with store() as session:
        boundary = deepcopy(session.get(OcrJob, child).metrics["nonworkflow_execution"])
    with service.recovery_context(boundary) as eligible:
        assert eligible is False
    with service.owned({"id": identity, "generation": 1, "token": "retired-token"}), service._exclusive("ingest-original:" + claim["lease"]["id"]) as acquired:
        assert acquired
        with pytest.raises(ValueError, match="owner_lost"):
            service.refuse_if_known(identity, 1)


def test_unknown_snapshot_failure_is_not_owned_refusal_or_ack(store, successor_producer, monkeypatch):
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.services import workflow_attempt_guard_service as guard
    successor_producer["produce"]()
    with store() as session:
        identity = session.execute(select(IngestDeliverySuccessor.id)).scalar_one()
    def unknown(*args, **kwargs):
        raise OSError("authored transient source lookup failure")
    monkeypatch.setattr(guard, "_order_execution_input", unknown)
    with pytest.raises(OSError, match="transient"):
        successor_producer["service"].process(identity, 1)
    with store() as session:
        row = session.get(IngestDeliverySuccessor, identity)
        assert row.state != "REFUSED" and row.terminal_receipt is None


def test_refusal_requires_original_mutex_and_exact_generation(store, successor_producer, monkeypatch):
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.services import workflow_attempt_guard_service as guard
    successor_producer["produce"]()
    with store() as session:
        identity = session.execute(select(IngestDeliverySuccessor.id)).scalar_one()
    service = successor_producer["service"]
    monkeypatch.setattr(guard, "_order_execution_input", lambda *args, **kwargs: {"authored_difference": True})
    with pytest.raises(ValueError, match="physical_owner_missing"):
        service.refuse_if_known(identity, 1)
    with service._exclusive("ingest-original:" + successor_producer["claim"]["lease"]["id"]) as acquired:
        assert acquired
        for generation in (None, True, 2):
            with pytest.raises(ValueError, match="generation"):
                service.refuse_if_known(identity, generation)
    with store() as session:
        row = session.get(IngestDeliverySuccessor, identity)
        assert row.terminal_receipt is None and row.state != "REFUSED"
