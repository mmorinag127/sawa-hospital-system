import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import storage_service  # noqa: E402


def test_load_bytes_from_uri_requires_configured_env_reference(monkeypatch):
    monkeypatch.delenv("TEMPLATE_BUCKET", raising=False)

    try:
        storage_service.load_bytes_from_uri("gs://${TEMPLATE_BUCKET}/invoice_templates/template.xlsx")
    except ValueError as exc:
        assert str(exc) == "storage_uri_env_required:TEMPLATE_BUCKET"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected unresolved storage URI env reference to block")


def test_load_bytes_from_uri_expands_configured_env_reference(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _FakeBlob:
        def download_as_bytes(self) -> bytes:
            return b"template"

    class _FakeBucket:
        def __init__(self, bucket: str):
            self.bucket = bucket

        def blob(self, object_path: str) -> _FakeBlob:
            calls.append((self.bucket, object_path))
            return _FakeBlob()

    class _FakeClient:
        def bucket(self, bucket: str) -> _FakeBucket:
            return _FakeBucket(bucket)

    class _FakeStorageModule:
        Client = _FakeClient

    monkeypatch.setenv("TEMPLATE_BUCKET", "configured-template-bucket")
    storage_service._GCS_CLIENT_LOCAL.__dict__.clear()
    monkeypatch.setattr(storage_service, "_import_google_cloud_storage", lambda: _FakeStorageModule)

    payload = storage_service.load_bytes_from_uri("gs://${TEMPLATE_BUCKET}/invoice_templates/template.xlsx")

    assert payload == b"template"
    assert calls == [("configured-template-bucket", "invoice_templates/template.xlsx")]


def test_save_bytes_to_gcs_reuses_thread_local_client(monkeypatch):
    created_clients: list[object] = []
    uploaded_objects: list[str] = []

    class _FakeBlob:
        def __init__(self, object_path: str):
            self.object_path = object_path

        def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
            uploaded_objects.append(f"{self.object_path}:{content_type}:{data.decode('utf-8')}")

    class _FakeBucket:
        def blob(self, object_path: str) -> _FakeBlob:
            return _FakeBlob(object_path)

    class _FakeClient:
        def __init__(self):
            created_clients.append(object())

        def bucket(self, bucket: str) -> _FakeBucket:
            assert bucket == "bucket"
            return _FakeBucket()

    class _FakeStorageModule:
        Client = _FakeClient

    storage_service._GCS_CLIENT_LOCAL.__dict__.clear()
    monkeypatch.setattr(storage_service, "_import_google_cloud_storage", lambda: _FakeStorageModule)

    first = storage_service.save_bytes_to_gcs(
        "bucket",
        "path/one.pdf",
        b"one",
        content_type="application/pdf",
    )
    second = storage_service.save_bytes_to_gcs(
        "bucket",
        "path/two.pdf",
        b"two",
        content_type="application/pdf",
    )

    assert first == "gs://bucket/path/one.pdf"
    assert second == "gs://bucket/path/two.pdf"
    assert len(created_clients) == 1
    assert uploaded_objects == [
        "path/one.pdf:application/pdf:one",
        "path/two.pdf:application/pdf:two",
    ]


def test_generate_signed_url_reuses_thread_local_client_and_cached_credentials(monkeypatch):
    created_clients: list[object] = []
    generated_urls: list[tuple[str, str, str | None, str | None]] = []
    auth_default_calls: list[object] = []

    class _FakeBlob:
        def __init__(self, object_path: str):
            self.object_path = object_path

        def generate_signed_url(
            self,
            *,
            version: str,
            expiration,
            method: str,
            service_account_email: str,
            access_token: str,
        ) -> str:
            generated_urls.append(
                (
                    self.object_path,
                    version,
                    service_account_email,
                    access_token,
                )
            )
            return f"https://signed.invalid/{self.object_path}?token={access_token}"

    class _FakeBucket:
        def blob(self, object_path: str) -> _FakeBlob:
            return _FakeBlob(object_path)

    class _FakeClient:
        def __init__(self):
            created_clients.append(object())

        def bucket(self, bucket: str) -> _FakeBucket:
            assert bucket == "bucket"
            return _FakeBucket()

    class _FakeStorageModule:
        Client = _FakeClient

    class _FakeCredentials:
        def __init__(self):
            self.service_account_email = "worker@example.invalid"
            self.token = None
            self.valid = False
            self.expiry = None
            self.refresh_calls = 0

        def refresh(self, _request) -> None:
            self.refresh_calls += 1
            self.token = f"token-{self.refresh_calls}"
            self.valid = True
            self.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    credentials = _FakeCredentials()

    storage_service._GCS_CLIENT_LOCAL.__dict__.clear()
    monkeypatch.setattr(storage_service, "_import_google_cloud_storage", lambda: _FakeStorageModule)

    import google.auth
    from google.auth.transport import requests as google_auth_requests

    def _fake_default(*, scopes):
        auth_default_calls.append(tuple(scopes))
        return credentials, "test-project"

    class _FakeRequest:
        pass

    monkeypatch.setattr(google.auth, "default", _fake_default)
    monkeypatch.setattr(google_auth_requests, "Request", _FakeRequest)

    first = storage_service.generate_signed_url("bucket", "path/one.png")
    second = storage_service.generate_signed_url("bucket", "path/two.png")

    assert first == "https://signed.invalid/path/one.png?token=token-1"
    assert second == "https://signed.invalid/path/two.png?token=token-1"
    assert len(created_clients) == 1
    assert len(auth_default_calls) == 1
    assert credentials.refresh_calls == 1
    assert generated_urls == [
        ("path/one.png", "v4", "worker@example.invalid", "token-1"),
        ("path/two.png", "v4", "worker@example.invalid", "token-1"),
    ]
