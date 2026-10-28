"""One saved-history protection rule for automatic and explicit cleanup."""
from sqlalchemy import or_, select

from src.db import Base
from src.models.ocr_training_sample import OcrTrainingSample
from src.models.order import Order, OrderLine
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_revision import OrderOcrRevision
from src.models.output import Bag, LabelRow, DeliveryNote

LEAF_ORDER_MODELS = (LabelRow, Bag, DeliveryNote, OrderLine)


def unreferenced_order_predicates():
    ignored = {model.__tablename__ for model in LEAF_ORDER_MODELS}
    predicates = [~select(1).select_from(model).where(model.order_id == Order.id).exists()
                  for model in (OcrTrainingSample, OrderOcrCache, OrderOcrRevision)]
    for model in LEAF_ORDER_MODELS:
        receipts = [getattr(model, field).is_not(None) for field in (
            'confirmed_snapshot_id', 'output_bundle_id', 'source_saved_sheet_id') if hasattr(model, field)]
        if receipts:
            predicates.append(~select(1).select_from(model).where(
                model.order_id == Order.id, or_(*receipts)).exists())
    for table in Base.metadata.tables.values():
        if table.name in ignored:
            continue
        for foreign_key in table.foreign_keys:
            if foreign_key.target_fullname == 'orders.id':
                predicates.append(~select(1).select_from(table).where(foreign_key.parent == Order.id).exists())
    return predicates
