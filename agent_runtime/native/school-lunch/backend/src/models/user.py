from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, Index, event

from src.db import Base
from src.account_identity_v1 import account_identity


def _default_account_lookup(context):
    return account_identity(context.get_current_parameters().get('account'))


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    account = Column(String, nullable=False)
    account_lookup = Column(String, nullable=False, default=_default_account_lookup)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index('ix_users_normalized_account', 'account_lookup'),)


@event.listens_for(User, 'before_insert')
@event.listens_for(User, 'before_update')
def _update_account_lookup(_mapper, _connection, user):
    user.account_lookup = account_identity(user.account)


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
