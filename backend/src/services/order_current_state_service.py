from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import session_scope
from src.models.order_current_state import OrderCurrentState
from src.services.workflow_attempt_guard_service import (
    lock_current_execution_write, lock_actual_write_ownership,
    validate_current_sheet_input, CurrentSheetInputChanged,
    CurrentSheetContext,
    lock_order_execution_input,
)

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
    hydrated = CurrentSheetContext(payload)
    hydrated.input_receipt = hydrated.pop("_current_sheet_input_receipt", None)
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
    if not isinstance(payload, dict):
        return None
    hydrated = _hydrate_current_state_payload(payload)
    try:
        with session_scope() as session:
            validate_current_sheet_input(session, order_id, hydrated)
    except CurrentSheetInputChanged:
        return None
    return hydrated


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
    safe_state["_current_sheet_input_receipt"] = _json_safe(getattr(state_json, "input_receipt", None))
    now = datetime.utcnow()
    with session_scope() as session:
        validate_current_sheet_input(session, normalized_order_id, state_json, writing=True)
        if (draft_id != state_json.get("draft_id")
                or evidence_run_id != state_json.get("base_evidence_run_id")
                or template_version_id != state_json.get("template_version_id")):
            raise CurrentSheetInputChanged("current_sheet_persist_reference_mismatch")
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


def delete_current_state(
    order_id: str, *, workflow_job_id: str | None = None,
    workflow_attempt_id: str | None = None, expected_workflow_revision: int | None = None,
    source_context: dict[str, Any] | None = None,
) -> None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return
    with session_scope() as session:
        if source_context is not None:
            validate_current_sheet_input(session, normalized_order_id, source_context, writing=True)
        elif lock_order_execution_input(session, normalized_order_id) is None:
            return
        if workflow_job_id is not None or workflow_attempt_id is not None or expected_workflow_revision is not None:
            allowed = lock_actual_write_ownership(
                session, job_id=workflow_job_id, order_id=normalized_order_id,
                workflow_attempt_id=workflow_attempt_id,
                expected_workflow_revision=expected_workflow_revision,
            )
        else:
            allowed = lock_current_execution_write(session, normalized_order_id)
        if not allowed:
            return
        session.execute(delete(OrderCurrentState).where(OrderCurrentState.order_id == normalized_order_id))
