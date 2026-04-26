import io
import json
import pathlib
import sys
from urllib.error import HTTPError

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import google.cloud.storage
import pytest

from src.services import ocr_pipeline_service  # noqa: E402


class _FakeBlob:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self._index = 0

    def exists(self, **_kwargs):
        return True

    def download_as_bytes(self, **_kwargs):
        payload = self._payloads[min(self._index, len(self._payloads) - 1)]
        self._index += 1
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class _FakeBucket:
    def __init__(self, blob):
        self._blob = blob

    def blob(self, _name):
        return self._blob


class _FakeClient:
    def __init__(self, blob):
        self._blob = blob

    def bucket(self, _bucket):
        return _FakeBucket(self._blob)


def test_wait_for_output_skips_partial_running_payload(monkeypatch):
    payloads = [
        {"status": "running", "stage": "ocr"},
        {"status": "done", "stage": "done", "pages": [{"page_index": 1}]},
    ]
    fake_blob = _FakeBlob(payloads)
    monkeypatch.setattr(google.cloud.storage, "Client", lambda: _FakeClient(fake_blob))
    monkeypatch.setattr(ocr_pipeline_service.time, "sleep", lambda _secs: None)

    result = ocr_pipeline_service._wait_for_output(
        bucket="bucket",
        object_name="output.json",
        timeout_seconds=1,
        poll_interval=0,
    )

    assert result["status"] == "done"
    assert result["stage"] == "done"
    assert fake_blob._index >= 2


def test_wait_for_output_returns_failed_terminal_payload(monkeypatch):
    payloads = [
        {"status": "failed", "stage": "error", "error": "ocr_failed"},
    ]
    fake_blob = _FakeBlob(payloads)
    monkeypatch.setattr(google.cloud.storage, "Client", lambda: _FakeClient(fake_blob))
    monkeypatch.setattr(ocr_pipeline_service.time, "sleep", lambda _secs: None)

    result = ocr_pipeline_service._wait_for_output(
        bucket="bucket",
        object_name="output.json",
        timeout_seconds=1,
        poll_interval=0,
    )

    assert result["status"] == "failed"
    assert result["stage"] == "error"


def test_wait_for_output_retries_after_transient_read_error(monkeypatch):
    payloads = [
        TimeoutError("temporary read timeout"),
        {"status": "done", "stage": "done", "pages": [{"page_index": 1}]},
    ]
    fake_blob = _FakeBlob(payloads)
    monkeypatch.setattr(google.cloud.storage, "Client", lambda: _FakeClient(fake_blob))
    monkeypatch.setattr(ocr_pipeline_service.time, "sleep", lambda _secs: None)

    result = ocr_pipeline_service._wait_for_output(
        bucket="bucket",
        object_name="output.json",
        timeout_seconds=1,
        poll_interval=0,
    )

    assert result["status"] == "done"
    assert fake_blob._index >= 2


def test_run_ocr_pipeline_saves_request_state_for_gcs_only_wait(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_wait_for_output",
        lambda **_kwargs: {"status": "done"},
    )
    request_calls: list[tuple[str | None, str | None]] = []
    success_calls: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(
        ocr_pipeline_service,
        "save_pipeline_request",
        lambda job_id, input_ref: request_calls.append((job_id, input_ref)),
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "save_pipeline_success",
        lambda job_id, output_ref: success_calls.append((job_id, output_ref)),
    )

    result = ocr_pipeline_service.run_ocr_pipeline(
        pdf_bytes=b"%PDF-1.4",
        job_id="MAIN-test",
        wait_for_output=True,
    )

    assert result["status"] == "done"
    assert request_calls == [("MAIN-test", request_calls[0][1])]
    assert request_calls[0][1].startswith("gs://bucket/input/")
    assert success_calls == [("MAIN-test", success_calls[0][1])]
    assert success_calls[0][1].startswith("gs://bucket/output/")


