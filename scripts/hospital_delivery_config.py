#!/usr/bin/env python3
"""Actions-only hospital transport bootstrap; never deploys a revision."""

import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hospital_release_context import require_context as release_context

PROJECT = "sawahospitalsystem"
REGION = "asia-northeast2"
SERVICE = "worker-stg"
URL = "https://worker-stg-avlnzjjrca-dt.a.run.app"
RUNTIME = f"worker-exec-stg@{PROJECT}.iam.gserviceaccount.com"
DEPLOY = f"sawa-github-deploy-stg@{PROJECT}.iam.gserviceaccount.com"
FLOWS = (("ingest", "HOSPITAL_INGEST_DELIVERY_"),
         ("workflow-ocr", "HOSPITAL_WORKFLOW_OCR_DELIVERY_"))
DEADLINE = 1800
LEASE = 2100


class Blocked(RuntimeError):
    pass


def require_context():
    try:
        environment = release_context(os.environ)
    except RuntimeError as exc:
        raise Blocked(str(exc)) from exc
    expected = {
        "WORKER_SERVICE": "worker-" + environment,
        "WORKER_URL": f"https://worker-{environment}-avlnzjjrca-dt.a.run.app",
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise Blocked("hospital delivery bootstrap requires exact release Actions context")
    if os.environ.get("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"):
        raise Blocked("credential impersonation override is forbidden")
    return environment


def gcloud(*args):
    result = subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}", "--quiet", "--format=json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        # Do not print cloud stderr, credentials, or raw service descriptors.
        raise Blocked(f"GCP prerequisite/operation denied: {' '.join(args[:3])}; no authority expansion attempted")
    return json.loads(result.stdout or "{}")


def require_equal(actual, expected, label):
    if actual != expected:
        raise Blocked(f"hospital delivery descriptor mismatch: {label}")


def email(name):
    return f"{name}@{PROJECT}.iam.gserviceaccount.com"


def descriptor(account):
    value = gcloud("iam", "service-accounts", "describe", account)
    require_equal(value.get("email"), account, "service account email")
    require_equal(value.get("projectId"), PROJECT, "service account project")
    if value.get("disabled") or not str(value.get("uniqueId", "")).isdigit():
        raise Blocked("enabled service account with actual uniqueId required")
    return {"email": account, "subject": str(value["uniqueId"])}


def resource_command(*args):
    """Place the IAM verb and resource before command-specific flags."""
    args = list(args)
    for verb in ("get-iam-policy", "add-iam-policy-binding"):
        if verb in args:
            index = args.index(verb)
            if index < 3 or args[index - 1].startswith("--"):
                raise Blocked("IAM command resource required")
            return gcloud(*args[:2], verb, args[index - 1],
                          *args[2:index - 1], *args[index + 1:])
    raise Blocked("supported IAM command verb required")


def ensure_binding(resource, member, role, *, read_only=False):
    def contains(policy):
        return any(item.get("role") == role and not item.get("condition")
                   and member in item.get("members", []) for item in policy.get("bindings", []))
    if not contains(resource_command(*resource, "get-iam-policy")):
        if read_only:
            raise Blocked("production resource IAM must be provisioned before deployment: " + role)
        conditions = () if resource[:2] == ("tasks", "queues") else ("--condition=None",)
        resource_command(*resource, "add-iam-policy-binding", f"--member={member}",
                         f"--role={role}", *conditions)
    if not contains(resource_command(*resource, "get-iam-policy")):
        raise Blocked("required resource-scoped IAM binding not verified")


