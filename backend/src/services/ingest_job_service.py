from src.staging_data import order_runtime_setting as _runtime_setting

from datetime import datetime, timedelta
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select, update, func, desc, exists, tuple_, String
from loguru import logger

from src.db import session_scope
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.services.ocr_job_service import (
    get_job_stale_at,
    is_job_recoverable,
    is_job_recovery_due,
    has_recorded_execution_input,
    is_job_stale,
)

_execution_lease = ContextVar("ingest_execution_lease", default=None)


def payload_digest(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@contextmanager
def ingest_execution_context(lease):
    token = _execution_lease.set(deepcopy(lease))
    try:
        yield
    finally:
        _execution_lease.reset(token)


def current_ingest_lease():
    return deepcopy(_execution_lease.get())


def ingest_terminal_receipt(session, job, captured):
    """Select one recorded completion contract; never reinterpret old jobs."""
    if job is None or not isinstance(captured, dict):
        return None
    metrics = job.metrics or {}
    mode = captured.get("ingest_ocr_mode")
    if mode not in (None, "registration_only", "hakodate"):
        raise ValueError("ingest_ocr_mode_unknown")
    protocol = (captured.get("ingest_lease") or {}).get("payload", {}).get("intake_protocol")
    if ((mode is None and protocol is not None)
            or (mode is not None and (protocol != "native_ingest_v2" or captured.get("intake_protocol") != protocol))):
        raise ValueError("intake_protocol_mismatch")
    if mode is None:
        kinds = [key for key in ("first_pass_terminal_receipt", "non_ocr_terminal_receipt") if metrics.get(key) is not None]
        if len(kinds) > 1:
            raise ValueError("ingest_terminal_receipt_ambiguous")
        return metrics.get(kinds[0]) if kinds else None
    if mode == "registration_only":
        if metrics.get("first_pass_terminal_receipt") is not None:
            raise ValueError("ingest_registration_receipt_kind_invalid")
        receipt = metrics.get("non_ocr_terminal_receipt")
        source_failure = metrics.get("source_failure_receipt")
        rerun = metrics.get("rerun_terminal_receipt")
        if source_failure is not None or rerun is not None:
            from src.services.workflow_attempt_guard_service import is_selected_pdf_failure
            if (not isinstance(source_failure, dict) or receipt != source_failure or rerun != source_failure
                    or not is_selected_pdf_failure(source_failure.get("error"))
                    or source_failure.get("job_id") != job.id
                    or source_failure.get("execution_id") != captured.get("execution_id")
                    or source_failure.get("order_id") != captured.get("order_id")
                    or source_failure.get("status") != "failed" or job.status != "failed"
                    or job.error_message != source_failure.get("error")
                    or source_failure.get("adopted") is not False or source_failure.get("evidence_run_id") is not None
                    or source_failure.get("workflow_attempt_id") is not None
                    or metrics.get("result_state") != "hard_failed"
                    or metrics.get("processing_stage") != "source_verification_failed"):
                raise ValueError("ingest_registration_source_failure_invalid")
            return receipt
        if receipt is None:
            return None
        document = captured.get("orders", {}).get(captured.get("order_id"), {}).get("document") or {}
        if (not isinstance(receipt, dict) or receipt.get("job_id") != job.id
                or receipt.get("execution_id") != captured.get("execution_id")
                or receipt.get("document_id") != document.get("id")
                or receipt.get("status") != job.status
                or receipt.get("result_state") != metrics.get("result_state")
                or not ((job.status == "done" and receipt.get("result_state") == "skipped" and job.error_message is None)
                        or (job.status == "failed" and receipt.get("result_state") == "hard_failed" and job.error_message))):
            raise ValueError("ingest_registration_terminal_invalid")
        return receipt
    receipt = metrics.get("rerun_terminal_receipt")
    source_failure = metrics.get("source_failure_receipt")
    for key in ("first_pass_terminal_receipt", "non_ocr_terminal_receipt"):
        other = metrics.get(key)
        if other is not None and not (key == "non_ocr_terminal_receipt" and source_failure is not None and other == source_failure == receipt):
            raise ValueError("ingest_terminal_receipt_ambiguous")
    if source_failure is not None:
        from src.services.workflow_attempt_guard_service import is_selected_pdf_failure
        if (source_failure != receipt or not is_selected_pdf_failure(source_failure.get("error"))
                or source_failure.get("adopted") is not False or source_failure.get("evidence_run_id") is not None
                or metrics.get("result_state") != "hard_failed" or job.error_message != source_failure.get("error")):
            raise ValueError("ingest_native_source_failure_invalid")
    if receipt is None:
        return None
    if (not isinstance(receipt, dict) or receipt.get("job_id") != job.id
            or receipt.get("execution_id") != captured.get("execution_id")
            or receipt.get("order_id") != captured.get("order_id")
            or receipt.get("status") != job.status or job.status not in {"done", "failed"}
            or receipt.get("error") != job.error_message
            or receipt.get("workflow_attempt_id") is not None):
        raise ValueError("ingest_native_terminal_owner_changed")
    evidence_id = receipt.get("evidence_run_id")
    if evidence_id is None:
        if job.status != "failed" or not job.error_message:
            raise ValueError("ingest_native_terminal_evidence_missing")
        return receipt
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    evidence = session.get(OrderOcrEvidenceRun, evidence_id)
    payload = evidence.payload_json if evidence is not None else None
    lineage = payload.get("_ocr_execution") if isinstance(payload, dict) else None
    original = captured.get("orders", {}).get(captured.get("order_id"), {})
    if (evidence is None or evidence.order_id != captured["order_id"]
            or evidence.template_version_id != original.get("order", {}).get("template_version_id")
            or evidence.producer_version != "hakodate_best_method_pipeline"
            or not isinstance(lineage, dict) or lineage.get("job_id") != job.id
            or lineage.get("nonworkflow_execution_id") != captured["execution_id"]
            or lineage.get("evidence_run_id") != evidence.id
            or lineage.get("selected_pdf_receipt") != captured.get("selected_pdf_receipt")):
        raise ValueError("ingest_native_terminal_evidence_foreign")
    if job.status == "done" and (receipt.get("error") is not None or job.error_message is not None
            or metrics.get("result_state") != "evidence_ready" or metrics.get("processing_stage") != "evidence_ready"):
        raise ValueError("ingest_native_terminal_state_invalid")
    if job.status == "failed" and (evidence.status != "diagnostic_only" or not job.error_message):
        raise ValueError("ingest_native_terminal_review_invalid")
    return receipt


def original_ingest_ocr_reference(lease):
    with session_scope() as session:
        if not lock_ingest_lease(session, lease):
            raise ValueError("ingest_attempt_changed")
        owner = OcrJob.metrics["ingest_lease"]
        ids = session.execute(select(OcrJob.id).where(owner["id"].as_string() == lease["id"],
            owner["claim_id"].as_string() == lease["recovery_claim_id"],
            owner["input_digest"].as_string() == lease["input_digest"])).scalars().all()
        if len(ids) != 1:
            raise ValueError("ingest_recovery_original_capture_missing")
        return ids[0]


def prepare_ingest_ocr_job(lease, job_id, payload):
    from src.services.ocr_job_service import _job_to_dict
    with session_scope() as session:
        if not lock_ingest_lease(session, lease):
            raise ValueError("ingest_attempt_changed")
        protocol = lease["payload"].get("intake_protocol")
        if protocol not in (None, "native_ingest_v2") or payload.intake_protocol != protocol:
            raise ValueError("intake_protocol_mismatch")
        session.execute(update(OcrJob).where(OcrJob.id == job_id).values(status=OcrJob.status))
        job = session.get(OcrJob, job_id, populate_existing=True, with_for_update=True)
        if job is not None and (job.metrics or {}).get("workflow_attempt_id") is not None:
            raise ValueError("ocr_execution_owner_changed")
        if job is not None and isinstance((job.metrics or {}).get("nonworkflow_execution"), dict):
            recorded = job.metrics["nonworkflow_execution"]
            if payload_digest(recorded.get("ingest_lease")) == payload_digest(lease):
                return _job_to_dict(job)
        if lease.get("recovery_claim_id") is not None and job is not None:
            recorded = (job.metrics or {}).get("nonworkflow_execution")
            prior = recorded.get("ingest_lease") if isinstance(recorded, dict) else None
            if not isinstance(prior, dict) or prior.get("claim_id") != lease["recovery_claim_id"] or prior.get("input_digest") != lease["input_digest"] or prior.get("id") != lease["id"]:
                raise ValueError("ingest_recovery_original_capture_missing")
            recorded = deepcopy(recorded)
            recorded["ingest_lease"] = deepcopy(lease)
            job.metrics = {**job.metrics, "ingest_lease": deepcopy(lease), "nonworkflow_execution": recorded}
            session.flush()
            return _job_to_dict(job)
        if job is not None and isinstance((job.metrics or {}).get("nonworkflow_execution"), dict):
            raise ValueError("ingest_original_job_reuse_forbidden")
        if job is None:
            job = OcrJob(id=job_id, created_at=datetime.utcnow())
            session.add(job)
        job.status = "running"
        job.input_reference = payload.pdf_uri
        job.order_id = payload.order_id
        job.uploaded_pdf_id = payload.uploaded_pdf_id
        job.order_document_id = payload.order_document_id
        job.input_artifact_digest = payload.content_sha256
        job.error_message = None
        job.updated_at = datetime.utcnow()
        retained = {key: value for key, value in (job.metrics or {}).items() if key not in {
            "nonworkflow_execution", "pipeline_delivery_receipt", "pending_receipt", "first_pass_terminal_receipt",
            "non_ocr_terminal_receipt", "source_failure_receipt", "rerun_terminal_receipt", "evidence_run_id", "reparse_evidence_run_id", "ingest_ocr_mode",
        }}
        job.metrics = {**retained, "ingest_lease": deepcopy(lease), "result_state": "processing",
                       "processing_stage": "input_capture", "next_recovery_at": None,
                       **({"ingest_ocr_mode": "registration_only" if payload.skip_ocr else "hakodate"}
                          if lease["payload"].get("intake_protocol") == "native_ingest_v2" else {})}
        job.output_reference = None
        session.flush()
        return _job_to_dict(job)


def fail_ingest_ocr_capture(lease, job_id, error):
    if not isinstance(error, ValueError):
        return False
    with session_scope() as session:
        if not lock_ingest_lease(session, lease):
            return False
        session.execute(update(OcrJob).where(OcrJob.id == job_id).values(status=OcrJob.status))
        job = session.get(OcrJob, job_id, populate_existing=True, with_for_update=True)
        if job is None or (job.metrics or {}).get("ingest_lease") != lease or (job.metrics or {}).get("nonworkflow_execution") is not None or (job.metrics or {}).get("workflow_attempt_id") is not None:
            return False
        job.status = "failed"
        job.error_message = str(error)
        job.updated_at = datetime.utcnow()
        from src.services.ingest_delivery_service import persist_uncaptured_failure
        receipt = persist_uncaptured_failure(lease, error, write_session=session)
        if receipt is None:
            raise ValueError("ingest_delivery_source_failure_persister_refused")
        job.metrics = {**job.metrics, "source_failure_receipt": receipt}
        return True


def lock_ingest_lease(session, lease, *, terminal=False, read_only=False):
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution
    boundary = current_nonworkflow_execution()
    if isinstance(boundary, dict) and boundary.get("ingest_successor_id"):
        from src.services.ingest_delivery_successor_service import fence
        return fence(session, boundary, lease, read_only=read_only)
    if not isinstance(lease, dict) or type(lease.get("attempts")) is not int or lease["attempts"] < 1 or any(not lease.get(key) for key in ("id", "generation", "claim_id", "input_digest")):
        return False
    from src.services.ingest_delivery_service import fence_original_write
    if not fence_original_write(session, lease, read_only=read_only):
        return False
    uploaded = lease.get("uploaded_lease")
    if uploaded is not None:
        from src.services.uploaded_pdf_service import lock_uploaded_pdf_lease
        if not lock_uploaded_pdf_lease(session, uploaded, terminal=terminal, read_only=read_only):
            return False
    conditions = (IngestJob.id == lease["id"], IngestJob.generation == lease["generation"],
                  IngestJob.claim_id == lease["claim_id"], IngestJob.input_digest == lease["input_digest"],
                  IngestJob.attempts == lease["attempts"])
    states = ["processing", "done"] if terminal else ["processing"]
    conditions = (*conditions, IngestJob.status.in_(states))
    if read_only:
        if session.execute(select(IngestJob.id).where(*conditions).with_for_update(read=True)).scalar_one_or_none() is None:
            return False
    else:
        result = session.execute(update(IngestJob).where(*conditions).values(status=IngestJob.status))
        if result.rowcount != 1:
            return False
    job = session.get(IngestJob, lease["id"], populate_existing=True)
    return job is not None and payload_digest(job.payload) == lease["input_digest"]


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from src.services.ingest_policy import require_explicit_ingest_week

    normalized = dict(payload)
    # The optional dataclass field is not a new key in the schema-1 protocol.
    if normalized.get("intake_protocol") is None:
        normalized.pop("intake_protocol", None)
    if "intake_protocol" in normalized and normalized["intake_protocol"] != "native_ingest_v2":
        raise ValueError("intake_protocol_invalid")
    normalized["week_hint"] = require_explicit_ingest_week(normalized.get("week_hint"))
    from src.services.ingest_facility_selection_service import validate_selection
    normalized["facility_selection"] = validate_selection(
        normalized.get("facility_selection"), normalized.get("facility_hint"), normalized["week_hint"])
    received_at = normalized.get("received_at")
    if hasattr(received_at, "isoformat"):
        normalized["received_at"] = received_at.isoformat()
    return normalized


def create_ingest_receipt(payload: dict[str, Any], force: bool = False, *, write_session=None) -> dict[str, Any]:
    from src.services.ingest_delivery_dispatch_service import required_config
    from src.services.ingest_delivery_service import transaction, create_intent
    config = required_config()
    normalized = _normalize_payload(payload)
    job_id = normalized["message_id"]
    now = datetime.utcnow()
    should_enqueue = True
    with (nullcontext(write_session) if write_session is not None else transaction()) as session:
        if session.bind.dialect.name == "postgresql":
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("ingest-job:" + job_id, 0))))
        session.execute(update(IngestJob).where(IngestJob.id == job_id).values(status=IngestJob.status))
        job = session.get(IngestJob, job_id, populate_existing=True, with_for_update=True)
        if job:
            _normalize_payload(job.payload)
            if payload_digest(job.payload) != job.input_digest:
                raise ValueError("ingest_persisted_input_changed")
            from src.services.workflow_attempt_guard_service import snapshots_equal
            if not snapshots_equal(job.payload.get("facility_selection"), normalized["facility_selection"]):
                raise ValueError("ingest_facility_selection_changed")
            if force:
                job.status = "pending"
                job.started_at = None
                job.finished_at = None
                job.last_error = None
                job.failure_kind = None
                job.generation = uuid4().hex
                job.generation_attempts = 0
                job.claim_id = None
                job.recovery_claim_id = None
                job.payload = normalized
                job.input_digest = payload_digest(normalized)
                should_enqueue = True
            else:
                should_enqueue = job.status == "pending" and job.input_digest == payload_digest(normalized)
            if force:
                job.updated_at = now
        else:
            from src.services.ingest_facility_selection_service import verify_initial_selection
            normalized["facility_selection"] = verify_initial_selection(
                normalized["facility_selection"], normalized.get("facility_hint"), normalized["week_hint"])
            job = IngestJob(
                    id=job_id,
                    status="pending",
                    payload=normalized,
                    attempts=0,
                    generation_attempts=0,
                    input_digest=payload_digest(normalized),
                    generation=uuid4().hex,
                    created_at=now,
                    updated_at=now,
                )
            session.add(job)
        session.flush()
        if should_enqueue:
            create_intent(session, job, config)
            session.flush()
        return {"id": job.id, "generation": job.generation, "input_digest": job.input_digest, "enqueued": should_enqueue}


