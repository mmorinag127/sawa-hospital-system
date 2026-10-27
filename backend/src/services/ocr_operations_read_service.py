"""Current SQL OCR ownership, separate from immutable historical observations."""
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import load_only

from src import db
from src.models.document import OrderDocument
from src.models.ingest_delivery import IngestDelivery
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_version import OrderVersion
from src.models.uploaded_pdf import UploadedPdf
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import ingest_delivery_service as ingest_delivery
from src.services import ocr_job_service
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_ocr_delivery_service as workflow_delivery
from src.services import workflow_attempt_guard_service as execution_guard
from src.services.ocr_terminal_evidence_read import read_terminal_evidence


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else None


def _source(session, order, job, receipt, *, native, selected=None):
    if (not isinstance(receipt, dict) or any(not isinstance(receipt.get(key), str) or not receipt[key]
            for key in ("uploaded_pdf_id", "document_id", "order_id", "message_id", "storage_uri", "sha256"))):
        raise ValueError("ocr_selected_pdf_receipt_required")
    document_id = order.current_document_id
    if selected is not None:
        if (native or not isinstance(selected, dict) or not isinstance(selected.get("id"), str)
                or selected.get("document_id") != receipt["document_id"]
                or selected.get("storage_uri") != receipt["storage_uri"]
                or not execution_guard.selected_version_input_matches(
                    session.get(OrderVersion, selected["id"]), order.id, selected)):
            raise ValueError("ocr_selected_version_source_changed")
        document_id = selected["document_id"]
    document = session.get(OrderDocument, document_id) if document_id else None
    upload = session.get(UploadedPdf, receipt.get("uploaded_pdf_id"))
    if (document is None or upload is None
            or receipt.get("order_id") != order.id
            or receipt.get("document_id") != document.id
            or receipt.get("message_id") != document.source_email_id
            or upload.message_id != document.source_email_id
            or (selected is None and order.document_uri != document.storage_uri)
            or receipt.get("storage_uri") != document.storage_uri
            or job.input_reference != document.storage_uri or upload.storage_uri != document.storage_uri
            or not receipt.get("sha256")
            or upload.content_sha256 != receipt["sha256"]
            or upload.current_order_id != order.id or upload.current_document_id != document.id):
        raise ValueError("ocr_current_document_source_changed")
    # Workflow reservation stores its authoritative source in the exact delivery
    # receipt, not these optional job columns. Native registration writes both.
    for recorded, expected in ((job.order_document_id, document.id), (job.uploaded_pdf_id, upload.id),
                               (job.input_artifact_digest, receipt["sha256"])):
        if (native or recorded is not None) and recorded != expected:
            raise ValueError("ocr_job_source_columns_changed")


