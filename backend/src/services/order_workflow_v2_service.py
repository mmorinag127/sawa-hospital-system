from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.ocr_job import OcrJob
from src.services import config_service


Base.metadata.create_all(bind=engine)

WORKFLOW_V2_META_KEY = "workflow_v2"


def _now() -> datetime:
    return datetime.utcnow()


def _normalize_id(value: object) -> str:
    return str(value or "").strip()


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:16]}"


def _format_week_code_from_range(week_start: str, week_end: str) -> str | None:
    try:
        start_date = date.fromisoformat(week_start)
        end_date = date.fromisoformat(week_end)
    except ValueError:
        return None
    if end_date < start_date:
        return None
    return f"{start_date.strftime('%Y-%m')}@{start_date.isoformat()}~{end_date.isoformat()}"


def _workflow_meta(row: OrderWorkflowState | None) -> dict[str, Any]:
    if row is None or not isinstance(row.secondary_actions_json, dict):
        return {}
    meta = row.secondary_actions_json.get(WORKFLOW_V2_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _write_workflow_meta(row: OrderWorkflowState, meta: dict[str, Any]) -> None:
    existing = dict(row.secondary_actions_json) if isinstance(row.secondary_actions_json, dict) else {}
    existing[WORKFLOW_V2_META_KEY] = dict(meta)
    row.secondary_actions_json = existing


def _serialize_datetime(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_order_service_module() -> Any:
    from src.services import order_service  # noqa: PLC0415

    return order_service


def _serialize_ocr_job(job: OcrJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    elapsed_seconds = _coerce_float(metrics.get("ocr_elapsed_seconds") or metrics.get("elapsed_seconds"))
    started_at = _coerce_datetime(metrics.get("ocr_started_at") or metrics.get("run_started_at"))
    finished_at = _coerce_datetime(
        metrics.get("ocr_finished_at")
        or metrics.get("run_finished_at")
        or (metrics.get("stage_updated_at") if job.status in {"done", "failed"} else None)
    )
    if elapsed_seconds is None and isinstance(started_at, datetime) and isinstance(finished_at, datetime):
        elapsed_seconds = max((finished_at - started_at).total_seconds(), 0.0)
    return {
        "ocr_job_id": job.id,
        "status": job.status,
        "created_at": _serialize_datetime(job.created_at),
        "updated_at": _serialize_datetime(job.updated_at),
        "started_at": _serialize_datetime(started_at),
        "finished_at": _serialize_datetime(finished_at),
        "elapsed_seconds": round(elapsed_seconds, 3) if elapsed_seconds is not None else None,
        "processing_stage": metrics.get("processing_stage"),
        "result_state": metrics.get("result_state"),
    }


def _serialize_workflow(row: OrderWorkflowState, *, ocr_job: OcrJob | None = None) -> dict[str, Any]:
    meta = _workflow_meta(row)
    return {
        "order_id": row.order_id,
        "state": row.state,
        "headline": row.headline,
        "primary_action": row.primary_action,
        "selected_ocr_result_id": row.evidence_run_id,
        "saved_sheet_id": row.draft_id,
        "confirmed_snapshot_id": row.confirmed_snapshot_id,
        "facility_id": meta.get("facility_id"),
        "week_start": meta.get("week_start"),
        "week_end": meta.get("week_end"),
        "template_id": meta.get("template_id"),
        "bagging_result_id": meta.get("bagging_result_id"),
        "output_bundle_id": meta.get("output_bundle_id"),
        "ocr_job": _serialize_ocr_job(ocr_job),
        "blockers": list(row.blockers_json or []),
        "warnings": list(row.warnings_json or []),
        "updated_at": _serialize_datetime(row.last_transition_at),
        "source": "workflow_v2",
    }


def _get_order_or_error(session: Any, order_id: str) -> tuple[Order | None, str | None]:
    normalized_order_id = _normalize_id(order_id)
    if not normalized_order_id:
        return None, "order_id_required"
    order = session.get(Order, normalized_order_id)
    if order is None:
        return None, "order_not_found"
    return order, None


def _get_or_create_workflow(session: Any, order_id: str) -> OrderWorkflowState:
    row = session.get(OrderWorkflowState, order_id)
    if row is not None:
        return row
    row = OrderWorkflowState(
        order_id=order_id,
        state="uploaded",
        headline="PDFと施設・週次を確認してください",
        primary_action="confirm_context",
        secondary_actions_json={WORKFLOW_V2_META_KEY: {}},
        blockers_json=[],
        warnings_json=[],
        confidence_band=None,
        last_transition_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def get_workflow(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        row = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(row)
        ocr_job_id = _normalize_id(meta.get("ocr_job_id"))
        ocr_job = session.get(OcrJob, ocr_job_id) if ocr_job_id else None
        return _serialize_workflow(row, ocr_job=ocr_job), None


def confirm_context(
    *,
    order_id: str,
    facility_id: str,
    week_start: str,
    week_end: str,
    template_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized_facility_id = _normalize_id(facility_id)
    normalized_week_start = _normalize_id(week_start)
    normalized_week_end = _normalize_id(week_end)
    if not normalized_facility_id:
        return None, "facility_id_required"
    if not normalized_week_start or not normalized_week_end:
        return None, "week_range_required"
    normalized_week_code = _format_week_code_from_range(normalized_week_start, normalized_week_end)
    if not normalized_week_code:
        return None, "week_range_invalid"
    facility_config = config_service.get_facility_config(normalized_facility_id)
    if not facility_config:
        return None, "facility_not_found"
    normalized_template_id = _normalize_id(template_id) or _normalize_id(facility_config.get("fax_template_id"))
    if not normalized_template_id:
        return None, "facility_template_unresolved"

    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        order.facility_code = normalized_facility_id
        order.week_code = normalized_week_code
        row = _get_or_create_workflow(session, order.id)
        row.state = "context_confirmed"
        row.headline = "施設・週次・テンプレートが確定しました"
        row.primary_action = "run_ocr"
        row.evidence_run_id = None
        row.draft_id = None
        row.confirmed_snapshot_id = None
        row.blockers_json = []
        row.warnings_json = []
        row.last_transition_at = _now()
        _write_workflow_meta(
            row,
            {
                "facility_id": normalized_facility_id,
                "week_start": normalized_week_start,
                "week_end": normalized_week_end,
                "week_code": normalized_week_code,
                "template_id": normalized_template_id,
                "bagging_result_id": None,
                "output_bundle_id": None,
            },
        )
        return _serialize_workflow(row), None


def mark_ocr_run_queued(order_id: str, job_id: str) -> tuple[dict[str, Any] | None, str | None]:
    normalized_job_id = _normalize_id(job_id)
    if not normalized_job_id:
        return None, "ocr_job_id_required"

    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(workflow)
        if not meta.get("facility_id") or not meta.get("week_start") or not meta.get("template_id"):
            return None, "context_not_confirmed"
        workflow.evidence_run_id = None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        session.flush()
        _delete_downstream_after_ocr_change(session, order.id)
        workflow.state = "ocr_running"
        workflow.headline = "OCR処理を実行中です"
        workflow.primary_action = "wait_ocr"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        meta["ocr_job_id"] = normalized_job_id
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return _serialize_workflow(workflow), None


def mark_ocr_run_completed(
    order_id: str,
    *,
    job_id: str,
    evidence_run_id: str | None = None,
    error: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized_job_id = _normalize_id(job_id)
    if not normalized_job_id:
        return None, "ocr_job_id_required"
    normalized_evidence_run_id = _normalize_id(evidence_run_id)
    normalized_error = _normalize_id(error)

    with session_scope() as session:
        order, order_error = _get_order_or_error(session, order_id)
        if order_error:
            return None, order_error
        workflow = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(workflow)
        if not meta.get("facility_id") or not meta.get("week_start") or not meta.get("template_id"):
            return None, "context_not_confirmed"
        if normalized_error:
            workflow.state = "ocr_failed"
            workflow.headline = "OCR処理に失敗しました。Step1から再実行してください"
            workflow.primary_action = "run_ocr"
            workflow.blockers_json = [normalized_error]
            workflow.warnings_json = []
        else:
            workflow.state = "ocr_completed"
            workflow.headline = "OCR結果が作成されました。正解OCRを一つ選択してください"
            workflow.primary_action = "select_ocr"
            workflow.blockers_json = []
            workflow.warnings_json = []
        workflow.evidence_run_id = None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        workflow.last_transition_at = _now()
        meta["ocr_job_id"] = normalized_job_id
        meta["latest_ocr_result_id"] = normalized_evidence_run_id or None
        meta["latest_ocr_error"] = normalized_error or None
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return _serialize_workflow(workflow), None


def _serialize_ocr_result(row: OrderOcrEvidenceRun, *, selected: bool) -> dict[str, Any]:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    manifest = row.artifact_manifest_json if isinstance(row.artifact_manifest_json, dict) else {}
    hakodate_overlay = payload.get("hakodate_overlay") if isinstance(payload.get("hakodate_overlay"), dict) else {}
    overlay_uri = _normalize_id(hakodate_overlay.get("uri") or hakodate_overlay.get("overlay_uri"))
    overlay_url = _normalize_id(hakodate_overlay.get("url") or hakodate_overlay.get("overlay_url"))
    if overlay_uri and not overlay_url:
        signed_url = getattr(_get_order_service_module(), "_signed_url_from_uri", lambda _uri: None)(overlay_uri)
        overlay_url = _normalize_id(signed_url)
    overlay_status = "ready" if overlay_url else "missing"
    return {
        "ocr_result_id": row.id,
        "order_id": row.order_id,
        "schema_version": row.schema_version,
        "producer_version": row.producer_version,
        "source": row.source,
        "status": row.status,
        "selected": selected,
        "artifact_manifest": manifest,
        "artifact_digest": row.artifact_digest,
        "pipeline_version": payload.get("pipeline_version") or row.producer_version,
        "overlay_url": overlay_url or None,
        "overlay_uri": overlay_uri or None,
        "overlay_status": overlay_status,
        "overlay_message": None if overlay_url else "このOCR結果には表示可能なoverlay成果物がありません。",
        "created_at": _serialize_datetime(row.created_at),
    }


def _compact_target_cell_map_for_sheet(
    *,
    target_cells: list[Any],
    fields: list[str],
    row_count: int,
) -> list[dict[str, Any]]:
    field_index = {str(field or "").strip(): idx for idx, field in enumerate(fields) if str(field or "").strip()}
    compact: list[dict[str, Any]] = []
    for target in target_cells:
        if not isinstance(target, dict):
            continue
        metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        truth = metadata.get("truth") if isinstance(metadata.get("truth"), dict) else {}
        field = str(
            truth.get("field")
            or target.get("field")
            or target.get("semantic_field")
            or ""
        ).strip()
        col_index = field_index.get(field)
        try:
            row_index = int(truth.get("row_index"))
        except (TypeError, ValueError):
            row_index = -1
        bbox = target.get("bbox") if isinstance(target.get("bbox"), list) else None
        center = target.get("center") if isinstance(target.get("center"), list) else None
        if row_index < 0 or row_index >= row_count or col_index is None or not bbox or len(bbox) != 4:
            continue
        compact.append(
            {
                "target_row_index": row_index,
                "target_col_index": col_index,
                "field": field,
                "sheet_cell": target.get("sheet_cell") or target.get("target_cell_id"),
                "target_cell_id": target.get("target_cell_id"),
                "bbox": [float(value) for value in bbox],
                "center": [float(value) for value in center[:2]] if center and len(center) >= 2 else None,
            }
        )
    return compact


def list_ocr_results(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order.id)
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .all()
        )
        return {
            "order_id": order.id,
            "selected_ocr_result_id": workflow.evidence_run_id,
            "results": [
                _serialize_ocr_result(row, selected=row.id == workflow.evidence_run_id)
                for row in rows
            ],
        }, None


def _delete_downstream_after_ocr_change(session: Any, order_id: str) -> None:
    current_state = session.get(OrderCurrentState, order_id)
    if current_state is not None:
        current_state.draft_id = None
        current_state.evidence_run_id = None
    session.query(OrderConfirmedSnapshot).filter(OrderConfirmedSnapshot.order_id == order_id).delete(synchronize_session=False)
    session.flush()
    session.query(OrderSheetDraft).filter(OrderSheetDraft.order_id == order_id).delete(synchronize_session=False)


def select_ocr_result(order_id: str, ocr_result_id: str) -> tuple[dict[str, Any] | None, str | None]:
    normalized_ocr_result_id = _normalize_id(ocr_result_id)
    if not normalized_ocr_result_id:
        return None, "ocr_result_id_required"

    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        ocr_result = session.get(OrderOcrEvidenceRun, normalized_ocr_result_id)
        if ocr_result is None or ocr_result.order_id != order.id:
            return None, "ocr_result_not_found"
        workflow = _get_or_create_workflow(session, order.id)
        if workflow.evidence_run_id != normalized_ocr_result_id:
            workflow.draft_id = None
            workflow.confirmed_snapshot_id = None
            session.flush()
            _delete_downstream_after_ocr_change(session, order.id)
        workflow.evidence_run_id = normalized_ocr_result_id
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        workflow.state = "ocr_selected"
        workflow.headline = "正解OCRが選択されました"
        workflow.primary_action = "edit_sheet"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        meta = _workflow_meta(workflow)
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return _serialize_workflow(workflow), None


def delete_ocr_result(order_id: str, ocr_result_id: str) -> tuple[dict[str, Any] | None, str | None]:
    normalized_ocr_result_id = _normalize_id(ocr_result_id)
    if not normalized_ocr_result_id:
        return None, "ocr_result_id_required"

    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        ocr_result = session.get(OrderOcrEvidenceRun, normalized_ocr_result_id)
        if ocr_result is None or ocr_result.order_id != order.id:
            return None, "ocr_result_not_found"
        workflow = _get_or_create_workflow(session, order.id)
        if workflow.evidence_run_id == normalized_ocr_result_id:
            workflow.draft_id = None
            workflow.confirmed_snapshot_id = None
            session.flush()
            _delete_downstream_after_ocr_change(session, order.id)
            workflow.evidence_run_id = None
            workflow.draft_id = None
            workflow.confirmed_snapshot_id = None
            workflow.state = "context_confirmed" if _workflow_meta(workflow).get("template_id") else "uploaded"
            workflow.headline = "正解OCRが削除されました。OCRを再実行してください"
            workflow.primary_action = "run_ocr"
            workflow.blockers_json = []
            workflow.warnings_json = []
            meta = _workflow_meta(workflow)
            meta["bagging_result_id"] = None
            meta["output_bundle_id"] = None
            _write_workflow_meta(workflow, meta)
        session.delete(ocr_result)
        workflow.last_transition_at = _now()
        return _serialize_workflow(workflow), None


def save_sheet(
    *,
    order_id: str,
    sheet: dict[str, Any],
    edited_by: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(sheet, dict):
        return None, "sheet_required"

    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        if not workflow.evidence_run_id:
            return None, "selected_ocr_required"
        evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id)
        if evidence is None or evidence.order_id != order.id:
            return None, "selected_ocr_missing"
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        session.flush()
        _delete_downstream_after_sheet_change(session, order.id)
        draft = OrderSheetDraft(
            id=_new_id("ODS"),
            order_id=order.id,
            base_evidence_run_id=evidence.id,
            base_template_resolution_id=_workflow_meta(workflow).get("template_id"),
            base_menu_snapshot_id=None,
            draft_sheet_json=dict(sheet),
            draft_state="saved",
            blockers_json=[],
            warnings_json=[],
            latest_patch_candidate_id=None,
            edited_by=_normalize_id(edited_by) or None,
            edited_at=_now(),
            created_at=_now(),
        )
        session.add(draft)
        workflow.draft_id = draft.id
        workflow.confirmed_snapshot_id = None
        workflow.state = "sheet_saved"
        workflow.headline = "シートが保存されました"
        workflow.primary_action = "run_bagging"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        meta = _workflow_meta(workflow)
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return {
            "workflow": _serialize_workflow(workflow),
            "saved_sheet": _serialize_saved_sheet(draft),
        }, None


def _delete_downstream_after_sheet_change(session: Any, order_id: str) -> None:
    session.query(OrderConfirmedSnapshot).filter(OrderConfirmedSnapshot.order_id == order_id).delete(synchronize_session=False)
    session.query(OrderSheetDraft).filter(OrderSheetDraft.order_id == order_id).delete(synchronize_session=False)


def _serialize_saved_sheet(row: OrderSheetDraft) -> dict[str, Any]:
    return {
        "saved_sheet_id": row.id,
        "order_id": row.order_id,
        "source_ocr_result_id": row.base_evidence_run_id,
        "sheet": row.draft_sheet_json if isinstance(row.draft_sheet_json, dict) else {},
        "state": row.draft_state,
        "edited_by": row.edited_by,
        "edited_at": _serialize_datetime(row.edited_at),
        "created_at": _serialize_datetime(row.created_at),
    }


def _sheet_rows(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sheet.get("rows") if isinstance(sheet, dict) else None
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            normalized.append({"row_index": index, **row})
        elif isinstance(row, list):
            normalized.append({"row_index": index, "cells": list(row)})
    return normalized


def _numeric_value(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _quantity_values_from_row(row: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for key, value in row.items():
        key_text = str(key or "").strip().lower()
        if key_text in {"row_index", "date", "daypart", "menu", "menu_name", "cells"}:
            continue
        numeric = _numeric_value(value)
        if numeric is not None:
            values.append(numeric)
    cells = row.get("cells")
    if isinstance(cells, list):
        for value in cells:
            numeric = _numeric_value(value)
            if numeric is not None:
                values.append(numeric)
    return values


def _draft_record_from_saved_sheet(saved_sheet: OrderSheetDraft) -> dict[str, Any]:
    return {
        "id": saved_sheet.id,
        "order_id": saved_sheet.order_id,
        "base_evidence_run_id": saved_sheet.base_evidence_run_id,
        "base_template_resolution_id": saved_sheet.base_template_resolution_id,
        "base_menu_snapshot_id": saved_sheet.base_menu_snapshot_id,
        "draft_sheet_json": saved_sheet.draft_sheet_json if isinstance(saved_sheet.draft_sheet_json, dict) else {},
        "draft_state": str(saved_sheet.draft_state or "saved").strip() or "saved",
        "blockers_json": list(saved_sheet.blockers_json or []),
        "warnings_json": list(saved_sheet.warnings_json or []),
    }


def _build_materialization_candidate_for_saved_sheet(
    *,
    order: Order,
    saved_sheet: OrderSheetDraft,
) -> dict[str, Any]:
    order_service_module = _get_order_service_module()
    return order_service_module._build_materialization_candidate_from_draft_record(  # noqa: SLF001
        order.id,
        draft_record=_draft_record_from_saved_sheet(saved_sheet),
        facility_id=order.facility_code,
        existing_week_code=order.week_code,
        received_at=order.received_at,
    )


def _build_bagging_result_payload(
    *,
    order_id: str,
    saved_sheet: OrderSheetDraft,
    materialization_candidate: dict[str, Any],
) -> dict[str, Any]:
    lines = [
        line
        for line in (materialization_candidate.get("lines") or [])
        if isinstance(line, dict)
    ]
    quantity_cells = []
    total_quantity = 0.0
    for line_idx, line in enumerate(lines):
        numeric = _numeric_value(line.get("quantity_corrected"))
        if numeric is None:
            numeric = _numeric_value(line.get("quantity_original"))
        if numeric is None:
            continue
        total_quantity += numeric
        quantity_cells.append(
            {
                "line_index": line_idx,
                "source_row_index": line.get("source_row_index"),
                "date": line.get("date"),
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name") or "",
                "diet_type": line.get("diet_type"),
                "area_id": line.get("area_id"),
                "quantity": numeric,
            }
        )
    return {
        "bagging_result_id": _new_id("OBG"),
        "order_id": order_id,
        "source_saved_sheet_id": saved_sheet.id,
        "source_ocr_result_id": saved_sheet.base_evidence_run_id,
        "status": "ready",
        "materialization_candidate": materialization_candidate,
        "summary": {
            "line_count": len(lines),
            "quantity_line_count": len(quantity_cells),
            "total_quantity": total_quantity,
        },
        "quantity_cells": quantity_cells,
        "created_at": _now().isoformat(),
    }


def _current_bagging_result(row: OrderWorkflowState) -> dict[str, Any] | None:
    meta = _workflow_meta(row)
    result = meta.get("bagging_result")
    return dict(result) if isinstance(result, dict) else None


def get_saved_sheet(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        if not workflow.draft_id:
            return None, "saved_sheet_missing"
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if draft is None or draft.order_id != order.id:
            return None, "saved_sheet_missing"
        return _serialize_saved_sheet(draft), None


def build_sheet_from_selected_ocr(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        if not workflow.evidence_run_id:
            return None, "selected_ocr_required"
        evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id)
        if evidence is None or evidence.order_id != order.id:
            return None, "selected_ocr_missing"
        payload = evidence.payload_json if isinstance(evidence.payload_json, dict) else None
        meta = _workflow_meta(workflow)
        facility_id = _normalize_id(meta.get("facility_id") or order.facility_code)
        template_id = _normalize_id(meta.get("template_id"))
        selected_ocr_result_id = evidence.id
    if not isinstance(payload, dict):
        return None, "selected_ocr_payload_missing"
    if not facility_id:
        return None, "facility_id_required"

    order_service_module = _get_order_service_module()
    assignment = order_service_module._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id=order_id,
        facility_id=facility_id,
        template_id=template_id or None,
        payload=payload,
    )
    if not isinstance(assignment, dict):
        return None, "assignment_unavailable"
    base_sheet, sheet_error = order_service_module._build_hakodate_weekly_menu_base_sheet(order_id)  # noqa: SLF001
    if sheet_error:
        return None, sheet_error
    if not isinstance(base_sheet, dict):
        return None, "sheet_unavailable"
    projected = order_service_module._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )
    projected["source"] = "workflow_v2_selected_ocr_projection"
    projected["base_evidence_run_id"] = selected_ocr_result_id
    projected["selected_ocr_result_id"] = selected_ocr_result_id
    projected["template_id"] = template_id or projected.get("template_id")
    target_cell_map = _compact_target_cell_map_for_sheet(
        target_cells=list(assignment.get("target_cells") or []),
        fields=[str(field or "").strip() for field in (projected.get("fields") or [])],
        row_count=len(projected.get("rows") or []),
    )
    projected["target_cell_map"] = target_cell_map
    return {
        "order_id": order_id,
        "selected_ocr_result_id": selected_ocr_result_id,
        "sheet": projected,
        "target_cell_map": target_cell_map,
        "assignment_summary": assignment.get("metrics") if isinstance(assignment.get("metrics"), dict) else {},
        "blockers": list(projected.get("blockers") or []),
        "warnings": list(projected.get("warnings") or []),
        "source": "workflow_v2_selected_ocr_projection",
    }, None


def run_bagging(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        if not workflow.draft_id:
            return None, "saved_sheet_required"
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if draft is None or draft.order_id != order.id:
            return None, "saved_sheet_missing"
        materialization_candidate = _build_materialization_candidate_for_saved_sheet(order=order, saved_sheet=draft)
        if not isinstance(materialization_candidate, dict):
            return None, "saved_sheet_materialization_failed"
        materialization_error = _normalize_id(materialization_candidate.get("error"))
        if materialization_error:
            return None, materialization_error
        bagging_result = _build_bagging_result_payload(
            order_id=order.id,
            saved_sheet=draft,
            materialization_candidate=materialization_candidate,
        )
        meta = _workflow_meta(workflow)
        meta["bagging_result_id"] = bagging_result["bagging_result_id"]
        meta["bagging_result"] = bagging_result
        meta["output_bundle_id"] = None
        meta["output_bundle"] = None
        _write_workflow_meta(workflow, meta)
        workflow.state = "bagging_ready"
        workflow.headline = "袋分け結果を確認してください"
        workflow.primary_action = "confirm_bagging"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        return {
            "workflow": _serialize_workflow(workflow),
            "bagging_result": bagging_result,
        }, None


def confirm_bagging(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        bagging_result = _current_bagging_result(workflow)
        if not bagging_result:
            return None, "bagging_result_required"
        workflow.state = "bagging_confirmed"
        workflow.headline = "袋分け結果が確認されました"
        workflow.primary_action = "review_outputs"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        return {
            "workflow": _serialize_workflow(workflow),
            "bagging_result": bagging_result,
        }, None


def prepare_output_review(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        bagging_result = _current_bagging_result(workflow)
        if not bagging_result:
            return None, "bagging_result_required"
        output_bundle = {
            "output_bundle_id": _new_id("OOB"),
            "order_id": order.id,
            "source_bagging_result_id": bagging_result.get("bagging_result_id"),
            "status": "review_ready",
            "artifacts": [],
            "created_at": _now().isoformat(),
        }
        meta = _workflow_meta(workflow)
        meta["output_bundle_id"] = output_bundle["output_bundle_id"]
        meta["output_bundle"] = output_bundle
        _write_workflow_meta(workflow, meta)
        workflow.state = "output_review"
        workflow.headline = "出力内容を確認してください"
        workflow.primary_action = "final_confirm"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        return {
            "workflow": _serialize_workflow(workflow),
            "bagging_result": bagging_result,
            "output_bundle": output_bundle,
        }, None


def final_confirm(order_id: str, *, confirmed_by: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(workflow)
        output_bundle = meta.get("output_bundle") if isinstance(meta.get("output_bundle"), dict) else None
        bagging_result = meta.get("bagging_result") if isinstance(meta.get("bagging_result"), dict) else None
        if not output_bundle:
            return None, "output_review_required"
        if not workflow.draft_id:
            return None, "saved_sheet_required"
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if draft is None or draft.order_id != order.id:
            return None, "saved_sheet_missing"
        materialization_candidate = (
            bagging_result.get("materialization_candidate")
            if isinstance(bagging_result.get("materialization_candidate"), dict)
            else _build_materialization_candidate_for_saved_sheet(order=order, saved_sheet=draft)
        )
        if not isinstance(materialization_candidate, dict):
            return None, "saved_sheet_materialization_failed"
        materialization_error = _normalize_id(materialization_candidate.get("error"))
        if materialization_error:
            return None, materialization_error
        order_service_module = _get_order_service_module()
        try:
            order_service_module._materialize_confirmed_lines_from_candidate(  # noqa: SLF001
                session,
                order,
                materialization_candidate,
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            return None, f"saved_sheet_materialization_failed:{exc}"
        snapshot_json = {
            "source": "workflow_v2",
            "order_id": order.id,
            "selected_ocr_result_id": workflow.evidence_run_id,
            "saved_sheet_id": draft.id,
            "saved_sheet": draft.draft_sheet_json if isinstance(draft.draft_sheet_json, dict) else {},
            "bagging_result": bagging_result,
            "output_bundle": output_bundle,
            "materialization_candidate": materialization_candidate,
        }
        digest = hashlib.sha256(
            json.dumps(snapshot_json, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        snapshot = OrderConfirmedSnapshot(
            id=_new_id("OCS"),
            order_id=order.id,
            draft_id=draft.id,
            snapshot_digest=digest,
            snapshot_json=snapshot_json,
            confirmed_by=_normalize_id(confirmed_by) or None,
            confirmed_at=_now(),
            created_at=_now(),
        )
        session.add(snapshot)
        workflow.confirmed_snapshot_id = snapshot.id
        workflow.state = "confirmed"
        workflow.headline = "注文が確定されました"
        workflow.primary_action = None
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        return {
            "workflow": _serialize_workflow(workflow),
            "confirmed_snapshot_id": snapshot.id,
            "snapshot_digest": digest,
        }, None


def get_inspection(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        ocr_results = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order.id)
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .all()
        )
        saved_sheet = None
        if workflow.draft_id:
            draft = session.get(OrderSheetDraft, workflow.draft_id)
            if draft is not None and draft.order_id == order.id:
                saved_sheet = _serialize_saved_sheet(draft)
        return {
            "order_id": order.id,
            "source": "workflow_v2_inspection",
            "workflow": _serialize_workflow(workflow),
            "ocr_results": [
                _serialize_ocr_result(row, selected=row.id == workflow.evidence_run_id)
                for row in ocr_results
            ],
            "saved_sheet": saved_sheet,
            "artifact_lineage": {
                "selected_ocr_result_id": workflow.evidence_run_id,
                "saved_sheet_id": workflow.draft_id,
                "confirmed_snapshot_id": workflow.confirmed_snapshot_id,
                "bagging_result_id": _workflow_meta(workflow).get("bagging_result_id"),
                "output_bundle_id": _workflow_meta(workflow).get("output_bundle_id"),
            },
            "bagging_result": _workflow_meta(workflow).get("bagging_result"),
            "output_bundle": _workflow_meta(workflow).get("output_bundle"),
        }, None
