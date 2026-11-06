from __future__ import annotations

from pathlib import Path
import ast
import importlib
import os
import shutil
import sqlite3
import uuid

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from src.models.facility_template_version import FacilityTemplateVersion


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def formal_startup_boundary(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    import src.main as main

    uri = f"sqlite:///{tmp_path / 'formal-startup.sqlite3'}"
    engine = create_engine(uri)
    try:
        with monkeypatch.context() as target:
            for name in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
                target.delenv(name, raising=False)
            target.setenv("ALEMBIC_DB_URI", uri)
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        monkeypatch.setattr(main.db, "engine", engine)
        yield engine
    finally:
        engine.dispose()


def _sqlite_schema_snapshot(engine) -> dict[str, tuple[str, ...]]:
    inspector = inspect(engine)
    return {
        table: tuple(sorted(column["name"] for column in inspector.get_columns(table)))
        for table in sorted(inspector.get_table_names())
    }


def _capture_sql(engine):
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", capture)
    return statements, capture


def _ddl(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))]


def test_services_do_not_run_schema_repairs_at_import_or_read_time() -> None:
    forbidden = (
        "Base.metadata.create_all",
        "ALTER TABLE",
        "__table__.create",
        "CREATE INDEX",
        "DROP CONSTRAINT",
    )
    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "services").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "order_cleanup_manifest_v1.py":
            tree = ast.parse(text)
            create = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create")
            statement = "manifest.execute('CREATE INDEX rows_owner ON rows(table_name,owner_id,key_json)')"
            calls = [node for node in ast.walk(create) if isinstance(node, ast.Call)
                     and ast.get_source_segment(text, node) == statement]
            assert len(calls) == 1
            assert "closing(sqlite3.connect(path)) as manifest" in ast.get_source_segment(text, create)
            text = text.replace(statement, "", 1)
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == []


def test_cleanup_manifest_ddl_is_private_sqlite_not_application_database(tmp_path, monkeypatch):
    from src.services import order_cleanup_manifest_v1 as service
    owned = tmp_path / "manifest"
    owned.mkdir(mode=0o700)
    monkeypatch.setattr(service.tempfile, "mkdtemp", lambda *, prefix: str(owned))
    class NoApplicationWrites:
        def execute(self, *args, **kwargs):
            pytest.fail("empty cleanup manifest must not touch application database")
    try:
        result = service.create(NoApplicationWrites(), {"owned": True}, [])
        assert Path(result["path"]) == owned / "plan.sqlite"
        assert Path(result["path"]).stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(result["path"]) as connection:
            assert connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='rows_owner'").fetchall() == [("rows_owner",)]
        assert result["sha256"] == service.digest_file(result["path"])
    finally:
        shutil.rmtree(owned)


