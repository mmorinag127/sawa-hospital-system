import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Tuple

import cv2
from flask import Flask, jsonify, request
from google.cloud import firestore, storage

from app.yomitoku_runner import run_yomitoku

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

db = firestore.Client(project=PROJECT_ID or None)
gcs = storage.Client(project=PROJECT_ID or None)

FIGURE_REGEX = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"


def parse_gcs_event(payload: dict) -> Tuple[str, str, str]:
    data = payload.get("data") or payload
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


def _extract_metadata(blob: storage.Blob) -> tuple[str | None, str | None]:
    metadata = blob.metadata or {}
    facility_id = metadata.get("facility_id")
    if isinstance(facility_id, str):
        facility_id = facility_id.strip() or None
    template_id = metadata.get("template_id") or metadata.get("preferred_template_id")
    if isinstance(template_id, str):
        template_id = template_id.strip() or None
    return facility_id, template_id


def _replace_markdown_images(markdown_text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return markdown_text
    updated = markdown_text
    for old_path, new_path in replacements.items():
        updated = updated.replace(old_path, new_path)
    return updated


def _extract_markdown_images(markdown_text: str) -> list[str]:
    return [match.group(1) for match in FIGURE_REGEX.finditer(markdown_text or "")]


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
        facility_id, template_id = _extract_metadata(blob)
        app.logger.info(
            "OCR pipeline input downloaded job=%s bytes=%s facility_id=%s template_id=%s",
            safe_job_id,
            len(pdf_bytes),
            facility_id,
            template_id,
        )

        _update_job_stage(job_ref, "ocr", facility_id=facility_id, template_id=template_id)
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
                "template_id": template_id,
                "engine": "yomitoku",
            },
        )

        page_results, ocr_pdf, layout_pdf = run_yomitoku(
            pdf_bytes=pdf_bytes,
            dpi=YOMITOKU_DPI,
            device=YOMITOKU_DEVICE,
            visualize=YOMITOKU_VIS,
            ignore_line_break=YOMITOKU_IGNORE_LINE_BREAK,
            no_figure=YOMITOKU_NO_FIGURE,
            figure_width=YOMITOKU_FIGURE_WIDTH,
            figure_dir=YOMITOKU_FIGURE_DIR,
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
                "template_id": template_id,
                "engine": "yomitoku",
            },
        )

        base = os.path.splitext(os.path.basename(name))[0]
        artifact_prefix = f"{output_prefix}{base}/"
        figure_prefix = f"{artifact_prefix}{YOMITOKU_FIGURE_DIR}/"

        uploaded_figures: dict[str, str] = {}
        pages: list[dict] = []
        markdown_pages: list[str] = []
        overlay_count = 0
        figure_count = 0

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
            pages.append(
                {
                    "page_index": page_index,
                    "markdown_uri": markdown_uri,
                    "ocr_overlay_uri": ocr_uri,
                    "layout_overlay_uri": layout_uri,
                    "figure_uris": figure_uris,
                }
            )

        combined = {}
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
            "elapsed_sec": elapsed,
        }

        table_raw = "\n\n---\n\n".join(markdown_pages)
        output_payload = {
            "job_id": job_id,
            "status": "done",
            "stage": "done",
            "engine": "yomitoku",
            "template_id": template_id,
            "facility_id": facility_id,
            "input_reference": input_reference,
            "output_reference": output_reference,
            "metrics": metrics,
            "pages": pages,
            "combined": combined,
            "table_raw": table_raw,
            "failed_cells": [],
        }
        gcs.bucket(bucket).blob(output_name).upload_from_string(
            json.dumps(output_payload, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
        )

        job_ref.update(
            {
                "status": "done",
                "template_id": template_id,
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
                    "template_id": template_id,
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
