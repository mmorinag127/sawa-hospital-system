from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re

import fitz
from src.services.intake_mode_service import get_manual_upload_storage_status
from src.services.storage_service import StorageService, save_bytes_to_gcs


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PLACEHOLDER_PAGE_TEXT_RE = re.compile(
    r"^page\s+\d+(?:\s*(?:/|of)\s*\d+)?$",
    re.IGNORECASE,
)


class ManualUploadConfigError(RuntimeError):
    pass


@dataclass
class ManualUploadSavedFile:
    message_id: str
    pdf_uri: str
    content_sha256: str
    original_filename: str
    received_at: datetime
    page_number: int | None = None
    total_pages: int | None = None
    split_group_id: str | None = None


@dataclass(frozen=True)
class _ManualUploadStorageTarget:
    mode: str
    prefix: str
    bucket: str | None = None
    storage: StorageService | None = None


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
    if _is_placeholder_only_pdf(data):
        raise ValueError("pdf_file appears to be a placeholder/test PDF")


def _normalize_pdf_page_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _page_has_placeholder_marker_only(page: fitz.Page) -> bool:
    text = _normalize_pdf_page_text(page.get_text("text"))
    if not text:
        return False
    if page.get_images(full=True):
        return False
    if page.get_drawings():
        return False
    return _PLACEHOLDER_PAGE_TEXT_RE.fullmatch(text) is not None


def _is_placeholder_only_pdf(pdf_bytes: bytes) -> bool:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return False
    try:
        if document.page_count <= 0:
            return False
        matched_pages = 0
        for page in document:
            if not _page_has_placeholder_marker_only(page):
                return False
            matched_pages += 1
        return matched_pages == document.page_count
    finally:
        document.close()


def _build_message_id(
    content_sha256: str,
    *,
    split_group_id: str | None = None,
    page_number: int | None = None,
    total_pages: int | None = None,
) -> str:
    base = f"upload:sha256:{content_sha256[:24]}"
    if split_group_id and page_number and total_pages and total_pages > 1:
        return f"{base}:split:{split_group_id[:12]}:{page_number}of{total_pages}"
    return base


def _storage_prefix(now: datetime) -> str:
    return f"manual-uploads/{now:%Y/%m/%d}"


def _local_storage_base() -> Path:
    return Path(__file__).resolve().parents[2] / "tmp" / "manual-uploads"


def _build_page_filename(name: str, *, page_number: int, total_pages: int) -> str:
    raw = Path(name or "upload.pdf").name
    stem = Path(raw).stem or "upload"
    suffix = Path(raw).suffix or ".pdf"
    return _sanitize_filename(f"{stem}__page-{page_number:02d}-of-{total_pages:02d}{suffix}")


def _save_pdf_variant(
    *,
    pdf_bytes: bytes,
    original_filename: str,
    received_at: datetime,
    storage_target: _ManualUploadStorageTarget,
    split_group_id: str | None = None,
    page_number: int | None = None,
    total_pages: int | None = None,
) -> ManualUploadSavedFile:
    content_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    message_id = _build_message_id(
        content_sha256,
        split_group_id=split_group_id,
        page_number=page_number,
        total_pages=total_pages,
    )
    safe_name = _sanitize_filename(original_filename)
    object_name = f"{content_sha256[:24]}-{safe_name}"

    if storage_target.mode == "gcs":
        bucket = str(storage_target.bucket or "").strip()
        object_path = f"{storage_target.prefix}/{object_name}"
        pdf_uri = save_bytes_to_gcs(
            bucket,
            object_path,
            pdf_bytes,
            content_type="application/pdf",
        )
    elif storage_target.mode == "local_ephemeral":
        storage = storage_target.storage or StorageService(_local_storage_base())
        pdf_uri = storage.save_bytes(f"{storage_target.prefix}/{object_name}", pdf_bytes)
    else:
        raise ManualUploadConfigError("manual upload storage is not configured")

    return ManualUploadSavedFile(
        message_id=message_id,
        pdf_uri=pdf_uri,
        content_sha256=content_sha256,
        original_filename=safe_name,
        received_at=received_at,
        page_number=page_number,
        total_pages=total_pages,
        split_group_id=split_group_id,
    )


def _resolve_storage_target(received_at: datetime) -> _ManualUploadStorageTarget:
    storage_status = get_manual_upload_storage_status()
    mode = str(storage_status.get("mode") or "").strip()
    prefix = _storage_prefix(received_at)
    if mode == "gcs":
        bucket = str(storage_status.get("bucket") or "").strip()
        if not bucket:
            raise ManualUploadConfigError("manual upload storage is not configured")
        return _ManualUploadStorageTarget(mode="gcs", bucket=bucket, prefix=prefix)
    if mode == "local_ephemeral":
        return _ManualUploadStorageTarget(
            mode="local_ephemeral",
            prefix=prefix,
            storage=StorageService(_local_storage_base()),
        )
    raise ManualUploadConfigError("manual upload storage is not configured")


def _split_pdf_pages(pdf_bytes: bytes) -> list[bytes]:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return [pdf_bytes]
    try:
        if document.page_count <= 1:
            return [pdf_bytes]
        pages: list[bytes] = []
        for page_index in range(document.page_count):
            page_doc = fitz.open()
            try:
                page_doc.insert_pdf(document, from_page=page_index, to_page=page_index)
                pages.append(page_doc.tobytes(garbage=3, deflate=True))
            finally:
                page_doc.close()
        return pages
    finally:
        document.close()


def save_uploaded_pdf_pages(
    *,
    pdf_bytes: bytes,
    original_filename: str,
    received_at: datetime,
) -> list[ManualUploadSavedFile]:
    _validate_pdf_bytes(pdf_bytes)
    page_bytes_list = _split_pdf_pages(pdf_bytes)
    storage_target = _resolve_storage_target(received_at)
    total_pages = len(page_bytes_list)
    if total_pages <= 1:
        return [
            _save_pdf_variant(
                pdf_bytes=pdf_bytes,
                original_filename=original_filename,
                received_at=received_at,
                storage_target=storage_target,
                page_number=1,
                total_pages=1,
            )
        ]
    split_group_id = hashlib.sha256(pdf_bytes).hexdigest()[:24]
    page_specs = [
        {
            "pdf_bytes": page_bytes,
            "original_filename": _build_page_filename(
                original_filename,
                page_number=index,
                total_pages=total_pages,
            ),
            "received_at": received_at,
            "storage_target": storage_target,
            "split_group_id": split_group_id,
            "page_number": index,
            "total_pages": total_pages,
        }
        for index, page_bytes in enumerate(page_bytes_list, start=1)
    ]
    max_workers = min(
        total_pages,
        max(1, int(os.getenv("MANUAL_UPLOAD_SAVE_WORKERS", "4") or 4)),
    )
    if max_workers <= 1:
        return [_save_pdf_variant(**spec) for spec in page_specs]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(lambda spec: _save_pdf_variant(**spec), page_specs))


def save_uploaded_pdf(
    *,
    pdf_bytes: bytes,
    original_filename: str,
    received_at: datetime,
) -> ManualUploadSavedFile:
    saved_files = save_uploaded_pdf_pages(
        pdf_bytes=pdf_bytes,
        original_filename=original_filename,
        received_at=received_at,
    )
    return saved_files[0]
