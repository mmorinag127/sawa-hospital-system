from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from src.db import session_scope
from src.models.user import User

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ALLOWED_ROLES = {"admin", "operator"}
_ALLOWED_STATUS = {"active", "inactive"}


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "account": user.account,
        "role": user.role,
        "status": user.status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _normalize_account(account: object) -> str:
    return str(account or "").strip().lower()


def _normalize_role(role: object) -> str:
    value = str(role or "operator").strip().lower()
    return value if value in _ALLOWED_ROLES else ""


def _normalize_status(status: object) -> str:
    value = str(status or "active").strip().lower()
    return value if value in _ALLOWED_STATUS else ""


def list_users() -> list[dict]:
    with session_scope() as session:
        users = (
            session.execute(
                select(User).order_by(User.created_at.desc(), User.account.asc())
            )
            .scalars()
            .all()
        )
        return [_serialize_user(item) for item in users]


def upsert_user(account: object, role: object = "operator", status: object = "active"):
    account_value = _normalize_account(account)
    role_value = _normalize_role(role)
    status_value = _normalize_status(status)
    if not account_value or not _EMAIL_PATTERN.fullmatch(account_value):
        return None, False, "invalid_account"
    if not role_value:
        return None, False, "invalid_role"
    if not status_value:
        return None, False, "invalid_status"

    with session_scope() as session:
        existing = (
            session.execute(select(User).where(User.account == account_value))
            .scalars()
            .first()
        )
        if existing:
            existing.role = role_value
            existing.status = status_value
            session.flush()
            return _serialize_user(existing), False, None

        user = User(
            id=f"USR{uuid4().hex[:10]}",
            account=account_value,
            role=role_value,
            status=status_value,
            created_at=datetime.utcnow(),
        )
        session.add(user)
        session.flush()
        return _serialize_user(user), True, None


def update_user(user_id: str, *, role: object | None = None, status: object | None = None):
    if not str(user_id or "").strip():
        return None, "user_not_found"

    with session_scope() as session:
        user = session.get(User, user_id)
        if not user:
            return None, "user_not_found"
        if role is not None:
            role_value = _normalize_role(role)
            if not role_value:
                return None, "invalid_role"
            user.role = role_value
        if status is not None:
            status_value = _normalize_status(status)
            if not status_value:
                return None, "invalid_status"
            user.status = status_value
        session.flush()
        return _serialize_user(user), None
