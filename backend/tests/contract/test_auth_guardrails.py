import base64
import importlib
import pathlib
import sys

from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
from src.db import engine  # noqa: E402
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
    assert "auth_disabled" not in res.json()

    me_res = client.get("/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["role"] == "admin"


def test_auth_rejects_basic_operator_when_legacy_env_present(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/orders", headers=_basic_header("operator", "secret"))
    assert res.status_code == 401

    me_res = client.get("/auth/me", headers=_basic_header("operator", "secret"))
    assert me_res.status_code == 401


def test_auth_rejects_basic_admin_when_legacy_env_present(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/auth/me", headers=_basic_header("admin", "secret"))
    assert res.status_code == 401


def test_shared_basic_credentials_are_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("OPERATOR_USER", "admin")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/auth/me", headers=_basic_header("admin", "secret"))
    assert res.status_code == 401


def test_staging_deploy_has_no_basic_admin_credentials():
    workflow = (ROOT.parent / ".github" / "workflows" / "deploy-stg.yml").read_text(
        encoding="utf-8"
    )
    assert "ADMIN_USER" not in workflow
    assert "ADMIN_PASSWORD" not in workflow
    assert "OPERATOR_USER" not in workflow
    assert "OPERATOR_PASSWORD" not in workflow


def test_predeploy_status_payload_uses_bearer_authorization_header():
    source = (ROOT.parent / "scripts" / "predeploy_env_checks.sh").read_text(
        encoding="utf-8"
    )
    assert 'status_json=$(curl -sS -H "Authorization: ${AUTHORIZATION_HEADER}"' in source
    assert 'curl -sS -u "$AUTHORIZATION_HEADER"' not in source


def test_portal_mode_rejects_local_basic_auth(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "portal")
    monkeypatch.setenv("PORTAL_AUTH_ME_URL", "https://portal.example/api/auth/me")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    assert client.get("/auth/me", headers=_basic_header("admin", "secret")).status_code == 401
    assert client.get("/orders", headers=_basic_header("operator", "operator-secret")).status_code == 401


def test_common_users_route_is_not_exposed_by_hospital(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/users", headers=_basic_header("operator", "operator-secret"))
    assert res.status_code == 404


def test_portal_user_management_rejects_operator_for_list_create_and_update(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    headers = _basic_header("operator", "operator-secret")
    body = {
        "account": "new-user@example.com",
        "role": "operator",
        "status": "active",
        "systems": ["hospital"],
    }

    assert client.get("/portal/users", headers=headers).status_code == 401
    assert client.post("/portal/users", json=body, headers=headers).status_code == 401
    assert client.put("/portal/users/user-id", json=body, headers=headers).status_code == 401


def test_portal_user_management_allows_admin_list_create_and_update(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    user_id = "portal-admin-contract-user"
    account = "portal-admin-contract@example.invalid"
    with engine.begin() as connection:
        connection.execute(
            text(
                """CREATE TABLE IF NOT EXISTS user_system_access (
                user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                system_key VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY(user_id, system_key)
                )"""
            )
        )
        connection.execute(text("DELETE FROM user_system_access WHERE user_id = :id"), {"id": user_id})
        connection.execute(
            text("DELETE FROM users WHERE id = :id OR lower(account) = :account"),
            {"id": user_id, "account": account},
        )

    client = TestClient(app)
    create_body = {
        "account": account,
        "role": "operator",
        "status": "active",
        "systems": ["hospital"],
    }

    try:
        create_res = client.post("/portal/users", json=create_body)
        assert create_res.status_code == 200
        created = create_res.json()["user"]
        user_id = created["id"]
        assert created == {"id": user_id, **create_body}

        list_res = client.get("/portal/users")
        assert list_res.status_code == 200
        listed = next(item for item in list_res.json()["items"] if item["id"] == user_id)
        assert listed == created

        update_body = {
            "account": account,
            "role": "admin",
            "status": "inactive",
            "systems": ["school-lunch", "shift"],
        }
        update_res = client.put(f"/portal/users/{user_id}", json=update_body)
        assert update_res.status_code == 200
        assert update_res.json()["user"] == {"id": user_id, **update_body}

        updated_list_res = client.get("/portal/users")
        assert updated_list_res.status_code == 200
        updated = next(item for item in updated_list_res.json()["items"] if item["id"] == user_id)
        assert updated == {"id": user_id, **update_body}
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM user_system_access WHERE user_id = :id"), {"id": user_id})
            connection.execute(
                text("DELETE FROM users WHERE id = :id OR lower(account) = :account"),
                {"id": user_id, "account": account},
            )


def test_auth_ignores_legacy_auth_header_cookie(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    token = _basic_header("operator", "secret")["Authorization"]
    res = client.get("/orders", cookies={"auth_header": token})
    assert res.status_code == 401


def test_admin_token_is_not_accepted_as_static_bearer(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_TOKEN", "static-admin-token")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/auth/me", headers=_bearer_header("static-admin-token"))
    assert res.status_code == 401


def test_google_admin_requires_active_registered_admin_role(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda _token, _request: "user@example.com")
    monkeypatch.setattr(auth_module, "_load_active_user_roles", lambda: {})

    client = TestClient(app)
    res = client.get("/auth/me", headers=_bearer_header())
    assert res.status_code == 403


def test_google_operator_requires_active_registered_role(monkeypatch):
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


def test_legacy_email_allowlists_do_not_bypass_common_user_database(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ALLOWED_EMAILS", "user@example.com")
    monkeypatch.setenv("ADMIN_EMAILS", "user@example.com")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    monkeypatch.setattr(auth_module, "_verify_google_token", lambda _token, _request: "user@example.com")
    monkeypatch.setattr(auth_module, "_load_active_user_roles", lambda: {})

    client = TestClient(app)
    admin_res = client.get("/auth/me", headers=_bearer_header())
    operator_res = client.get("/orders", headers=_bearer_header())
    assert admin_res.status_code == 403
    assert operator_res.status_code == 403


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
    admin_res = client.get("/auth/me", headers=_bearer_header())
    operator_res = client.get("/orders", headers=_bearer_header())
    assert admin_res.status_code == 503
    assert operator_res.status_code == 503
