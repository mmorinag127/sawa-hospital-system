"""Owned pipeline requests; only HTTP and GCS are component doubles."""
import io
import json
from hashlib import sha256
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlparse

import google.cloud.storage
import pytest

from src.db import session_scope
from src.models.ocr_job import OcrJob
from src.services import ocr_pipeline_service as pipeline
from src.services import ocr_job_service, order_service, order_workflow_v2_service as workflow
from src.services import workflow_attempt_guard_service as guard
from src.services import workflow_ocr_delivery_service as delivery
from src.services.ocr_pipeline_state_store import load_pipeline_state
from src.services.storage_service import load_selected_pdf_bytes
from tests.helpers.ingest_original import configure_delivery, configure_workflow_delivery
from tests.unit.test_h2_operator_repair_revision import repair_order  # noqa: F401


@pytest.fixture
def owned_pipeline(repair_order, monkeypatch, tmp_path, pipeline_transport):
    configure_delivery(monkeypatch)
    configure_workflow_delivery(monkeypatch)
    order_id, revision, _ = repair_order
    from src.models.order import Order
    with session_scope() as session:
        original_uri = session.get(Order, order_id).document_uri
    confirmed, error = workflow.confirm_context(order_id=order_id, facility_id="fixture",
        week_start="2026-10-05", week_end="2026-10-11", expected_revision=revision)
    assert error is None, error
    revision = confirmed["revision"]
    job_id = "owned-pipeline-request"
    queued, error = workflow.mark_ocr_run_queued(order_id, job_id,
        input_reference=original_uri, request_mode="ocr_rerun", expected_revision=revision,
        job_metrics=order_service._build_reparse_request_metadata(
            ocr_prompt=" custom prompt ", prompt_preset="custom-preset",
            requested_provider="gemini", ocr_model="custom-model", llm_assist=True))
    assert error is None, error
    claim = delivery.claim_delivery(queued["delivery"]["id"], queued["delivery"]["generation"],
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert not claim.get("duplicate"), claim
    attempt = queued["ocr_job"]["workflow_attempt_id"]
    revision = queued["workflow"]["revision"]
    receipt = pipeline_transport.receipt
    statuses, calls = pipeline_transport.statuses, pipeline_transport.calls
    observations = pipeline_transport.observations
    with ExitStack() as stack:
        stack.enter_context(delivery.claim_context(claim))
        stack.enter_context(order_service.workflow_ocr_write_context(attempt, revision, job_id, order_id))
        stack.enter_context(guard.workflow_execution_context(job_id, order_id, attempt, revision))
        pdf = load_selected_pdf_bytes(original_uri, guard.processing_start_pdf_receipt())
        def run(*, wait=True, force_upload=False):
            return pipeline.run_ocr_pipeline(pdf_bytes=pdf, job_id=job_id, facility_id="fixture",
                input_reference=original_uri, wait_for_output=wait, force_upload=force_upload)
        yield SimpleNamespace(run=run, receipt=receipt, statuses=statuses, calls=calls,
            observations=observations, job_id=job_id, order_id=order_id, revision=revision,
            attempt=attempt, pdf=pdf, original_uri=original_uri, output_payload=pipeline_transport.output_payload,
            enable_http=lambda: monkeypatch.setenv("OCR_PIPELINE_URL", "https://pipeline.example.invalid/"),
            monkeypatch=monkeypatch)


@pytest.fixture
def pipeline_transport(monkeypatch, tmp_path):
    objects, calls, statuses = {}, [], ["done"]
    observations = {"http_error": None, "missing": False}
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("OCR_PIPELINE_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", str(tmp_path / "pipeline-state.json"))
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)

    def receipt():
        with session_scope() as session:
            owner = guard.current_nonworkflow_execution() or guard.current_workflow_execution()
            return dict(session.get(OcrJob, owner["job_id"]).metrics["pipeline_delivery_receipt"])

    class Blob:
        def __init__(self, name, generation=None):
            self.name = name
            self.generation = generation or 123
            self.metadata = objects.get(name, {}).get("metadata")
        def upload_from_string(self, data, **kwargs):
            assert kwargs["if_generation_match"] == 0
            objects[self.name] = {"data": data.encode() if isinstance(data, str) else bytes(data),
                                  "metadata": self.metadata}
        def reload(self, **kwargs):
            self.metadata = objects.get(self.name, {}).get("metadata")
        def exists(self, **kwargs):
            return not observations["missing"]
        def download_as_bytes(self, **kwargs):
            if self.name in objects:
                assert kwargs["if_generation_match"] == int(self.generation)
                return objects[self.name]["data"]
            if "if_generation_match" in kwargs:
                assert kwargs["if_generation_match"] == int(self.generation)
            calls.append(("output-read", self.name))
            status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            if isinstance(status, Exception):
                raise status
            r = receipt()
            # External response echoes the actual request identity. No local
            # delivery/owner receipt or validation result is replaced.
            response = {"status": status, "stage": "ocr" if status == "running" else "error" if status == "failed" else "done",
                "pages": [{"page_index": 1}], "job_id": r["pipeline_job_id"],
                "input_reference": r["input_reference"], "input_generation": r["generation"],
                "output_reference": r["output_reference"], "input_sha256": r["input_sha256"],
                "configured_input_sha256": r["configured_input_sha256"],
                "execution_owner": {"job_id": r["job_id"], "order_id": r["order_id"],
                    "workflow_attempt_id": r["workflow_attempt_id"], "workflow_revision": r["workflow_revision"],
                    "nonworkflow_execution_id": r["execution_id"]},
                "canonical_adoption_allowed": True,
                "canonical_stages": {"classification": "passed", "roi": "passed", "correction": "passed"}
            }
            response.update(deepcopy(observations.get("payload_overrides", {})))
            observations["last_payload"] = deepcopy(response)
            return json.dumps(response).encode()

    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(
        blob=lambda name, generation=None: Blob(name, generation)))
    monkeypatch.setattr(google.cloud.storage, "Client", lambda: client)
    from src.services import storage_service
    monkeypatch.setattr(storage_service, "_get_thread_gcs_client", lambda: client)
    def http(request, timeout):
        calls.append(("http", request, timeout))
        fault = observations["http_error"]
        if isinstance(fault, int):
            raise HTTPError(request.full_url, fault, "component response", None, io.BytesIO(b"component response"))
        if fault is not None:
            raise fault
        return SimpleNamespace(__enter__=lambda self: self)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b"{}"
    def urlopen(request, timeout):
        http(request, timeout)
        return Response()
    monkeypatch.setattr(pipeline, "urlopen", urlopen)
    # Identity-token acquisition is external authentication I/O.
    monkeypatch.setattr(pipeline, "_fetch_pipeline_identity_token", lambda audience: "component-token")
    def output_payload():
        r = receipt()
        ref = urlparse(r["output_reference"])
        data = client.bucket(ref.netloc).blob(ref.path.lstrip("/")).download_as_bytes(if_generation_match=123)
        return json.loads(data)
    return SimpleNamespace(receipt=receipt, statuses=statuses, calls=calls, objects=objects,
        observations=observations, output_payload=output_payload,
        enable_http=lambda: monkeypatch.setenv("OCR_PIPELINE_URL", "https://pipeline.example.invalid/"),
        monkeypatch=monkeypatch)


