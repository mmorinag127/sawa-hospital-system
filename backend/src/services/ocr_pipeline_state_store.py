import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from loguru import logger

from src.services.storage_service import get_default_output_bucket

try:
    from google.cloud import storage  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    storage = None


class OCRPipelineStateConfigError(RuntimeError):
    pass


def _default_state_uri() -> str | None:
    bucket = os.getenv("OCR_PIPELINE_STATE_BUCKET") or os.getenv("RAW_BUCKET") or get_default_output_bucket()
    if not bucket:
        return None
    object_name = os.getenv("OCR_PIPELINE_STATE_OBJECT", "ocr/pipeline_state.json")
    return f"gs://{bucket}/{object_name}"


def _state_uri() -> str | None:
    return os.getenv("OCR_PIPELINE_STATE_URI") or _default_state_uri()


def _load_gs(bucket: str, object_name: str) -> dict | None:
    if storage is None:
        return None
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_name)
    if not blob.exists():
        return None
    payload = blob.download_as_bytes()
    return json.loads(payload.decode("utf-8"))


def _save_gs(bucket: str, object_name: str, payload: dict) -> None:
    if storage is None:
        return
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_name)
    blob.upload_from_string(json.dumps(payload), content_type="application/json")


def _load_file(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def _save_file(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def load_pipeline_state() -> dict | None:
    uri = _state_uri()
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        return _load_file(path)
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket or not object_name:
            raise OCRPipelineStateConfigError(f"invalid pipeline state uri: {uri}")
        return _load_gs(bucket, object_name)
    raise OCRPipelineStateConfigError(f"unsupported pipeline state uri: {uri}")


def _save_state(payload: dict) -> None:
    uri = _state_uri()
    if not uri:
        return
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        _save_file(path, payload)
        logger.info("OCR pipeline state saved", uri=uri)
        return
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket or not object_name:
            raise OCRPipelineStateConfigError(f"invalid pipeline state uri: {uri}")
        _save_gs(bucket, object_name, payload)
        logger.info("OCR pipeline state saved", uri=uri)
        return
    raise OCRPipelineStateConfigError(f"unsupported pipeline state uri: {uri}")


def _base_payload(existing: dict | None = None) -> dict:
    now = datetime.now(tz=timezone.utc).isoformat()
    payload = dict(existing or {})
    payload.setdefault("last_success_at", None)
    payload.setdefault("last_error_at", None)
    payload.setdefault("last_error", None)
    payload.setdefault("last_request_at", None)
    payload.setdefault("last_output_ref", None)
    payload.setdefault("last_input_ref", None)
    payload.setdefault("last_job_id", None)
    payload["updated_at"] = now
    return payload


def save_pipeline_request(job_id: str | None, input_ref: str | None) -> None:
    existing = load_pipeline_state()
    payload = _base_payload(existing)
    payload.update(
        {
            "status": "running",
            "last_request_at": payload["updated_at"],
            "last_job_id": job_id,
            "last_input_ref": input_ref,
        }
    )
    _save_state(payload)


def save_pipeline_success(job_id: str | None, output_ref: str | None) -> None:
    existing = load_pipeline_state()
    payload = _base_payload(existing)
    payload.update(
        {
            "status": "ok",
            "last_success_at": payload["updated_at"],
            "last_job_id": job_id,
            "last_output_ref": output_ref,
            "last_error": None,
            "last_error_at": None,
        }
    )
    _save_state(payload)


def save_pipeline_error(job_id: str | None, error_message: str) -> None:
    existing = load_pipeline_state()
    payload = _base_payload(existing)
    payload.update(
        {
            "status": "error",
            "last_error": error_message,
            "last_error_at": payload["updated_at"],
            "last_job_id": job_id,
        }
    )
    _save_state(payload)
