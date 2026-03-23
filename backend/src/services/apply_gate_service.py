from __future__ import annotations

from typing import Any


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
    "ocr_evidence_recovery_required",
    "template_resolution_blocked",
}


def evaluate_sheet_gate(
    *,
    rows: list[Any] | None,
    source: str | None,
    blockers: list[str] | None,
    warnings: list[str] | None,
    draft_newer_than_lines: bool = False,
    auto_apply_blocked: bool = False,
    reparse_status: str | None = None,
) -> dict[str, list[str]]:
    apply_blockers: list[str] = []
    confirm_blockers: list[str] = []
    confirm_warnings: list[str] = []

    normalized_blockers = [str(item or "").strip() for item in (blockers or []) if str(item or "").strip()]
    normalized_warnings = [str(item or "").strip() for item in (warnings or []) if str(item or "").strip()]
    normalized_source = str(source or "").strip()
    normalized_reparse_status = str(reparse_status or "").strip().lower()

    if "sheet_weekly_menu_missing" in normalized_warnings:
        apply_blockers.append("weekly_menu_missing")
        confirm_blockers.append("weekly_menu_missing")
    for blocker in normalized_blockers:
        apply_blockers.append(blocker)
        confirm_blockers.append(blocker)
    for warning in normalized_warnings:
        if warning in _RECOVERABLE_BLOCKING_WARNINGS:
            apply_blockers.append(warning)
            confirm_blockers.append(warning)
    if draft_newer_than_lines:
        confirm_warnings.append("draft_newer_than_lines")
    if auto_apply_blocked:
        confirm_blockers.append("auto_apply_blocked")
    if not isinstance(rows, list) or len(rows) <= 0:
        apply_blockers.append("rows_empty")
    if "sheet_ocr_review_required" in normalized_warnings:
        confirm_warnings.append("ocr_review_required")
    if normalized_source.startswith("ocr_table"):
        confirm_warnings.append("ocr_table_fallback")
    if normalized_reparse_status == "stalled":
        apply_blockers.append("reparse_stale")
        confirm_blockers.append("reparse_stale")

    def _dedupe(items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            token = str(item or "").strip()
            if token and token not in seen:
                seen.add(token)
                deduped.append(token)
        return deduped

    return {
        "apply_blockers": _dedupe(apply_blockers),
        "confirm_blockers": _dedupe(confirm_blockers),
        "confirm_warnings": _dedupe(confirm_warnings),
    }


def evaluate_apply_gate(
    *,
    order_payload: dict[str, Any] | None,
    evidence_run: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
    candidate_resolution: dict[str, Any] | None,
    menu_context: dict[str, Any] | None = None,
    sheet_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    apply_blockers: list[str] = []
    confirm_blockers: list[str] = []
    apply_warnings: list[str] = []
    confirm_warnings: list[str] = []

    facility = str((order_payload or {}).get("facility") or "").strip()
    week = str((order_payload or {}).get("week_value") or (order_payload or {}).get("week") or "").strip()

    if not facility:
        apply_blockers.append("facility_missing")
        confirm_blockers.append("facility_missing")
    if not week:
        apply_blockers.append("week_missing")
        confirm_blockers.append("week_missing")

    if isinstance(menu_context, dict):
        if menu_context.get("weekly_menu_missing"):
            apply_blockers.append("weekly_menu_missing")
            confirm_blockers.append("weekly_menu_missing")
        elif menu_context.get("menu_entries_missing"):
            apply_blockers.append("menu_entries_missing")
            confirm_blockers.append("menu_entries_missing")

    capabilities = (evidence_run or {}).get("capabilities_json") if isinstance(evidence_run, dict) else {}
    quantity_selected_via_user_choice = False
    if isinstance(capabilities, dict):
        if not capabilities.get("step2_view_ready"):
            apply_blockers.append("evidence_view_unavailable")
            confirm_blockers.append("evidence_view_unavailable")
        if not capabilities.get("step2_edit_ready"):
            apply_blockers.append("evidence_edit_unavailable")
            confirm_blockers.append("evidence_edit_unavailable")
        if capabilities.get("semantic_shell_only"):
            apply_blockers.append("semantic_shell_only")
            confirm_blockers.append("semantic_shell_only")
        if capabilities.get("recovery_required"):
            apply_warnings.append("recovery_recommended")
            confirm_warnings.append("recovery_recommended")

    resolutions = (candidate_resolution or {}).get("resolutions") if isinstance(candidate_resolution, dict) else {}
    if isinstance(resolutions, dict):
        for decision_type, resolution in resolutions.items():
            if not isinstance(resolution, dict):
                continue
            if resolution.get("requires_user_choice"):
                apply_blockers.append(f"{decision_type}_choice_required")
                confirm_blockers.append(f"{decision_type}_choice_required")
            if resolution.get("blocked") and not resolution.get("resolved_value"):
                apply_blockers.append(f"{decision_type}_unresolved")
                confirm_blockers.append(f"{decision_type}_unresolved")
        column_mapping = resolutions.get("column_mapping") if isinstance(resolutions.get("column_mapping"), dict) else None
        quantity = resolutions.get("quantity") if isinstance(resolutions.get("quantity"), dict) else None
        quantity_selected_via_user_choice = bool(isinstance(quantity, dict) and quantity.get("selected_via_user_choice"))
        if (
            isinstance(column_mapping, dict)
            and column_mapping.get("attention_required")
            and not column_mapping.get("selected_via_user_choice")
        ):
            apply_warnings.append("column_mapping_review_required")
            confirm_warnings.append("column_mapping_review_required")
        if (
            isinstance(quantity, dict)
            and quantity.get("attention_required")
            and not quantity.get("selected_via_user_choice")
        ):
            apply_warnings.append("quantity_review_required")
            confirm_warnings.append("quantity_review_required")
    if isinstance(capabilities, dict) and capabilities.get("numeric_trust_low") and not quantity_selected_via_user_choice:
        apply_warnings.append("numeric_trust_low")
        confirm_warnings.append("numeric_trust_low")

    draft_payload = None
    if isinstance(draft_sheet, dict):
        draft_payload = draft_sheet.get("draft_sheet_json") if isinstance(draft_sheet.get("draft_sheet_json"), dict) else draft_sheet
    rows = draft_payload.get("rows") if isinstance(draft_payload, dict) else None
    if not isinstance(rows, list) or len(rows) <= 0:
        apply_blockers.append("draft_rows_empty")
        confirm_blockers.append("draft_rows_empty")

    source = str((draft_payload or {}).get("source") or "").strip()
    if source.startswith("ocr_table"):
        apply_warnings.append("ocr_table_fallback")
        confirm_warnings.append("ocr_table_fallback")

    if isinstance(sheet_gate, dict):
        apply_blockers.extend([str(item or "").strip() for item in (sheet_gate.get("apply_blockers") or []) if str(item or "").strip()])
        confirm_blockers.extend([str(item or "").strip() for item in (sheet_gate.get("confirm_blockers") or []) if str(item or "").strip()])
        confirm_warnings.extend([str(item or "").strip() for item in (sheet_gate.get("confirm_warnings") or []) if str(item or "").strip()])

    def _dedupe(items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            token = str(item or "").strip()
            if token and token not in seen:
                seen.add(token)
                deduped.append(token)
        return deduped

    deduped_apply_blockers = _dedupe(apply_blockers)
    deduped_confirm_blockers = _dedupe(confirm_blockers)
    deduped_apply_warnings = _dedupe(apply_warnings)
    deduped_confirm_warnings = _dedupe(confirm_warnings)
    deduped_blockers = _dedupe(deduped_apply_blockers + deduped_confirm_blockers)
    deduped_warnings = _dedupe(deduped_apply_warnings + deduped_confirm_warnings)

    can_apply = not deduped_apply_blockers
    can_confirm = not deduped_confirm_blockers and "recovery_recommended" not in deduped_confirm_warnings
    return {
        "can_apply": can_apply,
        "can_confirm": can_confirm,
        "blockers": deduped_blockers,
        "warnings": deduped_warnings,
        "apply_blockers": deduped_apply_blockers,
        "confirm_blockers": deduped_confirm_blockers,
        "apply_warnings": deduped_apply_warnings,
        "confirm_warnings": deduped_confirm_warnings,
    }
