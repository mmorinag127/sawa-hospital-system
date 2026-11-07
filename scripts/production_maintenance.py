"""Actions-only planned maintenance; never restore a database or reopen on failure."""

import json
import copy
import os
from pathlib import Path
import time
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import urlopen

from hospital_production_preflight import cloud, revision_images
from hospital_release_context import PROJECT, REGION, require_context

WRITERS = ("worker-prod", "ocr-pipeline-prod")
SERVICES = ("web-prod", *WRITERS)
QUEUES = {"hospital-ingest-prod", "hospital-workflow-ocr-prod"}
SCHEDULES = {"hospital-ingest-drain-prod", "hospital-workflow-ocr-drain-prod",
             "ingest-retry-prod", "shipping-tracking-refresh-prod"}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def context():
    require(require_context(os.environ) == "prod", "production Actions required")
    require([item.get("account") for item in cloud("auth", "list", "--filter=status:ACTIVE")] ==
            [f"sawa-github-deploy-prod@{PROJECT}.iam.gserviceaccount.com"],
            "production deploy identity required")
    return Path(os.environ["RUNNER_TEMP"]) / "hospital-maintenance.json"


def serving(service, allowed_tag=None):
    traffic = service.get("status", {}).get("traffic", [])
    require(not any(item.get("tag") and item["tag"] != allowed_tag for item in traffic),
            "tagged routes require explicit review")
    active = [item for item in traffic if item.get("percent")]
    require(len(active) == 1 and active[0].get("percent") == 100,
            "one serving revision required")
    return active[0]["revisionName"]


def save(path, record):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def mutation(path, record, operation, *args):
    entry = {"operation": operation, "state": "started"}
    record.setdefault("operations", []).append(entry)
    save(path, record)
    result = cloud(*args)
    entry["state"] = "returned"
    save(path, record)
    return result


def download_record(phase):
    require(require_context(os.environ) == "prod", "production context required")
    require(phase in {"start", "database"}, "unknown maintenance phase")
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"]
    prefix = "hospital-maintenance-" + ("start-" if phase == "start" else "") + run + "-"
    output = subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/actions/runs/{run}/artifacts?per_page=100"], text=True)
    artifacts = [item for page in json.loads(output) for item in page["artifacts"]
                 if item["name"].startswith(prefix) and item["name"][len(prefix):].isdigit()
                 and not item.get("expired")]
    require(artifacts, "same-run maintenance artifact missing")
    selected = max(artifacts, key=lambda item: int(item["name"][len(prefix):]))
    require(int(selected["name"][len(prefix):]) <= int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "future attempt artifact rejected")
    destination = Path(os.environ["RUNNER_TEMP"])
    with tempfile.TemporaryDirectory(dir=destination) as directory:
        subprocess.run(["gh", "run", "download", run, "--repo", repository,
                        "--name", selected["name"], "--dir", directory], check=True,
                       capture_output=True)
        record = json.loads((Path(directory) / "hospital-maintenance.json").read_text())
        require(record.get("run") == run and record.get("sha") == os.environ["GITHUB_SHA"]
                and record.get("ref") == os.environ["GITHUB_REF"], "artifact source mismatch")
        if phase == "start":
            rollback = json.loads((Path(directory) / "hospital-rollback.json").read_text())
            require(rollback.get("run") == run and rollback.get("sha") == record["sha"]
                    and rollback.get("services") == record["services"], "original rollback mismatch")
            save(destination / "hospital-rollback.json", rollback)
        save(destination / "hospital-maintenance.json", record)


