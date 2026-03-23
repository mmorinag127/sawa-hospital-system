from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from src.db import Base, engine, session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import evidence_manifest_service, template_resolution_service


Base.metadata.create_all(bind=engine)


def _ensure_order_ocr_evidence_run_schema() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(order_ocr_evidence_runs)")).fetchall()
        if not rows:
            return
        columns = {str(row[1]) for row in rows if len(row) > 1}
        if "source" not in columns:
            conn.execute(text("ALTER TABLE order_ocr_evidence_runs ADD COLUMN source VARCHAR"))


_ensure_order_ocr_evidence_run_schema()


_EVIDENCE_META_KEYS = (
    "job_id",
    "status",
    "stage",
    "engine",
    "template_id",
    "facility_id",
    "facility_candidates",
    "date_strings",
    "input_reference",
    "output_reference",
    "metrics",
)

_EVIDENCE_ARTIFACT_KEYS = (
    "pages",
    "combined",
    "table_raw",
    "tables",
    "failed_cells",
    "column_mapping_resolution",
    "column_mapping_candidates",
    "quantity_resolution",
    "critical_quantity_candidates",
    "quantity_candidates",
    "quantity_subgrid_passes",
    "page_correction",
    "page_correction_artifacts",
    "template_resolution",
    "table_box",
    "grid_column_edges",
    "grid_row_edges",
    "roi_extraction",
    "cell_issues",
)

_HIGH_RISK_NUMERIC_ISSUE_CODES = {
    "merged_numeric_cell",
    "overextended_span",
    "invalid_numeric_spike",
    "all_quantity_blank",
    "unexpected_dense_fill",
    "missing_blank_anchor_rows",
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _has_meaningful_evidence(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("pages"), list) and payload.get("pages"):
        return True
    if isinstance(payload.get("tables"), list) and payload.get("tables"):
        return True
    if str(payload.get("table_raw") or "").strip():
        return True
    if isinstance(payload.get("quantity_subgrid_passes"), list) and payload.get("quantity_subgrid_passes"):
        return True
    if isinstance(payload.get("template_resolution"), dict):
        return True
    if isinstance(payload.get("page_correction"), dict):
        return True
    if isinstance(payload.get("page_correction_artifacts"), dict):
        return True
    return False


def classify_evidence_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "persistable": False,
            "error": "evidence_invalid_payload",
            "message": "OCR output payload is not a dictionary",
        }
    status = str(payload.get("status") or "").strip().lower()
    stage = str(payload.get("stage") or "").strip().lower()
    upstream_error = str(payload.get("error") or "").strip()
    if status in {"failed", "error"} or stage in {"failed", "error"}:
        detail = upstream_error or stage or status or "unknown"
        return {
            "persistable": False,
            "error": "ocr_pipeline_failed",
            "message": detail,
            "status": status or None,
            "stage": stage or None,
        }
    if not _has_meaningful_evidence(payload):
        return {
            "persistable": False,
            "error": "evidence_unusable",
            "message": "OCR output does not contain reusable evidence artifacts",
            "status": status or None,
            "stage": stage or None,
        }
    return {
        "persistable": True,
        "status": status or None,
        "stage": stage or None,
    }


def _extract_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    for key in _EVIDENCE_META_KEYS + _EVIDENCE_ARTIFACT_KEYS:
        if key in payload:
            extracted[key] = copy.deepcopy(payload.get(key))
    resolution = template_resolution_service.normalize_template_resolution_state(
        extracted.get("template_resolution") if isinstance(extracted.get("template_resolution"), dict) else None
    )
    if isinstance(resolution, dict):
        extracted["template_resolution"] = resolution
    effective_grid_metadata = template_resolution_service.resolve_effective_grid_metadata(
        template_resolution=resolution if isinstance(resolution, dict) else None,
        payload=extracted,
    )
    if isinstance(effective_grid_metadata, dict):
        if not extracted.get("table_box") and isinstance(effective_grid_metadata.get("table_box"), list):
            extracted["table_box"] = copy.deepcopy(effective_grid_metadata.get("table_box"))
        if not extracted.get("grid_column_edges") and isinstance(effective_grid_metadata.get("grid_column_edges"), list):
            extracted["grid_column_edges"] = copy.deepcopy(effective_grid_metadata.get("grid_column_edges"))
        if not extracted.get("grid_row_edges") and isinstance(effective_grid_metadata.get("grid_row_edges"), list):
            extracted["grid_row_edges"] = copy.deepcopy(effective_grid_metadata.get("grid_row_edges"))
    enriched = evidence_manifest_service.ensure_evidence_manifest(extracted)
    return dict(enriched) if isinstance(enriched, dict) else extracted


