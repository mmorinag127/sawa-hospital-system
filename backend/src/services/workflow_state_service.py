from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime
from typing import Any
import re

from sqlalchemy import select, update

from src.db import session_scope
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_workflow_state import OrderWorkflowState
from src.services.ocr_job_service import (
    describe_job_state,
    get_latest_order_job,
    get_job_request_mode,
    is_order_reparse_job,
)
from src.services import (
    apply_gate_service,
    candidate_resolution_service,
    config_service,
    critical_decision_service,
    draft_sheet_service,
    ocr_evidence_service,
    position_column_mapping_service,
    week_candidate_service,
)

_WORKFLOW_REFRESH_STACK: ContextVar[tuple[str, ...]] = ContextVar(
    "workflow_refresh_stack",
    default=(),
)


def _serialize(row: OrderWorkflowState) -> dict[str, Any]:
    secondary_actions = row.secondary_actions_json
    return {
        "order_id": row.order_id,
        "evidence_run_id": row.evidence_run_id,
        "draft_id": row.draft_id,
        "confirmed_snapshot_id": row.confirmed_snapshot_id,
        "state": row.state,
        "headline": row.headline,
        "primary_action": row.primary_action,
        "secondary_actions_json": (
            dict(secondary_actions)
            if isinstance(secondary_actions, dict)
            else list(secondary_actions or [])
        ),
        "blockers_json": list(row.blockers_json or []),
        "warnings_json": list(row.warnings_json or []),
        "confidence_band": row.confidence_band,
        "last_transition_at": row.last_transition_at.isoformat() if isinstance(row.last_transition_at, datetime) else None,
    }


