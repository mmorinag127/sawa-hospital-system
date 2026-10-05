#!/usr/bin/env python3
"""Staging Actions-only transport for the explicit menu-master migration."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from apply_menu_master_revision_migration import MigrationBlocked, upgrade_staging  # noqa: E402
from staging_db_target import (  # noqa: E402
    StagingConfigBlocked, load_service_db_config as _load_service_db_config, verify_connection_target,
)


# Same pinned binary/checksum as bootstrap_automation_user.sh; no mutable latest URL.
PROXY_URL = "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.24.1/cloud-sql-proxy.linux.amd64"
PROXY_SHA256 = "fae2766aac9d614a2bdef2f2a7778f3d054f3acd5ff07a81a9e300bd471512eb"
STAGING_INSTANCE = "sawahospitalsystem:asia-northeast2:orders-stg"
STAGING_DATABASE = "orders"


def require_staging_context() -> None:
    required = {
        "GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/develop", "GITHUB_REF_NAME": "develop",
        "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2",
        "WEB_SERVICE": "web-stg", "WORKER_SERVICE": "worker-stg",
    }
    for name, expected in required.items():
        if os.environ.get(name) != expected:
            raise MigrationBlocked(f"0027 blocked: staging Actions requires {name}={expected}")
    if not re.fullmatch(r"[1-9][0-9]*", os.environ.get("GITHUB_RUN_ID", "")):
        raise MigrationBlocked("0027 blocked: staging Actions run id required")


@contextmanager
def cloud_sql_proxy(instance):
    # Refuse an existing listener rather than treating another process as our proxy.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 5432))
    with tempfile.TemporaryDirectory(prefix="stg-menu-proxy-") as directory:
        binary = Path(directory) / "cloud-sql-proxy"
        with urllib.request.urlopen(PROXY_URL, timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != PROXY_SHA256:
            raise MigrationBlocked("0027 blocked: Cloud SQL proxy checksum mismatch")
        binary.write_bytes(content)
        binary.chmod(0o700)
        process = subprocess.Popen(
            [str(binary), "--address", "127.0.0.1", "--port", "5432", instance],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(30):
                if process.poll() is not None:
                    raise MigrationBlocked("0027 blocked: Cloud SQL proxy exited before readiness")
                try:
                    with socket.create_connection(("127.0.0.1", 5432), timeout=1):
                        if process.poll() is None:
                            break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise MigrationBlocked("0027 blocked: Cloud SQL proxy readiness timeout")
            yield
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run() -> None:
    require_staging_context()
    web = _load_service_db_config(os.environ["PROJECT_ID"], os.environ["REGION"], os.environ["WEB_SERVICE"])
    worker = _load_service_db_config(os.environ["PROJECT_ID"], os.environ["REGION"], os.environ["WORKER_SERVICE"])
    if (web.instance_connection_name, web.db_name, web.db_user) != (worker.instance_connection_name, worker.db_name, worker.db_user):
        raise MigrationBlocked("0027 blocked: staging web and worker database targets differ")
    if web.instance_connection_name != STAGING_INSTANCE or web.db_name != STAGING_DATABASE or web.db_user != "orders_app":
        raise MigrationBlocked("0027 blocked: verified orders-stg instance and orders database required")
    with cloud_sql_proxy(web.instance_connection_name):
        engine = create_engine(URL.create(
            "postgresql+psycopg2", username=web.db_user, password=web.db_password,
            host="127.0.0.1", port=5432, database=web.db_name,
        ), hide_parameters=True, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                verify_connection_target(connection)
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(text("SET LOCAL statement_timeout = '60s'"))
                upgrade_staging(connection)
        finally:
            engine.dispose()


def main() -> int:
    try:
        run()
    except (MigrationBlocked, StagingConfigBlocked) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # gcloud/config, proxy and database errors may include secrets; never relay them.
        print("0027 failed: staging migration error; cloud/DB details suppressed", file=sys.stderr)
        return 1
    print("0027 staging gate passed: schema verified and revision applied or already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
