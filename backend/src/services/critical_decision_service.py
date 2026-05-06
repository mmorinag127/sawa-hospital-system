from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from src.db import session_scope
from src.models.order_critical_decision import OrderCriticalDecision

INTERNAL_CANDIDATE_EVIDENCE_ACK_DECISION_TYPE = "_candidate_evidence_ack"


def _serialize_decision(item: OrderCriticalDecision) -> dict[str, Any]:
    candidate_set = item.candidate_set_json if isinstance(item.candidate_set_json, dict) else {}
    return {
        "id": item.id,
        "order_id": item.order_id,
        "decision_type": item.decision_type,
        "candidate_set_json": candidate_set,
        "base_evidence_run_id": str(candidate_set.get("base_evidence_run_id") or "").strip() or None,
        "selected_value": item.selected_value,
        "selected_by": item.selected_by,
        "selected_at": item.selected_at.isoformat() if isinstance(item.selected_at, datetime) else None,
    }


def is_internal_decision_type(decision_type: str | None) -> bool:
    return str(decision_type or "").strip() == INTERNAL_CANDIDATE_EVIDENCE_ACK_DECISION_TYPE


def list_decisions(order_id: str) -> list[dict[str, Any]]:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return []
    with session_scope() as session:
        rows = (
            session.query(OrderCriticalDecision)
            .filter(OrderCriticalDecision.order_id == normalized_order_id)
            .order_by(OrderCriticalDecision.selected_at.desc(), OrderCriticalDecision.id.desc())
            .all()
        )
        return [_serialize_decision(row) for row in rows]


