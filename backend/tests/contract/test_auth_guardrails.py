import base64
import importlib
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
from src.main import app  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _bearer_header(token: str = "google-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_auth_default_fails_closed_when_env_missing(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("OPERATOR_USER", raising=False)
    monkeypatch.delenv("OPERATOR_PASSWORD", raising=False)
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/orders")
    assert res.status_code == 401


def test_auth_can_be_explicitly_disabled_in_tests(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/auth/config")
    assert res.status_code == 200
    assert res.json()["auth_disabled"] is True

    me_res = client.get("/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["role"] == "admin"


def test_auth_enables_basic_operator_when_env_present(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/orders", headers=_basic_header("operator", "secret"))
    assert res.status_code == 200

    me_res = client.get("/auth/me", headers=_basic_header("operator", "secret"))
    assert me_res.status_code == 200
    assert me_res.json()["role"] == "operator"


def test_auth_me_returns_admin_for_basic_admin(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/auth/me", headers=_basic_header("admin", "secret"))
    assert res.status_code == 200
    assert res.json()["role"] == "admin"


def test_operator_basic_cannot_access_admin_route(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/users", headers=_basic_header("operator", "operator-secret"))
    assert res.status_code == 403


def test_google_admin_requires_registered_role_or_admin_allowlist(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda _token, _request: "user@example.com")
    monkeypatch.setattr(auth_module, "_load_active_user_roles", lambda: {})

    client = TestClient(app)
    res = client.get("/users", headers=_bearer_header())
    assert res.status_code == 403


def test_google_operator_requires_registered_role_or_operator_allowlist(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda _token, _request: "user@example.com")
    monkeypatch.setattr(auth_module, "_load_active_user_roles", lambda: {})

    client = TestClient(app)
    res = client.get("/orders", headers=_bearer_header())
    assert res.status_code == 403


def test_allowed_email_does_not_grant_google_admin(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ALLOWED_EMAILS", "user@example.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda _token, _request: "user@example.com")
    monkeypatch.setattr(auth_module, "_load_active_user_roles", lambda: {})

    client = TestClient(app)
    res = client.get("/users", headers=_bearer_header())
    assert res.status_code == 403


def test_google_role_lookup_failure_blocks_admin_and_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("ALLOWED_EMAILS", "operator@example.com")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda _token, _request: "admin@example.com")

    def raise_lookup_failure():
        raise auth_module.UserRoleLookupError("db unavailable")

    monkeypatch.setattr(auth_module, "_load_active_user_roles", raise_lookup_failure)

    client = TestClient(app)
    admin_res = client.get("/users", headers=_bearer_header())
    operator_res = client.get("/orders", headers=_bearer_header())
    assert admin_res.status_code == 503
    assert operator_res.status_code == 503
