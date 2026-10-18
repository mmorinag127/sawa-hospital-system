import os
import time
import json
import urllib.error
import urllib.request

from fastapi import Depends, HTTPException, status, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.db import session_scope


class UserContext:
    def __init__(self, role: str, account: str | None = None, systems: tuple[str, ...] | None = None):
        self.role = role
        self.account = account
        self.systems = systems


GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_IDS = [
    item.strip()
    for item in os.getenv("GOOGLE_OAUTH_CLIENT_IDS", GOOGLE_OAUTH_CLIENT_ID).split(",")
    if item.strip()
]
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "local").strip().lower()
PORTAL_AUTH_ME_URL = os.getenv("PORTAL_AUTH_ME_URL", "").strip()


def is_auth_disabled() -> bool:
    return os.getenv("AUTH_DISABLED", "false").lower() == "true"


def _automation_configs() -> list[tuple[str, str, str, str]]:
    return [
        (
            system,
            os.getenv(f"{prefix}_EMAIL", "").strip().lower(),
            os.getenv(f"{prefix}_AUDIENCE", "").strip(),
            os.getenv(f"{prefix}_SUBJECT", "").strip(),
        )
        for system, prefix in (
            ("shift", "AUTOMATION_AUTH"),
            ("school-lunch", "SCHOOL_LUNCH_AUTOMATION_AUTH"),
        )
    ]


def automation_system(account: str) -> str | None:
    matches = [system for system, email, _, _ in _automation_configs() if email and account.lower() == email]
    if len(matches) > 1:
        raise HTTPException(status_code=403, detail="Distinct automation identities required")
    return matches[0] if matches else None


def _require_system_access(user: UserContext, system: str) -> None:
    machine_system = automation_system(user.account or "")
    if machine_system is not None and system != machine_system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="System access denied")
    if user.systems is None or system not in user.systems:
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
        if not isinstance(payload, dict):
            raise ValueError("Invalid portal authentication response")
    except urllib.error.HTTPError as exc:
        if exc.code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            raise HTTPException(status_code=exc.code, detail="Portal authentication rejected") from exc
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Portal authentication unavailable") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Portal authentication unavailable") from exc
    role = str(payload.get("role") or "").lower()
    account = str(payload.get("account") or "").strip().lower()
    if role not in {"admin", "operator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not account:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    systems = payload.get("systems")
    if systems is not None and (not isinstance(systems, list) or any(not isinstance(item, str) for item in systems)):
        raise HTTPException(status_code=503, detail="Portal authentication unavailable")
    return UserContext(role=role, account=account, systems=tuple(systems) if systems is not None else None)


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
    configs = _automation_configs()
    for _, email, audience, subject in configs:
        if all((email, audience, subject)) and audience not in candidates:
            candidates.append(audience)
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
    matching_configs = [
        config for config in configs
        if config[1] and email == config[1]
    ]
    if not matching_configs and any(audience and payload.get("aud") == audience for _, _, audience, _ in configs):
        _raise_unauthorized()
    for machine_system, automation_email, automation_audience, automation_subject in matching_configs:
        if (
            not all((automation_email, automation_audience, automation_subject))
            or automation_audience in GOOGLE_OAUTH_CLIENT_IDS
            or payload.get("aud") != automation_audience
            or email != automation_email
            or payload.get("sub") != automation_subject
            or payload.get("email_verified") is not True
            or not email.endswith(".iam.gserviceaccount.com")
        ):
            _raise_unauthorized()
        if any(
            other_system != machine_system and (other_email == automation_email or other_subject == automation_subject)
            for other_system, other_email, other_audience, other_subject in configs
        ):
            _raise_unauthorized()
        issued_at, expires_at = payload.get("iat"), payload.get("exp")
        now = time.time()
        if (
            type(issued_at) not in (int, float)
            or type(expires_at) not in (int, float)
            or not now - 3600 <= issued_at <= now + 30
            or not now < expires_at <= issued_at + 3600
        ):
            _raise_unauthorized()
    if payload.get("email_verified") is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return str(email)


def _google_email_or_none(request: Request) -> str | None:
    token = _get_bearer_token(request)
    if not token:
        return None
    return _verify_google_token(token, request)


def _local_current_user(account: str | None) -> UserContext:
    identity = str(account or "").strip().lower()
    if not identity:
        raise HTTPException(status_code=403, detail="Active registered user required")
    try:
        with session_scope() as session:
            rows = session.execute(text(
                "SELECT u.id, u.account, u.role, u.status, a.system_key "
                "FROM users u LEFT JOIN user_system_access a "
                "ON a.user_id = u.id AND a.enabled = TRUE "
                "WHERE lower(trim(u.account)) = :account ORDER BY u.id, a.system_key"
            ), {"account": identity}).mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="User role lookup unavailable") from exc
    if not rows:
        raise HTTPException(status_code=403, detail="Forbidden")
    if len({row["id"] for row in rows}) != 1:
        raise HTTPException(status_code=403, detail="Unique active registered user required")
    row = rows[0]
    role = str(row["role"] or "").strip().lower()
    if str(row["status"] or "").strip().lower() != "active" or role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Forbidden")
    systems = tuple(row["system_key"] for row in rows if row["system_key"] is not None)
    machine_system = automation_system(identity)
    if machine_system is not None and role != "operator":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Automation operator required")
    if machine_system is not None and systems != (machine_system,):
        raise HTTPException(status_code=403, detail="Automation single-system access required")
    return UserContext(role, identity, systems)


def get_current_admin(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="admin")
    user = get_current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if AUTH_PROVIDER != "portal":
        _require_system_access(user, "hospital")
    return user


def get_current_operator(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="operator")
    user = get_current_user(request)
    if AUTH_PROVIDER != "portal":
        _require_system_access(user, "hospital")
    return UserContext(role="operator", account=user.account, systems=user.systems)


def get_current_user(request: Request) -> UserContext:
    if is_auth_disabled():
        return UserContext(role="admin")
    portal_user = _portal_user(request)
    if portal_user:
        return portal_user
    google_email = _google_email_or_none(request)
    if not google_email:
        _raise_unauthorized()
    return _local_current_user(google_email)


def require_role(required: str):
    dependency_source = get_current_admin if required == "admin" else get_current_operator

    def dependency(user: UserContext = Depends(dependency_source)):
        if user.role != required:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency


def require_portal_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not is_auth_disabled() and AUTH_PROVIDER != "portal":
        user = _local_current_user(user.account)
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user
