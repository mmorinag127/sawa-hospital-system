"""C1 contract tests: real ORM/auth decisions, isolated SQLite and opt-in local PG."""

import importlib.util
import io
import os
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import urllib.error
from urllib.parse import urlsplit
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from src import db
from src.api import auth, menu_masters, portal
from src.models.menu import MenuMaster, MenuFacilityOverride, MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry
from src.models.user import User, AuditLog
from src.services import menu_service as service


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts/apply_menu_master_revision_migration.py"
ENDPOINTS = [("GET", "/menu-masters"), ("GET", "/menu-masters/existing"),
             ("POST", "/menu-masters"), ("PUT", "/menu-masters/existing")]


def apply_migration(connection, filename):
    spec = importlib.util.spec_from_file_location("c1_migration", ROOT / "backend/migrations" / filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    if request.param == "sqlite":
        engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "menu.sqlite3")))
        yield engine
        engine.dispose()
        return
    raw_uri = os.getenv("C1_POSTGRES_URI")
    if not raw_uri:
        pytest.skip("C1_POSTGRES_URI required for dedicated local PostgreSQL proof")
    url = sa.make_url(raw_uri)
    assert url.drivername == "postgresql+psycopg2"
    assert url.database == "c1_menu_master" and url.host is None
    assert Path(url.query["host"]).resolve() == ROOT / "tmp/pgs"
    assert url.query["port"] == "55437"
    assert not url.query.get("options"), "test owns its schema/search_path"
    owner = sa.create_engine(url)
    schema = "c1_" + uuid4().hex
    with owner.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    engine = sa.create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        yield engine
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        owner.dispose()


@pytest.fixture
def store(database, monkeypatch):
    metadata = sa.MetaData()
    for model in (MenuMaster, MenuFacilityOverride, MonthlyMenu, MonthlyMenuItem,
                  MonthlyMenuEntry, User, AuditLog):
        model.__table__.to_metadata(metadata)
    # The migrated monthly table uses the 0023 expression index, not create_all's old constraint.
    monthly = metadata.tables["monthly_menu_items"]
    monthly.constraints.difference_update(
        {constraint for constraint in monthly.constraints if constraint.name == "uq_monthly_menu_item_scope"}
    )
    metadata.create_all(database)
    with database.begin() as connection:
        apply_migration(connection, "0023_monthly_menu_item_identity_indexes.py")
        apply_migration(connection, "0026_user_system_access.py")
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(database, autoflush=False))
    monkeypatch.setattr(service, "engine", database)
    monkeypatch.setattr(service, "_MENU_SCHEMA_INITIALIZED", False)
    return database


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth, "GOOGLE_OAUTH_CLIENT_IDS", ["c1-client"])
    for prefix in ("AUTOMATION_AUTH", "SCHOOL_LUNCH_AUTOMATION_AUTH"):
        for suffix in ("EMAIL", "AUDIENCE", "SUBJECT"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)

    def verify_token(token, transport, audience):
        if token == "invalid":
            raise ValueError("test token rejected")
        return {"email": token + "@c1.invalid", "email_verified": token != "unverified", "aud": "c1-client"}

    def no_network(*args, **kwargs):
        raise AssertionError("external HTTP is forbidden in C1 tests")

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", verify_token)
    monkeypatch.setattr(auth.urllib.request, "urlopen", no_network)
    with db.session_scope() as session:
        for account, role, status in (
            ("operator", "operator", "active"), ("admin", "admin", "active"),
            ("inactive", "operator", "inactive"), ("viewer", "viewer", "active"),
            ("noaccess", "operator", "active"), ("disabled", "operator", "active"),
            ("shift", "operator", "active"),
        ):
            session.add(User(id=account, account=account + "@c1.invalid", role=role, status=status))
        session.flush()
        for account in ("operator", "admin", "inactive", "viewer", "disabled", "shift"):
            session.execute(sa.text(
                "INSERT INTO user_system_access(user_id,system_key,enabled) VALUES(:id,:system,:enabled)"
            ), {"id": account, "system": "shift" if account == "shift" else "hospital",
                "enabled": account != "disabled"})
        session.add(MenuMaster(id="existing", name="Existing", normalized_name="existing", qty_per_serving=1))
    auth.invalidate_user_cache()
    app = FastAPI()
    app.include_router(menu_masters.router)
    with TestClient(app, headers={"Authorization": "Bearer operator"}) as api:
        yield api
    auth.invalidate_user_cache()


