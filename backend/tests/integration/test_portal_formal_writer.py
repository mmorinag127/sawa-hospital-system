"""Real formal schema, authorization decisions and transaction teardown."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from alembic import command

from test_formal_alembic_fresh_chain import formal_database
from src import db
from src.api import auth, portal
from src.maintenance.formal_migration_schema import read_catalog, require_formal_head

REQUIRED = {
    "orders": ("received_at",),
    "order_documents": ("received_at", "ocr_attempts", "status"),
    "ocr_jobs": ("status", "created_at", "updated_at"),
    "label_rows": ("created_at",),
    "delivery_notes": ("created_at",),
    "manufacturing_aggregate_rows": ("created_at",),
    "users": ("status", "created_at"),
    "audit_logs": ("created_at",),
    "notifications": ("sent_at",),
}


@pytest.fixture(autouse=True)
def explicit_formal_target(monkeypatch):
    for key in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(key, raising=False)


def snapshot(engine):
    with engine.connect() as connection:
        rows = {}
        for table in sa.inspect(connection).get_table_names():
            name = connection.dialect.identifier_preparer.quote(table)
            rows[table] = sorted(repr(tuple(row)) for row in connection.exec_driver_sql(f"SELECT * FROM {name}"))
        return json.dumps(read_catalog(connection), sort_keys=True, default=str), rows


@pytest.fixture
def formal_client(formal_database, monkeypatch):
    engine, config = formal_database
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users(id, account, role, status, created_at) VALUES (:id, :account, :role, 'active', '2020-01-02 03:04:05')"), [
            {"id": "existing-admin", "account": "admin@formal.invalid", "role": "admin"},
            {"id": "existing-operator", "account": "operator@formal.invalid", "role": "operator"},
        ])
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True))
    monkeypatch.setattr(portal, "session_scope", db.session_scope)
    monkeypatch.setattr(auth, "session_scope", db.session_scope)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "google")
    accounts = {"Bearer admin-proof": "admin@formal.invalid", "Bearer operator-proof": "operator@formal.invalid"}
    monkeypatch.setattr(auth, "_google_email_or_none", lambda request: accounts.get(request.headers.get("authorization")))
    app = FastAPI()
    app.include_router(portal.router)

    @app.on_event("startup")
    def readonly_startup():
        with engine.connect() as connection:
            require_formal_head(connection)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield engine, client


def test_fourteen_nullable_attributes_match_formal_schema(formal_database):
    engine, config = formal_database
    command.upgrade(config, "head")
    with engine.connect() as connection:
        require_formal_head(connection)
        inspector = sa.inspect(connection)
        count = 0
        for table, names in REQUIRED.items():
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            for name in names:
                model = db.Base.metadata.tables[table].c[name]
                assert columns[name]["nullable"] is False
                assert model.nullable is False
                assert model.default is not None
                count += 1
        assert count == 14


def test_portal_create_update_created_at_and_authorization(formal_client):
    engine, client = formal_client
    before = snapshot(engine)
    body = {"account": "new@formal.invalid", "role": "operator", "status": "active", "systems": ["hospital"]}
    assert client.post("/portal/users", json=body).status_code == 401
    assert client.post("/portal/users", json=body, headers={"Authorization": "Bearer operator-proof"}).status_code == 403
    assert snapshot(engine) == before
    admin = {"Authorization": "Bearer admin-proof"}
    created = client.post("/portal/users", json=body, headers=admin)
    assert created.status_code == 200, created.text
    user_id = created.json()["user"]["id"]
    with engine.connect() as connection:
        timestamp = connection.execute(sa.text("SELECT created_at FROM users WHERE id=:id"), {"id": user_id}).scalar_one()
        assert timestamp is not None
        existing = connection.execute(sa.text("SELECT created_at FROM users WHERE id='existing-admin'")).scalar_one()
    body.update(role="admin", status="inactive", systems=["school-lunch", "shift"])
    response = client.put(f"/portal/users/{user_id}", json=body, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["user"] == {"id": user_id, **body}
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT created_at FROM users WHERE id=:id"), {"id": user_id}).scalar_one() == timestamp
        assert connection.execute(sa.text("SELECT created_at FROM users WHERE id='existing-admin'")).scalar_one() == existing
        assert connection.execute(sa.text("SELECT system_key FROM user_system_access WHERE user_id=:id ORDER BY system_key"), {"id": user_id}).scalars().all() == ["school-lunch", "shift"]


@pytest.mark.parametrize("kind", ["missing", "conflict", "not_found"])
def test_portal_invalid_and_conflicting_commands_preserve_every_row(formal_client, kind):
    engine, client = formal_client
    before = snapshot(engine)
    headers = {"Authorization": "Bearer admin-proof"}
    body = {"account": "admin@formal.invalid", "role": "operator", "status": "active", "systems": ["hospital"]}
    if kind == "missing":
        body.pop("account")
        response = client.post("/portal/users", json=body, headers=headers)
        assert response.status_code == 400
    elif kind == "conflict":
        response = client.put("/portal/users/existing-operator", json=body, headers=headers)
        assert response.status_code == 409
        assert response.json()["detail"] == "Account already exists"
    else:
        response = client.put("/portal/users/unknown-id", json=body, headers=headers)
        assert response.status_code == 404
    assert snapshot(engine) == before


@pytest.mark.parametrize("operation", ["create", "update"])
def test_portal_child_failure_uses_actual_session_teardown(formal_client, operation):
    engine, client = formal_client
    before = snapshot(engine)
    reached = []

    def fault(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO user_system_access"):
            reached.append(statement)
            raise RuntimeError("injected child write failure after parent write")

    sa.event.listen(engine, "before_cursor_execute", fault)
    try:
        body = {"account": "rollback@formal.invalid", "role": "operator", "status": "active", "systems": ["hospital"]}
        method = client.post if operation == "create" else client.put
        path = "/portal/users" if operation == "create" else "/portal/users/existing-operator"
        response = method(path, json=body, headers={"Authorization": "Bearer admin-proof"})
        assert response.status_code == 500
        assert len(reached) == 1
    finally:
        sa.event.remove(engine, "before_cursor_execute", fault)
    assert snapshot(engine) == before
