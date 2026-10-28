"""Staging Actions-only transport for reviewed adoption and formal upgrades."""
import os
from pathlib import Path
import subprocess
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from run_stg_menu_master_migration import (
    require_staging_context, cloud_sql_proxy, _load_service_db_config,
    STAGING_INSTANCE, STAGING_DATABASE,
)
from staging_db_target import StagingConfigBlocked, verify_connection_target
from src.maintenance.live_schema_adoption import adopt
from src.maintenance.formal_migration_schema import require_formal_head
from stg_replay_schema_targets import service_configuration, replay_environment, registered_databases


def upgrade_connection(connection):
    tracked = inspect(connection).has_table("alembic_version")
    empty = not inspect(connection).get_table_names()
    connection.rollback()
    if not tracked and not empty:
        receipt = adopt(connection)
        print("Verified staging adoption: " + receipt["baseline"] + " -> " + receipt["revision"])
    else:
        previous = os.environ.get("ALEMBIC_DB_URI")
        os.environ["ALEMBIC_DB_URI"] = connection.engine.url.render_as_string(hide_password=False)
        try:
            config = Config(str(ROOT / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        finally:
            if previous is None:
                os.environ.pop("ALEMBIC_DB_URI", None)
            else:
                os.environ["ALEMBIC_DB_URI"] = previous
    require_formal_head(connection)


def run():
    require_staging_context()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if os.environ.get("GITHUB_SHA") != sha:
        raise RuntimeError("adoption blocked: Actions source SHA mismatch")
    configs = [_load_service_db_config(os.environ["PROJECT_ID"], os.environ["REGION"], os.environ[key])
               for key in ("WEB_SERVICE", "WORKER_SERVICE")]
    for config in configs:
        if (config.instance_connection_name, config.db_name, config.db_user) != (
                STAGING_INSTANCE, STAGING_DATABASE, "orders_app"):
            raise RuntimeError("adoption blocked: unexpected staging target")
    web = configs[0]
    settings = service_configuration(os.environ["WEB_SERVICE"])
    if settings != service_configuration(os.environ["WORKER_SERVICE"]):
        raise RuntimeError("adoption blocked: staging replay configurations differ")
    with cloud_sql_proxy(web.instance_connection_name):
        url = URL.create("postgresql+psycopg2", username=web.db_user, password=web.db_password,
                         host="127.0.0.1", port=5432, database=web.db_name)
        engine = create_engine(url, hide_parameters=True,
                               connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=300000"})
        try:
            with engine.connect() as connection:
                verify_connection_target(connection)
                connection.rollback()
                upgrade_connection(connection)
        finally:
            engine.dispose()
        if settings:
            from src import staging_data

            with replay_environment(settings):
                registry, generation = staging_data.read_registry()
                databases = registered_databases(registry, staging_data.manifest_for)
                for database in databases:
                    replay_url = url.set(database=database)
                    replay_engine = create_engine(replay_url, hide_parameters=True,
                        connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=300000"})
                    try:
                        with replay_engine.connect() as connection:
                            verify_connection_target(connection, database=database)
                            connection.rollback()
                            upgrade_connection(connection)
                        print("Verified registered staging replay schema: " + database)
                    finally:
                        replay_engine.dispose()
                final_registry, final_generation = staging_data.read_registry()
                if generation != final_generation or registry != final_registry:
                    raise RuntimeError("adoption blocked: replay registry changed during migration")


if __name__ == "__main__":
    try:
        run()
    except StagingConfigBlocked as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print("Staging schema adoption/upgrade failed; database details suppressed", file=sys.stderr)
        raise SystemExit(1)
