"""Captured Cloud Tasks transport; operational values are explicit inputs."""

import json
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlsplit

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2

ENV_FIELDS = {
    "project": "SHIFT_SOLVE_TASKS_PROJECT", "location": "SHIFT_SOLVE_TASKS_LOCATION",
    "queue": "SHIFT_SOLVE_TASKS_QUEUE", "url": "SHIFT_SOLVE_TASKS_TARGET_URL",
    "service_account": "SHIFT_SOLVE_TASKS_SERVICE_ACCOUNT",
    "audience": "SHIFT_SOLVE_TASKS_AUDIENCE", "issuer": "SHIFT_SOLVE_TASKS_ISSUER",
    "subject": "SHIFT_SOLVE_TASKS_SUBJECT", "deadline_seconds": "SHIFT_SOLVE_TASKS_DEADLINE_SECONDS",
}


def configured_transport(environment):
    from . import agent_trial_transport as local
    if local.enabled():
        return local.configuration()
    values = {key: environment.get(name) for key, name in ENV_FIELDS.items()}
    if any(type(value) is not str or not value or value.strip() != value for value in values.values()):
        raise ValueError("shift_transport_configuration_required")
    values["deadline_seconds"] = int(values["deadline_seconds"])
    config = CloudTaskConfig(**values)
    config.validate()
    target = urlsplit(config.url)
    if target.path != "/worker/solve-deliveries/process" or target.query:
        raise ValueError("shift_transport_target_path_invalid")
    return config


class DispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudTaskConfig:
    project: str
    location: str
    queue: str
    url: str
    service_account: str
    audience: str
    issuer: str
    subject: str
    deadline_seconds: int

    def validate(self):
        values = asdict(self)
        for key, value in values.items():
            if key != "deadline_seconds" and (type(value) is not str or not value or value.strip() != value):
                raise ValueError("shift_transport_configuration_required")
        if any("/" in values[key] for key in ("project", "location", "queue")):
            raise ValueError("shift_transport_resource_invalid")
        url = urlsplit(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("shift_transport_url_invalid")
        if self.issuer not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("shift_transport_issuer_invalid")
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com", self.service_account):
            raise ValueError("shift_transport_service_account_invalid")
        if type(self.deadline_seconds) is not int or not 15 <= self.deadline_seconds <= 1800:
            raise ValueError("shift_transport_http_deadline_invalid")


def capture_contract(config, run_id, generation):
    config.validate()
    if any(type(value) is not int or value <= 0 for value in (run_id, generation)):
        raise ValueError("shift_transport_identity_invalid")
    return {"schema": 1, "config": asdict(config), "run_id": run_id, "generation": generation,
        "task_name": tasks_v2.CloudTasksClient.task_path(config.project, config.location, config.queue,
            f"solve-{run_id}-generation-{generation}"),
        "body": json.dumps({"run_id": run_id, "generation": generation}, sort_keys=True, separators=(",", ":"))}


def validate_contract(contract):
    if type(contract) is not dict or set(contract) != {"schema", "config", "run_id", "generation", "task_name", "body"}:
        raise ValueError("shift_transport_contract_invalid")
    if type(contract["schema"]) is not int or contract["schema"] != 1:
        raise ValueError("shift_transport_contract_invalid")
    if type(contract["config"]) is not dict or set(contract["config"]) != set(CloudTaskConfig.__dataclass_fields__):
        raise ValueError("shift_transport_contract_invalid")
    config = CloudTaskConfig(**contract["config"])
    if contract != capture_contract(config, contract["run_id"], contract["generation"]):
        raise ValueError("shift_transport_contract_invalid")
    return config


def _headers_match(headers, contract, config):
    observed = {}
    for name, value in headers.items():
        key = name.lower()
        if key in observed:
            return False
        observed[key] = value
    expected = {"content-type": "application/json", "user-agent": "Google-Cloud-Tasks",
        "host": urlsplit(config.url).netloc,
        "content-length": str(len(contract["body"].encode()))}
    return (observed.get("content-type") == expected["content-type"]
        and all(name in expected and value == expected[name] for name, value in observed.items()))


def _matches(task, contract, config):
    http = task.http_request
    protobuf = tasks_v2.HttpRequest.pb(http)
    deadline = tasks_v2.Task.pb(task).dispatch_deadline
    return (task.name == contract["task_name"] and int(http.http_method) == int(tasks_v2.HttpMethod.POST)
        and http.url == config.url and bytes(http.body) == contract["body"].encode()
        and _headers_match(http.headers, contract, config)
        and protobuf.HasField("oidc_token")
        and http.oidc_token.service_account_email == config.service_account
        and http.oidc_token.audience == config.audience
        and not protobuf.HasField("oauth_token")
        and deadline.seconds == config.deadline_seconds and deadline.nanos == 0)


def send_captured(contract, *, client):
    config = validate_contract(contract)
    task = {"name": contract["task_name"], "dispatch_deadline": {"seconds": config.deadline_seconds},
        "http_request": {"http_method": tasks_v2.HttpMethod.POST, "url": config.url,
            "headers": {"Content-Type": "application/json"}, "body": contract["body"].encode(),
            "oidc_token": {"service_account_email": config.service_account, "audience": config.audience}}}
    try:
        actual = client.create_task(request={"parent": contract["task_name"].rsplit("/tasks/", 1)[0],
            "task": task, "response_view": tasks_v2.Task.View.FULL})
    except AlreadyExists:
        try:
            actual = client.get_task(request={"name": contract["task_name"], "response_view": tasks_v2.Task.View.FULL})
        except Exception as error:
            raise DispatchError("shift_task_lookup_failed") from error
    except Exception as error:
        raise DispatchError("shift_task_dispatch_failed") from error
    if not _matches(actual, contract, config):
        raise DispatchError("shift_task_contract_differs")
    return actual.name


def pending_delivery(session, delivery_id):
    from .models import SolveDelivery, SolveRun, SolveDeliveryReview
    from .solve_delivery_service import _validate_delivery_capture
    delivery = session.query(SolveDelivery).filter_by(id=delivery_id).with_for_update(nowait=True).populate_existing().one_or_none()
    if delivery is None or delivery.state != "pending":
        raise DispatchError("shift_task_pending_intent_required")
    if (delivery.owner_attempt is not None or delivery.lease_until is not None or delivery.receipt is not None
            or session.query(SolveDeliveryReview).filter_by(delivery_id=delivery.id).one_or_none() is not None):
        raise ValueError("shift_task_pending_intent_invalid")
    _validate_delivery_capture(delivery, session.get(SolveRun, delivery.solve_run_id))
    contract = delivery.transport_contract
    validate_contract(contract)
    if contract["run_id"] != delivery.solve_run_id or contract["generation"] != delivery.generation:
        raise DispatchError("shift_task_identity_differs")
    return delivery


def dispatch_delivery(session, delivery_id, *, client):
    delivery = pending_delivery(session, delivery_id)
    contract = delivery.transport_contract
    # Transport acknowledgement is not an execution or terminal receipt.
    return send_captured(contract, client=client)
