"""Operator recovery of the current failed recognition, with input ownership."""
from copy import deepcopy
import base64
import json
from math import ceil
import re

import cv2

from src.services import hakodate_cell_ocr_batch_service as batch
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from src.services import order_workflow_v2_service as workflow
from src.services import hakodate_step_review_pipeline_service as geometry


DIAGNOSTIC_SUBSET = "diagnostic_subset/v1"
FULL_MANUAL = "full_manual/v1"


def _workflow_terminal_owner(session, order, job, receipt):
    from src.services import workflow_ocr_delivery_service as delivery

    metrics = job.metrics or {}
    row = delivery._locked(session, metrics.get("workflow_ocr_delivery_id"), read_only=True)
    if row is None:
        raise ValueError("recognition_workflow_delivery_required")
    delivery._validate_identity(row)
    terminal = metrics.get("rerun_terminal_receipt")
    if (row.order_id != order.id or row.job_id != job.id
            or row.workflow_attempt_id != metrics.get("workflow_attempt_id")
            or row.selected_pdf_receipt != receipt
            or receipt.get("document_id") != order.current_document_id
            or metrics.get("selected_pdf_receipt") != receipt
            or row.request_identity["reparse_read_input"] != metrics.get("reparse_read_input")
            or not isinstance(terminal, dict) or terminal != row.result_receipt
            or row.state not in {delivery.CLAIMED, delivery.ACKNOWLEDGED}
            or terminal.get("job_id") != job.id or terminal.get("order_id") != order.id
            or terminal.get("workflow_attempt_id") != row.workflow_attempt_id
            or terminal.get("workflow_revision") != row.workflow_revision
            or terminal.get("status") != job.status
            or terminal.get("evidence_run_id") != metrics.get("evidence_run_id")
            or terminal.get("workflow_delivery_owner") != {"delivery_id": row.id,
                "generation": row.delivery_generation, "claim_token": row.claim_token}):
        raise ValueError("recognition_workflow_terminal_changed")
    return row


def diagnostic_records(payload, *, checkpoint, item, loaded, captured):
    """Only immutable canonical artifacts can supply the unedited recognition cells."""
    from src.services import canonical_ocr_artifact_service as artifacts, order_service
    from src.services.hakodate_recognizer_contact_sheet import build_recognizer_contact_sheet
    from io import BytesIO
    from PIL import Image

    order_service._validate_hakodate_original_execution(payload, allow_review=True)
    verified = artifacts.validate_payload(payload)
    source = payload["hakodate_repair_source_receipt"]
    current_source = order_service._hakodate_owned_source(item["configured_input"], item["selected_pdf_receipt"], original_input=captured)
    if (any(source.get(key) != current_source[key] for key in (
            "selected_pdf_receipt", "configured_input_digest", "physical_menu_digest",
            "expected_sheet_cells", "expected_target_semantics"))
            or source["expected_sheet_cells"] != loaded["expected_sheet_cells"]):
        raise ValueError("recognition_diagnostic_source_changed")
    records = json.loads(verified["records"])
    cells = [record.get("sheet_cell") for record in records]
    if cells != loaded["expected_sheet_cells"] or len(cells) != len(set(cells)):
        raise ValueError("recognition_diagnostic_cells_changed")
    if geometry.canonical_target_semantics(records) != geometry.canonical_target_semantics(loaded["regions"]):
        raise ValueError("recognition_diagnostic_cells_changed")
    if any(record.get("bbox") != region.get("bbox") for record, region in zip(records, loaded["regions"])):
        raise ValueError("recognition_diagnostic_geometry_changed")
    contact, _, _, skipped = build_recognizer_contact_sheet(
        rectified_fax_bgr=loaded["raw_rectified"], regions=loaded["regions"], line_mask=None, mode="corner_cc")
    with Image.open(BytesIO(verified["contact_sheet"])) as stored:
        if skipped or contact.size != stored.size or contact.tobytes() != stored.convert("RGB").tobytes():
            raise ValueError("recognition_diagnostic_contact_changed")
    return records


