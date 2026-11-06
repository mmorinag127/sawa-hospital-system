"""Read-only hospital production snapshot for an isolated local rehearsal."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from run_prod_schema_adoption import production_database
from hospital_snapshot_fingerprints import fingerprints
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from src import staging_copy_schema as preservation


def capture(directory, proxy, pg_dump, *, resolve_database=production_database, include_settings=False):
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
    settings_digest = None
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
            if include_settings:
                origin = "https://worker-prod-avlnzjjrca-dt.a.run.app"
                access = subprocess.run(["gcloud", "auth", "print-access-token"],
                    capture_output=True, text=True, check=True).stdout.strip()
                identity = "worker-exec-stg@sawahospitalsystem.iam.gserviceaccount.com"
                token_request = Request("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                    + identity + ":generateIdToken", method="POST",
                    headers={"Authorization": "Bearer " + access, "Content-Type": "application/json"},
                    data=json.dumps({"audience": origin, "includeEmail": True}).encode())
                with urlopen(token_request, timeout=30) as response:
                    token = json.load(response)["token"]
                descriptor = json.loads(subprocess.check_output(["gcloud", "run", "services", "describe",
                    "worker-prod", "--project=sawahospitalsystem", "--region=asia-northeast2", "--format=json"]))
                tags = [row for row in descriptor["status"]["traffic"] if row.get("tag") == "staging-source-api"]
                source_url = "https://staging-source-api---worker-prod-avlnzjjrca-dt.a.run.app"
                if len(tags) != 1 or tags[0].get("url") != source_url:
                    raise RuntimeError("existing production settings export target unavailable")
                request = Request(source_url + "/internal/staging-source-settings?" + urlencode({"snapshot": snapshot}),
                                  headers={"Authorization": "Bearer " + token})
                with urlopen(request, timeout=120) as response:
                    settings = json.load(response)
                if settings.get("database_snapshot") != snapshot or settings.get("schema") != 1:
                    raise RuntimeError("settings capture snapshot mismatch")
                if settings.get("revision") != tags[0].get("revisionName"):
                    raise RuntimeError("settings capture revision mismatch")
                documents = settings.get("settings", {})
                if set(documents) != {"FACILITY_MASTER_URI", "INGEST_POLICY_URI", "OCR_PIPELINE_STATE_URI"}:
                    raise RuntimeError("settings capture incomplete")
                for document in documents.values():
                    if isinstance(document, dict):
                        raw = base64.b64decode(document["content_base64"], validate=True)
                        if hashlib.sha256(raw).hexdigest() != document.get("sha256"):
                            raise RuntimeError("settings document digest mismatch")
                        if not isinstance(json.loads(raw), dict):
                            raise RuntimeError("settings document must be an object")
                    elif not isinstance(document, str) or not document.startswith((
                        "gs://sawahospitalsystem-prod-raw/", "gs://sawahospitalsystem-prod-templates/"
                    )):
                        raise RuntimeError("settings production source mismatch")
                settings_raw = (json.dumps(settings, indent=2) + "\n").encode()
                (directory / "settings.json").write_bytes(settings_raw)
                settings_digest = hashlib.sha256(settings_raw).hexdigest()
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
            **({"settings_sha256": settings_digest} if settings_digest else {}),
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
    parser.add_argument("--include-settings", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    capture(args.directory, args.proxy, args.pg_dump, include_settings=args.include_settings)
