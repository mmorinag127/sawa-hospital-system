#!/usr/bin/env python3
"""Explicitly apply migration 0027; runtime code never invokes this helper."""

import argparse
import importlib.util
from pathlib import Path
import sys

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine


def upgrade(connection) -> None:
    source = Path(__file__).resolve().parents[1] / "backend/migrations/0027_menu_master_revision.py"
    spec = importlib.util.spec_from_file_location("menu_master_revision_migration", source)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    except migration.RevisionSchemaError as exc:
        raise MigrationBlocked(str(exc)) from None


class MigrationBlocked(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-uri-stdin", action="store_true", required=True,
                        help="Read the URI from stdin; never put credentials in argv")
    parser.parse_args()
    engine = None
    try:
        uri = sys.stdin.read().strip()
        if not uri:
            raise MigrationBlocked("0027 blocked: stdin DB URI is empty")
        engine = create_engine(uri, hide_parameters=True)
        with engine.begin() as connection:
            upgrade(connection)
    except MigrationBlocked as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Driver/parser errors can contain credentials; do not print their text or traceback.
        print("0027 failed: connection or migration error; URI and driver details suppressed", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print("0027 complete: revision schema applied or already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
