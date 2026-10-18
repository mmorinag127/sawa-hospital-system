"""Portal transport failures must not become credential denials or local fallback."""

import io
import json
import urllib.error

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api import auth


@pytest.fixture
def portal_client(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "portal")
    monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "https://portal.example.invalid/auth/me?existing=1")

    def forbidden_fallback(*args, **kwargs):
        pytest.fail("Portal mode must not fall back to local token or role validation")

    monkeypatch.setattr(auth, "_google_email_or_none", forbidden_fallback)
    monkeypatch.setattr(auth, "_load_active_user_roles", forbidden_fallback)
    app = FastAPI()

    @app.get("/user")
    def user(owner=Depends(auth.get_current_user)):
        return {"account": owner.account, "role": owner.role}

    @app.get("/operator")
    def operator(owner=Depends(auth.get_current_operator)):
        return {"account": owner.account, "role": owner.role}

    @app.get("/admin")
    def admin(owner=Depends(auth.get_current_admin)):
        return {"account": owner.account, "role": owner.role}

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-portal-token"
        yield client


@pytest.mark.parametrize("path", ["/user", "/operator", "/admin"])
@pytest.mark.parametrize("upstream_status", [401, 403, 400, 404, 429, 500, 502, 503, 504])
def test_upstream_http_status_contract(monkeypatch, portal_client, path, upstream_status):
    calls = []

    def transport(request, *, timeout):
        calls.append((request.full_url, request.get_header("Authorization"), timeout))
        raise urllib.error.HTTPError(request.full_url, upstream_status, "private upstream detail", {}, None)

    monkeypatch.setattr(auth.urllib.request, "urlopen", transport)
    response = portal_client.get(path)
    expected_status = upstream_status if upstream_status in (401, 403) else 503
    assert response.status_code == expected_status
    assert response.json() == {"detail": (
        "Portal authentication rejected" if expected_status in (401, 403)
        else "Portal authentication unavailable"
    )}
    assert calls == [(
        "https://portal.example.invalid/auth/me?existing=1&system=hospital",
        "Bearer synthetic-portal-token", 5,
    )]


@pytest.mark.parametrize("path", ["/user", "/operator", "/admin"])
@pytest.mark.parametrize("error", [urllib.error.URLError("private network detail"), TimeoutError("private timeout")])
def test_network_failure_is_unavailable(monkeypatch, portal_client, path, error):
    def transport(*args, **kwargs):
        raise error

    monkeypatch.setattr(auth.urllib.request, "urlopen", transport)
    response = portal_client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Portal authentication unavailable"}


@pytest.mark.parametrize("body", [b"not json", b"null", b"[]", b'"text"', b"1"])
def test_invalid_response_is_unavailable(monkeypatch, portal_client, body):
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body))
    response = portal_client.get("/user")
    assert response.status_code == 503
    assert response.json() == {"detail": "Portal authentication unavailable"}


@pytest.mark.parametrize("role,path,expected_role,expected_status", [
    ("operator", "/user", "operator", 200),
    ("operator", "/operator", "operator", 200),
    ("operator", "/admin", None, 403),
    ("admin", "/user", "admin", 200),
    ("admin", "/operator", "operator", 200),
    ("admin", "/admin", "admin", 200),
])
def test_success_and_admin_restriction_are_preserved(
    monkeypatch, portal_client, role, path, expected_role, expected_status
):
    body = json.dumps({"account": " Operator@Example.Invalid ", "role": role}).encode()
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body))
    response = portal_client.get(path)
    assert response.status_code == expected_status
    assert response.json() == (
        {"account": "operator@example.invalid", "role": expected_role}
        if expected_status == 200 else {"detail": "Forbidden"}
    )


@pytest.mark.parametrize("payload", [{}, {"role": "viewer", "account": "operator@example.invalid"}, {"role": "admin", "account": ""}])
def test_existing_payload_authorization_rejection_stays_forbidden(monkeypatch, portal_client, payload):
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode()))
    response = portal_client.get("/user")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("configuration", ["missing-bearer", "missing-upstream"])
def test_missing_configuration_stops_without_transport(monkeypatch, portal_client, configuration):
    def forbidden_transport(*args, **kwargs):
        pytest.fail("Incomplete portal inputs must stop before transport")

    monkeypatch.setattr(auth.urllib.request, "urlopen", forbidden_transport)
    if configuration == "missing-bearer":
        portal_client.headers.pop("Authorization")
        expected = 401
    else:
        monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "")
        expected = 503
    assert portal_client.get("/user").status_code == expected
