"""Exact Cloud Tasks inspection; unknown transport state never renews an intent."""
from __future__ import annotations

from copy import deepcopy
import os
from urllib.parse import quote, urlparse

from src.services import workflow_ocr_delivery_service as delivery
from src.services.workflow_ocr_delivery_execution_service import execution_mutex

_CONFIG_KEYS = (
    "PROJECT_ID", "LOCATION", "QUEUE", "TARGET_URL", "SERVICE_ACCOUNT_EMAIL",
    "AUDIENCE", "DISPATCH_DEADLINE_SECONDS",
)
PREFIX = "HOSPITAL_WORKFLOW_OCR_DELIVERY_"


class DeliveryConfigurationError(RuntimeError):
    pass


def required_config():
    config = {key: str(os.getenv(PREFIX + key) or "").strip() for key in _CONFIG_KEYS}
    missing = [PREFIX + key for key, value in config.items() if not value]
    if missing:
        raise DeliveryConfigurationError("workflow_ocr_delivery_config_missing:" + ",".join(missing))
    if urlparse(config["TARGET_URL"]).scheme != "https":
        raise DeliveryConfigurationError("workflow_ocr_delivery_target_https_required")
    try:
        deadline = int(config["DISPATCH_DEADLINE_SECONDS"])
    except ValueError as exc:
        raise DeliveryConfigurationError("workflow_ocr_delivery_deadline_invalid") from exc
    if not 15 <= deadline <= 1800:
        raise DeliveryConfigurationError("workflow_ocr_delivery_deadline_invalid")
    if not config["SERVICE_ACCOUNT_EMAIL"].endswith(".iam.gserviceaccount.com"):
        raise DeliveryConfigurationError("workflow_ocr_delivery_service_identity_invalid")
    return config


def task_contract(delivery_id, generation):
    config = required_config()
    parent = f'projects/{config["PROJECT_ID"]}/locations/{config["LOCATION"]}/queues/{config["QUEUE"]}'
    return {
        "name": parent + "/tasks/" + delivery_id + "-g" + str(generation),
        "http_request": {
            "http_method": "POST",
            "url": config["TARGET_URL"].rstrip("/") + "/worker/workflow-ocr-deliveries/"
                + quote(delivery_id, safe="") + "/" + str(generation),
            "oidc_token": {"service_account_email": config["SERVICE_ACCOUNT_EMAIL"], "audience": config["AUDIENCE"]},
        },
        "dispatch_deadline": {"seconds": int(config["DISPATCH_DEADLINE_SECONDS"])},
    }


def next_task_contract(original, delivery_id, generation):
    contract = deepcopy(original)
    parent = original["name"].rsplit("/tasks/", 1)[0]
    contract["name"] = parent + "/tasks/" + delivery_id + "-g" + str(generation)
    contract["http_request"]["url"] = original["http_request"]["url"].rsplit("/", 1)[0] + "/" + str(generation)
    return contract


def inspect_task(client, contract):
    from google.api_core.exceptions import NotFound
    from google.cloud import tasks_v2
    try:
        task = client.get_task(request={"name": contract["name"], "response_view": tasks_v2.Task.View.FULL})
    except NotFound:
        return "missing"
    except Exception as exc:
        raise RuntimeError("workflow_ocr_delivery_lookup_unknown") from exc
    request = task.http_request
    expected = contract["http_request"]
    deadline = tasks_v2.Task.pb(task).dispatch_deadline
    if (task.name != contract["name"] or request.url != expected["url"]
            or request.http_method != tasks_v2.HttpMethod.POST
            or bool(request.body)
            or tasks_v2.HttpRequest.pb(request).HasField("oauth_token")
            or request.oidc_token.service_account_email != expected["oidc_token"]["service_account_email"]
            or request.oidc_token.audience != expected["oidc_token"]["audience"]
            or deadline.seconds != contract["dispatch_deadline"]["seconds"]
            or deadline.nanos != contract["dispatch_deadline"].get("nanos", 0)):
        raise RuntimeError("workflow_ocr_delivery_task_contract_mismatch")
    return "exists"


def dispatch(delivery_id, generation):
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    observed = delivery.prepare_dispatch(delivery_id, generation)
    if observed is None:
        return {"action": "noop"}
    contract = observed["task_contract"]
    try:
        client = tasks_v2.CloudTasksClient()
        state = inspect_task(client, contract)
        if state == "missing" and observed["state"] == delivery.PENDING:
            task = deepcopy(contract)
            task["http_request"]["http_method"] = tasks_v2.HttpMethod.POST
            try:
                client.create_task(request={"parent": contract["name"].rsplit("/tasks/", 1)[0], "task": task})
            except AlreadyExists:
                pass
            # Includes tombstones and a task consumed before the DB acknowledgement.
            state = inspect_task(client, contract)
        if state == "missing":
            with execution_mutex(delivery_id) as acquired:
                if not acquired:
                    return {"action": "active_owner"}
                current = delivery.prepare_dispatch(delivery_id, generation)
                if current is None:
                    return {"action": "noop"}
                if current != observed:
                    return {"action": "superseded"}
                renewed = delivery.renew_missing_delivery(delivery_id, observed)
                return {"action": "recovery_pending" if renewed else "superseded"}
        if state != "exists":
            raise RuntimeError("workflow_ocr_delivery_lookup_unknown")
        receipt = {"task_name": contract["name"], "executable_confirmed": True}
        if not delivery.mark_dispatched(delivery_id, generation, receipt):
            raise RuntimeError("workflow_ocr_delivery_dispatch_cas_failed")
        return {"action": "dispatched", **receipt}
    except Exception as exc:
        delivery.record_dispatch_error(delivery_id, generation, str(exc))
        raise


def drain_pending(limit):
    outcomes = []
    for item in delivery.pending_dispatches(limit):
        if "error" in item:
            outcomes.append({**item, "action": "blocked_identity"})
            continue
        try:
            outcomes.append({**item, **dispatch(item["delivery_id"], item["generation"])})
        except Exception as exc:
            outcomes.append({**item, "action": "dispatch_failed", "error": str(exc)})
    return {"deliveries": outcomes}
