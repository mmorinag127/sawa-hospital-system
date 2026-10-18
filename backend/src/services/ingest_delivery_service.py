"""Original ingest ownership and transport generations are separate durable records."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
import json
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import event, or_, select, text, update
from sqlalchemy.orm import Session
from src.db import session_scope
from src.models.ingest_delivery import IngestDelivery
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.uploaded_pdf import UploadedPdf
from src.services.workflow_ocr_delivery_execution_service import execution_mutex

_claim = ContextVar("ingest_delivery_claim", default=None)


def _same(left, right):
    return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
        right, sort_keys=True, separators=(",", ":"), allow_nan=False)


@contextmanager
def transaction():
    with session_scope() as session:
        if session.bind.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        elif session.bind.dialect.name != "postgresql":
            raise ValueError("ingest_delivery_dialect_unsupported")
        yield session


def _locked(session, delivery_id):
    session.execute(update(IngestDelivery).where(IngestDelivery.id == delivery_id).values(state=IngestDelivery.state))
    return session.get(IngestDelivery, delivery_id, populate_existing=True, with_for_update=True)


def _original(session, row, *, read_only=False):
    from src.services.ingest_job_service import payload_digest
    job = session.get(IngestJob, row.job_id, populate_existing=True, with_for_update={"read": True} if read_only else True)
    if job is None or job.generation != row.business_generation:
        return None
    if job.input_digest != row.input_digest or payload_digest(job.payload) != row.input_digest or not _same(job.payload, row.original_payload):
        raise ValueError("ingest_delivery_original_changed")
    if row.uploaded_identity is not None:
        uploaded = session.get(UploadedPdf, row.uploaded_identity["id"], populate_existing=True, with_for_update={"read": True} if read_only else True)
        from src.services.uploaded_pdf_service import _serialize_uploaded_pdf
        if uploaded is None or any(not _same(_serialize_uploaded_pdf(uploaded)[key], value)
                                   for key, value in row.uploaded_identity.items()):
            raise ValueError("ingest_delivery_uploaded_original_changed")
    return job


def create_intent(session, job, config):
    from src.services.ingest_delivery_dispatch_service import task_contract
    from src.services.uploaded_pdf_service import _serialize_uploaded_pdf
    rows = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == job.id,
        IngestDelivery.business_generation == job.generation).with_for_update()).scalars().all()
    if rows:
        if len(rows) != 1 or _original(session, rows[0]) is None:
            raise ValueError("ingest_delivery_original_missing")
        return rows[0]
    if job.status != "pending" or not job.generation or not job.input_digest:
        raise ValueError("ingest_delivery_reservation_required")
    uploaded_identity = None
    if job.payload.get("uploaded_pdf_id") is not None:
        uploaded = session.get(UploadedPdf, job.payload["uploaded_pdf_id"], with_for_update=True)
        if uploaded is None:
            raise ValueError("ingest_delivery_upload_missing")
        values = _serialize_uploaded_pdf(uploaded)
        keys = ("id", "message_id", "storage_uri", "content_sha256", "received_at", "facility_hint",
                "week_hint", "facility_name", "skip_ocr", "source_kind", "original_filename", "page_count")
        uploaded_identity = {key: deepcopy(values[key]) for key in keys}
    row = IngestDelivery(id="ID-" + uuid4().hex, job_id=job.id, business_generation=job.generation,
        input_digest=job.input_digest, original_payload=deepcopy(job.payload), uploaded_identity=uploaded_identity,
        generation=1, state="PENDING", visit_count=0, task_contract={})
    row.task_contract = task_contract(row.id, row.generation, config)
    session.add(row)
    return row


def delivery_for_job(job_id, generation, input_digest):
    with transaction() as session:
        row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == job_id,
            IngestDelivery.business_generation == generation, IngestDelivery.input_digest == input_digest)).scalar_one_or_none()
        if row is None:
            raise ValueError("ingest_delivery_original_intent_missing")
        return row.id, row.generation


def inspect_generation(delivery_id, generation):
    if type(generation) is not int or generation < 1:
        raise ValueError("ingest_delivery_generation_required")
    with transaction() as session:
        row = _locked(session, delivery_id)
        if row is None or generation > row.generation:
            raise ValueError("ingest_delivery_generation_unknown")
        return {"duplicate": generation < row.generation or row.state == "ACKNOWLEDGED", "job_id": row.job_id}


def prepare_dispatch(delivery_id, generation):
    with transaction() as session:
        row = _locked(session, delivery_id)
        if row is None or type(generation) is not int or generation > row.generation:
            raise ValueError("ingest_delivery_generation_unknown")
        if generation < row.generation or row.state == "ACKNOWLEDGED":
            return None
        job = _original(session, row)
        if job is None or job.status in {"done", "error"} or row.handoff_receipt is not None or row.terminal_receipt is not None:
            return None
        row.visit_count += 1
        row.updated_at = datetime.utcnow()
        return {"state": row.state, "task_contract": deepcopy(row.task_contract)}


def mark_sent(delivery_id, generation):
    with transaction() as session:
        row = _locked(session, delivery_id)
        if row is not None and row.generation == generation and row.state == "PENDING":
            row.state = "SENT"


def record_dispatch_error(delivery_id, generation, error):
    with transaction() as session:
        row = _locked(session, delivery_id)
        if row is not None and row.generation == generation and row.state != "ACKNOWLEDGED":
            row.last_error = error


def drain_candidates(limit):
    if type(limit) is not int or limit < 1:
        raise ValueError("ingest_delivery_drain_limit_required")
    with transaction() as session:
        rows = session.execute(select(IngestDelivery).join(IngestJob, IngestJob.id == IngestDelivery.job_id).where(
            IngestDelivery.state != "ACKNOWLEDGED", IngestDelivery.business_generation == IngestJob.generation,
            or_(IngestJob.status.in_(["pending", "processing"]),
                IngestDelivery.terminal_receipt.is_not(None), IngestDelivery.handoff_receipt.is_not(None),
                IngestJob.failure_kind == "budget_exhausted")
        ).order_by(IngestDelivery.visit_count, IngestDelivery.updated_at, IngestDelivery.id)
            .limit(limit).with_for_update(of=IngestDelivery, skip_locked=True)).scalars().all()
        for row in rows:
            row.visit_count += 1
            row.updated_at = datetime.utcnow()
        return [(row.id, row.generation) for row in rows]


def renew_missing(delivery_id, generation):
    observed = inspect_generation(delivery_id, generation)
    with execution_mutex("ingest-original:" + observed["job_id"]) as acquired:
        if not acquired:
            return {"action": "active_owner"}
        with transaction() as session:
            row = _locked(session, delivery_id)
            job = _original(session, row)
            if row.generation != generation or row.state == "ACKNOWLEDGED" or job is None or job.status not in {"pending", "processing"}:
                return {"action": "noop"}
            if row.handoff_receipt is not None or row.terminal_receipt is not None:
                return {"action": "receipt_waiting"}
            from src.services.ingest_delivery_dispatch_service import renew_contract
            row.generation += 1
            row.task_contract = renew_contract(row.task_contract, row.id, row.generation)
            row.state = "PENDING"
            row.claim_token = None
            return {"action": "renewed", "generation": row.generation}


@contextmanager
def claim_context(claim):
    token = _claim.set(claim)
    try:
        yield
    finally:
        _claim.reset(token)


def claim_delivery(delivery_id, generation):
    from src.services import ingest_job_service, uploaded_pdf_service
    with transaction() as session:
        row = _locked(session, delivery_id)
        if row is None or type(generation) is not int or generation > row.generation:
            raise ValueError("ingest_delivery_generation_unknown")
        if generation < row.generation or row.state == "ACKNOWLEDGED":
            return {"duplicate": True}
        job = _original(session, row)
        if job is None:
            row.state = "ACKNOWLEDGED"
            return {"duplicate": True, "superseded": True}
        row.claim_token = uuid4().hex
        if row.owner_lease is None and job.status not in {"done", "error"}:
            uploaded = None
            if row.uploaded_identity is not None:
                uploaded = uploaded_pdf_service.claim_uploaded_pdf(row.uploaded_identity["id"],
                    worker_instance=row.id, write_session=session)
                if uploaded is None:
                    raise ValueError("ingest_delivery_uploaded_claim_refused")
            lease = ingest_job_service.claim_ingest_job(row.job_id, generation=row.business_generation,
                input_digest=row.input_digest, uploaded_lease=uploaded, write_session=session)
            if lease is None:
                raise ValueError("ingest_delivery_original_claim_refused")
            row.owner_lease = deepcopy(lease)
        row.state = "CLAIMED"
        return {"delivery_id": row.id, "generation": row.generation, "claim_token": row.claim_token,
                "lease": deepcopy(row.owner_lease), "duplicate": False,
                "execute": job.status in {"pending", "processing"} and row.handoff_receipt is None}


def fence_original_write(session, lease, *, read_only=False):
    row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
        IngestDelivery.business_generation == lease["generation"]).with_for_update(read=read_only)).scalar_one_or_none()
    if row is None:
        raise ValueError("ingest_delivery_original_intent_missing")
    if not _same(row.owner_lease, lease):
        return False
    claim = _claim.get()
    if claim is not None:
        return row.id == claim["delivery_id"] and row.generation == claim["generation"] and row.claim_token == claim["claim_token"] and row.state == "CLAIMED"
    return row.state == "ACKNOWLEDGED" and row.handoff_receipt is not None


def _receipt(session, row, job, *, validate_pipeline=True):
    captured = (job.metrics or {}).get("nonworkflow_execution")
    if not isinstance(captured, dict) or not _same(captured.get("ingest_lease"), row.owner_lease):
        raise ValueError("ingest_delivery_capture_owner_mismatch")
    if captured.get("job_id") != job.id or captured.get("input_reference") != job.input_reference:
        raise ValueError("ingest_delivery_capture_input_mismatch")
    from src.services.ingest_job_service import ingest_terminal_receipt
    terminal = ingest_terminal_receipt(session, job, captured)
    if job.status in {"done", "failed"}:
        if terminal is None:
            return None, None
        if not isinstance(terminal, dict) or terminal.get("execution_id") != captured.get("execution_id") or terminal.get("job_id") != job.id or terminal.get("status") != job.status:
            raise ValueError("ingest_delivery_terminal_receipt_missing")
        return "terminal", {"job_id": job.id, "capture": deepcopy(captured), "receipt": deepcopy(terminal)}
    pipeline = (job.metrics or {}).get("pipeline_delivery_receipt")
    if job.status == "awaiting_output" and isinstance(pipeline, dict) and job.input_reference and job.output_reference:
        selected = captured.get("selected_pdf_receipt")
        read_input = captured.get("orders", {}).get(captured.get("order_id"))
        configured = read_input.get("configured_input") if isinstance(read_input, dict) else None
        if not isinstance(selected, dict) or not isinstance(configured, dict):
            raise ValueError("ingest_delivery_pipeline_original_missing")
        configured_digest = sha256(json.dumps(configured, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if (not _same(selected, pipeline.get("selected_pdf_receipt"))
            or not selected.get("sha256") or pipeline.get("input_sha256") != selected["sha256"]
            or pipeline.get("configured_input_sha256") != configured_digest):
            raise ValueError("ingest_delivery_pipeline_original_mismatch")
        from src.services.ocr_job_service import own_pipeline_delivery_matches, validate_own_pipeline_result
        from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
        with nonworkflow_execution_context(captured):
            if not own_pipeline_delivery_matches(job, pipeline["input_reference"], job.output_reference):
                raise ValueError("ingest_delivery_pipeline_owner_mismatch")
            if validate_pipeline:
                validate_own_pipeline_result({"input_reference": pipeline["input_reference"],
                    "input_generation": pipeline["generation"], "output_reference": job.output_reference,
                    "job_id": pipeline["pipeline_job_id"], "status": "pending"}, job.output_reference, write_session=session)
        return "handoff", {"job_id": job.id, "capture": deepcopy(captured), "pipeline_receipt": deepcopy(pipeline),
                           "input_reference": job.input_reference, "output_reference": job.output_reference}
    return None, None


@event.listens_for(Session, "before_flush")
def bind_owned_receipt(session, flush_context, instances):
    for job in list(session.dirty):
        if not isinstance(job, OcrJob):
            continue
        captured = (job.metrics or {}).get("nonworkflow_execution")
        if isinstance(captured, dict) and captured.get("ingest_successor_id"):
            continue
        lease = captured.get("ingest_lease") if isinstance(captured, dict) else None
        if not isinstance(lease, dict):
            continue
        row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
            IngestDelivery.business_generation == lease["generation"]).with_for_update()).scalar_one_or_none()
        if row is None or not fence_original_write(session, lease):
            raise ValueError("ingest_delivery_receipt_owner_lost")
        # The actual waiting persister already checks pipeline ownership. Do not
        # refresh its still-dirty OcrJob through the lookup helper during flush.
        kind, receipt = _receipt(session, row, job, validate_pipeline=False)
        if kind == "handoff":
            row.handoff_receipt = receipt
        elif kind == "terminal":
            row.terminal_receipt = receipt


def acknowledge_claim(claim):
    with transaction() as session:
        row = _locked(session, claim["delivery_id"])
        if row.generation != claim["generation"] or row.claim_token != claim["claim_token"]:
            raise ValueError("ingest_delivery_ack_owner_lost")
        original = _original(session, row)
        if original is None:
            row.state = "ACKNOWLEDGED"
            return "superseded"
        if original.status == "error" and original.failure_kind == "budget_exhausted":
            owner = row.owner_lease
            if (not isinstance(owner, dict) or owner.get("claim_refusal") != "budget_exhausted"
                or owner.get("id") != row.job_id or owner.get("generation") != row.business_generation
                or owner.get("input_digest") != row.input_digest
                or original.last_error != "ingest_recovery_budget_exhausted"):
                raise ValueError("ingest_delivery_budget_receipt_missing")
            _require_uploaded_terminal(session, row, "failed")
            row.state = "ACKNOWLEDGED"
            return "business_failed"
        stored = row.terminal_receipt or row.handoff_receipt
        if stored is None:
            raise ValueError("ingest_delivery_owned_receipt_missing")
        if stored.get("kind") == "source_failure":
            if (original.status != "error" or original.failure_kind != "business"
                or original.last_error != stored["error"] or original.claim_id != stored["owner_lease"]["claim_id"]
                or not _same(stored["owner_lease"], row.owner_lease)
                or stored["input_digest"] != row.input_digest or stored["business_generation"] != row.business_generation):
                raise ValueError("ingest_delivery_source_failure_proof_refused")
            if row.uploaded_identity is not None:
                uploaded = session.get(UploadedPdf, row.uploaded_identity["id"], with_for_update=True)
                owner = row.owner_lease["uploaded_lease"]
                if (uploaded is None or uploaded.status != "failed" or uploaded.last_error_message != stored["error"]
                    or uploaded.lease_token != owner["lease_token"] or uploaded.attempt_count != owner["attempt_count"]):
                    raise ValueError("ingest_delivery_source_failure_upload_unsettled")
            row.state = "ACKNOWLEDGED"
            return "business_failed"
        if stored.get("kind") == "non_ocr_document":
            from src.models.document import OrderDocument
            document = session.get(OrderDocument, stored["document_id"], populate_existing=True, with_for_update=True)
            expected_status = "done" if stored["receipt"]["status"] == "done" else "error"
            if (not _same(stored["owner_lease"], row.owner_lease) or not _same(stored["original_payload"], row.original_payload)
                or document is None or document.source_email_id != row.job_id or document.storage_uri != row.original_payload["pdf_uri"]
                or document.status != ("skipped" if expected_status == "done" else "error") or original.status != expected_status):
                raise ValueError("ingest_delivery_non_ocr_receipt_not_settled")
            _require_uploaded_terminal(session, row, "completed" if expected_status == "done" else "failed")
            row.state = "ACKNOWLEDGED"
            return "business_done" if expected_status == "done" else "business_failed"
        job = session.get(OcrJob, stored["job_id"], populate_existing=True, with_for_update=True)
        if job is None:
            raise ValueError("ingest_delivery_original_ocr_job_missing")
        kind, receipt = _receipt(session, row, job)
        if kind == "terminal" and _same(receipt, row.terminal_receipt) and original.status in {"done", "error"}:
            result = "business_failed" if original.status == "error" else "business_done"
        elif kind == "handoff" and _same(receipt, row.handoff_receipt) and original.status == "processing":
            result = "durable_handoff"
        else:
            raise ValueError("ingest_delivery_receipt_not_settled")
        if kind == "terminal" and row.uploaded_identity is not None:
            upload = session.get(UploadedPdf, row.uploaded_identity["id"], populate_existing=True, with_for_update=True)
            owner = row.owner_lease["uploaded_lease"]
            if upload.status != ("completed" if original.status == "done" else "failed") or upload.lease_token != owner["lease_token"] or upload.attempt_count != owner["attempt_count"]:
                raise ValueError("ingest_delivery_uploaded_terminal_not_settled")
        row.state = "ACKNOWLEDGED"
        return result


def _require_uploaded_terminal(session, row, status, *, read_only=False):
    if row.uploaded_identity is None:
        return
    upload = session.get(UploadedPdf, row.uploaded_identity["id"], populate_existing=True, with_for_update={"read": True} if read_only else True)
    owner = row.owner_lease.get("uploaded_lease") if isinstance(row.owner_lease, dict) else None
    if (upload is None or not isinstance(owner, dict) or upload.status != status
        or upload.lease_token != owner["lease_token"] or upload.attempt_count != owner["attempt_count"]):
        raise ValueError("ingest_delivery_uploaded_terminal_not_settled")


def process(delivery_id, generation):
    from src.workers.ingest_worker import process_ingest_job
    observed = inspect_generation(delivery_id, generation)
    if observed["duplicate"]:
        return {"acknowledged": True, "duplicate": True}
    with execution_mutex("ingest-original:" + observed["job_id"]) as acquired:
        if not acquired:
            raise ValueError("ingest_delivery_execution_busy")
        claim = claim_delivery(delivery_id, generation)
        if claim["duplicate"]:
            return {"acknowledged": True, "duplicate": True}
        with claim_context(claim):
            lease = claim["lease"]
            if claim["execute"] and lease is not None and lease.get("claim_refusal") != "budget_exhausted":
                process_ingest_job(lease["id"], generation=lease["generation"], input_digest=lease["input_digest"], owned_lease=lease)
            result = acknowledge_claim(claim)
        return {"acknowledged": True, "result": result}


def settle_saved(delivery_id, generation):
    observed = inspect_generation(delivery_id, generation)
    if observed["duplicate"]:
        return {"action": "acknowledged"}
    with execution_mutex("ingest-original:" + observed["job_id"]) as acquired:
        if not acquired:
            return {"action": "active_owner"}
        with transaction() as session:
            row = _locked(session, delivery_id)
            if row.generation != generation or row.state == "ACKNOWLEDGED":
                return {"action": "noop"}
            if (row.handoff_receipt is None and row.terminal_receipt is None
                and (row.owner_lease or {}).get("claim_refusal") != "budget_exhausted"):
                return None
            claim = {"delivery_id": row.id, "generation": row.generation, "claim_token": row.claim_token}
        with claim_context(claim):
            return {"action": acknowledge_claim(claim)}


@contextmanager
def recovery_context(execution):
    if execution.get("ingest_successor_id"):
        from src.services.ingest_delivery_successor_service import recovery_context as successor_recovery
        with successor_recovery(execution) as eligible:
            yield eligible
        return
    lease = execution.get("ingest_lease")
    if lease is None:
        yield True
        return
    with execution_mutex("ingest-original:" + lease["id"]) as acquired:
        if not acquired:
            yield False
            return
        with transaction() as session:
            row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
                IngestDelivery.business_generation == lease["generation"])).scalar_one_or_none()
            eligible = row is not None and row.state == "ACKNOWLEDGED" and row.handoff_receipt is not None and _same(row.owner_lease, lease)
        yield eligible


def persist_runner_receipt(lease, terminal):
    """Persist the actual returned non-OCR receipt, never manufacture one from return/exception."""
    from src.models.document import OrderDocument
    from src.services.ingest_job_service import lock_ingest_lease
    with transaction() as session:
        if not lock_ingest_lease(session, lease, terminal=True):
            # A finalized original OCR business receipt may already have settled.
            row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
                IngestDelivery.business_generation == lease["generation"])).scalar_one_or_none()
            if (row is not None and row.terminal_receipt is not None and _same(row.owner_lease, lease)
                and _same(row.terminal_receipt.get("receipt"), terminal) and fence_original_write(session, lease)):
                return
            raise ValueError("ingest_delivery_terminal_persister_refused")
        row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
            IngestDelivery.business_generation == lease["generation"]).with_for_update()).scalar_one()
        job = session.get(OcrJob, terminal.get("job_id"), populate_existing=True, with_for_update=True) if terminal.get("job_id") is not None else None
        if job is not None:
            captured = (job.metrics or {}).get("nonworkflow_execution")
            if not isinstance(captured, dict) or not _same(captured.get("ingest_lease"), lease) or terminal.get("execution_id") != captured["execution_id"] or terminal.get("status") != job.status:
                raise ValueError("ingest_delivery_terminal_original_mismatch")
            from src.services.ingest_job_service import ingest_terminal_receipt
            if captured.get("ingest_ocr_mode") in {"hakodate", "registration_only"}:
                if ingest_terminal_receipt(session, job, captured) != terminal:
                    raise ValueError("ingest_native_runner_receipt_changed")
                return
            key = "first_pass_terminal_receipt" if "evidence_run_id" in terminal else "non_ocr_terminal_receipt"
            existing = (job.metrics or {}).get(key)
            if existing is not None and not _same(existing, terminal):
                raise ValueError("ingest_delivery_terminal_original_mismatch")
            job.metrics = {**job.metrics, key: deepcopy(terminal)}
            session.flush()
            return
        document = session.get(OrderDocument, terminal.get("document_id"), with_for_update=True)
        if (document is None or document.source_email_id != lease["id"] or document.storage_uri != row.original_payload["pdf_uri"]
            or terminal.get("status") not in {"done", "failed"}
            or document.status not in {"skipped", "error"}
            or (terminal["status"] == "done") != (document.status == "skipped")):
            raise ValueError("ingest_delivery_non_ocr_document_receipt_refused")
        row.terminal_receipt = {"kind": "non_ocr_document", "document_id": document.id,
            "owner_lease": deepcopy(lease), "original_payload": deepcopy(row.original_payload), "receipt": deepcopy(terminal)}


def persist_uncaptured_failure(lease, error, *, write_session=None):
    """An owned saved business error is a terminal failure, never OCR success."""
    from src.services.ingest_job_service import lock_ingest_lease
    from src.services.uploaded_pdf_service import schedule_uploaded_pdf_retry
    if not isinstance(error, ValueError):
        return None
    with (nullcontext(write_session) if write_session is not None else transaction()) as session:
        row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
            IngestDelivery.business_generation == lease["generation"]).with_for_update()).scalar_one_or_none()
        if row is None or not fence_original_write(session, lease):
            return None
        original = _original(session, row)
        if original is None:
            return None
        if row.terminal_receipt is not None and row.terminal_receipt.get("kind") == "source_failure":
            return deepcopy(row.terminal_receipt)
        for job in session.execute(select(OcrJob).where(OcrJob.metrics["ingest_lease"]["id"].as_string() == lease["id"],
            OcrJob.metrics["ingest_lease"]["claim_id"].as_string() == lease["claim_id"])).scalars():
            if (job.metrics or {}).get("nonworkflow_execution") is not None or (job.metrics or {}).get("workflow_attempt_id") is not None:
                return None
        if not lock_ingest_lease(session, lease):
            return None
        receipt = {"kind": "source_failure", "status": "failed", "error": str(error),
            "job_id": row.job_id, "business_generation": row.business_generation,
            "input_digest": row.input_digest, "original_payload": deepcopy(row.original_payload),
            "owner_lease": deepcopy(lease)}
        original.status = "error"
        original.failure_kind = "business"
        original.last_error = str(error)
        original.finished_at = datetime.utcnow()
        original.updated_at = datetime.utcnow()
        row.terminal_receipt = receipt
        uploaded = lease.get("uploaded_lease")
        if uploaded is not None:
            if schedule_uploaded_pdf_retry(uploaded["id"], lease=uploaded, worker_instance=uploaded["lease_owner"],
                error_code="ingest_source_failure", error_message=str(error), terminal=True, write_session=session) is None:
                raise ValueError("ingest_delivery_source_failure_warning_refused")
        session.flush()
        return deepcopy(receipt)


def settle_non_ocr_skip(lease, terminal):
    from src.models.document import OrderDocument
    from src.services.ingest_job_service import lock_ingest_lease
    from src.services.uploaded_pdf_service import _write_attempt
    if terminal.get("status") != "done" or terminal.get("result_state") != "skipped":
        raise ValueError("ingest_delivery_skip_receipt_required")
    with transaction() as session:
        if not lock_ingest_lease(session, lease, terminal=True):
            raise ValueError("ingest_delivery_skip_owner_lost")
        row = session.execute(select(IngestDelivery).where(IngestDelivery.job_id == lease["id"],
            IngestDelivery.business_generation == lease["generation"]).with_for_update()).scalar_one()
        stored = row.terminal_receipt
        if stored is None or not _same(stored.get("receipt"), terminal):
            raise ValueError("ingest_delivery_skip_terminal_not_saved")
        document = session.get(OrderDocument, terminal.get("document_id"), with_for_update=True)
        if document is None or document.status != "skipped" or document.source_email_id != lease["id"] or document.storage_uri != row.original_payload["pdf_uri"]:
            raise ValueError("ingest_delivery_skip_document_mismatch")
        ingest = session.get(IngestJob, lease["id"])
        now = datetime.utcnow()
        ingest.status = "done"
        ingest.finished_at = now
        ingest.updated_at = now
        ingest.last_error = None
        uploaded = lease.get("uploaded_lease")
        if uploaded is not None:
            upload = session.get(UploadedPdf, uploaded["id"], with_for_update=True)
            if upload.current_document_id != document.id:
                raise ValueError("ingest_delivery_skip_uploaded_document_mismatch")
            upload.status = "completed"
            upload.current_stage = "registration_skipped"
            upload.lease_expires_at = None
            upload.next_retry_at = None
            upload.updated_at = now
            _write_attempt(session, uploaded_pdf_id=upload.id, attempt_no=upload.attempt_count,
                stage="registration_skipped", status="completed", worker_instance=uploaded["lease_owner"], finished_at=now)
