from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import sys
import threading
import time
from contextvars import ContextVar
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Tuple

import cv2
from flask import Flask, jsonify, request
from google.cloud import firestore, storage

from app.issue_detection import detect_table_cell_issues, merge_cell_issues
from app.page_correction import correct_pdf_for_yomitoku
from app.pdf_render import render_pdf_to_page_images, render_pdf_to_png_bytes
from app.postprocess import postprocess_and_retry
from app.preprocess import build_images_for_match_and_ocr
from app.quantity_subgrid import build_quantity_subgrid_second_passes
from app.evidence_manifest import ensure_evidence_manifest
from app.rois import crop_rois, load_template_config, validate_captured_config
from app.template_match import choose_template_and_warp
from app.template_resolution import build_template_resolution
from app.yomitoku_runner import ocr_image_text, ocr_image_words, prewarm_analyzer, run_yomitoku
from app.storage_uri import decorate_gcs_uri, format_gcs_uri, parse_gcs_uri, validate_generation, validate_source_query
from app import figure_markup


class MandatoryOcrStageFailed(RuntimeError):
    pass


class TerminalPersistenceIncomplete(RuntimeError):
    pass


class TerminalReceiptConflict(TerminalPersistenceIncomplete):
    pass


_ACTIVE_PRODUCER_JOBS: set[str] = set()
_ACTIVE_PRODUCER_LOCK = threading.Lock()
_OUTPUT_OWNER = ContextVar("pipeline_output_owner", default=None)


def _owned_output_name(output_prefix, source):
    identity = {key: value for key, value in source.items() if key != "output_reference"}
    return f"{output_prefix}{_terminal_digest(identity)}/{Path(parse_gcs_uri(source['input_reference']).object_name).name}.g{source['input_generation']}.json"


def _owned_output_context():
    context = _OUTPUT_OWNER.get()
    if context is None:
        raise TerminalReceiptConflict("output_original_owner_required")
    return context


def _assert_output_owner(job_ref, source):
    snapshot = job_ref.get()
    job = snapshot.to_dict() or {}
    if job.get("execution_claim") != {"job_id": source["job_id"], "output_reference": source["output_reference"]}:
        raise TerminalReceiptConflict("output_execution_claim_changed")
    return snapshot, job


def _terminal_digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _same_original_source(payload, source):
    return isinstance(payload, dict) and all(key in payload for key in source) and _terminal_digest({key: payload[key] for key in source}) == _terminal_digest(source)


def _original_pipeline_source(bucket, name, generation, *, blob=None, data=None):
    if blob is None or data is None:
        blob, data = _download_generation_input(bucket, name, generation)
    _verify_selected_pdf_input(blob, data)
    _, configured_digest = _load_configured_input(blob)
    owner = json.loads((blob.metadata or {}).get("execution_owner", "null"))
    if not isinstance(owner, dict) or not owner.get("job_id") or not owner.get("order_id") or (owner.get("workflow_attempt_id") is None) != (owner.get("workflow_revision") is None):
        raise TerminalReceiptConflict("terminal_execution_owner_missing")
    return {
        "job_id": f"{bucket}:{name}:{generation}",
        "input_reference": format_gcs_uri(bucket, name), "input_generation": str(generation),
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "configured_input_sha256": configured_digest,
        "configured_input_receipt": json.loads(blob.metadata["configured_input_receipt"]),
        "execution_owner": owner,
        "selected_pdf_receipt": json.loads(blob.metadata["selected_pdf_receipt"]),
    }


def _terminal_source(bucket, name, generation, output_name, *, blob=None, data=None):
    source = _original_pipeline_source(bucket, name, generation, blob=blob, data=data)
    source["output_reference"] = format_gcs_uri(bucket, output_name)
    return source


def _update_owned_terminal_job(job_ref, patch, receipt):
    snapshot = job_ref.get()
    existing = snapshot.to_dict() or {}
    source = receipt["source"]
    if existing.get("execution_claim") != {"job_id": source["job_id"], "output_reference": source["output_reference"]} or (existing.get("terminal_receipt") is not None and existing["terminal_receipt"] != receipt):
        raise TerminalReceiptConflict("terminal_store_owner_mismatch")
    job_ref.update(patch, option=db.write_option(last_update_time=snapshot.update_time))


