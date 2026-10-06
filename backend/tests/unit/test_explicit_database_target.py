import pytest
from sqlalchemy.engine import make_url

from src.maintenance.database_target import explicit_database_uri


@pytest.fixture(autouse=True)
def clear_target(monkeypatch):
    for name in ("ALEMBIC_DB_URI", "DATABASE_URL", "DB_URI", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("migration", (False, True))
@pytest.mark.parametrize("value", (None, "", " ", "\t\n"))
def test_missing_or_blank_target_stops_before_connection(monkeypatch, value, migration):
    if value is not None:
        monkeypatch.setenv("ALEMBIC_DB_URI" if migration else "DB_URI", value)
    with pytest.raises(RuntimeError, match="required|explicit|nonblank"):
        explicit_database_uri(migration=migration)


@pytest.mark.parametrize("migration", (False, True))
def test_alias_disagreement_is_never_silently_selected(monkeypatch, migration):
    monkeypatch.setenv("DB_URI", "sqlite:///explicit.db")
    monkeypatch.setenv("ALEMBIC_DB_URI", "sqlite:///different.db")
    with pytest.raises(RuntimeError, match="disagree"):
        explicit_database_uri(migration=migration)


@pytest.mark.parametrize("migration", (False, True))
def test_cloudsql_components_preserve_explicit_target(monkeypatch, migration):
    for name, value in {"DB_USER": "orders", "DB_PASSWORD": "owned@secret", "DB_NAME": "orders", "DB_HOST": "/cloudsql/project:region:instance"}.items():
        monkeypatch.setenv(name, value)
    expected = "postgresql+psycopg2://orders:owned%40secret@/orders?host=/cloudsql/project:region:instance"
    if migration:
        monkeypatch.setenv("ALEMBIC_DB_URI", expected)
    assert make_url(explicit_database_uri(migration=migration)) == make_url(expected)
    monkeypatch.setenv("DB_URI", "sqlite:///different.db")
    with pytest.raises(RuntimeError, match="disagree"):
        explicit_database_uri(migration=migration)


def test_partial_components_are_not_ignored(monkeypatch):
    monkeypatch.setenv("DB_URI", "sqlite:///explicit.db")
    monkeypatch.setenv("DB_HOST", "/cloudsql/project:region:instance")
    with pytest.raises(RuntimeError, match="components"):
        explicit_database_uri()


def test_invalid_uri_does_not_echo_input(monkeypatch):
    monkeypatch.setenv("DB_URI", "owned-secret-invalid")
    with pytest.raises(RuntimeError, match="valid explicit") as caught:
        explicit_database_uri()
    assert "owned-secret" not in str(caught.value)


def test_matching_aliases_are_preserved_in_runtime_and_migration(monkeypatch):
    for name in ("DB_URI", "DATABASE_URL", "ALEMBIC_DB_URI"):
        monkeypatch.setenv(name, "sqlite:///explicit.db")
    assert explicit_database_uri() == explicit_database_uri(migration=True) == "sqlite:///explicit.db"


@pytest.mark.parametrize("runtime", (False, True))
def test_inspection_import_is_db_free_but_runtime_requires_target(runtime):
    import os
    from pathlib import Path
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[3]
    environment = {"PATH": os.environ["PATH"], "PYTHONPATH": str(root / "backend") + ":" + str(root / "scripts")}
    code = "import src.db" if runtime else (
        "import sys; import run_stg_runtime_schema_preflight; "
        "import apply_menu_master_revision_migration; import src.maintenance.formal_migration_schema; "
        "assert 'src.db' not in sys.modules; assert 'src.models' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=environment, capture_output=True, text=True, timeout=30)
    if runtime:
        assert result.returncode != 0
        assert "explicit database configuration is required" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
