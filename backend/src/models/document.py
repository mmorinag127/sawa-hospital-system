from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer

from src.db import Base


class OrderDocument(Base):
    __tablename__ = "order_documents"

    id = Column(String, primary_key=True)
    facility_code = Column(String, nullable=True)
    week_code = Column(String, nullable=True)
    storage_uri = Column(String, nullable=False)
    source_email_id = Column(String, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)
    ocr_attempts = Column(Integer, default=0)
    status = Column(String, default="pending")
    error_message = Column(String, nullable=True)