def _persist_terminal_intent(job_ref, bucket, output_name, intent):
    """Repair stores only from immutable own intent; never recompute recognition."""
    from google.api_core.exceptions import PreconditionFailed, NotFound
    from urllib.parse import urlparse
    intent_blob = gcs.bucket(bucket).blob(output_name + ".terminal-intent.json")
    try:
        intent_blob.upload_from_string(json.dumps(intent, ensure_ascii=False, sort_keys=True), content_type="application/json", if_generation_match=0)
    except PreconditionFailed:
        stored = json.loads(intent_blob.download_as_bytes(timeout=30, retry=None))
        if stored != intent:
            raise TerminalReceiptConflict("terminal_intent_conflict")
    payload = intent["result"]
    receipt = {"source": intent["source"], "result_digest": intent["result_digest"],
               "status": payload["status"], "intent_object": output_name + ".terminal-intent.json"}
    existing = job_ref.get().to_dict() or {}
    if existing.get("execution_claim") != {"job_id": intent["source"]["job_id"], "output_reference": intent["source"]["output_reference"]} or (existing.get("terminal_receipt") is not None and existing["terminal_receipt"] != receipt):
        raise TerminalReceiptConflict("terminal_store_owner_mismatch")
    patch = {"status": payload["status"], "terminal_receipt": receipt, "updated_at": firestore.SERVER_TIMESTAMP}
    patch.update({key: payload[key] for key in ("metrics", "template_id", "error", "error_type") if key in payload})
    output = gcs.bucket(bucket).blob(output_name)
    try:
        output.reload()
        output_generation = int(output.generation)
        previous_output = json.loads(output.download_as_bytes(timeout=30, retry=None, if_generation_match=output_generation))
    except NotFound:
        previous_output = None
        output_generation = 0
    if previous_output is not None:
        source = intent["source"]
        if not _same_original_source(previous_output, source):
            raise TerminalReceiptConflict("terminal_output_owner_mismatch")
        if previous_output.get("status") in {"done", "failed"} and _terminal_digest(previous_output) != intent["result_digest"]:
            raise TerminalReceiptConflict("terminal_output_result_conflict")
    complete = previous_output is not None and _terminal_digest(previous_output) == intent["result_digest"] and existing.get("terminal_receipt") == receipt and existing.get("status") == payload["status"]
    if not complete:
        _update_owned_terminal_job(job_ref, {"status": "terminal_persisting", "terminal_receipt": receipt,
                                            "updated_at": firestore.SERVER_TIMESTAMP}, receipt)
        _update_owned_terminal_job(job_ref, patch, receipt)
        output.upload_from_string(json.dumps(payload, ensure_ascii=False), content_type="application/json; charset=utf-8", if_generation_match=output_generation)
        output_generation = int(output.generation)
    persisted = json.loads(output.download_as_bytes(timeout=30, retry=None, if_generation_match=output_generation))
    job = job_ref.get().to_dict()
    if job.get("execution_claim") != existing["execution_claim"]:
        raise TerminalReceiptConflict("terminal_claim_changed_before_ack")
    if _terminal_digest(persisted) != intent["result_digest"] or job.get("terminal_receipt") != receipt or job.get("status") != payload["status"]:
        raise TerminalPersistenceIncomplete("terminal_dual_store_incomplete")
    source = intent["source"]
    parsed = parse_gcs_uri(source["input_reference"])
    try:
        current_source = _terminal_source(bucket, parsed.object_name, source["input_generation"], output_name)
    except ValueError as exc:
        raise TerminalReceiptConflict("terminal_source_changed_before_ack") from exc
    if parsed.bucket != bucket or source != current_source:
        raise TerminalReceiptConflict("terminal_source_changed_before_ack")
    return payload


def _complete_terminal(job_ref, bucket, name, generation, output_name, source, payload):
    try:
        current_source = _terminal_source(bucket, name, generation, output_name)
    except ValueError as exc:
        raise TerminalReceiptConflict("terminal_source_changed") from exc
    if source != current_source:
        raise TerminalReceiptConflict("terminal_source_changed")
    if any(payload.get(key) != value for key, value in source.items()):
        raise TerminalReceiptConflict("terminal_result_identity_mismatch")
    intent = {"source": source, "result": payload, "result_digest": _terminal_digest(payload)}
    try:
        return _persist_terminal_intent(job_ref, bucket, output_name, intent)
    except TerminalReceiptConflict:
        raise
    except Exception as exc:
        raise TerminalPersistenceIncomplete("terminal_transport_repair_required") from exc


