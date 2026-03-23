from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_workflow_state import OrderWorkflowState
from src.services.ocr_job_service import describe_job_state, get_job as get_ocr_job
from src.services import (
    apply_gate_service,
    candidate_resolution_service,
    critical_decision_service,
    draft_sheet_service,
    menu_service,
    ocr_evidence_service,
)


Base.metadata.create_all(bind=engine)


def _serialize(row: OrderWorkflowState) -> dict[str, Any]:
    return {
        "order_id": row.order_id,
        "evidence_run_id": row.evidence_run_id,
        "draft_id": row.draft_id,
        "confirmed_snapshot_id": row.confirmed_snapshot_id,
        "state": row.state,
        "headline": row.headline,
        "primary_action": row.primary_action,
        "secondary_actions_json": list(row.secondary_actions_json or []),
        "blockers_json": list(row.blockers_json or []),
        "warnings_json": list(row.warnings_json or []),
        "confidence_band": row.confidence_band,
        "last_transition_at": row.last_transition_at.isoformat() if isinstance(row.last_transition_at, datetime) else None,
    }


def get_workflow_state(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        row = session.get(OrderWorkflowState, normalized_order_id)
        if not row:
            return None
        return _serialize(row)


def _load_order_payload(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        order = session.get(Order, normalized_order_id)
        if not order:
            return None
        return {
            "id": order.id,
            "facility": order.facility_code,
            "week": order.week_code,
            "week_value": order.week_code,
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
        }
    menu = menu_service.get_menu_for_facility(month_id, facility) if facility else menu_service.get_menu(month_id)
    if not isinstance(menu, dict):
        return {
            "month_id": month_id,
            "weekly_menu_missing": True,
            "menu_entries_missing": False,
            "entries_count": 0,
        }
    raw_entries = menu.get("entries")
    entries = [item for item in raw_entries if isinstance(item, dict)] if isinstance(raw_entries, list) else []
    if isinstance(week_start, date) and isinstance(week_end, date):
        entries = [
            item
            for item in entries
            if (
                isinstance(_normalize_entry_date(item.get("menu_date")), date)
                and week_start <= _normalize_entry_date(item.get("menu_date")) <= week_end
            )
        ]
    return {
        "month_id": month_id,
        "weekly_menu_missing": False,
        "menu_entries_missing": len(entries) <= 0,
        "entries_count": len(entries),
    }


def _build_sheet_gate(*, order_id: str, order_payload: dict[str, Any] | None, draft_sheet: dict[str, Any] | None) -> dict[str, Any]:
    draft_payload = (
        draft_sheet.get("draft_sheet_json")
        if isinstance(draft_sheet, dict) and isinstance(draft_sheet.get("draft_sheet_json"), dict)
        else (draft_sheet if isinstance(draft_sheet, dict) else {})
    )
    rows = draft_payload.get("rows") if isinstance(draft_payload, dict) else None
    source = str((draft_payload or {}).get("source") or "").strip()
    blockers = []
    warnings = []
    if isinstance(draft_sheet, dict):
        blockers = [str(item or "").strip() for item in (draft_sheet.get("blockers_json") or []) if str(item or "").strip()]
        warnings = [str(item or "").strip() for item in (draft_sheet.get("warnings_json") or []) if str(item or "").strip()]
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
    reparse_job = get_ocr_job(f"OCR-{order_id}")
    reparse_state = describe_job_state(reparse_job if isinstance(reparse_job, dict) else None)
    return apply_gate_service.evaluate_sheet_gate(
        rows=rows if isinstance(rows, list) else None,
        source=source,
        blockers=blockers,
        warnings=warnings,
        draft_newer_than_lines=draft_newer_than_lines,
        auto_apply_blocked="auto_apply_blocked" in blockers or "auto_apply_blocked" in warnings,
        reparse_status=reparse_state.get("status"),
    )


def _latest_evidence_is_new_candidate(
    evidence_run: dict[str, Any] | None,
    draft_sheet: dict[str, Any] | None,
) -> bool:
    latest_evidence_run_id = str((evidence_run or {}).get("id") or "").strip() or None
    draft_base_evidence_run_id = (
        str((draft_sheet or {}).get("base_evidence_run_id") or "").strip() or None
        if isinstance(draft_sheet, dict)
        else None
    )
    if not latest_evidence_run_id or not draft_base_evidence_run_id:
        return False
    return latest_evidence_run_id != draft_base_evidence_run_id


def _infer_reparse_request_mode(
    reparse_job: dict[str, Any] | None,
    reparse_state: dict[str, Any] | None,
) -> str:
    if not isinstance(reparse_job, dict):
        return ""
    metrics = reparse_job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    direct_mode = (
        str(metrics.get("request_mode") or metrics.get("rerun_mode") or "").strip().lower()
    )
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
    candidate_resolution["requires_user_choice"] = bool(candidate_resolution["critical_choices"])
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
    has_new_candidate: bool = False,
) -> tuple[str, str, str | None, list[str], list[str], str]:
    blockers = list((apply_gate or {}).get("blockers") or [])
    warnings = list((apply_gate or {}).get("warnings") or [])
    order_status = str((order_payload or {}).get("status") or "").strip()
    resolutions = (candidate_resolution or {}).get("resolutions") if isinstance(candidate_resolution, dict) else {}
    unresolved_choice_types = {
        str(key).strip()
        for key, value in (resolutions or {}).items()
        if isinstance(value, dict) and value.get("requires_user_choice")
    }
    identity_choice_required = bool(unresolved_choice_types & {"facility", "week"})
    layout_choice_required = bool(unresolved_choice_types & {"template", "column_mapping", "quantity"})
    attention_required = bool(
        isinstance(candidate_resolution, dict)
        and (
            candidate_resolution.get("attention_required")
            or "column_mapping_review_required" in warnings
            or "quantity_review_required" in warnings
        )
    )
    quantity_resolution = resolutions.get("quantity") if isinstance(resolutions, dict) else None
    quantity_selected_via_user_choice = bool(
        isinstance(quantity_resolution, dict) and quantity_resolution.get("selected_via_user_choice")
    )
    capabilities = evidence_run.get("capabilities_json") if isinstance(evidence_run, dict) else {}
    semantic_shell_only = bool(isinstance(capabilities, dict) and capabilities.get("semantic_shell_only"))
    numeric_trust_low = bool(
        isinstance(capabilities, dict)
        and capabilities.get("numeric_trust_low")
        and not quantity_selected_via_user_choice
    )
    reparse_status = str((reparse_state or {}).get("status") or "").strip().lower()
    normalized_reparse_request_mode = str(reparse_request_mode or "").strip().lower()
    if order_status == "確定":
        return "confirmed", "確定済み", "none", blockers, warnings, "high"
    if normalized_reparse_request_mode == "ocr_rerun" and reparse_status in {"running", "pending"}:
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
    if has_new_candidate:
        return "new_evidence_available", "新しいOCR候補があります。切替えるか選んでください", "switch_to_new_evidence", blockers, warnings, "medium"
    if identity_choice_required:
        return "identity_choice_required", "施設または週の候補選択が必要です", "resolve_identity_choice", blockers, warnings, "medium"
    if layout_choice_required:
        return "layout_choice_required", "OCR候補の選択が必要です", "resolve_layout_choice", blockers, warnings, "medium"
    if not isinstance(evidence_run, dict):
        return "uploaded", "OCR証拠の生成待ちです", "run_ocr_pipeline", blockers or ["evidence_missing"], warnings, "low"
    if isinstance(capabilities, dict) and capabilities.get("recovery_required"):
        return "recovery_required", "OCR基盤の復旧が必要です", "recover_ocr_evidence", blockers or ["evidence_recovery_required"], warnings, "low"
    semantic_only_blockers = [
        item
        for item in blockers
        if item not in {"semantic_shell_only", "evidence_edit_unavailable", "draft_rows_empty"}
    ]
    if (semantic_shell_only or numeric_trust_low) and not semantic_only_blockers:
        return "semantic_shell_only", "メニュー枠はありますが、数量はまだ信用できません", "rerun_ocr_pipeline", blockers, warnings, "low"
    if isinstance(draft_sheet, dict):
        if attention_required:
            return "review_required", "高リスクなOCR候補を確認してください", "review_critical_cells", blockers, warnings, "medium"
        if (apply_gate or {}).get("can_apply"):
            return "apply_ready", "下書きを明細へ反映できます", "apply_draft", blockers, warnings, "medium"
        if blockers:
            return "draft_blocked", "下書きはありますが、反映前に条件の解消が必要です", "resolve_blockers", blockers, warnings, "medium"
        return "draft_ready", "下書きを確認して必要なら修正してください", "edit_draft", blockers, warnings, "medium"
    return "evidence_ready", "OCR証拠を確認できます", "open_draft", blockers, warnings, "medium"


def refresh_workflow_state(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    order_payload = _load_order_payload(normalized_order_id)
    if not isinstance(order_payload, dict):
        return None
    evidence_run = ocr_evidence_service.get_latest_evidence_run(normalized_order_id)
    draft_sheet = draft_sheet_service.get_latest_sheet_draft(normalized_order_id)
    if not isinstance(draft_sheet, dict):
        initial = draft_sheet_service.build_initial_sheet_draft(normalized_order_id)
        if isinstance(initial, dict):
            draft_sheet = {
                "id": None,
                "order_id": normalized_order_id,
                "base_evidence_run_id": initial.get("base_evidence_run_id"),
                "draft_sheet_json": initial,
                "draft_state": "draft_ready",
                "blockers_json": [],
                "warnings_json": [],
            }
    active_evidence_run = _resolve_active_evidence_run(evidence_run, draft_sheet)
    has_new_candidate = _latest_evidence_is_new_candidate(evidence_run, draft_sheet)
    evidence_payload = active_evidence_run.get("payload_json") if isinstance(active_evidence_run, dict) else None
    reparse_job = get_ocr_job(f"OCR-{normalized_order_id}")
    reparse_state = describe_job_state(reparse_job if isinstance(reparse_job, dict) else None)
    reparse_request_mode = _infer_reparse_request_mode(
        reparse_job if isinstance(reparse_job, dict) else None,
        reparse_state,
    )
    if isinstance(reparse_state, dict) and reparse_request_mode:
        reparse_state = {**reparse_state, "request_mode": reparse_request_mode}
    candidate_resolution = candidate_resolution_service.resolve_order_candidates(
        order_id=normalized_order_id,
        facility_code=str(order_payload.get("facility") or "").strip() or None,
        week_code=str(order_payload.get("week_value") or order_payload.get("week") or "").strip() or None,
        received_at=order_payload.get("received_at"),
        evidence_payload=evidence_payload if isinstance(evidence_payload, dict) else None,
    )
    synced_decisions = critical_decision_service.sync_pending_decisions(
        normalized_order_id,
        list(candidate_resolution.get("critical_choices") or []),
        base_evidence_run_id=str((active_evidence_run or {}).get("id") or "").strip() or None,
    )
    candidate_resolution, _unresolved_types = _merge_selected_decisions_into_resolution(
        candidate_resolution,
        synced_decisions,
    )
    apply_gate = apply_gate_service.evaluate_apply_gate(
        order_payload=order_payload,
        evidence_run=active_evidence_run,
        draft_sheet=draft_sheet,
        candidate_resolution=candidate_resolution,
        menu_context=_build_menu_context(
            facility_code=str(order_payload.get("facility") or "").strip() or None,
            week_code=str(order_payload.get("week_value") or order_payload.get("week") or "").strip() or None,
        ),
        sheet_gate=_build_sheet_gate(
            order_id=normalized_order_id,
            order_payload=order_payload,
            draft_sheet=draft_sheet,
        ),
    )
    state, headline, primary_action, blockers, warnings, confidence_band = _derive_state(
        order_payload=order_payload,
        evidence_run=active_evidence_run,
        draft_sheet=draft_sheet,
        candidate_resolution=candidate_resolution,
        apply_gate=apply_gate,
        reparse_state=reparse_state,
        reparse_request_mode=reparse_request_mode,
        has_new_candidate=has_new_candidate,
    )
    secondary_actions = []
    if state in {"uploaded", "recovery_required"}:
        secondary_actions = ["rerun_yomitoku", "llm_reparse"]
    elif state == "semantic_shell_only":
        secondary_actions = ["recover_ocr_evidence", "save_draft"]
    elif state == "rerun_failed_keep_current":
        secondary_actions = ["keep_current_draft", "recover_ocr_evidence", "llm_reparse"]
    elif state == "new_evidence_available":
        secondary_actions = ["keep_current_draft", "llm_reparse"]
    elif state == "rerun_in_progress":
        secondary_actions = ["wait"]
    elif state in {"draft_ready", "draft_blocked", "apply_ready", "review_required"}:
        secondary_actions = ["rerun_yomitoku", "save_draft", "llm_reparse"]
    elif state in {"identity_choice_required", "layout_choice_required"}:
        secondary_actions = ["select_candidate", "save_draft"]

    with session_scope() as session:
        row = session.get(OrderWorkflowState, normalized_order_id)
        if not row:
            row = OrderWorkflowState(order_id=normalized_order_id)
            session.add(row)
        row.evidence_run_id = str((active_evidence_run or {}).get("id") or "").strip() or None
        row.draft_id = str((draft_sheet or {}).get("id") or "").strip() or None
        row.confirmed_snapshot_id = _latest_confirmed_snapshot_id(normalized_order_id)
        row.state = state
        row.headline = headline
        row.primary_action = primary_action
        row.secondary_actions_json = secondary_actions
        row.blockers_json = blockers
        row.warnings_json = warnings
        row.confidence_band = confidence_band
        row.last_transition_at = datetime.utcnow()
        session.flush()
        serialized = _serialize(row)

    serialized["candidate_resolution"] = candidate_resolution
    serialized["critical_decisions"] = synced_decisions
    serialized["apply_gate"] = apply_gate
    serialized["candidate_evidence_run_id"] = (
        str((evidence_run or {}).get("id") or "").strip() or None
        if _latest_evidence_is_new_candidate(evidence_run, draft_sheet)
        else None
    )
    serialized["active_evidence_run_id"] = str((active_evidence_run or {}).get("id") or "").strip() or None
    serialized["reparse_state"] = reparse_state
    return serialized
