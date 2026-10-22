import uuid
import hmac
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import text, select, delete, update, func
from sqlalchemy.exc import SQLAlchemyError

from src.api.auth import UserContext, automation_system, get_current_user, is_auth_disabled, require_portal_admin, _local_current_user
from src.api import auth
from src.db import session_scope
from src.models.user import AuditLog
from src.models.user import User
from src.models.portal_session import PortalSession, PortalLoginAttempt
from src.services import portal_session_service as sessions

router = APIRouter(prefix="/portal")
SYSTEMS = {"hospital", "shift", "school-lunch"}


@router.get("/auth/start")
def start_google_login(request: Request, response: Response, next: str = "/"):
    destination = sessions.local_destination(next)
    origin = sessions.public_origin()
    ttl = sessions.configured_seconds("PORTAL_LOGIN_TTL_SECONDS")
    if ttl > 600:
        raise HTTPException(503, "Login challenge lifetime exceeds limit")
    if not auth.GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(503, "Google login unavailable")
    challenge, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    previous = request.cookies.get(sessions.COOKIE)
    with session_scope() as session:
        session.add(PortalLoginAttempt(token_hash=sessions.digest(challenge), nonce_hash=sessions.digest(nonce),
            previous_session_hash=sessions.digest(previous) if previous else None,
            return_path=destination, expires_at=datetime.utcnow() + timedelta(seconds=ttl)))
    response.set_cookie(sessions.LOGIN_COOKIE, challenge, max_age=ttl, secure=True, httponly=True, samesite="none", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"client_id": auth.GOOGLE_OAUTH_CLIENT_ID, "nonce": nonce,
        "login_uri": origin + "/api/portal/auth/callback"}


