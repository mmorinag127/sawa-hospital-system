from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import or_

from src.db import session_scope
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.output import Bag, DeliveryNote, LabelRow, ManufacturingAggregateRow
from src.models.ocr_job import OcrJob
from src.services import config_service, facility_template_version_service, sheet_week_service, workflow_v2_sheet_review_service
from src.services.pdf_render import render_pdf_to_png_bytes
from src.services.storage_service import load_bytes_from_uri

WORKFLOW_V2_META_KEY = "workflow_v2"
EXPANDED_CELL_COPY_MODES = {"auto", "enabled", "disabled"}
_WORKFLOW_V2_CANONICAL_STATES = {
    "uploaded",
    "context_confirmed",
    "ocr_blocked",
    "ocr_running",
    "ocr_failed",
    "ocr_completed",
    "ocr_selected",
    "sheet_saved",
    "bagging_ready",
    "output_review",
    "confirmed",
    "facility_template_unresolved",
    "facility_template_ambiguous",
    "template_version_required",
    "template_version_mismatch",
    "saved_sheet_template_mismatch",
    "bagging_result_template_mismatch",
    "bagging_result_source_mismatch",
    "output_bundle_template_mismatch",
    "output_bundle_source_mismatch",
    "confirmed_snapshot_template_mismatch",
    "ocr_job_not_found",
    "ocr_job_order_mismatch",
    "legacy_ocr_evidence_not_selectable",
}
_WORKFLOW_V2_LEGACY_STATES = {
    "apply_ready",
    "draft_ready",
    "draft_blocked",
    "review_required",
    "evidence_ready",
    "semantic_shell_only",
    "identity_choice_required",
    "layout_choice_required",
    "recovery_required",
    "rerun_in_progress",
    "rerun_failed_keep_current",
}
_WORKFLOW_STATE_UI: dict[str, tuple[str | None, str | None]] = {
    "uploaded": ("PDFから施設・週次を確認してください", "confirm_context"),
    "context_confirmed": ("施設・週次・テンプレートが確定しました", "run_ocr"),
    "ocr_completed": ("OCR結果が作成されました。正解OCRを一つ選択してください", "select_ocr"),
    "ocr_selected": ("正解OCRが選択されました", "edit_sheet"),
    "sheet_saved": ("シートが保存されました", "run_bagging"),
    "bagging_ready": ("袋分け結果を確認してください", "confirm_bagging"),
    "output_review": ("出力内容を確認してください", "final_confirm"),
    "confirmed": ("注文が確定されました", None),
}

_OCR_PROGRESS_STEPS: dict[str, tuple[int, str]] = {
    "queued": (1, "OCR準備中"),
    "document_load": (2, "PDF読込"),
    "ocr_pipeline": (3, "OCR処理"),
    "hakodate_live_pipeline": (3, "位置合わせ/OCR"),
    "inference": (3, "OCR推論"),
    "persist_evidence": (4, "OCR結果保存"),
    "project_hakodate_sheet": (5, "シート候補作成"),
    "evidence_ready": (6, "OCR完了"),
    "draft_blocked": (6, "OCR完了/要確認"),
}
_OCR_PROGRESS_TOTAL = 6
_OCR_PREREQUISITE_BLOCKERS = {
    "facility_missing",
    "facility_not_found",
    "week_unresolved",
    "menu_entries_missing",
    "monthly_menu_object_missing",
    "monthly_menu_facility_scope_missing",
    "monthly_menu_lookup_failed",
    "sheet_fields_not_found",
    "sheet_fields_duplicate",
    "sheet_template_field_invalid",
    "sheet_quantity_columns_missing",
    "sheet_quantity_column_unmapped",
    "sheet_week_dates_incomplete",
    "week_menu_date_mismatch",
    "sheet_date_mismatch",
    "sheet_canonical_mismatch",
    "template_unresolved",
    "weekly_menu_sheet_missing",
    "ocr_prerequisite_check_failed",
}


def _now() -> datetime:
    return datetime.utcnow()


def _normalize_id(value: object) -> str:
    return str(value or "").strip()


def _normalize_expanded_cell_copy_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in EXPANDED_CELL_COPY_MODES else "auto"


def _facility_config_with_expanded_cell_mode(
    facility_config: dict[str, Any] | None,
    mode: object,
) -> dict[str, Any] | None:
    normalized_mode = _normalize_expanded_cell_copy_mode(mode)
    if normalized_mode == "auto":
        return facility_config
    next_config = dict(facility_config or {})
    next_config["expanded_cell_same_daypart_copy_enabled"] = normalized_mode == "enabled"
    return next_config


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


def _week_range_from_week_code(week_code: object) -> tuple[str | None, str | None]:
    _month_id, start_date, end_date = sheet_week_service.parse_sheet_week_value(week_code)
    if isinstance(start_date, date) and isinstance(end_date, date):
        return start_date.isoformat(), end_date.isoformat()
    return None, None


