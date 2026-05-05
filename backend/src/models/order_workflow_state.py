from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderWorkflowState(Base):
    __tablename__ = "order_workflow_states"

    order_id = Column(String, ForeignKey("orders.id"), primary_key=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    evidence_run_id = Column(String, ForeignKey("order_ocr_evidence_runs.id"), nullable=True)
    draft_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True)
    confirmed_snapshot_id = Column(String, ForeignKey("order_confirmed_snapshots.id"), nullable=True)
    state = Column(String, nullable=False, default="uploaded")
    headline = Column(String, nullable=True)
    primary_action = Column(String, nullable=True)
    secondary_actions_json = Column(JSON, nullable=True)
    blockers_json = Column(JSON, nullable=True)
    warnings_json = Column(JSON, nullable=True)
    confidence_band = Column(String, nullable=True)
    last_transition_at = Column(DateTime, default=datetime.utcnow, nullable=False)