def invoke(client, method, path, headers=None):
    return client.request(method, path, headers=headers, json={"name": "Added", "revision": 1,
                                                             "qty_per_serving": 7})


@pytest.mark.parametrize("unit", ["g", "cut", "count"])
def test_all_fields_normalization_saved_item_and_readonly_inputs(client, unit):
    payload = {"name": "  Test Fish  ", "unit_type": unit, "qty_per_serving": "0",
               "bag_max_qty": 8, "bag_max_unit": "count", "temp_type": "hot",
               "daypart": "lunch", "category": "Free category", "condiments": ["Sauce"],
               "id": "injected", "normalized_name": "injected", "revision": 90}
    response = client.post("/menu-masters", json=payload)
    assert response.status_code == 200, response.text
    created = response.json()["item"]
    assert created["id"] != "injected" and created["revision"] == 1
    expected = {key: value for key, value in payload.items() if key not in {"id", "normalized_name", "revision"}}
    expected.update(name="Test Fish", qty_per_serving=0, daypart="昼食")
    assert {key: created[key] for key in expected} == expected
    assert created["normalized_name"] == "testfish"
    patch = {**expected, "name": "  Chicken  ", "revision": 1, "unit_type": "count",
             "qty_per_serving": 3, "bag_max_qty": 0, "bag_max_unit": "cut", "temp_type": "cold",
             "daypart": "dinner", "category": "Changed", "condiments": ["  Salt  ", ""],
             "id": "injected", "normalized_name": "injected"}
    updated = client.put(f"/menu-masters/{created['id']}", json=patch)
    assert updated.status_code == 200, updated.text
    saved = updated.json()["item"]
    assert updated.json()["updated"] is True and saved["revision"] == 2
    expected.update(name="Chicken", unit_type="count", qty_per_serving=3, bag_max_qty=0,
                    bag_max_unit="cut", temp_type="cold", daypart="夕食", category="Changed", condiments=["Salt"])
    assert {key: saved[key] for key in expected} == expected
    assert saved["id"] == created["id"] and saved["normalized_name"] == "chicken"
    assert client.get(f"/menu-masters/{created['id']}").json() == {"item": saved}


def test_null_zero_duplicate_and_stale_no_write(client):
    created = client.post("/menu-masters", json={"name": "Fish"}).json()["item"]
    path = f"/menu-masters/{created['id']}"
    for key in ("unit_type", "qty_per_serving", "bag_max_qty", "bag_max_unit", "temp_type", "daypart", "category"):
        assert created[key] is None
    assert created["condiments"] == []
    assert client.post("/menu-masters", json={"name": " F I S H ", "qty_per_serving": 90}).json()["item"] == created
    saved = client.put(path, json={"revision": 1, "qty_per_serving": 0, "bag_max_qty": 0}).json()["item"]
    assert saved["qty_per_serving"] == saved["bag_max_qty"] == 0 and saved["revision"] == 2
    assert client.put(path, json={"revision": 1, "name": "Old edit"}).status_code == 409
    assert client.get(path).json()["item"] == saved
    cleared = client.put(path, json={"revision": 2, "qty_per_serving": None,
                                    "bag_max_qty": None, "condiments": None}).json()["item"]
    assert cleared["qty_per_serving"] is cleared["bag_max_qty"] is None
    assert cleared["condiments"] == [] and cleared["revision"] == 3
    assert client.put(path, json={"revision": 3, "name": "Existing"}).status_code == 400
    assert client.get(path).json()["item"] == cleared


@pytest.mark.parametrize("revision", ["missing", None, True, False, 0, -1, 1.0, "1", {}, []])
def test_revision_strict_validation_no_write(client, revision):
    original = client.get("/menu-masters/existing").json()
    body = {"name": "Forbidden overwrite"}
    if revision != "missing":
        body["revision"] = revision
    assert client.put("/menu-masters/existing", json=body).status_code == 422
    assert client.get("/menu-masters/existing").json() == original


