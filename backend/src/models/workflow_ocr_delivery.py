from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from src.db import Base


class WorkflowOcrDelivery(Base):
    __tablename__ = "workflow_ocr_deliveries"
    __table_args__ = (
        UniqueConstraint("workflow_attempt_id", name="uq_workflow_ocr_delivery_original_attempt"),
        UniqueConstraint("workflow_attempt_id", "delivery_generation", name="uq_workflow_ocr_delivery_attempt_generation"),
    )

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    job_id = Column(String, ForeignKey("ocr_jobs.id"), nullable=False, index=True)
    workflow_attempt_id = Column(String, nullable=False, index=True)
    workflow_revision = Column(Integer, nullable=False)
    delivery_generation = Column(Integer, nullable=False)
    task_name = Column(String, nullable=False, unique=True)
    task_contract = Column(JSON, nullable=False)
    identity_sha256 = Column(String, nullable=False)
    visit_count = Column(Integer, nullable=False, default=0)
    dispatch_error = Column(String, nullable=True)
    handoff_receipt = Column(JSON, nullable=True)
    result_receipt = Column(JSON, nullable=True)
    request_mode = Column(String, nullable=False)
    selected_pdf_receipt = Column(JSON, nullable=False)
    request_identity = Column(JSON, nullable=False)
    state = Column(String, nullable=False, index=True)
    claim_token = Column(String, nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    transport_receipt = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
