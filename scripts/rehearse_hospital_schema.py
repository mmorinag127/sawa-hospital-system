"""Restore a reviewed dump into an owned local database and rehearse adoption."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests/integration")]

from live_schema_adoption_support import owned_postgres, target
from run_stg_schema_adoption import upgrade_connection
from hospital_snapshot_fingerprints import fingerprints
from src import staging_copy_schema as preservation


def sequences(connection):
    quote = connection.dialect.identifier_preparer.quote_identifier
    names = connection.exec_driver_sql(
        "SELECT schemaname, sequencename FROM pg_sequences "
        "WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema' ORDER BY 1,2"
    ).all()
    return {schema + '.' + name: tuple(connection.exec_driver_sql(
        'SELECT last_value, is_called FROM ' + quote(schema) + '.' + quote(name)).one())
        for schema, name in names}


def restore_owned(directory, dump):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGHOST=str(directory / "socket"), PGPORT="55495",
               PGUSER="adoption_test", PGDATABASE="postgres")
    with (directory / "restore.log").open("w") as log:
        subprocess.run([
            str(Path(os.environ["OWNED_POSTGRES_PG_BIN"]) / "pg_restore"),
            "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
            "--dbname=postgres", str(dump),
        ], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    return env


def run(dump, source_receipt, *, verify_restore=False):
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
        raise RuntimeError("local rehearsal only")
    dump = dump.resolve(strict=True)
    source_receipt = json.loads(source_receipt.read_text())
    if (source_receipt.get('instance') != 'sawahospitalsystem:asia-northeast2:orders-prod'
            or source_receipt.get('database') != 'orders'
            or source_receipt.get('production_read_only') is not True
            or source_receipt.get('fingerprint_format') != 'sha256-sorted-row-sha256-v1'):
        raise RuntimeError('verified production snapshot receipt required')
    with dump.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != source_receipt.get('dump_sha256'):
        raise RuntimeError("reviewed dump checksum mismatch")
    started = time.monotonic()
    with owned_postgres() as (engine, directory):
        # The destination is created by owned_postgres, never supplied by the caller.
        env = restore_owned(directory, dump)
        restored = time.monotonic()
        with target(engine.url), engine.connect() as connection:
            original_columns = preservation.columns(connection)
            before = fingerprints(connection, original_columns)
            if before != source_receipt.get('tables'):
                raise RuntimeError('restored values differ from production snapshot')
            sequence_before = sequences(connection)
            connection.rollback()
            upgrade_connection(connection, environment="local production-copy rehearsal")
            connection.rollback()
            if fingerprints(connection, original_columns) != before:
                raise RuntimeError('migration changed existing restored values')
            sequence_after = sequences(connection)
            if any(sequence_after.get(name) != value for name, value in sequence_before.items()):
                raise RuntimeError('migration changed an existing restored sequence')
        migrated = time.monotonic()
        if verify_restore:
            migrated_dump = directory / "post-migration.dump"
            with engine.connect() as connection:
                migrated_columns = preservation.columns(connection)
                migrated_values = fingerprints(connection, migrated_columns)
                migrated_sequences = sequences(connection)
            with (directory / "dump.log").open("w") as log:
                subprocess.run([
                    str(Path(os.environ["OWNED_POSTGRES_PG_BIN"]) / "pg_dump"),
                    "--format=custom", "--no-owner", "--no-acl",
                    "--file=" + str(migrated_dump), "postgres",
                ], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        receipt = {
            "dump_sha256": digest,
            "source_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "worktree_dirty": bool(subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
            "restore_and_start_seconds": round(restored - started, 3),
            "migration_seconds": round(migrated - restored, 3),
            "formal_head_verified": True,
            "existing_values_preserved": True,
            "restored_sequences_preserved": True,
            "source_sequence_snapshot_consistent": False,
            "preserved_tables": before,
            "production_modified": False,
        }
        (directory / "rehearsal.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if verify_restore:
        recovery_started = time.monotonic()
        checks = (
            ("post_migration_backup", migrated_dump, migrated_columns,
             migrated_values, migrated_sequences),
            ("pre_migration_restore", dump, original_columns, before, sequence_before),
        )
        for name, backup, expected_columns, expected_values, expected_sequences in checks:
            with owned_postgres() as (recovered, recovery_directory):
                restore_owned(recovery_directory, backup)
                with recovered.connect() as connection:
                    if (preservation.columns(connection) != expected_columns
                            or fingerprints(connection, expected_columns) != expected_values
                            or sequences(connection) != expected_sequences):
                        raise RuntimeError(name + " does not preserve the captured database")
                receipt[name] = {"verified": True, "directory": str(recovery_directory)}
        receipt["recovery_rehearsal_seconds"] = round(time.monotonic() - recovery_started, 3)
        receipt["cloud_sql_restore_verified"] = False
        receipt["old_runtime_verified"] = False
        (directory / "rehearsal.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(str(directory / "rehearsal.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--verify-restore", action="store_true",
                        help="Also verify migrated backup and original restore in fresh local DBs")
    args = parser.parse_args()
    os.umask(0o077)
    run(args.dump, args.source_receipt, verify_restore=args.verify_restore)
