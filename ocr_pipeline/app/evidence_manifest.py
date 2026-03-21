from __future__ import annotations

import hashlib
import json
from typing import Any


REQUIRED_ARTIFACT_KEYS = (
    "corrected_pdf",
    "overlay_pages",
    "table_raw",
    "quantity_subgrid",
    "template_resolution",
    "grid_metadata",
)

EVIDENCE_CONTEXT_KEYS = (
    "page_correction_artifacts",
    "combined",
    "quantity_subgrid_passes",
    "template_resolution",
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _has_overlay_pages(pages: object) -> bool:
    if not isinstance(pages, list) or not pages:
        return False
    for page in pages:
        if not isinstance(page, dict):
            return False
        if not any(
            isinstance(page.get(key), str) and str(page.get(key)).strip()
            for key in ("ocr_overlay_uri", "layout_overlay_uri")
        ):
            return False
    return True


def _has_grid_metadata(payload: dict[str, Any]) -> bool:
    roi = payload.get("roi_extraction") if isinstance(payload.get("roi_extraction"), dict) else {}
    has_table_box = bool(
        isinstance(roi.get("table_box"), (list, tuple)) and len(roi.get("table_box")) >= 4
    )
    has_columns = bool(isinstance(roi.get("grid_column_edges"), list) and len(roi.get("grid_column_edges")) >= 2)
    has_rows = bool(isinstance(roi.get("grid_row_edges"), list) and len(roi.get("grid_row_edges")) >= 2)
    return has_table_box and has_columns and has_rows


def _has_evidence_context(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("evidence_manifest"), dict):
        return True
    return any(key in payload for key in EVIDENCE_CONTEXT_KEYS)


def ensure_evidence_manifest(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return payload
    if not _has_evidence_context(payload):
        return payload
    artifacts = {
        "corrected_pdf": bool(
            str(
                ((payload.get("page_correction_artifacts") or {}).get("corrected_pdf_uri"))
                or ((payload.get("combined") or {}).get("corrected_pdf"))
                or ""
            ).strip()
        ),
        "overlay_pages": _has_overlay_pages(payload.get("pages")),
        "table_raw": bool(str(payload.get("table_raw") or "").strip()),
        "quantity_subgrid": isinstance(payload.get("quantity_subgrid_passes"), list),
        "template_resolution": isinstance(payload.get("template_resolution"), dict),
        "grid_metadata": _has_grid_metadata(payload),
    }
    missing = [key for key, present in artifacts.items() if not present]
    manifest = {
        "version": 1,
        "required_artifacts": list(REQUIRED_ARTIFACT_KEYS),
        "artifacts": artifacts,
        "missing_artifacts": missing,
        "artifacts_complete": not missing,
    }
    manifest["digest"] = _digest(manifest)
    enriched = dict(payload)
    enriched["evidence_manifest"] = manifest
    enriched["evidence_digest"] = manifest["digest"]
    return enriched
