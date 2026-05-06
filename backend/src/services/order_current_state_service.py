from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import session_scope
from src.models.order_current_state import OrderCurrentState

_CURRENT_STATE_SNAPSHOT_VERSION = "v2"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _parse_optional_iso_datetime(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return value


def _hydrate_current_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(payload)
    order_payload = hydrated.get("order_payload")
    if isinstance(order_payload, dict):
        next_order_payload = dict(order_payload)
        next_order_payload["received_at"] = _parse_optional_iso_datetime(
            next_order_payload.get("received_at")
        )
        next_order_payload["lines_updated_at"] = _parse_optional_iso_datetime(
            next_order_payload.get("lines_updated_at")
        )
        hydrated["order_payload"] = next_order_payload
    return hydrated


def _serialize(row: OrderCurrentState) -> dict[str, Any]:
    return {
        "order_id": row.order_id,
        "template_version_id": row.template_version_id,
        "draft_id": row.draft_id,
        "evidence_run_id": row.evidence_run_id,
        "snapshot_version": str(row.snapshot_version or "").strip() or _CURRENT_STATE_SNAPSHOT_VERSION,
        "state_json": row.state_json if isinstance(row.state_json, dict) else {},
        "updated_at": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else None,
    }


def get_current_state(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        row = session.get(OrderCurrentState, normalized_order_id)
        if not row:
            return None
        return _serialize(row)


def get_current_state_payload(order_id: str) -> dict[str, Any] | None:
    state = get_current_state(order_id)
    if not isinstance(state, dict):
        return None
    if str(state.get("snapshot_version") or "").strip() != _CURRENT_STATE_SNAPSHOT_VERSION:
        return None
    payload = state.get("state_json")
    return _hydrate_current_state_payload(payload) if isinstance(payload, dict) else None


def persist_current_state(
    *,
    order_id: str,
    state_json: dict[str, Any],
    draft_id: str | None = None,
    evidence_run_id: str | None = None,
    template_version_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or not isinstance(state_json, dict):
        return None
    safe_state = _json_safe(state_json)
    now = datetime.utcnow()
    with session_scope() as session:
        values = {
            "order_id": normalized_order_id,
            "template_version_id": str(template_version_id or "").strip() or None,
            "draft_id": str(draft_id or "").strip() or None,
            "evidence_run_id": str(evidence_run_id or "").strip() or None,
            "snapshot_version": _CURRENT_STATE_SNAPSHOT_VERSION,
            "state_json": safe_state,
            "updated_at": now,
        }
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            stmt = pg_insert(OrderCurrentState).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[OrderCurrentState.order_id],
                set_={
                    "draft_id": values["draft_id"],
                    "template_version_id": values["template_version_id"],
                    "evidence_run_id": values["evidence_run_id"],
                    "snapshot_version": values["snapshot_version"],
                    "state_json": values["state_json"],
                    "updated_at": values["updated_at"],
                },
            )
            session.execute(stmt)
            row = session.get(OrderCurrentState, normalized_order_id)
        else:
            row = session.get(OrderCurrentState, normalized_order_id)
            if row is None:
                row = OrderCurrentState(**values)
                session.add(row)
            else:
                row.draft_id = values["draft_id"]
                row.template_version_id = values["template_version_id"]
                row.evidence_run_id = values["evidence_run_id"]
                row.snapshot_version = values["snapshot_version"]
                row.state_json = values["state_json"]
                row.updated_at = values["updated_at"]
        session.flush()
        return _serialize(row)


def delete_current_state(order_id: str) -> None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return
    with session_scope() as session:
        session.execute(delete(OrderCurrentState).where(OrderCurrentState.order_id == normalized_order_id))