def create_ingest_job(payload: dict[str, Any], force: bool = False) -> tuple[str, bool]:
    receipt = create_ingest_receipt(payload, force=force)
    return receipt["id"], receipt["enqueued"]


def restart_ingest_job(job_id: str, *, write_session=None) -> bool:
    from src.services.ingest_delivery_dispatch_service import required_config
    from src.services.ingest_delivery_service import transaction, create_intent
    config = required_config()
    now = datetime.utcnow()
    with (nullcontext(write_session) if write_session is not None else transaction()) as session:
        session.execute(update(IngestJob).where(IngestJob.id == job_id).values(status=IngestJob.status))
        job = session.get(IngestJob, job_id, populate_existing=True, with_for_update=True)
        if job is None:
            return False
        _normalize_payload(job.payload)
        digest = payload_digest(job.payload)
        if digest != job.input_digest:
            raise ValueError("ingest_persisted_input_changed")
        result = session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .values(
                status="pending",
                started_at=None,
                finished_at=None,
                updated_at=now,
                last_error=None,
                failure_kind=None,
                generation=uuid4().hex,
                generation_attempts=0,
                claim_id=None,
                recovery_claim_id=None,
                input_digest=digest,
            )
        )
        if result.rowcount > 0:
            job = session.get(IngestJob, job_id, populate_existing=True)
            create_intent(session, job, config)
            session.flush()
            return True
        return False