def test_not_found_blank_and_legacy_values(client):
    assert client.get("/menu-masters/unknown").status_code == 404
    assert client.put("/menu-masters/unknown", json={"revision": 1, "name": "Missing"}).status_code == 404
    assert client.post("/menu-masters", json={"name": "  "}).status_code == 400
    # These are business strings, not new API enums. Existing coercion remains authoritative.
    assert client.post("/menu-masters", json={"name": 123, "daypart": "custom",
                                             "category": "custom", "unit_type": "custom"}).status_code == 200


def test_list_paging_search_stable_ties_and_clamp(client):
    with db.session_scope() as session:
        session.add_all([MenuMaster(id="b", name="Same Fish", normalized_name="fish-b"),
                         MenuMaster(id="a", name="Same Fish", normalized_name="fish-a"),
                         MenuMaster(id="z", name="Zeta", normalized_name="zeta")])
    page = client.get("/menu-masters", params={"q": " FISH ", "limit": 1, "offset": 1}).json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 1, 1)
    assert [item["id"] for item in page["items"]] == ["b"]
    assert [item["id"] for item in client.get("/menu-masters", params={"order": "desc"}).json()["items"]] == ["z", "a", "b", "existing"]
    assert client.get("/menu-masters", params={"offset": 99}).json() == {"items": [], "total": 4, "offset": 99, "limit": 1000}
    for supplied, expected in [(-1, 1), (0, 1000), (10000, 5000)]:
        response = client.get("/menu-masters", params={"limit": supplied})
        assert response.status_code == 200 and response.json()["limit"] == expected
    for params in ({"sort": "name; DROP TABLE menu_masters"}, {"sort": "condiments"},
                   {"order": "ASC"}, {"offset": -1}):
        assert client.get("/menu-masters", params=params).status_code == 422
    for field in service._MENU_MASTER_SORT_COLUMNS:
        assert client.get("/menu-masters", params={"sort": field}).status_code == 200


@pytest.mark.parametrize("monthly", [False, True])
def test_two_sessions_conflict_rolls_back_whole_transaction(store, monthly):
    first = service.create_menu_master({"name": "Fish", "qty_per_serving": 1})
    with Session(store) as winner, db.session_scope() as observer:
        winning = winner.get(MenuMaster, first["id"])
        # Use the real shared transaction context so StaleDataError triggers rollback.
        with pytest.raises(StaleDataError):
            with db.session_scope() as loser:
                stale = loser.get(MenuMaster, first["id"])
                assert winning.revision == stale.revision == 1
                service._update_menu_master_in_session(winner, winning, {"qty_per_serving": 2})
                winner.commit()
                loser.add(MonthlyMenu(id="rollback-proof", filename="must not commit"))
                if monthly:
                    item = MonthlyMenuItem(name="Fish", menu_master_id=first["id"])
                    service._upsert_menu_master(loser, item, {"qty_per_serving": 9}, master=stale)
                else:
                    service._update_menu_master_in_session(loser, stale, {"qty_per_serving": 9})
        fresh = observer.get(MenuMaster, first["id"])
        assert (fresh.revision, fresh.qty_per_serving) == (2, 2)
        assert observer.get(MonthlyMenu, "rollback-proof") is None


def test_api_actual_flush_race_is_409_and_rolls_back(client, store, monkeypatch):
    original = service._update_menu_master_in_session

    def concurrent_update(session, master, body):
        original(session, master, body)
        with Session(store) as competitor:
            newer = competitor.get(MenuMaster, master.id)
            newer.qty_per_serving = 8
            competitor.commit()

    monkeypatch.setattr(service, "_update_menu_master_in_session", concurrent_update)
    response = client.put("/menu-masters/existing", json={"revision": 1, "qty_per_serving": 9})
    assert response.status_code == 409, response.text
    saved = client.get("/menu-masters/existing").json()["item"]
    assert (saved["revision"], saved["qty_per_serving"]) == (2, 8)


@pytest.mark.parametrize("provider", ["local", "portal"])
@pytest.mark.parametrize("token,expected", [("operator", 200), ("admin", 200), ("invalid", 401),
    ("unverified", 403), ("unknown", 403), ("inactive", 403), ("viewer", 403),
    ("noaccess", 403), ("disabled", 403), ("shift", 403), ("basic", 401), ("absent", 401)])
