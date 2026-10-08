"""Actual COPY-equivalent package/owned SQLite proof; not image or Cloud Run proof."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa


BACKEND = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "runtime_packaging_behavior", Path(__file__).with_name("test_formal_catalog_packaging.py")
)
behavior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(behavior)


def test_actual_copy_config_and_declared_migration_resources():
    copies = behavior._copies()
    assert copies.count(["alembic.ini", "./alembic.ini"]) == 1
    assert ["migrations", "./migrations"] in copies
    assert ["alembic/catalog_contracts.json", "./alembic/catalog_contracts.json"] in copies
    config = Config(str(BACKEND / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    assert Path(config.get_main_option("script_location")) == BACKEND / "alembic"
    assert Path(config.get_main_option("version_locations")) == BACKEND / "migrations"
    heads = scripts.get_heads()
    assert len(heads) == 1
    chain = list(scripts.walk_revisions(base="base", head=heads[0]))
    assert chain
    assert all(Path(revision.path).parent == BACKEND / "migrations" for revision in chain)
    contract = json.loads((BACKEND / "alembic/catalog_contracts.json").read_text())
    for dialect in ("sqlite", "postgresql"):
        assert heads[0] in contract["revisions"][dialect]


@pytest.mark.parametrize("state", ["head", "damaged", "missing_resource"])
def test_actual_copy_preserves_existing_behavior_and_database(tmp_path, monkeypatch, state):
    behavior.test_copy_equivalent_package_readonly_guard(tmp_path, monkeypatch, state)
    package = tmp_path / "app"
    assert (package / "alembic.ini").read_bytes() == (BACKEND / "alembic.ini").read_bytes()
    original = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    packaged = ScriptDirectory.from_config(Config(str(package / "alembic.ini")))
    assert packaged.get_heads() == original.get_heads()
    for revision in original.walk_revisions():
        copied = packaged.get_revision(revision.revision)
        assert Path(copied.path).is_relative_to(package / "migrations")
        assert Path(copied.path).read_bytes() == Path(revision.path).read_bytes()


@pytest.mark.parametrize("missing", ["ini", "migrations", "head_migration"])
def test_missing_formal_source_stops_without_database_writes(tmp_path, monkeypatch, missing):
    for name in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(name, raising=False)
    database = tmp_path / "formal.sqlite3"
    uri = f"sqlite:///{database}"
    monkeypatch.setenv("ALEMBIC_DB_URI", uri)
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO users(id, account, role, status, created_at) VALUES ('source-proof', 'source-proof', 'operator', 'active', '2026-10-09 00:00:00')"))
        with engine.connect() as connection:
            before = {
                "schema": connection.exec_driver_sql("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").all(),
                "rows": connection.exec_driver_sql("SELECT * FROM users").all(),
                "revision": connection.exec_driver_sql("SELECT * FROM alembic_version").all(),
            }
        package = tmp_path / "app"
        package.mkdir()
        prepared = subprocess.run(
            ["/bin/bash", str(behavior.ROOT / "scripts/prepare_hakodate_backend_deploy_source.sh"), "runtime-source-proof", str(tmp_path / "context")],
            cwd=behavior.ROOT, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "TZ": "UTC"},
            capture_output=True, text=True, timeout=30,
        )
        assert prepared.returncode == 0, prepared.stderr
        context = Path(prepared.stdout.strip())
        assert context == tmp_path / "context/backend"
        for source, destination in behavior._copies():
            target = package / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            if (context / source).is_dir():
                shutil.copytree(context / source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(context / source, target)
        assert (package / "alembic.ini").read_bytes() == (BACKEND / "alembic.ini").read_bytes()
        assert (package / "alembic/catalog_contracts.json").read_bytes() == (BACKEND / "alembic/catalog_contracts.json").read_bytes()
        scripts = ScriptDirectory.from_config(Config(str(package / "alembic.ini")))
        head = scripts.get_revision(scripts.get_current_head())
        assert Path(head.path).read_bytes() == (BACKEND / "migrations" / Path(head.path).name).read_bytes()
        if missing == "ini":
            (package / "alembic.ini").unlink()
        elif missing == "migrations":
            shutil.rmtree(package / "migrations")
        else:
            Path(head.path).unlink()
        statements = tmp_path / "statements.json"
        code = """
import json, sys
from pathlib import Path
import sqlalchemy as sa
from src.maintenance import formal_migration_schema as guard
package = Path.cwd()
assert Path(guard.__file__).is_relative_to(package)
assert guard.ALEMBIC_CONFIG == package / 'alembic.ini'
assert guard.CONTRACT == package / 'alembic/catalog_contracts.json'
seen = []
engine = sa.create_engine(sys.argv[1])
sa.event.listen(engine, 'before_cursor_execute', lambda c, u, s, p, x, m: seen.append(s))
try:
    with engine.connect() as connection:
        guard.require_formal_head(connection)
finally:
    Path(sys.argv[2]).write_text(json.dumps(seen))
    engine.dispose()
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, f"sqlite:///file:{database}?mode=ro&uri=true", str(statements)],
            cwd=package, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "TZ": "UTC", "PYTHONPATH": str(package), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0, "missing source must never accept a guessed head"
        assert ("script_location" if missing == "ini" else "formal migration") in result.stderr
        seen = json.loads(statements.read_text())
        assert seen
        assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE")) for sql in seen)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").all() == before["schema"]
            assert connection.exec_driver_sql("SELECT * FROM users").all() == before["rows"]
            assert connection.exec_driver_sql("SELECT * FROM alembic_version").all() == before["revision"]
    finally:
        engine.dispose()
