from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re

from src.services.intake_mode_service import get_manual_upload_storage_status
from src.services.storage_service import StorageService, save_bytes_to_gcs


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ManualUploadConfigError(RuntimeError):
    pass


@dataclass
class ManualUploadSavedFile:
    message_id: str
    pdf_uri: str
    content_sha256: str
    original_filename: str
    received_at: datetime


def _sanitize_filename(name: str) -> str:
    raw = Path(name or "upload.pdf").name
    if not raw:
        raw = "upload.pdf"
    safe = _FILENAME_SAFE_RE.sub("_", raw).strip("._") or "upload.pdf"
    if not safe.lower().endswith(".pdf"):
        safe = f"{safe}.pdf"
    return safe


def _validate_pdf_bytes(data: bytes) -> None:
    if not data:
        raise ValueError("pdf_file is empty")
    max_mb = int(os.getenv("MANUAL_UPLOAD_MAX_MB", "20") or 20)
    max_bytes = max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ValueError(f"pdf_file exceeds {max_mb}MB limit")
    if b"%PDF" not in data[:1024]:
        raise ValueError("pdf_file must be a PDF")


def _build_message_id(content_sha256: str) -> str:
    return f"upload:sha256:{content_sha256[:24]}"


def _storage_prefix(now: datetime) -> str:
    return f"manual-uploads/{now:%Y/%m/%d}"


def _local_storage_base() -> Path:
    return Path(__file__).resolve().parents[2] / "tmp" / "manual-uploads"


def save_uploaded_pdf(
    *,
    pdf_bytes: bytes,
    original_filename: str,
    received_at: datetime,
) -> ManualUploadSavedFile:
    _validate_pdf_bytes(pdf_bytes)
    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    message_id = _build_message_id(content_sha256)
    safe_name = _sanitize_filename(original_filename)
    object_name = f"{content_sha256[:24]}-{safe_name}"
    storage_status = get_manual_upload_storage_status()
    prefix = _storage_prefix(received_at)

    if storage_status.get("mode") == "gcs":
        bucket = str(storage_status.get("bucket") or "").strip()
        object_path = f"{prefix}/{object_name}"
        pdf_uri = save_bytes_to_gcs(
            bucket,
            object_path,
            pdf_bytes,
            content_type="application/pdf",
        )
    elif storage_status.get("mode") == "local_ephemeral":
        storage = StorageService(_local_storage_base())
        pdf_uri = storage.save_bytes(f"{prefix}/{object_name}", pdf_bytes)
    else:
        raise ManualUploadConfigError("manual upload storage is not configured")

    return ManualUploadSavedFile(
        message_id=message_id,
        pdf_uri=pdf_uri,
        content_sha256=content_sha256,
        original_filename=safe_name,
        received_at=received_at,
    )
