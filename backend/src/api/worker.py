from datetime import datetime, timedelta
import os

from fastapi import APIRouter, Header, HTTPException, Request
from google.oauth2 import id_token

from src.services import workflow_ocr_delivery_service as delivery
from src.services import workflow_ocr_delivery_dispatch_service
from src.services.workflow_ocr_delivery_execution_service import execution_mutex

router = APIRouter()


@router.post("/worker/ingest-deliveries/process")
def process_ingest_delivery(body: dict, authorization: str | None = Header(default=None)):
    from src.services.ingest_delivery_auth_service import require_identity
    from src.services import ingest_delivery_service
    require_identity(authorization)
    if set(body) != {"delivery_id", "generation"} or not isinstance(body["delivery_id"], str) or not body["delivery_id"]:
        raise HTTPException(status_code=422, detail="ingest_delivery_request_invalid")
    try:
        return ingest_delivery_service.process(body["delivery_id"], body["generation"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/worker/ingest-deliveries/drain")
def drain_ingest_deliveries(limit: int, authorization: str | None = Header(default=None)):
    from src.services.ingest_delivery_auth_service import require_identity
    from src.services.ingest_delivery_dispatch_service import drain
    require_identity(authorization, drain=True)
    if limit <= 0:
        raise HTTPException(status_code=422, detail="ingest_delivery_drain_limit_required")
    result = drain(limit)
    from src.services.ingest_delivery_successor_service import drain as drain_successors
    result["auto_llm"] = drain_successors(limit)
    from src.workers.ingest_worker import run_ocr_job_recovery_once
    result["ocr_reconciled"] = run_ocr_job_recovery_once(limit)
    return result


@router.post("/worker/ingest-deliveries/auto-llm")
def process_ingest_auto_llm(body: dict, authorization: str | None = Header(default=None)):
    from src.services.ingest_delivery_auth_service import require_identity
    from src.services.ingest_delivery_successor_service import process
    require_identity(authorization)
    if set(body) != {"delivery_id", "generation"} or not isinstance(body["delivery_id"], str) or not body["delivery_id"]:
        raise HTTPException(status_code=422, detail="auto_llm_request_invalid")
    try:
        return process(body["delivery_id"], body["generation"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _required_worker_identity(request, authorization, *, drain=False):
    prefix = "HOSPITAL_WORKFLOW_OCR_DELIVERY_" + ("DRAIN_" if drain else "WORKER_")
    config = {key: str(os.getenv(prefix + key) or "").strip() for key in ("EMAIL", "SUBJECT", "AUDIENCE")}
    if not all(config.values()) or not config["EMAIL"].endswith(".iam.gserviceaccount.com"):
        raise HTTPException(status_code=503, detail="workflow_ocr_delivery_auth_config_missing")
    from src.services.ingest_delivery_auth_service import verified_delivery_claims
    payload = verified_delivery_claims(authorization, config["AUDIENCE"])
    if (payload.get("email") != config["EMAIL"] or payload.get("sub") != config["SUBJECT"]
            or payload.get("email_verified") is not True or payload.get("aud") != config["AUDIENCE"]):
        raise HTTPException(status_code=403, detail="Forbidden")


def _lease_expiry():
    raw = str(os.getenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_PROCESS_LEASE_SECONDS") or "").strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="workflow_ocr_delivery_process_lease_config_missing") from exc
    if seconds < 1:
        raise HTTPException(status_code=503, detail="workflow_ocr_delivery_process_lease_config_missing")
    return datetime.utcnow() + timedelta(seconds=seconds)


def _execute_claim(claim):
    from src.api.orders import (
        OcrBackgroundExecutionFailure,
        _complete_workflow_ocr_run, _run_ocr_rerun_background, _run_reparse_background,
    )
    from src.services import order_service, ocr_job_service
    params = claim["request_identity"]["request_parameters"]
    common = {
        "order_id": claim["order_id"], "ocr_job_id": claim["job_id"],
        "selected_document_id": claim["request_identity"]["selected_document_id"],
        "expected_revision": claim["workflow_revision"],
        "workflow_attempt_id": claim["workflow_attempt_id"],
    }
    if claim["business_status"] == "awaiting_output":
        # Reconcile the original saved pending receipt, never relaunch its OCR.
        job = ocr_job_service.get_job(claim["job_id"])
        pending = (job.get("metrics") or {}).get("pending_receipt") if isinstance(job, dict) else None
        if not isinstance(pending, dict) or not order_service.reconcile_pending_workflow_reparse(job, pending):
            raise ValueError("workflow_ocr_delivery_pending_reconciliation_not_complete")
        return {"reconciled": True}
    if claim["request_mode"] == "ocr_rerun":
        callback = _run_ocr_rerun_background
    elif claim["request_mode"] in {"ocr_reparse", "llm_reparse"}:
        if type(params["reparse_llm_assist"]) is not bool:
            raise ValueError("workflow_ocr_delivery_llm_assist_required")
        callback = _run_reparse_background
        common.update(
            ocr_prompt=params["reparse_ocr_prompt"], prompt_preset=params["reparse_prompt_preset"],
            ocr_provider=params["reparse_requested_provider"], ocr_model=params["reparse_ocr_model"],
            llm_assist=params["reparse_llm_assist"])
    else:
        raise ValueError("workflow_ocr_delivery_request_mode_unknown")
    try:
        return callback(**common)
    except OcrBackgroundExecutionFailure as exc:
        original = exc.original_exception
        if not delivery.callback_failure_completion_allowed(claim["job_id"]):
            raise original
        receipt, completion_error = _complete_workflow_ocr_run(
            claim["order_id"], claim["job_id"], error=f"{type(original).__name__}: {original}",
            expected_revision=claim["workflow_revision"],
            workflow_attempt_id=claim["workflow_attempt_id"],
        )
        if receipt is None or receipt.get("status") != "failed":
            raise ValueError(completion_error or "ocr_rerun_terminal_persist_refused") from exc
        return {"terminal_receipt": receipt}


@router.post("/worker/workflow-ocr-deliveries/{delivery_id}/{generation}")
def process_workflow_ocr_delivery(delivery_id: str, generation: int, request: Request,
                                 authorization: str | None = Header(default=None)):
    _required_worker_identity(request, authorization)
    return execute_workflow_ocr_delivery(delivery_id, generation)


def execute_workflow_ocr_delivery(delivery_id: str, generation: int):
    """Shared business executor; public worker authentication stays at the route."""
    try:
        # Validate identity/generation before an active duplicate can receive ACK.
        observed = delivery.inspect_generation(delivery_id, generation)
        if observed["duplicate"]:
            return {"acknowledged": True, "duplicate": True}
        with execution_mutex(delivery_id) as acquired:
            if not acquired:
                return {"acknowledged": True, "duplicate": True, "active_owner": True}
            claim = delivery.claim_delivery(delivery_id, generation, lease_expires_at=_lease_expiry())
            if claim.get("duplicate"):
                return {"acknowledged": True, "duplicate": True}
            with delivery.claim_context(claim):
                _execute_claim(claim)
            if not delivery.acknowledge_claim(delivery_id, generation, claim["claim_token"]):
                raise ValueError("workflow_ocr_delivery_ack_rejected")
            return {"acknowledged": True, "duplicate": False}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/worker/workflow-ocr-deliveries/drain")
def drain_workflow_ocr_deliveries(request: Request, limit: int,
                                authorization: str | None = Header(default=None)):
    _required_worker_identity(request, authorization, drain=True)
    try:
        return workflow_ocr_delivery_dispatch_service.drain_pending(limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