def test_run_ocr_pipeline_posts_with_identity_token_when_http_trigger_enabled(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_wait_for_output",
        lambda **_kwargs: {"status": "done"},
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_success", lambda *_args, **_kwargs: None)

    observed = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout):
        observed["url"] = req.full_url
        observed["timeout"] = timeout
        observed["headers"] = {key.lower(): value for key, value in req.header_items()}
        observed["payload"] = req.data.decode("utf-8")
        return _FakeResponse()

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    result = ocr_pipeline_service.run_ocr_pipeline(
        pdf_bytes=b"%PDF-1.4",
        job_id="MAIN-http",
        wait_for_output=True,
    )

    assert result["status"] == "done"
    assert observed["url"] == "https://ocr-pipeline-stg.example.run.app/"
    assert observed["timeout"] == ocr_pipeline_service._get_request_timeout(wait_for_output=False)
    assert observed["headers"]["authorization"] == "Bearer token-for:https://ocr-pipeline-stg.example.run.app"
    assert json.loads(observed["payload"])["bucket"] == "bucket"


def test_run_ocr_pipeline_posts_even_when_async_http_trigger_enabled(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )

    request_calls: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(
        ocr_pipeline_service,
        "save_pipeline_request",
        lambda job_id, input_ref: request_calls.append((job_id, input_ref)),
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_success", lambda *_args, **_kwargs: None)

    observed = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout):
        observed["url"] = req.full_url
        observed["timeout"] = timeout
        observed["headers"] = {key.lower(): value for key, value in req.header_items()}
        observed["payload"] = req.data.decode("utf-8")
        return _FakeResponse()

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    result = ocr_pipeline_service.run_ocr_pipeline(
        pdf_bytes=b"%PDF-1.4",
        job_id="MAIN-http-async",
        wait_for_output=False,
    )

    assert result["status"] == "running"
    assert observed["url"] == "https://ocr-pipeline-stg.example.run.app/"
    assert observed["timeout"] == ocr_pipeline_service._get_request_timeout(wait_for_output=False)
    assert observed["headers"]["authorization"] == "Bearer token-for:https://ocr-pipeline-stg.example.run.app"
    assert json.loads(observed["payload"])["bucket"] == "bucket"
    assert request_calls == [("MAIN-http-async", request_calls[0][1])]
    assert request_calls[0][1].startswith("gs://bucket/input/")


