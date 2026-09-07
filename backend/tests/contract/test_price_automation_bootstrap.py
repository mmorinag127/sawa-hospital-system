from unittest.mock import Mock

import pytest
from sqlalchemy import text

from test_automation_bootstrap import engine, _context, _snapshot
from src.services.portal_access_bootstrap_service import PortalAccessBootstrapError
from src.services.price_automation_bootstrap_service import run_price_automation_bootstrap

EMAIL = "sawa-price-ui-verify-stg@sawahospitalsystem.iam.gserviceaccount.com"


def test_price_identity_is_separate_idempotent_and_preserves_other_users(engine, monkeypatch):
    _context(monkeypatch, "stg")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users VALUES('shift', 'shift@example.com', 'operator', 'active', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO user_system_access VALUES('shift', 'shift', TRUE)"))
        result = run_price_automation_bootstrap(connection, environment="stg", email=EMAIL)
        assert result["created"] is True
        assert connection.execute(text("SELECT system_key FROM user_system_access WHERE user_id = :id"), {"id": result["user_id"]}).scalars().all() == ["school-lunch"]
        assert connection.execute(text("SELECT system_key FROM user_system_access WHERE user_id = 'shift'")).scalars().all() == ["shift"]
        snapshot = _snapshot(connection)
        assert run_price_automation_bootstrap(connection, environment="stg", email=EMAIL)["created"] is False
        assert _snapshot(connection) == snapshot


@pytest.mark.parametrize("mutation", [
    "UPDATE users SET role='admin'",
    "UPDATE users SET status='inactive'",
    "DELETE FROM user_system_access",
    "INSERT INTO user_system_access SELECT id, 'hospital', TRUE FROM users",
    "INSERT INTO user_system_access SELECT id, 'shift', TRUE FROM users",
    "INSERT INTO users SELECT 'duplicate', account, role, status, created_at FROM users",
])
def test_price_identity_conflicts_block_without_modification(engine, monkeypatch, mutation):
    _context(monkeypatch, "stg")
    with engine.begin() as connection:
        run_price_automation_bootstrap(connection, environment="stg", email=EMAIL)
        connection.execute(text(mutation))
        snapshot = _snapshot(connection)
        with pytest.raises(PortalAccessBootstrapError):
            run_price_automation_bootstrap(connection, environment="stg", email=EMAIL)
        assert _snapshot(connection) == snapshot


@pytest.mark.parametrize("field,value", [("GITHUB_ACTIONS", "false"), ("GITHUB_RUN_ID", ""), ("GITHUB_REF", "refs/heads/main"), ("GITHUB_REF_NAME", "main")])
def test_price_bootstrap_never_runs_locally_or_outside_develop(monkeypatch, field, value):
    _context(monkeypatch, "stg")
    monkeypatch.setenv(field, value)
    connection = Mock()
    with pytest.raises(PortalAccessBootstrapError):
        run_price_automation_bootstrap(connection, environment="stg", email=EMAIL)
    assert not connection.mock_calls


def test_price_bootstrap_rejects_production_and_other_identities(monkeypatch):
    _context(monkeypatch, "stg")
    for environment, email in [("prod", EMAIL), ("stg", "sawa-ui-verify-stg@sawahospitalsystem.iam.gserviceaccount.com")]:
        connection = Mock()
        with pytest.raises(PortalAccessBootstrapError):
            run_price_automation_bootstrap(connection, environment=environment, email=email)
        assert not connection.mock_calls
