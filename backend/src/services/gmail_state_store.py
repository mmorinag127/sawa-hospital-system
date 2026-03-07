import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from google.cloud import storage
from loguru import logger


class GmailStateConfigError(RuntimeError):
    pass


def _default_state_uri() -> str:
    bucket = os.getenv("GMAIL_WATCH_STATE_BUCKET") or os.getenv("RAW_BUCKET")
    if not bucket:
        raise GmailStateConfigError("missing env: GMAIL_WATCH_STATE_BUCKET or RAW_BUCKET")
    object_name = os.getenv("GMAIL_WATCH_STATE_OBJECT", "gmail/watch_state.json")
    return f"gs://{bucket}/{object_name}"


def _state_uri() -> str:
    return os.getenv("GMAIL_WATCH_STATE_URI") or _default_state_uri()


def _load_gs(bucket: str, object_name: str) -> dict | None:
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_name)
    if not blob.exists():
        return None
    payload = blob.download_as_bytes()
    return json.loads(payload.decode("utf-8"))


def _save_gs(bucket: str, object_name: str, payload: dict) -> None:
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


def load_watch_state() -> dict | None:
    uri = _state_uri()
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        return _load_file(path)
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket or not object_name:
            raise GmailStateConfigError(f"invalid watch state uri: {uri}")
        return _load_gs(bucket, object_name)
    raise GmailStateConfigError(f"unsupported watch state uri: {uri}")


def save_watch_state(history_id: str | None, expiration: str | None = None) -> None:
    payload = {
        "historyId": history_id,
        "expiration": expiration,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "status": "ok",
        "error_code": None,
        "error_message": None,
    }
    uri = _state_uri()
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        _save_file(path, payload)
        logger.info("Gmail watch state saved", uri=uri)
        return
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket or not object_name:
            raise GmailStateConfigError(f"invalid watch state uri: {uri}")
        _save_gs(bucket, object_name, payload)
        logger.info("Gmail watch state saved", uri=uri)
        return
    raise GmailStateConfigError(f"unsupported watch state uri: {uri}")


def save_watch_error(error_code: str, error_message: str | None = None) -> None:
    payload = {
        "historyId": None,
        "expiration": None,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "status": "error",
        "error_code": error_code,
        "error_message": error_message,
    }
    uri = _state_uri()
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme else uri
        _save_file(path, payload)
        logger.info("Gmail watch error state saved", uri=uri, error_code=error_code)
        return
    if parsed.scheme == "gs":
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket or not object_name:
            raise GmailStateConfigError(f"invalid watch state uri: {uri}")
        _save_gs(bucket, object_name, payload)
        logger.info("Gmail watch error state saved", uri=uri, error_code=error_code)
        return
    raise GmailStateConfigError(f"unsupported watch state uri: {uri}")