@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_current_auth_and_hospital_access(client, monkeypatch, provider, token, expected, method, path):
    calls = []
    if provider == "portal":
        portal_app = FastAPI()
        portal_app.include_router(portal.router)
        portal_client = TestClient(portal_app)

        def in_process_portal(request, timeout):
            url = urlsplit(request.full_url)
            assert url.netloc == "portal.c1.invalid" and url.query == "system=hospital"
            calls.append(url.query)
            # Only transport is replaced: the real Portal route and DB grants decide access.
            auth.AUTH_PROVIDER = "local"
            try:
                response = portal_client.get(url.path + "?" + url.query,
                                             headers={"Authorization": request.get_header("Authorization")})
            finally:
                auth.AUTH_PROVIDER = "portal"
            if response.status_code != 200:
                raise urllib.error.HTTPError(request.full_url, response.status_code, "rejected", {}, None)
            return io.BytesIO(response.content)

        monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "http://portal.c1.invalid/portal/auth/me")
        monkeypatch.setattr(auth.urllib.request, "urlopen", in_process_portal)
    monkeypatch.setattr(auth, "AUTH_PROVIDER", provider)
    header = "Basic b3BlcmF0b3I6c2VjcmV0" if token == "basic" else "" if token == "absent" else "Bearer " + token
    before = service.get_menu_master("existing")
    response = invoke(client, method, path, {"Authorization": header})
    assert response.status_code == expected, response.text
    if expected != 200:
        assert service.get_menu_master("existing") == before
        assert service.list_menu_masters_page()["total"] == 1
    if provider == "portal":
        assert bool(calls) == (token not in {"basic", "absent"})
        portal_client.close()


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_missing_revision_is_operator_visible_and_never_repaired(client, store, monkeypatch, method, path):
    with store.begin() as connection:
        connection.execute(sa.text("ALTER TABLE menu_masters DROP COLUMN revision"))
    monkeypatch.setattr(service, "_MENU_SCHEMA_INITIALIZED", False)
    statements = []
    sa.event.listen(store, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql))
    response = invoke(client, method, path)
    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["code"] == "menu_schema_not_migrated"
    assert "menu_masters.revision" in response.json()["detail"]["message"]
    with pytest.raises(service.MenuSchemaNotMigrated, match="menu_masters.revision"):
        service.ensure_menu_schema()  # Same check used by main's startup hook.
    assert not any(sql.lstrip().upper().startswith(("ALTER", "CREATE", "DROP", "UPDATE", "INSERT")) for sql in statements)
    with store.connect() as connection:
        assert connection.execute(sa.text("SELECT qty_per_serving FROM menu_masters WHERE id='existing'")).scalar_one() == 1


@pytest.mark.parametrize("database", ["postgresql"], indirect=True)
def test_pg_missing_0023_index_stops_reads_and_writes(client, store, monkeypatch):
    with store.begin() as connection:
        connection.execute(sa.text("DROP INDEX uq_monthly_menu_items_scope_identity"))
    monkeypatch.setattr(service, "_MENU_SCHEMA_INITIALIZED", False)
    for method, path in ENDPOINTS:
        response = invoke(client, method, path)
        assert response.status_code == 503
        assert "migration 0023" in response.json()["detail"]["message"]
    with pytest.raises(service.MenuSchemaNotMigrated, match="migration 0023"):
        service.list_menu_masters()
    with pytest.raises(service.MenuSchemaNotMigrated, match="migration 0023"):
        service.update_menu_master("existing", {"qty_per_serving": 99})
    assert "uq_monthly_menu_items_scope_identity" not in {index["name"] for index in sa.inspect(store).get_indexes("monthly_menu_items")}
    with store.connect() as connection:
        assert connection.execute(sa.text("SELECT qty_per_serving FROM menu_masters WHERE id='existing'")).scalar_one() == 1


