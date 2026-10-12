"""Owned SQLite poll transitions; external GCS double, no native OCR/live proof."""

from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

from google.cloud import storage
from google.oauth2 import id_token
import pytest

from modernization_owned_pipeline_transport import install_owned_pipeline_transport
from test_modernization_ocr_permanent_failure import (
    owned_poll, poll, response_from_receipt,
)
from test_modernization_owned_ocr_workflow import (
    FACILITY, JOB,
    _owned_workflow_database_snapshot,
    owned_producer,
)
from src.models.ocr_job import OcrJob
from src import db
from src.services import ocr_evidence_service, ocr_job_service, order_service, storage_service
from src.services import ocr_pipeline_service as pipeline
from src.services.ocr_pipeline_state_store import load_pipeline_state
from src.services.storage_uri import parse_gcs_uri
from src.workers import ingest_worker


def install_transition(monkeypatch, case, terminal, *, partial=None):
    source = parse_gcs_uri(case.receipt["output_reference"])
    payloads = [deepcopy(partial) if partial is not None else
                response_from_receipt(case.receipt, "running"), deepcopy(terminal)]
    encoded = [json.dumps(payload).encode("utf-8") for payload in payloads]
    generations = (73, 74)
    events = []
    created = []

    class Blob:
        def __init__(self, index):
            self.index = index
            self.generation = None

        def exists(self, **kwargs):
            events.append(("exists", self.index, deepcopy(kwargs)))
            assert kwargs == {"timeout": 4.5, "retry": None}
            return True

        def reload(self, **kwargs):
            events.append(("reload", self.index, deepcopy(kwargs)))
            assert kwargs == {"timeout": 4.5, "retry": None}
            self.generation = str(generations[self.index])

        def download_as_bytes(self, **kwargs):
            events.append(("download", self.index, deepcopy(kwargs)))
            assert self.generation == str(generations[self.index])
            assert kwargs == {
                "if_generation_match": generations[self.index],
                "timeout": 4.5, "retry": None,
            }
            return encoded[self.index]

    class Client:
        def bucket(self, bucket):
            assert bucket == source.bucket
            events.append(("bucket", bucket))

            def blob(name):
                assert name == source.object_name
                index = len(created)
                assert index < 2, "terminal result must not be retried"
                assert case.clock.sleeps == ([] if index == 0 else [1])
                events.append(("blob", name))
                item = Blob(index)
                created.append(item)
                return item

            return SimpleNamespace(blob=blob)

    monkeypatch.setattr(storage, "Client", Client)
    return source, payloads, encoded, events


def assert_transition_unchanged(case, before, receipt, files, transition):
    source, payloads, encoded, events = transition
    expected = []
    for index, generation in enumerate((73, 74)):
        expected.extend([
            ("bucket", source.bucket), ("blob", source.object_name),
            ("exists", index, {"timeout": 4.5, "retry": None}),
            ("reload", index, {"timeout": 4.5, "retry": None}),
            ("download", index, {"if_generation_match": generation,
                                 "timeout": 4.5, "retry": None}),
        ])
    assert events == expected
    assert case.clock.sleeps == [1]
    assert case.clock.now == 1
    assert _owned_workflow_database_snapshot() == before
    assert case.receipt == receipt
    assert ocr_job_service.own_pipeline_delivery_for_output(receipt["output_reference"]) == receipt
    assert [json.loads(value) for value in encoded] == payloads
    assert {path: path.read_bytes() for path in files} == files


def original_state(case):
    return (
        _owned_workflow_database_snapshot(), deepcopy(case.receipt),
        {path: path.read_bytes() for path in (case.f.pdf, case.f.master_json, case.f.master_copy)},
    )


