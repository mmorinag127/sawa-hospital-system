from __future__ import annotations

from src.staging_data import school_runtime_setting as _runtime_setting


import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from hashlib import sha256
from src.services import implementation_price_ocr_execution_guard as execution

from sqlalchemy import select, update

from src.db import session_scope
from src.models.implementation_price_ocr_job import ImplementationPriceOcrJob
from src.services import implementation_price_storage, implementation_price_table_ocr_service
from src.services.implementation_price_scope_service import require_facility, require_same_facility
from src.services import implementation_price_ocr_delivery_service as delivery_service
from src.services.implementation_price_ocr_dispatch_service import configured_mode, OcrDispatchConfigurationError, _required_env


JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
_JOB_STATUSES = {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED}
_PROVIDER = "yomitoku"


class OcrJobNotFoundError(FileNotFoundError):
    pass


class OcrJobConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrJobProcessResult:
    job: dict[str, Any]
    action: str


@dataclass(frozen=True)
class _OcrJobFinalizeResult:
    job: dict[str, Any]
    applied: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _job_or_raise(session, job_id: str) -> ImplementationPriceOcrJob:
    job = session.get(ImplementationPriceOcrJob, job_id)
    if job is None:
        raise OcrJobNotFoundError(f"ocr job not found: {job_id}")
    return job


_UNSPECIFIED_DOCUMENT = object()


def validate_original_document(session, document, *, facility_id, document_type="cooking_arrangement", job=None):
    with session.no_autoflush:
        return _validate_original_document(session, document, facility_id=facility_id, document_type=document_type, job=job)


def _validate_original_document(session, document, *, facility_id, document_type, job):
    if document is None or not execution.same_json(document.facility_id, facility_id) or sha256(document.content).hexdigest() != document.sha256:
        raise ValueError("Original document school/content digest differs")
    if document.ocr_job_id is None:
        if job is not None:
            raise ValueError("Original document job link is missing")
        return
    if job is None:
        with session.no_autoflush:
            job = session.get(ImplementationPriceOcrJob, document.ocr_job_id)
    if job is None:
        raise ValueError("Original document linked job is unavailable")
    source = execution.source_contract(job)
    if not execution.same_json(job.document_type, document_type) or not execution.same_json(job.facility_id, facility_id):
        raise ValueError("Original document linked school/type differs")
    if not execution.same_json(source["original_document"], {"id": document.id, "facility_id": document.facility_id,
            "sha256": document.sha256, "filename": document.filename, "ocr_job_id": document.ocr_job_id}):
        raise ValueError("Original document recorded identity differs")


def pending_ocr_source(session, job_id, document_id=_UNSPECIFIED_DOCUMENT):
    from sqlalchemy import inspect
    from src.models.school_lunch_document import SchoolLunchDocument
    def matches(row, field, expected):
        state = inspect(row)
        attribute = state.attrs[field]
        values = list(attribute.history.added) + list(attribute.history.deleted)
        values.append(attribute.loaded_value)
        if field == "id" and state.identity:
            values.append(state.identity[0])
        return any(execution.same_json(value, expected) for value in values)
    document_ids = [] if document_id is _UNSPECIFIED_DOCUMENT else [document_id]
    for cached in session.identity_map.values():
        if isinstance(cached, ImplementationPriceOcrJob) and matches(cached, "id", job_id):
            source = inspect(cached).attrs.source_json.loaded_value
            if isinstance(source, dict) and "source_document_id" in source:
                document_ids.append(source["source_document_id"])
    for row in session.new | session.dirty | session.deleted:
        if isinstance(row, ImplementationPriceOcrJob) and matches(row, "id", job_id):
            return True
        if isinstance(row, SchoolLunchDocument) and (matches(row, "ocr_job_id", job_id)
                or any(matches(row, "id", identity) for identity in document_ids)):
            return True
    return False


def validated_successful_result(session, job_id, *, facility_id, document_type, document_id=_UNSPECIFIED_DOCUMENT, for_adoption=True):
    with session.no_autoflush:
        return _validated_successful_result(session, job_id, facility_id=facility_id, document_type=document_type,
            document_id=document_id, for_adoption=for_adoption)


