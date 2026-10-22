"""Cross-process physical execution ownership, independent of lease expiry."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import text
from src.storage_types import same_json


def digest(source):
    return sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_contract(job):
    source = job.source_json
    required = {"schema", "pdf_reference", "pdf_sha256", "storage_version", "document_type", "filename", "facility_id", "dpi", "device", "provider", "source_document_id", "original_document"}
    if not isinstance(source, dict) or not required.issubset(source) or type(source["schema"]) is not int or source["schema"] != 1:
        raise ValueError("Original OCR source contract is unavailable")
    if type(source["facility_id"]) is not int or source["facility_id"] < 1:
        raise ValueError("Original school identity is invalid")
    original = source["original_document"]
    if source["source_document_id"] is None:
        if original is not None:
            raise ValueError("Unlinked original document proof must be explicit null")
    else:
        if not isinstance(original, dict) or not {"id", "facility_id", "sha256", "filename", "ocr_job_id"}.issubset(original):
            raise ValueError("Original upload document proof is unavailable")
        if not same_json(original["id"], source["source_document_id"]) or type(original["id"]) is not str or not original["id"] or not same_json(original["facility_id"], source["facility_id"]) or not same_json(original["ocr_job_id"], job.id):
            raise ValueError("Original upload document identity is invalid")
        if type(original["filename"]) is not str or not original["filename"] or type(original["sha256"]) is not str or len(original["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in original["sha256"]):
            raise ValueError("Original upload document metadata is invalid")
    if any(not isinstance(source[key], str) or not source[key] for key in ("pdf_reference", "document_type", "filename", "provider")):
        raise ValueError("Original OCR source types are invalid")
    if type(source["dpi"]) is not int or source["dpi"] < 1 or not isinstance(source["device"], str) or not source["device"]:
        raise ValueError("Original OCR parameters are invalid")
    if not isinstance(source["pdf_sha256"], str) or len(source["pdf_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in source["pdf_sha256"]):
        raise ValueError("Original PDF digest is invalid")
    for captured, column in (("pdf_reference", "stored_pdf_ref"), ("document_type", "document_type"),
                             ("filename", "filename"), ("facility_id", "facility_id"), ("provider", "provider")):
        if type(source[captured]) is not type(getattr(job, column)) or source[captured] != getattr(job, column):
            raise ValueError("Original OCR source identity changed")
    if digest(source) != job.input_digest or not job.business_attempt_id:
        raise ValueError("Original OCR source/attempt proof is unavailable")
    version = source["storage_version"]
    if not isinstance(version, dict) or version.get("mode") not in {"local", "gcs"}:
        raise ValueError("Original storage version is unavailable")
    keys = {"device", "inode", "size", "mtime_ns"} if version["mode"] == "local" else {"bucket", "object", "generation"}
    if not keys.issubset(version) or (version["mode"] == "gcs" and (type(version["generation"]) is not int or version["generation"] < 1)):
        raise ValueError("Original storage version is invalid")
    if version["mode"] == "local" and any(type(version[key]) is not int or version[key] < 0 for key in keys):
        raise ValueError("Original local storage metadata is invalid")
    from src.services.implementation_price_storage import parse_stored_reference
    try:
        parsed = parse_stored_reference(source["pdf_reference"])
    except (ValueError, FileNotFoundError) as exc:
        raise ValueError("Original source URI is invalid") from exc
    if version["mode"] == "gcs":
        if not same_json(parsed, version):
            raise ValueError("Original GCS reference/version differs")
    elif parsed["mode"] != "local":
        raise ValueError("Original local reference is invalid")
    return dict(source)


@contextmanager
def execution_mutex(job_id):
    from src.db import get_engine
    engine = get_engine()
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": "school-ocr:" + job_id}).scalar_one())
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": "school-ocr:" + job_id})
                    connection.commit()
    elif engine.dialect.name == "sqlite":
        import fcntl
        database = engine.url.database
        if not database or database == ":memory:":
            raise ValueError("Physical OCR mutex requires an explicit file-backed SQLite database")
        path = Path(database).resolve().parent / (".ocr-" + sha256(job_id.encode()).hexdigest() + ".lock")
        with path.open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
    else:
        raise ValueError("OCR physical mutex database driver is unsupported")