def test_partial_then_done_keeps_input_receipt_across_output_generations(owned_poll, monkeypatch):
    before, receipt, files = original_state(owned_poll)
    done = response_from_receipt(receipt)
    transition = install_transition(monkeypatch, owned_poll, done)
    result = poll(owned_poll)
    assert result == done
    assert result["status"] == result["stage"] == "done"
    assert result["input_generation"] == receipt["generation"]
    assert result["execution_owner"] == done["execution_owner"]
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("field,replacement", [
    ("job_id", "foreign-job"),
    ("order_id", "foreign-order"),
    ("workflow_attempt_id", "foreign-attempt"),
    ("workflow_revision", 1),
    ("nonworkflow_execution_id", "foreign-execution"),
])
def test_foreign_terminal_owner_after_partial_stops_without_retry(owned_poll, monkeypatch, field, replacement):
    before, receipt, files = original_state(owned_poll)
    done = response_from_receipt(receipt)
    assert done["execution_owner"][field] != replacement
    done["execution_owner"][field] = replacement
    transition = install_transition(monkeypatch, owned_poll, done)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_terminal_owner_mismatch$"):
        poll(owned_poll)
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("field", [
    "input_reference", "input_generation", "output_reference", "job_id",
    "input_sha256", "configured_input_sha256",
])
def test_terminal_source_drift_after_partial_stops_without_retry(owned_poll, monkeypatch, field):
    before, receipt, files = original_state(owned_poll)
    done = response_from_receipt(receipt)
    if field == "input_generation":
        done[field] = str(int(done[field]) + 1)
    elif field.endswith("sha256"):
        done[field] = ("0" if done[field][0] != "0" else "1") + done[field][1:]
    else:
        done[field] += "-foreign"
    transition = install_transition(monkeypatch, owned_poll, done)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_result_generation_mismatch$"):
        poll(owned_poll)
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


def test_failed_after_partial_keeps_current_stage_refusal_contract(owned_poll, monkeypatch):
    """Current validator contract, not resolution of the old failed-success oracle."""
    before, receipt, files = original_state(owned_poll)
    failed = response_from_receipt(receipt, "failed")
    failed["error"] = "synthetic external provider failure"
    transition = install_transition(monkeypatch, owned_poll, failed)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_required_stage_failed$"):
        poll(owned_poll)
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("status,stage", [
    ("running", None), ("pending", None), ("queued", None),
    ("running", "download"), ("running", "ocr"), ("running", "upload"),
    ("running", "synthetic-progress-phase"),
])
def test_nonterminal_optional_stage_waits_for_exact_done(owned_poll, monkeypatch, status, stage):
    before, receipt, files = original_state(owned_poll)
    partial = response_from_receipt(receipt, status)
    if stage is None:
        partial.pop("stage")
    else:
        partial["stage"] = stage
    # Match the actual asynchronous acknowledgment / handoff shape, not a terminal.
    for key in ("execution_owner", "input_sha256", "configured_input_sha256",
                "canonical_stages", "canonical_adoption_allowed"):
        partial.pop(key)
    assert ocr_job_service.validate_own_pipeline_result(partial, receipt["output_reference"]) is partial
    assert pipeline.is_ocr_pipeline_output_pending(partial) is True
    done = response_from_receipt(receipt)
    transition = install_transition(monkeypatch, owned_poll, done, partial=partial)
    assert poll(owned_poll) == done
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("status", [
    pytest.param("<missing>", id="missing"), None, "", "Done", "DONE", " done", "done ",
    "unknown", 0, [], {},
])
def test_noncanonical_status_cannot_be_promoted_by_done_stage(owned_poll, monkeypatch, status):
    before, receipt, files = original_state(owned_poll)
    invalid = response_from_receipt(receipt)
    if status == "<missing>":
        invalid.pop("status")
    else:
        invalid["status"] = status
    transition = install_transition(monkeypatch, owned_poll, invalid)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_result_status_invalid$"):
        poll(owned_poll)
    assert pipeline.is_ocr_pipeline_output_pending(invalid) is False
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("status,stage", [
    (status, stage) for status in ("running", "pending", "queued")
    for stage in ("done", "error", "failed")
] + [("done", "error"), ("done", "failed"), ("error", "done"), ("failed", "done")])
def test_terminal_stage_contradiction_stops_without_retry(owned_poll, monkeypatch, status, stage):
    before, receipt, files = original_state(owned_poll)
    invalid = response_from_receipt(receipt, status)
    invalid["stage"] = stage
    transition = install_transition(monkeypatch, owned_poll, invalid)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_result_status_stage_mismatch$"):
        poll(owned_poll)
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("change", [
    {"canonical_adoption_allowed": False}, {"canonical_stages": None},
    {"canonical_stages": {"classification": "passed", "roi": "failed", "correction": "passed"}},
    {"canonical_stages": {"classification": "passed", "roi": "passed"}},
    {"canonical_stages": {"classification": "passed", "roi": "passed", "correction": "passed", "extra": "passed"}},
])
def test_terminal_canonical_stage_refusal_after_partial(owned_poll, monkeypatch, change):
    before, receipt, files = original_state(owned_poll)
    invalid = {**response_from_receipt(receipt), **change}
    transition = install_transition(monkeypatch, owned_poll, invalid)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_required_stage_failed$"):
        poll(owned_poll)
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


