from datetime import timedelta
import os
from pathlib import Path
from typing import Union
from urllib.parse import urlparse
import json

StoragePath = Union[str, Path]


class StorageService:
    def __init__(self, base_path: StoragePath):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, relative_path: str, data: bytes) -> str:
        target = self.base_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    def open(self, relative_path: str) -> bytes:
        target = self.base_path / relative_path
        return target.read_bytes()


def load_bytes_from_uri(uri: str) -> bytes:
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme else uri)
        return path.read_bytes()
    if parsed.scheme == "gs":
        try:
            from google.cloud import storage  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ValueError("google-cloud-storage is not installed") from exc
        bucket_name = parsed.netloc
        blob_name = parsed.path.lstrip("/")
        if not bucket_name or not blob_name:
            raise ValueError(f"invalid gs uri: {uri}")
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        return blob.download_as_bytes()
    raise ValueError(f"unsupported storage uri: {uri}")


def save_bytes_to_gcs(bucket: str, object_path: str, data: bytes, content_type: str | None = None) -> str:
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("google-cloud-storage is not installed") from exc
    if not bucket or not object_path:
        raise ValueError("bucket and object_path are required")
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_path)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{bucket}/{object_path}"


def generate_signed_url(bucket: str, object_path: str, expires_in_seconds: int = 3600) -> str:
    try:
        from google.cloud import storage  # type: ignore
        import google.auth
        from google.auth.transport.requests import Request as AuthRequest
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("google-cloud-storage is not installed") from exc
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_path)
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not credentials.valid:
        credentials.refresh(AuthRequest())
    service_account_email = getattr(credentials, "service_account_email", None)
    if not service_account_email:
        service_account_email = (
            os.getenv("GCP_SERVICE_ACCOUNT_EMAIL")
            or os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
        )
    if not service_account_email or not credentials.token:
        raise ValueError("missing service account email or access token for signed URL")
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expires_in_seconds),
        method="GET",
        service_account_email=service_account_email,
        access_token=credentials.token,
    )


def _sanitize_job_id(job_id: str) -> str:
    return (
        job_id.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )


def build_artifact_path(job_id: str, name: str) -> str:
    safe_job_id = _sanitize_job_id(job_id)
    return f"artifacts/{safe_job_id}/{name}"


def build_output_path(job_id: str, name: str) -> str:
    safe_job_id = _sanitize_job_id(job_id)
    return f"output/{safe_job_id}/{name}"


def save_artifact_json(storage: StorageService, job_id: str, name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return storage.save_bytes(build_artifact_path(job_id, name), data)


def save_artifact_bytes(storage: StorageService, job_id: str, name: str, data: bytes) -> str:
    return storage.save_bytes(build_artifact_path(job_id, name), data)


def save_output_json(storage: StorageService, job_id: str, name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return storage.save_bytes(build_output_path(job_id, name), data)


def get_default_output_bucket() -> str | None:
    return os.getenv("OCR_OUTPUT_BUCKET") or os.getenv("RAW_BUCKET")


def get_default_artifact_bucket() -> str | None:
    return os.getenv("OCR_ARTIFACT_BUCKET") or os.getenv("RAW_BUCKET")


def save_artifact_bytes_to_gcs(
    bucket: str, job_id: str, name: str, data: bytes, content_type: str | None = None
) -> str:
    object_path = build_artifact_path(job_id, name)
    return save_bytes_to_gcs(bucket, object_path, data, content_type=content_type)


def save_output_bytes_to_gcs(
    bucket: str, job_id: str, name: str, data: bytes, content_type: str | None = None
) -> str:
    object_path = build_output_path(job_id, name)
    return save_bytes_to_gcs(bucket, object_path, data, content_type=content_type)
