import time
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from src.api import auth as auth_module
from src.db import engine
from src.main import app
from src.models.user import AuditLog


AUTOMATION_AUDIENCE = "https://automation.example.invalid/shift"
HUMAN_AUDIENCE = "human-client.apps.googleusercontent.com"
AUTOMATION_SUBJECT = "109876543210987654321"
AUTOMATION_ENV = (
    "AUTOMATION_AUTH_AUDIENCE",
    "AUTOMATION_AUTH_EMAIL",
    "AUTOMATION_AUTH_SUBJECT",
)
HEADERS = {"Authorization": "Bearer google-signed-id-token"}
client = TestClient(app)


@pytest.fixture
def automation(monkeypatch):
    account = f"automation-{uuid.uuid4().hex}@test-project.iam.gserviceaccount.com"
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth_module, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth_module, "GOOGLE_OAUTH_CLIENT_IDS", [HUMAN_AUDIENCE])
    for name, value in zip(
        AUTOMATION_ENV, (AUTOMATION_AUDIENCE, account, AUTOMATION_SUBJECT)
    ):
        monkeypatch.setenv(name, value)
    issued_at = int(time.time()) - 5
    claims = {
        "iss": "https://accounts.google.com",
        "aud": AUTOMATION_AUDIENCE,
        "email": account,
        "sub": AUTOMATION_SUBJECT,
        "email_verified": True,
        "iat": issued_at,
        "exp": issued_at + 3600,
    }

    # Only replace Google's external token-verification boundary. Application
    # claim validation, user lookup, grants and audit persistence remain real.
    def verify(token, request, audience):
        assert token == "google-signed-id-token"
        assert isinstance(request, auth_module.google_requests.Request)
        if audience != claims["aud"]:
            raise ValueError("Wrong audience")
        return dict(claims)

    verifier = Mock(side_effect=verify)
    monkeypatch.setattr(auth_module.id_token, "verify_oauth2_token", verifier)
    auth_module.invalidate_user_cache()
    yield claims, verifier
    auth_module.invalidate_user_cache()


@pytest.fixture
def seed_user():
    users = []

    def seed(account, *, role="operator", status="active", systems=("shift",), enabled=True):
        user_id = str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text(
                """CREATE TABLE IF NOT EXISTS user_system_access (
                user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                system_key VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY(user_id, system_key),
                CONSTRAINT ck_user_system_access_system_key
                  CHECK (system_key IN ('hospital', 'shift', 'school-lunch'))
                )"""
            ))
            connection.execute(
                text("INSERT INTO users(id, account, role, status) VALUES(:id, :account, :role, :status)"),
                {"id": user_id, "account": account, "role": role, "status": status},
            )
            for system in systems:
                connection.execute(
                    text("INSERT INTO user_system_access(user_id, system_key, enabled) VALUES(:id, :system, :enabled)"),
                    {"id": user_id, "system": system, "enabled": enabled},
                )
        users.append((user_id, account))
        auth_module.invalidate_user_cache()
        return user_id

    yield seed
    with engine.begin() as connection:
        for user_id, account in users:
            connection.execute(AuditLog.__table__.delete().where(AuditLog.actor == account))
            connection.execute(text("DELETE FROM user_system_access WHERE user_id=:id"), {"id": user_id})
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
    auth_module.invalidate_user_cache()


def _audit_ids():
    with engine.connect() as connection:
        return set(connection.execute(select(AuditLog.id)).scalars())


def _assert_rejected(status_code, *, path="/portal/automation/auth", method="POST", headers=None):
    before = _audit_ids()
    response = client.request(method, path, headers=HEADERS if headers is None else headers)
    assert response.status_code == status_code, response.text
    assert _audit_ids() == before


def test_signed_service_account_operator_with_shift_access_is_allowed(automation, seed_user):
    claims, verifier = automation
    seed_user(claims["email"])
    before = _audit_ids()
    response = client.post("/portal/automation/auth", headers=HEADERS)
    expected = {"account": claims["email"], "role": "operator", "systems": ["shift"]}
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert AUTOMATION_AUDIENCE in [call.args[2] for call in verifier.call_args_list]
    added = _audit_ids() - before
    assert len(added) == 1
    with engine.connect() as connection:
        entry = connection.execute(
            select(AuditLog.__table__).where(AuditLog.id.in_(added))
        ).mappings().one()
    assert entry["actor"] == claims["email"]
    assert entry["action"] == "automation_login"
    assert entry["target"] == "shift"
    assert entry["metadata"] == {"systems": ["shift"]}
    portal_response = client.get("/portal/auth/me?system=shift", headers=HEADERS)
    assert portal_response.status_code == 200
    assert portal_response.json() == expected
    assert _audit_ids() == before | added


@pytest.mark.parametrize("case", [
    "wrong-audience", "human-audience", "wrong-subject", "wrong-email",
    "unverified-email", "missing-email-verified", "expired", "future",
    "over-one-hour", "missing-iat", "missing-exp", "boolean-iat", "string-exp",
])
@pytest.mark.parametrize("path,method", [
    ("/portal/automation/auth", "POST"),
    ("/portal/auth/me?system=shift", "GET"),
])
def test_invalid_machine_claims_fail_closed(automation, seed_user, case, path, method):
    claims, verifier = automation
    seed_user(claims["email"])
    changes = {
        "wrong-audience": ("aud", "https://wrong.example.invalid"),
        "human-audience": ("aud", HUMAN_AUDIENCE),
        "wrong-subject": ("sub", "other-subject"),
        "wrong-email": ("email", "deploy@test-project.iam.gserviceaccount.com"),
        "unverified-email": ("email_verified", False),
        "expired": ("exp", int(time.time()) - 60),
        "future": ("iat", int(time.time()) + 300),
        "over-one-hour": ("exp", claims["iat"] + 3601),
        "boolean-iat": ("iat", True),
        "string-exp": ("exp", str(claims["exp"])),
    }
    missing = {"missing-email-verified": "email_verified", "missing-iat": "iat", "missing-exp": "exp"}
    if case in missing:
        claims.pop(missing[case])
    else:
        key, value = changes[case]
        claims[key] = value
    _assert_rejected(401, path=path, method=method)
    assert verifier.called