def _diagnostic_source(session, order, job, evidence_id, *, checkpoint, item, loaded, captured):
    evidence = session.get(workflow.OrderOcrEvidenceRun, evidence_id)
    if evidence is None or evidence.order_id != order.id:
        raise ValueError("recognition_review_result_changed")
    payload = evidence.payload_json
    execution = payload.get("_ocr_execution") if isinstance(payload, dict) else None
    if (not isinstance(execution, dict) or execution.get("job_id") != job.id
            or execution.get("evidence_run_id") != evidence_id):
        raise ValueError("recognition_review_result_changed")
    terminal = (job.metrics or {}).get("rerun_terminal_receipt")
    if (not isinstance(terminal, dict) or terminal.get("job_id") != job.id
            or terminal.get("order_id") != order.id or terminal.get("evidence_run_id") != evidence_id
            or terminal.get("status") != job.status
            or execution.get("selected_pdf_receipt") != item["selected_pdf_receipt"]):
        raise ValueError("recognition_diagnostic_terminal_changed")
    if _is_native(job):
        capture = (job.metrics or {}).get("nonworkflow_execution") or {}
        if (execution.get("nonworkflow_execution_id") != capture.get("execution_id")
                or execution.get("workflow_attempt_id") is not None
                or job.order_document_id != order.current_document_id
                or job.input_artifact_digest != item["selected_pdf_receipt"]["sha256"]):
            raise ValueError("recognition_native_owner_changed")
    else:
        owner = _workflow_terminal_owner(session, order, job, item["selected_pdf_receipt"])
        if (execution.get("workflow_attempt_id") != owner.workflow_attempt_id
                or execution.get("workflow_revision") != owner.workflow_revision
                or execution.get("processing_start_input") != owner.request_identity["reparse_read_input"]):
            raise ValueError("recognition_review_result_changed")
    records = diagnostic_records(payload, checkpoint=checkpoint, item=item, loaded=loaded, captured=captured)
    review = payload["hakodate_recognition_review"]
    cells = [row["sheet_cell"] for row in review["cells"]]
    if not cells or len(cells) != len(set(cells)):
        raise ValueError("recognition_review_result_changed")
    proof = {"recognition_mode": DIAGNOSTIC_SUBSET,
             "recognition_payload_digest": workflow._stable_json_hash(payload),
             "recognition_records_receipt": deepcopy(payload["hakodate_canonical_pipeline"]["artifact_packet"]["outputs"]["records"]),
             "recognition_review_cells": cells}
    return records, proof


def resume_diagnostic_source(override, *, item, captured):
    """Revalidate the explicitly captured original result, never the newest result."""
    from src.services import ingest_delivery_service as delivery
    from sqlalchemy import select
    from src.models.ingest_delivery import IngestDelivery

    receipt = override["source_receipt"]
    with workflow.session_scope() as session:
        order = session.get(workflow.Order, item["order_id"])
        job = session.get(workflow.OcrJob, receipt.get("recognition_job_id"))
        if order is None or job is None or job.order_id != order.id or job.status not in {"done", "failed"}:
            raise ValueError("recognition_review_result_changed")
        if (job.metrics or {}).get("recognition_checkpoint") != override["checkpoint"]:
            raise ValueError("recognition_checkpoint_changed")
        if _is_native(job):
            capture = (job.metrics or {}).get("nonworkflow_execution") or {}
            lease = capture.get("ingest_lease") or {}
            row = session.execute(select(IngestDelivery).where(
                IngestDelivery.job_id == lease.get("id"),
                IngestDelivery.business_generation == lease.get("generation"))).scalar_one_or_none()
            if row is None:
                raise ValueError("recognition_native_generation_changed")
            kind, terminal = delivery._receipt(session, row, job)
            if (kind != "terminal" or terminal != row.terminal_receipt
                    or receipt.get("native_recognition_owner") != workflow._stable_json_hash(terminal)):
                raise ValueError("recognition_native_terminal_changed")
            original = delivery._original(session, row, read_only=True)
            if (original is None or original.generation != lease.get("generation")
                    or original.claim_id != lease.get("claim_id") or original.input_digest != lease.get("input_digest")
                    or original.payload != lease.get("payload") or original.attempts != lease.get("attempts")
                    or original.status != ("done" if job.status == "done" else "error")):
                raise ValueError("recognition_native_generation_changed")
        loaded = checkpoints.load_recognizer_checkpoint(checkpoint=override["checkpoint"], item=item)
        records, proof = _diagnostic_source(session, order, job, receipt.get("recognition_evidence_run_id"),
            checkpoint=override["checkpoint"], item=item, loaded=loaded, captured=captured)
        if any(receipt.get(key) != value for key, value in proof.items()):
            raise ValueError("recognition_diagnostic_source_changed")
        evidence = session.get(workflow.OrderOcrEvidenceRun, receipt["recognition_evidence_run_id"])
        return records, deepcopy(evidence.payload_json["hakodate_ocr_evidence_records"])