def _owner(session, order, state, job, meta, native_deliveries, evidence_reads):
    if job.order_id != order.id or meta.get("ocr_job_id") != job.id:
        raise ValueError("ocr_workflow_job_owner_changed")
    if not isinstance(job.metrics, dict):
        raise ValueError("ocr_job_metrics_required")
    metrics = job.metrics
    capture, attempt = metrics.get("nonworkflow_execution"), metrics.get("workflow_attempt_id")
    expected = meta.get("ocr_job_attempt_id")
    if type(expected) is not type(attempt) or expected != attempt:
        raise ValueError("ocr_workflow_attempt_changed")
    if capture is not None:
        if (not isinstance(capture, dict) or attempt is not None
                or capture.get("ingest_ocr_mode") != "hakodate"
                or metrics.get("ingest_ocr_mode") != "hakodate"
                or capture.get("intake_protocol") != "native_ingest_v2"
                or capture.get("job_id") != job.id or capture.get("order_id") != order.id
                or capture.get("job_order_id") != order.id
                or capture.get("input_reference") != job.input_reference
                or capture.get("selected_version_input") is not None
                or not isinstance(capture.get("execution_id"), str) or not capture["execution_id"]):
            raise ValueError("ocr_native_execution_changed")
        lease = capture.get("ingest_lease")
        if (not isinstance(lease, dict) or lease != metrics.get("ingest_lease")
                or any(not isinstance(lease.get(key), str) or not lease[key]
                       for key in ("id", "generation", "claim_id", "input_digest"))):
            raise ValueError("ocr_native_lease_changed")
        rows = native_deliveries.get((lease["id"], lease["generation"]), [])
        if len(rows) != 1:
            raise ValueError("ocr_native_generation_required")
        delivery = rows[0]
        original = ingest_delivery._validate_original_job(delivery, session.get(IngestJob, delivery.job_id))
        if original is not None and delivery.uploaded_identity is not None:
            ingest_delivery._validate_original_upload(
                delivery, session.get(UploadedPdf, delivery.uploaded_identity["id"]),
            )
        if (original is None or delivery.owner_lease != lease
                or original.claim_id != lease.get("claim_id") or original.attempts != lease.get("attempts")
                or original.input_digest != lease.get("input_digest") or original.payload != lease.get("payload")
                or delivery.state not in {"CLAIMED", "ACKNOWLEDGED"}):
            raise ValueError("ocr_native_generation_changed")
        receipt = capture.get("selected_pdf_receipt")
        owned = capture.get("orders")
        start = capture.get("start_orders")
        if (not isinstance(owned, dict) or set(owned) != {order.id}
                or not isinstance(owned[order.id], dict)
                or not isinstance(start, dict) or not isinstance(start.get(order.id), dict)
                or not isinstance(start[order.id].get("processing_start_input"), dict)
                or not start[order.id].get("input_sha256")
                or owned[order.id].get("document", {}).get("id") != order.current_document_id):
            raise ValueError("ocr_native_captured_input_required")
        _source(session, order, job, receipt, native=True)
        with execution_guard.nonworkflow_execution_context(capture):
            execution_guard.processing_start_read_input()
        if job.status in {"done", "failed"}:
            kind, terminal = ingest_delivery._receipt(session, delivery, job, validate_pipeline=False, _read_evidence=evidence_reads)
            if kind != "terminal" or terminal != delivery.terminal_receipt:
                raise ValueError("ocr_native_terminal_changed")
            if original.status != ("done" if job.status == "done" else "error"):
                raise ValueError("ocr_native_terminal_original_changed")
            upload = session.get(UploadedPdf, delivery.uploaded_identity["id"]) if delivery.uploaded_identity is not None else None
            ingest_delivery._validate_uploaded_terminal(delivery, upload,
                "completed" if job.status == "done" else "failed")
        elif original.status != "processing" or delivery.state != "CLAIMED":
            raise ValueError("ocr_native_active_owner_changed")
        return "native_ingest_v2", capture["execution_id"], lease["generation"], receipt
    if (not isinstance(attempt, str) or not attempt or metrics.get("ingest_ocr_mode") is not None
            or metrics.get("ingest_lease") is not None):
        raise ValueError("ocr_execution_owner_required")
    delivery_id = metrics.get("workflow_ocr_delivery_id")
    if not isinstance(delivery_id, str) or not delivery_id:
        raise ValueError("ocr_workflow_delivery_required")
    delivery = session.get(WorkflowOcrDelivery, delivery_id)
    if delivery is None:
        raise ValueError("ocr_workflow_delivery_required")
    workflow_delivery._validate_identity(delivery)
    request = delivery.request_identity
    if (delivery.job_id != job.id or delivery.order_id != order.id
            or delivery.workflow_attempt_id != attempt
            or metrics.get("selected_pdf_receipt") != delivery.selected_pdf_receipt
            or any(metrics.get(key) != request[key] for key in (
                "reparse_read_input", "selected_version_input", "first_pass_source"))):
        raise ValueError("ocr_workflow_delivery_changed")
    _source(session, order, job, delivery.selected_pdf_receipt, native=False,
            selected=request["selected_version_input"])
    # Active reservations and completed result revisions are different contracts;
    # the shared guard checks the former CAS or the latter exact terminal receipt.
    business = workflow_delivery._business_status_from_rows(delivery, job, state)
    if business not in {"terminal", "eligible", "handoff", "awaiting_output"}:
        raise ValueError("ocr_workflow_" + business)
    return "workflow", attempt, delivery.delivery_generation, delivery.selected_pdf_receipt


