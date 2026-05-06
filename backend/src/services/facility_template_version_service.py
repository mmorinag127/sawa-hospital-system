from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, text

from src.db import Base, engine
from src.models.facility import Facility, FacilityConfig
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import config_service
from src.services.config_validator import validate_facility_config
from src.services.template_field_schema_service import derive_row_fields_from_columns


Base.metadata.create_all(bind=engine)


def _ensure_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        existing_tables = {
            str(row[0])
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }
        if "facility_template_versions" not in existing_tables:
            return
        table_columns: dict[str, set[str]] = {}
        for table_name in (
            "orders",
            "ocr_jobs",
            "order_ocr_evidence_runs",
            "order_sheet_drafts",
            "order_confirmed_snapshots",
            "order_workflow_states",
            "order_current_states",
        ):
            if table_name not in existing_tables:
                continue
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            table_columns[table_name] = {str(row[1]) for row in rows if len(row) > 1}
        if "orders" in table_columns and "template_version_id" not in table_columns["orders"]:
            conn.execute(text("ALTER TABLE orders ADD COLUMN template_version_id VARCHAR"))
        for table_name in (
            "ocr_jobs",
            "order_ocr_evidence_runs",
            "order_sheet_drafts",
            "order_confirmed_snapshots",
            "order_workflow_states",
            "order_current_states",
        ):
            if table_name in table_columns and "template_version_id" not in table_columns[table_name]:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN template_version_id VARCHAR"))


_ensure_sqlite_schema()

WORKFLOW_V2_META_KEY = "workflow_v2"


def _now() -> datetime:
    return datetime.utcnow()


def _new_template_version_id() -> str:
    return f"FTV{uuid4().hex[:16]}"


def _normalize_token(value: object) -> str:
    return str(value or "").strip().lower()


_SOURCE_INDEX_REQUIRED_ROLES = {"date", "daypart", "menu_name", "quantity", "note", "remarks"}


def _parse_source_index(column: dict[str, Any]) -> int | None:
    raw = column.get("source_index")
    try:
        parsed = int(raw) if raw is not None else None
    except Exception:
        return None
    return parsed if parsed is not None and parsed >= 0 else None


def _column_id_for(column: dict[str, Any], source_index: int | None, fallback_index: int) -> str:
    existing = str(column.get("column_id") or "").strip()
    if existing:
        return existing
    role = _normalize_token(column.get("role")) or "col"
    if source_index is None:
        return f"col_unresolved_{fallback_index:03d}_{role}"
    return f"col_{source_index:03d}_{role}"


def _source_index_required(column: dict[str, Any]) -> bool:
    return _normalize_token(column.get("role")) in _SOURCE_INDEX_REQUIRED_ROLES


def _semantic_for_column(column: dict[str, Any]) -> dict[str, Any]:
    role = _normalize_token(column.get("role"))
    if role != "quantity":
        return {
            "role": role or "unknown",
            "aggregation_role": "exclude",
        }
    diet_type = str(column.get("diet_type") or "unknown").strip() or "unknown"
    area_id = str(column.get("area_id") or "X").strip() or "X"
    bag_type = str(column.get("bag_type") or "").strip() or None
    return {
        "role": "quantity",
        "diet_type": diet_type,
        "area_id": area_id,
        "bag_type": bag_type,
        "aggregation_role": "exclude" if diet_type in {"placeholder", "unknown"} else "include",
    }


def normalize_template_columns(columns: Any) -> list[dict[str, Any]]:
    normalized = config_service.normalize_fax_template_columns(columns)
    enriched: list[dict[str, Any]] = []
    for idx, raw_column in enumerate(normalized):
        column = deepcopy(raw_column)
        column["index"] = idx
        source_index = _parse_source_index(column)
        if source_index is None:
            column.pop("source_index", None)
        else:
            column["source_index"] = source_index
        column["column_id"] = _column_id_for(column, source_index, idx)
        semantic = _semantic_for_column(column)
        column["semantic"] = semantic
        column["read_target"] = bool(_normalize_token(column.get("role")) in {"quantity", "note", "remarks"})
        enriched.append(column)
    return enriched


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


def _next_version_label(session: Any, facility_id: str) -> str:
    count = (
        session.query(FacilityTemplateVersion)
        .filter(FacilityTemplateVersion.facility_id == facility_id)
        .count()
    )
    return str(count + 1)


