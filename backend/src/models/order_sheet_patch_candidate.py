from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, JSON, String

from src.db import Base


class OrderSheetPatchCandidate(Base):
    __tablename__ = "order_sheet_patch_candidates"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    draft_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True, index=True)
    source = Column(String, nullable=True)
    patch_scope = Column(String, nullable=True)
    status = Column(String, nullable=True)
    confidence_score = Column(Float, nullable=True)
    patch_json = Column(JSON, nullable=True)
    apply_plan_json = Column(JSON, nullable=True)
    apply_ready_metadata_json = Column(JSON, nullable=True)
    blockers_json = Column(JSON, nullable=True)
    warnings_json = Column(JSON, nullable=True)
    created_by = Column(String, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    base_draft_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True, index=True)
    base_evidence_run_id = Column(String, ForeignKey("order_ocr_evidence_runs.id"), nullable=True, index=True)
    candidate_state = Column(String, nullable=False, default="ready")
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    prompt_preset = Column(String, nullable=True)
    baseline_source = Column(String, nullable=True)
    baseline_revision_id = Column(String, nullable=True)
    summary_json = Column(JSON, nullable=True)
    issues_json = Column(JSON, nullable=True)
    patches_json = Column(JSON, nullable=True)
    proposed_draft_sheet_json = Column(JSON, nullable=True)
    applied_by = Column(String, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
