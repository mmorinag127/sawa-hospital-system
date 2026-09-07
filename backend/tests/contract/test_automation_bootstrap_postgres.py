import os
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.portal_access_bootstrap_service import (  # noqa: E402
    PortalAccessBootstrapError,
    ensure_user_system_access_schema,
)
from src.services.portal_automation_bootstrap_service import (  # noqa: E402
    run_portal_automation_bootstrap,
)


CANONICAL_NAME = "ck_user_system_access_system_key"
CANONICAL_CHECK = "system_key IN ('hospital', 'shift', 'school-lunch')"
EMAIL = "sawa-ui-verify-stg@sawahospitalsystem.iam.gserviceaccount.com"


@pytest.fixture
def connection(monkeypatch):
    uri = os.environ.get("TEST_BOOTSTRAP_DB_URI")
    if not uri:
        pytest.skip("TEST_BOOTSTRAP_DB_URI is required for real PostgreSQL tests")
    assert make_url(uri).get_backend_name() == "postgresql", "PostgreSQL is required"
    engine = create_engine(uri, future=True)
    schema = f"automation_bootstrap_{uuid.uuid4().hex}"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_REF_NAME", "develop")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/develop")
    try:
        with engine.connect() as connection:
            created = False
            try:
                with connection.begin():
                    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                created = True
                with connection.begin():
                    # Exclude public so neither fixtures nor bootstrap can touch it.
                    connection.execute(text(f'SET search_path TO "{schema}"'))
                    connection.execute(text(
                        "CREATE TABLE users (id VARCHAR PRIMARY KEY, account VARCHAR NOT NULL, "
                        "role VARCHAR NOT NULL, status VARCHAR, created_at TIMESTAMP)"
                    ))
                    connection.execute(text(
                        "CREATE TABLE user_system_access (user_id VARCHAR NOT NULL, "
                        "system_key VARCHAR NOT NULL, enabled BOOLEAN NOT NULL, "
                        "PRIMARY KEY (user_id, system_key), "
                        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
                    ))
                    connection.execute(text(
                        "CREATE TABLE audit_logs (id VARCHAR PRIMARY KEY, actor VARCHAR NOT NULL, "
                        "action VARCHAR NOT NULL, target VARCHAR NOT NULL, fac VARCHAR, wek VARCHAR, "
                        "metadata JSON, created_at TIMESTAMP)"
                    ))
                    connection.execute(text(
                        "INSERT INTO users VALUES "
                        "('other', 'other@example.com', 'admin', 'active', CURRENT_TIMESTAMP), "
                        "('inactive', 'inactive@example.com', 'operator', 'inactive', CURRENT_TIMESTAMP)"
                    ))
                    connection.execute(text(
                        "INSERT INTO user_system_access VALUES "
                        "('other', 'hospital', TRUE), ('other', 'school-lunch', FALSE), "
                        "('inactive', 'shift', FALSE)"
                    ))
                    connection.execute(text(
                        "INSERT INTO audit_logs(id, actor, action, target, created_at) "
                        "VALUES ('existing-audit', 'existing-actor', 'existing-action', "
                        "'other', CURRENT_TIMESTAMP)"
                    ))
                yield connection
            finally:
                connection.rollback()
                if created:
                    with connection.begin():
                        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        engine.dispose()


def _snapshot(connection):
    return {
        table: connection.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2")).all()
        for table in ("users", "user_system_access", "audit_logs")
    }


def _checks(connection):
    # OID comparison also detects a dropped/recreated constraint.
    return connection.execute(text(
        "SELECT oid, conname, pg_get_constraintdef(oid), convalidated "
        "FROM pg_constraint WHERE conrelid = 'user_system_access'::regclass "
        "AND contype = 'c' ORDER BY conname"
    )).all()


def _add_check(connection, expression, name=CANONICAL_NAME):
    connection.execute(text(
        f'ALTER TABLE user_system_access ADD CONSTRAINT "{name}" CHECK ({expression})'
    ))


def _bootstrap(connection):
    return run_portal_automation_bootstrap(connection, environment="stg", email=EMAIL)