def capture():
    path = context()
    require(not path.exists(), "never replace an existing maintenance record")
    inventory = cloud("run", "services", "list", "--region=" + REGION)
    attached = {item["metadata"]["name"] for item in inventory
                if f"{PROJECT}:{REGION}:orders-prod" in item.get("spec", {}).get("template", {})
                .get("metadata", {}).get("annotations", {}).get("run.googleapis.com/cloudsql-instances", "").split(",")}
    require(attached == set(SERVICES), "unreviewed database consumer service")
    from restore_hospital_database import assert_database_inventory
    assert_database_inventory()
    record = {"run": os.environ["GITHUB_RUN_ID"], "sha": os.environ["GITHUB_SHA"],
              "ref": os.environ["GITHUB_REF"], "services": {}, "queues": {},
              "schedules": {}, "writers_stopped": False, "database_drained": False,
              "application_checks_passed": False, "reopened": False}
    for name in SERVICES:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        require(not service["metadata"].get("labels", {}).get("sawa-maintenance-run"),
                "existing maintenance requires original-run recovery; never recapture it")
        if name == "web-prod":
            containers = service["spec"]["template"]["spec"]["containers"]
            require(containers and containers[0].get("ports")
                    and not any(item.get("ports") for item in containers[1:]),
                    "portal ingress container must be unambiguous")
        revision = serving(service)
        mode = service["metadata"].get("annotations", {}).get("run.googleapis.com/scalingMode", "automatic")
        require(mode == "automatic", "unexpected service scaling before maintenance")
        detail = cloud("run", "revisions", "describe", revision, "--region=" + REGION)
        require(any(item.get("type") == "Ready" and item.get("status") == "True"
                    for item in detail.get("status", {}).get("conditions", [])), "original revision not ready")
        record["services"][name] = {"revision": revision, "uid": detail["metadata"]["uid"],
                                    "images": revision_images(detail), "scaling": "auto"}
    rollback = json.loads((path.parent / "hospital-rollback.json").read_text())
    require(rollback.get("run") == record["run"] and rollback.get("sha") == record["sha"]
            and rollback.get("services") == record["services"],
            "preflight rollback targets changed before maintenance")
    for kind, names, field in (("tasks", QUEUES, "queues"), ("scheduler", SCHEDULES, "schedules")):
        resource = "queues" if kind == "tasks" else "jobs"
        for item in cloud(kind, resource, "list", "--location=" + REGION):
            name = item["name"].split("/")[-1]
            if name in names:
                require(item.get("state") in {"RUNNING", "ENABLED", "PAUSED"}, "unexpected transport state")
                record[field][name] = item["state"]
    save(path, record)
    return record


def load():
    path = context()
    record = json.loads(path.read_text())
    require(all(record.get(key) == os.environ[env] for key, env in
                (("run", "GITHUB_RUN_ID"), ("sha", "GITHUB_SHA"), ("ref", "GITHUB_REF"))),
            "maintenance record belongs to another run")
    require(set(record["services"]) == set(SERVICES), "maintenance service inventory mismatch")
    return path, record


def stop_writers():
    path, record = load()
    verify_display()
    for name in WRITERS:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        require(serving(service) == record["services"][name]["revision"], "writer revision changed")
    for field, kind, resource in (("queues", "tasks", "queues"), ("schedules", "scheduler", "jobs")):
        for name in record[field]:
            current = cloud(kind, resource, "describe", name, "--location=" + REGION)
            if current.get("state") != "PAUSED":
                mutation(path, record, "pause:" + name, kind, resource, "pause", name, "--location=" + REGION)
            require(cloud(kind, resource, "describe", name, "--location=" + REGION).get("state") == "PAUSED",
                    "transport pause not confirmed")
    for name in WRITERS:
        mutation(path, record, "stop:" + name, "run", "services", "update", name,
                 "--scaling=0", "--region=" + REGION, "--quiet")
    assert_stopped()
    record["writers_stopped"] = True
    save(path, record)


def assert_stopped():
    for name in WRITERS:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        serving(service)
        annotations = service["metadata"].get("annotations", {})
        require(annotations.get("run.googleapis.com/scalingMode") == "manual"
                and str(annotations.get("run.googleapis.com/manualInstanceCount")) == "0",
                "writer is not stopped")


def drain(engine, timeout=1800):
    from sqlalchemy import text
    path, record = load()
    require(record["writers_stopped"], "writers must be stopped before drain")
    assert_stopped()
    deadline = time.monotonic() + timeout
    with engine.connect() as connection:
        require(connection.execute(text("SELECT current_database()")).scalar_one() == "orders",
                "unexpected maintenance database")
        while True:
            remaining = connection.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() AND backend_type='client backend'"
            )).scalar_one()
            connection.rollback()
            if remaining == 0:
                break
            require(time.monotonic() < deadline, "database clients did not drain")
            time.sleep(5)
    assert_stopped()
    record["database_drained"] = True
    save(path, record)


