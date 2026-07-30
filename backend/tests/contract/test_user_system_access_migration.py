from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_user_system_access_migration_is_explicit_and_hospital_only():
    source = (ROOT / "migrations" / "0026_user_system_access.py").read_text(encoding="utf-8")

    assert 'revision = "0026"' in source
    assert 'down_revision = "0025"' in source
    assert '"user_system_access"' in source
    assert "SELECT id, 'hospital', TRUE" in source
    assert "WHERE lower(status) = 'active'" in source
    assert "SELECT id, 'shift', TRUE" not in source
    assert "SELECT id, 'school-lunch', TRUE" not in source
    assert "portal_hospital_access_migrated" in source


def test_portal_does_not_create_permission_schema_as_runtime_fallback():
    source = (ROOT / "src" / "api" / "portal.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS user_system_access" not in source


def test_main_does_not_bootstrap_portal_schema_on_startup():
    source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")

    assert "portal.ensure_portal_schema()" not in source
