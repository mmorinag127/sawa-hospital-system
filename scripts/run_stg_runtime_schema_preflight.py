#!/usr/bin/env python3
"""Staging Actions-only, read-only runtime schema preflight."""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

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


BASE_TABLE = "base_menu_cycle_items"
BASE_COLUMNS = (
    "id", "cycle_day", "daypart", "category", "name", "diet_type", "slot_index",
)
FACILITY_TABLE = "facility_template_versions"
FACILITY_COLUMNS = (
    "id", "facility_id", "version", "status", "template_id", "source", "config_json",
    "columns_json", "cells_json", "template_digest", "validation_json", "valid_from",
    "valid_to", "created_by", "created_at", "activated_at", "archived_at",
)
FACILITY_INDEXES = (
    "ix_facility_template_versions_valid_from",
    "ix_facility_template_versions_valid_to",
)


def _columns(connection, table: str) -> set[str]:
    return set(connection.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :table_name"
    ), {"table_name": table}).scalars().all())


def _require_columns(connection, table: str, required: tuple[str, ...]) -> None:
    actual = _columns(connection, table)
    if not actual:
        raise MigrationBlocked(f"runtime schema preflight blocked: missing table {table}")
    missing = sorted(set(required) - actual)
    if missing:
        raise MigrationBlocked(
            f"runtime schema preflight blocked: missing columns in {table}: {', '.join(missing)}"
        )


def validate_catalog(connection) -> None:
    """Reject incomplete migrated schemas without changing them."""
    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    verify_connection_target(connection)
    _require_columns(connection, BASE_TABLE, BASE_COLUMNS)
    _require_columns(connection, FACILITY_TABLE, FACILITY_COLUMNS)
    indexes = set(connection.execute(text(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = current_schema() AND tablename = :table_name"
    ), {"table_name": FACILITY_TABLE}).scalars().all())
    missing = sorted(set(FACILITY_INDEXES) - indexes)
    if missing:
        raise MigrationBlocked(
            "runtime schema preflight blocked: missing indexes in "
            f"{FACILITY_TABLE}: {', '.join(missing)}"
        )


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