def _entry(session, order, state, job, meta, native_deliveries, evidence_reads):
    result = {"order_id": order.id, "document_id": order.current_document_id,
              "job_id": meta.get("ocr_job_id") if isinstance(meta.get("ocr_job_id"), str) else None,
              "workflow_revision": state.revision,
              "workflow_state": state.state, "status": "blocked", "blockers": [],
              "kind": None, "execution_id": None, "generation": None,
              "source_status": job.status if job else None,
              "created_at": _iso(job.created_at) if job else None,
              "updated_at": _iso(job.updated_at) if job else _iso(state.last_transition_at)}
    try:
        if meta.get("ocr_job_id") is not None and not isinstance(meta["ocr_job_id"], str):
            raise ValueError("ocr_recorded_job_reference_invalid")
        if job is None:
            raise ValueError("ocr_recorded_job_missing")
        terminal = job.metrics.get("rerun_terminal_receipt") if isinstance(job.metrics, dict) else None
        if (isinstance(terminal, dict) and terminal.get("evidence_run_id") is not None
                and not isinstance(terminal["evidence_run_id"], str)):
            raise ValueError("ocr_terminal_evidence_identity_invalid")
        kind, execution, generation, pdf = _owner(session, order, state, job, meta, native_deliveries, evidence_reads)
        record = ocr_job_service._job_to_dict(job)
        described = ocr_job_service.describe_job_state(record)
        common = workflow._recorded_workflow_job_status(state, meta, job, _described_state=described)
        if common is None or common.get("attemptIdentity") != execution:
            raise ValueError("ocr_recorded_status_owner_changed")
        result.update(kind=kind, execution_id=execution, generation=generation,
                      document_id=pdf["document_id"],
                      common_job_status=common, job=workflow._serialize_ocr_job(job))
        result["status"] = {"hard_failed": "failed", "stalled": "stalled",
                            "done": "succeeded"}.get(described["status"], described["status"])
        if described.get("blockers"):
            result["blockers"] = described["blockers"]
            result["status"] = "blocked"
        terminal = job.metrics.get("rerun_terminal_receipt")
        if job.status in {"done", "failed"} and isinstance(terminal, dict):
            evidence_id = terminal.get("evidence_run_id")
            evidence = evidence_reads.get(evidence_id) if evidence_id else None
            if evidence_id:
                lineage = evidence.execution if evidence is not None else None
                if (evidence is None or evidence.order_id != order.id or not isinstance(lineage, dict)
                        or lineage.get("job_id") != job.id or lineage.get("evidence_run_id") != evidence_id
                        or lineage.get("selected_pdf_receipt") != pdf
                        or lineage.get("workflow_attempt_id") != job.metrics.get("workflow_attempt_id")
                        or (kind == "native_ingest_v2" and lineage.get("nonworkflow_execution_id") != execution)):
                    raise ValueError("ocr_terminal_evidence_owner_changed")
                review = evidence.recognition_review
                if evidence.status == "diagnostic_only" or isinstance(review, dict) and review.get("count", 0):
                    result["status"] = "review_required"
            elif job.status == "done":
                raise ValueError("ocr_terminal_evidence_required")
        if job.metrics.get("result_state") == "skipped":
            result["status"] = "not_recognized"
        if result["status"] not in {"pending", "queued", "running", "awaiting_output", "recovering",
                                     "failed", "stalled", "succeeded", "review_required", "blocked", "not_recognized"}:
            raise ValueError("ocr_job_state_unknown")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        result["status"] = "blocked"
        result["blockers"] = [str(exc) if isinstance(exc, ValueError) else "ocr_owner_proof_invalid"]
    return result


