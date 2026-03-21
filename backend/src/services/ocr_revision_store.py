from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from src.db import Base, engine, session_scope
from src.models.order_ocr_revision import OrderOcrRevision


Base.metadata.create_all(bind=engine)


def _revision_to_dict(row: OrderOcrRevision) -> dict[str, Any]:
    payload = {
        "revision_id": row.id,
        "edited_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "ui_mode": row.ui_mode,
        "fields": row.fields if isinstance(row.fields, list) else [],
        "header": row.header if isinstance(row.header, list) else [],
        "row_ids": row.row_ids if isinstance(row.row_ids, list) else [],
        "rows": row.rows if isinstance(row.rows, list) else [],
        "row_count": int(row.row_count or 0),
        "before_digest": row.before_digest,
        "after_digest": row.after_digest,
        "changed": bool(row.changed),
        "markdown": row.markdown,
    }
    if row.sheet_save_only:
        payload["sheet_save_only"] = True
    if row.sheet_save_mode:
        payload["sheet_save_mode"] = row.sheet_save_mode
    if isinstance(row.metadata_json, dict):
        payload.update(row.metadata_json)
    return payload


def append_revision(order_id: str, revision: dict[str, Any]) -> dict[str, Any]:
    revision_id = str(revision.get("revision_id") or "").strip()
    if not revision_id:
        raise ValueError("revision_id is required")
    with session_scope() as session:
        existing = session.get(OrderOcrRevision, revision_id)
        if existing:
            return _revision_to_dict(existing)
        metadata = dict(revision)
        for key in (
            "revision_id",
            "edited_at",
            "ui_mode",
            "fields",
            "header",
            "row_ids",
            "rows",
            "row_count",
            "before_digest",
            "after_digest",
            "changed",
            "markdown",
            "sheet_save_only",
            "sheet_save_mode",
        ):
            metadata.pop(key, None)
        row = OrderOcrRevision(
            id=revision_id,
            order_id=order_id,
            ui_mode=str(revision.get("ui_mode") or "").strip() or None,
            row_count=int(revision.get("row_count") or 0),
            changed=bool(revision.get("changed")),
            sheet_save_only=bool(revision.get("sheet_save_only")),
            sheet_save_mode=str(revision.get("sheet_save_mode") or "").strip() or None,
            before_digest=str(revision.get("before_digest") or "").strip() or None,
            after_digest=str(revision.get("after_digest") or "").strip() or None,
            fields=revision.get("fields") if isinstance(revision.get("fields"), list) else [],
            header=revision.get("header") if isinstance(revision.get("header"), list) else [],
            row_ids=revision.get("row_ids") if isinstance(revision.get("row_ids"), list) else [],
            rows=revision.get("rows") if isinstance(revision.get("rows"), list) else [],
            markdown=str(revision.get("markdown") or "").strip() or None,
            metadata_json=metadata or None,
            created_at=_coerce_datetime(revision.get("edited_at")) or datetime.utcnow(),
        )
        session.add(row)
        session.flush()
        return _revision_to_dict(row)


def _coerce_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def list_revisions(order_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    normalized_limit = max(1, min(int(limit), 100))
    with session_scope() as session:
        rows = (
            session.execute(
                select(OrderOcrRevision)
                .where(OrderOcrRevision.order_id == order_id)
                .order_by(OrderOcrRevision.created_at.desc(), OrderOcrRevision.id.desc())
                .limit(normalized_limit)
            )
            .scalars()
            .all()
        )
        return [_revision_to_dict(row) for row in rows]


def get_latest_revision(order_id: str, *, exact_only: bool = False) -> dict[str, Any] | None:
    revisions = list_revisions(order_id, limit=50)
    for revision in revisions:
        if str(revision.get("ui_mode") or "").strip().lower() != "sheet":
            continue
        if not isinstance(revision.get("rows"), list):
            continue
        if exact_only:
            is_exact = bool(revision.get("sheet_save_only")) or (
                str(revision.get("sheet_save_mode") or "").strip().lower() == "exact"
            )
            if not is_exact:
                continue
        return revision
    return None


def get_revision_summary(order_id: str) -> dict[str, Any]:
    with session_scope() as session:
        count_value = (
            session.execute(
                select(func.count(OrderOcrRevision.id)).where(OrderOcrRevision.order_id == order_id)
            ).scalar_one_or_none()
            or 0
        )
        latest = (
            session.execute(
                select(OrderOcrRevision)
                .where(OrderOcrRevision.order_id == order_id)
                .order_by(OrderOcrRevision.created_at.desc(), OrderOcrRevision.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        latest_payload = _revision_to_dict(latest) if latest else None
        return {
            "count": int(count_value or 0),
            "last_revision_id": latest_payload.get("revision_id") if isinstance(latest_payload, dict) else None,
            "last_edited_at": latest_payload.get("edited_at") if isinstance(latest_payload, dict) else None,
        }