def _validated_successful_result(session, job_id, *, facility_id, document_type, document_id, for_adoption):
    from src.models.school_lunch_document import SchoolLunchDocument
    from copy import deepcopy
    if for_adoption and pending_ocr_source(session, job_id, document_id):
        raise ValueError("Pending original source changes must be explicitly saved before adoption")
    with session.no_autoflush:
        if for_adoption and session.get_bind().dialect.name == "sqlite":
            session.execute(update(ImplementationPriceOcrJob).where(ImplementationPriceOcrJob.id == job_id)
                .values(id=ImplementationPriceOcrJob.id, updated_at=ImplementationPriceOcrJob.updated_at)
                .execution_options(synchronize_session=False))
        if for_adoption:
            job = session.execute(select(ImplementationPriceOcrJob).where(ImplementationPriceOcrJob.id == job_id)
                .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        else:
            job = session.get(ImplementationPriceOcrJob, job_id)
    if job is None:
        raise ValueError("Original OCR job is unavailable")
    source = execution.source_contract(job)
    recorded = source["source_document_id"]
    if for_adoption and recorded is not None and pending_ocr_source(session, job_id, recorded):
        raise ValueError("Pending original document changes must be explicitly saved before adoption")
    if not execution.same_json(job.facility_id, facility_id) or not execution.same_json(job.document_type, document_type):
        raise ValueError("Original OCR school/document type differs")
    if "source_document_id" not in source:
        raise ValueError("Original source document identity is absent")
    if document_id is not _UNSPECIFIED_DOCUMENT and not execution.same_json(recorded, document_id):
        raise ValueError("Original source document identity differs")
    if recorded is not None:
        if type(recorded) is not str or not recorded:
            raise ValueError("Original source document identity is malformed")
        with session.no_autoflush:
            if for_adoption:
                document = session.execute(select(SchoolLunchDocument).where(SchoolLunchDocument.id == recorded)
                    .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
            else:
                document = session.get(SchoolLunchDocument, recorded)
        validate_original_document(session, document, facility_id=facility_id, document_type=document_type, job=job)
    if job.status != JOB_STATUS_SUCCEEDED or not _terminal_owner_matches(job) or not isinstance(job.result_json, dict):
        raise ValueError("Original successful OCR terminal result is unavailable")
    return deepcopy(job.result_json)


def _result_payload(job: ImplementationPriceOcrJob) -> dict[str, Any] | None:
    from sqlalchemy.orm import object_session
    session = object_session(job)
    if session is None:
        return None
    try:
        return validated_successful_result(session, job.id, facility_id=job.facility_id, document_type=job.document_type, for_adoption=False)
    except ValueError:
        return None


def serialize_job(job: ImplementationPriceOcrJob) -> dict[str, Any]:
    try:
        execution.source_contract(job)
        input_version = job.input_digest
        source_error = None
    except ValueError as exc:
        input_version = None
        source_error = str(exc)
    if job.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED} and job.source_json is not None and not _terminal_owner_matches(job):
        source_error = "Original terminal receipt is unavailable or changed"
    if job.status == JOB_STATUS_SUCCEEDED and not isinstance(job.result_json, dict):
        source_error = "Original successful result is unavailable"
    if job.status == JOB_STATUS_SUCCEEDED:
        from sqlalchemy.orm import object_session
        try:
            session = object_session(job)
            if session is None:
                raise ValueError("Original caller session is unavailable")
            validated_successful_result(session, job.id, facility_id=job.facility_id, document_type=job.document_type, for_adoption=False)
        except ValueError as exc:
            source_error = str(exc)
    business_error = str(job.error_message or "") or None
    dispatch_error = str(job.dispatch_error_message or "") or None
    delivery_generation = (job.delivery_json or {}).get("generation")
    status = {
        "domain": "school-lunch", "jobId": job.id,
        "inputVersion": input_version, "inputReference": job.stored_pdf_ref,
        "attemptIdentity": job.attempt_count,
        "state": job.status if job.status in _JOB_STATUSES and source_error is None else None,
        "sourceState": job.status,
        "sourceLabel": "未実行" if job.status == JOB_STATUS_QUEUED and job.attempt_count == 0 else "",
        "failure": {"code": "source_contract_unavailable", "message": source_error} if source_error else ({"code": None, "message": business_error} if business_error else None),
        "artifactReference": None, "deliveryGeneration": delivery_generation,
    }
    return {
        "job_status": status,
        "id": job.id,
        "facility_id": job.facility_id,
        "document_type": job.document_type,
        "filename": job.filename,
        "status": job.status,
        "provider": job.provider,
        "review_required": bool(job.review_required),
        "stored_pdf_ref": job.stored_pdf_ref,
        "result": _result_payload(job) if job.status == JOB_STATUS_SUCCEEDED else None,
        "error": business_error,
        "dispatch_error": dispatch_error,
        "attempt_count": job.attempt_count,
        "delivery_generation": delivery_generation,
        "source_contract": dict(job.source_json) if isinstance(job.source_json, dict) else None,
        "input_digest": job.input_digest,
        "business_attempt_id": job.business_attempt_id,
        "execution_token": job.execution_token,
        "source_contract_error": source_error,
        "result_provenance": dict(job.result_owner_json) if isinstance(job.result_owner_json, dict) else None,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "lease_expires_at": job.lease_expires_at,
        "finished_at": job.finished_at,
    }


def create_ocr_job(*, facility_id: int, document_type: str, filename: str, pdf_bytes: bytes, source_document=None,
                   dpi: int | None = None, device: str | None = None) -> dict[str, Any]:
    mode = configured_mode()
    if mode == "cloud_tasks":
        _required_env("IMPLEMENTATION_PRICE_OCR_GCS_BUCKET")
    normalized_type = implementation_price_table_ocr_service.normalize_document_type(document_type)
    if not pdf_bytes:
        raise ValueError("pdf_bytes is required")
    if source_document is not None and source_document.facility_id != facility_id:
        raise ValueError("source document belongs to a different school")
    with session_scope() as session:
        require_facility(session, facility_id)

    job_id = uuid4().hex
    storage = implementation_price_storage.get_write_storage()
    stored_pdf_ref = storage.save_pdf(job_id=job_id, filename=filename, pdf_bytes=pdf_bytes)
    effective_dpi = int(_runtime_setting("IMPLEMENTATION_PRICE_OCR_DPI", "200")) if dpi is None else dpi
    effective_device = (_runtime_setting("IMPLEMENTATION_PRICE_OCR_DEVICE", "cpu").strip() or "cpu") if device is None else device
    source = {"schema": 1, "pdf_reference": stored_pdf_ref, "pdf_sha256": sha256(pdf_bytes).hexdigest(),
              "storage_version": implementation_price_storage.storage_identity(stored_pdf_ref),
              "document_type": normalized_type, "filename": str(filename or "").strip(),
              "facility_id": facility_id, "dpi": effective_dpi, "device": effective_device, "provider": _PROVIDER,
              "source_document_id": source_document.id if source_document is not None else None,
              "original_document": {"id": source_document.id, "facility_id": source_document.facility_id,
                  "sha256": source_document.sha256, "filename": source_document.filename, "ocr_job_id": job_id}
                  if source_document is not None else None}
    now = _utcnow()
    with session_scope() as session:
        job = ImplementationPriceOcrJob(
            id=job_id,
            facility_id=facility_id,
            document_type=normalized_type,
            filename=str(filename or "").strip(),
            status=JOB_STATUS_QUEUED,
            provider=_PROVIDER,
            review_required=True,
            stored_pdf_ref=stored_pdf_ref,
            result_json=None,
            error_message=None,
            dispatch_error_message=None,
            attempt_count=0,
            source_json=source,
            input_digest=execution.digest(source),
            business_attempt_id=uuid4().hex,
            created_at=now,
            updated_at=now,
            queued_at=now,
            started_at=None,
            lease_expires_at=None,
            finished_at=None,
        )
        session.add(job)
        execution.source_contract(job)
        delivery_service.new_intent(job, mode=mode)
        session.flush()
        if source_document is not None:
            source_document.ocr_job_id = job_id
            session.add(source_document)
            session.flush()
        session.refresh(job)
        response = serialize_job(job)
        if source_document is not None:
            response["source_document_id"] = source_document.id
            response["source_document_ocr_job_id"] = source_document.ocr_job_id
        return response


def get_ocr_job(job_id: str, *, facility_id: int) -> dict[str, Any]:
    with session_scope() as session:
        require_facility(session, facility_id)
        job = _job_or_raise(session, job_id)
        require_same_facility(job.facility_id, facility_id)
        return serialize_job(job)


def retry_failed_ocr_job(job_id: str, *, facility_id: int) -> dict[str, Any]:
    mode = configured_mode()
    now = _utcnow()
    with delivery_service.job_write(job_id) as (session, job):
        require_facility(session, facility_id)
        require_same_facility(job.facility_id, facility_id)
        execution.source_contract(job)
        if job.status == JOB_STATUS_SUCCEEDED:
            raise OcrJobConflictError("succeeded jobs cannot be retried")
        if job.status == JOB_STATUS_QUEUED and not job.dispatch_error_message:
            saved = delivery_service.intent(job)
            if (saved["state"] == "pending" and saved["mode"] == mode
                    and saved.get("input_digest") == job.input_digest
                    and saved.get("business_attempt_id") == job.business_attempt_id
                    and saved["expected_attempt_count"] == job.attempt_count):
                # Resume the committed transport, without a new business attempt
                # or generation. Dispatch still verifies the exact provider task.
                return serialize_job(job)
        if job.status == JOB_STATUS_QUEUED and job.dispatch_error_message:
            delivery_service.new_intent(job, mode=mode)
            job.dispatch_error_message = None
            job.updated_at = now
            session.flush()
            session.refresh(job)
            return serialize_job(job)
        if job.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
            raise OcrJobConflictError("only failed or dispatch-failed queued jobs can be retried")
        job.status = JOB_STATUS_QUEUED
        job.business_attempt_id = uuid4().hex
        job.execution_token = None
        job.error_message = None
        job.dispatch_error_message = None
        job.result_json = None
        job.result_owner_json = None
        job.review_required = True
        job.queued_at = now
        job.started_at = None
        job.lease_expires_at = None
        job.finished_at = None
        job.updated_at = now
        delivery_service.new_intent(job, mode=mode)
        session.flush()
        session.refresh(job)
        return serialize_job(job)


def _claim_queued_job(job_id: str, *, delivery_generation: int) -> tuple[dict[str, Any], bool]:
    now = _utcnow()
    lease_seconds = max(int(os.getenv("IMPLEMENTATION_PRICE_OCR_LEASE_SECONDS", "900")), 1)
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    with delivery_service.job_write(job_id) as (session, job):
        require_facility(session, job.facility_id)
        delivery = delivery_service.intent(job)
        if delivery_generation > delivery["generation"]:
            raise OcrJobConflictError("unknown future delivery generation")
        if delivery_generation != delivery["generation"] or delivery["state"] == "claimed" or job.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED}:
            return serialize_job(job), False
        execution.source_contract(job)
        if delivery.get("input_digest") != job.input_digest or delivery.get("business_attempt_id") != job.business_attempt_id:
            raise ValueError("Original delivery input/attempt differs")
        if delivery.get("mode") not in {"local", "cloud_tasks"}:
            raise OcrDispatchConfigurationError("OCR delivery transport is unresolved")
        delivery_service.require_transport_source(job, delivery["mode"])
        result = session.execute(
            update(ImplementationPriceOcrJob)
            .execution_options(synchronize_session=False)
            .where(
                ImplementationPriceOcrJob.id == job_id,
                ImplementationPriceOcrJob.attempt_count == delivery["expected_attempt_count"],
                ImplementationPriceOcrJob.input_digest == job.input_digest,
                ImplementationPriceOcrJob.business_attempt_id == job.business_attempt_id,
                ImplementationPriceOcrJob.execution_token == job.execution_token,
                ImplementationPriceOcrJob.delivery_json["generation"].as_integer() == delivery_generation,
                (
                    (ImplementationPriceOcrJob.status == JOB_STATUS_QUEUED)
                    | (
                        (ImplementationPriceOcrJob.status == JOB_STATUS_RUNNING)
                        & (ImplementationPriceOcrJob.lease_expires_at <= now)
                    )
                ),
            )
            .values(
                status=JOB_STATUS_RUNNING,
                started_at=now,
                finished_at=None,
                updated_at=now,
                error_message=None,
                dispatch_error_message=None,
                lease_expires_at=lease_expires_at,
                attempt_count=ImplementationPriceOcrJob.attempt_count + (1 if job.execution_token is None else 0),
                execution_token=uuid4().hex,
            )
        )
        if result.rowcount:
            job.delivery_json = {**delivery, "state": "claimed"}
            job = _job_or_raise(session, job_id)
            session.flush()
            session.refresh(job)
            return serialize_job(job), True

        job = _job_or_raise(session, job_id)
        if job.status == JOB_STATUS_SUCCEEDED:
            return serialize_job(job), False
        if job.status == JOB_STATUS_RUNNING:
            return serialize_job(job), False
        if job.status == JOB_STATUS_FAILED:
            raise OcrJobConflictError("failed job must be retried explicitly before processing")
        raise OcrJobConflictError(f"job is not claimable from status {job.status}")


