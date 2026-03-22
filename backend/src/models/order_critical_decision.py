from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderCriticalDecision(Base):
    __tablename__ = "order_critical_decisions"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    decision_type = Column(String, nullable=False)
    candidate_set_json = Column(JSON, nullable=False)
    selected_value = Column(String, nullable=True)
    selected_by = Column(String, nullable=True)
    selected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
