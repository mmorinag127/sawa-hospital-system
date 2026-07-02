from __future__ import annotations

import re
from typing import Any

from src.services import candidate_resolution_service, ocr_evidence_service, position_column_mapping_service


_RECOVERABLE_BLOCKING_WARNINGS = {
    "week_unresolved",
    "menu_entries_missing",
    "sheet_fields_not_found",
    "sheet_fields_duplicate",
    "sheet_template_field_invalid",
    "sheet_quantity_columns_missing",
    "sheet_quantity_column_unmapped",
    "sheet_week_dates_incomplete",
    "week_menu_date_mismatch",
    "sheet_date_mismatch",
    "sheet_canonical_mismatch",
    "sheet_suspicious_blank_row",
    "sheet_structural_projection_corrupted",
    "template_resolution_blocked",
}

_LAYOUT_RESOLUTION_TYPES = {"template", "column_mapping", "quantity"}
_POSITION_FALLBACK_LAYOUT_SUPPRESSED_ISSUES = {
    "template_unresolved",
    "template_resolution_blocked",
    "sheet_quantity_column_unmapped",
    "sheet_payload_mapping_blocked_unresolved_template",
}
_POSITION_FALLBACK_SAVED_SHEET_ONLY_SUPPRESSED_ISSUES = {
    "ocr_evidence_recovery_required",
}
_STALE_AUTHORITATIVE_SHEET_SUPPRESSED_ISSUES = {
    "sheet_ocr_review_required",
    "sheet_payload_mapping_low_confidence",
    "sheet_order_lines_unmapped_fallback_payload",
    "column_mapping_review_required",
    "quantity_review_required",
    "numeric_trust_low",
}
_HUMAN_REVIEW_GATE_WARNINGS = {
    "column_mapping_review_required",
    "quantity_review_required",
    "numeric_trust_low",
    "ocr_review_required",
}
_RAW_SHEET_HUMAN_REVIEW_WARNINGS = {
    "sheet_ocr_review_required",
}
_GENERIC_FIELD_PATTERN = re.compile(r"col\d+")


def _dedupe_tokens(items: list[str] | None) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


def source_uses_saved_sheet(source: str | None) -> bool:
    normalized = str(source or "").strip()
    return normalized.startswith("draft_sheet") or normalized.startswith("edited_sheet")


def sheet_fields_are_semantic(fields: list[Any] | None) -> bool:
    normalized_fields = [
        str(field or "").strip()
        for field in (fields or [])
        if str(field or "").strip()
    ]
    return bool(normalized_fields) and not all(
        _GENERIC_FIELD_PATTERN.fullmatch(token)
        for token in normalized_fields
    )


def canonical_sheet_source(
    source: str | None,
    *,
    has_persisted_draft: bool = False,
) -> str:
    normalized = str(source or "").strip()
    if has_persisted_draft and not source_uses_saved_sheet(normalized):
        return "draft_sheet"
    return normalized


def authoritative_sheet_suppresses_stale_evidence_issues(
    *,
    source: str | None,
    rows: list[Any] | None,
    has_semantic_fields: bool = False,
    clean_saved_draft: bool = False,
    authoritative_persisted_draft: bool = False,
    base_evidence_run_id: str | None = None,
    active_evidence_run_id: str | None = None,
) -> bool:
    return False


def _stale_issue_suppressions(
    *,
    source: str | None,
    clean_saved_draft: bool = False,
    position_fallback_semantics_ready: bool = False,
    stale_authoritative_sheet: bool = False,
) -> set[str]:
    return set()


def filter_stale_issue_tokens(
    tokens: list[str] | None,
    *,
    source: str | None,
    clean_saved_draft: bool = False,
    position_fallback_semantics_ready: bool = False,
    stale_authoritative_sheet: bool = False,
) -> list[str]:
    filtered: list[str] = []
    for item in tokens or []:
        token = str(item or "").strip()
        if not token:
            continue
        filtered.append(token)
    return _dedupe_tokens(filtered)


