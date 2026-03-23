import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import evidence_manifest_service, ocr_evidence_service, template_resolution_service  # noqa: E402


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


def test_template_resolution_does_not_block_only_on_classifier_mismatch_within_requested_scope():
    resolution = template_resolution_service.build_template_resolution(
        requested_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
        requested_template_ids=[
            "fax_layout_regular_soft_mixer_forbidden_v1",
            "fax_layout_floor_2f3f_v1",
        ],
        resolved_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
        classification={
            "matched_template_id": "fax_layout_floor_2f3f_v1",
            "confidence": 0.93,
            "candidates": [
                {"id": "fax_layout_floor_2f3f_v1", "score": 0.93},
                {"id": "fax_layout_regular_soft_mixer_forbidden_v1", "score": 0.91},
            ],
        },
        page_correction_summary={"pages": [{"mode": "template_warp", "template_id": "fax_layout_regular_soft_mixer_forbidden_v1"}]},
    )

    assert resolution["mismatch"] is True
    assert resolution["classifier_mismatch"] is True
    assert resolution["warp_mismatch"] is False
    assert resolution["blocked"] is False
    assert resolution["blocked_reasons"] == []


def test_payload_has_quantity_column_semantics_when_template_registry_can_supply_column_edges():
    payload = {
        "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                "layout_overlay_uri": "gs://bucket/layout-page-1.png",
            }
        ],
        "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|5|",
        "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "5"]]}],
        "template_resolution": {
            "requested_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "requested_template_ids": [
                "fax_layout_regular_soft_mixer_forbidden_v1",
                "fax_layout_floor_2f3f_v1",
            ],
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "matched_template_id": "fax_layout_floor_2f3f_v1",
            "blocked": True,
            "blocked_reasons": ["template_mismatch"],
        },
        "table_box": None,
        "grid_column_edges": [],
        "grid_row_edges": [],
    }

    assert ocr_evidence_service.payload_has_quantity_column_semantics(payload) is True


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
