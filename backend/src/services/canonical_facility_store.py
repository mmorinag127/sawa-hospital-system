"""Facility JSON bytes and their storage revision are one CAS snapshot."""

from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
from datetime import date
import fcntl
import hashlib
import json
import math
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


def canonical_date(value: object, field: str) -> date:
    if value is None or value == "":
        raise CanonicalStoreError("facility_version_date_required", status_code=422, field=field)
    if type(value) is date:
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise CanonicalStoreError("facility_version_date_invalid", status_code=422, field=field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CanonicalStoreError("facility_version_date_invalid", status_code=422, field=field) from exc
    if parsed.isoformat() != value:
        raise CanonicalStoreError("facility_version_date_invalid", status_code=422, field=field)
    return parsed


def facility_versions(master: dict, facility_id: str) -> list[dict]:
    entries = master.get("facilities")
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise CanonicalStoreError("facility_versions_master_invalid", status_code=422)
    matches = [entry for entry in entries if entry.get("facility_id") == facility_id]
    if len(matches) != 1:
        raise CanonicalStoreError("facility_version_target_unresolved", status_code=409, facility_id=facility_id)
    entry = matches[0]
    versions = entry.get("versions", [])
    if not isinstance(versions, list):
        raise CanonicalStoreError("facility_versions_invalid", status_code=422)
    ids = set()
    ranges = []
    for version in versions:
        if not isinstance(version, dict):
            raise CanonicalStoreError("facility_version_invalid", status_code=422)
        version_id = version.get("version_id")
        if not isinstance(version_id, str) or not version_id.strip() or version_id in ids:
            raise CanonicalStoreError("facility_version_identity_invalid", status_code=422)
        ids.add(version_id)
        start = canonical_date(version.get("valid_from"), "valid_from")
        if "valid_to" not in version:
            raise CanonicalStoreError("facility_version_date_required", status_code=422, field="valid_to")
        end = None if version["valid_to"] is None else canonical_date(version["valid_to"], "valid_to")
        if end is not None and end < start:
            raise CanonicalStoreError("facility_version_period_invalid", status_code=422)
        content = version.get("facility")
        if not isinstance(content, dict) or content.get("facility_id") != facility_id or \
                not isinstance(content.get("facility_name"), str) or not content["facility_name"].strip() or \
                "areas" not in content or (content["areas"] is not None and not isinstance(content["areas"], list)) or \
                not isinstance(content.get("fax_template"), dict) or not content["fax_template"]:
            raise CanonicalStoreError("facility_version_snapshot_incomplete", status_code=422, version_id=version_id)
        validate_authored_snapshot(content, version_id)
        for field in ("valid_from", "valid_to"):
            if field in content and content[field] != version[field]:
                raise CanonicalStoreError("facility_version_period_mismatch", status_code=422, field=field)
        for old_start, old_end in ranges:
            if (old_end is None or start <= old_end) and (end is None or old_start <= end):
                raise CanonicalStoreError("facility_version_period_overlap", status_code=409)
        ranges.append((start, end))
    return deepcopy(versions)


def validate_authored_snapshot(content: dict, version_id: str) -> None:
    # These are the production validators, not template normalization or repair.
    from src.services import config_validator
    from src.services.facility_template_version_service import validate_template_columns
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    try:
        errors = config_validator.validate_facility_config(content)["errors"]
    except (TypeError, ValueError, AttributeError) as exc:
        raise CanonicalStoreError("facility_version_snapshot_incomplete", status_code=422,
                                  version_id=version_id, validation_errors=["config_schema_invalid"]) from exc
    warnings = []
    template = content["fax_template"]
    columns = template.get("columns")
    if not isinstance(columns, list) or any(not isinstance(column, dict) for column in columns):
        errors.append("template_columns_missing_or_invalid")
    else:
        config_validator._validate_columns(columns, "fax_template.columns", errors, warnings)
        # The production semantic validator expects an object when semantic is supplied.
        if any(column.get("semantic") is not None and not isinstance(column["semantic"], dict)
               for column in columns):
            errors.append("template_column_semantic_invalid")
        else:
            errors.extend(validate_template_columns([
                {key: value for key, value in column.items() if key != "semantic" or value is not None}
                for column in columns
            ])["errors"])
        for column in columns:
            if column.get("role") in ("quantity", "quantity_change") and \
                    canonical_field_name_from_template_column(column) is None:
                errors.append("template_quantity_field_unresolved")
    for field, validator in (("match", config_validator._validate_template_match),
                             ("warp", config_validator._validate_template_warp),
                             ("rois", config_validator._validate_template_rois),
                             ("postprocess", config_validator._validate_template_postprocess)):
        if field in template:
            if field != "rois":
                validator(template[field], f"fax_template.{field}", errors)
    validate_authored_geometry(template, errors)
    config_validator._validate_ocr_provider_config(template, "fax_template", errors)
    config_validator._validate_order_form_source_config(template, "fax_template", errors)
    if errors:
        raise CanonicalStoreError("facility_version_snapshot_incomplete", status_code=422,
                                  version_id=version_id, validation_errors=errors)


def validate_authored_geometry(template: dict, errors: list[str]) -> None:
    from src.services import config_validator

    def numbers(value, size):
        return isinstance(value, list) and len(value) == size and all(
            type(item) in (int, float) and math.isfinite(item) for item in value)

    dimensions = None
    warp = template.get("warp")
    if isinstance(warp, dict) and "output_size" in warp:
        output = warp["output_size"]
        if not numbers(output, 2) or any(item <= 0 for item in output):
            errors.append("fax_template.warp.output_size invalid")
        else:
            dimensions = output

    def box(value, path, *, xywh=False, normalized=False):
        if value is None:
            return
        if isinstance(value, dict) and xywh:
            value = [value.get(key) for key in ("x", "y", "w", "h")]
        if not numbers(value, 4):
            errors.append(f"{path} must contain four finite coordinates")
            return
        x, y, a, b = value
        right, bottom = (x + a, y + b) if xywh else (a, b)
        if x < 0 or y < 0 or right <= x or bottom <= y:
            errors.append(f"{path} has invalid origin or extent")
        # ROI consumer uses this threshold, not the top-level bbox contract.
        roi_normalized = xywh and max(value) <= 1.5
        if normalized or roi_normalized:
            if right > 1 or bottom > 1:
                errors.append(f"{path} exceeds normalized domain")
        elif dimensions is None:
            errors.append(f"{path} pixel domain requires recorded warp dimensions")
        elif right > dimensions[0] or bottom > dimensions[1]:
            errors.append(f"{path} exceeds recorded warp dimensions")

    for field in ("facility_name_box", "table_box", "grid_table_box"):
        if field in template:
            # fax_extractor name/date crops are normalized XYXY; grid uses explicit units.
            normalized = field == "facility_name_box" or template.get("units") == "normalized"
            box(template[field], f"fax_template.{field}", normalized=normalized)
    def edges(value, path, normalized, limit):
        if value is None:
            return
        if not isinstance(value, list) or len(value) < 2 or not numbers(value, len(value)):
            errors.append(f"{path} must contain finite ordered edges")
        elif value[0] < 0 or any(left >= right for left, right in zip(value, value[1:])):
            errors.append(f"{path} has invalid order or extent")
        elif normalized and value[-1] > 1:
            errors.append(f"{path} exceeds normalized domain")
        elif not normalized and (limit is None or value[-1] > limit):
            errors.append(f"{path} exceeds or lacks recorded pixel domain")
    for field, axis in (("grid_column_edges", 0), ("grid_row_edges", 1)):
        if field in template:
            edges(template[field], f"fax_template.{field}", template.get("units") == "normalized",
                  dimensions[axis] if dimensions else None)
    for idx, item in enumerate(config_validator._ensure_list(template.get("date_boxes"), "fax_template.date_boxes", errors)):
        if not isinstance(item, dict) or "bbox" not in item:
            errors.append(f"fax_template.date_boxes[{idx}].bbox required")
        else:
            box(item["bbox"], f"fax_template.date_boxes[{idx}].bbox", normalized=True)
    rois = template.get("rois")
    if rois is None:
        return
    if not isinstance(rois, dict):
        errors.append("fax_template.rois must be an object")
        return
    for field in ("facility_name_box", "table_box", "menu_band", "notes_box"):
        if field in rois:
            box(rois[field], f"fax_template.rois.{field}", xywh=True)
    qty = rois.get("qty")
    if qty is None:
        return
    if not isinstance(qty, dict):
        errors.append("fax_template.rois.qty must be an object")
        return
    for field in ("column_edges", "row_edges"):
        if field in qty:
            edges(qty[field], f"fax_template.rois.qty.{field}", True, None)
    # Validate schema/count without misclassifying the builder's dictionary XYWH cells.
    view = deepcopy(rois)
    cells = config_validator._ensure_list(qty.get("boxes_row_major"), "fax_template.rois.qty.boxes_row_major", errors)
    view["qty"]["boxes_row_major"] = []
    for idx, cell in enumerate(cells):
        box(cell, f"fax_template.rois.qty.boxes_row_major[{idx}]", xywh=True)
        view["qty"]["boxes_row_major"].append(
            [cell.get(key) for key in ("x", "y", "w", "h")] if isinstance(cell, dict) else cell)
    config_validator._validate_template_rois(view, "fax_template.rois", errors)
    schema = qty.get("schema")
    if isinstance(schema, dict):
        for field in ("rows", "cols"):
            if schema.get(field) is not None and (type(schema[field]) is not int or schema[field] < 0):
                errors.append(f"fax_template.rois.qty.schema.{field} invalid")
        rows, cols = schema.get("rows"), schema.get("cols")
        if type(rows) is int and type(cols) is int and rows > 0 and cols > 0 and len(cells) < rows * cols:
            errors.append("fax_template.rois.qty fixed schema lacks recorded cells")


def validate_facility_versions(master: dict) -> None:
    if not isinstance(master, dict):
        raise CanonicalStoreError("facility_versions_master_invalid", status_code=422)
    entries = master.get("facilities")
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise CanonicalStoreError("facility_versions_master_invalid", status_code=422)
    for entry in entries:
        if "versions" in entry:
            facility_versions(master, entry.get("facility_id"))


def resolve_facility_version(snapshot: MasterSnapshot, facility_id: str, effective_date: object) -> dict:
    selected_date = canonical_date(effective_date, "effective_date")
    versions = facility_versions(snapshot.master, facility_id)
    matches = [version for version in versions
               if canonical_date(version["valid_from"], "valid_from") <= selected_date and
               (version["valid_to"] is None or selected_date <= canonical_date(version["valid_to"], "valid_to"))]
    if len(matches) != 1:
        raise CanonicalStoreError("facility_version_date_unrecorded" if not matches else "facility_version_date_ambiguous",
                                  status_code=409, facility_id=facility_id, effective_date=selected_date.isoformat())
    version = matches[0]
    return {"facility_id": facility_id, "effective_date": selected_date.isoformat(), "version": version,
            "facility": deepcopy(version["facility"]), "revision": snapshot.revision, "source": "canonical_json"}


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
    validate_facility_versions(master)
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
