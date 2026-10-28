from datetime import datetime
from sqlalchemy import Column, Computed, String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import deferred

from src.db import Base
from src.models.generated_json import GeneratedJSON


class OcrJob(Base):
    __tablename__ = "ocr_jobs"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=True, index=True)
    uploaded_pdf_id = Column(String, ForeignKey("uploaded_pdfs.id"), nullable=True, index=True)
    order_document_id = Column(String, ForeignKey("order_documents.id"), nullable=True, index=True)
    input_artifact_digest = Column(String, nullable=True, index=True)
    status = Column(String, default="running", index=True, nullable=False)
    input_reference = Column(String, nullable=False)
    template_id = Column(String, nullable=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    output_reference = Column(String, nullable=True)
    metrics = Column(JSON, nullable=True)
    display_current_document_json = deferred(Column(GeneratedJSON, Computed(
        "CASE WHEN metrics ->> 'workflow_attempt_id' IS NULL "
        "THEN metrics -> 'nonworkflow_execution' -> 'orders' -> order_id -> 'order' -> 'current_document_id' "
        "ELSE metrics -> 'reparse_read_input' -> 'order' -> 'current_document_id' END", persisted=True)))
    display_native_template_json = deferred(Column(GeneratedJSON, Computed(
        "metrics -> 'nonworkflow_execution' -> 'orders' -> order_id -> 'order' -> 'template_version_id'", persisted=True)))
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
