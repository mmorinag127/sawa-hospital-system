"""Canonical, immutable checkpoints for the Hakodate recognizer boundary."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from uuid import uuid4
from typing import Any, Callable

import cv2
import numpy as np

from src.services import storage_service


CONTRACT = "hakodate_recognizer_checkpoint/v1"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _receipt(uri: str, data: bytes, content_type: str) -> dict[str, Any]:
    return {"uri": uri, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "content_type": content_type}


def _load(receipt: dict[str, Any], *, content_type: str) -> bytes:
    if not isinstance(receipt, dict) or receipt.get("content_type") != content_type:
        raise ValueError("hakodate_checkpoint_receipt_invalid")
    data = storage_service.load_bytes_from_uri(str(receipt.get("uri") or ""), verification=True)
    if len(data) != receipt.get("size_bytes") or hashlib.sha256(data).hexdigest() != receipt.get("sha256"):
        raise ValueError("hakodate_checkpoint_bytes_changed")
    return data


def _source(item: dict[str, Any]) -> dict[str, Any]:
    selected_pdf = item.get("selected_pdf_receipt")
    configured = item.get("configured_input")
    identity = {key: item.get(key) for key in ("order_id", "facility_code", "week_code")}
    storage_service.verify_selected_pdf_source(selected_pdf)
    if not isinstance(configured, dict) or not configured:
        raise ValueError("hakodate_checkpoint_configured_input_required")
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise ValueError("hakodate_checkpoint_input_identity_required")
    required = ("original_physical_draft", "original_target_semantics", "original_expected_sheet_cells",
                "selected_page", "quad_px", "quad_source", "header_axis_override", "row_axis_override")
    if any(key not in item for key in required):
        raise ValueError("hakodate_checkpoint_source_fields_required")
    if (not isinstance(item["original_physical_draft"], dict) or not item["original_physical_draft"]
            or not item["original_target_semantics"] or not item["quad_px"]
            or not item["quad_source"] or type(item["selected_page"]) is not int or item["selected_page"] < 1):
        raise ValueError("hakodate_checkpoint_source_fields_invalid")
    _expected_cells(item["original_expected_sheet_cells"])
    for key in ("quad_override", "header_axis_override", "row_axis_override"):
        if item.get(key) is not None and not isinstance(item.get(key), dict):
            raise ValueError("hakodate_checkpoint_source_fields_invalid")
    return {
        "selected_pdf_receipt": deepcopy(selected_pdf),
        "configured_input_digest": _digest(configured),
        "order_input_identity": identity,
        "recognition_input_digest": _digest({**{key: item[key] for key in required}, "quad_override": item.get("quad_override")}),
    }


def _expected_cells(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(cell, str) or not cell.strip() for cell in value)
            or len(value) != len(set(value))):
        raise ValueError("hakodate_checkpoint_expected_cells_required")


def _regions(regions: list[dict[str, Any]], expected_sheet_cells: list[str]) -> list[dict[str, Any]]:
    _expected_cells(expected_sheet_cells)
    if not isinstance(regions, list) or any(not isinstance(region, dict) for region in regions):
        raise ValueError("hakodate_checkpoint_regions_invalid")
    if not isinstance(regions, list) or [region.get("sheet_cell") for region in regions] != expected_sheet_cells:
        raise ValueError("hakodate_checkpoint_cell_identity_changed")
    for region in regions:
        box = region.get("bbox")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError("hakodate_checkpoint_regions_invalid")
    return deepcopy(regions)


def _resume_item(item: dict[str, Any]) -> dict[str, Any]:
    """Only retain canonical inputs needed after the pre-recognition boundary."""
    keys = (
        "order_id", "facility_code", "week_code", "configured_input",
        "original_physical_draft", "original_target_semantics", "original_expected_sheet_cells",
        "selected_page", "quad_px", "quad_source", "quad_override", "header_axis_override", "row_axis_override",
    )
    return {key: deepcopy(item.get(key)) for key in keys}


def _metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("hakodate_checkpoint_metadata_required")
    if not isinstance(value, dict):
        raise ValueError("hakodate_checkpoint_metadata_invalid")
    required = ("axis_evidence", "rectified_quad_points", "snap_debug")
    if any(key not in value for key in required):
        raise ValueError("hakodate_checkpoint_metadata_required")
    if not isinstance(value["axis_evidence"], dict) or not isinstance(value["snap_debug"], dict):
        raise ValueError("hakodate_checkpoint_metadata_invalid")
    points = value["rectified_quad_points"]
    if (not isinstance(points, (list, tuple)) or len(points) != 4
            or any(not isinstance(p, (list, tuple)) or len(p) != 2
                   or any(type(v) not in (int, float) or not math.isfinite(v) for v in p) for p in points)):
        raise ValueError("hakodate_checkpoint_metadata_invalid")
    frozen = {key: deepcopy(value[key]) for key in required}
    _json_bytes(frozen)
    return frozen


def persist_recognizer_checkpoint(*, item: dict[str, Any], raw_rectified_bgr: np.ndarray, regions: list[dict[str, Any]], expected_sheet_cells: list[str], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store the exact pre-recognition image and its canonical cell identities."""
    source = _source(item)
    frozen_regions = _regions(regions, expected_sheet_cells)
    if expected_sheet_cells != item["original_expected_sheet_cells"]:
        raise ValueError("hakodate_checkpoint_cell_identity_changed")
    if not isinstance(raw_rectified_bgr, np.ndarray) or raw_rectified_bgr.size == 0:
        raise ValueError("hakodate_checkpoint_image_required")
    ok, encoded = cv2.imencode(".png", raw_rectified_bgr)
    if not ok:
        raise ValueError("hakodate_checkpoint_image_encode_failed")
    image = encoded.tobytes()
    manifest = {
        "contract": CONTRACT,
        "source": source,
        "expected_sheet_cells": list(expected_sheet_cells),
        "regions": frozen_regions,
        "resume_item": _resume_item(item),
        "metadata": _metadata(metadata) if metadata is not None else None,
        "image": None,
    }
    _json_bytes(manifest)
    namespace = f"OCR-CHECKPOINT-{uuid4().hex}"
    image_uri = storage_service.save_canonical_artifact_bytes(namespace, "rectified.png", image, "image/png")
    manifest["image"] = _receipt(image_uri, image, "image/png")
    manifest_bytes = _json_bytes(manifest)
    manifest_uri = storage_service.save_canonical_artifact_bytes(namespace, "checkpoint.json", manifest_bytes, "application/json")
    return {**manifest, "checkpoint": _receipt(manifest_uri, manifest_bytes, "application/json")}


