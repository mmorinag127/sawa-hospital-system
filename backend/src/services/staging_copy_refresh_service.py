"""Durable staging copy refresh control. Business/Test databases are never a journal."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit

from src import staging_data as datasets


# Only runtime keys actually consumed by the per-dataset business boundary.
# Authentication/database/API credentials must never enter a copied manifest.
SETTING_KEYS = {"order": {"FACILITY_MASTER_URI", "INGEST_POLICY_URI", "OCR_PIPELINE_STATE_URI"},
                "shift": {"GCS_BUCKET_NAME"}, "school-lunch": set()}


class RefreshError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def _require_staging():
    if not datasets.staging_runtime():
        raise RefreshError("staging_copy_disabled")


def _blob(name):
    _require_staging()
    from google.cloud import storage
    parsed = datasets._staging_uri(datasets.refresh_control_uri() + name)
    return storage.Client().bucket(parsed.netloc).blob(parsed.path.lstrip("/"))


def write_registry(value, generation):
    datasets.validate_registry(value)
    _blob("registry.json").upload_from_string(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        content_type="application/json", if_generation_match=generation,
    )


def source_configuration():
    from google.api_core.exceptions import NotFound
    try:
        blob = _blob("sources.json")
        blob.reload()
        raw = blob.download_as_bytes(if_generation_match=int(blob.generation))
    except NotFound:
        raise RefreshError("production_sources_not_configured") from None
    try:
        config = json.loads(raw, object_pairs_hook=datasets._unique_object)
        if config.get("schema") != 1 or set(config.get("components", {})) != {"order", "shift", "school-lunch"}:
            raise ValueError()
        for name, entry in config["components"].items():
            if entry.get("mode") == "retain":
                if set(entry) != {"mode", "snapshot", "destination_bucket"}:
                    raise ValueError()
                retained = datasets.retained_snapshot(entry["snapshot"])
                if datasets._staging_uri(retained["uri"]).netloc != entry["destination_bucket"]:
                    raise ValueError()
                continue
            required = {"source_service", "destination_service", "settings_keys", "expected_migration_heads", "source_buckets", "local_objects", "destination_bucket"}
            if name == "order":
                required.add("producer_service")
            if (not required <= set(entry) or set(entry) - required - {"source_migration_heads", "settings_mode"} or not isinstance(entry["expected_migration_heads"], list)
                    or not entry["expected_migration_heads"] or not isinstance(entry["source_buckets"], list)
                    or not entry["source_buckets"]
                    or any(not isinstance(head, str) or not re.fullmatch(r"[A-Za-z0-9_]+", head) for head in entry["expected_migration_heads"])):
                raise ValueError()
            if "settings_mode" in entry and (name != "order" or entry["settings_mode"] != "runtime_export"
                    or entry["source_service"] != "projects/sawahospitalsystem/locations/asia-northeast2/services/worker-prod"):
                raise ValueError()
            if "source_migration_heads" in entry:
                heads = entry["source_migration_heads"]
                if (not isinstance(heads, list) or (not heads and name != "order")
                        or any(not isinstance(head, str) or not re.fullmatch(r"[A-Za-z0-9_]+", head) for head in heads)
                        or name not in {"order", "shift"}):
                    raise ValueError()
            service_roles = [("source_service", "prod"), ("destination_service", "stg")]
            if name == "order":
                service_roles.append(("producer_service", "prod"))
            for key, suffix in service_roles:
                if not re.fullmatch(r"projects/[a-z0-9-]+/locations/[a-z0-9-]+/services/[a-z0-9-]+-" + suffix, str(entry[key])):
                    raise ValueError()
            if (not isinstance(entry["settings_keys"], list)
                    or any(not re.fullmatch(r"[A-Z][A-Z0-9_]+", key) for key in entry["settings_keys"])
                    or len(entry["settings_keys"]) != len(set(entry["settings_keys"]))
                    or set(entry["settings_keys"]) != SETTING_KEYS[name]):
                raise ValueError()
            if any(datasets._staging_bucket(bucket) for bucket in entry["source_buckets"]):
                raise ValueError()
            if not datasets._staging_bucket(entry["destination_bucket"]):
                raise ValueError()
            if (not isinstance(entry["local_objects"], dict)
                    or any(not datasets.local_source_reference(reference) for reference in entry["local_objects"])):
                raise ValueError()
        if all(entry.get("mode") == "retain" for entry in config["components"].values()):
            raise ValueError()
        return config, hashlib.sha256(raw).hexdigest()
    except (AttributeError, KeyError, ValueError, TypeError):
        raise RefreshError("production_source_configuration_invalid") from None


def status():
    _require_staging()
    registry, _ = datasets.read_registry()
    try:
        config, digest = source_configuration()
        configured, reason = True, None
    except RefreshError as exc:
        configured, reason, digest, config = False, str(exc), None, {"components": {}}
    return {"configured": configured, "blocked_reason": reason,
            "source_configuration_sha256": digest, "active_dataset": registry["active"],
            "job": registry.get("refresh"),
            "refresh_components": [name for name, entry in config["components"].items() if entry.get("mode") != "retain"],
            "retained_components": [name for name, entry in config["components"].items() if entry.get("mode") == "retain"]}


def request_refresh(request_id):
    _require_staging()
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise RefreshError("refresh_request_id_invalid")
    _, source_digest = source_configuration()
    registry, generation = datasets.read_registry()
    job = registry.get("refresh")
    if job and job["id"] == request_id:
        if job["source_configuration_sha256"] != source_digest and job["phase"] == "queued":
            job.update(phase="failed", error="refresh_source_configuration_changed", updated_at=now().isoformat())
            write_registry(registry, generation)
            return job
        if job["phase"] == "queued":
            dispatch(job)
        return job
    if job and job["phase"] not in {"succeeded", "failed"}:
        raise RefreshError("refresh_already_running")
    job = {"id": request_id, "dataset": "replay-" + request_id,
           "phase": "queued", "component": None, "created_at": now().isoformat(),
           "updated_at": now().isoformat(), "source_configuration_sha256": source_digest,
           "error": None, "attempt": None, "lease_until": None, "delivery_generation": 1}
    registry["refresh"] = job
    write_registry(registry, generation)
    # A dispatch failure leaves a durable queued request. Same-id retry only resends it.
    dispatch(job)
    return job


def dispatch(job, remaining=3):
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from src.services.workflow_ocr_delivery_dispatch_service import required_config
    config = required_config()
    if "mode" in config or not urlsplit(config["TARGET_URL"]).netloc.split(".")[0].startswith("worker-stg-"):
        raise RefreshError("refresh_worker_target_invalid")
    parent = f'projects/{config["PROJECT_ID"]}/locations/{config["LOCATION"]}/queues/{config["QUEUE"]}'
    if remaining < 1:
        raise RefreshError("refresh_dispatch_unconfirmed")
    delivery_generation = job["delivery_generation"]
    task = {"name": parent + "/tasks/staging-copy-refresh-" + job["id"] + "-g" + str(delivery_generation),
            "http_request": {"http_method": tasks_v2.HttpMethod.POST,
                "url": config["TARGET_URL"].rstrip("/") + "/worker/staging-copy-refresh/" + job["id"] + "/" + str(delivery_generation),
                "oidc_token": {"service_account_email": config["SERVICE_ACCOUNT_EMAIL"], "audience": config["AUDIENCE"]}},
            "dispatch_deadline": {"seconds": 1800}}
    client = tasks_v2.CloudTasksClient()
    try:
        client.create_task(request={"parent": parent, "task": task})
        return
    except AlreadyExists:
        from src.services.workflow_ocr_delivery_dispatch_service import inspect_task
        if inspect_task(client, task) == "exists":
            return
    registry, generation = datasets.read_registry()
    current = registry.get("refresh")
    if not current or current["id"] != job["id"]:
        raise RefreshError("refresh_request_not_current")
    if current["phase"] != "queued":
        return
    if current["delivery_generation"] == delivery_generation:
        current["delivery_generation"] += 1
        write_registry(registry, generation)
    dispatch(current, remaining - 1)


def claim(request_id, delivery_generation):
    registry, generation = datasets.read_registry()
    job = registry.get("refresh")
    if not job or job["id"] != request_id or job["delivery_generation"] != delivery_generation:
        raise RefreshError("refresh_request_not_current")
    if job["phase"] in {"succeeded", "failed"}:
        return None
    if job.get("lease_until") and datetime.fromisoformat(job["lease_until"]) > now():
        raise RefreshError("refresh_execution_in_progress")
    job.update(phase="starting", attempt=uuid.uuid4().hex,
               lease_until=(now() + timedelta(minutes=30)).isoformat(), updated_at=now().isoformat())
    write_registry(registry, generation)
    return deepcopy(job)


def checkpoint(claimed, phase, component=None, *, error=None, published=None):
    registry, generation = datasets.read_registry()
    job = registry.get("refresh")
    if (not job or job["id"] != claimed["id"] or job.get("attempt") != claimed["attempt"]
            or job["phase"] in {"succeeded", "failed"}
            or datetime.fromisoformat(job["lease_until"]) <= now()):
        raise RefreshError("refresh_execution_fenced")
    job.update(phase=phase, component=component, error=error, updated_at=now().isoformat(),
               lease_until=(now() + timedelta(minutes=30)).isoformat())
    if published is not None:
        if phase != "succeeded" or published["id"] != claimed["dataset"]:
            raise RefreshError("refresh_publication_invalid")
        if claimed["dataset"] in registry["snapshots"]:
            raise RefreshError("refresh_snapshot_already_published")
        registry["snapshots"][published["id"]] = {key: published[key] for key in ("uri", "sha256", "snapshot_at")}
        registry["active"] = published["id"]
        job["completed_at"] = now().isoformat()
        job["missing_at_source"] = published.get("missing_at_source", {})
    write_registry(registry, generation)


def execute(request_id, delivery_generation):
    _require_staging()
    job = claim(request_id, delivery_generation)
    if job is None:
        return {"terminal": True}
    try:
        config, digest = source_configuration()
        if digest != job["source_configuration_sha256"]:
            raise RefreshError("refresh_source_configuration_changed")
        from src.services.staging_copy_refresh_engine import build_snapshot
        published = build_snapshot(job, config, lambda phase, component=None: checkpoint(job, phase, component))
        _, current_digest = source_configuration()
        if current_digest != job["source_configuration_sha256"]:
            raise RefreshError("refresh_source_configuration_changed")
        checkpoint(job, "succeeded", published=published)
    except Exception as exc:
        # Never persist exception strings, SQL, credentials, or signed URLs.
        code = str(exc) if isinstance(exc, RefreshError) else "refresh_failed:" + type(exc).__name__
        checkpoint(job, "failed", error=code)
        return {"terminal": True, "succeeded": False}
    return {"terminal": True, "succeeded": True, "dataset": job["dataset"]}
