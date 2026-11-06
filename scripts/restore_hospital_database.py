"""Owner-dispatched database recovery; keep services disabled on any failure."""

import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import Request, urlopen

from hospital_production_preflight import cloud, revision_images
from hospital_release_context import PROJECT, REGION, require_context

SERVICES = ("web-prod", "worker-prod", "ocr-pipeline-prod")
INSTANCE = "orders-prod"
QUEUES = {"hospital-ingest-prod", "hospital-workflow-ocr-prod"}
JOBS = {"hospital-ingest-drain-prod", "hospital-workflow-ocr-drain-prod",
        "ingest-retry-prod", "shipping-tracking-refresh-prod"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def serving(service):
    traffic = [row for row in service.get("status", {}).get("traffic", []) if row.get("percent")]
    require(len(traffic) == 1 and traffic[0].get("percent") == 100,
            "one serving revision is required")
    return traffic[0]["revisionName"]


def check_target(name, target):
    revision = cloud("run", "revisions", "describe", target["revision"], "--region=" + REGION)
    metadata = revision["metadata"]
    require(metadata.get("uid") == target["uid"] and not metadata.get("deletionTimestamp")
            and metadata.get("labels", {}).get("serving.knative.dev/service") == name,
            "retained revision identity changed")
    require(revision_images(revision) == target["images"],
            "retained revision image changed")
    require(any(row.get("type") == "Ready" and row.get("status") == "True"
                for row in revision.get("status", {}).get("conditions", [])),
            "retained revision is not ready")


def backup_record(ident):
    require(re.fullmatch(r"[1-9][0-9]*", str(ident)) is not None, "backup ID invalid")
    result = cloud("sql", "backups", "describe", str(ident), "--instance=" + INSTANCE)
    require(result.get("status") == "SUCCESSFUL" and result.get("instance") == INSTANCE,
            "successful backup of orders-prod required")
    return result


def assert_disabled():
    for name in SERVICES:
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        annotations = service["metadata"].get("annotations", {})
        require(annotations.get("run.googleapis.com/scalingMode") == "manual"
                and str(annotations.get("run.googleapis.com/manualInstanceCount")) == "0",
                "service not disabled")
        require(not any(row.get("tag") for row in service.get("status", {}).get("traffic", [])),
                "tagged revision remains reachable")


def assert_current(current):
    for name in SERVICES:
        require(serving(cloud("run", "services", "describe", name, "--region=" + REGION)) == current[name],
                "serving revision changed during recovery preparation")


def assert_database_inventory():
    databases = cloud("sql", "databases", "list", "--instance=" + INSTANCE)
    require({row["name"] for row in databases} <= {"orders", "postgres", "template0", "template1"},
            "instance contains another database; instance-wide restoration forbidden")
    jobs = cloud("run", "jobs", "list", "--region=" + REGION)
    for job in jobs:
        # Reject attached jobs even when idle: an external scheduler could start one.
        template = job.get("spec", {}).get("template", {}).get("spec", {}).get("template", {})
        attached = template.get("metadata", {}).get("annotations", {}).get("run.googleapis.com/cloudsql-instances", "")
        require(PROJECT + ":" + REGION + ":" + INSTANCE not in attached.split(","),
                "database consumer job requires a separate quiescence plan")


def restore_backup_configuration(expected, instance):
    actual = instance.get("settings", {}).get("backupConfiguration")
    if actual == expected:
        return
    require(isinstance(expected, dict) and expected.get("enabled") is True,
            "original enabled backup policy required")
    token = subprocess.run(["gcloud", "auth", "print-access-token"],
                           capture_output=True, text=True, check=True).stdout.strip()
    request = Request(
        f"https://sqladmin.googleapis.com/v1/projects/{PROJECT}/instances/{INSTANCE}",
        method="PATCH", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        data=json.dumps({"settings": {
            "settingsVersion": instance["settings"]["settingsVersion"],
            "backupConfiguration": {key: value for key, value in expected.items()
                                    if key not in {"transactionalLogStorageState", "backupTier"}},
        }}).encode())
    with urlopen(request, timeout=60) as response:
        operation = json.load(response)
    require(operation.get("targetId") == INSTANCE and operation.get("name"),
            "backup policy operation target mismatch")
    cloud("sql", "operations", "wait", operation["name"], "--timeout=1800")
    observed = cloud("sql", "instances", "describe", INSTANCE)
    require(observed.get("settings", {}).get("backupConfiguration") == expected,
            "original backup policy was not restored")


def run():
    env = os.environ
    require(require_context(env) == "prod", "production Actions required")
    require(env.get("DATABASE_RECOVERY_CONFIRMATION") == "RESTORE_ORDERS_PROD_DATABASE",
            "explicit destructive database recovery confirmation required")
    active = cloud("auth", "list", "--filter=status:ACTIVE")
    require([row.get("account") for row in active] ==
            [f"sawa-github-deploy-prod@{PROJECT}.iam.gserviceaccount.com"],
            "production deploy identity required")
    root = Path(env["RUNNER_TEMP"])
    receipt = json.loads((root / "rollback/hospital-rollback.json").read_text())
    require(all(receipt.get(key) == value for key, value in {
        "run": env["ORIGINAL_RUN"], "sha": env["GITHUB_SHA"], "ref": env["GITHUB_REF"],
        "project": PROJECT, "region": REGION,
    }.items()), "rollback receipt belongs to another release")
    require(receipt.get("rollback_strategy") == "restore_database",
            "original deployment did not select database restoration")
    original = json.loads((root / "rollback/orders-prod-recovery-point.json").read_text())
    baseline = backup_record(original["id"])
    require(baseline.get("description") == f"release-{env['GITHUB_SHA']}-{env['ORIGINAL_RUN']}",
            "backup does not belong to original deployment")
    current = json.loads(env["CURRENT_REVISIONS_JSON"])
    require(set(current) == set(SERVICES) == set(receipt["services"]), "service inventory mismatch")
    instance = cloud("sql", "instances", "describe", INSTANCE)
    require(instance.get("databaseVersion", "").startswith("POSTGRES_")
            and not instance.get("replicaNames"), "unsupported database topology")
    assert_database_inventory()
    inventory = cloud("run", "services", "list", "--region=" + REGION)
    attached = {item["metadata"]["name"] for item in inventory
                if PROJECT + ":" + REGION + ":" + INSTANCE in item.get("spec", {}).get("template", {})
                .get("metadata", {}).get("annotations", {}).get("run.googleapis.com/cloudsql-instances", "").split(",")}
    require(attached == set(SERVICES), "unreviewed database consumer service")
    scaling = {}
    original_traffic = {}
    for name in SERVICES:
        check_target(name, receipt["services"][name])
        service = cloud("run", "services", "describe", name, "--region=" + REGION)
        require(serving(service) == current[name], "serving revision changed since approval")
        annotations = service["metadata"].get("annotations", {})
        require(receipt["services"][name].get("scaling") == "auto",
                "original automatic scaling must be recorded by the release")
        mode = annotations.get("run.googleapis.com/scalingMode", "automatic")
        require(mode == "automatic" or (mode == "manual" and
                str(annotations.get("run.googleapis.com/manualInstanceCount")) == "0"),
                "unexpected current scaling; only automatic or stopped recovery is supported")
        scaling[name] = receipt["services"][name]["scaling"]
        original_traffic[name] = service.get("status", {}).get("traffic", [])
    # Resolve credentials before disabling traffic; never put them in artifacts.
    from run_prod_schema_adoption import production_database
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    config = production_database()
    engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
        password=config.db_password, host="127.0.0.1", port=5432, database="orders"),
        hide_parameters=True, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"})
    try:
        with engine.connect() as connection:
            require(tuple(connection.execute(text("SELECT current_database(), current_user")).one()) ==
                    ("orders", "orders_app"), "recovery connection target mismatch")
        queues = cloud("tasks", "queues", "list", "--location=" + REGION)
        jobs = cloud("scheduler", "jobs", "list", "--location=" + REGION)
        for row in queues:
            name = row["name"].split("/")[-1]
            if name in QUEUES:
                cloud("tasks", "queues", "pause", name, "--location=" + REGION)
                require(cloud("tasks", "queues", "describe", name, "--location=" + REGION).get("state") == "PAUSED",
                        "queue did not pause")
        for row in jobs:
            name = row["name"].split("/")[-1]
            if name in JOBS and row.get("state") != "PAUSED":
                cloud("scheduler", "jobs", "pause", name, "--location=" + REGION)
                require(cloud("scheduler", "jobs", "describe", name, "--location=" + REGION).get("state") == "PAUSED",
                        "scheduler did not pause")
        assert_current(current)
        for name in SERVICES:
            cloud("run", "services", "update-traffic", name, "--clear-tags", "--region=" + REGION, "--quiet")
            cloud("run", "services", "update", name, "--scaling=0", "--region=" + REGION, "--quiet")
        assert_disabled()
        deadline = time.monotonic() + 3600
        with engine.connect() as connection:
            while True:
                remaining = connection.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND pid<>pg_backend_pid() AND backend_type='client backend'"
                )).scalar_one()
                connection.rollback()
                if remaining == 0:
                    break
                require(time.monotonic() < deadline, "database clients did not drain; recovery stopped")
                time.sleep(5)
        engine.dispose()
        assert_current(current)
        assert_database_inventory()
        description = f"before-restore-{env['GITHUB_SHA']}-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"
        cloud("sql", "backups", "create", "--instance=" + INSTANCE, "--description=" + description, "--quiet")
        records = cloud("sql", "backups", "list", "--instance=" + INSTANCE)
        saved = [row for row in records if row.get("description") == description and row.get("status") == "SUCCESSFUL"]
        require(len(saved) == 1, "post-update recovery backup was not confirmed")
        result = {"baseline_backup": str(baseline["id"]), "post_update_backup": str(saved[0]["id"]),
                  "instance": INSTANCE, "database_restored": False, "traffic_restored": False,
                  "transports_resumed": False, "services": receipt["services"], "scaling": scaling,
                  "original_traffic": original_traffic,
                  "original_backup_configuration": instance.get("settings", {}).get("backupConfiguration"),
                  "sha": env["GITHUB_SHA"], "ref": env["GITHUB_REF"], "run": env["GITHUB_RUN_ID"],
                  "original_run": env["ORIGINAL_RUN"]}
        output = root / "database-recovery.json"
        output.write_text(json.dumps(result, indent=2) + "\n")
        assert_disabled()
        assert_current(current)
        assert_database_inventory()
        cloud("sql", "backups", "restore", str(baseline["id"]), "--backup-instance=" + INSTANCE,
              "--restore-instance=" + INSTANCE, "--quiet")
        restored = cloud("sql", "instances", "describe", INSTANCE)
        require(restored.get("state") == "RUNNABLE", "restored instance not runnable")
        result["database_restored"] = True
        output.write_text(json.dumps(result, indent=2) + "\n")
        restore_backup_configuration(instance.get("settings", {}).get("backupConfiguration"), restored)
        for name in SERVICES:
            check_target(name, receipt["services"][name])
            cloud("run", "services", "update-traffic", name, "--region=" + REGION,
                  "--to-revisions=" + receipt["services"][name]["revision"] + "=100", "--quiet")
            require(serving(cloud("run", "services", "describe", name, "--region=" + REGION)) ==
                    receipt["services"][name]["revision"], "old traffic restoration failed")
        result["traffic_restored"] = True
        result["services_still_disabled"] = True
        output.write_text(json.dumps(result, indent=2) + "\n")
        # Reopening is a separate reviewed operation after restored DB/config checks.
        # There is deliberately no finally block that resumes services or transports.
    finally:
        engine.dispose()


