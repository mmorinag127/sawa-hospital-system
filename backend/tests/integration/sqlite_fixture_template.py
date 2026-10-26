"""Build a formal SQLite schema once, then provide independent database copies."""

from pathlib import Path
from hashlib import sha256
import os
import shutil
import sys

from alembic import command
from alembic.config import Config
import sqlalchemy as sa


def formal_schema_source_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    paths = [root / "alembic.ini", root / "alembic/catalog_contracts.json",
             *sorted((root / "alembic").rglob("*.py")),
             *sorted((root / "migrations").rglob("*.py"))]
    digest = sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def migrated_sqlite_template(tmp_path_factory, *, alembic_ini: Path, name: str) -> Path:
    template = tmp_path_factory.mktemp(name) / "formal-empty.sqlite3"
    sys.audit('sawa.test.generated-sqlite-template', str(template.parent))
    engine = sa.create_engine(f"sqlite:///{template}")
    names = ("ALEMBIC_DB_URI", "DB_URI", "DATABASE_URL")
    previous_uris = {key: os.environ.get(key) for key in names}
    try:
        uri = f"sqlite:///{template}"
        for key in names:
            os.environ[key] = uri
        config = Config(str(alembic_ini))
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        for key, value in previous_uris.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        engine.dispose()
    return template


def sqlite_engine_from_template(template: Path, destination: Path, *, expected_digest=None, **kwargs) -> sa.Engine:
    if destination.exists():
        raise RuntimeError("SQLite fixture destination must be a new per-case path")
    if expected_digest is not None and file_digest(template) != expected_digest:
        raise RuntimeError("SQLite fixture template differs from its verified schema")
    shutil.copyfile(template, destination)
    if expected_digest is not None and file_digest(destination) != expected_digest:
        raise RuntimeError("SQLite fixture copy differs from its verified schema")
    return sa.create_engine(f"sqlite:///{destination}", **kwargs)


def file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