def _finalize_job_success(
    job_id: str,
    result_payload: dict[str, Any],
    *,
    expected_attempt_count: int,
    owner: dict[str, Any],
) -> _OcrJobFinalizeResult:
    now = _utcnow()
    with session_scope() as session:
        result = session.execute(
            update(ImplementationPriceOcrJob)
            .execution_options(synchronize_session=False)
            .where(
                ImplementationPriceOcrJob.id == job_id,
                ImplementationPriceOcrJob.status == JOB_STATUS_RUNNING,
                ImplementationPriceOcrJob.attempt_count == expected_attempt_count,
                *_owner_conditions(owner),
            )
            .values(
                status=JOB_STATUS_SUCCEEDED,
                review_required=bool(result_payload.get("review_required", True)),
                result_json=result_payload,
                result_owner_json={**{key: owner[key] for key in ("input_digest", "business_attempt_id", "execution_token", "delivery_generation")},
                                   "status": JOB_STATUS_SUCCEEDED, "attempt_count": expected_attempt_count},
                error_message=None,
                finished_at=now,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        job = _job_or_raise(session, job_id)
        session.flush()
        session.refresh(job)
        return _OcrJobFinalizeResult(job=serialize_job(job), applied=bool(result.rowcount))


def _finalize_job_failure(
    job_id: str,
    error_message: str,
    *,
    expected_attempt_count: int,
    owner: dict[str, Any],
) -> _OcrJobFinalizeResult:
    now = _utcnow()
    with session_scope() as session:
        result = session.execute(
            update(ImplementationPriceOcrJob)
            .execution_options(synchronize_session=False)
            .where(
                ImplementationPriceOcrJob.id == job_id,
                ImplementationPriceOcrJob.status == JOB_STATUS_RUNNING,
                ImplementationPriceOcrJob.attempt_count == expected_attempt_count,
                *_owner_conditions(owner),
            )
            .values(
                status=JOB_STATUS_FAILED,
                error_message=str(error_message or "OCR processing failed"),
                result_json=None,
                result_owner_json={**{key: owner[key] for key in ("input_digest", "business_attempt_id", "execution_token", "delivery_generation")},
                                   "status": JOB_STATUS_FAILED, "attempt_count": expected_attempt_count},
                review_required=True,
                finished_at=now,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        job = _job_or_raise(session, job_id)
        session.flush()
        session.refresh(job)
        return _OcrJobFinalizeResult(job=serialize_job(job), applied=bool(result.rowcount))


def _owner_conditions(owner):
    return (ImplementationPriceOcrJob.input_digest == owner["input_digest"],
            ImplementationPriceOcrJob.business_attempt_id == owner["business_attempt_id"],
            ImplementationPriceOcrJob.execution_token == owner["execution_token"],
            ImplementationPriceOcrJob.lease_expires_at > _utcnow(),
            ImplementationPriceOcrJob.delivery_json["generation"].as_integer() == owner["delivery_generation"])


def _terminal_owner_matches(job):
    return bool(job.execution_token) and execution.same_json(job.result_owner_json, {
        "input_digest": job.input_digest, "business_attempt_id": job.business_attempt_id,
        "execution_token": job.execution_token, "delivery_generation": (job.delivery_json or {}).get("generation"),
        "status": job.status, "attempt_count": job.attempt_count})


def _validate_delivery_read(job, generation):
    source = execution.source_contract(job)
    delivery = delivery_service.intent(job)
    if (type(generation) is not int or generation != delivery["generation"]
        or delivery.get("input_digest") != job.input_digest or delivery.get("business_attempt_id") != job.business_attempt_id):
        raise OcrJobConflictError("Original delivery source/attempt/generation differs")
    if job.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED} and not _terminal_owner_matches(job):
        raise OcrJobConflictError("Original persisted terminal receipt is unavailable")
    return source


def _active_duplicate(job):
    expires = job.lease_expires_at
    return (job.status == JOB_STATUS_RUNNING and bool(job.execution_token)
            and expires is not None and expires.replace(tzinfo=timezone.utc) > _utcnow()
            and delivery_service.intent(job)["state"] == "claimed")


def process_ocr_job(job_id: str, *, delivery_generation: int, dpi=None, device=None) -> OcrJobProcessResult:
    with session_scope() as session:
        _validate_delivery_read(_job_or_raise(session, job_id), delivery_generation)
    with execution.execution_mutex(job_id) as acquired:
        if not acquired:
            with session_scope() as session:
                job = _job_or_raise(session, job_id)
                _validate_delivery_read(job, delivery_generation)
                if not _active_duplicate(job):
                    raise OcrJobConflictError("Original execution is busy without an active duplicate receipt")
                return OcrJobProcessResult(job=serialize_job(job), action="noop_execution_active")
        return _process_owned_ocr_job(job_id, delivery_generation=delivery_generation, dpi=dpi, device=device)


def _process_owned_ocr_job(
    job_id: str,
    *,
    delivery_generation: int,
    dpi: int | None = None,
    device: str | None = None,
) -> OcrJobProcessResult:
    if type(delivery_generation) is not int or delivery_generation < 1:
        raise ValueError("positive delivery_generation is required")
    claimed_job, claimed = _claim_queued_job(job_id, delivery_generation=delivery_generation)
    if not claimed:
        with session_scope() as session:
            job = _job_or_raise(session, job_id)
            _validate_delivery_read(job, delivery_generation)
            if job.status not in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED} and not _active_duplicate(job):
                raise OcrJobConflictError("Delivery has no original persisted terminal or active receipt")
        return OcrJobProcessResult(job=claimed_job, action="noop_duplicate")

    try:
        source = claimed_job["source_contract"]
        if (dpi is not None and dpi != source["dpi"]) or (device is not None and device != source["device"]):
            raise ValueError("Execution parameters differ from original captured input")
        pdf_bytes = implementation_price_storage.load_original(source)
        result_payload = implementation_price_table_ocr_service.extract_yomitoku_candidates(
            pdf_bytes=pdf_bytes,
            document_type=claimed_job["document_type"],
            filename=claimed_job["filename"],
            dpi=source["dpi"],
            device=source["device"],
        )
        if not isinstance(result_payload, dict):
            raise ValueError("Recognition returned no original result object")
    except Exception as exc:  # noqa: BLE001
        finalized = _finalize_job_failure(
            job_id,
            str(exc),
            expected_attempt_count=claimed_job["attempt_count"],
            owner=claimed_job,
        )
        if not finalized.applied:
            raise OcrJobConflictError("Original failure ownership was refused; no terminal ACK")
        return OcrJobProcessResult(
            job=finalized.job,
            action="processed_failed" if finalized.applied else "noop_superseded",
        )
    finalized = _finalize_job_success(
        job_id,
        result_payload,
        expected_attempt_count=claimed_job["attempt_count"],
        owner=claimed_job,
    )
    if not finalized.applied:
        raise OcrJobConflictError("Original success ownership was refused; no terminal ACK")
    return OcrJobProcessResult(
        job=finalized.job,
        action="processed_succeeded" if finalized.applied else "noop_superseded",
    )


def record_dispatch_failure(job_id: str, error_message: str, *, delivery_generation: int) -> dict[str, Any]:
    now = _utcnow()
    with delivery_service.job_write(job_id) as (session, job):
        delivery = delivery_service.intent(job)
        if job.status not in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING} or delivery["generation"] != delivery_generation or delivery["state"] != "pending":
            return serialize_job(job)
        job.dispatch_error_message = str(error_message or "OCR job dispatch failed")
        job.updated_at = now
        session.flush()
        session.refresh(job)
        return serialize_job(job)
