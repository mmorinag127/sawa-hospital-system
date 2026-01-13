from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON

from src.db import Base


class OcrJob(Base):
    __tablename__ = "ocr_jobs"

    id = Column(String, primary_key=True)
    status = Column(String, default="running")
    input_reference = Column(String, nullable=False)
    template_id = Column(String, nullable=True)
    output_reference = Column(String, nullable=True)
    metrics = Column(JSON, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
