from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, JSON

from src.db import Base


class OcrJob(Base):
    __tablename__ = "ocr_jobs"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=True, index=True)
    uploaded_pdf_id = Column(String, ForeignKey("uploaded_pdfs.id"), nullable=True, index=True)
    order_document_id = Column(String, ForeignKey("order_documents.id"), nullable=True, index=True)
    input_artifact_digest = Column(String, nullable=True, index=True)
    status = Column(String, default="running")
    input_reference = Column(String, nullable=False)
    template_id = Column(String, nullable=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    output_reference = Column(String, nullable=True)
    metrics = Column(JSON, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
