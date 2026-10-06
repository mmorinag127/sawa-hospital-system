"""Standard Alembic environment for numbered hospital migrations."""

from __future__ import annotations

import os

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, inspect, pool, text
from sqlalchemy.engine import make_url
from src.maintenance.formal_migration_schema import require_revision_catalog

config = context.config
# Stable, database-local namespace shared by every formal migration revision.
MIGRATION_LOCK = (0x53415741, 0x4D494752)


def _database_url() -> str:
    database_url = os.environ.get("ALEMBIC_DB_URI")
    if not database_url:
        raise RuntimeError("ALEMBIC_DB_URI is required; automatic database adoption is disabled")
    return database_url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connect_args = {"timeout": 0} if make_url(section["sqlalchemy.url"]).get_backend_name() == "sqlite" else {}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool, connect_args=connect_args)
    try:
        with connectable.connect() as connection, connection.begin():
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            elif connection.dialect.name == "postgresql":
                acquired = connection.execute(text("SELECT pg_try_advisory_xact_lock(:namespace, :key)"),
                    {"namespace": MIGRATION_LOCK[0], "key": MIGRATION_LOCK[1]}).scalar_one()
                if not acquired:
                    raise RuntimeError("formal migration blocked: migration lock is already held")
            _require_safe_database_state(connection)
            context.configure(connection=connection, transactional_ddl=True)
            context.run_migrations()
    finally:
        connectable.dispose()


def _require_safe_database_state(connection) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    views = set(inspector.get_view_names())
    materialized_views = (
        set(inspector.get_materialized_view_names())
        if connection.dialect.name == "postgresql" else set()
    )
    if "alembic_version" not in tables:
        if tables or views or materialized_views:
            raise RuntimeError(
                "database has tables or views but no alembic_version; automatic adoption and upgrade are disabled"
            )
        return

    revisions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if len(revisions) != 1:
        raise RuntimeError("alembic_version must contain exactly one revision before upgrade")
    try:
        revision = ScriptDirectory.from_config(config).get_revision(revisions[0])
    except Exception as exc:
        raise RuntimeError(f"alembic_version contains unknown revision: {revisions[0]}") from exc
    if revision is None:
        raise RuntimeError(f"alembic_version contains unknown revision: {revisions[0]}")
    require_revision_catalog(connection, revisions[0])


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
