import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from src.api.auth import UserContext, get_current_user, require_role
from src.db import engine, session_scope
from src.models.user import AuditLog

router = APIRouter(prefix="/portal")
SYSTEMS = {"hospital", "shift", "school-lunch"}


def ensure_portal_schema() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """CREATE TABLE IF NOT EXISTS user_system_access (
                user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                system_key VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY(user_id, system_key)
                )"""
            )
        )


def _systems(session, user_id: str) -> list[str]:
    return list(
        session.execute(
            text(
                "SELECT system_key FROM user_system_access "
                "WHERE user_id = :id AND enabled = TRUE ORDER BY system_key"
            ),
            {"id": user_id},
        ).scalars()
    )

@router.get("/auth/me")
def portal_me(system: str | None = None, user: UserContext = Depends(get_current_user)):
    if system is not None and system not in SYSTEMS:
        raise HTTPException(status_code=400, detail="Unknown system")
    if not user.account:
        raise HTTPException(status_code=403, detail="Registered account required")
    with session_scope() as session:
        row = (
            session.execute(
                text("SELECT id, account, status FROM users WHERE lower(account) = :account"),
                {"account": (user.account or "").lower()},
            ).mappings().first()
            if user.account
            else None
        )
        if not row or str(row["status"] or "").lower() != "active":
            raise HTTPException(status_code=403, detail="Active registered user required")
        systems = _systems(session, row["id"])
    if system and system not in systems:
        raise HTTPException(status_code=403, detail="System access denied")
    return {"role": user.role, "account": user.account, "systems": systems}

@router.get("/users", dependencies=[Depends(require_role("admin"))])
def list_users():
    with session_scope() as session:
        rows = session.execute(text("SELECT id,account,role,status FROM users ORDER BY lower(account)")).mappings().all()
        return {"items": [{**dict(row), "systems": _systems(session, row["id"])} for row in rows]}

def _save(body: dict, actor: UserContext, user_id: str | None = None):
    account = str(body.get("account") or "").strip().lower()
    role = str(body.get("role") or "operator")
    status = str(body.get("status") or "active")
    systems = sorted(set(body.get("systems") or []))
    if (
        "@" not in account
        or role not in {"admin", "operator"}
        or status not in {"active", "inactive"}
        or any(system not in SYSTEMS for system in systems)
    ):
        raise HTTPException(status_code=400, detail="Invalid user")
    with session_scope() as session:
        existing = session.execute(text("SELECT id FROM users WHERE lower(account)=:account"), {"account": account}).scalar()
        target = user_id or existing or str(uuid.uuid4())
        if user_id and not session.execute(
            text("SELECT id FROM users WHERE id = :id"), {"id": user_id}
        ).scalar():
            raise HTTPException(status_code=404, detail="User not found")
        if existing and existing != target:
            raise HTTPException(status_code=409, detail="Account already exists")
        values = {"id": target, "account": account, "role": role, "status": status}
        if session.execute(text("SELECT id FROM users WHERE id = :id"), {"id": target}).scalar():
            session.execute(
                text("UPDATE users SET account=:account, role=:role, status=:status WHERE id=:id"),
                values,
            )
        else:
            session.execute(
                text("INSERT INTO users(id, account, role, status) VALUES(:id, :account, :role, :status)"),
                values,
            )
        session.execute(text("DELETE FROM user_system_access WHERE user_id=:id"), {"id":target})
        for key in systems:
            session.execute(
                text(
                    "INSERT INTO user_system_access(user_id, system_key, enabled) "
                    "VALUES(:id, :key, TRUE)"
                ),
                {"id": target, "key": key},
            )
        session.add(
            AuditLog(
                id=str(uuid.uuid4()),
                actor=actor.account or "unknown",
                action="portal_user_updated" if user_id else "portal_user_created",
                target=target,
                metadata_json={
                    "account": account,
                    "role": role,
                    "status": status,
                    "systems": systems,
                },
            )
        )
    return {"id": target, "account": account, "role": role, "status": status, "systems": systems}

@router.post("/users")
def create_user(body: dict, actor: UserContext = Depends(require_role("admin"))):
    return {"user": _save(body, actor)}

@router.put("/users/{user_id}")
def update_user(user_id: str, body: dict, actor: UserContext = Depends(require_role("admin"))):
    return {"user": _save(body, actor, user_id)}
