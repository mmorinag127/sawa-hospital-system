"""Production Actions transport for reviewed adoption, upgrade and read-only preflight."""

import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from hospital_release_context import PROJECT, REGION, require_context
from scripts import portal_prod_db_bootstrap as transport
from run_stg_schema_adoption import upgrade_connection
from src.maintenance.formal_migration_schema import require_formal_head

INSTANCE = PROJECT + ":" + REGION + ":orders-prod"


@contextmanager
def migration_import_target(connection):
    keys = ('DB_URI', 'DATABASE_URL', 'ALEMBIC_DB_URI', 'DB_USER', 'DB_PASSWORD',
            'DB_NAME', 'DB_HOST', 'DB_PORT', 'DB_DRIVER')
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        uri = connection.engine.url.render_as_string(hide_password=False)
        for key in keys[:3]:
            os.environ[key] = uri
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def production_database():
    configs = []
    for name in ("web-prod", "worker-prod"):
        data = transport._run_gcloud_json("run", "services", "describe", name,
                                         "--project=" + PROJECT, "--region=" + REGION)
        traffic = [item for item in data.get("status", {}).get("traffic", []) if item.get("percent", 0)]
        if len(traffic) != 1 or traffic[0].get("percent") != 100:
            raise RuntimeError("unambiguous production traffic required")
        template = data["spec"]["template"]
        revision_name = traffic[0].get("revisionName")
        if not revision_name:
            raise RuntimeError("serving production revision required")
        revision = transport._run_gcloud_json("run", "revisions", "describe", revision_name,
                                             "--project=" + PROJECT, "--region=" + REGION)
        live_entries = revision["spec"]["containers"][0].get("env", [])
        configured_entries = template["spec"]["containers"][0].get("env", [])
        database_fields = {"DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_URI"}
        live_database = {item["name"]: item for item in live_entries if item["name"] in database_fields}
        configured_database = {item["name"]: item for item in configured_entries if item["name"] in database_fields}
        if live_database != configured_database:
            raise RuntimeError("serving and configured production databases differ")
        attached = template.get("metadata", {}).get("annotations", {}).get("run.googleapis.com/cloudsql-instances", "").split(",")
        allowed = {INSTANCE}
        if name == "web-prod":
            allowed.add(PROJECT + ":" + REGION + ":school-lunch-prod")
        if INSTANCE not in attached or not set(attached) <= allowed:
            raise RuntimeError("production database attachment mismatch")
        entries = template["spec"]["containers"][0].get("env", [])
        values = {item["name"]: item.get("value") for item in entries}
        if values.get("DB_URI") or "DB_URI" in values and values["DB_URI"] is None:
            raise RuntimeError("DB_URI override is not supported for migration")
        if values.get("DB_HOST") != "/cloudsql/" + INSTANCE:
            raise RuntimeError("production database host mismatch")
        if values.get("DB_NAME") != "orders" or values.get("DB_USER") != "orders_app":
            raise RuntimeError("production database or role mismatch")
        # Existing bootstrap resolves the credential without logging its value.
        config = transport._load_service_db_config(PROJECT, REGION, name)
        configs.append(config)
    if configs[0].db_password != configs[1].db_password:
        raise RuntimeError("production web/worker database credentials differ")
    return configs[0]


def run(*, verify_only=False):
    if require_context(os.environ) != "prod":
        raise RuntimeError("production release context required")
    if (os.environ.get("WEB_SERVICE"), os.environ.get("WORKER_SERVICE")) != ("web-prod", "worker-prod"):
        raise RuntimeError("production services required")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if sha != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("production checkout SHA mismatch")
    config = production_database()
    engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
        password=config.db_password, host="127.0.0.1", port=5432, database=config.db_name),
        hide_parameters=True, connect_args={"options": "-c lock_timeout=5000 -c statement_timeout=300000"})
    try:
        with engine.connect() as connection:
            if tuple(connection.execute(text("SELECT current_database(), current_user")).one()) != ("orders", "orders_app"):
                raise RuntimeError("connected production database or role mismatch")
            connection.rollback()
            if not verify_only:
                with migration_import_target(connection):
                    upgrade_connection(connection, environment="production")
            connection.rollback()
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                require_formal_head(connection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        run(verify_only=args.verify_only)
    except Exception:
        print("production schema preparation failed; database details suppressed", file=sys.stderr)
        raise SystemExit(1)
    print("production schema migration and read-only preflight passed")
