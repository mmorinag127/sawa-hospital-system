from pathlib import Path
import shutil
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from tests.integration import sqlite_fixture_template as helper


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


def test_verified_copy_rejects_changed_template_before_copy(tmp_path):
    template = tmp_path / "template"
    template.write_bytes(b"verified")
    expected = helper.file_digest(template)
    template.write_bytes(b"changed")
    destination = tmp_path / "case"
    with pytest.raises(RuntimeError, match="template differs"):
        helper.sqlite_engine_from_template(template, destination, expected_digest=expected)
    assert not destination.exists()


def test_verified_copy_rejects_partial_copy_before_engine_creation(tmp_path, monkeypatch):
    template = tmp_path / "template"
    template.write_bytes(b"verified")
    expected = helper.file_digest(template)
    monkeypatch.setattr(helper.shutil, "copyfile", lambda source, target: target.write_bytes(b"partial"))
    monkeypatch.setattr(helper.sa, "create_engine", lambda *a, **k: pytest.fail("corrupt copy connected"))
    with pytest.raises(RuntimeError, match="copy differs"):
        helper.sqlite_engine_from_template(template, tmp_path / "case", expected_digest=expected)


@pytest.mark.parametrize("changed_source", ["catalog", "migration"])
def test_formal_fixture_rejects_changed_migration_source_before_copy(request, tmp_path, monkeypatch, changed_source):
    _, _, expected = request.getfixturevalue("verified_formal_sqlite_template")
    root = Path(helper.__file__).resolve().parents[2]
    copied = tmp_path / "schema-source"
    shutil.copytree(root / "alembic", copied / "alembic", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "migrations", copied / "migrations", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(root / "alembic.ini", copied / "alembic.ini")
    monkeypatch.setattr(helper, "__file__", str(copied / "tests/integration/helper.py"))
    assert helper.formal_schema_source_digest() == expected
    changed = (copied / "alembic/catalog_contracts.json" if changed_source == "catalog"
               else next((copied / "migrations").rglob("*.py")))
    changed.write_text("changed source")
    assert helper.formal_schema_source_digest() != expected
    monkeypatch.delenv("C2_ISOLATED_PG_URI", raising=False)
    monkeypatch.setattr(helper, "sqlite_engine_from_template",
                        lambda *a, **k: pytest.fail("changed schema source copied"))
    with pytest.raises(RuntimeError, match="Formal migration source changed"):
        request.getfixturevalue("isolated_formal_schema")


def test_verified_template_identity_matches_real_formal_schema(request):
    from src.maintenance.formal_migration_schema import require_formal_head

    template, digest, schema_source = request.getfixturevalue("verified_formal_sqlite_template")
    assert helper.file_digest(template) == digest
    assert helper.formal_schema_source_digest() == schema_source
    verifier = sa.create_engine(f"sqlite:///{template}")
    try:
        with verifier.connect() as connection:
            require_formal_head(connection)
    finally:
        verifier.dispose()
    assert helper.file_digest(template) == digest


def test_delivery_template_copies_keep_schema_and_data_independent(delivery_sqlite_template, tmp_path):
    from src import db

    template, digest = delivery_sqlite_template
    first = helper.sqlite_engine_from_template(template, tmp_path / "delivery-first.sqlite3", expected_digest=digest)
    second = helper.sqlite_engine_from_template(template, tmp_path / "delivery-second.sqlite3", expected_digest=digest)
    reference = sa.create_engine("sqlite://")
    try:
        db.Base.metadata.create_all(reference)
        def schema(engine):
            with engine.connect() as connection:
                return connection.exec_driver_sql(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).all()
        assert schema(first) == schema(second) == schema(reference)
        with first.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE case_only (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO case_only VALUES (7)")
        assert "case_only" not in sa.inspect(second).get_table_names()
        assert helper.file_digest(template) == digest
    finally:
        first.dispose()
        second.dispose()
        reference.dispose()


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
