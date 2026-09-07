import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from scripts import portal_automation_db_bootstrap as cli  # noqa: E402
from src.services.portal_automation_bootstrap_service import (  # noqa: E402
    PortalAutomationBootstrapError,
    require_automation_context,
    run_portal_automation_bootstrap,
)


def _email(environment):
    return f"sawa-ui-verify-{environment}@sawahospitalsystem.iam.gserviceaccount.com"


def _context(monkeypatch, environment):
    branch = "develop" if environment == "stg" else "release/prod-20260907"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_REF_NAME", branch)
    monkeypatch.setenv("GITHUB_REF", f"refs/heads/{branch}")


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", future=True)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE users (id VARCHAR PRIMARY KEY, account VARCHAR NOT NULL, "
            "role VARCHAR NOT NULL, status VARCHAR, created_at TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE user_system_access (user_id VARCHAR NOT NULL, "
            "system_key VARCHAR NOT NULL, enabled BOOLEAN NOT NULL, "
            "PRIMARY KEY (user_id, system_key), "
            "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, "
            "CONSTRAINT ck_user_system_access_system_key "
            "CHECK(system_key IN ('hospital', 'shift', 'school-lunch')))"
        ))
        connection.execute(text(
            "CREATE TABLE audit_logs (id VARCHAR PRIMARY KEY, actor VARCHAR NOT NULL, "
            "action VARCHAR NOT NULL, target VARCHAR NOT NULL, fac VARCHAR, wek VARCHAR, "
            "metadata JSON, created_at TIMESTAMP)"
        ))
    yield engine
    engine.dispose()


def _snapshot(connection):
    return {
        table: connection.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2")).all()
        for table in ("users", "user_system_access", "audit_logs")
    }


@pytest.mark.parametrize("environment", ["stg", "prod"])
def test_registration_audit_idempotence_and_other_users_preserved(engine, monkeypatch, environment):
    _context(monkeypatch, environment)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users VALUES('other', 'other@example.com', 'admin', 'active', CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO user_system_access VALUES('other', 'hospital', TRUE), "
            "('other', 'school-lunch', FALSE)"
        ))
        before = _snapshot(connection)
        result = run_portal_automation_bootstrap(connection, environment=environment, email=_email(environment))
        assert result["created"] is True
        user = connection.execute(text("SELECT account, role, status FROM users WHERE id = :id"),
                                  {"id": result["user_id"]}).one()
        assert tuple(user) == (_email(environment), "operator", "active")
        assert connection.execute(text(
            "SELECT system_key, enabled FROM user_system_access WHERE user_id = :id"
        ), {"id": result["user_id"]}).all() == [("shift", True)]
        assert connection.execute(text("SELECT actor, action, target FROM audit_logs")).all() == [
            (f"system:{environment}-portal-automation-db-bootstrap", "portal_automation_shift_user_created", result["user_id"])
        ]
        after = _snapshot(connection)
        assert [row for row in after["users"] if row[0] == "other"] == before["users"]
        assert [row for row in after["user_system_access"] if row[0] == "other"] == before["user_system_access"]
        repeated = run_portal_automation_bootstrap(connection, environment=environment, email=_email(environment))
        assert repeated == {"user_id": result["user_id"], "created": False}
        assert _snapshot(connection) == after


@pytest.mark.parametrize("mutation", [
    "UPDATE users SET role = 'admin'",
    "UPDATE users SET status = 'inactive'",
    "UPDATE users SET account = upper(account)",
    "DELETE FROM user_system_access",
    "UPDATE user_system_access SET enabled = FALSE",
    "INSERT INTO user_system_access SELECT id, 'hospital', TRUE FROM users",
    "INSERT INTO user_system_access SELECT id, 'school-lunch', TRUE FROM users",
    "INSERT INTO users SELECT 'duplicate', account, role, status, created_at FROM users",
])
def test_existing_mismatch_is_rejected_without_changes(engine, monkeypatch, mutation):
    _context(monkeypatch, "stg")
    with engine.begin() as connection:
        run_portal_automation_bootstrap(connection, environment="stg", email=_email("stg"))
        connection.execute(text(mutation))
        before = _snapshot(connection)
        with pytest.raises(PortalAutomationBootstrapError, match="existing automation"):
            run_portal_automation_bootstrap(connection, environment="stg", email=_email("stg"))
        assert _snapshot(connection) == before


@pytest.mark.parametrize("environment,variable,value", [
    ("stg", "GITHUB_ACTIONS", "false"),
    ("prod", "GITHUB_ACTIONS", ""),
    ("stg", "GITHUB_RUN_ID", ""),
    ("stg", "GITHUB_REF_NAME", "release/prod-20260907"),
    ("prod", "GITHUB_REF_NAME", "develop"),
    ("prod", "GITHUB_REF_NAME", "release/prod-"),
    ("stg", "GITHUB_REF", "refs/tags/develop"),
    ("prod", "GITHUB_REF", "refs/tags/release/prod-20260907"),
    ("stg", "GITHUB_REF", "refs/pull/12/merge"),
])
def test_context_guard_precedes_database_access(monkeypatch, environment, variable, value):
    _context(monkeypatch, environment)
    monkeypatch.setenv(variable, value)
    connection = Mock()
    with pytest.raises(PortalAutomationBootstrapError):
        run_portal_automation_bootstrap(connection, environment=environment, email=_email(environment))
    assert connection.mock_calls == []


@pytest.mark.parametrize("environment,email", [
    ("stg", _email("prod")), ("prod", _email("stg")),
    ("stg", "operator@example.com"), ("dev", _email("stg")),
])
def test_identity_environment_guard(monkeypatch, environment, email):
    _context(monkeypatch, environment)
    with pytest.raises(PortalAutomationBootstrapError):
        require_automation_context(environment=environment, email=email)


