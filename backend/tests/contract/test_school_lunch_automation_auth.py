import os
import uuid

import pytest
from sqlalchemy import select, text

import test_automation_auth as shift
from test_automation_auth import automation as shift_automation, seed_user


ENV = tuple(f"SCHOOL_LUNCH_AUTOMATION_AUTH_{key}" for key in ("AUDIENCE", "EMAIL", "SUBJECT"))
LOGIN = "/portal/automation/auth?system=school-lunch"
PATHS = [(LOGIN, "POST"), ("/portal/auth/me?system=school-lunch", "GET"), ("/portal/auth/me", "GET")]


@pytest.fixture
def automation(shift_automation, monkeypatch):
    claims, verifier = shift_automation
    values = (
        "https://automation.example.invalid/school-lunch",
        f"school-lunch-{uuid.uuid4().hex}@test-project.iam.gserviceaccount.com",
        "209876543210987654321",
    )
    for name, value in zip(ENV, values):
        monkeypatch.setenv(name, value)
    claims.update(aud=values[0], email=values[1], sub=values[2])
    return claims, verifier


def test_school_lunch_login_and_shared_auth_identity(automation, seed_user):
    claims, verifier = automation
    user_id = seed_user(claims["email"], systems=("school-lunch",))
    # Disabled grants are not effective permissions.
    with shift.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO user_system_access(user_id, system_key, enabled) VALUES(:id, 'shift', FALSE)"
        ), {"id": user_id})
    before = shift._audit_ids()
    expected = {"account": claims["email"], "role": "operator", "systems": ["school-lunch"]}
    for path, method in PATHS:
        response = shift.client.request(method, path, headers=shift.HEADERS)
        assert response.status_code == 200, response.text
        assert response.json() == expected
    added = shift._audit_ids() - before
    assert len(added) == 1
    with shift.engine.connect() as connection:
        audit = connection.execute(select(shift.AuditLog.__table__).where(shift.AuditLog.id.in_(added))).mappings().one()
    assert audit["actor"] == claims["email"]
    assert audit["action"] == "automation_login"
    assert audit["target"] == "school-lunch"
    assert audit["metadata"] == {"systems": ["school-lunch"]}
    assert verifier.call_args.args[2] == claims["aud"]


@pytest.mark.parametrize("case", [
    "wrong-audience", "human-audience", "wrong-subject", "wrong-email",
    "unverified-email", "missing-email-verified", "expired", "future",
    "over-one-hour", "missing-iat", "missing-exp", "boolean-iat", "string-exp",
])
@pytest.mark.parametrize("path,method", PATHS)
def test_invalid_claims(automation, seed_user, case, path, method):
    shift.test_invalid_machine_claims_fail_closed(automation, seed_user, case, path, method)


@pytest.mark.parametrize("missing", [ENV, *[(name,) for name in ENV]])
@pytest.mark.parametrize("path,method", PATHS)
def test_explicit_configuration_required(automation, seed_user, monkeypatch, missing, path, method):
    claims, _ = automation
    seed_user(claims["email"], systems=("school-lunch",))
    for name in missing:
        monkeypatch.delenv(name)
    shift._assert_rejected(401, path=path, method=method)


@pytest.mark.parametrize("case", ["inactive", "unregistered", "no-access", "disabled", "admin"])
@pytest.mark.parametrize("path,method", PATHS)
def test_rbac_rejects(automation, seed_user, case, path, method):
    claims, _ = automation
    if case != "unregistered":
        seed_user(claims["email"], role="admin" if case == "admin" else "operator",
                  status="inactive" if case == "inactive" else "active",
                  systems=() if case == "no-access" else ("school-lunch",), enabled=case != "disabled")
    shift._assert_rejected(403, path=path, method=method)


@pytest.mark.parametrize("systems", [
    ("school-lunch", "shift"), ("school-lunch", "hospital"),
    ("school-lunch", "hospital", "shift"), ("shift",), ("hospital",),
])
@pytest.mark.parametrize("path,method", PATHS)
def test_exact_effective_permissions_required(automation, seed_user, systems, path, method):
    seed_user(automation[0]["email"], systems=systems)
    shift._assert_rejected(403, path=path, method=method)


@pytest.mark.parametrize("path,method", [
    ("/portal/automation/auth", "POST"), ("/portal/automation/auth?system=shift", "POST"),
    ("/portal/auth/me?system=shift", "GET"), ("/portal/auth/me?system=hospital", "GET"),
    ("/orders", "GET"), ("/portal/users", "GET"), ("/ocr/templates", "GET"),
])
@pytest.mark.parametrize("role", ["operator", "admin"])
def test_other_systems_and_admin_rejected(automation, seed_user, path, method, role):
    seed_user(automation[0]["email"], role=role, systems=("school-lunch", "hospital", "shift"))
    shift._assert_rejected(403, path=path, method=method)


def test_invalid_signature(automation, seed_user):
    seed_user(automation[0]["email"], systems=("school-lunch",))
    automation[1].side_effect = ValueError("Invalid Google signature")
    shift._assert_rejected(401, path=LOGIN)


@pytest.mark.parametrize("field,env", [("email", "AUTOMATION_AUTH_EMAIL"), ("sub", "AUTOMATION_AUTH_SUBJECT")])
def test_identity_must_be_distinct(automation, seed_user, monkeypatch, field, env):
    claims, _ = automation
    monkeypatch.setenv(env, claims[field])
    seed_user(claims["email"], systems=("school-lunch",))
    shift._assert_rejected(401, path=LOGIN)


@pytest.mark.parametrize("system", ["hospital", "admin", "unknown", ""])
def test_invalid_login_target(automation, seed_user, system):
    seed_user(automation[0]["email"], systems=("school-lunch",))
    shift._assert_rejected(400, path=f"/portal/automation/auth?system={system}")


def test_shift_cannot_use_school_lunch_with_both_configured(automation, seed_user):
    claims, _ = automation
    claims.update(aud=os.environ["AUTOMATION_AUTH_AUDIENCE"], email=os.environ["AUTOMATION_AUTH_EMAIL"],
                  sub=os.environ["AUTOMATION_AUTH_SUBJECT"])
    seed_user(claims["email"])
    shift._assert_rejected(403, path=LOGIN)
    shift._assert_rejected(403, path="/portal/auth/me?system=school-lunch", method="GET")
    response = shift.client.post("/portal/automation/auth?system=shift", headers=shift.HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["systems"] == ["shift"]


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_humans_unchanged_with_both_identities(automation, seed_user, monkeypatch, role):
    shift.test_existing_human_google_authentication_is_preserved(automation, seed_user, monkeypatch, role, True)


@pytest.mark.parametrize("case", ["human-client-audience", "non-service-account", "old-iat", "shift-audience", "shift-subject"])
def test_additional_identity_boundaries(automation, seed_user, monkeypatch, case):
    claims, _ = automation
    if case == "human-client-audience":
        monkeypatch.setattr(shift.auth_module, "GOOGLE_OAUTH_CLIENT_IDS", [claims["aud"]])
    elif case == "non-service-account":
        claims["email"] = "machine@example.com"
        monkeypatch.setenv("SCHOOL_LUNCH_AUTOMATION_AUTH_EMAIL", claims["email"])
    elif case == "old-iat":
        claims["iat"] -= 3600
    elif case == "shift-audience":
        claims["aud"] = os.environ["AUTOMATION_AUTH_AUDIENCE"]
    else:
        claims["sub"] = os.environ["AUTOMATION_AUTH_SUBJECT"]
    seed_user(claims["email"], systems=("school-lunch",))
    shift._assert_rejected(401, path=LOGIN)