@router.post("/auth/callback")
async def google_callback(request: Request):
    # Cross-origin form redirects can serialize their origin as "null".
    # Identity still requires double-submit CSRF, signed claims and a one-use nonce.
    if request.headers.get("origin") not in {"null", "https://accounts.google.com", sessions.public_origin()}:
        raise HTTPException(403, "Google callback origin rejected")
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(400, "Invalid Google response")
    raw = await request.body()
    if len(raw) > 16384:
        raise HTTPException(400, "Invalid Google response")
    try:
        body = parse_qs(raw.decode("utf-8"), strict_parsing=True)
    except (UnicodeError, ValueError):
        raise HTTPException(400, "Invalid Google response") from None
    csrf = body.get("g_csrf_token", [])
    credential = body.get("credential", [])
    cookie_csrf = request.cookies.get("g_csrf_token", "")
    challenge = request.cookies.get(sessions.LOGIN_COOKIE, "")
    if (len(csrf) != 1 or not cookie_csrf or not hmac.compare_digest(csrf[0], cookie_csrf)
            or len(credential) != 1 or not challenge):
        raise HTTPException(403, "Google callback CSRF rejected")
    try:
        payload = auth.id_token.verify_oauth2_token(credential[0], auth.google_requests.Request(), auth.GOOGLE_OAUTH_CLIENT_ID)
    except Exception:
        raise HTTPException(401, "Google identity rejected") from None
    if (not auth.GOOGLE_OAUTH_CLIENT_ID or payload.get("aud") != auth.GOOGLE_OAUTH_CLIENT_ID
            or payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
            or type(payload.get("exp")) not in (int, float) or payload["exp"] <= time.time()
            or not isinstance(payload.get("sub"), str) or not 1 <= len(payload["sub"]) <= 255
            or payload.get("email_verified") is not True or not isinstance(payload.get("email"), str)
            or not isinstance(payload.get("nonce"), str)):
        raise HTTPException(401, "Google identity rejected")
    account = payload["email"].strip().lower()
    identity = _local_current_user(account)
    if automation_system(account) is not None:
        raise HTTPException(403, "Human login required")
    with session_scope() as session:
        attempt = session.get(PortalLoginAttempt, sessions.digest(challenge))
        now = datetime.utcnow()
        if (attempt is None or attempt.expires_at <= now
                or not hmac.compare_digest(attempt.nonce_hash, sessions.digest(payload["nonce"]))):
            raise HTTPException(401, "Login attempt expired or consumed")
        destination = sessions.local_destination(attempt.return_path)
        previous_session_hash = attempt.previous_session_hash
        consumed = session.execute(delete(PortalLoginAttempt).where(
            PortalLoginAttempt.token_hash == attempt.token_hash, PortalLoginAttempt.expires_at > now)).rowcount
        if consumed != 1:
            raise HTTPException(401, "Login attempt expired or consumed")
        user = session.execute(select(User).where(func.lower(func.trim(User.account)) == identity.account, User.status == "active")).scalar_one_or_none()
        if user is None:
            raise HTTPException(403, "Active registered user required")
        # The business Lax cookie is absent on Google's cross-site callback POST.
        if previous_session_hash:
            session.execute(update(PortalSession).where(PortalSession.token_hash == previous_session_hash).values(revoked_at=now))
        token, csrf_token, lifetime = sessions.issue(session, user.id, payload["sub"], now)
    response = RedirectResponse("/auth/session?next=" + quote(destination, safe=""), status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.set_cookie(sessions.COOKIE, token, max_age=lifetime, secure=True, httponly=True, samesite="lax", path="/")
    response.set_cookie(sessions.CSRF_COOKIE, csrf_token, max_age=lifetime, secure=True, httponly=False, samesite="lax", path="/")
    response.delete_cookie(sessions.LOGIN_COOKIE, secure=True, httponly=True, samesite="none", path="/")
    return response


@router.post("/auth/logout")
def logout(request: Request):
    if request.headers.get("origin") != sessions.public_origin():
        raise HTTPException(403, "Portal logout origin rejected")
    token = request.cookies.get(sessions.COOKIE)
    if token:
        sessions.require_cookie_csrf(request)
        sessions.revoke(token)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    for name in (sessions.COOKIE, sessions.CSRF_COOKIE):
        response.delete_cookie(name, secure=True, httponly=name == sessions.COOKIE, samesite="lax", path="/")
    return response


@router.post("/users/{user_id}/revoke-sessions", dependencies=[Depends(require_portal_admin)])
def revoke_user_sessions(user_id: str):
    with session_scope() as session:
        session.execute(update(PortalSession).where(PortalSession.user_id == user_id).values(revoked_at=datetime.utcnow()))
    return Response(status_code=204)


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
def portal_me(system: str | None = None, user: UserContext = Depends(get_current_user), request: Request = None, response: Response = None):
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    if system is not None and system not in SYSTEMS:
        raise HTTPException(status_code=400, detail="Unknown system")
    if not user.account:
        raise HTTPException(status_code=403, detail="Registered account required")
    if auth.AUTH_PROVIDER != "portal":
        user = _local_current_user(user.account)
    if user.systems is None:
        raise HTTPException(status_code=503, detail="Portal authentication unavailable")
    systems = list(user.systems)
    machine_system = automation_system(user.account)
    if machine_system is not None and systems != [machine_system]:
        raise HTTPException(status_code=403, detail="Automation single-system access required")
    if system and system not in systems:
        raise HTTPException(status_code=403, detail="System access denied")
    result = {"role": user.role, "account": user.account, "systems": systems}
    if request is not None and getattr(request.state, "portal_session_generation", None):
        result["session_generation"] = request.state.portal_session_generation
    return result


@router.post("/automation/auth")
def automation_login(system: str = "shift", user: UserContext = Depends(get_current_user)):
    if system not in {"shift", "school-lunch"}:
        raise HTTPException(status_code=400, detail="Unknown automation system")
    if not user.account or automation_system(user.account) != system or user.role != "operator":
        raise HTTPException(status_code=403, detail="Automation operator required")
    result = portal_me(system=system, user=user)
    with session_scope() as session:
        session.add(AuditLog(
            id=str(uuid.uuid4()),
            actor=user.account,
            action="automation_login",
            target=system,
            metadata_json={"systems": result["systems"]},
        ))
    return result

@router.get("/users", dependencies=[Depends(require_portal_admin)])
def list_users():
    with session_scope() as session:
        rows = session.execute(text("SELECT id,account,role,status FROM users ORDER BY lower(account)")).mappings().all()
        return {"items": [{**dict(row), "systems": _systems(session, row["id"])} for row in rows]}


def _require_current_write_actor(session, actor: UserContext, account: str, user_id: str | None):
    if is_auth_disabled():
        return
    actor_account = str(actor.account or "").strip().lower()
    if not actor_account:
        raise HTTPException(status_code=403, detail="Active registered admin required")
    try:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            # Reserve the writer before reading authority; a retired actor cannot
            # upgrade an earlier read snapshot into an authorized write.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif dialect != "postgresql":
            raise HTTPException(status_code=503, detail="Admin authority lookup unavailable")
        statement = (
            "SELECT id, account, role, status FROM users "
            "WHERE lower(trim(account)) IN (:actor, :account) OR id = :target ORDER BY id"
        )
        if dialect == "postgresql":
            # Both administrators editing each other take the same lock order.
            # NOWAIT makes a competing retirement an explicit retryable conflict.
            statement += " FOR UPDATE NOWAIT"
        rows = session.execute(text(statement), {
            "actor": actor_account, "account": account, "target": user_id,
        }).mappings().all()
    except SQLAlchemyError as exc:
        original = getattr(exc, "orig", None)
        code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
        sqlite_code = getattr(original, "sqlite_errorcode", None)
        if code in {"55P03", "40P01", "40001"} or (
            sqlite_code is not None and sqlite_code & 255 in {5, 6}
        ):
            raise HTTPException(status_code=409, detail="Admin authority write conflict") from exc
        raise HTTPException(status_code=503, detail="Admin authority lookup unavailable") from exc
    actors = [row for row in rows if str(row["account"]).strip().lower() == actor_account]
    if len(actors) != 1 or str(actors[0]["status"]).strip().lower() != "active" or str(actors[0]["role"]).strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="Active registered admin required")


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
        _require_current_write_actor(session, actor, account, user_id)
        matches = session.execute(
            text("SELECT id FROM users WHERE lower(trim(account))=:account"),
            {"account": account},
        ).scalars().all()
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="Account matches multiple users")
        existing = matches[0] if matches else None
        target = user_id or existing or str(uuid.uuid4())
        if user_id and not session.execute(
            text("SELECT id FROM users WHERE id = :id"), {"id": user_id}
        ).scalar():
            raise HTTPException(status_code=404, detail="User not found")
        if existing and existing != target:
            raise HTTPException(status_code=409, detail="Account already exists")
        values = {"id": target, "account": account, "role": role, "status": status}
        previous = session.execute(text("SELECT account FROM users WHERE id = :id"), {"id": target}).scalar()
        if previous is not None:
            if status == "inactive" or str(previous).strip().lower() != account:
                session.execute(update(PortalSession).where(
                    PortalSession.user_id == target, PortalSession.revoked_at.is_(None),
                ).values(revoked_at=datetime.utcnow()))
            session.execute(
                text("UPDATE users SET account=:account, role=:role, status=:status WHERE id=:id"),
                values,
            )
        else:
            session.execute(
                text("INSERT INTO users(id, account, role, status, created_at) VALUES(:id, :account, :role, :status, CURRENT_TIMESTAMP)"),
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
def create_user(body: dict, actor: UserContext = Depends(require_portal_admin)):
    return {"user": _save(body, actor)}

@router.put("/users/{user_id}")
def update_user(user_id: str, body: dict, actor: UserContext = Depends(require_portal_admin)):
    return {"user": _save(body, actor, user_id)}
