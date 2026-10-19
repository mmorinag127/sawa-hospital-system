"""Current authority on formal owned databases; only identity/HTTP transport is synthetic."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import uuid

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src import db
from src.api import auth, auth_config, portal
import test_portal_admin_write_authority as r39

ACCOUNT = "actor@current-authority.invalid"
BODY = {"account": "new@current-authority.invalid", "role": "operator", "status": "active", "systems": ["hospital"]}


def migrate(engine, monkeypatch):
    for name in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALEMBIC_DB_URI", engine.url.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@pytest.fixture(params=["sqlite", "pg"])
def database(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'current.sqlite3'}", connect_args={"check_same_thread": False, "timeout": 5})

        def fk(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        sa.event.listen(engine, "connect", fk)
        try:
            migrate(engine, monkeypatch)
            yield engine
        finally:
            engine.dispose()
            sa.event.remove(engine, "connect", fk)
        return
    uri = r39._validated_ci_pg_uri(os.environ)
    if uri is None:
        pytest.skip("UNVERIFIED PostgreSQL: no canonical hosted CI profile")
    repo = Path(__file__).resolve().parents[3]
    assert Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve() == repo
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == os.environ["GITHUB_SHA"]
    name = f"r39_admin_{os.environ['GITHUB_RUN_ID']}_{uuid.uuid4().hex}"
    expected = {"database": name, "run_id": os.environ["GITHUB_RUN_ID"], "source_sha": os.environ["GITHUB_SHA"],
                "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"], "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
                "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"]}
    receipt = dict(expected, created=False, dropped=False)
    bootstrap = sa.create_engine(uri, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool, hide_parameters=True)
    engine = None
    directory = repo / "backend/.logs/current-actor-pg"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with bootstrap.connect() as connection:
            r39._require_pg_identity(connection, "bootstrap_test")
            assert connection.execute(sa.text("SELECT oid FROM pg_database WHERE datname=:name"), {"name": name}).scalar_one_or_none() is None
            connection.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER postgres TEMPLATE template0')
            receipt["created"] = True
            r39._capture_created_pg_oid(connection, receipt)
        engine = sa.create_engine(sa.engine.make_url(uri).set(database=name), hide_parameters=True)
        with engine.connect() as connection:
            r39._require_pg_identity(connection, name)
        migrate(engine, monkeypatch)
        yield engine
    finally:
        try:
            if engine is not None:
                engine.dispose()
            if receipt["created"]:
                r39._drop_created_pg_database(bootstrap, receipt, expected)
        except Exception as exc:
            receipt["cleanup_error_type"] = type(exc).__name__
            raise
        finally:
            bootstrap.dispose()
            r39._record_pg_receipt(receipt, (tmp_path / "pg-ownership.json", directory / f"{name}.json"))


def seed(engine, user_id, *, account=ACCOUNT, role="admin", status="active", systems=("hospital", "shift", "school-lunch")):
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users(id,account,role,status,created_at) VALUES(:id,:account,:role,:status,CURRENT_TIMESTAMP)"),
                           {"id": user_id, "account": account, "role": role, "status": status})
        for system in systems:
            connection.execute(sa.text("INSERT INTO user_system_access(user_id,system_key,enabled) VALUES(:id,:system,TRUE)"), {"id": user_id, "system": system})


def all_tables(engine):
    with engine.connect() as connection:
        quote = engine.dialect.identifier_preparer.quote
        return {
            name: sorted(repr(tuple(row)) for row in connection.exec_driver_sql(f"SELECT * FROM {quote(name)}"))
            for name in sa.inspect(connection).get_table_names()
        }


@pytest.fixture
def owner(database, monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=database, autoflush=False))
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "google")
    monkeypatch.setattr(auth, "_google_email_or_none", lambda request: ACCOUNT)
    for key in ("AUTOMATION_AUTH_EMAIL", "SCHOOL_LUNCH_AUTOMATION_AUTH_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    app = FastAPI()
    app.include_router(portal.router)
    app.include_router(auth_config.router)

    @app.get("/admin")
    def admin(user=Depends(auth.get_current_admin)):
        return {"role": user.role, "account": user.account}

    @app.get("/operator")
    def operator(user=Depends(auth.get_current_operator)):
        return {"role": user.role, "account": user.account}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield database, client


@pytest.mark.parametrize("kind", ["demote", "inactive", "deleted", "grants", "promote"])
def test_committed_authority_changes_are_immediate(owner, kind):
    engine, client = owner
    seed(engine, "actor", role="operator" if kind == "promote" else "admin")
    assert client.get("/auth/me").status_code == 200
    captured = auth.UserContext("admin", ACCOUNT)
    with engine.begin() as connection:
        if kind == "deleted":
            connection.exec_driver_sql("DELETE FROM users WHERE id='actor'")
        elif kind == "grants":
            connection.exec_driver_sql("DELETE FROM user_system_access WHERE user_id='actor'")
        else:
            update = {"demote": "role='operator'", "promote": "role='admin'", "inactive": "status='inactive'"}[kind]
            connection.exec_driver_sql("UPDATE users SET " + update + " WHERE id='actor'")
    before = r39.snapshot(engine)
    invalid = kind in {"inactive", "deleted"}
    expected = "operator" if kind == "demote" else "admin"
    identity = client.get("/auth/me")
    assert identity.status_code == (403 if invalid else 200)
    if not invalid:
        assert identity.json()["role"] == expected
    projection = client.get("/portal/auth/me")
    assert projection.status_code == (403 if invalid else 200)
    if not invalid:
        assert projection.json() == {"role": expected, "account": ACCOUNT, "systems": [] if kind == "grants" else ["hospital", "school-lunch", "shift"]}
    assert client.get("/portal/users").status_code == (200 if kind in {"grants", "promote"} else 403)
    assert client.get("/admin").status_code == (200 if kind == "promote" else 403)
    assert client.get("/operator").status_code == (403 if invalid or kind == "grants" else 200)
    if kind in {"demote", "inactive", "deleted"}:
        with pytest.raises(HTTPException) as caught:
            portal._save(BODY, captured)
        assert caught.value.status_code == 403
    assert r39.snapshot(engine) == before


@pytest.mark.parametrize("roles,statuses", [(("admin", "operator"), ("active", "active")), (("admin", "admin"), ("active", "active")), (("operator", "admin"), ("active", "inactive"))])
@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_normalized_account_never_combines_authority(owner, roles, statuses, reverse):
    engine, client = owner
    rows = [("first", ACCOUNT.upper(), roles[0], statuses[0], ("hospital",)), ("second", " " + ACCOUNT + " ", roles[1], statuses[1], ("shift",))]
    for user_id, account, role, status, systems in reversed(rows) if reverse else rows:
        seed(engine, user_id, account=account, role=role, status=status, systems=systems)
    before = r39.snapshot(engine)
    for path in ("/auth/me", "/portal/auth/me", "/portal/auth/me?system=hospital", "/portal/auth/me?system=shift", "/portal/users", "/admin", "/operator"):
        assert client.get(path).status_code == 403
    with pytest.raises(HTTPException) as caught:
        portal._save(BODY, auth.UserContext("admin", ACCOUNT))
    assert caught.value.status_code == 403
    assert r39.snapshot(engine) == before


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_normal_role_projection_and_operator_coercion(owner, role):
    engine, client = owner
    seed(engine, "actor", role=role, systems=("hospital",))
    seed(engine, "unrelated", account="other@current-authority.invalid", systems=("shift",))
    before = r39.snapshot(engine)
    assert client.get("/auth/me").json()["role"] == role
    assert client.get("/operator").json() == {"role": "operator", "account": ACCOUNT}
    assert client.get("/admin").status_code == (200 if role == "admin" else 403)
    assert client.get("/portal/auth/me?system=shift").status_code == 403
    assert client.get("/portal/auth/me").json()["systems"] == ["hospital"]
    assert r39.snapshot(engine) == before


def test_missing_and_lookup_unavailable_do_not_fall_back(owner):
    engine, client = owner
    assert client.get("/auth/me").status_code == 403
    seed(engine, "actor")
    assert client.get("/auth/me").status_code == 200

    def unavailable(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT u.id, u.account, u.role, u.status"):
            raise sa.exc.OperationalError(statement, parameters, RuntimeError("synthetic SQL transport failure"))

    before = r39.snapshot(engine)
    sa.event.listen(engine, "before_cursor_execute", unavailable)
    try:
        for path in ("/auth/me", "/portal/auth/me", "/portal/users", "/admin", "/operator"):
            assert client.get(path).status_code == 503
    finally:
        sa.event.remove(engine, "before_cursor_execute", unavailable)
    assert r39.snapshot(engine) == before


def test_disabled_auth_remains_explicit(owner, monkeypatch):
    _engine, client = owner
    monkeypatch.setenv("AUTH_DISABLED", "true")
    assert client.get("/auth/me").json() == {"role": "admin", "auth_disabled": True}
    assert client.get("/admin").status_code == 200
    assert client.get("/operator").json()["role"] == "operator"
    assert client.get("/portal/users").status_code == 200
    assert client.get("/portal/auth/me").status_code == 403


@pytest.mark.parametrize("role,systems,expected", [("operator", ("shift",), 200), ("admin", ("shift",), 403), ("operator", ("shift", "hospital"), 403), ("operator", (), 403)])
def test_automation_retains_single_system_constraint(owner, monkeypatch, role, systems, expected):
    engine, client = owner
    monkeypatch.setenv("AUTOMATION_AUTH_EMAIL", ACCOUNT)
    seed(engine, "actor", role=role, systems=systems)
    before = r39.snapshot(engine)
    assert client.get("/portal/auth/me?system=shift").status_code == expected
    assert client.get("/admin").status_code == 403
    assert client.get("/operator").status_code == 403
    assert r39.snapshot(engine) == before


def test_current_admin_audit_and_later_captured_context_stop(owner):
    engine, client = owner
    seed(engine, "actor")
    response = client.post("/portal/users", json=BODY)
    assert response.status_code == 200
    captured = auth.UserContext("admin", ACCOUNT)
    with engine.begin() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 1
        connection.exec_driver_sql("UPDATE users SET role='operator' WHERE id='actor'")
    before = r39.snapshot(engine)
    for target in (None, response.json()["user"]["id"]):
        with pytest.raises(HTTPException) as caught:
            portal._save(BODY, captured, target)
        assert caught.value.status_code == 403
    assert r39.snapshot(engine) == before


def test_self_demotion_and_direct_stale_context_projection(owner):
    engine, client = owner
    seed(engine, "actor")
    captured = auth.UserContext("admin", ACCOUNT)
    response = client.put("/portal/users/actor", json=dict(BODY, account=ACCOUNT))
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "operator"
    before = r39.snapshot(engine)
    assert client.get("/auth/me").json()["role"] == "operator"
    assert portal.portal_me(user=captured)["role"] == "operator"
    with pytest.raises(HTTPException) as caught:
        auth.require_portal_admin(captured)
    assert caught.value.status_code == 403
    assert client.get("/portal/users").status_code == 403
    with pytest.raises(HTTPException) as caught:
        portal._save(BODY, captured)
    assert caught.value.status_code == 403
    assert r39.snapshot(engine) == before


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("stored_account", [" NEW@current-authority.invalid ", "New@current-authority.invalid", BODY["account"]])
def test_normalized_target_reuses_single_existing_id(owner, operation, stored_account):
    engine, client = owner
    seed(engine, "actor")
    seed(engine, "legacy-target", account=stored_account, role="admin", status="inactive", systems=("shift",))
    before = all_tables(engine)
    response = client.post("/portal/users", json=BODY) if operation == "create" else client.put("/portal/users/legacy-target", json=BODY)
    assert response.status_code == 200
    assert response.json()["user"] == {"id": "legacy-target", **BODY}
    after = all_tables(engine)
    assert {name: rows for name, rows in after.items() if name not in {"users", "user_system_access", "audit_logs"}} == {
        name: rows for name, rows in before.items() if name not in {"users", "user_system_access", "audit_logs"}
    }
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM users").scalar_one() == 2
        assert connection.execute(sa.text("SELECT id FROM users WHERE lower(trim(account))=:account"), {"account": BODY["account"]}).scalars().all() == ["legacy-target"]
        audit = connection.exec_driver_sql("SELECT action,target FROM audit_logs").one()
        assert tuple(audit) == ("portal_user_created" if operation == "create" else "portal_user_updated", "legacy-target")
        assert connection.exec_driver_sql("SELECT account,role,status FROM users WHERE id='actor'").one() == (ACCOUNT, "admin", "active")
        assert connection.exec_driver_sql("SELECT system_key FROM user_system_access WHERE user_id='legacy-target'").scalars().all() == ["hospital"]


@pytest.mark.parametrize("operation", ["create", "update-first", "update-other"])
@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_targets_refuse_before_any_table_write(owner, operation, reverse):
    engine, client = owner
    seed(engine, "actor")
    targets = [("first-target", BODY["account"], "operator", "active", ("hospital",)),
               ("second-target", " NEW@current-authority.invalid ", "admin", "inactive", ("shift",))]
    for user_id, account, role, status, systems in reversed(targets) if reverse else targets:
        seed(engine, user_id, account=account, role=role, status=status, systems=systems)
    seed(engine, "other-target", account="other@current-authority.invalid", role="operator", systems=())
    before = all_tables(engine)
    if operation == "create":
        response = client.post("/portal/users", json=BODY)
    else:
        target = "first-target" if operation == "update-first" else "other-target"
        response = client.put("/portal/users/" + target, json=BODY)
    assert response.status_code == 409
    assert response.json() == {"detail": "Account matches multiple users"}
    assert all_tables(engine) == before


@pytest.mark.parametrize("stored_account", [" NEW@current-authority.invalid ", "New@current-authority.invalid"])
def test_target_collision_with_another_id_preserves_every_table(owner, stored_account):
    engine, client = owner
    seed(engine, "actor")
    seed(engine, "existing-target", account=stored_account, systems=("shift",))
    seed(engine, "edited-target", account="edited@current-authority.invalid", role="operator", systems=("school-lunch",))
    before = all_tables(engine)
    response = client.put("/portal/users/edited-target", json=BODY)
    assert response.status_code == 409
    assert response.json() == {"detail": "Account already exists"}
    assert all_tables(engine) == before


def test_normalized_target_missing_explicit_id_still_returns_404(owner):
    engine, client = owner
    seed(engine, "actor")
    seed(engine, "legacy-target", account=" NEW@current-authority.invalid ")
    before = all_tables(engine)
    assert client.put("/portal/users/missing-id", json=BODY).status_code == 404
    assert all_tables(engine) == before


def test_r39_cross_admin_retirement_still_serializes(owner):
    engine, _client = owner
    seed(engine, "actor")
    seed(engine, "second", account="second@current-authority.invalid")
    barrier = threading.Barrier(2)

    def demote(account, target, target_account):
        barrier.wait(timeout=5)
        try:
            portal._save(dict(BODY, account=target_account), auth.UserContext("admin", account), target)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(demote, ACCOUNT, "second", "second@current-authority.invalid")
        second = pool.submit(demote, "second@current-authority.invalid", "actor", ACCOUNT)
        assert sorted([first.result(timeout=15), second.result(timeout=15)]) in ([200, 403], [200, 409])
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM users WHERE role='admin'").scalar_one() == 1


@pytest.mark.parametrize("operation", ["create", "update"])
def test_sqlite_inflight_request_cannot_outlive_committed_retirement(owner, operation):
    engine, client = owner
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite writer reservation neighbor; PG NOWAIT tested separately")
    seed(engine, "actor")
    seed(engine, "target", account=BODY["account"], role="operator", systems=())
    attempting = threading.Event()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            attempting.set()

    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as retirement, ThreadPoolExecutor(max_workers=1) as pool:
            retirement.exec_driver_sql("BEGIN IMMEDIATE")
            retirement.exec_driver_sql("UPDATE users SET status='inactive' WHERE id='actor'")
            attempting.clear()
            if operation == "create":
                future = pool.submit(client.post, "/portal/users", json=dict(BODY, account="fresh@current-authority.invalid"))
            else:
                future = pool.submit(client.put, "/portal/users/target", json=BODY)
            assert attempting.wait(3)
            retirement.commit()
            assert future.result(timeout=10).status_code == 403
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM audit_logs").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM users").scalar_one() == 2
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM user_system_access WHERE user_id='target'").scalar_one() == 0
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)


def test_pg_nowait_then_committed_retirement(owner):
    engine, _client = owner
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock semantics only; SQLite serializes BEGIN IMMEDIATE")
    seed(engine, "actor")
    captured = auth.UserContext("admin", ACCOUNT)
    before = r39.snapshot(engine)
    with engine.connect() as retirement:
        retirement.exec_driver_sql("UPDATE users SET status='inactive' WHERE id='actor'")
        with pytest.raises(HTTPException) as caught:
            portal._save(BODY, captured)
        assert caught.value.status_code == 409
        assert r39.snapshot(engine) == before
        retirement.commit()
    after = r39.snapshot(engine)
    with pytest.raises(HTTPException) as caught:
        portal._save(BODY, captured)
    assert caught.value.status_code == 403
    assert r39.snapshot(engine) == after


@pytest.mark.parametrize("invalid", ["inactive", "invalid-role", "disabled-grant"])
def test_invalid_actor_or_disabled_grant_stops(owner, invalid):
    engine, client = owner
    seed(engine, "actor", status="inactive" if invalid == "inactive" else "active", role="viewer" if invalid == "invalid-role" else "admin", systems=("hospital",))
    if invalid == "disabled-grant":
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE user_system_access SET enabled=FALSE WHERE user_id='actor'")
    before = r39.snapshot(engine)
    assert client.get("/admin").status_code == 403
    assert client.get("/operator").status_code == 403
    assert client.get("/portal/auth/me?system=hospital").status_code == 403
    if invalid == "disabled-grant":
        assert client.get("/portal/auth/me").json()["systems"] == []
    else:
        assert client.get("/auth/me").status_code == 403
    assert r39.snapshot(engine) == before


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_portal_mode_uses_fresh_upstream_not_local_authority(monkeypatch, role):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "portal")
    monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "https://authority.invalid/me")
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("Configured Portal authority must not query a local actor")

    monkeypatch.setattr(auth, "_local_current_user", forbidden)
    monkeypatch.setattr(auth, "_google_email_or_none", forbidden)
    payload = {"account": ACCOUNT, "role": role, "systems": ["hospital", "shift"]}

    def transport(request, timeout):
        assert request.full_url == "https://authority.invalid/me?system=hospital"
        assert timeout == 5
        calls.append(1)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(auth.urllib.request, "urlopen", transport)
    app = FastAPI()
    app.include_router(portal.router)
    app.include_router(auth_config.router)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-local-transport-only"
        assert client.get("/auth/me").json()["role"] == role
        assert client.get("/portal/auth/me?system=shift").json() == payload
        payload["role"] = "operator" if role == "admin" else "admin"
        assert client.get("/auth/me").json()["role"] == payload["role"]
    assert len(calls) == 3


def test_portal_projection_missing_systems_is_unavailable_without_local_guess(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "portal")
    monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "https://authority.invalid/me")
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps({"account": ACCOUNT, "role": "admin"}).encode()))
    app = FastAPI()
    app.include_router(portal.router)
    with TestClient(app) as client:
        assert client.get("/portal/auth/me", headers={"Authorization": "Bearer synthetic-local-only"}).status_code == 503


@pytest.fixture
def upstream_client(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "portal")
    monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "https://authority.invalid/me?existing=1")

    def forbidden(*args, **kwargs):
        pytest.fail("Portal mode must not fall back to local identity or authority")

    monkeypatch.setattr(auth, "_local_current_user", forbidden)
    monkeypatch.setattr(auth, "_google_email_or_none", forbidden)
    app = FastAPI()
    for name, dependency in [("user", auth.get_current_user), ("admin", auth.get_current_admin), ("operator", auth.get_current_operator)]:
        def register(name, dependency):
            @app.get("/" + name)
            def endpoint(user=Depends(dependency)):
                return {"role": user.role, "account": user.account}
        register(name, dependency)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-upstream-only"
        yield client


@pytest.mark.parametrize("path", ["/user", "/admin", "/operator"])
@pytest.mark.parametrize("status", [401, 403, 400, 404, 429, 500, 502, 503, 504])
def test_upstream_denial_and_unavailable_remain_distinct(upstream_client, monkeypatch, path, status):
    calls = []

    def transport(request, *, timeout):
        calls.append((request.full_url, timeout))
        assert request.get_header("Authorization") == "Bearer synthetic-upstream-only"
        raise urllib.error.HTTPError(request.full_url, status, "synthetic upstream failure", {}, None)

    monkeypatch.setattr(auth.urllib.request, "urlopen", transport)
    response = upstream_client.get(path)
    assert response.status_code == (status if status in (401, 403) else 503)
    assert calls == [("https://authority.invalid/me?existing=1&system=hospital", 5)]


@pytest.mark.parametrize("path", ["/user", "/admin", "/operator"])
@pytest.mark.parametrize("failure", [urllib.error.URLError("synthetic transport"), TimeoutError("synthetic timeout")])
def test_upstream_transport_errors_never_retire_identity_as_401(upstream_client, monkeypatch, path, failure):
    def transport(*args, **kwargs):
        raise failure

    monkeypatch.setattr(auth.urllib.request, "urlopen", transport)
    assert upstream_client.get(path).status_code == 503


@pytest.mark.parametrize("body", [b"not json", b"null", b"[]", b'"text"', b"1"])
def test_upstream_invalid_json_shape_stops_without_local_guess(upstream_client, monkeypatch, body):
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body))
    assert upstream_client.get("/user").status_code == 503
