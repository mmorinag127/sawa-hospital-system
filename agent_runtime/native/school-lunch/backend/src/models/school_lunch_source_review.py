from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, JSON, String

from src.db import Base


class SchoolLunchSourceReview(Base):
    __tablename__ = "school_lunch_source_reviews"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_source_review_version"),)

    job_id = Column(String, primary_key=True)
    version = Column(Integer, nullable=False)
    source_fingerprint = Column(String(64), nullable=False)
    rows = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