def payload_has_quantity_column_semantics(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    resolution = template_resolution_service.normalize_template_resolution_state(
        payload.get("template_resolution") if isinstance(payload.get("template_resolution"), dict) else None
    )
    effective_grid_metadata = template_resolution_service.resolve_effective_grid_metadata(
        template_resolution=resolution if isinstance(resolution, dict) else None,
        payload=payload,
    )
    if not isinstance(effective_grid_metadata, dict):
        return False
    table_box = effective_grid_metadata.get("table_box")
    column_edges = effective_grid_metadata.get("grid_column_edges")
    template_present = bool(
        isinstance(resolution, dict)
        and str(resolution.get("resolved_template_id") or resolution.get("template_id") or "").strip()
    )
    template_blocked = bool(
        isinstance(resolution, dict)
        and (
            resolution.get("blocked")
            or (resolution.get("blocked_reasons") or [])
        )
    )
    return bool(template_present and not template_blocked and isinstance(table_box, list) and isinstance(column_edges, list))


def payload_has_high_risk_numeric_issues(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    failed_cells = payload.get("failed_cells")
    if isinstance(failed_cells, list) and failed_cells:
        return True
    for issue in payload.get("cell_issues") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("issue_code") or "").strip()
        if code in _HIGH_RISK_NUMERIC_ISSUE_CODES:
            return True
    quantity_resolution = payload.get("quantity_resolution")
    if isinstance(quantity_resolution, dict):
        if quantity_resolution.get("requires_user_choice"):
            return True
        if quantity_resolution.get("blocked"):
            return True
        blocked_reasons = quantity_resolution.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and any(str(item or "").strip() for item in blocked_reasons):
            return True
    critical_candidates = payload.get("critical_quantity_candidates")
    if isinstance(critical_candidates, list) and critical_candidates:
        return True
    return False


def _build_capabilities(payload: dict[str, Any]) -> dict[str, bool]:
    manifest = payload.get("evidence_manifest")
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    overlay_pages = bool((artifacts or {}).get("overlay_pages"))
    corrected_pdf = bool((artifacts or {}).get("corrected_pdf"))
    table_raw = bool((artifacts or {}).get("table_raw"))
    tables = bool(payload.get("tables"))
    quantity_subgrid = bool((artifacts or {}).get("quantity_subgrid"))
    effective_grid_metadata = template_resolution_service.resolve_effective_grid_metadata(
        template_resolution=payload.get("template_resolution") if isinstance(payload, dict) else None,
        payload=payload,
    )
    grid_metadata = isinstance(effective_grid_metadata, dict)
    template_resolution = template_resolution_service.normalize_template_resolution_state(
        payload.get("template_resolution") if isinstance(payload.get("template_resolution"), dict) else None
    )
    template_blocked = bool(
        isinstance(template_resolution, dict)
        and (
            template_resolution.get("blocked")
            or (template_resolution.get("blocked_reasons") or [])
        )
    )
    template_present = isinstance(template_resolution, dict) and bool(
        str(template_resolution.get("resolved_template_id") or template_resolution.get("template_id") or "").strip()
    )
    quantity_column_semantics_ready = payload_has_quantity_column_semantics(payload)

    step2_view_ready = overlay_pages or corrected_pdf or bool(payload.get("input_reference"))
    step2_edit_ready = table_raw or tables or quantity_subgrid
    semantic_shell_only = bool(
        step2_view_ready and step2_edit_ready and (not template_present or not quantity_column_semantics_ready)
    )
    numeric_trust_low = bool(
        step2_edit_ready
        and (
            not quantity_subgrid
            or semantic_shell_only
            or payload_has_high_risk_numeric_issues(payload)
        )
    )
    apply_ready = step2_edit_ready and not template_blocked and template_present and quantity_column_semantics_ready
    confirm_ready = apply_ready and bool(quantity_subgrid or table_raw)
    recovery_required = not step2_view_ready or not step2_edit_ready
    return {
        "step2_view_ready": bool(step2_view_ready),
        "step2_edit_ready": bool(step2_edit_ready),
        "semantic_shell_only": bool(semantic_shell_only),
        "numeric_trust_low": bool(numeric_trust_low),
        "quantity_column_semantics_ready": bool(quantity_column_semantics_ready),
        "grid_metadata_complete": bool(grid_metadata),
        "rerunnable": bool(step2_view_ready or step2_edit_ready or payload.get("input_reference")),
        "switch_candidate_available": False,
        "apply_ready": bool(apply_ready),
        "confirm_ready": bool(confirm_ready),
        "recovery_required": bool(recovery_required),
    }


def _build_degraded_reasons(payload: dict[str, Any], capabilities: dict[str, bool]) -> list[str]:
    reasons: list[str] = []
    missing = evidence_manifest_service.evidence_missing_artifacts(payload)
    if missing:
        reasons.extend([f"missing:{item}" for item in missing])
    resolution = payload.get("template_resolution")
    if isinstance(resolution, dict):
        reasons.extend(
            [
                f"template:{token}"
                for token in (
                    str(item or "").strip() for item in (resolution.get("blocked_reasons") or [])
                )
                if token
            ]
        )
    if capabilities.get("recovery_required"):
        reasons.append("capability:recovery_required")
    # keep stable order while removing duplicates
    deduped: list[str] = []
    for item in reasons:
        if item not in deduped:
            deduped.append(item)
    return deduped


def build_evidence_run_record(
    order_id: str,
    payload: dict[str, Any],
    *,
    schema_version: str = "v1_legacy",
    producer_version: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    payload_state = classify_evidence_payload(payload)
    if not payload_state.get("persistable") or not isinstance(payload, dict):
        return None
    extracted = _extract_evidence_payload(payload)
    manifest = extracted.get("evidence_manifest")
    if not isinstance(manifest, dict):
        manifest = evidence_manifest_service.build_evidence_manifest(extracted)
        extracted["evidence_manifest"] = manifest
    capabilities = _build_capabilities(extracted)
    capabilities["legacy_editable"] = bool(str(schema_version or "").startswith("v1_legacy"))
    degraded_reasons = _build_degraded_reasons(extracted, capabilities)
    artifact_digest = _digest(extracted)
    resolved_status = str(status or extracted.get("status") or "ready").strip() or "ready"
    return {
        "order_id": normalized_order_id,
        "schema_version": schema_version,
        "producer_version": producer_version or "legacy-cache-mirror/v1",
        "source": str(source or "unknown").strip() or "unknown",
        "status": resolved_status,
        "payload_json": extracted,
        "artifact_manifest_json": manifest,
        "artifact_digest": artifact_digest,
        "capabilities_json": capabilities,
        "degraded_reasons_json": degraded_reasons,
    }


def persist_evidence_run(
    order_id: str,
    payload: dict[str, Any],
    *,
    schema_version: str = "v1_legacy",
    producer_version: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    record = build_evidence_run_record(
        order_id,
        payload,
        schema_version=schema_version,
        producer_version=producer_version,
        status=status,
        source=source,
    )
    if not isinstance(record, dict):
        return None
    with session_scope() as session:
        latest = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == record["order_id"])
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .first()
        )
        if latest and str(latest.artifact_digest or "") == str(record["artifact_digest"]):
            existing = _serialize_evidence_run(latest, include_payload=True)
            existing["created"] = False
            return existing
        run = OrderOcrEvidenceRun(
            id=f"OEV{uuid4().hex[:12]}",
            order_id=record["order_id"],
            schema_version=record["schema_version"],
            producer_version=record["producer_version"],
            source=record.get("source"),
            status=record["status"],
            payload_json=record["payload_json"],
            artifact_manifest_json=record["artifact_manifest_json"],
            artifact_digest=record["artifact_digest"],
            capabilities_json=record["capabilities_json"],
            degraded_reasons_json=record["degraded_reasons_json"],
        )
        session.add(run)
        session.flush()
        created = _serialize_evidence_run(run, include_payload=True)
        created["created"] = True
        return created


def backfill_evidence_run_from_cached_payload(
    order_id: str,
    payload: dict[str, Any],
    *,
    schema_version: str = "v1_legacy",
    producer_version: str | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    return persist_evidence_run(
        order_id,
        payload,
        schema_version=schema_version,
        producer_version=producer_version or "legacy-cache-backfill/v1",
        status=str(payload.get("status") or "ready").strip() or "ready",
        source=source or "legacy-cache-backfill",
    )


def get_latest_evidence_run(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        latest = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == normalized_order_id)
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .first()
        )
        if not latest:
            return None
        return _serialize_evidence_run(latest, include_payload=True)


def get_evidence_run(evidence_run_id: str) -> dict[str, Any] | None:
    normalized_evidence_run_id = str(evidence_run_id or "").strip()
    if not normalized_evidence_run_id:
        return None
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, normalized_evidence_run_id)
        if not row:
            return None
        return _serialize_evidence_run(row, include_payload=True)


