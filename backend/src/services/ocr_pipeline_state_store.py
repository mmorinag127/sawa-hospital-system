import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from loguru import logger

from src.services.storage_uri import parse_gcs_uri

from google.api_core.exceptions import NotFound

_STORAGE_UNLOADED = object()
storage = _STORAGE_UNLOADED


class OCRPipelineStateConfigError(RuntimeError):
    pass


def _storage_sdk():
    global storage
    if storage is _STORAGE_UNLOADED:
        try:
            from google.cloud import storage as sdk
        except ImportError:
            storage = None
        else:
            storage = sdk
    if storage is None:
        raise OCRPipelineStateConfigError("pipeline state GCS storage SDK is unavailable")
    return storage


def _state_uri() -> str:
    uri = (os.getenv("OCR_PIPELINE_STATE_URI") or "").strip()
    from src.staging_data import current_dataset, data_setting
    if current_dataset() != "test":
        uri = data_setting("OCR_PIPELINE_STATE_URI", feature="order")
        from src.staging_data import _staging_uri
        physical = _staging_uri(uri)
        if not physical.path.startswith("/stg-replay/" + current_dataset() + "/"):
            raise OCRPipelineStateConfigError("staging_dataset_state_snapshot_mismatch")
    if not uri:
        raise OCRPipelineStateConfigError("OCR_PIPELINE_STATE_URI is not configured")
    try:
        parsed = urlparse(uri)
        if "?" in uri or "#" in uri:
            raise OCRPipelineStateConfigError("pipeline state URI must identify a writable object")
        if parsed.scheme in ("", "file"):
            if parsed.netloc or not os.path.isabs(parsed.path):
                raise OCRPipelineStateConfigError("pipeline state file path must be absolute and local")
        elif parsed.scheme == "gs":
            source = parse_gcs_uri(uri)
            if source.generation is not None or source.query or source.fragment:
                raise OCRPipelineStateConfigError("pipeline state URI must identify a writable object")
        else:
            raise OCRPipelineStateConfigError(f"unsupported pipeline state uri: {uri}")
    except ValueError as exc:
        raise OCRPipelineStateConfigError("invalid OCR_PIPELINE_STATE_URI") from exc
    return uri


def _load_gs(bucket: str, object_name: str) -> dict | None:
    client = _storage_sdk().Client()
    blob = client.bucket(bucket).blob(object_name)
    try:
        payload = blob.download_as_bytes()
    except NotFound:
        return None
    return json.loads(payload.decode("utf-8"))


def _save_gs(bucket: str, object_name: str, payload: dict) -> None:
    client = _storage_sdk().Client()
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
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        return _load_file(path)
    if parsed.scheme == "gs":
        source = parse_gcs_uri(uri)
        if source.generation is not None or source.query or source.fragment:
            raise OCRPipelineStateConfigError("pipeline state URI must identify a writable object")
        return _load_gs(source.bucket, source.object_name)
    raise OCRPipelineStateConfigError(f"unsupported pipeline state uri: {uri}")


def _save_state(payload: dict) -> None:
    uri = _state_uri()
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        _save_file(path, payload)
        logger.info("OCR pipeline state saved", uri=uri)
        return
    if parsed.scheme == "gs":
        source = parse_gcs_uri(uri)
        if source.generation is not None or source.query or source.fragment:
            raise OCRPipelineStateConfigError("pipeline state URI must identify a writable object")
        _save_gs(source.bucket, source.object_name, payload)
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
