from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String

from src.db import Base


class OrderConfirmedSnapshot(Base):
    __tablename__ = "order_confirmed_snapshots"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)
    draft_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True)
    saved_sheet_id = Column(String, ForeignKey("order_sheet_drafts.id"), nullable=True, index=True)
    bagging_result_id = Column(String, ForeignKey("order_bagging_results.id"), nullable=True, index=True)
    output_bundle_id = Column(String, ForeignKey("order_output_bundles.id"), nullable=True, index=True)
    snapshot_digest = Column(String, nullable=False)
    snapshot_json = Column(JSON, nullable=False)
    confirmed_by = Column(String, nullable=True)
    confirmed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
