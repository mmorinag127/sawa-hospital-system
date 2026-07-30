import os
import threading
import time
import json
import urllib.error
import urllib.request

from fastapi import Depends, HTTPException, status, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import select, text

from src.db import session_scope
from src.models.user import User


class UserContext:
    def __init__(self, role: str, account: str | None = None):
        self.role = role
        self.account = account


GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_IDS = [
    item.strip()
    for item in os.getenv("GOOGLE_OAUTH_CLIENT_IDS", GOOGLE_OAUTH_CLIENT_ID).split(",")
    if item.strip()
]
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "local").strip().lower()
PORTAL_AUTH_ME_URL = os.getenv("PORTAL_AUTH_ME_URL", "").strip()


_USER_ROLE_CACHE_TTL_SECONDS = max(float(os.getenv("AUTH_USER_CACHE_TTL_SECONDS", "15")), 0.0)
_USER_ROLE_CACHE_LOCK = threading.Lock()
_USER_ROLE_CACHE_EXPIRES_AT = 0.0
_USER_ROLE_CACHE: dict[str, str] = {}


class UserRoleLookupError(RuntimeError):
    pass


def is_auth_disabled() -> bool:
    return os.getenv("AUTH_DISABLED", "false").lower() == "true"


def invalidate_user_cache() -> None:
    global _USER_ROLE_CACHE_EXPIRES_AT, _USER_ROLE_CACHE
    with _USER_ROLE_CACHE_LOCK:
        _USER_ROLE_CACHE_EXPIRES_AT = 0.0
        _USER_ROLE_CACHE = {}


def _load_active_user_roles() -> dict[str, str]:
    global _USER_ROLE_CACHE_EXPIRES_AT, _USER_ROLE_CACHE

    now = time.monotonic()
    with _USER_ROLE_CACHE_LOCK:
        if now < _USER_ROLE_CACHE_EXPIRES_AT:
            return dict(_USER_ROLE_CACHE)

    try:
        roles: dict[str, str] = {}
        with session_scope() as session:
            users = session.execute(select(User)).scalars().all()
            for user in users:
                account = str(user.account or "").strip().lower()
                role = str(user.role or "").strip().lower()
                status_value = str(user.status or "active").strip().lower()
                if not account or status_value != "active":
                    continue
                if role not in {"admin", "operator"}:
                    continue
                if roles.get(account) == "admin":
                    continue
                roles[account] = role
    except Exception as exc:  # noqa: BLE001
        raise UserRoleLookupError("Failed to load active user roles") from exc

    with _USER_ROLE_CACHE_LOCK:
        _USER_ROLE_CACHE = roles
        _USER_ROLE_CACHE_EXPIRES_AT = now + _USER_ROLE_CACHE_TTL_SECONDS
    return dict(roles)


def _raise_role_lookup_unavailable():
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="User role lookup unavailable",
    )


def _has_system_access(account: str, system: str) -> bool:
    try:
        with session_scope() as session:
            return bool(
                session.execute(
                    text(
                        "SELECT 1 FROM users u "
                        "JOIN user_system_access usa ON usa.user_id = u.id "
                        "WHERE lower(u.account) = :account "
                        "AND lower(u.status) = 'active' "
                        "AND usa.system_key = :system AND usa.enabled = TRUE"
                    ),
                    {"account": account.strip().lower(), "system": system},
                ).scalar()
            )
    except Exception as exc:  # noqa: BLE001
        raise UserRoleLookupError("Failed to load system access") from exc


def _require_system_access(account: str, system: str) -> None:
    try:
        allowed = _has_system_access(account, system)
    except UserRoleLookupError:
        _raise_role_lookup_unavailable()
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="System access denied")


def _auth_header(request: Request) -> str:
    return request.headers.get("Authorization", "")


def _raise_unauthorized():
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _get_bearer_token(request: Request) -> str | None:
    auth = _auth_header(request)
    if not auth.startswith("Bearer "):
        return None
    return auth.removeprefix("Bearer ").strip()


