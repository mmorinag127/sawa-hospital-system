import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_support import hospital_admin, hospital_operator, registered_hospital_operator  # noqa: F401
from src.api import auth as auth_module
from src.db import engine, session_scope
from src.main import app
from src.models.user import User
from test_portal_google_auth import _cleanup, _seed_user


PROTECTED_ROUTES = [
    pytest.param("GET", "/monthly-menus/latest", {}, id="monthly-menus"),
    pytest.param("GET", "/shipping/status/latest", {}, id="shipping"),
    pytest.param("POST", "/order-forms/generate", {"params": {"facility_id": "FAC00001", "month_id": "2026-03"}}, id="order-forms"),
    pytest.param("GET", "/health/backlog", {}, id="system-backlog"),
]


@pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
@pytest.mark.parametrize("authorization", [
    pytest.param(None, id="absent"),
    pytest.param("Basic b3BlcmF0b3I6c2VjcmV0", id="retired-basic"),
    pytest.param("Bearer", id="malformed-scheme"),
    pytest.param("Bearer ", id="empty-token"),
    pytest.param("Bearer invalid-google-token", id="invalid-token"),
    pytest.param("Bearer invalid token", id="malformed-token"),
])
def test_protected_routes_reject_missing_or_invalid_auth(
    monkeypatch, hospital_operator, method, path, kwargs, authorization
):
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    headers = {} if authorization is None else {"Authorization": authorization}
    response = TestClient(app).request(method, path, headers=headers, **kwargs)
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"
    if authorization and authorization.startswith("Bearer invalid"):
        assert hospital_operator.verifier.called
    else:
        hospital_operator.verifier.assert_not_called()


@pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
@pytest.mark.parametrize("case", ["inactive", "missing-grant", "wrong-system", "disabled-grant", "invalid-role"])
def test_protected_routes_check_real_registered_role_and_hospital_grant(monkeypatch, method, path, kwargs, case):
    systems = () if case == "missing-grant" else ("shift",) if case == "wrong-system" else ("hospital",)
    with registered_hospital_operator(
        monkeypatch, status="inactive" if case == "inactive" else "active", systems=systems
    ) as operator:
        with session_scope() as session:
            if case == "invalid-role":
                session.get(User, operator.user_id).role = "viewer"
            if case == "disabled-grant":
                session.execute(
                    text("UPDATE user_system_access SET enabled = FALSE WHERE user_id = :id"),
                    {"id": operator.user_id},
                )
        auth_module.invalidate_user_cache()
        response = TestClient(app).request(method, path, headers=operator.headers, **kwargs)
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Forbidden" if case in {"inactive", "invalid-role"} else "System access denied"
        )
        assert operator.verifier.called


def test_active_hospital_operator_reaches_real_endpoint_but_not_admin(hospital_operator):
    assert os.environ["AUTH_DISABLED"] == "false"
    with session_scope() as session:
        user = session.get(User, hospital_operator.user_id)
        assert (user.account, user.role, user.status) == (hospital_operator.account, "operator", "active")
    client = TestClient(app)
    response = client.get("/health/backlog", headers=hospital_operator.headers)
    assert response.status_code == 200
    assert "ingest_queue_depth" in response.json()
    assert client.get("/system/db/download", headers=hospital_operator.headers).status_code == 403
    assert client.post("/system/clear-all", headers=hospital_operator.headers, json={"confirm": "CLEAR_ALL"}).status_code == 403
    assert hospital_operator.verifier.called


def test_hospital_admin_fixture_uses_registered_admin_role_and_enabled_grant(hospital_admin):
    assert os.environ["AUTH_DISABLED"] == "false"
    with session_scope() as session:
        user = session.get(User, hospital_admin.user_id)
        assert (user.account, user.role, user.status) == (hospital_admin.account, "admin", "active")
        assert session.execute(
            text("SELECT system_key FROM user_system_access WHERE user_id = :id AND enabled = TRUE"),
            {"id": hospital_admin.user_id},
        ).scalars().all() == ["hospital"]
    response = TestClient(app).get("/system/db/download", headers=hospital_admin.headers)
    assert response.status_code == 200
    assert response.content
    assert hospital_admin.verifier.called


def test_helper_clears_cache_restores_patches_and_preserves_other_users(monkeypatch):
    original_env = {key: os.environ.get(key) for key in ("AUTH_DISABLED", "AUTH_PROVIDER")}
    original_provider = auth_module.AUTH_PROVIDER
    original_audiences = auth_module.GOOGLE_OAUTH_CLIENT_IDS
    original_verifier = auth_module.id_token.verify_oauth2_token
    other_id = _seed_user(f"other-contract-{uuid4().hex}@example.invalid")
    try:
        with registered_hospital_operator(monkeypatch) as operator:
            response = TestClient(app).get("/health/backlog", headers=operator.headers)
            assert response.status_code == 200
            assert auth_module._USER_ROLE_CACHE[operator.account] == "operator"
        assert {key: os.environ.get(key) for key in original_env} == original_env
        assert auth_module.AUTH_PROVIDER == original_provider
        assert auth_module.GOOGLE_OAUTH_CLIENT_IDS is original_audiences
        assert auth_module.id_token.verify_oauth2_token is original_verifier
        assert auth_module._USER_ROLE_CACHE == {}
        assert auth_module._USER_ROLE_CACHE_EXPIRES_AT == 0.0
        with session_scope() as session:
            assert session.get(User, operator.user_id) is None
            assert session.get(User, other_id) is not None
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM user_system_access WHERE user_id = :id"),
                {"id": operator.user_id},
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT system_key FROM user_system_access WHERE user_id = :id AND enabled = TRUE"),
                {"id": other_id},
            ).scalars().all() == ["hospital"]
    finally:
        _cleanup(other_id)
