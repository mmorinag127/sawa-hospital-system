from __future__ import annotations

from typing import Any


def evaluate_apply_gate(
    *,
    order_payload: dict[str, Any] | None,
    evidence_run: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
    candidate_resolution: dict[str, Any] | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    facility = str((order_payload or {}).get("facility") or "").strip()
    week = str((order_payload or {}).get("week_value") or (order_payload or {}).get("week") or "").strip()

    if not facility:
        blockers.append("facility_missing")
    if not week:
        blockers.append("week_missing")

    capabilities = (evidence_run or {}).get("capabilities_json") if isinstance(evidence_run, dict) else {}
    if isinstance(capabilities, dict):
        if not capabilities.get("step2_view_ready"):
            blockers.append("evidence_view_unavailable")
        if not capabilities.get("step2_edit_ready"):
            blockers.append("evidence_edit_unavailable")
        if capabilities.get("recovery_required"):
            warnings.append("recovery_recommended")

    resolutions = (candidate_resolution or {}).get("resolutions") if isinstance(candidate_resolution, dict) else {}
    if isinstance(resolutions, dict):
        for decision_type, resolution in resolutions.items():
            if not isinstance(resolution, dict):
                continue
            if resolution.get("requires_user_choice"):
                blockers.append(f"{decision_type}_choice_required")
            if resolution.get("blocked") and not resolution.get("resolved_value"):
                blockers.append(f"{decision_type}_unresolved")
        column_mapping = resolutions.get("column_mapping") if isinstance(resolutions.get("column_mapping"), dict) else None
        quantity = resolutions.get("quantity") if isinstance(resolutions.get("quantity"), dict) else None
        if (
            isinstance(column_mapping, dict)
            and column_mapping.get("attention_required")
            and not column_mapping.get("selected_via_user_choice")
        ):
            warnings.append("column_mapping_review_required")
        if (
            isinstance(quantity, dict)
            and quantity.get("attention_required")
            and not quantity.get("selected_via_user_choice")
        ):
            warnings.append("quantity_review_required")

    draft_payload = None
    if isinstance(draft_sheet, dict):
        draft_payload = draft_sheet.get("draft_sheet_json") if isinstance(draft_sheet.get("draft_sheet_json"), dict) else draft_sheet
    rows = draft_payload.get("rows") if isinstance(draft_payload, dict) else None
    if not isinstance(rows, list) or len(rows) <= 0:
        blockers.append("draft_rows_empty")

    source = str((draft_payload or {}).get("source") or "").strip()
    if source.startswith("ocr_table"):
        warnings.append("ocr_table_fallback")

    deduped_blockers: list[str] = []
    for item in blockers:
        if item not in deduped_blockers:
            deduped_blockers.append(item)
    deduped_warnings: list[str] = []
    for item in warnings:
        if item not in deduped_warnings:
            deduped_warnings.append(item)

    can_apply = not deduped_blockers
    can_confirm = can_apply and "recovery_recommended" not in deduped_warnings
    return {
        "can_apply": can_apply,
        "can_confirm": can_confirm,
        "blockers": deduped_blockers,
        "warnings": deduped_warnings,
    }