def _replay_terminal(job_ref, bucket, name, generation, output_name):
    from google.api_core.exceptions import NotFound
    try:
        intent = json.loads(gcs.bucket(bucket).blob(output_name + ".terminal-intent.json").download_as_bytes(timeout=30, retry=None))
    except NotFound:
        job = job_ref.get().to_dict() or {}
        expected = {"job_id": f"{bucket}:{name}:{generation}", "output_reference": format_gcs_uri(bucket, output_name)}
        with _ACTIVE_PRODUCER_LOCK:
            active = expected["job_id"] in _ACTIVE_PRODUCER_JOBS
        if active and job.get("status") == "running" and job.get("execution_claim") == expected:
            return {"status": "running", **expected}, 202
        return {"status": "needs_inspection", "error": "terminal_intent_missing"}, 409
    if not isinstance(intent, dict) or intent.get("source") != _terminal_source(bucket, name, generation, output_name) or not isinstance(intent.get("result"), dict) or intent.get("result_digest") != _terminal_digest(intent["result"]) or intent["result"].get("status") not in {"done", "failed"}:
        raise TerminalReceiptConflict("terminal_replay_receipt_mismatch")
    result = intent["result"]
    if any(result.get(key) != value for key, value in intent["source"].items()):
        raise TerminalReceiptConflict("terminal_replay_identity_mismatch")
    stored = job_ref.get().to_dict() or {}
    if stored.get("execution_claim") != {"job_id": intent["source"]["job_id"], "output_reference": intent["source"]["output_reference"]}:
        raise TerminalReceiptConflict("terminal_replay_owner_mismatch")
    return _persist_terminal_intent(job_ref, bucket, output_name, intent), 200


def _duplicate_outcome(job_ref, bucket, name, generation, output_name):
    from google.api_core.exceptions import NotFound, PreconditionFailed
    try:
        result, status = _replay_terminal(job_ref, bucket, name, generation, output_name)
        return (_terminal_ack(result) if status == 200 else result), status
    except (TerminalReceiptConflict, ValueError, NotFound, PreconditionFailed) as exc:
        return {"status": "needs_inspection", "error": str(exc)}, 409


def _terminal_ack(payload):
    source = {key: payload[key] for key in (
        "job_id", "input_reference", "input_generation", "input_sha256", "configured_input_sha256",
        "execution_owner", "selected_pdf_receipt", "configured_input_receipt", "output_reference",
    )}
    return {
        **{key: payload[key] for key in ("status", "job_id", "template_id", "input_reference", "input_generation", "input_sha256", "output_reference", "error") if key in payload},
        "terminal_receipt": {"source": source, "status": payload["status"], "result_digest": _terminal_digest(payload)},
    }

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
OCR_PREWARM_ANALYZER = os.environ.get("OCR_PREWARM_ANALYZER", "true").lower() == "true"

app.logger.info(
    "OCR pipeline runtime config gunicorn_timeout=%s gunicorn_cmd_args=%s prewarm_analyzer=%s",
    os.environ.get("GUNICORN_TIMEOUT", ""),
    os.environ.get("GUNICORN_CMD_ARGS", ""),
    OCR_PREWARM_ANALYZER,
)

db = firestore.Client(project=PROJECT_ID or None)
gcs = storage.Client(project=PROJECT_ID or None)

_PREWARM_STARTED = False
_PREWARM_LOCK = threading.Lock()


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"


def parse_gcs_event(payload: dict) -> Tuple[str, str, str]:
    data = payload.get("data") or payload
    bucket = data.get("bucket") or data.get("bucketId")
    name = data.get("name") or data.get("object") or data.get("objectId")
    generation = str(data.get("generation") or "")
    if not bucket or not name or not generation.isdigit():
        raise ValueError(f"Invalid event payload keys={list((data or {}).keys())}")
    return bucket, name, generation


def _safe_job_id(source: dict) -> str:
    identity = {key: value for key, value in source.items() if key != "output_reference"}
    return "ocr-" + _terminal_digest(identity)


def _update_job_stage(job_ref, stage: str, **extra) -> None:
    source, owner_ref = _owned_output_context()
    if owner_ref is not job_ref:
        raise TerminalReceiptConflict("output_execution_claim_changed")
    snapshot, job = _assert_output_owner(job_ref, source)
    if job.get("terminal_receipt") is not None:
        raise TerminalReceiptConflict("output_terminal_replay_required")
    payload = {"stage": stage, "updated_at": firestore.SERVER_TIMESTAMP}
    payload.update(extra)
    job_ref.update(payload, option=db.write_option(last_update_time=snapshot.update_time))
    app.logger.info("OCR pipeline stage: %s job=%s", stage, getattr(job_ref, "id", "unknown"))


