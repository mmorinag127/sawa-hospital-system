import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import evidence_manifest_service, template_resolution_service  # noqa: E402


def test_build_evidence_manifest_marks_missing_overlay_as_incomplete():
    payload = {
        "table_raw": "|a|b|",
        "quantity_subgrid_passes": [],
        "template_resolution": {"resolved_template_id": "tpl-1"},
        "table_box": [0.1, 0.2, 0.8, 0.9],
        "grid_column_edges": [0.1, 0.5, 0.8],
        "grid_row_edges": [0.2, 0.4, 0.9],
        "pages": [],
    }

    manifest = evidence_manifest_service.build_evidence_manifest(payload)

    assert manifest["artifacts_complete"] is False
    assert "overlay_pages" in manifest["missing_artifacts"]
    assert "digest" in manifest


def test_template_resolution_flags_mismatch_and_low_confidence():
    resolution = template_resolution_service.build_template_resolution(
        requested_template_id="tpl-requested",
        requested_template_ids=["tpl-requested"],
        resolved_template_id="tpl-resolved",
        classification={
            "matched_template_id": "tpl-resolved",
            "confidence": 0.41,
            "candidates": [{"id": "tpl-resolved", "score": 0.41}],
        },
        page_correction_summary={
            "pages": [
                {"mode": "template_warp", "template_id": "tpl-other"},
            ]
        },
    )

    assert resolution["blocked"] is True
    assert "template_mismatch" in resolution["blocked_reasons"]
    assert "template_confidence_low" in resolution["blocked_reasons"]
    assert "page_correction_template_mismatch" in resolution["blocked_reasons"]


def test_legacy_payload_without_evidence_context_does_not_trigger_blocker():
    payload = {
        "rows": [
            {
                "row_index": 0,
                "date_mmdd": "03/21",
                "daypart": "朝",
                "menu": "Menu A",
            }
        ]
    }

    enriched = evidence_manifest_service.ensure_evidence_manifest(payload)

    assert enriched == payload
    assert evidence_manifest_service.evidence_missing_artifacts(payload) == []
    assert evidence_manifest_service.evidence_is_complete(payload) is True
