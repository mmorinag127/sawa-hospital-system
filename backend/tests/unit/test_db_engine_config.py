from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import patch


def _reload_db_module():
    sys.modules.pop("src.db", None)
    return importlib.import_module("src.db")


def test_postgres_engine_enables_pre_ping_and_recycle(monkeypatch):
    monkeypatch.setenv("DB_URI", "postgresql+psycopg2://user:pass@db.example.local/app")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "123")
    monkeypatch.setenv("DB_POOL_SIZE", "2")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")

    with patch("sqlalchemy.create_engine", return_value=object()) as create_engine_mock:
        _reload_db_module()

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
        _reload_db_module()

    _, kwargs = create_engine_mock.call_args
    assert "pool_pre_ping" not in kwargs
    assert "pool_recycle" not in kwargs
    assert "pool_use_lifo" not in kwargs
    assert kwargs["connect_args"]["check_same_thread"] is False