def show(image):
    path, record = load()
    require(image.startswith(f"{REGION}-docker.pkg.dev/{PROJECT}/backend/") and "@sha256:" in image,
            "immutable maintenance image required")
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service) == record["services"]["web-prod"]["revision"], "portal revision changed")
    replacement = copy.deepcopy(service)
    replacement.pop("status", None)
    metadata = replacement["metadata"]
    metadata.setdefault("labels", {})["sawa-maintenance-run"] = os.environ["GITHUB_RUN_ID"]
    for key in ("creationTimestamp", "generation", "resourceVersion", "selfLink", "uid"):
        metadata.pop(key, None)
    template = replacement["spec"]["template"]
    template["metadata"]["name"] = "web-prod-maint-" + record["run"]
    record["maintenance_revision"] = template["metadata"]["name"]
    save(path, record)
    containers = template["spec"]["containers"]
    container = containers[0]
    # Sidecars must not keep database connections or start business work during maintenance.
    template["spec"]["containers"] = [container]
    template["metadata"].get("annotations", {}).pop("run.googleapis.com/container-dependencies", None)
    container["image"] = image
    container["command"] = ["node"]
    container["args"] = ["/sawa-maintenance/server.cjs"]
    container["startupProbe"] = {"httpGet": {"path": "/healthz", "port": 8080}}
    container.pop("livenessProbe", None)
    replacement["spec"]["traffic"] = [{"latestRevision": True, "percent": 100}]
    target = path.with_name("maintenance-service.json")
    target.write_text(json.dumps(replacement))
    target.chmod(0o600)
    try:
        mutation(path, record, "show-maintenance", "run", "services", "replace", str(target),
                 "--region=" + REGION, "--quiet")
    finally:
        target.unlink(missing_ok=True)
    observed = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    revision = serving(observed)
    require(revision == record["maintenance_revision"], "maintenance revision identity mismatch")
    record["maintenance_visible"] = True
    save(path, record)
    verify_display()


def restore_frontend_entrypoint(service):
    _, record = load()
    require(record.get("portal_bootstrap_complete") is True, "completed bootstrap required")
    require(serving(service) == record["maintenance_revision"], "maintenance portal changed")
    original = record["services"]["web-prod"]
    revision = cloud("run", "revisions", "describe", original["revision"], "--region=" + REGION)
    require(revision["metadata"]["uid"] == original["uid"] and revision_images(revision) == original["images"],
            "original portal revision changed")
    old = revision["spec"]["containers"]
    require(old and old[0].get("ports"), "original ingress container missing")
    service["spec"]["template"]["spec"]["containers"] = copy.deepcopy(old)
    annotations = service["spec"]["template"]["metadata"].setdefault("annotations", {})
    dependencies = revision["metadata"].get("annotations", {}).get("run.googleapis.com/container-dependencies")
    if dependencies is not None:
        annotations["run.googleapis.com/container-dependencies"] = dependencies
    return service


def verify_display():
    _, record = load()
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service, record.get("candidate_tag")) == record.get("maintenance_revision"),
            "maintenance traffic changed")
    for route in ("/", "/login", "/api/auth/me"):
        try:
            response = urlopen(service["status"]["url"] + route, timeout=60)
        except HTTPError as error:
            response = error
        with response:
            require(response.code == 503 and response.headers.get("X-Sawa-Maintenance") == "1",
                    "maintenance response not confirmed")


def candidate_url():
    path, record = load()
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service, record.get("candidate_tag")) == record["maintenance_revision"],
            "maintenance traffic must remain active")
    revision = service["status"]["latestReadyRevisionName"]
    require(revision not in {record["maintenance_revision"], record["services"]["web-prod"]["revision"]},
            "new candidate revision required")
    expected = sorted([os.environ["IMAGE"], os.environ["SAWA_AGENT_RUNTIME_IMAGE"]])
    require(all("@sha256:" in image for image in expected), "immutable candidate images required")
    detail = cloud("run", "revisions", "describe", revision, "--region=" + REGION)
    require(sorted(revision_images(detail)) == expected, "candidate images differ from release")
    tag = "verify-" + record["run"]
    record.update(candidate_revision=revision, candidate_tag=tag,
                  candidate_images=expected, candidate_uid=detail["metadata"]["uid"])
    save(path, record)
    cloud("run", "services", "update-traffic", "web-prod", "--set-tags=" + tag + "=" + revision,
          "--to-revisions=" + record["maintenance_revision"] + "=100", "--region=" + REGION, "--quiet")
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(serving(service, tag) == record["maintenance_revision"], "candidate changed public traffic")
    tagged = [item for item in service["status"]["traffic"] if item.get("tag") == tag]
    require(len(tagged) == 1 and tagged[0].get("revisionName") == revision
            and not tagged[0].get("percent"), "candidate route mismatch")
    url = tagged[0]["url"]
    require(url.startswith("https://") and url.endswith(".a.run.app"), "unexpected candidate URL")
    record["candidate_url"] = url
    save(path, record)
    verify_display()
    return url