@pytest.mark.parametrize("missing", [AUTOMATION_ENV, *[(name,) for name in AUTOMATION_ENV]])
def test_unset_or_partial_machine_configuration_is_rejected(automation, seed_user, monkeypatch, missing):
    claims, _ = automation
    seed_user(claims["email"])
    for name in missing:
        monkeypatch.delenv(name)
    _assert_rejected(401)


def test_invalid_google_signature_is_rejected_without_audit(automation, seed_user):
    claims, verifier = automation
    seed_user(claims["email"])
    verifier.side_effect = ValueError("Invalid Google signature")
    _assert_rejected(401)
    assert verifier.called


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer"}, {"Authorization": "Basic b3BlcmF0b3I6cGFzcw=="}])
def test_missing_or_non_bearer_authentication_is_rejected(automation, headers):
    _, verifier = automation
    _assert_rejected(401, headers=headers)
    verifier.assert_not_called()


@pytest.mark.parametrize("case", ["inactive", "unregistered", "no-shift", "disabled-shift", "admin"])
@pytest.mark.parametrize("path,method", [
    ("/portal/automation/auth", "POST"),
    ("/portal/auth/me?system=shift", "GET"),
    ("/portal/auth/me", "GET"),
])
def test_machine_identity_cannot_bypass_common_rbac_or_system_access(automation, seed_user, case, path, method):
    claims, verifier = automation
    if case != "unregistered":
        seed_user(
            claims["email"],
            role="admin" if case == "admin" else "operator",
            status="inactive" if case == "inactive" else "active",
            systems=("hospital",) if case == "no-shift" else ("shift",),
            enabled=case != "disabled-shift",
        )
    _assert_rejected(403, path=path, method=method)
    assert verifier.called


@pytest.mark.parametrize("systems", [
    ("shift", "hospital"),
    ("shift", "school-lunch"),
    ("shift", "hospital", "school-lunch"),
])
@pytest.mark.parametrize("path,method", [
    ("/portal/automation/auth", "POST"),
    ("/portal/auth/me?system=shift", "GET"),
    ("/portal/auth/me", "GET"),
])
def test_machine_extra_system_grants_are_rejected(automation, seed_user, systems, path, method):
    claims, _ = automation
    seed_user(claims["email"], systems=systems)
    _assert_rejected(403, path=path, method=method)


@pytest.mark.parametrize("role", ["operator", "admin"])
@pytest.mark.parametrize("systems", [("shift",), ("shift", "hospital")])
@pytest.mark.parametrize("path", ["/orders", "/portal/users", "/ocr/templates"])
def test_machine_cannot_use_hospital_or_admin_endpoints(automation, seed_user, role, systems, path):
    claims, _ = automation
    seed_user(claims["email"], role=role, systems=systems)
    _assert_rejected(403, path=path, method="GET")


def test_machine_admin_is_rejected_by_common_auth_me(automation, seed_user):
    claims, _ = automation
    seed_user(claims["email"], role="admin")
    _assert_rejected(403, path="/auth/me", method="GET")


@pytest.mark.parametrize("audience", [AUTOMATION_AUDIENCE, HUMAN_AUDIENCE])
@pytest.mark.parametrize("path,method", [
    ("/portal/automation/auth", "POST"),
    ("/portal/auth/me?system=shift", "GET"),
    ("/orders", "GET"),
    ("/portal/users", "GET"),
    ("/ocr/templates", "GET"),
])
def test_deploy_identity_is_not_reused_as_application_identity(automation, audience, path, method):
    claims, _ = automation
    claims.update(
        aud=audience,
        email=f"deploy-{uuid.uuid4().hex}@test-project.iam.gserviceaccount.com",
        sub="deploy-subject",
    )
    # Deployment IAM credentials do not register a common application user.
    _assert_rejected(401 if audience == AUTOMATION_AUDIENCE else 403, path=path, method=method)


@pytest.mark.parametrize("role", ["operator", "admin"])
@pytest.mark.parametrize("configured", [True, False])
def test_existing_human_google_authentication_is_preserved(automation, seed_user, monkeypatch, role, configured):
    claims, verifier = automation
    claims.update(aud=HUMAN_AUDIENCE, email=f"human-{uuid.uuid4().hex}@example.com", sub="human-subject")
    if not configured:
        for name in AUTOMATION_ENV:
            monkeypatch.delenv(name)
    systems = ["hospital", "school-lunch", "shift"]
    seed_user(claims["email"], role=role, systems=tuple(systems))
    before = _audit_ids()
    response = client.get("/portal/auth/me?system=shift", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {"account": claims["email"], "role": role, "systems": systems}
    assert verifier.call_args.args[2] == HUMAN_AUDIENCE
    hospital_response = client.get("/orders", headers=HEADERS)
    assert hospital_response.status_code == 200, hospital_response.text
    admin_response = client.get("/portal/users", headers=HEADERS)
    assert admin_response.status_code == (200 if role == "admin" else 403), admin_response.text
    assert _audit_ids() == before
    _assert_rejected(403)
