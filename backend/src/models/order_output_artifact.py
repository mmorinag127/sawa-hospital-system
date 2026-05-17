from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderBaggingResult(Base):
    __tablename__ = "order_bagging_results"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    source_saved_sheet_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=False, index=True)
    source_ocr_result_id = Column(String, ForeignKey("order_ocr_evidence_runs.id"), nullable=True, index=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    payload_json = Column(JSON, nullable=False)
    payload_digest = Column(String, nullable=False, index=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OrderOutputBundle(Base):
    __tablename__ = "order_output_bundles"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    source_bagging_result_id = Column(String, ForeignKey("order_bagging_results.id"), nullable=False, index=True)
    source_saved_sheet_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=False, index=True)
    source_ocr_result_id = Column(String, ForeignKey("order_ocr_evidence_runs.id"), nullable=True, index=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    materialization_digest = Column(String, nullable=True, index=True)
    payload_json = Column(JSON, nullable=False)
    payload_digest = Column(String, nullable=False, index=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
