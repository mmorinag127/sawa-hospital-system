"""Durable transport intent; transport retries never create a business attempt."""
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse
from copy import deepcopy
import json
import math

from sqlalchemy import func, select, update

from src.db import session_scope
from src.models.implementation_price_ocr_job import ImplementationPriceOcrJob
from src.services import implementation_price_ocr_dispatch_service as dispatch
from src.services import implementation_price_ocr_execution_guard as execution


@contextmanager
def job_write(job_id):
    with session_scope() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = session.execute(select(ImplementationPriceOcrJob).where(
            ImplementationPriceOcrJob.id == job_id).with_for_update()).scalar_one_or_none()
        if job is None:
            from src.services.implementation_price_ocr_job_service import OcrJobNotFoundError
            raise OcrJobNotFoundError(f"ocr job not found: {job_id}")
        yield session, job


def intent(job):
    value = job.delivery_json
    if not isinstance(value, dict) or type(value.get("generation")) is not int or value["generation"] < 1:
        raise dispatch.OcrDispatchConfigurationError("OCR job delivery intent is unresolved")
    if type(value.get("expected_attempt_count")) is not int or value["expected_attempt_count"] < 0:
        raise dispatch.OcrDispatchConfigurationError("OCR delivery attempt is unresolved")
    if value.get("state") not in {"pending", "sent", "claimed"}:
        raise dispatch.OcrDispatchConfigurationError("OCR delivery state is unresolved")
    contract = value.get("contract")
    if value.get("mode") not in {"local", "cloud_tasks"} or not isinstance(contract, dict) or contract.get("mode") != value["mode"]:
        raise dispatch.OcrDispatchConfigurationError("Original OCR delivery contract is unavailable")
    required = {"schema", "url", "method", "body", "mode"}
    required |= {"task_name", "service_account", "audience", "issuer", "subject", "deadline_seconds"} if value["mode"] == "cloud_tasks" else {"timeout_seconds", "token_digest"}
    if not required.issubset(contract) or type(contract["schema"]) is not int or contract["schema"] != 1 or contract["method"] != "POST" or not dispatch.delivery_body_matches(contract["body"], job.id, value["generation"]):
        raise dispatch.OcrDispatchConfigurationError("Original OCR delivery contract is invalid")
    if not isinstance(contract["url"], str) or urlparse(contract["url"]).scheme not in {"http", "https"}:
        raise dispatch.OcrDispatchConfigurationError("Original worker URL is invalid")
    if value["mode"] == "cloud_tasks" and (type(contract["deadline_seconds"]) is not int or not 1 <= contract["deadline_seconds"] <= 1800 or any(not isinstance(contract[key], str) or not contract[key] for key in ("task_name", "service_account", "audience", "issuer", "subject"))):
        raise dispatch.OcrDispatchConfigurationError("Original Cloud Tasks contract is invalid")
    if value["mode"] == "local" and (type(contract["timeout_seconds"]) not in {float, int} or not math.isfinite(contract["timeout_seconds"]) or contract["timeout_seconds"] <= 0 or not isinstance(contract["token_digest"], str) or len(contract["token_digest"]) != 64):
        raise dispatch.OcrDispatchConfigurationError("Original local delivery contract is invalid")
    if value["mode"] == "cloud_tasks" and not contract["task_name"].endswith(f"/tasks/ocr-{job.id}-delivery-{value['generation']}"):
        raise dispatch.OcrDispatchConfigurationError("Original task identity is invalid")
    return dict(value)


def require_transport_source(job, mode):
    if mode not in {"local", "cloud_tasks"}:
        raise dispatch.OcrDispatchConfigurationError("OCR delivery transport mode is unresolved")
    if mode == "cloud_tasks" and urlparse(job.stored_pdf_ref).scheme != "gs":
        raise dispatch.OcrDispatchConfigurationError("Cloud OCR delivery requires an explicitly stored shared GCS source")


def new_intent(job, *, mode):
    require_transport_source(job, mode)
    execution.source_contract(job)
    previous = intent(job)["generation"] if job.delivery_json is not None else 0
    if previous:
        saved = intent(job)
        if saved["mode"] != mode:
            raise dispatch.OcrDispatchConfigurationError("Original transport mode changed")
        contract = deepcopy(saved["contract"])
        contract["body"] = json.dumps({"job_id": job.id, "delivery_generation": previous + 1})
        if mode == "cloud_tasks":
            contract["task_name"] = contract["task_name"].rsplit("/tasks/", 1)[0] + f"/tasks/ocr-{job.id}-delivery-{previous + 1}"
    else:
        contract = dispatch.capture_contract(job.id, 1, mode)
    job.delivery_json = {"generation": previous + 1,
        "expected_attempt_count": int(job.attempt_count), "state": "pending", "mode": mode,
        "contract": contract, "input_digest": job.input_digest, "business_attempt_id": job.business_attempt_id}


def renew_missing_delivery(job_id, observed):
    with execution.execution_mutex(job_id) as acquired:
        if not acquired:
            return "noop_execution_active"
        return _renew_missing_delivery(job_id, observed)


