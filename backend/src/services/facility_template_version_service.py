from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from sqlalchemy import inspect

from src.db import engine
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState

WORKFLOW_V2_META_KEY = "workflow_v2"
VALID_TEMPLATE_VERSION_STATUSES = {"draft", "active", "archived", "invalid", "repair_blocked"}
_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED = False


def ensure_facility_template_version_schema() -> None:
    global _FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED
    if _FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED:
        return
    inspector = inspect(engine)
    if "facility_template_versions" not in set(inspector.get_table_names()):
        raise RuntimeError(
            "facility template version schema is not migrated; run migrations through 0025: "
            "facility_template_versions"
        )
    columns = {
        str(column.get("name") or "")
        for column in inspector.get_columns("facility_template_versions")
    }
    required_columns = {column.name for column in FacilityTemplateVersion.__table__.columns}
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        raise RuntimeError(
            "facility template version schema is not migrated; run migrations through 0025: "
            + ", ".join(f"facility_template_versions.{column}" for column in missing_columns)
        )
    if engine.dialect.name == "postgresql":
        indexes = {str(index.get("name") or "") for index in inspector.get_indexes("facility_template_versions")}
        missing_indexes = sorted(
            {"ix_facility_template_versions_valid_from", "ix_facility_template_versions_valid_to"} - indexes
        )
        if missing_indexes:
            raise RuntimeError(
                "facility template version schema is not migrated; run migrations through 0025: "
                + ", ".join(missing_indexes)
            )
    _FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED = True


def _normalize_token(value: object) -> str:
    return str(value or "").strip().lower()


_SOURCE_INDEX_REQUIRED_ROLES = {"date", "daypart", "menu_name", "aux", "quantity", "note", "remarks"}


def _parse_source_index(column: dict[str, Any]) -> int | None:
    raw = column.get("source_index")
    try:
        parsed = int(raw) if raw is not None else None
    except Exception:
        return None
    return parsed if parsed is not None and parsed >= 0 else None




def _source_index_required(column: dict[str, Any]) -> bool:
    return _normalize_token(column.get("role")) in _SOURCE_INDEX_REQUIRED_ROLES




