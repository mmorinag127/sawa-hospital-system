from contextlib import contextmanager
from datetime import datetime, timedelta
import time

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from src.api import auth, portal
from src.models.user import User, AuditLog
from src.models.portal_session import PortalSession, PortalLoginAttempt
from src.services import portal_session_service as sessions

ORIGIN = "https://portal.example.invalid"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "sessions.sqlite"))
    for table in (User.__table__, AuditLog.__table__, PortalSession.__table__, PortalLoginAttempt.__table__):
        table.create(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user_system_access (user_id TEXT, system_key TEXT, enabled BOOLEAN)"))
        connection.execute(text("INSERT INTO user_system_access VALUES ('user', 'hospital', TRUE), ('user', 'shift', TRUE)"))

    @contextmanager
    def scope():
        with Session(engine) as session, session.begin():
            yield session

    with scope() as session:
        session.add(User(id="user", account="human@example.invalid", role="admin", status="active"))
    for module in (auth, portal, sessions):
        monkeypatch.setattr(module, "session_scope", scope)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth, "GOOGLE_OAUTH_CLIENT_ID", "fixture-client")
    for key, value in {"PORTAL_PUBLIC_ORIGIN": ORIGIN, "PORTAL_SESSION_ABSOLUTE_SECONDS": "604800",
                       "PORTAL_SESSION_IDLE_SECONDS": "86400", "PORTAL_LOGIN_TTL_SECONDS": "300"}.items():
        monkeypatch.setenv(key, value)
    claims = {"aud": "fixture-client", "iss": "https://accounts.google.com", "exp": time.time() + 300,
              "sub": "google-subject", "email_verified": True, "email": "human@example.invalid"}
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", lambda *args: dict(claims))
    app = FastAPI()
    app.include_router(portal.router)

    @app.post("/mutation")
    def mutation(user=Depends(auth.get_current_operator)):
        return {"role": user.role}

    with TestClient(app, base_url=ORIGIN) as client:
        yield client, scope, claims
    engine.dispose()


def login(fixture):
    client, _, claims = fixture
    response = client.get("/portal/auth/start", params={"next": "/shift/"})
    assert response.status_code == 200
    assert "SameSite=none" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    claims["nonce"] = response.json()["nonce"]
    assert response.json()["login_uri"] == ORIGIN + "/api/portal/auth/callback"
    client.cookies.set("g_csrf_token", "csrf-google")
    return client.post("/portal/auth/callback", data={"credential": "synthetic-google-token", "g_csrf_token": "csrf-google"},
                       headers={"Origin": "https://accounts.google.com"}, follow_redirects=False)


