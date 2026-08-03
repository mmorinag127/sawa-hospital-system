#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.services.portal_access_bootstrap_service import (  # noqa: E402
    BOOTSTRAP_ACTOR,
    PortalAccessBootstrapError,
    run_portal_access_bootstrap_gate,
)


@dataclass(frozen=True)
class ServiceDbConfig:
    instance_connection_name: str
    db_name: str
    db_user: str
    db_password: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Production-only portal DB migration/bootstrap gate. "
            "Must run from GitHub Actions after production environment approval."
        )
    )
    parser.add_argument("--project-id", default=os.getenv("PROJECT_ID", "sawahospitalsystem"))
    parser.add_argument("--region", default=os.getenv("REGION", "asia-northeast2"))
    parser.add_argument("--service", default=os.getenv("WEB_SERVICE", "web-prod"))
    parser.add_argument(
        "--bootstrap-admin-email",
        default=os.getenv("PORTAL_BOOTSTRAP_ADMIN_EMAIL", ""),
    )
    parser.add_argument(
        "--deploy-verification-email",
        default=os.getenv("PORTAL_DEPLOY_VERIFICATION_EMAIL", ""),
    )
    parser.add_argument("--db-host", default=os.getenv("PORTAL_BOOTSTRAP_DB_HOST", "127.0.0.1"))
    parser.add_argument("--db-port", default=os.getenv("PORTAL_BOOTSTRAP_DB_PORT", "5432"))
    parser.add_argument("--actor", default=os.getenv("PORTAL_BOOTSTRAP_ACTOR", BOOTSTRAP_ACTOR))
    return parser.parse_args()


def _require_ci_cd_prod_context() -> None:
    if os.getenv("GITHUB_ACTIONS") != "true" or not os.getenv("GITHUB_RUN_ID"):
        raise SystemExit(
            "blocked: portal_prod_db_bootstrap.py must run from GitHub Actions after production approval"
        )
    ref_name = str(os.getenv("GITHUB_REF_NAME") or "").strip()
    if not ref_name.startswith("release/prod-"):
        raise SystemExit(
            f"blocked: portal_prod_db_bootstrap.py requires release/prod-*; got {ref_name or 'missing'}"
        )


def _run_gcloud_json(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["gcloud", *args, "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _load_secret(project_id: str, secret_name: str, version: str | None = None) -> str:
    version_token = str(version or "latest").strip() or "latest"
    completed = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            version_token,
            f"--secret={secret_name}",
            f"--project={project_id}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _extract_container(service_data: dict[str, Any]) -> dict[str, Any]:
    containers = (
        service_data.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if not containers:
        raise PortalAccessBootstrapError("Cloud Run service has no containers")
    return containers[0]


def _extract_secret_ref(env_entry: dict[str, Any]) -> tuple[str, str | None] | None:
    candidates = [
        env_entry.get("valueFrom", {}).get("secretKeyRef", {}),
        env_entry.get("valueSource", {}).get("secretKeyRef", {}),
    ]
    for candidate in candidates:
        secret_name = str(
            candidate.get("name")
            or candidate.get("secret")
            or candidate.get("secretName")
            or ""
        ).strip()
        secret_version = str(
            candidate.get("key")
            or candidate.get("version")
            or candidate.get("secretVersion")
            or ""
        ).strip()
        if secret_name:
            return secret_name, secret_version or None
    return None


def _load_service_db_config(project_id: str, region: str, service: str) -> ServiceDbConfig:
    service_data = _run_gcloud_json(
        "run",
        "services",
        "describe",
        service,
        f"--project={project_id}",
        f"--region={region}",
    )
    annotations = (
        service_data.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("annotations", {})
    )
    instance_connection_name = str(
        annotations.get("run.googleapis.com/cloudsql-instances") or ""
    ).strip()
    if not instance_connection_name:
        raise PortalAccessBootstrapError("Cloud Run service is missing the Cloud SQL instance annotation")

    container = _extract_container(service_data)
    env_map: dict[str, str] = {}
    secret_refs: dict[str, tuple[str, str | None]] = {}
    for entry in container.get("env", []):
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if "value" in entry:
            env_map[name] = str(entry.get("value") or "")
            continue
        secret_ref = _extract_secret_ref(entry)
        if secret_ref:
            secret_refs[name] = secret_ref

    db_name = str(env_map.get("DB_NAME") or "").strip()
    db_user = str(env_map.get("DB_USER") or "").strip()
    if not db_name or not db_user:
        raise PortalAccessBootstrapError("Cloud Run DB_NAME and DB_USER env vars are required")

    db_password = str(env_map.get("DB_PASSWORD") or "").strip()
    if not db_password and "DB_PASSWORD" in secret_refs:
        secret_name, secret_version = secret_refs["DB_PASSWORD"]
        db_password = _load_secret(project_id, secret_name, secret_version)
    if not db_password:
        secret_name = str(env_map.get("DB_PASSWORD_SECRET") or "").strip()
        if secret_name:
            db_password = _load_secret(project_id, secret_name)
    if not db_password:
        raise PortalAccessBootstrapError(
            "DB password could not be resolved from Cloud Run env or Secret Manager"
        )

    return ServiceDbConfig(
        instance_connection_name=instance_connection_name,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
    )


def main() -> int:
    _require_ci_cd_prod_context()
    args = parse_args()
    config = _load_service_db_config(args.project_id, args.region, args.service)
    engine = create_engine(
        "postgresql+psycopg2://"
        f"{quote_plus(config.db_user)}:{quote_plus(config.db_password)}"
        f"@{args.db_host}:{args.db_port}/{config.db_name}",
        future=True,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_use_lifo=True,
    )
    try:
        with engine.begin() as connection:
            result = run_portal_access_bootstrap_gate(
                connection,
                bootstrap_admin_email=args.bootstrap_admin_email,
                deploy_verification_email=args.deploy_verification_email,
                actor=args.actor,
            )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "service": args.service,
                "instance_connection_name": config.instance_connection_name,
                "bootstrap_admin_configured": bool(str(args.bootstrap_admin_email or "").strip()),
                "deploy_verification_email_configured": bool(
                    str(args.deploy_verification_email or "").strip()
                ),
                **result.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
