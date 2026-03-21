from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text

from src.db import Base


class OrderOcrRevision(Base):
    __tablename__ = "order_ocr_revisions"

    id = Column(String, primary_key=True)
    order_id = Column(String, nullable=False, index=True)
    ui_mode = Column(String, nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    changed = Column(Boolean, nullable=False, default=False)
    sheet_save_only = Column(Boolean, nullable=False, default=False)
    sheet_save_mode = Column(String, nullable=True)
    before_digest = Column(String, nullable=True)
    after_digest = Column(String, nullable=True)
    fields = Column(JSON, nullable=True)
    header = Column(JSON, nullable=True)
    row_ids = Column(JSON, nullable=True)
    rows = Column(JSON, nullable=True)
    markdown = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
