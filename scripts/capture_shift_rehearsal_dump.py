"""Capture the serving shift production DB read-only for local migration rehearsal."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote, urlsplit

from capture_hospital_rehearsal_dump import capture


def database():
    project, region = "sawahospitalsystem", "asia-northeast2"
    instance = f"{project}:{region}:shift-prod"
    def cloud(*args):
        return subprocess.check_output(["gcloud", *args, "--project=" + project], text=True)
    service = json.loads(cloud("run", "services", "describe", "shift-api-prod", "--region=" + region, "--format=json"))
    traffic = [v for v in service["status"]["traffic"] if v.get("percent")]
    if len(traffic) != 1 or traffic[0]["percent"] != 100:
        raise RuntimeError("single serving shift revision required")
    revision = json.loads(cloud("run", "revisions", "describe", traffic[0]["revisionName"], "--region=" + region, "--format=json"))
    if (revision["metadata"]["labels"].get("serving.knative.dev/service") != "shift-api-prod"
            or revision["metadata"]["annotations"].get("run.googleapis.com/cloudsql-instances") != instance):
        raise RuntimeError("shift production database attachment mismatch")
    env = [v for v in revision["spec"]["containers"][0]["env"] if v["name"] == "DATABASE_URL"]
    ref = env[0]["valueFrom"]["secretKeyRef"] if len(env) == 1 else {}
    if ref.get("name") != "shift-prod-database-url":
        raise RuntimeError("unexpected shift production database secret")
    uri = urlsplit(cloud("secrets", "versions", "access", ref["key"], "--secret=" + ref["name"]).strip())
    pairs = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
    q = dict(pairs)
    if (len(q) != len(pairs) or set(q) - {"host", "sslmode", "port"} or uri.fragment
            or uri.scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
            or uri.path != "/shift_scheduler" or not uri.username or not uri.password
            or (uri.hostname and "host" in q) or (uri.port and "port" in q)
            or q.get("port", str(uri.port or 5432)) != "5432"):
        raise RuntimeError("unexpected shift production database URI")
    host = q.get("host", uri.hostname)
    if host != "/cloudsql/" + instance:
        descriptor = json.loads(cloud("sql", "instances", "describe", "shift-prod", "--format=json"))
        if descriptor["connectionName"] != instance or ipaddress.ip_address(host) not in {
                ipaddress.ip_address(v["ipAddress"]) for v in descriptor["ipAddresses"]}:
            raise RuntimeError("shift database address mismatch")
    return SimpleNamespace(instance_connection_name=instance, db_name="shift_scheduler",
                           db_user=unquote(uri.username), db_password=unquote(uri.password))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--proxy", type=Path, required=True)
    parser.add_argument("--pg-dump", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    capture(args.directory, args.proxy, args.pg_dump, resolve_database=database)
