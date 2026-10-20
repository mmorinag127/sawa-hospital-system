"""Explicit local/GCS object storage boundary for generated files."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


DEFAULT_GCS_BUCKET_PREFIX = "shift"
DEFAULT_LOCAL_STORAGE_ROOT = "./tmp/storage"


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    backend: str
    storage_key: str
    content_type: str
    size_bytes: int
    sha256: str
    bucket_name: str | None = None
    local_path: str | None = None


def _normalized_gcs_prefix() -> str:
    return os.getenv("GCS_BUCKET_PREFIX", DEFAULT_GCS_BUCKET_PREFIX).strip().strip("/")


def _normalized_storage_key(object_name: str) -> str:
    cleaned_name = object_name.strip().lstrip("/")
    if not cleaned_name:
        raise StorageError("保存先の object key が空です")
    prefix = _normalized_gcs_prefix()
    return f"{prefix}/{cleaned_name}" if prefix else cleaned_name


def _local_storage_root() -> Path:
    return Path(os.getenv("LOCAL_STORAGE_ROOT", DEFAULT_LOCAL_STORAGE_ROOT)).resolve()


def _create_gcs_client():
    from google.cloud import storage as gcs_storage

    return gcs_storage.Client()


def _store_local_file(storage_key: str, content: bytes) -> Path:
    local_path = _local_storage_root() / Path(storage_key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)
    return local_path


def store_bytes(object_name: str, content: bytes, content_type: str) -> StoredObject:
    storage_key = _normalized_storage_key(object_name)
    digest = hashlib.sha256(content).hexdigest()
    bucket_name = os.getenv("GCS_BUCKET_NAME", "").strip() or None

    if bucket_name:
        try:
            client = _create_gcs_client()
            blob = client.bucket(bucket_name).blob(storage_key)
            blob.upload_from_string(content, content_type=content_type)
        except Exception as error:  # pragma: no cover - requires external client failure
            raise StorageError(
                "GCSへの保存に失敗しました。GCS_BUCKET_NAME、GCS_BUCKET_PREFIX、認証設定を確認してください"
            ) from error
        return StoredObject(
            backend="gcs",
            storage_key=storage_key,
            content_type=content_type,
            size_bytes=len(content),
            sha256=digest,
            bucket_name=bucket_name,
        )

    try:
        local_path = _store_local_file(storage_key, content)
    except Exception as error:
        raise StorageError(
            "ローカル保存に失敗しました。LOCAL_STORAGE_ROOT の設定を確認してください"
        ) from error
    return StoredObject(
        backend="local",
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        local_path=str(local_path),
    )


def maybe_upload_bytes(object_name: str, content: bytes, content_type: str) -> str | None:
    stored_object = store_bytes(object_name, content, content_type)
    if stored_object.backend == "gcs":
        return stored_object.storage_key
    return None
