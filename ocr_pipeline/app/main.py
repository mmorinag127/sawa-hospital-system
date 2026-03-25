from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Tuple

import cv2
from flask import Flask, jsonify, request
from google.cloud import firestore, storage

from app.issue_detection import detect_table_cell_issues, merge_cell_issues
from app.page_correction import correct_pdf_for_yomitoku
from app.pdf_render import render_pdf_to_page_images, render_pdf_to_png_bytes
from app.postprocess import _tesseract_digits_text, postprocess_and_retry
from app.preprocess import build_images_for_match_and_ocr
from app.quantity_subgrid import build_quantity_subgrid_second_passes
from app.evidence_manifest import ensure_evidence_manifest
from app.rois import crop_rois, load_template_config
from app.template_match import choose_template_and_warp
from app.template_resolution import build_template_resolution
from app.yomitoku_runner import ocr_image_text, ocr_image_words, run_yomitoku

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("gunicorn.error").setLevel(logging.INFO)
logging.getLogger("gunicorn.access").setLevel(logging.INFO)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

PROJECT_ID = (
    os.environ.get("GCP_PROJECT")
    or os.environ.get("GCP_PROJECT_ID")
    or os.environ.get("GOOGLE_CLOUD_PROJECT")
    or ""
)
JOB_COLLECTION = os.environ.get("JOB_COLLECTION", "jobs")
INPUT_PREFIX = os.environ.get("OCR_INPUT_PREFIX", "input/")
OUTPUT_PREFIX = os.environ.get("OCR_OUTPUT_PREFIX", "output/")

YOMITOKU_DEVICE = os.environ.get("OCR_YOMITOKU_DEVICE", "cpu").strip()
YOMITOKU_DPI = int(os.environ.get("OCR_YOMITOKU_DPI", "200"))
YOMITOKU_VIS = os.environ.get("OCR_YOMITOKU_VIS", "true").lower() == "true"
YOMITOKU_VIS_PDF = os.environ.get("OCR_YOMITOKU_VIS_PDF", "false").lower() == "true"
YOMITOKU_IGNORE_LINE_BREAK = (
    os.environ.get("OCR_YOMITOKU_IGNORE_LINE_BREAK", "false").lower() == "true"
)
YOMITOKU_NO_FIGURE = os.environ.get("OCR_YOMITOKU_NO_FIGURE", "false").lower() == "true"
YOMITOKU_FIGURE_WIDTH = int(os.environ.get("OCR_YOMITOKU_FIGURE_WIDTH", "200"))
YOMITOKU_FIGURE_DIR = os.environ.get("OCR_YOMITOKU_FIGURE_DIR", "figures")
OCR_PIPELINE_MODE = os.environ.get("OCR_PIPELINE_MODE", "structured_v2").strip().lower()
OCR_ENABLE_TEMPLATE_MATCH = os.environ.get("OCR_ENABLE_TEMPLATE_MATCH", "true").lower() == "true"
OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY = (
    os.environ.get("OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY", "false").lower() == "true"
)
OCR_TEMPLATE_MATCH_DPI = int(os.environ.get("OCR_TEMPLATE_MATCH_DPI", "300"))
OCR_ENABLE_TEMPLATE_ROI = os.environ.get("OCR_ENABLE_TEMPLATE_ROI", "true").lower() == "true"
OCR_ENABLE_PAGE_CORRECTION = os.environ.get("OCR_ENABLE_PAGE_CORRECTION", "true").lower() == "true"
OCR_ENABLE_QUANTITY_SUBGRID_SECOND_PASS = (
    os.environ.get("OCR_ENABLE_QUANTITY_SUBGRID_SECOND_PASS", "true").lower() == "true"
)
OCR_QUANTITY_SUBGRID_MAX_PASSES = int(os.environ.get("OCR_QUANTITY_SUBGRID_MAX_PASSES", "1"))
OCR_TEMPLATE_ROI_TEXT_OCR_ENGINE = (
    os.environ.get("OCR_TEMPLATE_ROI_TEXT_OCR_ENGINE", "skip").strip().lower()
)

db = firestore.Client(project=PROJECT_ID or None)
gcs = storage.Client(project=PROJECT_ID or None)

FIGURE_REGEX = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"


def _unwrap_pubsub_push_payload(payload: dict) -> dict:
    message = payload.get("message")
    if not isinstance(message, dict):
        return payload
    encoded_data = message.get("data")
    if not isinstance(encoded_data, str) or not encoded_data:
        raise ValueError("Invalid Pub/Sub push payload: missing message.data")
    try:
        decoded = base64.b64decode(encoded_data)
        decoded_payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid Pub/Sub push payload: cannot decode message.data") from exc
    if not isinstance(decoded_payload, dict):
        raise ValueError("Invalid Pub/Sub push payload: decoded body is not an object")
    return decoded_payload


def parse_gcs_event(payload: dict) -> Tuple[str, str, str]:
    data = payload.get("data") or payload
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        data = _unwrap_pubsub_push_payload(data)
    bucket = data.get("bucket") or data.get("bucketId")
    name = data.get("name") or data.get("object") or data.get("objectId")
    generation = str(data.get("generation") or data.get("metageneration") or "")
    if not bucket or not name:
        raise ValueError(f"Invalid event payload keys={list((data or {}).keys())}")
    return bucket, name, generation


