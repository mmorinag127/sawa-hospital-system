"""State-source components with real local files, not native/live GCS proof.

Caller definitions use in-memory external GCS transport and failing file IO.
Authored inputs, PDF receipts, execution ownership and delivery fences are real.
No synthetic provider response is presented as native OCR success.
"""

from datetime import datetime
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from src.services import ocr_pipeline_state_store as state
from src.services import ocr_pipeline_service as pipeline
from src.services import ocr_job_service
from src.services.storage_service import load_selected_pdf_bytes
from src.services.workflow_attempt_guard_service import (
    capture_nonworkflow_execution, nonworkflow_execution_context,
)
from src import db
from src.models.ocr_job import OcrJob
from test_modernization_owned_ocr_workflow import FACILITY, JOB, ORDER, OwnedProducer
from modernization_owned_pipeline_transport import install_owned_pipeline_transport


@pytest.fixture(autouse=True)
def isolated_state_environment(monkeypatch):
    for name in ("OCR_PIPELINE_STATE_URI", "OCR_PIPELINE_STATE_BUCKET",
                 "OCR_PIPELINE_STATE_OBJECT", "RAW_BUCKET", "OCR_OUTPUT_BUCKET",
                 "OCR_ARTIFACT_BUCKET"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("environment", [
    {}, {"OCR_PIPELINE_STATE_URI": ""}, {"OCR_PIPELINE_STATE_URI": " \t\n"},
    {"RAW_BUCKET": "raw-only"}, {"OCR_OUTPUT_BUCKET": "output-only"},
    {"OCR_PIPELINE_STATE_BUCKET": "state-bucket-only"},
    {"OCR_PIPELINE_STATE_BUCKET": "state-bucket", "OCR_PIPELINE_STATE_OBJECT": "state.json",
     "RAW_BUCKET": "raw", "OCR_OUTPUT_BUCKET": "output", "OCR_ARTIFACT_BUCKET": "artifact"},
])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_dedicated_uri_is_required_without_any_bucket_fallback(monkeypatch, environment, operation):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(state.OCRPipelineStateConfigError) as raised:
        if operation == "read":
            state.load_pipeline_state()
        else:
            state._save_state({"status": "must-not-be-written"})
    assert str(raised.value) == "OCR_PIPELINE_STATE_URI is not configured"


@pytest.mark.parametrize("uri,error", [
    ("relative.json", "pipeline state file path must be absolute and local"),
    ("file:relative.json", "pipeline state file path must be absolute and local"),
    ("https://example.invalid/state.json", "unsupported pipeline state uri: https://example.invalid/state.json"),
    ("gs://bucket/state.json?generation=42", "pipeline state URI must identify a writable object"),
    ("gs://bucket/state.json?query=1", "pipeline state URI must identify a writable object"),
    ("gs://bucket/state.json#fragment", "pipeline state URI must identify a writable object"),
    ("file:///tmp/state.json?query=1", "pipeline state URI must identify a writable object"),
    ("file:///tmp/state.json#fragment", "pipeline state URI must identify a writable object"),
    ("file://remote/tmp/state.json", "pipeline state file path must be absolute and local"),
    ("gs://bucket", "invalid OCR_PIPELINE_STATE_URI"),
])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_invalid_state_uri_stops_before_storage(monkeypatch, uri, error, operation):
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", uri)
    monkeypatch.setattr(state, "storage", None)
    with pytest.raises(state.OCRPipelineStateConfigError) as raised:
        if operation == "read":
            state.load_pipeline_state()
        else:
            state._save_state({"status": "must-not-be-written"})
    assert str(raised.value) == error


@pytest.mark.parametrize("file_url", [False, True])
def test_explicit_local_first_creation_and_state_transitions_preserve_payload(tmp_path, monkeypatch, file_url):
    path = tmp_path / "pipeline-state.json"
    uri = path.as_uri() if file_url else str(path)
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", f" \t{uri}\n")
    assert state._state_uri() == uri
    assert state.load_pipeline_state() is None
    assert not path.exists()
    state.save_pipeline_request("request-job", "gs://owned/input.pdf")
    requested = state.load_pipeline_state()
    assert json.loads(path.read_text(encoding="utf-8")) == requested
    assert requested["status"] == "running"
    assert requested["last_job_id"] == "request-job"
    assert requested["last_input_ref"] == "gs://owned/input.pdf"
    assert requested["last_request_at"] == requested["updated_at"]
    assert requested["last_success_at"] is None
    assert requested["last_error_at"] is None
    assert requested["last_error"] is None
    assert requested["last_output_ref"] is None
    state._save_state({**requested, "independent_existing_key": {"keep": [0, "value"]}})
    state.save_pipeline_success("success-job", "gs://owned/output.json")
    succeeded = state.load_pipeline_state()
    assert succeeded["status"] == "ok"
    assert succeeded["last_job_id"] == "success-job"
    assert succeeded["last_input_ref"] == requested["last_input_ref"]
    assert succeeded["last_request_at"] == requested["last_request_at"]
    assert succeeded["last_output_ref"] == "gs://owned/output.json"
    assert succeeded["last_success_at"] == succeeded["updated_at"]
    assert succeeded["last_error"] is None
    state.save_pipeline_error("error-job", "explicit processing failure")
    failed = state.load_pipeline_state()
    assert failed["status"] == "error"
    assert failed["last_job_id"] == "error-job"
    assert failed["last_error"] == "explicit processing failure"
    assert failed["last_error_at"] == failed["updated_at"]
    assert failed["last_success_at"] == succeeded["last_success_at"]
    assert failed["last_request_at"] == requested["last_request_at"]
    assert failed["last_input_ref"] == requested["last_input_ref"]
    assert failed["last_output_ref"] == succeeded["last_output_ref"]
    for payload in (succeeded, failed):
        assert payload["independent_existing_key"] == {"keep": [0, "value"]}
    timestamps = [datetime.fromisoformat(payload["updated_at"]) for payload in (requested, succeeded, failed)]
    assert all(stamp.utcoffset().total_seconds() == 0 for stamp in timestamps)
    assert timestamps == sorted(timestamps)
    assert json.loads(path.read_text(encoding="utf-8")) == failed


@pytest.mark.parametrize("operation", ["read", "write"])
def test_gcs_sdk_absence_is_explicit_for_both_storage_wrappers(monkeypatch, operation):
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", "gs://owned/state.json")
    monkeypatch.setattr(state, "storage", None)
    assert state._state_uri() == "gs://owned/state.json"
    with pytest.raises(state.OCRPipelineStateConfigError) as raised:
        if operation == "read":
            state.load_pipeline_state()
        else:
            state._save_state({"status": "must-not-be-written"})
    assert str(raised.value) == "pipeline state GCS storage SDK is unavailable"


@pytest.mark.parametrize("trigger_url", [None, "https://pipeline.invalid/run"])
@pytest.mark.parametrize("wait_for_output", [False, True])
def test_real_owned_caller_request_write_failure_propagates_before_http_or_poll(
    tmp_path, monkeypatch, trigger_url, wait_for_output, isolated_formal_sqlite_template,
):
    """Transport component: real ownership, no real GCS/HTTP/recognition."""
    f = OwnedProducer(tmp_path, monkeypatch, formal_template=isolated_formal_sqlite_template)
    try:
        monkeypatch.setenv("OCR_PIPELINE_STATE_URI", str(tmp_path / "caller-state.json"))
        monkeypatch.setenv("OCR_PIPELINE_BUCKET", "explicit-component-input")
        monkeypatch.delenv("OCR_PIPELINE_INPUT_BUCKET", raising=False)
        monkeypatch.setenv("OCR_PIPELINE_MAX_INFLIGHT", "0")
        monkeypatch.setenv("OCR_PIPELINE_INPUT_PREFIX", "input/")
        monkeypatch.setenv("OCR_PIPELINE_OUTPUT_PREFIX", "output/")
        if trigger_url is None:
            monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)
        else:
            monkeypatch.setenv("OCR_PIPELINE_URL", trigger_url)

        objects, transport_events = install_owned_pipeline_transport(monkeypatch)
        external_calls = []

        def forbidden_http(*args, **kwargs):
            external_calls.append("http")
            raise AssertionError("HTTP must not follow a failed request-state write")

        def forbidden_poll(*args, **kwargs):
            external_calls.append("poll")
            raise AssertionError("Polling must not follow a failed request-state write")

        monkeypatch.setattr(pipeline, "urlopen", forbidden_http)
        monkeypatch.setattr(pipeline, "_wait_for_output", forbidden_poll)
        request_error = OSError("synthetic request state IO failure")
        secondary_error = OSError("synthetic error recording IO failure")
        attempted_states = []

        def failing_file_write(path, payload):
            assert path == str(tmp_path / "caller-state.json")
            attempted_states.append(deepcopy(payload))
            if len(attempted_states) == 1:
                assert payload["status"] == "running"
                raise request_error
            assert payload["status"] == "error"
            raise secondary_error

        monkeypatch.setattr(state, "_save_file", failing_file_write)
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
            pdf_bytes = load_selected_pdf_bytes(str(f.pdf), f.receipt)
            with pytest.raises(OSError) as raised:
                pipeline.run_ocr_pipeline(
                    pdf_bytes=pdf_bytes, job_id=JOB, facility_id=FACILITY,
                    input_reference=str(f.pdf), force_upload=True,
                    wait_for_output=wait_for_output,
                )
        assert raised.value is request_error
        assert raised.value.__notes__ == [
            "OCR pipeline state error recording failed: OSError: synthetic error recording IO failure",
        ]
        assert [item["status"] for item in attempted_states] == ["running", "error"]
        assert attempted_states[1]["last_error"] == str(request_error)
        assert all(item["last_job_id"] == JOB for item in attempted_states)
        assert external_calls == []
        assert not (tmp_path / "caller-state.json").exists()
        assert [event[0] for event in transport_events] == ["upload", "upload", "reload", "download"]
        pdf_objects = [(key, value) for key, value in objects.items() if value[3] == "application/pdf"]
        assert len(pdf_objects) == 1
        key, (uploaded_bytes, metadata, generation, _) = pdf_objects[0]
        assert uploaded_bytes == f.pdf.read_bytes()
        assert json.loads(metadata["selected_pdf_receipt"]) == f.receipt
        configured_receipt = json.loads(metadata["configured_input_receipt"])
        configured_object = objects[(configured_receipt["bucket"], configured_receipt["object_name"])]
        assert sha256(configured_object[0]).hexdigest() == configured_receipt["sha256"]
        with db.session_scope() as session:
            job = session.get(OcrJob, JOB)
            receipt = job.metrics["pipeline_delivery_receipt"]
            assert receipt["execution_id"] == boundary["execution_id"]
            assert receipt["selected_input_reference"] == str(f.pdf)
            assert receipt["selected_pdf_receipt"] == f.receipt
            assert receipt["input_reference"] == f"gs://{key[0]}/{key[1]}"
            assert receipt["generation"] == str(generation)
            assert receipt["input_sha256"] == sha256(uploaded_bytes).hexdigest()
            assert receipt["configured_input_sha256"] == configured_receipt["sha256"]
            assert job.status != "done"
    finally:
        f.engine.dispose()
