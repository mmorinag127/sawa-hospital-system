from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest


_MISSING = object()


@pytest.fixture(autouse=True)
def isolated_database_env(monkeypatch):
    for name in (
        "DB_URI",
        "ALEMBIC_DB_URI",
        "DATABASE_URL",
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME",
        "DB_HOST",
        "DB_PORT",
        "DB_DRIVER",
    ):
        monkeypatch.delenv(name, raising=False)


@contextmanager
def _reload_db_module():
    """Load a mocked ``src.db`` without leaking it into later test modules."""
    import src

    original_module = sys.modules.get("src.db", _MISSING)
    original_package_attr = getattr(src, "db", _MISSING)
    sys.modules.pop("src.db", None)
    try:
        yield importlib.import_module("src.db")
    finally:
        sys.modules.pop("src.db", None)
        if original_module is _MISSING:
            sys.modules.pop("src.db", None)
        else:
            sys.modules["src.db"] = original_module
        if original_package_attr is _MISSING:
            if hasattr(src, "db"):
                delattr(src, "db")
        else:
            setattr(src, "db", original_package_attr)


def test_postgres_engine_enables_pre_ping_and_recycle(monkeypatch):
    monkeypatch.setenv("DB_URI", "postgresql+psycopg2://user:pass@db.example.local/app")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "123")
    monkeypatch.setenv("DB_POOL_SIZE", "2")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")

    with patch("sqlalchemy.create_engine", return_value=object()) as create_engine_mock:
        with _reload_db_module():
            pass

    _, kwargs = create_engine_mock.call_args
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 123
    assert kwargs["pool_use_lifo"] is True
    assert kwargs["pool_size"] == 2
    assert kwargs["max_overflow"] == 0


def test_sqlite_engine_does_not_set_queue_pool_options(monkeypatch):
    monkeypatch.setenv("DB_URI", "sqlite:///./test.db")
    monkeypatch.delenv("DB_POOL_RECYCLE_SECONDS", raising=False)

    with patch("sqlalchemy.create_engine", return_value=object()) as create_engine_mock:
        with _reload_db_module():
            pass

    _, kwargs = create_engine_mock.call_args
    assert "pool_pre_ping" not in kwargs
    assert "pool_recycle" not in kwargs
    assert "pool_use_lifo" not in kwargs
    assert kwargs["connect_args"]["check_same_thread"] is False


def test_reload_db_module_restores_registry_package_attribute_and_engine(monkeypatch):
    import src
    from src import db as runtime_db

    runtime_engine = runtime_db.engine
    monkeypatch.setenv("DB_URI", "sqlite:///./test.db")

    with patch("sqlalchemy.create_engine", return_value=object()):
        with _reload_db_module() as reloaded_db:
            assert sys.modules["src.db"] is reloaded_db
            assert src.db is reloaded_db
            assert reloaded_db.engine is not runtime_engine

    assert sys.modules["src.db"] is runtime_db
    assert src.db is runtime_db
    assert runtime_db.engine is runtime_engine
