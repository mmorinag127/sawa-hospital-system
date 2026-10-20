from datetime import datetime

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text

from src.db import Base
from src.storage_types import StrictJSON, UTCNaiveDateTime


class ImplementationPriceOcrJob(Base):
    __tablename__ = "implementation_price_ocr_jobs"

    id = Column(String, primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=True, index=True)
    document_type = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False)
    review_required = Column(Boolean, nullable=False, default=True)
    stored_pdf_ref = Column(String, nullable=False)
    result_json = Column(StrictJSON, nullable=True)
    error_message = Column(Text, nullable=True)
    dispatch_error_message = Column(Text, nullable=True)
    delivery_json = Column(StrictJSON, nullable=True)
    delivery_visit_count = Column(Integer, nullable=True)
    source_json = Column(StrictJSON, nullable=True)
    input_digest = Column(String, nullable=True)
    business_attempt_id = Column(String, nullable=True)
    execution_token = Column(String, nullable=True)
    result_owner_json = Column(StrictJSON, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    created_at = Column(UTCNaiveDateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(UTCNaiveDateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    queued_at = Column(UTCNaiveDateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(UTCNaiveDateTime, nullable=True)
    lease_expires_at = Column(UTCNaiveDateTime, nullable=True)
    finished_at = Column(UTCNaiveDateTime, nullable=True)
