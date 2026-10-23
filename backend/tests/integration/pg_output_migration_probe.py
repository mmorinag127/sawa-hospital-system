"""Observe a formal migration's pre-rollback catalog without changing it."""

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine


def main():
    # Reject targets before importing any production module or connecting.
    expected = (
        "postgresql+psycopg2://sawa_output_test:isolated-test-only"
        "@127.0.0.1:5432/sawa_modernization_output_test"
    )
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI") or any(
        os.environ.get(key) != expected for key in
        ("MODERNIZATION_PG_TEST_URI", "DB_URI", "DATABASE_URL", "ALEMBIC_DB_URI")
    ):
        raise RuntimeError("migration probe requires the exact isolated local test target")
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ["RUNNER_TEMP"]).resolve() / "pg-output-boundary"
    destination = args.evidence.resolve()
    if not destination.is_relative_to(root) or destination.exists():
        raise RuntimeError("migration probe requires a new owned evidence path")

    from src.maintenance.formal_migration_schema import (
        CONTRACT, formal_head_revision, read_catalog, table_shapes,
    )

    observing = False

    def before_rollback(connection):
        nonlocal observing
        if observing or connection.engine.url != sa.engine.make_url(expected):
            return
        observing = True
        try:
            head = formal_head_revision()
            contract = json.loads(CONTRACT.read_text())
            reference = contract["revisions"]["postgresql"][head]["tables"]["bags"]
            expected_shape = contract["shapes"][reference]
            actual = table_shapes(read_catalog(connection), "postgresql")
            failure = sys.exc_info()[1]
            evidence = {
                "phase": "engine rollback event before DBAPI rollback",
                "head": head,
                "exception": None if failure is None else {
                    "type": type(failure).__name__, "message": str(failure),
                },
                "sqlalchemy_version": sa.__version__,
                "postgresql_version": connection.exec_driver_sql("SELECT version()").scalar_one(),
                "postgresql_version_num": connection.exec_driver_sql("SHOW server_version_num").scalar_one(),
                "expected_bags_indexes": expected_shape["indexes"],
                "actual_bags_indexes": actual.get("bags", {}).get("indexes"),
                "expected_bags_native": expected_shape["native"],
                "actual_bags_native": actual.get("bags", {}).get("native"),
            }
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as output:
                json.dump(evidence, output, ensure_ascii=False, indent=2)
                output.write("\n")
        except Exception:
            # Never turn an observer failure into a replacement migration error.
            print("migration rollback observer failed:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            observing = False

    event.listen(Engine, "rollback", before_rollback)
    try:
        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    finally:
        event.remove(Engine, "rollback", before_rollback)


if __name__ == "__main__":
    main()
