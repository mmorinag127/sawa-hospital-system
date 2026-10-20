from __future__ import annotations

import copy
import hashlib
import json
from contextlib import nullcontext
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.db import session_scope
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import (
    evidence_manifest_service,
    hakodate_ocr_evidence_service,
    order_current_state_service,
    template_resolution_service,
)
from src.services.workflow_attempt_guard_service import lock_actual_write_ownership, current_nonworkflow_execution
from src.services import recognition_acceptance_service as recognition_acceptance

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
    "input_generation",
    "input_sha256",
    "output_reference",
    "metrics",
    "_ocr_execution",
    "_reparse_debug",
    "diagnostic_pipeline_output",
    "_reparse_recognizer_result",
    "_first_pass_recognizer",
    "raw_recognizer_payload",
    "auxiliary_pipeline_result",
    "provider_debug",
    "error",
)

_EVIDENCE_ARTIFACT_KEYS = (
    "pages",
    "combined",
    "table_raw",
    "table_rows",
    "rows",
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
    "hakodate_preprocessing",
    "hakodate_repair_source_receipt",
    "hakodate_target_cell_map",
    "hakodate_ocr_evidence",
    "hakodate_ocr_evidence_records",
    "hakodate_overlay",
    "hakodate_canonical_pipeline",
    "hakodate_recognition_review",
)

_HIGH_RISK_NUMERIC_ISSUE_CODES = {
    "merged_numeric_cell",
    "overextended_span",
    "invalid_numeric_spike",
    "all_quantity_blank",
    "unexpected_dense_fill",
    "missing_blank_anchor_rows",
}

