#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.portal_prod_db_bootstrap import _load_service_db_config  # noqa: E402
from src.services.portal_automation_bootstrap_service import (  # noqa: E402
    PortalAutomationBootstrapError,
    require_automation_context,
    run_portal_automation_bootstrap,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub Actions-only automation user DB bootstrap")
    parser.add_argument("--environment", required=True, choices=("stg", "prod"))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=5432)
    return parser.parse_args()


def _run() -> int:
    args = parse_args()
    require_automation_context(environment=args.environment, email=args.email)
    if args.project_id != "sawahospitalsystem":
        raise PortalAutomationBootstrapError("Cloud Run project must be sawahospitalsystem")
    if args.region != "asia-northeast2":
        raise PortalAutomationBootstrapError("Cloud Run region must be asia-northeast2")
    if args.service not in {f"web-{args.environment}", f"worker-{args.environment}"}:
        raise PortalAutomationBootstrapError("Cloud Run service does not match environment")
    config = _load_service_db_config(args.project_id, args.region, args.service)
    engine = create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=config.db_user,
            password=config.db_password,
            host=args.db_host,
            port=args.db_port,
            database=config.db_name,
        ),
        future=True,
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            result = run_portal_automation_bootstrap(
                connection, environment=args.environment, email=args.email
            )
    finally:
        engine.dispose()
    print(json.dumps({"environment": args.environment, "service": args.service, **result}))
    return 0


def main() -> int:
    try:
        return _run()
    except PortalAutomationBootstrapError as exc:
        # Dedicated bootstrap errors contain only developer-controlled diagnostics.
        print(f"blocked: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Driver and gcloud exceptions may contain credentials or connection URLs.
        print(f"blocked: automation DB bootstrap failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