def list_evidence_runs(order_id: str) -> list[dict[str, Any]]:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return []
    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == normalized_order_id)
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .all()
        )
        return [_serialize_evidence_run(row, include_payload=False) for row in rows]


def _serialize_evidence_run(
    row: OrderOcrEvidenceRun,
    *,
    include_payload: bool,
) -> dict[str, Any]:
    payload_json = _extract_evidence_payload(row.payload_json if isinstance(row.payload_json, dict) else {})
    manifest = payload_json.get("evidence_manifest")
    if not isinstance(manifest, dict):
        manifest = evidence_manifest_service.build_evidence_manifest(payload_json)
    capabilities = _build_capabilities(payload_json)
    capabilities["legacy_editable"] = bool(str(row.schema_version or "").startswith("v1_legacy"))
    degraded_reasons = _build_degraded_reasons(payload_json, capabilities)
    serialized = {
        "id": row.id,
        "order_id": row.order_id,
        "schema_version": row.schema_version,
        "producer_version": row.producer_version,
        "source": row.source,
        "status": row.status,
        "artifact_digest": row.artifact_digest,
        "artifact_manifest_json": manifest,
        "capabilities_json": capabilities,
        "degraded_reasons_json": degraded_reasons,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }
    if include_payload:
        serialized["payload_json"] = payload_json
    return serialized
