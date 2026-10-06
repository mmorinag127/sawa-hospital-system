"""Offline contract tests for the staging runtime-schema preflight."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "runtime_schema_preflight", ROOT / "scripts/run_stg_runtime_schema_preflight.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


CONTEXT = {
    "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "12345", "GITHUB_REF": "refs/heads/develop",
    "GITHUB_REF_NAME": "develop", "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2",
    "WEB_SERVICE": "web-stg", "WORKER_SERVICE": "worker-stg",
}


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "verify_connection_target", lambda connection: None)
    uri = f"sqlite:///{tmp_path / 'preflight.sqlite3'}"
    with monkeypatch.context() as target:
        for name in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
            target.delenv(name, raising=False)
        target.setenv("ALEMBIC_DB_URI", uri)
        command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    engine = sa.create_engine(uri)
    try:
        with engine.connect() as connection:
            connection.statements = []
            original = connection.execute

            def execute(statement, *args, **kwargs):
                sql = str(statement)
                if sql.startswith("SET "):
                    # Offline SQLite exercises the real common catalog checker;
                    # PostgreSQL transaction settings are asserted separately on owned PG.
                    connection.statements.append(sql)
                    return None
                return original(statement, *args, **kwargs)

            def capture(connection, cursor, statement, parameters, context, executemany):
                connection.statements.append(statement)

            monkeypatch.setattr(connection, "execute", execute)
            sa.event.listen(engine, "before_cursor_execute", capture)
            try:
                yield connection
            finally:
                sa.event.remove(engine, "before_cursor_execute", capture)
    finally:
        engine.dispose()


def assert_catalog_only(statements):
    writes = ("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
    assert not any(statement.lstrip().upper().startswith(writes) for statement in statements)


def test_missing_table_blocks_without_mutating_sql(catalog):
    catalog.exec_driver_sql("DROP TABLE base_menu_cycle_items")
    catalog.statements.clear()
    with pytest.raises(runner.MigrationBlocked, match="missing table base_menu_cycle_items"):
        runner.validate_catalog(catalog)
    assert_catalog_only(catalog.statements)


def test_missing_column_blocks_without_mutating_sql(catalog):
    catalog.exec_driver_sql("ALTER TABLE facility_template_versions RENAME COLUMN template_digest TO unknown_digest")
    catalog.statements.clear()
    with pytest.raises(runner.MigrationBlocked, match="missing columns in facility_template_versions: template_digest"):
        runner.validate_catalog(catalog)
    assert_catalog_only(catalog.statements)


def test_missing_index_blocks_without_mutating_sql(catalog):
    catalog.exec_driver_sql("DROP INDEX ix_facility_template_versions_valid_to")
    catalog.statements.clear()
    with pytest.raises(runner.MigrationBlocked, match="missing indexes in facility_template_versions"):
        runner.validate_catalog(catalog)
    assert_catalog_only(catalog.statements)


def test_success_uses_read_only_catalog_sql(catalog):
    runner.validate_catalog(catalog)
    assert "SET TRANSACTION READ ONLY" in catalog.statements
    assert_catalog_only(catalog.statements)


def test_context_target_rejection_precedes_transport(monkeypatch, capsys):
    for key, value in CONTEXT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WORKER_SERVICE", "worker-prod")
    called = []
    monkeypatch.setattr(runner, "_load_service_db_config", lambda *args: called.append(args))
    assert runner.main() == 1
    assert called == []
    assert "staging Actions requires WORKER_SERVICE=worker-stg" in capsys.readouterr().err


def test_database_target_rejection_precedes_proxy(monkeypatch, capsys):
    for key, value in CONTEXT.items():
        monkeypatch.setenv(key, value)
    rejected = SimpleNamespace(
        instance_connection_name="other-project:asia-northeast2:orders-stg",
        db_name=runner.STAGING_DATABASE,
        db_user="orders_app",
        db_password="test-password",
    )
    proxy_calls = []
    monkeypatch.setattr(runner, "_load_service_db_config", lambda *args: rejected)
    monkeypatch.setattr(runner, "cloud_sql_proxy", lambda *args: proxy_calls.append(args))
    assert runner.main() == 1
    assert proxy_calls == []
    assert "verified orders-stg target required" in capsys.readouterr().err


def test_owned_proxy_and_engine_are_cleaned_up(monkeypatch):
    for key, value in CONTEXT.items():
        monkeypatch.setenv(key, value)
    events = []
    config = SimpleNamespace(
        instance_connection_name=runner.STAGING_INSTANCE,
        db_name=runner.STAGING_DATABASE,
        db_user="orders_app",
        db_password="secret-not-logged",
    )

    @contextmanager
    def proxy(instance):
        events.append(("proxy-enter", instance))
        try:
            yield
        finally:
            events.append(("proxy-exit", instance))

    class Transaction:
        def rollback(self):
            events.append("rollback")

    class Connection:
        def begin(self):
            events.append("begin")
            return Transaction()

    class ConnectionContext:
        def __enter__(self):
            return Connection()

        def __exit__(self, *args):
            events.append("connection-exit")

    class Engine:
        def connect(self):
            return ConnectionContext()

        def dispose(self):
            events.append("dispose")

    monkeypatch.setattr(runner, "require_staging_context", lambda: None)
    monkeypatch.setattr(runner, "_load_service_db_config", lambda *args: config)
    monkeypatch.setattr(runner, "cloud_sql_proxy", proxy)
    monkeypatch.setattr(runner, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(runner, "validate_catalog", lambda connection: events.append("validate"))
    runner.run()
    assert events == [
        ("proxy-enter", runner.STAGING_INSTANCE), "begin", "validate", "rollback",
        "connection-exit", "dispose", ("proxy-exit", runner.STAGING_INSTANCE),
    ]
