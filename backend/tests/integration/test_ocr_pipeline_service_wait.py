import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import google.cloud.storage

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