def _workflow_meta(row: OrderWorkflowState | None) -> dict[str, Any]:
    if row is None or not isinstance(row.secondary_actions_json, dict):
        return {}
    meta = row.secondary_actions_json.get(WORKFLOW_V2_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _write_workflow_meta(row: OrderWorkflowState, meta: dict[str, Any]) -> None:
    existing = dict(row.secondary_actions_json) if isinstance(row.secondary_actions_json, dict) else {}
    existing[WORKFLOW_V2_META_KEY] = dict(meta)
    row.secondary_actions_json = existing


def _normalize_context_suggestion(suggestion: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(suggestion, dict):
        return None
    facility_id = _normalize_id(suggestion.get("facility_id"))
    facility_name = _normalize_id(suggestion.get("facility_name"))
    week_code = (
        sheet_week_service.normalize_sheet_week_value(suggestion.get("week_code"))
        or sheet_week_service.normalize_sheet_week_value(suggestion.get("week_hint"))
        or _normalize_id(suggestion.get("week_code"))
        or _normalize_id(suggestion.get("week_hint"))
    )
    week_start = _normalize_id(suggestion.get("week_start"))
    week_end = _normalize_id(suggestion.get("week_end"))
    if week_code and (not week_start or not week_end):
        parsed_start, parsed_end = _week_range_from_week_code(week_code)
        week_start = week_start or _normalize_id(parsed_start)
        week_end = week_end or _normalize_id(parsed_end)
    candidates = [
        item
        for item in (suggestion.get("facility_candidates") or [])
        if isinstance(item, dict)
    ]
    normalized: dict[str, Any] = {
        "source": _normalize_id(suggestion.get("source")) or "ocr_context_suggestion",
        "facility_id": facility_id or None,
        "facility_name": facility_name or None,
        "facility_candidates": candidates[:5],
        "week_code": week_code or None,
        "week_start": week_start or None,
        "week_end": week_end or None,
        "week_label": sheet_week_service.format_sheet_week_label(week_code) if week_code else None,
        "date_hints": [
            str(item).strip()
            for item in (suggestion.get("date_hints") or [])
            if str(item).strip()
        ][:20],
        "confidence": _normalize_id(suggestion.get("confidence")) or ("medium" if facility_id or week_code else None),
        "created_at": _normalize_id(suggestion.get("created_at")) or _now().isoformat(),
    }
    if not normalized["facility_id"] and not normalized["week_code"] and not normalized["facility_candidates"]:
        return None
    return normalized


def _order_context_suggestion(order: Order) -> dict[str, Any] | None:
    facility_id = _normalize_id(order.facility_code)
    week_code = sheet_week_service.normalize_sheet_week_value(order.week_code) or _normalize_id(order.week_code)
    if not facility_id and not week_code:
        return None
    facility_name = None
    if facility_id:
        try:
            facility = config_service.get_facility_config(facility_id)
            if isinstance(facility, dict):
                facility_name = _normalize_id(facility.get("facility_name"))
        except Exception:
            facility_name = None
    return _normalize_context_suggestion(
        {
            "source": "order_ingest_context",
            "facility_id": facility_id or None,
            "facility_name": facility_name,
            "week_code": week_code or None,
            "confidence": "medium",
        }
    )


def _ocr_progress_payload(job: OcrJob | None, metrics: dict[str, Any]) -> dict[str, Any]:
    stage = _normalize_id(metrics.get("processing_stage")).lower()
    result_state = _normalize_id(metrics.get("result_state")).lower()
    status = _normalize_id(job.status if job is not None else None).lower()
    if status in {"done", "failed"} or result_state in {"done", "hard_failed", "draft_ready_blocked"}:
        step, label = _OCR_PROGRESS_STEPS.get(stage, (_OCR_PROGRESS_TOTAL, "OCR完了" if status == "done" else "OCR停止"))
    else:
        step, label = _OCR_PROGRESS_STEPS.get(stage, (1, "OCR処理中"))
    return {
        "progress_step": step,
        "progress_total": _OCR_PROGRESS_TOTAL,
        "progress_label": label,
    }


def _facility_config_has_resolved_fax_template(
    facility_config: dict[str, Any] | None,
    *,
    template_id: str | None = None,
) -> bool:
    return _facility_config_template_resolution_error(facility_config, template_id=template_id) is None


def _facility_config_template_resolution_error(
    facility_config: dict[str, Any] | None,
    *,
    template_id: str | None = None,
) -> str | None:
    if not isinstance(facility_config, dict):
        return "facility_template_unresolved"
    template = facility_config.get("fax_template") if isinstance(facility_config.get("fax_template"), dict) else {}
    resolved_template_id = (
        _normalize_id(template_id)
        or _normalize_id(facility_config.get("fax_template_id"))
        or _normalize_id(template.get("template_id"))
    )
    if not resolved_template_id:
        return "facility_template_unresolved"
    columns = facility_template_version_service.normalize_template_columns(template.get("columns"))
    validation = facility_template_version_service.validate_template_columns(columns)
    if validation.get("errors"):
        return "facility_template_unresolved"
    return None


def _workflow_meta_has_confirmed_context(meta: dict[str, Any]) -> bool:
    return bool(
        _normalize_id(meta.get("facility_id"))
        and _normalize_id(meta.get("week_start"))
        and _normalize_id(meta.get("week_end"))
    )


def _workflow_meta_has_resolved_template(meta: dict[str, Any]) -> bool:
    return bool(_normalize_id(meta.get("template_version_id")))


def _workflow_v2_projection_context_error(meta: dict[str, Any]) -> str | None:
    if not _workflow_meta_has_confirmed_context(meta):
        return "context_not_confirmed"
    if not _workflow_meta_has_resolved_template(meta):
        return "template_version_required"
    return None


def workflow_has_confirmed_ocr_context(workflow: dict[str, Any] | None) -> bool:
    if not isinstance(workflow, dict):
        return False
    meta = {
        "facility_id": workflow.get("facility_id"),
        "week_start": workflow.get("week_start"),
        "week_end": workflow.get("week_end"),
        "template_version_id": workflow.get("template_version_id"),
    }
    return _workflow_meta_has_confirmed_context(meta) and _workflow_meta_has_resolved_template(meta)


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


def _ocr_prerequisite_headline(error: str) -> str:
    if error in {"menu_entries_missing", "monthly_menu_object_missing", "monthly_menu_lookup_failed"}:
        return "対象週の月次メニューが未登録です。メニューを登録してからOCRを実行してください"
    if error == "monthly_menu_facility_scope_missing":
        return "対象施設の月次メニュー差分が未登録です。メニュー設定を確認してください"
    if error == "week_unresolved":
        return "週次が未確定です。Step1で週次を確定してください"
    return "OCR前提条件が未解決です。Step1の施設・週次・メニュー設定を確認してください"


def _hakodate_weekly_menu_base_sheet_error(order_id: str) -> str | None:
    try:
        order_service = _get_order_service_module()
        builder = getattr(order_service, "_build_hakodate_weekly_menu_base_sheet", None)
        if not callable(builder):
            return None
        sheet, error = builder(order_id)
    except Exception:
        return "ocr_prerequisite_check_failed"
    if error:
        return str(error).strip() or "weekly_menu_sheet_missing"
    if not isinstance(sheet, dict):
        return "weekly_menu_sheet_missing"
    return None


def _is_prerequisite_failure_job(job: OcrJob | None) -> bool:
    if job is None:
        return False
    error_message = _normalize_id(job.error_message)
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    metric_error = _normalize_id(metrics.get("error"))
    haystack = f"{error_message} {metric_error}"
    if "hakodate_best_method_draft_sheet_missing" in haystack:
        return True
    return any(code in haystack for code in _OCR_PREREQUISITE_BLOCKERS)


def _refresh_ocr_prerequisite_state(
    session: Any,
    order: Order,
    row: OrderWorkflowState,
) -> None:
    meta = _workflow_meta(row)
    if not _workflow_meta_has_confirmed_context(meta) or not _workflow_meta_has_resolved_template(meta):
        return
    if row.primary_action not in {"run_ocr", "wait_ocr"} and row.state not in {
        "context_confirmed",
        "ocr_blocked",
        "ocr_failed",
    }:
        return
    if row.state == "ocr_running" or row.primary_action == "wait_ocr":
        return

    blocker = _hakodate_weekly_menu_base_sheet_error(order.id)
    current_blockers = [
        _normalize_id(item)
        for item in (row.blockers_json or [])
        if _normalize_id(item)
    ]
    ocr_job_id = _normalize_id(meta.get("ocr_job_id"))
    ocr_job = session.get(OcrJob, ocr_job_id) if ocr_job_id else None
    if blocker:
        row.state = "ocr_blocked"
        row.headline = _ocr_prerequisite_headline(blocker)
        row.primary_action = "run_ocr"
        row.blockers_json = [blocker]
        row.warnings_json = []
        row.last_transition_at = _now()
        return

    if row.state == "ocr_blocked" or any(item in _OCR_PREREQUISITE_BLOCKERS for item in current_blockers) or (
        row.state == "ocr_failed" and _is_prerequisite_failure_job(ocr_job)
    ):
        row.state = "context_confirmed"
        row.headline = "施設・週次・テンプレートが確定しました"
        row.primary_action = "run_ocr"
        row.blockers_json = [
            item
            for item in current_blockers
            if item not in _OCR_PREREQUISITE_BLOCKERS and item != "hakodate_live_rerun_failed"
        ]
        row.warnings_json = []
        row.last_transition_at = _now()


def ensure_ocr_prerequisites(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        row = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(row)
        if not _workflow_meta_has_confirmed_context(meta):
            return None, "context_not_confirmed"
        if not _workflow_meta_has_resolved_template(meta):
            _apply_template_lineage_blocker(row, "template_version_required")
            return _serialize_workflow(row), "template_version_required"
        _refresh_ocr_prerequisite_state(session, order, row)
        blockers = [
            _normalize_id(item)
            for item in (row.blockers_json or [])
            if _normalize_id(item)
        ]
        ocr_job_id = _normalize_id(meta.get("ocr_job_id"))
        ocr_job = session.get(OcrJob, ocr_job_id) if ocr_job_id else None
        prerequisite_blocker = next(
            (item for item in blockers if item in _OCR_PREREQUISITE_BLOCKERS),
            None,
        )
        if prerequisite_blocker:
            return _serialize_workflow(row, ocr_job=ocr_job), prerequisite_blocker
        return _serialize_workflow(row, ocr_job=ocr_job), None


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
    progress = _ocr_progress_payload(job, metrics)
    return {
        "ocr_job_id": job.id,
        "order_id": job.order_id,
        "uploaded_pdf_id": job.uploaded_pdf_id,
        "order_document_id": job.order_document_id,
        "input_artifact_digest": job.input_artifact_digest,
        "status": job.status,
        "template_version_id": job.template_version_id,
        "created_at": _serialize_datetime(job.created_at),
        "updated_at": _serialize_datetime(job.updated_at),
        "started_at": _serialize_datetime(started_at),
        "finished_at": _serialize_datetime(finished_at),
        "elapsed_seconds": round(elapsed_seconds, 3) if elapsed_seconds is not None else None,
        "processing_stage": metrics.get("processing_stage"),
        "result_state": metrics.get("result_state"),
        "error_message": _normalize_id(job.error_message) or None,
        "error": _normalize_id(metrics.get("error")) or None,
        **progress,
    }


def _effective_workflow_template_id(meta: dict[str, Any]) -> str | None:
    template_id = _normalize_id(meta.get("template_id"))
    if template_id:
        return template_id
    facility_id = _normalize_id(meta.get("facility_id"))
    if not facility_id:
        return None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception:
        return None
    if not isinstance(facility_config, dict):
        return None
    return _normalize_id(facility_config.get("fax_template_id")) or None


def _effective_workflow_template_version_id(row: OrderWorkflowState, meta: dict[str, Any]) -> str | None:
    return _normalize_id(row.template_version_id) or _normalize_id(meta.get("template_version_id")) or None


def _require_workflow_template_version_from_context(
    session: Any,
    *,
    order: Order,
    workflow: OrderWorkflowState,
    meta: dict[str, Any],
) -> tuple[str | None, str | None]:
    template_version_id = _effective_workflow_template_version_id(workflow, meta)
    if template_version_id:
        facility_id = _normalize_id(meta.get("facility_id")) or _normalize_id(order.facility_code)
        version = session.get(FacilityTemplateVersion, template_version_id)
        if version is None or version.status != "active":
            return None, "template_version_mismatch"
        if facility_id and _normalize_id(version.facility_id) != facility_id:
            return None, "template_version_mismatch"
        columns = list(version.columns_json or [])
        validation = facility_template_version_service.validate_template_columns(columns)
        if validation.get("errors"):
            return None, "facility_template_unresolved"
        return template_version_id, None
    if not _workflow_meta_has_confirmed_context(meta):
        return None, "context_not_confirmed"
    if not _workflow_meta_has_resolved_template(meta):
        return None, "template_version_required"
    facility_id = _normalize_id(meta.get("facility_id")) or _normalize_id(order.facility_code)
    if not facility_id:
        return None, "facility_missing"
    template_version, template_error = facility_template_version_service.resolve_single_active_template_version(
        session,
        facility_id,
    )
    if template_error:
        return None, template_error
    return template_version.id, None


def _resolve_evidence_template_version(
    evidence: OrderOcrEvidenceRun,
    workflow: OrderWorkflowState,
    meta: dict[str, Any],
    *,
    required_template_version_id: str | None = None,
) -> tuple[str | None, str | None]:
    workflow_template_version_id = (
        _normalize_id(required_template_version_id)
        or _normalize_id(workflow.template_version_id)
        or _normalize_id(meta.get("template_version_id"))
    )
    evidence_template_version_id = _normalize_id(evidence.template_version_id)
    if workflow_template_version_id and not evidence_template_version_id:
        return None, "template_version_mismatch"
    if workflow_template_version_id and evidence_template_version_id != workflow_template_version_id:
        return None, "template_version_mismatch"
    if evidence_template_version_id and not workflow_template_version_id:
        return None, "template_version_mismatch"
    return evidence_template_version_id or workflow_template_version_id or None, None


def _ensure_ocr_job_lineage(
    job: OcrJob | None,
    *,
    order_id: str,
    template_version_id: str,
) -> str | None:
    if job is None:
        return "ocr_job_not_found"
    normalized_order_id = _normalize_id(order_id)
    normalized_template_version_id = _normalize_id(template_version_id)
    if not normalized_template_version_id:
        return "template_version_required"
    job_order_id = _normalize_id(job.order_id)
    if job_order_id and job_order_id != normalized_order_id:
        return "ocr_job_order_mismatch"
    job_template_version_id = _normalize_id(job.template_version_id)
    if job_template_version_id and job_template_version_id != normalized_template_version_id:
        return "template_version_mismatch"
    job.order_id = normalized_order_id
    job.template_version_id = normalized_template_version_id
    return None


def _evidence_is_legacy_cache_backfill(row: OrderOcrEvidenceRun) -> bool:
    return _normalize_id(row.source) == "legacy-cache-backfill"


def _apply_template_lineage_blocker(workflow: OrderWorkflowState, error: str | None) -> None:
    normalized_error = _normalize_id(error)
    if not normalized_error:
        return
    if normalized_error == "facility_template_unresolved":
        workflow.state = "facility_template_unresolved"
        workflow.headline = "施設テンプレートが未登録または無効です"
        workflow.primary_action = "register_facility_template"
    elif normalized_error == "template_version_required":
        workflow.state = "template_version_required"
        workflow.headline = "施設テンプレートの版が未確定です。Step1で施設テンプレートを確定してください"
        workflow.primary_action = "confirm_context"
    elif normalized_error == "template_version_mismatch":
        workflow.state = "template_version_mismatch"
        workflow.headline = "施設テンプレートが変更されています。OCRを再実行してください"
        workflow.primary_action = "run_ocr"
    elif normalized_error == "ocr_job_not_found":
        workflow.state = "ocr_job_not_found"
        workflow.headline = "OCRジョブが見つかりません。Step1から再実行してください"
        workflow.primary_action = "run_ocr"
    elif normalized_error == "ocr_job_order_mismatch":
        workflow.state = "ocr_job_order_mismatch"
        workflow.headline = "OCRジョブの注文紐づけが一致しません。Step1から再実行してください"
        workflow.primary_action = "run_ocr"
    elif normalized_error == "legacy_ocr_evidence_not_selectable":
        workflow.state = "legacy_ocr_evidence_not_selectable"
        workflow.headline = "旧キャッシュ由来のOCR結果は正解にできません。OCRを再実行してください"
        workflow.primary_action = "run_ocr"
    elif normalized_error == "facility_template_ambiguous":
        workflow.state = "facility_template_ambiguous"
        workflow.headline = "施設テンプレートが複数有効です。管理画面で一つに確定してください"
        workflow.primary_action = "register_facility_template"
    else:
        workflow.state = normalized_error
        workflow.headline = normalized_error
        workflow.primary_action = "confirm_context"
    workflow.evidence_run_id = None
    workflow.draft_id = None
    workflow.confirmed_snapshot_id = None
    workflow.blockers_json = [normalized_error]
    workflow.warnings_json = []
    workflow.last_transition_at = _now()


def _serialize_workflow(row: OrderWorkflowState, *, ocr_job: OcrJob | None = None) -> dict[str, Any]:
    meta = _workflow_meta(row)
    effective_template_id = _effective_workflow_template_id(meta)
    template_version_id = _effective_workflow_template_version_id(row, meta)
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
        "template_id": effective_template_id,
        "template_version_id": template_version_id,
        "template_source": meta.get("template_source") or ("facility_resolved_template" if effective_template_id else None),
        "expanded_cell_copy_mode": _normalize_expanded_cell_copy_mode(meta.get("expanded_cell_copy_mode")),
        "context_suggestion": _normalize_context_suggestion(meta.get("context_suggestion"))
        or None,
        "bagging_result_id": meta.get("bagging_result_id"),
        "output_bundle_id": meta.get("output_bundle_id"),
        "ocr_job": _serialize_ocr_job(ocr_job),
        "blockers": list(row.blockers_json or []),
        "warnings": list(row.warnings_json or []),
        "updated_at": _serialize_datetime(row.last_transition_at),
        "source": "workflow_v2",
    }


def _workflow_v2_meta_exists(row: OrderWorkflowState) -> bool:
    if not isinstance(row.secondary_actions_json, dict):
        return False
    return isinstance(row.secondary_actions_json.get(WORKFLOW_V2_META_KEY), dict)


def _canonical_workflow_v2_state(row: OrderWorkflowState, meta: dict[str, Any] | None = None) -> str:
    state = _normalize_id(row.state)
    if state in _WORKFLOW_V2_CANONICAL_STATES:
        return state
    if state and state not in _WORKFLOW_V2_LEGACY_STATES:
        return state
    workflow_meta = meta if isinstance(meta, dict) else _workflow_meta(row)
    if not _workflow_v2_meta_exists(row):
        return state
    if row.confirmed_snapshot_id:
        return "confirmed"
    if _normalize_id(workflow_meta.get("output_bundle_id")) and isinstance(workflow_meta.get("output_bundle"), dict):
        return "output_review"
    if _normalize_id(workflow_meta.get("bagging_result_id")) and isinstance(workflow_meta.get("bagging_result"), dict):
        return "bagging_ready"
    if row.draft_id:
        return "sheet_saved"
    if row.evidence_run_id:
        return "ocr_selected"
    if _workflow_meta_has_confirmed_context(workflow_meta):
        return "context_confirmed"
    return state or "uploaded"


def _apply_canonical_workflow_state_projection(
    payload: dict[str, Any],
    *,
    row: OrderWorkflowState,
    state: str,
) -> dict[str, Any]:
    current_state = _normalize_id(payload.get("state"))
    if not _workflow_v2_meta_exists(row) or not state or state == current_state:
        return payload
    projected = dict(payload)
    projected["state"] = state
    headline, primary_action = _WORKFLOW_STATE_UI.get(state, (projected.get("headline"), projected.get("primary_action")))
    projected["headline"] = headline
    projected["primary_action"] = primary_action
    projected["blockers"] = []
    projected["warnings"] = []
    projected["legacy_state"] = current_state or None
    projected["source"] = "workflow_v2_lineage_projection"
    return projected


def _workflow_blocker_projection(serialized: dict[str, Any], error: str) -> dict[str, Any]:
    normalized_error = _normalize_id(error)
    projected = dict(serialized)
    projected["blockers"] = [normalized_error]
    projected["warnings"] = []
    if normalized_error == "template_version_required":
        projected["state"] = "template_version_required"
        projected["headline"] = "施設テンプレートの版が未確定です。Step1で施設テンプレートを確定してください"
        projected["primary_action"] = "confirm_context"
    elif normalized_error == "template_version_mismatch":
        projected["state"] = "template_version_mismatch"
        projected["headline"] = "施設テンプレートが変更されています。OCRを再実行してください"
        projected["primary_action"] = "run_ocr"
    elif normalized_error == "saved_sheet_template_mismatch":
        projected["state"] = "saved_sheet_template_mismatch"
        projected["headline"] = "保存済みシートが現在の施設テンプレートと一致しません。選択OCRからシートを再生成してください"
        projected["primary_action"] = "edit_sheet"
    elif normalized_error == "bagging_result_template_mismatch":
        projected["state"] = "bagging_result_template_mismatch"
        projected["headline"] = "袋分け結果が現在の施設テンプレートと一致しません。袋分けを再作成してください"
        projected["primary_action"] = "run_bagging"
    elif normalized_error == "bagging_result_source_mismatch":
        projected["state"] = "bagging_result_source_mismatch"
        projected["headline"] = "袋分け結果が現在の保存済みシートから作られていません。袋分けを再作成してください"
        projected["primary_action"] = "run_bagging"
    elif normalized_error == "output_bundle_template_mismatch":
        projected["state"] = "output_bundle_template_mismatch"
        projected["headline"] = "出力確認が現在の施設テンプレートと一致しません。出力確認を再作成してください"
        projected["primary_action"] = "final_confirm"
    elif normalized_error == "output_bundle_source_mismatch":
        projected["state"] = "output_bundle_source_mismatch"
        projected["headline"] = "出力確認が現在の袋分け結果から作られていません。出力確認を再作成してください"
        projected["primary_action"] = "final_confirm"
    elif normalized_error == "confirmed_snapshot_template_mismatch":
        projected["state"] = "confirmed_snapshot_template_mismatch"
        projected["headline"] = "確定snapshotが現在の施設テンプレートと一致しません。出力確認から確定し直してください"
        projected["primary_action"] = "final_confirm"
    elif normalized_error == "facility_template_unresolved":
        projected["state"] = "facility_template_unresolved"
        projected["headline"] = "施設テンプレートが未解決です。施設区分列を確認してください"
        projected["primary_action"] = "register_facility_template"
    elif normalized_error == "legacy_ocr_evidence_not_selectable":
        projected["state"] = "legacy_ocr_evidence_not_selectable"
        projected["headline"] = "旧キャッシュ由来のOCR結果は正解にできません。OCRを再実行してください"
        projected["primary_action"] = "run_ocr"
    elif normalized_error in {"selected_ocr_required", "selected_ocr_missing"}:
        projected["state"] = normalized_error
        projected["headline"] = "選択OCRが現在の施設テンプレートと一致しません。OCRを再実行してください"
        projected["primary_action"] = "run_ocr"
    elif normalized_error in {"saved_sheet_required", "saved_sheet_missing"}:
        projected["state"] = normalized_error
        projected["headline"] = "保存済みシートがありません。Step3でシートを作成して保存してください"
        projected["primary_action"] = "edit_sheet"
    elif normalized_error == "bagging_result_required":
        projected["state"] = "bagging_result_required"
        projected["headline"] = "袋分け結果がありません。Step4で袋分けを作成してください"
        projected["primary_action"] = "run_bagging"
    elif normalized_error == "output_review_required":
        projected["state"] = "output_review_required"
        projected["headline"] = "出力確認がありません。Step5の出力確認を作成してください"
        projected["primary_action"] = "final_confirm"
    elif normalized_error == "confirmed_snapshot_required":
        projected["state"] = "confirmed_snapshot_required"
        projected["headline"] = "確定snapshotがありません。出力確認から確定し直してください"
        projected["primary_action"] = "final_confirm"
    else:
        projected["state"] = normalized_error
        projected["headline"] = normalized_error
        projected["primary_action"] = "confirm_context"
    return projected


def _workflow_state_requires_selected_ocr(state: str) -> bool:
    return state in {
        "ocr_selected",
        "sheet_saved",
        "bagging_ready",
        "output_review",
        "confirmed",
    }


def _workflow_state_requires_saved_sheet(state: str) -> bool:
    return state in {
        "sheet_saved",
        "bagging_ready",
        "output_review",
        "confirmed",
    }


def _workflow_state_requires_bagging(state: str) -> bool:
    return state in {"bagging_ready", "output_review", "confirmed"}


def _workflow_state_requires_output_review(state: str) -> bool:
    return state in {"output_review", "confirmed"}


def _workflow_has_downstream_lineage(row: OrderWorkflowState) -> bool:
    meta = _workflow_meta(row)
    return bool(
        row.evidence_run_id
        or row.draft_id
        or row.confirmed_snapshot_id
        or _normalize_id(row.template_version_id)
        or _normalize_id(meta.get("template_version_id"))
        or _normalize_id(meta.get("latest_ocr_result_id"))
        or _normalize_id(meta.get("bagging_result_id"))
        or _normalize_id(meta.get("output_bundle_id"))
        or isinstance(meta.get("bagging_result"), dict)
        or isinstance(meta.get("output_bundle"), dict)
    )


def _workflow_lineage_error(session: Any, *, order: Order, workflow: OrderWorkflowState) -> str | None:
    meta = _workflow_meta(workflow)
    state = _canonical_workflow_v2_state(workflow, meta)
    if not state or state in {
        "uploaded",
        "not_initialized",
        "facility_template_unresolved",
        "template_version_required",
        "template_version_mismatch",
        "ocr_failed",
    }:
        return None

    if _workflow_state_requires_selected_ocr(state) or _workflow_state_requires_saved_sheet(state):
        if not _workflow_meta_has_confirmed_context(meta):
            return "context_not_confirmed"

    template_version_id = _effective_workflow_template_version_id(workflow, meta)
    if _workflow_state_requires_selected_ocr(state) or _workflow_state_requires_saved_sheet(state):
        if not template_version_id:
            return "template_version_required"
        version = session.get(FacilityTemplateVersion, template_version_id)
        facility_id = _normalize_id(meta.get("facility_id")) or _normalize_id(order.facility_code)
        if version is None or version.status != "active":
            return "template_version_mismatch"
        if facility_id and _normalize_id(version.facility_id) != facility_id:
            return "template_version_mismatch"
        validation = facility_template_version_service.validate_template_columns(list(version.columns_json or []))
        if validation.get("errors"):
            return "facility_template_unresolved"

    if _workflow_state_requires_selected_ocr(state):
        if not workflow.evidence_run_id:
            return "selected_ocr_required"
        evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id)
        if evidence is None or evidence.order_id != order.id:
            return "selected_ocr_missing"
        if _evidence_is_legacy_cache_backfill(evidence) or _normalize_id(evidence.status) == "repair_blocked":
            return "legacy_ocr_evidence_not_selectable"
        _evidence_template_version_id, template_error = _resolve_evidence_template_version(
            evidence,
            workflow,
            meta,
            required_template_version_id=template_version_id,
        )
        if template_error:
            return template_error

    if _workflow_state_requires_saved_sheet(state):
        if not workflow.draft_id:
            return "saved_sheet_required"
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if draft is None or draft.order_id != order.id:
            return "saved_sheet_missing"
        if template_version_id and _normalize_id(draft.template_version_id) != template_version_id:
            return "saved_sheet_template_mismatch"

    bagging_result = meta.get("bagging_result") if isinstance(meta.get("bagging_result"), dict) else None
    if _workflow_state_requires_bagging(state):
        if not bagging_result:
            return "bagging_result_required"
        if template_version_id and _normalize_id(bagging_result.get("template_version_id")) != template_version_id:
            return "bagging_result_template_mismatch"
        if workflow.draft_id and _normalize_id(bagging_result.get("source_saved_sheet_id")) != _normalize_id(workflow.draft_id):
            return "bagging_result_source_mismatch"

    output_bundle = meta.get("output_bundle") if isinstance(meta.get("output_bundle"), dict) else None
    if _workflow_state_requires_output_review(state):
        if not output_bundle:
            return "output_review_required"
        if template_version_id and _normalize_id(output_bundle.get("template_version_id")) != template_version_id:
            return "output_bundle_template_mismatch"
        if workflow.draft_id and _normalize_id(output_bundle.get("source_saved_sheet_id")) != _normalize_id(workflow.draft_id):
            return "output_bundle_source_mismatch"
        bagging_result_id = _normalize_id(bagging_result.get("bagging_result_id")) if isinstance(bagging_result, dict) else None
        if bagging_result_id and _normalize_id(output_bundle.get("source_bagging_result_id")) != bagging_result_id:
            return "output_bundle_source_mismatch"

    if state == "confirmed":
        if not workflow.confirmed_snapshot_id:
            return "confirmed_snapshot_required"
        snapshot = session.get(OrderConfirmedSnapshot, workflow.confirmed_snapshot_id)
        if snapshot is None or snapshot.order_id != order.id:
            return "confirmed_snapshot_required"
        if workflow.draft_id and _normalize_id(snapshot.draft_id) != _normalize_id(workflow.draft_id):
            return "confirmed_snapshot_required"
        if template_version_id and _normalize_id(snapshot.template_version_id) != template_version_id:
            return "confirmed_snapshot_template_mismatch"
    return None


def _serialize_workflow_checked(
    session: Any,
    *,
    order: Order,
    workflow: OrderWorkflowState,
    ocr_job: OcrJob | None = None,
) -> dict[str, Any]:
    serialized = _serialize_workflow(workflow, ocr_job=ocr_job)
    meta = _workflow_meta(workflow)
    canonical_state = _canonical_workflow_v2_state(workflow, meta)
    lineage_error = _workflow_lineage_error(session, order=order, workflow=workflow)
    if lineage_error:
        return _workflow_blocker_projection(serialized, lineage_error)
    serialized = _apply_canonical_workflow_state_projection(
        serialized,
        row=workflow,
        state=canonical_state,
    )
    return serialized


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


def _get_workflow(session: Any, order_id: str) -> OrderWorkflowState | None:
    return session.get(OrderWorkflowState, order_id)


def _serialize_uninitialized_workflow(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "state": "not_initialized",
        "headline": "Step1で施設・週次を確定してください",
        "primary_action": "confirm_context",
        "selected_ocr_result_id": None,
        "saved_sheet_id": None,
        "confirmed_snapshot_id": None,
        "facility_id": None,
        "week_start": None,
        "week_end": None,
        "template_id": None,
        "template_version_id": None,
        "template_source": None,
        "expanded_cell_copy_mode": "auto",
        "context_suggestion": _order_context_suggestion(order),
        "bagging_result_id": None,
        "output_bundle_id": None,
        "ocr_job": None,
        "blockers": ["workflow_not_initialized"],
        "warnings": [],
        "updated_at": None,
        "source": "workflow_v2_projection",
    }


def _require_existing_workflow_template_version(
    session: Any,
    *,
    order: Order,
    workflow: OrderWorkflowState,
    meta: dict[str, Any],
) -> tuple[str | None, str | None]:
    template_version_id = _effective_workflow_template_version_id(workflow, meta)
    if not template_version_id:
        return None, "template_version_required"
    facility_id = _normalize_id(meta.get("facility_id")) or _normalize_id(order.facility_code)
    version = session.get(FacilityTemplateVersion, template_version_id)
    if version is None or version.status != "active":
        return None, "template_version_mismatch"
    if facility_id and _normalize_id(version.facility_id) != facility_id:
        return None, "template_version_mismatch"
    columns = list(version.columns_json or [])
    validation = facility_template_version_service.validate_template_columns(columns)
    if validation.get("errors"):
        return None, "facility_template_unresolved"
    return template_version_id, None


def get_workflow(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        row = _get_workflow(session, order.id)
        if row is None:
            return _serialize_uninitialized_workflow(order), None
        meta = _workflow_meta(row)
        ocr_job_id = _normalize_id(meta.get("ocr_job_id"))
        ocr_job = session.get(OcrJob, ocr_job_id) if ocr_job_id else None
        serialized = _serialize_workflow_checked(session, order=order, workflow=row, ocr_job=ocr_job)
        if not serialized.get("context_suggestion"):
            serialized["context_suggestion"] = _order_context_suggestion(order)
        return serialized, None


def record_context_suggestion(
    *,
    order_id: str,
    suggestion: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = _normalize_context_suggestion(suggestion)
    if normalized is None:
        return None, "context_suggestion_empty"
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        row = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(row)
        meta["context_suggestion"] = normalized
        _write_workflow_meta(row, meta)
        if row.state in {"uploaded", "", None}:
            row.state = "uploaded"
            row.headline = "PDFから施設・週次候補を推定しました。確認して確定してください"
            row.primary_action = "confirm_context"
            row.last_transition_at = _now()
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
    requested_template_id = _normalize_id(template_id)
    normalized_template_id = requested_template_id or _normalize_id(facility_config.get("fax_template_id"))
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        order.facility_code = normalized_facility_id
        order.week_code = normalized_week_code
        existing_workflow = _get_workflow(session, order.id)
        row = existing_workflow or _get_or_create_workflow(session, order.id)
        if existing_workflow is not None and _workflow_has_downstream_lineage(existing_workflow):
            _delete_all_ocr_and_downstream_after_template_change(session, order.id)
        current_meta = _workflow_meta(row)
        expanded_cell_copy_mode = _normalize_expanded_cell_copy_mode(current_meta.get("expanded_cell_copy_mode"))
        template_version, template_error = facility_template_version_service.resolve_single_active_template_version(
            session,
            normalized_facility_id,
        )
        if template_error:
            row.state = "facility_template_unresolved"
            row.headline = (
                "施設テンプレートが複数有効です。管理画面で一つに確定してください"
                if template_error == "facility_template_ambiguous"
                else "施設テンプレートが未登録です"
            )
            row.primary_action = "register_facility_template"
            row.template_version_id = None
            row.evidence_run_id = None
            row.draft_id = None
            row.confirmed_snapshot_id = None
            row.blockers_json = [template_error]
            row.warnings_json = []
            row.last_transition_at = _now()
            _write_workflow_meta(
                row,
                {
                    "facility_id": normalized_facility_id,
                    "week_start": normalized_week_start,
                    "week_end": normalized_week_end,
                    "week_code": normalized_week_code,
                    "template_id": None,
                    "template_version_id": None,
                    "template_source": None,
                    "expanded_cell_copy_mode": expanded_cell_copy_mode,
                    "bagging_result_id": None,
                    "output_bundle_id": None,
                },
            )
            return None, template_error
        if requested_template_id and _normalize_id(template_version.template_id) != requested_template_id:
            row.state = "template_version_mismatch"
            row.headline = "選択されたテンプレートと施設の有効テンプレートが一致しません"
            row.primary_action = "register_facility_template"
            row.template_version_id = None
            row.evidence_run_id = None
            row.draft_id = None
            row.confirmed_snapshot_id = None
            row.blockers_json = ["template_version_mismatch"]
            row.warnings_json = []
            row.last_transition_at = _now()
            _write_workflow_meta(
                row,
                {
                    "facility_id": normalized_facility_id,
                    "week_start": normalized_week_start,
                    "week_end": normalized_week_end,
                    "week_code": normalized_week_code,
                    "template_id": requested_template_id or None,
                    "template_version_id": None,
                    "template_source": None,
                    "expanded_cell_copy_mode": expanded_cell_copy_mode,
                    "bagging_result_id": None,
                    "output_bundle_id": None,
                },
            )
            return None, "template_version_mismatch"

        row.state = "context_confirmed"
        row.headline = "施設・週次・テンプレートが確定しました"
        row.primary_action = "run_ocr"
        row.template_version_id = template_version.id
        row.evidence_run_id = None
        row.draft_id = None
        row.confirmed_snapshot_id = None
        order.template_version_id = template_version.id
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
                "template_id": _normalize_id(template_version.template_id) or normalized_template_id or None,
                "template_version_id": template_version.id,
                "template_version_digest": template_version.template_digest,
                "template_source": "facility_template_version",
                "expanded_cell_copy_mode": expanded_cell_copy_mode,
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
        if not _workflow_meta_has_confirmed_context(meta):
            return None, "context_not_confirmed"
        if not _workflow_meta_has_resolved_template(meta):
            _apply_template_lineage_blocker(workflow, "template_version_required")
            return _serialize_workflow(workflow), "template_version_required"
        template_version_id, template_error = _require_workflow_template_version_from_context(
            session,
            order=order,
            workflow=workflow,
            meta=meta,
        )
        if template_error:
            _apply_template_lineage_blocker(workflow, template_error)
            return _serialize_workflow(workflow), template_error
        template_version = session.get(FacilityTemplateVersion, template_version_id) if template_version_id else None
        if template_version is None:
            _apply_template_lineage_blocker(workflow, "facility_template_unresolved")
            return _serialize_workflow(workflow), "facility_template_unresolved"
        _refresh_ocr_prerequisite_state(session, order, workflow)
        blockers = [
            _normalize_id(item)
            for item in (workflow.blockers_json or [])
            if _normalize_id(item)
        ]
        prerequisite_blocker = next(
            (item for item in blockers if item in _OCR_PREREQUISITE_BLOCKERS),
            None,
        )
        if prerequisite_blocker:
            return _serialize_workflow(workflow), prerequisite_blocker
        ocr_job = session.get(OcrJob, normalized_job_id)
        lineage_error = _ensure_ocr_job_lineage(
            ocr_job,
            order_id=order.id,
            template_version_id=template_version.id,
        )
        if lineage_error:
            _apply_template_lineage_blocker(workflow, lineage_error)
            return _serialize_workflow(workflow, ocr_job=ocr_job), lineage_error
        workflow.evidence_run_id = None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        workflow.template_version_id = template_version.id
        order.template_version_id = template_version.id
        session.flush()
        _delete_downstream_after_ocr_change(session, order.id)
        workflow.state = "ocr_running"
        workflow.headline = "OCR処理を実行中です"
        workflow.primary_action = "wait_ocr"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        meta["ocr_job_id"] = normalized_job_id
        meta["template_version_id"] = template_version.id
        meta["template_version_digest"] = template_version.template_digest
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return _serialize_workflow(workflow, ocr_job=ocr_job), None


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
        if not _workflow_meta_has_confirmed_context(meta):
            return None, "context_not_confirmed"
        if not _workflow_meta_has_resolved_template(meta):
            _apply_template_lineage_blocker(workflow, "template_version_required")
            return _serialize_workflow(workflow), "template_version_required"
        workflow_template_version_id, template_error = _require_workflow_template_version_from_context(
            session,
            order=order,
            workflow=workflow,
            meta=meta,
        )
        if template_error:
            _apply_template_lineage_blocker(workflow, template_error)
            return _serialize_workflow(workflow), template_error
        ocr_job = session.get(OcrJob, normalized_job_id)
        lineage_error = _ensure_ocr_job_lineage(
            ocr_job,
            order_id=order.id,
            template_version_id=workflow_template_version_id or "",
        )
        if lineage_error:
            _apply_template_lineage_blocker(workflow, lineage_error)
            return _serialize_workflow(workflow, ocr_job=ocr_job), lineage_error
        if normalized_evidence_run_id and not normalized_error:
            evidence = session.get(OrderOcrEvidenceRun, normalized_evidence_run_id)
            if evidence is None or evidence.order_id != order.id:
                _apply_template_lineage_blocker(workflow, "template_version_mismatch")
                return _serialize_workflow(workflow), "template_version_mismatch"
            if _evidence_is_legacy_cache_backfill(evidence) or _normalize_id(evidence.status) == "repair_blocked":
                _apply_template_lineage_blocker(workflow, "legacy_ocr_evidence_not_selectable")
                return _serialize_workflow(workflow), "legacy_ocr_evidence_not_selectable"
            _evidence_template_version_id, template_error = _resolve_evidence_template_version(
                evidence,
                workflow,
                meta,
                required_template_version_id=workflow_template_version_id,
            )
            if template_error:
                _apply_template_lineage_blocker(workflow, template_error)
                return _serialize_workflow(workflow), template_error
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
        workflow.template_version_id = workflow_template_version_id or workflow.template_version_id
        workflow.last_transition_at = _now()
        meta["ocr_job_id"] = normalized_job_id
        meta["latest_ocr_result_id"] = normalized_evidence_run_id or None
        meta["latest_ocr_error"] = normalized_error or None
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return _serialize_workflow(workflow, ocr_job=ocr_job), None


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
        "template_version_id": row.template_version_id,
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
    def _sheet_field(value: object) -> str:
        token = str(value or "").strip()
        return "remarks" if token == "note" else token

    for target in target_cells:
        if not isinstance(target, dict):
            continue
        metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        truth = metadata.get("truth") if isinstance(metadata.get("truth"), dict) else {}
        field = ""
        logical_targets = target.get("logical_targets") if isinstance(target.get("logical_targets"), list) else []
        candidates = [
            target.get("semantic_field"),
            target.get("field"),
            *[
                candidate
                for logical_target in logical_targets
                if isinstance(logical_target, dict)
                for candidate in (logical_target.get("semantic_field"), logical_target.get("field"))
            ],
            truth.get("field"),
        ]
        for candidate in candidates:
            candidate_field = _sheet_field(candidate)
            if candidate_field and candidate_field in field_index:
                field = candidate_field
                break
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


def _align_hakodate_sheet_payload_for_workflow(
    order_service_module: Any,
    sheet: dict[str, Any],
    target_cells: list[Any],
) -> dict[str, Any]:
    aligner = getattr(order_service_module, "_align_hakodate_sheet_payload_to_target_cells", None)
    if not callable(aligner):
        return sheet
    aligned = aligner(sheet, target_cells)
    return aligned if isinstance(aligned, dict) else sheet


def list_ocr_results(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_workflow(session, order.id)
        workflow_meta = _workflow_meta(workflow) if workflow is not None else {}
        workflow_template_version_id = (
            _effective_workflow_template_version_id(workflow, workflow_meta)
            if workflow is not None
            else None
        )
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order.id)
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .all()
        )
        visible_rows = rows
        hidden_template_mismatch_result_count = 0
        if workflow is not None:
            visible_rows = []
            for row in rows:
                row_template_version_id = _normalize_id(row.template_version_id)
                if workflow_template_version_id and row_template_version_id == workflow_template_version_id:
                    visible_rows.append(row)
                else:
                    hidden_template_mismatch_result_count += 1
        selected_ocr_result_id = workflow.evidence_run_id if workflow is not None else None
        checked_workflow = (
            _serialize_workflow_checked(session, order=order, workflow=workflow)
            if workflow is not None
            else None
        )
        return {
            "order_id": order.id,
            "selected_ocr_result_id": selected_ocr_result_id,
            "workflow_state": checked_workflow["state"] if checked_workflow is not None else "not_initialized",
            "blockers": checked_workflow["blockers"] if checked_workflow is not None else ["workflow_not_initialized"],
            "candidate_template_version_id": workflow_template_version_id,
            "hidden_template_mismatch_result_count": hidden_template_mismatch_result_count,
            "results": [
                _serialize_ocr_result(row, selected=row.id == selected_ocr_result_id)
                for row in visible_rows
            ],
        }, None


def _clear_downstream_references_before_delete(
    session: Any,
    order_id: str,
    *,
    clear_evidence: bool,
) -> None:
    workflow = session.get(OrderWorkflowState, order_id)
    if workflow is not None:
        if clear_evidence:
            workflow.evidence_run_id = None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
    current_state = session.get(OrderCurrentState, order_id)
    if current_state is not None:
        current_state.draft_id = None
        if clear_evidence:
            current_state.evidence_run_id = None
            current_state.template_version_id = None
    order = session.get(Order, order_id)
    if order is not None and _normalize_id(order.status) == "確定":
        order.status = "要確認"
    session.flush()


def _delete_materialized_downstream_rows_for_order(session: Any, order_id: str) -> None:
    workflow = session.get(OrderWorkflowState, order_id)
    meta = _workflow_meta(workflow) if workflow is not None else {}
    snapshot_ids = [
        _normalize_id(row[0])
        for row in session.query(OrderConfirmedSnapshot.id)
        .filter(OrderConfirmedSnapshot.order_id == order_id)
        .all()
        if _normalize_id(row[0])
    ]
    output_bundle_ids = {
        _normalize_id(meta.get("output_bundle_id")),
    }
    output_bundle = meta.get("output_bundle") if isinstance(meta.get("output_bundle"), dict) else None
    if output_bundle is not None:
        output_bundle_ids.add(_normalize_id(output_bundle.get("output_bundle_id")))
    output_bundle_ids = {item for item in output_bundle_ids if item}

    session.query(LabelRow).filter(LabelRow.order_id == order_id).delete(synchronize_session=False)
    session.query(Bag).filter(Bag.order_id == order_id).delete(synchronize_session=False)
    session.query(DeliveryNote).filter(DeliveryNote.order_id == order_id).delete(synchronize_session=False)
    aggregate_query = session.query(ManufacturingAggregateRow)
    aggregate_filters = []
    if snapshot_ids:
        aggregate_filters.append(ManufacturingAggregateRow.confirmed_snapshot_id.in_(snapshot_ids))
    if output_bundle_ids:
        aggregate_filters.append(ManufacturingAggregateRow.output_bundle_id.in_(sorted(output_bundle_ids)))
    if aggregate_filters:
        aggregate_query.filter(or_(*aggregate_filters)).delete(synchronize_session=False)
    session.query(OrderLine).filter(OrderLine.order_id == order_id).delete(synchronize_session=False)
    session.flush()


def _delete_downstream_after_ocr_change(session: Any, order_id: str) -> None:
    _delete_materialized_downstream_rows_for_order(session, order_id)
    _clear_downstream_references_before_delete(session, order_id, clear_evidence=True)
    session.query(OrderConfirmedSnapshot).filter(OrderConfirmedSnapshot.order_id == order_id).delete(synchronize_session=False)
    session.flush()
    session.query(OrderSheetDraft).filter(OrderSheetDraft.order_id == order_id).delete(synchronize_session=False)


def _delete_all_ocr_and_downstream_after_template_change(session: Any, order_id: str) -> int:
    workflow = session.get(OrderWorkflowState, order_id)
    if workflow is not None:
        workflow.evidence_run_id = None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
    _delete_downstream_after_ocr_change(session, order_id)
    session.flush()
    deleted = (
        session.query(OrderOcrEvidenceRun)
        .filter(OrderOcrEvidenceRun.order_id == order_id)
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def set_expanded_cell_copy_mode(order_id: str, mode: str) -> tuple[dict[str, Any] | None, str | None]:
    normalized_mode = _normalize_expanded_cell_copy_mode(mode)
    if normalized_mode != str(mode or "").strip().lower():
        return None, "expanded_cell_copy_mode_invalid"

    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(workflow)
        previous_mode = _normalize_expanded_cell_copy_mode(meta.get("expanded_cell_copy_mode"))
        meta["expanded_cell_copy_mode"] = normalized_mode
        meta["bagging_result_id"] = None
        meta["bagging_result"] = None
        meta["output_bundle_id"] = None
        meta["output_bundle"] = None
        if previous_mode != normalized_mode:
            workflow.draft_id = None
            workflow.confirmed_snapshot_id = None
            session.flush()
            _delete_downstream_after_sheet_change(session, order.id)
            if workflow.evidence_run_id:
                workflow.state = "ocr_selected"
                workflow.headline = "拡大セル設定を変更しました。選択OCRからシートを再生成してください"
                workflow.primary_action = "edit_sheet"
            elif _workflow_meta_has_confirmed_context(meta) and _workflow_meta_has_resolved_template(meta):
                workflow.state = "context_confirmed"
                workflow.headline = "拡大セル設定を変更しました。OCRを実行してください"
                workflow.primary_action = "run_ocr"
            else:
                workflow.state = "uploaded"
                workflow.headline = "PDFと施設・週次を確認してください"
                workflow.primary_action = "confirm_context"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        _write_workflow_meta(workflow, meta)
        return _serialize_workflow(workflow), None


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
        if _evidence_is_legacy_cache_backfill(ocr_result) or _normalize_id(ocr_result.status) == "repair_blocked":
            workflow = _get_or_create_workflow(session, order.id)
            _apply_template_lineage_blocker(workflow, "legacy_ocr_evidence_not_selectable")
            return _serialize_workflow(workflow), "legacy_ocr_evidence_not_selectable"
        workflow = _get_or_create_workflow(session, order.id)
        context_error = _workflow_v2_projection_context_error(_workflow_meta(workflow))
        if context_error:
            return None, context_error
        meta = _workflow_meta(workflow)
        workflow_template_version_id, template_error = _require_workflow_template_version_from_context(
            session,
            order=order,
            workflow=workflow,
            meta=meta,
        )
        if template_error:
            _apply_template_lineage_blocker(workflow, template_error)
            return None, template_error
        evidence_template_version_id, template_error = _resolve_evidence_template_version(
            ocr_result,
            workflow,
            meta,
            required_template_version_id=workflow_template_version_id,
        )
        if template_error:
            _apply_template_lineage_blocker(workflow, template_error)
            return _serialize_workflow(workflow), "template_version_mismatch"
        if workflow.evidence_run_id != normalized_ocr_result_id:
            workflow.draft_id = None
            workflow.confirmed_snapshot_id = None
            session.flush()
            _delete_downstream_after_ocr_change(session, order.id)
        workflow.template_version_id = evidence_template_version_id or workflow_template_version_id or None
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
        meta["template_version_id"] = _normalize_id(workflow.template_version_id) or None
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
            meta = _workflow_meta(workflow)
            workflow.state = (
                "context_confirmed"
                if _workflow_meta_has_confirmed_context(meta) and _workflow_meta_has_resolved_template(meta)
                else "uploaded"
            )
            workflow.headline = "正解OCRが削除されました。OCRを再実行してください"
            workflow.primary_action = "run_ocr"
            workflow.blockers_json = []
            workflow.warnings_json = []
            meta["bagging_result_id"] = None
            meta["output_bundle_id"] = None
            _write_workflow_meta(workflow, meta)
        session.delete(ocr_result)
        workflow.last_transition_at = _now()
        return _serialize_workflow(workflow), None


def save_facility_template_columns(
    order_id: str,
    columns: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, order_error = _get_order_or_error(session, order_id)
        if order_error:
            return None, order_error
        result, error = facility_template_version_service.save_columns_for_order(
            session,
            order=order,
            columns=columns,
            actor="workflow-v2-facility-template-columns",
        )
        if error:
            return result, error
        workflow = _get_or_create_workflow(session, order.id)
        meta = _workflow_meta(workflow)
        template_version = result.get("template_version") if isinstance(result, dict) else None
        template_version_id = _normalize_id((template_version or {}).get("id"))
        template_version_digest = _normalize_id((template_version or {}).get("template_digest"))
        deleted_ocr_results = _delete_all_ocr_and_downstream_after_template_change(session, order.id)
        workflow.template_version_id = template_version_id or None
        workflow.evidence_run_id = None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        order.template_version_id = template_version_id or None
        meta["latest_ocr_result_id"] = None
        meta["latest_ocr_error"] = None
        meta["template_version_id"] = template_version_id or None
        meta["template_version_digest"] = template_version_digest or None
        meta["bagging_result_id"] = None
        meta["bagging_result"] = None
        meta["output_bundle_id"] = None
        meta["output_bundle"] = None
        meta["expanded_cell_copy_mode"] = _normalize_expanded_cell_copy_mode(meta.get("expanded_cell_copy_mode"))
        if _workflow_meta_has_confirmed_context(meta) and _workflow_meta_has_resolved_template(meta):
            workflow.state = "context_confirmed"
            workflow.headline = "施設区分を保存しました。OCRを再実行してください"
            workflow.primary_action = "run_ocr"
            workflow.blockers_json = []
        else:
            workflow.state = "facility_template_unresolved"
            workflow.headline = "施設テンプレートが未解決です"
            workflow.primary_action = "register_facility_template"
            workflow.blockers_json = ["facility_template_unresolved"]
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        _write_workflow_meta(workflow, meta)
        resolved_config = result.get("resolved_config") if isinstance(result, dict) else None
        validation = result.get("validation") if isinstance(result, dict) else None
        return {
            "updated": True,
            "workflow": _serialize_workflow(workflow),
            "resolved_config": resolved_config,
            "validation": validation,
            "template_version": template_version,
            "ocr_results_cleared": deleted_ocr_results,
        }, None


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
        workflow_meta = _workflow_meta(workflow)
        context_error = _workflow_v2_projection_context_error(workflow_meta)
        if context_error:
            _apply_template_lineage_blocker(workflow, context_error)
            return None, context_error
        workflow_template_version_id, template_error = _require_workflow_template_version_from_context(
            session,
            order=order,
            workflow=workflow,
            meta=workflow_meta,
        )
        if template_error:
            _apply_template_lineage_blocker(workflow, template_error)
            return None, template_error
        evidence_template_version_id, template_error = _resolve_evidence_template_version(
            evidence,
            workflow,
            workflow_meta,
            required_template_version_id=workflow_template_version_id,
        )
        if template_error:
            _apply_template_lineage_blocker(workflow, template_error)
            return None, template_error
        resolved_template_version_id = evidence_template_version_id or workflow_template_version_id or None
        workflow.draft_id = None
        workflow.confirmed_snapshot_id = None
        session.flush()
        _delete_downstream_after_sheet_change(session, order.id)
        draft = OrderSheetDraft(
            id=_new_id("ODS"),
            order_id=order.id,
            template_version_id=resolved_template_version_id,
            base_evidence_run_id=evidence.id,
            base_template_resolution_id=workflow_meta.get("template_id"),
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
        workflow.template_version_id = draft.template_version_id
        workflow.confirmed_snapshot_id = None
        workflow.state = "sheet_saved"
        workflow.headline = "シートが保存されました"
        workflow.primary_action = "run_bagging"
        workflow.blockers_json = []
        workflow.warnings_json = []
        workflow.last_transition_at = _now()
        meta = _workflow_meta(workflow)
        meta["template_version_id"] = draft.template_version_id
        meta["bagging_result_id"] = None
        meta["output_bundle_id"] = None
        _write_workflow_meta(workflow, meta)
        return {
            "workflow": _serialize_workflow(workflow),
            "saved_sheet": _serialize_saved_sheet(draft),
        }, None


def propose_sheet_auto_edit(
    *,
    order_id: str,
    sheet: dict[str, Any] | None = None,
    model: str | None = None,
    use_llm: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_workflow(session, order.id)
        review_sheet = sheet if isinstance(sheet, dict) else None
        if review_sheet is None and workflow is not None and workflow.draft_id:
            draft = session.get(OrderSheetDraft, workflow.draft_id)
            if draft is not None and draft.order_id == order.id and isinstance(draft.draft_sheet_json, dict):
                review_sheet = dict(draft.draft_sheet_json)
        if review_sheet is None:
            return None, "sheet_required"
        document_uri = str(order.document_uri or "").strip()
    fax_image_png_base64, fax_image_meta = _render_order_fax_page_for_ai(document_uri)
    result = workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet=review_sheet,
        evidence_payload=None,
        model=model,
        use_llm=use_llm,
        fax_image_png_base64=fax_image_png_base64,
        fax_image_meta=fax_image_meta,
    )
    return result, None


def _render_order_fax_page_for_ai(document_uri: str) -> tuple[str | None, dict[str, Any]]:
    document_uri = str(document_uri or "").strip()
    if not document_uri:
        return None, {"status": "missing_document_uri"}
    try:
        dpi = max(96, min(int(os.getenv("WORKFLOW_V2_AI_FAX_DPI", "140")), 220))
    except ValueError:
        dpi = 140
    try:
        max_pixels = max(1_000_000, int(os.getenv("WORKFLOW_V2_AI_FAX_MAX_PIXELS", "4500000")))
    except ValueError:
        max_pixels = 4_500_000
    try:
        pdf_bytes = load_bytes_from_uri(document_uri)
        png_bytes = render_pdf_to_png_bytes(pdf_bytes=pdf_bytes, dpi=dpi, page=1, max_pixels=max_pixels)
    except Exception as exc:  # noqa: BLE001
        return None, {
            "status": "render_failed",
            "document_uri": document_uri,
            "error": str(exc),
        }
    return base64.b64encode(png_bytes).decode("ascii"), {
        "status": "attached",
        "document_uri": document_uri,
        "dpi": dpi,
        "max_pixels": max_pixels,
        "bytes": len(png_bytes),
    }


def _delete_downstream_after_sheet_change(session: Any, order_id: str) -> None:
    _delete_materialized_downstream_rows_for_order(session, order_id)
    _clear_downstream_references_before_delete(session, order_id, clear_evidence=False)
    session.query(OrderConfirmedSnapshot).filter(OrderConfirmedSnapshot.order_id == order_id).delete(synchronize_session=False)
    session.flush()
    session.query(OrderSheetDraft).filter(OrderSheetDraft.order_id == order_id).delete(synchronize_session=False)


def _serialize_saved_sheet(row: OrderSheetDraft) -> dict[str, Any]:
    return {
        "saved_sheet_id": row.id,
        "order_id": row.order_id,
        "template_version_id": row.template_version_id,
        "source_ocr_result_id": row.base_evidence_run_id,
        "sheet": row.draft_sheet_json if isinstance(row.draft_sheet_json, dict) else {},
        "state": row.draft_state,
        "edited_by": row.edited_by,
        "edited_at": _serialize_datetime(row.edited_at),
        "created_at": _serialize_datetime(row.created_at),
    }


def _saved_sheet_with_target_cell_map(
    *,
    order: Order,
    workflow: OrderWorkflowState,
    saved_sheet: OrderSheetDraft,
    evidence_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    serialized = _serialize_saved_sheet(saved_sheet)
    sheet = serialized.get("sheet") if isinstance(serialized.get("sheet"), dict) else {}
    if isinstance(sheet.get("target_cell_map"), list) and sheet.get("target_cell_map"):
        return serialized

    if not isinstance(evidence_payload, dict):
        return serialized

    fields = [str(field or "").strip() for field in (sheet.get("fields") or [])]
    row_count = len(sheet.get("rows") or [])
    if not fields or row_count <= 0:
        return serialized

    meta = _workflow_meta(workflow)
    facility_id = _normalize_id(meta.get("facility_id") or order.facility_code)
    template_id = _normalize_id(meta.get("template_id"))
    if not facility_id:
        return serialized

    try:
        assignment = _get_order_service_module()._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
            order_id=order.id,
            facility_id=facility_id,
            template_id=template_id or None,
            payload=evidence_payload,
        )
    except Exception:
        return serialized
    if not isinstance(assignment, dict):
        return serialized

    target_cells = list(assignment.get("target_cells") or [])
    aligned_sheet = _align_hakodate_sheet_payload_for_workflow(
        _get_order_service_module(),
        sheet,
        target_cells,
    )
    if aligned_sheet is not sheet:
        sheet = aligned_sheet
        fields = [str(field or "").strip() for field in (sheet.get("fields") or [])]
        row_count = len(sheet.get("rows") or [])

    target_cell_map = _compact_target_cell_map_for_sheet(
        target_cells=target_cells,
        fields=fields,
        row_count=row_count,
    )
    if target_cell_map:
        enriched_sheet = dict(sheet)
        enriched_sheet["target_cell_map"] = target_cell_map
        serialized["sheet"] = enriched_sheet
    return serialized


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


def _build_order_payload_for_outputs(*, order: Order, lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": order.id,
        "facility": order.facility_code,
        "facility_code": order.facility_code,
        "week": order.week_code,
        "week_code": order.week_code,
        "stored_week_value": order.week_code,
        "received_at": order.received_at.isoformat() if order.received_at else None,
        "lines": lines,
    }


def _build_basic_bag_rows_for_candidate(*, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        if _is_excluded_aggregation_diet(line.get("diet_type")):
            continue
        numeric = _numeric_value(line.get("quantity_corrected"))
        if numeric is None:
            numeric = _numeric_value(line.get("quantity_original"))
        if numeric is None:
            continue
        rows.append(
            {
                "date": line.get("date"),
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name") or "",
                "menu_category": line.get("menu_category"),
                "diet_type": line.get("diet_type"),
                "area_id": line.get("area_id"),
                "bag_type": line.get("bag_type") or "standard",
                "quantity": numeric,
            }
        )
    rows.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("daypart") or ""),
            str(row.get("menu_name") or ""),
            str(row.get("diet_type") or ""),
            str(row.get("area_id") or ""),
            str(row.get("bag_type") or ""),
        )
    )
    return rows


def _build_bag_rows_for_candidate(*, order: Order, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order_payload = _build_order_payload_for_outputs(order=order, lines=lines)
    try:
        from src.services import output_builder  # noqa: PLC0415

        enriched_lines = output_builder.build_order_lines_for_outputs(order_payload)
        return output_builder.build_bag_payload_for_outputs(order_payload, order_lines=enriched_lines)
    except Exception:
        # Bagging preview must not block when optional menu/portion enrichment is unavailable.
        # The raw candidate still gives the operator a visible bagging result.
        return _build_basic_bag_rows_for_candidate(lines=lines)


def _build_bagging_result_payload(
    *,
    order: Order,
    saved_sheet: OrderSheetDraft,
    materialization_candidate: dict[str, Any],
) -> dict[str, Any]:
    lines = [
        line
        for line in (materialization_candidate.get("lines") or [])
        if isinstance(line, dict) and not _is_excluded_aggregation_diet(line.get("diet_type"))
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
    bag_rows = _build_bag_rows_for_candidate(order=order, lines=lines)
    return {
        "bagging_result_id": _new_id("OBG"),
        "order_id": order.id,
        "source_saved_sheet_id": saved_sheet.id,
        "source_ocr_result_id": saved_sheet.base_evidence_run_id,
        "template_version_id": saved_sheet.template_version_id,
        "status": "ready",
        "materialization_candidate": materialization_candidate,
        "summary": {
            "line_count": len(lines),
            "quantity_line_count": len(quantity_cells),
            "total_quantity": total_quantity,
            "bag_row_count": len(bag_rows),
        },
        "quantity_cells": quantity_cells,
        "bag_rows": bag_rows,
        "created_at": _now().isoformat(),
    }


def _is_excluded_aggregation_diet(diet_type: object) -> bool:
    normalized = str(diet_type or "").strip().lower()
    return normalized in {"placeholder", "unknown"}


def _materialization_candidate_has_excluded_lines(candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    for line in candidate.get("lines") or []:
        if isinstance(line, dict) and _is_excluded_aggregation_diet(line.get("diet_type")):
            return True
    return False


def _bagging_result_has_excluded_materialization_lines(bagging_result: object) -> bool:
    if not isinstance(bagging_result, dict):
        return False
    return _materialization_candidate_has_excluded_lines(bagging_result.get("materialization_candidate"))


def _build_output_bundle_payload(*, order: Order, bagging_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_bundle_id": _new_id("OOB"),
        "order_id": order.id,
        "source_bagging_result_id": bagging_result.get("bagging_result_id"),
        "source_saved_sheet_id": bagging_result.get("source_saved_sheet_id"),
        "source_ocr_result_id": bagging_result.get("source_ocr_result_id"),
        "template_version_id": bagging_result.get("template_version_id"),
        "status": "review_ready",
        "artifacts": [],
        "created_at": _now().isoformat(),
    }


def _order_line_digest(line: OrderLine) -> str:
    payload = {
        "date": line.date.isoformat() if line.date else None,
        "daypart": line.daypart,
        "menu_name": line.menu_name,
        "diet_type": line.diet_type,
        "area_id": line.area_id,
        "bag_type": line.bag_type,
        "quantity_original": line.quantity_original,
        "quantity_corrected": line.quantity_corrected,
        "change_note": line.change_note,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _serialize_materialized_order_line(line: OrderLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "line_id": line.line_id,
        "date": line.date.isoformat() if line.date else None,
        "daypart": line.daypart,
        "menu_name": line.menu_name,
        "diet_type": line.diet_type,
        "area_id": line.area_id,
        "bag_type": line.bag_type,
        "quantity_original": line.quantity_original,
        "quantity_corrected": line.quantity_corrected,
        "change_note": line.change_note,
        "line_digest": line.line_digest,
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
        workflow = _get_workflow(session, order.id)
        if workflow is None:
            return None, "workflow_not_initialized"
        if not workflow.draft_id:
            return None, "saved_sheet_missing"
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if draft is None or draft.order_id != order.id:
            return None, "saved_sheet_missing"
        workflow_template_version_id = _normalize_id(workflow.template_version_id) or _normalize_id(
            _workflow_meta(workflow).get("template_version_id")
        )
        draft_template_version_id = _normalize_id(draft.template_version_id)
        if workflow_template_version_id and draft_template_version_id != workflow_template_version_id:
            return None, "saved_sheet_template_mismatch"
        return _serialize_saved_sheet(draft), None


def build_sheet_from_selected_ocr(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_workflow(session, order.id)
        if workflow is None:
            return None, "workflow_not_initialized"
        if not workflow.evidence_run_id:
            return None, "selected_ocr_required"
        evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id)
        if evidence is None or evidence.order_id != order.id:
            return None, "selected_ocr_missing"
        payload = evidence.payload_json if isinstance(evidence.payload_json, dict) else None
        meta = _workflow_meta(workflow)
        context_error = _workflow_v2_projection_context_error(meta)
        if context_error:
            return None, context_error
        workflow_template_version_id, template_error = _require_existing_workflow_template_version(
            session,
            order=order,
            workflow=workflow,
            meta=meta,
        )
        if template_error:
            return None, template_error
        template_version_id, template_error = _resolve_evidence_template_version(
            evidence,
            workflow,
            meta,
            required_template_version_id=workflow_template_version_id,
        )
        if template_error:
            return None, template_error
        facility_id = _normalize_id(meta.get("facility_id"))
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
    target_cells = list(assignment.get("target_cells") or [])
    base_sheet = _align_hakodate_sheet_payload_for_workflow(
        order_service_module,
        base_sheet,
        target_cells,
    )
    projected = order_service_module._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config=_facility_config_with_expanded_cell_mode(
            config_service.get_facility_config(facility_id),
            meta.get("expanded_cell_copy_mode"),
        ),
        week_sheet_name=None,
    )
    projected["source"] = "workflow_v2_selected_ocr_projection"
    projected["base_evidence_run_id"] = selected_ocr_result_id
    projected["template_version_id"] = template_version_id or None
    projected["selected_ocr_result_id"] = selected_ocr_result_id
    projected["template_id"] = template_id or projected.get("template_id")
    projected["expanded_cell_copy_mode"] = _normalize_expanded_cell_copy_mode(meta.get("expanded_cell_copy_mode"))
    target_cell_map = _compact_target_cell_map_for_sheet(
        target_cells=target_cells,
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
        workflow_template_version_id = _normalize_id(workflow.template_version_id) or _normalize_id(_workflow_meta(workflow).get("template_version_id"))
        draft_template_version_id = _normalize_id(draft.template_version_id)
        if workflow_template_version_id and draft_template_version_id != workflow_template_version_id:
            return None, "saved_sheet_template_mismatch"
        materialization_candidate = _build_materialization_candidate_for_saved_sheet(order=order, saved_sheet=draft)
        if not isinstance(materialization_candidate, dict):
            return None, "saved_sheet_materialization_failed"
        materialization_error = _normalize_id(materialization_candidate.get("error"))
        if materialization_error:
            return None, materialization_error
        bagging_result = _build_bagging_result_payload(
            order=order,
            saved_sheet=draft,
            materialization_candidate=materialization_candidate,
        )
        meta = _workflow_meta(workflow)
        meta["bagging_result_id"] = bagging_result["bagging_result_id"]
        meta["bagging_result"] = bagging_result
        meta["anomaly_review_id"] = None
        meta["anomaly_review"] = None
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


def run_sheet_anomaly_review(
    order_id: str,
    *,
    sheet: dict[str, Any] | None = None,
    model: str | None = None,
    use_llm: bool | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        review_sheet = sheet if isinstance(sheet, dict) else None
        if review_sheet is None and not workflow.draft_id:
            return None, "saved_sheet_required"
        draft = session.get(OrderSheetDraft, workflow.draft_id) if workflow.draft_id else None
        source_saved_sheet_id = None
        if review_sheet is None:
            if draft is None or draft.order_id != order.id:
                return None, "saved_sheet_missing"
            workflow_template_version_id = _normalize_id(workflow.template_version_id) or _normalize_id(_workflow_meta(workflow).get("template_version_id"))
            draft_template_version_id = _normalize_id(draft.template_version_id)
            if workflow_template_version_id and draft_template_version_id != workflow_template_version_id:
                return None, "saved_sheet_template_mismatch"
            review_sheet = draft.draft_sheet_json if isinstance(draft.draft_sheet_json, dict) else {}
            source_saved_sheet_id = draft.id
        anomaly_review = workflow_v2_sheet_review_service.build_sheet_anomaly_report(
            sheet=review_sheet,
            evidence_payload=None,
            model=model,
            use_llm=use_llm,
        )
        anomaly_review_id = _new_id("OAR")
        anomaly_review["anomaly_review_id"] = anomaly_review_id
        anomaly_review["source_saved_sheet_id"] = source_saved_sheet_id
        anomaly_review["source"] = "unsaved_sheet" if sheet is not None else "saved_sheet"
        anomaly_review["source_bagging_result_id"] = None
        if sheet is None:
            meta = _workflow_meta(workflow)
            meta["anomaly_review_id"] = anomaly_review_id
            meta["anomaly_review"] = anomaly_review
            _write_workflow_meta(workflow, meta)
        return {
            "workflow": _serialize_workflow(workflow),
            "anomaly_review": anomaly_review,
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
        if _bagging_result_has_excluded_materialization_lines(bagging_result):
            return None, "bagging_result_stale_template_columns"
        workflow_template_version_id = _normalize_id(workflow.template_version_id) or _normalize_id(_workflow_meta(workflow).get("template_version_id"))
        bagging_template_version_id = _normalize_id(bagging_result.get("template_version_id"))
        if workflow_template_version_id and bagging_template_version_id != workflow_template_version_id:
            return None, "bagging_result_template_mismatch"
        output_bundle = _build_output_bundle_payload(order=order, bagging_result=bagging_result)
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


def prepare_output_review(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order, error = _get_order_or_error(session, order_id)
        if error:
            return None, error
        workflow = _get_or_create_workflow(session, order.id)
        bagging_result = _current_bagging_result(workflow)
        if not bagging_result:
            return None, "bagging_result_required"
        if _bagging_result_has_excluded_materialization_lines(bagging_result):
            return None, "bagging_result_stale_template_columns"
        workflow_template_version_id = _normalize_id(workflow.template_version_id) or _normalize_id(_workflow_meta(workflow).get("template_version_id"))
        bagging_template_version_id = _normalize_id(bagging_result.get("template_version_id"))
        if workflow_template_version_id and bagging_template_version_id != workflow_template_version_id:
            return None, "bagging_result_template_mismatch"
        output_bundle = _build_output_bundle_payload(order=order, bagging_result=bagging_result)
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
        if _bagging_result_has_excluded_materialization_lines(bagging_result):
            return None, "bagging_result_stale_template_columns"
        if not workflow.draft_id:
            return None, "saved_sheet_required"
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if draft is None or draft.order_id != order.id:
            return None, "saved_sheet_missing"
        workflow_template_version_id = _normalize_id(workflow.template_version_id) or _normalize_id(meta.get("template_version_id"))
        draft_template_version_id = _normalize_id(draft.template_version_id)
        if workflow_template_version_id and draft_template_version_id != workflow_template_version_id:
            return None, "saved_sheet_template_mismatch"
        bagging_template_version_id = _normalize_id(bagging_result.get("template_version_id")) if isinstance(bagging_result, dict) else None
        output_template_version_id = _normalize_id(output_bundle.get("template_version_id")) if isinstance(output_bundle, dict) else None
        output_source_bagging_result_id = _normalize_id(output_bundle.get("source_bagging_result_id")) if isinstance(output_bundle, dict) else None
        bagging_result_id = _normalize_id(bagging_result.get("bagging_result_id")) if isinstance(bagging_result, dict) else None
        output_source_saved_sheet_id = _normalize_id(output_bundle.get("source_saved_sheet_id")) if isinstance(output_bundle, dict) else None
        bagging_source_saved_sheet_id = _normalize_id(bagging_result.get("source_saved_sheet_id")) if isinstance(bagging_result, dict) else None
        if workflow_template_version_id and bagging_template_version_id != workflow_template_version_id:
            return None, "bagging_result_template_mismatch"
        if workflow_template_version_id and output_template_version_id != workflow_template_version_id:
            return None, "output_bundle_template_mismatch"
        if not bagging_result_id or output_source_bagging_result_id != bagging_result_id:
            return None, "output_bundle_source_mismatch"
        if output_source_saved_sheet_id and output_source_saved_sheet_id != draft.id:
            return None, "output_bundle_source_mismatch"
        if bagging_source_saved_sheet_id and bagging_source_saved_sheet_id != draft.id:
            return None, "bagging_result_source_mismatch"
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
        session.flush()
        materialized_lines = (
            session.query(OrderLine)
            .filter(OrderLine.order_id == order.id)
            .order_by(OrderLine.date, OrderLine.daypart, OrderLine.menu_name, OrderLine.id)
            .all()
        )
        for line in materialized_lines:
            line.line_digest = _order_line_digest(line)
        order.status = "確定"
        invalidate_orders_cache = getattr(order_service_module, "_invalidate_orders_cache", None)
        if callable(invalidate_orders_cache):
            invalidate_orders_cache()
        snapshot_json = {
            "source": "workflow_v2",
            "order_id": order.id,
            "template_version_id": draft_template_version_id or workflow_template_version_id or None,
            "selected_ocr_result_id": workflow.evidence_run_id,
            "saved_sheet_id": draft.id,
            "saved_sheet": draft.draft_sheet_json if isinstance(draft.draft_sheet_json, dict) else {},
            "bagging_result": bagging_result,
            "output_bundle": output_bundle,
            "materialization_candidate": materialization_candidate,
            "order_lines": [_serialize_materialized_order_line(line) for line in materialized_lines],
        }
        digest = hashlib.sha256(
            json.dumps(snapshot_json, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        snapshot = OrderConfirmedSnapshot(
            id=_new_id("OCS"),
            order_id=order.id,
            template_version_id=draft_template_version_id or workflow_template_version_id or None,
            draft_id=draft.id,
            snapshot_digest=digest,
            snapshot_json=snapshot_json,
            confirmed_by=_normalize_id(confirmed_by) or None,
            confirmed_at=_now(),
            created_at=_now(),
        )
        session.add(snapshot)
        session.flush()
        for line in materialized_lines:
            line.confirmed_snapshot_id = snapshot.id
        output_bundle = {**output_bundle, "confirmed_snapshot_id": snapshot.id}
        meta["output_bundle"] = output_bundle
        _write_workflow_meta(workflow, meta)
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
        workflow = _get_workflow(session, order.id)
        ocr_results = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order.id)
            .order_by(OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc())
            .all()
        )
        saved_sheet = None
        if workflow is not None and workflow.draft_id:
            draft = session.get(OrderSheetDraft, workflow.draft_id)
            if draft is not None and draft.order_id == order.id:
                evidence_payload = None
                evidence_id = _normalize_id(draft.base_evidence_run_id or workflow.evidence_run_id)
                if evidence_id:
                    evidence = session.get(OrderOcrEvidenceRun, evidence_id)
                    if evidence is not None and evidence.order_id == order.id and isinstance(evidence.payload_json, dict):
                        evidence_payload = evidence.payload_json
                saved_sheet = _saved_sheet_with_target_cell_map(
                    order=order,
                    workflow=workflow,
                    saved_sheet=draft,
                    evidence_payload=evidence_payload,
                )
        return {
            "order_id": order.id,
            "source": "workflow_v2_inspection",
            "workflow": (
                _serialize_workflow_checked(session, order=order, workflow=workflow)
                if workflow is not None
                else _serialize_uninitialized_workflow(order)
            ),
            "ocr_results": [
                _serialize_ocr_result(
                    row,
                    selected=row.id == (workflow.evidence_run_id if workflow is not None else None),
                )
                for row in ocr_results
            ],
            "saved_sheet": saved_sheet,
            "artifact_lineage": {
                "selected_ocr_result_id": workflow.evidence_run_id if workflow is not None else None,
                "saved_sheet_id": workflow.draft_id if workflow is not None else None,
                "confirmed_snapshot_id": workflow.confirmed_snapshot_id if workflow is not None else None,
                "bagging_result_id": (
                    _workflow_meta(workflow).get("bagging_result_id") if workflow is not None else None
                ),
                "anomaly_review_id": (
                    _workflow_meta(workflow).get("anomaly_review_id") if workflow is not None else None
                ),
                "output_bundle_id": (
                    _workflow_meta(workflow).get("output_bundle_id") if workflow is not None else None
                ),
            },
            "bagging_result": _workflow_meta(workflow).get("bagging_result") if workflow is not None else None,
            "anomaly_review": _workflow_meta(workflow).get("anomaly_review") if workflow is not None else None,
            "output_bundle": _workflow_meta(workflow).get("output_bundle") if workflow is not None else None,
        }, None