def bootstrap(*, allow_paused=False):
    environment = require_context()
    read_only = environment == "prod"
    service_target = "worker-" + environment
    url = f"https://worker-{environment}-avlnzjjrca-dt.a.run.app"
    runtime = email("worker-exec-" + environment)
    deploy_identity = email("sawa-github-deploy-" + environment)
    active = gcloud("auth", "list", "--filter=status:ACTIVE")
    require_equal([item["account"] for item in active], [deploy_identity], "existing WIF deploy identity")
    worker = gcloud("run", "services", "describe", service_target, f"--region={REGION}")
    require_equal(worker.get("status", {}).get("url"), url, "worker URL")
    spec = worker["spec"]["template"]["spec"]
    require_equal(spec.get("serviceAccountName"), runtime, "worker runtime identity")
    descriptor(runtime)
    # Fail early on missing Scheduler authority; never grant project/Scheduler roles.
    jobs = gcloud("scheduler", "jobs", "list", f"--location={REGION}")
    accounts = gcloud("iam", "service-accounts", "list")
    queues = gcloud("tasks", "queues", "list", f"--location={REGION}")
    for values, key in ((jobs, "name"), (accounts, "email"), (queues, "name")):
        if not isinstance(values, list) or any(not isinstance(item, dict) or not item.get(key) for item in values):
            raise Blocked("valid resource list required before deciding absence")
    project = gcloud("projects", "describe", PROJECT)
    number = str(project["projectNumber"])
    if not number.isdigit():
        raise Blocked("actual numeric projectNumber required")
    agents = {
        "task": f"service-{number}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
        "drain": f"service-{number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com",
    }
    policy = gcloud("projects", "get-iam-policy", PROJECT)
    for kind, role in (("task", "roles/cloudtasks.serviceAgent"), ("drain", "roles/cloudscheduler.serviceAgent")):
        if not any(item.get("role") == role and not item.get("condition")
                   and f"serviceAccount:{agents[kind]}" in item.get("members", [])
                   for item in policy.get("bindings", [])):
            raise Blocked(f"existing Google-managed service agent binding required: {role}")
    env = {}
    evidence = {"worker_url": url, "runtime": runtime, "project_number": number,
                "google_service_agents": agents, "flows": {}}
    for flow, prefix in FLOWS:
        queue = f"hospital-{flow}-{environment}"
        identities = {}
        for kind in ("task", "drain"):
            name = f"hosp-{flow}-{kind}-{environment}"
            account = email(name)
            if not any(item.get("email") == account for item in accounts):
                if read_only:
                    raise Blocked("production delivery account missing: " + name)
                gcloud("iam", "service-accounts", "create", name)
            identities[kind] = descriptor(account)
            ensure_binding(("iam", "service-accounts", account),
                           f"serviceAccount:{agents[kind]}", "roles/iam.serviceAccountOpenIdTokenCreator", read_only=read_only)
            ensure_binding(("run", "services", f"--region={REGION}", service_target),
                           f"serviceAccount:{account}", "roles/run.invoker", read_only=read_only)
        ensure_binding(("iam", "service-accounts", identities["task"]["email"]),
                       f"serviceAccount:{runtime}", "roles/iam.serviceAccountUser", read_only=read_only)
        ensure_binding(("iam", "service-accounts", identities["drain"]["email"]),
                       f"serviceAccount:{deploy_identity}", "roles/iam.serviceAccountUser", read_only=read_only)
        if not any(item.get("name", "").endswith(f"/queues/{queue}") for item in queues):
            if read_only:
                raise Blocked("production delivery queue missing: " + queue)
            gcloud("tasks", "queues", "create", queue, f"--location={REGION}",
                   "--max-concurrent-dispatches=2", "--max-dispatches-per-second=1")
        queue_desc = gcloud("tasks", "queues", "describe", queue, f"--location={REGION}")
        require_equal(queue_desc.get("name"), f"projects/{PROJECT}/locations/{REGION}/queues/{queue}", "queue name")
        if not (allow_paused and environment == "prod" and queue_desc.get("state") == "PAUSED"):
            require_equal(queue_desc.get("state"), "RUNNING", "queue state")
        require_equal(queue_desc.get("rateLimits", {}).get("maxConcurrentDispatches"), 2, "queue concurrency")
        require_equal(queue_desc.get("rateLimits", {}).get("maxDispatchesPerSecond"), 1, "queue dispatch rate")
        if queue_desc.get("httpTarget"):
            raise Blocked("queue-level HTTP override is forbidden")
        for role in ("roles/cloudtasks.enqueuer", "roles/cloudtasks.viewer"):
            ensure_binding(("tasks", "queues", f"--location={REGION}", queue),
                           f"serviceAccount:{runtime}", role, read_only=read_only)
        job = f"hospital-{flow}-drain-{environment}"
        uri = f"{url}/worker/{flow}-deliveries/drain?limit=10"
        if not any(item.get("name", "").endswith(f"/jobs/{job}") for item in jobs):
            if read_only:
                raise Blocked("production drain scheduler missing: " + job)
            gcloud("scheduler", "jobs", "create", "http", job, f"--location={REGION}",
                   "--schedule=*/1 * * * *", "--time-zone=Etc/UTC", "--http-method=POST",
                   f"--uri={uri}", f"--oidc-service-account-email={identities['drain']['email']}",
                   f"--oidc-token-audience={url}", "--attempt-deadline=180s")
        job_desc = gcloud("scheduler", "jobs", "describe", job, f"--location={REGION}")
        target = job_desc.get("httpTarget", {})
        for actual, expected, label in (
            (job_desc.get("name"), f"projects/{PROJECT}/locations/{REGION}/jobs/{job}", "job name"),
            (job_desc.get("state"), "PAUSED" if allow_paused and environment == "prod"
             and job_desc.get("state") == "PAUSED" else "ENABLED", "drain state"),
            (job_desc.get("schedule"), "*/1 * * * *", "drain schedule"),
            (job_desc.get("timeZone"), "Etc/UTC", "drain timezone"),
            (job_desc.get("attemptDeadline"), "180s", "drain deadline"),
            (target.get("uri"), uri, "drain URL"),
            (target.get("httpMethod"), "POST", "drain method"),
            (target.get("oidcToken"), {"serviceAccountEmail": identities["drain"]["email"], "audience": url}, "drain OIDC"),
        ):
            require_equal(actual, expected, label)
        if target.get("oauthToken") or target.get("body"):
            raise Blocked("unexpected drain authentication/body")
        if {key.lower() for key in target.get("headers", {})} & {
            "authorization", "proxy-authorization", "cookie", "x-serverless-authorization",
        }:
            raise Blocked("unexpected drain credential header")
        values = {"PROJECT_ID": PROJECT, "LOCATION": REGION, "QUEUE": queue,
                  "TARGET_URL": url, "SERVICE_ACCOUNT_EMAIL": identities["task"]["email"],
                  "AUDIENCE": url, "DISPATCH_DEADLINE_SECONDS": str(DEADLINE)}
        for kind, key in (("task", "WORKER"), ("drain", "DRAIN")):
            values.update({f"{key}_ISSUER": "https://accounts.google.com", f"{key}_AUDIENCE": url,
                           f"{key}_EMAIL": identities[kind]["email"], f"{key}_SUBJECT": identities[kind]["subject"]})
        env.update({prefix + key: value for key, value in values.items()})
        evidence["flows"][flow] = {"queue": queue_desc["name"], "identities": identities,
                                   "drain_job": job_desc["name"], "drain_uri": uri}
    env["HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS"] = str(LEASE)
    return env, evidence


def main():
    output = Path("hospital-delivery-artifacts")
    output.mkdir(exist_ok=True)
    try:
        env, evidence = bootstrap(allow_paused=True)
        fragment = ",".join(f"{key}={value}" for key, value in sorted(env.items()))
        (output / "verified-env.txt").write_text(fragment + "\n")
        (output / "verified-descriptors.json").write_text(json.dumps(evidence, indent=2) + "\n")
        with open(os.environ["GITHUB_ENV"], "a") as handle:
            handle.write(f"HOSPITAL_DELIVERY_ENV={fragment}\n")
    except Exception as exc:
        message = str(exc) if isinstance(exc, Blocked) else "hospital bootstrap failed; cloud details suppressed"
        (output / "status.json").write_text(json.dumps({"status": "blocked", "reason": message}) + "\n")
        print(message, file=sys.stderr)
        return 1
    (output / "status.json").write_text('{"status":"verified"}\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