def current_item(checkpoint, captured, receipt, order_id):
    stored = checkpoint.get("resume_item")
    if not isinstance(stored, dict):
        raise ValueError("recognition_checkpoint_resume_context_required")
    item = deepcopy(stored)
    order = captured["order"]
    week = order["week_code"]
    config = captured["configured_input"]
    entries = captured["physical_menu_entries"]
    meta = ((captured.get("workflow") or {}).get("secondary_actions_json") or {}).get("workflow_v2") or {}
    plan = batch.capture_original_target_plan(configured_input=config, physical_menu_entries=entries, week_code=week)
    item.update({
        "order_id": order_id, "facility_code": order["facility_code"], "week_code": week,
        "configured_input": deepcopy(config), "selected_pdf_receipt": deepcopy(receipt["selected_pdf_receipt"]),
        "original_physical_draft": batch.original_physical_draft(physical_menu_entries=entries, week_code=week),
        "original_expected_sheet_cells": batch.expected_sheet_cells_from_target_regions(plan),
        "original_target_semantics": geometry.canonical_target_semantics(plan),
        "selected_page": config["facility"]["fax_template"]["page"],
        "quad_override": deepcopy(meta.get("quad_override")),
        "header_axis_override": deepcopy(meta.get("header_axis_override")),
        "row_axis_override": deepcopy(meta.get("row_axis_override")),
    })
    return item


def operator_values(values, cells):
    if not isinstance(values, dict) or set(values) != set(cells):
        raise ValueError("recognition_values_all_cells_required")
    if any(not isinstance(v, str) or (v != "" and not re.fullmatch(r"[0-9]+", v)) for v in values.values()):
        raise ValueError("recognition_values_invalid")
    return dict(values)


