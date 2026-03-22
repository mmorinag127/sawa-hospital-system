from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderSheetDraft(Base):
    __tablename__ = "order_sheet_drafts"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    base_evidence_run_id = Column(String, ForeignKey("order_ocr_evidence_runs.id"), nullable=True)
    base_template_resolution_id = Column(String, nullable=True)
    base_menu_snapshot_id = Column(String, nullable=True)
    draft_sheet_json = Column(JSON, nullable=False)
    draft_state = Column(String, nullable=False, default="draft")
    blockers_json = Column(JSON, nullable=True)
    warnings_json = Column(JSON, nullable=True)
    latest_patch_candidate_id = Column(String, nullable=True)
    edited_by = Column(String, nullable=True)
    edited_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