def get_ingest_job(job_id: str) -> IngestJob | None:
    with session_scope() as session:
        return session.get(IngestJob, job_id)


def get_ingest_job_snapshot(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if job is None:
            return None
        payload = job.payload or {}
        return {
            "id": job.id,
            "status": job.status,
            "attempts": job.attempts,
            "generation_attempts": job.generation_attempts,
            "input_digest": job.input_digest,
            "generation": job.generation,
            "claim_id": job.claim_id,
            "failure_kind": job.failure_kind,
            "last_error": job.last_error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "message_id": payload.get("message_id"),
            "pdf_uri": payload.get("pdf_uri"),
            "received_at": payload.get("received_at"),
            "uploaded_pdf_id": payload.get("uploaded_pdf_id"),
        }


def is_processing_snapshot_stale(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if str(snapshot.get("status") or "").strip().lower() != "processing":
        return False
    started_at_raw = snapshot.get("started_at")
    if not started_at_raw:
        return True
    try:
        started_at = datetime.fromisoformat(str(started_at_raw))
    except ValueError:
        return True
    return started_at < _stale_threshold()


def get_ingest_payload(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(IngestJob, job_id)
        if not job:
            return None
        payload = job.payload or {}
        return dict(payload)


def _stale_threshold() -> datetime:
    minutes = int(_runtime_setting("INGEST_JOB_STALE_MINUTES", "30"))
    return datetime.utcnow() - timedelta(minutes=minutes)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(_runtime_setting(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(value, minimum)


def _auto_recovery_window_hours() -> int:
    return _env_int("INGEST_JOB_AUTO_RECOVERY_WINDOW_HOURS", 24)


def _auto_recovery_cutoff() -> datetime:
    return datetime.utcnow() - timedelta(hours=_auto_recovery_window_hours())


def _max_auto_attempts() -> int:
    return _env_int("INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", 3)


def _eligible_ingest_job_condition(stale_before: datetime, recovery_cutoff: datetime):
    max_attempts = _max_auto_attempts()
    return and_(IngestJob.generation.is_not(None), IngestJob.input_digest.is_not(None), IngestJob.generation_attempts.is_not(None), or_(
        and_(
            IngestJob.status == "pending",
            IngestJob.updated_at >= recovery_cutoff,
            IngestJob.generation_attempts < max_attempts,
        ),
        and_(
            IngestJob.status == "processing",
            IngestJob.started_at < stale_before,
            IngestJob.generation_attempts < max_attempts,
            ~exists(select(OcrJob.id).where(OcrJob.status.in_(["awaiting_output", "recovering"]),
                OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]["id"].as_string() == IngestJob.id,
                OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]["claim_id"].as_string() == IngestJob.claim_id)),
        ),
    ))


def claim_ingest_job(job_id: str, *, generation: str, input_digest: str, uploaded_lease=None, write_session=None):
    if not generation or not input_digest:
        return None
    now = datetime.utcnow()
    stale_before = _stale_threshold()
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        if uploaded_lease is not None:
            from src.services.uploaded_pdf_service import lock_uploaded_pdf_lease
            if not lock_uploaded_pdf_lease(session, uploaded_lease):
                return None
        session.execute(update(IngestJob).where(IngestJob.id == job_id, IngestJob.generation == generation, IngestJob.input_digest == input_digest).values(status=IngestJob.status))
        previous = session.get(IngestJob, job_id, populate_existing=True, with_for_update=True)
        original = previous.payload if previous is not None and previous.generation == generation and previous.input_digest == input_digest else None
        if original is None or previous.generation_attempts is None:
            return None
        _normalize_payload(original)
        if previous.status == "processing" and session.execute(select(OcrJob.id).where(
            OcrJob.status.in_(["awaiting_output", "recovering"]),
            OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]["id"].as_string() == job_id,
            OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]["claim_id"].as_string() == previous.claim_id,
        ).limit(1)).first() is not None:
            return None
        if original.get("uploaded_pdf_id") is not None and uploaded_lease is None:
            return None
        if uploaded_lease is not None:
            from src.services.uploaded_pdf_service import build_ingest_payload
            captured = build_ingest_payload(uploaded_lease, facility_selection=original["facility_selection"])
            for key in ("message_id", "pdf_uri", "received_at", "facility_hint", "week_hint", "facility_name", "skip_ocr", "source_kind", "original_filename", "content_sha256", "uploaded_pdf_id"):
                if original.get(key) != captured.get(key):
                    raise ValueError("uploaded_pdf_original_input_changed")
        if payload_digest(original) != input_digest:
            raise ValueError("ingest_persisted_input_changed")
        eligible = or_(IngestJob.status == "pending",
                       and_(IngestJob.status == "processing", IngestJob.started_at < stale_before))
        exhausted = session.execute(update(IngestJob).where(
            IngestJob.id == job_id, IngestJob.generation == generation, IngestJob.input_digest == input_digest,
            IngestJob.claim_id == previous.claim_id, IngestJob.attempts == previous.attempts,
            eligible, IngestJob.generation_attempts >= _max_auto_attempts(),
        ).values(status="error", failure_kind="budget_exhausted", last_error="ingest_recovery_budget_exhausted",
                 finished_at=now, updated_at=now))
        if exhausted.rowcount == 1:
            if uploaded_lease is not None:
                from src.services.uploaded_pdf_service import stop_uploaded_pdf_budget
                stop_uploaded_pdf_budget(session, uploaded_lease)
            return {"claim_refusal": "budget_exhausted", "id": job_id,
                    "generation": generation, "input_digest": input_digest,
                    "uploaded_lease": deepcopy(uploaded_lease)}
        claim_id = uuid4().hex
        recovery_claim_id = previous.claim_id if previous.status == "processing" else previous.recovery_claim_id
        result = session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .where(IngestJob.generation == generation, IngestJob.input_digest == input_digest)
            .where(IngestJob.generation_attempts < _max_auto_attempts())
            .where(
                (IngestJob.status == "pending")
                | ((IngestJob.status == "processing") & (IngestJob.started_at < stale_before))
            )
            .values(
                status="processing",
                attempts=IngestJob.attempts + 1,
                generation_attempts=IngestJob.generation_attempts + 1,
                started_at=now,
                updated_at=now,
                claim_id=claim_id,
                recovery_claim_id=recovery_claim_id,
            )
        )
        if result.rowcount != 1:
            return None
        job = session.get(IngestJob, job_id, populate_existing=True)
        if payload_digest(job.payload) != input_digest:
            raise ValueError("ingest_persisted_input_changed")
        return {"id": job.id, "generation": generation, "input_digest": input_digest,
                "claim_id": claim_id, "attempts": job.attempts, "payload": deepcopy(job.payload),
                "recovery_claim_id": recovery_claim_id,
                "uploaded_lease": deepcopy(uploaded_lease)}


