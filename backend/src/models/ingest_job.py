from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, JSON

from src.db import Base


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="pending")
    payload = Column(JSON, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    generation_attempts = Column(Integer, nullable=True)
    input_digest = Column(String, nullable=True)
    generation = Column(String, nullable=True)
    claim_id = Column(String, nullable=True)
    recovery_claim_id = Column(String, nullable=True)
    failure_kind = Column(String, nullable=True)
    last_error = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
