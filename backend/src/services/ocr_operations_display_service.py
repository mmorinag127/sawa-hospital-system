"""Stored OCR progress for display, separate from execution authorization."""
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from src.db import session_scope
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.services.workflow_display_read_service import _job_summary, read_recorded_job_values


SOURCE = "recorded_sql_workflow_display/v1"
TERMINAL = {"done", "failed", "error", "cancelled"}


def _states(session, order_ids=None):
    meta = OrderWorkflowState.secondary_actions_json["workflow_v2"]
    statement = select(
        OrderWorkflowState.order_id, OrderWorkflowState.revision, OrderWorkflowState.state,
        OrderWorkflowState.template_version_id, OrderWorkflowState.last_transition_at,
        OrderWorkflowState.headline, OrderWorkflowState.primary_action,
        OrderWorkflowState.blockers_json, OrderWorkflowState.warnings_json,
        meta["ocr_job_id"].label("job_id"), meta["ocr_job_attempt_id"].label("attempt"),
    )
    if order_ids is not None:
        statement = statement.where(OrderWorkflowState.order_id.in_(order_ids))
    return session.execute(statement).mappings().all()


def get_order_job_displays(order_ids):
    identities = list(dict.fromkeys(order_ids))
    if not identities:
        return {}
    with session_scope() as session, session.no_autoflush:
        result = {}
        for offset in range(0, len(identities), 400):
            chunk = identities[offset:offset + 400]
            states = {row["order_id"]: row for row in _states(session, chunk)}
            jobs = read_recorded_job_values(session, _references(states.values()))
            orders = session.execute(select(Order.id, Order.current_document_id).where(Order.id.in_(chunk)))
            for order in orders:
                state = states.get(order.id)
                job, error = None, None
                if state is not None:
                    meta = ({"ocr_job_id": state["job_id"], "ocr_job_attempt_id": state["attempt"]}
                        if state["job_id"] is not None or state["attempt"] is not None else {})
                    job, error, _ = _job_summary(session, order, SimpleNamespace(**state),
                        meta, _rows=jobs)
                workflow = None if state is None else {key: state[key] for key in
                    ("state", "headline", "primary_action", "blockers_json", "warnings_json")}
                result[order.id] = {"job": job, "error": error,
                    "workflow_state": workflow, "validation_state": "references_only"}
        return result


def _references(states):
    return {row["job_id"] for row in states if isinstance(row["job_id"], str) and row["job_id"]}


def _history_statement(session):
    identity = OrderWorkflowState.secondary_actions_json["workflow_v2"]["ocr_job_id"]
    kind = func.json_typeof(identity) if session.get_bind().dialect.name == "postgresql" else func.json_type(identity)
    expected_kind = "string" if session.get_bind().dialect.name == "postgresql" else "text"
    references = select(identity.as_string()).where(kind == expected_kind, identity.as_string() != "")
    statement = select(OcrJob.id.label("job_id"), OcrJob.order_id,
        OcrJob.order_document_id.label("document_id"), OcrJob.status.label("source_status"),
        OcrJob.created_at, OcrJob.updated_at, OcrJob.error_message.label("error"))
    return statement.where(OcrJob.id.not_in(references))


def _serial(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}


def get_operations_display():
    with session_scope() as session, session.no_autoflush:
        states = _states(session)
        references = _references(states)
        jobs = {}
        identities = sorted(references)
        for offset in range(0, len(identities), 400):
            jobs.update(read_recorded_job_values(session, identities[offset:offset + 400]))
        eligible = [state for state in states if state["job_id"] is not None
            or state["state"] in {"ocr_running", "ocr_completed", "ocr_failed"}]
        order_ids = sorted({state["order_id"] for state in eligible})
        orders = {}
        for offset in range(0, len(order_ids), 400):
            orders.update({row.id: row for row in session.execute(select(Order.id, Order.current_document_id)
                .where(Order.id.in_(order_ids[offset:offset + 400])))})
        items = []
        for state in eligible:
            identity = state["job_id"]
            if identity is None and state["state"] not in {"ocr_running", "ocr_completed", "ocr_failed"}:
                continue
            item = {"order_id": state["order_id"], "document_id": None, "job_id": identity if isinstance(identity, str) else None,
                "workflow_revision": state["revision"], "workflow_state": state["state"],
                "kind": None, "execution_id": None, "generation": None,
                "status": "blocked", "source_status": None, "blockers": [],
                "validation_state": "references_only", "updated_at": state["last_transition_at"]}
            recorded = jobs.get(identity) if isinstance(identity, str) else None
            if recorded is not None:
                item.update(source_status=recorded["status"], created_at=recorded["created_at"], updated_at=recorded["updated_at"])
            order = orders.get(state["order_id"])
            if order is None:
                item["blockers"] = ["order_not_found"]
            else:
                item["document_id"] = order.current_document_id
                job, error, common = _job_summary(session, order, SimpleNamespace(**state),
                    {"ocr_job_id": identity, "ocr_job_attempt_id": state["attempt"]}, _rows=jobs)
                if error:
                    item["blockers"] = [error]
                elif job is not None:
                    native = job.get("native_ingest")
                    item.update(job=job, source_status=job["status"], common_job_status=common,
                        created_at=job["created_at"], updated_at=job["updated_at"],
                        kind="native_ingest_v2" if native else "workflow",
                        generation=job["recorded_generation"],
                        execution_id=native["execution_id"] if native else job["workflow_attempt_id"])
                    item["status"] = {"hard_failed": "failed", "done": "succeeded"}.get(job["recorded_state"], job["recorded_state"])
                    if job.get("result_state") == "review_required":
                        item["status"] = "review_required"
                    elif job.get("result_state") == "skipped":
                        item["status"] = "not_recognized"
                    if item["status"] not in {"pending", "queued", "running", "awaiting_output", "recovering",
                            "failed", "stalled", "succeeded", "review_required", "not_recognized"}:
                        item["status"] = "blocked"
                        item["blockers"] = ["ocr_job_state_unknown"]
            items.append(_serial(item))
        historical = _history_statement(session)
        history_count = session.scalar(select(func.count()).select_from(historical.subquery()))
        for row in session.execute(historical.where(OcrJob.status.not_in(TERMINAL))).mappings():
            items.append({**_serial(row), "status": "blocked", "kind": None, "execution_id": None,
                "generation": None, "validation_state": "references_only",
                "blockers": ["ocr_active_job_not_referenced_by_workflow"]})
        counts = dict(Counter(item["status"] for item in items))
        priority = ("blocked", "review_required", "failed", "stalled", "running", "recovering",
                    "awaiting_output", "queued", "pending", "not_recognized", "succeeded")
        status = next((value for value in priority if counts.get(value)), "idle")
        return {"source": SOURCE, "validation_state": "references_only",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "summary": {"status": status, "counts": counts, "total": len(items)}, "items": items,
            "history": [], "history_loaded": False, "history_count": history_count}


def get_operations_history(*, limit=50, cursor=None):
    with session_scope() as session, session.no_autoflush:
        statement = _history_statement(session)
        if cursor is not None:
            statement = statement.where(OcrJob.id > cursor)
        rows = session.execute(statement.order_by(OcrJob.id).limit(limit + 1)).mappings().all()
        return {"source": SOURCE, "history": [_serial(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["job_id"] if len(rows) > limit else None}
