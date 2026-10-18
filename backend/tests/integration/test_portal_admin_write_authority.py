"""Request authority and real transaction serialization on owned formal SQLite."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import json
import os
import re
import subprocess
import sys
import uuid
from unittest.mock import MagicMock, Mock

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src import db
from src.api import auth, portal


BODY = {"account": "target@example.invalid", "role": "operator", "status": "active", "systems": ["hospital"]}
ACTOR = auth.UserContext("admin", "admin@example.invalid")


def snapshot(engine):
    with engine.connect() as connection:
        return {
            name: sorted(repr(tuple(row)) for row in connection.exec_driver_sql(f'SELECT * FROM "{name}"'))
            for name in ("users", "user_system_access", "audit_logs")
        }


@pytest.fixture
def owned(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'authority.sqlite3'}"
    engine = sa.create_engine(url, connect_args={"check_same_thread": False, "timeout": 5})

    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    sa.event.listen(engine, "connect", foreign_keys)
    for key in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ALEMBIC_DB_URI", url)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users(id,account,role,status,created_at) VALUES(:id,:account,:role,'active',CURRENT_TIMESTAMP)"
        ), [
            {"id": "admin", "account": ACTOR.account, "role": "admin"},
            {"id": "second", "account": "second@example.invalid", "role": "admin"},
            {"id": "target", "account": BODY["account"], "role": "operator"},
        ])
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    monkeypatch.setattr(portal, "session_scope", db.session_scope)
    monkeypatch.setattr(auth, "session_scope", db.session_scope)
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "google")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "_USER_ROLE_CACHE_TTL_SECONDS", 600)
    monkeypatch.setattr(auth, "_google_email_or_none", lambda request: ACTOR.account)
    auth.invalidate_user_cache()
    app = FastAPI()
    app.include_router(portal.router)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield engine, client
    finally:
        auth.invalidate_user_cache()
        engine.dispose()
        sa.event.remove(engine, "connect", foreign_keys)


def submit(client, operation, body=None):
    body = dict(BODY if body is None else body)
    if operation == "create":
        body["account"] = "new@example.invalid"
        return client.post("/portal/users", json=body)
    return client.put("/portal/users/target", json=body)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("retirement", ["role='operator'", "status='inactive'"])
def test_cached_admin_cannot_write_after_committed_retirement(owned, operation, retirement):
    engine, client = owned
    assert auth._registered_role(ACTOR.account) == "admin"
    with engine.begin() as connection:
        connection.exec_driver_sql(f"UPDATE users SET {retirement} WHERE id='admin'")
    assert auth._registered_role(ACTOR.account) == "admin"
    before = snapshot(engine)
    response = submit(client, operation)
    assert response.status_code == 403, response.text
    assert snapshot(engine) == before


@pytest.mark.parametrize("operation", ["create", "update"])
def test_current_admin_preserves_response_access_and_audit(owned, operation):
    engine, client = owned
    response = submit(client, operation)
    assert response.status_code == 200, response.text
    expected = dict(BODY, account="new@example.invalid") if operation == "create" else BODY
    result = response.json()["user"]
    assert {key: result[key] for key in expected} == expected
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 1
        assert connection.execute(sa.text("SELECT system_key FROM user_system_access WHERE user_id=:id"), {"id": result["id"]}).scalars().all() == ["hospital"]


@pytest.mark.parametrize("kind", ["missing-account", "unregistered", "duplicate"])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_actor_must_be_unique_and_present(owned, kind, operation):
    engine, client = owned
    actor = ACTOR
    if kind == "missing-account":
        actor = auth.UserContext("admin")
    elif kind == "unregistered":
        actor = auth.UserContext("admin", "absent@example.invalid")
    else:
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO users(id,account,role,status,created_at) VALUES('duplicate','admin@example.invalid','admin','active',CURRENT_TIMESTAMP)")
    # Portal keeps the registered dependency even when another test reloads auth.
    client.app.dependency_overrides[portal.get_current_user] = lambda: actor
    before = snapshot(engine)
    assert submit(client, operation).status_code == 403
    assert snapshot(engine) == before


@pytest.mark.parametrize("operation", ["create", "update"])
def test_authority_read_failure_is_503_and_no_writes(owned, operation):
    engine, client = owned
    assert auth._registered_role(ACTOR.account) == "admin"
    before = snapshot(engine)

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT id, account, role, status FROM users"):
            raise sa.exc.OperationalError(statement, parameters, RuntimeError("owned lookup failure"))

    sa.event.listen(engine, "before_cursor_execute", fail)
    try:
        assert submit(client, operation).status_code == 503
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail)
    assert snapshot(engine) == before


def test_self_demotion_succeeds_then_same_cached_context_is_denied(owned):
    engine, client = owned
    assert auth._registered_role(ACTOR.account) == "admin"
    response = client.put("/portal/users/admin", json=dict(BODY, account=ACTOR.account))
    assert response.status_code == 200
    before = snapshot(engine)
    assert submit(client, "create").status_code == 403
    assert snapshot(engine) == before


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("retirement", ["role='operator'", "status='inactive'"])
def test_same_cached_context_cannot_bypass_shared_save(owned, operation, retirement):
    engine, _client = owned
    assert auth._registered_role(ACTOR.account) == ACTOR.role
    with engine.begin() as connection:
        connection.exec_driver_sql(f"UPDATE users SET {retirement} WHERE id='admin'")
    before = snapshot(engine)
    with pytest.raises(HTTPException) as caught:
        portal._save(dict(BODY, account="new@example.invalid") if operation == "create" else BODY,
                     ACTOR, None if operation == "create" else "target")
    assert caught.value.status_code == 403
    assert snapshot(engine) == before


def test_explicit_disabled_auth_keeps_existing_behavior(owned, monkeypatch):
    _engine, client = owned
    monkeypatch.setenv("AUTH_DISABLED", "true")
    assert submit(client, "create").status_code == 200


@pytest.mark.parametrize("operation", ["create", "update"])
def test_real_concurrent_retirement_commits_before_actor_write(owned, operation):
    engine, client = owned
    assert auth._registered_role(ACTOR.account) == "admin"
    attempting = threading.Event()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            attempting.set()

    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as retirement, ThreadPoolExecutor(max_workers=1) as pool:
            retirement.exec_driver_sql("BEGIN IMMEDIATE")
            retirement.exec_driver_sql("UPDATE users SET status='inactive' WHERE id='admin'")
            attempting.clear()
            future = pool.submit(submit, client, operation)
            assert attempting.wait(3)
            retirement.commit()
            response = future.result(timeout=10)
        assert response.status_code == 403, response.text
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM user_system_access").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM users").scalar_one() == 3
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)


def test_two_admins_cross_demotion_is_serialized(owned):
    engine, _client = owned
    barrier = threading.Barrier(2)

    def demote(actor, target, account):
        barrier.wait(timeout=5)
        try:
            portal._save(dict(BODY, account=account), actor, target)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(demote, ACTOR, "second", "second@example.invalid")
        second = pool.submit(demote, auth.UserContext("admin", "second@example.invalid"), "admin", ACTOR.account)
        assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [200, 403]
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 1


def test_busy_reservation_stops_409_without_writes(owned, monkeypatch):
    engine, client = owned
    # Use a separate engine with an immediate timeout, not a mocked SQL result.
    contender = sa.create_engine(engine.url, connect_args={"check_same_thread": False, "timeout": 0})
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    sa.event.listen(contender, "connect", foreign_keys)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=contender, autoflush=False))
    assert auth._registered_role(ACTOR.account) == "admin"
    before = snapshot(engine)
    try:
        with engine.connect() as owner:
            owner.exec_driver_sql("BEGIN IMMEDIATE")
            assert submit(client, "create").status_code == 409
            owner.rollback()
        assert snapshot(engine) == before
    finally:
        contender.dispose()
        sa.event.remove(contender, "connect", foreign_keys)


def _validated_ci_pg_uri(environment):
    """No connection or environment mutation; absent local profile is unverified."""
    uri = environment.get("TEST_BOOTSTRAP_DB_URI")
    if "TEST_BOOTSTRAP_DB_URI" not in environment and environment.get("GITHUB_ACTIONS") != "true":
        return None
    required = {
        "GITHUB_ACTIONS": "true", "CI": "true",
        "GITHUB_REPOSITORY": "mmorinag127/sawa-hospital-system",
        "GITHUB_WORKFLOW": "CI", "GITHUB_JOB": "backend",
        "GITHUB_SERVER_URL": "https://github.com",
        "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
    }
    if any(environment.get(key) != value for key, value in required.items()):
        raise ValueError("R39 PostgreSQL requires the canonical hosted CI job")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        if not re.fullmatch(r"[1-9][0-9]*", environment.get(key, "")):
            raise ValueError("R39 PostgreSQL requires a CI run identity")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        if not re.fullmatch(r"[0-9a-f]{40}", environment.get(key, "")):
            raise ValueError("R39 PostgreSQL requires a source identity")
    event, ref = environment.get("GITHUB_EVENT_NAME"), environment.get("GITHUB_REF", "")
    if not ((event == "pull_request" and re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", ref)) or
            (event == "push" and re.fullmatch(r"refs/heads/(develop|main|master|release/prod-[A-Za-z0-9._/-]+)", ref))):
        raise ValueError("R39 PostgreSQL requires the existing CI event/ref")
    if environment.get("GITHUB_WORKFLOW_REF") != f"mmorinag127/sawa-hospital-system/.github/workflows/ci.yml@{ref}":
        raise ValueError("R39 PostgreSQL requires the canonical workflow identity")
    if uri != "postgresql+psycopg2://postgres:ci-bootstrap-only@localhost:5432/bootstrap_test":
        raise ValueError("R39 PostgreSQL requires the exact existing CI service profile")
    return uri


def _require_pg_identity(connection, database):
    actual = connection.execute(sa.text(
        "SELECT current_database(), current_user, current_setting('server_version_num')::integer"
    )).one()
    if actual[0] != database or actual[1] != "postgres" or actual[2] // 10000 != 16:
        raise RuntimeError("R39 PostgreSQL database/owner/version mismatch")


def _require_created_pg_receipt(receipt, expected):
    bindings = ("database", "run_id", "source_sha", "run_attempt", "workflow_ref", "workflow_sha")
    if any(key not in expected or receipt.get(key) != expected[key] for key in bindings):
        raise RuntimeError("R39 PostgreSQL cleanup receipt binding mismatch; refusing DROP")
    database = expected["database"]
    if not isinstance(database, str) or len(database) > 63 or not re.fullmatch(r"r39_admin_[1-9][0-9]*_[0-9a-f]{32}", database):
        raise RuntimeError("R39 PostgreSQL cleanup database name is invalid; refusing DROP")
    if not database.startswith(f"r39_admin_{expected['run_id']}_"):
        raise RuntimeError("R39 PostgreSQL cleanup database/run mismatch; refusing DROP")
    oid = receipt.get("oid")
    if receipt.get("created") is not True or type(oid) is not int or oid <= 0 or receipt.get("owner") != "postgres":
        raise RuntimeError("R39 PostgreSQL cleanup requires a captured positive OID and owner; refusing DROP")
    return oid


def _capture_created_pg_oid(connection, receipt):
    if receipt.get("created") is not True or "oid" in receipt:
        raise RuntimeError("R39 PostgreSQL creation identity cannot be adopted or recaptured")
    row = connection.execute(sa.text(
        "SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname=:name"
    ), {"name": receipt["database"]}).one()
    if type(row[0]) is not int or row[0] <= 0 or row[1] != "postgres":
        raise RuntimeError("R39 PostgreSQL created database identity mismatch")
    receipt.update(oid=row[0], owner=row[1])


def _drop_created_pg_database(bootstrap, receipt, expected):
    oid = _require_created_pg_receipt(receipt, expected)
    database = expected["database"]
    with bootstrap.connect() as connection:
        _require_pg_identity(connection, "bootstrap_test")
        row = connection.execute(sa.text(
            "SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname=:name"
        ), {"name": database}).one()
        if type(row[0]) is not int or row[0] != oid or row[1] != "postgres":
            raise RuntimeError("R39 PostgreSQL cleanup ownership mismatch; refusing DROP")
        connection.exec_driver_sql(f'DROP DATABASE "{database}"')
        if connection.execute(sa.text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": database}).scalar_one_or_none() is not None:
            raise RuntimeError("R39 PostgreSQL owned database remains after DROP")
        receipt["dropped"] = True


def _record_pg_receipt(receipt, paths):
    content = json.dumps(receipt, indent=2) + "\n"
    errors = []
    for path in paths:
        try:
            path.write_text(content)
        except OSError as exc:
            errors.append(type(exc).__name__)
    if errors:
        receipt["record_error_types"] = errors
        # Only non-secret ownership metadata; retained in pytest/CI logs if files fail.
        print("R39 PG receipt write failure: " + json.dumps(receipt, sort_keys=True))
        raise OSError("R39 PG ownership receipt artifact write failed")


@pytest.fixture
def owned_pg(tmp_path, monkeypatch):
    uri = _validated_ci_pg_uri(os.environ)
    if uri is None:
        pytest.skip("UNVERIFIED PostgreSQL: no canonical GitHub CI service profile")
    repo = Path(__file__).resolve().parents[3]
    if Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve() != repo:
        pytest.fail("R39 PostgreSQL requires the actual CI checkout workspace")
    checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if checkout_sha != os.environ["GITHUB_SHA"]:
        pytest.fail("R39 PostgreSQL requires the exact CI checkout source")
    database = f"r39_admin_{os.environ['GITHUB_RUN_ID']}_{uuid.uuid4().hex}"
    if len(database) > 63 or not re.fullmatch(r"r39_admin_[1-9][0-9]*_[0-9a-f]{32}", database):
        pytest.fail("R39 PostgreSQL owned database name is invalid")
    expected = {"database": database,
                "run_id": os.environ["GITHUB_RUN_ID"], "source_sha": os.environ["GITHUB_SHA"],
                "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
                "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"]}
    receipt = {**expected, "created": False, "dropped": False}
    receipt_path = tmp_path / "pg-ownership.json"
    # The existing CI log artifact already includes backend/.logs.
    log_directory = repo / "backend/.logs/r39-pg-ownership"
    log_directory.mkdir(parents=True, exist_ok=True)
    artifact_receipt = log_directory / f"{database}.json"

    def record():
        _record_pg_receipt(receipt, (receipt_path, artifact_receipt))

    bootstrap = sa.create_engine(uri, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool,
                                 hide_parameters=True, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"})
    engine = None
    try:
        with bootstrap.connect() as connection:
            _require_pg_identity(connection, "bootstrap_test")
            exists = connection.execute(sa.text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": database}).scalar_one_or_none()
            if exists is not None:
                raise RuntimeError("R39 PostgreSQL owned database already exists; refusing adoption")
            connection.exec_driver_sql(f'CREATE DATABASE "{database}" OWNER postgres TEMPLATE template0')
            receipt["created"] = True
            _capture_created_pg_oid(connection, receipt)
            record()
        url = sa.engine.make_url(uri).set(database=database)
        engine = sa.create_engine(url, hide_parameters=True,
                                  connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"})
        with engine.connect() as connection:
            _require_pg_identity(connection, database)
            connection.rollback()
            for key in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
                monkeypatch.delenv(key, raising=False)
            monkeypatch.setenv("ALEMBIC_DB_URI", url.render_as_string(hide_password=False))
            config = Config(str(repo / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO users(id,account,role,status,created_at) VALUES(:id,:account,:role,'active',CURRENT_TIMESTAMP)"
            ), [{"id": "admin", "account": ACTOR.account, "role": "admin"},
                {"id": "second", "account": "second@example.invalid", "role": "admin"},
                {"id": "target", "account": BODY["account"], "role": "operator"}])
        monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
        monkeypatch.setattr(portal, "session_scope", db.session_scope)
        monkeypatch.setattr(auth, "session_scope", db.session_scope)
        monkeypatch.setattr(auth, "AUTH_PROVIDER", "google")
        monkeypatch.setenv("AUTH_DISABLED", "false")
        monkeypatch.setattr(auth, "_USER_ROLE_CACHE_TTL_SECONDS", 600)
        monkeypatch.setattr(auth, "_google_email_or_none", lambda request: ACTOR.account)
        auth.invalidate_user_cache()
        app = FastAPI()
        app.include_router(portal.router)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield engine, client
    except Exception as exc:
        receipt["failure_type"] = type(exc).__name__
        try:
            record()
        except OSError:
            pass  # The diagnostic was logged; preserve the original setup failure.
        raise
    finally:
        auth.invalidate_user_cache()
        try:
            if engine is not None:
                engine.dispose()
            if receipt["created"]:
                _drop_created_pg_database(bootstrap, receipt, expected)
        except Exception as exc:
            receipt["cleanup_error_type"] = type(exc).__name__
            receipt["leftover_database_unverified"] = receipt["created"] and not receipt["dropped"]
            raise
        finally:
            try:
                bootstrap.dispose()
            except Exception as exc:
                receipt["bootstrap_cleanup_error_type"] = type(exc).__name__
                raise
            finally:
                failure_in_progress = sys.exc_info()[0] is not None
                try:
                    record()
                except OSError:
                    if not failure_in_progress:
                        raise


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("retirement", ["role='operator'", "status='inactive'"])
def test_pg_cached_admin_after_independent_committed_retirement(owned_pg, operation, retirement):
    engine, client = owned_pg
    assert auth._registered_role(ACTOR.account) == "admin"
    with engine.begin() as connection:
        connection.exec_driver_sql(f"UPDATE users SET {retirement} WHERE id='admin'")
    assert auth._registered_role(ACTOR.account) == "admin"
    before = snapshot(engine)
    assert submit(client, operation).status_code == 403
    assert snapshot(engine) == before


@pytest.mark.parametrize("operation", ["create", "update"])
def test_pg_current_admin_response_access_and_audit(owned_pg, operation):
    engine, client = owned_pg
    response = submit(client, operation)
    assert response.status_code == 200
    result = response.json()["user"]
    expected = dict(BODY, account="new@example.invalid") if operation == "create" else BODY
    assert {key: result[key] for key in expected} == expected
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 1
        assert connection.execute(sa.text("SELECT system_key FROM user_system_access WHERE user_id=:id"), {"id": result["id"]}).scalars().all() == ["hospital"]


@pytest.mark.parametrize("operation", ["create", "update"])
def test_pg_retirement_row_lock_nowait_then_same_cached_actor_denied(owned_pg, operation):
    engine, client = owned_pg
    assert auth._registered_role(ACTOR.account) == "admin"
    before = snapshot(engine)
    attempting = threading.Event()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE NOWAIT" in statement:
            attempting.set()

    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as retirement, ThreadPoolExecutor(max_workers=1) as pool:
            retirement.exec_driver_sql("UPDATE users SET status='inactive' WHERE id='admin'")
            future = pool.submit(submit, client, operation)
            assert attempting.wait(5)
            assert future.result(timeout=10).status_code == 409
            # Other connections still see the committed pre-retirement snapshot.
            assert snapshot(engine) == before
            retirement.commit()
        after = snapshot(engine)
        assert submit(client, operation).status_code == 403
        assert snapshot(engine) == after
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 0
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)


def test_pg_two_admins_cross_demotion_serializes_real_connections(owned_pg):
    engine, _client = owned_pg
    barrier = threading.Barrier(2)

    def demote(actor, target, account):
        barrier.wait(timeout=5)
        try:
            portal._save(dict(BODY, account=account), actor, target)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(demote, ACTOR, "second", "second@example.invalid")
        second = pool.submit(demote, auth.UserContext("admin", "second@example.invalid"), "admin", ACTOR.account)
        outcomes = sorted([first.result(timeout=15), second.result(timeout=15)])
    assert outcomes in ([200, 403], [200, 409])
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 1


@pytest.mark.parametrize("kind", ["missing", "duplicate"])
def test_pg_unknown_or_ambiguous_actor_stops_shared_save(owned_pg, kind):
    engine, _client = owned_pg
    actor = auth.UserContext("admin", "absent@example.invalid") if kind == "missing" else ACTOR
    if kind == "duplicate":
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO users(id,account,role,status,created_at) VALUES('duplicate','admin@example.invalid','admin','active',CURRENT_TIMESTAMP)")
    before = snapshot(engine)
    with pytest.raises(HTTPException) as caught:
        portal._save(dict(BODY, account="new@example.invalid"), actor)
    assert caught.value.status_code == 403
    assert snapshot(engine) == before


def _synthetic_ci_metadata():
    # Pure validator input only. Never installed in os.environ or used to connect.
    return {
        "GITHUB_ACTIONS": "true", "CI": "true",
        "GITHUB_REPOSITORY": "mmorinag127/sawa-hospital-system",
        "GITHUB_WORKFLOW": "CI", "GITHUB_JOB": "backend",
        "GITHUB_SERVER_URL": "https://github.com",
        "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "a" * 40, "GITHUB_WORKFLOW_SHA": "b" * 40,
        "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/1/merge",
        "GITHUB_WORKFLOW_REF": "mmorinag127/sawa-hospital-system/.github/workflows/ci.yml@refs/pull/1/merge",
        "TEST_BOOTSTRAP_DB_URI": "postgresql+psycopg2://postgres:ci-bootstrap-only@localhost:5432/bootstrap_test",
    }


def test_pg_profile_validator_absent_local_is_unverified_not_proof():
    assert _validated_ci_pg_uri({}) is None


def test_pg_profile_validator_synthetic_metadata_only_never_connects():
    metadata = _synthetic_ci_metadata()
    assert _validated_ci_pg_uri(metadata) == metadata["TEST_BOOTSTRAP_DB_URI"]


@pytest.mark.parametrize("key,value", [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "other/repo"),
    ("GITHUB_WORKFLOW", "Other"), ("GITHUB_JOB", "frontend"),
    ("GITHUB_RUN_ID", ""), ("GITHUB_RUN_ATTEMPT", "0"),
    ("GITHUB_SHA", "invalid"), ("GITHUB_WORKFLOW_SHA", "invalid"),
    ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_REF", "refs/heads/unapproved"),
    ("GITHUB_WORKFLOW_REF", "other/workflow"), ("RUNNER_ENVIRONMENT", "self-hosted"),
    ("TEST_BOOTSTRAP_DB_URI", ""), ("TEST_BOOTSTRAP_DB_URI", "postgresql://localhost/other"),
    ("TEST_BOOTSTRAP_DB_URI", "postgresql+psycopg2://postgres:ci-bootstrap-only@localhost:5432/bootstrap_test?options=unsafe"),
])
def test_pg_profile_validator_rejects_invalid_metadata_before_connect(key, value):
    metadata = _synthetic_ci_metadata()
    metadata[key] = value
    with pytest.raises(ValueError):
        _validated_ci_pg_uri(metadata)


@pytest.mark.parametrize("profile", ["", "sqlite:///forbidden"])
def test_pg_fixture_invalid_provided_local_profile_stops_before_engine(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("TEST_BOOTSTRAP_DB_URI", profile)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    attempts = []

    def forbidden_connect(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Invalid PG profile must not construct an engine")

    monkeypatch.setattr(sa, "create_engine", forbidden_connect)
    fixture = owned_pg.__wrapped__(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError):
            next(fixture)
    finally:
        fixture.close()
    assert attempts == []


def _unit_ownership_receipt():
    # Synthetic guard inputs only: no real DB, CI environment or lock evidence.
    metadata = _synthetic_ci_metadata()
    expected = {"database": "r39_admin_123_" + "0" * 32,
                "run_id": metadata["GITHUB_RUN_ID"], "source_sha": metadata["GITHUB_SHA"],
                "run_attempt": metadata["GITHUB_RUN_ATTEMPT"],
                "workflow_ref": metadata["GITHUB_WORKFLOW_REF"],
                "workflow_sha": metadata["GITHUB_WORKFLOW_SHA"]}
    return expected, {**expected, "created": True, "dropped": False, "oid": 42, "owner": "postgres"}


def _unit_drop_transport(oid=42, owner="postgres"):
    # Control-flow unit double, explicitly NOT PostgreSQL proof.
    bootstrap = MagicMock()
    connection = bootstrap.connect.return_value.__enter__.return_value
    identity, observed, absent = Mock(), Mock(), Mock()
    identity.one.return_value = ("bootstrap_test", "postgres", 160000)
    observed.one.return_value = (oid, owner)
    absent.scalar_one_or_none.return_value = None
    connection.execute.side_effect = [identity, observed, absent]
    return bootstrap, connection


@pytest.mark.parametrize("kind", [
    "missing-oid", "null-oid", "true-oid", "false-oid", "zero-oid", "negative-oid", "string-oid",
    "missing-owner", "foreign-owner", "not-created", "truthy-created",
    "database", "run_id", "source_sha", "run_attempt", "workflow_ref", "workflow_sha",
])
def test_unit_unverified_creation_receipt_denies_before_cleanup_connect(kind):
    expected, receipt = _unit_ownership_receipt()
    if kind == "missing-oid":
        receipt.pop("oid")
    elif kind.endswith("-oid"):
        receipt["oid"] = {"null-oid": None, "true-oid": True, "false-oid": False,
                          "zero-oid": 0, "negative-oid": -1, "string-oid": "42"}[kind]
    elif kind == "missing-owner":
        receipt.pop("owner")
    elif kind == "foreign-owner":
        receipt["owner"] = "other"
    elif kind == "not-created":
        receipt["created"] = False
    elif kind == "truthy-created":
        receipt["created"] = 1
    else:
        receipt[kind] = "mismatched"
    bootstrap = MagicMock()
    with pytest.raises(RuntimeError, match="refusing DROP"):
        _drop_created_pg_database(bootstrap, receipt, expected)
    bootstrap.connect.assert_not_called()
    assert receipt["dropped"] is False


@pytest.mark.parametrize("oid,owner", [(43, "postgres"), (None, "postgres"),
                                      (True, "postgres"), (0, "postgres"), (42, "other")])
def test_unit_current_database_oid_owner_mismatch_denies_before_drop(oid, owner):
    expected, receipt = _unit_ownership_receipt()
    bootstrap, connection = _unit_drop_transport(oid, owner)
    with pytest.raises(RuntimeError, match="refusing DROP"):
        _drop_created_pg_database(bootstrap, receipt, expected)
    bootstrap.connect.assert_called_once_with()
    connection.exec_driver_sql.assert_not_called()
    assert receipt["oid"] == 42
    assert receipt["dropped"] is False


def test_unit_matching_recorded_oid_owner_binding_allows_only_exact_drop():
    expected, receipt = _unit_ownership_receipt()
    assert _require_created_pg_receipt(receipt, expected) == 42
    bootstrap, connection = _unit_drop_transport()
    _drop_created_pg_database(bootstrap, receipt, expected)
    connection.exec_driver_sql.assert_called_once_with(f'DROP DATABASE "{expected["database"]}"')
    assert receipt["dropped"] is True


def test_unit_creation_oid_capture_failure_never_adopts_cleanup_lookup():
    expected, receipt = _unit_ownership_receipt()
    receipt.pop("oid")
    receipt.pop("owner")
    creation = Mock()
    creation.execute.side_effect = RuntimeError("unit creation OID read failure")
    with pytest.raises(RuntimeError, match="OID read failure"):
        _capture_created_pg_oid(creation, receipt)
    bootstrap = MagicMock()
    with pytest.raises(RuntimeError, match="captured positive OID"):
        _drop_created_pg_database(bootstrap, receipt, expected)
    bootstrap.connect.assert_not_called()
    assert "oid" not in receipt


@pytest.mark.parametrize("oid,owner", [(None, "postgres"), (True, "postgres"),
                                      (0, "postgres"), (42, "other")])
def test_unit_invalid_creation_oid_is_not_recorded_or_recovered(oid, owner):
    expected, receipt = _unit_ownership_receipt()
    receipt.pop("oid")
    receipt.pop("owner")
    creation = Mock()
    creation.execute.return_value.one.return_value = (oid, owner)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        _capture_created_pg_oid(creation, receipt)
    assert "oid" not in receipt and "owner" not in receipt
    bootstrap = MagicMock()
    with pytest.raises(RuntimeError, match="captured positive OID"):
        _drop_created_pg_database(bootstrap, receipt, expected)
    bootstrap.connect.assert_not_called()


def test_unit_memory_oid_captured_before_artifact_failure_can_cleanup_but_error_remains(tmp_path, monkeypatch, capsys):
    expected, receipt = _unit_ownership_receipt()
    receipt.pop("oid")
    receipt.pop("owner")
    creation = Mock()
    creation.execute.return_value.one.return_value = (42, "postgres")
    _capture_created_pg_oid(creation, receipt)
    bad, good = tmp_path / "bad.json", tmp_path / "good.json"
    write_text = Path.write_text

    def refuse_first(path, *args, **kwargs):
        if path == bad:
            raise OSError("unit artifact failure")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse_first)
    bootstrap, connection = _unit_drop_transport()
    with pytest.raises(OSError, match="artifact write failed"):
        try:
            _record_pg_receipt(receipt, (bad, good))
        finally:
            _drop_created_pg_database(bootstrap, receipt, expected)
    assert receipt["dropped"] is True
    assert json.loads(good.read_text())["oid"] == 42
    connection.exec_driver_sql.assert_called_once()
    assert "record_error_types" in capsys.readouterr().out


def test_unit_uncaptured_oid_and_broken_artifacts_log_failure_without_drop(tmp_path, monkeypatch, capsys):
    expected, receipt = _unit_ownership_receipt()
    receipt.pop("oid")
    receipt.pop("owner")

    def refuse_write(*args, **kwargs):
        raise OSError("unit artifact failure")

    monkeypatch.setattr(Path, "write_text", refuse_write)
    with pytest.raises(OSError):
        _record_pg_receipt(receipt, (tmp_path / "first.json", tmp_path / "second.json"))
    receipt["failure_type"] = "OSError"
    bootstrap = MagicMock()
    with pytest.raises(RuntimeError, match="captured positive OID") as failure:
        _drop_created_pg_database(bootstrap, receipt, expected)
    receipt["cleanup_error_type"] = type(failure.value).__name__
    receipt["leftover_database_unverified"] = True
    with pytest.raises(OSError):
        _record_pg_receipt(receipt, (tmp_path / "first.json", tmp_path / "second.json"))
    bootstrap.connect.assert_not_called()
    output = capsys.readouterr().out
    assert '"failure_type": "OSError"' in output
    assert '"cleanup_error_type": "RuntimeError"' in output
    assert '"leftover_database_unverified": true' in output