def gate_requires_human_review(
    *,
    apply_warnings: list[str] | None = None,
    confirm_warnings: list[str] | None = None,
) -> bool:
    warning_tokens = {
        str(item or "").strip()
        for item in list(apply_warnings or []) + list(confirm_warnings or [])
        if str(item or "").strip()
    }
    return bool(warning_tokens & _HUMAN_REVIEW_GATE_WARNINGS)


def raw_sheet_requires_human_review(warnings: list[str] | None) -> bool:
    warning_tokens = {
        str(item or "").strip()
        for item in (warnings or [])
        if str(item or "").strip()
    }
    return bool(warning_tokens & _RAW_SHEET_HUMAN_REVIEW_WARNINGS)


def gate_can_apply(
    *,
    apply_blockers: list[str] | None,
    apply_warnings: list[str] | None = None,
    confirm_warnings: list[str] | None = None,
) -> bool:
    return not _dedupe_tokens(apply_blockers) and not gate_requires_human_review(
        apply_warnings=apply_warnings,
        confirm_warnings=confirm_warnings,
    )


def gate_can_confirm(
    *,
    confirm_blockers: list[str] | None,
    confirm_warnings: list[str] | None = None,
    apply_warnings: list[str] | None = None,
) -> bool:
    deduped_confirm_warnings = _dedupe_tokens(confirm_warnings)
    return (
        not _dedupe_tokens(confirm_blockers)
        and "recovery_recommended" not in deduped_confirm_warnings
        and not gate_requires_human_review(
            apply_warnings=apply_warnings,
            confirm_warnings=deduped_confirm_warnings,
        )
    )


def has_clean_saved_draft(draft_sheet: dict[str, Any] | None) -> bool:
    if not isinstance(draft_sheet, dict):
        return False
    if not str(draft_sheet.get("id") or "").strip():
        return False
    draft_payload = (
        draft_sheet.get("draft_sheet_json")
        if isinstance(draft_sheet.get("draft_sheet_json"), dict)
        else draft_sheet
    )
    rows = draft_payload.get("rows") if isinstance(draft_payload, dict) else None
    if not isinstance(rows, list) or len(rows) <= 0:
        return False
    blockers = [
        str(item or "").strip()
        for item in (draft_sheet.get("blockers_json") or [])
        if str(item or "").strip()
    ]
    warnings = [
        str(item or "").strip()
        for item in list(draft_sheet.get("warnings_json") or []) + list((draft_payload or {}).get("warnings") or [])
        if str(item or "").strip()
    ]
    warnings = [
        token
        for token in warnings
        if token not in {"llm_patch_applied"}
    ]
    return len(blockers) == 0 and len(warnings) == 0


