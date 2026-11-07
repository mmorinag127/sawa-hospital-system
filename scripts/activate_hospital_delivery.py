"""Enable prepared production transports only after same-run application checks."""
import json
import os
from pathlib import Path

from hospital_delivery_config import FLOWS, REGION, bootstrap, gcloud, require_equal
from hospital_release_context import require_context
from hospital_production_preflight import revision_images


def run():
    require_equal(require_context(os.environ), "prod", "production dispatch")
    maintenance = os.environ.get("PRODUCTION_MAINTENANCE") == "1"
    if maintenance:
        from production_maintenance import load, save, mutation
        path, record = load()
        require_equal(record.get("candidate_checks_passed"), True, "candidate application checks")
        require_equal(record.get("writers_verified_open"), True, "verified writer activation")

    def resume(group, name):
        if maintenance:
            mutation(path, record, "resume:" + name, *group, "resume", name, "--location=" + REGION)
        else:
            gcloud(*group, "resume", name, "--location=" + REGION)

    bootstrap(allow_paused=True)
    from registry_image_digest import resolve_image_digest
    for service, key in (("web-prod", "PRODUCTION_FRONTEND_IMAGE"),
                         ("worker-prod", "PRODUCTION_BACKEND_IMAGE"),
                         ("ocr-pipeline-prod", "PRODUCTION_PRODUCER_IMAGE")):
        expected = resolve_image_digest(os.environ[key])
        descriptor = gcloud("run", "services", "describe", service, "--region=" + REGION)
        traffic = [row for row in descriptor.get("status", {}).get("traffic", []) if row.get("percent")]
        require_equal(len(traffic), 1, "single serving revision")
        require_equal(traffic[0].get("percent"), 100, "full serving revision")
        revision = gcloud("run", "revisions", "describe", traffic[0]["revisionName"], "--region=" + REGION)
        images = revision_images(revision)
        require_equal(expected in images, True, "same-run immutable image")
        if service == "web-prod":
            require_equal(sorted(images), sorted([expected, os.environ["PRODUCTION_AGENT_IMAGE"]]),
                          "same-run web and agent images")
        else:
            require_equal(images, [expected], "single expected runtime image")
        require_equal(any(row.get("type") == "Ready" and row.get("status") == "True"
                          for row in revision.get("status", {}).get("conditions", [])), True, "ready revision")
    changed = []
    try:
        for flow, _ in FLOWS:
            for group, name, active in (
                (("tasks", "queues"), f"hospital-{flow}-prod", "RUNNING"),
                (("scheduler", "jobs"), f"hospital-{flow}-drain-prod", "ENABLED"),
            ):
                state = gcloud(*group, "describe", name, "--location=" + REGION).get("state")
                if state == active:
                    continue
                require_equal(state, "PAUSED", "prepared transport")
                changed.append((group, name))
                resume(group, name)
                require_equal(gcloud(*group, "describe", name, "--location=" + REGION).get("state"),
                              active, "activated transport")
        bootstrap()
        if maintenance:
            # Existing schedules outside the new delivery flows retain their original state.
            prepared = {f"hospital-{flow}-drain-prod" for flow, _ in FLOWS}
            for name, original in record["schedules"].items():
                if name in prepared or original != "ENABLED":
                    continue
                state = gcloud("scheduler", "jobs", "describe", name, "--location=" + REGION).get("state")
                require_equal(state, "PAUSED", "maintenance schedule remains paused")
                changed.append((("scheduler", "jobs"), name))
                resume(("scheduler", "jobs"), name)
                require_equal(gcloud("scheduler", "jobs", "describe", name, "--location=" + REGION).get("state"),
                              "ENABLED", "original schedule restored")
            gcloud("run", "services", "update", "web-prod", "--remove-labels=sawa-maintenance-run",
                   "--region=" + REGION, "--quiet")
            descriptor = gcloud("run", "services", "describe", "web-prod", "--region=" + REGION)
            require_equal(bool(descriptor.get("metadata", {}).get("labels", {}).get("sawa-maintenance-run")),
                          False, "maintenance marker cleared")
            record["application_checks_passed"] = True
            record["reopened"] = True
            save(path, record)
    except Exception:
        failures = []
        for group, name in reversed(changed):
            try:
                gcloud(*group, "pause", name, "--location=" + REGION)
                require_equal(gcloud(*group, "describe", name, "--location=" + REGION).get("state"),
                              "PAUSED", "activation failure cleanup")
            except Exception:
                failures.append(name)
        (Path(os.environ["RUNNER_TEMP"]) / "hospital-activation-failure.json").write_text(
            json.dumps({"unconfirmed_paused": failures,
                        "resume_attempted": [name for _, name in changed],
                        "completed_deliveries_reverted": False}) + "\n")
        raise


if __name__ == "__main__":
    try:
        run()
    except Exception:
        raise SystemExit("production delivery activation failed; inspect the failure receipt") from None
