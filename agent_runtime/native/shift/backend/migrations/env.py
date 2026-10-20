from logging.config import fileConfig
from pathlib import Path
import sys
from contextlib import nullcontext

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from shift_scheduler_backend import models as _models  # noqa: E402,F401
from shift_scheduler_backend.db import Base, get_database_url  # noqa: E402
from shift_scheduler_backend.schema_contract import (  # noqa: E402
    _assert_catalog,
    assert_migration_source,
    migration_transaction,
)


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

provided_database_url = config.attributes.get("database_url")
if provided_database_url is None:
    requested_database_url = get_database_url()
else:
    if not isinstance(provided_database_url, str) or not provided_database_url or provided_database_url != provided_database_url.strip():
        raise RuntimeError("provided database_url must be a non-empty SQLAlchemy URL")
    try:
        make_url(provided_database_url)
    except ArgumentError as exc:
        raise RuntimeError("provided database_url must be a valid SQLAlchemy URL") from exc
    requested_database_url = provided_database_url
database_url = requested_database_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", database_url)
target_metadata = Base.metadata


def _observe_catalog_before_postflight(connection, revision: str) -> None:
    """Invoke the explicit read-only observer at the migration/postflight boundary."""
    observer = config.attributes.get("catalog_observer")
    if observer is None:
        return
    if not callable(observer):
        raise RuntimeError("catalog_observer must be callable")
    observer(connection, revision)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_connection = config.attributes.get("connection")
    connectable = None
    if provided_connection is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    else:
        if make_url(requested_database_url) != provided_connection.engine.url:
            raise RuntimeError("provided Alembic connection does not match sqlalchemy.url")
    connection_scope = (
        connectable.connect() if connectable is not None else nullcontext(provided_connection)
    )
    with connection_scope as connection:
        script = ScriptDirectory.from_config(config)
        with migration_transaction(connection):
            assert_migration_source(connection, script)
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transactional_ddl=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            destination_argument = context.get_revision_argument()
            destination = script.get_revision(destination_argument)
            if destination is None:
                from sqlalchemy import inspect

                inspector = inspect(connection)
                if set(inspector.get_table_names()) - {"alembic_version"} or inspector.get_view_names():
                    raise RuntimeError("Alembic revision target is unresolved or did not produce an empty catalog")
            else:
                _observe_catalog_before_postflight(connection, destination.revision)
                _assert_catalog(connection, destination.revision)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