def resolve_current_sheet_state(
    current_sheet_context: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
) -> dict[str, Any]:
    context = current_sheet_context if isinstance(current_sheet_context, dict) else {}
    draft_record = (
        context.get("draft_record")
        if isinstance(context.get("draft_record"), dict)
        else draft_sheet
        if isinstance(draft_sheet, dict)
        else None
    )
    draft_payload = (
        context.get("draft_payload")
        if isinstance(context.get("draft_payload"), dict)
        else (
            draft_record.get("draft_sheet_json")
            if isinstance(draft_record, dict) and isinstance(draft_record.get("draft_sheet_json"), dict)
            else draft_record
            if isinstance(draft_record, dict)
            else None
        )
    )
    fields = (
        list(context.get("fields") or [])
        if isinstance(context.get("fields"), list)
        else list(draft_payload.get("fields") or [])
        if isinstance(draft_payload, dict)
        else []
    )
    rows = (
        context.get("rows")
        if isinstance(context.get("rows"), list)
        else draft_payload.get("rows")
        if isinstance(draft_payload, dict) and isinstance(draft_payload.get("rows"), list)
        else None
    )
    has_semantic_fields = (
        bool(context.get("has_semantic_fields"))
        if context
        else sheet_fields_are_semantic(fields)
    )
    has_persisted_draft = (
        bool(context.get("has_persisted_draft"))
        if context
        else bool(isinstance(draft_record, dict) and str(draft_record.get("id") or "").strip())
    )
    raw_source = (
        str(context.get("source") or "").strip()
        or str((draft_payload or {}).get("source") or "").strip()
        or str((draft_record or {}).get("draft_state") or "").strip()
        or "draft"
    )
    source = (
        raw_source
        if context
        else canonical_sheet_source(
            raw_source,
            has_persisted_draft=bool(
                has_persisted_draft
                and has_semantic_fields
                and isinstance(rows, list)
                and len(rows) > 0
            ),
        )
    )
    blockers = (
        [str(item).strip() for item in (context.get("blockers") or []) if str(item).strip()]
        if context
        else _dedupe_tokens(
            [
                *[
                    str(item).strip()
                    for item in ((draft_record or {}).get("blockers_json") or [])
                    if str(item).strip()
                ],
                *[
                    str(item).strip()
                    for item in ((draft_payload or {}).get("blockers") or [])
                    if str(item).strip()
                ],
            ]
        )
    )
    warnings = (
        [str(item).strip() for item in (context.get("warnings") or []) if str(item).strip()]
        if context
        else _dedupe_tokens(
            [
                *[
                    str(item).strip()
                    for item in ((draft_record or {}).get("warnings_json") or [])
                    if str(item).strip()
                ],
                *[
                    str(item).strip()
                    for item in ((draft_payload or {}).get("warnings") or [])
                    if str(item).strip()
                ],
            ]
        )
    )
    clean_saved_draft = (
        bool(context.get("clean_saved_draft"))
        if context
        else has_clean_saved_draft(draft_record)
    )
    authoritative_persisted_draft = (
        bool(context.get("authoritative_persisted_draft"))
        if context and "authoritative_persisted_draft" in context
        else bool(
            has_persisted_draft
            and has_semantic_fields
            and isinstance(rows, list)
            and len(rows) > 0
        )
    )
    return {
        "draft_record": draft_record,
        "draft_payload": draft_payload,
        "fields": fields,
        "rows": rows,
        "has_semantic_fields": has_semantic_fields,
        "has_persisted_draft": has_persisted_draft,
        "source": source,
        "raw_source": raw_source,
        "blockers": blockers,
        "warnings": warnings,
        "clean_saved_draft": clean_saved_draft,
        "authoritative_persisted_draft": authoritative_persisted_draft,
        "resolved_week_id": (
            str(context.get("resolved_week_id") or "").strip()
            if context
            else str((draft_payload or {}).get("resolved_week_id") or (draft_payload or {}).get("week_id") or "").strip()
        )
        or None,
        "base_evidence_run_id": (
            str(context.get("base_evidence_run_id") or "").strip()
            if context
            else str((draft_record or {}).get("base_evidence_run_id") or (draft_payload or {}).get("base_evidence_run_id") or "").strip()
        )
        or None,
        "facility_id": (
            str(context.get("facility_id") or "").strip()
            if context
            else str((draft_payload or {}).get("facility_id") or "").strip()
        )
        or None,
    }