def test_run_ocr_pipeline_async_returns_running_when_http_trigger_times_out(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setenv("OCR_PIPELINE_TRIGGER_TIMEOUT_SECONDS", "9")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_success", lambda *_args, **_kwargs: None)

    observed = {}

    def _fake_urlopen(req, timeout):
        observed["timeout"] = timeout
        raise TimeoutError("trigger stalled")

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    result = ocr_pipeline_service.run_ocr_pipeline(
        pdf_bytes=b"%PDF-1.4",
        job_id="MAIN-http-timeout-async",
        wait_for_output=False,
    )

    assert observed["timeout"] == 9.0
    assert result["status"] == "running"
    assert result["output_reference"].startswith("gs://bucket/output/")
    assert "OCR pipeline request timeout" in str(result.get("trigger_error") or "")


def test_run_ocr_pipeline_raises_pending_error_with_references_on_timeout(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_wait_for_output",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("missing output")),
    )

    try:
        ocr_pipeline_service.run_ocr_pipeline(
            pdf_bytes=b"%PDF-1.4",
            job_id="MAIN-timeout",
            wait_for_output=True,
        )
    except ocr_pipeline_service.OCRPipelineOutputPendingError as exc:
        assert exc.input_reference and exc.input_reference.startswith("gs://bucket/input/")
        assert exc.output_reference == "gs://bucket/output/MAIN-timeout_123.pdf.json" or exc.output_reference.startswith("gs://bucket/output/")
        return

    raise AssertionError("expected OCRPipelineOutputPendingError")


def test_run_ocr_pipeline_keeps_waiting_when_http_trigger_returns_502_but_output_arrives(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *_args, **_kwargs: None)
    success_calls: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(
        ocr_pipeline_service,
        "save_pipeline_success",
        lambda job_id, output_ref: success_calls.append((job_id, output_ref)),
    )
    wait_calls: list[dict[str, object]] = []

    def _fake_wait_for_output(**kwargs):
        wait_calls.append(kwargs)
        return {"status": "done", "stage": "done", "pages": [{"page_index": 1}]}

    monkeypatch.setattr(ocr_pipeline_service, "_wait_for_output", _fake_wait_for_output)

    def _fake_urlopen(req, timeout):
        raise HTTPError(req.full_url, 502, "temporary", hdrs=None, fp=io.BytesIO(b"temporary 502"))

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    result = ocr_pipeline_service.run_ocr_pipeline(
        pdf_bytes=b"%PDF-1.4",
        job_id="MAIN-http-502-done",
        wait_for_output=True,
    )

    assert result["status"] == "done"
    assert "OCR pipeline HTTP 502" in str(result.get("trigger_error") or "")
    assert wait_calls
    assert success_calls == [("MAIN-http-502-done", success_calls[0][1])]
    assert success_calls[0][1].startswith("gs://bucket/output/")


def test_run_ocr_pipeline_async_returns_running_when_http_trigger_returns_502(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_success", lambda *_args, **_kwargs: None)

    def _fake_urlopen(req, timeout):
        raise HTTPError(req.full_url, 502, "temporary", hdrs=None, fp=io.BytesIO(b"temporary 502"))

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    result = ocr_pipeline_service.run_ocr_pipeline(
        pdf_bytes=b"%PDF-1.4",
        job_id="MAIN-http-502-async",
        wait_for_output=False,
    )

    assert result["status"] == "running"
    assert result["output_reference"].startswith("gs://bucket/output/")
    assert result["input_reference"].startswith("gs://bucket/input/")
    assert "OCR pipeline HTTP 502" in str(result.get("trigger_error") or "")


def test_run_ocr_pipeline_http_502_still_raises_pending_when_output_never_arrives(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_wait_for_output",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("missing output")),
    )

    def _fake_urlopen(req, timeout):
        raise HTTPError(req.full_url, 502, "temporary", hdrs=None, fp=io.BytesIO(b"temporary 502"))

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    try:
        ocr_pipeline_service.run_ocr_pipeline(
            pdf_bytes=b"%PDF-1.4",
            job_id="MAIN-http-502-timeout",
            wait_for_output=True,
        )
    except ocr_pipeline_service.OCRPipelineOutputPendingError as exc:
        assert exc.input_reference and exc.input_reference.startswith("gs://bucket/input/")
        assert exc.output_reference and exc.output_reference.startswith("gs://bucket/output/")
        return

    raise AssertionError("expected OCRPipelineOutputPendingError")


def test_run_ocr_pipeline_http_403_terminalizes_trigger_failure(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_BUCKET", "bucket")
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline-stg.example.run.app/")
    monkeypatch.setenv("OCR_PIPELINE_TARGET_AUDIENCE", "https://ocr-pipeline-stg.example.run.app")
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_upload_pdf_bytes",
        lambda **_kwargs: "123",
    )
    monkeypatch.setattr(
        ocr_pipeline_service,
        "_fetch_pipeline_identity_token",
        lambda audience: f"token-for:{audience}",
    )
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *_args, **_kwargs: None)
    saved_errors: list[tuple[str | None, str]] = []
    monkeypatch.setattr(
        ocr_pipeline_service,
        "save_pipeline_error",
        lambda job_id, error_message: saved_errors.append((job_id, error_message)),
    )
    wait_called = {"value": False}

    def _should_not_wait(**_kwargs):
        wait_called["value"] = True
        raise AssertionError("_wait_for_output should not run after terminal trigger failure")

    monkeypatch.setattr(ocr_pipeline_service, "_wait_for_output", _should_not_wait)

    def _fake_urlopen(req, timeout):
        raise HTTPError(
            req.full_url,
            403,
            "forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"forbidden"}'),
        )

    monkeypatch.setattr(ocr_pipeline_service, "urlopen", _fake_urlopen)

    with pytest.raises(ocr_pipeline_service.OCRPipelineTriggerFailedError) as excinfo:
        ocr_pipeline_service.run_ocr_pipeline(
            pdf_bytes=b"%PDF-1.4",
            job_id="MAIN-http-403-terminal",
            wait_for_output=True,
        )

    assert "OCR pipeline HTTP 403" in str(excinfo.value)
    assert len(saved_errors) == 1
    assert saved_errors[0][0] == "MAIN-http-403-terminal"
    assert "OCR pipeline HTTP 403" in saved_errors[0][1]
    assert wait_called["value"] is False