def test_stage_absent_done_still_requires_and_preserves_terminal_receipt(owned_poll, monkeypatch):
    before, receipt, files = original_state(owned_poll)
    done = response_from_receipt(receipt)
    done.pop("stage")
    transition = install_transition(monkeypatch, owned_poll, done)
    assert poll(owned_poll) == done
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("payload", [
    {"status": "Running"}, {"status": " pending "}, {"stage": "upload"},
    {"status": "done", "stage": "running"}, {"status": "error", "stage": "upload"},
])
def test_pending_classifier_does_not_normalize_or_promote_stage(payload):
    assert pipeline.is_ocr_pipeline_output_pending(payload) is False


@pytest.mark.parametrize("field", [
    "job_id", "order_id", "workflow_attempt_id", "workflow_revision", "nonworkflow_execution_id",
])
def test_diagnostic_error_never_bypasses_terminal_owner(owned_poll, monkeypatch, field):
    before, receipt, files = original_state(owned_poll)
    error = response_from_receipt(receipt, "error")
    error["execution_owner"][field] = "foreign-owner"
    transition = install_transition(monkeypatch, owned_poll, error)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_terminal_owner_mismatch$"):
        poll(owned_poll)
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


def test_error_is_refused_by_default_but_preserved_by_diagnostic_poll(owned_poll, monkeypatch):
    before, receipt, files = original_state(owned_poll)
    error = response_from_receipt(receipt, "error")
    error["error"] = "synthetic provider error"
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_required_stage_failed$"):
        ocr_job_service.validate_own_pipeline_result(error, receipt["output_reference"])
    assert ocr_job_service.validate_own_pipeline_result(
        error, receipt["output_reference"], allow_terminal_error=True,
    ) is error
    transition = install_transition(monkeypatch, owned_poll, error)
    assert poll(owned_poll) == error
    assert_transition_unchanged(owned_poll, before, receipt, files, transition)


@pytest.mark.parametrize("loader", ["once", "retry", "ingest-completed"])
def test_default_loaders_refuse_error_without_retry_or_database_write(owned_poll, monkeypatch, loader):
    before, receipt, files = original_state(owned_poll)
    error = response_from_receipt(receipt, "error")
    error["error"] = "synthetic provider error"
    source = parse_gcs_uri(receipt["output_reference"])
    reads = []

    class Blob:
        def download_as_bytes(self, **kwargs):
            assert kwargs == {}
            reads.append(receipt["output_reference"])
            assert len(reads) == 1, "lineage refusal must not become a retry"
            return json.dumps(error).encode()

    class Client:
        def bucket(self, bucket):
            assert bucket == source.bucket

            def blob(name):
                assert name == source.object_name
                return Blob()

            return SimpleNamespace(blob=blob)

    monkeypatch.setattr(storage, "Client", Client)
    monkeypatch.setattr(storage_service._GCS_CLIENT_LOCAL, "client", None, raising=False)
    monkeypatch.setattr(order_service, "time", owned_poll.clock)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_required_stage_failed$"):
        if loader == "once":
            order_service._load_pipeline_output_once(receipt["output_reference"])
        elif loader == "retry":
            order_service._load_pipeline_output_with_retry(receipt["output_reference"], wait_seconds_override=3)
        else:
            ingest_worker._load_completed_ocr_output(receipt["output_reference"])
    assert reads == [receipt["output_reference"]]
    assert owned_poll.clock.sleeps == []
    assert _owned_workflow_database_snapshot() == before
    assert ocr_job_service.own_pipeline_delivery_for_output(receipt["output_reference"]) == receipt
    assert {path: path.read_bytes() for path in files} == files


