"""Validate the runtime source contract without reading the GCS object."""

import os

from src.services.canonical_facility_store import source_location


def worker_env_argument():
    os.environ["K_SERVICE"] = os.environ.get("WORKER_SERVICE") or "worker"
    uri = source_location()
    delimiter = "|"
    while delimiter in uri:
        delimiter += "|"
    return f"--update-env-vars=^{delimiter}^FACILITY_MASTER_URI={uri}"


if __name__ == "__main__":
    print(worker_env_argument())
