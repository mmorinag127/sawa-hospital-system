from datetime import datetime

from sqlalchemy import Column, Computed, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import deferred

from src.db import Base
from src.models.generated_json import GeneratedJSON


class OrderOcrEvidenceRun(Base):
    __tablename__ = "order_ocr_evidence_runs"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    schema_version = Column(String, nullable=False)
    producer_version = Column(String, nullable=True)
    source = Column(String, nullable=True)
    status = Column(String, nullable=False, default="ready")
    payload_json = Column(JSON, nullable=False)
    display_overlay_json = deferred(Column(GeneratedJSON, Computed("payload_json -> 'hakodate_overlay'", persisted=True)))
    artifact_manifest_json = Column(JSON, nullable=True)
    artifact_digest = Column(String, nullable=False, index=True)
    capabilities_json = Column(JSON, nullable=True)
    degraded_reasons_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
