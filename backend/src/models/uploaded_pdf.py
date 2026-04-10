from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint

from src.db import Base


class UploadedPdf(Base):
    __tablename__ = "uploaded_pdfs"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_uploaded_pdfs_message_id"),
    )

    id = Column(String, primary_key=True)
    message_id = Column(String, nullable=False)
    content_sha256 = Column(String, nullable=False, index=True)
    source_kind = Column(String, nullable=False, default="manual_upload")
    original_filename = Column(String, nullable=False)
    storage_uri = Column(String, nullable=False)
    received_at = Column(DateTime, nullable=False)
    page_count = Column(Integer, nullable=True)
    facility_hint = Column(String, nullable=True)
    week_hint = Column(String, nullable=True)
    facility_name = Column(String, nullable=True)
    skip_ocr = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default="pending", index=True)
    current_stage = Column(String, nullable=False, default="uploaded", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    last_error_code = Column(String, nullable=True)
    last_error_message = Column(String, nullable=True)
    alerted_at = Column(DateTime, nullable=True)
    current_order_id = Column(String, nullable=True, index=True)
    current_document_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UploadedPdfAttempt(Base):
    __tablename__ = "uploaded_pdf_attempts"
    __table_args__ = (
        UniqueConstraint("uploaded_pdf_id", "attempt_no", name="uq_uploaded_pdf_attempt_scope"),
    )

    id = Column(String, primary_key=True)
    uploaded_pdf_id = Column(String, nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    stage = Column(String, nullable=False)
    status = Column(String, nullable=False, default="running")
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    worker_instance = Column(String, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