@pytest.mark.parametrize("consumer", ["first-pass", "evidence"])
def test_error_cannot_pass_firstpass_or_evidence_validation(owned_poll, consumer):
    before, receipt, files = original_state(owned_poll)
    error = response_from_receipt(receipt, "error")
    error.update(engine="yomitoku", error="synthetic provider error")
    with pytest.raises(ocr_job_service.OwnPipelineLineageError,
                       match="^ocr_pipeline_required_stage_failed$"):
        if consumer == "first-pass":
            with db.session_scope() as session:
                ocr_job_service.validate_own_first_pass_recognizer(error, write_session=session)
        else:
            ocr_evidence_service.persist_evidence_run(
                receipt["order_id"], error, workflow_job_id=JOB,
                template_version_id=owned_poll.f.projection_id,
                status="diagnostic_only", producer_version="synthetic-component",
            )
    assert _owned_workflow_database_snapshot() == before
    assert ocr_job_service.own_pipeline_delivery_for_output(receipt["output_reference"]) == receipt
    assert {path: path.read_bytes() for path in files} == files


def run_with_published_output(case, monkeypatch, change):
    published = []

    def publish(key):
        receipt = ocr_job_service.own_pipeline_delivery_for_output(None)
        source = parse_gcs_uri(receipt["output_reference"])
        assert key == (source.bucket, source.object_name)
        payload = {**response_from_receipt(receipt), **deepcopy(change)}
        published.append((deepcopy(receipt), deepcopy(payload)))
        assert len(published) == 1
        objects[key] = (json.dumps(payload).encode(), {}, 73, "application/json")

    objects, events = install_owned_pipeline_transport(monkeypatch, on_missing=publish)
    result = pipeline.run_ocr_pipeline(
        pdf_bytes=case.f.pdf.read_bytes(), job_id=JOB, facility_id=FACILITY,
        input_reference=str(case.f.pdf), force_upload=True, wait_for_output=True,
    )
    receipt, payload = published[0]
    assert result == payload
    assert result["input_generation"] == receipt["generation"]
    assert ocr_job_service.own_pipeline_delivery_for_output(result["output_reference"]) == receipt
    input_source = parse_gcs_uri(receipt["input_reference"])
    assert objects[(input_source.bucket, input_source.object_name)][0] == case.f.pdf.read_bytes()
    assert len([event for event in events if event[0] == "upload"]) == 2
    return result, receipt


def assert_only_uploaded_receipt_changed(before, after, receipt):
    expected = deepcopy(before)
    jobs = [row for row in expected[OcrJob.__tablename__] if row["id"] == JOB]
    assert len(jobs) == 1
    jobs[0]["metrics"]["pipeline_delivery_receipt"] = receipt
    assert after == expected


