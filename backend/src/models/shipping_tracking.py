from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String

from src.db import Base


class ShippingTrackingLog(Base):
    __tablename__ = "shipping_tracking_logs"

    id = Column(String, primary_key=True)
    tracking_key = Column(String, nullable=False, index=True)
    tracking_number = Column(String, nullable=False)
    ship_date = Column(Date, nullable=True, index=True)
    facility_name = Column(String, nullable=True)
    status = Column(String, nullable=False)
    delivered = Column(Boolean, nullable=False, default=False)
    arrival_text = Column(String, nullable=True)
    error = Column(String, nullable=True)
    source = Column(String, nullable=False, default="manual")
    looked_up_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ShippingTrackingCurrent(Base):
    __tablename__ = "shipping_tracking_current"

    tracking_key = Column(String, primary_key=True)
    tracking_number = Column(String, nullable=False)
    ship_date = Column(Date, nullable=True, index=True)
    facility_name = Column(String, nullable=True)
    status = Column(String, nullable=False)
    delivered = Column(Boolean, nullable=False, default=False)
    arrival_text = Column(String, nullable=True)
    error = Column(String, nullable=True)
    source = Column(String, nullable=False, default="manual")
    looked_up_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ShippingTrackingEvent(Base):
    __tablename__ = "shipping_tracking_events"

    id = Column(String, primary_key=True)
    tracking_key = Column(String, nullable=False, index=True)
    tracking_number = Column(String, nullable=False)
    event_order = Column(Integer, nullable=False, default=0)
    event_status = Column(String, nullable=False)
    event_at_text = Column(String, nullable=True)
    event_at = Column(DateTime, nullable=True, index=True)
    office_name = Column(String, nullable=True)
    looked_up_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