def get_active_template_version(session: Any, facility_id: str) -> FacilityTemplateVersion | None:
    normalized_facility_id = str(facility_id or "").strip()
    if not normalized_facility_id:
        return None
    return (
        session.query(FacilityTemplateVersion)
        .filter(
            FacilityTemplateVersion.facility_id == normalized_facility_id,
            FacilityTemplateVersion.status == "active",
        )
        .order_by(FacilityTemplateVersion.activated_at.desc(), FacilityTemplateVersion.created_at.desc())
        .first()
    )


def serialize_template_version(row: FacilityTemplateVersion | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "facility_id": row.facility_id,
        "version": row.version,
        "status": row.status,
        "template_id": row.template_id,
        "source": row.source,
        "columns": list(row.columns_json or []),
        "cells": list(row.cells_json or []),
        "template_digest": row.template_digest,
        "validation": dict(row.validation_json or {}),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "activated_at": row.activated_at.isoformat() if isinstance(row.activated_at, datetime) else None,
    }


def _workflow_meta_payload(row: OrderWorkflowState) -> dict[str, Any]:
    existing = row.secondary_actions_json if isinstance(row.secondary_actions_json, dict) else {}
    meta = existing.get(WORKFLOW_V2_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _write_workflow_meta_payload(row: OrderWorkflowState, meta: dict[str, Any]) -> None:
    existing = dict(row.secondary_actions_json or {}) if isinstance(row.secondary_actions_json, dict) else {}
    existing[WORKFLOW_V2_META_KEY] = dict(meta)
    row.secondary_actions_json = existing


def _stamp_workflow_template_lineage(
    row: OrderWorkflowState,
    *,
    facility_id: str,
    version: FacilityTemplateVersion,
) -> bool:
    changed = False
    if not str(row.template_version_id or "").strip():
        row.template_version_id = version.id
        changed = True
    meta = _workflow_meta_payload(row)
    if not str(meta.get("facility_id") or "").strip() and facility_id:
        meta["facility_id"] = facility_id
        changed = True
    if not str(meta.get("template_version_id") or "").strip():
        meta["template_version_id"] = version.id
        changed = True
    if not str(meta.get("template_version_digest") or "").strip():
        meta["template_version_digest"] = version.template_digest
        changed = True
    if not str(meta.get("template_id") or "").strip() and str(version.template_id or "").strip():
        meta["template_id"] = version.template_id
        changed = True
    if changed:
        _write_workflow_meta_payload(row, meta)
    return changed


def _set_template_version_if_missing(row: Any, version_id: str) -> bool:
    if row is None:
        return False
    if str(getattr(row, "template_version_id", "") or "").strip():
        return False
    row.template_version_id = version_id
    return True


def _ensure_active_version_for_backfill(
    session: Any,
    *,
    facility_id: str,
    cache: dict[str, FacilityTemplateVersion],
    summary: dict[str, Any],
    created_by: str,
) -> FacilityTemplateVersion | None:
    normalized_facility_id = str(facility_id or "").strip()
    if not normalized_facility_id:
        return None
    if normalized_facility_id in cache:
        return cache[normalized_facility_id]
    active_before = get_active_template_version(session, normalized_facility_id)
    try:
        facility_config = config_service.get_facility_config(normalized_facility_id)
    except Exception:
        facility_config = None
    version = ensure_active_template_version_from_resolved_config(
        session,
        facility_id=normalized_facility_id,
        facility_config=facility_config if isinstance(facility_config, dict) else None,
        created_by=created_by,
    )
    if version is None:
        skipped = summary.setdefault("skipped", {})
        skipped["facility_template_unresolved"] = int(skipped.get("facility_template_unresolved") or 0) + 1
        return None
    if active_before is None:
        summary["active_versions_created"] = int(summary.get("active_versions_created") or 0) + 1
    else:
        summary["active_versions_reused"] = int(summary.get("active_versions_reused") or 0) + 1
    cache[normalized_facility_id] = version
    return version


def backfill_facility_template_version_lineage(
    session: Any,
    *,
    dry_run: bool = False,
    actor: str = "facility-template-version-backfill",
) -> dict[str, Any]:
    """Stamp canonical facility-template lineage onto existing order artifacts.

    This only fills missing ``template_version_id`` values. Existing non-null
    lineage is never overwritten because a mismatch must be resolved explicitly.
    """

    summary: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "active_versions_created": 0,
        "active_versions_reused": 0,
        "updated": {
            "orders": 0,
            "ocr_jobs": 0,
            "order_ocr_evidence_runs": 0,
            "order_sheet_drafts": 0,
            "order_confirmed_snapshots": 0,
            "order_workflow_states": 0,
            "order_current_states": 0,
        },
        "skipped": {},
    }
    version_by_facility: dict[str, FacilityTemplateVersion] = {}

    for facility in session.query(Facility).order_by(Facility.id).all():
        _ensure_active_version_for_backfill(
            session,
            facility_id=facility.id,
            cache=version_by_facility,
            summary=summary,
            created_by=actor,
        )

    for order in session.query(Order).order_by(Order.id).all():
        facility_id = str(order.facility_code or "").strip()
        if not facility_id:
            skipped = summary.setdefault("skipped", {})
            skipped["order_facility_missing"] = int(skipped.get("order_facility_missing") or 0) + 1
            continue
        version = _ensure_active_version_for_backfill(
            session,
            facility_id=facility_id,
            cache=version_by_facility,
            summary=summary,
            created_by=actor,
        )
        if version is None:
            skipped = summary.setdefault("skipped", {})
            skipped["order_template_unresolved"] = int(skipped.get("order_template_unresolved") or 0) + 1
            continue
        updated = summary["updated"]
        if _set_template_version_if_missing(order, version.id):
            updated["orders"] += 1

        workflow = session.get(OrderWorkflowState, order.id)
        if workflow is not None and _stamp_workflow_template_lineage(
            workflow,
            facility_id=facility_id,
            version=version,
        ):
            updated["order_workflow_states"] += 1

        job_ids = [f"OCR-{order.id}"]
        message_id = str(order.message_id or "").strip()
        if message_id:
            job_ids.append(f"OCR-{message_id}")
        if workflow is not None:
            meta_job_id = str(_workflow_meta_payload(workflow).get("ocr_job_id") or "").strip()
            if meta_job_id and meta_job_id not in job_ids:
                job_ids.append(meta_job_id)
        for job_id in job_ids:
            job = session.get(OcrJob, job_id)
            if _set_template_version_if_missing(job, version.id):
                updated["ocr_jobs"] += 1

        for row in (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order.id, OrderOcrEvidenceRun.template_version_id.is_(None))
            .all()
        ):
            row.template_version_id = version.id
            updated["order_ocr_evidence_runs"] += 1
        for row in (
            session.query(OrderSheetDraft)
            .filter(OrderSheetDraft.order_id == order.id, OrderSheetDraft.template_version_id.is_(None))
            .all()
        ):
            row.template_version_id = version.id
            updated["order_sheet_drafts"] += 1
        for row in (
            session.query(OrderConfirmedSnapshot)
            .filter(OrderConfirmedSnapshot.order_id == order.id, OrderConfirmedSnapshot.template_version_id.is_(None))
            .all()
        ):
            row.template_version_id = version.id
            updated["order_confirmed_snapshots"] += 1
        current_state = session.get(OrderCurrentState, order.id)
        if current_state is not None and _set_template_version_if_missing(current_state, version.id):
            state_payload = current_state.state_json if isinstance(current_state.state_json, dict) else {}
            next_payload = dict(state_payload)
            next_payload["template_version_id"] = version.id
            current_state.state_json = next_payload
            updated["order_current_states"] += 1

    if dry_run:
        session.rollback()
    else:
        session.flush()
    return summary


