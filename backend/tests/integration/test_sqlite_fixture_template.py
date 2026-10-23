from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

import sqlite_fixture_template as helper


def test_migration_failure_restores_environment_and_disposes_engine(monkeypatch, tmp_path_factory):
    engine = MagicMock()
    monkeypatch.setattr(helper.sa, "create_engine", lambda *a, **k: engine)
    monkeypatch.setenv("ALEMBIC_DB_URI", "sqlite:///original-alembic")
    monkeypatch.setenv("DB_URI", "sqlite:///original-db")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def fail(*args):
        raise RuntimeError("migration failure")

    monkeypatch.setattr(helper.command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="migration failure"):
        helper.migrated_sqlite_template(tmp_path_factory, alembic_ini=Path("unused.ini"), name="failed")
    assert helper.os.environ["ALEMBIC_DB_URI"] == "sqlite:///original-alembic"
    assert helper.os.environ["DB_URI"] == "sqlite:///original-db"
    assert "DATABASE_URL" not in helper.os.environ
    engine.dispose.assert_called_once_with()


def test_template_copies_have_independent_committed_data(tmp_path):
    template = tmp_path / "template.sqlite3"
    original = sa.create_engine(f"sqlite:///{template}")
    try:
        with original.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE entries (id INTEGER PRIMARY KEY)")
    finally:
        original.dispose()
    digest = helper.file_digest(template)
    first = helper.sqlite_engine_from_template(template, tmp_path / "first.sqlite3")
    second = helper.sqlite_engine_from_template(template, tmp_path / "second.sqlite3")
    try:
        with first.begin() as connection:
            connection.exec_driver_sql("INSERT INTO entries VALUES (1)")
        with second.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM entries").scalar_one() == 0
        assert helper.file_digest(template) == digest
    finally:
        first.dispose()
        second.dispose()


def test_existing_destination_is_not_overwritten(tmp_path):
    template, destination = tmp_path / "template", tmp_path / "destination"
    template.write_bytes(b"template")
    destination.write_bytes(b"existing")
    with pytest.raises(RuntimeError, match="new per-case path"):
        helper.sqlite_engine_from_template(template, destination)
    assert destination.read_bytes() == b"existing"


@pytest.mark.parametrize("case_value", [41, 73])
def test_shared_formal_fixture_keeps_each_case_independent(isolated_formal_schema, request, case_value):
    from src.maintenance.formal_migration_schema import require_formal_head

    engine = isolated_formal_schema
    template = None
    if engine.dialect.name == "sqlite":
        template = request.getfixturevalue("isolated_formal_sqlite_template")
        digest = helper.file_digest(template)
        assert Path(engine.url.database) != template
    assert not sa.inspect(engine).has_table("fixture_ownership_probe")
    with engine.begin() as connection:
        require_formal_head(connection)
        if engine.dialect.name == "sqlite":
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        connection.exec_driver_sql("CREATE TABLE fixture_ownership_probe (id INTEGER PRIMARY KEY)")
        connection.execute(sa.text("INSERT INTO fixture_ownership_probe VALUES (:value)"), {"value": case_value})
        assert connection.exec_driver_sql("SELECT id FROM fixture_ownership_probe").scalar_one() == case_value
    if template is not None:
        assert helper.file_digest(template) == digest
