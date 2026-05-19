#!/usr/bin/env bash
set -euo pipefail

# Exception-only destructive path. This is not part of the normal prod release flow.

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
INSTANCE="${INSTANCE:-orders-prod}"
DATABASE="${DATABASE:-orders}"
STG_EXPORT_URI="${STG_EXPORT_URI:-}"
PROD_BACKUP_URI="${PROD_BACKUP_URI:-}"
CONFIRM="${CONFIRM:-}"
DROP_SCHEMA="${DROP_SCHEMA:-1}"

if [[ "$CONFIRM" != "RESTORE_PROD_FROM_STG" ]]; then
  echo "Refusing to restore prod DB without CONFIRM=RESTORE_PROD_FROM_STG" >&2
  exit 2
fi
if [[ -z "$STG_EXPORT_URI" || -z "$PROD_BACKUP_URI" ]]; then
  echo "STG_EXPORT_URI and PROD_BACKUP_URI are required" >&2
  exit 2
fi

gcloud storage ls "$STG_EXPORT_URI" >/dev/null
gcloud storage ls "$PROD_BACKUP_URI" >/dev/null

if [[ "$DROP_SCHEMA" == "1" ]]; then
  if [[ ! -x ".release-ops-venv/bin/python" ]]; then
    echo "missing .release-ops-venv; run task release_ops_setup first" >&2
    exit 2
  fi
  CLOUDSQL_CONNECTOR_AUTH="${CLOUDSQL_CONNECTOR_AUTH:-gcloud-token}" \
  .release-ops-venv/bin/python - <<'PY'
import os
import subprocess
from contextlib import closing

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials

PROJECT_ID = os.getenv("PROJECT_ID", "sawahospitalsystem")
INSTANCE = os.getenv("INSTANCE", "orders-prod")
DATABASE = os.getenv("DATABASE", "orders")
CONN = f"{PROJECT_ID}:asia-northeast2:{INSTANCE}"

def secret(name: str) -> str:
    return subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest", f"--secret={name}", f"--project={PROJECT_ID}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

def connector() -> Connector:
    mode = os.getenv("CLOUDSQL_CONNECTOR_AUTH", "gcloud-token")
    if mode in {"gcloud", "gcloud-token", "token"}:
        token = subprocess.run(["gcloud", "auth", "print-access-token"], check=True, capture_output=True, text=True).stdout.strip()
        return Connector(credentials=Credentials(token=token), quota_project=PROJECT_ID)
    return Connector()

c = connector()
try:
    with closing(c.connect(CONN, "pg8000", user="orders_app", password=secret("db-password"), db=DATABASE)) as conn:
        conn.autocommit = True
        with closing(conn.cursor()) as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO orders_app")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
finally:
    c.close()
PY
fi

gcloud sql import sql "$INSTANCE" "$STG_EXPORT_URI" \
  --project="$PROJECT_ID" \
  --database="$DATABASE" \
  --quiet

printf 'restored %s from %s; backup was %s\n' "$INSTANCE" "$STG_EXPORT_URI" "$PROD_BACKUP_URI"