def _resolved_config_from_parts(
    *,
    facility_id: str,
    facility_name: str | None,
    config: dict[str, Any],
    template_id: str | None,
    columns: list[dict[str, Any]],
    version: FacilityTemplateVersion,
) -> dict[str, Any]:
    facility_payload = {"facility_id": facility_id, "facility_name": facility_name, **deepcopy(config)}
    try:
        resolved = config_service._build_facility_config(  # noqa: SLF001
            facility_id=facility_id,
            facility=facility_payload,
            selected_template_id=template_id,
        )
    except Exception:
        resolved = deepcopy(config)
    resolved["facility_id"] = facility_id
    if facility_name:
        resolved["facility_name"] = facility_name
    if template_id:
        resolved["fax_template_id"] = template_id
        resolved.setdefault("fax_template_ids", [template_id])
    fax_template = deepcopy(resolved.get("fax_template") or {})
    fax_template["template_id"] = template_id or fax_template.get("template_id")
    fax_template["columns"] = deepcopy(columns)
    fax_template["columns_authoritative"] = True
    fax_template["main_ocr_row_fields"] = derive_row_fields_from_columns(columns)
    fax_template["facility_template_version_id"] = version.id
    fax_template["facility_template_version_digest"] = version.template_digest
    resolved["fax_template"] = fax_template
    resolved["facility_template_version_id"] = version.id
    resolved["facility_template_version"] = serialize_template_version(version)
    return resolved


