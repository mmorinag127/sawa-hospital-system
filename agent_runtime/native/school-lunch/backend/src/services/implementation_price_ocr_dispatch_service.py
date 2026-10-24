from __future__ import annotations

import json
import math
import os
from hashlib import sha256

import requests
from urllib.parse import urlsplit
from src import staging_data as datasets


class OcrDispatchConfigurationError(RuntimeError):
    pass


class OcrDispatchError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    if not value:
        raise OcrDispatchConfigurationError(f"{name} is required")
    return value


def _worker_process_url(job_id: str) -> str:
    base_url = _required_env("IMPLEMENTATION_PRICE_OCR_WORKER_URL")
    return f"{base_url.rstrip('/')}/jobs/{job_id}/process"




def cloud_task_name(job_id: str, generation: int) -> str:
    from google.cloud import tasks_v2
    return tasks_v2.CloudTasksClient.task_path(
        _required_env("IMPLEMENTATION_PRICE_OCR_TASKS_PROJECT"),
        _required_env("IMPLEMENTATION_PRICE_OCR_TASKS_LOCATION"),
        _required_env("IMPLEMENTATION_PRICE_OCR_TASKS_QUEUE"),
        f"{datasets.delivery_namespace(feature='school-lunch')}ocr-{job_id}-delivery-{generation}")


def inspect_cloud_task(task_name: str, *, contract) -> str:
    from google.api_core.exceptions import NotFound
    from google.cloud import tasks_v2
    try:
        task = tasks_v2.CloudTasksClient().get_task(request={"name": task_name, "response_view": tasks_v2.Task.View.FULL})
        if task.name != task_name:
            raise OcrDispatchError("Cloud Tasks returned a different task identity")
        http = task.http_request
        if (http.url != contract["url"] or int(http.http_method) != 1 or bytes(http.body) != contract["body"].encode()
            or dict(http.headers).get("Content-Type") != "application/json"
            or not datasets.delivery_dataset_matches(http.headers, contract.get("dataset"))
            or http.oidc_token.service_account_email != contract["service_account"]
            or http.oidc_token.audience != contract["audience"]
            or task.dispatch_deadline.total_seconds() != contract["deadline_seconds"]
            or http.oauth_token.service_account_email):
            raise OcrDispatchError("Cloud Tasks original delivery contract differs")
        return "exists"
    except NotFound:
        return "missing"
    except Exception as exc:
        raise OcrDispatchError(f"Cloud Tasks exact-task lookup failed: {exc}") from exc


def _dispatch_cloud_tasks(job_id: str, generation: int, *, contract) -> str:
    return send_ocr_delivery(job_id, generation, mode="cloud_tasks", contract=contract)


def configured_mode() -> str:
    mode = str(os.getenv("IMPLEMENTATION_PRICE_OCR_DISPATCH_MODE", "") or "").strip().lower()
    if mode not in {"local", "cloud_tasks"}:
        raise OcrDispatchConfigurationError(
            "IMPLEMENTATION_PRICE_OCR_DISPATCH_MODE must be explicitly set to local or cloud_tasks")
    return mode


def capture_contract(job_id, generation, mode):
    contract = {"schema": 1, "url": _worker_process_url(job_id), "method": "POST",
                "body": json.dumps({"job_id": job_id, "delivery_generation": generation}), "mode": mode}
    if datasets.current_dataset(feature="school-lunch") != "test":
        contract["dataset"] = datasets.current_dataset(feature="school-lunch")
    if mode == "cloud_tasks":
        deadline = int(_required_env("IMPLEMENTATION_PRICE_OCR_TASKS_DISPATCH_DEADLINE_SECONDS"))
        if not 1 <= deadline <= 1800:
            raise OcrDispatchConfigurationError("Cloud Tasks deadline must be between 1 and 1800")
        contract.update(task_name=cloud_task_name(job_id, generation),
            service_account=_required_env("IMPLEMENTATION_PRICE_OCR_TASKS_SERVICE_ACCOUNT"),
            audience=_required_env("IMPLEMENTATION_PRICE_OCR_TASKS_AUDIENCE"),
            issuer=_required_env("IMPLEMENTATION_PRICE_OCR_TASKS_ISSUER"), deadline_seconds=deadline)
        contract["subject"] = _required_env("IMPLEMENTATION_PRICE_OCR_TASKS_SUBJECT")
    elif mode == "local":
        contract.update(timeout_seconds=float(_required_env("IMPLEMENTATION_PRICE_OCR_WORKER_TIMEOUT_SECONDS")),
            token_digest=sha256(_required_env("IMPLEMENTATION_PRICE_OCR_LOCAL_TOKEN").encode()).hexdigest())
        if not math.isfinite(contract["timeout_seconds"]) or contract["timeout_seconds"] <= 0:
            raise OcrDispatchConfigurationError("Local timeout must be positive")
    else:
        raise OcrDispatchConfigurationError("Original delivery mode is unavailable")
    return contract


def send_captured(contract):
    if datasets.current_dataset(feature="school-lunch") != "test":
        if (contract.get("dataset") != datasets.current_dataset(feature="school-lunch")
                or "-stg-" not in urlsplit(contract.get("url", "")).netloc):
            raise datasets.DatasetUnavailable("staging_dataset_original_delivery_not_replayable")
    if contract["mode"] == "local":
        token = _required_env("IMPLEMENTATION_PRICE_OCR_LOCAL_TOKEN")
        if sha256(token.encode()).hexdigest() != contract["token_digest"]:
            raise OcrDispatchConfigurationError("Local delivery credential changed from original contract")
        try:
            response = requests.post(contract["url"], data=contract["body"].encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + token, **datasets.delivery_headers(feature="school-lunch")},
                timeout=contract["timeout_seconds"])
            response.raise_for_status()
            return "exists"
        except requests.RequestException as exc:
            raise OcrDispatchError("Local original delivery failed") from exc
    from google.cloud import tasks_v2
    from google.api_core.exceptions import AlreadyExists
    task = {"name": contract["task_name"], "dispatch_deadline": {"seconds": contract["deadline_seconds"]},
        "http_request": {"http_method": tasks_v2.HttpMethod.POST, "url": contract["url"],
            "headers": {"Content-Type": "application/json", **({datasets.HEADER: contract["dataset"]} if "dataset" in contract else {})}, "body": contract["body"].encode(),
            "oidc_token": {"service_account_email": contract["service_account"], "audience": contract["audience"]}}}
    try:
        tasks_v2.CloudTasksClient().create_task(parent=contract["task_name"].rsplit("/tasks/", 1)[0], task=task)
    except AlreadyExists:
        return inspect_cloud_task(contract["task_name"], contract=contract)
    except Exception as exc:
        raise OcrDispatchError("Original Cloud Tasks delivery failed") from exc
    return "exists"


def delivery_body_matches(body, job_id, generation):
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return False
    return (type(generation) is int and generation > 0 and isinstance(value, dict)
            and set(value) == {"job_id", "delivery_generation"} and type(value["job_id"]) is str
            and value["job_id"] == job_id and type(value["delivery_generation"]) is int
            and value["delivery_generation"] == generation)


def send_ocr_delivery(job_id: str, generation: int, *, mode: str, contract) -> str:
    if contract["mode"] != mode or not delivery_body_matches(contract["body"], job_id, generation):
        raise OcrDispatchConfigurationError("Original delivery identity differs")
    return send_captured(contract)


def dispatch_ocr_job(job_id: str, generation: int) -> None:
    from src.services.implementation_price_ocr_delivery_service import dispatch_delivery
    dispatch_delivery(job_id, generation)
