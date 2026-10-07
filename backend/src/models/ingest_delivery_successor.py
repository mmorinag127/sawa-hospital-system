from datetime import datetime
import json
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, event, select
from src.db import Base


class IngestDeliverySuccessor(Base):
    __tablename__ = "ingest_delivery_successors"
    id = Column(String, primary_key=True)
    predecessor_job_id = Column(String, ForeignKey("ocr_jobs.id"), nullable=False, unique=True)
    parent_delivery_id = Column(String, ForeignKey("ingest_deliveries.id"), nullable=False)
    job_id = Column(String, ForeignKey("ocr_jobs.id"), nullable=False, unique=True)
    original = Column(JSON, nullable=False)
    generation = Column(Integer, nullable=False)
    state = Column(String, nullable=False, index=True)
    task_contract = Column(JSON, nullable=False)
    claim_token = Column(String, nullable=True)
    started = Column(Boolean, nullable=False, default=False)
    terminal_receipt = Column(JSON, nullable=True)
    handoff_receipt = Column(JSON, nullable=True)
    visit_count = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    @property
    def owner_lease(self):
        return self.original["execution_input"]["ingest_lease"]


@event.listens_for(IngestDeliverySuccessor, "before_update")
def require_original_contract(mapper, connection, row):
    stored = connection.execute(select(IngestDeliverySuccessor.__table__).where(
        IngestDeliverySuccessor.id == row.id)).mappings().one()
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if type(row.generation) is not int or row.generation < 1 or (stored["started"] and row.started is not True):
        raise ValueError("auto_llm_execution_transition_refused")
    for key in ("predecessor_job_id", "parent_delivery_id", "job_id", "original"):
        if canonical(stored[key]) != canonical(getattr(row, key)):
            raise ValueError("auto_llm_original_immutable")
    from src.services.ingest_delivery_dispatch_service import renew_contract
    if row.generation == stored["generation"]:
        expected = stored["task_contract"]
    elif (type(row.generation) is int and row.generation == stored["generation"] + 1
        and stored["terminal_receipt"] is None and stored["handoff_receipt"] is None and row.state == "PENDING"):
        expected = renew_contract(stored["task_contract"], row.id, row.generation)
    else:
        raise ValueError("auto_llm_generation_transition_refused")
    if canonical(row.task_contract) != canonical(expected):
        raise ValueError("auto_llm_transport_contract_immutable")
