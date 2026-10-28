from datetime import datetime
from sqlalchemy import Column, Computed, String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import deferred
from sqlalchemy.ext.hybrid import hybrid_property

from src.db import Base
from src.models.generated_json import GeneratedJSON
from src.models.ocr_display_projection_v1 import DisplayProjection
from src.models.ocr_metrics_v1 import MergeMetrics, split_metrics


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
    evidence_metrics = Column("metrics", JSON, nullable=True)
    progress_metrics = Column(JSON, nullable=True)

    @hybrid_property
    def metrics(self):
        base, progress = self.evidence_metrics, self.progress_metrics
        cached = self.__dict__.get("_metrics_value")
        if cached is not None and cached[0] is base and cached[1] is progress:
            return cached[2]
        if not isinstance(progress, dict) or not progress:
            return base
        value = {**(base if isinstance(base, dict) else {}), **progress}
        self.__dict__["_metrics_value"] = (base, progress, value)
        return value

    @metrics.inplace.setter
    def _set_metrics(self, value):
        self.evidence_metrics, self.progress_metrics = split_metrics(value)
        self.__dict__["_metrics_value"] = (self.evidence_metrics, self.progress_metrics, value)

    @metrics.inplace.expression
    @classmethod
    def _metrics_expression(cls):
        return MergeMetrics(cls.evidence_metrics, cls.progress_metrics)

    @metrics.inplace.update_expression
    @classmethod
    def _metrics_update(cls, value):
        base, progress = split_metrics(value)
        return [(cls.evidence_metrics, base), (cls.progress_metrics, progress)]
    display_read_json = deferred(Column(GeneratedJSON, Computed(DisplayProjection(), persisted=True)))
    display_current_document_json = deferred(Column(GeneratedJSON, Computed(
        "CASE WHEN metrics ->> 'workflow_attempt_id' IS NULL "
        "THEN metrics -> 'nonworkflow_execution' -> 'orders' -> order_id -> 'order' -> 'current_document_id' "
        "ELSE metrics -> 'reparse_read_input' -> 'order' -> 'current_document_id' END", persisted=True)))
    display_native_template_json = deferred(Column(GeneratedJSON, Computed(
        "metrics -> 'nonworkflow_execution' -> 'orders' -> order_id -> 'order' -> 'template_version_id'", persisted=True)))
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
