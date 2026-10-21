from sqlalchemy import Column, String, DateTime, Integer, ForeignKey
from src.db import Base


class PortalSession(Base):
    __tablename__ = "portal_sessions"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    google_sub = Column(String(255), nullable=False)
    account = Column(String, nullable=False)
    csrf_hash = Column(String(64), nullable=False)
    generation = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    idle_seconds = Column(Integer, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class PortalLoginAttempt(Base):
    __tablename__ = "portal_login_attempts"
    token_hash = Column(String(64), primary_key=True)
    nonce_hash = Column(String(64), nullable=False)
    previous_session_hash = Column(String(64), nullable=True)
    return_path = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
