"""Durable transport generations retain one immutable business attempt."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import re
from uuid import uuid4

from sqlalchemy import select, update

from src.db import session_scope
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery

PENDING = "pending_dispatch"
DISPATCHED = "dispatched"
CLAIMED = "claimed"
ACKNOWLEDGED = "acknowledged"
ABANDONED = "abandoned"
TERMINAL_STATES = {ACKNOWLEDGED, ABANDONED}
REQUEST_KEYS = {
    "request_mode", "reparse_ocr_prompt", "reparse_prompt_preset",
    "reparse_requested_provider", "reparse_ocr_model", "reparse_llm_assist",
}
_claim_context = ContextVar("workflow_delivery_claim", default=None)
_terminal_attempt_context = ContextVar("workflow_delivery_terminal_attempt", default=None)


def _now():
    return datetime.utcnow()


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _positive(value):
    return type(value) is int and value > 0


@contextmanager
def delivery_write():
    with session_scope() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif session.get_bind().dialect.name != "postgresql":
            raise ValueError("workflow_ocr_delivery_dialect_unsupported")
        yield session


def _locked(session, delivery_id):
    return session.execute(select(WorkflowOcrDelivery).where(
        WorkflowOcrDelivery.id == delivery_id).with_for_update()
        .execution_options(populate_existing=True)).scalar_one_or_none()


def _identity(row):
    return {"order_id": row.order_id, "job_id": row.job_id,
        "workflow_attempt_id": row.workflow_attempt_id, "workflow_revision": row.workflow_revision,
        "request_mode": row.request_mode, "pdf": row.selected_pdf_receipt, "request": row.request_identity}


def _validate_identity(row):
    if (not _positive(row.workflow_revision) or not _positive(row.delivery_generation)
            or not all(isinstance(value, str) and value for value in (
                row.id, row.order_id, row.job_id, row.workflow_attempt_id, row.task_name))):
        raise ValueError("workflow_ocr_delivery_identity_required")
    request, pdf = row.request_identity, row.selected_pdf_receipt
    if (not isinstance(request, dict) or set(request) != {
            "reparse_read_input", "selected_version_input", "request_parameters", "selected_document_id", "first_pass_source"}
            or not isinstance(request["reparse_read_input"], dict)
            or not isinstance(request["reparse_read_input"].get("order"), dict)
            or request["reparse_read_input"]["order"].get("id") != row.order_id
            or not request["reparse_read_input"].get("configured_input")
            or not isinstance(request["reparse_read_input"].get("physical_menu_entries"), list)
            or not isinstance(pdf, dict)
            or any(not isinstance(pdf.get(key), str) or not pdf[key] for key in (
                "uploaded_pdf_id", "document_id", "order_id", "message_id", "storage_uri", "sha256"))
            or pdf["order_id"] != row.order_id
            or not re.fullmatch(r"[0-9a-f]{64}", pdf["sha256"])):
        raise ValueError("workflow_ocr_delivery_captured_source_missing")
    if (pdf.get("generation") is not None and (
            not isinstance(pdf["generation"], str) or not pdf["generation"].isdigit())):
        raise ValueError("workflow_ocr_delivery_pdf_generation_invalid")
    source = request["first_pass_source"]
    if source is not None and (not isinstance(source, dict) or source.get("order_id") != row.order_id
            or not source.get("evidence_run_id") or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("input_sha256", "")))):
        raise ValueError("workflow_ocr_delivery_first_pass_source_invalid")
    if (not isinstance(row.task_contract, dict) or row.task_contract.get("name") != row.task_name
            or not row.task_name.endswith("/tasks/" + row.id + "-g" + str(row.delivery_generation))):
        raise ValueError("workflow_ocr_delivery_task_identity_invalid")
    selected = request["selected_version_input"]
    if selected is not None and (not isinstance(selected, dict)
            or selected.get("order_id") != row.order_id
            or selected.get("document_id") != pdf["document_id"]
            or selected.get("storage_uri") != pdf["storage_uri"]):
        raise ValueError("workflow_ocr_delivery_selected_version_mismatch")
    if request["selected_document_id"] is not None and request["selected_document_id"] != pdf["document_id"]:
        raise ValueError("workflow_ocr_delivery_selected_document_mismatch")
    params = request["request_parameters"]
    if (not isinstance(params, dict) or set(params) != REQUEST_KEYS
            or row.request_mode not in {"ocr_reparse", "llm_reparse", "ocr_rerun"}
            or params["request_mode"] != row.request_mode):
        raise ValueError("workflow_ocr_delivery_request_mode_unknown")
    if row.request_mode in {"ocr_reparse", "llm_reparse"}:
        if type(params["reparse_llm_assist"]) is not bool:
            raise ValueError("workflow_ocr_delivery_llm_assist_required")
        if any(params[key] is not None and not isinstance(params[key], str)
               for key in REQUEST_KEYS - {"request_mode", "reparse_llm_assist"}):
            raise ValueError("workflow_ocr_delivery_request_parameters_invalid")
    if _digest(_identity(row)) != row.identity_sha256:
        raise ValueError("workflow_ocr_delivery_identity_digest_mismatch")


def _business_status(session, row):
    """Only an exact persisted terminal receipt permits a business-result ACK."""
    job = session.get(OcrJob, row.job_id, populate_existing=True)
    workflow = session.get(OrderWorkflowState, row.order_id, populate_existing=True)
    if job is None or workflow is None:
        raise ValueError("workflow_ocr_delivery_business_owner_missing")
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    secondary = workflow.secondary_actions_json
    meta = secondary.get("workflow_v2", {}) if isinstance(secondary, dict) else {}
    job_attempt, workflow_attempt = metrics.get("workflow_attempt_id"), meta.get("ocr_job_attempt_id")
    if not isinstance(job_attempt, str) or not job_attempt or not isinstance(workflow_attempt, str) or not workflow_attempt:
        raise ValueError("workflow_ocr_delivery_business_attempt_missing")
    if job_attempt != workflow_attempt:
        raise ValueError("workflow_ocr_delivery_business_attempt_disagrees")
    if job_attempt != row.workflow_attempt_id or workflow_attempt != row.workflow_attempt_id:
        return "superseded"
    receipt = metrics.get("rerun_terminal_receipt")
    if (isinstance(receipt, dict) and receipt == row.result_receipt and all(receipt.get(key) == value for key, value in {
        "order_id": row.order_id, "job_id": row.job_id,
        "workflow_attempt_id": row.workflow_attempt_id, "workflow_revision": row.workflow_revision,
    }.items()) and receipt.get("status") in {"done", "failed"} and job.status == receipt["status"]
            and receipt.get("evidence_run_id") == metrics.get("evidence_run_id")
            and receipt.get("workflow_delivery_owner") == {
                "delivery_id": row.id, "generation": row.delivery_generation, "claim_token": row.claim_token}):
        return "terminal"
    if job.status in {"done", "failed", "cancelled"}:
        return "hard_stop"
    request = row.request_identity
    if (job.order_id != row.order_id or workflow.revision != row.workflow_revision
            or workflow.state != "ocr_running"
            or metrics.get("workflow_completion_revision") != row.workflow_revision
            or meta.get("ocr_job_id") != row.job_id
            or metrics.get("workflow_ocr_delivery_id") != row.id
            or job.input_reference != row.selected_pdf_receipt["storage_uri"]
            or metrics.get("selected_pdf_receipt") != row.selected_pdf_receipt
            or metrics.get("reparse_read_input") != request["reparse_read_input"]
            or metrics.get("selected_version_input") != request["selected_version_input"]
            or metrics.get("first_pass_source") != request["first_pass_source"]
            or any(metrics.get(key) != value for key, value in request["request_parameters"].items())):
        raise ValueError("workflow_ocr_delivery_original_identity_changed")
    if job.status == "awaiting_output":
        return "handoff" if _validated_handoff(job, row) else "awaiting_output"
    if job.status != "running":
        raise ValueError("workflow_ocr_delivery_business_state_unknown")
    return "eligible"


def create_intent_in_session(session, *, order_id, job_id, workflow_attempt_id,
                             workflow_revision, request_mode, selected_pdf_receipt, request_identity):
    from src.services.workflow_ocr_delivery_dispatch_service import task_contract
    from src.services.workflow_attempt_guard_service import capture_selected_pdf_receipt, lock_selected_version_input
    delivery_id = f"WOD-{uuid4().hex}"
    contract = task_contract(delivery_id, 1)
    row = WorkflowOcrDelivery(id=delivery_id, order_id=order_id, job_id=job_id,
        workflow_attempt_id=workflow_attempt_id, workflow_revision=workflow_revision,
        delivery_generation=1, task_name=contract["name"], task_contract=contract,
        request_mode=request_mode, selected_pdf_receipt=deepcopy(selected_pdf_receipt),
        request_identity=deepcopy(request_identity), state=PENDING, visit_count=0)
    row.identity_sha256 = _digest(_identity(row))
    _validate_identity(row)
    job = session.get(OcrJob, job_id)
    if job is None:
        raise ValueError("workflow_ocr_delivery_job_missing")
    job.metrics = {**job.metrics, "workflow_ocr_delivery_id": row.id}
    session.flush()
    if _business_status(session, row) != "eligible":
        raise ValueError("workflow_ocr_delivery_original_attempt_mismatch")
    order, selected = session.get(Order, order_id), request_identity["selected_version_input"]
    if selected is not None and not lock_selected_version_input(session, order_id, selected):
        raise ValueError("workflow_ocr_delivery_selected_version_changed")
    if capture_selected_pdf_receipt(session, order, selected, job.input_reference) != selected_pdf_receipt:
        raise ValueError("workflow_ocr_delivery_selected_pdf_changed")
    session.add(row)
    session.flush()
    return row


def snapshot(row):
    return {key: deepcopy(getattr(row, key)) for key in (
        "id", "delivery_generation", "task_name", "task_contract", "state",
        "claim_token", "lease_expires_at", "identity_sha256")}


def prepare_dispatch(delivery_id, generation):
    if not _positive(generation):
        raise ValueError("workflow_ocr_delivery_generation_required")
    with delivery_write() as session:
        row = _locked(session, delivery_id)
        if row is None or generation > row.delivery_generation:
            raise ValueError("workflow_ocr_delivery_generation_unknown")
        _validate_identity(row)
        if generation < row.delivery_generation or row.state in TERMINAL_STATES:
            return None
        status = _business_status(session, row)
        if status in {"terminal", "handoff", "superseded", "hard_stop"}:
            row.state = ("blocked_business_terminal" if status == "hard_stop" else
                ACKNOWLEDGED if status in {"terminal", "handoff"} else ABANDONED)
            return None
        if row.state == CLAIMED and (row.lease_expires_at is None or row.lease_expires_at > _now()):
            return None
        if row.state not in {PENDING, DISPATCHED, CLAIMED}:
            raise ValueError("workflow_ocr_delivery_state_unknown")
        return snapshot(row)


def mark_dispatched(delivery_id, generation, receipt):
    with delivery_write() as session:
        row = _locked(session, delivery_id)
        if row is None or row.delivery_generation != generation or receipt.get("task_name") != row.task_name:
            return False
        if row.state in {CLAIMED, ACKNOWLEDGED, ABANDONED}:
            return True
        if row.state not in {PENDING, DISPATCHED}:
            return False
        row.state, row.transport_receipt = DISPATCHED, deepcopy(receipt)
        row.dispatch_error = None
        row.updated_at = _now()
        return True


def inspect_generation(delivery_id, generation):
    if not _positive(generation):
        raise ValueError("workflow_ocr_delivery_generation_required")
    with session_scope() as session:
        row = session.get(WorkflowOcrDelivery, delivery_id)
        if row is None or generation > row.delivery_generation:
            raise ValueError("workflow_ocr_delivery_generation_unknown")
        _validate_identity(row)
        return {"duplicate": generation < row.delivery_generation or row.state in TERMINAL_STATES}


def claim_delivery(delivery_id, generation, *, lease_expires_at):
    if not _positive(generation):
        raise ValueError("workflow_ocr_delivery_generation_required")
    with delivery_write() as session:
        row = _locked(session, delivery_id)
        if row is None or generation > row.delivery_generation:
            raise ValueError("workflow_ocr_delivery_generation_unknown")
        _validate_identity(row)
        if generation < row.delivery_generation or row.state in TERMINAL_STATES:
            return {"duplicate": True}
        if row.state == CLAIMED and row.lease_expires_at is not None and row.lease_expires_at > _now():
            return {"duplicate": True}
        status = _business_status(session, row)
        if status in {"terminal", "handoff", "superseded", "hard_stop"}:
            if status == "hard_stop":
                raise ValueError("workflow_ocr_delivery_owned_terminal_receipt_missing")
            row.state = ACKNOWLEDGED if status in {"terminal", "handoff"} else ABANDONED
            return {"duplicate": True}
        if row.state not in {PENDING, DISPATCHED, CLAIMED}:
            raise ValueError("workflow_ocr_delivery_state_unknown")
        if lease_expires_at <= _now():
            raise ValueError("workflow_ocr_delivery_lease_invalid")
        row.state, row.claim_token, row.lease_expires_at = CLAIMED, uuid4().hex, lease_expires_at
        row.updated_at = _now()
        return {
            "delivery_id": row.id, "generation": generation, "claim_token": row.claim_token,
            "order_id": row.order_id, "job_id": row.job_id,
            "workflow_attempt_id": row.workflow_attempt_id, "workflow_revision": row.workflow_revision,
            "request_mode": row.request_mode, "request_identity": deepcopy(row.request_identity),
            "selected_pdf_receipt": deepcopy(row.selected_pdf_receipt), "business_status": status,
        }


@contextmanager
def claim_context(claim):
    token = _claim_context.set(claim)
    terminal_token = _terminal_attempt_context.set({"job_id": claim["job_id"], "started": False})
    try:
        yield
    finally:
        _terminal_attempt_context.reset(terminal_token)
        _claim_context.reset(token)


def callback_failure_completion_allowed(job_id):
    claim = _claim_context.get()
    marker = _terminal_attempt_context.get()
    return bool(claim is not None and claim["job_id"] == job_id
        and marker is not None and marker["job_id"] == job_id and not marker["started"])


def lock_claim_write(session, *, job_id, order_id, attempt, revision):
    """Run inside the actual result transaction after workflow/job locks."""
    claim = _claim_context.get()
    job = session.get(OcrJob, job_id)
    delivery_id = (job.metrics or {}).get("workflow_ocr_delivery_id") if job is not None else None
    if delivery_id is None:
        return claim is None
    if claim is None:
        row = _locked(session, delivery_id)
        return bool(row is not None and row.state in {CLAIMED, ACKNOWLEDGED}
            and row.order_id == order_id and row.job_id == job_id
            and row.workflow_attempt_id == attempt and row.workflow_revision == revision
            and _validated_handoff(job, row))
    if (claim.get("delivery_id") != delivery_id or claim.get("job_id") != job_id
            or claim.get("order_id") != order_id or claim.get("workflow_attempt_id") != attempt
            or claim.get("workflow_revision") != revision):
        return False
    conditions = (
        WorkflowOcrDelivery.id == delivery_id,
        WorkflowOcrDelivery.delivery_generation == claim["generation"],
        WorkflowOcrDelivery.claim_token == claim["claim_token"],
        WorkflowOcrDelivery.state == CLAIMED,
        WorkflowOcrDelivery.lease_expires_at > _now(),
    )
    if session.get_bind().dialect.name == "sqlite":
        return session.execute(update(WorkflowOcrDelivery).where(*conditions)
            .values(updated_at=WorkflowOcrDelivery.updated_at)).rowcount == 1
    return session.execute(select(WorkflowOcrDelivery.id).where(*conditions)
        .with_for_update()).scalar_one_or_none() is not None


def acknowledge_claim(delivery_id, generation, claim_token):
    with delivery_write() as session:
        row = _locked(session, delivery_id)
        if (row is None or row.delivery_generation != generation or row.claim_token != claim_token
                or row.state != CLAIMED or row.lease_expires_at is None or row.lease_expires_at <= _now()):
            return False
        _validate_identity(row)
        if _business_status(session, row) not in {"terminal", "handoff", "superseded"}:
            raise ValueError("workflow_ocr_delivery_persisted_result_missing")
        row.state, row.lease_expires_at, row.updated_at = ACKNOWLEDGED, None, _now()
        return True


def renew_missing_delivery(delivery_id, observed):
    from src.services.workflow_ocr_delivery_dispatch_service import next_task_contract
    with delivery_write() as session:
        row = _locked(session, delivery_id)
        if row is None or snapshot(row) != observed:
            return False
        if _business_status(session, row) not in {"eligible", "awaiting_output"}:
            return False
        if row.state == CLAIMED and (row.lease_expires_at is None or row.lease_expires_at > _now()):
            return False
        contract = next_task_contract(row.task_contract, row.id, row.delivery_generation + 1)
        row.delivery_generation += 1
        row.task_name, row.task_contract, row.state = contract["name"], contract, PENDING
        row.claim_token = row.lease_expires_at = row.transport_receipt = None
        row.updated_at = _now()
        return True


def record_dispatch_error(delivery_id, generation, error):
    with delivery_write() as session:
        row = _locked(session, delivery_id)
        if row is not None and row.delivery_generation == generation and row.state not in TERMINAL_STATES:
            row.dispatch_error = error


def pending_dispatches(limit):
    if not _positive(limit):
        raise ValueError("workflow_ocr_delivery_drain_limit_invalid")
    with delivery_write() as session:
        rows = session.execute(select(WorkflowOcrDelivery).where(
            WorkflowOcrDelivery.state.in_([PENDING, DISPATCHED]) |
            ((WorkflowOcrDelivery.state == CLAIMED) & (WorkflowOcrDelivery.lease_expires_at <= _now()))
        ).order_by(WorkflowOcrDelivery.visit_count, WorkflowOcrDelivery.created_at, WorkflowOcrDelivery.id)
            .limit(limit).with_for_update(skip_locked=True)).scalars().all()
        items = []
        for row in rows:
            row.visit_count += 1
            try:
                _validate_identity(row)
                status = _business_status(session, row)
            except ValueError as exc:
                row.dispatch_error = str(exc)
                items.append({"delivery_id": row.id, "generation": row.delivery_generation,
                    "error": str(exc)})
                continue
            if status in {"eligible", "awaiting_output"}:
                items.append({"delivery_id": row.id, "generation": row.delivery_generation})
            else:
                row.state = ("blocked_business_terminal" if status == "hard_stop" else
                    ACKNOWLEDGED if status in {"terminal", "handoff"} else ABANDONED)
        return items


def _validated_handoff(job, row):
    from src.services.order_service import _pending_request_matches
    pending = (job.metrics or {}).get("pending_receipt")
    saved = row.handoff_receipt
    if not isinstance(pending, dict) or not isinstance(saved, dict) or pending != saved.get("pending_receipt"):
        return False
    owner = pending.get("workflow_delivery_owner")
    pipeline = pending.get("pipeline_delivery_receipt")
    if not isinstance(pipeline, dict):
        return False
    original_pipeline = {
        "job_id": row.job_id, "order_id": row.order_id,
        "workflow_attempt_id": row.workflow_attempt_id, "workflow_revision": row.workflow_revision,
        "execution_id": None, "selected_input_reference": row.selected_pdf_receipt["storage_uri"],
        "input_reference": pending.get("input_reference"), "output_reference": pending.get("output_reference"),
        "input_sha256": row.selected_pdf_receipt["sha256"], "selected_pdf_receipt": row.selected_pdf_receipt,
    }
    if (any(pipeline.get(key) != value for key, value in original_pipeline.items())
            or type(pipeline.get("generation")) not in {str, int}
            or not str(pipeline["generation"]).isdigit()):
        return False
    return bool(isinstance(owner, dict) and owner == saved.get("owner")
        and owner.get("delivery_id") == row.id and owner.get("generation") == row.delivery_generation
        and owner.get("claim_token") == row.claim_token
        and saved.get("selected_pdf_receipt") == row.selected_pdf_receipt
        and pending.get("order_id") == row.order_id and pending.get("job_id") == row.job_id
        and pending.get("workflow_attempt_id") == row.workflow_attempt_id
        and pending.get("workflow_revision") == row.workflow_revision
        and pending.get("selected_input_reference") == row.selected_pdf_receipt["storage_uri"]
        and pending.get("selected_document_id") == row.selected_pdf_receipt["document_id"]
        and pending.get("output_reference") == job.output_reference
        and isinstance(pending.get("input_reference"), str) and bool(pending["input_reference"])
        and isinstance(pending.get("output_reference"), str) and bool(pending["output_reference"])
        and pending.get("pipeline_delivery_receipt") == (job.metrics or {}).get("pipeline_delivery_receipt")
        and _pending_request_matches(job.metrics, pending))


def bind_pending_handoff_in_session(session, job):
    """Runs before the original pending-receipt transaction flushes."""
    claim = _claim_context.get()
    if job.status in {"done", "failed"}:
        track_terminal_job(session, job.id)
        # mark_ocr_run_completed may flush its terminal state before the enclosing
        # original result transaction constructs the receipt. Validate at commit.
        if isinstance((job.metrics or {}).get("rerun_terminal_receipt"), dict):
            bind_terminal_result_in_session(session, job, claim)
        return
    if claim is None or job.id != claim["job_id"] or job.status != "awaiting_output":
        return
    if not lock_claim_write(session, job_id=job.id, order_id=claim["order_id"],
                            attempt=claim["workflow_attempt_id"], revision=claim["workflow_revision"]):
        raise ValueError("workflow_ocr_delivery_handoff_claim_lost")
    row = _locked(session, claim["delivery_id"])
    pending = deepcopy((job.metrics or {}).get("pending_receipt"))
    if not isinstance(pending, dict):
        raise ValueError("workflow_ocr_delivery_handoff_receipt_missing")
    owner = {key: claim[key] for key in ("delivery_id", "generation", "claim_token")}
    pending["workflow_delivery_owner"] = owner
    job.metrics = {**job.metrics, "pending_receipt": pending}
    row.handoff_receipt = {"owner": owner, "pending_receipt": pending,
        "selected_pdf_receipt": deepcopy(row.selected_pdf_receipt)}
    if not _validated_handoff(job, row):
        raise ValueError("workflow_ocr_delivery_handoff_identity_mismatch")


def bind_terminal_result_in_session(session, job, claim):
    row = _locked(session, job.metrics["workflow_ocr_delivery_id"])
    receipt = deepcopy((job.metrics or {}).get("rerun_terminal_receipt"))
    if row is None or not isinstance(receipt, dict):
        raise ValueError("workflow_ocr_delivery_terminal_receipt_missing")
    if claim is not None:
        if not lock_claim_write(session, job_id=job.id, order_id=row.order_id,
                                attempt=row.workflow_attempt_id, revision=row.workflow_revision):
            raise ValueError("workflow_ocr_delivery_terminal_claim_lost")
        owner = {key: claim[key] for key in ("delivery_id", "generation", "claim_token")}
    elif row.handoff_receipt is not None and _validated_handoff(job, row):
        owner = deepcopy(row.handoff_receipt["owner"])
    else:
        raise ValueError("workflow_ocr_delivery_terminal_owner_missing")
    if any(receipt.get(key) != value for key, value in {
        "order_id": row.order_id, "job_id": row.job_id,
        "workflow_attempt_id": row.workflow_attempt_id, "workflow_revision": row.workflow_revision,
        "status": job.status, "evidence_run_id": job.metrics.get("evidence_run_id"),
    }.items()):
        raise ValueError("workflow_ocr_delivery_terminal_identity_mismatch")
    receipt["workflow_delivery_owner"] = owner
    job.metrics = {**job.metrics, "rerun_terminal_receipt": receipt}
    row.result_receipt = deepcopy(receipt)


def track_terminal_job(session, job_id):
    marker = _terminal_attempt_context.get()
    if marker is not None and marker["job_id"] == job_id:
        # Mutable state survives rollback and is shared with copy_context workers.
        marker["started"] = True
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        raise ValueError("workflow_ocr_delivery_terminal_transaction_missing")
    session.info.setdefault("workflow_delivery_terminal_jobs", {}).setdefault(transaction, set()).add(job_id)


def terminal_jobs_for_commit(session):
    tracked = session.info.get("workflow_delivery_terminal_jobs", {})
    transaction = session.get_nested_transaction() or session.get_transaction()
    jobs = set()
    # Session.commit prepares the root before closing open savepoints. Validate
    # every ancestor now, rather than relying on a later child merge/event.
    while transaction is not None:
        jobs.update(tracked.get(transaction, ()))
        transaction = transaction.parent
    return jobs


def require_terminal_receipts_before_commit(session):
    for job_id in terminal_jobs_for_commit(session):
        job = session.get(OcrJob, job_id)
        receipt = (job.metrics or {}).get("rerun_terminal_receipt") if job is not None else None
        row = session.get(WorkflowOcrDelivery, (job.metrics or {}).get("workflow_ocr_delivery_id")) if job is not None else None
        if row is None or not isinstance(receipt, dict) or receipt != row.result_receipt:
            raise ValueError("workflow_ocr_delivery_terminal_transaction_incomplete")