def reopen():
    env = os.environ
    require(require_context(env) == "prod" and
            env.get("DATABASE_RECOVERY_CONFIRMATION") == "REOPEN_RESTORED_ORDERS_PROD",
            "explicit reopening confirmation required")
    require([row.get("account") for row in cloud("auth", "list", "--filter=status:ACTIVE")] ==
            [f"sawa-github-deploy-prod@{PROJECT}.iam.gserviceaccount.com"], "production identity required")
    result = json.loads((Path(env["RUNNER_TEMP"]) / "recovery/database-recovery.json").read_text())
    require(all(result.get(key) == value for key, value in {
        "sha": env["GITHUB_SHA"], "ref": env["GITHUB_REF"], "run": env["RECOVERY_RUN"],
        "original_run": env["ORIGINAL_RUN"], "instance": INSTANCE, "database_restored": True,
        "traffic_restored": True, "services_still_disabled": True,
    }.items()), "completed recovery receipt required")
    require(set(result["services"]) == set(SERVICES) and result["scaling"] ==
            {name: "auto" for name in SERVICES}, "recovery service inventory mismatch")
    backup_record(result["baseline_backup"])
    backup_record(result["post_update_backup"])
    instance = cloud("sql", "instances", "describe", INSTANCE)
    require(instance.get("state") == "RUNNABLE" and
            instance.get("settings", {}).get("backupConfiguration") == result["original_backup_configuration"],
            "restored database or backup policy changed")
    assert_disabled()
    for name in SERVICES:
        check_target(name, result["services"][name])
        require(serving(cloud("run", "services", "describe", name, "--region=" + REGION)) ==
                result["services"][name]["revision"], "recovered traffic changed")
    try:
        for name in reversed(SERVICES):
            cloud("run", "services", "update", name, "--scaling=auto", "--region=" + REGION, "--quiet")
            service = cloud("run", "services", "describe", name, "--region=" + REGION)
            require(service["metadata"].get("annotations", {}).get("run.googleapis.com/scalingMode", "automatic") ==
                    "automatic", "reopening not confirmed")
        (Path(env["RUNNER_TEMP"]) / "database-reopen.json").write_text(json.dumps({
            "run": env["GITHUB_RUN_ID"], "recovery_run": env["RECOVERY_RUN"],
            "sha": env["GITHUB_SHA"], "services_reopened": list(SERVICES),
            "transports_resumed": False, "business_flow_verified": False,
        }, indent=2) + "\n")
    except Exception:
        failures = []
        for name in SERVICES:
            try:
                cloud("run", "services", "update", name, "--scaling=0", "--region=" + REGION, "--quiet")
            except Exception:
                failures.append(name)
        if failures:
            (Path(env["RUNNER_TEMP"]) / "reopen-stop-failures.json").write_text(
                json.dumps({"unconfirmed_disabled_services": failures}) + "\n")
        raise


