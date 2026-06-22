from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Date, JSON
from sqlalchemy.orm import relationship

from src.db import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True)
    facility_code = Column(String, nullable=True, index=True)
    week_code = Column(String, nullable=True, index=True)
    status = Column(String, default="要確認", index=True)
    current_document_id = Column(String, nullable=True)
    superseded_document_ids = Column(JSON, nullable=True)
    document_uri = Column(String, nullable=False)
    message_id = Column(String, nullable=False, index=True)
    received_at = Column(DateTime, default=datetime.utcnow, index=True)
    lines_updated_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    archived_by = Column(String, nullable=True)
    template_version_id = Column(String, ForeignKey("facility_template_versions.id"), nullable=True, index=True)

    lines = relationship("OrderLine", back_populates="order", cascade="all, delete-orphan")


class OrderLine(Base):
    __tablename__ = "order_lines"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    confirmed_snapshot_id = Column(String, ForeignKey("order_confirmed_snapshots.id"), nullable=True, index=True)
    line_digest = Column(String, nullable=True, index=True)
    line_id = Column(String, nullable=True)
    date = Column(Date, nullable=True)
    daypart = Column(String, nullable=True)
    menu_name = Column(String, nullable=True)
    diet_type = Column(String, nullable=True)
    area_id = Column(String, nullable=True)
    bag_type = Column(String, nullable=True)
    quantity_original = Column(Float, nullable=True)
    quantity_corrected = Column(Float, nullable=True)
    change_note = Column(String, nullable=True)

    order = relationship("Order", back_populates="lines")


class OrderMenuSnapshot(Base):
    __tablename__ = "order_menu_snapshots"

    id = Column(String, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, unique=True)
    snapshot_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