def _repair_missing_source_indexes(
    columns: list[dict[str, Any]],
    *,
    reference_columns: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_source_index_repair_disabled", status_code=409)




def normalize_template_columns(columns: Any) -> list[dict[str, Any]]:
    """Compatibility export: preserve authored columns; never repair or enrich them."""
    from src.services.canonical_facility_store import CanonicalStoreError
    if not isinstance(columns, list) or any(not isinstance(column, dict) for column in columns):
        raise CanonicalStoreError("authored_template_columns_required", status_code=422)
    validation = validate_template_columns(columns)
    if validation["errors"]:
        raise CanonicalStoreError("authored_template_columns_invalid", status_code=422, errors=validation["errors"])
    return deepcopy(columns)


def validate_template_columns(columns: list[dict[str, Any]]) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not columns:
        errors.append("template_columns_missing")
    column_ids = [str(column.get("column_id") or "").strip() for column in columns]
    duplicate_ids = sorted({column_id for column_id in column_ids if column_id and column_ids.count(column_id) > 1})
    if duplicate_ids:
        errors.append("template_column_id_duplicate")
    if not any(_normalize_token(column.get("role")) == "quantity" for column in columns):
        errors.append("template_quantity_columns_missing")
    source_indexes: list[int] = []
    source_index_missing = False
    source_index_invalid = False
    excluded_quantity_warning_added = False
    for column in columns:
        raw_source_index = column.get("source_index")
        parsed_source_index = _parse_source_index(column)
        if raw_source_index is not None and parsed_source_index is None:
            source_index_invalid = True
        if parsed_source_index is not None:
            source_indexes.append(parsed_source_index)
        elif _source_index_required(column):
            source_index_missing = True
        if (
            not excluded_quantity_warning_added
            and _normalize_token(column.get("role")) == "quantity"
            and (column.get("semantic") or {}).get("aggregation_role") == "exclude"
        ):
            warnings.append("template_quantity_column_excluded_from_aggregation")
            excluded_quantity_warning_added = True
    if source_index_invalid:
        errors.append("template_source_index_invalid")
    if source_index_missing:
        errors.append("template_source_index_missing")
    duplicate_source_indexes = sorted({value for value in source_indexes if source_indexes.count(value) > 1})
    if duplicate_source_indexes:
        errors.append("template_source_index_duplicate")
    return {"errors": errors, "warnings": warnings}


def template_digest(
    *,
    template_id: str | None,
    columns: list[dict[str, Any]],
    cells: list[dict[str, Any]] | None = None,
) -> str:
    payload = {
        "template_id": str(template_id or "").strip() or None,
        "columns": columns,
        "cells": cells or [],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()








def template_version_applies_on(row: FacilityTemplateVersion | None, effective_date: object | None) -> bool:
    from src.services.canonical_facility_store import CanonicalStoreError, canonical_date
    from src.services.canonical_facility_lineage import verify_projection
    if row is None:
        return False
    try:
        verify_projection(row)
        day = canonical_date(effective_date, "effective_date")
        return row.valid_from <= day and (row.valid_to is None or day <= row.valid_to)
    except CanonicalStoreError:
        return False






def get_active_template_versions(
    session: Any,
    facility_id: str,
    *,
    effective_date: object | None = None,
) -> list[FacilityTemplateVersion]:
    """Compatibility name: selects JSON only, with a required explicit date."""
    row, error = resolve_single_active_template_version(session, facility_id, effective_date=effective_date)
    if error:
        from src.services.canonical_facility_store import CanonicalStoreError
        raise CanonicalStoreError(error, status_code=409)
    return [row]


def get_active_template_version(
    session: Any,
    facility_id: str,
    *,
    effective_date: object | None = None,
) -> FacilityTemplateVersion | None:
    row, error = resolve_single_active_template_version(session, facility_id, effective_date=effective_date)
    if error:
        from src.services.canonical_facility_store import CanonicalStoreError
        raise CanonicalStoreError(error, status_code=409)
    return row


def resolve_single_active_template_version(
    session: Any,
    facility_id: str,
    *,
    effective_date: object | None = None,
) -> tuple[FacilityTemplateVersion | None, str | None]:
    _config, row, error = resolve_facility_config_for_date(session, facility_id=facility_id, effective_date=effective_date)
    return row, error


def serialize_template_version(row: FacilityTemplateVersion | None) -> dict[str, Any] | None:
    if row is None:
        return None
    from src.services.canonical_facility_lineage import SOURCE, verify_projection
    receipt = verify_projection(row) if row.source == SOURCE else None
    result = {
        "canonical_lineage": receipt,
        "reference_status": ("canonical_projection" if getattr(row, "materialized", True) else "canonical_selection") if receipt else "legacy_readonly_history",
        "id": row.id,
        "facility_id": row.facility_id,
        "version": row.version,
        "status": row.status,
        "template_id": row.template_id,
        "source": row.source,
        "config": deepcopy(row.config_json) if isinstance(row.config_json, dict) else None,
        "columns": list(row.columns_json or []),
        "template_digest": row.template_digest,
        "validation": dict(row.validation_json or {}),
        "valid_from": row.valid_from.isoformat() if isinstance(row.valid_from, date) else None,
        "valid_to": row.valid_to.isoformat() if isinstance(row.valid_to, date) else None,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "activated_at": row.activated_at.isoformat() if isinstance(row.activated_at, datetime) else None,
    }
    if receipt:
        template = receipt["authored_version"]["facility"]["fax_template"]
        if "cells" in template:
            result["cells"] = deepcopy(template["cells"])
    else:
        result["cells"] = deepcopy(row.cells_json)
    return result


def get_legacy_template_history(session: Any, version_id: str) -> dict[str, Any] | None:
    """Explicit historical inspection only: not a dated resolver or lineage adoption."""
    from src.services.canonical_facility_lineage import read_projection
    row = read_projection(session, version_id)
    return serialize_template_version(row)


def materialize_facility_version_lineage(session: Any, selection: dict) -> tuple[dict, Any]:
    from src.services.canonical_facility_lineage import materialize
    return materialize(session, selection)




def _write_workflow_meta_payload(row: OrderWorkflowState, meta: dict[str, Any]) -> None:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_missing_lineage_stamp_disabled", status_code=409)


def _stamp_workflow_template_lineage(
    row: OrderWorkflowState,
    *,
    facility_id: str,
    version: FacilityTemplateVersion,
) -> bool:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_missing_lineage_stamp_disabled", status_code=409)


def _set_template_version_if_missing(row: Any, version_id: str) -> bool:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_missing_lineage_stamp_disabled", status_code=409)


def _ensure_active_version_for_backfill(
    session: Any,
    *,
    facility_id: str,
    cache: dict[str, FacilityTemplateVersion],
    summary: dict[str, Any],
    created_by: str,
) -> FacilityTemplateVersion | None:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_canonical_lineage_backfill_disabled", status_code=409)


def backfill_facility_template_version_lineage(
    session: Any,
    *,
    dry_run: bool = False,
    actor: str = "facility-template-version-backfill",
) -> dict[str, Any]:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_canonical_lineage_backfill_disabled", status_code=409)


def _resolved_config_from_parts(
    *,
    facility_id: str,
    facility_name: str | None,
    config: dict[str, Any],
    template_id: str | None,
    columns: list[dict[str, Any]],
    version: FacilityTemplateVersion,
) -> dict[str, Any]:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_db_config_resolution_disabled", status_code=409)


def ensure_active_template_version_from_resolved_config(
    session: Any,
    *,
    facility_id: str,
    facility_config: dict[str, Any] | None = None,
    created_by: str = "legacy-resolved-config-import",
) -> FacilityTemplateVersion | None:
    from src.services.canonical_facility_store import CanonicalStoreError
    raise CanonicalStoreError("legacy_current_config_lineage_stamp_disabled", status_code=409)


def resolve_facility_config_for_date(
    session: Any,
    *,
    facility_id: str,
    effective_date: object | None,
) -> tuple[dict[str, Any] | None, FacilityTemplateVersion | None, str | None]:
    from src.services.canonical_facility_lineage import resolve
    from src.services.canonical_facility_store import CanonicalStoreError
    if not isinstance(facility_id, str) or not facility_id:
        return None, None, "facility_id_required"
    try:
        config, row = resolve(session, facility_id, effective_date)
        return config, row, None
    except CanonicalStoreError as exc:
        return None, None, exc.detail["error"]


def save_columns_for_order(
    session: Any,
    *,
    order: Order,
    columns: list[dict[str, Any]] | None,
    actor: str = "workflow-v2-facility-template-columns",
) -> tuple[dict[str, Any] | None, str | None]:
    return {"error": "legacy_facility_template_column_override_disabled"}, "legacy_facility_template_column_override_disabled"


def save_template_registration_for_facility(
    session: Any,
    *,
    facility_id: str,
    fax_template_id: str,
    fax_template_ids: list[str] | None = None,
    actor: str = "facility-fax-template-registration",
) -> tuple[dict[str, Any] | None, str | None]:
    return {"error": "legacy_fax_template_registry_disabled"}, "legacy_fax_template_registry_disabled"
