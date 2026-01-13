from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, JSON

from src.db import Base


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="pending")
    payload = Column(JSON, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