def request(method="GET", headers=None):
    return Request({"type": "http", "method": method, "path": "/", "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]})


def test_redirect_issues_only_hashed_credentials_and_consumes_challenge(fixture):
    response = login(fixture)
    client, scope, _ = fixture
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/session?next=%2Fshift%2F"
    cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith(sessions.COOKIE + "="))
    assert all(value in cookie for value in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=604800"))
    token = client.cookies.get(sessions.COOKIE)
    with scope() as session:
        row = session.get(PortalSession, sessions.digest(token))
        assert row.google_sub == "google-subject"
        assert row.account == "human@example.invalid"
        assert row.expires_at - row.created_at == timedelta(days=7)
        assert row.idle_seconds == 86400
        assert token not in repr(row.__dict__)
        assert "synthetic-google-token" not in repr(row.__dict__)
        assert session.query(PortalLoginAttempt).count() == 0
    assert client.get("/portal/auth/me").json()["session_generation"]


@pytest.mark.parametrize("reuse", [False, True])
def test_account_change_cannot_transfer_existing_session(fixture, reuse):
    client, scope, _ = fixture
    assert login(fixture).status_code == 303
    token = client.cookies.get(sessions.COOKIE)
    with scope() as session:
        session.get(User, "user").account = "replacement@example.invalid"
        if reuse:
            session.add(User(id="other", account="human@example.invalid", role="admin", status="active"))
    assert client.get("/portal/auth/me").status_code == 401
    assert client.get("/portal/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_account_comparison_uses_existing_normalization(fixture):
    client, scope, _ = fixture
    assert login(fixture).status_code == 303
    with scope() as session:
        session.get(User, "user").account = " HUMAN@example.invalid "
    assert client.get("/portal/auth/me").status_code == 200


@pytest.mark.parametrize("change", ["inactive", "account"])
def test_admin_update_permanently_revokes_old_sessions(fixture, change):
    client, scope, _ = fixture
    assert login(fixture).status_code == 303
    token = client.cookies.get(sessions.COOKIE)
    with scope() as session:
        session.add(User(id="admin", account="admin@example.invalid", role="admin", status="active"))
    actor = auth.UserContext("admin", "admin@example.invalid")
    original = {"account": "human@example.invalid", "role": "admin", "status": "active", "systems": ["hospital", "shift"]}
    changed = dict(original)
    changed["status" if change == "inactive" else "account"] = "inactive" if change == "inactive" else "changed@example.invalid"
    portal._save(changed, actor, "user")
    portal._save(original, actor, "user")
    with scope() as session:
        assert session.get(PortalSession, sessions.digest(token)).revoked_at is not None
        assert session.query(AuditLog).filter_by(action="portal_user_updated").count() == 2
    assert client.get("/portal/auth/me").status_code == 401


def test_user_update_failure_rolls_back_revocation_and_audit(fixture, monkeypatch):
    client, scope, _ = fixture
    assert login(fixture).status_code == 303
    token = client.cookies.get(sessions.COOKIE)

    @contextmanager
    def failing_scope():
        with scope() as session:
            yield session
            session.flush()
            raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(portal, "session_scope", failing_scope)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        portal._save({"account": "human@example.invalid", "role": "admin", "status": "inactive", "systems": ["hospital"]},
                     auth.UserContext("admin", "human@example.invalid"), "user")
    with scope() as session:
        assert session.get(User, "user").status == "active"
        assert session.get(PortalSession, sessions.digest(token)).revoked_at is None
        assert session.query(AuditLog).count() == 0


@pytest.mark.parametrize("key,value", [("aud", "other"), ("iss", "evil"), ("exp", 0), ("sub", ""),
    ("email_verified", False), ("email", "unregistered@example.invalid"), ("nonce", "wrong")])
def test_callback_rejects_claims(fixture, key, value):
    client, scope, claims = fixture
    config = client.get("/portal/auth/start").json()
    claims["nonce"] = config["nonce"]
    claims[key] = value
    client.cookies.set("g_csrf_token", "fixture")
    response = client.post("/portal/auth/callback", data={"credential": "fixture", "g_csrf_token": "fixture"},
                           headers={"Origin": "https://accounts.google.com"})
    assert response.status_code in (401, 403)
    with scope() as session:
        assert session.query(PortalSession).count() == 0


def test_relogin_revokes_old_session_without_lax_cookie_on_callback(fixture):
    client, scope, claims = fixture
    assert login(fixture).status_code == 303
    old = client.cookies.get(sessions.COOKIE)
    claims["nonce"] = client.get("/portal/auth/start").json()["nonce"]
    challenge = client.cookies.get(sessions.LOGIN_COOKIE)
    client.cookies.delete(sessions.COOKIE)
    data = {"credential": "fixture", "g_csrf_token": "csrf-google"}
    headers = {"Origin": "https://accounts.google.com"}
    assert client.post("/portal/auth/callback", data=data, headers=headers, follow_redirects=False).status_code == 303
    with scope() as session:
        assert session.get(PortalSession, sessions.digest(old)).revoked_at is not None
    client.cookies.set(sessions.LOGIN_COOKIE, challenge)
    assert client.post("/portal/auth/callback", data=data, headers=headers).status_code == 401


@pytest.mark.parametrize("seconds,allowed", [(86399, True), (86400, False), (86401, False)])
def test_idle_boundary(fixture, monkeypatch, seconds, allowed):
    client, scope, _ = fixture
    login(fixture)
    token = client.cookies.get(sessions.COOKIE)
    with scope() as session:
        issued = session.get(PortalSession, sessions.digest(token)).created_at
    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return issued + timedelta(seconds=seconds)
    monkeypatch.setattr(sessions, "datetime", Clock)
    assert client.get("/portal/auth/me").status_code == (200 if allowed else 401)


@pytest.mark.parametrize("seconds,allowed", [(604799, True), (604800, False), (604801, False)])
def test_absolute_boundary_never_extended_by_use(fixture, monkeypatch, seconds, allowed):
    client, scope, _ = fixture
    login(fixture)
    token = client.cookies.get(sessions.COOKIE)
    with scope() as session:
        row = session.get(PortalSession, sessions.digest(token))
        issued, expiry = row.created_at, row.expires_at
        row.last_seen_at = issued + timedelta(seconds=seconds - 1)
    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return issued + timedelta(seconds=seconds)
    monkeypatch.setattr(sessions, "datetime", Clock)
    assert client.get("/portal/auth/me").status_code == (200 if allowed else 401)
    with scope() as session:
        assert session.get(PortalSession, sessions.digest(token)).expires_at == expiry


def test_current_role_system_and_inactive_are_not_cached(fixture):
    client, scope, _ = fixture
    login(fixture)
    with scope() as session:
        session.get(User, "user").role = "operator"
        session.execute(text("UPDATE user_system_access SET enabled=FALSE WHERE system_key='shift'"))
    assert client.get("/portal/auth/me").json()["role"] == "operator"
    assert client.get("/portal/auth/me?system=shift").status_code == 403
    with scope() as session:
        session.get(User, "user").status = "inactive"
    assert client.get("/portal/auth/me").status_code == 403


def test_direct_backend_cookie_csrf_and_logout(fixture):
    client, _, _ = fixture
    login(fixture)
    token = client.cookies.get(sessions.COOKIE)
    csrf = client.cookies.get(sessions.CSRF_COOKIE)
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": "https://evil.invalid", "X-Sawa-CSRF": csrf}):
        assert client.post("/mutation", headers=headers).status_code == 403
        assert client.post("/portal/auth/logout", headers=headers).status_code == 403
    headers = {"Origin": ORIGIN, "X-Sawa-CSRF": csrf}
    assert client.post("/mutation", headers=headers).status_code == 200
    assert client.post("/portal/auth/logout", headers=headers).status_code == 204
    assert client.get("/portal/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


@pytest.mark.parametrize("csrf_cookie", [None, "remaining-csrf"])
def test_legacy_or_expired_cookie_logout_is_idempotent(fixture, csrf_cookie):
    client, _, _ = fixture
    if csrf_cookie:
        client.cookies.set(sessions.CSRF_COOKIE, csrf_cookie)
    for _ in range(2):
        assert client.post("/portal/auth/logout", headers={"Origin": ORIGIN}).status_code == 204
    assert client.post("/portal/auth/logout", headers={"Origin": "https://evil.invalid"}).status_code == 403


def test_concurrent_revoke_cannot_be_undone(fixture, monkeypatch):
    client, scope, _ = fixture
    login(fixture)
    token = client.cookies.get(sessions.COOKIE)
    original = auth._local_current_user
    def revoke_during_lookup(account):
        identity = original(account)
        sessions.revoke(token)
        return identity
    monkeypatch.setattr(auth, "_local_current_user", revoke_during_lookup)
    assert client.get("/portal/auth/me").status_code == 401
    with scope() as session:
        assert session.get(PortalSession, sessions.digest(token)).revoked_at is not None


def test_google_expiry_does_not_shorten_issued_session(fixture):
    client, _, claims = fixture
    login(fixture)
    claims["exp"] = 0
    assert client.get("/portal/auth/me").status_code == 200


@pytest.mark.parametrize("authorization", ["Basic obsolete", "Bearer", "invalid"])
def test_explicit_invalid_authorization_does_not_fall_back_to_cookie(fixture, authorization):
    client, _, _ = fixture
    login(fixture)
    assert client.get("/portal/auth/me", headers={"Authorization": authorization}).status_code == 401


def test_machine_bearer_mutation_does_not_require_human_cookie_csrf(fixture, monkeypatch):
    client, scope, claims = fixture
    account = "machine@fixture.iam.gserviceaccount.com"
    audience = "https://fixture.invalid/machine"
    for key, value in {"AUTOMATION_AUTH_EMAIL": account, "AUTOMATION_AUTH_AUDIENCE": audience, "AUTOMATION_AUTH_SUBJECT": "machine-sub"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(auth, "GOOGLE_OAUTH_CLIENT_IDS", ["fixture-client"])
    claims.update(email=account, aud=audience, sub="machine-sub", iat=int(time.time()) - 1, exp=int(time.time()) + 300)
    with scope() as session:
        session.add(User(id="machine", account=account, role="operator", status="active"))
        session.execute(text("INSERT INTO user_system_access VALUES ('machine', 'shift', TRUE)"))
    # The real machine claim and registered-grant checks remain in _verify_google_token.
    result = client.get("/portal/auth/me?system=shift", headers={"Authorization": "Bearer machine-token"})
    assert result.status_code == 200
    assert "session_generation" not in result.json()
    claims["sub"] = "wrong-sub"
    assert client.get("/portal/auth/me", headers={"Authorization": "Bearer machine-token"}).status_code == 401


@pytest.mark.parametrize("failure", ["signature", "csrf", "origin", "expired-challenge"])
def test_callback_boundary_failures_do_not_issue_session(fixture, monkeypatch, failure):
    client, scope, claims = fixture
    claims["nonce"] = client.get("/portal/auth/start").json()["nonce"]
    client.cookies.set("g_csrf_token", "correct")
    if failure == "signature":
        def reject(*args):
            raise ValueError("private token detail")
        monkeypatch.setattr(auth.id_token, "verify_oauth2_token", reject)
    if failure == "expired-challenge":
        with scope() as session:
            session.query(PortalLoginAttempt).one().expires_at = datetime.utcnow() - timedelta(seconds=1)
    result = client.post("/portal/auth/callback", data={"credential": "private-credential", "g_csrf_token": "wrong" if failure == "csrf" else "correct"},
                        headers={"Origin": "https://evil.invalid" if failure == "origin" else "https://accounts.google.com"})
    assert result.status_code in (401, 403)
    assert "private" not in result.text
    with scope() as session:
        assert session.query(PortalSession).count() == 0


@pytest.mark.parametrize("destination", ["//evil.invalid", "/%2fexample.invalid", "/auth/session", "/api/orders", "/../evil", "/%252fexample", "/\\evil"])
def test_unsafe_destination_rejected(destination):
    with pytest.raises(HTTPException):
        sessions.local_destination(destination)
