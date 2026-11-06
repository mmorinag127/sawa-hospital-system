"""Read-only hospital production snapshot for an isolated local rehearsal."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from run_prod_schema_adoption import production_database
from hospital_snapshot_fingerprints import fingerprints
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from src import staging_copy_schema as preservation


def capture(directory, proxy, pg_dump, *, resolve_database=production_database):
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("local read-only capture required")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    config = resolve_database()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([str(proxy.resolve(strict=True)), "--address=127.0.0.1",
                                "--port=" + str(port), config.instance_connection_name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    engine = None
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("read-only capture proxy stopped")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError("read-only capture proxy unavailable")
        engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
            password=config.db_password, host="127.0.0.1", port=port, database=config.db_name),
            hide_parameters=True, connect_args={"options": "-c default_transaction_read_only=on"})
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                raise RuntimeError("read-only snapshot required")
            snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            columns = preservation.columns(connection)
            original = fingerprints(connection, columns)
            env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
            env.update(PGHOST="127.0.0.1", PGPORT=str(port), PGUSER=config.db_user,
                       PGPASSWORD=config.db_password, PGDATABASE=config.db_name,
                       PGOPTIONS="-c default_transaction_read_only=on")
            dump = directory / "production.dump"
            result = subprocess.run([str(pg_dump.resolve(strict=True)), "--format=custom",
                "--no-owner", "--no-acl", "--snapshot=" + snapshot, "--file=" + str(dump)],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=1200)
            if result.returncode:
                raise RuntimeError("read-only dump failed; credential details suppressed")
        with dump.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        (directory / "source.json").write_text(json.dumps({
            "instance": config.instance_connection_name, "database": config.db_name,
            "dump_sha256": digest, "tables": original, "production_read_only": True,
            "fingerprint_format": "sha256-sorted-row-sha256-v1",
        }, indent=2) + "\n")
        print(str(directory / "source.json"))
    finally:
        if engine is not None:
            engine.dispose()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--proxy", type=Path, required=True)
    parser.add_argument("--pg-dump", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    capture(args.directory, args.proxy, args.pg_dump)
