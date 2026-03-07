from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String

from src.db import Base


class ShippingTrackingLog(Base):
    __tablename__ = "shipping_tracking_logs"

    id = Column(String, primary_key=True)
    tracking_key = Column(String, nullable=False, index=True)
    tracking_number = Column(String, nullable=False)
    facility_name = Column(String, nullable=True)
    status = Column(String, nullable=False)
    delivered = Column(Boolean, nullable=False, default=False)
    arrival_text = Column(String, nullable=True)
    error = Column(String, nullable=True)
    source = Column(String, nullable=False, default="manual")
    looked_up_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