def complete_ingest_job(job_id: str, *, lease) -> bool:
    now = datetime.utcnow()
    with session_scope() as session:
        if not isinstance(lease, dict) or job_id != lease.get("id") or not lock_ingest_lease(session, lease):
            return False
        session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .values(status="done", finished_at=now, updated_at=now, last_error=None)
        )
        return True


def fail_ingest_job(job_id: str, error_message: str, *, lease, write_session=None) -> bool:
    now = datetime.utcnow()
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        if not isinstance(lease, dict) or job_id != lease.get("id") or not lock_ingest_lease(session, lease):
            return False
        session.execute(
            update(IngestJob)
            .where(IngestJob.id == job_id)
            .values(status="error", finished_at=now, updated_at=now, last_error=error_message, failure_kind="business")
        )
        return True


def fail_ingest_attempt(lease, error):
    from src.services.ingest_delivery_service import transaction
    with transaction() as session:
        accepted = fail_ingest_job(lease["id"], str(error), lease=lease, write_session=session)
        uploaded = lease.get("uploaded_lease")
        if accepted and uploaded is not None:
            from src.services.uploaded_pdf_service import schedule_uploaded_pdf_retry
            if schedule_uploaded_pdf_retry(uploaded["id"], lease=uploaded, worker_instance=uploaded["lease_owner"],
                error_code="ingest_failed", error_message=str(error), terminal=True, write_session=session) is None:
                raise ValueError("ingest_delivery_failed_upload_warning_refused")
        return accepted