_HAKODATE_ALLOWED_EVIDENCE_ENGINES = {
    "hakodate_cell_crop_ocr",
    "yomitoku_contact_sheet_batch",
}
_HAKODATE_ALLOWED_SOURCE_SCOPES = {
    "hakodate_cell_crop_batch",
    "hakodate_cell_crop_ocr",
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def hakodate_evidence_record_allowed(record: dict[str, Any]) -> bool:
    source_scope = str(record.get("source_scope") or "").strip()
    engine = str(record.get("engine") or "").strip()
    if source_scope == "operator_cell_review":
        metadata = record.get("engine_metadata")
        if not isinstance(metadata, dict):
            return False
        original = metadata.get("operator_output")
        return bool(
            engine == "operator" and metadata.get("source") == "operator"
            and metadata.get("edited_by") and isinstance(original, dict)
            and original.get("recognition_source") == "operator"
            and original.get("sheet_cell") == metadata.get("sheet_cell")
            and isinstance(original.get("operator_value"), str)
            and original["operator_value"] == record.get("raw_text")
        )
    if source_scope in _HAKODATE_ALLOWED_SOURCE_SCOPES:
        return True
    if engine in _HAKODATE_ALLOWED_EVIDENCE_ENGINES:
        return True
    return not source_scope and not engine and not str(record.get("evidence_id") or "").strip()


def _extract_hakodate_target_cells(payload: dict[str, Any], *, _analysis=None) -> list[dict[str, Any]]:
    if _analysis is not None:
        return _analysis.target_cells(payload)
    from src.services.order_service import _extract_hakodate_target_cells_from_payload
    return _extract_hakodate_target_cells_from_payload(payload)


def _extract_hakodate_evidence_records(payload: dict[str, Any], *, _analysis=None) -> list[dict[str, Any]]:
    if _analysis is not None:
        return _analysis.evidence_records(payload, order_id="")
    from src.services.order_service import _extract_hakodate_ocr_evidence_records_from_payload
    return _extract_hakodate_ocr_evidence_records_from_payload(payload, order_id="")


def _summarize_hakodate_payload(payload: dict[str, Any], *, expected_source: dict | None = None,
                                _analysis=None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    try:
        accepted = (recognition_acceptance.accepted_values(payload) if _analysis is None
                    else _analysis.accepted_values(payload))
        target_cells = _extract_hakodate_target_cells(payload, _analysis=_analysis)
        evidence_records = _extract_hakodate_evidence_records(payload, _analysis=_analysis)
    except ValueError as exc:
        return {"target_cell_count": 0, "evidence_record_count": 0, "assigned_target_count": 0, "assignment_blockers": [], "blockers": [str(exc)]}
    canonical_pipeline = (
        payload.get("hakodate_canonical_pipeline")
        if isinstance(payload.get("hakodate_canonical_pipeline"), dict)
        else None
    )
    has_hakodate_signal = bool(
        target_cells
        or evidence_records
        or isinstance(canonical_pipeline, dict)
        or isinstance(payload.get("hakodate_overlay"), dict)
    )
    if not has_hakodate_signal:
        return None

    blockers: list[str] = []
    if canonical_pipeline is None:
        blockers.append("hakodate_canonical_outcome_required")
    if isinstance(canonical_pipeline, dict):
        from src.services import order_service
        source = payload.get("hakodate_repair_source_receipt")
        if canonical_pipeline.get("version") != order_service.HAKODATE_CANONICAL_PIPELINE_VERSION:
            blockers.append("hakodate_canonical_version_required")
        elif not isinstance(source, dict):
            blockers.append("hakodate_artifact_source_mismatch")
        else:
            try:
                if expected_source is not None:
                    order_service._validate_hakodate_owned_artifacts(payload, expected_source, _analysis=_analysis)
                else:
                    order_service._validate_hakodate_original_execution(payload, _analysis=_analysis)
            except (ValueError, KeyError, TypeError) as exc:
                blockers.append(str(exc))
    if not target_cells:
        blockers.append("hakodate_target_cell_map_missing")
    if not evidence_records:
        blockers.append("hakodate_ocr_evidence_missing")

    assigned_target_count = 0
    resolved_blank_targets = False
    assignment_blockers: list[str] = []
    if target_cells and evidence_records:
        assign = hakodate_ocr_evidence_service.assign_evidence_to_target_cells if _analysis is None else _analysis.assign
        assignment_result = assign(
            evidence_records=evidence_records,
            target_cells=target_cells,
            accepted_values=accepted,
        )
        summary = assignment_result.get("summary") if isinstance(assignment_result, dict) else {}
        assigned_target_count = int((summary or {}).get("assigned_target_count") or 0)
        resolved_blank_targets = hakodate_ocr_evidence_service.has_resolved_blank_targets(assignment_result)
        assignment_blockers = [
            str(item).strip()
            for item in (assignment_result.get("blockers") or [])
            if str(item).strip()
        ]
        blockers.extend(assignment_blockers)
        if assigned_target_count <= 0 and not resolved_blank_targets:
            blockers.append("hakodate_assignment_missing")

    if not accepted and isinstance(canonical_pipeline, dict) and str(canonical_pipeline.get("status") or "").strip().lower() == "blocked":
        blockers.extend(
            str(item).strip()
            for item in (canonical_pipeline.get("blockers") or [])
            if str(item).strip()
        )

    deduped_blockers: list[str] = []
    for blocker in blockers:
        if blocker and blocker not in deduped_blockers:
            deduped_blockers.append(blocker)
    return {
        "target_cell_count": len(target_cells),
        "evidence_record_count": len(evidence_records),
        "assigned_target_count": assigned_target_count,
        "resolved_blank_targets": resolved_blank_targets,
        "assignment_blockers": assignment_blockers,
        "blockers": deduped_blockers,
    }


def _has_meaningful_evidence(payload: dict[str, Any]) -> bool:
    recognized = payload.get("_first_pass_recognizer")
    if isinstance(recognized, dict) and recognized.get("recognized_rows") and payload.get("table_rows") == recognized["recognized_rows"]:
        return True
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
    hakodate_state = _summarize_hakodate_payload(payload)
    if isinstance(hakodate_state, dict) and int(hakodate_state.get("evidence_record_count") or 0) > 0:
        return True
    return False


def _has_failed_evidence_status(*values: object) -> bool:
    return any(str(value or "").strip().lower() in {"failed", "error"} for value in values)


def classify_evidence_payload(payload: object, *, expected_hakodate_source: dict | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "persistable": False,
            "error": "evidence_invalid_payload",
            "message": "OCR output payload is not a dictionary",
        }
    status = str(payload.get("status") or "").strip().lower()
    stage = str(payload.get("stage") or "").strip().lower()
    upstream_error = str(payload.get("error") or "").strip()
    if _has_failed_evidence_status(status, stage):
        detail = upstream_error or stage or status or "unknown"
        return {
            "persistable": False,
            "error": "ocr_pipeline_failed",
            "message": detail,
            "status": status or None,
            "stage": stage or None,
        }
    hakodate_state = _summarize_hakodate_payload(payload, expected_source=expected_hakodate_source)
    if isinstance(hakodate_state, dict):
        blockers = [str(item).strip() for item in (hakodate_state.get("blockers") or []) if str(item).strip()]
        if blockers:
            return {
                "persistable": False,
                "error": "evidence_unusable",
                "message": f"Hakodate canonical evidence blocked: {', '.join(blockers)}",
                "status": status or None,
                "stage": stage or None,
            }
        if int(hakodate_state.get("assigned_target_count") or 0) > 0 or hakodate_state.get("resolved_blank_targets"):
            return {
                "persistable": True,
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


def _extract_evidence_payload(payload: dict[str, Any], *, _analysis=None) -> dict[str, Any]:
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
    enriched = evidence_manifest_service.ensure_evidence_manifest(extracted, _analysis=_analysis)
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


def _build_capabilities(payload: dict[str, Any], *, _analysis=None) -> dict[str, bool]:
    manifest = payload.get("evidence_manifest")
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    overlay_pages = bool((artifacts or {}).get("overlay_pages"))
    corrected_pdf = bool((artifacts or {}).get("corrected_pdf"))
    table_raw = bool((artifacts or {}).get("table_raw"))
    tables = bool(payload.get("tables"))
    quantity_subgrid = bool((artifacts or {}).get("quantity_subgrid"))
    hakodate_state = _summarize_hakodate_payload(payload, _analysis=_analysis)
    hakodate_ready = bool(
        isinstance(hakodate_state, dict)
        and not list(hakodate_state.get("blockers") or [])
        and (int(hakodate_state.get("assigned_target_count") or 0) > 0 or hakodate_state.get("resolved_blank_targets"))
    )
    hakodate_overlay = bool(
        str(
            ((payload.get("hakodate_overlay") or {}).get("uri"))
            or ((payload.get("hakodate_overlay") or {}).get("url"))
            or ""
        ).strip()
    )
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
    quantity_column_semantics_ready = payload_has_quantity_column_semantics(payload) or hakodate_ready

    step2_view_ready = overlay_pages or corrected_pdf or hakodate_overlay or bool(payload.get("input_reference"))
    step2_edit_ready = table_raw or tables or quantity_subgrid or hakodate_ready
    semantic_shell_only = (
        False
        if hakodate_ready
        else bool(step2_view_ready and step2_edit_ready and (not template_present or not quantity_column_semantics_ready))
    )
    numeric_trust_low = (
        False
        if hakodate_ready
        else bool(
            step2_edit_ready
            and (
                not quantity_subgrid
                or semantic_shell_only
                or payload_has_high_risk_numeric_issues(payload)
            )
        )
    )
    apply_ready = (
        step2_edit_ready
        and not template_blocked
        and (template_present or hakodate_ready)
        and (quantity_column_semantics_ready or hakodate_ready)
    )
    confirm_ready = apply_ready and bool(quantity_subgrid or table_raw or hakodate_ready)
    recovery_required = not step2_view_ready or not step2_edit_ready
    return {
        "step2_view_ready": bool(step2_view_ready),
        "step2_edit_ready": bool(step2_edit_ready),
        "semantic_shell_only": bool(semantic_shell_only),
        "numeric_trust_low": bool(numeric_trust_low),
        "quantity_column_semantics_ready": bool(quantity_column_semantics_ready),
        "grid_metadata_complete": bool(grid_metadata or hakodate_ready),
        "rerunnable": bool(step2_view_ready or step2_edit_ready or payload.get("input_reference")),
        "switch_candidate_available": False,
        "apply_ready": bool(apply_ready),
        "confirm_ready": bool(confirm_ready),
        "recovery_required": bool(recovery_required),
    }


def _build_degraded_reasons(payload: dict[str, Any], capabilities: dict[str, bool], *, _analysis=None) -> list[str]:
    reasons: list[str] = []
    missing = evidence_manifest_service.evidence_missing_artifacts(payload, _analysis=_analysis)
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
    template_version_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    payload_state = classify_evidence_payload(payload)
    if not isinstance(payload, dict):
        return None
    if not payload_state.get("persistable") and status != "diagnostic_only":
        return None
    extracted = _extract_evidence_payload(payload)
    manifest = extracted.get("evidence_manifest")
    if not isinstance(manifest, dict):
        manifest = evidence_manifest_service.build_evidence_manifest(extracted)
        extracted["evidence_manifest"] = manifest
    capabilities = _build_capabilities(extracted)
    if status == "diagnostic_only":
        extracted["diagnostic_pipeline_output"] = copy.deepcopy(payload)
        capabilities = {key: False for key in capabilities}
    capabilities["legacy_editable"] = bool(str(schema_version or "").startswith("v1_legacy"))
    degraded_reasons = _build_degraded_reasons(extracted, capabilities)
    artifact_digest = _digest(extracted)
    normalized_source = str(source or "unknown").strip() or "unknown"
    resolved_status = str(status or extracted.get("status") or "ready").strip() or "ready"
    if normalized_source == "legacy-cache-backfill" or (producer_version or "legacy-cache-mirror/v1") == "legacy-cache-mirror/v1":
        resolved_status = "repair_blocked"
    return {
        "order_id": normalized_order_id,
        "template_version_id": str(template_version_id or "").strip() or None,
        "schema_version": schema_version,
        "producer_version": producer_version or "legacy-cache-mirror/v1",
        "source": normalized_source,
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
    template_version_id: str | None = None,
    workflow_attempt_id: str | None = None,
    workflow_job_id: str | None = None,
    expected_workflow_revision: int | None = None,
    result_disposition: str | None = None,
    write_session: Any = None,
) -> dict[str, Any] | None:
    evidence_id = f"OEV{uuid4().hex[:12]}"
    payload = copy.deepcopy(payload)
    if isinstance(payload, dict) and payload.get("_ocr_execution") is not None and not workflow_job_id:
        return None
    if isinstance(payload, dict) and workflow_job_id:
        from src.services.workflow_attempt_guard_service import processing_start_read_input, processing_start_pdf_receipt
        execution_input = current_nonworkflow_execution()
        payload["_ocr_execution"] = {
            "job_id": workflow_job_id,
            "workflow_attempt_id": workflow_attempt_id,
            "workflow_revision": expected_workflow_revision,
            "evidence_run_id": evidence_id,
            "disposition": result_disposition or "pipeline_result",
            "nonworkflow_execution_id": execution_input.get("execution_id") if execution_input is not None else None,
            "processing_start_input": (
                processing_start_read_input()
            ),
            "selected_pdf_receipt": processing_start_pdf_receipt(),
        }
    record = build_evidence_run_record(
        order_id,
        payload,
        schema_version=schema_version,
        producer_version=producer_version,
        status=status,
        source=source,
        template_version_id=template_version_id,
    )
    if not isinstance(record, dict):
        return None
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        if not lock_actual_write_ownership(
            session,
            job_id=workflow_job_id,
            workflow_attempt_id=workflow_attempt_id,
            order_id=record["order_id"],
            expected_workflow_revision=expected_workflow_revision,
        ):
            return None
        if record["status"] == "ready" and isinstance(payload, dict) and _extract_hakodate_target_cells(payload):
            from src.services import order_service
            from src.services.workflow_attempt_guard_service import processing_start_configured_input, processing_start_pdf_receipt
            expected_source = order_service._hakodate_owned_source(processing_start_configured_input(), processing_start_pdf_receipt())
            expected_source.update(job_id=workflow_job_id, workflow_attempt_id=workflow_attempt_id, workflow_revision=expected_workflow_revision)
            order_service._validate_hakodate_owned_artifacts(payload, expected_source)
            manifest = copy.deepcopy(record.get("artifact_manifest_json") or {})
            manifest["original_hakodate_source"] = copy.deepcopy(expected_source)
            record["artifact_manifest_json"] = manifest
        if workflow_job_id:
            from src.models.ocr_job import OcrJob
            from src.services.workflow_attempt_guard_service import processing_start_read_input
            execution_input = current_nonworkflow_execution()
            own_job = session.get(OcrJob, workflow_job_id)
            selected_pdf = (
                execution_input.get("selected_pdf_receipt") if execution_input is not None
                else (own_job.metrics or {}).get("selected_pdf_receipt") if own_job is not None else None
            )
            if not isinstance(selected_pdf, dict):
                return None
            payload["_ocr_execution"]["selected_pdf_receipt"] = copy.deepcopy(selected_pdf)
            record["payload_json"]["_ocr_execution"] = copy.deepcopy(payload["_ocr_execution"])
        if workflow_job_id and isinstance(payload, dict) and "_first_pass_recognizer" in payload:
            from src.services.ocr_job_service import validate_own_first_pass_recognizer

            validate_own_first_pass_recognizer(payload, write_session=session)
        elif workflow_job_id and isinstance(payload, dict) and payload.get("engine") == "yomitoku":
            from src.services.ocr_job_service import validate_own_pipeline_result

            validate_own_pipeline_result(payload, payload.get("output_reference"), write_session=session)
        normalized_template_version_id = str(record.get("template_version_id") or "").strip()
        order = session.get(Order, record["order_id"])
        if workflow_attempt_id is not None:
            from src.models.order_workflow_state import OrderWorkflowState
            from src.models.ocr_job import OcrJob

            workflow = session.get(OrderWorkflowState, record["order_id"])
            job = session.get(OcrJob, workflow_job_id)
            if (
                workflow is None or job is None or not normalized_template_version_id
                or workflow.template_version_id != normalized_template_version_id
                or job.template_version_id != normalized_template_version_id
                or order is None or order.template_version_id != normalized_template_version_id
            ):
                return None
        from src.services import canonical_facility_lineage as lineage
        from src.services.workflow_attempt_guard_service import processing_start_read_input, snapshots_equal

        original = processing_start_read_input()
        original_order = original.get("order") or {}
        captured_projection = original.get("original_template_projection")
        captured_lineage = original.get("original_template_lineage")
        captured_proof = original.get("original_template_source_proof")
        if (not normalized_template_version_id or order is None
                or original_order.get("id") != order.id
                or original_order.get("template_version_id") != normalized_template_version_id
                or order.template_version_id != normalized_template_version_id
                or original_order.get("facility_code") != order.facility_code
                or not isinstance(captured_projection, dict)
                or not isinstance(captured_lineage, dict)
                or not isinstance(captured_proof, dict)):
            raise ValueError("original_template_capture_missing_or_mismatched")
        template_version = lineage.read_projection(session, normalized_template_version_id, lock=True)
        if template_version is None:
            raise ValueError("original_template_projection_missing")
        lineage.verify_projection(template_version, captured_lineage)
        lineage.validate_source_proof(captured_proof, captured_lineage["authored_version"])
        physical_projection = {column.key: getattr(template_version, column.key)
                               for column in FacilityTemplateVersion.__table__.columns}
        if (captured_lineage["facility_id"] != order.facility_code
                or captured_projection.get("id") != normalized_template_version_id
                or not snapshots_equal((original.get("configured_input") or {}).get("facility"), captured_lineage["authored_version"]["facility"])
                or not snapshots_equal(physical_projection, captured_projection)
                or not snapshots_equal(template_version.validation_json["creation_source_proof"], captured_proof)):
            raise ValueError("original_template_projection_changed")
        run = OrderOcrEvidenceRun(
            id=evidence_id,
            order_id=record["order_id"],
            template_version_id=normalized_template_version_id,
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
        if write_session is not None:
            from src.models.order_current_state import OrderCurrentState

            current_state = session.get(OrderCurrentState, record["order_id"])
            if current_state is not None:
                session.delete(current_state)
    if write_session is None:
        order_current_state_service.delete_current_state(
            record["order_id"], workflow_job_id=workflow_job_id,
            workflow_attempt_id=workflow_attempt_id,
            expected_workflow_revision=expected_workflow_revision,
        )
    return created


def get_latest_evidence_run(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        latest = (
            session.query(OrderOcrEvidenceRun)
            .filter(
                OrderOcrEvidenceRun.order_id == normalized_order_id,
                OrderOcrEvidenceRun.status != "repair_blocked",
            )
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


def get_selected_evidence_run(order_id: str) -> dict[str, Any] | None:
    from src.models.order_workflow_state import OrderWorkflowState

    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        row = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id) if workflow and workflow.evidence_run_id else None
        if (
            row is None or row.order_id != order_id
            or row.template_version_id != workflow.template_version_id
            or not evidence_run_is_selectable(row)
        ):
            return None
        return _serialize_evidence_run(row, include_payload=True)


class SelectedEvidenceRejected(ValueError):
    """A selected persisted result cannot be used by a dependent operation."""


def evidence_run_selectability_error(row: OrderOcrEvidenceRun, *, _analysis=None) -> str | None:
    try:
        payload, accepted = recognition_acceptance._payload_for_evidence_with_accepted_values(row, copy_payload=False)
        if accepted is None:
            accepted = (recognition_acceptance.accepted_values(payload) if _analysis is None
                        else _analysis.accepted_values(payload))
    except (ValueError, KeyError, TypeError) as exc:
        return str(exc) or "evidence_unusable"
    if any(key in payload for key in ("hakodate_preprocessing", "hakodate_ocr_evidence", "hakodate_target_cell_map", "hakodate_ocr_evidence_records", "hakodate_overlay")) and not isinstance(payload.get("hakodate_canonical_pipeline"), dict):
        return "evidence_unusable"
    if isinstance(payload.get("hakodate_canonical_pipeline"), dict):
        from src.services import order_service
        from src.services.workflow_attempt_guard_service import snapshots_equal
        source = payload.get("hakodate_repair_source_receipt")
        if not isinstance(source, dict):
            return "evidence_unusable"
        try:
            validated_source = order_service._validate_hakodate_original_execution(payload, _analysis=_analysis, _accepted_values=accepted)
            original_source = (row.artifact_manifest_json or {}).get("original_hakodate_source")
            if accepted:
                original_source = payload[recognition_acceptance.KEY].get("original_hakodate_source")
                if original_source != validated_source:
                    return "recognition_acceptance_changed"
            if not isinstance(original_source, dict):
                return "evidence_unusable"
            if not snapshots_equal(source, original_source):
                raise ValueError("hakodate_artifact_source_mismatch")
            assign = hakodate_ocr_evidence_service.assign_evidence_to_target_cells if _analysis is None else _analysis.assign
            assignment = assign(
                evidence_records=_extract_hakodate_evidence_records(payload, _analysis=_analysis),
                target_cells=_extract_hakodate_target_cells(payload, _analysis=_analysis),
                accepted_values=accepted,
            )
            if assignment.get("blockers"):
                return "evidence_unusable"
        except (ValueError, KeyError, TypeError) as exc:
            return str(exc) if isinstance(exc, ValueError) and str(exc) else "evidence_unusable"
    if not accepted and _has_failed_evidence_status(row.status, payload.get("status"), payload.get("stage")):
        return "evidence_unusable"
    if (row.payload_json or {}).get("canonical_adoption_allowed") is False:
        return "evidence_unusable"
    if (row.status == "diagnostic_only" and not accepted) or row.status == "repair_blocked" or row.source in {"legacy-cache-backfill", "legacy-cache-mirror"}:
        return "evidence_unusable"
    if row.producer_version == "legacy-cache-mirror/v1":
        return "evidence_unusable"
    execution = (row.payload_json or {}).get("_ocr_execution")
    if execution is not None:
        if not isinstance(execution, dict) or execution.get("evidence_run_id") != row.id or not execution.get("job_id"):
            return "evidence_unusable"
        if (execution.get("workflow_attempt_id") is None) != (execution.get("workflow_revision") is None):
            return "evidence_unusable"
        if execution.get("disposition") not in ("pipeline_result", "accepted") and not (accepted and execution.get("disposition") == "rejected"):
            return "evidence_unusable"
    return None


def evidence_run_is_selectable(row: OrderOcrEvidenceRun, *, _analysis=None) -> bool:
    return evidence_run_selectability_error(row, _analysis=_analysis) is None


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
    _analysis=None,
) -> dict[str, Any]:
    from src.services.canonical_ocr_artifact_service import _EvidenceAnalysis
    if _analysis is None:
        _analysis = _EvidenceAnalysis()
    payload_json = _extract_evidence_payload(row.payload_json if isinstance(row.payload_json, dict) else {}, _analysis=_analysis)
    acceptance = (row.artifact_manifest_json or {}).get(recognition_acceptance.KEY)
    if acceptance is not None:
        payload_json = copy.deepcopy(row.payload_json)
    manifest = copy.deepcopy(row.artifact_manifest_json) if isinstance(row.artifact_manifest_json, dict) else None
    capabilities = _build_capabilities(recognition_acceptance.payload_for_evidence(row) if acceptance is not None else payload_json, _analysis=_analysis)
    capabilities["legacy_editable"] = bool(str(row.schema_version or "").startswith("v1_legacy"))
    if row.status == "diagnostic_only" and acceptance is None:
        capabilities = {key: False for key in capabilities}
    degraded_reasons = _build_degraded_reasons(payload_json, capabilities, _analysis=_analysis)
    serialized = {
        "id": row.id,
        "order_id": row.order_id,
        "template_version_id": row.template_version_id,
        "schema_version": row.schema_version,
        "producer_version": row.producer_version,
        "source": row.source,
        "status": row.status,
        "execution_lineage": copy.deepcopy(payload_json.get("_ocr_execution")),
        "selectable": evidence_run_is_selectable(row, _analysis=_analysis),
        "artifact_digest": row.artifact_digest,
        "artifact_manifest_json": manifest,
        "capabilities_json": capabilities,
        "degraded_reasons_json": degraded_reasons,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }
    if include_payload:
        serialized["payload_json"] = payload_json
    return serialized
