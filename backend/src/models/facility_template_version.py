from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class FacilityTemplateVersion(Base):
    __tablename__ = "facility_template_versions"

    id = Column(String, primary_key=True)
    facility_id = Column(String, ForeignKey("facilities.id"), nullable=False, index=True)
    version = Column(String, nullable=False)
    status = Column(String, nullable=False, default="draft", index=True)
    template_id = Column(String, nullable=True)
    source = Column(String, nullable=True)
    columns_json = Column(JSON, nullable=False)
    cells_json = Column(JSON, nullable=True)
    template_digest = Column(String, nullable=False, index=True)
    validation_json = Column(JSON, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    activated_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
