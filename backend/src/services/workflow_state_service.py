from __future__ import annotations

from datetime import datetime
from typing import Any

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_workflow_state import OrderWorkflowState
from src.services import apply_gate_service, candidate_resolution_service, critical_decision_service, draft_sheet_service, ocr_evidence_service


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
    if order_status == "確定":
        return "confirmed", "確定済み", "none", blockers, warnings, "high"
    if identity_choice_required:
        return "identity_choice_required", "施設または週の候補選択が必要です", "resolve_identity_choice", blockers, warnings, "medium"
    if layout_choice_required:
        return "layout_choice_required", "OCR候補の選択が必要です", "resolve_layout_choice", blockers, warnings, "medium"
    if not isinstance(evidence_run, dict):
        return "uploaded", "OCR証拠の生成待ちです", "run_ocr_pipeline", blockers or ["evidence_missing"], warnings, "low"
    capabilities = evidence_run.get("capabilities_json") if isinstance(evidence_run, dict) else {}
    if isinstance(capabilities, dict) and capabilities.get("recovery_required"):
        return "recovery_required", "OCR基盤の復旧が必要です", "recover_ocr_evidence", blockers or ["evidence_recovery_required"], warnings, "low"
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
    evidence_payload = evidence_run.get("payload_json") if isinstance(evidence_run, dict) else None
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
    )
    candidate_resolution, _unresolved_types = _merge_selected_decisions_into_resolution(
        candidate_resolution,
        synced_decisions,
    )
    apply_gate = apply_gate_service.evaluate_apply_gate(
        order_payload=order_payload,
        evidence_run=evidence_run,
        draft_sheet=draft_sheet,
        candidate_resolution=candidate_resolution,
    )
    state, headline, primary_action, blockers, warnings, confidence_band = _derive_state(
        order_payload=order_payload,
        evidence_run=evidence_run,
        draft_sheet=draft_sheet,
        candidate_resolution=candidate_resolution,
        apply_gate=apply_gate,
    )
    secondary_actions = []
    if state in {"uploaded", "recovery_required"}:
        secondary_actions = ["rerun_yomitoku", "llm_reparse"]
    elif state in {"draft_ready", "draft_blocked", "apply_ready", "review_required"}:
        secondary_actions = ["save_draft", "llm_reparse"]
    elif state in {"identity_choice_required", "layout_choice_required"}:
        secondary_actions = ["select_candidate", "save_draft"]

    with session_scope() as session:
        row = session.get(OrderWorkflowState, normalized_order_id)
        if not row:
            row = OrderWorkflowState(order_id=normalized_order_id)
            session.add(row)
        row.evidence_run_id = str((evidence_run or {}).get("id") or "").strip() or None
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
    return serialized
