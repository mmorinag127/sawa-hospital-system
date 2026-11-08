"""Validate the runtime source contract without reading the GCS object."""

import os
import json
import sys

from src.services.canonical_facility_store import source_location


def worker_env_argument():
    os.environ["K_SERVICE"] = os.environ.get("WORKER_SERVICE") or "worker"
    uri = source_location()
    delimiter = "|"
    while delimiter in uri:
        delimiter += "|"
    return f"--update-env-vars=^{delimiter}^FACILITY_MASTER_URI={uri}"


def verify_revision_source(revision):
    expected = source_location()
    values = [entry.get("value") for container in revision["spec"]["containers"]
              for entry in container.get("env", []) if entry.get("name") == "FACILITY_MASTER_URI"]
    if values != [expected]:
        raise ValueError("deployed facility master differs from approved workflow source")


if __name__ == "__main__":
    if sys.argv[1:] == ["--verify-revision"]:
        verify_revision_source(json.load(sys.stdin))
    elif len(sys.argv) == 1:
        print(worker_env_argument())
    else:
        raise ValueError("unsupported facility source verification argument")