def read_operations(session):
    """No writes, network lookups, latest-job selection or transport renewal."""
    states = {row.order_id: row for row in session.scalars(select(OrderWorkflowState))}
    references = {meta["ocr_job_id"] for state in states.values()
                  if isinstance((meta := workflow._workflow_meta(state, copy_meta=False)).get("ocr_job_id"), str)}
    # History needs scalar columns, never the potentially large captured metrics.
    jobs = {job.id: job for job in session.scalars(select(OcrJob).options(load_only(
        OcrJob.id, OcrJob.order_id, OcrJob.order_document_id, OcrJob.status,
        OcrJob.created_at, OcrJob.updated_at, OcrJob.error_message)).order_by(OcrJob.id))}
    for chunk in _chunks(references):
        for job in session.scalars(select(OcrJob).where(OcrJob.id.in_(chunk))):
            jobs[job.id] = job
    orders_with_jobs = {job.order_id for job in jobs.values()}
    order_ids = orders_with_jobs | {state.order_id for state in states.values()
        if workflow._workflow_meta(state, copy_meta=False).get("ocr_job_id") is not None
        or state.state in {"ocr_running", "ocr_failed"}}
    orders = []
    for chunk in _chunks(order_ids - {None}):
        orders.extend(session.scalars(select(Order).where(Order.id.in_(chunk))))
    orders.sort(key=lambda row: row.id)
    current = [jobs[key] for key in references if key in jobs]
    native_ids, document_ids, upload_ids, evidence_ids, delivery_ids, version_ids = set(), set(), set(), set(), set(), set()
    for job in current:
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        capture = metrics.get("nonworkflow_execution")
        lease = capture.get("ingest_lease") if isinstance(capture, dict) else None
        if isinstance(lease, dict) and isinstance(lease.get("id"), str):
            native_ids.add(lease["id"])
        receipt = capture.get("selected_pdf_receipt") if isinstance(capture, dict) else metrics.get("selected_pdf_receipt")
        if isinstance(receipt, dict):
            for key, target in (("document_id", document_ids), ("uploaded_pdf_id", upload_ids)):
                if isinstance(receipt.get(key), str):
                    target.add(receipt[key])
        terminal = metrics.get("rerun_terminal_receipt")
        if isinstance(terminal, dict) and isinstance(terminal.get("evidence_run_id"), str):
            evidence_ids.add(terminal["evidence_run_id"])
        if isinstance(metrics.get("workflow_ocr_delivery_id"), str):
            delivery_ids.add(metrics["workflow_ocr_delivery_id"])
        selected = metrics.get("selected_version_input")
        if isinstance(selected, dict) and isinstance(selected.get("id"), str):
            version_ids.add(selected["id"])
    document_ids.update(order.current_document_id for order in orders if order.current_document_id)
    # Strong references keep SQLAlchemy's weak identity map populated. Guarded
    # populate_existing reads still execute; no fence or refresh is intercepted.
    canonical_rows = []
    for model, identities in ((OrderDocument, document_ids), (UploadedPdf, upload_ids),
                              (WorkflowOcrDelivery, delivery_ids), (OrderVersion, version_ids)):
        for chunk in _chunks(identities):
            canonical_rows.extend(session.scalars(select(model).where(model.id.in_(chunk))))
    evidence_reads = read_terminal_evidence(session, evidence_ids)
    native_deliveries = {}
    for chunk in _chunks(native_ids):
        for row in session.scalars(select(IngestDelivery).where(IngestDelivery.job_id.in_(chunk))):
            native_deliveries.setdefault((row.job_id, row.business_generation), []).append(row)
    for chunk in _chunks(native_ids):
        canonical_rows.extend(session.scalars(select(IngestJob).where(IngestJob.id.in_(chunk))))
    original_upload_ids = {row.uploaded_identity["id"] for rows in native_deliveries.values() for row in rows
                          if isinstance(row.uploaded_identity, dict) and isinstance(row.uploaded_identity.get("id"), str)}
    for chunk in _chunks(original_upload_ids - upload_ids):
        canonical_rows.extend(session.scalars(select(UploadedPdf).where(UploadedPdf.id.in_(chunk))))
    items, referenced = [], set()
    for order in orders:
        state = states.get(order.id)
        meta = workflow._workflow_meta(state, copy_meta=False) if state is not None else {}
        job_id = meta.get("ocr_job_id")
        if job_id is not None:
            if isinstance(job_id, str):
                referenced.add(job_id)
            items.append(_entry(session, order, state, jobs.get(job_id) if isinstance(job_id, str) else None, meta, native_deliveries, evidence_reads))
        elif state is not None and (state.state in {"ocr_running", "ocr_failed"}
                or order.id in orders_with_jobs):
            items.append(_entry(session, order, state, None, meta, native_deliveries, evidence_reads))
    # Non-current jobs remain dated history. An unowned active job is additionally
    # visible as a blocker, not silently counted as idle or claimed by an order.
    history = []
    for job in jobs.values():
        if job.id in referenced:
            continue
        historical = {"job_id": job.id, "order_id": job.order_id, "document_id": job.order_document_id,
                      "source_status": job.status, "created_at": _iso(job.created_at),
                      "updated_at": _iso(job.updated_at), "error": job.error_message}
        history.append(historical)
        if job.status not in {"done", "failed", "error", "cancelled"}:
            items.append({**historical, "status": "blocked", "kind": None, "execution_id": None,
                          "generation": None, "blockers": ["ocr_active_job_not_referenced_by_workflow"]})
    counts = dict(Counter(item["status"] for item in items))
    status = "idle"
    for candidate in ("blocked", "review_required", "failed", "stalled", "running", "recovering",
                      "awaiting_output", "queued", "pending", "not_recognized", "succeeded"):
        if counts.get(candidate):
            status = candidate
            break
    return {"source": "recorded_sql_workflow/v1", "observed_at": datetime.now(timezone.utc).isoformat(),
            "summary": {"status": status, "counts": counts, "total": len(items)},
            "items": items, "history": history}


def get_operations():
    with db.session_scope() as session:
        return read_operations(session)


def _chunks(values):
    values = sorted(values)
    for offset in range(0, len(values), 400):
        yield values[offset:offset + 400]