def test_missing_check_is_repaired_preserving_data_and_is_idempotent(connection):
    with connection.begin():
        before = _snapshot(connection)
        assert _checks(connection) == []
        assert ensure_user_system_access_schema(connection) is True
        repaired = _checks(connection)
        assert len(repaired) == 1
        assert repaired[0][1] == CANONICAL_NAME
        assert repaired[0][3] is True
        expected_definition = connection.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'user_system_access'::regclass AND conname = :name"
        ), {"name": CANONICAL_NAME}).scalar_one()
        for key in ("hospital", "shift", "school-lunch"):
            assert f"'{key}'" in expected_definition
        assert _snapshot(connection) == before

    with connection.begin():
        assert ensure_user_system_access_schema(connection) is False
        assert _checks(connection) == repaired
        assert _snapshot(connection) == before
        result = _bootstrap(connection)
        assert result["created"] is True
        after = _snapshot(connection)
        for table, rows in before.items():
            assert [row for row in after[table] if row[0] in {r[0] for r in rows}] == rows
        assert connection.execute(text(
            "SELECT account, role, status FROM users WHERE id = :id"
        ), {"id": result["user_id"]}).one() == (EMAIL, "operator", "active")
        assert connection.execute(text(
            "SELECT system_key, enabled FROM user_system_access WHERE user_id = :id"
        ), {"id": result["user_id"]}).all() == [("shift", True)]

    with connection.begin():
        assert _bootstrap(connection) == {"user_id": result["user_id"], "created": False}
        assert _snapshot(connection) == after
        assert _checks(connection) == repaired

    with pytest.raises(IntegrityError):
        with connection.begin():
            connection.execute(text(
                "INSERT INTO user_system_access VALUES ('other', 'invalid-system', TRUE)"
            ))
    with connection.begin():
        assert _snapshot(connection) == after
        assert _checks(connection) == repaired


def test_existing_canonical_check_is_unchanged(connection):
    with connection.begin():
        _add_check(connection, CANONICAL_CHECK)
        before = _snapshot(connection)
        checks = _checks(connection)
    for _ in range(2):
        with connection.begin():
            assert ensure_user_system_access_schema(connection) is False
            assert _checks(connection) == checks
            assert _snapshot(connection) == before


@pytest.mark.parametrize("operation", [ensure_user_system_access_schema, _bootstrap])
def test_invalid_existing_key_aborts_and_rolls_back_without_registration(connection, operation):
    with connection.begin():
        connection.execute(text(
            "INSERT INTO user_system_access VALUES ('other', 'invalid-system', TRUE)"
        ))
        before = _snapshot(connection)
        assert _checks(connection) == []
    with pytest.raises(IntegrityError) as failure:
        with connection.begin():
            operation(connection)
    original = failure.value.orig
    assert (getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)) == "23514"
    with connection.begin():
        assert _snapshot(connection) == before
        assert _checks(connection) == []
        assert connection.execute(text(
            "SELECT COUNT(*) FROM users WHERE account = :email"
        ), {"email": EMAIL}).scalar_one() == 0


@pytest.mark.parametrize("operation", [ensure_user_system_access_schema, _bootstrap])
@pytest.mark.parametrize("name", [CANONICAL_NAME, "legacy_system_key_check"])
@pytest.mark.parametrize("expression", [
    "system_key <> 'invalid-system'",
    "system_key IN ('hospital', 'shift', 'school-lunch', 'invalid-system')",
    "system_key IN ('hospital', 'shift', 'school-lunch') OR TRUE",
])
def test_different_existing_check_blocks_without_rewriting(connection, operation, name, expression):
    with connection.begin():
        _add_check(connection, expression, name)
        before = _snapshot(connection)
        checks = _checks(connection)
    with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
        with connection.begin():
            operation(connection)
    with connection.begin():
        assert _checks(connection) == checks
        assert _snapshot(connection) == before
        assert connection.execute(text(
            "SELECT COUNT(*) FROM users WHERE account = :email"
        ), {"email": EMAIL}).scalar_one() == 0


def test_unvalidated_check_blocks_without_registration(connection):
    with connection.begin():
        connection.execute(text(
            f"ALTER TABLE user_system_access ADD CONSTRAINT {CANONICAL_NAME} "
            f"CHECK ({CANONICAL_CHECK}) NOT VALID"
        ))
        before = _snapshot(connection)
        checks = _checks(connection)
    with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
        with connection.begin():
            _bootstrap(connection)
    with connection.begin():
        assert _checks(connection) == checks
        assert _snapshot(connection) == before
