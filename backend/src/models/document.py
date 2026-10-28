from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index, Integer

from src.db import Base


class OrderDocument(Base):
    __tablename__ = "order_documents"
    __table_args__ = (
        Index("ix_order_documents_storage_uri", "storage_uri", postgresql_using="hash"),
        Index("ix_order_documents_source_email_id", "source_email_id", postgresql_using="hash"),
        Index("ix_order_documents_retention", "received_at", "id"),
    )

    id = Column(String, primary_key=True)
    facility_code = Column(String, nullable=True)
    week_code = Column(String, nullable=True)
    storage_uri = Column(String, nullable=False)
    source_email_id = Column(String, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ocr_attempts = Column(Integer, default=0, nullable=False)
    status = Column(String, default="pending", nullable=False)
    error_message = Column(String, nullable=True)