def get_latest_decision(order_id: str, decision_type: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    normalized_type = str(decision_type or "").strip()
    if not normalized_order_id or not normalized_type:
        return None
    with session_scope() as session:
        row = (
            session.query(OrderCriticalDecision)
            .filter(
                OrderCriticalDecision.order_id == normalized_order_id,
                OrderCriticalDecision.decision_type == normalized_type,
            )
            .order_by(OrderCriticalDecision.selected_at.desc(), OrderCriticalDecision.id.desc())
            .first()
        )
        return _serialize_decision(row) if row else None


def sync_pending_decisions(
    order_id: str,
    critical_choices: list[dict[str, Any]] | None,
    *,
    base_evidence_run_id: str | None = None,
) -> list[dict[str, Any]]:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return []
    normalized_base_evidence_run_id = str(base_evidence_run_id or "").strip() or None

    def _normalize_candidate_set(item: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(item)
        if normalized_base_evidence_run_id:
            normalized["base_evidence_run_id"] = normalized_base_evidence_run_id
        elif "base_evidence_run_id" in normalized:
            normalized["base_evidence_run_id"] = str(normalized.get("base_evidence_run_id") or "").strip() or None
        return normalized

    desired = {
        str(item.get("decision_type") or "").strip(): _normalize_candidate_set(item)
        for item in (critical_choices or [])
        if isinstance(item, dict) and str(item.get("decision_type") or "").strip()
    }
    with session_scope() as session:
        existing_rows = (
            session.query(OrderCriticalDecision)
            .filter(OrderCriticalDecision.order_id == normalized_order_id)
            .all()
        )
        preserved: dict[str, dict[str, Any]] = {}
        for row in existing_rows:
            if is_internal_decision_type(row.decision_type) and row.decision_type not in desired:
                continue
            current_candidate_set = row.candidate_set_json if isinstance(row.candidate_set_json, dict) else {}
            preserved[row.decision_type] = {
                "id": row.id,
                "selected_value": row.selected_value,
                "selected_by": row.selected_by,
                "selected_at": row.selected_at,
                "base_evidence_run_id": str(current_candidate_set.get("base_evidence_run_id") or "").strip() or None,
            }
            session.delete(row)
        session.flush()
        for decision_type, candidate_set in desired.items():
            previous = preserved.get(decision_type) or {}
            next_base_evidence_run_id = str(candidate_set.get("base_evidence_run_id") or "").strip() or None
            previous_base_evidence_run_id = str(previous.get("base_evidence_run_id") or "").strip() or None
            keep_selection = bool(
                previous.get("selected_value")
                and (
                    (
                        not previous_base_evidence_run_id
                        and not next_base_evidence_run_id
                    )
                    or (
                        previous_base_evidence_run_id
                        and previous_base_evidence_run_id == next_base_evidence_run_id
                    )
                )
            )
            session.add(
                OrderCriticalDecision(
                    id=str(previous.get("id") or f"OCD{uuid4().hex[:12]}"),
                    order_id=normalized_order_id,
                    decision_type=decision_type,
                    candidate_set_json=candidate_set,
                    selected_value=(str(previous.get("selected_value") or "").strip() or None) if keep_selection else None,
                    selected_by=(str(previous.get("selected_by") or "").strip() or None) if keep_selection else None,
                    selected_at=(
                        previous.get("selected_at")
                        if keep_selection and isinstance(previous.get("selected_at"), datetime)
                        else datetime.utcnow()
                    ),
                )
            )
        session.flush()
        rows = (
            session.query(OrderCriticalDecision)
            .filter(OrderCriticalDecision.order_id == normalized_order_id)
            .order_by(OrderCriticalDecision.selected_at.desc(), OrderCriticalDecision.id.desc())
            .all()
        )
        return [_serialize_decision(row) for row in rows]


def project_pending_decisions(
    order_id: str,
    critical_choices: list[dict[str, Any]] | None,
    *,
    base_evidence_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the decision projection without creating/deleting decision rows."""

    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return []
    normalized_base_evidence_run_id = str(base_evidence_run_id or "").strip() or None

    desired: dict[str, dict[str, Any]] = {}
    for item in critical_choices or []:
        if not isinstance(item, dict):
            continue
        decision_type = str(item.get("decision_type") or "").strip()
        if not decision_type:
            continue
        candidate_set = dict(item)
        if normalized_base_evidence_run_id:
            candidate_set["base_evidence_run_id"] = normalized_base_evidence_run_id
        elif "base_evidence_run_id" in candidate_set:
            candidate_set["base_evidence_run_id"] = str(candidate_set.get("base_evidence_run_id") or "").strip() or None
        desired[decision_type] = candidate_set

    existing_by_type = {
        str(item.get("decision_type") or "").strip(): item
        for item in list_decisions(normalized_order_id)
        if isinstance(item, dict) and str(item.get("decision_type") or "").strip()
    }
    projected: list[dict[str, Any]] = []
    for decision_type, candidate_set in desired.items():
        previous = existing_by_type.get(decision_type) or {}
        next_base_evidence_run_id = str(candidate_set.get("base_evidence_run_id") or "").strip() or None
        previous_base_evidence_run_id = str(previous.get("base_evidence_run_id") or "").strip() or None
        keep_selection = bool(
            previous.get("selected_value")
            and (
                (
                    not previous_base_evidence_run_id
                    and not next_base_evidence_run_id
                )
                or (
                    previous_base_evidence_run_id
                    and previous_base_evidence_run_id == next_base_evidence_run_id
                )
            )
        )
        selected_value = str(previous.get("selected_value") or "").strip() or None if keep_selection else None
        selected_by = str(previous.get("selected_by") or "").strip() or None if keep_selection else None
        selected_at = previous.get("selected_at") if keep_selection else None
        projected.append(
            {
                "id": str(previous.get("id") or f"pending:{decision_type}").strip(),
                "order_id": normalized_order_id,
                "decision_type": decision_type,
                "candidate_set_json": candidate_set,
                "base_evidence_run_id": next_base_evidence_run_id,
                "selected_value": selected_value,
                "selected_by": selected_by,
                "selected_at": selected_at,
            }
        )
    return projected


def acknowledge_candidate_evidence(
    order_id: str,
    candidate_evidence_run_id: str,
    *,
    selected_by: str | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    normalized_candidate_id = str(candidate_evidence_run_id or "").strip()
    if not normalized_order_id or not normalized_candidate_id:
        return None
    with session_scope() as session:
        row = (
            session.query(OrderCriticalDecision)
            .filter(
                OrderCriticalDecision.order_id == normalized_order_id,
                OrderCriticalDecision.decision_type == INTERNAL_CANDIDATE_EVIDENCE_ACK_DECISION_TYPE,
            )
            .order_by(OrderCriticalDecision.selected_at.desc(), OrderCriticalDecision.id.desc())
            .first()
        )
        if not row:
            row = OrderCriticalDecision(
                id=f"OCD{uuid4().hex[:12]}",
                order_id=normalized_order_id,
                decision_type=INTERNAL_CANDIDATE_EVIDENCE_ACK_DECISION_TYPE,
                candidate_set_json={},
            )
            session.add(row)
        row.candidate_set_json = {"base_evidence_run_id": normalized_candidate_id}
        row.selected_value = normalized_candidate_id
        row.selected_by = str(selected_by or "").strip() or None
        row.selected_at = datetime.utcnow()
        session.flush()
        return _serialize_decision(row)


def get_acknowledged_candidate_evidence_run_id(order_id: str) -> str | None:
    decision = get_acknowledged_candidate_evidence_decision(order_id)
    if not isinstance(decision, dict):
        return None
    return str(decision.get("selected_value") or "").strip() or None


def get_acknowledged_candidate_evidence_decision(order_id: str) -> dict[str, Any] | None:
    return get_latest_decision(order_id, INTERNAL_CANDIDATE_EVIDENCE_ACK_DECISION_TYPE)


def choose_decision(
    order_id: str,
    decision_type: str,
    selected_value: str,
    *,
    selected_by: str | None = None,
    current_evidence_run_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    normalized_type = str(decision_type or "").strip()
    normalized_value = str(selected_value or "").strip()
    if not normalized_order_id or not normalized_type or not normalized_value:
        return None
    with session_scope() as session:
        row = (
            session.query(OrderCriticalDecision)
            .filter(
                OrderCriticalDecision.order_id == normalized_order_id,
                OrderCriticalDecision.decision_type == normalized_type,
            )
            .order_by(OrderCriticalDecision.selected_at.desc(), OrderCriticalDecision.id.desc())
            .first()
        )
        if not row:
            return None
        if current_evidence_run_id is not None:
            candidate_set = row.candidate_set_json if isinstance(row.candidate_set_json, dict) else {}
            decision_evidence_run_id = str(candidate_set.get("base_evidence_run_id") or "").strip() or None
            normalized_current_evidence_run_id = str(current_evidence_run_id or "").strip() or None
            if decision_evidence_run_id and normalized_current_evidence_run_id and decision_evidence_run_id != normalized_current_evidence_run_id:
                return None
        row.selected_value = normalized_value
        row.selected_by = str(selected_by or "").strip() or None
        row.selected_at = datetime.utcnow()
        session.flush()
        return _serialize_decision(row)
