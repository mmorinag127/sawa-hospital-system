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
    "page_correction",
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
    table_box = payload.get("table_box")
    column_edges = payload.get("grid_column_edges")
    row_edges = payload.get("grid_row_edges")
    return (
        isinstance(table_box, list)
        and len(table_box) >= 4
        and isinstance(column_edges, list)
        and len(column_edges) >= 2
        and isinstance(row_edges, list)
        and len(row_edges) >= 2
    )


def _has_evidence_context(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("evidence_manifest"), dict):
        return True
    return any(key in payload for key in EVIDENCE_CONTEXT_KEYS)


def build_evidence_manifest(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = payload if isinstance(payload, dict) else {}
    artifacts = {
        "corrected_pdf": bool(
            str(
                ((normalized.get("page_correction_artifacts") or {}).get("corrected_pdf_uri"))
                or ((normalized.get("page_correction") or {}).get("corrected_pdf_uri"))
                or ((normalized.get("combined") or {}).get("corrected_pdf"))
                or ""
            ).strip()
        ),
        "overlay_pages": _has_overlay_pages(normalized.get("pages")),
        "table_raw": bool(str(normalized.get("table_raw") or "").strip()),
        "quantity_subgrid": isinstance(normalized.get("quantity_subgrid_passes"), list),
        "template_resolution": isinstance(normalized.get("template_resolution"), dict),
        "grid_metadata": _has_grid_metadata(normalized),
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
    return manifest


def ensure_evidence_manifest(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return payload
    if not _has_evidence_context(payload):
        return payload
    manifest = payload.get("evidence_manifest")
    if not isinstance(manifest, dict):
        manifest = build_evidence_manifest(payload)
    elif "digest" not in manifest:
        manifest = dict(manifest)
        manifest["digest"] = _digest({k: v for k, v in manifest.items() if k != "digest"})
    enriched = dict(payload)
    enriched["evidence_manifest"] = manifest
    enriched["evidence_digest"] = str(manifest.get("digest") or "").strip() or _digest(manifest)
    return enriched


def evidence_missing_artifacts(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if not _has_evidence_context(payload):
        return []
    manifest = ensure_evidence_manifest(payload).get("evidence_manifest")  # type: ignore[union-attr]
    if not isinstance(manifest, dict):
        return []
    missing = manifest.get("missing_artifacts")
    if not isinstance(missing, list):
        return []
    return [str(item).strip() for item in missing if str(item).strip()]


def evidence_is_complete(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return True
    if not _has_evidence_context(payload):
        return True
    manifest = ensure_evidence_manifest(payload).get("evidence_manifest")  # type: ignore[union-attr]
    if not isinstance(manifest, dict):
        return True
    return bool(manifest.get("artifacts_complete"))
