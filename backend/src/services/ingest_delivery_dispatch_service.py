"""Recorded Cloud Tasks transport; never reconstruct a resend from current config."""
from copy import deepcopy
import base64
import json
import os
from urllib.parse import urlparse

PREFIX = "HOSPITAL_INGEST_DELIVERY_"


def required_config():
    keys = ("PROJECT_ID", "LOCATION", "QUEUE", "TARGET_URL", "SERVICE_ACCOUNT_EMAIL", "AUDIENCE", "DISPATCH_DEADLINE_SECONDS")
    config = {key: os.getenv(PREFIX + key, "").strip() for key in keys}
    if not all(config.values()) or urlparse(config["TARGET_URL"]).scheme != "https":
        raise ValueError("ingest_delivery_configuration_required")
    if not config["SERVICE_ACCOUNT_EMAIL"].endswith(".iam.gserviceaccount.com"):
        raise ValueError("ingest_delivery_service_identity_required")
    seconds = int(config["DISPATCH_DEADLINE_SECONDS"])
    if not 15 <= seconds <= 1800:
        raise ValueError("ingest_delivery_deadline_invalid")
    for role in ("WORKER_", "DRAIN_"):
        trust = {key: os.getenv(PREFIX + role + key, "").strip() for key in ("ISSUER", "AUDIENCE", "EMAIL", "SUBJECT")}
        if not all(trust.values()) or trust["ISSUER"] not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("ingest_delivery_auth_configuration_required")
        if role == "WORKER_" and (trust["EMAIL"] != config["SERVICE_ACCOUNT_EMAIL"] or trust["AUDIENCE"] != config["AUDIENCE"]):
            raise ValueError("ingest_delivery_target_trust_mismatch")
    return config


def task_contract(delivery_id, generation, config):
    parent = f'projects/{config["PROJECT_ID"]}/locations/{config["LOCATION"]}/queues/{config["QUEUE"]}'
    body = json.dumps({"delivery_id": delivery_id, "generation": generation}, sort_keys=True, separators=(",", ":"))
    return {"name": f"{parent}/tasks/{delivery_id}-g{generation}",
        "http_request": {"http_method": "POST", "url": config["TARGET_URL"].rstrip("/") + "/worker/ingest-deliveries/process",
            "body": base64.b64encode(body.encode()).decode(), "headers": {"Content-Type": "application/json"},
            "oidc_token": {"service_account_email": config["SERVICE_ACCOUNT_EMAIL"], "audience": config["AUDIENCE"]}},
        "dispatch_deadline": {"seconds": int(config["DISPATCH_DEADLINE_SECONDS"])}}


def renew_contract(original, delivery_id, generation):
    contract = deepcopy(original)
    contract["name"] = original["name"].rsplit("/tasks/", 1)[0] + f"/tasks/{delivery_id}-g{generation}"
    contract["http_request"]["body"] = base64.b64encode(json.dumps(
        {"delivery_id": delivery_id, "generation": generation}, sort_keys=True, separators=(",", ":")).encode()).decode()
    return contract


def _headers_match(actual, expected):
    def normalized(headers):
        result = {}
        for key, value in headers.items():
            name = key.lower()
            if name in result:
                return None
            result[name] = value
        return result

    observed, recorded = normalized(actual), normalized(expected)
    if observed is None or recorded is None:
        return False
    if any(name not in observed or observed[name] != value for name, value in recorded.items()):
        return False
    # Provider-added transport headers are not authored request identity. They
    # must not introduce a second credential channel alongside the saved OIDC.
    credentials = {"authorization", "proxy-authorization", "cookie", "x-serverless-authorization"}
    return not ((observed.keys() - recorded.keys()) & credentials)


def inspect_task(client, contract):
    from google.api_core.exceptions import NotFound
    from google.cloud import tasks_v2
    try:
        task = client.get_task(request={"name": contract["name"], "response_view": tasks_v2.Task.View.FULL})
    except NotFound:
        return "missing"
    except Exception as exc:
        raise RuntimeError("ingest_delivery_lookup_unknown") from exc
    actual, expected = task.http_request, contract["http_request"]
    deadline = tasks_v2.Task.pb(task).dispatch_deadline
    if (task.name != contract["name"] or actual.url != expected["url"]
        or actual.http_method != tasks_v2.HttpMethod.POST or actual.body != base64.b64decode(expected["body"])
        or tasks_v2.HttpRequest.pb(actual).HasField("oauth_token")
        or not _headers_match(actual.headers, expected["headers"])
        or actual.oidc_token.service_account_email != expected["oidc_token"]["service_account_email"]
        or actual.oidc_token.audience != expected["oidc_token"]["audience"]
        or deadline.seconds != contract["dispatch_deadline"]["seconds"]
        or deadline.nanos != contract["dispatch_deadline"].get("nanos", 0)):
        raise ValueError("ingest_delivery_task_contract_mismatch")
    return "exists"


def dispatch(delivery_id, generation):
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from src.services import ingest_delivery_service as delivery
    settled = delivery.settle_saved(delivery_id, generation)
    if settled is not None:
        return settled
    observed = delivery.prepare_dispatch(delivery_id, generation)
    if observed is None:
        return {"action": "noop"}
    client = tasks_v2.CloudTasksClient()
    contract = observed["task_contract"]
    try:
        state = inspect_task(client, contract)
        if state == "missing" and observed["state"] == "PENDING":
            task = deepcopy(contract)
            task["http_request"]["http_method"] = tasks_v2.HttpMethod.POST
            task["http_request"]["body"] = base64.b64decode(task["http_request"]["body"])
            try:
                client.create_task(request={"parent": contract["name"].rsplit("/tasks/", 1)[0], "task": task})
            except AlreadyExists:
                pass
            state = inspect_task(client, contract)
        if state == "exists":
            delivery.mark_sent(delivery_id, generation)
            return {"action": "sent"}
        return delivery.renew_missing(delivery_id, generation)
    except Exception as exc:
        delivery.record_dispatch_error(delivery_id, generation, str(exc))
        raise


def drain(limit):
    from src.services import ingest_delivery_service as delivery
    results = []
    for delivery_id, generation in delivery.drain_candidates(limit):
        try:
            results.append(dispatch(delivery_id, generation))
        except Exception as exc:
            results.append({"delivery_id": delivery_id, "error": str(exc)})
    return {"items": results}
