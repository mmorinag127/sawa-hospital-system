from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, Index, event, func
from src.models.menu_upload_scope_v1 import scope_default, scope_kind

from src.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    account = Column(String, nullable=False)
    status = Column(String, default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    __table_args__ = (Index("ix_users_normalized_account", func.lower(func.trim(account))),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_id", "created_at", "id"),
        Index("ix_audit_logs_action_target_created_id", "action", "target", "created_at", "id"),
        Index("ix_audit_logs_menu_scope_latest", "action", "target", "menu_scope_kind", "created_at", "id"),
    )

    id = Column(String, primary_key=True)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=False)
    fac = Column(String, nullable=True)
    wek = Column(String, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True)
    menu_scope_kind = Column(String(12), nullable=False, default=scope_default, server_default='invalid')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)


@event.listens_for(AuditLog, "before_insert")
@event.listens_for(AuditLog, "before_update")
def _audit_scope(_mapper, _connection, entry):
    entry.menu_scope_kind = scope_kind(entry.metadata_json)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True)
    target_role = Column(String, nullable=False)
    type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    related_entity = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