def _portal_user(request: Request) -> UserContext | None:
    if AUTH_PROVIDER != "portal":
        return None
    authorization = _auth_header(request)
    if not authorization.startswith("Bearer "):
        _raise_unauthorized()
    if not PORTAL_AUTH_ME_URL:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Portal authentication unavailable")
    separator = "&" if "?" in PORTAL_AUTH_ME_URL else "?"
    upstream = urllib.request.Request(
        f"{PORTAL_AUTH_ME_URL}{separator}system=hospital",
        headers={"Authorization": authorization, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(upstream, timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        code = status.HTTP_403_FORBIDDEN if exc.code == 403 else status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=code, detail="Portal authentication rejected") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Portal authentication unavailable") from exc
    role = str(payload.get("role") or "").lower()
    account = str(payload.get("account") or "").strip().lower()
    if role not in {"admin", "operator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not account:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return UserContext(role=role, account=account)


def _audience_candidates(request: Request) -> list[str]:
    candidates: list[str] = []
    if GOOGLE_OAUTH_CLIENT_IDS:
        candidates.extend(GOOGLE_OAUTH_CLIENT_IDS)
    try:
        url = str(request.url)
        if url:
            candidates.append(url)
            candidates.append(url.rstrip("/"))
    except Exception:  # noqa: BLE001
        pass
    return [c for c in candidates if c]


def _verify_google_token(token: str, request: Request) -> str:
    candidates = _audience_candidates(request)
    if not candidates:
        _raise_unauthorized()
    payload = None
    for candidate in candidates:
        try:
            payload = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                candidate,
            )
            break
        except Exception:  # noqa: BLE001
            continue
    if not payload:
        _raise_unauthorized()
    email = payload.get("email")
    if not email:
        _raise_unauthorized()
    email = str(email).lower()
    if payload.get("email_verified") is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return str(email)


def _google_email_or_none(request: Request) -> str | None:
    token = _get_bearer_token(request)
    if not token:
        return None
    return _verify_google_token(token, request)


def get_current_admin(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="admin")
    portal_user = _portal_user(request)
    if portal_user:
        if portal_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return portal_user
    google_email = _google_email_or_none(request)
    if google_email:
        try:
            active_roles = _load_active_user_roles()
        except UserRoleLookupError:
            _raise_role_lookup_unavailable()
        registered_role = active_roles.get(str(google_email).lower())
        if registered_role == "admin":
            _require_system_access(google_email, "hospital")
            return UserContext(role="admin", account=google_email)
        if registered_role in {"operator"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    _raise_unauthorized()


def get_current_operator(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="operator")
    portal_user = _portal_user(request)
    if portal_user:
        return UserContext(role="operator", account=portal_user.account)
    google_email = _google_email_or_none(request)
    if google_email:
        try:
            active_roles = _load_active_user_roles()
        except UserRoleLookupError:
            _raise_role_lookup_unavailable()
        registered_role = active_roles.get(str(google_email).lower())
        if registered_role in {"admin", "operator"}:
            _require_system_access(google_email, "hospital")
            return UserContext(role="operator", account=google_email)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    _raise_unauthorized()


def get_current_user(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="admin")
    portal_user = _portal_user(request)
    if portal_user:
        return portal_user
    google_email = _google_email_or_none(request)
    if not google_email:
        _raise_unauthorized()
    try:
        active_roles = _load_active_user_roles()
    except UserRoleLookupError:
        _raise_role_lookup_unavailable()
    registered_role = active_roles.get(str(google_email).lower())
    if registered_role not in {"admin", "operator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return UserContext(role=registered_role, account=google_email)


def require_role(required: str):
    dependency_source = get_current_admin if required == "admin" else get_current_operator

    def dependency(user: UserContext = Depends(dependency_source)):
        if user.role != required:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency


def require_portal_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user
