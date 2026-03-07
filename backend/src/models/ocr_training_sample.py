from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String

from src.db import Base


class OcrTrainingSample(Base):
    __tablename__ = "ocr_training_samples"

    id = Column(String, primary_key=True)
    order_id = Column(String, nullable=False, unique=True, index=True)
    message_id = Column(String, nullable=True, index=True)
    facility_code = Column(String, nullable=True, index=True)
    week_code = Column(String, nullable=True)
    document_uri = Column(String, nullable=False)
    ocr_job_id = Column(String, nullable=True)
    ocr_provider = Column(String, nullable=True)
    ocr_output = Column(JSON, nullable=True)
    labeled_lines = Column(JSON, nullable=False)
    line_count = Column(Integer, nullable=False, default=0)
    has_corrections = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=False, default="manual")
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