def _encode_png(image_bgr) -> bytes:
    ok, png = cv2.imencode(".png", image_bgr)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return png.tobytes()


def _upload_bytes(bucket: str, object_name: str, data: bytes, content_type: str) -> str:
    from google.api_core.exceptions import PreconditionFailed
    source, job_ref = _owned_output_context()
    _assert_output_owner(job_ref, source)
    output = parse_gcs_uri(source["output_reference"])
    prefix = output.object_name.rsplit("/", 1)[0] + "/assets/"
    if output.bucket != bucket or not object_name.startswith(prefix):
        raise TerminalReceiptConflict("artifact_owner_namespace_mismatch")
    blob = gcs.bucket(bucket).blob(object_name)
    metadata = {"original_source": json.dumps(source, ensure_ascii=False, sort_keys=True), "sha256": hashlib.sha256(data).hexdigest()}
    blob.metadata = metadata
    try:
        blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
    except PreconditionFailed:
        blob.reload()
        existing = blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=30, retry=None)
        if blob.metadata != metadata or existing != data:
            raise TerminalReceiptConflict("immutable_artifact_conflict")
    if not str(blob.generation or "").isdigit():
        raise TerminalReceiptConflict("artifact_generation_required")
    _assert_output_owner(job_ref, source)
    return format_gcs_uri(bucket, object_name, blob.generation)


def _download_generation_input(bucket, name, generation):
    if not str(generation).isdigit():
        raise ValueError("input_generation_required")
    blob = gcs.bucket(bucket).blob(name, generation=int(generation))
    blob.reload()
    data = blob.download_as_bytes(if_generation_match=int(generation))
    _verify_selected_pdf_input(blob, data)
    return blob, data


def _verify_selected_pdf_input(blob, data):
    """URI correction for pending H2's selected-input fence (not a new receipt)."""
    import hashlib
    from urllib.parse import urlsplit

    raw = (blob.metadata or {}).get("selected_pdf_receipt")
    if not raw:
        raise ValueError("selected_pdf_start_receipt_required")
    receipt = json.loads(raw)
    if not isinstance(receipt, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or "")):
        raise ValueError("selected_pdf_digest_required")
    if hashlib.sha256(data).hexdigest() != receipt["sha256"]:
        raise ValueError("selected_pdf_bytes_changed")
    if urlsplit(receipt["storage_uri"]).scheme == "gs":
        source = parse_gcs_uri(receipt["storage_uri"])
        generation = receipt.get("generation")
        if generation is None and source.generation is None:
            original = gcs.bucket(source.bucket).blob(source.object_name)
            if hashlib.sha256(original.download_as_bytes(timeout=30, retry=None)).hexdigest() != receipt["sha256"]:
                raise ValueError("selected_pdf_bytes_changed")
            return
        generation = validate_generation(generation)
        if source.generation != generation:
            raise ValueError("selected_pdf_generation_required")
        original = gcs.bucket(source.bucket).blob(source.object_name)
        original.reload(timeout=30, retry=None)
        if str(original.generation) != generation:
            raise ValueError("selected_pdf_generation_changed")


def _load_configured_input(blob):
    receipt = json.loads((blob.metadata or {}).get("configured_input_receipt", "null"))
    if not isinstance(receipt, dict) or receipt.get("bucket") != blob.bucket.name or receipt.get("object_name") != blob.name + ".configured.json" or not str(receipt.get("generation", "")).isdigit():
        raise ValueError("captured_template_receipt_required")
    source = gcs.bucket(receipt["bucket"]).blob(receipt["object_name"], generation=int(receipt["generation"]))
    data = source.download_as_bytes(if_generation_match=int(receipt["generation"]), timeout=30, retry=None)
    if hashlib.sha256(data).hexdigest() != receipt.get("sha256"):
        raise ValueError("captured_template_receipt_mismatch")
    configured = json.loads(data)
    validate_captured_config(configured, receipt["sha256"])
    return configured, receipt["sha256"]


