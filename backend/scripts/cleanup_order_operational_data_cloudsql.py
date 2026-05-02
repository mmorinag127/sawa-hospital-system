#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if sys.version_info < (3, 11):
    sys.stderr.write("cleanup_order_operational_data_cloudsql.py requires Python 3.11+.\n")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


PROJECT_ID = "sawahospitalsystem"
DEFAULT_STG_CONN = "sawahospitalsystem:asia-northeast2:orders-stg"
DEFAULT_DB_NAME = "orders"
DEFAULT_DB_USER = "orders_app"
DEFAULT_STG_SECRET = "db-password-stg"


def _parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    return date.fromisoformat(token)


def _load_secret(secret_name: str) -> str:
    env_key = f"{secret_name.upper().replace('-', '_')}_VALUE"
    existing = str(os.getenv(env_key) or "").strip()
    if existing:
        return existing
    completed = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={secret_name}",
            f"--project={PROJECT_ID}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_gcloud_access_token() -> str:
    completed = subprocess.run(
        [
            "gcloud",
            "auth",
            "print-access-token",
            f"--project={PROJECT_ID}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _configure_cloudsql_session(args: argparse.Namespace) -> Connector:
    credentials = Credentials(token=_load_gcloud_access_token())
    connector = Connector(credentials=credentials, quota_project=PROJECT_ID)
    password = _load_secret(args.secret)

    def getconn() -> Any:
        return connector.connect(
            args.instance,
            "pg8000",
            user=args.db_user,
            password=password,
            db=args.db_name,
        )

    engine = create_engine(
        "postgresql+pg8000://",
        creator=getconn,
        future=True,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_use_lifo=True,
    )

    import src.db as db

    db.engine = engine
    db.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return connector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run by default. Run order operational cleanup against Cloud SQL without deleting "
            "facility/menu/template configuration."
        )
    )
    parser.add_argument("--instance", default=os.getenv("CLOUDSQL_INSTANCE", DEFAULT_STG_CONN))
    parser.add_argument("--db-name", default=os.getenv("DB_NAME", DEFAULT_DB_NAME))
    parser.add_argument("--db-user", default=os.getenv("DB_USER", DEFAULT_DB_USER))
    parser.add_argument("--secret", default=os.getenv("DB_PASSWORD_SECRET", DEFAULT_STG_SECRET))
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all-orders", action="store_true", help="Target every order row.")
    scope.add_argument("--received-from", help="Target orders received on or after YYYY-MM-DD.")
    parser.add_argument("--received-to", help="Target orders received on or before YYYY-MM-DD.")
    parser.add_argument("--export-pdfs-dir", help="Copy target order PDFs before cleanup and write manifest.json.")
    parser.add_argument("--apply", action="store_true", help="Actually delete targeted order data.")
    parser.add_argument(
        "--confirm",
        default="",
        help="Required with --apply. Must be CLEAN_ORDER_DATA.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    connector = _configure_cloudsql_session(args)
    try:
        from src.services.order_operational_cleanup_service import (
            CleanupScope,
            apply_order_cleanup,
            build_order_cleanup_plan,
            export_order_pdfs_for_cleanup,
        )

        cleanup_scope = CleanupScope(
            all_orders=bool(args.all_orders),
            received_from=_parse_date(args.received_from),
            received_to=_parse_date(args.received_to),
        )

        plan = build_order_cleanup_plan(cleanup_scope)
        result: dict[str, object] = {
            "mode": "apply" if args.apply else "dry_run",
            "cleanup_plan": plan,
        }

        if args.export_pdfs_dir:
            result["pdf_export"] = export_order_pdfs_for_cleanup(
                scope=cleanup_scope,
                output_dir=args.export_pdfs_dir,
            )

        if args.apply:
            result["cleanup_result"] = apply_order_cleanup(
                cleanup_scope,
                confirm_token=str(args.confirm or ""),
            )

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        connector.close()


if __name__ == "__main__":
    raise SystemExit(main())
