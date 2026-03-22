from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderConfirmedSnapshot(Base):
    __tablename__ = "order_confirmed_snapshots"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    draft_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True)
    snapshot_digest = Column(String, nullable=False)
    snapshot_json = Column(JSON, nullable=False)
    confirmed_by = Column(String, nullable=True)
    confirmed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
