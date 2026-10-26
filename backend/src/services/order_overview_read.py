"""Paginated stored facts for navigation, not execution or confirmation gates."""
from sqlalchemy import func, literal, or_, select, union_all

from src.db import session_scope
from src.models.order import Order, OrderLine
from src.models.uploaded_pdf import UploadedPdf
from src.services.sheet_week_service import to_sheet_month_id


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


def list_facility_orders(*, facility=None, week=None, status=None, limit=25, offset=0):
    uploads = select(
        UploadedPdf.id, UploadedPdf.facility_hint, UploadedPdf.week_hint, UploadedPdf.received_at,
        UploadedPdf.status, UploadedPdf.current_document_id,
    ).where(UploadedPdf.current_order_id.is_(None)).order_by(
        UploadedPdf.received_at.desc(), UploadedPdf.id.desc(),
    ).limit(200).subquery()
    facts = union_all(
        select(Order.id.label("id"), Order.facility_code.label("facility"), Order.week_code.label("week"),
               Order.status.label("status"), Order.received_at.label("received_at"),
               literal("order").label("source_kind"), literal(None).label("uploaded_pdf_id")),
        select((literal("UPLOAD-") + uploads.c.id), uploads.c.facility_hint, uploads.c.week_hint,
               literal("アップロード済み"), uploads.c.received_at, literal("uploaded_pdf"), uploads.c.id).where(or_(
                   func.lower(func.trim(func.coalesce(uploads.c.status, ""))) != "completed",
                   uploads.c.current_document_id.is_(None), uploads.c.current_document_id == "",
               )),
    ).subquery()
    with session_scope() as session:
        groups = session.execute(select(facts.c.facility, facts.c.week, facts.c.status, func.count().label("count"))
                                 .group_by(facts.c.facility, facts.c.week, facts.c.status)).mappings().all()
        progress = {}
        total = 0
        raw_weeks = set()
        for group in groups:
            normalized_week = to_sheet_month_id(group["week"])
            if week and normalized_week != week:
                continue
            raw_weeks.add(group["week"])
            if facility == "__unresolved__" and group["facility"]:
                continue
            if facility and facility != "__unresolved__" and group["facility"] != facility:
                continue
            if status and group["status"] != status:
                continue
            count = group["count"]
            total += count
            label = normalized_week or "未確定"
            row = progress.setdefault(label, {"week": label, "pending": 0, "review": 0, "confirmed": 0, "error": 0})
            field = {"未着": "pending", "要確認": "review", "確定": "confirmed", "エラー": "error"}.get(group["status"])
            if field:
                row[field] += count
        query = select(facts)
        if week:
            query = query.where(facts.c.week.in_(raw_weeks))
        if facility == "__unresolved__":
            query = query.where(or_(facts.c.facility.is_(None), facts.c.facility == ""))
        elif facility:
            query = query.where(facts.c.facility == facility)
        if status:
            query = query.where(facts.c.status == status)
        rows = session.execute(query.order_by(facts.c.received_at.desc().nullslast(), facts.c.id.desc())
                               .offset(offset).limit(limit)).mappings().all()
        return {
            "orders": [{**row, "week": to_sheet_month_id(row["week"])} for row in rows],
            "total": total,
            "facility_options": sorted({group["facility"] for group in groups if group["facility"]}),
            "week_options": sorted({value for group in groups if (value := to_sheet_month_id(group["week"]))}),
            "progress": [progress[key] for key in sorted(progress)],
        }
