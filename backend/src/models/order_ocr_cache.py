from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON

from src.db import Base


class OrderOcrCache(Base):
    __tablename__ = "order_ocr_cache"

    order_id = Column(String, primary_key=True)
    payload = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
