from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from src.db import Base


class IngestDelivery(Base):
    __tablename__ = "ingest_deliveries"
    __table_args__ = (UniqueConstraint("job_id", "business_generation", name="uq_ingest_delivery_original"),)
    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("ingest_jobs.id"), nullable=False, index=True)
    business_generation = Column(String, nullable=False)
    input_digest = Column(String, nullable=False)
    original_payload = Column(JSON, nullable=False)
    uploaded_identity = Column(JSON, nullable=True)
    generation = Column(Integer, nullable=False)
    state = Column(String, nullable=False, index=True)
    task_contract = Column(JSON, nullable=False)
    claim_token = Column(String, nullable=True)
    owner_lease = Column(JSON, nullable=True)
    handoff_receipt = Column(JSON, nullable=True)
    terminal_receipt = Column(JSON, nullable=True)
    visit_count = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