def finish():
    """Resume after a confirmed restore, without restoring the database twice."""
    env = os.environ
    require(require_context(env) == "prod" and
            env.get("DATABASE_RECOVERY_CONFIRMATION") == "FINISH_RESTORED_ORDERS_PROD",
            "explicit recovery continuation required")
    require([row.get("account") for row in cloud("auth", "list", "--filter=status:ACTIVE")] ==
            [f"sawa-github-deploy-prod@{PROJECT}.iam.gserviceaccount.com"], "production identity required")
    root = Path(env["RUNNER_TEMP"])
    result = json.loads((root / "recovery/database-recovery.json").read_text())
    require(all(result.get(key) == value for key, value in {
        "sha": env["GITHUB_SHA"], "ref": env["GITHUB_REF"], "run": env["RECOVERY_RUN"],
        "original_run": env["ORIGINAL_RUN"], "instance": INSTANCE, "database_restored": True,
    }.items()), "confirmed database restoration receipt required")
    require(set(result["services"]) == set(SERVICES), "recovery service inventory mismatch")
    assert_disabled()
    assert_database_inventory()
    backup_record(result["baseline_backup"])
    backup_record(result["post_update_backup"])
    instance = cloud("sql", "instances", "describe", INSTANCE)
    require(instance.get("state") == "RUNNABLE", "database is not runnable")
    for name in SERVICES:
        target = result["services"][name]
        check_target(name, target)
        previous = serving({"status": {"traffic": result["original_traffic"][name]}})
        current = serving(cloud("run", "services", "describe", name, "--region=" + REGION))
        require(current in {previous, target["revision"]}, "unexpected revision during continuation")
    restore_backup_configuration(result["original_backup_configuration"], instance)
    for name in SERVICES:
        cloud("run", "services", "update-traffic", name, "--region=" + REGION,
              "--to-revisions=" + result["services"][name]["revision"] + "=100", "--quiet")
        require(serving(cloud("run", "services", "describe", name, "--region=" + REGION)) ==
                result["services"][name]["revision"], "restored traffic not confirmed")
    assert_disabled()
    result.update(run=env["GITHUB_RUN_ID"], continued_from=env["RECOVERY_RUN"],
                  traffic_restored=True, services_still_disabled=True)
    (root / "database-recovery.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    try:
        if os.environ.get("DATABASE_RECOVERY_CONFIRMATION") == "REOPEN_RESTORED_ORDERS_PROD":
            reopen()
        elif os.environ.get("DATABASE_RECOVERY_CONFIRMATION") == "FINISH_RESTORED_ORDERS_PROD":
            finish()
        else:
            run()
    except Exception:
        raise SystemExit("database recovery stopped; services are not automatically reopened") from None
