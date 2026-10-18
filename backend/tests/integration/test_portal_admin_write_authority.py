"""Request authority and real transaction serialization on owned formal SQLite."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

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
    client.app.dependency_overrides[auth.get_current_user] = lambda: actor
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
