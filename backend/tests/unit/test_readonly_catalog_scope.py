"""Read auditing must not permit writes disguised as catalog inspection."""
import pytest

from tests.helpers.readonly_catalog import readonly_catalog_statements


@pytest.mark.parametrize("statement", [
    "UPDATE orders SET status=status", "DELETE FROM orders", "CREATE TABLE unexpected(id INTEGER)",
    "PRAGMA foreign_keys=OFF", "PRAGMA main.user_version=1", "PRAGMA writable_schema=ON",
    'PRAGMA main.table_xinfo("not_a_formal_table")', "BEGIN IMMEDIATE",
])
def test_catalog_read_allowlist_rejects_writes_settings_and_unknown_objects(isolated_formal_schema, statement):
    assert statement not in readonly_catalog_statements(isolated_formal_schema)


def test_actual_formal_sqlite_tables_remain_inspectable_without_allowing_arbitrary_pragma(isolated_formal_sqlite_template, tmp_path):
    from tests.integration.sqlite_fixture_template import sqlite_engine_from_template
    engine = sqlite_engine_from_template(isolated_formal_sqlite_template, tmp_path / "readonly-catalog.sqlite3")
    try:
        allowed = readonly_catalog_statements(engine)
    finally:
        engine.dispose()
    assert 'PRAGMA main.table_xinfo("orders")' in allowed
    assert 'PRAGMA main.foreign_key_list("order_documents")' in allowed
    assert 'PRAGMA main.index_list("monthly_menu_items")' in allowed
    assert "PRAGMA main.foreign_keys" not in allowed
