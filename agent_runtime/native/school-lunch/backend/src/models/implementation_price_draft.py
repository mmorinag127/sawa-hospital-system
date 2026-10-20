from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, Integer, String

from src.db import Base


class ImplementationPriceDraft(Base):
    __tablename__ = "implementation_price_drafts"

    id = Column(String(32), primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=True, index=True)
    implementation_date = Column(Date, nullable=True, index=True)
    label = Column(String(300), nullable=False)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
