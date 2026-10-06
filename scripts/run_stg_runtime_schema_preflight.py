#!/usr/bin/env python3
"""Staging Actions-only, read-only runtime schema preflight."""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from src.maintenance.formal_migration_schema import require_formal_head

from run_stg_menu_master_migration import (
    MigrationBlocked,
    STAGING_DATABASE,
    STAGING_INSTANCE,
    cloud_sql_proxy,
    require_staging_context,
)
from staging_db_target import (
    StagingConfigBlocked,
    load_service_db_config as _load_service_db_config,
    verify_connection_target,
)


def validate_catalog(connection) -> None:
    """Reject incomplete migrated schemas without changing them."""
    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    verify_connection_target(connection)
    try:
        require_formal_head(connection)
    except RuntimeError as exc:
        raise MigrationBlocked(f"runtime schema preflight blocked: {exc}") from exc


def run() -> None:
    require_staging_context()
    web = _load_service_db_config(
        os.environ["PROJECT_ID"], os.environ["REGION"], os.environ["WEB_SERVICE"]
    )
    worker = _load_service_db_config(
        os.environ["PROJECT_ID"], os.environ["REGION"], os.environ["WORKER_SERVICE"]
    )
    if (web.instance_connection_name, web.db_name, web.db_user) != (
        worker.instance_connection_name, worker.db_name, worker.db_user,
    ):
        raise MigrationBlocked("runtime schema preflight blocked: staging web and worker database targets differ")
    if (web.instance_connection_name, web.db_name, web.db_user) != (
        STAGING_INSTANCE, STAGING_DATABASE, "orders_app",
    ):
        raise MigrationBlocked("runtime schema preflight blocked: verified orders-stg target required")

    with cloud_sql_proxy(web.instance_connection_name):
        engine = create_engine(URL.create(
            "postgresql+psycopg2", username=web.db_user, password=web.db_password,
            host="127.0.0.1", port=5432, database=web.db_name,
        ), hide_parameters=True, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    validate_catalog(connection)
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()


def main() -> int:
    try:
        run()
    except (MigrationBlocked, StagingConfigBlocked) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("runtime schema preflight failed: cloud/DB details suppressed", file=sys.stderr)
        return 1
    print("runtime schema preflight passed: required migrated schema is read-ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
