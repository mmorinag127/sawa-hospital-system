"""COPY-equivalent component proof, not a Docker image execution test."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
BASE_DOCKERFILE_SHA256 = "4259d6f559cc2ab9d5ff85dfe92c21f44d216bda4120db6e52218280eddfdc3b"


def _copies():
    return [shlex.split(line)[1:] for line in (BACKEND / "Dockerfile").read_text().splitlines() if line.startswith("COPY ")]


def test_backend_runtime_catalog_copy_contract():
    assert ["src", "./src"] in _copies()
    assert ["alembic/catalog_contracts.json", "./alembic/catalog_contracts.json"] in _copies()
    after = (BACKEND / "Dockerfile").read_bytes()
    original = after.replace(b"COPY alembic/catalog_contracts.json ./alembic/catalog_contracts.json\n", b"")
    assert hashlib.sha256(original).hexdigest() == BASE_DOCKERFILE_SHA256


@pytest.mark.parametrize("state", ["head", "damaged", "missing_resource"])
def test_copy_equivalent_package_readonly_guard(tmp_path, monkeypatch, state):
    uri = f"sqlite:///{tmp_path / 'formal.sqlite3'}"
    for name in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALEMBIC_DB_URI", uri)
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO users(id, account, role, status, created_at) VALUES ('package-proof', 'package-proof', 'operator', 'active', '2026-10-06 00:00:00')"))
            if state == "damaged":
                connection.execute(sa.text("ALTER TABLE users ADD COLUMN unknown_package_column TEXT"))
        with engine.connect() as connection:
            before_schema = connection.execute(sa.text("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")).all()
            before_rows = connection.execute(sa.text("SELECT * FROM users")).all()
            before_version = connection.execute(sa.text("SELECT * FROM alembic_version")).all()
        package = tmp_path / "app"
        package.mkdir()
        prepare_env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "TZ": "UTC"}
        prepared = subprocess.run(["/bin/bash", str(ROOT / "scripts/prepare_hakodate_backend_deploy_source.sh"), "catalog-package-proof", str(tmp_path / "context")], cwd=ROOT, env=prepare_env, capture_output=True, text=True, timeout=30)
        assert prepared.returncode == 0, prepared.stderr
        context = Path(prepared.stdout.strip())
        assert context == tmp_path / "context/backend"
        assert (context / "Dockerfile").read_bytes() == (BACKEND / "Dockerfile").read_bytes()
        for source, destination in _copies():
            target = package / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            if (context / source).is_dir():
                shutil.copytree(context / source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(context / source, target)
        resource = package / "alembic/catalog_contracts.json"
        assert hashlib.sha256(resource.read_bytes()).digest() == hashlib.sha256((BACKEND / "alembic/catalog_contracts.json").read_bytes()).digest()
        if state == "missing_resource":
            resource.unlink()
        code = """
import json, sys
import sqlalchemy as sa
from src.maintenance.formal_migration_schema import require_formal_head
statements = []
engine = sa.create_engine(sys.argv[1])
sa.event.listen(engine, 'before_cursor_execute', lambda c, u, s, p, x, m: statements.append(s))
try:
    with engine.connect() as connection:
        require_formal_head(connection)
        assert connection.execute(sa.text('SELECT id FROM users')).scalars().all() == ['package-proof']
    assert not any(s.lstrip().upper().startswith(('CREATE', 'ALTER', 'DROP', 'INSERT', 'UPDATE', 'DELETE')) for s in statements)
    print(json.dumps({'accepted': True, 'module': sys.modules['src.maintenance.formal_migration_schema'].__file__}))
finally:
    engine.dispose()
"""
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "TZ": "UTC", "PYTHONPATH": str(package), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-c", code, uri], cwd=package, env=env, capture_output=True, text=True, timeout=30)
        if state == "head":
            assert result.returncode == 0, result.stderr
            assert Path(json.loads(result.stdout)["module"]).is_relative_to(package)
        else:
            assert result.returncode != 0
            assert ("catalog" if state == "damaged" else "FileNotFoundError") in result.stderr
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")).all() == before_schema
            assert connection.execute(sa.text("SELECT * FROM users")).all() == before_rows
            assert connection.execute(sa.text("SELECT * FROM alembic_version")).all() == before_version
    finally:
        engine.dispose()