def _native_owner(session, order, job):
    from sqlalchemy import select
    from src.models.ingest_delivery import IngestDelivery
    from src.services import ingest_delivery_service as delivery
    from src.services import workflow_attempt_guard_service as guard

    metrics = job.metrics or {}
    captured = metrics.get("nonworkflow_execution")
    if (not isinstance(captured, dict) or captured.get("ingest_ocr_mode") != "hakodate"
            or metrics.get("ingest_ocr_mode") != "hakodate"
            or metrics.get("workflow_attempt_id") is not None
            or captured.get("intake_protocol") != "native_ingest_v2"
            or captured.get("order_id") != order.id or captured.get("job_order_id") != order.id
            or captured.get("job_id") != job.id or not captured.get("execution_id")):
        raise ValueError("recognition_native_owner_changed")
    lease = captured.get("ingest_lease")
    if (not isinstance(lease, dict) or not isinstance(lease.get("payload"), dict)
            or metrics.get("ingest_lease") != lease):
        raise ValueError("recognition_native_owner_changed")
    orders = captured.get("orders")
    owned = orders.get(order.id) if isinstance(orders, dict) else None
    if (not isinstance(owned, dict) or set(orders) != {order.id}
            or not isinstance(owned.get("order"), dict) or not isinstance(owned.get("document"), dict)):
        raise ValueError("recognition_native_input_changed")
    starts = captured.get("start_orders")
    start = starts.get(order.id) if isinstance(starts, dict) else None
    if not isinstance(start, dict) or not isinstance(start.get("processing_start_input"), dict):
        raise ValueError("recognition_native_start_input_required")
    with guard.nonworkflow_execution_context(captured):
        start = guard.processing_start_read_input()
    if any(not guard.snapshots_equal(start.get(key), owned.get(key))
            for key in ("configured_input", "physical_menu_entries")):
        raise ValueError("recognition_native_input_changed")
    row = session.execute(select(IngestDelivery).where(
        IngestDelivery.job_id == lease.get("id"),
        IngestDelivery.business_generation == lease.get("generation"),
    ).with_for_update()).scalar_one_or_none()
    original = delivery._original(session, row, read_only=True) if row is not None else None
    if (original is None or row is None
            or original.status != ("done" if job.status == "done" else "error")
            or original.claim_id != lease.get("claim_id") or original.attempts != lease.get("attempts")
            or original.input_digest != lease.get("input_digest") or original.payload != lease.get("payload")
            or row.state not in {"CLAIMED", "ACKNOWLEDGED"}):
        raise ValueError("recognition_native_generation_changed")
    kind, terminal = delivery._receipt(session, row, job)
    if kind != "terminal" or terminal != row.terminal_receipt:
        raise ValueError("recognition_native_terminal_changed")
    if not isinstance(metrics.get("rerun_terminal_receipt"), dict):
        raise ValueError("recognition_native_terminal_changed")
    delivery._require_uploaded_terminal(session, row, "completed" if job.status == "done" else "failed")
    # Workflow review metadata may advance, but the terminal's business/source
    # snapshot must still be exact. Saving recovery never renews the old lease.
    current = guard._order_execution_input(session, order, lock=True, original_input=owned)
    for key in owned:
        if key not in {"workflow", "workflow_revision"} and not guard.snapshots_equal(current.get(key), owned[key]):
            raise ValueError("recognition_native_input_changed")
    source = guard.capture_selected_pdf_receipt(session, order)
    if (job.order_document_id != order.current_document_id
            or source != captured.get("selected_pdf_receipt")
            or job.input_artifact_digest != source["sha256"]
            or not guard.verify_selected_pdf_receipt(session, source)):
        raise ValueError("recognition_native_source_changed")
    return workflow._stable_json_hash(terminal)


def _is_native(job):
    metrics = job.metrics or {}
    captured = metrics.get("nonworkflow_execution")
    lease = metrics.get("ingest_lease")
    payload = lease.get("payload") if isinstance(lease, dict) else None
    return (metrics.get("ingest_ocr_mode") is not None
            or isinstance(captured, dict) and captured.get("ingest_ocr_mode") is not None
            or isinstance(payload, dict) and payload.get("intake_protocol") == "native_ingest_v2")