def _safe_job_id(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", job_id)


def _update_job_stage(job_ref, stage: str, **extra) -> None:
    payload = {"stage": stage, "updated_at": firestore.SERVER_TIMESTAMP}
    payload.update(extra)
    job_ref.update(payload)
    app.logger.info("OCR pipeline stage: %s job=%s", stage, getattr(job_ref, "id", "unknown"))


def _encode_png(image_bgr) -> bytes:
    ok, png = cv2.imencode(".png", image_bgr)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return png.tobytes()


def _upload_bytes(bucket: str, object_name: str, data: bytes, content_type: str) -> str:
    gcs.bucket(bucket).blob(object_name).upload_from_string(data, content_type=content_type)
    return f"gs://{bucket}/{object_name}"


def _upload_corrected_pdf_artifact(
    *,
    bucket: str,
    artifact_prefix: str,
    base: str,
    original_pdf_bytes: bytes,
    corrected_pdf_bytes: bytes,
    page_correction_summary: dict | None,
) -> str | None:
    if not isinstance(page_correction_summary, dict):
        return None
    if not page_correction_summary.get("applied"):
        return None
    if not page_correction_summary.get("corrected_pdf_generated"):
        return None
    if not corrected_pdf_bytes or corrected_pdf_bytes == original_pdf_bytes:
        return None
    corrected_object_name = f"{artifact_prefix}{base}_corrected.pdf"
    return _upload_bytes(
        bucket,
        corrected_object_name,
        corrected_pdf_bytes,
        "application/pdf",
    )


def _upload_text(bucket: str, object_name: str, text: str, content_type: str) -> str:
    gcs.bucket(bucket).blob(object_name).upload_from_string(text, content_type=content_type)
    return f"gs://{bucket}/{object_name}"


def _write_output_partial(
    *,
    bucket: str,
    object_name: str,
    job_id: str,
    status: str,
    stage: str,
    input_reference: str,
    output_reference: str,
    payload: dict | None = None,
) -> None:
    body = {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "input_reference": input_reference,
        "output_reference": output_reference,
    }
    if payload:
        body.update(payload)
    gcs.bucket(bucket).blob(object_name).upload_from_string(
        json.dumps(body, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )


def _extract_metadata(blob: storage.Blob) -> tuple[str | None, str | None, list[str]]:
    metadata = blob.metadata or {}
    facility_id = metadata.get("facility_id")
    if isinstance(facility_id, str):
        facility_id = facility_id.strip() or None
    template_id = metadata.get("template_id") or metadata.get("preferred_template_id")
    if isinstance(template_id, str):
        template_id = template_id.strip() or None
    template_ids_raw = (
        metadata.get("preferred_template_ids")
        or metadata.get("template_ids")
        or metadata.get("candidate_template_ids")
    )
    template_ids: list[str] = []
    if isinstance(template_ids_raw, str):
        text = template_ids_raw.strip()
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = [part.strip() for part in text.split(",") if part.strip()]
            if isinstance(parsed, list):
                template_ids = [str(item).strip() for item in parsed if str(item).strip()]
    if template_id and template_id not in template_ids:
        template_ids.insert(0, template_id)
    return facility_id, template_id, template_ids


def _replace_markdown_images(markdown_text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return markdown_text
    updated = markdown_text
    for old_path, new_path in replacements.items():
        updated = updated.replace(old_path, new_path)
    return updated


def _extract_markdown_images(markdown_text: str) -> list[str]:
    return [match.group(1) for match in FIGURE_REGEX.finditer(markdown_text or "")]


def _classification_score(diagnostics: dict | None, matched_template_id: str | None) -> float | None:
    if not isinstance(diagnostics, dict) or not matched_template_id:
        return None
    candidates = diagnostics.get("candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("id") or "").strip() != matched_template_id:
            continue
        try:
            return float(candidate.get("score"))
        except Exception:
            return None
    return None


def _normalize_roi_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except Exception:
            return str(value)
        return str(int(number)) if number.is_integer() else str(number)
    return str(value)


def _build_roi_overlay_rows(roi_result: dict[str, Any] | None, tpl_cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(roi_result, dict) or not isinstance(tpl_cfg, dict):
        return []
    if bool(roi_result.get("disable_overlay_rows")):
        return []
    qty = roi_result.get("qty")
    if not isinstance(qty, dict):
        return []
    row_order = roi_result.get("qty_row_order")
    if not isinstance(row_order, list) or not row_order:
        row_order = list(qty.keys())
    menu_band = str(roi_result.get("menu_band") or "")
    menu_lines = [line.strip() for line in menu_band.splitlines() if line.strip()]
    notes = str(roi_result.get("notes") or "").strip()
    row_fields = tpl_cfg.get("main_ocr_row_fields")
    if not isinstance(row_fields, list):
        row_fields = []

    overlay_rows: list[dict[str, Any]] = []
    for row_index, row_key in enumerate(row_order):
        row_qty = qty.get(row_key)
        if not isinstance(row_qty, dict):
            row_qty = {}
        row_payload: dict[str, Any] = {
            "row_index": row_index,
            "row_key": str(row_key),
            "source": "template_roi",
            "qty": dict(row_qty),
        }
        menu_value = menu_lines[row_index] if row_index < len(menu_lines) else ""
        if menu_value:
            row_payload["menu"] = menu_value
            row_payload["menu_name"] = menu_value
        for col_key, raw_value in row_qty.items():
            row_payload[f"qty.{col_key}"] = raw_value
        if notes and len(row_order) == 1:
            if "remarks" in row_fields:
                row_payload["remarks"] = notes
            elif "note" in row_fields:
                row_payload["note"] = notes
        overlay_rows.append(row_payload)
    return overlay_rows


def _build_roi_cell_issues(
    roi_result: dict[str, Any] | None,
    tpl_cfg: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(roi_result, dict) or not isinstance(tpl_cfg, dict):
        return []
    diagnostics = roi_result.get("qty_cell_diagnostics")
    if not isinstance(diagnostics, list):
        diagnostics = []
    failures = roi_result.get("failed_cells")
    if not isinstance(failures, list):
        failures = []
    failure_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        row_key = str(failure.get("row") or "").strip()
        col_key = str(failure.get("col") or "").strip()
        if not row_key or not col_key:
            continue
        failure_lookup[(row_key, col_key)] = dict(failure)

    issues: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        route = str(diagnostic.get("route") or "").strip()
        row_key = str(diagnostic.get("row") or "").strip()
        col_key = str(diagnostic.get("col") or "").strip()
        field = str(diagnostic.get("field") or f"qty.{col_key}").strip()
        if not row_key or not col_key or not field:
            continue
        failure = failure_lookup.get((row_key, col_key), {})
        reason = str(failure.get("reason") or "").strip()
        if route == "reject_low_confidence" or reason == "low_confidence":
            issue_code = "low_confidence"
        elif route == "reject_sanity_fail" or reason == "sanity_fail":
            issue_code = "sanity_fail"
        elif route.startswith("reject_"):
            issue_code = reason or "unreadable"
        else:
            issue_code = ""
        if not issue_code:
            continue
        try:
            row_index = int(diagnostic.get("row_index") if diagnostic.get("row_index") is not None else -1)
        except Exception:
            row_index = -1
        dedupe_key = (row_index, field, issue_code)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        issue: dict[str, Any] = {
            "row_index": row_index,
            "row_key": row_key,
            "col": col_key,
            "field": field,
            "issue_code": issue_code,
            "severity": "warning",
            "source": "template_roi",
            "route": route,
        }
        value = diagnostic.get("value")
        if value is not None:
            issue["value"] = value
        confidence = diagnostic.get("confidence")
        if confidence is not None:
            issue["confidence"] = confidence
        votes = diagnostic.get("votes")
        if votes is not None:
            issue["votes"] = votes
        max_allowed = diagnostic.get("max_allowed")
        if max_allowed is not None:
            issue["max_allowed"] = max_allowed
        raw_texts = diagnostic.get("raw_texts")
        if isinstance(raw_texts, list):
            issue["raw_texts"] = [str(text) for text in raw_texts if str(text).strip()][:5]
        if isinstance(failure, dict):
            if failure.get("raw"):
                issue["raw"] = str(failure.get("raw"))
            if failure.get("reason"):
                issue["reason"] = str(failure.get("reason"))
        issues.append(issue)
    return issues


def _count_non_empty_roi_cells(roi_result: dict[str, Any] | None) -> int:
    if not isinstance(roi_result, dict):
        return 0
    qty = roi_result.get("qty")
    if not isinstance(qty, dict):
        return 0
    count = 0
    for row in qty.values():
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if _normalize_roi_value(value) != "":
                count += 1
    return count


def _run_template_classification(
    *,
    pdf_bytes: bytes,
    requested_template_id: str | None,
    requested_template_ids: list[str] | None = None,
) -> tuple[dict | None, dict | None]:
    candidate_template_ids = [
        str(template_id).strip()
        for template_id in (requested_template_ids or [])
        if str(template_id).strip()
    ]
    if requested_template_id and requested_template_id not in candidate_template_ids:
        candidate_template_ids.insert(0, requested_template_id)
    should_discover = OCR_TEMPLATE_MATCH_ALLOW_DISCOVERY and not candidate_template_ids
    if not OCR_ENABLE_TEMPLATE_MATCH or (not candidate_template_ids and not should_discover):
        return None, None
    png_bytes = render_pdf_to_png_bytes(pdf_bytes, dpi=OCR_TEMPLATE_MATCH_DPI)
    img_match_bgr, img_ocr_bgr, img_alt_bgr = build_images_for_match_and_ocr(png_bytes)
    template_ids = candidate_template_ids or None
    matched_template_id = None
    warped_match = None
    warped_ocr = None
    warped_alt = None
    diagnostics: dict[str, Any] | None = None
    classification_mode = "preferred_only" if candidate_template_ids else "discovery"
    prefer_requested = bool(requested_template_id and candidate_template_ids)
    if prefer_requested:
        preferred_id = str(requested_template_id).strip()
        preferred_match_id, preferred_warped_match, preferred_warped_ocr, preferred_warped_alt, preferred_diagnostics = choose_template_and_warp(
            db,
            img_match_bgr,
            img_ocr_bgr,
            img_alt_bgr=img_alt_bgr,
            template_ids=[preferred_id],
        )
        if preferred_match_id == preferred_id:
            matched_template_id = preferred_match_id
            warped_match = preferred_warped_match
            warped_ocr = preferred_warped_ocr
            warped_alt = preferred_warped_alt
            diagnostics = preferred_diagnostics
            classification_mode = "preferred_primary"
    if matched_template_id is None:
        matched_template_id, warped_match, warped_ocr, warped_alt, diagnostics = choose_template_and_warp(
            db,
            img_match_bgr,
            img_ocr_bgr,
            img_alt_bgr=img_alt_bgr,
            template_ids=template_ids,
        )
        if prefer_requested:
            classification_mode = "preferred_fallback"
    confidence = _classification_score(diagnostics, matched_template_id)
    classification = {
        "requested_template_id": requested_template_id,
        "requested_template_ids": candidate_template_ids,
        "matched_template_id": matched_template_id,
        "mode": classification_mode,
        "confidence": confidence,
        "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
    }
    context = None
    if isinstance(matched_template_id, str) and matched_template_id.strip():
        tpl_cfg = load_template_config(db, matched_template_id.strip())
        context = {
            "template_id": matched_template_id.strip(),
            "template": tpl_cfg,
            "warped_match_bgr": warped_match,
            "warped_ocr_bgr": warped_ocr,
            "warped_alt_bgr": warped_alt,
        }
    return classification, context


def _run_template_roi_extraction(
    *,
    template_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not OCR_ENABLE_TEMPLATE_ROI or not isinstance(template_context, dict):
        return None
    tpl_cfg = template_context.get("template")
    warped_ocr_bgr = template_context.get("warped_ocr_bgr")
    if not isinstance(tpl_cfg, dict) or warped_ocr_bgr is None:
        return None
    ocr_words = ocr_image_words(warped_ocr_bgr, device=YOMITOKU_DEVICE)
    rois = crop_rois(
        warped_ocr_bgr,
        tpl_cfg,
        warped_alt_bgr=template_context.get("warped_alt_bgr"),
        ocr_words=ocr_words,
    )
    post = tpl_cfg.get("postprocess") if isinstance(tpl_cfg.get("postprocess"), dict) else {}
    qty_ocr_engine = str(post.get("qty_ocr_engine") or os.environ.get("OCR_QTY_OCR_ENGINE", "yomitoku")).strip().lower()
    text_ocr_engine = str(
        post.get("text_ocr_engine")
        or os.environ.get("OCR_TEMPLATE_ROI_TEXT_OCR_ENGINE", OCR_TEMPLATE_ROI_TEXT_OCR_ENGINE)
        or ""
    ).strip().lower()
    if not text_ocr_engine:
        text_ocr_engine = "skip" if qty_ocr_engine == "tesseract_digits" else "yomitoku"

    def _ocr_fn(image_bgr, _prompt: str, _max_tokens: int) -> str:
        if _max_tokens <= 32 and qty_ocr_engine == "tesseract_digits":
            return _tesseract_digits_text(image_bgr)
        if _max_tokens > 32 and text_ocr_engine in {"skip", "none", "disabled", "off"}:
            return ""
        if _max_tokens > 32 and text_ocr_engine == "tesseract_digits":
            return _tesseract_digits_text(image_bgr)
        return ocr_image_text(image_bgr, device=YOMITOKU_DEVICE)

    roi_result = postprocess_and_retry(
        rois=rois,
        tpl_cfg=tpl_cfg,
        ocr_fn=_ocr_fn,
        base_prompt="",
    )
    roi_result["template_id"] = template_context.get("template_id") or tpl_cfg.get("id")
    roi_result["source"] = "template_roi"
    roi_result["page_index"] = 1
    roi_result["overlay_rows"] = _build_roi_overlay_rows(roi_result, tpl_cfg)
    roi_result["cell_issues"] = _build_roi_cell_issues(roi_result, tpl_cfg)
    return roi_result


@app.post("/")
def handler():
    event = request.get_json(force=True, silent=False) or {}
    app.logger.info("OCR pipeline request received")
    try:
        bucket, name, generation = parse_gcs_event(event)
    except Exception:  # noqa: BLE001
        app.logger.exception("OCR pipeline invalid event payload")
        return jsonify({"status": "error", "error": "invalid_event"}), 400

    input_prefix = _normalize_prefix(INPUT_PREFIX)
    output_prefix = _normalize_prefix(OUTPUT_PREFIX)
    app.logger.info("OCR pipeline event parsed bucket=%s name=%s generation=%s", bucket, name, generation)
    if input_prefix and not name.startswith(input_prefix):
        app.logger.info("OCR pipeline ignored prefix_mismatch name=%s", name)
        return jsonify({"status": "ignored", "reason": "prefix_mismatch", "name": name}), 200

    job_id = f"{bucket}:{name}:{generation}"
    safe_job_id = _safe_job_id(job_id)
    output_name = f"{output_prefix}{os.path.basename(name)}.json"
    input_reference = f"gs://{bucket}/{name}"
    output_reference = f"gs://{bucket}/{output_name}"
    job_ref = db.collection(JOB_COLLECTION).document(safe_job_id)
    if job_ref.get().exists:
        app.logger.info("OCR pipeline duplicate job=%s", safe_job_id)
        return jsonify({"status": "duplicate", "job_id": job_id}), 200

    job_ref.set(
        {
            "status": "running",
            "input": {"bucket": bucket, "name": name, "generation": generation},
            "job_id": job_id,
            "safe_job_id": safe_job_id,
            "output": {"bucket": bucket, "name": output_name},
        }
    )
    app.logger.info("OCR pipeline job created job=%s", safe_job_id)
    _write_output_partial(
        bucket=bucket,
        object_name=output_name,
        job_id=job_id,
        status="running",
        stage="received",
        input_reference=input_reference,
        output_reference=output_reference,
    )

    start_time = time.time()
    try:
        _update_job_stage(job_ref, "download")
        _write_output_partial(
            bucket=bucket,
            object_name=output_name,
            job_id=job_id,
            status="running",
            stage="download",
            input_reference=input_reference,
            output_reference=output_reference,
        )
        blob = gcs.bucket(bucket).blob(name)
        blob.reload()
        pdf_bytes = blob.download_as_bytes()
        facility_id, template_id, template_ids = _extract_metadata(blob)
        resolved_template_id = template_id
        app.logger.info(
            "OCR pipeline input downloaded job=%s bytes=%s facility_id=%s template_id=%s template_ids=%s",
            safe_job_id,
            len(pdf_bytes),
            facility_id,
            template_id,
            template_ids,
        )
        classification = None
        template_context = None
        roi_extraction = None
        page_correction_summary = {
            "enabled": OCR_ENABLE_PAGE_CORRECTION,
            "applied": False,
            "document_rotation_deg": 0,
            "applied_page_count": 0,
            "template_warp_page_count": 0,
            "deskew_page_count": 0,
            "position_normalized_page_count": 0,
            "corrected_page_count": 0,
            "corrected_pdf_generated": False,
            "corrected_pdf_changed": False,
            "corrected_pdf_byte_length": 0,
            "corrected_pdf_uploaded": False,
            "corrected_pdf_uri": None,
            "pages": [],
        }
        classification_warnings: list[str] = []
        try:
            classification, template_context = _run_template_classification(
                pdf_bytes=pdf_bytes,
                requested_template_id=template_id,
                requested_template_ids=template_ids,
            )
        except Exception as exc:  # noqa: BLE001
            classification_warnings.append("template_match_error")
            app.logger.warning(
                "OCR pipeline template match failed job=%s requested_template_id=%s error=%s",
                safe_job_id,
                template_id,
                str(exc),
            )
        if isinstance(classification, dict):
            matched_template_id = classification.get("matched_template_id")
            if not resolved_template_id and isinstance(matched_template_id, str) and matched_template_id.strip():
                resolved_template_id = matched_template_id.strip()
            if template_id and matched_template_id and template_id != matched_template_id:
                classification_warnings.append("template_match_mismatch")
            if template_id and not matched_template_id:
                classification_warnings.append("template_match_unresolved")
        if OCR_ENABLE_TEMPLATE_ROI and isinstance(template_context, dict):
            try:
                roi_extraction = _run_template_roi_extraction(template_context=template_context)
            except Exception as exc:  # noqa: BLE001
                classification_warnings.append("roi_extraction_error")
                app.logger.warning(
                    "OCR pipeline roi extraction failed job=%s template_id=%s error=%s",
                    safe_job_id,
                    resolved_template_id,
                    str(exc),
                )

        yomitoku_pdf_bytes = pdf_bytes
        yomitoku_page_images = None
        if OCR_ENABLE_PAGE_CORRECTION:
            try:
                yomitoku_pdf_bytes, page_correction_summary, yomitoku_page_images = correct_pdf_for_yomitoku(
                    pdf_bytes=pdf_bytes,
                    dpi=YOMITOKU_DPI,
                    db=db,
                    preferred_template_id=resolved_template_id,
                    preferred_template_ids=template_ids,
                )
            except Exception as exc:  # noqa: BLE001
                classification_warnings.append("page_correction_error")
                app.logger.warning(
                    "OCR pipeline page correction failed job=%s template_id=%s error=%s",
                    safe_job_id,
                    resolved_template_id,
                    str(exc),
                )
                yomitoku_pdf_bytes = pdf_bytes

        _update_job_stage(job_ref, "ocr", facility_id=facility_id, template_id=resolved_template_id)
        _write_output_partial(
            bucket=bucket,
            object_name=output_name,
            job_id=job_id,
            status="running",
            stage="ocr",
            input_reference=input_reference,
            output_reference=output_reference,
            payload={
                "facility_id": facility_id,
                "template_id": resolved_template_id,
                "engine": "yomitoku",
            },
        )

        page_results, ocr_pdf, layout_pdf = run_yomitoku(
            pdf_bytes=yomitoku_pdf_bytes,
            dpi=YOMITOKU_DPI,
            device=YOMITOKU_DEVICE,
            visualize=YOMITOKU_VIS,
            ignore_line_break=YOMITOKU_IGNORE_LINE_BREAK,
            no_figure=YOMITOKU_NO_FIGURE,
            figure_width=YOMITOKU_FIGURE_WIDTH,
            figure_dir=YOMITOKU_FIGURE_DIR,
            page_images=yomitoku_page_images,
        )

        _update_job_stage(job_ref, "upload")
        _write_output_partial(
            bucket=bucket,
            object_name=output_name,
            job_id=job_id,
            status="running",
            stage="upload",
            input_reference=input_reference,
            output_reference=output_reference,
            payload={
                "facility_id": facility_id,
                "template_id": resolved_template_id,
                "engine": "yomitoku",
            },
        )

        base = os.path.splitext(os.path.basename(name))[0]
        artifact_prefix = f"{output_prefix}{base}/"
        figure_prefix = f"{artifact_prefix}{YOMITOKU_FIGURE_DIR}/"
        corrected_pdf_uri = _upload_corrected_pdf_artifact(
            bucket=bucket,
            artifact_prefix=artifact_prefix,
            base=base,
            original_pdf_bytes=pdf_bytes,
            corrected_pdf_bytes=yomitoku_pdf_bytes,
            page_correction_summary=page_correction_summary,
        )
        page_correction_summary["corrected_pdf_uploaded"] = bool(corrected_pdf_uri)
        page_correction_summary["corrected_pdf_uri"] = corrected_pdf_uri or None
        page_correction_artifacts = {
            "corrected_pdf_uri": corrected_pdf_uri,
            "corrected_pdf_uploaded": bool(corrected_pdf_uri),
        }

        uploaded_figures: dict[str, str] = {}
        pages: list[dict] = []
        structured_tables: list[dict] = []
        quantity_subgrid_passes: list[dict[str, Any]] = []
        markdown_pages: list[str] = []
        overlay_count = 0
        figure_count = 0
        warnings: list[str] = []
        warnings.extend(classification_warnings)

        for page_result in page_results:
            page_index = page_result.page_index
            figure_map = {path.name: path for path in page_result.figure_paths}
            markdown_text = page_result.markdown_text
            replacements: dict[str, str] = {}
            for ref in _extract_markdown_images(markdown_text):
                ref_name = Path(ref).name
                if ref_name in uploaded_figures:
                    replacements[ref] = uploaded_figures[ref_name]
                    continue
                figure_path = figure_map.get(ref_name)
                if figure_path is None:
                    continue
                figure_bytes = figure_path.read_bytes()
                figure_obj = f"{figure_prefix}{ref_name}"
                figure_uri = _upload_bytes(bucket, figure_obj, figure_bytes, "image/png")
                uploaded_figures[ref_name] = figure_uri
                replacements[ref] = figure_uri
                figure_count += 1
            markdown_text = _replace_markdown_images(markdown_text, replacements)
            markdown_obj = f"{artifact_prefix}{base}_p{page_index}.md"
            markdown_uri = _upload_text(
                bucket,
                markdown_obj,
                markdown_text,
                "text/markdown; charset=utf-8",
            )

            ocr_uri = None
            if page_result.ocr_vis is not None:
                overlay_count += 1
                ocr_obj = f"{artifact_prefix}{base}_p{page_index}_ocr.png"
                ocr_uri = _upload_bytes(
                    bucket,
                    ocr_obj,
                    _encode_png(page_result.ocr_vis),
                    "image/png",
                )

            layout_uri = None
            if page_result.layout_vis is not None:
                overlay_count += 1
                layout_obj = f"{artifact_prefix}{base}_p{page_index}_layout.png"
                layout_uri = _upload_bytes(
                    bucket,
                    layout_obj,
                    _encode_png(page_result.layout_vis),
                    "image/png",
                )

            figure_uris = []
            for ref in _extract_markdown_images(markdown_text):
                ref_name = Path(ref).name
                if ref_name in uploaded_figures:
                    figure_uris.append(uploaded_figures[ref_name])
            markdown_pages.append(markdown_text)
            page_tables = [dict(table) for table in page_result.tables if isinstance(table, dict)]
            structured_tables.extend(page_tables)
            pages.append(
                {
                    "page_index": page_index,
                    "markdown_uri": markdown_uri,
                    "ocr_overlay_uri": ocr_uri,
                    "layout_overlay_uri": layout_uri,
                    "figure_uris": figure_uris,
                    "tables": page_tables,
                }
            )

        if OCR_ENABLE_QUANTITY_SUBGRID_SECOND_PASS and structured_tables:
            try:
                quantity_source_pages = yomitoku_page_images or render_pdf_to_page_images(
                    yomitoku_pdf_bytes,
                    YOMITOKU_DPI,
                )
                subgrid_pass_artifacts = build_quantity_subgrid_second_passes(
                    page_results=page_results,
                    page_images=quantity_source_pages,
                    dpi=YOMITOKU_DPI,
                    device=YOMITOKU_DEVICE,
                    visualize=YOMITOKU_VIS,
                    ignore_line_break=YOMITOKU_IGNORE_LINE_BREAK,
                    no_figure=YOMITOKU_NO_FIGURE,
                    figure_width=YOMITOKU_FIGURE_WIDTH,
                    figure_dir=YOMITOKU_FIGURE_DIR,
                    max_passes=OCR_QUANTITY_SUBGRID_MAX_PASSES,
                )
                for subgrid_pass in subgrid_pass_artifacts:
                    prefix = (
                        f"{artifact_prefix}{base}_p{subgrid_pass.page_index}"
                        f"_t{subgrid_pass.table_index + 1}_qtysubgrid"
                    )
                    crop_png_uri = _upload_bytes(
                        bucket,
                        f"{prefix}.png",
                        subgrid_pass.crop_png_bytes,
                        "image/png",
                    )
                    ocr_pdf_uri = None
                    if subgrid_pass.ocr_pdf:
                        ocr_pdf_uri = _upload_bytes(
                            bucket,
                            f"{prefix}_ocr.pdf",
                            subgrid_pass.ocr_pdf,
                            "application/pdf",
                        )
                    layout_pdf_uri = None
                    if subgrid_pass.layout_pdf:
                        layout_pdf_uri = _upload_bytes(
                            bucket,
                            f"{prefix}_layout.pdf",
                            subgrid_pass.layout_pdf,
                            "application/pdf",
                        )
                    quantity_subgrid_passes.append(
                        {
                            "page_index": subgrid_pass.page_index,
                            "table_index": subgrid_pass.table_index,
                            "body_start_row": subgrid_pass.spec.body_start_row,
                            "menu_col_index": subgrid_pass.spec.menu_col_index,
                            "quantity_start_col_index": subgrid_pass.spec.quantity_start_col_index,
                            "crop_box_norm": list(subgrid_pass.spec.crop_box_norm),
                            "row_count": subgrid_pass.spec.row_count,
                            "quantity_col_count": subgrid_pass.spec.quantity_col_count,
                            "crop_png_uri": crop_png_uri,
                            "ocr_pdf_uri": ocr_pdf_uri,
                            "layout_pdf_uri": layout_pdf_uri,
                            "table_raw": subgrid_pass.markdown_text,
                            "tables": subgrid_pass.tables,
                            "normalized_rows": subgrid_pass.normalized_rows,
                            "normalization_patches": subgrid_pass.normalization_patches,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append("quantity_subgrid_second_pass_error")
                app.logger.warning(
                    "OCR quantity-subgrid second pass failed job=%s template_id=%s error=%s",
                    safe_job_id,
                    resolved_template_id,
                    str(exc),
                )

        structured_cell_issues = detect_table_cell_issues(
            tables=structured_tables,
            template=template_context.get("template") if isinstance(template_context, dict) else None,
            template_id=resolved_template_id,
        )
        roi_cell_issues = (
            list(roi_extraction.get("cell_issues") or [])
            if isinstance(roi_extraction, dict)
            else []
        )
        cell_issues = merge_cell_issues(structured_cell_issues, roi_cell_issues)

        combined = {}
        if corrected_pdf_uri:
            combined["corrected_pdf"] = corrected_pdf_uri
        if YOMITOKU_VIS and YOMITOKU_VIS_PDF:
            if ocr_pdf:
                combined["ocr_pdf"] = _upload_bytes(
                    bucket,
                    f"{artifact_prefix}{base}_ocr.pdf",
                    ocr_pdf,
                    "application/pdf",
                )
            if layout_pdf:
                combined["layout_pdf"] = _upload_bytes(
                    bucket,
                    f"{artifact_prefix}{base}_layout.pdf",
                    layout_pdf,
                    "application/pdf",
                )

        elapsed = round(time.time() - start_time, 2)
        metrics = {
            "page_count": len(page_results),
            "dpi": YOMITOKU_DPI,
            "device": YOMITOKU_DEVICE,
            "visualize": YOMITOKU_VIS,
            "visualize_pdf": YOMITOKU_VIS_PDF and YOMITOKU_VIS,
            "overlay_count": overlay_count,
            "figure_count": figure_count,
            "table_count": len(structured_tables),
            "yomitoku_cell_issue_count": len(structured_cell_issues),
            "cell_issue_count": len(cell_issues),
            "roi_overlay_row_count": len(roi_extraction.get("overlay_rows") or [])
            if isinstance(roi_extraction, dict)
            else 0,
            "roi_failed_cell_count": len(roi_extraction.get("failed_cells") or [])
            if isinstance(roi_extraction, dict)
            else 0,
            "roi_non_empty_quantity_cells": _count_non_empty_roi_cells(roi_extraction),
            "roi_accepted_qty_cells": int(
                ((roi_extraction.get("metrics") or {}).get("accepted_qty_cells") or 0)
            )
            if isinstance(roi_extraction, dict)
            else 0,
            "roi_rejected_qty_cells": int(
                ((roi_extraction.get("metrics") or {}).get("rejected_qty_cells") or 0)
            )
            if isinstance(roi_extraction, dict)
            else 0,
            "roi_low_confidence_qty_cells": int(
                ((roi_extraction.get("metrics") or {}).get("low_confidence_qty_cells") or 0)
            )
            if isinstance(roi_extraction, dict)
            else 0,
            "roi_sanity_rejected_qty_cells": int(
                ((roi_extraction.get("metrics") or {}).get("sanity_rejected_qty_cells") or 0)
            )
            if isinstance(roi_extraction, dict)
            else 0,
            "page_correction_applied": bool(page_correction_summary.get("applied")),
            "page_correction_applied_page_count": int(page_correction_summary.get("applied_page_count") or 0),
            "page_correction_template_warp_page_count": int(
                page_correction_summary.get("template_warp_page_count") or 0
            ),
            "page_correction_deskew_page_count": int(
                page_correction_summary.get("deskew_page_count") or 0
            ),
            "page_correction_position_normalized_page_count": int(
                page_correction_summary.get("position_normalized_page_count") or 0
            ),
            "page_correction_corrected_page_count": int(page_correction_summary.get("corrected_page_count") or 0),
            "page_correction_corrected_pdf_generated": bool(
                page_correction_summary.get("corrected_pdf_generated")
            ),
            "page_correction_corrected_pdf_uploaded": bool(
                page_correction_summary.get("corrected_pdf_uploaded")
            ),
            "quantity_subgrid_second_pass_count": len(quantity_subgrid_passes),
            "quantity_subgrid_normalization_patch_count": sum(
                len(item.get("normalization_patches") or [])
                for item in quantity_subgrid_passes
                if isinstance(item, dict)
            ),
            "elapsed_sec": elapsed,
        }
        if OCR_PIPELINE_MODE == "structured_v2" and not structured_tables:
            warnings.append("structured_tables_missing")
        if OCR_ENABLE_TEMPLATE_ROI and isinstance(template_context, dict) and not isinstance(roi_extraction, dict):
            warnings.append("roi_extraction_missing")

        table_raw = "\n\n---\n\n".join(markdown_pages)
        output_payload = {
            "version": "2" if OCR_PIPELINE_MODE == "structured_v2" else "1",
            "job_id": job_id,
            "status": "done",
            "stage": "done",
            "engine": "yomitoku",
            "pipeline_mode": OCR_PIPELINE_MODE,
            "template_id": resolved_template_id,
            "facility_id": facility_id,
            "input_reference": input_reference,
            "output_reference": output_reference,
            "metrics": metrics,
            "pages": pages,
            "tables": structured_tables,
            "quantity_subgrid_passes": quantity_subgrid_passes,
            "combined": combined,
            "table_raw": table_raw,
            "cell_issues": cell_issues,
            "failed_cells": list(roi_extraction.get("failed_cells") or [])
            if isinstance(roi_extraction, dict)
            else [],
            "yomitoku_cell_issues": structured_cell_issues,
            "roi_extraction": roi_extraction,
            "roi_overlay_policy": "audit_only",
            "roi_overlay_rows": list(roi_extraction.get("overlay_rows") or [])
            if isinstance(roi_extraction, dict)
            else [],
            "roi_cell_issues": roi_cell_issues,
            "classification": classification,
            "classification_confidence": classification.get("confidence")
            if isinstance(classification, dict)
            else None,
            "page_correction": page_correction_summary,
            "page_correction_artifacts": page_correction_artifacts,
            "warnings": warnings,
        }
        output_payload["template_resolution"] = build_template_resolution(
            requested_template_id=template_id,
            requested_template_ids=template_ids,
            resolved_template_id=resolved_template_id,
            classification=classification if isinstance(classification, dict) else None,
            page_correction_summary=page_correction_summary,
        )
        output_payload = ensure_evidence_manifest(output_payload) or output_payload
        gcs.bucket(bucket).blob(output_name).upload_from_string(
            json.dumps(output_payload, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
        )

        job_ref.update(
            {
                "status": "done",
                "template_id": resolved_template_id,
                "metrics": metrics,
                "output": {"bucket": bucket, "name": output_name},
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )
        app.logger.info("OCR pipeline stage: done job=%s", safe_job_id)
        return (
            jsonify(
                {
                    "status": "done",
                    "job_id": job_id,
                    "template_id": resolved_template_id,
                    "input_reference": input_reference,
                    "output_reference": output_reference,
                }
            ),
            200,
        )
    except Exception as exc:  # noqa: BLE001
        job_ref.update(
            {"status": "failed", "error": repr(exc), "updated_at": firestore.SERVER_TIMESTAMP}
        )
        _write_output_partial(
            bucket=bucket,
            object_name=output_name,
            job_id=job_id,
            status="failed",
            stage="error",
            input_reference=input_reference,
            output_reference=output_reference,
            payload={"error": repr(exc)},
        )
        app.logger.exception("OCR pipeline failed job=%s", safe_job_id)
        raise