def _attach_top_level_apply_flags(
    payload: dict[str, Any],
    apply_gate: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    gate = apply_gate if isinstance(apply_gate, dict) else {}
    normalized = dict(payload)
    normalized["can_apply"] = bool(gate.get("can_apply", False))
    normalized["can_confirm"] = bool(gate.get("can_confirm", False))
    return normalized


def get_workflow_state(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        row = session.get(OrderWorkflowState, normalized_order_id)
        if not row:
            return None
        return _serialize(row)


def list_workflow_states(order_ids: list[str]) -> dict[str, dict[str, Any]]:
    normalized_ids = [str(item or "").strip() for item in order_ids if str(item or "").strip()]
    if not normalized_ids:
        return {}
    with session_scope() as session:
        rows = (
            session.execute(
                select(OrderWorkflowState).where(OrderWorkflowState.order_id.in_(normalized_ids))
            )
            .scalars()
            .all()
        )
        return {
            str(row.order_id).strip(): _serialize(row)
            for row in rows
            if str(row.order_id).strip()
        }


def _load_order_payload(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    try:
        from src.services import order_service as _order_service
    except Exception:
        _order_service = None
    if _order_service is not None:
        canonical_payload = _order_service.get_order_by_id(normalized_order_id)
        if isinstance(canonical_payload, dict):
            return canonical_payload
    with session_scope() as session:
        order = session.get(Order, normalized_order_id)
        if not order:
            return None
        week_selection = week_candidate_service.resolve_current_week_selection(
            order.week_code,
            received_at=order.received_at,
            facility_id=str(order.facility_code or "").strip() or None,
        )
        week_month_id = str(week_selection.get("month_id") or order.week_code or "").strip() or None
        week_value = (
            str(week_selection.get("resolved_week_value") or "").strip()
            or str(week_selection.get("stored_week_value") or "").strip()
            or week_month_id
        )
        return {
            "id": order.id,
            "facility": order.facility_code,
            "week": week_month_id,
            "week_value": week_value,
            "persisted_week_value": week_value,
            "status": order.status,
            "received_at": order.received_at,
            "lines_updated_at": order.lines_updated_at,
        }


def _latest_confirmed_snapshot_id(order_id: str) -> str | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        row = (
            session.query(OrderConfirmedSnapshot)
            .filter(OrderConfirmedSnapshot.order_id == normalized_order_id)
            .order_by(OrderConfirmedSnapshot.confirmed_at.desc(), OrderConfirmedSnapshot.id.desc())
            .first()
        )
        return str(row.id).strip() if row and str(row.id).strip() else None


def _to_sheet_month_id(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) == 7 and text[4:5] == "-":
        return text
    if "@" in text:
        month_id = text.split("@", 1)[0].strip()
        if len(month_id) == 7 and month_id[4:5] == "-":
            return month_id
    return None


def _parse_sheet_week_range(value: object) -> tuple[str | None, date | None, date | None]:
    text = str(value or "").strip()
    month_id = _to_sheet_month_id(text)
    if not month_id or "@" not in text or "~" not in text:
        return month_id, None, None
    try:
        range_part = text.split("@", 1)[1]
        start_token, end_token = [item.strip() for item in range_part.split("~", 1)]
        return month_id, date.fromisoformat(start_token), date.fromisoformat(end_token)
    except Exception:
        return month_id, None, None


def _normalize_entry_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _build_menu_context(*, facility_code: str | None, week_code: str | None) -> dict[str, Any]:
    facility = str(facility_code or "").strip() or None
    week = str(week_code or "").strip()
    month_id, week_start, week_end = _parse_sheet_week_range(week)
    if not month_id:
        return {
            "month_id": None,
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 0,
            "order_codes": [],
        }
    try:
        from src.services import order_service as _order_service
    except Exception:
        _order_service = None
    if _order_service is None:
        return {
            "month_id": month_id,
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 0,
            "order_codes": [],
        }
    diagnostics = _order_service._build_monthly_menu_diagnostics(
        week_id=week,
        facility_id=facility,
    )
    order_codes = [str(item).strip() for item in (diagnostics.get("order_codes") or []) if str(item).strip()]
    return {
        "month_id": month_id,
        "weekly_menu_missing": "monthly_menu_object_missing" in order_codes,
        "menu_entries_missing": "menu_entries_missing" in order_codes,
        "entries_count": int(diagnostics.get("facility_entries_count") or diagnostics.get("global_entries_count") or 0),
        "order_codes": order_codes,
    }


def _build_menu_context_from_current_sheet_context(
    current_sheet_context: dict[str, Any] | None,
    *,
    facility_code: str | None,
    week_code: str | None,
) -> dict[str, Any]:
    context = current_sheet_context if isinstance(current_sheet_context, dict) else {}
    diagnostics = (
        dict(context.get("menu_diagnostics"))
        if isinstance(context.get("menu_diagnostics"), dict)
        else {}
    )
    if not diagnostics:
        return _build_menu_context(facility_code=facility_code, week_code=week_code)
    resolved_week = (
        str(week_code or "").strip()
        or str(context.get("resolved_week_id") or "").strip()
    )
    month_id, _week_start, _week_end = _parse_sheet_week_range(resolved_week)
    order_codes = [str(item).strip() for item in (diagnostics.get("order_codes") or []) if str(item).strip()]
    return {
        "month_id": month_id,
        "weekly_menu_missing": "monthly_menu_object_missing" in order_codes,
        "menu_entries_missing": "menu_entries_missing" in order_codes,
        "entries_count": int(diagnostics.get("facility_entries_count") or diagnostics.get("global_entries_count") or 0),
        "order_codes": order_codes,
    }


def _has_reviewable_numeric_issues(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    failed_cells = payload.get("failed_cells")
    if isinstance(failed_cells, list) and failed_cells:
        return True
    for issue in payload.get("cell_issues") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("issue_code") or "").strip()
        if code in {
            "merged_numeric_cell",
            "overextended_span",
            "invalid_numeric_spike",
            "all_quantity_blank",
            "unexpected_dense_fill",
            "missing_blank_anchor_rows",
        }:
            return True
    critical_candidates = payload.get("critical_quantity_candidates")
    if isinstance(critical_candidates, list) and critical_candidates:
        return True
    return False


def _build_sheet_gate(
    *,
    order_id: str,
    order_payload: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
    active_evidence_run: dict[str, Any] | None = None,
    candidate_resolution: dict[str, Any] | None = None,
    current_sheet_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sheet_state = apply_gate_service.resolve_current_sheet_state(
        current_sheet_context,
        draft_sheet,
    )
    rows = sheet_state.get("rows") if isinstance(sheet_state.get("rows"), list) else None
    has_semantic_fields = bool(sheet_state.get("has_semantic_fields"))
    source = str(sheet_state.get("source") or "").strip() or "draft"
    blockers = [str(item).strip() for item in (sheet_state.get("blockers") or []) if str(item).strip()]
    warnings = [str(item).strip() for item in (sheet_state.get("warnings") or []) if str(item).strip()]
    draft_newer_than_lines = False
    if isinstance(draft_sheet, dict) and str(draft_sheet.get("id") or "").strip():
        lines_updated_at = order_payload.get("lines_updated_at") if isinstance(order_payload, dict) else None
        draft_edited_at = draft_sheet.get("edited_at")
        if lines_updated_at is None:
            draft_newer_than_lines = True
        elif isinstance(draft_edited_at, str) and draft_edited_at:
            try:
                draft_newer_than_lines = datetime.fromisoformat(draft_edited_at) > lines_updated_at
            except Exception:
                draft_newer_than_lines = True
    clean_saved_draft = bool(sheet_state.get("clean_saved_draft"))
    position_fallback_semantics_ready = position_column_mapping_service.candidate_resolution_uses_position_fallback(
        candidate_resolution
    )
    reparse_job = get_latest_order_job(order_id)
    if not is_order_reparse_job(reparse_job if isinstance(reparse_job, dict) else None, order_id):
        reparse_job = None
    reparse_state = describe_job_state(reparse_job if isinstance(reparse_job, dict) else None)
    return apply_gate_service.evaluate_sheet_gate(
        rows=rows if isinstance(rows, list) else None,
        source=source,
        has_semantic_fields=has_semantic_fields,
        blockers=[
            item
            for item in blockers
            if item not in {"monthly_menu_object_missing", "menu_entries_missing", "weekly_menu_missing"}
        ],
        warnings=[
            item
            for item in warnings
            if item not in {"monthly_menu_object_missing", "menu_entries_missing", "weekly_menu_missing"}
        ],
        draft_newer_than_lines=draft_newer_than_lines,
        auto_apply_blocked="auto_apply_blocked" in blockers or "auto_apply_blocked" in warnings,
        reparse_status=reparse_state.get("status"),
        clean_saved_draft=clean_saved_draft,
        position_fallback_semantics_ready=position_fallback_semantics_ready,
        authoritative_persisted_draft=bool(sheet_state.get("authoritative_persisted_draft")),
        base_evidence_run_id=str(sheet_state.get("base_evidence_run_id") or "").strip() or None,
        active_evidence_run_id=str((active_evidence_run or {}).get("id") or "").strip() or None,
    )


def _job_candidate_evidence_run_id(reparse_job: dict[str, Any] | None) -> str | None:
    if not isinstance(reparse_job, dict):
        return None
    metrics = reparse_job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    if metrics.get("new_evidence_available") is not True:
        return None
    return str(metrics.get("evidence_run_id") or "").strip() or None


def _infer_reparse_request_mode(
    reparse_job: dict[str, Any] | None,
    reparse_state: dict[str, Any] | None,
) -> str:
    if not isinstance(reparse_job, dict):
        return ""
    metrics = reparse_job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    direct_mode = get_job_request_mode(reparse_job)
    if direct_mode:
        return direct_mode
    if metrics.get("evidence_only_rerun") is True:
        return "ocr_rerun"
    return ""


def _resolve_active_evidence_run(
    latest_evidence_run: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(draft_sheet, dict):
        return latest_evidence_run if isinstance(latest_evidence_run, dict) else None
    draft_base_evidence_run_id = str(draft_sheet.get("base_evidence_run_id") or "").strip() or None
    latest_evidence_run_id = str((latest_evidence_run or {}).get("id") or "").strip() or None
    if not draft_base_evidence_run_id or draft_base_evidence_run_id == latest_evidence_run_id:
        return latest_evidence_run if isinstance(latest_evidence_run, dict) else None
    resolved = ocr_evidence_service.get_evidence_run(draft_base_evidence_run_id)
    return resolved if isinstance(resolved, dict) else (latest_evidence_run if isinstance(latest_evidence_run, dict) else None)


def _resolve_candidate_evidence_run(
    latest_evidence_run: dict[str, Any] | None,
    active_evidence_run: dict[str, Any] | None,
    reparse_job: dict[str, Any] | None,
) -> dict[str, Any] | None:
    active_evidence_run_id = str((active_evidence_run or {}).get("id") or "").strip() or None
    latest_evidence_run_id = str((latest_evidence_run or {}).get("id") or "").strip() or None
    rerun_candidate_id = _job_candidate_evidence_run_id(reparse_job)
    if (
        rerun_candidate_id
        and rerun_candidate_id != active_evidence_run_id
        and (not latest_evidence_run_id or rerun_candidate_id == latest_evidence_run_id)
    ):
        rerun_candidate = ocr_evidence_service.get_evidence_run(rerun_candidate_id)
        if isinstance(rerun_candidate, dict):
            return rerun_candidate
    if latest_evidence_run_id and latest_evidence_run_id != active_evidence_run_id:
        return latest_evidence_run if isinstance(latest_evidence_run, dict) else None
    return None


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _candidate_requires_prompt(
    candidate_evidence_run: dict[str, Any] | None,
    acknowledged_candidate_decision: dict[str, Any] | None,
) -> bool:
    candidate_evidence_run_id = str((candidate_evidence_run or {}).get("id") or "").strip() or None
    if not candidate_evidence_run_id:
        return False
    if not isinstance(acknowledged_candidate_decision, dict):
        return True
    acknowledged_candidate_evidence_run_id = (
        str(acknowledged_candidate_decision.get("selected_value") or "").strip() or None
    )
    if candidate_evidence_run_id == acknowledged_candidate_evidence_run_id:
        return False
    candidate_created_at = _parse_optional_datetime(
        (candidate_evidence_run or {}).get("created_at"),
    )
    acknowledged_selected_at = _parse_optional_datetime(
        acknowledged_candidate_decision.get("selected_at"),
    )
    if candidate_created_at is not None and acknowledged_selected_at is not None:
        return candidate_created_at > acknowledged_selected_at
    return True


def _augment_workflow_evidence_run(
    evidence_run: dict[str, Any] | None,
    *,
    facility_code: str | None,
) -> dict[str, Any] | None:
    if not isinstance(evidence_run, dict):
        return evidence_run
    normalized_facility_code = str(facility_code or "").strip()
    if not normalized_facility_code:
        return evidence_run
    payload = evidence_run.get("payload_json")
    if not isinstance(payload, dict):
        return evidence_run
    if not candidate_resolution_service.position_fallback_allowed_for_facility(
        current_facility=normalized_facility_code,
        payload=payload,
    ):
        return evidence_run
    facility_config = config_service.get_facility_config(normalized_facility_code) or {}
    template = facility_config.get("fax_template") if isinstance(facility_config, dict) else None
    if not isinstance(template, dict):
        return evidence_run
    augmented_payload = position_column_mapping_service.augment_payload_with_position_fallback(
        payload,
        template,
        template_id=str(facility_config.get("fax_template_id") or "").strip() or None,
    )
    if not isinstance(augmented_payload, dict) or augmented_payload is payload:
        return evidence_run
    capabilities = ocr_evidence_service._build_capabilities(augmented_payload)
    capabilities["legacy_editable"] = bool(
        str(evidence_run.get("schema_version") or "").startswith("v1_legacy")
    )
    return {
        **evidence_run,
        "payload_json": augmented_payload,
        "capabilities_json": capabilities,
    }


def _load_workflow_current_sheet_context(
    order_id: str,
    *,
    refresh_draft_from_semantic: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    try:
        from src.services import order_service as _order_service
    except Exception:
        _order_service = None
    if _order_service is None:
        latest_draft = draft_sheet_service.get_latest_sheet_draft(order_id)
        if not isinstance(latest_draft, dict):
            return None
        draft_payload = (
            latest_draft.get("draft_sheet_json")
            if isinstance(latest_draft.get("draft_sheet_json"), dict)
            else latest_draft
        )
        fields = list(draft_payload.get("fields") or []) if isinstance(draft_payload, dict) else []
        return {
            "order_id": order_id,
            "draft_record": latest_draft,
            "draft_payload": draft_payload,
            "draft_id": str(latest_draft.get("id") or "").strip() or None,
            "source": str((draft_payload or {}).get("source") or latest_draft.get("draft_state") or "draft").strip() or "draft",
            "fields": fields,
            "rows": list((draft_payload or {}).get("rows") or []),
            "row_ids": list((draft_payload or {}).get("row_ids") or []),
            "warnings": [str(item).strip() for item in (latest_draft.get("warnings_json") or []) if str(item).strip()],
            "blockers": [str(item).strip() for item in (latest_draft.get("blockers_json") or []) if str(item).strip()],
            "clean_saved_draft": apply_gate_service.has_clean_saved_draft(latest_draft),
            "has_semantic_fields": bool(fields) and not all(re.fullmatch(r"col\d+", str(field or "").strip()) for field in fields),
            "menu_diagnostics": {},
            "resolved_week_id": None,
        }
    if force_refresh:
        return _order_service.refresh_current_sheet_context(
            order_id,
            refresh_draft_from_semantic=refresh_draft_from_semantic,
            upgrade_generic_from_sheet=True,
            backfill_from_revision=False,
        )
    return _order_service.get_current_sheet_context(
        order_id,
        refresh_draft_from_semantic=refresh_draft_from_semantic,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )


def _draft_sheet_has_quantity_values(draft_sheet: dict[str, Any] | None) -> bool:
    if not isinstance(draft_sheet, dict):
        return False
    payload = (
        draft_sheet.get("draft_sheet_json")
        if isinstance(draft_sheet.get("draft_sheet_json"), dict)
        else draft_sheet
    )
    if not isinstance(payload, dict):
        return False
    fields = payload.get("fields")
    rows = payload.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return False
    quantity_indexes = {
        idx for idx, field in enumerate(fields) if str(field or "").strip().startswith("qty.")
    }
    if not quantity_indexes:
        return False
    for row in rows:
        if not isinstance(row, list):
            continue
        for idx in quantity_indexes:
            if idx < len(row) and str(row[idx] or "").strip():
                return True
    return False


def _current_sheet_clears_evidence_review(
    current_sheet_context: dict[str, Any] | None,
    active_evidence_run: dict[str, Any] | None = None,
) -> bool:
    sheet_state = apply_gate_service.resolve_current_sheet_state(
        current_sheet_context,
        None,
    )
    rows = sheet_state.get("rows")
    if not bool(sheet_state.get("has_semantic_fields")) or not isinstance(rows, list) or not rows:
        return False
    sheet_gate = apply_gate_service.evaluate_sheet_gate(
        rows=rows,
        source=str(sheet_state.get("source") or "").strip() or None,
        has_semantic_fields=bool(sheet_state.get("has_semantic_fields")),
        blockers=[str(item).strip() for item in (sheet_state.get("blockers") or []) if str(item).strip()],
        warnings=[str(item).strip() for item in (sheet_state.get("warnings") or []) if str(item).strip()],
        clean_saved_draft=bool(sheet_state.get("clean_saved_draft")),
        authoritative_persisted_draft=bool(sheet_state.get("authoritative_persisted_draft")),
        base_evidence_run_id=str(sheet_state.get("base_evidence_run_id") or "").strip() or None,
        active_evidence_run_id=str((active_evidence_run or {}).get("id") or "").strip() or None,
    )
    if (sheet_gate.get("apply_blockers") or []) or (sheet_gate.get("confirm_blockers") or []):
        return False
    review_warnings = {
        "sheet_ocr_review_required",
        "sheet_payload_mapping_low_confidence",
        "sheet_order_lines_unmapped_fallback_payload",
        "column_mapping_review_required",
        "quantity_review_required",
        "numeric_trust_low",
    }
    stale_authoritative_sheet = apply_gate_service.authoritative_sheet_suppresses_stale_evidence_issues(
        source=str(sheet_state.get("source") or "").strip() or None,
        rows=rows,
        has_semantic_fields=bool(sheet_state.get("has_semantic_fields")),
        clean_saved_draft=bool(sheet_state.get("clean_saved_draft")),
        authoritative_persisted_draft=bool(sheet_state.get("authoritative_persisted_draft")),
        base_evidence_run_id=str(sheet_state.get("base_evidence_run_id") or "").strip() or None,
        active_evidence_run_id=str((active_evidence_run or {}).get("id") or "").strip() or None,
    )
    warnings = set(
        apply_gate_service.filter_stale_issue_tokens(
            [str(item or "").strip() for item in (sheet_state.get("warnings") or []) if str(item or "").strip()],
            source=str(sheet_state.get("source") or "").strip() or None,
            clean_saved_draft=bool(sheet_state.get("clean_saved_draft")),
            stale_authoritative_sheet=stale_authoritative_sheet,
        )
    )
    if warnings & review_warnings:
        return False
    return True


def _suppress_stale_evidence_review_warnings(apply_gate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(apply_gate, dict):
        return apply_gate
    stale_tokens = {
        "column_mapping_review_required",
        "quantity_review_required",
        "numeric_trust_low",
    }
    suppressed = dict(apply_gate)
    for key in ("warnings", "apply_warnings", "confirm_warnings"):
        values = suppressed.get(key)
        if not isinstance(values, list):
            continue
        suppressed[key] = [
            str(item).strip()
            for item in values
            if str(item).strip() and str(item).strip() not in stale_tokens
        ]
    return suppressed


def _decision_selected_label(decision: dict[str, Any], selected_value: str) -> str:
    candidate_set = decision.get("candidate_set_json") if isinstance(decision, dict) else {}
    candidates = candidate_set.get("candidates") if isinstance(candidate_set, dict) else []
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if value and value == selected_value:
                label = str(item.get("label") or value).strip()
                return label or value
    return selected_value


def _merge_selected_decisions_into_resolution(
    candidate_resolution: dict[str, Any] | None,
    synced_decisions: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, set[str]]:
    if not isinstance(candidate_resolution, dict):
        return candidate_resolution, set()
    resolutions = candidate_resolution.get("resolutions")
    if not isinstance(resolutions, dict):
        return candidate_resolution, set()
    unresolved_types: set[str] = set()
    for decision in synced_decisions or []:
        if not isinstance(decision, dict):
            continue
        decision_type = str(decision.get("decision_type") or "").strip()
        if not decision_type:
            continue
        resolution = resolutions.get(decision_type)
        if not isinstance(resolution, dict):
            continue
        selected_value = str(decision.get("selected_value") or "").strip()
        if not selected_value:
            unresolved_types.add(decision_type)
            resolution["requires_user_choice"] = True
            continue
        resolution["resolved_value"] = selected_value
        resolution["resolved_label"] = _decision_selected_label(decision, selected_value)
        resolution["requires_user_choice"] = False
        resolution["blocked"] = False
        resolution["blocked_reasons"] = [
            str(item).strip()
            for item in (resolution.get("blocked_reasons") or [])
            if str(item).strip() and "choice_required" not in str(item).strip()
        ]
        if not str(resolution.get("confidence") or "").strip() or str(resolution.get("confidence") or "").strip() == "unknown":
            resolution["confidence"] = "medium"
        resolution["selected_via_user_choice"] = True
        if decision_type in {"template", "column_mapping", "quantity"}:
            resolution["attention_required"] = False
            resolution["attention_reasons"] = []
    candidate_resolution["critical_choices"] = [
        {
            "decision_type": key,
            "title": {
                "facility": "施設候補を選択",
                "week": "対象週を選択",
                "template": "票面テンプレートを選択",
                "column_mapping": "列の並び候補を選択",
                "quantity": "重要な数量候補を選択",
            }.get(key, key),
            "candidates": list(value.get("candidates") or []),
            "blocked_reasons": list(value.get("blocked_reasons") or []),
        }
        for key, value in resolutions.items()
        if isinstance(value, dict) and bool(value.get("requires_user_choice"))
    ]
    gate_summary = candidate_resolution_service.summarize_resolution_gate(
        resolutions if isinstance(resolutions, dict) else {},
    )
    candidate_resolution["gate_summary"] = gate_summary
    candidate_resolution["requires_user_choice"] = bool(gate_summary.get("choice_required_types"))
    candidate_resolution["attention_required"] = bool(
        any(
            isinstance(value, dict) and bool(value.get("attention_required"))
            for value in resolutions.values()
        )
    )
    return candidate_resolution, unresolved_types


def _derive_state(
    *,
    order_payload: dict[str, Any] | None,
    evidence_run: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
    candidate_resolution: dict[str, Any] | None,
    apply_gate: dict[str, Any] | None,
    reparse_state: dict[str, Any] | None = None,
    reparse_request_mode: str | None = None,
    current_sheet_review_cleared: bool = False,
) -> tuple[str, str, str | None, list[str], list[str], str]:
    blockers = list((apply_gate or {}).get("blockers") or [])
    warnings = list((apply_gate or {}).get("warnings") or [])
    draft_payload = (
        draft_sheet.get("draft_sheet_json")
        if isinstance(draft_sheet, dict) and isinstance(draft_sheet.get("draft_sheet_json"), dict)
        else (draft_sheet if isinstance(draft_sheet, dict) else {})
    )
    draft_warnings = [
        str(item or "").strip()
        for item in list((draft_sheet or {}).get("warnings_json") or []) + list((draft_payload or {}).get("warnings") or [])
        if str(item or "").strip()
    ]
    order_status = str((order_payload or {}).get("status") or "").strip()
    clean_saved_draft = apply_gate_service.has_clean_saved_draft(draft_sheet)
    resolutions = (candidate_resolution or {}).get("resolutions") if isinstance(candidate_resolution, dict) else {}
    blocker_tokens = {str(item).strip() for item in blockers if str(item).strip()}
    identity_choice_required = bool(
        blocker_tokens
        & {"facility_choice_required", "facility_unresolved", "week_choice_required", "week_unresolved"}
    )
    layout_choice_required = bool(
        blocker_tokens
        & {
            "template_choice_required",
            "column_mapping_choice_required",
            "quantity_choice_required",
        }
    )
    position_fallback_semantics_ready = position_column_mapping_service.candidate_resolution_uses_position_fallback(
        candidate_resolution
    )
    draft_source = apply_gate_service.canonical_sheet_source(
        (draft_payload or {}).get("source"),
        has_persisted_draft=isinstance(draft_sheet, dict) and bool(str(draft_sheet.get("id") or "").strip()),
    )
    filtered_draft_warnings = apply_gate_service.filter_stale_issue_tokens(
        draft_warnings,
        source=draft_source,
        clean_saved_draft=clean_saved_draft,
        position_fallback_semantics_ready=position_fallback_semantics_ready,
    )
    attention_required = bool(
        isinstance(candidate_resolution, dict)
        and (
            candidate_resolution.get("attention_required")
            or "column_mapping_review_required" in warnings
            or "quantity_review_required" in warnings
        )
    )
    if any(
        token in {"column_mapping_review_required", "quantity_review_required"}
        for token in filtered_draft_warnings
    ):
        attention_required = True
    if clean_saved_draft:
        attention_required = False
    quantity_resolution = resolutions.get("quantity") if isinstance(resolutions, dict) else None
    quantity_selected_via_user_choice = bool(
        isinstance(quantity_resolution, dict) and quantity_resolution.get("selected_via_user_choice")
    )
    capabilities = evidence_run.get("capabilities_json") if isinstance(evidence_run, dict) else {}
    evidence_payload = evidence_run.get("payload_json") if isinstance(evidence_run, dict) else None
    position_fallback_clears_numeric_warning = bool(
        position_fallback_semantics_ready
        and not ocr_evidence_service.payload_has_high_risk_numeric_issues(evidence_payload)
    )
    semantic_shell_only = bool(
        isinstance(capabilities, dict)
        and capabilities.get("semantic_shell_only")
        and not clean_saved_draft
        and not position_fallback_semantics_ready
    )
    numeric_trust_low = bool(
        isinstance(capabilities, dict)
        and capabilities.get("numeric_trust_low")
        and not quantity_selected_via_user_choice
        and not clean_saved_draft
        and not position_fallback_clears_numeric_warning
    )
    draft_has_quantity_values = _draft_sheet_has_quantity_values(draft_sheet)
    has_persisted_draft = bool(isinstance(draft_sheet, dict) and str(draft_sheet.get("id") or "").strip())
    has_high_risk_numeric_issues = _has_reviewable_numeric_issues(evidence_payload)
    draft_requests_ocr_review = "sheet_ocr_review_required" in filtered_draft_warnings
    if current_sheet_review_cleared:
        attention_required = False
        semantic_shell_only = False
        numeric_trust_low = False
        has_high_risk_numeric_issues = False
        draft_requests_ocr_review = False
    reparse_status = str((reparse_state or {}).get("status") or "").strip().lower()
    normalized_reparse_request_mode = str(reparse_request_mode or "").strip().lower()
    draft_waiting_apply = "draft_newer_than_lines" in warnings
    if order_status == "確定" and not draft_waiting_apply:
        return "confirmed", "確定済み", "none", blockers, warnings, "high"
    if normalized_reparse_request_mode == "ocr_rerun" and reparse_status in {"running", "pending", "awaiting_output", "recovering"}:
        return "rerun_in_progress", "OCRパイプラインを再取得しています", "wait_for_rerun", blockers, warnings, "low"
    if (
        normalized_reparse_request_mode == "ocr_rerun"
        and reparse_status == "hard_failed"
        and (isinstance(draft_sheet, dict) or order_status == "確定")
    ):
        if "rerun_failed_keep_current" not in warnings:
            warnings = [*warnings, "rerun_failed_keep_current"]
        return (
            "rerun_failed_keep_current",
            "OCR再取得に失敗しました。現在のシートは保持されています",
            "rerun_ocr_pipeline",
            blockers,
            warnings,
            "low",
        )
    if not isinstance(evidence_run, dict):
        return "uploaded", "OCR証拠の生成待ちです", "run_ocr_pipeline", blockers or ["evidence_missing"], warnings, "low"
    if isinstance(capabilities, dict) and capabilities.get("recovery_required"):
        return "recovery_required", "OCR基盤の復旧が必要です", "recover_ocr_evidence", blockers or ["evidence_recovery_required"], warnings, "low"
    if identity_choice_required:
        return "identity_choice_required", "施設または週の候補選択が必要です", "resolve_identity_choice", blockers, warnings, "medium"
    if layout_choice_required:
        return "layout_choice_required", "OCR候補の選択が必要です", "resolve_layout_choice", blockers, warnings, "medium"
    semantic_only_blockers = [
        item
        for item in blockers
        if item not in {"semantic_shell_only", "evidence_edit_unavailable", "draft_rows_empty", "rows_empty"}
    ]
    if (
        numeric_trust_low
        and (
            has_high_risk_numeric_issues
            or draft_requests_ocr_review
            or (has_persisted_draft and draft_has_quantity_values)
        )
        and not semantic_only_blockers
    ):
        return "review_required", "数量候補はありますが信頼度が低いため、確認してから反映してください", "review_critical_cells", blockers, warnings, "medium"
    if semantic_shell_only and not semantic_only_blockers:
        return "semantic_shell_only", "メニュー枠はありますが、数量はまだ信用できません", "rerun_ocr_pipeline", blockers, warnings, "low"
    if numeric_trust_low and not semantic_only_blockers:
        return "semantic_shell_only", "メニュー枠はありますが、数量はまだ信用できません", "rerun_ocr_pipeline", blockers, warnings, "low"
    if isinstance(draft_sheet, dict):
        if attention_required:
            return "review_required", "高リスクなOCR候補を確認してください", "review_critical_cells", blockers, warnings, "medium"
        if (apply_gate or {}).get("can_apply"):
            return "apply_ready", "下書きを明細へ反映できます", "apply_draft", blockers, warnings, "medium"
        if blockers:
            return "draft_blocked", "下書きはありますが、反映前に条件の解消が必要です", "resolve_blockers", blockers, warnings, "medium"
        return "draft_ready", "下書きを確認して必要なら修正してください", "edit_draft", blockers, warnings, "medium"
    if blockers:
        return "draft_blocked", "下書きはありますが、反映前に条件の解消が必要です", "resolve_blockers", blockers, warnings, "medium"
    return "evidence_ready", "OCR証拠を確認できます", "open_draft", blockers, warnings, "medium"


def refresh_workflow_state(
    order_id: str,
    *,
    refresh_draft_from_semantic: bool = True,
    include_candidate_preview: bool = True,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    refresh_stack = _WORKFLOW_REFRESH_STACK.get()
    if normalized_order_id in refresh_stack:
        current_state = get_workflow_state(normalized_order_id)
        if current_state is not None:
            return current_state
    current_sheet_context = _load_workflow_current_sheet_context(
        normalized_order_id,
        refresh_draft_from_semantic=refresh_draft_from_semantic,
        force_refresh=True,
    )
    stack_token = _WORKFLOW_REFRESH_STACK.set((*refresh_stack, normalized_order_id))
    try:
        return _build_workflow_state_projection(
            normalized_order_id,
            refresh_draft_from_semantic=refresh_draft_from_semantic,
            include_candidate_preview=include_candidate_preview,
            current_sheet_context_override=current_sheet_context,
            force_refresh_current_sheet=False,
            persist=True,
        )
    finally:
        _WORKFLOW_REFRESH_STACK.reset(stack_token)


def project_workflow_state(
    order_id: str,
    *,
    refresh_draft_from_semantic: bool = True,
    current_sheet_context: dict[str, Any] | None = None,
    include_candidate_preview: bool = True,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    return _build_workflow_state_projection(
        normalized_order_id,
        refresh_draft_from_semantic=refresh_draft_from_semantic,
        include_candidate_preview=include_candidate_preview,
        current_sheet_context_override=current_sheet_context,
        force_refresh_current_sheet=False,
        persist=False,
    )


def _build_workflow_state_projection(
    normalized_order_id: str,
    *,
    refresh_draft_from_semantic: bool = True,
    include_candidate_preview: bool = True,
    current_sheet_context_override: dict[str, Any] | None = None,
    force_refresh_current_sheet: bool = False,
    persist: bool = True,
) -> dict[str, Any] | None:
    current_sheet_context = (
        current_sheet_context_override
        if isinstance(current_sheet_context_override, dict)
        else _load_workflow_current_sheet_context(
            normalized_order_id,
            refresh_draft_from_semantic=refresh_draft_from_semantic,
            force_refresh=force_refresh_current_sheet,
        )
    )
    order_payload = (
        current_sheet_context.get("order_payload")
        if isinstance(current_sheet_context, dict) and isinstance(current_sheet_context.get("order_payload"), dict)
        else _load_order_payload(normalized_order_id)
    )
    if isinstance(current_sheet_context, dict):
        context_order_payload = current_sheet_context.get("order_payload")
        if isinstance(context_order_payload, dict):
            order_payload = context_order_payload
    if not isinstance(order_payload, dict):
        return None
    current_sheet_facility_code = (
        str((current_sheet_context or {}).get("facility_id") or "").strip() or None
    )
    order_facility_code = str(order_payload.get("facility") or "").strip() or None
    order_week_code = (
        str(order_payload.get("persisted_week_value") or "").strip()
        or str(order_payload.get("week_value") or "").strip()
        or str(order_payload.get("week") or "").strip()
        or None
    )
    current_sheet_week_code = (
        str((current_sheet_context or {}).get("resolved_week_id") or "").strip() or None
    )
    current_week_code = (
        order_week_code
        if order_week_code and "@" in order_week_code
        else current_sheet_week_code
        or order_week_code
    )
    evidence_run = ocr_evidence_service.get_latest_evidence_run(normalized_order_id)
    draft_sheet = (
        current_sheet_context.get("draft_record")
        if isinstance(current_sheet_context, dict)
        else None
    )
    order_bound_job = get_latest_order_job(normalized_order_id)
    reparse_job = (
        order_bound_job
        if is_order_reparse_job(order_bound_job if isinstance(order_bound_job, dict) else None, normalized_order_id)
        else None
    )
    active_evidence_run = _resolve_active_evidence_run(evidence_run, draft_sheet)
    active_evidence_run = _augment_workflow_evidence_run(
        active_evidence_run,
        facility_code=order_facility_code or current_sheet_facility_code,
    )
    candidate_evidence_run = _resolve_candidate_evidence_run(
        evidence_run,
        active_evidence_run,
        reparse_job if isinstance(reparse_job, dict) else None,
    )
    candidate_evidence_run_id = str((candidate_evidence_run or {}).get("id") or "").strip() or None
    acknowledged_candidate_decision = critical_decision_service.get_acknowledged_candidate_evidence_decision(
        normalized_order_id,
    )
    acknowledged_candidate_evidence_run_id = (
        str((acknowledged_candidate_decision or {}).get("selected_value") or "").strip() or None
    )
    try:
        from src.services import order_service as _order_service
    except Exception:
        _order_service = None
    serialized_current_sheet_revision_id = (
        str((current_sheet_context or {}).get("current_sheet_revision_id") or "").strip()
        or None
    )
    candidate_sheet_state = {
        "current_sheet_revision_id": serialized_current_sheet_revision_id,
        "candidate_evidence_run_id": candidate_evidence_run_id,
        "candidate_preview_available": False,
        "candidate_preview_error": "candidate_not_found" if not candidate_evidence_run_id else None,
        "candidate_has_meaningful_diff": False,
        "candidate_preview_draft": None,
    }
    if include_candidate_preview and _order_service is not None:
        candidate_sheet_state = _order_service.candidate_sheet_state(
            normalized_order_id,
            candidate_evidence_run_id=candidate_evidence_run_id,
            current_sheet_context=current_sheet_context,
        )
    candidate_preview_available = bool((candidate_sheet_state or {}).get("candidate_preview_available"))
    candidate_has_meaningful_diff = bool((candidate_sheet_state or {}).get("candidate_has_meaningful_diff"))
    has_new_candidate = bool(
        isinstance(candidate_evidence_run, dict)
        and candidate_evidence_run_id
        and candidate_preview_available
        and candidate_has_meaningful_diff
        and _candidate_requires_prompt(
            candidate_evidence_run,
            acknowledged_candidate_decision,
        )
    )
    evidence_payload = active_evidence_run.get("payload_json") if isinstance(active_evidence_run, dict) else None
    reparse_state = describe_job_state(reparse_job if isinstance(reparse_job, dict) else None)
    reparse_request_mode = _infer_reparse_request_mode(reparse_job if isinstance(reparse_job, dict) else None, reparse_state)
    if isinstance(reparse_state, dict) and reparse_request_mode:
        reparse_state = {**reparse_state, "request_mode": reparse_request_mode}
    candidate_resolution = candidate_resolution_service.resolve_order_candidates(
        order_id=normalized_order_id,
        facility_code=order_facility_code or current_sheet_facility_code,
        week_code=current_week_code,
        received_at=order_payload.get("received_at"),
        evidence_payload=evidence_payload if isinstance(evidence_payload, dict) else None,
    )
    decision_projector = (
        critical_decision_service.sync_pending_decisions
        if persist
        else critical_decision_service.project_pending_decisions
    )
    synced_decisions_all = decision_projector(
        normalized_order_id,
        list(candidate_resolution.get("critical_choices") or []),
        base_evidence_run_id=str((active_evidence_run or {}).get("id") or "").strip() or None,
    )
    synced_decisions = [
        item
        for item in (synced_decisions_all or [])
        if not critical_decision_service.is_internal_decision_type(
            str(item.get("decision_type") or "").strip() if isinstance(item, dict) else "",
        )
    ]
    candidate_resolution, _unresolved_types = _merge_selected_decisions_into_resolution(
        candidate_resolution,
        synced_decisions,
    )
    apply_gate = apply_gate_service.evaluate_apply_gate(
        order_payload=order_payload,
        evidence_run=active_evidence_run,
        draft_sheet=draft_sheet,
        current_sheet_context=current_sheet_context,
        candidate_resolution=candidate_resolution,
        menu_context=_build_menu_context_from_current_sheet_context(
            current_sheet_context=current_sheet_context,
            facility_code=order_facility_code or current_sheet_facility_code,
            week_code=current_week_code,
        ),
        sheet_gate=_build_sheet_gate(
            order_id=normalized_order_id,
            order_payload=order_payload,
            draft_sheet=draft_sheet,
            active_evidence_run=active_evidence_run,
            candidate_resolution=candidate_resolution,
            current_sheet_context=current_sheet_context,
        ),
    )
    current_sheet_review_cleared = _current_sheet_clears_evidence_review(
        current_sheet_context,
        active_evidence_run=active_evidence_run,
    )
    if current_sheet_review_cleared:
        apply_gate = _suppress_stale_evidence_review_warnings(apply_gate)
    state, headline, primary_action, blockers, warnings, confidence_band = _derive_state(
        order_payload=order_payload,
        evidence_run=active_evidence_run,
        draft_sheet=draft_sheet,
        candidate_resolution=candidate_resolution,
        apply_gate=apply_gate,
        reparse_state=reparse_state,
        reparse_request_mode=reparse_request_mode,
        current_sheet_review_cleared=current_sheet_review_cleared,
    )
    secondary_actions = []
    if state in {"uploaded", "recovery_required"}:
        secondary_actions = ["rerun_yomitoku", "llm_reparse"]
    elif state == "semantic_shell_only":
        secondary_actions = ["recover_ocr_evidence", "save_draft"]
    elif state == "rerun_failed_keep_current":
        secondary_actions = ["keep_current_draft", "recover_ocr_evidence", "llm_reparse"]
    elif state == "rerun_in_progress":
        secondary_actions = ["wait"]
    elif state in {"draft_ready", "draft_blocked", "apply_ready", "review_required"}:
        secondary_actions = ["rerun_yomitoku", "save_draft", "llm_reparse"]
    elif state in {"identity_choice_required", "layout_choice_required"}:
        secondary_actions = ["select_candidate", "save_draft"]
    if has_new_candidate:
        secondary_actions = list(dict.fromkeys([*secondary_actions, "switch_to_new_evidence", "keep_current_draft"]))

    evidence_run_id = str((active_evidence_run or {}).get("id") or "").strip() or None
    draft_id = str((draft_sheet or {}).get("id") or "").strip() or None
    confirmed_snapshot_id = _latest_confirmed_snapshot_id(normalized_order_id)
    stored_secondary_actions: object = secondary_actions
    if persist:
        existing_workflow = get_workflow_state(normalized_order_id)
        existing_secondary = (
            existing_workflow.get("secondary_actions_json")
            if isinstance(existing_workflow, dict)
            else None
        )
        if isinstance(existing_secondary, dict) and isinstance(existing_secondary.get("workflow_v2"), dict):
            stored_secondary_actions = {
                **existing_secondary,
                "legacy_actions": secondary_actions,
            }
    update_payload = {
        "evidence_run_id": evidence_run_id,
        "draft_id": draft_id,
        "confirmed_snapshot_id": confirmed_snapshot_id,
        "state": state,
        "headline": headline,
        "primary_action": primary_action,
        "secondary_actions_json": stored_secondary_actions,
        "blockers_json": blockers,
        "warnings_json": warnings,
        "confidence_band": confidence_band,
    }
    if persist:
        last_transition_at = datetime.utcnow()
        with session_scope() as session:
            updated = session.execute(
                update(OrderWorkflowState)
                .where(OrderWorkflowState.order_id == normalized_order_id)
                .values(**update_payload, last_transition_at=last_transition_at)
            ).rowcount
            if not updated:
                session.add(
                    OrderWorkflowState(
                        order_id=normalized_order_id,
                        last_transition_at=last_transition_at,
                        **update_payload,
                    )
                )
            session.flush()
            row = session.get(OrderWorkflowState, normalized_order_id)
            serialized = _serialize(row) if isinstance(row, OrderWorkflowState) else {
                "order_id": normalized_order_id,
                "evidence_run_id": evidence_run_id,
                "draft_id": draft_id,
                "confirmed_snapshot_id": confirmed_snapshot_id,
                "state": state,
                "headline": headline,
                "primary_action": primary_action,
                "secondary_actions_json": list(secondary_actions),
                "blockers_json": list(blockers),
                "warnings_json": list(warnings),
                "confidence_band": confidence_band,
                "last_transition_at": last_transition_at.isoformat(),
            }
    else:
        persisted_state = get_workflow_state(normalized_order_id)
        serialized = {
            "order_id": normalized_order_id,
            "evidence_run_id": evidence_run_id,
            "draft_id": draft_id,
            "confirmed_snapshot_id": confirmed_snapshot_id,
            "state": state,
            "headline": headline,
            "primary_action": primary_action,
            "secondary_actions_json": list(secondary_actions),
            "blockers_json": list(blockers),
            "warnings_json": list(warnings),
            "confidence_band": confidence_band,
            "last_transition_at": (
                str((persisted_state or {}).get("last_transition_at") or "").strip() or None
            ),
        }

    serialized["candidate_resolution"] = candidate_resolution
    serialized["critical_decisions"] = synced_decisions
    serialized["apply_gate"] = apply_gate
    serialized = _attach_top_level_apply_flags(serialized, apply_gate)
    serialized_current_sheet_revision_id = (
        serialized_current_sheet_revision_id
        or str((candidate_sheet_state or {}).get("current_sheet_revision_id") or "").strip()
        or None
    )
    serialized["current_sheet_revision_id"] = serialized_current_sheet_revision_id
    candidate_prompt_visible = bool(
        has_new_candidate
        and serialized_current_sheet_revision_id
        and serialized_current_sheet_revision_id
        == (str((candidate_sheet_state or {}).get("current_sheet_revision_id") or "").strip() or None)
    )
    serialized["candidate_sheet_state"] = {
        "current_sheet_revision_id": serialized["current_sheet_revision_id"],
        "candidate_evidence_run_id": candidate_evidence_run_id,
        "candidate_preview_available": candidate_preview_available,
        "candidate_has_meaningful_diff": candidate_has_meaningful_diff,
        "candidate_preview_error": (
            str((candidate_sheet_state or {}).get("candidate_preview_error") or "").strip() or None
        ),
    }
    serialized["candidate_prompt_visible"] = candidate_prompt_visible
    serialized["candidate_evidence_run_id"] = candidate_evidence_run_id if candidate_prompt_visible else None
    serialized["acknowledged_candidate_evidence_run_id"] = acknowledged_candidate_evidence_run_id
    serialized["acknowledged_candidate_evidence_at"] = (
        acknowledged_candidate_decision.get("selected_at")
        if isinstance(acknowledged_candidate_decision, dict)
        else None
    )
    serialized["active_evidence_run_id"] = str((active_evidence_run or {}).get("id") or "").strip() or None
    serialized["reparse_state"] = reparse_state
    try:
        from src.services import order_service as _order_service
    except Exception:
        _order_service = None
    if _order_service is not None:
        effective_ocr_status = str((reparse_state or {}).get("status") or "").strip() or None
        if effective_ocr_status == "idle" and isinstance(active_evidence_run, dict):
            effective_ocr_status = "done"
        review_summary = _order_service.get_order_review_summary(
            normalized_order_id,
            lines_updated_at=order_payload.get("lines_updated_at"),
            ocr_status=effective_ocr_status,
            cached_payload=evidence_payload if isinstance(evidence_payload, dict) else None,
            ocr_metrics=(reparse_job.get("metrics") if isinstance(reparse_job, dict) else None),
            order_status=order_payload.get("status"),
            current_sheet_context=current_sheet_context,
            sheet_gate=apply_gate,
        )
        serialized.update(review_summary)
    return serialized
