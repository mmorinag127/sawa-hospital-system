from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, inspect, pool, text
from sqlalchemy.engine import make_url

config = context.config
provided = config.attributes.get("migration_uri")
from src.migration_catalog import explicit_database_uri, require_consistent_components
uri = provided if provided is not None else explicit_database_uri()
if not isinstance(uri, str) or not uri.strip():
    raise RuntimeError("Specify one explicit, consistent migration database URI")
require_consistent_components(uri)

from src.db import Base
from src.migration_catalog import read_catalog, require_revision_catalog
import src.models  # noqa: F401,E402


config.set_main_option("sqlalchemy.url", uri.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        if supplied.engine.url != make_url(uri):
            raise RuntimeError("Supplied migration connection does not match the explicit URI")
        if supplied.in_transaction():
            raise RuntimeError("Formal migrations require ownership of the transaction")
        apply_on_connection(supplied)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            apply_on_connection(connection)
    finally:
        connectable.dispose()


def apply_on_connection(connection):
    cursor = None
    previous_timeout = None
    if connection.dialect.name == "sqlite":
        cursor = connection.connection.driver_connection.cursor()
        previous_timeout = cursor.execute("PRAGMA busy_timeout").fetchone()[0]
        cursor.execute("PRAGMA busy_timeout=0")
    try:
        _apply_locked(connection)
    finally:
        if cursor is not None:
            try:
                cursor.execute(f"PRAGMA busy_timeout={previous_timeout}")
            finally:
                cursor.close()


def _apply_locked(connection):
    with connection.begin():
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif connection.dialect.name == "postgresql":
            if not connection.scalar(text("SELECT pg_try_advisory_xact_lock(734021906026)")):
                raise RuntimeError("Another formal migration holds the database lock")
        else:
            raise RuntimeError("Unsupported migration dialect")
        inspector = inspect(connection)
        tables = inspector.get_table_names()
        if "alembic_version" not in tables:
            catalog = read_catalog(connection)
            if catalog["tables"] or catalog["views"]:
                raise RuntimeError("Nonempty versionless database requires verified explicit adoption")
        else:
            versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if len(versions) != 1:
                raise RuntimeError("Migration version table must contain exactly one revision")
            require_revision_catalog(connection, versions[0])
        context.configure(connection=connection, target_metadata=target_metadata,
            compare_type=True, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
        versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if len(versions) != 1:
            raise RuntimeError("Migration must finish at one recognized revision")
        require_revision_catalog(connection, versions[0])


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
