from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import mimetypes
import os
from pathlib import Path
import re

from src.services.intake_mode_service import get_manual_upload_storage_status
from src.services.storage_service import StorageService, save_bytes_to_gcs


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".csv"}


@dataclass
class SavedMenuUpload:
    file_uri: str
    original_filename: str
    content_sha256: str
    media_type: str


def _sanitize_filename(name: str) -> str:
    raw = Path(name or "monthly-menu.xlsx").name
    if not raw:
        raw = "monthly-menu.xlsx"
    safe = _FILENAME_SAFE_RE.sub("_", raw).strip("._") or "monthly-menu.xlsx"
    suffix = Path(safe).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        safe = f"{safe}.xlsx"
    return safe


def _validate_file(data: bytes, filename: str) -> None:
    if not data:
        raise ValueError("menu file is empty")
    safe = _sanitize_filename(filename)
    suffix = Path(safe).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise ValueError("menu file must be .xlsx, .xlsm, or .csv")
    max_mb = int(os.getenv("MENU_UPLOAD_MAX_MB", "20") or 20)
    if len(data) > max_mb * 1024 * 1024:
        raise ValueError(f"menu file exceeds {max_mb}MB limit")


def _storage_prefix(month_id: str, now: datetime) -> str:
    return f"monthly-menu-uploads/{month_id}/{now:%Y/%m/%d}"


def _local_storage_base() -> Path:
    return Path(__file__).resolve().parents[2] / "tmp" / "monthly-menu-uploads"


def save_monthly_menu_upload(
    *,
    month_id: str,
    file_bytes: bytes,
    original_filename: str,
    uploaded_at: datetime,
) -> SavedMenuUpload:
    _validate_file(file_bytes, original_filename)
    safe_name = _sanitize_filename(original_filename)
    content_sha256 = hashlib.sha256(file_bytes).hexdigest()
    object_name = f"{content_sha256[:24]}-{safe_name}"
    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    prefix = _storage_prefix(month_id, uploaded_at)
    storage_status = get_manual_upload_storage_status()

    if storage_status.get("mode") == "gcs":
        bucket = str(storage_status.get("bucket") or "").strip()
        object_path = f"{prefix}/{object_name}"
        file_uri = save_bytes_to_gcs(bucket, object_path, file_bytes, content_type=media_type)
    elif storage_status.get("mode") == "local_ephemeral":
        storage = StorageService(_local_storage_base())
        file_uri = storage.save_bytes(f"{prefix}/{object_name}", file_bytes)
    else:
        raise RuntimeError("menu upload archive storage is not configured")

    return SavedMenuUpload(
        file_uri=file_uri,
        original_filename=safe_name,
        content_sha256=content_sha256,
        media_type=media_type,
    )
