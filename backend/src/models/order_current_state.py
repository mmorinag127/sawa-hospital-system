from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderCurrentState(Base):
    __tablename__ = "order_current_states"

    order_id = Column(String, ForeignKey("orders.id"), primary_key=True)
    draft_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True)
    evidence_run_id = Column(String, ForeignKey("order_ocr_evidence_runs.id"), nullable=True)
    snapshot_version = Column(String, nullable=False, default="v1")
    state_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
