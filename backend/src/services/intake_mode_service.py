from __future__ import annotations

import os


_MANUAL_UPLOAD_ALIASES = {"manual_upload", "pdf_upload", "upload"}


def get_intake_mode() -> str:
    raw = str(os.getenv("INGEST_MODE", "manual_upload") or "").strip().lower()
    if raw in _MANUAL_UPLOAD_ALIASES:
        return "manual_upload"
    return "manual_upload"


def manual_upload_enabled() -> bool:
    return True


def local_upload_storage_allowed() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return not bool(os.getenv("K_SERVICE"))


def get_manual_upload_storage_status() -> dict[str, object]:
    bucket = str(os.getenv("RAW_BUCKET") or "").strip()
    if bucket:
        return {
            "configured": True,
            "mode": "gcs",
            "persisted": True,
            "bucket": bucket,
        }
    if local_upload_storage_allowed():
        return {
            "configured": True,
            "mode": "local_ephemeral",
            "persisted": False,
            "bucket": None,
        }
    return {
        "configured": False,
        "mode": "unconfigured",
        "persisted": False,
        "bucket": None,
    }


def get_intake_status() -> dict[str, object]:
    return {
        "mode": get_intake_mode(),
        "manual_upload_enabled": manual_upload_enabled(),
        "manual_upload_storage": get_manual_upload_storage_status(),
    }
