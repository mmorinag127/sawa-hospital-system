import base64
import os
from fastapi import Depends, HTTPException, status, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token


class UserContext:
    def __init__(self, role: str):
        self.role = role


AUTH_DISABLED = os.getenv("AUTH_DISABLED", "true").lower() == "true"
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()


def _parse_emails(env_key: str) -> set[str]:
    raw = os.getenv(env_key, "")
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


ALLOWED_EMAILS = _parse_emails("ALLOWED_EMAILS")
ADMIN_EMAILS = _parse_emails("ADMIN_EMAILS") or ALLOWED_EMAILS


def _basic_credentials(request: Request):
    auth = request.headers.get("Authorization", "")
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
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth.removeprefix("Bearer ").strip()


def _verify_google_token(token: str) -> str:
    if not GOOGLE_OAUTH_CLIENT_ID:
        _raise_unauthorized()
    try:
        payload = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_OAUTH_CLIENT_ID,
        )
    except Exception:  # noqa: BLE001
        _raise_unauthorized()
    email = payload.get("email")
    if not email:
        _raise_unauthorized()
    email = str(email).lower()
    if payload.get("email_verified") is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return str(email)


def _google_email_or_none(request: Request) -> str | None:
    token = _get_bearer_token(request)
    if not token:
        return None
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if admin_token and token == admin_token:
        return "admin-token"
    return _verify_google_token(token)


def get_current_admin(request: Request) -> UserContext:
    if AUTH_DISABLED:
        return UserContext(role="admin")
    google_email = _google_email_or_none(request)
    if google_email:
        if not ADMIN_EMAILS or google_email in ADMIN_EMAILS:
            return UserContext(role="admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    username, password = _basic_credentials(request)
    admin_user = os.getenv("ADMIN_USER") or os.getenv("OPERATOR_USER")
    admin_pass = os.getenv("ADMIN_PASSWORD") or os.getenv("OPERATOR_PASSWORD")
    if _matches_basic(username, password, admin_user, admin_pass):
        return UserContext(role="admin")
    _raise_basic_unauthorized("Admin")


def get_current_operator(request: Request) -> UserContext:
    if AUTH_DISABLED:
        return UserContext(role="operator")
    google_email = _google_email_or_none(request)
    if google_email:
        return UserContext(role="operator")
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


def require_role(required: str):
    dependency_source = get_current_admin if required == "admin" else get_current_operator

    def dependency(user: UserContext = Depends(dependency_source)):
        if user.role != required:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency
