import os
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from loguru import logger

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.order import Order, OrderLine
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.workers import celery_app


DEFAULT_RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90") or "90")


def purge_old_records(retention_days: int | None = None) -> dict:
    days = retention_days or DEFAULT_RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=days)
    with session_scope() as session:
        order_ids = (
            session.execute(select(Order.id).where(Order.received_at < cutoff)).scalars().all()
        )
        if order_ids:
            session.execute(delete(LabelRow).where(LabelRow.order_id.in_(order_ids)))
            session.execute(delete(Bag).where(Bag.order_id.in_(order_ids)))
            session.execute(delete(DeliveryNote).where(DeliveryNote.order_id.in_(order_ids)))
            session.execute(delete(OrderLine).where(OrderLine.order_id.in_(order_ids)))
            session.execute(delete(Order).where(Order.id.in_(order_ids)))
        session.execute(delete(OrderDocument).where(OrderDocument.received_at < cutoff))
    logger.info("Retention purge completed", retention_days=days, order_count=len(order_ids))
    return {"retention_days": days, "orders_deleted": len(order_ids)}


@celery_app.task(name="backend.src.workers.retention_worker.purge_old_records")
def purge_old_records_task(retention_days: int | None = None):
    return purge_old_records(retention_days=retention_days)