def _renew_missing_delivery(job_id, observed):
    if observed.get("mode") != "cloud_tasks":
        raise dispatch.OcrDispatchConfigurationError("Only exact Cloud Tasks loss can renew this transport")
    with job_write(job_id) as (_session, job):
        current = intent(job)
        expires = job.lease_expires_at
        recoverable = job.status == "queued" or (job.status == "running"
            and expires is not None and expires.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc))
        if (not execution.same_json(current, observed) or not recoverable
                or job.attempt_count != observed["expected_attempt_count"]
                or current["state"] not in {"pending", "sent"}):
            return "noop_superseded"
        new_intent(job, mode="cloud_tasks")
        # The committed pending generation is independently recoverable even
        # if this process exits before its next transport call.
    return "recovery_pending"


def dispatch_delivery(job_id, generation):
    if type(generation) is not int or generation < 1:
        raise dispatch.OcrDispatchConfigurationError("Positive explicit delivery generation is required")
    try:
        with job_write(job_id) as (_session, job):
            delivery = intent(job)
            if delivery["generation"] != generation or delivery["state"] not in {"pending", "sent"} or job.status not in {"queued", "running"}:
                return "noop_superseded"
            execution.source_contract(job)
            mode = delivery["mode"]
            if delivery.get("input_digest") != job.input_digest or delivery.get("business_attempt_id") != job.business_attempt_id:
                raise dispatch.OcrDispatchConfigurationError("Original delivery source/attempt changed")
            require_transport_source(job, mode)
            task_name = delivery["contract"].get("task_name")
            if task_name is not None and (not isinstance(task_name, str)
                    or not task_name.endswith(f"/tasks/ocr-{job_id}-delivery-{generation}")):
                raise dispatch.OcrDispatchConfigurationError("OCR delivery task identity is invalid")
        if mode == "cloud_tasks":
            provider_state = dispatch.inspect_cloud_task(task_name, contract=delivery["contract"])
            if provider_state == "missing" and delivery["state"] == "sent":
                return renew_missing_delivery(job_id, delivery)
            if provider_state == "missing":
                provider_state = dispatch.send_ocr_delivery(job_id, generation, mode=mode, contract=delivery["contract"])
                if provider_state == "missing":
                    # AlreadyExists plus authoritative NotFound is a tombstone,
                    # not acceptance of an executable task.
                    return renew_missing_delivery(job_id, delivery)
            if provider_state != "exists":
                raise dispatch.OcrDispatchError("Cloud Tasks delivery state is unresolved")
        else:
            if delivery["state"] == "sent":
                raise dispatch.OcrDispatchError("Local accepted delivery remains unclaimed; operator inspection required")
            dispatch.send_ocr_delivery(job_id, generation, mode=mode, contract=delivery["contract"])
    except (dispatch.OcrDispatchConfigurationError, dispatch.OcrDispatchError) as exc:
        from src.services.implementation_price_ocr_job_service import record_dispatch_failure
        record_dispatch_failure(job_id, str(exc), delivery_generation=generation)
        raise
    with job_write(job_id) as (_session, job):
        current = intent(job)
        if current["generation"] == generation and current["state"] == "pending":
            job.delivery_json = {**current, "state": "sent"}
            job.dispatch_error_message = None
    return "dispatched"


def reserve_delivery_visits(*, limit, now):
    """Commit fair visit reservations before transport, even if the caller crashes."""
    with session_scope() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        state = ImplementationPriceOcrJob.delivery_json["state"].as_string()
        query = select(ImplementationPriceOcrJob.id).where(
            ((ImplementationPriceOcrJob.status == "queued") & (state.in_(["pending", "sent"]) | state.is_(None))) | (
                (ImplementationPriceOcrJob.status == "running") & (ImplementationPriceOcrJob.lease_expires_at <= now)
                & (state.in_(["pending", "sent", "claimed"]) | state.is_(None)))
        )
        visits = func.coalesce(ImplementationPriceOcrJob.delivery_visit_count, 0)
        ids = session.execute(query.order_by(visits, ImplementationPriceOcrJob.id)
            .limit(limit).with_for_update(skip_locked=True)).scalars().all()
        if ids:
            session.execute(update(ImplementationPriceOcrJob)
                .where(ImplementationPriceOcrJob.id.in_(ids))
                .values(delivery_visit_count=visits + 1,
                    updated_at=ImplementationPriceOcrJob.updated_at)
                .execution_options(synchronize_session=False))
    return ids


def drain_deliveries(*, limit=100):
    now = datetime.now(timezone.utc)
    ids = reserve_delivery_visits(limit=limit, now=now)
    outcomes = []
    for job_id in ids:
        try:
            with execution.execution_mutex(job_id) as acquired:
                if not acquired:
                    outcomes.append({"job_id": job_id, "action": "noop_execution_active"})
                    continue
                with job_write(job_id) as (_session, job):
                    if job.status not in {"queued", "running"}:
                        continue
                    execution.source_contract(job)
                    current = intent(job)
                    if job.status == "running":
                        expires = job.lease_expires_at
                        if expires is None or expires.replace(tzinfo=timezone.utc) > now:
                            continue
                        if current["state"] == "claimed":
                            new_intent(job, mode=current["mode"])
                    generation = intent(job)["generation"]
            action = dispatch_delivery(job_id, generation)
            outcomes.append({"job_id": job_id, "generation": generation, "action": action})
        except (dispatch.OcrDispatchConfigurationError, dispatch.OcrDispatchError, ValueError) as exc:
            outcomes.append({"job_id": job_id, "action": "dispatch_failed", "error": str(exc)})
    return {"deliveries": outcomes}