def test_actual_run_records_success_then_error_without_overwriting_success_stamp(owned_poll, monkeypatch):
    before, _receipt, files = original_state(owned_poll)
    done, done_receipt = run_with_published_output(owned_poll, monkeypatch, {})
    successful = load_pipeline_state()
    assert successful["status"] == "ok"
    assert successful["last_job_id"] == JOB
    assert successful["last_output_ref"] == done["output_reference"]
    assert successful["last_success_at"]
    assert successful["last_error"] is None
    after_done = _owned_workflow_database_snapshot()
    assert_only_uploaded_receipt_changed(before, after_done, done_receipt)
    error, error_receipt = run_with_published_output(owned_poll, monkeypatch, {
        "status": "error", "stage": "error", "error": "synthetic provider error",
    })
    failed = load_pipeline_state()
    assert failed["status"] == "error"
    assert failed["last_error"] == error["error"]
    assert failed["last_error_at"]
    assert failed["last_job_id"] == JOB
    assert failed["last_success_at"] == successful["last_success_at"]
    assert failed["last_output_ref"] == successful["last_output_ref"]
    assert error["output_reference"] != done["output_reference"]
    assert_only_uploaded_receipt_changed(after_done, _owned_workflow_database_snapshot(), error_receipt)
    assert owned_poll.clock.sleeps == []
    assert {path: path.read_bytes() for path in files} == files


@pytest.mark.parametrize("change,reason", [
    ({"status": "Done"}, "ocr_pipeline_result_status_invalid"),
    ({"status": "running"}, "ocr_pipeline_result_status_stage_mismatch"),
    ({"status": "failed", "stage": "failed"}, "ocr_pipeline_required_stage_failed"),
    ({"execution_owner": {}}, "ocr_pipeline_terminal_owner_mismatch"),
    ({"status": "error", "stage": "error", "execution_owner": {}}, "ocr_pipeline_terminal_owner_mismatch"),
    ({"input_sha256": "foreign"}, "ocr_pipeline_result_generation_mismatch"),
    ({"configured_input_sha256": "foreign"}, "ocr_pipeline_result_generation_mismatch"),
    ({"canonical_adoption_allowed": False}, "ocr_pipeline_required_stage_failed"),
])
def test_actual_run_refusal_never_records_success(owned_poll, monkeypatch, change, reason):
    before, _receipt, files = original_state(owned_poll)
    previous = load_pipeline_state()
    with pytest.raises(ocr_job_service.OwnPipelineLineageError, match=f"^{reason}$"):
        run_with_published_output(owned_poll, monkeypatch, change)
    state = load_pipeline_state()
    assert state["status"] == "error"
    assert state["last_error"] == reason
    assert state.get("last_success_at") == previous.get("last_success_at")
    assert state.get("last_output_ref") == previous.get("last_output_ref")
    receipt = ocr_job_service.own_pipeline_delivery_for_output(None)
    assert_only_uploaded_receipt_changed(before, _owned_workflow_database_snapshot(), receipt)
    assert owned_poll.clock.sleeps == []
    assert {path: path.read_bytes() for path in files} == files


