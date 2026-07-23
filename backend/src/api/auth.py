import base64
import os
import threading
import time

from fastapi import Depends, HTTPException, status, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import select

from src.db import session_scope
from src.models.user import User


class UserContext:
    def __init__(self, role: str):
        self.role = role


GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_IDS = [
    item.strip()
    for item in os.getenv("GOOGLE_OAUTH_CLIENT_IDS", GOOGLE_OAUTH_CLIENT_ID).split(",")
    if item.strip()
]


def _parse_emails(env_key: str) -> set[str]:
    raw = os.getenv(env_key, "")
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


ADMIN_SERVICE_ACCOUNTS = _parse_emails("ADMIN_SERVICE_ACCOUNTS")
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


def _auth_header(request: Request) -> str:
    return request.headers.get("Authorization", "")


def _basic_credentials(request: Request):
    auth = _auth_header(request)
    if not auth.startswith("Basic "):
        return None, None
    raw = auth.removeprefix("Basic ").strip()
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except Exception:
        return None, None
    if ":" not in decoded:
        return None, None
    return decoded.split(":", 1)


def _matches_basic(username: str | None, password: str | None, env_user: str | None, env_pass: str | None) -> bool:
    if not env_user or not env_pass:
        return False
    return username == env_user and password == env_pass


def _raise_basic_unauthorized(realm: str = "Orders"):
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": f'Basic realm="{realm}"'},
    )


def _raise_unauthorized():
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _get_bearer_token(request: Request) -> str | None:
    auth = _auth_header(request)
    if not auth.startswith("Bearer "):
        return None
    return auth.removeprefix("Bearer ").strip()


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
    google_email = _google_email_or_none(request)
    if google_email:
        if google_email in ADMIN_SERVICE_ACCOUNTS:
            return UserContext(role="admin")
        try:
            active_roles = _load_active_user_roles()
        except UserRoleLookupError:
            _raise_role_lookup_unavailable()
        registered_role = active_roles.get(str(google_email).lower())
        if registered_role == "admin":
            return UserContext(role="admin")
        if registered_role in {"operator"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    username, password = _basic_credentials(request)
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if _matches_basic(username, password, admin_user, admin_pass):
        return UserContext(role="admin")
    if (
        username
        and password
        and username == os.getenv("OPERATOR_USER")
        and password == os.getenv("OPERATOR_PASSWORD")
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    _raise_basic_unauthorized("Admin")


def get_current_operator(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="operator")
    google_email = _google_email_or_none(request)
    if google_email:
        if google_email in ADMIN_SERVICE_ACCOUNTS:
            return UserContext(role="operator")
        try:
            active_roles = _load_active_user_roles()
        except UserRoleLookupError:
            _raise_role_lookup_unavailable()
        registered_role = active_roles.get(str(google_email).lower())
        if registered_role in {"admin", "operator"}:
            return UserContext(role="operator")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    username, password = _basic_credentials(request)
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if _matches_basic(username, password, admin_user, admin_pass):
        return UserContext(role="operator")
    if (
        username
        and password
        and username == os.getenv("OPERATOR_USER")
        and password == os.getenv("OPERATOR_PASSWORD")
    ):
        return UserContext(role="operator")
    _raise_basic_unauthorized("Operator")


def get_current_user(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="admin")
    try:
        return get_current_admin(request)
    except HTTPException as exc:
        if exc.status_code not in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        }:
            raise
    return get_current_operator(request)


def require_role(required: str):
    dependency_source = get_current_admin if required == "admin" else get_current_operator

    def dependency(user: UserContext = Depends(dependency_source)):
        if user.role != required:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency
