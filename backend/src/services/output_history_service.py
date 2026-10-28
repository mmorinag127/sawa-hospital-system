"""Read persisted generation records, never materialize or repair outputs."""
from datetime import datetime, timezone

from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session, load_only

from src.models.order import Order
from src.models.order_output_artifact import OrderOutputBundle
from src.models.order_workflow_state import OrderWorkflowState
from src.services.order_output_artifact_service import load_output_bundle_payload
from src.services.order_workflow_v2_service import WORKFLOW_V2_META_KEY


class OutputHistoryNotFound(ValueError):
    pass


def _identity(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _meta_reference(actions):
    if not isinstance(actions, dict) or not isinstance(actions.get(WORKFLOW_V2_META_KEY), dict):
        raise ValueError("output_history_workflow_unresolved")
    meta = actions[WORKFLOW_V2_META_KEY]
    reference = meta.get("output_bundle_id")
    if not isinstance(reference, str) or not reference.strip() or reference != reference.strip():
        raise ValueError("output_history_current_reference_required")
    return meta, reference


def list_output_history(session: Session, *, order_id: str, expected_workflow_revision: int,
                        page: int, limit: int, before_id: str | None = None,
                        validate_records: bool = True, record_id: str | None = None) -> dict:
    if session.new or session.dirty or session.deleted:
        raise ValueError("output_history_clean_read_session_required")
    if not isinstance(order_id, str) or not order_id.strip() or order_id != order_id.strip():
        raise ValueError("output_history_order_id_required")
    if type(expected_workflow_revision) is not int or expected_workflow_revision < 1:
        raise ValueError("output_history_workflow_revision_required")
    if type(page) is not int or not 1 <= page <= 100000 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("output_history_pagination_invalid")
    with session.no_autoflush:
        if session.get(Order, order_id) is None:
            raise OutputHistoryNotFound("order_not_found")
        workflow = session.get(OrderWorkflowState, order_id)
        if workflow is None or type(workflow.revision) is not int or workflow.revision < 1:
            raise ValueError("output_history_workflow_revision_required")
        if workflow.revision != expected_workflow_revision:
            raise ValueError("output_history_workflow_revision_mismatch")
        if workflow.blockers_json:
            raise ValueError("output_history_workflow_unresolved")
        meta, current_id = _meta_reference(workflow.secondary_actions_json)
        current = load_output_bundle_payload(session, current_id, expected_order_id=order_id)
        if current is None:
            raise ValueError("output_history_current_artifact_invalid")
        if not workflow.draft_id or current.get("source_saved_sheet_id") != workflow.draft_id:
            raise ValueError("output_history_current_sheet_mismatch")
        if not meta.get("bagging_result_id") or current.get("source_bagging_result_id") != meta["bagging_result_id"]:
            raise ValueError("output_history_current_bagging_mismatch")
        if current.get("template_version_id") != workflow.template_version_id:
            raise ValueError("output_history_current_template_mismatch")
        total = session.scalar(select(func.count()).select_from(OrderOutputBundle).where(OrderOutputBundle.order_id == order_id))
        statement = select(OrderOutputBundle).where(OrderOutputBundle.order_id == order_id)
        if not validate_records:
            statement = statement.options(load_only(OrderOutputBundle.id, OrderOutputBundle.order_id,
                OrderOutputBundle.source_saved_sheet_id, OrderOutputBundle.source_bagging_result_id,
                OrderOutputBundle.template_version_id, OrderOutputBundle.created_at))
        if record_id is not None:
            if not _identity(record_id) or before_id is not None or page != 1:
                raise ValueError("output_history_record_request_invalid")
            statement = statement.where(OrderOutputBundle.id == record_id)
        elif before_id is not None:
            cursor = session.execute(select(OrderOutputBundle.id, OrderOutputBundle.created_at).where(
                OrderOutputBundle.order_id == order_id, OrderOutputBundle.id == before_id,
            )).first()
            if cursor is None:
                raise ValueError("output_history_cursor_invalid")
            if cursor.created_at is None:
                statement = statement.where(OrderOutputBundle.created_at.is_(None), OrderOutputBundle.id < cursor.id)
            else:
                statement = statement.where(or_(OrderOutputBundle.created_at < cursor.created_at,
                    OrderOutputBundle.created_at.is_(None),
                    and_(OrderOutputBundle.created_at == cursor.created_at, OrderOutputBundle.id < cursor.id)))
        elif page > 1:
            raise ValueError("output_history_cursor_required")
        records = session.scalars(statement.order_by(OrderOutputBundle.created_at.desc().nullslast(), OrderOutputBundle.id.desc())
                                  .limit(limit + 1)).all()
        entries = []
        for record in records[:limit]:
            if record is None or any(not _identity(getattr(record, field))
                                     for field in ("id", "order_id", "source_saved_sheet_id", "source_bagging_result_id")):
                raise ValueError("output_history_metadata_invalid")
            if record.template_version_id is not None and not _identity(record.template_version_id):
                raise ValueError("output_history_metadata_invalid")
            reasons = []
            payload = load_output_bundle_payload(session, record.id, expected_order_id=order_id) if validate_records else None
            if validate_records and payload is None:
                reasons.append("output_bundle_artifact_invalid")
            created_at = None
            if isinstance(record.created_at, datetime):
                # The model's datetime.utcnow producer stores naive values as UTC.
                timestamp = record.created_at
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp.utcoffset() is not None:
                    created_at = timestamp.isoformat()
            if created_at is None:
                reasons.append("output_bundle_created_at_invalid")
            entries.append({
                "output_bundle_id": record.id, "order_id": record.order_id,
                "source_saved_sheet_id": record.source_saved_sheet_id,
                "source_bagging_result_id": record.source_bagging_result_id,
                "template_version_id": record.template_version_id, "created_at": created_at,
                "current": record.id == current_id,
                "validation": "blocked" if reasons else "validated" if validate_records else "not_loaded", "blocked_reasons": reasons,
            })
        # A concurrent writer must not make the page look like the captured revision.
        observed = session.execute(select(OrderWorkflowState.revision, OrderWorkflowState.secondary_actions_json)
                                   .where(OrderWorkflowState.order_id == order_id)).first()
        if observed is None or observed.revision != expected_workflow_revision:
            raise ValueError("output_history_workflow_revision_mismatch")
        _, observed_id = _meta_reference(observed.secondary_actions_json)
        if observed_id != current_id:
            raise ValueError("output_history_current_reference_changed")
        if load_output_bundle_payload(session, current_id, expected_order_id=order_id) is None:
            raise ValueError("output_history_current_artifact_invalid")
        return {"order_id": order_id, "workflow_revision": expected_workflow_revision,
                "current_output_bundle_id": current_id, "page": page, "limit": limit,
                "total": total, "entries": entries,
                "next_before_id": records[limit-1].id if len(records) > limit else None}
