"""Offline contract tests for the staging runtime-schema preflight."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


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


class Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class CatalogConnection:
    def __init__(self, columns, indexes):
        self.columns = columns
        self.indexes = indexes
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "information_schema.columns" in sql:
            return Result(self.columns[params["table_name"]])
        if "pg_indexes" in sql:
            return Result(self.indexes)
        return Result([])


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(runner, "verify_connection_target", lambda connection: None)
    return CatalogConnection(
        {runner.BASE_TABLE: list(runner.BASE_COLUMNS), runner.FACILITY_TABLE: list(runner.FACILITY_COLUMNS)},
        list(runner.FACILITY_INDEXES),
    )


def assert_catalog_only(statements):
    writes = ("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
    assert not any(statement.lstrip().upper().startswith(writes) for statement in statements)


def test_missing_table_blocks_without_mutating_sql(catalog):
    catalog.columns[runner.BASE_TABLE] = []
    with pytest.raises(runner.MigrationBlocked, match="missing table base_menu_cycle_items"):
        runner.validate_catalog(catalog)
    assert_catalog_only(catalog.statements)


def test_missing_column_blocks_without_mutating_sql(catalog):
    catalog.columns[runner.FACILITY_TABLE].remove("template_digest")
    with pytest.raises(runner.MigrationBlocked, match="missing columns in facility_template_versions: template_digest"):
        runner.validate_catalog(catalog)
    assert_catalog_only(catalog.statements)


def test_missing_index_blocks_without_mutating_sql(catalog):
    catalog.indexes.remove("ix_facility_template_versions_valid_to")
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