def ensure_active_template_version_from_resolved_config(
    session: Any,
    *,
    facility_id: str,
    facility_config: dict[str, Any] | None = None,
    created_by: str = "legacy-resolved-config-import",
) -> FacilityTemplateVersion | None:
    normalized_facility_id = str(facility_id or "").strip()
    if not normalized_facility_id:
        return None
    if session.get(Facility, normalized_facility_id) is None:
        from src.services import facility_service  # local import avoids service startup cycles

        facility_service._ensure_facility_sync(session)  # noqa: SLF001
        session.flush()
        if session.get(Facility, normalized_facility_id) is None:
            return None
    config = facility_config if isinstance(facility_config, dict) else config_service.get_facility_config(normalized_facility_id)
    if not isinstance(config, dict):
        return None
    template = config.get("fax_template") if isinstance(config.get("fax_template"), dict) else {}
    columns = normalize_template_columns(template.get("columns"))
    validation = validate_template_columns(columns)
    if validation["errors"]:
        return None
    template_id = str(config.get("fax_template_id") or template.get("template_id") or "").strip() or None
    digest = template_digest(template_id=template_id, columns=columns)
    active = get_active_template_version(session, normalized_facility_id)
    if active is not None and active.template_digest == digest:
        return active
    now = _now()
    if active is not None:
        active.status = "archived"
        active.archived_at = now
    version = FacilityTemplateVersion(
        id=_new_template_version_id(),
        facility_id=normalized_facility_id,
        version=_next_version_label(session, normalized_facility_id),
        status="active",
        template_id=template_id,
        source=created_by,
        columns_json=columns,
        cells_json=[],
        template_digest=digest,
        validation_json={**validation, "source": created_by},
        created_by=created_by,
        created_at=now,
        activated_at=now,
    )
    session.add(version)
    session.flush()
    return version