def _failed_checkpoint(session, order, state, ocr_result_id=None):
    job_id = workflow._workflow_meta(state).get("ocr_job_id")
    job = session.get(workflow.OcrJob, job_id) if job_id else None
    if job is None or job.order_id != order.id or not isinstance(job.metrics, dict):
        raise ValueError("recognition_failed_job_required")
    native = _is_native(job)
    if native:
        if workflow._workflow_meta(state).get("ocr_job_attempt_id") is not None:
            raise ValueError("recognition_native_owner_changed")
        _native_owner(session, order, job)
        terminal_result = job.metrics["rerun_terminal_receipt"].get("evidence_run_id")
        if workflow._workflow_meta(state).get("latest_ocr_result_id") != terminal_result:
            raise ValueError("recognition_review_result_changed")
        if ocr_result_id is None and job.status == "failed":
            ocr_result_id = terminal_result
    elif ocr_result_id is None and job.status == "failed":
        terminal = job.metrics.get("rerun_terminal_receipt")
        if isinstance(terminal, dict) and terminal.get("evidence_run_id") is not None:
            if (terminal.get("job_id") != job.id or terminal.get("order_id") != order.id
                    or terminal.get("status") != job.status
                    or terminal.get("workflow_attempt_id") != job.metrics.get("workflow_attempt_id")):
                raise ValueError("recognition_review_result_changed")
            ocr_result_id = terminal["evidence_run_id"]
    if ocr_result_id is not None:
        if job.status not in {"done", "failed"} or workflow._workflow_meta(state).get("latest_ocr_result_id") != ocr_result_id:
            raise ValueError("recognition_review_result_changed")
        evidence = session.get(workflow.OrderOcrEvidenceRun, ocr_result_id)
        payload = evidence.payload_json if evidence is not None else None
        if evidence is None or evidence.order_id != order.id or not isinstance(payload, dict):
            raise ValueError("recognition_review_result_changed")
        execution = payload.get("_ocr_execution")
        review = payload.get("hakodate_recognition_review")
        if (not isinstance(execution, dict) or execution.get("job_id") != job.id
                or execution.get("evidence_run_id") != ocr_result_id
                or (not native and (not (job.metrics or {}).get("workflow_attempt_id")
                    or execution.get("workflow_attempt_id") != job.metrics["workflow_attempt_id"]))
                or (native and (execution.get("workflow_attempt_id") is not None
                    or (job.metrics or {}).get("rerun_terminal_receipt", {}).get("evidence_run_id") != ocr_result_id))
                or not isinstance(review, dict) or not isinstance(review.get("cells"), list)
                or not review["cells"] or type(review.get("count")) is not int
                or review["count"] != len(review["cells"])):
            raise ValueError("recognition_review_result_changed")
    elif job.status != "failed":
        raise ValueError("recognition_failed_job_required")
    checkpoint = (job.metrics or {}).get("recognition_checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("recognition_checkpoint_required")
    return job, deepcopy(checkpoint)


def get_review(order_id, *, document_id=None, ocr_result_id=None, mode=FULL_MANUAL):
    if mode not in {DIAGNOSTIC_SUBSET, FULL_MANUAL}:
        return None, "recognition_recovery_mode_invalid"
    with workflow.session_scope() as session:
        order, error = workflow._get_order_or_error(session, order_id)
        if error:
            return None, error
        state = workflow._get_workflow_readonly(session, order_id)
        if state is None:
            return None, "workflow_not_initialized"
        try:
            job, checkpoint = _failed_checkpoint(session, order, state, ocr_result_id)
            receipt, captured = workflow._axis_source_receipt(session, order, state, document_id)
            receipt["recognition_checkpoint_digest"] = workflow._stable_json_hash(checkpoint)
            receipt["recognition_job_id"] = job.id
            if ocr_result_id is not None:
                receipt["recognition_evidence_run_id"] = ocr_result_id
            elif job.status == "failed":
                terminal = job.metrics.get("rerun_terminal_receipt")
                if isinstance(terminal, dict) and terminal.get("evidence_run_id") is not None:
                    receipt["recognition_evidence_run_id"] = terminal["evidence_run_id"]
            if _is_native(job):
                receipt["native_recognition_owner"] = _native_owner(session, order, job)
                if job.metrics["rerun_terminal_receipt"].get("evidence_run_id") is not None:
                    receipt["recognition_evidence_run_id"] = job.metrics["rerun_terminal_receipt"]["evidence_run_id"]
            detail = (job.metrics or {}).get("error_detail") or job.error_message
            evidence_id = receipt.get("recognition_evidence_run_id")
            evidence = session.get(workflow.OrderOcrEvidenceRun, evidence_id) if evidence_id else None
            review_cells = deepcopy((evidence.payload_json or {}).get("hakodate_recognition_review", {}).get("cells", [])) if evidence else []
            item = current_item(checkpoint, captured, receipt, order_id)
            loaded = checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
            evidence_id = receipt.get("recognition_evidence_run_id")
            if mode == DIAGNOSTIC_SUBSET:
                if evidence_id is None:
                    raise ValueError("recognition_diagnostic_evidence_required_use_full_manual")
                _, proof = _diagnostic_source(session, order, job, evidence_id,
                    checkpoint=checkpoint, item=item, loaded=loaded, captured=captured)
                receipt.update(proof)
                required_cells = set(proof["recognition_review_cells"])
            else:
                receipt["recognition_mode"] = FULL_MANUAL
                required_cells = set(loaded["expected_sheet_cells"])
        except ValueError as exc:
            return None, str(exc)
    try:
        image = loaded["raw_rectified"]
        cells = []
        for region in loaded["regions"]:
            if region["sheet_cell"] not in required_cells:
                continue
            x0, y0, x1, y1 = region["bbox"]
            x0, y0 = max(0, int(x0)), max(0, int(y0))
            x1, y1 = min(image.shape[1], ceil(x1)), min(image.shape[0], ceil(y1))
            if x1 <= x0 or y1 <= y0:
                raise ValueError("recognition_cell_image_invalid")
            ok, encoded = cv2.imencode(".png", image[y0:y1, x0:x1])
            if not ok:
                raise ValueError("recognition_cell_image_invalid")
            cells.append({"cell": region["sheet_cell"], "image_png_base64": base64.b64encode(encoded).decode("ascii")})
        return {"order_id": order_id, "source_receipt": receipt, "error_detail": detail, "cells": cells,
                "review_cells": review_cells, "mode": receipt["recognition_mode"],
                "retained_cell_count": len(loaded["regions"]) - len(cells)}, None
    except ValueError as exc:
        return None, str(exc)


@workflow.workflow_command
def save_review(order_id, *, source_receipt, values, edited_by):
    if not edited_by:
        return None, "recognition_operator_identity_required"
    with workflow.session_scope() as session:
        order, error = workflow._get_order_or_error(session, order_id)
        if error:
            return None, error
        state = workflow._get_workflow(session, order_id)
        if state is None:
            return None, "workflow_not_initialized"
        try:
            error = workflow._validate_axis_receipt(session, order, state, source_receipt)
            if error:
                return None, error
            job, checkpoint = _failed_checkpoint(session, order, state, source_receipt.get("recognition_evidence_run_id"))
            if (_is_native(job) and source_receipt.get("native_recognition_owner") != _native_owner(session, order, job)
                    or not _is_native(job) and "native_recognition_owner" in source_receipt):
                raise ValueError("recognition_native_owner_changed")
            if (source_receipt.get("recognition_job_id") != job.id
                    or source_receipt.get("recognition_checkpoint_digest") != workflow._stable_json_hash(checkpoint)):
                raise ValueError("recognition_checkpoint_changed")
            captured = workflow.capture_reparse_read_input(session, order)
            item = current_item(checkpoint, captured, source_receipt, order_id)
            loaded = checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
            evidence_id = source_receipt.get("recognition_evidence_run_id")
            mode = source_receipt.get("recognition_mode")
            if mode == DIAGNOSTIC_SUBSET:
                if evidence_id is None:
                    raise ValueError("recognition_diagnostic_evidence_required_use_full_manual")
                _, proof = _diagnostic_source(session, order, job, evidence_id,
                    checkpoint=checkpoint, item=item, loaded=loaded, captured=captured)
                if any(source_receipt.get(key) != value for key, value in proof.items()):
                    raise ValueError("recognition_diagnostic_source_changed")
                required_cells = proof["recognition_review_cells"]
            elif mode == FULL_MANUAL:
                required_cells = loaded["expected_sheet_cells"]
            else:
                raise ValueError("recognition_recovery_mode_changed")
            authored = operator_values(values, required_cells)
        except ValueError as exc:
            return None, str(exc)
        meta = workflow._workflow_meta(state)
        try:
            workflow._validate_recognition_override_archive(meta)
        except ValueError as exc:
            return None, str(exc)
        meta["recognition_override"] = {"checkpoint": checkpoint, "values": authored, "mode": mode,
            "source_receipt": deepcopy(source_receipt), "edited_by": edited_by,
            "source": "operator", "created_at": workflow._now().isoformat()}
        meta.pop("recognition_override_invalidation", None)
        workflow._write_workflow_meta(state, meta)
        state.state = "context_confirmed"
        state.headline = "セルの手入力を保存しました。処理を再開できます"
        state.primary_action = "run_ocr"
        state.blockers_json = []
        state.last_transition_at = workflow._now()
        error = workflow._finalize_workflow_mutation(session, state)
        return workflow._serialize_workflow(state, ocr_job=job), error


@workflow.workflow_command
def accept_review(order_id, *, source_receipt, values, edited_by):
    """Adopt explicit interpretations without rewriting or rerunning recognition."""
    from src.services import recognition_acceptance_service as acceptance
    if not isinstance(edited_by, str) or not edited_by.strip():
        return None, "recognition_operator_identity_required"
    with workflow.session_scope() as session:
        order, error = workflow._get_order_or_error(session, order_id)
        if error:
            return None, error
        state = workflow._get_workflow(session, order_id)
        if state is None:
            return None, "workflow_not_initialized"
        try:
            error = workflow._validate_axis_receipt(session, order, state, source_receipt)
            if error:
                return None, error
            identity = source_receipt.get("recognition_evidence_run_id")
            if not identity:
                raise ValueError("recognition_review_result_changed")
            job, checkpoint = _failed_checkpoint(session, order, state, identity)
            if (source_receipt.get("recognition_job_id") != job.id
                    or source_receipt.get("recognition_checkpoint_digest") != workflow._stable_json_hash(checkpoint)):
                raise ValueError("recognition_checkpoint_changed")
            if (_is_native(job) and source_receipt.get("native_recognition_owner") != _native_owner(session, order, job)
                    or not _is_native(job) and "native_recognition_owner" in source_receipt):
                raise ValueError("recognition_native_owner_changed")
            if state.draft_id and state.evidence_run_id != identity:
                raise ValueError("recognition_acceptance_saved_draft_conflict")
            row = session.get(workflow.OrderOcrEvidenceRun, identity)
            if acceptance.KEY in (row.artifact_manifest_json or {}):
                raise ValueError("recognition_acceptance_already_recorded")
            service = workflow._get_order_service_module()
            captured = workflow.capture_reparse_read_input(session, order)
            captured["selected_pdf_receipt"] = source_receipt["selected_pdf_receipt"]
            snapshot = {"id": row.id, "payload_json": row.payload_json,
                        "artifact_manifest_json": row.artifact_manifest_json}
            service._validate_selected_hakodate_artifacts(snapshot, captured, allow_review=True)
            projected = acceptance.acknowledge(row.payload_json, values=values, edited_by=edited_by,
                created_at=workflow._now().isoformat(), source_receipt=source_receipt)
            audit = projected[acceptance.KEY]
            audit["artifact_digest"] = row.artifact_digest
            audit["original_hakodate_source"] = service._original_hakodate_execution_source(row.payload_json)
            meta = workflow._workflow_meta(state)
            history = meta.get("recognition_acceptances", [])
            if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
                raise ValueError("recognition_acceptance_history_invalid")
        except (ValueError, KeyError, TypeError) as exc:
            return None, str(exc)
        # Only metadata changes. Original OCR payload, digest, files and job stay exact.
        row.artifact_manifest_json = {**(row.artifact_manifest_json or {}), acceptance.KEY: audit}
        if not workflow.ocr_evidence_service.evidence_run_is_selectable(row):
            session.rollback()
            return None, "recognition_acceptance_other_blockers"
        meta["recognition_acceptances"] = history + [deepcopy(audit)]
        workflow._write_workflow_meta(state, meta)
        error = workflow._finalize_workflow_mutation(session, state)
        return workflow._serialize_workflow(state, ocr_job=job), error