def evaluate_sheet_gate(
    *,
    rows: list[Any] | None,
    source: str | None,
    has_semantic_fields: bool = False,
    blockers: list[str] | None,
    warnings: list[str] | None,
    draft_newer_than_lines: bool = False,
    auto_apply_blocked: bool = False,
    reparse_status: str | None = None,
    clean_saved_draft: bool = False,
    position_fallback_semantics_ready: bool = False,
    authoritative_persisted_draft: bool = False,
    base_evidence_run_id: str | None = None,
    active_evidence_run_id: str | None = None,
) -> dict[str, list[str]]:
    apply_blockers: list[str] = []
    confirm_blockers: list[str] = []
    confirm_warnings: list[str] = []
    stale_authoritative_sheet = authoritative_sheet_suppresses_stale_evidence_issues(
        source=source,
        rows=rows,
        has_semantic_fields=has_semantic_fields,
        clean_saved_draft=clean_saved_draft,
        authoritative_persisted_draft=authoritative_persisted_draft,
        base_evidence_run_id=base_evidence_run_id,
        active_evidence_run_id=active_evidence_run_id,
    )
    effective_position_fallback_semantics_ready = bool(
        position_fallback_semantics_ready and not authoritative_persisted_draft
    )

    normalized_source = str(source or "").strip()
    normalized_blockers = filter_stale_issue_tokens(
        blockers,
        source=normalized_source,
        clean_saved_draft=clean_saved_draft,
        position_fallback_semantics_ready=effective_position_fallback_semantics_ready,
        stale_authoritative_sheet=stale_authoritative_sheet,
    )
    normalized_warnings = filter_stale_issue_tokens(
        warnings,
        source=normalized_source,
        clean_saved_draft=clean_saved_draft,
        position_fallback_semantics_ready=effective_position_fallback_semantics_ready,
        stale_authoritative_sheet=stale_authoritative_sheet,
    )
    normalized_reparse_status = str(reparse_status or "").strip().lower()
    bypass_monthly_menu_object_block = _semantic_sheet_can_bypass_monthly_menu_object_block(
        rows=rows,
        has_semantic_fields=has_semantic_fields,
    )

    if "sheet_weekly_menu_missing" in normalized_warnings and not has_semantic_fields:
        apply_blockers.append("weekly_menu_missing")
        confirm_blockers.append("weekly_menu_missing")
    for blocker in normalized_blockers:
        if blocker == "monthly_menu_object_missing" and bypass_monthly_menu_object_block:
            confirm_warnings.append("monthly_menu_object_missing")
            continue
        apply_blockers.append(blocker)
        confirm_blockers.append(blocker)
    for warning in normalized_warnings:
        if warning in _RECOVERABLE_BLOCKING_WARNINGS:
            apply_blockers.append(warning)
            confirm_blockers.append(warning)
    if draft_newer_than_lines:
        confirm_blockers.append("draft_newer_than_lines")
        confirm_warnings.append("draft_newer_than_lines")
    if auto_apply_blocked:
        confirm_blockers.append("auto_apply_blocked")
    if not isinstance(rows, list) or len(rows) <= 0:
        apply_blockers.append("rows_empty")
    if "sheet_ocr_review_required" in normalized_warnings:
        confirm_warnings.append("ocr_review_required")
    if normalized_source.startswith("ocr_table") and not has_semantic_fields:
        confirm_warnings.append("ocr_table_fallback")
    if normalized_reparse_status == "stalled":
        apply_blockers.append("reparse_stale")
        confirm_blockers.append("reparse_stale")

    return {
        "apply_blockers": _dedupe_tokens(apply_blockers),
        "confirm_blockers": _dedupe_tokens(confirm_blockers),
        "confirm_warnings": _dedupe_tokens(confirm_warnings),
    }


def evaluate_sheet_gate_from_context(
    current_sheet_context: dict[str, Any] | None,
    *,
    draft_newer_than_lines: bool = False,
    auto_apply_blocked: bool = False,
    reparse_status: str | None = None,
    position_fallback_semantics_ready: bool = False,
    active_evidence_run_id: str | None = None,
) -> dict[str, list[str]]:
    state = resolve_current_sheet_state(current_sheet_context, None)
    return evaluate_sheet_gate(
        rows=state.get("rows") if isinstance(state.get("rows"), list) else None,
        source=str(state.get("source") or "").strip() or None,
        has_semantic_fields=bool(state.get("has_semantic_fields")),
        blockers=[str(item).strip() for item in (state.get("blockers") or []) if str(item).strip()],
        warnings=[str(item).strip() for item in (state.get("warnings") or []) if str(item).strip()],
        draft_newer_than_lines=draft_newer_than_lines,
        auto_apply_blocked=auto_apply_blocked,
        reparse_status=reparse_status,
        clean_saved_draft=bool(state.get("clean_saved_draft")),
        position_fallback_semantics_ready=position_fallback_semantics_ready,
        authoritative_persisted_draft=bool(state.get("authoritative_persisted_draft")),
        base_evidence_run_id=str(state.get("base_evidence_run_id") or "").strip() or None,
        active_evidence_run_id=str(active_evidence_run_id or "").strip() or None,
    )


