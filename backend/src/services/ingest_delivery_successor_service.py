from src.staging_data import order_runtime_setting as _runtime_setting

"""Durable automatic LLM continuation of one accepted first-pass result."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
import json
from hashlib import sha256

from sqlalchemy import event, or_, select, update
from sqlalchemy.orm import Session
from src.models.ingest_delivery_successor import IngestDeliverySuccessor
from src.models.ocr_job import OcrJob
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import ingest_delivery_service as parent
from src.services import ingest_delivery_dispatch_service as transport
from src.services.workflow_ocr_delivery_execution_service import execution_mutex

_owner = ContextVar("ingest_auto_llm_owner", default=None)
_physical_owner = ContextVar("ingest_auto_llm_physical_owner", default=None)


@contextmanager
def _exclusive(identity):
    with execution_mutex(identity) as acquired:
        token = _physical_owner.set(identity if acquired else None)
        try:
            yield acquired
        finally:
            _physical_owner.reset(token)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _recorded_identity(session, row):
    """Prove the saved original, without granting any domain write ownership."""
    if type(row.generation) is not int or row.generation < 1:
        raise ValueError("auto_llm_recorded_generation_invalid")
    delivery = session.get(parent.IngestDelivery, row.parent_delivery_id)
    job = session.get(OcrJob, row.job_id)
    if delivery is None or job is None:
        raise ValueError("auto_llm_recorded_owner_missing")
    lease = row.owner_lease
    from src.services.ingest_job_service import payload_digest
    if (delivery.job_id != lease["id"] or delivery.business_generation != lease["generation"]
        or delivery.input_digest != lease["input_digest"]
        or payload_digest(delivery.original_payload) != delivery.input_digest
        or not parent._same(delivery.owner_lease, lease)
        or not parent._same((delivery.terminal_receipt or {}).get("receipt"), row.original["predecessor_receipt"])):
        raise ValueError("auto_llm_recorded_predecessor_unproven")
    expected = transport.renew_contract(delivery.task_contract, row.id, row.generation)
    expected["http_request"]["url"] = expected["http_request"]["url"].rsplit("/", 1)[0] + "/auto-llm"
    if expected.get("mode") == "request_local":
        expected["kind"] = "auto-llm"
    capture = (job.metrics or {}).get("nonworkflow_execution")
    original = row.original["execution_input"]
    if (not parent._same(row.task_contract, expected) or not isinstance(capture, dict)
        or (job.metrics or {}).get("workflow_attempt_id") is not None
        or (job.metrics or {}).get("ingest_successor_id") != row.id
        or job.id != original["job_id"] or job.input_reference != original["input_reference"]
        or any(not parent._same(capture.get(key), original.get(key)) for key in
            ("job_id", "execution_id", "ingest_successor_id", "ingest_lease", "start_orders", "selected_pdf_receipt", "input_reference", "job_order_id"))):
        raise ValueError("auto_llm_recorded_contract_unproven")
    return {"id": row.id, "generation": row.generation, "token": row.claim_token,
        "job_id": row.job_id, "execution_id": original["execution_id"],
        "parent_delivery_id": row.parent_delivery_id, "predecessor_job_id": row.predecessor_job_id,
        "original_digest": _digest(row.original), "contract_digest": _digest(row.task_contract)}


def _known_conflict(session, row):
    """Only explicit observed differences, never an exception-string classifier."""
    _recorded_identity(session, row)
    lease = row.owner_lease
    original = session.get(parent.IngestJob, lease["id"], populate_existing=True, with_for_update=True)
    if original is None or not isinstance(original.generation, str) or not original.generation:
        raise ValueError("auto_llm_current_reservation_unknown")
    def proof(code, expected, observed):
        return {"code": code, "status": "superseded" if code == "original_superseded" else "refused",
            "expected_digest": _digest(expected), "observed_digest": _digest(observed)}
    if original.generation != lease["generation"]:
        return proof("original_superseded", lease["generation"], original.generation)
    if not isinstance(original.input_digest, str) or not original.input_digest or not isinstance(original.payload, dict):
        raise ValueError("auto_llm_current_source_unknown")
    saved = session.get(parent.IngestDelivery, row.parent_delivery_id)
    if original.input_digest != lease["input_digest"] or not parent._same(original.payload, saved.original_payload):
        return proof("original_source_changed", {"digest": lease["input_digest"], "payload": saved.original_payload},
            {"digest": original.input_digest, "payload": original.payload})
    predecessor = session.get(OcrJob, row.predecessor_job_id, populate_existing=True, with_for_update=True)
    expected = row.original["predecessor_receipt"]
    actual = (predecessor.metrics or {}).get("first_pass_terminal_receipt") if predecessor is not None else None
    if predecessor is None or not isinstance(actual, dict):
        raise ValueError("auto_llm_current_predecessor_unknown")
    if not parent._same(expected, actual):
        return proof("predecessor_changed", expected, actual)
    if predecessor.status != "done":
        if predecessor.status not in {"failed", "pending", "running", "recovering", "awaiting_output"}:
            raise ValueError("auto_llm_current_predecessor_state_unknown")
        return proof("predecessor_changed", {"status": "done", "receipt": expected},
            {"status": predecessor.status, "receipt": actual})
    evidence = session.get(OrderOcrEvidenceRun, row.original["evidence_id"], populate_existing=True, with_for_update=True)
    if evidence is None:
        raise ValueError("auto_llm_current_evidence_unknown")
    actual_evidence = {"digest": evidence.artifact_digest, "payload": evidence.payload_json} if evidence is not None else None
    expected_evidence = {"digest": row.original["evidence_digest"], "payload": row.original["payload"]}
    if not parent._same(expected_evidence, actual_evidence):
        return proof("evidence_changed", expected_evidence, actual_evidence)
    # Current data is read only to compare with the saved CAS snapshot, not to
    # supplement or recapture the accepted source/configuration.
    from src.models.order import Order
    from src.services.workflow_attempt_guard_service import _order_execution_input
    job = session.get(OcrJob, row.job_id)
    capture = job.metrics["nonworkflow_execution"]
    prepared = (predecessor.metrics or {}).get("nonworkflow_execution")
    if not row.started:
        if not isinstance(prepared, dict) or not isinstance(prepared.get("orders"), dict):
            raise ValueError("auto_llm_current_preparation_unknown")
        if not parent._same(capture["orders"], prepared["orders"]):
            return proof("prepared_version_changed", capture["orders"], prepared["orders"])
    order_id = capture["order_id"]
    order = session.get(Order, order_id, populate_existing=True, with_for_update=True)
    if order is None:
        raise ValueError("auto_llm_current_order_unknown")
    actual_input = _order_execution_input(session, order, lock=True) if order is not None else None
    expected_input = capture["orders"][order_id]
    if not parent._same(expected_input, actual_input):
        return proof("snapshot_changed", expected_input, actual_input)
    return None


_REFUSAL_CODES = {"original_superseded", "original_source_changed", "predecessor_changed", "evidence_changed", "snapshot_changed", "prepared_version_changed"}


def _validate_refusal(session, row):
    receipt = row.terminal_receipt
    job = session.get(OcrJob, row.job_id)
    identity = _recorded_identity(session, row)
    if (not isinstance(receipt, dict) or set(receipt) != {"kind", "identity", "proof", "warning", "recorded_at"}
        or receipt["kind"] != "owned_refusal" or row.state != "REFUSED" or not row.claim_token
        or not parent._same(receipt["identity"], identity)
        or set(receipt["proof"]) != {"code", "status", "expected_digest", "observed_digest"}
        or receipt["proof"]["code"] not in _REFUSAL_CODES
        or receipt["proof"]["status"] != ("superseded" if receipt["proof"]["code"] == "original_superseded" else "refused")
        or not parent._same(receipt["warning"], {"code": receipt["proof"]["code"], "domain_adoption": False})
        or job.status != "failed" or not parent._same((job.metrics or {}).get("auto_llm_refusal_receipt"), receipt)):
        raise ValueError("auto_llm_refusal_receipt_unproven")
    return "business_" + receipt["proof"]["status"]


def refuse_if_known(identity, generation):
    """Caller holds the original mutex. CAS writes only delivery and owned job diagnostic."""
    with parent.transaction() as session:
        row = _locked(session, identity)
        if row is None or type(generation) is not int or row.generation != generation:
            raise ValueError("auto_llm_refusal_generation_unknown")
        if _physical_owner.get() != "ingest-original:" + row.owner_lease["id"]:
            raise ValueError("auto_llm_refusal_physical_owner_missing")
        claim = _owner.get()
        if claim is not None and not parent._same(claim, {"id": row.id, "generation": row.generation, "token": row.claim_token}):
            raise ValueError("auto_llm_refusal_owner_lost")
        if (row.terminal_receipt or {}).get("kind") == "owned_refusal":
            return _validate_refusal(session, row)
        if row.terminal_receipt is not None:
            return None  # Never overwrite an owned completed business receipt.
        proof = _known_conflict(session, row)
        if proof is None:
            return None
        if claim is None:
            if row.state not in {"PENDING", "SENT", "CLAIMED"} and not (row.state == "ACKNOWLEDGED" and row.handoff_receipt is not None):
                raise ValueError("auto_llm_refusal_state_unknown")
            row.claim_token = uuid4().hex
            row.state = "CLAIMED"
            session.flush()
        elif row.state not in {"CLAIMED", "ACKNOWLEDGED"} or (row.state == "ACKNOWLEDGED" and row.handoff_receipt is None):
            raise ValueError("auto_llm_refusal_owner_lost")
        receipt = {"kind": "owned_refusal", "identity": _recorded_identity(session, row), "proof": proof,
            "warning": {"code": proof["code"], "domain_adoption": False}, "recorded_at": datetime.utcnow().isoformat()}
        job = session.get(OcrJob, row.job_id)
        metrics = {**(job.metrics or {}), "auto_llm_refusal_receipt": receipt,
            "delivery_warning": receipt["warning"], "result_state": proof["status"], "next_recovery_at": None}
        # Dedicated diagnostic CAS, not the order/evidence adoption guard. The
        # latter remains refused and cannot be bypassed by this receipt.
        changed = session.execute(update(OcrJob).where(OcrJob.id == row.job_id,
            OcrJob.metrics["ingest_successor_id"].as_string() == row.id,
            OcrJob.metrics["nonworkflow_execution"]["execution_id"].as_string() == receipt["identity"]["execution_id"],
            OcrJob.metrics["workflow_attempt_id"].as_string().is_(None)).values(status="failed",
                error_message="auto_llm_" + proof["code"], metrics=metrics, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ValueError("auto_llm_refusal_job_cas_lost")
        changed = session.execute(update(IngestDeliverySuccessor).where(IngestDeliverySuccessor.id == row.id,
            IngestDeliverySuccessor.generation == generation, IngestDeliverySuccessor.claim_token == row.claim_token,
            IngestDeliverySuccessor.state == row.state).values(state="REFUSED", terminal_receipt=receipt,
                last_error="auto_llm_" + proof["code"], updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ValueError("auto_llm_refusal_delivery_cas_lost")
        session.refresh(row)
        session.refresh(job)
        return _validate_refusal(session, row)


def diagnostic(identity):
    """Pure GET projection: no claim, snapshot reconstruction, provider or repair."""
    from src.db import session_scope
    with session_scope() as session:
        row = session.get(IngestDeliverySuccessor, identity)
        if row is None:
            raise ValueError("auto_llm_delivery_missing")
        receipt = row.terminal_receipt
        if (receipt or {}).get("kind") == "owned_refusal":
            _validate_refusal(session, row)
        return {"id": row.id, "job_id": row.job_id, "generation": row.generation, "state": row.state,
            "terminal_receipt": deepcopy(receipt), "warning": deepcopy(receipt.get("warning")) if isinstance(receipt, dict) else None}


def accept(session, job, execution_input, evidence, receipt, order):
    from src.workers.ingest_worker import _auto_llm_reparse_enabled, _looks_like_first_pass_ocr_payload
    if (not _auto_llm_reparse_enabled() or receipt["status"] != "done"
        or evidence.get("selectable") is not True or not _looks_like_first_pass_ocr_payload(evidence.get("payload_json"))
        or not order.id or not order.facility_code or not order.document_uri):
        return None
    lease = execution_input.get("ingest_lease")
    if not isinstance(lease, dict):
        return None
    if not parent.fence_original_write(session, lease):
        raise ValueError("auto_llm_predecessor_owner_lost")
    delivery = session.execute(select(parent.IngestDelivery).where(parent.IngestDelivery.job_id == lease["id"],
        parent.IngestDelivery.business_generation == lease["generation"]).with_for_update()).scalar_one()
    existing = session.execute(select(IngestDeliverySuccessor).where(
        IngestDeliverySuccessor.predecessor_job_id == job.id)).scalar_one_or_none()
    if existing is not None:
        if not parent._same(existing.original["predecessor_receipt"], receipt):
            raise ValueError("auto_llm_predecessor_changed")
        return existing.id
    identity = "AS-" + uuid4().hex
    boundary = deepcopy(execution_input)
    boundary.update(job_id="OCR-" + identity, job_order_id=order.id,
        execution_id=uuid4().hex, ingest_successor_id=identity)
    provider = str(_runtime_setting("OCR_AUTO_LLM_REPARSE_PROVIDER", "") or "").strip().lower() or None
    request = {"ocr_prompt": None, "prompt_preset": None, "ocr_provider": provider,
               "ocr_model": None, "llm_assist": True}
    saved = session.get(OrderOcrEvidenceRun, evidence["id"])
    if saved is None or not parent._same(saved.payload_json, evidence["payload_json"]):
        raise ValueError("auto_llm_original_evidence_missing")
    contract = transport.renew_contract(delivery.task_contract, identity, 1)
    contract["http_request"]["url"] = contract["http_request"]["url"].rsplit("/", 1)[0] + "/auto-llm"
    if contract.get("mode") == "request_local":
        contract["kind"] = "auto-llm"
    original = {"execution_input": boundary, "evidence_id": evidence["id"],
        "evidence_digest": saved.artifact_digest, "payload": deepcopy(evidence["payload_json"]),
        "request": request, "predecessor_receipt": deepcopy(receipt)}
    from src.services.order_service import _read_reparse_output_recovery_grace_seconds
    original["recovery_delay_seconds"] = _read_reparse_output_recovery_grace_seconds()
    child = OcrJob(id=boundary["job_id"], order_id=boundary.get("job_order_id"), status="pending",
        input_reference=boundary["input_reference"], template_id=job.template_id,
        template_version_id=job.template_version_id, metrics={"nonworkflow_execution": boundary,
            "ingest_successor_id": identity, "request_mode": "llm_reparse"})
    session.add(child)
    # These mappers have no ORM relationship to order their FK inserts.
    session.flush([child])
    session.add(IngestDeliverySuccessor(id=identity, predecessor_job_id=job.id,
        parent_delivery_id=delivery.id, job_id=boundary["job_id"], original=original,
        generation=1, state="PENDING", task_contract=contract, visit_count=0, started=False))
    return identity


def _locked(session, identity, *, read_only=False):
    if not read_only:
        session.execute(update(IngestDeliverySuccessor).where(IngestDeliverySuccessor.id == identity)
            .values(state=IngestDeliverySuccessor.state))
    return session.get(IngestDeliverySuccessor, identity, populate_existing=True, with_for_update={"read": True} if read_only else True)


def _predecessor(session, row, *, read_only=False):
    lock = {"read": True} if read_only else True
    delivery = session.get(parent.IngestDelivery, row.parent_delivery_id, with_for_update=lock)
    original_ingest = parent._original(session, delivery, read_only=read_only) if delivery is not None else None
    if original_ingest is None:
        raise ValueError("auto_llm_original_reservation_superseded")
    if original_ingest.status != "done":
        raise ValueError("auto_llm_predecessor_business_unsettled")
    parent._require_uploaded_terminal(session, delivery, "completed", read_only=read_only)
    job = session.get(OcrJob, row.predecessor_job_id, with_for_update=lock)
    evidence = session.get(OrderOcrEvidenceRun, row.original["evidence_id"], with_for_update=lock)
    if (job is None or job.status != "done" or not parent._same(
            (job.metrics or {}).get("first_pass_terminal_receipt"), row.original["predecessor_receipt"])
        or evidence is None or evidence.artifact_digest != row.original["evidence_digest"]
        or not parent._same(evidence.payload_json, row.original["payload"])):
        raise ValueError("auto_llm_predecessor_evidence_changed")
    return delivery


@contextmanager
def owned(claim):
    token = _owner.set(claim)
    try:
        yield
    finally:
        _owner.reset(token)


def fence(session, boundary, lease, *, read_only=False):
    claim = _owner.get()
    if claim is None or boundary.get("ingest_successor_id") != claim["id"]:
        return False
    row = _locked(session, claim["id"], read_only=read_only)
    if (row is None or row.generation != claim["generation"] or row.claim_token != claim["token"]
        or row.state not in {"CLAIMED", "ACKNOWLEDGED"}
        or (row.state == "ACKNOWLEDGED" and row.handoff_receipt is None)
        or not parent._same(lease, row.owner_lease)):
        return False
    original = row.original["execution_input"]
    if any(not parent._same(boundary.get(key), original.get(key)) for key in
        ("job_id", "execution_id", "start_orders", "selected_pdf_receipt", "input_reference", "job_order_id")):
        return False
    _predecessor(session, row, read_only=read_only)
    job = session.get(OcrJob, row.job_id)
    from src.services.ocr_job_service import REPARSE_REQUEST_FIELDS
    metrics = (job.metrics or {}) if job is not None else {}
    recorded = {name: metrics[key] for name, key in REPARSE_REQUEST_FIELDS.items() if key in metrics}
    if recorded and not parent._same(recorded, row.original["request"]):
        return False
    return True


def _preparing(session, row, capture):
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution
    boundary = current_nonworkflow_execution()
    if (row is None or row.state != "PENDING" or row.claim_token is not None or row.started
        or not isinstance(boundary, dict) or boundary.get("ingest_successor_id")
        or boundary.get("job_id") != row.predecessor_job_id
        or boundary.get("execution_id") != row.original["predecessor_receipt"]["execution_id"]
        or not parent.fence_original_write(session, boundary.get("ingest_lease"))):
        return False
    original = row.original["execution_input"]
    return (all(parent._same(capture.get(key), original.get(key)) for key in
        ("job_id", "execution_id", "start_orders", "selected_pdf_receipt", "input_reference", "job_order_id", "ingest_lease"))
        and parent._same(capture.get("orders"), boundary.get("orders")))


def advance_preparation(session, boundary):
    row = session.execute(select(IngestDeliverySuccessor).where(
        IngestDeliverySuccessor.predecessor_job_id == boundary["job_id"]).with_for_update()).scalar_one_or_none()
    if row is None:
        return
    job = session.get(OcrJob, row.job_id, with_for_update=True)
    capture = deepcopy((job.metrics or {}).get("nonworkflow_execution"))
    if not isinstance(capture, dict):
        raise ValueError("auto_llm_preparation_missing")
    capture["orders"] = deepcopy(boundary["orders"])
    if not _preparing(session, row, capture):
        raise ValueError("auto_llm_preparation_owner_lost")
    job.metrics = {**job.metrics, "nonworkflow_execution": capture}


def _receipt(session, row, job, *, validate_pipeline=True):
    capture = (job.metrics or {}).get("nonworkflow_execution")
    if not isinstance(capture, dict) or not fence(session, capture, capture.get("ingest_lease")):
        raise ValueError("auto_llm_receipt_owner_lost")
    if job.status in {"done", "failed"}:
        receipt = (job.metrics or {}).get("rerun_terminal_receipt") or (job.metrics or {}).get("non_ocr_terminal_receipt")
        if not isinstance(receipt, dict):
            return None, None
        if (receipt.get("job_id") != row.job_id or receipt.get("execution_id") != capture["execution_id"]
            or receipt.get("status") != job.status or receipt.get("order_id") != capture["order_id"]):
            raise ValueError("auto_llm_terminal_original_mismatch")
        evidence_id = receipt.get("evidence_run_id")
        evidence = session.get(OrderOcrEvidenceRun, evidence_id) if evidence_id else None
        if receipt["status"] == "done" and evidence is None:
            raise ValueError("auto_llm_terminal_evidence_missing")
        if evidence is not None:
            lineage = evidence.payload_json.get("_ocr_execution") if isinstance(evidence.payload_json, dict) else None
            if (evidence.order_id != capture["order_id"] or not isinstance(lineage, dict)
                or lineage.get("job_id") != job.id or lineage.get("evidence_run_id") != evidence.id
                or lineage.get("nonworkflow_execution_id") != capture["execution_id"]):
                raise ValueError("auto_llm_terminal_evidence_foreign")
        return "terminal", {"receipt": deepcopy(receipt), "claim": deepcopy(_owner.get()),
            "evidence_digest": evidence.artifact_digest if evidence is not None else None,
            "evidence_payload": deepcopy(evidence.payload_json) if evidence is not None else None}
    if job.status == "awaiting_output":
        from src.services.order_service import is_own_pending_ocr_result
        waiting = (job.metrics or {}).get("pending_receipt")
        if not is_own_pending_ocr_result({"outcome": "pending", "status": job.status,
            "output_reference": job.output_reference, "pending_receipt": waiting},
            order_id=capture["order_id"], job_id=job.id, execution_id=capture["execution_id"]) or not parent._same(
                (waiting or {}).get("request_kwargs"), row.original["request"]):
            raise ValueError("auto_llm_pending_receipt_missing")
    kind, receipt = parent._receipt(session, row, job, validate_pipeline=validate_pipeline)
    if kind == "handoff":
        receipt["claim"] = deepcopy(_owner.get())
    return kind, receipt


def validate_request(session, boundary, request):
    if not fence(session, boundary, boundary.get("ingest_lease")):
        return False
    row = session.get(IngestDeliverySuccessor, boundary["ingest_successor_id"])
    return parent._same(request, row.original["request"])


def validate_resume(session, boundary, payload, *, output_reference=None):
    if not fence(session, boundary, boundary.get("ingest_lease")):
        return False
    row = session.get(IngestDeliverySuccessor, boundary["ingest_successor_id"])
    source = payload.get("_ocr_execution") if isinstance(payload, dict) else None
    if parent._same(payload, row.original["payload"]):
        return (isinstance(source, dict) and source.get("job_id") == row.predecessor_job_id
            and source.get("evidence_run_id") == row.original["evidence_id"]
            and source.get("nonworkflow_execution_id") == row.original["predecessor_receipt"]["execution_id"]
            and source.get("workflow_attempt_id") is None and source.get("workflow_revision") is None)
    # Later reconciliation uses this successor's exact recorded downstream output,
    # not a substitute first-pass source or a latest evidence lookup.
    job = session.get(OcrJob, row.job_id)
    if not output_reference or job is None or job.output_reference != output_reference:
        return False
    from src.services.ocr_job_service import own_pipeline_delivery_matches, validate_own_pipeline_result
    pipeline = (job.metrics or {}).get("pipeline_delivery_receipt")
    if not isinstance(pipeline, dict) or not own_pipeline_delivery_matches(job, pipeline.get("input_reference"), output_reference):
        return False
    validate_own_pipeline_result(payload, output_reference, write_session=session)
    return True


@event.listens_for(Session, "before_flush")
def bind_receipt(session, flush_context, instances):
    for job in list(session.dirty):
        identity = (job.metrics or {}).get("ingest_successor_id") if isinstance(job, OcrJob) else None
        if not identity:
            continue
        row = session.get(IngestDeliverySuccessor, identity, with_for_update=True)
        if _owner.get() is None and job.status == "pending" and _preparing(
            session, row, (job.metrics or {}).get("nonworkflow_execution") or {}):
            continue
        kind, receipt = _receipt(session, row, job, validate_pipeline=False)
        if kind == "terminal":
            row.terminal_receipt = receipt
        elif kind == "handoff":
            row.handoff_receipt = receipt


def _ack(session, row):
    job = session.get(OcrJob, row.job_id, populate_existing=True, with_for_update=True)
    kind, receipt = _receipt(session, row, job)
    if kind == "terminal" and parent._same(receipt, row.terminal_receipt):
        result = "business_failed" if receipt["receipt"]["status"] == "failed" else "business_done"
    elif kind == "handoff" and parent._same(receipt, row.handoff_receipt):
        result = "durable_handoff"
    else:
        raise ValueError("auto_llm_owned_receipt_missing")
    row.state = "ACKNOWLEDGED"
    return result


def inspect(identity, generation):
    if type(generation) is not int or generation < 1:
        raise ValueError("auto_llm_generation_required")
    with parent.transaction() as session:
        row = _locked(session, identity)
        if row is None or generation > row.generation:
            raise ValueError("auto_llm_generation_unknown")
        if row.state == "REFUSED":
            _validate_refusal(session, row)
        return {"duplicate": generation < row.generation or row.state in {"ACKNOWLEDGED", "REFUSED"},
                "mutex": "ingest-original:" + row.owner_lease["id"]}


def process(identity, generation):
    observed = inspect(identity, generation)
    if observed["duplicate"]:
        return {"acknowledged": True, "duplicate": True}
    with _exclusive(observed["mutex"]) as acquired:
        if not acquired:
            raise ValueError("auto_llm_execution_busy")
        refusal = refuse_if_known(identity, generation)
        if refusal is not None:
            return {"acknowledged": True, "result": refusal}
        with parent.transaction() as session:
            row = _locked(session, identity)
            if row.generation != generation or row.state == "ACKNOWLEDGED":
                return {"acknowledged": True, "duplicate": True}
            predecessor = _predecessor(session, row)
            if predecessor.state != "ACKNOWLEDGED":
                raise ValueError("auto_llm_predecessor_ack_pending")
            job = session.get(OcrJob, row.job_id, with_for_update=True)
            if job is None or (job.status in {"done", "failed"} and row.terminal_receipt is None):
                raise ValueError("auto_llm_terminal_receipt_missing")
            if row.terminal_receipt is None and row.handoff_receipt is None:
                row.claim_token = uuid4().hex
            row.state = "CLAIMED"
            claim = {"id": identity, "generation": generation, "token": row.claim_token}
            original = deepcopy(row.original)
            if row.terminal_receipt is None and row.handoff_receipt is None:
                prepared = deepcopy((job.metrics or {}).get("nonworkflow_execution"))
                predecessor_job = session.get(OcrJob, row.predecessor_job_id)
                if not isinstance(prepared, dict) or (not row.started and not parent._same(prepared.get("orders"),
                    (predecessor_job.metrics or {}).get("nonworkflow_execution", {}).get("orders"))):
                    raise ValueError("auto_llm_prepared_version_mismatch")
                original["execution_input"] = prepared
                row.started = True
            pipeline = deepcopy((job.metrics or {}).get("pipeline_delivery_receipt"))
            execute = row.terminal_receipt is None and row.handoff_receipt is None
        from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
        with owned(claim), nonworkflow_execution_context(original["execution_input"]):
            if execute:
                if isinstance(pipeline, dict):
                    from src.services.ocr_job_service import own_pipeline_delivery_for_output
                    from src.services.order_service import _mark_reparse_job_awaiting_output
                    saved = own_pipeline_delivery_for_output(pipeline.get("output_reference"))
                    if not parent._same(saved, pipeline):
                        raise ValueError("auto_llm_saved_pipeline_changed")
                    waiting = _mark_reparse_job_awaiting_output(original["execution_input"]["job_id"],
                        input_reference=pipeline["input_reference"], output_reference=pipeline["output_reference"],
                        recovery_delay_seconds=original["recovery_delay_seconds"], metrics_patch={"request_mode": "llm_reparse"})
                    if waiting is None:
                        raise ValueError("auto_llm_pending_persister_refused")
                else:
                    from src.workers.ingest_worker import _run_auto_llm_reparse
                    _run_auto_llm_reparse(original["execution_input"]["order_id"], job_id=original["execution_input"]["job_id"],
                        execution_input=original["execution_input"], own_payload=original["payload"],
                        provider=original["request"]["ocr_provider"], original_request=original["request"])
            with parent.transaction() as session:
                result = _ack(session, _locked(session, identity))
        return {"acknowledged": True, "result": result}


def dispatch(identity, generation):
    observed = inspect(identity, generation)
    if observed["duplicate"]:
        return {"action": "acknowledged"}
    from src.services import agent_trial_transport as local
    if local.enabled():
        with parent.transaction() as session:
            row = _locked(session, identity)
            if row.generation != generation or row.state == "ACKNOWLEDGED":
                return {"action": "noop"}
            predecessor = _predecessor(session, row)
            if predecessor.state != "ACKNOWLEDGED":
                return {"action": "predecessor_pending"}
            contract = deepcopy(row.task_contract)
            local.validate(contract, identity, generation, "auto-llm")
            if row.state == "PENDING":
                row.state = "SENT"
        return local.submit(contract, identity, generation, "auto-llm", lambda: process(identity, generation))
    with _exclusive(observed["mutex"]) as acquired:
        if not acquired:
            return {"action": "active_owner"}
        refusal = refuse_if_known(identity, generation)
        if refusal is not None:
            return {"action": refusal}
        with parent.transaction() as session:
            row = _locked(session, identity)
            if row.generation != generation or row.state == "ACKNOWLEDGED":
                return {"action": "noop"}
            predecessor = _predecessor(session, row)
            if predecessor.state != "ACKNOWLEDGED":
                return {"action": "predecessor_pending"}
            if row.terminal_receipt is not None or row.handoff_receipt is not None:
                claim = {"id": identity, "generation": generation, "token": row.claim_token}
                from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
                with owned(claim), nonworkflow_execution_context(deepcopy(row.original["execution_input"])):
                    return {"action": _ack(session, row)}
            job = session.get(OcrJob, row.job_id, with_for_update=True)
            if job is None or job.status in {"done", "failed"}:
                raise ValueError("auto_llm_terminal_receipt_missing")
            row.visit_count += 1
            row.updated_at = datetime.utcnow()
            contract, state = deepcopy(row.task_contract), row.state
        from src import staging_data as datasets
        if datasets.current_dataset() != "test":
            from urllib.parse import urlsplit
            http = contract.get("http_request", {})
            if (http.get("headers", {}).get(datasets.HEADER) != datasets.current_dataset()
                    or "-stg-" not in urlsplit(http.get("url", "")).netloc):
                raise datasets.DatasetUnavailable("staging_dataset_original_delivery_not_replayable")
        from google.cloud import tasks_v2
        from google.api_core.exceptions import AlreadyExists
        import base64
        try:
            client = tasks_v2.CloudTasksClient()
            lookup = transport.inspect_task(client, contract)
            if lookup == "missing" and state == "PENDING":
                task = deepcopy(contract)
                task["http_request"]["http_method"] = tasks_v2.HttpMethod.POST
                task["http_request"]["body"] = base64.b64decode(task["http_request"]["body"])
                try:
                    client.create_task(request={"parent": contract["name"].rsplit("/tasks/", 1)[0], "task": task})
                except AlreadyExists:
                    pass
                lookup = transport.inspect_task(client, contract)
            with parent.transaction() as session:
                row = _locked(session, identity)
                if row.generation != generation:
                    return {"action": "superseded_transport"}
                if lookup == "exists":
                    if row.state == "PENDING":
                        row.state = "SENT"
                    return {"action": "sent"}
                row.generation += 1
                row.task_contract = transport.renew_contract(contract, identity, row.generation)
                row.state, row.claim_token = "PENDING", None
                return {"action": "renewed"}
        except Exception as exc:
            with parent.transaction() as session:
                row = _locked(session, identity)
                if row.generation == generation:
                    row.last_error = str(exc)
            raise


def drain(limit):
    if type(limit) is not int or limit < 1:
        raise ValueError("auto_llm_drain_limit_required")
    with parent.transaction() as session:
        rows = session.execute(select(IngestDeliverySuccessor)
            .join(parent.IngestDelivery, parent.IngestDelivery.id == IngestDeliverySuccessor.parent_delivery_id)
            .join(parent.IngestJob, parent.IngestJob.id == parent.IngestDelivery.job_id)
            .join(OcrJob, OcrJob.id == IngestDeliverySuccessor.job_id)
            .where(IngestDeliverySuccessor.state.not_in(["ACKNOWLEDGED", "REFUSED"]),
                or_(OcrJob.status.not_in(["done", "failed"]),
                    IngestDeliverySuccessor.terminal_receipt.is_not(None), IngestDeliverySuccessor.handoff_receipt.is_not(None)))
            .order_by(IngestDeliverySuccessor.visit_count, IngestDeliverySuccessor.updated_at, IngestDeliverySuccessor.id)
            .limit(limit).with_for_update(of=IngestDeliverySuccessor, skip_locked=True)).scalars().all()
        candidates = [(row.id, row.generation) for row in rows]
        for row in rows:
            row.visit_count += 1
            row.updated_at = datetime.utcnow()
    results = []
    for identity, generation in candidates:
        try:
            results.append(dispatch(identity, generation))
        except Exception as exc:
            results.append({"id": identity, "error": str(exc)})
    return results


@contextmanager
def recovery_context(boundary):
    identity = boundary.get("ingest_successor_id")
    with parent.transaction() as session:
        row = _locked(session, identity)
        if row is None or row.state != "ACKNOWLEDGED" or row.handoff_receipt is None or (row.terminal_receipt or {}).get("kind") == "owned_refusal":
            eligible = False
        else:
            eligible = True
            claim = {"id": identity, "generation": row.generation, "token": row.claim_token}
            mutex = "ingest-original:" + row.owner_lease["id"]
    if not eligible:
        yield False
        return
    with _exclusive(mutex) as acquired:
        if not acquired:
            yield False
            return
        with owned(claim):
            if refuse_if_known(identity, claim["generation"]) is not None:
                yield False
                return
            yield True
