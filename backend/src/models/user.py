from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON

from src.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    account = Column(String, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=False)
    fac = Column(String, nullable=True)
    wek = Column(String, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True)
    target_role = Column(String, nullable=False)
    type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    related_entity = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
