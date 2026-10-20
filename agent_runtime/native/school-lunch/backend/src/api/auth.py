import hmac
import os
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

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


ALLOWED_EMAILS = _parse_emails("ALLOWED_EMAILS")
ADMIN_EMAILS = _parse_emails("ADMIN_EMAILS")
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


def _has_valid_service_token(request: Request) -> bool:
    expected = os.getenv("SAWA_API_SERVICE_TOKEN", "").strip()
    supplied = request.headers.get("x-sawa-service-token", "")
    return bool(expected and hmac.compare_digest(supplied, expected))


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


def _shared_auth_required() -> bool:
    return os.getenv("SAWA_REQUIRE_API_AUTH", "").strip().lower() in {"1", "true", "yes"}


def _portal_user(request: Request) -> UserContext:
    def unavailable():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Canonical Portal authority unavailable")

    try:
        url = urlsplit(os.getenv("PORTAL_AUTH_ME_URL", "").strip())
        local_test = (os.getenv("SAWA_ENVIRONMENT", "").strip().lower() == "test"
                      and url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
        if (not url.hostname or url.username is not None or url.password is not None
                or url.fragment or (url.scheme != "https" and not local_test)):
            unavailable()
        url.port
        query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
                 if key != "system"]
        query.append(("system", "school-lunch"))
        endpoint = urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), ""))
    except ValueError:
        unavailable()
    token = _get_bearer_token(request)
    if not token:
        _raise_unauthorized()
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False, trust_env=False) as client:
            response = client.get(endpoint, headers={"Authorization": f"Bearer {token}",
                                                      "Accept": "application/json"})
            if response.status_code in {401, 403}:
                raise HTTPException(status_code=response.status_code, detail="Portal authorization rejected")
            if response.status_code != 200:
                unavailable()
            user = response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        unavailable()
    if (not isinstance(user, dict) or not isinstance(user.get("role"), str)
            or user["role"] not in {"admin", "operator"}
            or not isinstance(user.get("account"), str) or not user["account"].strip()
            or not isinstance(user.get("systems"), list)
            or not all(isinstance(system, str) for system in user["systems"])
            or "school-lunch" not in user["systems"]
            or ("auth_disabled" in user and user["auth_disabled"] is not False)):
        unavailable()
    return UserContext(role=user["role"])


def get_current_admin(request: Request) -> UserContext:
    if _shared_auth_required():
        user = _portal_user(request)
        if user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user
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
        if google_email in ADMIN_EMAILS:
            return UserContext(role="admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    _raise_unauthorized()


def get_current_operator(request: Request) -> UserContext:
    if _shared_auth_required():
        if _has_valid_service_token(request):
            return UserContext(role="operator")
        _portal_user(request)
        return UserContext(role="operator")
    if is_auth_disabled():
        return UserContext(role="operator")
    if _has_valid_service_token(request):
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
        if google_email in ALLOWED_EMAILS:
            return UserContext(role="operator")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    _raise_unauthorized()


def get_current_user(request: Request) -> UserContext:
    if _shared_auth_required():
        return _portal_user(request)
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