def _recognition_input_receipt(original_pdf_bytes, recognition_pdf_bytes, source_receipt, corrected_receipt):
    if not isinstance(source_receipt, dict) or source_receipt.get("sha256") != hashlib.sha256(original_pdf_bytes).hexdigest():
        raise ValueError("recognition_input_source_mismatch")
    if recognition_pdf_bytes == original_pdf_bytes:
        return {"representation": "original_identity", "receipt": dict(source_receipt)}
    if not isinstance(corrected_receipt, dict):
        raise ValueError("recognition_input_receipt_required")
    if corrected_receipt.get("source_sha256") != source_receipt["sha256"] or corrected_receipt.get("sha256") != hashlib.sha256(recognition_pdf_bytes).hexdigest():
        raise ValueError("recognition_input_source_mismatch")
    return {"representation": "corrected", "receipt": dict(corrected_receipt)}


def _upload_figure_artifact(bucket: str, object_name: str, figure) -> str:
    return _upload_bytes(bucket, object_name, figure.content, figure.mime_type)


def _publish_page_figures(bucket: str, figure_prefix: str, page_result) -> tuple[str, int, dict[str, str]]:
    from pathlib import PurePosixPath
    from urllib.parse import urlsplit, unquote

    def local_reference(ref, decode=True):
        if decode:
            parsed = urlsplit(ref)
            if parsed.scheme or parsed.netloc:
                return None
            if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
                raise ValueError("figure_reference_encoding_invalid")
            decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
        else:
            # FigureData.reference is the actual page-relative filesystem path,
            # not a URI. '?' / '#' / '%' here are literal key characters.
            decoded = ref
        path = PurePosixPath(decoded)
        if not decoded or path.is_absolute() or ".." in path.parts or "\\" in decoded or path.as_posix() != decoded:
            raise ValueError(f"invalid local figure reference on page {page_result.page_index}: {ref}")
        return decoded

    figure_map = {}
    for figure in page_result.figures:
        key = local_reference(figure.reference, False)
        if key is None:
            raise ValueError("exported figure requires an owned local reference")
        existing = figure_map.get(key)
        if existing is not None and (existing.content != figure.content or existing.mime_type != figure.mime_type):
            raise ValueError(f"ambiguous figure identity on page {page_result.page_index}: {figure.name}")
        figure_map[key] = figure
    refs = _extract_markdown_images(page_result.markdown_text)
    for ref in refs:
        key = local_reference(ref)
        if key is not None and key not in figure_map:
            raise ValueError(f"figure missing on page {page_result.page_index}: {ref}")
        if key is not None:
            validate_source_query(urlsplit(ref).query)
    uploaded = {}
    replacements = {}
    for ref in refs:
        key = local_reference(ref)
        if key is None:
            continue
        if key not in uploaded:
            uploaded[key] = _upload_figure_artifact(
                bucket, f"{figure_prefix}p{page_result.page_index}/{key}", figure_map[key]
            )
        replacements[ref] = decorate_gcs_uri(uploaded[key], ref)
    return _replace_markdown_images(page_result.markdown_text, replacements), len(uploaded), replacements


def _validate_figure_pages(page_results) -> None:
    seen = set()
    for page in page_results:
        if not isinstance(page.page_index, int) or isinstance(page.page_index, bool) or page.page_index < 1 or page.page_index in seen:
            raise ValueError(f"invalid or duplicate figure page identity: {page.page_index}")
        seen.add(page.page_index)


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
    uri = _upload_bytes(bucket, corrected_object_name, corrected_pdf_bytes, "application/pdf")
    from urllib.parse import parse_qs
    generation = validate_generation(parse_gcs_uri(uri).generation)
    if not generation.isdigit():
        raise ValueError("corrected_pdf_generation_missing")
    page_correction_summary["corrected_pdf_receipt"] = {
        "storage_uri": uri, "generation": generation,
        "sha256": hashlib.sha256(corrected_pdf_bytes).hexdigest(),
        "source_sha256": hashlib.sha256(original_pdf_bytes).hexdigest(),
    }
    return uri