def _poll(f, statuses):
    f.statuses[:] = statuses
    f.run(wait=False)
    reference = urlparse(f.receipt()["output_reference"])
    return pipeline._wait_for_output(bucket=reference.netloc, object_name=reference.path.lstrip("/"),
        timeout_seconds=1, poll_interval=0)


def test_wait_for_output_skips_partial_running_payload(owned_pipeline):
    result = _poll(owned_pipeline, ["running", "done"])
    assert result["status"] == result["stage"] == "done"
    assert len([call for call in owned_pipeline.calls if call[0] == "output-read"]) >= 2


def test_wait_for_output_returns_failed_terminal_payload(owned_pipeline):
    # Failed required stages are a real failure, never Pending or successful output.
    with pytest.raises(ocr_job_service.OwnPipelineLineageError, match="ocr_pipeline_required_stage_failed"):
        _poll(owned_pipeline, ["failed"])


def test_wait_for_output_retries_after_transient_read_error(owned_pipeline):
    result = _poll(owned_pipeline, [TimeoutError("temporary read timeout"), "done"])
    assert result["status"] == "done"
    assert len([call for call in owned_pipeline.calls if call[0] == "output-read"]) >= 2


def _assert_request(f):
    r = f.receipt()
    assert r["selected_input_reference"] == f.original_uri
    assert r["input_reference"].startswith("gs://bucket/input/")
    assert r["output_reference"].startswith("gs://bucket/output/")
    assert r["selected_pdf_receipt"]["sha256"] == sha256(f.pdf).hexdigest()
    state = load_pipeline_state()
    assert state["last_job_id"] == f.job_id
    return r


