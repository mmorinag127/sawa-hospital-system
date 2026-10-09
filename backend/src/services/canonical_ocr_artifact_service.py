"""Persist the actual best-method output contract before its workspace closes."""

from copy import deepcopy
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from uuid import uuid4

from src.services import storage_service


REQUIRED_OUTPUTS = (
    "pdf", "overlay", "overlay_png", "sheet_review_base", "contact_sheet",
    "records", "ocr_regions", "sheet_values",
)
CONTRACT = "hakodate_best_method_outputs/v1"


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receipt(uri, data, content_type):
    return {"uri": uri, "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data), "content_type": content_type}


def _verify(receipt):
    if not isinstance(receipt, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or "")):
        raise ValueError("canonical_artifact_receipt_invalid")
    uri = receipt.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ValueError("canonical_artifact_uri_required")
    try:
        data = storage_service.load_bytes_from_uri(uri, verification=True)
    except Exception as exc:
        raise ValueError("canonical_artifact_bytes_unavailable") from exc
    if len(data) != receipt.get("size_bytes") or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
        raise ValueError("canonical_artifact_bytes_changed")
    return data


def validate_payload(payload):
    pipeline = payload.get("hakodate_canonical_pipeline")
    if not isinstance(pipeline, dict):
        raise ValueError("canonical_artifact_packet_required")
    source = payload.get("hakodate_repair_source_receipt")
    selected_pdf = source.get("selected_pdf_receipt") if isinstance(source, dict) else None
    order_id = selected_pdf.get("order_id") if isinstance(selected_pdf, dict) else None
    if not order_id or pipeline.get("requested_order_id") != order_id:
        raise ValueError("canonical_artifact_source_changed")
    validate_packet(
        pipeline.get("artifact_packet"), source_receipt=source,
        outputs=pipeline.get("outputs"), expected_sheet_cells=pipeline.get("expected_sheet_cells"),
        producer_version=pipeline.get("version"), order_id=order_id,
    )
    outputs = pipeline["outputs"]
    overlay = payload.get("hakodate_overlay")
    if (not isinstance(overlay, dict) or overlay.get("uri") != outputs["overlay"]
            or overlay.get("sheet_review_base_uri") != outputs["sheet_review_base"]):
        raise ValueError("canonical_artifact_overlay_reference_changed")
    evidence = payload.get("hakodate_ocr_evidence")
    records = payload.get("hakodate_ocr_evidence_records")
    if (not isinstance(evidence, dict) or not isinstance(records, list)
            or evidence.get("records") != records
            or any(not isinstance(record, dict)
                   or record.get("raw_payload_ref") != outputs["records"] for record in records)):
        raise ValueError("canonical_artifact_evidence_reference_changed")


def persist_outputs(*, order_id, outputs, source_receipt, expected_sheet_cells, producer_version, workspace):
    if not isinstance(outputs, dict) or any(not outputs.get(key) for key in REQUIRED_OUTPUTS):
        raise ValueError("canonical_artifact_outputs_missing")
    if not isinstance(source_receipt, dict) or not source_receipt or not expected_sheet_cells:
        raise ValueError("canonical_artifact_source_required")
    namespace = f"OCR-{order_id}-{uuid4().hex}"
    receipts = {}
    saved_paths = {}
    for key, value in outputs.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_]+", key) or not isinstance(value, str):
            raise ValueError("canonical_artifact_output_invalid")
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ValueError("canonical_artifact_output_not_file")
        if path not in saved_paths:
            data = path.read_bytes()
            if not data:
                raise ValueError("canonical_artifact_output_empty")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            uri = storage_service.save_canonical_artifact_bytes(namespace, f"{key}{path.suffix}", data, content_type)
            if not uri.startswith("gs://") and Path(uri).resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError("canonical_artifact_storage_inside_workspace")
            saved_paths[path] = _receipt(uri, data, content_type)
            _verify(saved_paths[path])
        receipts[key] = deepcopy(saved_paths[path])
    metadata = {"contract": CONTRACT, "order_id": order_id, "producer_version": producer_version,
                "source_receipt": deepcopy(source_receipt), "expected_sheet_cells": list(expected_sheet_cells),
                "outputs": receipts}
    data = _json_bytes(metadata)
    uri = storage_service.save_canonical_artifact_bytes(namespace, "metadata.json", data, "application/json")
    packet = {**metadata, "metadata": _receipt(uri, data, "application/json")}
    validate_packet(packet, source_receipt=source_receipt, outputs={key: value["uri"] for key, value in receipts.items()},
                    expected_sheet_cells=expected_sheet_cells, producer_version=producer_version, order_id=order_id)
    return packet


def validate_packet(packet, *, source_receipt, outputs, expected_sheet_cells, producer_version, order_id):
    if not isinstance(packet, dict) or packet.get("contract") != CONTRACT:
        raise ValueError("canonical_artifact_packet_required")
    if (packet.get("source_receipt") != source_receipt or not source_receipt
            or packet.get("expected_sheet_cells") != expected_sheet_cells or not expected_sheet_cells
            or packet.get("producer_version") != producer_version or not producer_version
            or packet.get("order_id") != order_id or not order_id):
        raise ValueError("canonical_artifact_source_changed")
    receipts = packet.get("outputs")
    if (not isinstance(receipts, dict) or not isinstance(outputs, dict) or set(receipts) != set(outputs)
            or any(key not in receipts for key in REQUIRED_OUTPUTS)):
        raise ValueError("canonical_artifact_outputs_missing")
    metadata = {key: value for key, value in packet.items() if key != "metadata"}
    if _verify(packet.get("metadata")) != _json_bytes(metadata):
        raise ValueError("canonical_artifact_metadata_changed")
    for key, receipt in receipts.items():
        if not isinstance(receipt, dict) or outputs[key] != receipt.get("uri"):
            raise ValueError("canonical_artifact_reference_changed")
        data = _verify(receipt)
        if key in ("records", "ocr_regions") and not isinstance(json.loads(data), list):
            raise ValueError("canonical_artifact_content_invalid")
        if key == "sheet_values" and not isinstance(json.loads(data), dict):
            raise ValueError("canonical_artifact_content_invalid")
        if key in ("overlay", "overlay_png", "sheet_review_base", "contact_sheet") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("canonical_artifact_content_invalid")
        if key == "pdf" and not data.startswith(b"%PDF-"):
            raise ValueError("canonical_artifact_content_invalid")
