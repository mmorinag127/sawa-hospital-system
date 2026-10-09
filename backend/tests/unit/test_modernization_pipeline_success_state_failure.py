"""Owned caller transport components, not native OCR/provider completion proof.

The external provider's done response is explicitly synthetic. Its lineage is
derived from real uploads and the real owned delivery receipt; no recognizer,
config capture, receipt, execution fence or lineage validator is substituted.
"""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from modernization_owned_pipeline_transport import install_owned_pipeline_transport
from test_modernization_owned_ocr_workflow import FACILITY, JOB, ORDER, OwnedProducer
from src import db
from src.models.ocr_job import OcrJob
from src.services import ocr_job_service
from src.services import ocr_pipeline_service as pipeline
from src.services import ocr_pipeline_state_store as state
from src.services.storage_service import load_selected_pdf_bytes
from src.services.storage_uri import parse_gcs_uri
from src.services.workflow_attempt_guard_service import (
    capture_nonworkflow_execution, nonworkflow_execution_context,
)


@pytest.mark.parametrize("error_write_fails", [False, True])
def test_real_owned_caller_success_state_failure_never_returns_synthetic_done(
    tmp_path, monkeypatch, error_write_fails,
):
    from google.cloud import storage

    f = OwnedProducer(tmp_path, monkeypatch)
    try:
        path = tmp_path / "success-caller-state.json"
        monkeypatch.setenv("OCR_PIPELINE_STATE_URI", str(path))
        monkeypatch.setenv("OCR_PIPELINE_BUCKET", "explicit-component-input")
        monkeypatch.delenv("OCR_PIPELINE_INPUT_BUCKET", raising=False)
        monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)
        monkeypatch.setenv("OCR_PIPELINE_MAX_INFLIGHT", "0")
        monkeypatch.setenv("OCR_PIPELINE_INPUT_PREFIX", "input/")
        monkeypatch.setenv("OCR_PIPELINE_OUTPUT_PREFIX", "output/")
        monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "60")
        provider_responses = []

        def publish_synthetic_done(key):
            receipt = ocr_job_service.own_pipeline_delivery_for_output(None)
            assert f"gs://{key[0]}/{key[1]}" == receipt["output_reference"]
            original = parse_gcs_uri(receipt["input_reference"])
            data, metadata, generation, content_type = objects[(original.bucket, original.object_name)]
            assert content_type == "application/pdf"
            assert data == f.pdf.read_bytes()
            assert str(generation) == receipt["generation"]
            assert sha256(data).hexdigest() == receipt["input_sha256"]
            assert json.loads(metadata["selected_pdf_receipt"]) == receipt["selected_pdf_receipt"] == f.receipt
            configured = json.loads(metadata["configured_input_receipt"])
            configured_data = objects[(configured["bucket"], configured["object_name"])][0]
            assert sha256(configured_data).hexdigest() == configured["sha256"] == receipt["configured_input_sha256"]
            owner = json.loads(metadata["execution_owner"])
            assert owner == {
                "job_id": JOB, "order_id": ORDER,
                "workflow_attempt_id": None, "workflow_revision": None,
                "nonworkflow_execution_id": boundary["execution_id"],
            }
            # Only the external provider status/stages are synthetic transport data.
            response = {
                "status": "done", "stage": "done",
                "job_id": receipt["pipeline_job_id"],
                "input_reference": receipt["input_reference"],
                "input_generation": str(generation),
                "output_reference": receipt["output_reference"],
                "input_sha256": sha256(data).hexdigest(),
                "configured_input_sha256": sha256(configured_data).hexdigest(),
                "execution_owner": owner,
                "canonical_adoption_allowed": True,
                "canonical_stages": {"classification": "passed", "roi": "passed", "correction": "passed"},
            }
            blob = storage.Client().bucket(key[0]).blob(key[1])
            blob.upload_from_string(
                json.dumps(response).encode("utf-8"),
                content_type="application/json", if_generation_match=0,
            )
            provider_responses.append((deepcopy(receipt), response))

        objects, events = install_owned_pipeline_transport(monkeypatch, on_missing=publish_synthetic_done)
        http_calls = []

        def forbidden_http(*args, **kwargs):
            http_calls.append((args, kwargs))
            raise AssertionError("No HTTP trigger is configured for this transport component")

        monkeypatch.setattr(pipeline, "urlopen", forbidden_http)
        original_save_file = state._save_file
        success_error = OSError("synthetic success state IO failure")
        secondary_error = OSError("synthetic error recording IO failure")
        attempted = []
        requested = []

        def fail_only_success_and_optional_error(filename, payload):
            assert filename == str(path)
            attempted.append(deepcopy(payload))
            if payload["status"] == "ok":
                requested.append(json.loads(path.read_text(encoding="utf-8")))
                assert requested[0]["status"] == "running"
                assert payload["last_request_at"] == requested[0]["last_request_at"]
                assert payload["last_input_ref"] == requested[0]["last_input_ref"]
                raise success_error
            if payload["status"] == "error" and error_write_fails:
                raise secondary_error
            original_save_file(filename, payload)

        monkeypatch.setattr(state, "_save_file", fail_only_success_and_optional_error)
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
                    input_reference=str(f.pdf), force_upload=True, wait_for_output=True,
                )
        assert raised.value is success_error
        assert [item["status"] for item in attempted] == ["running", "ok", "error"]
        assert all(item["last_job_id"] == JOB for item in attempted)
        assert len(requested) == len(provider_responses) == 1
        receipt, response = provider_responses[0]
        assert attempted[0]["last_input_ref"] == receipt["input_reference"]
        assert attempted[1]["last_output_ref"] == receipt["output_reference"]
        assert attempted[2]["last_error"] == str(success_error)
        assert requested[0]["last_success_at"] is None
        assert requested[0]["last_output_ref"] is None
        assert http_calls == []
        output = parse_gcs_uri(receipt["output_reference"])
        output_key = (output.bucket, output.object_name)
        assert ("exists", output_key) in events
        assert ("download", output_key) in events
        assert json.loads(objects[output_key][0]) == response
        persisted = state.load_pipeline_state()
        assert persisted["last_request_at"] == requested[0]["last_request_at"]
        assert persisted["last_input_ref"] == receipt["input_reference"]
        assert persisted["last_success_at"] is None
        assert persisted["last_output_ref"] is None
        if error_write_fails:
            assert raised.value.__notes__ == [
                "OCR pipeline state error recording failed: OSError: synthetic error recording IO failure",
            ]
            assert persisted == requested[0]
        else:
            assert getattr(raised.value, "__notes__", []) == []
            assert persisted == attempted[2]
            assert persisted["status"] == "error"
            assert persisted["last_error"] == str(success_error)
            assert persisted["last_error_at"] == persisted["updated_at"]
        with db.session_scope() as session:
            job = session.get(OcrJob, JOB)
            assert job.metrics["pipeline_delivery_receipt"] == receipt
            assert job.status != "done"
    finally:
        f.engine.dispose()
