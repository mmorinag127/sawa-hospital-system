#!/usr/bin/env python3
"""Apply the facility-template-version lineage schema to an existing database.

This is intentionally idempotent because existing services may already have
created the new table via SQLAlchemy metadata while the legacy tables still
lack the added lineage columns.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

import asyncpg


LINEAGE_TABLES: Sequence[str] = (
    "orders",
    "ocr_jobs",
    "order_ocr_evidence_runs",
    "order_sheet_drafts",
    "order_confirmed_snapshots",
    "order_workflow_states",
    "order_current_states",
)

FACILITY_TEMPLATE_VERSION_SQL = """
CREATE TABLE IF NOT EXISTS facility_template_versions (
    id VARCHAR PRIMARY KEY,
    facility_id VARCHAR NOT NULL REFERENCES facilities(id),
    version VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'draft',
    template_id VARCHAR NULL,
    source VARCHAR NULL,
    columns_json JSON NOT NULL,
    cells_json JSON NULL,
    template_digest VARCHAR NOT NULL,
    validation_json JSON NULL,
    created_by VARCHAR NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    activated_at TIMESTAMP WITHOUT TIME ZONE NULL,
    archived_at TIMESTAMP WITHOUT TIME ZONE NULL
);
CREATE INDEX IF NOT EXISTS ix_facility_template_versions_facility_id
    ON facility_template_versions (facility_id);
CREATE INDEX IF NOT EXISTS ix_facility_template_versions_status
    ON facility_template_versions (status);
CREATE INDEX IF NOT EXISTS ix_facility_template_versions_template_digest
    ON facility_template_versions (template_digest);
"""


def _env_or_arg(value: str | None, env_name: str) -> str:
    resolved = value or os.getenv(env_name) or ""
    if not resolved:
        raise SystemExit(f"{env_name} is required")
    return resolved


async def _apply(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(
        host=_env_or_arg(args.host, "DB_HOST"),
        port=int(args.port or os.getenv("DB_PORT") or "5432"),
        database=_env_or_arg(args.database, "DB_NAME"),
        user=_env_or_arg(args.user, "DB_USER"),
        password=_env_or_arg(args.password, "DB_PASSWORD"),
        timeout=float(args.timeout),
    )
    try:
        async with conn.transaction():
            await conn.execute(FACILITY_TEMPLATE_VERSION_SQL)
            for table_name in LINEAGE_TABLES:
                constraint_name = f"fk_{table_name}_template_version_id"
                index_name = f"ix_{table_name}_template_version_id"
                await conn.execute(
                    f"ALTER TABLE {table_name} "
                    "ADD COLUMN IF NOT EXISTS template_version_id VARCHAR NULL"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} (template_version_id)"
                )
                await conn.execute(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint WHERE conname = '{constraint_name}'
                        ) THEN
                            ALTER TABLE {table_name}
                            ADD CONSTRAINT {constraint_name}
                            FOREIGN KEY (template_version_id)
                            REFERENCES facility_template_versions(id);
                        END IF;
                    END $$;
                    """
                )

        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND column_name = 'template_version_id'
              AND table_name = ANY($1::text[])
            ORDER BY table_name
            """,
            list(LINEAGE_TABLES),
        )
        print(f"template_version_id columns: {len(rows)} / {len(LINEAGE_TABLES)}")
        if len(rows) != len(LINEAGE_TABLES):
            present = {row["table_name"] for row in rows}
            missing = [table for table in LINEAGE_TABLES if table not in present]
            raise SystemExit(f"missing template_version_id columns: {', '.join(missing)}")
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host")
    parser.add_argument("--port")
    parser.add_argument("--database")
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--timeout", default="10")
    args = parser.parse_args()
    asyncio.run(_apply(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