def _upload_text(bucket: str, object_name: str, text: str, content_type: str) -> str:
    return _upload_bytes(bucket, object_name, text.encode("utf-8"), content_type)


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
    from google.api_core.exceptions import NotFound
    source, job_ref = _owned_output_context()
    _assert_output_owner(job_ref, source)
    if any(source[key] != value for key, value in {"job_id": job_id, "input_reference": input_reference, "output_reference": output_reference}.items()) or output_reference != format_gcs_uri(bucket, object_name):
        raise TerminalReceiptConflict("partial_output_identity_mismatch")
    body = {
        **source,
        "status": status,
        "stage": stage,
        "input_reference": input_reference,
        "input_generation": job_id.rsplit(":", 1)[1],
        "output_reference": output_reference,
    }
    if payload:
        body.update(payload)
    if not _same_original_source(body, source):
        raise TerminalReceiptConflict("partial_output_identity_mismatch")
    blob = gcs.bucket(bucket).blob(object_name)
    try:
        blob.reload()
        expected_generation = int(blob.generation)
        previous = json.loads(blob.download_as_bytes(if_generation_match=expected_generation, timeout=30, retry=None))
        if not _same_original_source(previous, source):
            raise TerminalReceiptConflict("partial_output_owner_mismatch")
        if previous.get("status") in {"done", "failed"}:
            raise TerminalReceiptConflict("output_terminal_replay_required")
    except NotFound:
        expected_generation = 0
    snapshot, job = _assert_output_owner(job_ref, source)
    if job.get("terminal_receipt") is not None:
        raise TerminalReceiptConflict("output_terminal_replay_required")
    job_ref.update({"stage": stage}, option=db.write_option(last_update_time=snapshot.update_time))
    blob.upload_from_string(
        json.dumps(body, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
        if_generation_match=expected_generation,
    )
    _assert_output_owner(job_ref, source)


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
    return figure_markup._replace_markdown_images(markdown_text, replacements)


def _extract_markdown_images(markdown_text: str) -> list[str]:
    return figure_markup._extract_markdown_images(markdown_text)


def _image_reference_spans(text: str):
    return figure_markup._image_reference_spans(text)


def _start_analyzer_prewarm() -> None:
    global _PREWARM_STARTED  # noqa: PLW0603
    if not OCR_PREWARM_ANALYZER:
        return
    with _PREWARM_LOCK:
        if _PREWARM_STARTED:
            return
        _PREWARM_STARTED = True

    def _runner() -> None:
        try:
            prewarm_analyzer(device=YOMITOKU_DEVICE, visualize=YOMITOKU_VIS)
            app.logger.info("OCR pipeline analyzer prewarm completed")
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("OCR pipeline analyzer prewarm failed error=%s", str(exc))

    threading.Thread(target=_runner, name="ocr-pipeline-prewarm", daemon=True).start()


_start_analyzer_prewarm()


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


def _template_roi_needs_ocr_words(tpl_cfg: dict[str, Any] | None) -> bool:
    if not isinstance(tpl_cfg, dict):
        return False
    rois_cfg = tpl_cfg.get("rois")
    if not isinstance(rois_cfg, dict):
        return False
    qty_cfg = rois_cfg.get("qty")
    if not isinstance(qty_cfg, dict):
        return False
    auto_headers = qty_cfg.get("auto_headers") or tpl_cfg.get("auto_headers") or []
    if not isinstance(auto_headers, list) or not auto_headers:
        return False
    column_edges = qty_cfg.get("column_edges") or []
    if isinstance(column_edges, list) and len(column_edges) >= 2:
        return False
    return True


def _run_template_classification(
    *,
    pdf_bytes: bytes,
    requested_template_id: str | None,
    requested_template_ids: list[str] | None = None,
    captured_config: dict | None = None,
    configured_digest: str | None = None,
) -> tuple[dict | None, dict | None]:
    authored = load_template_config(db, requested_template_id, captured_config=captured_config, expected_digest=configured_digest)
    authored_templates = {requested_template_id: authored}
    if requested_template_ids is not None and requested_template_ids != [] and requested_template_ids != [requested_template_id]:
        raise ValueError("captured_template_selection_required")
    candidate_template_ids = [requested_template_id]
    if not OCR_ENABLE_TEMPLATE_MATCH:
        return None, None
    png_bytes = render_pdf_to_png_bytes(pdf_bytes, dpi=OCR_TEMPLATE_MATCH_DPI)
    img_match_bgr, img_ocr_bgr, img_alt_bgr = build_images_for_match_and_ocr(png_bytes)
    preferred_id = str(requested_template_id).strip()
    preferred_match_id, preferred_warped_match, preferred_warped_ocr, preferred_warped_alt, preferred_diagnostics = choose_template_and_warp(
        db,
        img_match_bgr,
        img_ocr_bgr,
        img_alt_bgr=img_alt_bgr,
        template_ids=[preferred_id],
        authored_templates=authored_templates,
    )
    if preferred_match_id != preferred_id:
        raise RuntimeError(f"requested template did not match: {requested_template_id}")
    matched_template_id = preferred_match_id
    warped_match = preferred_warped_match
    warped_ocr = preferred_warped_ocr
    warped_alt = preferred_warped_alt
    diagnostics = preferred_diagnostics
    classification_mode = "preferred_primary"
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
        tpl_cfg = load_template_config(db, matched_template_id.strip(), captured_config=captured_config, expected_digest=configured_digest)
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
    ocr_words = None
    if _template_roi_needs_ocr_words(tpl_cfg):
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
    if qty_ocr_engine == "tesseract_digits" or text_ocr_engine == "tesseract_digits":
        raise RuntimeError("tesseract_digits has been removed")
    if not text_ocr_engine:
        text_ocr_engine = "yomitoku"

    def _ocr_fn(image_bgr, _prompt: str, _max_tokens: int) -> str:
        if _max_tokens > 32 and text_ocr_engine in {"skip", "none", "disabled", "off"}:
            return ""
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
    try:
        blob, pdf_bytes = _download_generation_input(bucket, name, generation)
        terminal_source = _original_pipeline_source(bucket, name, generation, blob=blob, data=pdf_bytes)
        output_name = _owned_output_name(output_prefix, terminal_source)
        terminal_source["output_reference"] = format_gcs_uri(bucket, output_name)
    except (ValueError, TerminalReceiptConflict) as exc:
        return jsonify({"status": "needs_inspection", "error": str(exc)}), 409
    input_reference = format_gcs_uri(bucket, name)
    output_reference = format_gcs_uri(bucket, output_name)
    safe_job_id = _safe_job_id(terminal_source)
    job_ref = db.collection(JOB_COLLECTION).document(safe_job_id)
    if job_ref.get().exists:
        app.logger.info("OCR pipeline duplicate job=%s", safe_job_id)
        result, status = _duplicate_outcome(job_ref, bucket, name, generation, output_name)
        return jsonify(result), status

    from google.api_core.exceptions import AlreadyExists, NotFound
    original_intent_present = False
    try:
        gcs.bucket(bucket).blob(output_name + ".terminal-intent.json").reload()
        original_intent_present = True
    except NotFound:
        try:
            gcs.bucket(bucket).blob(output_name).reload()
        except NotFound:
            pass
        else:
            if job_ref.get().exists:
                result, status = _duplicate_outcome(job_ref, bucket, name, generation, output_name)
                return jsonify(result), status
            return jsonify({"status": "needs_inspection", "error": "output_original_claim_missing"}), 409
    try:
        job_ref.create(
            {
                "status": "running",
                "input": {"bucket": bucket, "name": name, "generation": generation},
                "job_id": job_id,
                "safe_job_id": safe_job_id,
                "execution_claim": {"job_id": job_id, "output_reference": output_reference},
                "output": {"bucket": bucket, "name": output_name},
            }
        )
    except AlreadyExists:
        result, status = _duplicate_outcome(job_ref, bucket, name, generation, output_name)
        return jsonify(result), status
    if original_intent_present:
        result, status = _duplicate_outcome(job_ref, bucket, name, generation, output_name)
        return jsonify(result), status
    app.logger.info("OCR pipeline job created job=%s", safe_job_id)
    with _ACTIVE_PRODUCER_LOCK:
        _ACTIVE_PRODUCER_JOBS.add(job_id)

    start_time = time.time()
    owner_token = _OUTPUT_OWNER.set((terminal_source, job_ref))
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
        facility_id, template_id, template_ids = _extract_metadata(blob)
        configured_input, configured_digest = _load_configured_input(blob)
        authored_template = validate_captured_config(configured_input, configured_digest)
        if configured_input["facility"].get("facility_id") != facility_id or authored_template.get("template_id") != template_id:
            raise ValueError("captured_template_id_mismatch")
        resolved_template_id = template_id
        app.logger.info(
            "OCR pipeline input downloaded job=%s bytes=%s facility_id=%s template_id=%s template_ids=%s",
            safe_job_id,
            len(pdf_bytes),
            facility_id,
            template_id,
            template_ids,
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
        canonical_stages = {
            "classification": "required" if OCR_ENABLE_TEMPLATE_MATCH else "disabled",
            "roi": "required" if OCR_ENABLE_TEMPLATE_ROI else "disabled",
            "correction": "required" if OCR_ENABLE_PAGE_CORRECTION else "disabled",
        }
        try:
            classification, template_context = _run_template_classification(
                pdf_bytes=pdf_bytes,
                requested_template_id=template_id,
                requested_template_ids=template_ids,
                captured_config=configured_input,
                configured_digest=configured_digest,
            )
        except Exception as exc:  # noqa: BLE001
            raise MandatoryOcrStageFailed("template_match_error") from exc
        if isinstance(classification, dict):
            matched_template_id = classification.get("matched_template_id")
            if not resolved_template_id and isinstance(matched_template_id, str) and matched_template_id.strip():
                resolved_template_id = matched_template_id.strip()
            if template_id and matched_template_id and template_id != matched_template_id:
                classification_warnings.append("template_match_mismatch")
            if template_id and not matched_template_id:
                raise MandatoryOcrStageFailed("template_match_unresolved")
        if OCR_ENABLE_TEMPLATE_MATCH:
            if not isinstance(template_context, dict):
                raise MandatoryOcrStageFailed("template_match_unresolved")
            canonical_stages["classification"] = "passed"
        if OCR_ENABLE_TEMPLATE_ROI and not isinstance(template_context, dict):
            raise MandatoryOcrStageFailed("roi_template_context_missing")
        if OCR_ENABLE_TEMPLATE_ROI and isinstance(template_context, dict):
            try:
                roi_extraction = _run_template_roi_extraction(template_context=template_context)
                if not isinstance(roi_extraction, dict):
                    raise MandatoryOcrStageFailed("roi_extraction_missing")
                canonical_stages["roi"] = "passed"
            except Exception as exc:  # noqa: BLE001
                raise MandatoryOcrStageFailed("roi_extraction_error") from exc

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
                    authored_templates={template_id: authored_template},
                )
                if not yomitoku_pdf_bytes or not isinstance(page_correction_summary, dict):
                    raise MandatoryOcrStageFailed("page_correction_result_missing")
                canonical_stages["correction"] = "passed"
            except Exception as exc:  # noqa: BLE001
                raise MandatoryOcrStageFailed("page_correction_error") from exc

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

        _verify_selected_pdf_input(blob, pdf_bytes)
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
        _validate_figure_pages(page_results)

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
        artifact_prefix = output_name.rsplit("/", 1)[0] + "/assets/"
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
            "corrected_pdf_receipt": page_correction_summary.get("corrected_pdf_receipt"),
            "corrected_pdf_uri": corrected_pdf_uri,
            "corrected_pdf_uploaded": bool(corrected_pdf_uri),
        }
        page_correction_artifacts["recognition_input"] = _recognition_input_receipt(
            pdf_bytes, yomitoku_pdf_bytes,
            json.loads((blob.metadata or {})["selected_pdf_receipt"]),
            page_correction_summary.get("corrected_pdf_receipt"),
        )

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
            markdown_text, page_figure_count, page_assets = _publish_page_figures(bucket, figure_prefix, page_result)
            figure_count += page_figure_count
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

            figure_uris = list(page_assets.values())
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
                _verify_selected_pdf_input(blob, pdf_bytes)
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
            "input_generation": generation,
            "input_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "configured_input_sha256": configured_digest,
            "canonical_stages": canonical_stages,
            "canonical_adoption_allowed": True,
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
        _verify_selected_pdf_input(blob, pdf_bytes)
        output_payload.update({key: terminal_source[key] for key in ("execution_owner", "configured_input_receipt", "selected_pdf_receipt")})
        _complete_terminal(job_ref, bucket, name, generation, output_name, terminal_source, output_payload)
        app.logger.info("OCR pipeline stage: done job=%s", safe_job_id)
        return jsonify(_terminal_ack(output_payload)), 200
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, TerminalReceiptConflict):
            return jsonify({"status": "needs_inspection", "error": str(exc)}), 409
        if isinstance(exc, TerminalPersistenceIncomplete):
            raise
        if "terminal_source" not in locals():
            job_ref.update({"status": "needs_inspection", "error": repr(exc)})
            return jsonify({"status": "needs_inspection", "error": "terminal_source_unresolved"}), 409
        failed_payload = {**terminal_source, "status": "failed", "stage": "error",
                          "error": repr(exc), "error_type": type(exc).__name__, "canonical_adoption_allowed": False}
        _complete_terminal(job_ref, bucket, name, generation, output_name, terminal_source, failed_payload)
        app.logger.exception("OCR pipeline failed job=%s", safe_job_id)
        return jsonify(_terminal_ack(failed_payload)), 200
    finally:
        _OUTPUT_OWNER.reset(owner_token)
        with _ACTIVE_PRODUCER_LOCK:
            _ACTIVE_PRODUCER_JOBS.discard(job_id)
