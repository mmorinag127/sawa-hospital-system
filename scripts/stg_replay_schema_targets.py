"""Resolve only registered hospital replay databases on the existing stg instance."""
from contextlib import contextmanager
import os
import re

from staging_dataset_config import KEYS, configuration
from staging_db_target import PROJECT, REGION, StagingConfigBlocked, transport


def service_configuration(service):
    if service not in {"web-stg", "worker-stg"}:
        raise StagingConfigBlocked("replay-schema-service-rejected")
    descriptor = transport._run_gcloud_json("run", "services", "describe", service,
        "--project=" + PROJECT, "--region=" + REGION)
    values = {}
    for entry in descriptor["spec"]["template"]["spec"]["containers"][0].get("env", []):
        if entry.get("name") in KEYS:
            if entry["name"] in values or set(entry) != {"name", "value"}:
                raise StagingConfigBlocked("replay-schema-config-source-rejected")
            values[entry["name"]] = entry["value"]
    return configuration(values)


def migration_configuration(web, worker):
    if web and web != worker:
        raise StagingConfigBlocked("replay-schema-service-config-mismatch")
    return worker


@contextmanager
def replay_environment(settings):
    names = (*KEYS, "K_SERVICE", "APP_ENV")
    previous = {name: os.environ.get(name) for name in names}
    try:
        os.environ.update(settings, K_SERVICE="worker-stg", APP_ENV="stg")
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def registered_databases(registry, read_manifest):
    from src import staging_data

    staging_data.validate_registry(registry)
    databases = set()
    for identity in registry["snapshots"]:
        manifest = read_manifest(identity)
        if manifest.get("id") != identity:
            raise StagingConfigBlocked("replay-schema-snapshot-identity-mismatch")
        database = manifest["components"]["order"]["database"]
        if not isinstance(database, str) or re.fullmatch(r"sawa_replay_[a-z0-9_]{1,40}", database) is None:
            raise StagingConfigBlocked("replay-schema-database-rejected")
        databases.add(database)
    return sorted(databases)
