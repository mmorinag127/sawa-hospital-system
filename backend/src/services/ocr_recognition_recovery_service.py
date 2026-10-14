"""Operator recovery of the current failed recognition, with input ownership."""
from copy import deepcopy
import base64
from math import ceil
import re

import cv2

from src.services import hakodate_cell_ocr_batch_service as batch
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from src.services import order_workflow_v2_service as workflow
from src.services import hakodate_step_review_pipeline_service as geometry


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


def _failed_checkpoint(session, order, state):
    job_id = workflow._workflow_meta(state).get("ocr_job_id")
    job = session.get(workflow.OcrJob, job_id) if job_id else None
    if job is None or job.order_id != order.id or job.status != "failed":
        raise ValueError("recognition_failed_job_required")
    checkpoint = (job.metrics or {}).get("recognition_checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("recognition_checkpoint_required")
    return job, deepcopy(checkpoint)


def get_review(order_id, *, document_id=None):
    with workflow.session_scope() as session:
        order, error = workflow._get_order_or_error(session, order_id)
        if error:
            return None, error
        state = workflow._get_workflow_readonly(session, order_id)
        if state is None:
            return None, "workflow_not_initialized"
        try:
            job, checkpoint = _failed_checkpoint(session, order, state)
            receipt, captured = workflow._axis_source_receipt(session, order, state, document_id)
            receipt["recognition_checkpoint_digest"] = workflow._stable_json_hash(checkpoint)
            receipt["recognition_job_id"] = job.id
            detail = (job.metrics or {}).get("error_detail") or job.error_message
        except ValueError as exc:
            return None, str(exc)
    try:
        item = current_item(checkpoint, captured, receipt, order_id)
        loaded = checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
        image = loaded["raw_rectified"]
        cells = []
        for region in loaded["regions"]:
            x0, y0, x1, y1 = region["bbox"]
            x0, y0 = max(0, int(x0)), max(0, int(y0))
            x1, y1 = min(image.shape[1], ceil(x1)), min(image.shape[0], ceil(y1))
            if x1 <= x0 or y1 <= y0:
                raise ValueError("recognition_cell_image_invalid")
            ok, encoded = cv2.imencode(".png", image[y0:y1, x0:x1])
            if not ok:
                raise ValueError("recognition_cell_image_invalid")
            cells.append({"cell": region["sheet_cell"], "image_png_base64": base64.b64encode(encoded).decode("ascii")})
        return {"order_id": order_id, "source_receipt": receipt, "error_detail": detail, "cells": cells}, None
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
        error = workflow._validate_axis_receipt(session, order, state, source_receipt)
        if error:
            return None, error
        try:
            job, checkpoint = _failed_checkpoint(session, order, state)
            if (source_receipt.get("recognition_job_id") != job.id
                    or source_receipt.get("recognition_checkpoint_digest") != workflow._stable_json_hash(checkpoint)):
                raise ValueError("recognition_checkpoint_changed")
            captured = workflow.capture_reparse_read_input(session, order)
            item = current_item(checkpoint, captured, source_receipt, order_id)
            loaded = checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
            authored = operator_values(values, loaded["expected_sheet_cells"])
        except ValueError as exc:
            return None, str(exc)
        meta = workflow._workflow_meta(state)
        try:
            workflow._validate_recognition_override_archive(meta)
        except ValueError as exc:
            return None, str(exc)
        meta["recognition_override"] = {"checkpoint": checkpoint, "values": authored,
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