def test_runtime_modules_do_not_contain_ddl_statements() -> None:
    runtime_paths = (
        ROOT / "src" / "services" / "base_menu_service.py",
        ROOT / "src" / "services" / "facility_template_version_service.py",
        ROOT / "src" / "services" / "portal_access_bootstrap_service.py",
        ROOT / "src" / "main.py",
    )
    forbidden = ("CREATE TABLE", "ALTER TABLE", "CREATE INDEX")
    offenders = [
        f"{path.relative_to(ROOT)}: {token}"
        for path in runtime_paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_base_menu_missing_schema_blocks_without_runtime_repair(monkeypatch) -> None:
    from src.services import base_menu_service

    class MissingSchemaInspector:
        def get_table_names(self):
            return []

    monkeypatch.setattr(base_menu_service, "inspect", lambda _engine: MissingSchemaInspector())
    with pytest.raises(base_menu_service.BaseMenuSchemaNotMigrated, match="migration 0015"):
        base_menu_service.list_items()


def test_base_menu_owned_sqlite_missing_schema_is_unchanged_without_ddl(monkeypatch) -> None:
    from src.services import base_menu_service

    engine = create_engine("sqlite://")
    monkeypatch.setattr(base_menu_service, "get_engine", lambda: engine)
    before = _sqlite_schema_snapshot(engine)
    statements, capture = _capture_sql(engine)
    try:
        with pytest.raises(base_menu_service.BaseMenuSchemaNotMigrated, match="migration 0015"):
            base_menu_service.list_items()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert _sqlite_schema_snapshot(engine) == before
    assert _ddl(statements) == []


def test_base_menu_partial_schema_blocks_without_runtime_repair(monkeypatch) -> None:
    from src.services import base_menu_service

    class PartialSchemaInspector:
        def get_table_names(self):
            return ["base_menu_cycle_items"]

        def get_columns(self, _table):
            return [{"name": "id"}, {"name": "cycle_day"}]

    monkeypatch.setattr(base_menu_service, "inspect", lambda _engine: PartialSchemaInspector())
    with pytest.raises(base_menu_service.BaseMenuSchemaNotMigrated, match="base_menu_cycle_items.name"):
        base_menu_service.list_items()


def test_base_menu_owned_sqlite_partial_schema_is_unchanged_without_ddl(monkeypatch) -> None:
    from src.services import base_menu_service

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE base_menu_cycle_items (id VARCHAR PRIMARY KEY, cycle_day INTEGER NOT NULL)"))
    monkeypatch.setattr(base_menu_service, "get_engine", lambda: engine)
    before = _sqlite_schema_snapshot(engine)
    statements, capture = _capture_sql(engine)
    try:
        with pytest.raises(base_menu_service.BaseMenuSchemaNotMigrated, match="base_menu_cycle_items.name"):
            base_menu_service.list_items()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert _sqlite_schema_snapshot(engine) == before
    assert _ddl(statements) == []


def test_base_menu_invalid_update_keeps_pre_schema_not_found_result(monkeypatch) -> None:
    from src.services import base_menu_service

    monkeypatch.setattr(base_menu_service, "_require_base_menu_schema", lambda: (_ for _ in ()).throw(AssertionError()))
    assert base_menu_service.update_item("", {}, None) is None


def test_facility_template_version_partial_schema_blocks_without_runtime_repair(monkeypatch) -> None:
    from src.services import facility_template_version_service

    class PartialSchemaInspector:
        def get_table_names(self):
            return ["facility_template_versions"]

        def get_columns(self, _table):
            return [{"name": "id"}, {"name": "facility_id"}]

    monkeypatch.setattr(facility_template_version_service, "inspect", lambda _engine: PartialSchemaInspector())
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    with pytest.raises(RuntimeError, match="migrations through 0025"):
        facility_template_version_service.ensure_facility_template_version_schema()
    assert facility_template_version_service._FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED is False


def test_facility_template_version_owned_sqlite_missing_schema_is_unchanged_without_ddl(monkeypatch) -> None:
    from src.services import facility_template_version_service

    engine = create_engine("sqlite://")
    monkeypatch.setattr(facility_template_version_service, "get_engine", lambda: engine)
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    before = _sqlite_schema_snapshot(engine)
    statements, capture = _capture_sql(engine)
    try:
        with pytest.raises(RuntimeError, match="migrations through 0025"):
            facility_template_version_service.ensure_facility_template_version_schema()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert facility_template_version_service._FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED is False
    assert _sqlite_schema_snapshot(engine) == before
    assert _ddl(statements) == []


def test_facility_template_version_missing_orm_column_blocks_without_runtime_repair(monkeypatch) -> None:
    from src.services import facility_template_version_service

    required = {column.name for column in FacilityTemplateVersion.__table__.columns}

    class PartialSchemaInspector:
        def get_table_names(self):
            return ["facility_template_versions"]

        def get_columns(self, _table):
            return [{"name": name} for name in sorted(required - {"template_digest"})]

    monkeypatch.setattr(facility_template_version_service, "inspect", lambda _engine: PartialSchemaInspector())
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    with pytest.raises(RuntimeError, match="facility_template_versions.template_digest"):
        facility_template_version_service.ensure_facility_template_version_schema()


def test_facility_template_version_owned_sqlite_partial_schema_is_unchanged_without_ddl(monkeypatch) -> None:
    from src.services import facility_template_version_service

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE facility_template_versions (id VARCHAR PRIMARY KEY, facility_id VARCHAR NOT NULL)"))
    monkeypatch.setattr(facility_template_version_service, "get_engine", lambda: engine)
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    before = _sqlite_schema_snapshot(engine)
    statements, capture = _capture_sql(engine)
    try:
        with pytest.raises(RuntimeError, match="facility_template_versions.template_digest"):
            facility_template_version_service.ensure_facility_template_version_schema()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert facility_template_version_service._FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED is False
    assert _sqlite_schema_snapshot(engine) == before
    assert _ddl(statements) == []


def test_facility_template_version_migrated_schema_validates_twice_without_ddl(monkeypatch) -> None:
    from src.services import facility_template_version_service

    engine = create_engine("sqlite://")
    FacilityTemplateVersion.__table__.create(engine)
    monkeypatch.setattr(facility_template_version_service, "get_engine", lambda: engine)
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    statements, capture = _capture_sql(engine)
    try:
        facility_template_version_service.ensure_facility_template_version_schema()
        monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
        facility_template_version_service.ensure_facility_template_version_schema()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert _ddl(statements) == []


def test_runtime_import_and_base_menu_read_emit_no_ddl() -> None:
    from src.services import base_menu_service, facility_template_version_service

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(str(statement))

    event.listen(base_menu_service.get_engine(), "before_cursor_execute", capture)
    try:
        importlib.reload(base_menu_service)
        importlib.reload(facility_template_version_service)
        base_menu_service.list_items()
    finally:
        event.remove(base_menu_service.get_engine(), "before_cursor_execute", capture)

    assert _ddl(statements) == []
    assert len(statements) >= 2


def test_startup_calls_facility_schema_validator_without_ddl(monkeypatch, formal_startup_boundary) -> None:
    import src.main as main

    calls: list[str] = []
    inspections = []
    def checked(name, inspection):
        assert inspection.engine is formal_startup_boundary
        inspections.append(inspection)
        calls.append(name)
    monkeypatch.setattr(main.menu_service, "ensure_menu_schema", lambda *, inspection: checked("menu", inspection))
    monkeypatch.setattr(main.facility_template_version_service, "ensure_facility_template_version_schema", lambda *, inspection: checked("facility", inspection))
    monkeypatch.setattr(main.facility_service, "sync_facility_names_from_master", lambda: calls.append("facility-sync"))
    main._initialize_menu_schema()
    assert calls == ["menu", "facility", "facility-sync"]
    assert inspections[0] is inspections[1]


@pytest.mark.parametrize("partial", [False, True])
def test_startup_blocks_missing_or_partial_facility_schema_without_ddl(monkeypatch, partial, formal_startup_boundary) -> None:
    import src.main as main
    from src.services import facility_template_version_service

    engine = create_engine("sqlite://")
    if partial:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE facility_template_versions (id VARCHAR PRIMARY KEY, facility_id VARCHAR NOT NULL)"))
    monkeypatch.setattr(facility_template_version_service, "get_engine", lambda: engine)
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    monkeypatch.setattr(main.menu_service, "ensure_menu_schema", lambda *, inspection: None)
    monkeypatch.setattr(main.facility_service, "sync_facility_names_from_master", lambda: None)
    before = _sqlite_schema_snapshot(engine)
    statements, capture = _capture_sql(engine)
    try:
        with pytest.raises(ValueError, match="verified catalog must belong to the current startup engine"):
            main._initialize_menu_schema()
        with pytest.raises(RuntimeError, match="migrations through 0025"):
            facility_template_version_service.ensure_facility_template_version_schema()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert facility_template_version_service._FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED is False
    assert _sqlite_schema_snapshot(engine) == before
    assert _ddl(statements) == []


def test_startup_accepts_migrated_facility_schema_without_ddl(monkeypatch, formal_startup_boundary) -> None:
    import src.main as main
    from src.services import facility_template_version_service

    engine = create_engine("sqlite://")
    FacilityTemplateVersion.__table__.create(engine)
    monkeypatch.setattr(facility_template_version_service, "get_engine", lambda: engine)
    monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
    monkeypatch.setattr(main.menu_service, "ensure_menu_schema", lambda *, inspection: None)
    monkeypatch.setattr(main.facility_service, "sync_facility_names_from_master", lambda: None)
    before = _sqlite_schema_snapshot(engine)
    statements, capture = _capture_sql(engine)
    try:
        with pytest.raises(ValueError, match="verified catalog must belong to the current startup engine"):
            main._initialize_menu_schema()
        facility_template_version_service.ensure_facility_template_version_schema()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert facility_template_version_service._FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED is True
    assert _sqlite_schema_snapshot(engine) == before
    assert _ddl(statements) == []


def test_postgres_missing_facility_effective_date_indexes_blocks_without_mutation(monkeypatch) -> None:
    uri = os.environ.get("TEST_BOOTSTRAP_DB_URI")
    if not uri:
        pytest.skip("TEST_BOOTSTRAP_DB_URI is required for owned PostgreSQL index validation")
    assert make_url(uri).get_backend_name() == "postgresql"
    from src.services import facility_template_version_service

    engine = create_engine(uri, future=True)
    schema = f"runtime_schema_guard_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.execute(text(
                "CREATE TABLE facility_template_versions ("
                "id VARCHAR PRIMARY KEY, facility_id VARCHAR NOT NULL, version VARCHAR NOT NULL, "
                "status VARCHAR NOT NULL, template_id VARCHAR, source VARCHAR, config_json JSON, "
                "columns_json JSON NOT NULL, cells_json JSON, template_digest VARCHAR NOT NULL, "
                "validation_json JSON, valid_from DATE, valid_to DATE, created_by VARCHAR, "
                "created_at TIMESTAMP NOT NULL, activated_at TIMESTAMP, archived_at TIMESTAMP)"
            ))
            monkeypatch.setattr(facility_template_version_service, "get_engine", lambda: connection)
            monkeypatch.setattr(facility_template_version_service, "_FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED", False)
            before = {
                "columns": tuple(sorted(column["name"] for column in inspect(connection).get_columns("facility_template_versions"))),
                "indexes": tuple(sorted(index["name"] for index in inspect(connection).get_indexes("facility_template_versions"))),
            }
            statements, capture = _capture_sql(connection)
            try:
                with pytest.raises(RuntimeError, match="ix_facility_template_versions_valid_from"):
                    facility_template_version_service.ensure_facility_template_version_schema()
            finally:
                event.remove(connection, "before_cursor_execute", capture)
            after = {
                "columns": tuple(sorted(column["name"] for column in inspect(connection).get_columns("facility_template_versions"))),
                "indexes": tuple(sorted(index["name"] for index in inspect(connection).get_indexes("facility_template_versions"))),
            }
            assert facility_template_version_service._FACILITY_TEMPLATE_VERSION_SCHEMA_INITIALIZED is False
            assert after == before
            assert _ddl(statements) == []
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        engine.dispose()