@pytest.mark.parametrize("status", ["done", "error"])
@pytest.mark.parametrize("transport", ["success", "http-503", "connection-failure"])
@pytest.mark.parametrize("provider_diagnostic", [
    pytest.param({}, id="missing"),
    pytest.param({"trigger_error": "provider original diagnostic"}, id="provider-text"),
    pytest.param({"trigger_error": ""}, id="provider-empty"),
    pytest.param({"trigger_error": None}, id="provider-null"),
])
def test_actual_run_terminal_output_preserves_trigger_diagnostics(
    owned_poll, monkeypatch, status, transport, provider_diagnostic,
):
    before, _receipt, files = original_state(owned_poll)
    previous = load_pipeline_state()
    url = "https://synthetic-ocr-trigger.invalid/run"
    audience = "https://synthetic-ocr-trigger.invalid"
    monkeypatch.setenv("OCR_PIPELINE_URL", url)
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", audience)
    monkeypatch.setenv("OCR_PIPELINE_TRIGGER_TIMEOUT_SECONDS", "2.5")
    http_requests, token_requests, published = [], [], []

    # External identity-token HTTP exchange only; production header logic runs.
    def fetch_token(request, target_audience):
        assert callable(request)
        assert target_audience == audience
        token_requests.append(target_audience)
        return "synthetic-external-token"

    def trigger(request, timeout):
        receipt = ocr_job_service.own_pipeline_delivery_for_output(None)
        source = parse_gcs_uri(receipt["input_reference"])
        assert request.full_url == url
        assert request.get_method() == "POST"
        assert request.get_header("Content-type") == "application/json"
        assert request.get_header("Authorization") == "Bearer synthetic-external-token"
        assert timeout == 2.5
        assert json.loads(request.data) == {
            "bucket": source.bucket, "name": source.object_name,
            "generation": receipt["generation"],
        }
        http_requests.append((request.full_url, timeout))
        assert len(http_requests) == 1
        if transport == "http-503":
            raise HTTPError(url, 503, "temporary", hdrs=None,
                            fp=BytesIO(b"synthetic trigger unavailable"))
        if transport == "connection-failure":
            raise URLError("synthetic connection failure")
        return BytesIO(b"accepted")

    def publish(key):
        assert http_requests == [(url, 2.5)]
        receipt = ocr_job_service.own_pipeline_delivery_for_output(None)
        source = parse_gcs_uri(receipt["output_reference"])
        assert key == (source.bucket, source.object_name)
        payload = {**response_from_receipt(receipt, status), **provider_diagnostic}
        if status == "error":
            payload["error"] = "synthetic provider error"
        encoded = json.dumps(payload).encode()
        published.append((deepcopy(receipt), deepcopy(payload), encoded))
        assert len(published) == 1
        objects[key] = (encoded, {}, 73, "application/json")

    monkeypatch.setattr(id_token, "fetch_id_token", fetch_token)
    monkeypatch.setattr(pipeline, "urlopen", trigger)
    objects, events = install_owned_pipeline_transport(monkeypatch, on_missing=publish)
    result = pipeline.run_ocr_pipeline(
        pdf_bytes=owned_poll.f.pdf.read_bytes(), job_id=JOB, facility_id=FACILITY,
        input_reference=str(owned_poll.f.pdf), force_upload=True, wait_for_output=True,
    )
    receipt, provider_payload, encoded = published[0]
    expected = deepcopy(provider_payload)
    if "trigger_error" not in provider_diagnostic:
        if transport == "http-503":
            expected["trigger_error"] = "OCR pipeline HTTP 503: synthetic trigger unavailable"
        elif transport == "connection-failure":
            expected["trigger_error"] = (
                "OCR pipeline request failed: <urlopen error synthetic connection failure>"
            )
    assert result == expected
    assert token_requests == [audience]
    assert http_requests == [(url, 2.5)]
    assert json.loads(encoded) == provider_payload
    output = parse_gcs_uri(receipt["output_reference"])
    assert objects[(output.bucket, output.object_name)][0] == encoded
    assert ocr_job_service.own_pipeline_delivery_for_output(result["output_reference"]) == receipt
    original = parse_gcs_uri(receipt["input_reference"])
    assert objects[(original.bucket, original.object_name)][0] == owned_poll.f.pdf.read_bytes()
    assert len([event for event in events if event[0] == "upload"]) == 2
    state = load_pipeline_state()
    assert state["last_job_id"] == JOB
    if status == "error":
        assert state["status"] == "error"
        assert state["last_error"] == "synthetic provider error"
        assert state["last_error_at"]
        assert state["last_success_at"] == previous["last_success_at"]
        assert state["last_output_ref"] == previous["last_output_ref"]
    else:
        assert state["status"] == "ok"
        assert state["last_error"] is None
        assert state["last_error_at"] is None
        assert state["last_success_at"]
        assert state["last_output_ref"] == receipt["output_reference"]
    assert_only_uploaded_receipt_changed(before, _owned_workflow_database_snapshot(), receipt)
    assert owned_poll.clock.sleeps == []
    assert {path: path.read_bytes() for path in files} == files
