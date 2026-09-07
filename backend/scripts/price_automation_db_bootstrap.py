#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.portal_automation_db_bootstrap import parse_args
from scripts.portal_prod_db_bootstrap import _load_service_db_config
from src.services.portal_access_bootstrap_service import PortalAccessBootstrapError
from src.services.price_automation_bootstrap_service import require_price_automation_context, run_price_automation_bootstrap


def main() -> int:
    try:
        args = parse_args()
        require_price_automation_context(environment=args.environment, email=args.email)
        if args.project_id != "sawahospitalsystem" or args.region != "asia-northeast2" or args.service not in {"web-stg", "worker-stg"}:
            raise PortalAccessBootstrapError("Staging project, region and service are required")
        config = _load_service_db_config(args.project_id, args.region, args.service)
        engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
            password=config.db_password, host=args.db_host, port=args.db_port, database=config.db_name), pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                result = run_price_automation_bootstrap(connection, environment=args.environment, email=args.email)
        finally:
            engine.dispose()
        print(json.dumps({"environment": args.environment, **result}))
        return 0
    except PortalAccessBootstrapError as error:
        print(f"blocked: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"blocked: price automation bootstrap failed ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
