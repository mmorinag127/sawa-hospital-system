"""Route back to one retained revision; never replace a service or downgrade SQL."""

import json
import os
from pathlib import Path
import subprocess

from hospital_production_preflight import cloud, require_rollback_compatibility
from hospital_release_context import PROJECT, REGION, require_context


def serving(service):
    descriptor = cloud("run", "services", "describe", service, "--region=" + REGION)
    traffic = [item for item in descriptor["status"]["traffic"] if item.get("percent")]
    if len(traffic) != 1 or traffic[0]["percent"] != 100:
        raise RuntimeError("split or unresolved production traffic")
    return traffic[0]["revisionName"]


def main():
    env = os.environ
    if require_context(env) != "prod":
        raise RuntimeError("production dispatch required")
    service = env["TARGET_SERVICE"]
    if service not in {"web-prod", "worker-prod", "ocr-pipeline-prod"}:
        raise RuntimeError("unexpected rollback service")
    record = json.loads((Path(env["RUNNER_TEMP"]) / "rollback/hospital-rollback.json").read_text())
    if any(record[k] != value for k, value in {
        "run": env["ORIGINAL_RUN"], "sha": env["GITHUB_SHA"], "ref": env["GITHUB_REF"],
        "project": PROJECT, "region": REGION,
    }.items()):
        raise RuntimeError("rollback receipt does not identify this release")
    target = record["services"][service]
    rev = cloud("run", "revisions", "describe", target["revision"], "--region=" + REGION)
    meta = rev["metadata"]
    if (meta["uid"] != target["uid"] or meta.get("deletionTimestamp")
            or meta["labels"]["serving.knative.dev/service"] != service
            or not any(c["type"] == "Ready" and c["status"] == "True" for c in rev["status"]["conditions"])):
        raise RuntimeError("recorded rollback revision not available")
    # This release-controlled result must come from the old runtime against the
    # migrated rehearsal DB. Absence is not evidence of backward compatibility.
    require_rollback_compatibility(service, target)
    current = serving(service)
    if current != env["CURRENT_REVISION"] or current == target["revision"]:
        raise RuntimeError("serving revision changed or already restored")
    subprocess.run(["gcloud", "run", "services", "update-traffic", service,
                    "--project=" + PROJECT, "--region=" + REGION,
                    "--to-revisions=" + target["revision"] + "=100", "--quiet"], check=True)
    if serving(service) != target["revision"]:
        raise RuntimeError("traffic restoration not confirmed")


if __name__ == "__main__":
    main()
