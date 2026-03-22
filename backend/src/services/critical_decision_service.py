from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from src.db import Base, engine, session_scope
from src.models.order_critical_decision import OrderCriticalDecision


Base.metadata.create_all(bind=engine)


def _serialize_decision(item: OrderCriticalDecision) -> dict[str, Any]:
    return {
        "id": item.id,
        "order_id": item.order_id,
        "decision_type": item.decision_type,
        "candidate_set_json": item.candidate_set_json if isinstance(item.candidate_set_json, dict) else {},
        "selected_value": item.selected_value,
        "selected_by": item.selected_by,
        "selected_at": item.selected_at.isoformat() if isinstance(item.selected_at, datetime) else None,
    }


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


def sync_pending_decisions(order_id: str, critical_choices: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return []
    desired = {
        str(item.get("decision_type") or "").strip(): item
        for item in (critical_choices or [])
        if isinstance(item, dict) and str(item.get("decision_type") or "").strip()
    }
    with session_scope() as session:
        existing_rows = (
            session.query(OrderCriticalDecision)
            .filter(OrderCriticalDecision.order_id == normalized_order_id)
            .all()
        )
        for row in existing_rows:
            if row.decision_type not in desired:
                session.delete(row)
                continue
            candidate_set = desired.pop(row.decision_type)
            if row.selected_value:
                # keep resolved decision, but refresh displayed candidate metadata.
                row.candidate_set_json = candidate_set
                continue
            row.candidate_set_json = candidate_set
            row.selected_at = datetime.utcnow()
        for decision_type, candidate_set in desired.items():
            session.add(
                OrderCriticalDecision(
                    id=f"OCD{uuid4().hex[:12]}",
                    order_id=normalized_order_id,
                    decision_type=decision_type,
                    candidate_set_json=candidate_set,
                    selected_value=None,
                    selected_by=None,
                    selected_at=datetime.utcnow(),
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


def choose_decision(
    order_id: str,
    decision_type: str,
    selected_value: str,
    *,
    selected_by: str | None = None,
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
        row.selected_value = normalized_value
        row.selected_by = str(selected_by or "").strip() or None
        row.selected_at = datetime.utcnow()
        session.flush()
        return _serialize_decision(row)