def settle_ingest_ocr_terminal(execution):
    if isinstance(execution, dict) and execution.get("ingest_successor_id"):
        return None
    lease = execution.get("ingest_lease") if isinstance(execution, dict) else None
    if lease is None:
        return None
    with session_scope() as session:
        if not lock_ingest_lease(session, lease):
            return None
        session.execute(update(OcrJob).where(OcrJob.id == execution["job_id"]).values(status=OcrJob.status))
        job = session.get(OcrJob, execution["job_id"], populate_existing=True, with_for_update=True)
        stored = (job.metrics or {}).get("nonworkflow_execution") if job is not None else None
        receipt = ingest_terminal_receipt(session, job, stored)
        if (job is None or not isinstance(stored, dict) or stored.get("execution_id") != execution["execution_id"]
            or stored.get("ingest_lease") != lease or not isinstance(receipt, dict)
            or receipt.get("execution_id") != execution["execution_id"] or receipt.get("job_id") != job.id
            or receipt.get("status") != job.status or job.status not in {"done", "failed"}
            or job.input_reference != execution["input_reference"]):
            return None
        now = datetime.utcnow()
        failed = job.status == "failed"
        error = job.error_message or receipt.get("error") or ("first_pass_output_rejected" if failed else None)
        session.execute(update(IngestJob).where(IngestJob.id == lease["id"]).values(
            status="error" if failed else "done", finished_at=now, updated_at=now,
            last_error=error, failure_kind="business" if failed else None))
        return {"status": job.status, "error": error, "lease": deepcopy(lease)}


def _recorded_recovery_owner(job):
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    pending = metrics.get("pending_receipt")
    if metrics.get("workflow_attempt_id") is not None:
        from src.services.order_service import is_own_pending_ocr_result, _pending_request_matches
        from src.services.ocr_job_service import get_job_request_mode
        return bool(isinstance(pending, dict)
            and get_job_request_mode(job) in {"ocr_reparse", "llm_reparse"}
            and is_own_pending_ocr_result(
                {"outcome": "pending", "status": job["status"], "output_reference": job["output_reference"],
                 "pending_receipt": pending}, order_id=job["order_id"], job_id=job["id"],
                workflow_attempt_id=metrics["workflow_attempt_id"],
                workflow_revision=metrics.get("workflow_completion_revision"))
            and _pending_request_matches(metrics, pending))
    captured = metrics.get("nonworkflow_execution")
    if not isinstance(captured, dict):
        logger.warning("OCR recovery stopped: processing-start input missing", job_id=job["id"])
        return False
    valid = has_recorded_execution_input(job)
    if not valid:
        logger.warning("OCR recovery stopped: recorded execution owner missing", job_id=job["id"])
    return valid


