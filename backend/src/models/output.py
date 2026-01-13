from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Date, ForeignKey, JSON

from src.db import Base


class Bag(Base):
    __tablename__ = "bags"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    date = Column(Date, nullable=True)
    daypart = Column(String, nullable=True)
    menu_name = Column(String, nullable=True)
    diet_type = Column(String, nullable=True)
    area_id = Column(String, nullable=True)
    bag_type = Column(String, nullable=True)
    quantity = Column(Float, nullable=False)


class LabelRow(Base):
    __tablename__ = "label_rows"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    bag_id = Column(String, ForeignKey("bags.id"), nullable=True)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class DeliveryNote(Base):
    __tablename__ = "delivery_notes"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    facility_code = Column(String, nullable=False)
    date = Column(Date, nullable=True)
    file_uri = Column(String, nullable=True)
    payload_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ManufacturingAggregateRow(Base):
    __tablename__ = "manufacturing_aggregate_rows"

    id = Column(String, primary_key=True)
    week_code = Column(String, nullable=False)
    facility_code = Column(String, nullable=False)
    menu_name = Column(String, nullable=True)
    diet_type = Column(String, nullable=True)
    area_id = Column(String, nullable=True)
    bag_type = Column(String, nullable=True)
    quantity = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
