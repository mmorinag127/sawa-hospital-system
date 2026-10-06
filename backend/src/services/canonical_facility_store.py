"""Facility JSON bytes and their storage revision are one CAS snapshot."""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable
from urllib.parse import urlparse

from src.services.storage_service import _get_thread_gcs_client


class CanonicalStoreError(Exception):
    def __init__(self, error: str, *, status_code: int = 503, **detail):
        super().__init__(error)
        self.status_code = status_code
        self.detail = {"error": error, **detail}


@dataclass(frozen=True)
class MasterSnapshot:
    master: dict
    revision: str
    source: str


def source_location() -> str:
    uri = os.getenv("FACILITY_MASTER_URI", "").strip()
    if uri:
        parsed = urlparse(uri)
        if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/") or parsed.query or parsed.fragment:
            raise CanonicalStoreError("facility_master_uri_invalid")
        return uri
    if os.getenv("K_SERVICE"):
        raise CanonicalStoreError("facility_master_gcs_uri_required")
    path = os.getenv("FACILITY_MASTER_PATH", "").strip()
    if not path:
        raise CanonicalStoreError("facility_master_source_required")
    return str(Path(path).resolve())


def _source_key(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-finite JSON number")


def _decode(data: bytes) -> dict:
    try:
        master = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if not isinstance(master, dict) or not isinstance(master.get("facilities"), list):
            raise ValueError("facilities array required")
        ids = set()
        for entry in master["facilities"]:
            facility_id = entry.get("facility_id") if isinstance(entry, dict) else None
            if not isinstance(facility_id, str) or not facility_id.strip() or facility_id in ids:
                raise ValueError("unique facility_id required")
            ids.add(facility_id)
        return master
    except (ValueError, UnicodeError) as exc:
        raise CanonicalStoreError("facility_master_json_invalid") from exc


def _encode(master: dict) -> bytes:
    try:
        data = (json.dumps(master, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalStoreError("facility_master_json_invalid", status_code=400) from exc
    try:
        _decode(data)
    except CanonicalStoreError as exc:
        raise CanonicalStoreError("facility_master_json_invalid", status_code=400) from exc
    return data


def check_revision(snapshot: MasterSnapshot, expected_revision: str) -> None:
    if not isinstance(expected_revision, str) or not expected_revision:
        raise CanonicalStoreError("facility_master_expected_revision_required", status_code=428)
    if snapshot.revision != expected_revision:
        raise CanonicalStoreError("facility_master_revision_conflict", status_code=409)


@contextmanager
def _local_lock(source: str, *, exclusive: bool):
    # Lock a stable sidecar, not the inode replaced by atomic save.
    try:
        with open(source + ".lock", "a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise CanonicalStoreError("facility_master_local_storage_unavailable") from exc


def _read_local(source: str) -> MasterSnapshot:
    with open(source, "rb") as stream:
        data = stream.read()
        stat = os.fstat(stream.fileno())
    revision = f"local:{_source_key(source)}:{stat.st_ino}:{stat.st_mtime_ns}:{hashlib.sha256(data).hexdigest()}"
    return MasterSnapshot(_decode(data), revision, source)


def _gcs_blob(source: str):
    parsed = urlparse(source)
    return _get_thread_gcs_client().bucket(parsed.netloc).blob(parsed.path.lstrip("/"))


def _read_gcs(source: str) -> MasterSnapshot:
    from google.api_core.exceptions import PreconditionFailed

    blob = _gcs_blob(source)
    blob.reload(retry=None)
    generation = blob.generation
    if generation is None or int(generation) <= 0:
        raise CanonicalStoreError("facility_master_generation_missing")
    try:
        data = blob.download_as_bytes(if_generation_match=int(generation), retry=None)
    except PreconditionFailed as exc:
        raise CanonicalStoreError("facility_master_read_changed", status_code=409) from exc
    return MasterSnapshot(_decode(data), f"gcs:{_source_key(source)}:{generation}", source)


def read_snapshot() -> MasterSnapshot:
    source = source_location()
    try:
        if source.startswith("gs://"):
            return _read_gcs(source)
        with _local_lock(source, exclusive=False):
            return _read_local(source)
    except CanonicalStoreError:
        raise
    except Exception as exc:
        raise CanonicalStoreError("facility_master_storage_unavailable") from exc


def update_snapshot(expected_revision: str, transform: Callable[[dict], dict]) -> MasterSnapshot:
    if not isinstance(expected_revision, str) or not expected_revision:
        raise CanonicalStoreError("facility_master_expected_revision_required", status_code=428)
    source = source_location()
    try:
        if source.startswith("gs://"):
            from google.api_core.exceptions import PreconditionFailed

            current = _read_gcs(source)
            check_revision(current, expected_revision)
            data = _encode(transform(current.master))
            blob = _gcs_blob(source)
            try:
                blob.upload_from_string(
                    data, content_type="application/json",
                    if_generation_match=int(current.revision.rsplit(":", 1)[1]), retry=None,
                )
            except PreconditionFailed as exc:
                raise CanonicalStoreError("facility_master_revision_conflict", status_code=409) from exc
            except Exception as exc:
                raise CanonicalStoreError("facility_master_save_outcome_unknown", outcome="unknown", reload_required=True) from exc
            if blob.generation is None or int(blob.generation) <= 0:
                raise CanonicalStoreError("facility_master_save_result_missing", saved=True, reload_required=True)
            return MasterSnapshot(_decode(data), f"gcs:{_source_key(source)}:{blob.generation}", source)
        with _local_lock(source, exclusive=True):
            current = _read_local(source)
            check_revision(current, expected_revision)
            data = _encode(transform(current.master))
            path = Path(source)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as stream:
                    temporary = stream.name
                    os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    stat = os.fstat(stream.fileno())
                os.replace(temporary, source)
                temporary = None
                revision = f"local:{_source_key(source)}:{stat.st_ino}:{stat.st_mtime_ns}:{hashlib.sha256(data).hexdigest()}"
                try:
                    directory = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError as exc:
                    raise CanonicalStoreError(
                        "facility_master_local_durability_unconfirmed", saved=True,
                        revision=revision, reload_required=True,
                    ) from exc
                return MasterSnapshot(_decode(data), revision, source)
            finally:
                if temporary is not None:
                    os.unlink(temporary)
    except CanonicalStoreError:
        raise
    except Exception as exc:
        raise CanonicalStoreError("facility_master_storage_unavailable") from exc