def list_ocr_recovery_candidates(limit):
    from src.services.ocr_job_service import _job_to_dict
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.ingest_delivery import IngestDelivery
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.services.ingest_delivery_service import _same
    from src.services.ingest_delivery_successor_service import _recorded_identity
    if type(limit) is not int or limit < 1:
        raise ValueError("ocr_recovery_limit_required")
    original = OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]
    successor = OcrJob.metrics["nonworkflow_execution"]["ingest_successor_id"].as_string()
    owns_ingest = exists(select(IngestJob.id).where(
        IngestJob.id == original["id"].as_string(),
        or_(and_(successor.is_(None), IngestJob.status == "processing"),
            and_(successor.is_not(None), IngestJob.status == "done")),
        IngestJob.generation == original["generation"].as_string(),
        IngestJob.claim_id == original["claim_id"].as_string(),
        IngestJob.input_digest == original["input_digest"].as_string()))
    workflow_attempt = OcrJob.metrics["workflow_attempt_id"].as_string()
    owns_workflow = exists(select(OrderWorkflowState.order_id).where(
        OrderWorkflowState.order_id == OcrJob.order_id,
        OrderWorkflowState.revision.cast(String) == OcrJob.metrics["workflow_completion_revision"].as_string()))
    with session_scope() as session:
        query = select(OcrJob).where(
            or_(OcrJob.status.in_(["awaiting_output", "recovering", "running", "pending"]),
                and_(OcrJob.status.in_(["failed", "error"]), OcrJob.error_message.ilike("%ocr pipeline output not found%"))),
            or_(and_(workflow_attempt.is_not(None), owns_workflow),
                and_(workflow_attempt.is_(None), or_(original["id"].as_string().is_(None), owns_ingest))),
        )
        # The formal schema requires updated_at. A timestamp/id key bounds this
        # read while rejected rows consume scan pages, not the admission budget.
        key = tuple_(OcrJob.updated_at, OcrJob.id)
        ceiling = session.execute(query.with_only_columns(OcrJob.updated_at, OcrJob.id)
            .order_by(OcrJob.updated_at.desc(), OcrJob.id.desc()).limit(1)).first()
        if ceiling is None:
            return []
        query = query.where(key <= tuple(ceiling)).order_by(OcrJob.updated_at, OcrJob.id)
        accepted, cursor = [], None
        while len(accepted) < limit:
            page = query if cursor is None else query.where(key > cursor)
            rows = session.execute(page.limit(limit)).scalars().all()
            if not rows:
                break
            for row in rows:
                cursor = (row.updated_at, row.id)
                value = _job_to_dict(row)
                if is_job_recoverable(value) and _recorded_recovery_owner(value):
                    captured = (value["metrics"] or {}).get("nonworkflow_execution")
                    if isinstance(captured, dict):
                        # The worker only polls legacy output before its retry is due.
                        if (captured.get("ingest_ocr_mode") != "hakodate"
                            and not str(value.get("output_reference") or "").strip()
                            and not is_job_recovery_due(value)):
                            continue
                        lease = captured.get("ingest_lease")
                        identity = captured.get("ingest_successor_id")
                        if identity:
                            owner = session.get(IngestDeliverySuccessor, identity)
                            if (owner is None or owner.job_id != row.id or owner.state != "ACKNOWLEDGED"
                                or owner.handoff_receipt is None
                                or (owner.terminal_receipt or {}).get("kind") == "owned_refusal"
                                or not _same(owner.owner_lease, lease)):
                                continue
                            try:
                                _recorded_identity(session, owner)
                            except ValueError:
                                logger.warning("OCR recovery stopped: recorded successor owner changed", job_id=row.id)
                                continue
                        elif lease is not None:
                            owner = session.execute(select(IngestDelivery).where(
                                IngestDelivery.job_id == lease["id"],
                                IngestDelivery.business_generation == lease["generation"])).scalar_one_or_none()
                            if (owner is None or owner.state != "ACKNOWLEDGED" or owner.handoff_receipt is None
                                or not _same(owner.owner_lease, lease)):
                                continue
                    accepted.append(value)
                    if len(accepted) == limit:
                        break
        return accepted


def recover_completed_upload(uploaded, captured):
    from src.services.uploaded_pdf_service import lock_uploaded_pdf_lease, build_ingest_payload, _write_attempt
    from src.services.workflow_attempt_guard_service import lock_order_execution_input
    from src.models.document import OrderDocument
    from src.models.uploaded_pdf import UploadedPdf
    with session_scope() as session:
        if not lock_uploaded_pdf_lease(session, uploaded):
            return False
        session.execute(update(IngestJob).where(IngestJob.id == captured["id"]).values(status=IngestJob.status))
        job = session.get(IngestJob, captured["id"], populate_existing=True)
        if job is None or job.status != "done" or job.generation != captured["generation"] or job.claim_id != captured["claim_id"] or job.input_digest != captured["input_digest"] or payload_digest(job.payload) != job.input_digest:
            return False
        _normalize_payload(job.payload)
        authored = build_ingest_payload(uploaded, facility_selection=job.payload["facility_selection"])
        if any(job.payload.get(key) != authored.get(key) for key in ("message_id", "pdf_uri", "received_at", "facility_hint", "week_hint", "facility_name", "skip_ocr", "source_kind", "original_filename", "content_sha256", "uploaded_pdf_id")):
            return False
        original = OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]
        rows = session.execute(select(OcrJob).where(original["id"].as_string() == job.id,
            original["generation"].as_string() == job.generation, original["claim_id"].as_string() == job.claim_id,
            OcrJob.status == "done")).scalars().all()
        if len(rows) != 1:
            return False
        ocr = rows[0]
        boundary = deepcopy(ocr.metrics["nonworkflow_execution"])
        order = lock_order_execution_input(session, boundary.get("order_id"))
        ocr = session.get(OcrJob, ocr.id, populate_existing=True, with_for_update=True)
        if order is None or ocr.status != "done" or ocr.metrics.get("nonworkflow_execution") != boundary:
            return False
        receipt = ingest_terminal_receipt(session, ocr, boundary)
        document = boundary.get("orders", {}).get(boundary.get("order_id"), {}).get("document")
        if not isinstance(receipt, dict) or receipt.get("job_id") != ocr.id or receipt.get("execution_id") != boundary["execution_id"] or receipt.get("status") != "done" or not isinstance(document, dict) or document.get("storage_uri") != uploaded["storage_uri"] or document.get("source_email_id") != uploaded["message_id"]:
            return False
        saved_document = session.get(OrderDocument, document["id"], populate_existing=True, with_for_update=True)
        if saved_document is None or order.current_document_id != saved_document.id or saved_document.storage_uri != uploaded["storage_uri"] or saved_document.source_email_id != uploaded["message_id"] or order.archived_at is not None:
            return False
        row = session.get(UploadedPdf, uploaded["id"])
        row.current_order_id, row.current_document_id = order.id, saved_document.id
        row.status, row.current_stage = "completed", "completed"
        row.lease_expires_at, row.next_retry_at = None, None
        row.last_error_code, row.last_error_message = None, None
        row.updated_at = datetime.utcnow()
        _write_attempt(session, uploaded_pdf_id=row.id, attempt_no=row.attempt_count, stage="completed",
            status="completed", worker_instance=uploaded["lease_owner"], finished_at=row.updated_at)
        return True


