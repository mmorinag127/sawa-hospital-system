import os
from datetime import datetime, timedelta

from sqlalchemy import JSON, case, cast, delete, func, literal, select
from loguru import logger

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_version import OrderVersion
from src.models.ocr_training_sample import OcrTrainingSample
from src.models.uploaded_pdf import UploadedPdf
from src.services.order_retention_policy import (
    LEAF_ORDER_MODELS as _PURGED_CHILDREN,
    unreferenced_order_predicates as _unreferenced_order_predicates,
)
from src.workers import celery_app


DEFAULT_RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90") or "90")
_BATCH_SIZE = 200


def _superseded_document_is_retained(session):
    value = Order.superseded_document_ids
    if session.get_bind().dialect.name == "postgresql":
        kind = func.json_typeof(value)
        members = func.json_array_elements_text(case(
            (kind == "array", value), else_=cast(literal("[]"), JSON),
        )).table_valued("value", joins_implicitly=True)
    elif session.get_bind().dialect.name == "sqlite":
        kind = func.json_type(value)
        members = func.json_each(case((kind == "array", value), else_="[]")).table_valued(
            "value", joins_implicitly=True)
    else:
        raise RuntimeError("retention document references require PostgreSQL or SQLite")
    malformed = select(1).select_from(Order).where(
        value.is_not(None), kind.not_in(("array", "null")),
    ).exists()
    referenced = select(1).select_from(Order, members).where(
        members.c.value == OrderDocument.id,
    ).exists()
    return malformed | referenced


def _begin_retention_transaction(session):
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    elif connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def purge_old_records(retention_days: int | None = None) -> dict:
    days = DEFAULT_RETENTION_DAYS if retention_days is None else retention_days
    if type(days) is not int or days <= 0:
        raise ValueError("retention_days must be a positive integer")
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = 0
    with session_scope() as session:
        _begin_retention_transaction(session)
        while order_ids := session.execute(select(Order.id).where(
            Order.received_at < cutoff, *_unreferenced_order_predicates(),
        ).order_by(Order.id).limit(_BATCH_SIZE).with_for_update(skip_locked=True)).scalars().all():
            order_ids = session.scalars(select(Order.id).where(Order.id.in_(order_ids),
                Order.received_at < cutoff, *_unreferenced_order_predicates())).all()
            if not order_ids:
                session.commit()
                _begin_retention_transaction(session)
                continue
            for model in _PURGED_CHILDREN:
                session.execute(delete(model).where(model.order_id.in_(order_ids)))
            session.execute(delete(Order).where(Order.id.in_(order_ids)))
            session.commit()
            deleted += len(order_ids)
            logger.info("Retention order batch committed", order_count=len(order_ids))
            _begin_retention_transaction(session)
        document_conditions = (
            OrderDocument.received_at < cutoff,
            ~_superseded_document_is_retained(session),
            ~select(1).select_from(Order).where(
                (Order.current_document_id == OrderDocument.id)
                | (Order.message_id == OrderDocument.source_email_id)
                | (Order.document_uri == OrderDocument.storage_uri),
            ).exists(),
            ~select(1).select_from(OrderVersion).where(
                (OrderVersion.document_id == OrderDocument.id)
                | (OrderVersion.message_id == OrderDocument.source_email_id)
                | (OrderVersion.storage_uri == OrderDocument.storage_uri),
            ).exists(),
            ~select(1).select_from(UploadedPdf).where(
                (UploadedPdf.current_document_id == OrderDocument.id)
                | (UploadedPdf.message_id == OrderDocument.source_email_id)
                | (UploadedPdf.storage_uri == OrderDocument.storage_uri),
            ).exists(),
            ~select(1).select_from(OcrTrainingSample).where(
                OcrTrainingSample.document_uri == OrderDocument.storage_uri,
            ).exists(),
            ~select(1).select_from(OcrJob).where(
                (OcrJob.order_document_id == OrderDocument.id)
                | (OcrJob.input_reference == OrderDocument.storage_uri),
            ).exists(),
        )
        while document_ids := session.execute(select(OrderDocument.id).where(
            *document_conditions,
        ).order_by(OrderDocument.id).limit(_BATCH_SIZE).with_for_update(skip_locked=True)).scalars().all():
            session.execute(delete(OrderDocument).where(OrderDocument.id.in_(document_ids), *document_conditions))
            session.commit()
            _begin_retention_transaction(session)
    logger.info("Retention purge completed", retention_days=days, order_count=deleted)
    return {"retention_days": days, "orders_deleted": deleted}


@celery_app.task(name="backend.src.workers.retention_worker.purge_old_records")
def purge_old_records_task(retention_days: int | None = None):
    return purge_old_records(retention_days=retention_days)
