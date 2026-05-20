from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from src.db import Base


class OrderVersion(Base):
    __tablename__ = "order_versions"
    __table_args__ = (
        UniqueConstraint("order_id", "version_no", name="uq_order_versions_order_version"),
        UniqueConstraint("document_id", name="uq_order_versions_document"),
    )

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    document_id = Column(String, nullable=False, index=True)
    message_id = Column(String, nullable=False)
    storage_uri = Column(String, nullable=False)
    facility_code = Column(String, nullable=True)
    week_code = Column(String, nullable=True)
    received_at = Column(DateTime, nullable=False)
    line_snapshot = Column(JSON, nullable=False, default=list)
    is_current = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
