from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, LargeBinary, String
from src.db import Base


class SchoolLunchDocument(Base):
    __tablename__ = "school_lunch_documents"
    id = Column(String(32), primary_key=True)
    facility_id = Column(Integer, ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False, index=True)
    target_month = Column(String(7), nullable=False)
    filename = Column(String(300), nullable=False)
    content = Column(LargeBinary, nullable=False)
    sha256 = Column(String(64), nullable=False)
    extracted_json = Column(JSON, nullable=True)
    ocr_job_id = Column(String, ForeignKey("implementation_price_ocr_jobs.id", ondelete="RESTRICT"), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
