"""Compare complete startup catalogs with the immutable deployed implementation."""
from pathlib import Path
import subprocess
from types import ModuleType

import pytest
from sqlalchemy import event

from tests.integration.test_menu_readonly_boundary import readonly_database

ROOT = Path(__file__).resolve().parents[3]
BASE = "9d1847743e578eeb3d4438c45f8327feca80d621"
SOURCES = {
    "shift": ("agent_runtime/native/shift/backend/shift_scheduler_backend/schema_contract.py", "catalog_document"),
    "school": ("agent_runtime/native/school-lunch/backend/src/migration_catalog.py", "read_catalog"),
}


def catalog_reader(domain, baseline=False):
    path, name = SOURCES[domain]
    source = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT, text=True) if baseline else (ROOT / path).read_text()
    module = ModuleType(f"catalog_{domain}_{baseline}")
    module.__file__ = str(ROOT / path)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module, getattr(module, name)


@pytest.mark.parametrize("domain", ["shift", "school"])
def test_bulk_catalog_preserves_every_field_and_detects_schema_drift(readonly_database, domain):
    _, before = catalog_reader(domain, baseline=True)
    _, after = catalog_reader(domain)
    statements = []
    def record(*args):
        statements.append(args[2])
    event.listen(readonly_database, "before_cursor_execute", record)
    try:
        with readonly_database.begin() as connection:
            original = before(connection)
            before_count = len(statements)
            statements.clear()
            assert after(connection) == original
            if connection.dialect.name == "postgresql":
                assert len(statements) < before_count
            connection.exec_driver_sql("CREATE TABLE catalog_probe (id INTEGER PRIMARY KEY, amount INTEGER NOT NULL CHECK (amount >= 0), owner_id INTEGER REFERENCES catalog_probe(id))")
            changed = after(connection)
            assert changed != original
            assert changed == before(connection)
            assert "catalog_probe" in changed["tables"]
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("CREATE TRIGGER catalog_probe_trigger AFTER INSERT ON catalog_probe BEGIN SELECT 1; END")
            else:
                connection.exec_driver_sql("CREATE FUNCTION catalog_probe_trigger_fn() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'")
                connection.exec_driver_sql("CREATE TRIGGER catalog_probe_trigger BEFORE INSERT ON catalog_probe FOR EACH ROW EXECUTE FUNCTION catalog_probe_trigger_fn()")
            # School retains SQLite trigger definitions; Shift rejects them.
            if domain == "school" and connection.dialect.name == "sqlite":
                assert after(connection) == before(connection)
                assert after(connection) != changed
            else:
                for reader in (before, after):
                    with pytest.raises(RuntimeError, match="[Uu]nsupported"):
                        reader(connection)
    finally:
        event.remove(readonly_database, "before_cursor_execute", record)


@pytest.mark.parametrize("domain", ["shift", "school"])
def test_missing_bulk_table_is_not_substituted_with_empty_structure(readonly_database, domain, monkeypatch):
    if readonly_database.dialect.name != "postgresql":
        pytest.skip("bulk reflection is PostgreSQL-only")
    module, reader = catalog_reader(domain)
    inspect = module.inspect
    def broken(connection):
        inspector = inspect(connection)
        monkeypatch.setattr(inspector, "get_multi_columns", lambda **kwargs: {})
        return inspector
    monkeypatch.setattr(module, "inspect", broken)
    with readonly_database.connect() as connection, pytest.raises(KeyError):
        reader(connection)