def _menu_context_blockers(menu_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(menu_context, dict):
        return []
    if isinstance(menu_context.get("order_codes"), list):
        return _dedupe_tokens([str(item).strip() for item in (menu_context.get("order_codes") or []) if str(item).strip()])
    if menu_context.get("weekly_menu_missing"):
        return ["monthly_menu_object_missing"]
    if menu_context.get("menu_entries_missing"):
        return ["menu_entries_missing"]
    return []


def _semantic_sheet_can_bypass_monthly_menu_object_block(
    *,
    rows: list[Any] | None,
    has_semantic_fields: bool,
) -> bool:
    if not has_semantic_fields:
        return False
    if not isinstance(rows, list) or len(rows) <= 0:
        return False
    return True


def evaluate_apply_gate(
    *,
    order_payload: dict[str, Any] | None,
    evidence_run: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
    current_sheet_context: dict[str, Any] | None = None,
    candidate_resolution: dict[str, Any] | None,
    menu_context: dict[str, Any] | None = None,
    sheet_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    apply_blockers: list[str] = []
    confirm_blockers: list[str] = []
    apply_warnings: list[str] = []
    confirm_warnings: list[str] = []

    sheet_state = resolve_current_sheet_state(current_sheet_context, draft_sheet)
    facility = (
        str(sheet_state.get("facility_id") or "").strip()
        or str((order_payload or {}).get("facility") or "").strip()
    )
    week = (
        str(sheet_state.get("resolved_week_id") or "").strip()
        or str((order_payload or {}).get("week_value") or (order_payload or {}).get("week") or "").strip()
    )
    clean_saved_draft = bool(sheet_state.get("clean_saved_draft"))
    evidence_payload = (evidence_run or {}).get("payload_json") if isinstance(evidence_run, dict) else None
    rows = sheet_state.get("rows") if isinstance(sheet_state.get("rows"), list) else None
    fields = sheet_state.get("fields") if isinstance(sheet_state.get("fields"), list) else None
    source = str(sheet_state.get("source") or "").strip()
    sheet_warnings = [
        str(item).strip()
        for item in (sheet_state.get("warnings") or [])
        if str(item).strip()
    ]
    has_semantic_fields = bool(sheet_state.get("has_semantic_fields"))
    authoritative_persisted_draft = bool(sheet_state.get("authoritative_persisted_draft"))
    base_evidence_run_id = str(sheet_state.get("base_evidence_run_id") or "").strip() or None
    active_evidence_run_id = str((evidence_run or {}).get("id") or "").strip() or None
    position_fallback_semantics_ready = position_column_mapping_service.candidate_resolution_uses_position_fallback(
        candidate_resolution
    )
    stale_authoritative_sheet = authoritative_sheet_suppresses_stale_evidence_issues(
        source=source,
        rows=rows,
        has_semantic_fields=has_semantic_fields,
        clean_saved_draft=clean_saved_draft,
        authoritative_persisted_draft=authoritative_persisted_draft,
        base_evidence_run_id=base_evidence_run_id,
        active_evidence_run_id=active_evidence_run_id,
    )
    effective_position_fallback_semantics_ready = bool(
        position_fallback_semantics_ready
        and not authoritative_persisted_draft
    )
    position_fallback_clears_numeric_warning = bool(
        effective_position_fallback_semantics_ready
        and not ocr_evidence_service.payload_has_high_risk_numeric_issues(evidence_payload)
    )
    if stale_authoritative_sheet:
        position_fallback_clears_numeric_warning = True

    if not facility:
        apply_blockers.append("facility_missing")
        confirm_blockers.append("facility_missing")
    if not week:
        apply_blockers.append("week_missing")
        confirm_blockers.append("week_missing")

    capabilities = (evidence_run or {}).get("capabilities_json") if isinstance(evidence_run, dict) else {}
    quantity_selected_via_user_choice = False
    if isinstance(capabilities, dict):
        if not capabilities.get("step2_view_ready") and not authoritative_persisted_draft:
            apply_blockers.append("evidence_view_unavailable")
            confirm_blockers.append("evidence_view_unavailable")
        if not capabilities.get("step2_edit_ready") and not authoritative_persisted_draft:
            apply_blockers.append("evidence_edit_unavailable")
            confirm_blockers.append("evidence_edit_unavailable")
        if (
            capabilities.get("semantic_shell_only")
            and not clean_saved_draft
            and not authoritative_persisted_draft
            and not effective_position_fallback_semantics_ready
        ):
            apply_blockers.append("semantic_shell_only")
            confirm_blockers.append("semantic_shell_only")
        if capabilities.get("recovery_required") and not clean_saved_draft and not authoritative_persisted_draft:
            apply_warnings.append("recovery_recommended")
            confirm_warnings.append("recovery_recommended")

    resolutions = (candidate_resolution or {}).get("resolutions") if isinstance(candidate_resolution, dict) else {}
    if isinstance(resolutions, dict):
        suppressed_decision_types = set()
        if str(sheet_state.get("facility_id") or "").strip():
            suppressed_decision_types.add("facility")
        if str(sheet_state.get("resolved_week_id") or "").strip():
            suppressed_decision_types.add("week")
        if clean_saved_draft or authoritative_persisted_draft:
            suppressed_decision_types |= _LAYOUT_RESOLUTION_TYPES
        gate_summary = candidate_resolution_service.summarize_resolution_gate(
            resolutions,
            suppress_decision_types=suppressed_decision_types,
        )
        for decision_type in gate_summary.get("choice_required_types") or []:
            apply_blockers.append(f"{decision_type}_choice_required")
            confirm_blockers.append(f"{decision_type}_choice_required")
        for decision_type in gate_summary.get("blocked_types") or []:
            apply_blockers.append(f"{decision_type}_unresolved")
            confirm_blockers.append(f"{decision_type}_unresolved")
        column_mapping = resolutions.get("column_mapping") if isinstance(resolutions.get("column_mapping"), dict) else None
        quantity = resolutions.get("quantity") if isinstance(resolutions.get("quantity"), dict) else None
        quantity_selected_via_user_choice = bool(isinstance(quantity, dict) and quantity.get("selected_via_user_choice"))
        if (
            isinstance(column_mapping, dict)
            and column_mapping.get("attention_required")
            and not column_mapping.get("selected_via_user_choice")
            and not clean_saved_draft
            and not authoritative_persisted_draft
        ):
            apply_warnings.append("column_mapping_review_required")
            confirm_warnings.append("column_mapping_review_required")
        if (
            isinstance(column_mapping, dict)
            and column_mapping.get("partial_quantity_mapping")
            and not clean_saved_draft
            and not authoritative_persisted_draft
        ):
            apply_blockers.append("sheet_quantity_column_unmapped")
            confirm_blockers.append("sheet_quantity_column_unmapped")
        if (
            isinstance(quantity, dict)
            and quantity.get("attention_required")
            and not quantity.get("selected_via_user_choice")
            and not clean_saved_draft
            and not authoritative_persisted_draft
        ):
            apply_warnings.append("quantity_review_required")
            confirm_warnings.append("quantity_review_required")
    if (
        isinstance(capabilities, dict)
        and capabilities.get("numeric_trust_low")
        and not quantity_selected_via_user_choice
        and not clean_saved_draft
        and not position_fallback_clears_numeric_warning
    ):
        apply_warnings.append("numeric_trust_low")
        confirm_warnings.append("numeric_trust_low")
    if not isinstance(rows, list) or len(rows) <= 0:
        apply_blockers.append("draft_rows_empty")
        confirm_blockers.append("draft_rows_empty")
    bypass_monthly_menu_object_block = _semantic_sheet_can_bypass_monthly_menu_object_block(
        rows=rows,
        has_semantic_fields=has_semantic_fields,
    )
    for menu_blocker in _menu_context_blockers(menu_context):
        if authoritative_persisted_draft:
            apply_warnings.append(menu_blocker)
            confirm_warnings.append(menu_blocker)
            continue
        if menu_blocker == "monthly_menu_object_missing" and bypass_monthly_menu_object_block:
            apply_warnings.append(menu_blocker)
            confirm_warnings.append(menu_blocker)
            continue
        apply_blockers.append(menu_blocker)
        confirm_blockers.append(menu_blocker)
    if source.startswith("ocr_table") and not has_semantic_fields:
        apply_warnings.append("ocr_table_fallback")
        confirm_warnings.append("ocr_table_fallback")

    if isinstance(sheet_gate, dict):
        apply_blockers.extend(
            filter_stale_issue_tokens(
                sheet_gate.get("apply_blockers") or [],
                source=source,
                clean_saved_draft=clean_saved_draft,
                position_fallback_semantics_ready=effective_position_fallback_semantics_ready,
                stale_authoritative_sheet=stale_authoritative_sheet,
            )
        )
        confirm_blockers.extend(
            filter_stale_issue_tokens(
                sheet_gate.get("confirm_blockers") or [],
                source=source,
                clean_saved_draft=clean_saved_draft,
                position_fallback_semantics_ready=effective_position_fallback_semantics_ready,
                stale_authoritative_sheet=stale_authoritative_sheet,
            )
        )
        confirm_warnings.extend(
            filter_stale_issue_tokens(
                sheet_gate.get("confirm_warnings") or [],
                source=source,
                clean_saved_draft=clean_saved_draft,
                position_fallback_semantics_ready=effective_position_fallback_semantics_ready,
                stale_authoritative_sheet=stale_authoritative_sheet,
            )
        )

    deduped_apply_blockers = _dedupe_tokens(apply_blockers)
    deduped_confirm_blockers = _dedupe_tokens(confirm_blockers)
    deduped_apply_warnings = _dedupe_tokens(apply_warnings)
    deduped_confirm_warnings = _dedupe_tokens(confirm_warnings)
    deduped_blockers = _dedupe_tokens(deduped_apply_blockers + deduped_confirm_blockers)
    deduped_warnings = _dedupe_tokens(deduped_apply_warnings + deduped_confirm_warnings)

    requires_human_review = gate_requires_human_review(
        apply_warnings=deduped_apply_warnings,
        confirm_warnings=deduped_confirm_warnings,
    )
    can_apply = gate_can_apply(
        apply_blockers=deduped_apply_blockers,
        apply_warnings=deduped_apply_warnings,
        confirm_warnings=deduped_confirm_warnings,
    )
    can_confirm = gate_can_confirm(
        confirm_blockers=deduped_confirm_blockers,
        confirm_warnings=deduped_confirm_warnings,
        apply_warnings=deduped_apply_warnings,
    )
    return {
        "can_apply": can_apply,
        "can_confirm": can_confirm,
        "requires_human_review": requires_human_review,
        "blockers": deduped_blockers,
        "warnings": deduped_warnings,
        "apply_blockers": deduped_apply_blockers,
        "confirm_blockers": deduped_confirm_blockers,
        "apply_warnings": deduped_apply_warnings,
        "confirm_warnings": deduped_confirm_warnings,
    }