def legacy_table(database, revision=None):
    metadata = sa.MetaData()
    columns = [sa.Column(column.name, column.type, primary_key=column.primary_key, nullable=column.nullable)
               for column in MenuMaster.__table__.columns if column.name != "revision"]
    if revision is not None:
        columns.append(revision)
    table = sa.Table("menu_masters", metadata, *columns)
    metadata.create_all(database)
    values = [
        {"id": "old-" + unit, "name": " Old " + unit, "normalized_name": "legacy-" + unit,
         "unit_type": unit, "qty_per_serving": 0 if unit == "count" else None,
         "bag_max_qty": 12, "bag_max_unit": unit, "temp_type": "hot", "daypart": "lunch",
         "category": "Legacy", "condiments": [" Sauce "], "updated_at": datetime(2020, 1, 2, 3, 4, 5)}
        for unit in ("g", "cut", "count")
    ]
    if revision is not None and revision.server_default is None:
        for value in values:
            value["revision"] = 1
    with database.begin() as connection:
        connection.execute(table.insert(), values)
    return table


def run_helper(database):
    return subprocess.run([sys.executable, str(HELPER), "--db-uri-stdin"],
                          input=database.url.render_as_string(hide_password=False),
                          capture_output=True, text=True, check=False)


def test_explicit_migration_first_second_run_preserves_data(database):
    table = legacy_table(database)
    with database.connect() as connection:
        before = connection.execute(sa.select(table).order_by(table.c.id)).mappings().all()
    for attempt in (1, 2):
        result = run_helper(database)
        assert result.returncode == 0, result.stderr
        with database.connect() as connection:
            assert connection.execute(sa.select(table).order_by(table.c.id)).mappings().all() == before
            assert list(connection.execute(sa.text("SELECT revision FROM menu_masters")).scalars()) == [1, 1, 1]
        column = next(column for column in sa.inspect(database).get_columns("menu_masters") if column["name"] == "revision")
        assert column["nullable"] is False and isinstance(column["type"], sa.Integer)
    with database.begin() as connection:
        connection.execute(sa.text("UPDATE menu_masters SET revision=2 WHERE id='old-g'"))
    assert run_helper(database).returncode == 0
    with database.connect() as connection:
        assert connection.execute(sa.text("SELECT revision FROM menu_masters WHERE id='old-g'")).scalar_one() == 2
    statements = []
    sa.event.listen(database, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql))
    with database.begin() as connection:
        apply_migration(connection, "0027_menu_master_revision.py")
    assert not any(sql.lstrip().upper().startswith(("ALTER", "CREATE", "DROP", "UPDATE", "INSERT")) for sql in statements)


@pytest.mark.parametrize("kind", ["text", "bigint", "nullable", "default2", "no-default"])
def test_explicit_migration_wrong_schema_stops_without_repair(database, kind):
    column = sa.Column("revision", sa.String() if kind == "text" else sa.BigInteger() if kind == "bigint" else sa.Integer(),
                       nullable=kind == "nullable",
                       server_default=None if kind == "no-default" else "2" if kind == "default2" else "1")
    table = legacy_table(database, column)
    with database.connect() as connection:
        before = connection.execute(sa.select(table).order_by(table.c.id)).all()
    result = run_helper(database)
    assert result.returncode == 1 and "0027 blocked" in result.stderr
    assert "Traceback" not in result.stderr
    with database.connect() as connection:
        assert connection.execute(sa.select(table).order_by(table.c.id)).all() == before


def test_explicit_migration_missing_table_and_invalid_revision_stop(database):
    result = run_helper(database)
    assert result.returncode == 1 and "menu_masters is missing" in result.stderr
    assert "menu_masters" not in sa.inspect(database).get_table_names()
    legacy_table(database)
    assert run_helper(database).returncode == 0
    with database.begin() as connection:
        connection.execute(sa.text("UPDATE menu_masters SET revision=0 WHERE id='old-g'"))
    result = run_helper(database)
    assert result.returncode == 1 and "non-positive" in result.stderr
    with database.connect() as connection:
        assert connection.execute(sa.text("SELECT revision FROM menu_masters WHERE id='old-g'")).scalar_one() == 0


@pytest.mark.parametrize("failure", ["parser", "driver"])
def test_helper_never_logs_secret_uri(failure):
    secret = "C1-SECRET-SENTINEL"
    uri = (f"not-a-dialect://user:{secret}@invalid/db" if failure == "parser" else
           f"postgresql+psycopg2://user:{secret}@/c1_menu_master?host={ROOT}/tmp/no-socket&port=55437")
    result = subprocess.run([sys.executable, str(HELPER), "--db-uri-stdin"],
                            input=uri, text=True, capture_output=True)
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr and "Traceback" not in result.stderr
    assert secret not in " ".join(result.args)