def promote():
    path, record = load()
    verify_display()
    require(record.get("writers_verified_open") is True, "writer activation must be verified")
    require(record.get("candidate_revision") and record.get("candidate_url"), "verified candidate required")
    service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
    require(service["status"]["latestReadyRevisionName"] == record["candidate_revision"],
            "candidate changed before promotion")
    detail = cloud("run", "revisions", "describe", record["candidate_revision"], "--region=" + REGION)
    require(detail["metadata"]["uid"] == record["candidate_uid"]
            and sorted(revision_images(detail)) == record["candidate_images"], "candidate identity changed")
    cloud("run", "services", "update-traffic", "web-prod",
          "--to-revisions=" + record["candidate_revision"] + "=100",
          "--remove-tags=" + record["candidate_tag"], "--region=" + REGION, "--quiet")
    require(serving(cloud("run", "services", "describe", "web-prod", "--region=" + REGION))
            == record["candidate_revision"], "promotion not confirmed")
    record["candidate_checks_passed"] = True
    save(path, record)


def hold_on_failure():
    path, record = load()
    if record.get("reopened"):
        return
    maintenance = record.get("maintenance_revision")
    require(maintenance, "maintenance revision is not recorded; inspect original run")
    failures = []
    try:
        service = cloud("run", "services", "describe", "web-prod", "--region=" + REGION)
        current = serving(service, record.get("candidate_tag"))
    except Exception:
        current = None
        failures.append("portal-inspection")
    if current is not None and current not in {maintenance, record.get("candidate_revision")}:
        current = None
        failures.append("unrelated-portal-change")
    arguments = ["run", "services", "update-traffic", "web-prod",
                 "--to-revisions=" + maintenance + "=100", "--region=" + REGION, "--quiet"]
    if record.get("candidate_tag"):
        arguments.append("--remove-tags=" + record["candidate_tag"])
    try:
        require(current is not None, "portal ownership could not be checked")
        mutation(path, record, "failure-hold-portal", *arguments)
        cloud("run", "services", "update", "web-prod", "--update-labels=sawa-maintenance-run=" + record["run"],
              "--region=" + REGION, "--quiet")
        verify_display()
    except Exception:
        failures.append("portal-maintenance")
    for name in WRITERS:
        try:
            mutation(path, record, "failure-stop:" + name, "run", "services", "update", name,
                     "--scaling=0", "--region=" + REGION, "--quiet")
        except Exception:
            failures.append(name)
    try:
        assert_stopped()
    except Exception:
        failures.append("writer-stop-verification")
    record["failure_hold_unconfirmed"] = failures
    record["held_after_failure"] = not failures
    save(path, record)
    require(not failures, "maintenance recovery incomplete: " + ", ".join(failures))


def resume_writer(name, expected_image):
    _, record = load()
    require(name in WRITERS and record["database_drained"], "verified maintenance required")
    service = cloud("run", "services", "describe", name, "--region=" + REGION)
    revision_name = serving(service)
    require(revision_name != record["services"][name]["revision"], "never reopen original writer on migrated DB")
    revision = cloud("run", "revisions", "describe", revision_name, "--region=" + REGION)
    images = [item["image"] for item in revision["spec"]["containers"]]
    require(expected_image and (expected_image in images or expected_image in revision_images(revision)),
            "new writer image mismatch")
    require(all(any(entry.get("name") == "SAWA_RELEASE_READ_ONLY" and entry.get("value") == "1"
                    for entry in container.get("env", []))
                for container in revision["spec"]["containers"]), "writer read-only gate missing")
    annotations = service["metadata"].get("annotations", {})
    require(annotations.get("run.googleapis.com/scalingMode") == "manual"
            and str(annotations.get("run.googleapis.com/manualInstanceCount")) == "0",
            "writer was not held stopped")
    cloud("run", "services", "update", name, "--scaling=auto", "--region=" + REGION, "--quiet")
    observed = cloud("run", "services", "describe", name, "--region=" + REGION)
    require(serving(observed) == revision_name,
            "writer traffic changed while reopening")
    require(observed["metadata"].get("annotations", {}).get("run.googleapis.com/scalingMode", "automatic") == "automatic",
            "writer automatic scaling not confirmed")