def load_recognizer_checkpoint(*, checkpoint: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Reject changed inputs before returning recognizer-only state."""
    manifest_bytes = _load(checkpoint.get("checkpoint"), content_type="application/json")
    manifest = json.loads(manifest_bytes)
    if manifest != {key: value for key, value in checkpoint.items() if key != "checkpoint"}:
        raise ValueError("hakodate_checkpoint_manifest_changed")
    if manifest.get("contract") != CONTRACT or manifest.get("source") != _source(item):
        raise ValueError("hakodate_checkpoint_input_changed")
    regions = _regions(manifest.get("regions"), manifest.get("expected_sheet_cells"))
    image = _load(manifest.get("image"), content_type="image/png")
    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("hakodate_checkpoint_image_invalid")
    if manifest.get("resume_item") != _resume_item(item):
        raise ValueError("hakodate_checkpoint_input_changed")
    metadata = _metadata(manifest["metadata"]) if manifest.get("metadata") is not None else None
    return {
        "raw_rectified": decoded,
        "regions": regions,
        "expected_sheet_cells": list(manifest["expected_sheet_cells"]),
        "resume_item": {**deepcopy(manifest["resume_item"]),
                        "selected_pdf_receipt": deepcopy(manifest["source"]["selected_pdf_receipt"])},
        "metadata": metadata,
        "axis_evidence": deepcopy(metadata["axis_evidence"]) if metadata is not None else None,
        "rectified_quad_points": deepcopy(metadata["rectified_quad_points"]) if metadata is not None else None,
        "snap_debug": deepcopy(metadata["snap_debug"]) if metadata is not None else None,
    }


def resume_recognizer_from_checkpoint(*, checkpoint: dict[str, Any], item: dict[str, Any], recognize: Callable[..., Any]) -> Any:
    """Run only the recognizer callback; preprocessing is intentionally absent."""
    state = load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
    return recognize(raw_rectified_bgr=state["raw_rectified"], regions=state["regions"], expected_sheet_cells=state["expected_sheet_cells"])