def test_missing_schema_is_not_migrated(engine, monkeypatch):
    _context(monkeypatch, "stg")
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE user_system_access"))
        with pytest.raises(PortalAutomationBootstrapError, match="table is required"):
            run_portal_automation_bootstrap(connection, environment="stg", email=_email("stg"))
        assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 0


def _args(environment):
    return SimpleNamespace(environment=environment, email=_email(environment),
                           project_id="sawahospitalsystem", region="asia-northeast2",
                           service=f"web-{environment}", db_host="127.0.0.1", db_port=5432)


@pytest.mark.parametrize("environment", ["stg", "prod"])
def test_cli_reuses_cloud_run_config_and_db_transaction(engine, monkeypatch, environment):
    _context(monkeypatch, environment)
    args = _args(environment)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    config = Mock(return_value=SimpleNamespace(db_user="user", db_password="p@ss", db_name="db"))
    monkeypatch.setattr(cli, "_load_service_db_config", config)
    create = Mock(return_value=engine)
    monkeypatch.setattr(cli, "create_engine", create)
    dispose = Mock()
    monkeypatch.setattr(engine, "dispose", dispose)
    assert cli.main() == 0
    config.assert_called_once_with(args.project_id, args.region, args.service)
    url = create.call_args.args[0]
    assert (url.username, url.password, url.host, url.port, url.database) == ("user", "p@ss", "127.0.0.1", 5432, "db")
    dispose.assert_called_once()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT account FROM users")).scalar_one() == _email(environment)


@pytest.mark.parametrize("invalid", ["local", "service", "email"])
def test_cli_guard_precedes_cloud_access(monkeypatch, invalid):
    _context(monkeypatch, "stg")
    args = _args("stg")
    if invalid == "local":
        monkeypatch.delenv("GITHUB_ACTIONS")
    elif invalid == "service":
        args.service = "web-prod"
    else:
        args.email = _email("prod")
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    load = Mock()
    monkeypatch.setattr(cli, "_load_service_db_config", load)
    assert cli.main() == 1
    load.assert_not_called()


def test_cli_argument_contract(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "portal_automation_db_bootstrap.py", "--environment", "prod",
        "--project-id", "sawahospitalsystem", "--region", "asia-northeast2",
        "--service", "web-prod", "--email", _email("prod"),
        "--db-host", "localhost", "--db-port", "15432",
    ])
    args = cli.parse_args()
    assert vars(args) == {**vars(_args("prod")), "db_host": "localhost", "db_port": 15432}


@pytest.mark.parametrize("failure_at", ["config", "engine", "transaction"])
def test_cli_errors_never_expose_secrets(monkeypatch, capsys, failure_at):
    _context(monkeypatch, "stg")
    monkeypatch.setattr(cli, "parse_args", lambda: _args("stg"))
    secret = "postgresql://user:private-password@host/db private-gcloud-output"
    config = Mock(return_value=SimpleNamespace(db_user="user", db_password="private-password", db_name="db"))
    engine = Mock()
    create = Mock(return_value=engine)
    if failure_at == "config":
        config.side_effect = RuntimeError(secret)
    elif failure_at == "engine":
        create.side_effect = RuntimeError(secret)
    else:
        engine.begin.side_effect = RuntimeError(secret)
    monkeypatch.setattr(cli, "_load_service_db_config", config)
    monkeypatch.setattr(cli, "create_engine", create)
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "blocked: automation DB bootstrap failed (RuntimeError)\n"
    if failure_at == "transaction":
        engine.dispose.assert_called_once()


def test_cli_reports_shared_schema_guard(monkeypatch, capsys):
    _context(monkeypatch, "stg")
    monkeypatch.setattr(cli, "parse_args", lambda: _args("stg"))
    message = "user_system_access system_key constraint must allow only hospital, shift, and school-lunch"
    monkeypatch.setattr(cli, "_load_service_db_config", Mock(side_effect=cli.PortalAccessBootstrapError(message)))
    assert cli.main() == 1
    assert capsys.readouterr().err == f"blocked: {message}\n"


@pytest.mark.parametrize("environment", ["stg", "prod"])
@pytest.mark.parametrize("field,value,message", [
    ("project_id", "other-project", "Cloud Run project must be sawahospitalsystem"),
    ("project_id", "", "Cloud Run project must be sawahospitalsystem"),
    ("region", "asia-northeast1", "Cloud Run region must be asia-northeast2"),
    ("region", "", "Cloud Run region must be asia-northeast2"),
])
def test_cli_pins_project_and_region_before_cloud_access(
    monkeypatch, capsys, environment, field, value, message
):
    _context(monkeypatch, environment)
    args = _args(environment)
    setattr(args, field, value)
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    load = Mock()
    create = Mock()
    monkeypatch.setattr(cli, "_load_service_db_config", load)
    monkeypatch.setattr(cli, "create_engine", create)
    assert cli.main() == 1
    load.assert_not_called()
    create.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"blocked: {message}\n"


@pytest.mark.parametrize("message", [
    "automation bootstrap requires GitHub Actions",
    "automation bootstrap branch does not match environment",
    "automation email does not match environment",
    "existing automation user or access does not match",
    "existing automation users are ambiguous",
])
def test_cli_reports_safe_bootstrap_diagnostics(monkeypatch, capsys, message):
    monkeypatch.setattr(cli, "_run", Mock(side_effect=PortalAutomationBootstrapError(message)))
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"blocked: {message}\n"
