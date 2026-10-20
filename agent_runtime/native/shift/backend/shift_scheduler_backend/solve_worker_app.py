"""Dedicated authenticated solve worker; no database startup or public cutover."""

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .solve_delivery_dispatch_service import CloudTaskConfig


PROCESS_PATH = "/worker/solve-deliveries/process"
ENV_FIELDS = {
    "project": "SHIFT_SOLVE_TASKS_PROJECT", "location": "SHIFT_SOLVE_TASKS_LOCATION",
    "queue": "SHIFT_SOLVE_TASKS_QUEUE", "url": "SHIFT_SOLVE_TASKS_TARGET_URL",
    "service_account": "SHIFT_SOLVE_TASKS_SERVICE_ACCOUNT",
    "audience": "SHIFT_SOLVE_TASKS_AUDIENCE", "issuer": "SHIFT_SOLVE_TASKS_ISSUER",
    "subject": "SHIFT_SOLVE_TASKS_SUBJECT", "deadline_seconds": "SHIFT_SOLVE_TASKS_DEADLINE_SECONDS",
}


def _utc_clock():
    return datetime.now(UTC).replace(tzinfo=None)


def _required(name):
    value = os.environ.get(name)
    if not value or value != value.strip():
        raise ValueError("shift_worker_configuration_required")
    return value


def _configuration(config, lease_seconds):
    try:
        if config is None:
            values = {key: _required(name) for key, name in ENV_FIELDS.items()}
            values["deadline_seconds"] = int(values["deadline_seconds"])
            config = CloudTaskConfig(**values)
        if lease_seconds is None:
            lease_seconds = int(_required("SHIFT_SOLVE_TASKS_LEASE_SECONDS"))
        if not isinstance(config, CloudTaskConfig):
            raise ValueError("shift_worker_configuration_invalid")
        config.validate()
        target = urlsplit(config.url)
        if target.path != PROCESS_PATH or target.query:
            raise ValueError("shift_worker_target_path_invalid")
        if type(lease_seconds) is not int or lease_seconds <= config.deadline_seconds:
            raise ValueError("shift_worker_lease_must_cover_deadline_and_elapsed_time")
        timedelta(seconds=lease_seconds)
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(503, "shift_worker_configuration_required") from error
    return config, lease_seconds


def _session_factory():
    # Invoked by process_delivery only after identity and body checks.
    try:
        database_url = _required("DATABASE_URL")
    except ValueError as error:
        raise HTTPException(503, "shift_worker_database_configuration_required") from error
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError
    try:
        make_url(database_url)
    except (ArgumentError, ValueError) as error:
        raise HTTPException(503, "shift_worker_database_configuration_invalid") from error
    from .db import get_session_factory
    try:
        factory = get_session_factory()
    except ArgumentError as error:
        raise HTTPException(503, "shift_worker_database_configuration_invalid") from error
    return factory()


def create_app(*, config=None, lease_seconds=None, session_factory=None, clock=None, verifier=None):
    worker = FastAPI(title="Shift solve worker", docs_url=None, redoc_url=None, openapi_url=None)

    @worker.get("/health")
    def health():
        return {"status": "alive"}

    @worker.post(PROCESS_PATH)
    async def process(request: Request):
        selected, duration = _configuration(config, lease_seconds)
        actual_clock = _utc_clock if clock is None else clock
        now = actual_clock()
        if type(now) is not datetime or now.tzinfo is not None:
            raise HTTPException(503, "shift_worker_utc_clock_required")
        try:
            lease_until = now + timedelta(seconds=duration)
        except OverflowError as error:
            raise HTTPException(503, "shift_worker_lease_invalid") from error
        actual_verifier = verifier
        if actual_verifier is None:
            from google.oauth2.id_token import verify_oauth2_token
            actual_verifier = verify_oauth2_token
        from .solve_delivery_worker import process_delivery
        return await run_in_threadpool(process_delivery, await request.body(), request.headers.get("authorization"),
            config=selected, session_factory=_session_factory if session_factory is None else session_factory,
            clock=actual_clock, lease_until=lease_until, verifier=actual_verifier)

    @worker.post("/worker/solve-deliveries/recover")
    async def recover(request: Request):
        selected, _ = _configuration(config, lease_seconds)
        try:
            trust = replace(selected,
                service_account=_required("SHIFT_SOLVE_RECOVERY_SERVICE_ACCOUNT"),
                audience=_required("SHIFT_SOLVE_RECOVERY_AUDIENCE"),
                issuer=_required("SHIFT_SOLVE_RECOVERY_ISSUER"),
                subject=_required("SHIFT_SOLVE_RECOVERY_SUBJECT"))
            trust.validate()
            limit = int(_required("SHIFT_SOLVE_RECOVERY_BATCH_LIMIT"))
            if limit <= 0:
                raise ValueError("shift_recovery_limit_invalid")
        except (ValueError, TypeError) as error:
            raise HTTPException(503, "shift_recovery_configuration_required") from error
        actual_verifier = verifier
        if actual_verifier is None:
            from google.oauth2.id_token import verify_oauth2_token
            actual_verifier = verify_oauth2_token
        body = await request.body()
        authorization = request.headers.get("authorization")
        def authenticated_recovery():
            from .solve_delivery_auth import require_identity
            from .solve_delivery_recovery import recover_expired_deliveries
            require_identity(authorization, config=trust, verifier=actual_verifier)
            try:
                value = json.loads(body)
            except (ValueError, TypeError) as error:
                raise HTTPException(400, "shift_recovery_empty_object_required") from error
            if type(value) is not dict or value:
                raise HTTPException(400, "shift_recovery_empty_object_required")
            actual_clock = _utc_clock if clock is None else clock
            now = actual_clock()
            if type(now) is not datetime or now.tzinfo is not None:
                raise HTTPException(503, "shift_worker_utc_clock_required")
            return recover_expired_deliveries(
                session_factory=_session_factory if session_factory is None else session_factory,
                config=selected, now=now, limit=limit)
        return await run_in_threadpool(authenticated_recovery)

    return worker


app = create_app()