def list_pending_jobs(limit: int = 10) -> list[str]:
    stale_before = _stale_threshold()
    recovery_cutoff = _auto_recovery_cutoff()
    with session_scope() as session:
        rows = (
            session.execute(
                select(IngestJob.id)
                .where(_eligible_ingest_job_condition(stale_before, recovery_cutoff))
                .order_by(IngestJob.updated_at.desc(), IngestJob.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)


def list_jobs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        query = select(IngestJob).order_by(desc(IngestJob.updated_at))
        if status:
            query = query.where(IngestJob.status == status)
        rows = session.execute(query.limit(limit)).scalars().all()
        items: list[dict[str, Any]] = []
        for job in rows:
            payload = job.payload or {}
            items.append(
                {
                    "id": job.id,
                    "status": job.status,
                    "attempts": job.attempts,
                    "generation_attempts": job.generation_attempts,
                    "generation": job.generation,
                    "claim_id": job.claim_id,
                    "input_digest": job.input_digest,
                    "failure_kind": job.failure_kind,
                    "last_error": job.last_error,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "message_id": payload.get("message_id"),
                    "pdf_uri": payload.get("pdf_uri"),
                    "received_at": payload.get("received_at"),
                }
            )
        return items


def reset_stale_processing(minutes: int | None = None, limit: int = 100) -> list[str]:
    if minutes is None:
        minutes = int(_runtime_setting("INGEST_JOB_STALE_MINUTES", "30"))
    stale_before = datetime.utcnow() - timedelta(minutes=minutes)
    now = datetime.utcnow()
    with session_scope() as session:
        stale_ids = (
            session.execute(
                select(IngestJob.id, IngestJob.generation, IngestJob.claim_id, IngestJob.started_at)
                .where((IngestJob.status == "processing") & (IngestJob.started_at < stale_before))
                .where(IngestJob.generation.is_not(None), IngestJob.input_digest.is_not(None))
                .order_by(IngestJob.started_at.asc())
                .limit(limit)
            )
            .all()
        )
        if not stale_ids:
            return []
        reset = []
        for job_id, generation, claim_id, started_at in stale_ids:
            locked = session.execute(update(IngestJob).where(IngestJob.id == job_id, IngestJob.generation == generation,
                IngestJob.claim_id == claim_id, IngestJob.status == "processing", IngestJob.started_at == started_at)
                .values(status=IngestJob.status))
            if locked.rowcount != 1:
                continue
            if session.execute(select(OcrJob.id).where(OcrJob.status.in_(["awaiting_output", "recovering"]),
                OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]["id"].as_string() == job_id,
                OcrJob.metrics["nonworkflow_execution"]["ingest_lease"]["claim_id"].as_string() == claim_id).limit(1)).first() is not None:
                continue
            from src.models.ingest_delivery import IngestDelivery
            intent = session.execute(select(IngestDelivery.id).where(IngestDelivery.job_id == job_id,
                IngestDelivery.business_generation == generation, IngestDelivery.state != "ACKNOWLEDGED")).first()
            if intent is not None:
                reset.append(job_id)
        return reset


def summarize_ingest_jobs() -> dict[str, Any]:
    stale_before = _stale_threshold()
    recovery_cutoff = _auto_recovery_cutoff()
    max_attempts = _max_auto_attempts()
    now = datetime.utcnow()
    with session_scope() as session:
        rows = session.execute(
            select(IngestJob.status, func.count(IngestJob.id))
            .group_by(IngestJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        eligible_condition = _eligible_ingest_job_condition(stale_before, recovery_cutoff)
        oldest_pending_at = session.execute(
            select(func.min(IngestJob.created_at)).where(eligible_condition)
        ).scalar_one_or_none()
        oldest_processing_at = session.execute(
            select(func.min(IngestJob.started_at)).where(IngestJob.status == "processing")
        ).scalar_one_or_none()
        stale_processing_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(
                    (IngestJob.status == "processing") & (IngestJob.started_at < stale_before)
                )
            ).scalar_one()
            or 0
        )
        eligible_backlog_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(eligible_condition)
            ).scalar_one()
            or 0
        )
        blocked_old_pending_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(
                    IngestJob.status.in_(["pending", "error"]),
                    IngestJob.updated_at < recovery_cutoff,
                )
            ).scalar_one()
            or 0
        )
        blocked_attempt_exhausted_count = int(
            session.execute(
                select(func.count(IngestJob.id)).where(
                    IngestJob.status.in_(["pending", "error", "processing"]),
                    IngestJob.generation_attempts >= max_attempts,
                )
            ).scalar_one()
            or 0
        )
        return {
            "total": sum(counts.values()),
            "counts": counts,
            "pending_count": counts.get("pending", 0),
            "error_count": counts.get("error", 0),
            "processing_count": counts.get("processing", 0),
            "done_count": counts.get("done", 0),
            "stale_processing_count": stale_processing_count,
            "eligible_backlog_count": eligible_backlog_count,
            "blocked_old_pending_count": blocked_old_pending_count,
            "blocked_attempt_exhausted_count": blocked_attempt_exhausted_count,
            "auto_recovery_window_hours": _auto_recovery_window_hours(),
            "auto_recovery_max_attempts": max_attempts,
            "oldest_pending_at": oldest_pending_at.isoformat() if oldest_pending_at else None,
            "oldest_pending_seconds": (
                max(int((now - oldest_pending_at).total_seconds()), 0) if oldest_pending_at else None
            ),
            "oldest_processing_at": oldest_processing_at.isoformat() if oldest_processing_at else None,
            "oldest_processing_seconds": (
                max(int((now - oldest_processing_at).total_seconds()), 0)
                if oldest_processing_at
                else None
            ),
        }


