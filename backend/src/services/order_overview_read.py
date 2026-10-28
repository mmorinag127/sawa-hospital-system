"""Paginated stored facts for navigation, not execution or confirmation gates."""
from sqlalchemy import and_, func, literal, or_, select, union_all

from src.db import session_scope
from src.models.order import Order, OrderLine
from src.models.uploaded_pdf import UploadedPdf
from src.services.sheet_week_service import to_sheet_month_id
from src.models.upload_visibility_v1 import unresolved_upload_conditions as upload_visibility


def unresolved_upload_conditions():
    return upload_visibility(UploadedPdf)


def list_daily_order_facts(target_date, *, facility=None, status=None):
    query = select(Order.id, Order.facility_code.label("facility"), Order.week_code.label("week"),
                   Order.status, Order.received_at, func.count(OrderLine.id).label("line_count"))
    query = query.join(OrderLine, Order.id == OrderLine.order_id).where(OrderLine.date == target_date)
    if facility:
        query = query.where(Order.facility_code == facility)
    if status:
        query = query.where(Order.status == status)
    query = query.group_by(Order.id).order_by(Order.received_at.desc().nullslast(), Order.id.desc())
    with session_scope() as session:
        return [{**row, "week": to_sheet_month_id(row["week"])} for row in session.execute(query).mappings()]


def list_facility_orders(*, facility=None, week=None, status=None, limit=25, offset=0,
                         before_id=None, before_source_kind=None):
    if (before_id is None) != (before_source_kind is None) or before_source_kind not in (None, "order", "uploaded_pdf"):
        raise ValueError("order_overview_cursor_invalid")
    if offset and before_id is None:
        raise ValueError("order_overview_cursor_required")
    uploads = select(
        UploadedPdf.id, UploadedPdf.facility_hint, UploadedPdf.week_hint, UploadedPdf.received_at,
    ).where(*unresolved_upload_conditions()).subquery()
    facts = union_all(
        select(Order.id.label("id"), Order.facility_code.label("facility"), Order.week_code.label("week"),
               Order.status.label("status"), Order.received_at.label("received_at"),
               literal("order").label("source_kind"), literal(None).label("uploaded_pdf_id")),
        select((literal("UPLOAD-") + uploads.c.id), uploads.c.facility_hint, uploads.c.week_hint,
               literal("アップロード済み"), uploads.c.received_at, literal("uploaded_pdf"), uploads.c.id),
    ).subquery()
    with session_scope() as session:
        facility_options = session.execute(select(facts.c.facility).where(
            facts.c.facility.is_not(None), facts.c.facility != "",
        ).distinct()).scalars().all()
        stored_weeks = session.execute(select(facts.c.week).distinct()).scalars().all()
        week_options = sorted({value for raw in stored_weeks if (value := to_sheet_month_id(raw))})
        filters = []
        if week:
            raw_weeks = [raw for raw in stored_weeks if to_sheet_month_id(raw) == week]
            filters.append(facts.c.week.in_(raw_weeks))
        if facility == "__unresolved__":
            filters.append(or_(facts.c.facility.is_(None), facts.c.facility == ""))
        elif facility:
            filters.append(facts.c.facility == facility)
        if status:
            filters.append(facts.c.status == status)
        groups = session.execute(select(facts.c.facility, facts.c.week, facts.c.status, func.count().label("count"))
                                 .where(*filters)
                                 .group_by(facts.c.facility, facts.c.week, facts.c.status)).mappings().all()
        progress = {}
        total = 0
        for group in groups:
            normalized_week = to_sheet_month_id(group["week"])
            count = group["count"]
            total += count
            label = normalized_week or "未確定"
            row = progress.setdefault(label, {"week": label, "pending": 0, "review": 0, "confirmed": 0, "error": 0})
            field = {"未着": "pending", "要確認": "review", "確定": "confirmed", "エラー": "error"}.get(group["status"])
            if field:
                row[field] += count
        query = select(facts).where(*filters)
        if before_id is not None:
            anchor = session.execute(select(facts.c.received_at).where(*filters,
                facts.c.id == before_id, facts.c.source_kind == before_source_kind)).one_or_none()
            if anchor is None:
                raise ValueError("order_overview_cursor_invalid")
            tie = or_(facts.c.id < before_id, and_(facts.c.id == before_id, facts.c.source_kind < before_source_kind))
            if anchor.received_at is None:
                query = query.where(facts.c.received_at.is_(None), tie)
            else:
                query = query.where(or_(facts.c.received_at < anchor.received_at,
                    facts.c.received_at.is_(None), and_(facts.c.received_at == anchor.received_at, tie)))
        rows = session.execute(query.order_by(facts.c.received_at.desc().nullslast(),
            facts.c.id.desc(), facts.c.source_kind.desc()).limit(limit + 1)).mappings().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "orders": [{**row, "week": to_sheet_month_id(row["week"])} for row in rows],
            "total": total,
            "next_cursor": {"id": rows[-1]["id"], "source_kind": rows[-1]["source_kind"]} if has_more else None,
            "facility_options": sorted(facility_options),
            "week_options": week_options,
            "progress": [progress[key] for key in sorted(progress)],
        }
