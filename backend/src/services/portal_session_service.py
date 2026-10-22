"""Opaque portal credentials; Google tokens are never persisted."""
from datetime import datetime, timedelta
import hashlib
import hmac
import os
import re
import secrets
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException
from sqlalchemy import update
from src.db import control_session_scope as session_scope
from src.models.portal_session import PortalSession
from src.models.user import User

COOKIE = "__Host-sawa_session"
CSRF_COOKIE = "__Host-sawa_csrf"
LOGIN_COOKIE = "__Host-sawa_login"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configured_seconds(name):
    value = os.environ.get(name, "")
    if not re.fullmatch(r"[1-9][0-9]{0,7}", value):
        raise HTTPException(503, "Portal session configuration unavailable")
    return int(value)


def public_origin():
    value = os.environ.get("PORTAL_PUBLIC_ORIGIN", "")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise HTTPException(503, "Portal origin unavailable")
    return value


def local_destination(value):
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise HTTPException(400, "Invalid return destination")
    decoded = unquote(value)
    path = urlsplit(decoded).path
    if (re.search(r"[\\\x00-\x20\x7f]", decoded) or decoded.startswith("//")
            or re.search(r"%(?:2f|5c|2e|25)", decoded, re.I)
            or any(part in {".", "..", "api", "_next"} for part in path.split("/"))
            or re.match(r"^/(?:login|logout|auth)(?:/|$)|^/school-lunch/auth(?:/|$)", path)):
        raise HTTPException(400, "Invalid return destination")
    return value


def require_cookie_csrf(request, expected_hash=None):
    token = request.headers.get("x-sawa-csrf", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if (request.headers.get("origin") != public_origin() or not token or not cookie
            or not hmac.compare_digest(token, cookie)
            or (expected_hash is not None and not hmac.compare_digest(digest(token), expected_hash))):
        raise HTTPException(403, "Portal CSRF rejected")


def issue(session, user_id, google_sub, now=None):
    now = now or datetime.utcnow()
    absolute = configured_seconds("PORTAL_SESSION_ABSOLUTE_SECONDS")
    idle = configured_seconds("PORTAL_SESSION_IDLE_SECONDS")
    if idle > absolute:
        raise HTTPException(503, "Portal session configuration unavailable")
    token, csrf = "ps_" + secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    user = session.get(User, user_id)
    account = str(user.account or "").strip().lower() if user is not None else ""
    if not account:
        raise HTTPException(401, "Invalid portal account")
    row = PortalSession(token_hash=digest(token), user_id=user_id, google_sub=google_sub, csrf_hash=digest(csrf),
        account=account,
        generation=secrets.token_hex(16), created_at=now, last_seen_at=now,
        expires_at=now + timedelta(seconds=absolute), idle_seconds=idle)
    session.add(row)
    return token, csrf, absolute


def authenticate(token, request, *, cookie_auth=False):
    from src.api.auth import _local_current_user, automation_system
    if not re.fullmatch(r"ps_[A-Za-z0-9_-]{43}", token):
        raise HTTPException(401, "Invalid portal session")
    now = datetime.utcnow()
    with session_scope() as session:
        row = session.get(PortalSession, digest(token))
        if (row is None or row.revoked_at is not None or row.expires_at <= now
                or row.last_seen_at + timedelta(seconds=row.idle_seconds) <= now):
            raise HTTPException(401, "Portal session expired or revoked")
        if cookie_auth and request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_cookie_csrf(request, row.csrf_hash)
        user = session.get(User, row.user_id)
        if user is None or user.status != "active":
            raise HTTPException(403, "Active registered user required")
        if str(user.account or "").strip().lower() != row.account:
            raise HTTPException(401, "Portal account changed")
        identity = _local_current_user(row.account)
        if automation_system(identity.account) is not None:
            raise HTTPException(403, "Human portal session required")
        # Do not resurrect a session concurrently revoked after the initial read.
        changed = session.execute(update(PortalSession).where(
            PortalSession.token_hash == row.token_hash, PortalSession.revoked_at.is_(None),
            PortalSession.expires_at > now,
            PortalSession.last_seen_at > now - timedelta(seconds=row.idle_seconds),
        ).values(last_seen_at=now)).rowcount
        if changed != 1:
            raise HTTPException(401, "Portal session expired or revoked")
        request.state.portal_session_generation = row.generation
        return identity


def revoke(token):
    with session_scope() as session:
        session.execute(update(PortalSession).where(PortalSession.token_hash == digest(token),
            PortalSession.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))