def summarize_backlog_metrics() -> dict[str, Any]:
    from src.services.uploaded_pdf_service import summarize_uploaded_pdfs

    ingest_summary = summarize_ingest_jobs()
    uploaded_pdf_summary = summarize_uploaded_pdfs()
    now = datetime.utcnow()
    recent_since = now - timedelta(hours=int(_runtime_setting("OCR_BACKLOG_RECENT_HOURS", "24")))
    with session_scope() as session:
        rows = session.execute(
            select(OcrJob.status, func.count(OcrJob.id)).group_by(OcrJob.status)
        ).all()
        counts = {status: int(count) for status, count in rows if status}
        active_count = counts.get("running", 0) + counts.get("pending", 0)
        awaiting_output_count = counts.get("awaiting_output", 0)
        recovering_count = counts.get("recovering", 0)
        oldest_active_at = session.execute(
            select(func.min(OcrJob.created_at)).where(OcrJob.status.in_(["running", "pending"]))
        ).scalar_one_or_none()
        recent_backlog_skipped_count = int(
            session.execute(
                select(func.count(OcrJob.id)).where(
                    (OcrJob.error_message == "backlog_skipped") & (OcrJob.updated_at >= recent_since)
                )
            ).scalar_one()
            or 0
        )
        recent_failed_count = int(
            session.execute(
                select(func.count(OcrJob.id)).where(
                    (OcrJob.status == "failed") & (OcrJob.updated_at >= recent_since)
                )
            ).scalar_one()
            or 0
        )
        active_jobs = [
            {
                "id": job.id,
                "status": job.status,
                "metrics": job.metrics,
                "updated_at": job.updated_at,
                "error_message": job.error_message,
                "output_reference": job.output_reference,
            }
            for job in (
                session.execute(
                    select(OcrJob).where(OcrJob.status.in_(["running", "pending"]))
                )
                .scalars()
                .all()
            )
        ]
        recoverable_jobs = [
            {
                "id": job.id,
                "status": job.status,
                "metrics": job.metrics,
                "updated_at": job.updated_at,
                "error_message": job.error_message,
                "output_reference": job.output_reference,
            }
            for job in (
                session.execute(
                    select(OcrJob).where(
                        OcrJob.status.in_(["awaiting_output", "recovering", "failed", "error", "running", "pending"])
                    )
                )
                .scalars()
                .all()
            )
        ]
    recoverable_jobs = [job for job in recoverable_jobs if is_job_recoverable(job)]
    active_jobs = [job for job in active_jobs if not is_job_recoverable(job)]
    active_count = len(active_jobs)
    stale_jobs = [job for job in active_jobs if is_job_stale(job)]
    stale_oldest_seconds = None
    if stale_jobs:
        stale_ats = [get_job_stale_at(job) for job in stale_jobs]
        stale_ats = [value for value in stale_ats if value is not None]
        if stale_ats:
            oldest_stale_at = min(stale_ats)
            stale_oldest_seconds = max(int((now - oldest_stale_at).total_seconds()), 0)
    ingest_queue_depth = int(ingest_summary.get("eligible_backlog_count") or 0)
    uploaded_pdf_queue_depth = int(uploaded_pdf_summary.get("eligible_backlog_count") or 0)
    oldest_pending_seconds = ingest_summary.get("oldest_pending_seconds")
    status = "ok"
    if ingest_queue_depth > 0 or uploaded_pdf_queue_depth > 0 or active_count > 0:
        status = "warn"
    if recent_backlog_skipped_count > 0:
        status = "fail"
    return {
        "status": status,
        "ingest_queue_depth": ingest_queue_depth,
        "uploaded_pdf_queue_depth": uploaded_pdf_queue_depth,
        "ocr_queue_depth": active_count,
        "exports_queue_depth": 0,
        "oldest_pending_seconds": oldest_pending_seconds or 0,
        "ingest": ingest_summary,
        "uploaded_pdfs": uploaded_pdf_summary,
        "ocr": {
            "total": sum(counts.values()),
            "counts": counts,
            "active_count": active_count,
            "running_count": counts.get("running", 0),
            "pending_count": counts.get("pending", 0),
            "awaiting_output_count": awaiting_output_count,
            "recovering_count": recovering_count,
            "recoverable_count": len(recoverable_jobs),
            "failed_count": counts.get("failed", 0),
            "done_count": counts.get("done", 0),
            "completed_count": counts.get("completed", 0),
            "oldest_active_at": oldest_active_at.isoformat() if oldest_active_at else None,
            "oldest_active_seconds": (
                max(int((now - oldest_active_at).total_seconds()), 0) if oldest_active_at else None
            ),
            "recent_backlog_skipped_count": recent_backlog_skipped_count,
            "recent_failed_count": recent_failed_count,
            "recent_window_hours": int(_runtime_setting("OCR_BACKLOG_RECENT_HOURS", "24")),
            "stale_count": len(stale_jobs),
            "stale_oldest_seconds": stale_oldest_seconds,
        },
    }