def open_writers():
    path, record = load()
    verify_display()
    require(record.get("candidate_url") and record.get("portal_bootstrap_complete"),
            "checked portal candidate and database bootstrap required")
    candidate = cloud("run", "revisions", "describe", record["candidate_revision"], "--region=" + REGION)
    require(candidate["metadata"]["uid"] == record["candidate_uid"]
            and sorted(revision_images(candidate)) == record["candidate_images"], "candidate identity changed")
    from registry_image_digest import resolve_image_digest
    expected = {name: resolve_image_digest(os.environ[key]) for name, key in (
        ("worker-prod", "PRODUCTION_BACKEND_IMAGE"), ("ocr-pipeline-prod", "PRODUCTION_PRODUCER_IMAGE"))}
    for name in WRITERS:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        revision = serving(service)
        require(revision != record["services"][name]["revision"], "original writer must not reopen")
        detail = cloud("run", "revisions", "describe", revision, "--region=" + REGION)
        images = revision_images(detail)
        require(images == [expected[name]], "writer is not the verified release image")
        containers = detail["spec"]["containers"]
        require(len(containers) == 1 and any(entry.get("name") == "SAWA_RELEASE_READ_ONLY"
                and entry.get("value") == "1" for entry in containers[0].get("env", [])),
                "read-only writer required before opening")
        mutation(path, record, "open:" + name, "run", "services", "update", name,
                 "--remove-env-vars=SAWA_RELEASE_READ_ONLY", "--region=" + REGION, "--quiet")
        observed = cloud("run", "services", "describe", name, "--region=" + REGION)
        ready = cloud("run", "revisions", "describe", serving(observed), "--region=" + REGION)
        require(revision_images(ready) == images, "writer image changed while opening")
        require(any(item.get("type") == "Ready" and item.get("status") == "True"
                    for item in ready.get("status", {}).get("conditions", [])), "opened writer is not ready")
        require(not any(entry.get("name") == "SAWA_RELEASE_READ_ONLY"
                    for container in ready["spec"]["containers"] for entry in container.get("env", [])),
                "writer read-only gate removal not confirmed")
    record["writers_verified_open"] = True
    save(path, record)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("capture", "stop", "show", "verify-display", "candidate-url", "promote", "hold-on-failure", "open-writers", "download-start", "download-database", "drain", "bootstrap-complete", "require-bootstrap", "resume-writer"))
    parser.add_argument("--image")
    parser.add_argument("--service")
    args = parser.parse_args()
    if args.action.startswith("download-"):
        download_record(args.action.removeprefix("download-"))
    elif args.action == "capture":
        capture()
    elif args.action == "stop":
        stop_writers()
    elif args.action == "show":
        show(args.image or "")
    elif args.action == "verify-display":
        verify_display()
    elif args.action == "candidate-url":
        print(candidate_url())
    elif args.action == "promote":
        promote()
    elif args.action == "open-writers":
        open_writers()
    elif args.action == "hold-on-failure":
        hold_on_failure()
    elif args.action == "resume-writer":
        resume_writer(args.service, args.image)
    elif args.action in {"bootstrap-complete", "require-bootstrap"}:
        path, record = load()
        require(record.get("database_drained") and record.get("maintenance_revision"), "verified maintenance required")
        if args.action == "bootstrap-complete":
            assert_stopped()
            record["portal_bootstrap_complete"] = True
            save(path, record)
        else:
            require(record.get("portal_bootstrap_complete") is True, "portal bootstrap not completed")
    else:
        from run_prod_schema_adoption import production_database
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        config = production_database()
        engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
            password=config.db_password, host="127.0.0.1", port=5432, database="orders"),
            hide_parameters=True, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"})
        try:
            drain(engine)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