def save_columns_for_order(
    session: Any,
    *,
    order: Order,
    columns: list[dict[str, Any]] | None,
    actor: str = "workflow-v2-facility-template-columns",
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(columns, list) or not columns:
        return None, "columns_invalid"
    facility_id = str(order.facility_code or "").strip()
    if not facility_id:
        return None, "facility_missing"
    facility = session.get(Facility, facility_id)
    if facility is None:
        from src.services import facility_service  # local import avoids service startup cycles

        facility_service._ensure_facility_sync(session)  # noqa: SLF001
        session.flush()
        facility = session.get(Facility, facility_id)
    if facility is None:
        return None, "facility_not_found"

    current_config = facility.config.config_json if facility.config and isinstance(facility.config.config_json, dict) else {}
    normalized_columns = normalize_template_columns(columns)
    column_validation = validate_template_columns(normalized_columns)
    if column_validation["errors"]:
        return {"validation": column_validation}, "validation_error"

    next_config = deepcopy(current_config)
    override = deepcopy(next_config.get("fax_template_override") or {})
    override["columns"] = normalized_columns
    override["columns_authoritative"] = True
    override["main_ocr_row_fields"] = derive_row_fields_from_columns(normalized_columns)
    next_config["fax_template_override"] = override
    next_config["facility_template_source"] = "operator_override"

    validation = validate_facility_config(next_config)
    validation = {
        "errors": list(validation.get("errors") or []) + list(column_validation.get("errors") or []),
        "warnings": list(validation.get("warnings") or []) + list(column_validation.get("warnings") or []),
    }
    if validation["errors"]:
        return {"validation": validation}, "validation_error"

    sanitized = config_service.sanitize_facility_config_for_storage(
        facility_id,
        next_config,
        current_config=current_config,
        allow_authoritative_column_changes=True,
    )
    session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
    session.add(FacilityConfig(facility_id=facility_id, config_json=sanitized))

    active_versions = (
        session.query(FacilityTemplateVersion)
        .filter(
            FacilityTemplateVersion.facility_id == facility_id,
            FacilityTemplateVersion.status == "active",
        )
        .all()
    )
    now = _now()
    for active in active_versions:
        active.status = "archived"
        active.archived_at = now

    template_id = (
        str(sanitized.get("fax_template_id") or "").strip()
        or str((sanitized.get("fax_template_ids") or [None])[0] or "").strip()
        or str((current_config.get("fax_template_id") if isinstance(current_config, dict) else "") or "").strip()
        or None
    )
    digest = template_digest(template_id=template_id, columns=normalized_columns)
    version = FacilityTemplateVersion(
        id=_new_template_version_id(),
        facility_id=facility_id,
        version=_next_version_label(session, facility_id),
        status="active",
        template_id=template_id,
        source=actor,
        columns_json=normalized_columns,
        cells_json=[],
        template_digest=digest,
        validation_json=validation,
        created_by=actor,
        created_at=now,
        activated_at=now,
    )
    session.add(version)
    order.template_version_id = version.id
    session.flush()

    resolved_config = _resolved_config_from_parts(
        facility_id=facility_id,
        facility_name=getattr(facility, "name", None),
        config=sanitized,
        template_id=template_id,
        columns=normalized_columns,
        version=version,
    )
    return {
        "updated": True,
        "validation": validation,
        "resolved_config": resolved_config,
        "template_version": serialize_template_version(version),
    }, None


def save_template_registration_for_facility(
    session: Any,
    *,
    facility_id: str,
    fax_template_id: str,
    fax_template_ids: list[str] | None = None,
    actor: str = "facility-fax-template-registration",
) -> tuple[dict[str, Any] | None, str | None]:
    normalized_facility_id = str(facility_id or "").strip()
    primary_template_id = str(fax_template_id or "").strip()
    if not normalized_facility_id:
        return None, "facility_id_required"
    if not primary_template_id:
        return None, "fax_template_id_required"
    registry = config_service.load_fax_template_registry()
    template_ids: list[str] = []
    for item in [primary_template_id, *(fax_template_ids or [])]:
        token = str(item or "").strip()
        if token and token not in template_ids:
            template_ids.append(token)
    missing_template_ids = [template_id for template_id in template_ids if template_id not in registry]
    if missing_template_ids:
        return {"error": "fax_template_not_found", "template_ids": missing_template_ids}, "fax_template_not_found"

    facility = session.get(Facility, normalized_facility_id)
    if facility is None:
        from src.services import facility_service  # local import avoids service startup cycles

        facility_service._ensure_facility_sync(session)  # noqa: SLF001
        session.flush()
        facility = session.get(Facility, normalized_facility_id)
    if facility is None:
        return None, "facility_not_found"

    current_config = facility.config.config_json if facility.config and isinstance(facility.config.config_json, dict) else {}
    next_config = deepcopy(current_config)
    next_config["fax_template_id"] = primary_template_id
    next_config["fax_template_ids"] = template_ids
    next_config["facility_template_source"] = "operator_override"
    validation = validate_facility_config(next_config)
    if validation["errors"]:
        return {"validation": validation}, "validation_error"
    sanitized = config_service.sanitize_facility_config_for_storage(
        normalized_facility_id,
        next_config,
        current_config=current_config,
        allow_authoritative_column_changes=False,
    )
    session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == normalized_facility_id))
    session.add(FacilityConfig(facility_id=normalized_facility_id, config_json=sanitized))

    facility_payload = {
        "facility_id": normalized_facility_id,
        "facility_name": getattr(facility, "name", None),
        **deepcopy(sanitized),
    }
    resolved_candidate = config_service._build_facility_config(  # noqa: SLF001
        facility_id=normalized_facility_id,
        facility=facility_payload,
        selected_template_id=primary_template_id,
    )
    fax_template = resolved_candidate.get("fax_template") if isinstance(resolved_candidate, dict) else {}
    columns = normalize_template_columns((fax_template or {}).get("columns"))
    if not columns:
        return {"validation": {"errors": ["facility_template_columns_missing"], "warnings": []}}, "validation_error"

    active_versions = (
        session.query(FacilityTemplateVersion)
        .filter(
            FacilityTemplateVersion.facility_id == normalized_facility_id,
            FacilityTemplateVersion.status == "active",
        )
        .all()
    )
    now = _now()
    for active in active_versions:
        active.status = "archived"
        active.archived_at = now

    digest = template_digest(template_id=primary_template_id, columns=columns)
    version = FacilityTemplateVersion(
        id=_new_template_version_id(),
        facility_id=normalized_facility_id,
        version=_next_version_label(session, normalized_facility_id),
        status="active",
        template_id=primary_template_id,
        source=actor,
        columns_json=columns,
        cells_json=[],
        template_digest=digest,
        validation_json=validation,
        created_by=actor,
        created_at=now,
        activated_at=now,
    )
    session.add(version)
    session.flush()
    resolved_config = _resolved_config_from_parts(
        facility_id=normalized_facility_id,
        facility_name=getattr(facility, "name", None),
        config=sanitized,
        template_id=primary_template_id,
        columns=columns,
        version=version,
    )
    return {
        "updated": True,
        "config": sanitized,
        "validation": validation,
        "resolved_config": resolved_config,
        "template_version": serialize_template_version(version),
    }, None
