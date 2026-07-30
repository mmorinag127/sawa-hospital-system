import base64
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api import auth as auth_module
from src.api import portal
from src.db import engine
from src.main import app


client = TestClient(app)


def _seed_user(account: str, *, status: str = "active", systems: tuple[str, ...] = ("hospital",)) -> str:
    portal.ensure_portal_schema()
    user_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users(id, account, role, status) VALUES(:id, :account, 'operator', :status)"),
            {"id": user_id, "account": account, "status": status},
        )
        for system in systems:
            connection.execute(
                text("INSERT INTO user_system_access(user_id, system_key, enabled) VALUES(:id, :system, TRUE)"),
                {"id": user_id, "system": system},
            )
    auth_module.invalidate_user_cache()
    return user_id


def _cleanup(user_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM user_system_access WHERE user_id=:id"), {"id": user_id})
        connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
    auth_module.invalidate_user_cache()


def _google(monkeypatch, account: str) -> dict[str, str]:
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth_module, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda token, request: account)
    return {"Authorization": "Bearer verified-google-id-token"}


def test_registered_active_user_with_requested_grant_is_allowed(monkeypatch):
    account = "allowed.portal@example.com"
    user_id = _seed_user(account, systems=("hospital", "shift"))
    try:
        response = client.get("/portal/auth/me?system=hospital", headers=_google(monkeypatch, account))
        assert response.status_code == 200
        assert response.json()["account"] == account
        assert response.json()["systems"] == ["hospital", "shift"]
    finally:
        _cleanup(user_id)


def test_unregistered_user_is_denied(monkeypatch):
    response = client.get(
        "/portal/auth/me?system=hospital",
        headers=_google(monkeypatch, "missing.portal@example.com"),
    )
    assert response.status_code == 403


def test_inactive_user_is_denied(monkeypatch):
    account = "inactive.portal@example.com"
    user_id = _seed_user(account, status="inactive")
    try:
        response = client.get("/portal/auth/me?system=hospital", headers=_google(monkeypatch, account))
        assert response.status_code == 403
    finally:
        _cleanup(user_id)


def test_user_without_requested_system_is_denied(monkeypatch):
    account = "shift.only.portal@example.com"
    user_id = _seed_user(account, systems=("shift",))
    try:
        response = client.get("/portal/auth/me?system=hospital", headers=_google(monkeypatch, account))
        assert response.status_code == 403
    finally:
        _cleanup(user_id)


def test_missing_malformed_and_basic_auth_are_denied(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth_module, "AUTH_PROVIDER", "local")
    basic = base64.b64encode(b"admin:obsolete").decode("ascii")
    assert client.get("/portal/auth/me?system=hospital").status_code == 401
    assert client.get("/portal/auth/me?system=hospital", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get(
        "/portal/auth/me?system=hospital", headers={"Authorization": f"Basic {basic}"}
    ).status_code == 401


def test_google_verification_path_is_preserved(monkeypatch):
    account = "verified.portal@example.com"
    user_id = _seed_user(account)
    calls: list[str] = []
    try:
        monkeypatch.setenv("AUTH_DISABLED", "false")
        monkeypatch.setattr(auth_module, "AUTH_PROVIDER", "local")

        def verify(token, request):
            calls.append(token)
            return account

        monkeypatch.setattr(auth_module, "_verify_google_token", verify)
        response = client.get(
            "/portal/auth/me?system=hospital",
            headers={"Authorization": "Bearer google-id-token"},
        )
        assert response.status_code == 200
        assert calls == ["google-id-token"]
    finally:
        _cleanup(user_id)