def test_run_ocr_pipeline_saves_request_state_for_gcs_only_wait(owned_pipeline):
    f = owned_pipeline
    result = f.run()
    r = _assert_request(f)
    assert result["status"] == "done"
    assert result["output_reference"] == r["output_reference"]


def _http(f):
    f.enable_http()
    result = f.run(wait=False)
    _, req, timeout = next(call for call in f.calls if call[0] == "http")
    assert req.full_url == "https://pipeline.example.invalid/"
    assert {key.lower(): value for key, value in req.header_items()}["authorization"] == "Bearer component-token"
    assert json.loads(req.data)["bucket"] == "bucket"
    assert timeout == pipeline._get_request_timeout(wait_for_output=False)
    _assert_request(f)
    return result


def test_run_ocr_pipeline_posts_with_identity_token_when_http_trigger_enabled(owned_pipeline):
    f = owned_pipeline
    f.enable_http()
    result = f.run()
    assert result["status"] == "done"
    _, request, timeout = next(call for call in f.calls if call[0] == "http")
    assert {key.lower(): value for key, value in request.header_items()}["authorization"] == "Bearer component-token"
    assert timeout == pipeline._get_request_timeout(wait_for_output=False)
    assert json.loads(request.data)["bucket"] == "bucket"
    _assert_request(f)


def test_run_ocr_pipeline_posts_even_when_async_http_trigger_enabled(owned_pipeline):
    assert _http(owned_pipeline)["status"] == "running"


def test_run_ocr_pipeline_async_returns_running_when_http_trigger_times_out(owned_pipeline):
    f = owned_pipeline
    f.monkeypatch.setenv("OCR_PIPELINE_TRIGGER_TIMEOUT_SECONDS", "9")
    f.observations["http_error"] = TimeoutError("trigger stalled")
    result = _http(f)
    assert result["status"] == "running"
    assert "OCR pipeline request timeout" in result["trigger_error"]
    assert next(call for call in f.calls if call[0] == "http")[2] == 9.0


def _pending(f):
    f.observations["missing"] = True
    f.monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "0")
    with pytest.raises(pipeline.OCRPipelineOutputPendingError) as error:
        f.run()
    r = _assert_request(f)
    assert error.value.input_reference == r["input_reference"]
    assert error.value.output_reference == r["output_reference"]


def test_run_ocr_pipeline_raises_pending_error_with_references_on_timeout(owned_pipeline):
    _pending(owned_pipeline)


def test_run_ocr_pipeline_keeps_waiting_when_http_trigger_returns_502_but_output_arrives(owned_pipeline):
    f = owned_pipeline
    f.enable_http()
    f.observations["http_error"] = 502
    result = f.run()
    assert result["status"] == "done"
    assert "OCR pipeline HTTP 502" in result["trigger_error"]
    assert any(call[0] == "output-read" for call in f.calls)
    _assert_request(f)


def test_run_ocr_pipeline_async_returns_running_when_http_trigger_returns_502(owned_pipeline):
    f = owned_pipeline
    f.observations["http_error"] = 502
    result = _http(f)
    assert result["status"] == "running"
    assert "OCR pipeline HTTP 502" in result["trigger_error"]


def test_run_ocr_pipeline_http_502_still_raises_pending_when_output_never_arrives(owned_pipeline):
    owned_pipeline.enable_http()
    owned_pipeline.observations["http_error"] = 502
    _pending(owned_pipeline)


def test_run_ocr_pipeline_http_403_terminalizes_trigger_failure(owned_pipeline):
    f = owned_pipeline
    f.enable_http()
    f.observations["http_error"] = 403
    with pytest.raises(pipeline.OCRPipelineTriggerFailedError, match="OCR pipeline HTTP 403"):
        f.run()
    assert not any(call[0] == "output-read" for call in f.calls)
    state = load_pipeline_state()
    assert "OCR pipeline HTTP 403" in state["last_error"]
    assert state["last_job_id"] == f.job_id
